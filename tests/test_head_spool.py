"""ACEK_HEAD_SPOOL: the head takes the spool of the lane that is in it, as a swap writes it.

The web page sends it after its Load into head N. It runs the swap's own spool step
(Swapper.set_head_spool): the tag in the U1's names, else the ACEK_SPOOL record.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import pytest
from ace2k_u1_swap import SwapState
from test_tools import run, tools_rig

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

RED_HEAD1 = (
    "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 VENDOR=Generic FILAMENT_TYPE=PLA"
    " FILAMENT_SUBTYPE='' FILAMENT_COLOR_RGBA=FF0000FF FORCE=1"
)


def a2_in_head_1(tmp_path: Path) -> SimpleNamespace:
    """A rig whose head 1 (a:1 + a:2) has a:2 as its lane, no spool set on any lane."""
    rig, _ = tools_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[1])
    rig.adapter.tag_writer.tags = types.SimpleNamespace(known_types={"PLA", "PETG"})  # U1's table
    return rig


def settings(rig: SimpleNamespace) -> list[str]:
    return [ln for ln in rig.gcode.lines if ln.startswith("SET_PRINT_FILAMENT_CONFIG")]


def test_the_lanes_record_goes_on_its_head_as_a_swap_writes_it(tmp_path: Path) -> None:
    rig = a2_in_head_1(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ff0000"})
    slot = rig.adapter.slot_of_head(0)
    slot.setting_for = None
    rig.gcode.lines.clear()
    gcmd = run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "a", "LANE": "2"})
    assert settings(rig) == [RED_HEAD1]
    assert slot.setting_for is slot.lanes[1]  # the head's setting speaks for a:2 now
    assert gcmd.lines == ["ace2k_u1: head 1: a:2's spool set on the head"]


def test_a_tag_goes_on_the_head_in_the_u1s_names(tmp_path: Path) -> None:
    rig = a2_in_head_1(tmp_path)
    record = {"brand": "Bambu", "material": "PLA", "name": "PLA Matte", "color_rgba": "FF0000FF"}
    rig.units["a"].lanes[1]["tag"] = dict(state="read", uid="x", source="tag", record=record)
    names = {("Bambu", "PLA", "Matte"), ("generic", "PLA", "generic")}
    rig.adapter.tag_writer.tags = types.SimpleNamespace(known_types={"PLA", "PETG"}, names=names)
    run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "a", "LANE": "2"})
    assert settings(rig) == [
        "SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 VENDOR=Bambu FILAMENT_TYPE=PLA"
        " FILAMENT_SUBTYPE='Matte' FILAMENT_COLOR_RGBA=FF0000FF FORCE=1"
    ]


def test_a_lane_with_no_spool_set_writes_nothing_and_only_says_so(tmp_path: Path) -> None:
    # not an error: the load before it went through, and the head's setting is left as it was
    rig = a2_in_head_1(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    slot.setting_for = slot.lanes[1]  # written for a:2 before, by a tag since forgotten
    rig.gcode.lines.clear()
    gcmd = run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "a", "LANE": "2"})
    assert gcmd.lines == ["ace2k_u1: head 1: a:2 has no spool set: set it with Edit spool"]
    assert settings(rig) == []
    assert slot.setting_for is slot.lanes[1]


def test_a_setting_the_u1_refuses_is_an_error(tmp_path: Path) -> None:
    rig = a2_in_head_1(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ff0000"})
    real = rig.gcode.run_script_from_command

    def refuses(script: str) -> None:
        real(script)
        if script.startswith("SET_PRINT_FILAMENT_CONFIG"):
            raise RuntimeError("official filament, not configurable")

    rig.gcode.run_script_from_command = refuses
    with pytest.raises(ValueError, match="spool setting refused: official filament"):
        run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "a", "LANE": "2"})
    # the head kept its setting, so the lane it was written for stays (a swap drops it, as its
    # head just took a new lane)
    slot = rig.adapter.slot_of_head(0)
    assert slot.setting_for is slot.lanes[1]


def test_a_pending_swap_is_refused(tmp_path: Path) -> None:
    # a swap paused on a failure sets its head's spool itself when it goes on at its RESUME
    rig = a2_in_head_1(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ff0000"})
    slot = rig.adapter.slot_of_head(0)
    rig.module.swapper.swap_pending = SwapState(slot, slot.lanes[0], slot.lanes[1], 2, "push")
    rig.gcode.lines.clear()
    line = r"^ace2k_u1: head 1: a:1 → a:2 \(T2\) is pending at push — RESUME it first$"
    with pytest.raises(ValueError, match=line):
        run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "a", "LANE": "2"})
    assert settings(rig) == []


def test_a_lane_that_is_not_in_a_head_is_refused(tmp_path: Path) -> None:
    rig = a2_in_head_1(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PLA", "COLOR": "ff0000"})
    rig.gcode.lines.clear()
    # a:1 is parked: head 1's lane is a:2
    with pytest.raises(ValueError, match="^ace2k_u1: lane a:1 is not in head 1: a:2 is"):
        run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "a", "LANE": "1"})
    # no lane named in the head, as after a restart
    rig.adapter.set_current(rig.adapter.slot_of_head(0), None)
    with pytest.raises(ValueError, match="^ace2k_u1: lane a:1 is not in head 1: no lane is"):
        run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "a", "LANE": "1"})
    assert settings(rig) == []


def test_a_lane_that_feeds_no_head_or_an_unhooked_adapter_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1+a:2, a:3, a:4")
    with pytest.raises(ValueError, match="^ace2k_u1: lane b:1 feeds no head"):
        run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "b", "LANE": "1"})
    rig.module.adapter = None
    with pytest.raises(ValueError, match="the adapter is not hooked"):
        run(rig, "ACEK_HEAD_SPOOL", {"UNIT": "a", "LANE": "3"})
    assert settings(rig) == []
