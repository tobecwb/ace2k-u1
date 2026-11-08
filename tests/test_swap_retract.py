"""The swap's retraction: each tool owes, when it leaves a shared head, what the slicer retracted.

The slicer retracts a tool before its T (10 mm in the file seen) and pushes that much back when
the same tool comes back. The swap records it from printing_e_pos at its start and, when the tool
comes back, retracts the rest of it just before the U1's pre-extrusion, whose own 0.5 mm makes
the total. The U1's macro cannot take the value (it fails on a RETRACT_LENGTH), so the rest is a
G1 E in one script between a save and a restore of the G-code state. The U1 counts that
retraction in printing_e_pos only while the print is printing, so in a pause the swap keeps the
U1's 0.5 mm and the recorded value stays for the tool's next return. A record is replaced only
by a new valid reading.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest
from test_shared_runout import new_spool, ran_out
from test_shared_runout import resume as refill_resume
from test_tools import ends, order_is, probe_finds, resume, swap_rig, tool

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from fakes import FakeAce2k

FIRST_USE = "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0"
BACK_TO_T0 = "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0 FORCE=1 LENGTH=0"
SAVE = "SAVE_GCODE_STATE NAME=ACEK_SWAP_RETRACT"
RESTORE = "RESTORE_GCODE_STATE NAME=ACEK_SWAP_RETRACT"
REST = (SAVE, "M83", "G1 E-9.5 F1800", RESTORE)  # for 10 mm
SWAP_RESTORE = "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0"  # the swap's own, also on a failure


def rig_at(tmp_path: Path, order: list[int]) -> tuple[SimpleNamespace, FakeAce2k]:
    """The swap rig with the file's T lines set to order, the print at its first T (T0, a:1)."""
    rig, a = swap_rig(tmp_path)
    order_is(rig, order, 0)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    return rig, a


def head_e(rig: SimpleNamespace, value: float) -> None:
    """The slicer's retraction before the next T: head 1's printing_e_pos."""
    rig.printer.objects["extruder"].printing_e_pos = value


def owed(rig: SimpleNamespace) -> dict[int, float]:
    return rig.module.tracker.retract_owed


def no_retract_length(rig: SimpleNamespace) -> bool:
    """No line sent to the U1 passes a RETRACT_LENGTH: the U1's macro fails on one."""
    return not [ln for ln in rig.sent if "RETRACT_LENGTH" in ln]


def rest_lines(rig: SimpleNamespace) -> list[str]:
    """The lines of a retraction of the swap's own sent: any G1 E- of any length, any line of
    the retraction block."""
    return [ln for ln in rig.sent if ln.startswith("G1 E-") or "ACEK_SWAP_RETRACT" in ln]


def fails_at(rig: SimpleNamespace, line: str) -> None:
    """The U1 refuses line once, as it reaches it."""
    real, left = rig.u1.run, [line]

    def run(ln: str) -> None:
        if left and ln == left[0]:
            left.clear()
            raise RuntimeError(f"refused: {ln}")
        real(ln)

    rig.u1.run = run


def test_a_first_use_keeps_the_u1s_retraction_and_records_the_tool_that_left(
    tmp_path: Path,
) -> None:
    rig, _a = rig_at(tmp_path, [0, 2])
    tool(rig, 2)
    assert FIRST_USE in rig.sent  # unchanged: the U1's own 0.5 mm
    assert rest_lines(rig) == []
    assert rig.u1.preextrude_retract == [-0.5]
    assert owed(rig) == {0: 10.0}  # T0 left head 1 retracted 10 mm


def test_a_tool_that_comes_back_retracts_what_the_slicer_retracted(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2, 0])
    tool(rig, 2)
    head_e(rig, -10.0)  # the slicer's G1 E-10 before T0
    rig.sent.clear()
    tool(rig, 0)
    at = rig.sent.index(BACK_TO_T0)
    assert tuple(rig.sent[at - 4 : at]) == REST  # just before the U1's pre-extrusion
    assert rest_lines(rig) == [SAVE, "G1 E-9.5 F1800", RESTORE]  # once, and nothing else
    assert no_retract_length(rig)
    assert rig.u1.preextrude_retract == [-0.5, -0.5]  # the U1's own each time: 9.5 + 0.5 = 10
    assert rig.u1.e_pos_at_preextrude[-1] == -9.5  # the rest, counted before the macro
    # before the slicer's G1 E10: retracted 10, so the push brings the head back to 0
    assert rig.printer.objects["extruder"].printing_e_pos == -10.0
    assert owed(rig) == {2: 10.0}  # T0's taken, T2's recorded
    assert (
        "ace2k_u1: head 1: pre-extrusion after the swap (T0), retracting 10 mm as the slicer did"
        in rig.gcode.lines
    )


