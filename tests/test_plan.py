"""The plan: a G-code file's tools and T order, the colour match, the swap count."""

from __future__ import annotations

import os
import pathlib
import re
import threading
import types
from pathlib import Path
from typing import TYPE_CHECKING

import ace2k_u1_plan as plan
import ace2k_u1_tracker
import fakes
import pytest
import test_tools

if TYPE_CHECKING:
    from fakes import FakeReactor

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
COLOURS = ["#000000", "#d32c34", "#ffffff", "#21ff06", "#ffff0a", "#800080", "#0000ff", "#fd8008"]


@pytest.mark.parametrize("name", ["8_colours_no_tower.gcode", "8_colours_tower.gcode"])
def test_a_slicer_file_reads_its_eight_tools(name: str) -> None:
    p = plan.read_file(FIXTURES / name)
    assert [t.colour for t in p.tools] == COLOURS
    assert {t.material for t in p.tools} == {"PLA"}
    assert {(t.temp, t.first_temp) for t in p.tools} == {(220, 220)}
    assert p.order[0] == 0 and set(p.order) == set(range(8))
    assert all(a != b for a, b in zip(p.order, p.order[1:]))


def test_a_slicer_file_keeps_its_raw_t_lines_besides_the_merged_order() -> None:
    """The raw T lines, repeats included (the start's T0 lines), in file order."""
    p = plan.read_file(FIXTURES / "8_colours_no_tower.gcode")
    text = (FIXTURES / "8_colours_no_tower.gcode").read_text().splitlines()
    assert p.raw == [int(line[1:]) for line in text if line[:1] == "T" and line[1:].isdigit()]
    assert p.raw[:4] == [0, 0, 0, 1]
    merged = [n for i, n in enumerate(p.raw) if i == 0 or p.raw[i - 1] != n]
    assert p.order == merged


def test_a_file_without_its_config_block_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "x.gcode"
    f.write_text("G28\nT0\n")
    with pytest.raises(plan.PlanError, match="no slicer config block"):
        plan.read_file(f)


def test_close_blacks_match_and_black_and_white_do_not() -> None:
    assert plan.distance("#000000", "#0a0a0a") < 5
    assert plan.distance("#000000", "#ffffff") > 90


@pytest.mark.parametrize(
    ("colour", "name"),
    [
        ("#ffffff", "white"),
        ("#000000", "black"),
        ("#d32c34", "red"),
        ("#e8d5b0", "beige"),
        ("#1e3a8a", "navy"),
        ("#43a047", "green"),
        ("#2e7d32", "dark green"),
        ("#795548", "brown"),
        ("#808080", "gray"),
    ],
)
def test_a_colour_gets_the_name_of_the_closest_colour_of_the_table(colour: str, name: str) -> None:
    assert plan.colour_name(colour) == name


def test_a_colour_that_is_not_set_has_no_name() -> None:
    assert plan.colour_name(None) is None
    assert plan.colour_name("") is None


def lane(key: str, colour: str | None, material: str | None = "PLA") -> plan.LaneSpool:
    return plan.LaneSpool(key=key, colour=colour, material=material)


def tool(colour: str | None, material: str | None = "PLA") -> plan.Tool:
    return plan.Tool(colour=colour, material=material, temp=220, first_temp=220)


def test_each_tool_gets_its_closest_lane_of_the_same_material() -> None:
    lanes = [lane("a:1", "#000000"), lane("a:2", "#ffffff"), lane("b:1", "#d32c34", "PETG")]
    got = plan.suggest([tool("#0a0a0a"), tool("#fefefe"), tool("#d32c34", "ABS")], lanes, 25.0, 8.0)
    assert got == [("a:1", "closest"), ("a:2", "closest"), (None, "no_material")]


def test_a_doubt_is_left_to_the_operator() -> None:
    lanes = [lane("a:1", "#000000"), lane("a:2", "#050505"), lane("a:3", "#ffffff")]
    assert plan.suggest([tool("#000000")], lanes, 25.0, 8.0) == [(None, "two_close")]
    assert plan.suggest([tool("#ff0000")], lanes, 25.0, 8.0) == [(None, "too_far")]
    lanes.append(lane("b:1", None))
    assert plan.suggest([tool("#ffffff")], lanes, 25.0, 8.0) == [(None, "no_colour")]


