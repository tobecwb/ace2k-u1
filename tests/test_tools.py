"""Virtual tools: the spools, the plan, the start check, the T commands, the swap, the
temperatures."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import pathlib
import re
import shutil
import types
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1
import ace2k_u1_common
import ace2k_u1_config
import ace2k_u1_eject
import ace2k_u1_probe
import ace2k_u1_refill
import ace2k_u1_rollback
import ace2k_u1_swap
import ace2k_u1_wraps
import fakes
import pytest
from ace2k_u1_swap import OwedTool, SwapState
from test_check import Gcode
from test_tags_wiring import BAMBU, TABLE
from test_units import Gcmd, LanesConfig, make_rig

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked, Slot
    from ace2k_u1_plan import LaneSpool
    from ace2k_u1_printer_types import GCodeCommand, TagRecord
    from ace2k_u1_spools import UnitLanes
    from fakes import FakeAce2k, FakeMove
    from test_check import Printer

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def tools_rig(
    tmp_path: Path,
    lanes: str = "a:1+a:2, a:3, a:4, b:1+b:2+b:3+b:4",
    active: dict[str, tuple[str, int]] | None = None,
    values: Mapping[str, object] | None = None,
) -> tuple[SimpleNamespace, dict[str, FakeAce2k]]:
    """active: the coordinator's choices ({extruder: (unit name, lane index)}) at the hook;
    values: the [ace2k_u1] keys besides lanes:."""
    units, by_name = fakes.two_units()
    units.share("extruder", ("a", 0), ("a", 1))
    units.share("extruder3", ("b", 0), ("b", 1), ("b", 2), ("b", 3))
    units.active.update(active or {})
    gcodes = tmp_path / "gcodes"
    gcodes.mkdir(exist_ok=True)
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", gcodes / "eight.gcode")
    rig = make_rig(
        lanes,
        units,
        by_name,
        config_dir=tmp_path,
        sdcard_path=gcodes,
        print_stats=True,
        task_config=True,
        values=values,
    )
    return rig, by_name


def sent_macro(rig: SimpleNamespace, line: str) -> bool:
    """line (a swap macro's, without its MATERIAL=) sent with a MATERIAL=, whatever its value."""
    return any(ln.startswith(line + " MATERIAL=") for ln in rig.sent)


def run(rig: SimpleNamespace, command: str, params: dict[str, str]) -> Gcmd:
    gcmd = Gcmd(params, command)
    rig.gcode.commands[command](gcmd)
    return gcmd


def test_a_spool_record_survives_a_restart(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ffffff"})
    saved = json.loads((tmp_path / "ace2k_u1_spools.json").read_text())
    assert saved == {"a:2": {"vendor": "Generic", "type": "PLA", "color": "#ffffff"}}
    assert not (tmp_path / "ace2k_u1_spools.json.tmp").exists()
    rig2, _ = tools_rig(tmp_path)
    assert rig2.module.spool_store.records["a:2"]["color"] == "#ffffff"


def test_the_status_publishes_the_spool_records_as_a_copy(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    assert rig.module.get_status()["spools"] == {}
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PETG", "COLOR": "1A2B3C"})
    status = rig.module.get_status()
    assert status["spools"] == {"a:2": {"vendor": "Generic", "type": "PETG", "color": "#1a2b3c"}}
    json.dumps(status["spools"])  # what Moonraker sends on
    assert status["spools"]["a:2"] is not rig.module.spool_store.records["a:2"]  # a copy
    # not hooked: published all the same, so the page names a lane's spool either way
    rig.module.adapter = None
    status = rig.module.get_status()
    assert status["hooked"] is False
    assert status["spools"] == {"a:2": {"vendor": "Generic", "type": "PETG", "color": "#1a2b3c"}}


def test_the_status_copies_are_built_once_per_change(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    first, second = rig.module.get_status(), rig.module.get_status()
    assert first["spools"] == second["spools"] and first["plan"] == second["plan"]
    assert first["spools"] is second["spools"] and first["plan"] is second["plan"]
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PLA", "COLOR": "0a0a0a"})
    assert rig.module.get_status()["spools"]["a:1"]["color"] == "#0a0a0a"
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.tracker.plan.tools)}
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    p = rig.module.get_status()["plan"]
    assert p["set"] is True and p["lanes"][0]["colour"] == "#0a0a0a"
    rig.module.tracker.plan = None  # a plan dropped
    assert rig.module.get_status()["plan"] is None


def test_the_adapter_writes_no_state_file(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    rig.adapter.set_current(rig.adapter.slot_of_head(0), rig.adapter.slot_of_head(0).lanes[1])
    assert not (tmp_path / "ace2k_u1_state.json").exists()


def test_a_restart_knows_no_lane_in_a_shared_head(tmp_path: Path) -> None:
    (tmp_path / "ace2k_u1_state.json").write_text('{"heads": {"1": "a:2"}}')
    rig, _ = tools_rig(tmp_path)
    assert rig.adapter.slot_of_head(0).current is None


def test_a_spool_with_a_bad_colour_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    with pytest.raises(ValueError, match="COLOR must be 6 hex digits"):
        run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "white"})
    assert not (tmp_path / "ace2k_u1_spools.json").exists()


def test_a_spool_of_an_unknown_lane_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    with pytest.raises(ValueError):
        run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "5", "TYPE": "PLA", "COLOR": "ffffff"})
    with pytest.raises(ValueError, match="no unit c"):
        run(rig, "ACEK_SPOOL", {"UNIT": "c", "LANE": "1", "TYPE": "PLA", "COLOR": "ffffff"})


def test_a_spool_without_its_type_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    with pytest.raises(ValueError, match="TYPE= is the material"):
        run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "COLOR": "ffffff"})
    assert not (tmp_path / "ace2k_u1_spools.json").exists()