def test_a_departing_tool_left_unretracted_owes_nothing(tmp_path: Path) -> None:
    for value in (0.0, 0.5):  # printing_e_pos is capped at 0; anything not negative owes nothing
        rig, _a = rig_at(tmp_path, [0, 2])
        owed(rig)[0] = 7.0  # an old value is dropped, never kept
        head_e(rig, value)
        tool(rig, 2)
        assert owed(rig) == {}, value


def test_a_retraction_out_of_range_keeps_the_record(tmp_path: Path) -> None:
    """A value past 15 mm, or no number, is no reading: the slicer's earlier value stays."""
    for value in (-15.5, -100.0, math.nan):
        rig, _a = rig_at(tmp_path, [0, 2])
        owed(rig)[0] = 7.0
        head_e(rig, value)
        tool(rig, 2)
        assert owed(rig) == {0: 7.0}, value


def test_an_unread_extruder_position_keeps_the_record(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2])
    rig.module.tracker.t_raw = 1  # the print at its T2
    owed(rig)[0] = 7.0
    lane = rig.adapter.slot_of_head(0).lanes[0]  # a:1, T0's lane
    rig.module.tracker.record_retract(0, lane, 2, None)
    assert owed(rig) == {0: 7.0}
    rig.module.tracker.record_retract(0, lane, 2, -10.0)
    assert owed(rig) == {0: 10.0}


def test_the_longest_retraction_mirrored_is_15_mm(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2])
    head_e(rig, -15.0)
    tool(rig, 2)
    assert owed(rig) == {0: 15.0}


def test_the_departing_tool_is_the_last_t_on_its_head(tmp_path: Path) -> None:
    """T1 is on head 2: between T0 and T2 on head 1, it is not the tool that left head 1."""
    rig, _a = rig_at(tmp_path, [0, 1, 2])
    rig.module.tracker.t_index = rig.module.tracker.t_raw = 1  # T1 ran on head 2
    tool(rig, 2)
    assert owed(rig) == {0: 10.0}


def test_the_same_tool_again_swaps_nothing_and_keeps_what_is_owed(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2, 2])
    tool(rig, 2)
    rig.sent.clear()
    tool(rig, 2)
    assert rig.sent == []  # no swap
    assert owed(rig) == {0: 10.0}


def test_a_failed_pre_extrusion_after_the_retraction_spends_it_once(tmp_path: Path) -> None:
    """The rest ran before the U1's pre-extrusion failed: T0's value is spent, and the books the
    failure puts back count the rest. The RESUME then finishes the swap while the U1 still reads
    paused, so it sends no retraction of its own, and the U1's 0.5 mm of the retry makes the
    total."""
    rig, _a = rig_at(tmp_path, [0, 2, 0])
    tool(rig, 2)
    head_e(rig, -10.0)
    fails_at(rig, BACK_TO_T0)
    assert tool(rig, 0) == []
    assert rig.module.swapper.swap_pending.step == "return"
    assert owed(rig) == {2: 10.0}  # T0's spent: the filament moved
    # the failed swap puts back the slicer's -10 and the rest's -9.5; the pause retracts 2 more
    assert rig.printer.objects["extruder"].printing_e_pos == -21.5
    rig.sent.clear()
    assert resume(rig) == [True]
    assert BACK_TO_T0 in rig.sent
    assert no_retract_length(rig)
    assert rest_lines(rig) == []
    assert owed(rig) == {2: 10.0}
    assert rig.module.swapper.swap_pending is None


def test_a_swap_in_a_pause_neither_records_nor_spends(tmp_path: Path) -> None:
    """A T typed in a pause: printing_e_pos then holds the pause's retraction too, and the U1
    would not count a retraction of the swap. Both records stay as they were."""
    rig, _a = rig_at(tmp_path, [0, 2])
    owed(rig).update({0: 7.0, 2: 5.0})
    rig.print_stats.state = "paused"
    tool(rig, 2)
    assert FIRST_USE in rig.sent
    assert no_retract_length(rig)
    assert rest_lines(rig) == []
    assert owed(rig) == {0: 7.0, 2: 5.0}


def test_a_print_not_where_the_t_is_records_nothing(tmp_path: Path) -> None:
    """The file's T lines do not hold this T where the print is (no T2 after it): the tool that
    left is not known, so the earlier record stays. Without that check the T0 line before would
    be taken for the tool that left, and its record replaced by 10 mm."""
    rig, _a = rig_at(tmp_path, [0, 0])
    rig.module.tracker.t_raw = 1  # at the second T0 line; T2 is not found after it
    owed(rig)[0] = 7.0
    tool(rig, 2)
    assert "ace2k_u1: head 1: a:2 in (0 s)" in rig.gcode.lines  # the swap ran
    assert rig.module.tracker.t_raw == 1
    assert owed(rig) == {0: 7.0}