def test_a_lane_whose_bay_is_empty_is_no_candidate() -> None:
    empty = plan.LaneSpool(key="a:1", colour="#000000", material="PLA", empty=True)
    lanes = [empty, lane("a:2", "#ffffff")]
    assert plan.suggest([tool("#000000")], lanes, 25.0, 8.0) == [(None, "too_far")]
    assert plan.suggest([tool("#000000")], [empty], 25.0, 8.0) == [(None, "no_material")]
    # an empty bay's lane, its colour not known, leaves the others' suggestion alone
    gone = plan.LaneSpool(key="a:3", colour=None, material=None, empty=True)
    assert plan.suggest([tool("#fefefe")], [gone, lane("a:2", "#ffffff")], 25.0, 8.0) == [
        ("a:2", "closest")
    ]
    assert lane("a:2", "#ffffff").empty is False


def test_swaps_count_a_lane_change_in_a_head_not_a_first_use() -> None:
    choice = {0: "a:1", 1: "a:2", 2: "b:1"}
    head_of = {"a:1": 0, "a:2": 0, "b:1": 1}.get
    assert plan.swaps([0, 2, 1, 2, 0, 1], choice, head_of) == {0: 3, 1: 0}


def test_a_config_block_without_filament_colour_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "x.gcode"
    f.write_text("; CONFIG_BLOCK_START\n; layer_height = 0.2\n; CONFIG_BLOCK_END\n")
    with pytest.raises(plan.PlanError, match="no filament_colour"):
        plan.read_file(f)


def test_short_lists_and_empty_values_leave_none(tmp_path: Path) -> None:
    f = tmp_path / "x.gcode"
    f.write_text(
        "; EXECUTABLE_BLOCK_START\nT0\nT1\n; EXECUTABLE_BLOCK_END\n"
        "; CONFIG_BLOCK_START\n; filament_colour = #ff0000;#00ff00\n"
        "; filament_type = PLA\n; nozzle_temperature = 210\n"
        "; nozzle_temperature_initial_layer =\n; CONFIG_BLOCK_END\n"
    )
    p = plan.read_file(f)
    assert p.tools[0] == plan.Tool("#ff0000", "PLA", 210, None)
    assert p.tools[1] == plan.Tool("#00ff00", None, None, None)


def test_a_tool_without_colour_or_material_is_left_to_the_operator() -> None:
    lanes = [lane("a:1", "#000000")]
    assert plan.suggest([tool(None)], lanes, 25.0, 8.0) == [(None, "no_colour")]
    assert plan.suggest([tool("#000000", None)], lanes, 25.0, 8.0) == [(None, "no_material")]


def test_swaps_skip_a_lane_no_head_owns() -> None:
    head_of = {"a:1": 0}.get
    assert plan.swaps([0, 1, 0], {0: "a:1", 1: "z:9"}, head_of) == {0: 0}


def test_a_second_read_of_an_unchanged_file_is_the_cached_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.gcode"
    f.write_bytes((FIXTURES / "8_colours_tower.gcode").read_bytes())
    parsed = []
    real = plan._parse
    monkeypatch.setattr(plan, "_parse", lambda path: parsed.append(path) or real(path))
    plan._CACHE.clear()
    first = plan.read_file(f)
    second = plan.read_file(f)
    assert len(parsed) == 1 and second == first
    second.order.append(99)  # a caller's change never reaches the cache
    assert plan.read_file(f) == first and len(parsed) == 1
    os.utime(f, (1, 1))  # changed: read again
    assert plan.read_file(f) == first and len(parsed) == 2


def test_a_large_file_reads_its_config_block_and_t_order(tmp_path: Path) -> None:
    body = []
    for i in range(200_000):
        if i % 1000 == 0:
            body.append(f"T{(i // 1000) % 3} ; tool change\r")
        else:
            body.append(f"G1 X{i % 100} E0.1")
    f = tmp_path / "big.gcode"
    f.write_bytes(
        (
            "; HEADER_BLOCK_START\n; CONFIG_BLOCK_START is said in a comment\n"
            "; HEADER_BLOCK_END\n; EXECUTABLE_BLOCK_START\nT0\nT0\n"
            + "\n".join(body)
            + "\nT2 \n; EXECUTABLE_BLOCK_END\n; CONFIG_BLOCK_START\n"
            "; filament_colour = #000000;#ffffff;#ff0000\n; filament_type = PLA;PETG;PLA\n"
            "; nozzle_temperature = 210,240,215\n; CONFIG_BLOCK_END\nT1\n"
        ).encode()
    )
    p = plan.read_file(f)
    assert [t.colour for t in p.tools] == ["#000000", "#ffffff", "#ff0000"]
    assert [t.material for t in p.tools] == ["PLA", "PETG", "PLA"]
    assert [t.temp for t in p.tools] == [210, 240, 215]
    assert p.order == [0, 1, 2] * 66 + [0, 1, 2]  # repeats collapsed, the T after the block out


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "x.gcode"
    f.write_bytes(b"")
    with pytest.raises(plan.PlanError, match="no slicer config block"):
        plan.read_file(f)