def test_a_spool_needs_an_ace2k_unit_loaded(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    rig.module.units = lambda: None
    with pytest.raises(ValueError, match="no ace2k unit loaded"):
        run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ffffff"})


def test_a_spool_is_kept_while_the_adapter_is_not_hooked(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    rig.module.adapter = None
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ffffff"})
    assert rig.module.spool_store.records["a:2"]["type"] == "PLA"


def spools(rig: SimpleNamespace, colours: dict[str, str]) -> None:
    for key, colour in colours.items():
        unit, lane = key.split(":")
        run(rig, "ACEK_SPOOL", {"UNIT": unit, "LANE": lane, "TYPE": "PLA", "COLOR": colour})


EIGHT = {
    "a:1": "000000",
    "a:2": "ffffff",
    "a:3": "d32c34",
    "a:4": "ffff0a",
    "b:1": "21ff06",
    "b:2": "0000ff",
    "b:3": "fd8008",
    "b:4": "800080",
}


def test_the_plan_suggests_a_lane_per_tool_and_counts_the_swaps(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    p = rig.module.get_status()["plan"]
    lanes = ["a:1", "a:3", "a:2", "b:1", "a:4", "b:4", "b:2", "b:3"]
    assert [t["lane"] for t in p["tools"]] == lanes
    assert p["set"] is False and p["heads"][0] > 0 and p["heads"][1] == 0
    assert p["file"] == "eight.gcode" and p["choice"] is None
    assert not {"path", "kept"} & set(p)
    # the file's T order, so the page recounts the swaps of a changed choice
    assert (
        p["order"] == rig.module.tracker.plan.order
        and p["order"] is not rig.module.tracker.plan.order
    )


def test_the_plan_publishes_each_hooked_lanes_spool(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    lanes = rig.module.get_status()["plan"]["lanes"]
    assert lanes[:3] == [
        {
            "key": "a:1",
            "head": 0,
            "colour": "#000000",
            "colour_name": "black",
            "material": "PLA",
            "empty": False,
        },
        {
            "key": "a:2",
            "head": 0,
            "colour": "#ffffff",
            "colour_name": "white",
            "material": "PLA",
            "empty": False,
        },
        {
            "key": "a:3",
            "head": 1,
            "colour": "#d32c34",
            "colour_name": "red",
            "material": "PLA",
            "empty": False,
        },
    ]
    assert sorted(ln["key"] for ln in lanes) == sorted(EIGHT)
    # an Edit spool between the plan and its choice: the choice publishes the new colour
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PLA", "COLOR": "0a0a0a"})
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    p = rig.module.get_status()["plan"]
    assert p["lanes"][0]["colour"] == "#0a0a0a"
    assert p["choice"] == {n: lane for n, lane in enumerate(full.values())}


def test_the_plan_set_line_names_each_tool_its_lane_and_its_head(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    head = {"a:1": 1, "a:2": 1, "a:3": 2, "a:4": 3, "b:1": 4, "b:2": 4, "b:3": 4, "b:4": 4}
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.tracker.plan.tools)}
    gcmd = run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    tools = ", ".join(f"{t}={lane} (head {head[lane]})" for t, lane in full.items())
    assert gcmd.lines == [f"ace2k_u1: plan of eight.gcode set: {tools}"]


def test_a_lanes_tag_comes_before_its_spool_and_the_head_setting_last(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path, lanes="a:1, a:2, a:3, a:4")
    ptc = fakes.FakePrintTaskConfig()
    ptc.status["filament_type"][2] = "PETG"
    ptc.status["filament_color_rgba"][2] = "00FF00FF"
    rig.module.printer.objects["print_task_config"] = ptc
    record = {"material": "PLA", "color_rgba": "FF0000FF"}
    by_name["a"].lanes[0]["tag"] = dict(state="read", uid="x", source="tag", record=record)
    spools(rig, {"a:1": "000000", "a:2": "ffffff"})
    lane = {h.lane: h for h in rig.adapter.hooked}
    spool = rig.module.spool_lanes.lane_spool
    assert spool(lane[0]) == ("a:1", "#ff0000", "PLA", False)  # the tag
    assert spool(lane[1]) == ("a:2", "#ffffff", "PLA", False)  # the ACEK_SPOOL record
    assert spool(lane[2]) == ("a:3", "#00ff00", "PETG", False)  # the U1's setting of its head
    assert spool(lane[3]) == ("a:4", None, None, False)  # nothing known


def test_a_group_lane_not_in_its_head_takes_no_head_setting(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    ptc = fakes.FakePrintTaskConfig()
    ptc.status["filament_type"][0] = "PLA"
    ptc.status["filament_color_rgba"][0] = "123456FF"
    rig.module.printer.objects["print_task_config"] = ptc
    a1 = next(h for h in rig.adapter.hooked if h.unit.name == "a" and h.lane == 0)
    assert rig.module.spool_lanes.lane_spool(a1) == ("a:1", None, None, False)
    rig.adapter.set_current(rig.adapter.slot_of(a1), a1)
    # in the head, but the head's setting was not written for it: not its spool
    assert rig.module.spool_lanes.lane_spool(a1) == ("a:1", None, None, False)
    rig.adapter.slot_of(a1).setting_for = a1  # its spool written to the head (a tag, say)
    assert rig.module.spool_lanes.lane_spool(a1) == ("a:1", "#123456", "PLA", False)


def test_a_lane_whose_bay_is_empty_is_never_suggested_nor_chosen(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    tools = rig.module.tracker.plan.tools
    n = next(i for i, t in enumerate(tools) if t["lane"] == "a:3")
    full = {f"T{i}": t["lane"] for i, t in enumerate(tools)}
    # a:3 alone feeds head 2: the U1's setting of its head, but its spool is out of its bay
    del rig.module.spool_store.records["a:3"]
    by_name["a"].lanes[2]["insert"] = True
    tick(rig)
    rig.task_config.status["filament_type"][1] = "PLA"  # written for its spool in the bay
    rig.task_config.status["filament_color_rgba"][1] = tools[n]["colour"][1:].upper() + "FF"
    tick(rig)
    a3 = next(h for h in rig.adapter.hooked if h.unit.name == "a" and h.lane == 2)
    assert rig.module.spool_lanes.lane_spool(a3) == ("a:3", tools[n]["colour"], "PLA", False)
    by_name["a"].lanes[2]["insert"] = False
    assert rig.module.spool_lanes.lane_spool(a3) == ("a:3", None, None, True)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    assert rig.module.tracker.plan.tools[n]["lane"] is None
    shown = {ln["key"]: ln for ln in rig.module.get_status()["plan"]["lanes"]}
    assert shown["a:3"] == {
        "key": "a:3",
        "head": 1,
        "colour": None,
        "colour_name": None,
        "material": None,
        "empty": True,
    }
    assert shown["a:1"]["empty"] is False
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    assert str(err.value) == f"ace2k_u1: T{n}: a:3 has no spool in its bay"
    assert rig.module.tracker.plan.set is False


def test_a_plan_outside_the_gcode_folder_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    with pytest.raises(ValueError, match="not a file of the G-code folder"):
        run(rig, "ACEK_PLAN", {"FILE": "../ace2k_u1_spools.json"})
    # a leading / is the G-code folder's own root, as the U1's start reads it: no file there
    with pytest.raises(ValueError, match="no file"):
        run(rig, "ACEK_PLAN", {"FILE": str(tmp_path / "gcodes" / "eight.gcode")})


def test_a_sibling_folder_with_the_same_prefix_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes2").mkdir()
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes2" / "eight.gcode")
    with pytest.raises(ValueError, match="not a file of the G-code folder"):
        run(rig, "ACEK_PLAN", {"FILE": "../gcodes2/eight.gcode"})


def test_a_hand_edited_spool_file_keeps_only_its_good_records(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "ace2k_u1_spools.json").write_text(
        json.dumps(
            {
                "a:1": {"vendor": "Generic", "type": "PLA", "color": "#000000"},
                "a:2": {"type": "PLA", "color": "white"},
                "a:3": {"color": "#ffffff"},
                "a:4": "PLA",
                "b:1": {"type": 3, "color": "#ffffff"},
            }
        )
    )
    rig, _ = tools_rig(tmp_path)
    assert list(rig.module.spool_store.records) == ["a:1"]
    assert "spool records dropped (bad shape): a:2, a:3, a:4, b:1" in caplog.text
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})  # never raises on the file's content


def test_a_spool_not_saved_is_the_gcode_error_and_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, {"a:1": "000000"})

    def fail() -> NoReturn:
        raise OSError("disk full")

    monkeypatch.setattr(rig.module.spool_store, "write", fail)
    with pytest.raises(ValueError, match="spool not saved: disk full"):
        run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PLA", "COLOR": "ffffff"})
    with pytest.raises(ValueError, match="spool not saved"):
        run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ffffff"})
    assert rig.module.spool_store.records == {
        "a:1": {"vendor": "Generic", "type": "PLA", "color": "#000000"}
    }


def test_the_published_plan_is_a_copy_and_json(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    published = rig.module.get_status()["plan"]
    json.dumps(published)
    rig.module.tracker.plan.tools[0]["lane"] = "b:4"
    rig.module.tracker.plan.lanes[0]["colour"] = "#123456"
    assert published["tools"][0]["lane"] == "a:1"
    assert published["lanes"][0]["colour"] == "#000000"


def test_the_plan_reads_each_units_status_once_per_command(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, EIGHT)
    reads = []
    for name, unit in by_name.items():
        real = unit.get_status
        unit.get_status = lambda eventtime, real=real, name=name: (
            reads.append(name) or real(eventtime)
        )
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    assert sorted(reads) == ["a", "b"]
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    reads.clear()
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    assert sorted(reads) == ["a", "b"]  # the material check and the lanes: one read per unit
    assert rig.module.tracker.plan.set is True


def test_a_quoted_file_name_with_a_space_is_read(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / "eight colours.gcode")
    run(rig, "ACEK_PLAN", {"FILE": '"eight colours.gcode"'})
    assert rig.module.get_status()["plan"]["file"] == "eight colours.gcode"
    run(rig, "ACEK_PLAN", {"FILE": "'eight colours.gcode'"})
    assert rig.module.get_status()["plan"]["file"] == "eight colours.gcode"


def test_a_file_the_plan_cannot_read_is_the_gcode_error(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "bare.gcode").write_text("G28\nT0\n")
    with pytest.raises(ValueError, match="bare.gcode: no slicer config block"):
        run(rig, "ACEK_PLAN", {"FILE": "bare.gcode"})
    assert rig.module.get_status()["plan"] is None


def test_a_plan_refused_for_its_file_leaves_the_previous_plan_as_it_was(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    before = rig.module.tracker.plan
    (tmp_path / "gcodes" / "bare.gcode").write_text("G28\nT0\n")
    with pytest.raises(ValueError, match="bare.gcode: no slicer config block"):
        run(rig, "ACEK_PLAN", {"FILE": "bare.gcode"})
    assert rig.module.tracker.plan is before and before.set is True
    assert rig.module.get_status()["plan"]["file"] == "eight.gcode"
    assert (
        len(print_start(rig)) == 1 and rig.module.tracker.plan_started is True
    )  # still the planned


def test_the_choice_needs_every_tool_and_the_same_file(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    with pytest.raises(ValueError, match="T1 has no lane"):
        run(rig, "ACEK_PLAN_SET", {"FILE": "eight.gcode", "T0": "a:1"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    with pytest.raises(ValueError, match="no plan of other.gcode"):
        shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / "other.gcode")
        run(rig, "ACEK_PLAN_SET", dict(full, FILE="other.gcode"))
    with pytest.raises(ValueError, match="T2: no hooked lane c:9"):
        run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode", T2="c:9"))
    assert rig.module.get_status()["plan"]["set"] is False
    os.utime(tmp_path / "gcodes" / "eight.gcode", (1, 1))
    with pytest.raises(ValueError, match="changed since"):
        run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    assert rig.module.get_status()["plan"]["set"] is False
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    assert rig.module.get_status()["plan"]["set"] is True
    assert set(rig.module.tracker.plan.choice.values()) <= set(rig.adapter.hooked)


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_no_plan_and_no_choice_while_a_print_is_on(tmp_path: Path, state: str) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    before = rig.module.tracker.plan
    rig.print_stats.state = state
    full = {f"T{n}": t["lane"] for n, t in enumerate(before.tools)}
    for command, params in (
        ("ACEK_PLAN", {"FILE": "eight.gcode"}),
        ("ACEK_PLAN_SET", dict(full, FILE="eight.gcode")),
    ):
        with pytest.raises(ValueError) as err:
            run(rig, command, params)
        assert str(err.value) == "ace2k_u1: a print is on: plan the next file after it"
        assert rig.module.tracker.plan is before and before.set is True


def test_a_new_plan_drops_the_choice(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    assert rig.module.get_status()["plan"]["set"] is False
    assert rig.module.tracker.plan.choice is None


def test_a_finished_print_drops_the_plan(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    print_start(rig)
    rig.print_stats.state = "printing"
    rig.module.watch_print(rig.reactor.now)
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.get_status()["plan"] is None


@pytest.mark.parametrize(
    "end_unload,state", [(True, "complete"), (False, "complete"), (True, "cancelled")]
)
def test_the_end_unload_takes_out_the_shared_heads_other_used_lanes(
    tmp_path: Path, end_unload: bool, state: str
) -> None:
    """The option "Unload the filament at the end" unloaded only each
    head's last lane; the other lanes of a shared head the print used stayed parked behind its
    splitter. With END_UNLOAD=1 in the plan they are ejected at the print's end (complete);
    the U1's own end unload takes each head's current lane."""
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode", END_UNLOAD="1" if end_unload else "0"))
    page_map(rig)
    print_start(rig)
    rig.print_stats.state = "printing"
    rig.module.watch_print(rig.reactor.now)
    for head, last in ((0, 1), (3, 3)):
        slot = rig.adapter.slot_of_head(head)
        rig.adapter.set_current(slot, slot.lanes[last])
    ejected = []
    rig.adapter.ejector.start_eject = lambda lane, unit=None: ejected.append((unit.name, lane + 1))
    rig.print_stats.state = state
    rig.module.watch_print(rig.reactor.now)
    rig.reactor.run_async()  # queued out of the watch tick, as the heaters' off is
    rig.reactor.run_timers()
    want = [("a", 1), ("b", 1), ("b", 2), ("b", 3)] if end_unload and state == "complete" else []
    assert sorted(ejected) == want


def end_unload_rig(tmp_path: Path) -> SimpleNamespace:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode", END_UNLOAD="1"))
    page_map(rig)
    print_start(rig)
    rig.print_stats.state = "printing"
    return rig


def test_the_end_unload_leaves_a_shared_head_with_no_lane_known(tmp_path: Path) -> None:
    rig = end_unload_rig(tmp_path)
    rig.module.watch_print(rig.reactor.now)
    for head in (0, 3):
        rig.adapter.slot_of_head(head).current = None  # a restart mid-print
    ejected = []
    rig.adapter.ejector.start_eject = lambda lane, unit=None: ejected.append((unit.name, lane + 1))
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    rig.reactor.run_async()
    rig.reactor.run_timers()
    assert ejected == []
    line = (
        "ace2k_u1: head 1: no lane known in it at the print's end: its other lanes stay —"
        " ACEK_EJECT each"
    )
    assert line in rig.gcode.lines


def test_the_end_unload_waits_out_the_u1_unloading_the_head(tmp_path: Path) -> None:
    rig = end_unload_rig(tmp_path)
    for head, last in ((0, 1), (3, 3)):
        slot = rig.adapter.slot_of_head(head)
        rig.adapter.set_current(slot, slot.lanes[last])
    rig.module.watch_print(rig.reactor.now)
    busy = {"on": True}
    rig.adapter.ejector.eject_refusal = lambda h: (
        "the U1 is loading/unloading this head" if busy["on"] else None
    )
    ejected = []
    rig.adapter.ejector.start_eject = lambda lane, unit=None: ejected.append((unit.name, lane + 1))
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    rig.reactor.run_async()
    rig.reactor.run_timers()
    assert ejected == []  # waiting
    busy["on"] = False
    rig.reactor.run_timers(rig.reactor.now + ace2k_u1.END_UNLOAD_RETRY_S)
    assert sorted(ejected) == [("a", 1), ("b", 1), ("b", 2), ("b", 3)]


def test_the_end_unload_stops_when_a_new_print_starts(tmp_path: Path) -> None:
    rig = end_unload_rig(tmp_path)
    for head, last in ((0, 1), (3, 3)):
        slot = rig.adapter.slot_of_head(head)
        rig.adapter.set_current(slot, slot.lanes[last])
    rig.module.watch_print(rig.reactor.now)
    rig.adapter.ejector.eject_refusal = lambda h: "the U1 is loading/unloading this head"
    ejected = []
    rig.adapter.ejector.start_eject = lambda lane, unit=None: ejected.append((unit.name, lane + 1))
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    rig.reactor.run_async()
    rig.reactor.run_timers()
    rig.adapter.ejector.eject_refusal = lambda h: None
    rig.print_stats.state = "printing"  # the next print, within the wait
    rig.reactor.run_timers(rig.reactor.now + ace2k_u1.END_UNLOAD_RETRY_S)
    assert ejected == []


def test_the_watch_ticks_the_plan_and_an_idle_printer_keeps_it(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.print_stats.state = "complete"  # an older print's end, before the plan's print started
    rig.adapter.watch(rig.reactor.now)
    assert rig.module.get_status()["plan"] is not None
    print_start(rig)
    rig.adapter.watch(rig.reactor.now)  # the start's tick may still read the older end
    assert rig.module.get_status()["plan"] is not None
    rig.print_stats.state = "printing"
    rig.adapter.watch(rig.reactor.now)
    rig.print_stats.state = "cancelled"
    rig.adapter.watch(rig.reactor.now)
    assert rig.module.get_status()["plan"] is None


def test_a_print_of_another_file_neither_starts_nor_drops_the_plan(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.print_stats.state = "printing"  # a print the page did not start (no start through it)
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.tracker.plan_started is False
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.tracker.plan is not None


def test_a_restart_has_no_plan(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    rig2, _ = tools_rig(tmp_path)
    assert rig2.module.get_status()["plan"] is None


def test_a_relative_config_path_keeps_the_spools_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = tools_rig(tmp_path)
    monkeypatch.chdir(tmp_path)
    rig.module.printer.start_args = {"config_file": "printer.cfg"}
    module = ace2k_u1.ACE2kU1(LanesConfig(rig.module.printer, {"lanes": "a:1, a:2, b:1, b:2"}))
    assert module.spool_store.path == str(tmp_path.resolve() / "ace2k_u1_spools.json")


# --- the start check --------------------------------------------------------------------------


def print_start(
    rig: SimpleNamespace,
    command: str = "SDCARD_PRINT_FILE",
    name: str = "eight.gcode",
    **params: str,
) -> list[GCodeCommand]:
    calls = []
    rig.gcode.commands_orig[command] = calls.append
    gcmd = Gcmd(dict(params, FILENAME=name), command)
    rig.gcode.commands[command](gcmd)
    return calls


def page_map(rig: SimpleNamespace) -> None:
    """The U1's tool table as the page's Print sets it: each tool on its chosen lane's head."""
    for n, h in rig.module.tracker.plan.choice.items():
        rig.task_config.map_table[n] = h.head


def set_plan(rig: SimpleNamespace, name: str = "eight.gcode") -> None:
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": name})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_PLAN_SET", dict(full, FILE=name))
    page_map(rig)


def lanes_named(rig: SimpleNamespace) -> None:
    """Each shared head's lane named (ACEK_LANE_ACTIVE): a print with no plan starts on it."""
    for head in (0, 3):
        slot = rig.adapter.slot_of_head(head)
        rig.adapter.set_current(slot, slot.lanes[0])


def test_a_planned_start_whose_table_sends_a_tool_to_another_head_is_refused(
    tmp_path: Path,
) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.task_config.map_table[6] = 0  # T6's lane b:2 feeds head 4
    with pytest.raises(ValueError) as err:
        print_start(rig)
    assert str(err.value) == (
        "ace2k_u1: T6 goes to head 1 but its lane b:2 feeds head 4: start it from the ace2k page"
    )
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == [] and rig.units_obj.handed == []
    assert rig.module.tracker.plan_started is False


def test_the_choice_refuses_a_lane_of_another_material(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_SPOOL", {"UNIT": "b", "LANE": "2", "TYPE": "PETG", "COLOR": "0000ff"})
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    assert str(err.value) == "ace2k_u1: T6: b:2 holds PETG, the file wants PLA"
    assert rig.module.tracker.plan.set is False and rig.module.tracker.plan.choice is None
    # a lane with no material set (no tag, no Edit spool) is refused too
    del rig.module.spool_store.records["b:2"]
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    assert str(err.value) == "ace2k_u1: T6: b:2 has no material set: set it with Edit spool"
    assert rig.module.tracker.plan.set is False
    # the same material in another case, or with spaces, is the same
    run(rig, "ACEK_SPOOL", {"UNIT": "b", "LANE": "2", "TYPE": "pla", "COLOR": "0000ff"})
    rig.module.spool_store.records["b:2"]["type"] = " pla "
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    assert rig.module.tracker.plan.choice[6].lane == 1


@pytest.mark.parametrize("command", ["SDCARD_PRINT_FILE", "SDCARD_PRINT_FILE_WITH_PARAMETERS"])
def test_a_shared_file_without_its_plan_is_refused(tmp_path: Path, command: str) -> None:
    rig, _ = tools_rig(tmp_path)
    rig.task_config.map_table = [0, 1, 2, 3, 0, 3, 3, 3] + [0] * 24  # T4 on head 1 with T0
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig, command)
    assert rig.gcode.orig_calls(command) == []


def test_the_refusal_names_the_ace2k_page(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)  # the default table: T4-T7 on head 1 with T0
    with pytest.raises(ValueError) as err:
        print_start(rig)
    assert str(err.value) == (
        "ace2k_u1: this file shares heads between tools: start it from the ace2k page"
    )


def test_a_plan_not_set_does_not_let_its_file_start(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig)
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []


def test_a_plan_set_for_another_file_does_not_let_this_one_start(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / "other.gcode")
    set_plan(rig, "other.gcode")
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig)
    assert rig.units_obj.handed == []


@pytest.mark.parametrize("command", ["SDCARD_PRINT_FILE", "SDCARD_PRINT_FILE_WITH_PARAMETERS"])
def test_a_file_with_one_tool_per_head_starts_as_before(tmp_path: Path, command: str) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1, a:2, a:3, a:4")
    stub = rig.gcode.commands[command]
    calls = print_start(rig, command)
    assert len(calls) == 1
    # without a + group neither start is wrapped: the U1's own command, unchanged
    assert rig.gcode.commands[command] is stub


def test_several_tools_on_a_head_with_one_lane_start_as_before(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    lanes_named(rig)
    rig.task_config.map_table = [0, 1, 2, 3, 1, 1, 2, 2] + [1] * 24  # heads 2 and 3: one lane
    calls = print_start(rig)
    assert len(calls) == 1 and rig.gcode.orig_calls("SDCARD_PRINT_FILE") == calls
    assert rig.units_obj.handed == []


def test_with_parameters_the_start_reads_its_own_map_table(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)  # the table as set shares head 1: not the one this start uses
    lanes_named(rig)
    command = "SDCARD_PRINT_FILE_WITH_PARAMETERS"
    calls = print_start(rig, command, MAP_TABLE="[[4,1],[5,1],[6,2],[7,2]]")
    assert len(calls) == 1
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig, command, MAP_TABLE="[[4,1],[5,3],[6,2],[7,2]]")  # T5 on head 4 with T3
    rig.task_config.map_table = [0, 1, 2, 3, 1, 1, 2, 2] + [1] * 24
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig, command)  # no MAP_TABLE: the default table, T4-T7 on head 1


FIVE_TWO_USED = """; EXECUTABLE_BLOCK_START
T0
G1 X1
T1
G1 X2
; EXECUTABLE_BLOCK_END
; CONFIG_BLOCK_START
; filament_colour = #000000;#D32C34;#FFFFFF;#21FF06;#FFFF0A
; filament_type = PLA;PLA;PLA;PLA;PLA
; CONFIG_BLOCK_END
"""


def test_the_start_counts_only_the_tools_the_file_prints(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)  # the default table: T4 on head 1 with T0
    lanes_named(rig)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    assert len(print_start(rig, name="five.gcode")) == 1  # only T0 and T1 print: heads 1, 2
    # a file with no T line: every tool of its config block counts
    (tmp_path / "gcodes" / "none.gcode").write_text(
        FIVE_TWO_USED.replace("T0\n", "").replace("T1\n", "")
    )
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig, name="none.gcode")


def head_one_named(rig: SimpleNamespace) -> None:
    """Head 1's lane named (ACEK_LANE_ACTIVE): a print with no plan starts on it."""
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])


@pytest.mark.parametrize("command", ["SDCARD_PRINT_FILE", "SDCARD_PRINT_FILE_WITH_PARAMETERS"])
def test_a_start_whose_tool_lands_on_a_head_with_no_filament_and_no_lane_is_refused(
    tmp_path: Path, command: str
) -> None:
    """A print started from Fluidd with the default table (one tool per
    head) printed T1-T3 on heads with nothing in them; the four lanes all feed head 1."""
    rig, _ = tools_rig(tmp_path, lanes="a:1+a:2+a:3+a:4")
    head_one_named(rig)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)  # T0 head 1, T1 head 2
    rig.task_config.status["filament_exist"] = [True, False, False, False]
    with pytest.raises(ValueError) as err:
        print_start(rig, command, name="five.gcode")
    assert str(err.value) == (
        "ace2k_u1: T1 prints on head 2, which has no filament and no lane: start it from the"
        " ace2k page"
    )
    assert rig.gcode.orig_calls(command) == []


def test_a_tool_on_a_head_with_filament_or_a_lane_starts(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1+a:2, a:3")  # a:3 alone feeds head 2
    head_one_named(rig)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.task_config.status["filament_exist"] = [True, False, False, False]
    assert len(print_start(rig, name="five.gcode")) == 1  # head 2 has a lane
    rig, _ = tools_rig(tmp_path, lanes="a:1+a:2+a:3+a:4")
    head_one_named(rig)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.task_config.status["filament_exist"] = [True, True, False, False]
    assert len(print_start(rig, name="five.gcode")) == 1  # head 2 has filament
    del rig.task_config.status["filament_exist"]
    assert len(print_start(rig, name="five.gcode")) == 1  # not known: not refused


def test_a_start_whose_map_table_keeps_its_tools_off_an_empty_head_starts(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1+a:2+a:3+a:4")
    head_one_named(rig)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.task_config.status["filament_exist"] = [True, False, False, False]
    command = "SDCARD_PRINT_FILE_WITH_PARAMETERS"
    # T1 on head 1 with T0: no empty head, but head 1 is shared: the page's start
    with pytest.raises(ValueError, match="shares heads between tools"):
        print_start(rig, command, name="five.gcode", MAP_TABLE="[[1,0]]")
    # T1 on head 3, which has filament: starts
    rig.task_config.status["filament_exist"] = [True, False, True, False]
    assert len(print_start(rig, command, name="five.gcode", MAP_TABLE="[[1,2]]")) == 1


def test_a_start_with_no_plan_probes_a_shared_head_whose_lane_is_not_known(tmp_path: Path) -> None:
    """After a restart nothing names head 1's lane; its sensor set, the probe names
    it before the U1's start (five.gcode: T0 on head 1, T1 on head 2)."""
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.left.runout_sensor[1].detected = True
    probed = []
    rig.module.probe.which_lane = lambda slot: probed.append(slot.head) or slot.lanes[1]
    seen = []
    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = lambda gcmd: seen.append(probed[:])
    rig.gcode.commands["SDCARD_PRINT_FILE"](Gcmd({"FILENAME": "five.gcode"}, "SDCARD_PRINT_FILE"))
    assert seen == [[0]]  # the probe ran before the U1's start
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is slot.lanes[1] and rig.units_obj.handed == [("a", 1)]
    assert rig.module.tracker.plan_started is False


def test_a_start_with_no_plan_whose_probe_fails_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.left.runout_sensor[1].detected = True

    def fail(slot: Slot) -> NoReturn:
        raise ace2k_u1_probe.ProbeError("head 1: no lane of the head holds its filament")

    rig.module.probe.which_lane = fail
    with pytest.raises(ValueError) as err:
        print_start(rig, name="five.gcode")
    assert str(err.value) == "ace2k_u1: head 1: no lane of the head holds its filament"
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []  # nothing heated
    assert rig.adapter.slot_of_head(0).current is None


def test_a_start_with_no_plan_on_an_empty_shared_head_with_no_lane_known_is_refused(
    tmp_path: Path,
) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.module.probe.which_lane = lambda slot: pytest.fail("an empty head is not probed")
    with pytest.raises(ValueError) as err:
        print_start(rig, name="five.gcode")
    line = "ace2k_u1: head 1: no lane is known for it: choose one with ACEK_LANE_ACTIVE"
    assert str(err.value) == line
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []
    lanes_named(rig)  # named: the start goes on, the head untouched
    assert len(print_start(rig, name="five.gcode")) == 1
    assert rig.units_obj.handed == []


def test_the_choice_needs_a_lane_only_for_the_tools_the_file_prints(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    run(rig, "ACEK_PLAN", {"FILE": "five.gcode"})
    with pytest.raises(ValueError, match="T1 has no lane"):
        run(rig, "ACEK_PLAN_SET", {"FILE": "five.gcode", "T0": "a:1"})
    # T2-T4 are in the config block but never printed: no lane needed
    run(rig, "ACEK_PLAN_SET", {"FILE": "five.gcode", "T0": "a:1", "T1": "a:3"})
    assert rig.module.tracker.plan.set is True and sorted(rig.module.tracker.plan.choice) == [0, 1]
    # a lane given for an unused tool is checked all the same, and kept
    run(rig, "ACEK_PLAN", {"FILE": "five.gcode"})
    with pytest.raises(ValueError, match="T3: no hooked lane c:9"):
        run(rig, "ACEK_PLAN_SET", {"FILE": "five.gcode", "T0": "a:1", "T1": "a:3", "T3": "c:9"})
    run(rig, "ACEK_PLAN_SET", {"FILE": "five.gcode", "T0": "a:1", "T1": "a:3", "T3": "b:1"})
    assert sorted(rig.module.tracker.plan.choice) == [0, 1, 3]


def test_a_file_the_plan_module_cannot_read_starts_as_before(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "bare.gcode").write_text("G28\nT0\nT4\n")
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert len(print_start(rig, name="bare.gcode")) == 1
    # an ordinary file of another slicer: one line, no traceback
    line = (
        "ace2k_u1: bare.gcode: not planned (no slicer config block: slice the file with"
        " OrcaSlicer): started unchecked"
    )
    records = [r for r in caplog.records if "bare.gcode" in r.getMessage()]
    assert [(r.levelno, r.getMessage(), r.exc_info) for r in records] == [
        (logging.INFO, line, None)
    ]
    caplog.clear()
    assert len(print_start(rig, name="missing.gcode")) == 1  # unexpected: logged in full
    assert "missing.gcode unread at its start: not checked" in caplog.text


def test_with_its_plan_set_each_shared_head_takes_its_first_tools_lane(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    page_map(rig)
    calls = print_start(rig)
    assert len(calls) == 1
    assert rig.adapter.slot_of_head(0).current.lane == 0  # T0 (black, a:1) is head 1's first
    assert rig.adapter.slot_of_head(3).current.lane == 0  # T3 (green, b:1) is head 4's first
    assert ("a", 0) in rig.units_obj.handed and ("b", 0) in rig.units_obj.handed


def test_the_first_lanes_are_taken_before_the_start_runs(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    seen = []
    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = lambda gcmd: seen.append(
        list(rig.units_obj.handed)
    )
    rig.gcode.commands["SDCARD_PRINT_FILE"](Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE"))
    assert seen == [[("a", 0), ("b", 0)]]
    assert rig.units_obj.active == {"extruder": ("a", 0), "extruder3": ("b", 0)}


def typed_rig(tmp_path: Path) -> SimpleNamespace:
    """tools_rig with every bay full, the U1's filament table (PLA, PETG), and the U1's
    SET_PRINT_FILAMENT_CONFIG applied to its print_task_config."""
    rig, by_name = tools_rig(tmp_path)
    table = ("generic_PLA_generic_print_temp", "generic_PETG_generic_print_temp")
    rig.module.printer.objects["filament_parameters"] = fakes.FakeFilamentParameters(table)
    rig.gcode.run_script_from_command = fakes.FakeGcode(rig.task_config).run_script
    for unit in by_name.values():
        for lane in unit.lanes:
            lane["insert"] = True
    return rig


def test_a_planned_start_sets_each_unset_used_head_from_its_first_lane(tmp_path: Path) -> None:
    """Bench 2026-10-31: head 1 shared, its setting NONE, its first lane with a record. The U1's
    load at the start met NONE, was refused ("the head was set for none") and paused the print.
    Now each used head left unset is written from the lane it starts with, before the U1's start."""
    rig = typed_rig(tmp_path)
    set_plan(rig)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[1])  # a:2 named last, head 1's sensor empty
    status = rig.task_config.status
    status["filament_type"][:] = ["NONE"] * 4  # left NONE since the spools were saved
    status["filament_color_rgba"][:] = ["FFFFFFFF"] * 4
    status["filament_type"][2] = "PETG"  # head 3 set for another known type: left alone
    seen = []
    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = lambda gcmd: seen.append(
        (list(status["filament_type"]), list(status["filament_color_rgba"]))
    )
    rig.gcode.commands["SDCARD_PRINT_FILE"](Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE"))
    kinds, colours = seen[0]  # as the U1's start (and its loads) found them
    assert kinds == ["PLA", "PLA", "PETG", "PLA"]
    assert colours[0] == "000000FF" and colours[3] == "21FF06FF"  # a:1 black, b:1 green
    assert slot.current is slot.lanes[0] and slot.setting_for is slot.lanes[0]
    assert rig.module.material._head_differs(slot.lanes[0]) is None  # the load goes on


def test_a_planned_start_rewrites_a_shared_heads_setting_left_for_another_lane(
    tmp_path: Path,
) -> None:
    """Review: head 1 set (PETG) for no lane of its own while a:1 is its lane; the plan starts
    head 1 with a:1 (PLA): the start writes a:1's spool, so its load is not refused."""
    rig = typed_rig(tmp_path)
    set_plan(rig)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])  # a:1 named, the head empty
    slot.setting_for = None
    status = rig.task_config.status
    status["filament_type"][0], status["filament_color_rgba"][0] = "PETG", "D32C34FF"
    assert len(print_start(rig)) == 1
    assert (status["filament_type"][0], status["filament_color_rgba"][0]) == ("PLA", "000000FF")
    assert slot.setting_for is slot.lanes[0]
    assert rig.module.material._head_differs(slot.lanes[0]) is None  # the load goes on


def test_a_planned_start_with_a_lanes_status_unread_writes_its_record(tmp_path: Path) -> None:
    rig = typed_rig(tmp_path)
    set_plan(rig)
    status = rig.task_config.status
    status["filament_type"][:] = ["NONE"] * 4
    real = rig.adapter.lane_status
    calls = []

    def unread(h: Hooked, eventtime: float | None = None) -> object:
        if h.head == 0 and h.lane == 0:
            calls.append(h)
            raise RuntimeError("no status")
        return real(h, eventtime)

    rig.adapter.lane_status = unread
    assert len(print_start(rig)) == 1
    assert calls and status["filament_type"][0] == "PLA"
    assert status["filament_color_rgba"][0] == "000000FF"  # a:1's record


def test_a_planned_start_with_a_type_the_u1_does_not_list_writes_its_color_and_says_so(
    tmp_path: Path,
) -> None:
    rig = typed_rig(tmp_path)
    set_plan(rig)
    status = rig.task_config.status
    status["filament_type"][:] = ["NONE"] * 4
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "ASA", "COLOR": "d32c34"})
    assert len(print_start(rig)) == 1  # the start goes on: the load gate does not refuse it
    assert (status["filament_type"][1], status["filament_color_rgba"][1]) == ("NONE", "D32C34FF")
    line = (
        "ace2k_u1: head 2: a:3's record says ASA, which the U1's filament table does not list:"
        " only its color is set — set a listed type with Edit spool"
    )
    assert rig.gcode.lines.count(line) == 1


def test_a_start_lane_with_no_tag_and_no_record_is_not_written_nor_said(tmp_path: Path) -> None:
    rig = typed_rig(tmp_path)
    h = rig.adapter.slot_of_head(1).lanes[0]  # a:3, no spool set
    before = list(rig.gcode.lines)
    rig.module.wraps._write_start_settings([h])
    assert rig.gcode.lines == before
    assert rig.task_config.status["filament_color_rgba"][1] == "FFFFFFFF"


def test_a_lane_that_feeds_no_head_is_not_written_at_a_start(tmp_path: Path) -> None:
    rig = typed_rig(tmp_path)
    spools(rig, {"a:2": "d32c34"})
    h = rig.adapter.slot_of_head(0).lanes[1]
    rig.adapter.slots.pop((id(h.ff), h.ch))
    assert rig.module.wraps._start_settings([h]) == []


def test_a_planned_start_whose_head_setting_is_unread_writes_nothing(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.module.u1.head_settings = lambda: None  # the U1's settings cannot be read
    assert len(print_start(rig)) == 1
    assert rig.task_config.status["filament_type"] == ["NONE"] * 4


def test_a_quoted_name_with_a_space_starts_with_its_plan(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / "eight colours.gcode")
    set_plan(rig, '"eight colours.gcode"')
    assert len(print_start(rig, name='"/eight colours.gcode"')) == 1
    assert rig.units_obj.handed == [("a", 0), ("b", 0)]


def test_a_start_probes_a_shared_head_holding_a_filament(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.left.runout_sensor[1].detected = True
    probed = []
    rig.module.probe.which_lane = lambda slot: probed.append(slot.head) or slot.lanes[1]
    seen = []
    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = lambda gcmd: seen.append(probed[:])
    rig.gcode.commands["SDCARD_PRINT_FILE"](Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE"))
    assert seen == [[0]]  # the probe ran before the U1's start
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is slot.lanes[1]  # a:2 found: the head's lane; T0 (a:1) swaps it
    assert rig.units_obj.handed == [("a", 1), ("b", 0)]


def test_a_start_probe_that_finds_the_heads_lane_hands_nothing_over(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    slot = rig.adapter.slot_of_head(0)
    a2 = slot.lanes[1]
    rig.adapter.set_current(slot, a2)
    a2.ff.runout_sensor[a2.ch].detected = True
    rig.module.probe.which_lane = lambda slot: slot.lanes[1]
    assert len(print_start(rig)) == 1
    assert slot.current is a2  # the head's first T swaps it
    assert rig.units_obj.handed == [("b", 0)]


def loaded_in_head(rig: SimpleNamespace, head: int = 0, i: int = 1) -> Hooked:
    """Lane i of the head is made the head's lane and loaded by the U1 (its last load step, then
    load_finish)."""
    slot = rig.adapter.slot_of_head(head)
    h = slot.lanes[i]
    rig.adapter.set_current(slot, h)
    h.ff.runout_sensor[h.ch].detected = True
    h.ff._set_channel_state(h.ch, "load_flushing", True)
    h.ff._set_channel_state(h.ch, "load_finish", True)
    return h


def probe_spy(rig: SimpleNamespace) -> list[int]:
    probed: list[int] = []
    rig.module.probe.which_lane = lambda slot: probed.append(slot.head) or slot.current
    return probed


def test_a_start_skips_the_probe_of_a_head_whose_lane_is_known_since_its_load(
    tmp_path: Path,
) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    a2 = loaded_in_head(rig)
    probed = probe_spy(rig)
    assert len(print_start(rig)) == 1
    assert probed == []
    assert rig.adapter.slot_of_head(0).current is a2
    assert "ace2k_u1: head 1: a:2 in it since its last load: no probe" in rig.gcode.lines


@pytest.mark.parametrize("change", ["move", "lane"])
def test_a_start_probes_a_head_whose_lane_may_have_changed_since_its_load(
    tmp_path: Path, change: str
) -> None:
    """A move of a lane of the head, or another lane named in it."""
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    a2 = loaded_in_head(rig)
    slot = rig.adapter.slot_of_head(0)
    if change == "move":
        event = {"unit": "a", "kind": "done", "mode": "rolling_back", "seq": 9}
        rig.adapter.on_feed_event(0, dict(event, motor_mm=40.0, filament_mm=-40.0))
    else:
        rig.adapter.set_current(slot, slot.lanes[0])
        rig.adapter.set_current(slot, a2)
    probed = probe_spy(rig)
    assert len(print_start(rig)) == 1
    assert probed == [0]


def test_a_start_probes_a_head_one_of_whose_lanes_still_moves(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    set_plan(rig)
    loaded_in_head(rig)
    by_name["a"].lanes[0]["mode"] = "feeding"  # a:1 fed by hand, not yet ended
    probed = probe_spy(rig)
    assert len(print_start(rig)) == 1
    assert probed == [0]


def test_a_reverse_assist_of_a_lane_of_the_head_forgets_the_loaded_lane(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    loaded_in_head(rig)
    event = {"unit": "a", "kind": "stopped", "mode": "assisting_back", "seq": 9}
    rig.adapter.on_feed_event(1, dict(event, motor_mm=30.0, filament_mm=-30.0))
    assert rig.adapter.slot_of_head(0).loaded is None


def test_only_a_load_of_the_u1_marks_the_lane(tmp_path: Path) -> None:
    """load_finish entered from a state that is no load step (here preload_finish) is no load."""
    rig, _ = tools_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a2 = slot.lanes[1]
    rig.adapter.set_current(slot, a2)
    a2.ff._set_channel_state(a2.ch, "preload_finish", True)
    a2.ff._set_channel_state(a2.ch, "load_finish", True)
    assert slot.loaded is None
    a2.ff._set_channel_state(a2.ch, "load_extruding", True)
    a2.ff._set_channel_state(a2.ch, "load_finish", True)
    assert slot.loaded is a2


def test_a_u1_flow_on_the_channel_forgets_the_loaded_lane(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    a2 = loaded_in_head(rig)
    slot = rig.adapter.slot_of_head(0)
    assert slot.loaded is a2
    a2.ff._set_channel_state(a2.ch, "unload_prepare", True)
    assert slot.loaded is None


def test_a_load_finish_asserted_again_is_no_load(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a2 = slot.lanes[1]
    a2.ff.channel_state[a2.ch] = "load_finish"
    rig.adapter.set_current(slot, a2)
    a2.ff._set_channel_state(a2.ch, "load_finish", True)
    assert slot.loaded is None


def test_a_failed_start_probe_refuses_the_start(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.left.runout_sensor[1].detected = True

    def fail(slot: Slot) -> NoReturn:
        raise ace2k_u1_probe.ProbeError("head 1: no lane of the head holds its filament")

    rig.module.probe.which_lane = fail
    started = []
    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = lambda gcmd: started.append(True)
    with pytest.raises(ValueError) as err:
        rig.gcode.commands["SDCARD_PRINT_FILE"](
            Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE")
        )
    assert str(err.value) == "ace2k_u1: head 1: no lane of the head holds its filament"
    assert started == []
    assert rig.units_obj.handed == []  # every head probed before any is handed
    assert rig.adapter.slot_of_head(0).current is None


def test_a_start_with_a_shared_heads_sensor_unread_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, by_name = tools_rig(tmp_path)
    set_plan(rig)
    by_name["a"].link_proven = True
    real = rig.adapter.head_set

    def head_set(h: Hooked) -> bool:
        if h.head == 0:
            raise RuntimeError("no sensor")
        return real(h)

    monkeypatch.setattr(rig.adapter, "head_set", head_set)
    with pytest.raises(ValueError, match="ace2k_u1: head 1: its sensor cannot be read"):
        print_start(rig)
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []


def test_an_empty_shared_head_hands_its_first_lane_over_and_rolls_nothing_back(
    tmp_path: Path,
) -> None:
    rig, by_name = tools_rig(tmp_path)
    set_plan(rig)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[1])  # a:2 was last in head 1, its sensor now empty
    assert len(print_start(rig)) == 1
    assert a.feed.started == []
    assert rig.units_obj.handed == [("a", 0), ("b", 0)] and slot.current is slot.lanes[0]


def past_the_splitter(rig: SimpleNamespace, a: FakeAce2k) -> list[FakeMove]:
    """a:2's filament sits past head 1's splitter, short of its sensor: a:1's first push ends
    blocked; a:2's push sets the sensor, its roll back clears it; a:1's next push reaches it."""
    sensor = rig.left.runout_sensor[1]
    pushes = []

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            pushes.append(move)
            if len(pushes) == 1:
                move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 480.0}
            else:
                sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            sensor.detected = True
        elif move.mode == "rollback":
            if move.lane == 1:
                sensor.detected = False
            move.event = {"kind": "done", "seq": move.seq}

    a.link_proven = True
    a.lanes[0]["insert"] = a.lanes[1]["insert"] = True
    a.feed.on_start = on_start
    return pushes


def started_moves(a: FakeAce2k) -> list[tuple[int, str, float, float]]:
    return [(lane, mode, length, speed) for lane, mode, length, speed, _m in a.feed.started]


def test_the_start_load_into_an_empty_shared_head_finds_a_lane_in_the_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ace2k_u1_probe, "INSIST_TRIES", 0)  # a real obstacle
    rig, by_name = tools_rig(tmp_path)
    set_plan(rig)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[1])  # a:2 was last in head 1, past the splitter
    past_the_splitter(rig, a)
    loads = []
    # the U1's start loads head 1: its push into the drive
    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = lambda gcmd: loads.append(
        (list(a.feed.started), rig.left._put_into_drive(1))
    )
    rig.gcode.commands["SDCARD_PRINT_FILE"](Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE"))
    assert loads[0][0] == [] and loads[0][1] is None  # the start itself pushed nothing
    assert slot.current is slot.lanes[0]  # T0's a:1 handed over
    assert started_moves(a) == [
        (0, "feed", 2000.0, 70.0),  # blocked after 480 mm, at feed_speed
        (0, "rollback", 480.0, 80.0),
        (1, "feed", 2000.0, 50.0),  # a:2 sets the head's sensor: it was in the way
        (1, "rollback", 1000.0, 80.0),  # behind the splitter
        (0, "feed", 2000.0, 70.0),  # pushed again: at the sensor
    ]
    assert "ace2k_u1: head 1: a:2 was past the splitter: rolled back" in rig.gcode.lines
    assert rig.left.channel_error[1] == "ok"


def test_a_load_into_an_empty_shared_head_pushes_through_the_probe(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    a.lanes[0]["insert"] = True
    real = rig.module.probe.push_in
    calls = []

    def push_in(slot: Slot, h: Hooked, speed: float | None) -> str | None:
        calls.append((slot.head, h.lane, speed))
        return real(slot, h, speed)

    rig.module.probe.push_in = push_in
    rig.reactor.on_pause = [lambda now: setattr(rig.left.runout_sensor[1], "detected", True)]
    assert rig.left._put_into_drive(1) is None
    assert calls == [(0, 0, 70.0)]  # at feed_speed
    assert a.feed.started[0][:2] == (0, "feed")


def test_a_load_whose_probe_push_fails_is_the_u1s_general_error(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])

    def push_in(slot: Slot, h: Hooked, speed: float | None) -> NoReturn:
        raise ace2k_u1_probe.ProbeError("head 1: the push of a:1 is blocked in the tube")

    rig.module.probe.push_in = push_in
    with pytest.raises(RuntimeError, match="lane a:1 push to e0: general"):
        rig.left._put_into_drive(1)
    assert rig.left.channel_error[1] == "general"
    assert "ace2k_u1: head 1: the push of a:1 is blocked in the tube" in rig.gcode.lines


def test_a_spool_put_into_a_parked_lanes_bay_is_loaded_with_its_tag_search(tmp_path: Path) -> None:
    """No grip and no park on a shared head's bay: the unit's own load pulls the spool to the
    parking point and searches for its tag past it, then comes back to the parking point."""
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])  # a:1 in head 1
    rig.left.runout_sensor[1].detected = True
    a.lanes[1]["insert"] = True  # a spool put into a:2's bay
    rig.adapter.watch(rig.reactor.monotonic())
    assert not [g for g in a.feed.grips if g[0] == 1]


def test_at_boot_no_lane_of_a_group_is_gripped_and_a_spool_put_in_is_left_to_the_units_load(
    tmp_path: Path,
) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is None  # a restart knows no lane in the head
    rig.adapter.watch(rig.reactor.monotonic())
    assert not [g for g in a.feed.grips if g[0] in (0, 1)]
    second = slot.lanes[1]
    a.lanes[1].update(insert=True, mode="loading")  # a spool put in: the unit's own load
    rig.adapter.watch(rig.reactor.monotonic())
    rig.adapter.on_feed_event(
        1, {"kind": "loaded", "mode": "loading", "unit": "a", "seq": 0, "filament_mm": 300.0}
    )
    a.lanes[1]["mode"] = "idle"
    rig.reactor.run_timers()
    assert a.rfid.reads == []  # the load's own search read the tag: no read at its end
    assert a.feed.started == []  # no move of this adapter's
    assert not second.at_grip  # at the parking point: the probe rolls it back like any other


def test_a_parked_lane_of_a_shared_head_has_no_grip_with_its_sensor_empty(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    a.lanes[1]["insert"] = True
    rig.adapter.watch(rig.reactor.monotonic())
    assert not [g for g in a.feed.grips if g[0] in (0, 1, 2, 3)]


def grip_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if " grip " in r.getMessage() or " park: " in r.getMessage()
    ]


def test_a_shared_heads_lanes_log_no_park_and_no_grip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    rig, by_name = tools_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    rig.left.runout_sensor[1].detected = False  # head 1 empty
    by_name["a"].lanes[1]["insert"] = True
    rig.adapter.watch(rig.reactor.monotonic())
    rig.adapter.watch(rig.reactor.monotonic())
    assert not [ln for ln in grip_lines(caplog) if "lane a:1 " in ln or "lane a:2 " in ln]


def test_an_image_without_the_park_grips_no_shared_bay_and_says_nothing(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    a.feed.has_park = False
    rig.adapter.watch(rig.reactor.monotonic())
    rig.adapter.watch(rig.reactor.monotonic())
    assert not [g for g in a.feed.grips if g[0] in (0, 1)]
    assert not [ln for ln in rig.gcode.lines if "has no park" in ln]


def test_a_start_probe_whose_move_the_unit_refuses_refuses_the_start(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    set_plan(rig)
    a = by_name["a"]
    a.link_proven = True
    rig.left.runout_sensor[1].detected = True
    a.feed.refuse = "error"  # a refusal of the unit that is not busy (ace2k's FeedRefused)
    with pytest.raises(ValueError) as err:
        print_start(rig)
    assert str(err.value) == ("ace2k_u1: head 1: a:1's roll back was refused — refused: error")
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []
    assert rig.units_obj.handed == [] and a.feed.started == []
    assert rig.adapter.slot_of_head(0).current is None


def test_the_preload_of_a_head_not_reported_shared_pushes_through_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    monkeypatch.setattr(rig.units_obj, "shared_lanes", lambda unit, lane: None)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    a.lanes[0]["insert"] = True
    real = rig.module.probe.push_in
    calls = []

    def push_in(slot: Slot, h: Hooked, speed: float | None) -> str | None:
        calls.append((slot.head, h.lane, speed))
        return real(slot, h, speed)

    rig.module.probe.push_in = push_in
    rig.reactor.on_pause = [lambda now: setattr(rig.left.runout_sensor[1], "detected", True)]
    rig.left._do_feed(1, "preload")
    assert calls == [(0, 0, 70.0)]
    assert rig.left.channel_error[1] == "ok"
    assert rig.left.channel_state[1] == "preload_finish"


def test_a_preload_whose_probe_push_fails_is_the_u1s_general_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = tools_rig(tmp_path)
    monkeypatch.setattr(rig.units_obj, "shared_lanes", lambda unit, lane: None)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    rig.units["a"].lanes[0]["insert"] = True

    def push_in(slot: Slot, h: Hooked, speed: float | None) -> NoReturn:
        raise ace2k_u1_probe.ProbeError("head 1: the push of a:1 is blocked in the tube")

    rig.module.probe.push_in = push_in
    rig.left._do_feed(1, "preload")
    assert rig.left.channel_error[1] == "general"
    assert "ace2k_u1: head 1: the push of a:1 is blocked in the tube" in rig.gcode.lines


def test_a_preload_blocked_twice_is_the_u1s_general_error_not_a_quiet_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = tools_rig(tmp_path)
    monkeypatch.setattr(rig.units_obj, "shared_lanes", lambda unit, lane: None)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    rig.units["a"].lanes[0]["insert"] = True

    def push_in(slot: Slot, h: Hooked, speed: float | None) -> NoReturn:
        h.push_end = "blocked"  # the second push ended blocked: the probe raises
        raise ace2k_u1_probe.ProbeError("head 1: the push of a:1 is blocked again")

    rig.module.probe.push_in = push_in
    rig.left._do_feed(1, "preload")
    assert rig.left.channel_error[1] == "general"
    assert "ace2k_u1: head 1: the push of a:1 is blocked again" in rig.gcode.lines
    assert slot.lanes[0].push_end is None


def test_a_head_not_handed_refuses_the_start(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.units_obj.hand_over_fail = RuntimeError("ace2k: lane a:1 not handed the head: late")
    with pytest.raises(ValueError) as err:
        print_start(rig)
    assert str(err.value) == "ace2k_u1: head 1: ace2k: lane a:1 not handed the head: late"
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []
    assert rig.adapter.slot_of_head(0).current is None


def test_a_start_while_printing_reaches_the_u1_untouched(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.print_stats.state = "printing"
    assert len(print_start(rig)) == 1  # the U1's own refusal (SD busy) is its to raise
    assert rig.units_obj.handed == [] and rig.adapter.slot_of_head(0).current is None


def test_a_start_while_paused_is_checked(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    rig.print_stats.state = "paused"  # the U1 loads a new file while paused: checked in full
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig)
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []
    rig.print_stats.state = "standby"  # no plan while a print is on: planned before the pause
    set_plan(rig)
    rig.print_stats.state = "paused"
    assert len(print_start(rig)) == 1
    assert rig.units_obj.handed == [("a", 0), ("b", 0)]


def test_a_plan_set_for_a_file_changed_since_does_not_let_it_start(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    os.utime(tmp_path / "gcodes" / "eight.gcode", (1, 1))
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig)
    assert rig.units_obj.handed == []
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []


def test_the_start_matches_the_file_name_without_case(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    with pytest.raises(ValueError, match="start it from the ace2k page"):
        print_start(rig, name="EIGHT.GCODE")  # read as the folder's eight.gcode, and refused
    set_plan(rig)
    assert len(print_start(rig, name="/EIGHT.GCODE")) == 1
    assert rig.units_obj.handed == [("a", 0), ("b", 0)]


@pytest.mark.parametrize(
    ("planned_as", "started_as"),
    [
        ("./sub/x.gcode", "sub/x.gcode"),
        ("sub/X.GCODE", "/sub/x.gcode"),
        ("/sub/x.gcode", "SUB/../sub/x.gcode"),
    ],
)
def test_the_plan_and_the_start_read_one_name_however_typed(
    tmp_path: Path, planned_as: str, started_as: str
) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "sub").mkdir()
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / "sub" / "x.gcode")
    set_plan(rig, planned_as)
    assert rig.module.tracker.plan.file == "sub/x.gcode" and rig.module.tracker.plan.set is True
    assert len(print_start(rig, name=started_as)) == 1
    assert rig.module.tracker.plan_started is True
    assert rig.units_obj.handed == [("a", 0), ("b", 0)]


def test_the_start_name_is_read_from_its_own_folder_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = tools_rig(tmp_path)
    sub = tmp_path / "gcodes" / "sub"
    sub.mkdir()
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", sub / "Eight.gcode")
    (tmp_path / "gcodes" / "deep").mkdir()

    def no_walk(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("the G-code folder walked")

    monkeypatch.setattr(ace2k_u1_wraps.os, "walk", no_walk)
    sd_name = ace2k_u1_wraps.Wraps._sd_name
    root = str(tmp_path / "gcodes")
    assert sd_name(root, "sub/EIGHT.GCODE") == "sub/Eight.gcode"
    assert sd_name(root, "sub/Eight.gcode") == "sub/Eight.gcode"
    assert sd_name(root, "eight.gcode") == "eight.gcode"
    assert sd_name(root, "EIGHT.gcode") == "eight.gcode"
    assert sd_name(root, "missing.gcode") == "missing.gcode"
    assert sd_name(root, "nowhere/x.gcode") == "nowhere/x.gcode"  # its folder cannot be listed


# --- the tool commands ------------------------------------------------------------------------


def planned(tmp_path: Path) -> tuple[SimpleNamespace, dict[str, FakeAce2k]]:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}
    run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    page_map(rig)
    print_start(rig)
    rig.print_stats.state = "printing"
    rig.module.watch_print(rig.reactor.now)
    rig.swaps = []
    rig.module.swapper.swap = lambda gcmd, slot, h, n: (
        rig.swaps.append((slot.head, h.lane, n)) or True
    )
    return rig, by_name


def tool(
    rig: SimpleNamespace, n: int, params: dict[str, str] | None = None
) -> list[dict[str, str]]:
    calls = []
    rig.gcode.commands_orig[f"T{n}"] = lambda gcmd: calls.append(gcmd.params)
    rig.gcode.commands[f"T{n}"](Gcmd(dict(params or {}), f"T{n}"))
    return calls


def test_tool_command_whose_lane_is_in_its_head_passes_through(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    assert tool(rig, 0) == [{}]  # T0 is a:1, head 1's current lane since the start
    assert rig.swaps == []


def test_tool_command_for_another_lane_of_the_head_swaps_first(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    assert tool(rig, 2) == [{}]  # T2 is a:2 (white), head 1 holds a:1
    assert rig.swaps == [(0, 1, 2)]


def test_tool_command_with_a0_or_without_a_plan_passes_through(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    assert tool(rig, 2, {"A": "0"}) == [{"A": "0"}]
    rig.module.tracker.plan = None
    assert tool(rig, 2) == [{}]
    assert rig.swaps == []


def test_tool_command_on_a_one_lane_head_passes_through(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    assert tool(rig, 1) == [{}]  # T1 is a:3, head 2 has one lane
    assert rig.swaps == []


def test_tool_command_outside_the_plans_print_or_without_a_lane_passes_through(
    tmp_path: Path,
) -> None:
    rig, _ = planned(tmp_path)
    assert tool(rig, 20) == [{}]  # the file has eight tools: T20 has no lane in the choice
    rig.module.tracker.plan_started = False
    assert tool(rig, 2) == [{}]
    rig.module.tracker.plan_started = True
    rig.module.tracker.plan.set = False
    assert tool(rig, 2) == [{}]
    assert rig.swaps == []


def test_a_tool_command_right_after_the_planned_start_swaps(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    print_start(rig)  # no watch tick between the start and the file's first T
    rig.swaps = []
    rig.module.swapper.swap = lambda gcmd, slot, h, n: (
        rig.swaps.append((slot.head, h.lane, n)) or True
    )
    assert tool(rig, 2) == [{}]
    assert rig.swaps == [(0, 1, 2)]


def test_a_tool_command_in_a_print_of_another_file_does_not_swap(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    lanes_named(rig)
    set_plan(rig)
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / "other.gcode")
    rig.task_config.map_table = [0, 1, 2, 3, 1, 1, 2, 2] + [1] * 24  # one tool per shared head
    assert len(print_start(rig, name="other.gcode")) == 1
    rig.print_stats.state = "printing"
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.tracker.plan_started is False
    rig.swaps = []
    rig.module.swapper.swap = lambda gcmd, slot, h, n: (
        rig.swaps.append((slot.head, h.lane, n)) or True
    )
    assert tool(rig, 2) == [{}]
    assert rig.swaps == []
    # nor is a temperature for a tool of the planned file kept
    temps = []
    rig.gcode.commands_orig["M104"] = lambda gcmd: temps.append(gcmd.params)
    rig.gcode.commands["M104"](Gcmd({"S": "170", "T": "2"}, "M104"))
    assert temps == [{"S": "170", "T": "2"}]


def test_a_new_start_while_paused_drops_the_swap_pending_of_the_print_before(
    tmp_path: Path,
) -> None:
    rig, _ = planned(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.module.swapper.swap_pending = SwapState(slot, slot.current, slot.lanes[-1], 2, step="push")
    rig.print_stats.state = "paused"
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / "other.gcode")
    rig.task_config.map_table = [0, 1, 2, 3, 1, 1, 2, 2] + [1] * 24
    assert len(print_start(rig, name="other.gcode")) == 1
    assert rig.module.tracker.plan_started is False
    assert rig.module.swapper.swap_pending is None
    assert tool(rig, 2) == [{}]  # the new print's T is no T of the old pending swap


def test_a_new_start_while_paused_drops_the_runouts_and_shared_ends_of_the_print_before(
    tmp_path: Path,
) -> None:
    rig, _ = planned(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    h = slot.current
    rig.module.refiller.runout_stopped = {0: {"lane": h, "emptied": False}}
    h.end_taken, h.end_declined, h.hold = True, "paused", True
    rig.module.swapper.tools_owed = [OwedTool(5, None)]
    rig.print_stats.state = "paused"
    shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / "other.gcode")
    rig.task_config.map_table = [0, 1, 2, 3, 1, 1, 2, 2] + [1] * 24
    assert len(print_start(rig, name="other.gcode")) == 1
    assert rig.module.refiller.runout_stopped == {} and rig.module.swapper.tools_owed == []
    assert (h.end_taken, h.end_declined, h.hold) == (False, None, False)


def test_a_planned_start_the_u1_refuses_leaves_the_plan_not_started(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)

    def refuse(gcmd: GCodeCommand) -> NoReturn:
        raise ValueError("SD busy")

    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = refuse
    with pytest.raises(ValueError, match="SD busy"):
        rig.gcode.commands["SDCARD_PRINT_FILE"](
            Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE")
        )
    assert rig.module.tracker.plan_started is False


def test_tool_command_that_does_not_swap_sends_nothing_to_any_unit(tmp_path: Path) -> None:
    rig, by_name = planned(tmp_path)
    for n in (0, 1, 3, 20):
        tool(rig, n)
    tool(rig, 2, {"A": "0"})
    for unit in by_name.values():
        assert unit.feed.started == [] and unit.feed.stopped == []


def test_tool_commands_are_wrapped_only_with_a_group(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1, a:2, a:3, a:4")
    assert all(rig.gcode.commands[f"T{n}"].__name__ == "handler" for n in range(32))
    assert rig.gcode.commands["RESUME"].__name__ == "handler"
    rig, _ = tools_rig(tmp_path)
    assert all(rig.gcode.commands[f"T{n}"].__name__ == "tool" for n in range(32))
    assert rig.gcode.commands["RESUME"].__name__ == "resume"


def test_tool_command_a_printer_lacks_is_skipped_and_logged_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = tools_rig(tmp_path)
    gcode = Gcode()
    for n in range(4):
        gcode.stub(f"T{n}")
    caplog.set_level(logging.INFO)
    caplog.clear()
    rig.module.wraps.wrap_tools(gcode)
    assert all(gcode.commands[f"T{n}"].__name__ == "tool" for n in range(4))
    assert "T4" not in gcode.commands
    lines = [r.getMessage() for r in caplog.records if "to wrap" in r.getMessage()]
    assert lines == ["ace2k_u1: no " + ", ".join(f"T{n}" for n in range(4, 32)) + " to wrap"]


# --- the swap ---------------------------------------------------------------------------------


# A failed swap's head while printing: the U1's own idle exit, the G-code state with no move;
# never a move over the print, because a hot nozzle may ooze onto the part.
PARKED = (
    "MOVE_TO_XY_IDLE_POSITION_EXTRUDER",
    "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0",
)


# a successful swap's end: the head selected with the U1's own T, the U1's own pre-extrusion
# (20 mm, fan, one clean, its idle exit), then the G-code state restored with no move; the next
# print move takes the head to the part
SUCCESS_EXIT = (
    "T0 A0",
    "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0",
    "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0",
)


def failing(rig: SimpleNamespace, prefix: str) -> None:
    """The U1 fails the scripts starting with prefix (after running them)."""
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        if script.startswith(prefix):
            raise RuntimeError("heater fault")
        real(script)

    rig.gcode.run_script_from_command = run_script


def reports_tick(rig: SimpleNamespace, a: FakeAce2k) -> None:
    """Each pause brings the unit's next sensors report."""
    rig.reactor.on_pause.append(lambda now: setattr(a, "reports", a.reports + 1))


def swap_rig(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:  # noqa: PLR0915 — the U1's macros, pause and resume, played in one rig
    rig, by_name = planned(tmp_path)
    del rig.module.swapper.swap  # the real one
    a = by_name["a"]
    a.feed.on_start = lambda move: setattr(move, "event", {"kind": "done", "seq": move.seq})
    # the free check after the unload reads the plunger as the probe does: the unit's link
    # proven, a sensors report per pause
    a.link_proven = True
    reports_tick(rig, a)
    rig.head_sensor = rig.left.runout_sensor[1]  # head 1 (index 0): the left feed's channel 1
    rig.head_sensor.detected = True
    sent = []
    # as measured on the U1: its unload takes the filament out of the hotend but leaves the tip
    # in the tube on the head's sensor; only the lane's roll back clears it (one that ends done
    # or runs out of its bay; rollback_clears False: one that leaves it set). The free check's
    # roll back before it leaves the sensor as it is (its final event carries no distance:
    # nothing freed is counted); a test that wants it otherwise sets the sensor itself
    rig.rollback_clears = True
    rig.in_free_check = False
    start_move = a.feed.start_move

    def start_and_clear(
        lane: int, mode: str, length: float, speed: float, **kw: bool | str
    ) -> FakeMove:
        move = start_move(lane, mode, length, speed, **kw)
        kind = (move.event or {}).get("kind")
        clears = rig.rollback_clears and not rig.in_free_check
        if mode == "rollback" and kind in ("done", "runout") and clears:
            rig.head_sensor.detected = False
        return move

    a.feed.start_move = start_and_clear
    free_check = rig.module.probe.free_check

    def marked_free_check(h: Hooked) -> float:
        rig.in_free_check = True
        try:
            return free_check(h)
        finally:
            rig.in_free_check = False

    rig.module.probe.free_check = marked_free_check
    rig.printer = rig.module.printer
    # the U1's macros as they move the heads' extruders: every line sent reaches them. The
    # print is printing, the slicer having retracted 10 mm before the T
    extruders = rig.printer.objects["extruder_list"]
    # head 1 (index 0): the left feed's channel 1
    # the print at G-code X120 Y80 Z12.2, the head's offsets in the base position
    rig.gcode_move = fakes.FakeGcodeMove(
        last=(120.5, 79.75, 12.4, 0.0), base=(0.5, -0.25, 0.2, 0.0)
    )
    rig.printer.objects["gcode_move"] = rig.gcode_move
    rig.u1 = fakes.U1Moves(
        dict(enumerate(extruders)), rig.print_stats, {0: (rig.left, 1)}, rig.gcode_move
    )
    rig.print_stats.state = "printing"
    extruders[0].printing_e_pos = -10.0

    # the U1's plain PAUSE (its INNER_PAUSE): head 1's printing_e_pos recorded here as it
    # was; the active head and the heads' targets recorded in INNER_RESUME's variables; its
    # 2 mm retraction added to the active head's printing_e_pos while printing; pause_resume
    # paused. On the U1 print_stats turns paused only once the SD loop has stopped, after the
    # T returns; here at once, for the tests
    rig.e_pos_at_pause = []
    rig.pause_resume = fakes.FakePauseResume()
    rig.printer.objects["pause_resume"] = rig.pause_resume
    rig.inner_resume = {}  # INNER_RESUME's variables

    def inner_pause() -> None:
        rig.e_pos_at_pause.append(extruders[0].printing_e_pos)
        active = rig.u1.active
        rig.inner_resume["last_extruder_index"] = str(active.extruder_num)
        rig.inner_resume["last_extruder_temp"] = f"{active.heater.target:g}"
        for ext in extruders:
            rig.inner_resume[f"extruder{ext.extruder_num}_temp"] = f"{ext.heater.target:g}"
        rig.u1.run("G1 E-2")  # _CLIENT_RETRACT, counted while printing
        rig.pause_resume.is_paused = True
        rig.print_stats.state = "paused"

    def run_script_from_command(script: str) -> None:
        for line in script.split("\n"):
            sent.append(line)
            rig.u1.run(line)
            words = line.split()
            if line == "PAUSE":
                inner_pause()
            elif words[:2] == ["SET_GCODE_VARIABLE", "MACRO=INNER_RESUME"]:
                name, value = words[2].split("=", 1)[1], words[3].split("=", 1)[1]
                rig.inner_resume[name] = value

    def u1_resume() -> None:
        """The U1's RESUME (its INNER_RESUME): the head its variables name selected again."""
        index = rig.inner_resume.get("last_extruder_index")
        if index is not None:
            rig.u1.active = extruders[int(index)]
        rig.pause_resume.is_paused = False
        rig.print_stats.state = "printing"

    rig.u1_resume = u1_resume

    rig.gcode.run_script_from_command = run_script_from_command
    rig.sent = sent
    # the U1's load leaves the loaded head the active one
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")
    # the U1's filament table: the types a head's setting may carry
    table = ("generic_PLA_generic_print_temp", "generic_PETG_generic_print_temp")
    rig.module.printer.objects["filament_parameters"] = fakes.FakeFilamentParameters(table)
    return rig, a


def test_a_swap_never_runs_the_u1s_load_nor_its_flush(tmp_path: Path) -> None:
    """The U1's load flushes by |printing_e_pos|; the swap's own load macro replaces it, so
    no flush of the U1's ever runs in a swap."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.u1.flush_retract == []
    # the load macro's purge primed the head: the U1's pre-extrusion (no purge) starts at 0
    # (the saved value plus the 20 mm it used to purge, capped at 0 as the U1 counts it)
    assert rig.u1.e_pos_at_preextrude == [0.0]


# the U1's own flows and the nozzle work the swap's macros now hold: none is the adapter's
NOT_THE_ADAPTERS = ("AUTO_FEEDING", "M109", "ROUGHLY_CLEAN", "G1 E", "M83")


def test_the_swap_runs_its_two_macros_and_no_u1_flow(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.sent == [
        "SAVE_GCODE_STATE NAME=ACEK_SWAP",
        "T0 A0",
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=220 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=220 MATERIAL=PLA",
        "M400",  # its moves run before the free check stops the old lane's follow
        rig.sent[4],  # SET_PRINT_FILAMENT_CONFIG … (the spool)
        "T0 A0",  # the load's head selected again, whatever the steps between left active
        "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA",
        *SUCCESS_EXIT,
    ]
    assert rig.sent[4].startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 ")
    assert not [ln for ln in rig.sent if ln.startswith(NOT_THE_ADAPTERS)]
    # The unload macro starts with the head's printing_e_pos untouched (the saved value,
    # as the U1's own unload ran it), the load macro with it at 0; the saved value back after each
    assert rig.u1.e_pos_at_macro == [-10.0, 0.0]
    # the U1's pre-extrusion (no purge) then starts at 0: the load macro's purge primed the head
    assert rig.u1.e_pos_at_preextrude == [0.0]


def test_a_successful_swap_ends_by_the_pre_extrusion_with_no_retraction_nor_return(
    tmp_path: Path,
) -> None:
    """The U1's pre-extrusion cleans once; the swap adds no retraction and no move back: the
    next print move takes the head to the part."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert not [ln for ln in rig.sent if ln.startswith("G1 E")]
    assert not [ln for ln in rig.sent if ln.startswith(("G1 X", "G1 Y", "G1 Z", "G91"))]
    assert tuple(rig.sent[-3:]) == SUCCESS_EXIT
    assert "ace2k_u1: head 1: a:2 in (0 s)" in rig.gcode.lines
    slot = rig.adapter.slot_of_head(0)
    assert slot.loaded is slot.current is slot.lanes[1]  # the next start does not probe it


def test_a_macro_that_fails_leaves_the_saved_extruder_position(tmp_path: Path) -> None:
    """An error inside either macro is the swap's failure at that step, and the head's
    printing_e_pos is back at the saved value before the pause."""
    for macro, step in (("ACEK_SWAP_UNLOAD", "unload"), ("ACEK_SWAP_LOAD", "load")):
        (tmp_path / step).mkdir()
        rig, a = swap_rig(tmp_path / step)
        a.feed.on_push = lambda rig=rig: setattr(rig.head_sensor, "detected", True)
        real = rig.gcode.run_script_from_command

        def run_script(script: str, macro: str = macro, real: Callable[[str], None] = real) -> None:
            real(script)  # the macro ran, its extruder moves counted, then failed
            if script.startswith(macro):
                raise RuntimeError("heater fault")

        rig.gcode.run_script_from_command = run_script
        assert tool(rig, 2) == []
        assert any(f"(T2) failed at {step}: heater fault" in ln for ln in rig.gcode.lines)
        assert rig.e_pos_at_pause[-1] == -10.0
        assert rig.u1.e_pos_at_macro[-1] == (-10.0 if step == "unload" else 0.0)


def test_a_held_old_lane_fails_the_swap_at_its_free_check(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True  # the gear still holds a:1: its plunger goes taut
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    assert tool(rig, 2) == []
    assert any("failed at free: a:1 is still held by head 1" in ln for ln in rig.gcode.lines)
    assert not [m for m in a.feed.started if m[1] == "feed"]  # a:2 never pushed
    assert [(lane, mode) for lane, mode, *_ in a.feed.started] == [
        (0, "assist_both"),  # a:1 follows the unload macro
        (0, "rollback"),
        (0, "assist_both"),  # held: the unload again, once
        (0, "rollback"),
    ]
    assert "ace2k_u1: head 1: a:1 is still held by head 1: unloading it again" in rig.gcode.lines
    assert sum(ln.startswith("ACEK_SWAP_UNLOAD") for ln in rig.sent) == 2
    assert rig.module.swapper.swap_pending.step == "free"
    # The unload macro's -70 mm is taken back, the saved value put back after it.
    assert rig.e_pos_at_pause == [-10.0]


def test_a_tip_held_after_the_unload_is_freed_by_a_second_unload(tmp_path: Path) -> None:
    """b:1 was held after the U1's unload; the unload run again
    freed it at once. The swap goes on, no pause."""
    rig, a = swap_rig(tmp_path)
    rollbacks = {"n": 0}

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            rollbacks["n"] += 1
            if rollbacks["n"] == 1:
                a.lanes[0]["pushed"] = True  # held after the first unload
                return
            a.lanes[0]["pushed"] = False
            rig.head_sensor.detected = False
        move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.module.swapper.swap_pending is None
    assert not any("failed" in ln for ln in rig.gcode.lines)
    assert sum(ln.startswith("ACEK_SWAP_UNLOAD") for ln in rig.sent) == 2


def test_a_second_unload_that_fails_says_so(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    real = rig.gcode.run_script_from_command
    unloads = {"n": 0}

    def run_script(script: str) -> None:
        real(script)
        if script.startswith("ACEK_SWAP_UNLOAD"):
            unloads["n"] += 1
            if unloads["n"] == 2:
                raise RuntimeError("heater fault")

    rig.gcode.run_script_from_command = run_script
    tool(rig, 2)
    assert any("failed at free: its unload again — heater fault" in ln for ln in rig.gcode.lines)


def test_a_resume_after_a_failed_free_check_unloads_again_with_no_probe(tmp_path: Path) -> None:
    """The swap failed at its free check (b:1 held); the RESUME
    waited for 60 °C and probed, though only the old lane could be in the head. It goes on from
    the unload, unprobed."""
    rig, a = swap_rig(tmp_path)
    held = {"on": True}

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback" and held["on"]:
            a.lanes[0]["pushed"] = True
            return
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = False
            rig.head_sensor.detected = False
        move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.module.swapper.swap_pending.step == "free"
    held["on"] = False
    rig.module.probe.which_lane = lambda slot: pytest.fail("the old lane never left: no probe")
    resume(rig)
    assert rig.module.swapper.swap_pending is None
    assert (
        "ace2k_u1: head 1: a:1 is still in it (the swap failed before the head emptied): its"
        " unload again, no probe"
    ) in rig.gcode.lines


def test_a_swaps_failure_is_an_error_line_after_the_pause_and_the_statuss_reason(
    tmp_path: Path,
) -> None:
    """The reason of an adapter's pause was a plain notice, soon scrolled
    away. It is an error line ("!! "), the last one, and the status's pause_reason, which a
    print's end clears."""
    rig, a = swap_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    tool(rig, 2)
    line = (
        "ace2k_u1: head 1: a:1 → a:2 (T2) failed at free: a:1 is still held by head 1 after a"
        " second unload"
    )
    assert rig.gcode.errors == [f"!! {line}"]
    assert rig.gcode.lines.count(line) == 1 and rig.gcode.lines[-1] == line
    assert rig.module.get_status()["pause_reason"] == line
    rig.module.print_over()
    assert rig.module.get_status()["pause_reason"] is None


@pytest.mark.parametrize("filled", [True, False])
def test_the_free_check_runs_only_after_an_unload(tmp_path: Path, filled: bool) -> None:
    """The free check follows the unload macro. A head whose sensor is empty at the
    swap runs no unload: no free-check move, no proven link needed, the whole roll back."""
    rig, a = swap_rig(tmp_path)
    rig.head_sensor.detected = filled
    a.link_proven = filled  # unproven: a free check would refuse to move
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.module.swapper.swap_pending is None
    cfg = rig.module.swap_cfg
    rollbacks = [
        (length, speed) for _l, mode, length, speed, _m in a.feed.started if mode == "rollback"
    ]
    free = [(cfg.probe_mm, cfg.probe_speed)] if filled else []
    assert rollbacks == free + [(cfg.splitter_mm[0], cfg.swap_speed)]
    assert any(ln.startswith("ACEK_SWAP_UNLOAD") for ln in rig.sent) is filled


def test_a_rerun_with_no_unload_forgets_an_earlier_free_check(tmp_path: Path) -> None:
    """An attempt whose free check freed 30 mm fails later; its steps run again from the unload
    with the head empty: no unload, no free check, the roll back the whole splitter length."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)

    def on_start(move: FakeMove) -> None:
        if rig.in_free_check:
            rig.head_sensor.detected = False
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -30.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    failing(rig, "ACEK_SWAP_LOAD")
    assert tool(rig, 2) == []
    state = rig.module.swapper.swap_pending
    assert state.freed_mm == 30.0
    rig.head_sensor.detected = False
    before = len(a.feed.started)
    for step in ("unload", "free", "roll back"):
        rig.module.swapper.swap_step(step, state)
    rollbacks = [
        length for _l, mode, length, _s, _m in a.feed.started[before:] if mode == "rollback"
    ]
    assert rollbacks == [rig.module.swap_cfg.splitter_mm[0]]


def test_the_roll_back_after_the_free_check_is_the_rest_of_the_splitter(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)

    def on_start(move: FakeMove) -> None:
        if rig.in_free_check:  # the tip leaves the head's sensor 30 mm into the free check
            rig.head_sensor.detected = False
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -30.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    tool(rig, 2)
    rollbacks = [length for _l, mode, length, _s, _m in a.feed.started if mode == "rollback"]
    assert rollbacks == [100.0, ace2k_u1_config.SPLITTER_DEFAULT_MM - 30.0]
    assert rig.module.swapper.swap_pending is None


def test_a_free_check_past_the_whole_splitter_rolls_nothing_more_back(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.splitter_mm[0] = 80.0
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)

    def on_start(move: FakeMove) -> None:
        if rig.in_free_check:
            rig.head_sensor.detected = False
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -95.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    tool(rig, 2)
    rollbacks = [length for _l, mode, length, _s, _m in a.feed.started if mode == "rollback"]
    assert rollbacks == [100.0]  # never a negative rest
    assert rig.adapter.slot_of_head(0).current.lane == 1


def test_a_head_empty_after_the_load_macro_fails_the_swap(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)  # on_push not set: nothing makes the sensor read filament
    # the push says it reached it
    rig.module.probe.push_in = lambda slot, h, speed, budget=None: None
    assert tool(rig, 2) == []
    line = "ace2k_u1: head 1: a:1 → a:2 (T2) failed at loaded: the head's sensor sees no filament"
    assert any(ln.startswith(line) for ln in rig.gcode.lines)
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent
    assert rig.module.swapper.swap_pending.step == "loaded"


def last_of_lane(log: list[tuple[object, ...]], lane: int) -> tuple[object, ...] | None:
    """The unit's last start or stop of lane in log, None without one."""
    entries = [entry for entry in log if entry[1] == lane]
    return entries[-1] if entries else None


def follow_spy(rig: SimpleNamespace, a: FakeAce2k) -> dict[str, bool]:
    """What follows when each swap macro starts, from now on: "old" — a:1's last start or stop
    at the unload macro is its follow's start; "new" — a:2 is the head's lane and its follow
    has started by the load macro."""
    seen = {}
    run = rig.gcode.run_script_from_command
    before = len(a.feed.log)

    def spy(script: str) -> None:
        log = a.feed.log[before:]
        if script.startswith("ACEK_SWAP_UNLOAD"):
            last = last_of_lane(log, 0)
            seen["old"] = last is not None and last[:3] == ("start", 0, "assist_both")
        if script.startswith("ACEK_SWAP_LOAD"):
            seen["new"] = rig.adapter.slot_of_head(0).current.lane == 1 and any(
                entry[:3] == ("start", 1, "assist_both") for entry in log
            )
        run(script)

    rig.gcode.run_script_from_command = spy
    return seen


def test_the_old_lane_follows_through_the_unload_macro_and_the_new_one_through_the_load(
    tmp_path: Path,
) -> None:
    """The old lane follows the unload macro's extrusion, armed by the swap itself
    before it; the new lane follows the load macro."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    old, new = rig.adapter.slot_of_head(0).lanes
    seen = follow_spy(rig, a)
    tool(rig, 2)
    assert seen == {"old": True, "new": True}
    assert new.follow_wanted is True and old.follow_wanted is False  # the roll back's own


def test_a_swap_resumed_from_its_unload_arms_the_old_lane_again_before_the_macro(
    tmp_path: Path,
) -> None:
    """The failure's put back stopped the old lane: the swap restarted from its unload (the
    probe finds a:1 in the head) arms it again before the unload macro."""
    rig, a = swap_rig(tmp_path)

    def held(move: FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True  # the gear still holds a:1
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = held
    assert tool(rig, 2) == []
    assert rig.module.swapper.swap_pending.step == "free"
    assert last_of_lane(a.feed.log, 0) == ("stop", 0)  # the put back's stop
    a.lanes[0]["pushed"] = False  # freed by hand during the pause
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    probe_finds(rig, 0)
    seen = follow_spy(rig, a)
    assert resume(rig) == [True]
    assert seen == {"old": True, "new": True}


def test_no_printing_temperature_known_loads_and_ends_at_the_load_temperature(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.module.tracker.plan.tools[2]["temp"] = None
    rig.module.tracker.plan.tools[2]["first_temp"] = None
    tool(rig, 2)
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=230 PRINT_TEMP=230 MATERIAL=PLA" in rig.sent
    line = (
        "ace2k_u1: head 1: no printing temperature known for T2: the load temperature, 230 °C,"
        " is kept"
    )
    assert line in rig.gcode.lines


def test_a_kept_temperature_is_dropped_only_after_the_load_macro_succeeded(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.module.tracker.plan.kept[2] = 200.0
    run_script = rig.gcode.run_script_from_command
    failing(rig, "ACEK_SWAP_LOAD")
    assert tool(rig, 2) == []
    assert rig.module.tracker.plan.kept[2] == 200.0  # the failed load used nothing
    rig.gcode.run_script_from_command = run_script
    probe_finds(rig, 1)
    assert resume(rig) == [True]
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=200 PRINT_TEMP=200 MATERIAL=PLA" in rig.sent
    assert 2 not in rig.module.tracker.plan.kept


class ReadKeys(LanesConfig):
    """A config that records every option read: Klipper refuses, at the start, an option of the
    section nothing read ("Option ... is not valid")."""

    def __init__(self, printer: Printer, values: Mapping[str, object]) -> None:
        super().__init__(printer, values)
        self.read = set()

    def get(self, name: str, default: object) -> object:
        self.read.add(name)
        return super().get(name, default)

    def getfloat(
        self,
        name: str,
        default: float | None,
        above: float | None = None,
        minval: float | None = None,
        maxval: float | None = None,
    ) -> object:
        self.read.add(name)
        return super().getfloat(name, default, above=above, minval=minval, maxval=maxval)

    def getint(
        self, name: str, default: int, minval: int | None = None, maxval: int | None = None
    ) -> object:
        self.read.add(name)
        return super().getint(name, default, minval=minval, maxval=maxval)


def test_swap_purge_mm_is_no_longer_read(tmp_path: Path) -> None:
    """The purge moved into the load macro: the key is not read, so Klipper refuses it as any
    option of the section nothing reads."""
    rig, _ = tools_rig(tmp_path, values={"swap_purge_mm": "5"})
    assert not hasattr(rig.module.swap_cfg, "swap_purge_mm")
    config = ReadKeys(rig.module.printer, {"swap_purge_mm": "5"})
    counts = ace2k_u1_config.lane_counts([(0, "a", 0), (0, "a", 1)])
    ace2k_u1_config.read_swap_keys(config, counts)
    assert "swap_speed" in config.read and "swap_purge_mm" not in config.read


@pytest.mark.parametrize(
    "macro, step", [("ACEK_SWAP_UNLOAD", "unload"), ("ACEK_SWAP_LOAD", "load")]
)
def test_a_failed_swap_leaves_the_head_parked_never_over_the_print(
    tmp_path: Path, macro: str, step: str
) -> None:
    """A hot nozzle that may still hold filament oozes onto the part."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    failing(rig, macro)
    assert tool(rig, 2) == []
    i = rig.sent.index("PAUSE")
    assert rig.sent[i - 2 : i] == [
        "MOVE_TO_XY_IDLE_POSITION_EXTRUDER",
        "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0",
    ]
    assert not [ln for ln in rig.sent if ln.startswith(("G1 X", "G1 Y", "G1 Z", "G91"))]
    assert any(f"failed at {step}: " in ln for ln in rig.gcode.lines)


def test_a_failed_swap_whose_idle_exit_fails_still_pauses_where_it_is(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    failing(rig, "ACEK_SWAP_LOAD")
    real = rig.gcode.run_script_from_command

    def idle_fails(script: str) -> None:
        if script == "MOVE_TO_XY_IDLE_POSITION_EXTRUDER":  # the put back's: the load failed
            raise RuntimeError("move out of range")
        real(script)

    rig.gcode.run_script_from_command = idle_fails
    assert tool(rig, 2) == []
    assert any("failed at load: " in ln for ln in rig.gcode.lines)
    assert any("the idle exit failed" in ln for ln in rig.gcode.lines)
    assert any("the head stays where it is" in ln for ln in rig.gcode.lines)
    # the G-code state still restored with no move, after the failed exit and before the PAUSE
    i = rig.sent.index("PAUSE")
    assert rig.sent[i - 1] == "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0"
    assert "MOVE_TO_XY_IDLE_POSITION_EXTRUDER" not in rig.sent[:i]


def test_a_failed_swap_with_z_not_homed_moves_no_axis(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.printer.objects["toolhead"].homed_axes = "xy"
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    failing(rig, "ACEK_SWAP_LOAD")
    assert tool(rig, 2) == []
    assert not [ln for ln in rig.sent if ln.startswith(("G1 X", "G1 Y", "G1 Z", "G91"))]


def test_a_swap_with_z_not_homed_ends_as_any_swap(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.printer.objects["toolhead"].homed_axes = "xy"
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert tuple(rig.sent[-3:]) == SUCCESS_EXIT
    assert "ace2k_u1: head 1: a:2 in (0 s)" in rig.gcode.lines


def test_a_swap_with_xy_not_homed_fails_before_anything_moves(tmp_path: Path) -> None:
    """X and Y not homed: the unload never starts — no head move, no lane move; the swap
    pauses at its unload with the line, the head left where it is."""
    rig, a = swap_rig(tmp_path)
    rig.printer.objects["toolhead"].homed_axes = ""
    started = len(a.feed.started)
    assert tool(rig, 2) == []
    assert len(a.feed.started) == started
    moving = ("ACEK_SWAP_", "MOVE_TO_XY_IDLE_POSITION_EXTRUDER", "G1 ", "G91", "T0 A0")
    assert not [ln for ln in rig.sent if ln.startswith(moving)]
    assert "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0" in rig.sent and "PAUSE" in rig.sent
    line = (
        "ace2k_u1: head 1: a:1 → a:2 (T2) failed at unload: head 1: XY not homed: the swap"
        " waits for the print's homing"
    )
    assert line in rig.gcode.lines
    # not homed, the put back tries no idle exit at all (none sent: moving, above)
    assert not [ln for ln in rig.gcode.lines if "the idle exit failed" in ln]


def test_a_swap_of_an_unknown_lane_with_xy_not_homed_probes_nothing(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.printer.objects["toolhead"].homed_axes = "z"
    rig.adapter.slot_of_head(0).current = None  # the head's lane not known
    asked = probe_finds(rig, 0)
    rig.head_sensor.detected = False  # empty: the swap would probe by pushing
    started = len(a.feed.started)
    assert tool(rig, 2) == []
    assert asked == [] and len(a.feed.started) == started
    assert any(
        "XY not homed: the swap waits for the print's homing" in ln for ln in rig.gcode.lines
    )


def preextrude(rig: SimpleNamespace, index: int, **params: str) -> int:
    """The file's SM_PRINT_PREEXTRUDE_FILAMENT INDEX=index; the U1's own calls it reached."""
    before = len(rig.gcode.orig_calls("SM_PRINT_PREEXTRUDE_FILAMENT"))
    name = "SM_PRINT_PREEXTRUDE_FILAMENT"
    rig.gcode.commands[name](Gcmd(dict(params, INDEX=str(index)), name))
    return len(rig.gcode.orig_calls(name)) - before


AGAIN = "ace2k_u1: head 1: pre-extrusion after the swap (T2)"


def done(rig: SimpleNamespace) -> list[bool]:
    """The U1's per-print record of the tools pre-extruded."""
    return rig.print_stats._config["print_job"]["preextrude_filament"]


def test_the_file_pre_extrusion_right_after_a_swap_is_skipped(tmp_path: Path) -> None:
    """The swap ran the U1's pre-extrusion itself: the file's call right after it, for the same
    head, does not reach the U1, forced or not; the next one does."""
    for n, forced in enumerate(({"FORCE": "1"}, {})):
        (tmp_path / str(n)).mkdir()
        rig, a = swap_rig(tmp_path / str(n))
        a.feed.on_push = lambda rig=rig: setattr(rig.head_sensor, "detected", True)
        tool(rig, 2)
        assert rig.gcode.lines.count(AGAIN) == 1  # said by the swap's last step
        assert done(rig)[2] is True  # the U1's record, set by its forced call (the fake plays it)
        assert preextrude(rig, 2, **forced) == 0
        assert preextrude(rig, 2) == 1


def test_a_mark_left_before_a_swap_never_skips_its_own_pre_extrusion(tmp_path: Path) -> None:
    """On the U1 the swap's own SM_PRINT_PREEXTRUDE_FILAMENT goes through the wrap: a mark left
    for the head (an earlier swap with no file pre-extrusion after it, then a refill with no T
    to clear it) must not skip it. The T clears the mark, so it is set here at the load."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    name = "SM_PRINT_PREEXTRUDE_FILAMENT"
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        if script.startswith("ACEK_SWAP_LOAD"):
            rig.module.tracker.preextrude_after = 0  # head 1's mark, left from before
        real(script)
        words = script.split()
        if words and words[0] == name:  # dispatched to the wrap, as the U1's gcode does
            params = dict(w.split("=", 1) for w in words[1:])
            rig.gcode.commands[name](Gcmd(params, name))

    rig.gcode.run_script_from_command = run_script
    before = len(rig.gcode.orig_calls(name))
    tool(rig, 2)
    assert len(rig.gcode.orig_calls(name)) == before + 1  # the U1's handler reached
    assert rig.module.tracker.preextrude_after == 0  # set again by this swap's success
    assert preextrude(rig, 2) == 0  # the file's call right after it is skipped


def test_the_pre_extrusion_without_a_swap_before_it_is_the_u1s(tmp_path: Path) -> None:
    rig, _a = swap_rig(tmp_path)
    done(rig)[0] = True
    tool(rig, 0)  # a:1 already in head 1: no swap
    assert preextrude(rig, 0) == 1
    assert done(rig)[0] is True
    assert AGAIN not in rig.gcode.lines


def test_the_pre_extrusion_of_another_head_or_after_another_t_reaches_the_u1(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    other = next(n for n, h in rig.module.tracker.plan.choice.items() if h.head != 0)
    assert preextrude(rig, other) == 1
    tool(rig, other)  # a later T: the mark is gone
    assert preextrude(rig, 2) == 1


def test_an_unreadable_pre_extrusion_after_a_swap_reaches_the_u1_and_spends_the_mark(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert preextrude(rig, 99) == 1  # no tool 99 in the U1's map
    assert rig.module.tracker.preextrude_after is None
    assert preextrude(rig, 2) == 1


def test_the_skip_mark_ends_with_the_print(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    assert preextrude(rig, 2) == 1


def test_a_pre_extrusion_that_fails_pauses_at_return_and_resume_runs_it_again(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    real = rig.gcode.run_script_from_command
    fails = ["SM_PRINT_PREEXTRUDE_FILAMENT"]

    def run_script(script: str) -> None:
        if fails and script.startswith(fails[0]):
            fails.clear()
            raise RuntimeError("heater fault")
        real(script)

    rig.gcode.run_script_from_command = run_script
    assert tool(rig, 2) == []
    assert any("(T2) failed at return" in ln for ln in rig.gcode.lines)
    assert rig.module.swapper.swap_pending.step == "return"
    assert rig.module.tracker.preextrude_after is None
    rig.sent.clear()
    assert resume(rig) == [True]
    assert rig.sent.count("SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0") == 1
    assert "ace2k_u1: head 1: a:2 in (0 s)" in rig.gcode.lines
    assert rig.module.swapper.swap_pending is None


def test_a_swap_retracts_nothing_and_ends_with_the_head_primed(tmp_path: Path) -> None:
    """No retraction of the swap's own. The load macro's purge primed the head, so the swap
    counts the 20 mm the U1's pre-extrusion used to purge: it starts, with no purge, at
    printing_e_pos 0 for any slicer retraction up to 20 mm, and a later pause, flush or resume
    does not retract the slicer's retraction from before the swap. It ends at the -0.5 of the
    U1's own retraction."""
    for saved in (0.0, -0.03, -10.0):
        rig, a = swap_rig(tmp_path)
        rig.printer.objects["extruder"].printing_e_pos = saved
        a.feed.on_push = lambda rig=rig: setattr(rig.head_sensor, "detected", True)
        tool(rig, 2)
        assert not [line for line in rig.sent if line.startswith("G1 E")]
        assert rig.u1.e_pos_at_preextrude == [0.0]
        assert rig.printer.objects["extruder"].printing_e_pos == -0.5


def test_the_u1_model_pre_extrudes_its_length(tmp_path: Path) -> None:
    """The U1's pre-extrusion extrudes LENGTH (20 mm by default), then retracts 0.5 mm and
    cleans, its cleans moving no filament; SOFT=1 is a string and leaves the length as it is."""
    for line, end in (
        ("SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1", -0.5),
        ("SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0", -10.5),
        ("INNER_PREEXTRUDE_FILAMENT SOFT=1 TEMP=230 LENGTH=0", -10.5),
        ("INNER_PREEXTRUDE_FILAMENT SOFT=1 TEMP=230", -0.5),
    ):
        rig, _a = swap_rig(tmp_path)
        rig.printer.objects["extruder"].printing_e_pos = -10.0
        rig.u1.run(line)
        assert rig.printer.objects["extruder"].printing_e_pos == end, line


def test_the_u1_model_ignores_an_auto_feeding_without_its_extruder(tmp_path: Path) -> None:
    rig, _a = swap_rig(tmp_path)
    for line in ("AUTO_FEEDING LOAD=1", "AUTO_FEEDING EXTRUDER= LOAD=1", "AUTO_FEEDING EXTRUDER=9"):
        rig.u1.run(line)
    assert rig.u1.flush_retract == []


def test_the_unload_takes_the_materials_temperature(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.unload_temps = {"PLA": 205.0}
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert (
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=205 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=220 MATERIAL=PLA"
        in rig.sent
    )


def test_no_printing_temperature_the_macros_take_the_lanes_load_temperature(tmp_path: Path) -> None:
    """With no printing temperature known, the unload macro, which ends heating toward it, is
    given the new lane's load temperature from the U1's filament table, before the roll back
    and the push; the load macro takes the same."""
    rig, a = swap_rig(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PETG", "COLOR": "ffffff"})
    fparams = rig.module.printer.objects["filament_parameters"]
    order = []
    script = rig.gcode.run_script_from_command
    rig.gcode.run_script_from_command = lambda text: order.append(text) or script(text)
    start_move = a.feed.start_move

    def start(lane: int, mode: str, length: float, speed: float, **kw: bool | str) -> FakeMove:
        order.append(mode)
        return start_move(lane, mode, length, speed, **kw)

    a.feed.start_move = start
    rig.module.tracker.plan.tools[2]["temp"] = None  # no printing temperature known
    rig.module.tracker.plan.tools[2]["first_temp"] = None
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    unload = (
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=220 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=245 MATERIAL=PLA"
    )
    assert unload in rig.sent
    assert sent_macro(rig, "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=245 PRINT_TEMP=245")
    assert ("load_temp", ("Generic", "PETG", "", 0.4, "standard")) in fparams.asked
    assert order.index(unload) < order.index("rollback")


def test_an_extruder_without_a_nozzle_diameter_is_taken_for_0_4_mm(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PETG", "COLOR": "ffffff"})
    fparams = rig.module.printer.objects["filament_parameters"]
    rig.module.swapper._extruder(0).nozzle_diameter = None
    rig.module.tracker.plan.tools[2]["temp"] = None  # no printing temperature known
    rig.module.tracker.plan.tools[2]["first_temp"] = None
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    unload = (
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=220 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=245 MATERIAL=PLA"
    )
    assert unload in rig.sent
    assert ("load_temp", ("Generic", "PETG", "", 0.4, "standard")) in fparams.asked


def test_the_new_lanes_load_temperature_falls_back_to_250(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    fparams = rig.module.printer.objects["filament_parameters"]

    def broken(*args: object) -> NoReturn:
        raise KeyError("no table")

    fparams.get_load_temp = broken
    rig.module.tracker.plan.tools[2]["temp"] = None  # no printing temperature known
    rig.module.tracker.plan.tools[2]["first_temp"] = None
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert sent_macro(
        rig, "ACEK_SWAP_UNLOAD HEAD=0 TEMP=220 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=250"
    )
    assert sent_macro(rig, "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=250 PRINT_TEMP=250")


def test_the_unload_reads_the_lanes_material_without_case(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.unload_temps = {"PLA": 220.0, "PETG": 240.0}
    record = {"material": "petg", "color_rgba": "FF0000FF"}
    a.lanes[0]["tag"] = dict(state="read", uid="x", source="tag", record=record)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert sent_macro(
        rig, "ACEK_SWAP_UNLOAD HEAD=0 TEMP=240 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=220"
    )


def test_a_material_not_listed_takes_the_u1s_unload_temperature(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PETG", "COLOR": "000000"})
    ptc = rig.task_config
    ptc.status["filament_vendor"][0] = "Generic"
    ptc.status["filament_type"][0] = "PETG"
    ptc.status["filament_sub_type"][0] = "Basic"
    fparams = rig.printer.objects["filament_parameters"]
    fparams.unload_temps["PETG"] = 255.0
    fparams.soft.add("PETG")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert sent_macro(
        rig, "ACEK_SWAP_UNLOAD HEAD=0 TEMP=255 SOFT=1 NOZZLE_DIAMETER=0.4 LOAD_TEMP=220"
    )
    asked = ("Generic", "PETG", "Basic", 0.4, "standard")
    assert ("unload_temp", asked) in fparams.asked and ("is_soft", asked) in fparams.asked


def test_without_the_u1s_filament_table_the_unload_takes_250(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.unload_temps = {}
    del rig.printer.objects["filament_parameters"]
    rig.module.tracker.plan.tools[2]["temp"] = None  # no printing temperature known
    rig.module.tracker.plan.tools[2]["first_temp"] = None
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert sent_macro(
        rig, "ACEK_SWAP_UNLOAD HEAD=0 TEMP=250 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=250"
    )


@pytest.mark.parametrize("setting, temp", [("PETG", 240), ("", 250)])
def test_an_unread_status_at_the_unload_falls_back_and_never_fails_the_swap(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, setting: str, temp: int
) -> None:
    """The lane's status unread: the head's setting's material, else the U1's unload
    temperature, else 250 (none here); said in the log, the swap goes on."""
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.unload_temps = {"PLA": 220.0, "PETG": 240.0}
    del rig.printer.objects["filament_parameters"]
    rig.task_config.status["filament_type"][0] = setting
    real = rig.module.spool_lanes.lane_spool

    def lane_spool(h: Hooked, units: UnitLanes | None = None) -> LaneSpool:
        if h.lane == 0:
            raise RuntimeError("status unread")
        return real(h, units)

    rig.module.spool_lanes.lane_spool = lane_spool
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    with caplog.at_level(logging.WARNING):
        tool(rig, 2)
    unload = f"ACEK_SWAP_UNLOAD HEAD=0 TEMP={temp} SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=220"
    assert sent_macro(rig, unload)
    assert rig.adapter.slot_of_head(0).current.lane == 1 and rig.module.swapper.swap_pending is None
    assert "a:1's spool unread for its material: status unread" in caplog.text


def test_a_swap_says_its_duration(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    sent = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        if script.startswith("ACEK_SWAP_LOAD"):
            rig.reactor.now += 42.0  # the load macro
        sent(script)

    rig.gcode.run_script_from_command = run_script
    start = rig.reactor.now
    tool(rig, 2)
    took = rig.reactor.now - start
    assert 42.0 <= took < 43.0
    assert f"ace2k_u1: head 1: a:2 in ({took:.0f} s)" in rig.gcode.lines


def resume(rig: SimpleNamespace) -> list[bool]:
    """The adapter's RESUME; the U1's own RESUME recorded, the print printing after it."""
    resumed = []

    def u1_resume(gcmd: GCodeCommand) -> None:
        resumed.append(True)
        rig.u1_resume()

    rig.gcode.commands_orig["RESUME"] = u1_resume
    rig.gcode.commands["RESUME"](Gcmd({}, "RESUME"))
    return resumed


def probe_finds(rig: SimpleNamespace, found: int | Exception) -> list[Slot]:
    """Head 1's probe stubbed (its own runs: test_probe): it names lane index found of the head,
    or raises found when it is an exception. Returns the slots it was asked about."""
    slot = rig.adapter.slot_of_head(0)
    asked = []

    def which_lane(s: Slot) -> Hooked:
        asked.append(s)
        if isinstance(found, Exception):
            raise found
        return slot.lanes[found]

    rig.module.probe.which_lane = which_lane
    return asked


def probe_error(msg: str) -> ace2k_u1_probe.ProbeError:
    return ace2k_u1_probe.ProbeError(msg)


def put_back_tail(temp: int = 220) -> list[str]:
    """What a failed swap sends last while printing: the parked exit, the plain PAUSE, then
    the INNER_RESUME variables (after the PAUSE, which records the heads' own)."""
    return [
        *PARKED,
        "PAUSE",
        f"SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=extruder0_temp VALUE={temp}",
        f"SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_temp VALUE={temp}",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0",
    ]


def ends(kind: str, **distances: float) -> Callable[[FakeMove], None]:
    return lambda move: setattr(move, "event", dict(distances, kind=kind, seq=move.seq))


def freed_then(rig: SimpleNamespace, kind: str, **distances: float) -> Callable[[FakeMove], None]:
    """An on_start in which the free check's roll back ends done (the old lane free) and every
    other move ends kind: a roll back that fails is the swap's own, after its free check."""

    def on_start(move: FakeMove) -> None:
        if getattr(rig, "in_free_check", False):  # only swap_rig marks it
            move.event = {"kind": "done", "seq": move.seq}
        else:
            move.event = dict(distances, kind=kind, seq=move.seq)

    return on_start


def test_swap_runs_the_steps_in_order(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)  # the push reaches it
    tool(rig, 2)  # the lines sent: test_the_swap_runs_its_two_macros_and_no_u1_flow
    modes = [(lane, mode, length) for lane, mode, length, _s, _m in a.feed.started]
    assert modes == [
        (0, "assist_both", 0.0),  # the old lane's follow, armed for the unload macro
        (0, "rollback", 100.0),  # the free check (probe_mm)
        (0, "rollback", 1000.0),
        (1, "feed", rig.adapter.head_budget_mm),
        (1, "assist_both", 0.0),  # the new lane's follow, armed at the head's sensor
    ]
    assert rig.units_obj.handed[-1] == ("a", 1)
    assert rig.adapter.slot_of_head(0).current.lane == 1
    assert "ace2k_u1: head 1: a:1 → a:2 (T2)" in rig.gcode.lines
    assert "ace2k_u1: head 1: a:2 in (0 s)" in rig.gcode.lines


def test_the_swaps_roll_back_has_a_free_tip_and_the_free_check_not(tmp_path: Path) -> None:
    """Host interface 14: the roll back after the free check found the tip out of the extruder
    forgives every catch past snag_free; the free check's own roll back, whose tip may still be
    in the head, keeps the one."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    lengths = [length for _l, mode, length, _s, _m in a.feed.started if mode == "rollback"]
    free = [tip for _l, mode, tip in a.feed.free_tips if mode == "rollback"]
    assert lengths == [100.0, 1000.0] and free == [False, True]


def test_a_unit_whose_image_ignores_a_rollbacks_free_tip_is_said_and_rolls_back_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_free_rollback", False)
    rig, a = swap_rig(tmp_path)
    said = (
        "the unit's firmware forgives one catch in a lane swap's roll back, not every one; flash"
        " v0.12.0"
    )
    # said at the hook, once per unit in use, named: the rig has two
    for name in ("a", "b"):
        assert f"ace2k_u1: ace2k {name}: {said}" in rig.gcode.lines
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert [tip for _l, mode, tip in a.feed.free_tips if mode == "rollback"] == [False, False]
    assert rig.module.swapper.swap_pending is None


def test_a_free_roll_back_waits_for_each_catch_it_may_forgive() -> None:
    snag = {"snag_fwd_mm": 20.0, "snag_back_mm": 5.0, "snag_rest_ms": 500, "snag_free_mm": 50.0}
    api = types.SimpleNamespace(snag=snag)
    h = types.SimpleNamespace(api=api, lane=0)  # a lane of a unit before host interface 17
    plain = ace2k_u1_rollback._rollback_wait_s(h, 950.0, 70.0, False)
    assert plain == pytest.approx(950.0 / 70.0 * 2.0 + 5.0)
    # the standstill's motor at 70 mm/s; the 5 mm touch, no longer than the unit's 10 mm landing,
    # whole at its 9 mm/s floor; the rest's wait
    each = 20.0 / 70.0 + 5.0 / 9.0 + 0.5
    assert ace2k_u1_rollback._rollback_wait_s(h, 950.0, 70.0, True) == pytest.approx(
        plain + 19 * each
    )
    # no feed.snag: ace2k's own values
    assert ace2k_u1_rollback._rollback_wait_s(
        types.SimpleNamespace(api=object(), lane=0), 950.0, 70.0, True
    ) == pytest.approx(plain + 19 * each)
    snag["snag_free_mm"] = 100.0  # fewer catches fit in the length
    assert ace2k_u1_rollback._rollback_wait_s(h, 950.0, 70.0, True) == pytest.approx(
        plain + 9 * each
    )


def test_a_free_roll_back_counts_the_touch_with_the_units_landing() -> None:
    snag = {"snag_fwd_mm": 20.0, "snag_back_mm": 15.0, "snag_rest_ms": 500, "snag_free_mm": 50.0}
    # the floor and the landing as the unit gives them
    api = types.SimpleNamespace(snag=snag, speed_min=10.0, landing_mm=8.0)
    h = types.SimpleNamespace(api=api, lane=0)
    plain = ace2k_u1_rollback._rollback_wait_s(h, 950.0, 70.0, False)
    # a 15 mm touch is longer than an 8 mm landing: 7 mm at 70 mm/s, the last 8 mm at 10 mm/s
    each = 20.0 / 70.0 + 7.0 / 70.0 + 8.0 / 10.0 + 0.5
    assert ace2k_u1_rollback._rollback_wait_s(h, 950.0, 70.0, True) == pytest.approx(
        plain + 19 * each
    )
    # a touch no longer than the landing runs whole at the floor
    snag["snag_back_mm"] = 8.0
    each = 20.0 / 70.0 + 8.0 / 10.0 + 0.5
    assert ace2k_u1_rollback._rollback_wait_s(h, 950.0, 70.0, True) == pytest.approx(
        plain + 19 * each
    )


def test_a_failed_swap_pauses_plainly_with_the_head_put_back(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    assert tool(rig, 2) == []  # no exception: a plain pause; the U1's T is not run
    # the unload left printing_e_pos 70 mm lower: the saved value is back before the PAUSE (the
    # U1's resume retracts by it)
    assert rig.e_pos_at_pause == [-10.0]
    assert rig.printer.objects["extruder"].printing_e_pos == -12.0  # the pause's own retraction
    assert rig.sent[-len(put_back_tail()) :] == put_back_tail()
    assert rig.print_stats.state == "paused"
    line = "ace2k_u1: head 1: a:1 → a:2 (T2) failed at roll back: a:1 ended stuck"
    assert line in rig.gcode.lines
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is slot.lanes[0]  # nothing handed over
    assert rig.module.swapper.swap_pending.step == "roll back"


@pytest.mark.parametrize("step", ["unload", "roll back", "push", "load"])
def test_a_failure_at_each_step_puts_the_head_back_then_pauses(tmp_path: Path, step: str) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    real = rig.gcode.run_script_from_command
    fails = {"unload": "ACEK_SWAP_UNLOAD", "load": "ACEK_SWAP_LOAD"}

    def run_script(script: str) -> None:
        real(script)  # the U1 ran it, its extruder moves counted, then failed
        if step in fails and script.startswith(fails[step]):
            raise RuntimeError("the U1 failed")

    rig.gcode.run_script_from_command = run_script
    if step == "roll back":
        a.feed.on_start = freed_then(rig, "stuck")
    elif step == "push":
        a.feed.on_push = None  # the push never reaches the head's sensor
        a.feed.on_start = lambda move: setattr(
            move, "event", {"kind": "stuck" if move.mode == "feed" else "done", "seq": move.seq}
        )
    assert tool(rig, 2) == []
    slot = rig.adapter.slot_of_head(0)
    h = slot.current
    # the head's lane follow stopped, and not re-armed during the pause
    assert a.feed.log[-1] == ("stop", h.lane) and h.follow_wanted is False
    assert rig.e_pos_at_pause == [-10.0]  # the saved value, whatever the U1 left
    assert rig.sent[-len(put_back_tail()) :] == put_back_tail()
    assert any(f"(T2) failed at {step}: " in line for line in rig.gcode.lines)
    assert rig.module.swapper.swap_pending.step == step


def test_resume_probes_finishes_the_swap_then_runs_the_t_then_the_u1s_resume(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2, {"A": "1"})
    order, t2 = [], []
    rig.gcode.commands_orig["T2"] = lambda gcmd: (order.append("T2"), t2.append(gcmd.params))
    rig.gcode.commands_orig["RESUME"] = lambda gcmd: order.append("RESUME")
    a.feed.on_start = lambda move: setattr(move, "event", {"kind": "done", "seq": move.seq})
    a.feed.on_push = lambda: (setattr(rig.head_sensor, "detected", True), order.append("push"))
    rig.head_sensor.detected = False  # the roll back had gone: the head is empty
    asked = probe_finds(rig, 0)
    rig.sent.clear()
    rig.gcode.commands["RESUME"](Gcmd({}, "RESUME"))
    assert order == ["push", "T2", "RESUME"]
    assert t2 == [{"A": "1"}]  # the T the print asked for, with its own parameters
    # the head's sensor empty: no probe roll back, the push looks for a lane in the way
    assert asked == []
    assert rig.adapter.slot_of_head(0).current.lane == 1
    assert rig.module.swapper.swap_pending is None
    # paused: no lift, and the restore does not move (the U1's RESUME moves back itself)
    assert "G91" not in rig.sent
    assert rig.sent[-1] == "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0"


def test_resume_with_the_old_lane_in_the_head_starts_the_swap_again_from_its_unload(
    tmp_path: Path,
) -> None:
    """The resumed roll back runs the whole splitter length: nothing of the first one is kept."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2)
    assert rig.head_sensor.detected  # the stuck roll back left the U1's tip on the sensor
    asked = probe_finds(rig, 0)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.sent.clear()
    started = len(a.feed.started)
    assert resume(rig) == [True]
    slot = rig.adapter.slot_of_head(0)
    assert asked == [slot]
    assert rig.sent[:2] == [
        "T0 A0",
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=220 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=220 MATERIAL=PLA",
    ]
    modes = [(lane, mode, length) for lane, mode, length, *_ in a.feed.started[started:]]
    assert modes == [
        (0, "assist_both", 0.0),  # a:1 armed again for the unload macro
        (0, "rollback", 100.0),  # the free check again
        (0, "rollback", 1000.0),
        (1, "feed", rig.adapter.head_budget_mm),
        (1, "assist_both", 0.0),
    ]
    assert slot.current is slot.lanes[1] and rig.module.swapper.swap_pending is None


def test_resume_with_the_new_lane_in_the_head_goes_on_from_its_spool(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = lambda move: setattr(
        move, "event", {"kind": "stuck" if move.mode == "feed" else "done", "seq": move.seq}
    )
    tool(rig, 2)
    assert "ace2k_u1: head 1: a:1 → a:2 (T2) failed at push: a:2 did not reach the head" in (
        " ".join(rig.gcode.lines)
    )
    assert rig.adapter.slot_of_head(0).current.lane == 1  # handed over before the push
    rig.head_sensor.detected = True  # the operator fed it to the head by hand
    asked = probe_finds(rig, 1)
    rig.sent.clear()
    started = len(a.feed.started)
    assert resume(rig) == [True]
    # The head read empty before the push, and only a:2 moved toward it: no probe (which would
    # wait for the head to cool), on from its spool
    assert asked == []
    assert NO_PROBE_LINE in rig.gcode.lines
    assert not any("waits for the head to cool" in line for line in rig.gcode.lines)
    # no push: a:2 is in the head; the lane's follow is armed for the load
    assert [mode for _l, mode, *_ in a.feed.started[started:]] == ["assist_both"]
    assert rig.sent[0].startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 ")
    assert rig.sent[1:3] == [
        "T0 A0",
        "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA",
    ]
    assert not [line for line in rig.sent if "UNLOAD" in line]


NO_PROBE_LINE = (
    "ace2k_u1: head 1: a:2 is in it (the head was empty before its push): on from its spool,"
    " no probe"
)


def test_a_push_short_of_the_sensor_whose_tip_arrives_later_goes_on_unprobed(
    tmp_path: Path,
) -> None:
    """The push ended short of the head's sensor ("distance"), and the tip set the sensor right
    after. The head was empty before the push, so the lane in it can only be a:2: the RESUME
    goes on from its spool with no probe, so no wait for the head to cool."""
    rig, a = swap_rig(tmp_path)
    rig.module.probe.push_in = lambda slot, h, speed, budget=None: "distance"
    assert tool(rig, 2) == []
    assert rig.module.swapper.swap_pending.step == "push"
    rig.head_sensor.detected = True  # the tip reached the sensor after the push gave up
    asked = probe_finds(rig, 0)
    rig.sent.clear()
    assert resume(rig) == [True]
    assert asked == [] and NO_PROBE_LINE in rig.gcode.lines
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is slot.lanes[1] and rig.module.swapper.swap_pending is None


def test_a_swap_failed_at_its_load_after_its_push_goes_on_unprobed(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    real = rig.gcode.run_script_from_command
    fails = ["ACEK_SWAP_LOAD"]

    def run_script(script: str) -> None:
        if fails and script.startswith(fails[0]):
            fails.clear()
            raise RuntimeError("heater fault")
        real(script)

    rig.gcode.run_script_from_command = run_script
    assert tool(rig, 2) == []
    assert rig.module.swapper.swap_pending.step == "load"
    asked = probe_finds(rig, 0)
    rig.sent.clear()
    assert resume(rig) == [True]
    assert asked == [] and NO_PROBE_LINE in rig.gcode.lines
    assert not [line for line in rig.sent if "UNLOAD" in line]
    assert rig.sent.count("ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA") == 1
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is slot.lanes[1] and rig.module.swapper.swap_pending is None


def test_a_push_whose_search_failed_is_still_probed_at_resume(tmp_path: Path) -> None:
    """A push that raised (its search for a lane in the way moved other lanes of the head) is no
    proof that only a:2 moved toward the head: with the sensor set, the RESUME probes."""
    rig, a = swap_rig(tmp_path)

    def push_in(slot: Slot, h: Hooked, speed: float, budget: float | None = None) -> NoReturn:
        raise probe_error("head 1: the push of a:2 is blocked in the tube, not by another lane")

    rig.module.probe.push_in = push_in
    assert tool(rig, 2) == []
    assert rig.module.swapper.swap_pending.step == "push"
    rig.head_sensor.detected = True
    asked = probe_finds(rig, 1)
    assert resume(rig) == [True]
    assert asked == [rig.adapter.slot_of_head(0)]
    assert NO_PROBE_LINE not in rig.gcode.lines


def test_a_probe_failure_at_resume_keeps_the_print_paused(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2)
    probe_finds(rig, probe_error("head 1: more than one lane holds its filament: a:1, a:2"))
    started = len(a.feed.started)
    rig.sent.clear()
    assert resume(rig) == []  # the U1's RESUME is not run
    line = (
        "ace2k_u1: head 1: a:1 → a:2 (T2): head 1: more than one lane holds its filament:"
        " a:1, a:2 — still paused"
    )
    assert line in rig.gcode.lines
    assert rig.print_stats.state == "paused" and rig.module.swapper.swap_pending is not None
    assert len(a.feed.started) == started and rig.sent == []


def test_a_swap_failing_again_at_resume_stays_paused_without_a_second_pause(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2)
    probe_finds(rig, 0)
    rig.sent.clear()
    assert resume(rig) == []  # stuck again: the U1's RESUME is not run
    assert "PAUSE" not in rig.sent and "G91" not in rig.sent
    assert rig.sent[-4:] == [
        "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=extruder0_temp VALUE=220",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_temp VALUE=220",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0",
    ]
    line = "ace2k_u1: head 1: a:1 → a:2 (T2) failed at roll back: a:1 ended stuck"
    assert rig.gcode.lines.count(line) == 2
    assert rig.print_stats.state == "paused" and rig.module.swapper.swap_pending is not None


def test_the_u1s_resume_selects_the_swap_head_after_a_failure_before_the_unload(
    tmp_path: Path,
) -> None:
    """The U1's pause records the active head and its resume selects it again; a
    failure before the unload's T0 A0 left head 2 active."""
    rig, a = swap_rig(tmp_path)
    extruders = rig.printer.objects["extruder_list"]
    rig.u1.active = extruders[1]  # head 2 printed last
    rig.head_sensor.detected = False  # nothing to unload: no T0 A0
    a.feed.on_start = lambda move: setattr(
        move, "event", {"kind": "stuck" if move.mode == "feed" else "done", "seq": move.seq}
    )
    assert tool(rig, 2) == []
    assert any("failed at push" in line for line in rig.gcode.lines)
    assert rig.inner_resume["last_extruder_index"] == "0"  # set after the PAUSE recorded 1
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == [True]
    assert rig.u1.active is extruders[0]


def load_on_its_head(rig: SimpleNamespace) -> None:
    """The load macro called with its own head active, selected by a T0 A0 sent right before."""
    load = next(i for i, ln in enumerate(rig.sent) if ln.startswith("ACEK_SWAP_LOAD"))
    assert rig.sent[load - 1] == "T0 A0"
    assert rig.u1.swap_macros[-1][0] == "ACEK_SWAP_LOAD"
    assert rig.u1.active_at_macro[-1] == 0


def test_a_swap_whose_unload_is_skipped_selects_its_head_before_the_load(tmp_path: Path) -> None:
    """Head 2 printed last and head 1 is empty: no unload, so no T0 A0 there; the load macro's
    extrusions must still reach head 1, never head 2."""
    rig, a = swap_rig(tmp_path)
    extruders = rig.printer.objects["extruder_list"]
    rig.u1.active = extruders[1]
    rig.head_sensor.detected = False
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert tool(rig, 2) == [{}]
    assert not [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_UNLOAD")]
    load_on_its_head(rig)


def test_a_resume_from_the_hand_over_selects_its_head_before_the_load(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    extruders = rig.printer.objects["extruder_list"]
    rig.u1.active = extruders[1]  # head 2 printed last
    rig.head_sensor.detected = False  # nothing to unload: no T0 A0
    rig.units_obj.hand_over_fail = RuntimeError("late")
    assert tool(rig, 2) == []
    assert rig.module.swapper.swap_pending.step == "hand over"
    rig.units_obj.hand_over_fail = None
    rig.u1.active = extruders[1]  # whatever the pause left selected
    rig.sent.clear()
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == [True]
    load_on_its_head(rig)


def test_a_failed_swap_with_no_temperature_known_still_pauses_and_selects_its_head(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.tracker.plan.tools[2]["temp"] = None
    rig.module.tracker.plan.tools[2]["first_temp"] = None
    a.feed.on_start = freed_then(rig, "stuck")
    assert tool(rig, 2) == []  # no exception
    assert "PAUSE" in rig.sent and rig.print_stats.state == "paused"
    assert (
        rig.sent[-1] == "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0"
    )
    assert not [line for line in rig.sent if "_temp VALUE=" in line]
    assert rig.module.swapper.swap_pending.step == "roll back"


def test_a_u1_t_that_fails_after_the_swap_stays_owed_until_a_resume_runs_it(tmp_path: Path) -> None:
    """The print went on past the T at the pause; the T is run before the U1's
    RESUME, and a failing one keeps the print paused until it runs."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2, {"A": "1"})
    probe_finds(rig, 0)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    runs = []

    def t2_fails(gcmd: GCodeCommand) -> NoReturn:
        runs.append(gcmd.params)
        gcmd.params["A"] = "0"  # a handler that changes its parameters must not touch the kept
        raise ValueError("Extruder busy")

    rig.gcode.commands_orig["T2"] = t2_fails
    assert resume(rig) == []  # the U1's RESUME is not run
    line = "ace2k_u1: T2 after the swap failed: Extruder busy — still paused; RESUME runs it"
    assert line in rig.gcode.lines
    assert rig.module.swapper.swap_pending is None and rig.module.swapper.tools_owed != []
    assert rig.adapter.slot_of_head(0).current.lane == 1
    started = len(a.feed.started)
    rig.gcode.commands_orig["T2"] = lambda gcmd: runs.append(gcmd.params)
    assert resume(rig) == [True]  # the owed T, then the U1's RESUME
    assert runs[1] == {"A": "1"}  # the retry gets the original parameters
    assert runs[0] is not runs[1]  # a copy each time
    assert rig.module.swapper.tools_owed == []
    assert len(a.feed.started) == started  # no swap again
    assert resume(rig) == [True] and len(runs) == 2  # owed no more


def test_a_t_whose_parameters_were_not_read_runs_plainly_and_says_so(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2)
    rig.module.swapper.swap_pending.tool_params = None
    probe_finds(rig, 0)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    runs = []
    rig.gcode.commands_orig["T2"] = lambda gcmd: runs.append((gcmd.command, gcmd.params))
    assert resume(rig) == [True]
    assert runs == [("T2", {})]
    assert "ace2k_u1: T2's parameters were not read: run as T2" in rig.gcode.lines


def test_a_pause_the_u1_does_not_take_falls_back_to_the_pausing_error(tmp_path: Path) -> None:
    """The U1's PAUSE returns without pausing in an excluded object's region (from SD): the
    swap's failure falls back to the G-code error that pauses the print, its variables set."""
    rig, a = swap_rig(tmp_path)
    real = rig.gcode.run_script_from_command

    def pause_ignored(script: str) -> None:
        if script == "PAUSE":
            rig.sent.append(script)  # "Cannot pause while in an excluded region"
            return
        real(script)

    rig.gcode.run_script_from_command = pause_ignored
    a.feed.on_start = freed_then(rig, "stuck")
    with pytest.raises(ValueError) as err:
        tool(rig, 2)
    assert str(err.value) == "ace2k_u1: head 1: a:1 → a:2 (T2) failed at roll back: a:1 ended stuck"
    assert rig.sent[-4:] == [
        "PAUSE",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=extruder0_temp VALUE=220",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_temp VALUE=220",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0",
    ]
    assert rig.module.swapper.swap_pending.step == "roll back"


def test_a_pause_that_fails_falls_back_to_the_pausing_error(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    real = rig.gcode.run_script_from_command

    def pause_fails(script: str) -> None:
        if script == "PAUSE":
            raise RuntimeError("Unable to pause print")
        real(script)

    rig.gcode.run_script_from_command = pause_fails
    a.feed.on_start = freed_then(rig, "stuck")
    with pytest.raises(ValueError, match=r"failed at roll back: .*its PAUSE failed: Unable"):
        tool(rig, 2)
    assert rig.sent[-1] == (
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0"
    )


def test_a_resumed_swap_keeps_the_pauses_own_retraction(tmp_path: Path) -> None:
    """The U1's pause retracts 2 mm into printing_e_pos and its resume compensates
    it: the resumed swap puts back the value read at the RESUME, not the one before the pause."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2)
    ext = rig.printer.objects["extruder"]
    assert rig.e_pos_at_pause == [-10.0] and ext.printing_e_pos == -12.0
    probe_finds(rig, 0)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.sent.clear()
    resume_marked(rig)
    # the swap makes no retraction
    assert not [line for line in rig.sent if line.startswith("G1 E")]
    assert ext.printing_e_pos == -12.0
    assert rig.sent[-1] == "<U1 RESUME>"


def test_a_swap_whose_save_failed_is_probed_once_at_its_resume(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)
    real = rig.gcode.run_script_from_command
    fails = ["SAVE_GCODE_STATE NAME=ACEK_SWAP"]

    def save_fails_once(script: str) -> None:
        real(script)
        if fails and script == fails[0]:
            fails.clear()
            raise RuntimeError("no room")

    rig.gcode.run_script_from_command = save_fails_once
    asked = probe_finds(rig, 1)  # a:2 in the head: the old lane stays unknown
    assert tool(rig, 2) == []
    assert "ace2k_u1: head 1: - → a:2 (T2) failed at unload: no room" in rig.gcode.lines
    assert asked == []  # nothing read before the save
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == [True]
    assert asked == [slot]  # the RESUME's probe only: the swap's start does not probe again
    assert slot.current is slot.lanes[1]


def test_an_unload_during_a_swap_does_not_eject_the_old_lane(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.module.adapter.swapping.add(0)
    rig.left._set_channel_state(1, "unload_finish")
    assert rig.adapter.ejector.eject_timers == {}


def test_swap_skips_the_unload_when_the_heads_sensor_reads_clear(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.head_sensor.detected = False
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert not [s for s in rig.sent if "UNLOAD=1" in s]
    assert rig.adapter.slot_of_head(0).current.lane == 1


def test_a_swap_roll_back_that_runs_out_of_the_bay_counts_as_rolled_back(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = ends("runout", motor_mm=90.0, filament_mm=-80.0)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert "ace2k_u1: head 1: a:1 rolled out of its bay during the roll back" in rig.gcode.lines
    new = rig.adapter.slot_of_head(0).lanes[1]
    assert rig.adapter.slot_of_head(0).current is new
    assert rig.module.swapper.swap_pending is None
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent


def test_the_u1_unload_leaving_its_tip_on_the_heads_sensor_does_not_fail_the_unload(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    seen = []  # the head's sensor at each start on the unit
    start = a.feed.start_move

    def watch(lane: int, mode: str, length: float, speed: float, **kw: bool | str) -> FakeMove:
        seen.append((lane, mode, rig.head_sensor.detected))
        return start(lane, mode, length, speed, **kw)

    a.feed.start_move = watch
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    # the free check and the roll back run with the unload's tip still on the sensor; the push
    # only once it is clear
    assert seen[1:4] == [(0, "rollback", True), (0, "rollback", True), (1, "feed", False)]
    assert rig.module.swapper.swap_pending is None


def test_a_roll_back_that_leaves_the_heads_sensor_set_fails_before_anything_is_pushed(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    rig.rollback_clears = False  # the U1's tip stays on the head's sensor after the roll back
    assert tool(rig, 2) == []
    line = (
        "ace2k_u1: head 1: a:1 → a:2 (T2) failed at roll back: the head's sensor still sees"
        " filament after a:1's roll back"
    )
    assert line in rig.gcode.lines
    assert [(lane, mode) for lane, mode, *_ in a.feed.started] == [
        (0, "assist_both"),
        (0, "rollback"),
        (0, "rollback"),
    ]
    slot = rig.adapter.slot_of_head(0)
    assert rig.units_obj.handed == [] or rig.units_obj.handed[-1] != ("a", 1)
    assert slot.current is slot.lanes[0]  # nothing handed over
    assert rig.module.swapper.swap_pending.step == "roll back"
    # the operator clears the head by hand: the resume reads it empty and goes on from the hand
    # over — no roll back again, the push finds any lane left in the way
    rig.head_sensor.detected = False
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == [True]
    modes = [(lane, mode) for lane, mode, *_ in a.feed.started]
    assert modes == [
        (0, "assist_both"),  # a:1 follows the unload macro
        (0, "rollback"),
        (0, "rollback"),
        (1, "feed"),
        (1, "assist_both"),
    ]
    assert slot.current is slot.lanes[1]


def test_a_swap_while_printing_reads_the_heads_switch_not_the_u1s_latched_runout(
    tmp_path: Path,
) -> None:
    # as measured on the U1: while printing, its runout helper keeps filament_detected True
    # after the switch empties, until the extruder has moved on; a swap moves no extruder
    rig, a = swap_rig(tmp_path)
    rig.print_stats.state = "printing"
    rig.head_sensor.latched = True
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    modes = [(lane, mode) for lane, mode, *_ in a.feed.started]
    assert modes == [
        (0, "assist_both"),  # a:1 follows the unload macro
        (0, "rollback"),
        (0, "rollback"),
        (1, "feed"),
        (1, "assist_both"),
    ]
    assert rig.adapter.slot_of_head(0).current.lane == 1
    assert rig.module.swapper.swap_pending is None


def test_the_head_sensor_is_its_switch_when_it_has_one(tmp_path: Path) -> None:
    rig, _ = swap_rig(tmp_path)
    adapter = rig.adapter
    h = adapter.slot_of_head(0).lanes[0]
    sensor = rig.head_sensor
    sensor.detected, sensor.latched = False, True
    assert adapter.head_set(h) is False
    sensor.detected, sensor.latched = True, False
    assert adapter.head_set(h) is True
    # no switch attribute (another Klipper build, a fake): the runout helper's status
    plain = fakes.PlainHeadSensor()
    plain.detected = True
    rig.left.runout_sensor[1] = plain
    assert adapter.head_set(h) is True


UNKNOWN_LINE = "ace2k_u1: head 1: its lane is not known: the print pauses to probe it"


def test_an_unknown_lane_while_printing_pauses_plainly_then_resume_probes_it(
    tmp_path: Path,
) -> None:
    """The probe's cold wait never runs inside a printing T: the swap pauses first."""
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)  # a filament in the head, its lane not known
    asked = probe_finds(rig, 0)
    assert tool(rig, 2) == []
    assert asked == [] and a.feed.started == []
    assert UNKNOWN_LINE in rig.gcode.lines
    assert not [line for line in rig.sent if "UNLOAD" in line or line.startswith("M10")]
    assert rig.sent[0] == "SAVE_GCODE_STATE NAME=ACEK_SWAP"
    assert rig.sent[-len(put_back_tail()) :] == put_back_tail()
    pending = rig.module.swapper.swap_pending
    assert pending.step == "unload" and pending.old is None
    # the RESUME probes: a:1 in the head, the swap runs from its unload
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == [True]
    assert asked == [slot]
    modes = [(lane, mode) for lane, mode, _l, _s, _m in a.feed.started]
    assert modes == [
        (0, "assist_both"),  # a:1 follows the unload macro
        (0, "rollback"),
        (0, "rollback"),
        (1, "feed"),
        (1, "assist_both"),
    ]
    assert slot.current is slot.lanes[1] and rig.module.swapper.swap_pending is None


def test_an_unknown_lane_while_paused_is_probed_in_the_t(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)
    rig.print_stats.state = "paused"
    rig.pause_resume.is_paused = True
    asked = probe_finds(rig, 0)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert tool(rig, 2) == [{}]
    assert asked == [slot] and UNKNOWN_LINE not in rig.gcode.lines
    assert slot.current is slot.lanes[1]


def test_a_failed_probe_of_an_unknown_lane_at_resume_keeps_the_print_paused(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)
    assert tool(rig, 2) == []
    assert UNKNOWN_LINE in rig.gcode.lines
    probe_finds(rig, probe_error("head 1: no lane of the head holds its filament"))
    assert resume(rig) == []
    assert a.feed.started == [] and not [s for s in rig.sent if "UNLOAD=1" in s]
    line = (
        "ace2k_u1: head 1: - → a:2 (T2): head 1: no lane of the head holds its filament — still"
        " paused"
    )
    assert rig.gcode.lines[-1] == line
    # the next RESUME probes again: a:1 is in the head
    probe_finds(rig, 0)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == [True]
    modes = [(lane, mode) for lane, mode, _l, _s, _m in a.feed.started]
    assert modes == [
        (0, "assist_both"),  # a:1 follows the unload macro
        (0, "rollback"),
        (0, "rollback"),
        (1, "feed"),
        (1, "assist_both"),
    ]
    assert slot.current is slot.lanes[1]


def test_swap_resumed_at_the_roll_back_checks_the_heads_sensor_after_it(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-100.0, motor_mm=-100.0)
    tool(rig, 2)
    assert rig.head_sensor.detected  # the U1's tip still on it: the roll back stopped short
    probe_finds(rig, 0)
    a.feed.on_start = ends("done")
    rig.rollback_clears = False  # something in the head the roll back does not take out
    assert resume(rig) == []
    line = (
        "ace2k_u1: head 1: a:1 → a:2 (T2) failed at roll back: the head's sensor still sees"
        " filament after a:1's roll back"
    )
    assert line in rig.gcode.lines
    modes = [(lane, mode, length) for lane, mode, length, _s, _m in a.feed.started]
    # each run: a:1 armed for the unload macro, its free check, then its roll back; nothing pushed
    assert modes == [(0, "assist_both", 0.0), (0, "rollback", 100.0), (0, "rollback", 1000.0)] * 2


def test_a_resumed_swap_restores_the_gcode_state_without_moving(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2)
    assert rig.print_stats.state == "paused"  # the failure's pause
    probe_finds(rig, 0)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.sent.clear()
    resume(rig)
    assert "SAVE_GCODE_STATE NAME=ACEK_SWAP" not in rig.sent
    assert "G91" not in rig.sent
    assert rig.sent[-1] == "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0"
    assert rig.gcode.lines.count("ace2k_u1: head 1: a:1 → a:2 (T2)") == 1
    assert "ace2k_u1: head 1: a:2 in (0 s)" in rig.gcode.lines


def test_a_successful_swap_leaves_nothing_for_resume(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.module.swapper.swap_pending is None
    started = len(a.feed.started)
    assert resume(rig) == [True]
    assert len(a.feed.started) == started


def test_a_swap_pending_is_dropped_with_its_plan(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2)
    rig.print_stats.state = "cancelled"
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.tracker.plan is None and rig.module.swapper.swap_pending is None
    started = len(a.feed.started)
    assert resume(rig) == [True]
    assert len(a.feed.started) == started


def test_the_swap_steps_and_the_refills_share_the_free_check() -> None:
    assert ace2k_u1_swap.SWAP_STEPS == (
        "unload",
        "free",
        "roll back",
        "hand over",
        "push",
        "spool",
        "load",
        "loaded",
        "return",
    )
    assert ace2k_u1_refill.REFILL_CLEAR_STEPS == ("unload", "free", "roll back")
    assert ace2k_u1_swap.LOADED_STEPS == ("loaded", "return")
    assert not hasattr(ace2k_u1, "SWAP_CANCEL_STATES")  # a swap sends no cancel of the U1's


def test_a_lane_in_error_after_its_load_keeps_the_print_paused_then_is_cleared(
    tmp_path: Path,
) -> None:
    """The new spool had a knot; its follow timed out during the load and the
    print went on with the lane in error. Now the swap fails at its loaded check, paused; the
    RESUME after it clears the error, arms the follow again and goes on."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        if script.startswith("ACEK_SWAP_LOAD"):
            a.lanes[1].update(mode="error", error="timeout")  # the knot: its follow timed out
        real(script)

    rig.gcode.run_script_from_command = run_script
    assert tool(rig, 2) == []
    line = (
        "ace2k_u1: head 1: a:1 → a:2 (T2) failed at loaded: a:2 is in error (timeout) after its"
        " load: check its spool (a knot, a tangle), then RESUME"
    )
    assert rig.gcode.lines[-1] == line
    assert rig.module.swapper.swap_pending.step == "loaded"
    assert a.feed.cleared == []
    rig.gcode.run_script_from_command = real
    started = len(a.feed.started)
    assert resume(rig) == [True]
    assert a.feed.cleared == [1]
    assert (1, "assist_both") in [(lane, mode) for lane, mode, *_ in a.feed.started[started:]]
    assert "ace2k_u1: a:2: its error (timeout) cleared, its follow armed again" in rig.gcode.lines


def failed_at(tmp_path: Path, step: str) -> tuple[SimpleNamespace, FakeAce2k]:
    """A swap to a:2 that fails after its load macro, the print paused: at "loaded" the head's
    sensor reads empty after the load (the push said it reached it); at "return" the U1's
    pre-extrusion fails once."""
    rig, a = swap_rig(tmp_path)
    real = rig.gcode.run_script_from_command
    if step == "loaded":
        rig.module.probe.push_in = lambda slot, h, speed, budget=None: None
    else:
        a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
        fails = ["SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0"]

        def run_script(script: str) -> None:
            if fails and script == fails[0]:
                fails.clear()
                raise RuntimeError("heater fault")
            real(script)

        rig.gcode.run_script_from_command = run_script
    assert tool(rig, 2) == []
    rig.gcode.run_script_from_command = real
    return rig, a


@pytest.mark.parametrize("step", ["loaded", "return"])
def test_a_swap_failed_after_its_load_goes_on_from_its_step_with_the_head_set(
    tmp_path: Path, step: str
) -> None:
    rig, a = failed_at(tmp_path, step)
    assert any(f"(T2) failed at {step}: " in line for line in rig.gcode.lines)
    rig.head_sensor.detected = True  # a:2 at the head's sensor (fed on by hand, if it was not)
    asked = probe_finds(rig, 1)
    del rig.sent[:]
    assert resume(rig) == [True]
    # The head's sensor is set, the swap's own load put a:2 there: no probe, no cooling,
    # no load again — on from the failed step
    assert asked == []
    assert not [line for line in rig.sent if line.startswith(("AUTO_FEEDING", "ACEK_SWAP_"))]
    assert tuple(rig.sent) == SUCCESS_EXIT
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is slot.lanes[1] and rig.module.swapper.swap_pending is None


def test_a_resume_at_return_selects_the_head_before_the_pre_extrusion(tmp_path: Path) -> None:
    """The U1's pre-extrusion sends a plain T<index> when the active head is not the tool's.
    That T would go back into the pending swap. The step selects the head first (T0 A0)."""
    rig, _ = failed_at(tmp_path, "return")
    rig.head_sensor.detected = True
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        if script.startswith("SM_PRINT_PREEXTRUDE_FILAMENT") and rig.u1.active.extruder_num != 0:
            rig.gcode.commands["T2"](Gcmd({}, "T2"))  # the U1's own T, as it sends it
        real(script)

    rig.gcode.run_script_from_command = run_script
    rig.u1.active = rig.printer.objects["extruder_list"][1]  # another head made active
    del rig.sent[:]
    assert resume(rig) == [True]
    pre = rig.sent.index("SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0")
    assert "T0 A0" in rig.sent[:pre]
    assert rig.sent.count("SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0") == 1
    assert [ln for ln in rig.gcode.lines if " in (" in ln] == ["ace2k_u1: head 1: a:2 in (0 s)"]
    assert rig.module.swapper.swap_pending is None


def test_a_swap_failed_at_its_loaded_check_with_the_head_empty_is_probed_as_before(
    tmp_path: Path,
) -> None:
    rig, a = failed_at(tmp_path, "loaded")
    del rig.module.probe.push_in  # the real push again
    asked = probe_finds(rig, 1)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    del rig.sent[:]
    started = len(a.feed.started)
    assert resume(rig) == [True]
    assert asked == []  # the sensor empty: on from the hand over, the push finds it
    assert [mode for _l, mode, *_ in a.feed.started[started:]][0] == "feed"
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent


def test_a_swap_failed_at_its_loaded_check_with_the_sensor_unreadable_stays_paused(
    tmp_path: Path,
) -> None:
    rig, a = failed_at(tmp_path, "loaded")
    rig.adapter.head_set = lambda h: (_ for _ in ()).throw(RuntimeError("no sensor"))
    asked = probe_finds(rig, 1)
    del rig.sent[:]
    assert resume(rig) == []
    assert asked == [] and rig.module.swapper.swap_pending.step == "loaded"
    assert "its sensor cannot be read" in rig.gcode.lines[-1]


def test_swap_rolls_back_and_pushes_at_swap_speed(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.swap_speed = 40.0
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    speeds = [speed for _lane, mode, _l, speed, _m in a.feed.started if mode != "assist_both"]
    # the free check at its own speed
    assert speeds == [rig.module.swap_cfg.probe_speed, 40.0, 40.0]


@pytest.mark.parametrize(
    "layer, kept, temp",
    [(None, None, 220), (1, None, 235), (0, None, 235), (2, None, 220), (1, 250, 250)],
)
def test_the_swaps_temperature(
    tmp_path: Path, layer: int | None, kept: int | None, temp: int
) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.tracker.plan.tools[2]["first_temp"] = 235
    rig.print_stats.current_layer = layer
    if kept is not None:
        rig.module.tracker.plan.kept[2] = kept
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert sent_macro(rig, f"ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP={temp} PRINT_TEMP={temp}")


def test_the_head_takes_the_new_lanes_spool(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.sent[4] == (
        "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 VENDOR=Generic FILAMENT_TYPE=PLA"
        " FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=FFFFFFFF FORCE=1"
    )


# --- fix round 1 ------------------------------------------------------------------------------


def resume_marked(rig: SimpleNamespace) -> None:
    """RESUME with the U1's own RESUME marked in what was sent."""

    def u1_resume(gcmd: GCodeCommand) -> None:
        rig.sent.append("<U1 RESUME>")
        rig.u1_resume()

    rig.gcode.commands_orig["RESUME"] = u1_resume
    rig.gcode.commands["RESUME"](Gcmd({}, "RESUME"))


def test_a_t_reissued_over_a_pending_swap_continues_it(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    assert tool(rig, 2) == []
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    asked = probe_finds(rig, 0)
    # the same T again: the pending swap goes on from what the probe reads, then the U1's T
    assert tool(rig, 2) == [{}]
    assert len(asked) == 1
    rollbacks = [length for _lane, mode, length, _s, _m in a.feed.started if mode == "rollback"]
    # each run: the free check, then the roll back
    assert rollbacks == [100.0, 1000.0, 100.0, 1000.0]
    assert rig.module.swapper.swap_pending is None
    assert rig.adapter.slot_of_head(0).current.lane == 1
    assert rig.sent.count("SAVE_GCODE_STATE NAME=ACEK_SWAP") == 1
    # paused: the U1's RESUME moves back to the print
    assert rig.sent[-1] == "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0"


def test_a_t_for_another_head_while_a_swap_is_pending_is_refused(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    tool(rig, 2)
    pending = rig.module.swapper.swap_pending
    started = len(a.feed.started)
    rig.sent.clear()
    for n in (1, 3):  # head 2 (one lane), head 4 (a group)
        calls = []
        rig.gcode.commands_orig[f"T{n}"] = lambda gcmd, calls=calls: calls.append(gcmd)
        rig.gcode.commands[f"T{n}"](Gcmd({}, f"T{n}"))  # no error
        line = (
            f"ace2k_u1: T{n} refused: head 1: a:1 → a:2 (T2) is pending at roll back"
            " — RESUME it first"
        )
        assert line in rig.gcode.lines
        assert calls == []
    assert rig.sent == []  # paused already: no second PAUSE
    # a print running with the swap pending: the refusal pauses it plainly
    rig.print_stats.state = "printing"
    rig.pause_resume.is_paused = False
    rig.gcode.commands["T3"](Gcmd({}, "T3"))
    assert rig.sent == ["PAUSE"] and rig.print_stats.state == "paused"
    assert rig.module.swapper.swap_pending is pending
    assert len(a.feed.started) == started


def test_a_lane_with_no_spool_known_leaves_the_heads_setting(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    del rig.module.spool_store.records["a:2"]  # no ACEK_SPOOL record, no tag
    ptc = rig.task_config
    ptc.status["filament_type"][0] = "PETG"  # the old lane's spool, on the head
    ptc.status["filament_color_rgba"][0] = "000000FF"
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert not [line for line in rig.sent if line.startswith("SET_PRINT_FILAMENT_CONFIG")]
    # the head's setting is the old lane's: never reported as the new lane's spool
    slot = rig.adapter.slot_of_head(0)
    assert slot.setting_for is None
    assert rig.module.spool_lanes.lane_spool(slot.current) == ("a:2", None, None, False)
    line = "ace2k_u1: head 1: a:2 has no spool set: set it with Edit spool"
    assert line in rig.gcode.lines


def test_the_head_setting_speaks_for_the_lane_whose_spool_was_written(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)  # a:2's ACEK_SPOOL record written to the head
    slot = rig.adapter.slot_of_head(0)
    assert slot.setting_for is slot.current is slot.lanes[1]
    # the operator names another lane in the head: the setting no longer speaks for it
    rig.adapter.set_current(slot, slot.lanes[0])
    assert slot.setting_for is None
    del rig.module.spool_store.records["a:1"]
    rig.task_config.status["filament_type"][0] = "PLA"
    rig.task_config.status["filament_color_rgba"][0] = "FFFFFFFF"
    assert rig.module.spool_lanes.lane_spool(slot.lanes[0]) == ("a:1", None, None, False)
    # Edit spool on the lane in the head (ACEK_SPOOL, then the head's setting): it speaks for it
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PETG", "COLOR": "00ff00"})
    assert slot.setting_for is slot.lanes[0]
    # Edit spool on a parked lane leaves it
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PETG", "COLOR": "00ff00"})
    assert slot.setting_for is slot.lanes[0]


def test_a_tagged_spool_gives_the_head_its_vendor_and_subtype(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    record = {"brand": "Bambu", "material": "PLA", "name": "PLA Matte", "color_rgba": "FF0000FF"}
    a.lanes[1]["tag"] = dict(state="read", uid="x", source="tag", record=record)
    # Bambu PLA Matte listed: the tag's names written (an unlisted one is Generic, test below)
    names = {("Bambu", "PLA", "Matte"), ("generic", "PLA", "generic")}
    rig.adapter.tag_writer.tags = types.SimpleNamespace(known_types={"PLA", "PETG"}, names=names)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.sent[4] == (
        "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 VENDOR=Bambu FILAMENT_TYPE=PLA"
        " FILAMENT_SUBTYPE='Matte' FILAMENT_COLOR_RGBA=FF0000FF FORCE=1"
    )


def test_a_tag_whose_material_the_u1_does_not_know_gives_the_head_its_colour_only(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    record = {"brand": "Bambu", "material": "PCTG", "name": "PCTG Basic", "color_rgba": "FF0000FF"}
    a.lanes[1]["tag"] = dict(state="read", uid="x", source="tag", record=record)
    rig.adapter.tag_writer.tags = types.SimpleNamespace(known_types={"PLA", "PETG"})
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.sent[4] == (
        "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 FILAMENT_COLOR_RGBA=FF0000FF FORCE=1"
    )


def test_a_spool_record_of_a_type_the_u1_does_not_know_gives_the_head_its_colour_only(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "WOOD", "COLOR": "00ff00"})
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.sent[4] == (
        "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 FILAMENT_COLOR_RGBA=00FF00FF FORCE=1"
    )


def test_a_spool_setting_the_u1_refuses_never_fails_the_swap(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    real = rig.gcode.run_script_from_command

    def refuses(script: str) -> None:
        real(script)
        if script.startswith("SET_PRINT_FILAMENT_CONFIG"):
            raise RuntimeError("official filament, not configurable")

    rig.gcode.run_script_from_command = refuses
    assert tool(rig, 2) == [{}]
    line = "ace2k_u1: head 1: spool setting refused: official filament, not configurable"
    assert line in rig.gcode.lines
    slot = rig.adapter.slot_of_head(0)
    assert slot.setting_for is None and slot.current is slot.lanes[1]
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent
    assert rig.module.swapper.swap_pending is None


def test_a_resumed_swap_whose_target_was_named_in_the_head_is_decided_by_the_probe(
    tmp_path: Path,
) -> None:
    """The head's lane not known, the operator named the swap's own target during the pause
    (ACEK_LANE_ACTIVE): the RESUME probe decides. It reads a:1 in the head, so a:1 is handed the
    head back, unloaded and rolled back before anything is pushed."""
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)
    probe_finds(rig, probe_error("head 1: no lane of the head holds its filament"))
    assert tool(rig, 2) == []
    rig.adapter.set_current(slot, slot.lanes[1])  # named, wrongly
    asked = probe_finds(rig, 0)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.units_obj.handed.clear()
    rig.sent.clear()
    assert resume(rig) == [True]
    assert asked == [slot]
    assert rig.units_obj.handed == [("a", 0), ("a", 1)]  # a:1 the head's again, then a:2
    assert (
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=220 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=220 MATERIAL=PLA"
        in rig.sent
    )
    modes = [(lane, mode) for lane, mode, _l, _s, _m in a.feed.started]
    assert modes == [
        (0, "assist_both"),  # a:1 follows the unload macro
        (0, "rollback"),
        (0, "rollback"),
        (1, "feed"),
        (1, "assist_both"),
    ]
    assert rig.module.swapper.swap_pending is None and slot.current is slot.lanes[1]


def test_a_target_the_probe_finds_in_the_head_follows_before_the_load_macro(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)
    probe_finds(rig, probe_error("head 1: no lane of the head holds its filament"))
    tool(rig, 2)
    probe_finds(rig, 1)  # a:2 put in the head by hand
    order = []
    done = a.feed.on_start

    def on_start(move: FakeMove) -> None:
        order.append(("start", move.lane, move.mode))
        if done is not None:
            done(move)

    a.feed.on_start = on_start
    sent = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        order.append(("sent", script))
        sent(script)

    rig.gcode.run_script_from_command = run_script
    rig.units_obj.handed.clear()
    assert resume(rig) == [True]
    assert rig.units_obj.handed == [("a", 1)]  # the lane found handed the head
    assert ("start", 1, "feed") not in order  # nothing pushed: it is in the head
    assert not [s for kind, *s in order if kind == "sent" and "UNLOAD" in s[0]]
    follow = order.index(("start", 1, "assist_both"))
    assert follow < order.index(
        ("sent", "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA")
    )
    assert slot.lanes[1].follow_wanted is True
    assert rig.module.swapper.swap_pending is None and slot.current is slot.lanes[1]


def test_a_swap_with_no_lane_known_and_an_empty_head_pushes_the_target_unprobed(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)
    rig.head_sensor.detected = False  # nothing at the head's sensor
    asked = probe_finds(rig, 0)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert tool(rig, 2) == [{}]
    assert asked == []  # the push is the probe of an empty head
    modes = [(lane, mode) for lane, mode, _l, _s, _m in a.feed.started]
    assert modes == [(1, "feed"), (1, "assist_both")]
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent
    assert rig.module.swapper.swap_pending is None and slot.current is slot.lanes[1]


@pytest.mark.parametrize("fail", ["roll back", "load"])
def test_the_u1s_resume_heats_the_head_to_the_new_tools_temperature(
    tmp_path: Path, fail: str
) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.tracker.plan.kept[2] = 250
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    real = rig.gcode.run_script_from_command

    def heat_fails(script: str) -> None:
        if script.startswith("ACEK_SWAP_LOAD"):
            raise RuntimeError("heater fault")
        real(script)

    if fail == "roll back":
        a.feed.on_start = freed_then(rig, "stuck")
    else:
        rig.gcode.run_script_from_command = heat_fails
    assert tool(rig, 2) == []
    assert any(f"failed at {fail}" in line for line in rig.gcode.lines)
    # the INNER_RESUME variables, which the U1's RESUME heats each head back to
    assert rig.sent[-3:] == [
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=extruder0_temp VALUE=250",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_temp VALUE=250",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0",
    ]
    a.feed.on_start = ends("done")
    rig.gcode.run_script_from_command = real
    probe_finds(rig, 0 if fail == "roll back" else 1)
    resume_marked(rig)
    assert rig.sent[-1] == "<U1 RESUME>"  # no heating of its own after it
    assert rig.module.swapper.swap_pending is None


def test_a_resume_with_no_swap_sends_no_temperature(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)  # a swap that did not pause
    resume_marked(rig)
    assert rig.sent[-1] == "<U1 RESUME>"
    resume_marked(rig)
    assert rig.sent[-1] == "<U1 RESUME>"


def test_a_pending_swap_ended_by_its_t_owes_a_later_resume_nothing(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tool(rig, 2)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    probe_finds(rig, 0)
    tool(rig, 2)  # the pending swap goes on with its T
    assert rig.module.swapper.swap_pending is None
    resume_marked(rig)
    assert rig.sent[-1] == "<U1 RESUME>"


def temp(rig: SimpleNamespace, command: str, params: dict[str, str]) -> list[dict[str, str]]:
    calls = []
    rig.gcode.commands_orig[command] = lambda gcmd: calls.append(gcmd.params)
    rig.gcode.commands[command](Gcmd(dict(params), command))
    return calls


def test_temperature_of_a_tool_not_in_its_head_is_kept_not_applied(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    assert temp(rig, "M104", {"S": "190", "T": "2"}) == []
    assert rig.module.tracker.plan.kept[2] == 190.0
    assert temp(rig, "M109", {"S": "220", "T": "2"}) == []
    assert rig.module.tracker.plan.kept[2] == 220.0
    said = [ln for ln in rig.gcode.lines if "temperature kept" in ln]
    assert said == ["ace2k_u1: T2's temperature kept for its swap: head 1 prints a:1"]


@pytest.mark.parametrize(
    "params",
    [
        {"S": "200", "T": "0"},
        {"S": "200"},
        {"S": "200", "T": "2", "A": "0"},
        {"S": "200", "T": "1"},
    ],
)
def test_temperature_passes_through_otherwise(tmp_path: Path, params: dict[str, str]) -> None:
    rig, _ = planned(tmp_path)
    assert temp(rig, "M104", params) == [params]


def test_temperature_passes_through_without_a_started_plan_or_a_group(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    assert temp(rig, "M104", {"S": "170", "T": "2"}) == [{"S": "170", "T": "2"}]
    rig, _ = tools_rig(tmp_path, lanes="a:1, a:2")
    assert temp(rig, "M109", {"S": "170", "T": "2"}) == [{"S": "170", "T": "2"}]


def test_a_kept_temperature_is_used_once_by_the_swap(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    temp(rig, "M104", {"S": "200", "T": "2"})
    tool(rig, 2)
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=200 PRINT_TEMP=200 MATERIAL=PLA" in rig.sent
    assert 2 not in rig.module.tracker.plan.kept
    tool(rig, 0)  # swap away, back to a:1
    del rig.sent[:]
    tool(rig, 2)  # and back, with no new M104: the plan's temperature
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent


def test_a_zero_is_not_kept_and_the_swap_takes_the_plans_temperature(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert temp(rig, "M104", {"S": "0", "T": "2"}) == []
    assert 2 not in rig.module.tracker.plan.kept
    tool(rig, 2)
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent


@pytest.mark.parametrize("end", ["complete", "cancelled", "error"])
def test_a_held_heater_off_is_sent_at_the_prints_end(tmp_path: Path, end: str) -> None:
    rig, _ = planned(tmp_path)
    scripts = []
    rig.gcode.run_script = scripts.append
    assert temp(rig, "M104", {"S": "0", "T": "2"}) == []  # head 1 prints a:1: held
    assert temp(rig, "M109", {"S": "0", "T": "7"}) == []  # head 4 prints b:1
    assert temp(rig, "M104", {"S": "70", "T": "2"}) == []  # a standby: no heater off
    rig.module.watch_print(rig.reactor.now)
    assert scripts == []  # not before the print's end
    rig.print_stats.state = end
    rig.module.watch_print(rig.reactor.now)
    assert scripts == []  # queued: the watch tick never waits on the G-code's lock
    assert rig.module.tracker.plan is None
    rig.reactor.run_async()
    assert scripts == ["M104 S0 T0 A0", "M104 S0 T3 A0"]
    assert "ace2k_u1: head 1: turned off at the print's end (T2's M104 S0 was held)" in (
        rig.gcode.lines
    )
    assert "ace2k_u1: head 4: turned off at the print's end (T7's M104 S0 was held)" in (
        rig.gcode.lines
    )


def test_a_heater_off_that_fails_past_its_own_guard_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = planned(tmp_path)
    rig.gcode.run_script = lambda script: None
    temp(rig, "M104", {"S": "0", "T": "2"})
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)

    def say(text: str) -> NoReturn:
        raise RuntimeError("console gone")

    rig.adapter.say = say
    with caplog.at_level(logging.ERROR):
        rig.reactor.run_async()  # never raises into the reactor
    assert "ace2k_u1: the heaters off at the print's end" in caplog.text


def test_no_heater_off_at_the_prints_end_without_a_held_one(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    scripts = []
    rig.gcode.run_script = scripts.append
    temp(rig, "M104", {"S": "70", "T": "2"})
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    rig.reactor.run_async()
    assert scripts == []


def test_a_standby_temperature_is_neither_applied_nor_kept(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)  # T7's plan temperature: 220, its floor 180
    assert temp(rig, "M104", {"S": "70", "T": "7"}) == []
    assert 7 not in rig.module.tracker.plan.kept
    assert temp(rig, "M104", {"S": "179", "T": "7"}) == []
    assert 7 not in rig.module.tracker.plan.kept
    # nothing kept: nothing said kept (the bench reads these lines)
    assert not [ln for ln in rig.gcode.lines if "temperature kept" in ln]
    assert temp(rig, "M104", {"S": "230", "T": "7"}) == []
    assert rig.module.tracker.plan.kept[7] == 230.0
    said = [ln for ln in rig.gcode.lines if "temperature kept" in ln]
    assert said == ["ace2k_u1: T7's temperature kept for its swap: head 4 prints b:1"]
    assert temp(rig, "M104", {"S": "70", "T": "7"}) == []  # a cool-down after it
    assert rig.module.tracker.plan.kept[7] == 230.0
    assert [ln for ln in rig.gcode.lines if "temperature kept" in ln] == said


def test_a_standby_temperature_swaps_at_the_plans_temperature(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert temp(rig, "M104", {"S": "70", "T": "2"}) == []
    tool(rig, 2)
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent
    assert not [line for line in rig.sent if "TEMP=70" in line or line.startswith("M10")]


def test_without_a_plan_temperature_the_floor_is_150(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    rig.module.tracker.plan.tools[7]["temp"] = None
    assert temp(rig, "M104", {"S": "140", "T": "7"}) == []
    assert 7 not in rig.module.tracker.plan.kept
    assert temp(rig, "M104", {"S": "150", "T": "7"}) == []
    assert rig.module.tracker.plan.kept[7] == 150.0


def test_the_kept_line_is_said_again_in_the_next_print(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    line = "ace2k_u1: T2's temperature kept for its swap: head 1 prints a:1"
    temp(rig, "M104", {"S": "230", "T": "2"})  # printing temperatures: kept
    temp(rig, "M104", {"S": "231", "T": "2"})
    assert rig.gcode.lines.count(line) == 1
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.tracker.plan is None
    rig.gcode.lines.clear()
    rig.print_stats.state = "standby"
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    run(
        rig,
        "ACEK_PLAN_SET",
        dict(
            FILE="eight.gcode",
            **{f"T{n}": t["lane"] for n, t in enumerate(rig.module.tracker.plan.tools)},
        ),
    )
    page_map(rig)
    print_start(rig)
    rig.print_stats.state = "printing"
    rig.module.watch_print(rig.reactor.now)
    temp(rig, "M104", {"S": "230", "T": "2"})
    assert rig.gcode.lines.count(line) == 1


class InternalError(Exception):
    """An internal error of the core (a unit's link down, say): Klipper must shut down on it."""


def test_an_internal_error_of_a_starts_hand_over_is_raised_as_it_came(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.units_obj.hand_over_fail = InternalError("link down")
    with pytest.raises(InternalError):
        print_start(rig)
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []


def test_an_internal_error_of_the_swaps_hand_over_is_no_pause(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.units_obj.hand_over_fail = InternalError("link down")
    with pytest.raises(InternalError):
        tool(rig, 2)
    assert rig.module.swapper.swap_pending is None and "PAUSE" not in rig.sent
    # a refusal (a follower late) pauses as ever
    (tmp_path / "again").mkdir()
    rig, a = swap_rig(tmp_path / "again")
    rig.units_obj.hand_over_fail = RuntimeError("late")
    assert tool(rig, 2) == []
    assert "ace2k_u1: head 1: a:1 → a:2 (T2) failed at hand over: late" in rig.gcode.lines
    assert rig.module.swapper.swap_pending is not None and "PAUSE" in rig.sent


def paused_unknown(rig: SimpleNamespace) -> None:
    """Head 1's lane not known and the print paused: a T typed now probes in the T."""
    rig.adapter.set_current(rig.adapter.slot_of_head(0), None)
    rig.print_stats.state = "paused"
    rig.pause_resume.is_paused = True


def test_an_internal_error_handing_a_probed_lane_the_head_is_no_pause(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    paused_unknown(rig)
    probe_finds(rig, 0)  # a:1 found: handed the head through the core
    rig.units_obj.hand_over_fail = InternalError("link down")
    with pytest.raises(InternalError):
        tool(rig, 2)
    assert rig.module.swapper.swap_pending is None
    # a refusal of that hand over is the swap's failure, named as the hand over
    (tmp_path / "again").mkdir()
    rig, a = swap_rig(tmp_path / "again")
    paused_unknown(rig)
    probe_finds(rig, 0)
    rig.units_obj.hand_over_fail = RuntimeError("late")
    assert tool(rig, 2) == []
    assert "ace2k_u1: head 1: a:1 → a:2 (T2) failed at hand over: late" in rig.gcode.lines
    assert rig.module.swapper.swap_pending.step == "hand over"  # as the line says


def test_a_tag_written_to_a_shared_head_speaks_for_its_current_lane(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    a = rig.adapter
    slot = a.slot_of_head(0)
    a.set_current(slot, slot.lanes[1])
    a.tag_writer.tags = types.SimpleNamespace(note_failed=lambda key: None)
    rig.gcode.run_script = lambda script: None
    a.tag_writer._run_tag(slot.lanes[1].key, ("clear", 0, None))
    assert slot.setting_for is slot.lanes[1]
    slot.setting_for = None
    a.tag_writer._run_tag(
        slot.lanes[0].key, ("clear", 0, None)
    )  # a parked lane's (never queued): left
    assert slot.setting_for is None


def remove_after_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The file removed between ACEK_PLAN's (or ACEK_PLAN_SET's) check and its read."""
    real = ace2k_u1.ACE2kU1.gcode_file

    def gone(self: ace2k_u1.ACE2kU1, gcmd: GCodeCommand) -> tuple[str, str]:
        name, path = real(self, gcmd)
        os.remove(path)
        return name, path

    monkeypatch.setattr(ace2k_u1.ACE2kU1, "gcode_file", gone)


def test_a_file_removed_before_its_plan_reads_it_is_the_gcode_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = tools_rig(tmp_path)
    remove_after_the_check(monkeypatch)
    with pytest.raises(ValueError, match="ace2k_u1: eight.gcode: "):
        run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    assert rig.module.tracker.plan is None


def test_a_file_removed_before_its_choice_reads_it_is_the_gcode_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.tracker.plan.tools)}
    remove_after_the_check(monkeypatch)
    with pytest.raises(ValueError, match="ace2k_u1: eight.gcode: "):
        run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    assert rig.module.tracker.plan.set is False


def test_a_planned_file_removed_before_its_start_starts_unchecked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    os.remove(tmp_path / "gcodes" / "eight.gcode")
    assert len(print_start(rig)) == 1  # the U1 says the file is missing; never refused here
    assert rig.module.tracker.plan_started is False
    assert "eight.gcode unread at its start: not checked" in caplog.text


def test_a_lane_back_in_the_head_past_the_hand_over_restarts_the_swap_from_the_unload(
    tmp_path: Path,
) -> None:
    """Rules 2 and 6: a:2's push raised (its search may have moved the other lanes), the
    operator fed a:1 back into the head during the pause: the RESUME probe reads a:1 in the head,
    so a:2 is never pushed while a:1 is there; the swap starts again from its unload with a:1 as
    its old lane, a:2 handed the head again through the core. A push that returned is not probed
    (test_a_push_short_of_the_sensor_whose_tip_arrives_later_goes_on_unprobed)."""
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a1, a2 = slot.lanes
    probe = rig.module.probe

    def push_in(slot: Slot, h: Hooked, speed: float, budget: float | None = None) -> NoReturn:
        del probe.push_in  # the real push again at the RESUME
        raise probe_error(
            "head 1: the push of a:2 is blocked again with the path to the head clear"
        )

    probe.push_in = push_in
    assert tool(rig, 2) == []
    assert slot.current is a2
    rig.head_sensor.detected = True  # a:1 fed back into the head
    asked = probe_finds(rig, 0)
    rig.units_obj.handed.clear()
    started = len(a.feed.started)
    rig.sent.clear()
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == [True]
    assert asked == [slot]
    assert rig.sent[:2] == [
        "T0 A0",
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=220 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=220 MATERIAL=PLA",
    ]
    modes = [(lane, mode) for lane, mode, *_ in a.feed.started[started:]]
    assert modes == [
        (0, "assist_both"),  # a:1 follows the unload macro
        (0, "rollback"),
        (0, "rollback"),
        (1, "feed"),
        (1, "assist_both"),
    ]
    assert rig.units_obj.handed == [("a", 0), ("a", 1)]
    assert slot.current is a2 and slot.setting_for is a2
    assert rig.module.swapper.swap_pending is None


def test_the_swaps_spool_step_records_only_the_lane_in_the_head(tmp_path: Path) -> None:
    rig, _a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a1, a2 = slot.lanes
    assert slot.current is a1
    rig.module.swapper.set_head_spool(a2)  # a:2's spool written while a:1 is the head's lane
    assert slot.setting_for is None
    rig.module.swapper.set_head_spool(a1)
    assert slot.setting_for is a1


def test_a_spool_record_is_dropped_when_its_bay_empties(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, {"a:2": "ffffff", "a:3": "000000"})
    lanes = by_name["a"].lanes
    lanes[1]["insert"] = True
    lanes[2]["insert"] = None  # no sensors' report: not known empty
    rig.adapter.watch(rig.reactor.now)
    assert sorted(rig.module.spool_store.records) == ["a:2", "a:3"]
    lanes[1]["insert"] = False  # the spool taken out of its bay
    rig.adapter.watch(rig.reactor.now)
    assert sorted(rig.module.spool_store.records) == ["a:3"]
    saved = json.loads((tmp_path / "ace2k_u1_spools.json").read_text())
    assert sorted(saved) == ["a:3"]
    assert rig.module.get_status()["spools"] == {
        "a:3": {"vendor": "Generic", "type": "PLA", "color": "#000000"}
    }


def test_a_one_lane_head_shows_the_u1s_setting_before_the_lanes_record(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    ptc = fakes.FakePrintTaskConfig()
    for head in (0, 1):
        ptc.status["filament_type"][head] = "PETG"
        ptc.status["filament_color_rgba"][head] = "00FF00FF"
    rig.module.printer.objects["print_task_config"] = ptc
    lane = {(h.unit.name, h.lane): h for h in rig.adapter.hooked}
    by_name["a"].lanes[2]["insert"] = True
    # a:3 alone feeds head 2: the U1's setting of its head, there since Klipper started
    assert rig.module.spool_lanes.lane_spool(lane["a", 2]) == ("a:3", "#00ff00", "PETG", False)
    spools(rig, {"a:1": "ffffff", "a:3": "ffffff"})
    # a record set since, not written on the head, is newer: it speaks for the lane
    assert rig.module.spool_lanes.lane_spool(lane["a", 2]) == ("a:3", "#ffffff", "PLA", False)
    tick(rig)
    ptc.status["filament_type"][1] = "ABS"  # the head written since, bay full
    tick(rig)
    assert rig.module.spool_lanes.lane_spool(lane["a", 2]) == ("a:3", "#00ff00", "ABS", False)
    # a:1 shares head 1: its record, whatever the head's setting
    slot = rig.adapter.slot_of(lane["a", 0])
    rig.adapter.set_current(slot, lane["a", 0])
    slot.setting_for = lane["a", 0]
    assert rig.module.spool_lanes.lane_spool(lane["a", 0]) == ("a:1", "#ffffff", "PLA", False)
    # a one-lane head with no setting: the record
    ptc.status["filament_type"][1] = ""
    assert rig.module.spool_lanes.lane_spool(lane["a", 2]) == ("a:3", "#ffffff", "PLA", False)


def test_a_failed_restore_of_the_gcode_state_pauses_and_resume_goes_on_from_the_probe(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    real = rig.gcode.run_script_from_command

    def restore_fails(script: str) -> None:
        real(script)
        if script.startswith("RESTORE_GCODE_STATE"):
            raise RuntimeError("no state")

    rig.gcode.run_script_from_command = restore_fails
    assert tool(rig, 2) == []  # the put back's restore fails again: logged, the pause comes
    assert "ace2k_u1: head 1: a:1 → a:2 (T2) failed at restore: no state" in rig.gcode.lines
    assert "PAUSE" in rig.sent
    assert rig.module.swapper.swap_pending.step == "restore"
    rig.gcode.run_script_from_command = real
    probe_finds(rig, 1)
    rig.sent.clear()
    assert resume(rig) == [True]
    assert (
        "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent
    )  # from its spool: what the probe read
    assert rig.sent[-1] == "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0"
    assert rig.module.swapper.swap_pending is None


def test_a_spool_record_not_dropped_for_a_write_failure_is_logged_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, {"a:2": "ffffff"})
    lanes = by_name["a"].lanes
    lanes[1]["insert"] = True
    rig.adapter.watch(rig.reactor.now)
    lanes[1]["insert"] = False  # the spool taken out by hand

    def fails() -> NoReturn:
        raise OSError("disk full")

    monkeypatch.setattr(rig.module.spool_store, "write", fails)
    for _ in range(3):
        rig.adapter.watch(rig.reactor.now)
    assert "a:2" in rig.module.spool_store.records  # kept for the next tick
    said = [r for r in caplog.records if "spool record not dropped" in r.getMessage()]
    assert len(said) == 1
    monkeypatch.undo()
    rig.adapter.watch(rig.reactor.now)
    assert "a:2" not in rig.module.spool_store.records


def test_a_spool_record_set_on_an_empty_bay_is_kept_until_the_bay_fills_and_empties(
    tmp_path: Path,
) -> None:
    rig, by_name = tools_rig(tmp_path)
    lanes = by_name["a"].lanes
    lanes[1]["insert"] = False
    rig.adapter.watch(rig.reactor.now)
    spools(rig, {"a:2": "ffffff"})  # set before the spool goes in
    for _ in range(3):
        rig.reactor.now += 1.0
        rig.adapter.watch(rig.reactor.now)
    assert "a:2" in rig.module.spool_store.records
    lanes[1]["insert"] = True
    rig.adapter.watch(rig.reactor.now)
    lanes[1]["insert"] = False
    rig.adapter.watch(rig.reactor.now)
    assert "a:2" not in rig.module.spool_store.records


def test_a_swap_roll_back_that_runs_out_of_the_bay_keeps_the_spool_record(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.lanes[0]["insert"] = True
    rig.adapter.watch(rig.reactor.now)
    assert "a:1" in rig.module.spool_store.records
    a.feed.on_start = ends("runout", motor_mm=90.0, filament_mm=-80.0)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    a.lanes[0].update(insert=False, mode="idle")  # the roll back took it out of its bay
    rig.reactor.now += 1.0
    rig.adapter.watch(rig.reactor.now)
    assert "a:1" in rig.module.spool_store.records
    rig.reactor.now += 10.0
    rig.adapter.watch(rig.reactor.now)  # still empty, never emptied by hand
    assert "a:1" in rig.module.spool_store.records


@pytest.mark.parametrize(
    "event, later, kept",
    [
        ({"kind": "runout", "mode": "following"}, 2.0, True),  # a tail lane ran out
        ({"kind": "unloaded", "mode": "unloading"}, 2.0, True),
        ({"kind": "done", "mode": "rolling_back"}, 4.0, True),
        ({"kind": "done", "mode": "feeding"}, 2.0, False),
        ({"kind": "runout", "mode": "rolling_back"}, 6.0, False),  # long before: by hand
    ],
)
def test_a_bay_emptied_by_a_move_of_its_lane_keeps_the_spool_record(
    tmp_path: Path, event: dict[str, str], later: float, kept: bool
) -> None:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, {"a:3": "ffffff"})
    lanes = by_name["a"].lanes
    lanes[2]["insert"] = True
    rig.adapter.watch(rig.reactor.now)
    rig.adapter.on_feed_event(2, dict(event, seq=7, unit="a"))
    rig.reactor.now += later
    lanes[2].update(insert=False, mode="idle")
    rig.adapter.watch(rig.reactor.now)
    assert ("a:3" in rig.module.spool_store.records) is kept


def test_a_bay_emptied_while_its_lane_moves_keeps_the_spool_record(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, {"a:3": "ffffff"})
    lanes = by_name["a"].lanes
    lanes[2]["insert"] = True
    rig.adapter.watch(rig.reactor.now)
    lanes[2].update(insert=False, mode="rolling_back")
    rig.adapter.watch(rig.reactor.now)
    lanes[2]["mode"] = "idle"
    rig.reactor.now += 10.0
    rig.adapter.watch(rig.reactor.now)
    assert "a:3" in rig.module.spool_store.records


@pytest.mark.parametrize("held", [True, False])
def test_a_swap_roll_back_gives_the_lane_its_hold_back(tmp_path: Path, held: bool) -> None:
    rig, a = swap_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[0]
    h.hold = held  # an eject, a nudge or another step holding the lane before the roll back
    rig.module.swapper.rollback.run(h, 100.0)
    assert a.feed.started[-1][:2] == (0, "rollback")
    assert h.hold is held


def test_the_busy_retry_takes_its_window_and_period(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    h = rig.adapter.slot_of_head(0).lanes[0]
    a.feed.refusals = ["busy", "busy"]
    start = rig.reactor.monotonic()
    move = rig.adapter.retry_busy_start(
        h, lambda: a.feed.start_move(0, "assist_both", 0.0, 10.0), wait_s=1.0, period=0.25
    )
    assert move is not None and a.feed.attempts == 3
    assert rig.reactor.monotonic() - start == pytest.approx(0.5)
    a.feed.refusals = ["busy"]
    with pytest.raises(Exception, match="busy"):  # no window: the first busy is raised
        rig.adapter.retry_busy_start(
            h, lambda: a.feed.start_move(0, "assist_both", 0.0, 10.0), wait_s=0.0, period=0.25
        )
    assert a.feed.attempts == 4


# --- the outgoing tool's cool-down on a shared head -------------------------------------------


def order_is(rig: SimpleNamespace, order: list[int], t_index: int) -> None:
    """The plan's order set to `order` (T0 = a:1 and T2 = a:2 share head 1, T1 = a:3 is head 2) and
    the print at `t_index` in it; head 1 holds T0's lane."""
    rig.module.tracker.plan.order = order
    rig.module.tracker.plan.raw = list(order)  # no repeated T lines
    rig.module.tracker.t_index = rig.module.tracker.t_raw = t_index


def test_the_outgoing_cool_down_of_a_head_about_to_swap_is_kept(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    order_is(rig, [0, 2], 0)
    assert temp(rig, "M104", {"S": "70", "T": "0"}) == []
    assert temp(rig, "M109", {"S": "70", "T": "0"}) == []
    # nothing stores the value: it is neither applied nor kept (no "standby" record)
    assert not hasattr(rig.module.tracker.plan, "standby")
    assert 0 not in rig.module.tracker.plan.kept
    said = [ln for ln in rig.gcode.lines if "cool-down kept" in ln]
    assert said == ["ace2k_u1: T0's cool-down kept: head 1 is about to swap"]


def test_the_cool_down_before_a_t_on_another_head_is_applied_though_the_head_returns(
    tmp_path: Path,
) -> None:
    rig, _ = planned(tmp_path)
    order_is(rig, [0, 1, 2], 0)  # head 1 a:1 -> head 2 -> head 1 a:2: head 1 cools
    assert temp(rig, "M104", {"S": "70", "T": "0"}) == [{"S": "70", "T": "0"}]
    assert not [ln for ln in rig.gcode.lines if "cool-down kept" in ln]


def test_a_zero_before_any_t_is_kept_when_the_first_t_swaps_its_head(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    order_is(rig, [2, 0], -1)
    assert temp(rig, "M104", {"S": "0", "T": "0"}) == []
    assert rig.module.tracker.plan.off_held == {0: 0}


def test_a_failed_start_leaves_the_t_index_as_it_was(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    rig.print_stats.state = "paused"  # a new file loaded over a paused print: checked as any start
    rig.module.tracker.t_index = 5

    def refuse(gcmd: GCodeCommand) -> NoReturn:
        raise RuntimeError("the U1 refuses")

    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = refuse
    with pytest.raises(RuntimeError):
        rig.gcode.commands["SDCARD_PRINT_FILE"](
            Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE")
        )
    assert rig.module.tracker.t_index == 5


def test_the_cool_down_before_the_same_lane_again_is_applied(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    order_is(rig, [0, 0], 0)
    assert temp(rig, "M104", {"S": "70", "T": "0"}) == [{"S": "70", "T": "0"}]
    assert not [ln for ln in rig.gcode.lines if "cool-down kept" in ln]


def test_the_cool_down_of_a_head_not_used_again_is_applied(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    rig.module.tracker.t_index = (
        len(rig.module.tracker.plan.order) - 1
    )  # the last T: nothing follows
    assert temp(rig, "M104", {"S": "70", "T": "0"}) == [{"S": "70", "T": "0"}]


def test_a_value_at_the_printing_temperature_passes(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    order_is(rig, [0, 2], 0)
    plan_temp = rig.module.tracker.plan.tools[0]["temp"]
    got = temp(rig, "M104", {"S": f"{plan_temp:g}", "T": "0"})
    assert got == [{"S": f"{plan_temp:g}", "T": "0"}]


def test_the_cool_down_of_a_one_lane_head_is_applied(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    rig.module.tracker.t_index = 0
    tool_n = next(n for n, h in rig.module.tracker.plan.choice.items() if h.head == 1)  # a:3 alone
    assert temp(rig, "M104", {"S": "70", "T": str(tool_n)}) == [{"S": "70", "T": str(tool_n)}]


def test_each_t_of_the_print_is_counted_once_and_the_swaps_own_t_not_at_all(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    order_is(rig, [0, 2, 1], -1)
    tool(rig, 0)
    tool(rig, 2)  # swaps (faked) and counts as one
    tool(rig, 2, {"A": "0"})
    assert rig.module.tracker.t_index == 1


def forget_rig(tmp_path: Path) -> tuple[SimpleNamespace, dict[str, FakeAce2k], Hooked]:
    """Lane a:3 (head 2 of its own) with a Bambu tag read and an ACEK_SPOOL record, tags on."""
    rig, by_name = tools_rig(tmp_path)
    rig.adapter.tag_writer.setup_tags(
        rig.task_config,
        fakes.FakeFilamentParameters(TABLE),
        rig.print_stats,
        True,
        fakes.FakeToolhead("extruder3"),
    )
    rig.adapter.gcode = fakes.FakeGcode(rig.task_config)  # the tag policy's writes land here
    lane = by_name["a"].lanes[2]
    lane["insert"] = True
    lane["tag"] = dict(state="read", uid=BAMBU["uid"], source="tag", record=dict(BAMBU))
    spools(rig, {"a:3": "00ff00"})
    h = rig.adapter.hooked_lane(by_name["a"], 2)
    return rig, by_name, h


def tick(rig: SimpleNamespace, dt: float = 0.2) -> None:
    rig.adapter.watch(rig.reactor.now)
    rig.reactor.run_timers()
    rig.reactor.now += dt


def writes(rig: SimpleNamespace) -> list[str]:
    return [ln for ln in rig.adapter.gcode.scripts if ln.startswith("SET_PRINT_FILAMENT_CONFIG")]


def set_tag(by_name: dict[str, FakeAce2k], state: str, record: TagRecord | None) -> None:
    by_name["a"].lanes[2]["tag"] = dict(
        state=state, uid=(record or {}).get("uid"), source="tag", record=record
    )


def test_a_forgotten_tag_is_not_applied_again(tmp_path: Path) -> None:
    rig, by_name, h = forget_rig(tmp_path)
    tick(rig)
    tick(rig)
    assert len(writes(rig)) == 1  # the tag was applied once
    assert rig.module.spool_lanes.lane_spool(h) == (
        "a:3",
        "#ff0000",
        "PLA",
        False,
    )  # the tag speaks
    rig.gcode.lines.clear()
    run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3"})
    assert rig.gcode.lines == ["ACEK_RFID_FORGET LANE=3 UNIT=a"]  # the core forgets it too
    assert rig.module.get_status()["lanes"]["a:3"]["tag_forgotten"] is True
    set_tag(by_name, "pending", None)
    tick(rig)
    set_tag(by_name, "read", dict(BAMBU))  # the unit reads the same tag on the next move
    for _ in range(3):
        tick(rig)
    assert len(writes(rig)) == 1  # nothing more written from the forgotten lane
    # the lane's own spool is its record now (the head keeps the tag's setting until Edit spool)
    assert rig.module.spool_lanes.own_record(h)[0] == "spool"


def test_read_tag_again_undoes_the_forget(tmp_path: Path) -> None:
    rig, by_name, h = forget_rig(tmp_path)
    tick(rig)
    tick(rig)
    run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3"})
    set_tag(by_name, "pending", None)
    tick(rig)
    rig.gcode.lines.clear()
    run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3", "READ": "1"})
    assert rig.gcode.lines == ["ACEK_RFID_FORGET LANE=3 UNIT=a"]
    assert h.tag_forgotten is False
    set_tag(by_name, "pending", None)  # the unit's tag goes pending after the core's forget
    tick(rig)
    set_tag(by_name, "read", dict(BAMBU))  # the same uid, read again
    for _ in range(3):
        tick(rig)
    assert len(writes(rig)) == 2  # the head is written again, for that tag
    assert writes(rig)[1] == writes(rig)[0] and "FF0000FF" in writes(rig)[1]
    assert rig.module.spool_lanes.lane_spool(h) == ("a:3", "#ff0000", "PLA", False)


def test_the_forgotten_mark_goes_when_the_spool_is_taken_out_by_hand(tmp_path: Path) -> None:
    rig, by_name, h = forget_rig(tmp_path)
    tick(rig)  # the bay has been seen full
    run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3"})
    tick(rig)
    assert h.tag_forgotten is True  # a tick with the bay still full keeps it
    by_name["a"].lanes[2]["insert"] = False
    tick(rig)
    assert h.tag_forgotten is False
    assert rig.module.get_status()["lanes"]["a:3"]["tag_forgotten"] is False


def test_the_forgotten_mark_stays_when_the_adapters_own_move_empties_the_bay(
    tmp_path: Path,
) -> None:
    rig, by_name, h = forget_rig(tmp_path)
    tick(rig)  # the bay has been seen full
    run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3"})
    tick(rig)
    rig.adapter.moved_out(h)  # an eject or a roll back of the adapter's
    by_name["a"].lanes[2]["insert"] = False
    tick(rig)
    assert h.tag_forgotten is True
    # a filament inserted into the empty bay is another spool: the mark goes and its tag applies
    before = len(writes(rig))
    by_name["a"].lanes[2]["insert"] = True
    set_tag(by_name, "read", dict(BAMBU, uid="00000002"))
    for _ in range(3):
        tick(rig)
    assert h.tag_forgotten is False
    assert len(writes(rig)) == before + 1


def test_a_read_restored_after_a_restart_sets_the_lanes_material_until_forgotten(
    tmp_path: Path,
) -> None:
    # After a restart that reset the unit, ace2k gives a full bay its last read back: shown as
    # read, marked restored. The adapter takes it as the lane's tag, and a forget still wins.
    rig, by_name, h = forget_rig(tmp_path)
    rig.module.spool_store.records.clear()  # no Edit spool record: only the tag speaks
    by_name["a"].lanes[2]["tag"] = dict(
        state="read", uid=BAMBU["uid"], source="restored", record=dict(BAMBU)
    )
    tick(rig)
    tick(rig)
    assert rig.module.get_status()["lanes"]["a:3"]["material_set"] is True
    assert rig.module.spool_lanes.lane_spool(h) == ("a:3", "#ff0000", "PLA", False)
    assert len(writes(rig)) == 1 and "FF0000FF" in writes(rig)[0]  # the head is set from it
    run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3"})
    assert h.tag_forgotten is True
    # the head's setting written from the forgotten read no longer speaks for the lane
    assert rig.module.get_status()["lanes"]["a:3"]["material_set"] is False


def test_a_tag_forget_needs_a_tag_read_and_a_hooked_lane(tmp_path: Path) -> None:
    rig, by_name, h = forget_rig(tmp_path)
    rig.gcode.lines.clear()
    set_tag(by_name, "no_tag", None)
    with pytest.raises(ValueError, match="no tag read"):
        run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3"})
    assert h.tag_forgotten is False and rig.gcode.lines == []
    rig.module.adapter = None
    with pytest.raises(ValueError, match="not hooked"):
        run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3"})


def test_a_tag_forget_of_a_lane_not_hooked_is_refused(tmp_path: Path) -> None:
    rig, _by_name, h = forget_rig(tmp_path)
    for key in [k for k, v in rig.adapter.by_key.items() if v is h]:
        del rig.adapter.by_key[key]
    with pytest.raises(ValueError, match="lane a:3 feeds no head: nothing to forget"):
        run(rig, "ACEK_TAG_FORGET", {"UNIT": "a", "LANE": "3"})


def test_a_blocked_push_shows_in_the_status(tmp_path: Path) -> None:
    rig, by_name, h = forget_rig(tmp_path)
    assert rig.module.get_status()["lanes"]["a:3"]["push_end"] is None
    h.push_end, h.push_end_seq = "blocked", 3  # push_to_head's final event
    assert rig.module.get_status()["lanes"]["a:3"]["push_end"] == "blocked"
    # the push's own event, late: kept
    rig.adapter.on_feed_event(2, {"unit": "a", "kind": "blocked", "seq": 3})
    assert h.push_end == "blocked"
    # a short manual feed starts and ends between two ticks: its final event clears it
    rig.adapter.on_feed_event(2, {"unit": "a", "kind": "done", "seq": 4})
    assert rig.module.get_status()["lanes"]["a:3"]["push_end"] is None
    h.push_end, h.push_end_seq = "blocked", 5
    by_name["a"].lanes[2]["mode"] = "feed"  # a move the events missed: the report shows it
    h.ended_at = None
    tick(rig)
    assert h.push_end is None


def test_the_lanes_part_of_the_status_is_built_again_only_on_change(tmp_path: Path) -> None:
    rig, _, h = forget_rig(tmp_path)
    first = rig.module.get_status()["lanes"]
    assert rig.module.get_status()["lanes"] is first
    h.push_end = "blocked"
    second = rig.module.get_status()["lanes"]
    assert second is not first and second["a:3"]["push_end"] == "blocked"
    assert first["a:3"]["push_end"] is None  # a published copy is never altered


# --- the plan's order on the file's real T lines (I-1) -------------------------------------------


def file_ts() -> list[int]:
    """The fixture's T lines as the file runs them, repeats included."""
    text = (FIXTURES / "8_colours_no_tower.gcode").read_text()
    return [int(line[1:]) for line in text.splitlines() if re.fullmatch(r"T\d+", line)]


def test_the_files_real_t_lines_keep_the_index_on_the_plans_order(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    order = rig.module.tracker.plan.order
    ts = file_ts()
    assert ts[:4] == [0, 0, 0, 1]  # the start's T{initial_extruder} lines repeat T0
    for n in ts:
        tool(rig, n)
        assert order[rig.module.tracker.t_index] == n
        tool(rig, n)  # a bare T of the same tool again (the U1's pre-extrude): not counted
        assert order[rig.module.tracker.t_index] == n
    assert rig.module.tracker.t_index == len(order) - 1


def test_the_cool_down_decision_follows_the_files_real_t_lines(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    choice = rig.module.tracker.plan.choice

    def swap(gcmd: GCodeCommand, slot: Slot, h: Hooked, n: int) -> bool:
        rig.adapter.set_current(slot, h)
        return True

    rig.module.swapper.swap = swap
    ts = file_ts()
    decided = 0
    for i, n in enumerate(ts):
        tool(rig, n)
        h = choice[n]
        if len(rig.adapter.slot_of(h).lanes) < 2:
            continue
        nxt = ts[i + 1] if i + 1 < len(ts) else None  # the very next T line, repeats included
        nh = None if nxt is None else choice[nxt]
        want_kept = nh is not None and nh.head == h.head and nh is not h
        got = temp(rig, "M104", {"S": "70", "T": str(n)})
        assert (got == []) is want_kept, (i, n, nxt)
        decided += 1
    assert decided > 0


def test_the_start_gcodes_cool_down_before_a_repeated_t_is_applied(tmp_path: Path) -> None:
    """The file's start runs T0, M104 T0 S130, T0, M109 T0 S130, T0, then the print; the
    plan's order merges the T0 lines, but the very next T line after the first T0 is T0 again
    (the same lane): the start's low values are applied. Only before T2 (another lane of head
    1) is a cool-down kept."""
    rig, _ = planned(tmp_path)
    rig.module.tracker.plan.order = [0, 2]
    rig.module.tracker.plan.raw = [0, 0, 0, 2]
    rig.module.tracker.t_index = rig.module.tracker.t_raw = -1
    assert temp(rig, "M104", {"S": "140", "T": "0"}) == [{"S": "140", "T": "0"}]
    tool(rig, 0)
    assert temp(rig, "M104", {"S": "130", "T": "0"}) == [{"S": "130", "T": "0"}]
    tool(rig, 0)
    assert temp(rig, "M109", {"S": "130", "T": "0"}) == [{"S": "130", "T": "0"}]
    tool(rig, 0)
    tool(rig, 0)  # a bare T of the same tool again (the U1's pre-extrude): not counted
    assert rig.module.tracker.t_index == 0 and rig.module.tracker.t_raw == 2
    assert temp(rig, "M104", {"S": "70", "T": "0"}) == []  # before T2: kept
    said = [ln for ln in rig.gcode.lines if "cool-down kept" in ln]
    assert said == ["ace2k_u1: T0's cool-down kept: head 1 is about to swap"]


def test_a_t_typed_while_paused_is_not_counted(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    tool(rig, 0)
    rig.print_stats.state = "paused"
    tool(rig, 1)
    assert rig.module.tracker.t_index == 0


def test_a_t_typed_for_the_pending_head_while_paused_keeps_the_pending_t_owed(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    assert tool(rig, 2) == []  # head 1 a:1 -> a:2 for T2 fails: pending, paused
    index = rig.module.tracker.t_index
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    probe_finds(rig, 0)
    # the operator's T0 (a:1) while paused: the pending swap is finished, then a:1 goes back in
    assert tool(rig, 0) == [{}]
    slot = rig.adapter.slot_of_head(0)
    assert slot.current.lane == 0
    assert rig.module.tracker.t_index == index  # typed while paused: not counted
    assert [owed.tool for owed in rig.module.swapper.tools_owed] == [2]
    # RESUME: the file's T2 owed puts a:2 back in its head, then the U1's T2, then its RESUME
    t2 = []
    rig.gcode.commands_orig["T2"] = lambda gcmd: t2.append(gcmd.params)
    resume_marked(rig)
    assert slot.current.lane == 1
    assert t2 == [{}]
    assert rig.sent[-1] == "<U1 RESUME>"
    assert rig.module.swapper.tools_owed == [] and rig.module.swapper.swap_pending is None


def other_head_swaps(rig: SimpleNamespace) -> list[tuple[int, int, object]]:
    """Head 4's swaps (a b lane) recorded and taken as done; head 1's run for real."""
    real, swaps = rig.module.swapper.swap, []

    def swap(
        gcmd: GCodeCommand,
        slot: Slot | None,
        new: Hooked,
        tool: int,
        params: object = ace2k_u1_swap.UNREAD,
    ) -> bool:
        if slot is not None and slot.head == 3:
            swaps.append((new.lane, tool, params))
            slot.current = new
            return True
        return real(gcmd, slot, new, tool, params=params)

    rig.module.swapper.swap = swap
    return swaps


def file_t(
    rig: SimpleNamespace, n: int, params: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """The file's T<n>, run while the print prints (its PAUSE pauses it again)."""
    rig.print_stats.state = "printing"
    rig.pause_resume.is_paused = False
    return tool(rig, n, params)


def u1_tools(rig: SimpleNamespace, *tools: int) -> list[tuple[str, dict[str, str]]]:
    order = []
    for n in tools:
        rig.gcode.commands_orig[f"T{n}"] = lambda gcmd, n=n: order.append((f"T{n}", gcmd.params))
    return order


def test_a_t_of_another_head_refused_while_printing_runs_at_resume_after_the_pending_one(
    tmp_path: Path,
) -> None:
    """The file's T5 (head 4, b:4 not in) refused while head 1's swap is pending is
    owed: RESUME finishes the swap, runs T2, then T5 through the T path (its head swaps), then
    the U1's RESUME; the T's are not counted again."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    assert tool(rig, 2) == []  # pending, paused
    assert file_t(rig, 5, {"A": "1"}) == []  # the file goes on: its T5 refused and paused
    index = rig.module.tracker.t_index
    assert [e.tool for e in rig.module.swapper.tools_owed] == [5]
    swaps = other_head_swaps(rig)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    probe_finds(rig, 0)
    order = u1_tools(rig, 2, 5)
    resume_marked(rig)
    assert order == [("T2", {}), ("T5", {"A": "1"})]
    assert [(lane, n) for lane, n, _p in swaps] == [(3, 5)]  # b:4 put in head 4 first
    assert rig.sent[-1] == "<U1 RESUME>"
    assert rig.adapter.slot_of_head(0).current.lane == 1
    assert rig.module.swapper.tools_owed == [] and rig.module.swapper.swap_pending is None
    assert rig.module.tracker.t_index == index


def test_an_owed_t_that_fails_at_resume_keeps_it_and_the_rest_owed(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    tool(rig, 2)
    file_t(rig, 5)  # the file's: refused, owed
    file_t(rig, 1)  # head 2, one lane: refused, owed
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    probe_finds(rig, 0)
    order = u1_tools(rig, 2, 1)

    def t5_fails(gcmd: GCodeCommand) -> NoReturn:
        raise ValueError("Extruder busy")

    other_head_swaps(rig)
    rig.gcode.commands_orig["T5"] = t5_fails
    resume_marked(rig)
    assert order == [("T2", {})]
    assert "<U1 RESUME>" not in rig.sent
    assert [e.tool for e in rig.module.swapper.tools_owed] == [5, 1]
    rig.gcode.commands_orig["T5"] = lambda gcmd: order.append(("T5", gcmd.params))
    resume_marked(rig)
    assert order == [("T2", {}), ("T5", {}), ("T1", {})]
    assert rig.sent[-1] == "<U1 RESUME>" and rig.module.swapper.tools_owed == []


def test_a_t_typed_while_paused_and_refused_is_not_owed_and_nothing_is_owed_twice(
    tmp_path: Path,
) -> None:
    """Only the file's T's are owed; a console T while paused says its refusal."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    tool(rig, 2)  # pending, paused
    assert tool(rig, 5) == []  # typed at the console while paused
    line = "ace2k_u1: T5 refused: head 1: a:1 → a:2 (T2) is pending at roll back — RESUME it first"
    assert rig.gcode.lines[-1] == line
    assert rig.module.swapper.tools_owed == []
    file_t(rig, 5)
    file_t(rig, 5)  # the same T again: owed once
    assert [e.tool for e in rig.module.swapper.tools_owed] == [5]


def test_an_earlier_owed_t_is_kept_when_the_pending_swaps_t_is_added(tmp_path: Path) -> None:
    """The T typed for the pending head while paused owes the pending swap's T without dropping
    the T owed before it; the pending swap's T, the file's first, runs first."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    tool(rig, 2)  # head 1 a:1 -> a:2 pending, paused
    file_t(rig, 5)  # the file's, refused: owed
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    probe_finds(rig, 0)
    assert tool(rig, 0) == [{}]  # the pending swap finished, then a:1 back for the typed T0
    assert [e.tool for e in rig.module.swapper.tools_owed] == [2, 5]
    tool(rig, 0)  # typed again: the same pending entry is not owed twice
    assert [e.tool for e in rig.module.swapper.tools_owed] == [2, 5]
    swaps = other_head_swaps(rig)
    order = u1_tools(rig, 2, 5)
    resume_marked(rig)
    assert order == [("T2", {}), ("T5", {})]
    assert [n for _lane, n, _p in swaps] == [5]
    assert rig.adapter.slot_of_head(0).current.lane == 1
    assert rig.sent[-1] == "<U1 RESUME>" and rig.module.swapper.tools_owed == []


# --- the manual feed and the active lane of a shared head ----------------------------------------


def feed(rig: SimpleNamespace, unit: str, lane: int) -> list[dict[str, str]]:
    calls = []
    rig.gcode.commands_orig["ACEK_FEED"] = lambda gcmd: calls.append(gcmd.params)
    rig.gcode.commands["ACEK_FEED"](Gcmd({"UNIT": unit, "LANE": str(lane), "LENGTH": "50"}))
    return calls


def test_a_manual_feed_of_a_parked_lane_into_a_set_head_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])  # a:1 in head 1
    rig.left.runout_sensor[1].detected = True
    with pytest.raises(ValueError, match="head 1 holds another lane's filament: unload it first"):
        feed(rig, "a", 2)
    assert feed(rig, "a", 1) != []  # the head's own lane: as before
    assert feed(rig, "a", 3) != []  # a one-lane head: as before
    rig.left.runout_sensor[1].detected = False  # head 1 empty: the parked lane may feed
    assert feed(rig, "a", 2) != []


def test_a_manual_feed_the_adapter_cannot_name_reaches_ace2k_as_it_is(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])  # a:1 in head 1, its sensor set
    rig.left.runout_sensor[1].detected = True
    calls = []
    rig.gcode.commands_orig["ACEK_FEED"] = lambda gcmd: calls.append(gcmd.params)
    # no unit c: ace2k's own handler refuses it
    rig.gcode.commands["ACEK_FEED"](Gcmd({"UNIT": "c", "LANE": "2", "LENGTH": "50"}))
    assert len(calls) == 1


def test_the_manual_feed_is_not_wrapped_without_a_group(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1, a:2, a:3, a:4")
    assert rig.gcode.commands["ACEK_FEED"].__qualname__ == "Gcode.stub.<locals>.handler"


def lane_active(rig: SimpleNamespace) -> list[dict[str, str]]:
    calls = []
    rig.gcode.stub("ACEK_LANE_ACTIVE")
    rig.gcode.commands_orig["ACEK_LANE_ACTIVE"] = lambda gcmd: calls.append(gcmd.params)
    rig.module.wraps.wrap_lane_active(rig.gcode)
    return calls


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_lane_active_on_a_shared_head_is_refused_during_a_planned_print(
    tmp_path: Path, state: str
) -> None:
    rig, _ = planned(tmp_path)
    calls = lane_active(rig)
    rig.print_stats.state = state
    cmd = rig.gcode.commands["ACEK_LANE_ACTIVE"]
    with pytest.raises(ValueError, match="head 1: ACEK_LANE_ACTIVE refused"):
        cmd(Gcmd({"UNIT": "a", "LANE": "2"}, "ACEK_LANE_ACTIVE"))
    assert calls == []
    cmd(Gcmd({"UNIT": "a", "LANE": "3"}, "ACEK_LANE_ACTIVE"))  # a one-lane head: as before
    assert len(calls) == 1
    rig.print_stats.state = "complete"
    cmd(Gcmd({"UNIT": "a", "LANE": "2"}, "ACEK_LANE_ACTIVE"))  # the print over: as before
    assert len(calls) == 2


def test_lane_active_without_lane_in_a_planned_print_reaches_ace2ks_handler(tmp_path: Path) -> None:
    """No LANE= names no lane: left to ace2k's own handler, which refuses it."""
    rig, _ = planned(tmp_path)
    calls = lane_active(rig)
    rig.gcode.commands["ACEK_LANE_ACTIVE"](Gcmd({"UNIT": "a"}, "ACEK_LANE_ACTIVE"))
    assert calls == [{"UNIT": "a"}]


@pytest.mark.parametrize(
    "command, params",
    [("ACEK_SPOOL", {"TYPE": "PLA", "COLOR": "ffffff"}), ("ACEK_TAG_FORGET", {})],
)
def test_a_lane_of_a_unit_declared_but_not_loaded_is_a_gcode_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, params: dict[str, str]
) -> None:
    """The coordinator gives no unit for one the config declares that is not loaded yet:
    the G-code error, as ACEK_EJECT's, never an internal error."""
    rig, _ = tools_rig(tmp_path)
    monkeypatch.setattr(rig.units_obj, "unit_for", lambda gcmd: None)
    with pytest.raises(ValueError, match="^ace2k_u1: lane 2: its unit is not loaded yet$"):
        run(rig, command, dict(params, UNIT="c", LANE="2"))


def test_lane_active_whose_head_lanes_fail_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = tools_rig(tmp_path)
    calls = lane_active(rig)

    def fails() -> NoReturn:
        raise RuntimeError("no coordinator")

    rig.adapter.follow.adopt_active = fails
    rig.gcode.commands["ACEK_LANE_ACTIVE"](Gcmd({"UNIT": "a", "LANE": "2"}, "ACEK_LANE_ACTIVE"))
    assert len(calls) == 1
    assert any("ACEK_LANE_ACTIVE's head lanes" in r.getMessage() for r in caplog.records)


MISSING = (
    "ace2k_u1: ACEK_SWAP_LOAD is not defined: copy it from the release's config/ace2k-u1.cfg"
    " — a print that uses a shared head is refused"
)


def drop_macro(rig: SimpleNamespace, name: str) -> None:
    """The macro gone from the config: neither its object nor its command."""
    del rig.module.printer.objects[f"gcode_macro {name}"]
    rig.gcode.register_command(name, None)


def test_a_macro_of_a_lower_case_section_is_found_by_its_command(tmp_path: Path) -> None:
    """Klipper names a [gcode_macro acek_swap_unload] object as its section is written and
    registers its command upper-case: the command is what the swap calls."""
    rig, _ = tools_rig(tmp_path)
    for name in ("ACEK_SWAP_UNLOAD", "ACEK_SWAP_LOAD"):
        macro = rig.module.printer.objects.pop(f"gcode_macro {name}")
        rig.module.printer.objects[f"gcode_macro {name.lower()}"] = macro
    assert "ACEK_SWAP_UNLOAD" in rig.gcode.ready_gcode_handlers  # the rig registers both
    assert rig.module.swapper.missing_swap_macros() == []
    rig.gcode.register_command("ACEK_SWAP_LOAD", None)
    assert rig.module.swapper.missing_swap_macros() == ["ACEK_SWAP_LOAD"]


def test_a_missing_swap_macro_is_said_at_the_hook(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    assert not [ln for ln in rig.gcode.lines if "is not defined" in ln]
    drop_macro(rig, "ACEK_SWAP_LOAD")
    rig.gcode.lines.clear()
    rig.module._hook(rig.reactor.now)
    assert [ln for ln in rig.gcode.lines if "is not defined" in ln] == [MISSING]


def test_both_missing_macros_are_said_unload_first(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    drop_macro(rig, "ACEK_SWAP_UNLOAD")
    drop_macro(rig, "ACEK_SWAP_LOAD")
    assert rig.module.swapper.missing_swap_macros() == ["ACEK_SWAP_UNLOAD", "ACEK_SWAP_LOAD"]
    rig.gcode.lines.clear()
    rig.module._hook(rig.reactor.now)
    said = [ln for ln in rig.gcode.lines if "is not defined" in ln]
    assert [ln.split()[1] for ln in said] == ["ACEK_SWAP_UNLOAD", "ACEK_SWAP_LOAD"]


def test_no_macro_line_without_a_shared_head(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1, a:2, a:3, a:4")
    drop_macro(rig, "ACEK_SWAP_LOAD")
    rig.gcode.lines.clear()
    rig.module._hook(rig.reactor.now)
    assert not [ln for ln in rig.gcode.lines if "is not defined" in ln]


def test_a_planned_start_that_swaps_is_refused_without_its_macros(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    assert rig.module.tracker.plan.heads[0] > 0
    drop_macro(rig, "ACEK_SWAP_LOAD")
    with pytest.raises(ValueError, match="ACEK_SWAP_LOAD is not defined") as err:
        print_start(rig)
    assert str(err.value) == MISSING


def test_a_planned_start_on_a_shared_head_with_no_swap_is_refused_without_its_macros(
    tmp_path: Path,
) -> None:
    """A refill on that head needs both macros: refused before any head is probed or handed."""
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.module.tracker.plan.heads = {head: 0 for head in rig.module.tracker.plan.heads}
    drop_macro(rig, "ACEK_SWAP_LOAD")
    rig.left.runout_sensor[1].detected = True  # head 1 set: a start would probe it
    probed = []
    rig.module.probe.which_lane = lambda slot: probed.append(slot) or slot.lanes[0]
    with pytest.raises(ValueError) as err:
        print_start(rig)
    assert str(err.value) == MISSING
    assert probed == [] and rig.units_obj.handed == []
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []


def test_a_start_with_no_plan_on_a_shared_head_is_refused_without_its_macros(
    tmp_path: Path,
) -> None:
    """five.gcode: T0 alone on head 1 (shared), T1 on head 2: one tool per head, no swap."""
    rig, _ = tools_rig(tmp_path)
    lanes_named(rig)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    drop_macro(rig, "ACEK_SWAP_LOAD")
    with pytest.raises(ValueError) as err:
        print_start(rig, name="five.gcode")
    assert str(err.value) == MISSING
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []


def test_a_start_on_heads_with_one_lane_each_starts_without_the_macros(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.task_config.map_table = [1, 2] + [1] * 30  # T0 on head 2, T1 on head 3: one lane each
    drop_macro(rig, "ACEK_SWAP_UNLOAD")
    drop_macro(rig, "ACEK_SWAP_LOAD")
    assert len(print_start(rig, name="five.gcode")) == 1


def test_a_start_refused_with_both_macros_missing_names_both(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    drop_macro(rig, "ACEK_SWAP_UNLOAD")
    drop_macro(rig, "ACEK_SWAP_LOAD")
    with pytest.raises(ValueError) as err:
        print_start(rig)
    assert str(err.value) == (
        "ace2k_u1: ACEK_SWAP_UNLOAD and ACEK_SWAP_LOAD are not defined: copy them from the"
        " release's config/ace2k-u1.cfg — a print that uses a shared head is refused"
    )


def test_a_swap_whose_load_macro_is_missing_fails_at_its_load(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    drop_macro(rig, "ACEK_SWAP_LOAD")
    assert tool(rig, 2) == []
    assert any("failed at load: ACEK_SWAP_LOAD is not defined" in ln for ln in rig.gcode.lines)
    assert not [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD")]
    # failed before anything is sent: the load's own T0 A0 never went (the unload's did)
    assert rig.sent.count("T0 A0") == 1


def test_a_swap_whose_unload_macro_is_missing_fails_at_its_unload(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    drop_macro(rig, "ACEK_SWAP_UNLOAD")
    assert tool(rig, 2) == []
    assert any("failed at unload: ACEK_SWAP_UNLOAD is not defined" in ln for ln in rig.gcode.lines)
    assert not [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_UNLOAD")]
    # failed before anything is sent or changed: no head selected, the old lane never armed
    assert "T0 A0" not in rig.sent
    assert a.feed.started == []


def test_the_macros_get_each_lanes_material(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PETG", "COLOR": "ff0000"})
    tool(rig, 2)
    unload = [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_UNLOAD")]
    load = [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD")]
    assert unload[0].endswith(" MATERIAL=PLA")  # the old lane's (the head's setting)
    assert load[0].endswith(" MATERIAL=PETG")  # the new lane's spool


def test_a_material_is_kept_to_one_word_for_the_macro_line(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[1]
    rig.module.spool_lanes.lane_spool = lambda lane: types.SimpleNamespace(material="pla silk")
    assert rig.module.spool_lanes.material(h) == "PLA SILK"  # unload_temps reads it as written
    assert ace2k_u1_config.material_param("PLA SILK") == "PLASILK"


def test_a_new_lane_with_no_spool_gets_no_material_from_the_old_lanes_setting(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    rig.task_config.status["filament_type"][0] = "PLA"  # the head's setting, a:1's
    h = rig.adapter.slot_of_head(0).lanes[1]  # a:2: no spool record
    rig.module.spool_lanes.lane_spool = lambda lane: types.SimpleNamespace(material=None)
    assert rig.module.spool_lanes.material(h) == ""  # the load's MATERIAL= is empty, not PLA
    assert rig.module.spool_lanes.material(h, head_setting=True) == "PLA"  # an unload's old lane


def test_unload_temps_match_a_material_written_either_way(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.unload_temps = {"PLASILK": 205.0}
    h = rig.adapter.slot_of_head(0).lanes[0]
    assert rig.module.swapper._unload_temp(h, "PLA SILK") == 205.0


def test_the_lanes_own_spool_from_a_tag_is_catalogued_as_the_tag_policy_writes_it(
    tmp_path: Path,
) -> None:
    # a swap writes the head's setting from _own_spool: a tag name the U1 does not list is
    # Generic there too, as the tag policy writes it
    rig, by_name, h = forget_rig(tmp_path)
    set_tag(by_name, "read", dict(BAMBU, name="PLA Lite"))
    assert rig.module.spool_lanes.own_spool(h) == ("Generic", "PLA", "", "FF0000FF")
    set_tag(by_name, "read", dict(BAMBU))  # PLA Basic: listed in the fake table
    assert rig.module.spool_lanes.own_spool(h) == ("BambuLab", "PLA", "Basic", "FF0000FF")


def parked_load(
    rig: SimpleNamespace,
    a: FakeAce2k,
    lane: int,
    mm: float,
    kind: str = "loaded",
    tag: TagRecord | None = None,
) -> None:
    """lane's bay seen empty under the park, then a spool put in and the unit's own load's end."""
    rig.adapter.watch(rig.reactor.monotonic())
    a.lanes[lane].update(insert=True, mode="loading")
    if tag is not None:
        a.lanes[lane]["tag"] = tag
    rig.adapter.watch(rig.reactor.monotonic())
    rig.adapter.on_feed_event(
        lane, {"kind": kind, "mode": "loading", "unit": "a", "seq": 0, "filament_mm": mm}
    )
    a.lanes[lane]["mode"] = "idle"
    rig.reactor.run_timers()


def test_a_park_load_of_several_moves_is_judged_by_its_whole_travel(tmp_path: Path) -> None:
    """The park's pull paused for a tag read at 100 mm and went on to the parking point. The
    loaded event carries the whole load, 300 mm, so the lane is not short of the probe."""
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    second = rig.adapter.slot_of_head(0).lanes[1]
    parked_load(rig, a, 1, 300.0)
    assert not second.at_grip


def test_a_park_load_whose_tag_was_read_on_the_way_asks_no_second_read(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    parked_load(rig, a, 1, 300.0, tag={"state": "read", "uid": "04AA"})
    assert a.rfid.reads == []


# --- branches the split moves, driven for their outcome (characterisation) -----------------------


def test_a_start_with_no_plan_reads_an_unreadable_sensor_as_set_and_probes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.adapter.head_set = lambda h: (_ for _ in ()).throw(RuntimeError("no sensor"))
    rig.module.probe.which_lane = lambda slot: slot.lanes[1]
    with caplog.at_level(logging.ERROR):
        assert len(print_start(rig, name="five.gcode")) == 1
    assert any(
        r.getMessage() == "ace2k_u1: head 1's sensor unread at a start" and r.exc_info
        for r in caplog.records
    )
    assert rig.adapter.slot_of_head(0).current is rig.adapter.slot_of_head(0).lanes[1]


def test_a_start_with_no_plan_whose_probe_is_refused_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.left.runout_sensor[1].detected = True

    def refuse(slot: Slot) -> NoReturn:
        raise RuntimeError("a follower did not end")

    rig.module.probe.which_lane = refuse
    with pytest.raises(ValueError) as err:
        print_start(rig, name="five.gcode")
    assert str(err.value) == "ace2k_u1: head 1: a follower did not end"
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []
    assert rig.adapter.slot_of_head(0).current is None


def test_a_start_with_no_plan_whose_hand_over_is_refused_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    (tmp_path / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
    rig.left.runout_sensor[1].detected = True
    rig.module.probe.which_lane = lambda slot: slot.lanes[1]

    def refuse(slot: Slot, h: Hooked) -> NoReturn:
        raise RuntimeError("the core refused")

    rig.adapter.head_watch.hand_head = refuse
    with pytest.raises(ValueError) as err:
        print_start(rig, name="five.gcode")
    assert str(err.value) == "ace2k_u1: head 1: the core refused"
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == []


def test_a_planned_start_whose_probe_is_refused_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    rig.left.runout_sensor[1].detected = True

    def refuse(slot: Slot) -> NoReturn:
        raise RuntimeError("a follower did not end")

    rig.module.probe.which_lane = refuse
    with pytest.raises(ValueError) as err:
        print_start(rig)
    assert str(err.value) == "ace2k_u1: head 1: a follower did not end"
    assert rig.gcode.orig_calls("SDCARD_PRINT_FILE") == [] and rig.units_obj.handed == []


def test_the_end_unload_without_an_adapter_does_nothing(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    rig.module.adapter = None
    timers = len(rig.reactor.timers)
    assert rig.module._end_unload({"choice": {}}) is None
    assert len(rig.reactor.timers) == timers  # no retry timer registered


def test_the_end_unload_says_a_refused_eject_and_goes_on_with_the_next_lane(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = end_unload_rig(tmp_path)
    for head, last in ((0, 1), (3, 3)):
        slot = rig.adapter.slot_of_head(head)
        rig.adapter.set_current(slot, slot.lanes[last])
    rig.module.watch_print(rig.reactor.now)
    ejected = []

    def start_eject(lane: int, unit: FakeAce2k | None = None) -> None:
        if (unit.name, lane + 1) == ("a", 1):
            raise ace2k_u1_eject.EjectRefused("ace2k_u1: lane a:1: eject refused: busy")
        if (unit.name, lane + 1) == ("b", 1):
            raise RuntimeError("boom")
        ejected.append((unit.name, lane + 1))

    rig.adapter.ejector.start_eject = start_eject
    rig.print_stats.state = "complete"
    with caplog.at_level(logging.ERROR):
        rig.module.watch_print(rig.reactor.now)
        rig.reactor.run_async()
        rig.reactor.run_timers()
    assert "ace2k_u1: lane a:1: eject refused: busy" in rig.gcode.lines
    assert any(
        r.getMessage() == "ace2k_u1: lane b:1 unload at the print's end" and r.exc_info
        for r in caplog.records
    )
    assert sorted(ejected) == [("b", 2), ("b", 3)]  # the others still went


def test_a_roll_back_with_no_end_is_stopped_and_fails_the_swap(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if rig.in_free_check:  # the free check ends; the swap's own roll back never does
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    assert tool(rig, 2) == []
    assert a.feed.log.count(("stop", 0)) >= 1 and a.feed.log[-1][0] == "stop"
    assert "ace2k_u1: head 1: a:1 → a:2 (T2) failed at roll back: a:1 roll back timed out" in (
        rig.gcode.lines
    )
    assert rig.module.swapper.swap_pending.step == "roll back"


def test_a_swap_from_an_unknown_lane_moves_nothing(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    state = SwapState(slot, None, slot.lanes[1], 2)
    started = len(a.feed.started)
    with pytest.raises(RuntimeError, match="^the lane in head 1 is unknown$"):
        rig.module.swapper.swap_step("unload", state)
    assert len(a.feed.started) == started and rig.sent == []


def test_the_step_table_is_the_swaps_steps_in_order(tmp_path: Path) -> None:
    rig, _a = swap_rig(tmp_path)
    assert tuple(rig.module.swapper.steps) == ace2k_u1_swap.SWAP_STEPS


def test_a_step_not_in_the_table_runs_nothing(tmp_path: Path) -> None:
    """swap_step of "restore" (every step done) or any other name outside SWAP_STEPS."""
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    state = SwapState(slot, slot.current, slot.lanes[1], 2, step="restore")
    before = dataclasses.replace(state)
    started = len(a.feed.started)
    rig.module.swapper.swap_step("restore", state)
    assert state == before and len(a.feed.started) == started and rig.sent == []


def test_a_push_that_does_not_reach_the_sensor_fails_the_swap_with_its_budget(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    rig.module.probe.push_in = lambda slot, h, speed, budget=None: "distance"
    assert tool(rig, 2) == []
    budget = rig.adapter.head_budget_mm
    line = (
        f"ace2k_u1: head 1: a:1 → a:2 (T2) failed at push: a:2 did not reach the head's sensor"
        f" within its {budget:g} mm"
    )
    assert line in rig.gcode.lines
    assert rig.module.swapper.swap_pending.step == "push"


def lane_fit_rig(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k, Hooked]:
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    return rig, a, slot.lanes[1]


def test_the_lane_fit_of_a_status_that_cannot_be_read_lets_the_load_stand(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, a, h = lane_fit_rig(tmp_path)
    state = SwapState(rig.adapter.slot_of(h), None, h, 2)
    before = dataclasses.replace(state)
    rig.adapter.lane_status = lambda lane: (_ for _ in ()).throw(RuntimeError("no status"))
    with caplog.at_level("ERROR"):
        assert rig.module.swapper._lane_fit(state, h) is None
    assert any(
        r.getMessage() == "ace2k_u1: a:2 after its load" and r.exc_info for r in caplog.records
    )
    assert state == before


def test_the_lane_fit_whose_follow_does_not_arm_after_the_clear_raises(tmp_path: Path) -> None:
    rig, a, h = lane_fit_rig(tmp_path)
    a.lanes[1].update(mode="error", error="tangled")
    # the first fit raised already; this is the RESUME's
    state = SwapState(rig.adapter.slot_of(h), None, h, 2, lane_error="tangled")
    rig.adapter.follow.arm = lambda lane, wait: None  # the follow does not arm
    rig.gcode.lines.clear()
    with pytest.raises(RuntimeError) as err:
        rig.module.swapper._lane_fit(state, h)
    assert str(err.value) == "a:2's error (tangled) cleared, but its follow did not arm"
    assert a.feed.cleared == [1]  # the error cleared on the lane
    assert state.lane_error is None
    assert not any("follow armed again" in line for line in rig.gcode.lines)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, True, "230"])
def test_a_temperature_the_u1s_table_gives_that_is_no_finite_positive_number_is_250(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """The unload's and the load's temperature from the U1's filament table: anything but a
    finite positive number (NaN, inf included) takes the default."""
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.unload_temps = {}
    h = rig.adapter.hooked[0]
    monkeypatch.setattr(rig.module.u1, "unload_temp", lambda ext, head: value)
    monkeypatch.setattr(rig.module.u1, "load_temp", lambda spool, ext, nozzle: value)
    assert rig.module.swapper._unload_temp(h) == 250.0
    assert rig.module.swapper._load_temp(h) == 250.0
    monkeypatch.setattr(rig.module.u1, "unload_temp", lambda ext, head: 235)
    monkeypatch.setattr(rig.module.u1, "load_temp", lambda spool, ext, nozzle: 225.0)
    assert (rig.module.swapper._unload_temp(h), rig.module.swapper._load_temp(h)) == (235.0, 225.0)


def test_a_unit_with_host_interface_17_bounds_a_swaps_moves_by_its_own_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The free check's and the roll back's waits are ace2k's budget of each move
    (feed.move_wait_s); the roll back still adds the catches its free tip may meet."""
    monkeypatch.setattr(fakes.FakeFeedApi, "API_VERSION", 17)
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)

    def on_start(move: FakeMove) -> None:
        if rig.in_free_check:  # the tip leaves the head's sensor 30 mm into the free check
            rig.head_sensor.detected = False
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -30.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    tool(rig, 2)
    assert (0, 100.0, 50.0) in a.feed.waits  # the free check: probe_mm at probe_speed
    assert (0, 970.0, 80.0) in a.feed.waits  # the roll back: the rest at swap_speed
    assert rig.module.swapper.swap_pending is None
    # only the roll back counts the unit's tail: the free check's tip may still be in the head
    assert a.feed.reverse_waits == [(0, 970.0, 80.0)]


def test_a_swaps_roll_back_that_runs_into_the_units_tail_ends_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host interface 17: the roll back's wait counts the tail the unit runs once the filament
    leaves its motor (feed.move_wait_s with reverse). A final event that comes after the move's
    own budget and after the old formula, but within the tail's budget, ends the roll back as
    done, not as timed out."""
    monkeypatch.setattr(fakes.FakeFeedApi, "API_VERSION", 17)
    # a tail long enough that its budget outlasts the old formula, the floor of the wait
    monkeypatch.setattr(fakes, "TAIL_MM", 1000.0)
    rig, a = swap_rig(tmp_path)
    a.feed.snag = {"snag_free_mm": 10000.0}  # no catch to wait for in the roll back
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rolling = []

    def on_start(move: FakeMove) -> None:
        if rig.in_free_check:  # the tip leaves the head's sensor 30 mm into the free check
            rig.head_sensor.detected = False
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -30.0}
        elif move.mode == "rollback":
            rolling.append((move, rig.reactor.now))  # its final event comes later
        else:
            move.event = {"kind": "done", "seq": move.seq}

    # 920 mm at 70 mm/s: the move's own budget 1.5 × 920 / 70 + 5 = 24.7 s, the old formula
    # 2 × 920 / 70 + 5 = 31.3 s, the tail's 1000 mm too 1.5 × 1920 / 70 + 5 = 46.1 s; the unit
    # ends the tail 32.3 s after the start
    without_tail = a.feed.move_wait_s(0, 920.0, 70.0)
    with_tail = a.feed.move_wait_s(0, 920.0, 70.0, reverse=True)
    a.feed.waits.clear()
    a.feed.reverse_waits.clear()
    ends_after = max(without_tail, ace2k_u1_common.move_wait_s(920.0, 70.0)) + 1.0
    assert ends_after < with_tail

    def tail_ends(now: float) -> None:
        for move, started in rolling:
            if move.event is None and now - started >= ends_after:
                move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    rig.reactor.on_pause.append(tail_ends)
    tool(rig, 2)
    assert [line for line in rig.gcode.lines if "timed out" in line] == []
    assert len(rolling) == 1 and rolling[0][0].event["kind"] == "done"
    assert rig.module.swapper.swap_pending is None


def test_the_swap_loads_at_the_new_tools_printing_temperature(tmp_path: Path) -> None:
    """PLA to PETG: the old tip at the old material's temperature, the new filament loaded at its
    own printing temperature; no heating to the U1's load temperature, no fan back."""
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.module.tracker.plan.kept[2] = 245.0  # the new tool's printing temperature
    tool(rig, 2)
    assert sent_macro(rig, "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=245 PRINT_TEMP=245")
    unload = next(ln for ln in rig.sent if ln.startswith("ACEK_SWAP_UNLOAD"))
    assert "LOAD_TEMP=245" in unload