def test_an_unknown_lane_is_recorded_once_the_probe_names_it(tmp_path: Path) -> None:
    """The swap reads printing_e_pos as it starts, pauses to probe, and the probe at RESUME names
    a:1 (T0's lane): T0 owes what the slicer retracted before the pause, not the pause's -12."""
    rig, a = rig_at(tmp_path, [0, 2])
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)
    probe_finds(rig, 0)
    assert tool(rig, 2) == []
    assert owed(rig) == {}
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == [True]
    assert owed(rig) == {0: 10.0}


def test_the_record_is_per_print(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2])
    tool(rig, 2)
    assert owed(rig) == {0: 10.0}
    rig.module.tracker.reset_print()  # a print's start, end or cancel
    assert owed(rig) == {}


def test_a_refill_keeps_the_u1s_retraction_and_leaves_the_record(tmp_path: Path) -> None:
    rig, a = ran_out(tmp_path)
    owed(rig).update({0: 10.0, 2: 6.0})
    a.lanes[0]["insert"] = True  # the new spool in the bay
    new_spool(rig)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.sent.clear()
    refill_resume(rig)
    assert "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0 FORCE=1 LENGTH=0" in rig.sent
    assert rest_lines(rig) == []
    assert owed(rig) == {0: 10.0, 2: 6.0}


def test_a_retraction_of_half_a_millimetre_or_less_adds_nothing(tmp_path: Path) -> None:
    """The U1's pre-extrusion retracts 0.5 mm itself: there is no rest to retract, and the
    console says so."""
    for value in (0.5, 0.3):
        rig, _a = rig_at(tmp_path, [0, 2, 0])
        tool(rig, 2)
        owed(rig)[0] = value  # T0 left head 1 retracted that much
        rig.sent.clear()
        tool(rig, 0)
        assert BACK_TO_T0 in rig.sent, value
        assert rest_lines(rig) == [], value
        assert no_retract_length(rig), value
        assert 0 not in owed(rig), value  # spent all the same
        line = (
            "ace2k_u1: head 1: pre-extrusion after the swap (T0), the U1's own 0.5 mm"
            f" retraction covers the slicer's {value:g} mm"
        )
        assert line in rig.gcode.lines, value
        assert not [ln for ln in rig.gcode.lines if "retracting" in ln], value


def test_the_retraction_runs_as_one_script(tmp_path: Path) -> None:
    """Nothing can run between its save and its restore while E is relative."""
    rig, _a = rig_at(tmp_path, [0, 2, 0])
    tool(rig, 2)
    head_e(rig, -10.0)
    scripts = []
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        scripts.append(script)
        real(script)

    rig.gcode.run_script_from_command = run_script
    tool(rig, 0)
    assert "\n".join(REST) in scripts
    assert owed(rig) == {2: 10.0}


def test_the_value_is_spent_before_the_retraction_is_sent(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2, 0])
    tool(rig, 2)
    head_e(rig, -10.0)
    seen = []
    real = rig.u1.run

    def run(line: str) -> None:
        if line == SAVE:
            seen.append(dict(owed(rig)))
        real(line)

    rig.u1.run = run
    tool(rig, 0)
    assert seen == [{2: 10.0}]


def test_a_failed_retraction_is_spent_and_the_swaps_restore_puts_the_state_back(
    tmp_path: Path,
) -> None:
    """Refused at its G1 or at its restore, the value is spent: a failure may cost it once, but
    never retracts it twice. No restore of the block is sent on its own; the swap's own restore
    brings back the E mode."""
    for line in ("G1 E-9.5 F1800", RESTORE):
        rig, _a = rig_at(tmp_path, [0, 2, 0])
        tool(rig, 2)
        head_e(rig, -10.0)
        fails_at(rig, line)
        rig.sent.clear()
        assert tool(rig, 0) == [], line
        assert rig.module.swapper.swap_pending.step == "return", line
        assert rig.sent.count(RESTORE) == (line == RESTORE), line  # the script's own only
        at = rig.sent.index(line)
        assert SWAP_RESTORE in rig.sent[at + 1 :], line
        assert BACK_TO_T0 not in rig.sent, line
        assert owed(rig) == {2: 10.0}, line


def test_the_u1_model_refuses_a_retract_length(tmp_path: Path) -> None:
    """The U1's macro applies abs to RETRACT_LENGTH as a string: it fails, whatever the value."""
    rig, _a = rig_at(tmp_path, [0, 2])
    abs_of_str = r"bad operand type for abs\(\): 'str'"
    with pytest.raises(RuntimeError, match=abs_of_str):
        rig.u1.run("SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0 FORCE=1 LENGTH=0 RETRACT_LENGTH=10")
    with pytest.raises(RuntimeError, match=abs_of_str):
        rig.u1.run("INNER_PREEXTRUDE_FILAMENT SOFT=0 TEMP=230 LENGTH=0 RETRACT_LENGTH=0.5")