def test_materials_match_without_case_or_spaces() -> None:
    lanes = [lane("a:1", "#000000", "pla "), lane("a:2", "#ffffff", None)]
    assert plan.suggest([tool("#000000", " PLA")], lanes, 25.0, 8.0) == [("a:1", "closest")]
    assert plan.same_material("PLA", "pla ") and not plan.same_material("PLA", "PETG")
    assert not plan.same_material(None, "PLA") and not plan.same_material("", "")


@pytest.mark.parametrize(
    "text, colour",
    [
        ("#FF0000", "#ff0000"),
        ("ff0000", "#ff0000"),
        (" #00ff00ff ", "#00ff00"),
        ("00FF0080", "#00ff00"),
        ("#ff00", None),
        ("red", None),
        ("", None),
        (None, None),
    ],
)
def test_a_colour_is_normalised_to_rrggbb_or_refused(text: str | None, colour: str | None) -> None:
    assert plan.normal_colour(text) == colour


def plan_reader() -> tuple[ace2k_u1_tracker.PrintTracker, FakeReactor]:
    """A PrintTracker with a fake reactor, as read_plan reads it: (tracker, reactor)."""
    reactor = fakes.FakeReactor()
    host = types.SimpleNamespace(reactor=reactor, adapter=None, u1=None, units=lambda: None)
    return ace2k_u1_tracker.PrintTracker(host), reactor


def test_the_plan_is_read_in_a_thread_while_the_reactor_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parse runs off the reactor; the caller waits on a completion, so the reactor's
    pause runs while it does — the parse here can only end once the reactor has paused."""
    tracker, reactor = plan_reader()
    paused = threading.Event()
    reactor.on_pause = [lambda _now: paused.set()]
    threads = []

    def read_file(_path: object) -> plan.ParsedPlan:
        threads.append(threading.current_thread())
        assert paused.wait(5.0)
        return plan.ParsedPlan(tools=[], order=[1], raw=[1])

    monkeypatch.setattr(plan, "read_file", read_file)
    assert tracker.read_plan("x.gcode") == plan.ParsedPlan(tools=[], order=[1], raw=[1])
    assert threads and threads[0] is not threading.current_thread()


@pytest.mark.parametrize(
    "error", [plan.PlanError("no slicer config block"), OSError(2, "gone"), ValueError("bad")]
)
def test_a_read_in_the_thread_raises_its_error_on_the_reactor(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    tracker, _ = plan_reader()

    def read_file(_path: object) -> plan.ParsedPlan:
        raise error

    monkeypatch.setattr(plan, "read_file", read_file)
    with pytest.raises(type(error)) as got:
        tracker.read_plan("x.gcode")
    assert got.value is error and str(got.value) == str(error)


def test_a_threaded_read_of_an_unchanged_file_is_the_cached_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker, _ = plan_reader()
    f = tmp_path / "x.gcode"
    f.write_bytes((FIXTURES / "8_colours_tower.gcode").read_bytes())
    parsed = []
    real = plan._parse
    monkeypatch.setattr(plan, "_parse", lambda path: parsed.append(path) or real(path))
    plan._CACHE.clear()
    first = tracker.read_plan(str(f))
    assert tracker.read_plan(str(f)) == first and len(parsed) == 1


def threaded_reads(monkeypatch: pytest.MonkeyPatch) -> list[threading.Thread]:
    """The threads read_file ran in, appended per call."""
    threads = []
    real = plan.read_file

    def read_file(path: str) -> plan.ParsedPlan:
        threads.append(threading.current_thread())
        return real(path)

    monkeypatch.setattr(plan, "read_file", read_file)
    return threads


def test_acek_plan_and_a_print_start_read_the_file_off_the_reactor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = test_tools.tools_rig(tmp_path)
    threads = threaded_reads(monkeypatch)
    test_tools.spools(rig, test_tools.EIGHT)
    test_tools.run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    assert rig.module.tracker.plan is not None
    test_tools.lanes_named(rig)
    with pytest.raises(ValueError, match="shares heads between tools"):
        test_tools.print_start(rig)  # a file with no plan set: read at its start, refused
    assert len(threads) == 2 and threading.current_thread() not in threads


def test_an_infinite_temperature_is_unknown(tmp_path: Path) -> None:
    """int(float("inf")) raises OverflowError: the temperature is unknown, not an error."""
    f = tmp_path / "x.gcode"
    f.write_text(
        "; CONFIG_BLOCK_START\n; filament_colour = #ff0000;#00ff00;#0000ff\n"
        "; nozzle_temperature = inf,-inf,nan\n"
        "; nozzle_temperature_initial_layer = 1e999,215,220\n; CONFIG_BLOCK_END\n"
    )
    p = plan.read_file(f)
    assert [(t.temp, t.first_temp) for t in p.tools] == [(None, None), (None, 215), (None, 220)]


def test_acek_plan_reports_an_infinite_temperature_as_unknown(tmp_path: Path) -> None:
    rig, _ = test_tools.tools_rig(tmp_path)
    gcodes = tmp_path / "gcodes"
    text = (gcodes / "eight.gcode").read_text()
    infinite = "; nozzle_temperature = inf,220,220,220,220,220,220,220"
    text = text.replace("; nozzle_temperature = 220,220,220,220,220,220,220,220", infinite)
    (gcodes / "inf.gcode").write_text(text)
    test_tools.spools(rig, test_tools.EIGHT)
    gcmd = test_tools.run(rig, "ACEK_PLAN", {"FILE": "inf.gcode"})
    assert gcmd.lines == ["ace2k_u1: plan of inf.gcode: 8 tools"]
    tools = rig.module.get_status()["plan"]["tools"]
    assert tools[0]["temp"] is None and tools[1]["temp"] == 220


@pytest.mark.parametrize("name", ["8_colours_tower.gcode", "8_colours_no_tower.gcode"])
def test_each_order_entry_has_the_file_offset_of_its_t_line(name: str) -> None:
    path = FIXTURES / name
    parsed = plan.read_file(path)
    data = path.read_bytes()
    assert len(parsed.order_at) == len(parsed.order)
    assert all(a < b for a, b in zip(parsed.order_at, parsed.order_at[1:]))
    for n, at in zip(parsed.order, parsed.order_at):
        line = data[at : data.index(b"\n", at)]
        found = re.match(rb"T(\d+)", line)
        assert found is not None and found.group(1) == str(n).encode()
    assert parsed.size == path.stat().st_size


def test_a_repeated_t_keeps_the_offset_of_its_first_line_with_crlf_and_a_t_first(
    tmp_path: Path,
) -> None:
    # The first T is the executable block's first byte, the lines end in CRLF, and each T repeats.
    text = (
        "; EXECUTABLE_BLOCK_START\r\nT0\r\nG1 X1\r\nT0\r\nT1\r\nG1 X2\r\nT1\r\nT0\r\n"
        "; EXECUTABLE_BLOCK_END\r\n; CONFIG_BLOCK_START\r\n"
        "; filament_colour = #000000;#ffffff\r\n; filament_type = PLA;PETG\r\n"
        "; CONFIG_BLOCK_END\r\n"
    )
    f = tmp_path / "crlf.gcode"
    f.write_bytes(text.encode())
    p = plan.read_file(f)
    data = text.encode()
    first = data.index(b"T0")
    assert p.order == [0, 1, 0] and p.raw == [0, 0, 1, 1, 0]
    assert p.order_at == [first, data.index(b"T1"), data.rindex(b"T0")]
    assert first == len(b"; EXECUTABLE_BLOCK_START\r\n")
    assert p.size == len(data)


def test_a_parsed_plan_built_by_hand_needs_no_offsets() -> None:
    p = plan.ParsedPlan(tools=[], order=[1], raw=[1])
    assert p.order_at == () and p.size == 0
    # The default is immutable, so no two plans built by hand can share one list.
    assert plan.ParsedPlan(tools=[], order=[], raw=[]).order_at is p.order_at
    assert isinstance(p.order_at, tuple)
