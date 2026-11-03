"""A spool that runs out in a shared head: the lane stopped at its tail notice, the
plain pause, the swap's unload macro and free check, the rest rolled out of the bay, and the refill
at RESUME."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1_refill
import ace2k_u1_swap
import fakes
import pytest
from ace2k_u1_swap import SwapState
from test_tools import (
    FIVE_TWO_USED,
    drop_macro,
    ends,
    freed_then,
    print_start,
    run,
    swap_rig,
    tick,
    tools_rig,
)
from test_units import Gcmd

if TYPE_CHECKING:
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked
    from ace2k_u1_printer_types import GCodeCommand
    from fakes import FakeAce2k, FakeMove

LINE = (
    "ace2k_u1: head 1: a:1 ran out — its rest is out of bay a:1: take it out, put a new spool in"
    " and RESUME"
)
START_LINE = (
    "ace2k_u1: heads several lanes feed take a spool's end at the unit's bay sensor, whatever"
    " runout_source says"
)


def refill_rig(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:
    """swap_rig, with the G-code a timer sends (run_script) recorded as the swap's own, and which
    of the two ran each script."""
    rig, a = swap_rig(tmp_path)
    from_command = rig.gcode.run_script_from_command
    rig.runners = []

    def run_script(script: str) -> None:
        rig.runners.append(("run_script", script))
        from_command(script)

    def run_script_from_command(script: str) -> None:
        rig.runners.append(("run_script_from_command", script))
        from_command(script)

    rig.gcode.run_script = run_script
    rig.gcode.run_script_from_command = run_script_from_command
    rig.lane = rig.adapter.slot_of_head(0).current  # a:1, T0's lane, in head 1 since the start
    rig.lane.follow_wanted = True
    # the U1's setting of head 1, written for a:1 at the start: the run-out lane's material once
    # its bay reads empty (its spool gone)
    rig.task_config.status["filament_type"][0] = "PLA"
    a.lanes[0]["insert"] = False  # the spool's end has passed the bay's sensor
    a.lanes[0]["mode"] = "following"
    return rig, a


def new_spool(rig: SimpleNamespace, lane: int = 1, kind: str = "PLA") -> None:
    """The refill's new spool set by hand: the run-out spool's record is dropped at the refill."""
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": str(lane), "TYPE": kind, "COLOR": "ffffff"})


def tail(rig: SimpleNamespace, lane: int = 0) -> None:
    rig.adapter.on_feed_event(lane, {"kind": "tail", "unit": "a", "seq": 0, "mode": "following"})


def run_pauses(rig: SimpleNamespace) -> None:
    rig.adapter.pause_runner._run_pauses(rig.reactor.now)


def ran_out(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:
    """The tail notice and the pause timer's run: the refill pending, the print paused."""
    rig, a = refill_rig(tmp_path)
    tail(rig)
    a.feed.on_start = ends("runout")
    run_pauses(rig)
    a.lanes[0]["mode"] = "idle"
    return rig, a


def test_the_tail_notice_stops_the_lane_before_the_pause_timer_runs(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    tail(rig)
    h = rig.lane
    assert a.feed.log[-1] == ("stop", 0)
    assert h.follow_wanted is False and h.hold is True
    assert rig.sent == []  # nothing for the U1 yet: the pause timer has not run
    assert rig.adapter.pause_runner.pause_pending == [("shared runout", h, "tail")]
    assert "ace2k_u1: lane a:1 ran out at the bay — its tail feeds on to e0" not in rig.gcode.lines


def test_the_pause_timer_pauses_unloads_and_rolls_the_rest_out_of_the_bay(tmp_path: Path) -> None:
    rig, a = ran_out(tmp_path)
    assert rig.sent == [
        "PAUSE",
        "T0 A0",
        # the heater left off (LOAD_TEMP=0): the new spool may be long coming; the tip formed at
        # the U1's own unload temperature (its eject's, 250 °C without its table), not 220
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=250 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=0 MATERIAL=PLA",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=extruder0_temp VALUE=220",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_temp VALUE=220",
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0",
    ]
    # from a timer, outside the G-code lock: run_script, never run_script_from_command
    assert {runner for runner, _ in rig.runners} == {"run_script"}
    cfg = rig.module.swap_cfg
    rollbacks = [(lane, mode, length, speed) for lane, mode, length, speed, _m in a.feed.started]
    # the free check, then the rest: head_budget_mm and REFILL_EXTRA_MM less what the free check
    # rolled back (none counted: its final event carries no distance), in moves of at most the
    # unit's longest — the first runs out of the bay, which ends it
    assert rollbacks == [
        (0, "rollback", cfg.probe_mm, cfg.probe_speed),
        (0, "rollback", fakes.FakeFeedApi.move_max_mm, cfg.swap_speed),
    ]
    assert rig.u1.swap_macros[-1][1]["LOAD_TEMP"] == "0"
    assert rig.u1.e_pos_at_macro == [-12.0]  # the unload leaves it untouched
    # printing_e_pos goes back to its value before the unload (the pause's retraction in it)
    assert rig.printer.objects["extruder"].printing_e_pos == -12.0
    assert rig.print_stats.state == "paused"
    assert rig.gcode.lines[-1] == LINE
    pending = rig.module.swapper.swap_pending
    assert pending.refill and pending.old is pending.new is rig.lane
    assert pending.step == "hand over"
    assert rig.lane.hold is False
    assert rig.adapter.swapping == set()


def test_a_refills_rest_waits_for_the_units_tail_on_host_interface_17(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rest's roll back counts the tail the unit runs once the filament leaves its motor
    (feed.move_wait_s with reverse); the free check before it does not."""
    monkeypatch.setattr(fakes.FakeFeedApi, "API_VERSION", 17)
    rig, a = ran_out(tmp_path)
    cfg = rig.module.swap_cfg
    assert a.feed.waits == [
        (0, cfg.probe_mm, cfg.probe_speed),
        (0, fakes.FakeFeedApi.move_max_mm, cfg.swap_speed),
    ]
    assert a.feed.reverse_waits == [(0, fakes.FakeFeedApi.move_max_mm, cfg.swap_speed)]
    assert rig.gcode.lines[-1] == LINE


def test_the_run_out_lane_is_not_armed_before_its_unload(tmp_path: Path) -> None:
    """The run-out lane stays stopped through the unload macro — its end must not leave
    the unit's motor — unlike a swap's old lane, which follows it."""
    rig, a = refill_rig(tmp_path)
    tail(rig)
    since = len(a.feed.log)
    a.feed.on_start = ends("runout")
    real = rig.gcode.run_script
    seen = []

    def run_script(script: str) -> None:
        if script.startswith("ACEK_SWAP_UNLOAD"):
            seen.append((list(a.feed.log[since:]), rig.lane.follow_wanted))
        real(script)

    rig.gcode.run_script = run_script
    run_pauses(rig)
    assert seen == [([], False)]  # nothing started on the unit before the macro, no follow
    assert not [e for e in a.feed.log[since:] if e[:3] == ("start", 0, "assist_both")]


def test_the_rest_rolled_back_is_head_budget_less_what_the_free_check_rolled_back(
    tmp_path: Path,
) -> None:
    rig, a = refill_rig(tmp_path)
    tail(rig)

    def on_start(move: FakeMove) -> None:
        if rig.in_free_check:  # the rest's tip leaves the head's sensor 30 mm into the check
            rig.head_sensor.detected = False
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -30.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}  # never out of its bay: the whole rest

    a.feed.on_start = on_start
    run_pauses(rig)
    lengths = [length for _l, mode, length, _s, _m in a.feed.started if mode == "rollback"]
    rest = rig.adapter.head_budget_mm + ace2k_u1_swap.REFILL_EXTRA_MM - 30.0
    assert lengths == [
        rig.module.swap_cfg.probe_mm,
        fakes.FakeFeedApi.move_max_mm,
        rest - fakes.FakeFeedApi.move_max_mm,
    ]
    assert rig.gcode.lines[-1] == LINE


def test_a_roll_back_that_ends_done_counts_as_the_rest_out(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    tail(rig)
    a.feed.on_start = ends("done")
    run_pauses(rig)
    assert rig.gcode.lines[-1] == LINE
    assert rig.module.swapper.swap_pending.step == "hand over"


@pytest.mark.parametrize("step", ["unload", "free", "roll back"])
def test_a_failure_says_its_step_and_leaves_the_refill_pending_paused(
    tmp_path: Path, step: str
) -> None:
    rig, a = refill_rig(tmp_path)
    real = rig.gcode.run_script

    def run_script(script: str) -> None:
        real(script)
        if step == "unload" and script.startswith("ACEK_SWAP_UNLOAD"):
            raise RuntimeError("the U1 failed")

    def held(move: FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True  # the gear still holds the rest: its plunger goes taut

    rig.gcode.run_script = run_script
    a.feed.on_start = held if step == "free" else freed_then(rig, "stuck")
    tail(rig)
    run_pauses(rig)
    assert any(
        line.startswith(f"ace2k_u1: head 1: refill of a:1 failed at {step}: ")
        for line in rig.gcode.lines
    )
    assert LINE not in rig.gcode.lines
    assert rig.print_stats.state == "paused"
    pending = rig.module.swapper.swap_pending
    # a free check that fails is pending at its unload: the gear held the rest, the unload runs
    # again (test_a_refill_whose_free_check_fails_runs_its_unload_again_at_resume)
    assert pending.refill and pending.step == ("unload" if step == "free" else step)
    assert rig.printer.objects["extruder"].printing_e_pos == -12.0
    assert rig.lane.follow_wanted is False
    index = "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0"
    assert rig.sent[-1] == index


def test_a_refill_whose_free_check_fails_runs_its_unload_again_at_resume(tmp_path: Path) -> None:
    """The gear still held the rest after the unload: a free check run again alone would find it
    held again, forever. RESUME runs the unload again (LOAD_TEMP=0), then the free check."""
    rig, a = refill_rig(tmp_path)
    held = []

    def gripped(move: FakeMove) -> None:
        if move.mode == "rollback" and not held:
            held.append(move)
            a.lanes[0]["pushed"] = True  # the gear still holds the rest: its plunger goes taut

    a.feed.on_start = gripped
    tail(rig)
    run_pauses(rig)
    assert any(
        line.startswith("ace2k_u1: head 1: refill of a:1 failed at free: ")
        for line in rig.gcode.lines
    )
    assert rig.module.swapper.swap_pending.step == "unload"  # its unload runs again
    a.lanes[0]["pushed"] = False
    a.feed.on_start = ends("runout")
    rig.sent.clear()
    started = len(a.feed.started)
    assert resume(rig) == []  # the U1's RESUME not run: the new spool comes first
    assert rig.sent[:2] == [
        "T0 A0",
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=250 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=0 MATERIAL=PLA",
    ]
    # the free check, then the rest
    assert [mode for _l, mode, *_ in a.feed.started[started:]] == ["rollback", "rollback"]
    assert rig.gcode.lines[-1] == LINE
    assert rig.module.swapper.swap_pending.step == "hand over"


def resume(rig: SimpleNamespace) -> list[str]:
    """The adapter's RESUME, the U1's own recorded."""
    order = rig.order = getattr(rig, "order", [])

    def u1_resume(gcmd: GCodeCommand) -> None:
        order.append("RESUME")
        rig.u1_resume()

    rig.gcode.commands_orig["RESUME"] = u1_resume
    rig.gcode.commands["RESUME"](Gcmd({}, "RESUME"))
    return order


def test_resume_with_the_bay_empty_says_so_and_stays_paused(tmp_path: Path) -> None:
    rig, a = ran_out(tmp_path)
    started = len(a.feed.started)
    assert resume(rig) == []
    line = "ace2k_u1: head 1: put a new spool in bay a:1 first — still paused"
    assert rig.gcode.lines[-1] == line
    assert rig.print_stats.state == "paused"
    assert rig.module.swapper.swap_pending.step == "hand over"
    assert len(a.feed.started) == started  # nothing moved


def test_resume_with_a_new_spool_pushes_loads_finishes_then_the_u1s_resume(tmp_path: Path) -> None:
    rig, a = ran_out(tmp_path)
    a.lanes[0]["insert"] = True  # the new spool in the bay
    new_spool(rig)  # the new spool set (its record dropped with the run-out one)
    rig.order = []
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: (setattr(rig.head_sensor, "detected", True), rig.order.append("push"))
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        if script.startswith("ACEK_SWAP_LOAD"):
            rig.order.append("load")
        if script.startswith("RESTORE_GCODE_STATE"):
            rig.order.append("finish")
        real(script)

    rig.gcode.run_script_from_command = run_script
    t0 = []
    rig.gcode.commands_orig["T0"] = t0.append
    rig.sent.clear()
    assert resume(rig) == ["push", "load", "finish", "RESUME"]
    assert t0 == []  # no T owed: the print asked for none
    pushes = [(lane, mode, speed) for lane, mode, _l, speed, _m in a.feed.started[2:]]  # free, rest
    assert pushes[0] == (0, "feed", rig.module.swap_cfg.swap_speed)
    assert rig.module.swapper.swap_pending is None and rig.module.swapper.tools_owed == []
    assert rig.adapter.slot_of_head(0).current is rig.lane
    assert rig.sent[0] == "SAVE_GCODE_STATE NAME=ACEK_SWAP"
    assert rig.sent[1].startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 ")
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent
    assert rig.sent[-2:] == [
        "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=0 FORCE=1 LENGTH=0",  # the refill's tool, T0
        "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0",
    ]
    assert "MOVE_TO_XY_IDLE_POSITION_EXTRUDER" not in rig.sent  # the pre-extrusion's own exit
    assert rig.print_stats.state == "printing"
    assert "ace2k_u1: head 1: a:1 in (0 s)" in rig.gcode.lines
    assert rig.module.tracker.preextrude_after is None  # a refill leaves no skip mark
    assert rig.module.tracker.wait_skip_tool is None  # nor a mark for the wait


def test_a_refills_push_short_of_the_sensor_whose_tip_arrives_later_goes_on_unprobed(
    tmp_path: Path,
) -> None:
    """Bench: the refill's push ended short of the head's sensor, and the new tip set it right
    after. The head was empty before the push, so the lane in it can only be the refilled one: the
    next RESUME goes on from its spool with no probe, so no wait for the head to cool."""
    rig, a = ran_out(tmp_path)
    a.lanes[0]["insert"] = True
    new_spool(rig)
    rig.module.probe.push_in = lambda slot, h, speed, budget=None: "distance"
    assert resume(rig) == []
    assert rig.module.swapper.swap_pending.step == "push"
    assert any("refill of a:1 failed at push" in line for line in rig.gcode.lines)
    rig.head_sensor.detected = True  # the tip reached the sensor after the push gave up

    def which_lane(slot: object) -> NoReturn:
        pytest.fail("the head was empty before the push: no probe")

    rig.module.probe.which_lane = which_lane
    rig.sent.clear()
    assert resume(rig) == ["RESUME"]
    assert (
        "ace2k_u1: head 1: a:1 is in it (the head was empty before its push): on from its spool,"
        " no probe"
    ) in rig.gcode.lines
    assert "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA" in rig.sent
    assert rig.module.swapper.swap_pending is None


def test_a_refills_resume_selects_its_head_before_the_load(tmp_path: Path) -> None:
    """Another head selected while the print waited for the new spool: the refill's load macro
    still runs on head 1, a T0 A0 sent right before it."""
    rig, a = ran_out(tmp_path)
    a.lanes[0]["insert"] = True
    new_spool(rig)  # the new spool set (its record dropped with the run-out one)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.u1.active = rig.printer.objects["extruder_list"][1]
    rig.sent.clear()
    rig.runners.clear()
    assert resume(rig) == ["RESUME"]
    load = next(i for i, ln in enumerate(rig.sent) if ln.startswith("ACEK_SWAP_LOAD"))
    assert rig.sent[load - 1] == "T0 A0"
    assert rig.u1.swap_macros[-1][0] == "ACEK_SWAP_LOAD"
    assert rig.u1.active_at_macro[-1] == 0
    # from RESUME, a command: run_script_from_command, never the timer's run_script
    assert ("run_script_from_command", "T0 A0") in rig.runners
    # the new spool's tip pushed from the bay, its budget over the unit's longest move: the first
    # move is at most that
    pushes = [length for _l, mode, length, _s, _m in a.feed.started if mode == "feed"]
    assert pushes[0] == fakes.FakeFeedApi.move_max_mm


def test_a_refill_with_no_spool_and_no_head_setting_waits_for_the_spool(tmp_path: Path) -> None:
    """The run-out lane's record dropped with its bay and the head's setting
    gone, the refill loaded and then the U1's RESUME refused ("e0 not edit filament"). Now the
    RESUME says so and stays paused, nothing moved; with the spool set it goes on."""
    rig, a = ran_out(tmp_path)
    rig.module.spool_store.records.pop("a:1", None)
    rig.task_config.status["filament_type"][0] = "NONE"  # the U1's setting of head 1 cleared
    a.lanes[0]["insert"] = True  # a new spool in the bay
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    started = len(a.feed.started)
    assert resume(rig) == []  # the U1's RESUME not run
    assert len(a.feed.started) == started  # nothing moved
    assert rig.gcode.lines[-1] == (
        "ace2k_u1: head 1: the new spool in bay a:1 is not known (no tag read, no spool set):"
        " set it (Edit spool, or ACEK_SPOOL), then RESUME; still paused"
    )
    assert rig.module.get_status()["pause_reason"] == rig.gcode.lines[-1]
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PLA", "COLOR": "ffffff"})
    assert resume(rig) == ["RESUME"]
    assert rig.module.get_status()["pause_reason"] is None  # the print goes on


def test_a_refills_new_spool_tag_is_read_before_it_is_asked_for(tmp_path: Path) -> None:
    """The tag first; the spool asked for only when no tag tells it."""
    rig, a = ran_out(tmp_path)
    rig.module.spool_store.records.pop("a:1", None)
    rig.task_config.status["filament_type"][0] = "NONE"
    a.lanes[0]["insert"] = True
    a.lanes[0]["tag"] = {"state": "pending"}
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    record = {"brand": "Generic", "material": "PLA", "color_rgba": "FFFFFFFF"}

    run_from_command = rig.gcode.run_script_from_command

    def run_script_from_command(script: str) -> None:  # the unit's read with motion finds the tag
        if script.startswith("ACEK_RFID_READ LANE=1 UNIT=a MOVE=1"):
            a.lanes[0]["tag"] = {"state": "read", "record": record}
        run_from_command(script)

    rig.gcode.run_script_from_command = run_script_from_command
    assert resume(rig) == ["RESUME"]
    assert "ace2k_u1: a:1: reading the new spool's tag" in rig.gcode.lines
    assert not any("is not known" in ln for ln in rig.gcode.lines)


def test_a_refills_new_spool_of_another_material_is_refused(tmp_path: Path) -> None:
    """The PLA ran out and an ABS went in by mistake."""
    rig, a = ran_out(tmp_path)
    assert rig.module.tracker.plan is not None and rig.module.tracker.plan.set
    tool = next(n for n, h in rig.module.tracker.plan.choice.items() if h is rig.lane)
    want = rig.module.tracker.plan.tools[tool]["material"]
    a.lanes[0]["insert"] = True
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "ABS", "COLOR": "ffffff"})
    started = len(a.feed.started)
    assert resume(rig) == []
    assert len(a.feed.started) == started
    assert rig.gcode.lines[-1] == (
        f"ace2k_u1: head 1: T{tool} wants {want}, a:1 holds ABS: put a spool of that material in"
        " bay a:1 (or set its spool if it is one), then RESUME; still paused"
    )


def test_a_refill_counts_the_new_spool_only_not_the_heads_setting(tmp_path: Path) -> None:
    """After a refill the head's setting is the spool that ran out (PLA);
    an untagged spool with no record must be set, not loaded as that PLA."""
    rig, a = ran_out(tmp_path)
    rig.module.spool_store.records.pop("a:1", None)
    assert rig.task_config.status["filament_type"][0] == "PLA"  # the run-out spool's setting
    a.lanes[0]["insert"] = True
    a.lanes[0]["tag"] = {"state": "no_tag"}
    started = len(a.feed.started)
    assert resume(rig) == []
    assert len(a.feed.started) == started
    assert "the new spool in bay a:1 is not known" in rig.gcode.lines[-1]


def test_no_tag_read_with_the_heads_sensor_set(tmp_path: Path) -> None:
    """The read moves the filament out of its bay toward the head: only with
    the head empty."""
    rig, a = ran_out(tmp_path)
    rig.module.spool_store.records.pop("a:1", None)
    a.lanes[0]["insert"] = True
    a.lanes[0]["tag"] = {"state": "pending"}
    rig.head_sensor.detected = True
    assert rig.module.refiller._head_empty_now(rig.lane) is False
    sent = []
    rig.gcode.run_script_from_command = sent.append
    assert rig.module.refiller.spool_known(rig.module.swapper.swap_pending) is False
    assert not [x for x in sent if x.startswith("ACEK_RFID_READ")]
    assert "its sensor sees filament, so the new spool's tag is not read" in rig.gcode.lines[-1]
    assert "set the spool (Edit spool, or ACEK_SPOOL), or clear the head" in rig.gcode.lines[-1]


def test_a_refills_rest_past_the_drive_gear_after_a_whole_move_is_out(tmp_path: Path) -> None:
    """The rest's first 2000 mm ran done, the next move ended
    unload_incomplete (the tail at the bay's sensor, past the gear): the rest is out, no failure."""
    rig, a = refill_rig(tmp_path)
    tail(rig)
    ends_ = iter(["done", "unload_incomplete"])

    def on_start(move: FakeMove) -> None:
        if rig.in_free_check:
            move.event = {"kind": "done", "seq": move.seq}
        else:
            move.event = {"kind": next(ends_, "done"), "seq": move.seq}

    a.feed.on_start = on_start
    run_pauses(rig)
    assert rig.gcode.lines[-1] == LINE
    assert "a:1's tail is past the unit's drive gear, at its bay" in " ".join(rig.gcode.lines)
    assert rig.module.swapper.swap_pending.step == "hand over"
    assert 0 in a.feed.cleared  # the unit's error on the lane cleared
    # the tail still at the bay, never taken out: the RESUME waits for it, nothing moved
    a.lanes[0]["insert"] = True
    started = len(a.feed.started)
    assert resume(rig) == []
    assert len(a.feed.started) == started
    assert "the old filament's tail is still in bay a:1" in rig.gcode.lines[-1]
    rig.lane.bay_emptied_at = rig.reactor.monotonic() + 1.0  # taken out, the new spool in
    new_spool(rig)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig) == ["RESUME"]


def test_a_refills_rest_of_one_move_past_the_drive_gear_is_out_too(tmp_path: Path) -> None:
    """The tail at the bay ends the rest in its first move as well."""
    rig, a = refill_rig(tmp_path)
    tail(rig)

    def on_start(move: FakeMove) -> None:
        if not rig.in_free_check:
            rig.head_sensor.detected = False  # the rest left the head
        kind = "done" if rig.in_free_check else "unload_incomplete"
        move.event = {"kind": kind, "seq": move.seq}

    a.feed.on_start = on_start
    run_pauses(rig)
    assert rig.gcode.lines[-1] == LINE
    assert rig.module.swapper.swap_pending.step == "hand over"


def test_a_refill_drops_the_run_out_spools_record(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    new_spool(rig)  # a record for a:1 before it runs out
    tail(rig)
    a.feed.on_start = ends("runout")
    run_pauses(rig)
    assert "a:1" not in rig.module.spool_store.records


def test_a_tag_still_being_read_says_so_and_stays_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ace2k_u1_refill, "TAG_WAIT_S", 0.5)
    rig, a = ran_out(tmp_path)
    a.lanes[0]["insert"] = True
    a.lanes[0]["tag"] = {"state": "searching"}
    started = len(a.feed.started)
    assert resume(rig) == []
    assert len(a.feed.started) == started
    assert "is still being read — RESUME again in a moment" in rig.gcode.lines[-1]


def test_the_spool_check_reads_the_step_the_probe_goes_on_from(tmp_path: Path) -> None:
    """Review: a refill failed at its load, the head's sensor empty at RESUME: the probe goes on
    from the hand over, before the spool step, so the missing spool is caught there."""
    rig, a = ran_out(tmp_path)
    rig.module.spool_store.records.pop("a:1", None)
    rig.task_config.status["filament_type"][0] = "NONE"
    a.lanes[0]["insert"] = True
    rig.module.swapper.swap_pending.step = "load"  # failed at its load: past the spool step
    started = len(a.feed.started)
    assert resume(rig) == []
    assert len(a.feed.started) == started
    assert rig.module.swapper.swap_pending.step == "hand over"
    assert "the new spool in bay a:1 is not known" in rig.gcode.lines[-1]


def test_resume_after_a_failed_roll_back_takes_the_rest_out_again_and_stays_paused(
    tmp_path: Path,
) -> None:
    rig, a = refill_rig(tmp_path)
    a.feed.on_start = freed_then(rig, "stuck")
    tail(rig)
    run_pauses(rig)
    assert rig.module.swapper.swap_pending.step == "roll back"
    a.feed.on_start = ends("runout")
    assert resume(rig) == []
    # the free check and the failed roll back, then the roll back again alone
    assert [mode for _l, mode, *_ in a.feed.started] == ["rollback"] * 3
    assert rig.gcode.lines[-1] == LINE
    assert rig.module.swapper.swap_pending.step == "hand over"
    assert rig.print_stats.state == "paused"


def test_resume_while_the_rest_is_still_rolling_back_does_nothing(tmp_path: Path) -> None:
    rig, a = ran_out(tmp_path)
    rig.module.swapper.swap_pending.timer = True  # the pause timer still runs it
    a.lanes[0]["insert"] = True
    started = len(a.feed.started)
    assert resume(rig) == []
    assert "still being rolled back" in rig.gcode.lines[-1]
    assert len(a.feed.started) == started


def test_the_u1s_runout_never_fires_for_a_lane_of_a_shared_head(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    rig.adapter.runout_source = "unit"
    tail(rig)
    rig.adapter.on_feed_event(0, {"kind": "tail_out", "unit": "a", "seq": 0, "mode": "following"})
    assert [path for path, _h, _k in rig.adapter.pause_runner.pause_pending] == ["shared runout"]
    a.feed.on_start = ends("runout")
    run_pauses(rig)
    assert not any("IS_RUNOUT" in line for line in rig.sent)
    assert not any("pausing the print" in line for line in rig.gcode.lines)


LONE_LINE = (
    "ace2k_u1: head 2: a:3 ran out — its rest is out of bay a:3: take it out, put a new spool in"
    " and RESUME"
)


def lone_ran_out(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k, Hooked]:
    """runout_source: unit, a:3 alone in head 2 runs out at its bay; the pause timer runs."""
    rig, a = refill_rig(tmp_path)
    rig.adapter.runout_source = "unit"
    h = rig.adapter.slot_of_head(1).current  # a:3, alone in head 2
    h.follow_wanted = True
    rig.left.runout_sensor[0].detected = True  # e1 (head 2): the left feed's channel 0
    a.lanes[2].update(insert=False, mode="following")
    a.feed.on_start = ends("runout")
    real = a.feed.start_move

    def clears_e1(lane: int, mode: str, length: float, speed: float, **kw: bool | str) -> FakeMove:
        move = real(lane, mode, length, speed, **kw)
        if lane == 2 and mode == "rollback" and not rig.in_free_check:
            rig.left.runout_sensor[0].detected = False  # the rest's roll back clears e1
        return move

    a.feed.start_move = clears_e1
    tail(rig, 2)
    return rig, a, h


def test_a_one_lane_head_with_the_unit_as_source_takes_the_end_at_the_bay(tmp_path: Path) -> None:
    rig, a, h = lone_ran_out(tmp_path)
    assert ("stop", 2) in a.feed.log and h.follow_wanted is False and h.hold is True
    assert rig.adapter.pause_runner.pause_pending == [("shared runout", h, "tail")]
    assert "ace2k_u1: lane a:3 ran out at the bay — its tail feeds on to e1" not in rig.gcode.lines


def test_a_one_lane_head_with_the_unit_as_source_refills_as_a_shared_head(tmp_path: Path) -> None:
    rig, a, h = lone_ran_out(tmp_path)
    run_pauses(rig)
    assert rig.sent[:2] == ["PAUSE", "T1 A0"]
    assert rig.sent[2].startswith("ACEK_SWAP_UNLOAD HEAD=1 ")
    assert " LOAD_TEMP=0 " in rig.sent[2]  # the heater left off at the end
    modes = [(lane, mode) for lane, mode, _l, _s, _m in a.feed.started]
    assert modes[:2] == [(2, "rollback"), (2, "rollback")]  # the free check, then the rest
    assert rig.gcode.lines[-1] == LONE_LINE
    pending = rig.module.swapper.swap_pending
    assert pending.refill and pending.old is pending.new is h and pending.step == "hand over"
    assert rig.print_stats.state == "paused"


def test_a_one_lane_heads_refill_reads_the_new_spool_at_resume(tmp_path: Path) -> None:
    rig, a, h = lone_ran_out(tmp_path)
    run_pauses(rig)
    a.lanes[2]["insert"] = True  # the new spool in the bay, no tag read, no spool set
    a.lanes[2]["mode"] = "idle"
    assert resume(rig) == []
    assert rig.gcode.lines[-1].startswith("ace2k_u1: head 2: the new spool in bay a:3 is not known")
    new_spool(rig, 3)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.left.runout_sensor[0], "detected", True)
    assert resume(rig) == ["RESUME"]
    assert any(ln.startswith("ACEK_SWAP_LOAD HEAD=1 ") for ln in rig.sent)
    assert rig.module.swapper.swap_pending is None
    assert "ace2k_u1: head 2: a:3 in (0 s)" in rig.gcode.lines


def test_a_one_lane_heads_refill_does_not_take_the_old_spools_setting(tmp_path: Path) -> None:
    """The head's setting was the run-out spool's: the new spool must be told by its tag, Edit
    spool or Define, or a change of the setting with it in the bay."""
    rig, a, h = lone_ran_out(tmp_path)
    rig.task_config.status["filament_type"][1] = "PLA"  # the run-out spool's
    run_pauses(rig)
    tick(rig)  # the bay seen empty
    a.lanes[2]["insert"] = True  # the new spool in the bay, no tag read, no spool set
    a.lanes[2]["mode"] = "idle"
    tick(rig)
    tick(rig)
    assert rig.module.material.defined(h) is False
    assert resume(rig) == []
    assert rig.gcode.lines[-1].startswith("ace2k_u1: head 2: the new spool in bay a:3 is not known")
    rig.task_config.status["filament_type"][1] = "PETG"  # changed with the new spool in
    tick(rig)
    assert rig.module.material.defined(h) is True
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.left.runout_sensor[0], "detected", True)
    assert resume(rig) == ["RESUME"]


def test_a_shared_heads_setting_stops_speaking_for_its_lane_once_its_bay_is_seen_empty(
    tmp_path: Path,
) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    h = rig.adapter.hooked_lane(a, 0)
    slot = rig.adapter.slot_of(h)
    rig.adapter.set_current(slot, h)
    rig.task_config.status["filament_type"][0] = "PLA"
    slot.setting_for = h
    a.lanes[0]["insert"] = True
    tick(rig)
    assert rig.module.material.defined(h) is True
    a.lanes[0]["insert"] = False
    for _ in range(12):  # out of the head for SETTING_LEAVE_S
        tick(rig)
    assert slot.setting_for is None
    a.lanes[0]["insert"] = True  # a new spool, no tag, no record
    tick(rig)
    assert rig.module.material.defined(h) is False


def test_a_shared_heads_refill_does_not_take_the_setting_written_for_its_lane(
    tmp_path: Path,
) -> None:
    """The setting written for a:1 is the run-out spool's: the new spool must be told."""
    rig, a = ran_out(tmp_path)
    rig.module.spool_store.records.pop("a:1", None)
    rig.adapter.slot_of_head(0).setting_for = rig.lane
    a.lanes[0]["insert"] = True
    a.lanes[0]["tag"] = {"state": "no_tag"}
    assert resume(rig) == []
    assert "the new spool in bay a:1 is not known" in rig.gcode.lines[-1]


def test_a_one_lane_head_keeps_the_tail_with_the_head_as_source(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    tail(rig, 2)  # a:3, alone in head 2; runout_source: head
    assert ("stop", 2) not in a.feed.log
    assert "ace2k_u1: lane a:3 ran out at the bay — its tail feeds on to e1" in rig.gcode.lines
    assert rig.adapter.pause_runner.pause_pending == []


def lone_rig(tmp_path: Path, source: str) -> SimpleNamespace:
    """Four heads of one lane each, with runout_source as given."""
    (tmp_path / source).mkdir()
    rig, _ = tools_rig(tmp_path / source, "a:1, a:2, a:3, a:4", values={"runout_source": source})
    return rig


def test_one_lane_heads_resume_and_tools_go_on_with_a_pending_refill_with_the_unit_source(
    tmp_path: Path,
) -> None:
    for source, guarded in (("unit", True), ("head", False)):
        rig = lone_rig(tmp_path, source)
        swapper = rig.module.swapper
        h = rig.adapter.slot_of_head(0).current
        swapper.swap_pending = SwapState(
            slot=rig.adapter.slot_of(h), old=h, new=h, tool=None, refill=True
        )
        went_on, swapped, u1 = [], [], []
        swapper.go_on = lambda gcmd, pending, seen=went_on: seen.append(pending) or False
        swapper.swap = lambda gcmd, slot, lane, n, seen=swapped: seen.append(n) or False
        rig.gcode.commands_orig["RESUME"] = u1.append
        rig.gcode.commands_orig["T0"] = u1.append
        rig.gcode.commands["RESUME"](Gcmd({}, "RESUME"))
        rig.gcode.commands["T0"](Gcmd({}, "T0"))
        if guarded:  # the refill goes on first; a T waits for it
            assert len(went_on) == 1 and swapped == [0] and u1 == [], source
        else:  # the U1's own commands, unchanged
            assert went_on == [] and swapped == [] and len(u1) == 2, source


def test_one_lane_heads_refuse_a_start_without_the_macros_with_the_unit_source(
    tmp_path: Path,
) -> None:
    for source, refused in (("unit", True), ("head", False)):
        rig = lone_rig(tmp_path, source)
        (tmp_path / source / "gcodes" / "five.gcode").write_text(FIVE_TWO_USED)
        drop_macro(rig, "ACEK_SWAP_LOAD")
        if refused:
            with pytest.raises(ValueError, match="ACEK_SWAP_LOAD is not defined"):
                print_start(rig, name="five.gcode")
        else:
            assert len(print_start(rig, name="five.gcode")) == 1


def test_a_new_spool_for_a_one_lane_heads_pending_refill_is_parked_and_not_told(
    tmp_path: Path,
) -> None:
    rig, a, h = lone_ran_out(tmp_path)
    run_pauses(rig)
    a.lanes[2].update(insert=True, mode="idle")  # the new spool in, early
    a.feed.grips.clear()
    since = len(rig.left.calls)
    for _ in range(10):
        rig.reactor.now += 0.5
        rig.adapter.watch(rig.reactor.now)
    assert (2, "park") in a.feed.grips
    assert ("port_event", True, 0) not in rig.left.calls[since:]  # no preload of e1


def test_the_watch_checks_no_shared_end_of_a_one_lane_heads_lane(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    asked = []
    real = rig.adapter.swap_pending

    def swap_pending() -> bool:
        asked.append(True)
        return real()

    rig.adapter.swap_pending = swap_pending
    for h in rig.adapter.hooked:  # the shared heads' lanes: taken already, nothing asked
        if rig.adapter.shared(h):
            h.end_taken = True
    a.lanes[2]["tail"] = True  # a:3, alone in head 2
    rig.adapter.watch(rig.reactor.now)
    assert asked == []
    assert ("stop", 2) not in a.feed.log and rig.adapter.pause_runner.pause_pending == []


def test_a_parked_lane_changes_nothing_new(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    parked = rig.adapter.slot_of_head(0).lanes[1]  # a:2, behind head 1's splitter
    a.lanes[0]["insert"] = True  # head 1's own lane still has its spool
    a.lanes[1]["insert"] = False
    tail(rig, 1)
    rig.adapter.watch(rig.reactor.now)
    assert ("stop", 1) not in a.feed.log
    assert rig.adapter.pause_runner.pause_pending == []
    assert rig.module.swapper.swap_pending is None
    assert parked.hold is False


def test_a_tail_outside_a_print_is_the_line_only(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    rig.print_stats.state = "standby"
    tail(rig)
    assert ("stop", 0) not in a.feed.log
    assert rig.adapter.pause_runner.pause_pending == []
    assert "ace2k_u1: lane a:1 ran out at the bay — its tail feeds on to e0" in rig.gcode.lines


@pytest.mark.parametrize(
    "lanes, source, said",
    [
        ("a:1+a:2, a:3, a:4, b:1+b:2+b:3+b:4", None, True),
        ("a:1+a:2, a:3, a:4, b:1+b:2+b:3+b:4", "unit", False),
        ("a:1, a:2, a:3, a:4", None, False),
    ],
)
def test_the_start_says_once_that_a_shared_head_takes_the_end_at_the_bay(
    tmp_path: Path, lanes: str, source: str | None, said: bool
) -> None:
    values = {} if source is None else {"runout_source": source}
    rig, _ = tools_rig(tmp_path, lanes=lanes, values=values)
    assert rig.gcode.lines.count(START_LINE) == (1 if said else 0)


def test_the_config_comment_says_the_same() -> None:
    text = (Path(__file__).resolve().parents[1] / "config" / "ace2k-u1.cfg").read_text()
    assert "heads several lanes feed take a spool's end at the unit's bay sensor" in " ".join(
        line.lstrip("#").strip() for line in text.splitlines()
    )


# --- the plain PAUSE not taken from the timer ----------------------------------------------------


def pause_not_taken(rig: SimpleNamespace) -> None:
    """The U1 ignores the plain PAUSE (pause_resume neither paused nor pausing); the adapter's
    runout pause for the head recorded instead of run."""
    real = rig.gcode.run_script

    def run_script(script: str) -> None:
        if script == "PAUSE":
            rig.sent.append("PAUSE")  # sent, not taken
            return
        real(script)

    rig.gcode.run_script = run_script
    rig.runout_paused = []  # (lane, the swap pending while it ran)
    extruder = rig.printer.objects["extruder"]

    def runout_pause(h: Hooked) -> None:
        """The U1's runout pause as the adapter runs it from the timer: its PAUSE IS_RUNOUT=1
        retracts 2 mm into printing_e_pos and pauses the print."""
        rig.runout_paused.append((h, rig.module.swapper.swap_pending))
        rig.gcode.run_script("PAUSE IS_RUNOUT=1")
        extruder.printing_e_pos -= 2.0
        rig.pause_resume.is_paused = True
        rig.print_stats.state = "paused"

    rig.adapter.pause_runner.runout_pause = runout_pause


def test_a_plain_pause_not_taken_falls_back_to_the_runout_pause_and_keeps_the_refill(
    tmp_path: Path,
) -> None:
    rig, a = refill_rig(tmp_path)
    pause_not_taken(rig)
    tail(rig)
    run_pauses(rig)
    # the refill not visible to a T of the head until the runout pause has returned
    assert rig.runout_paused == [(rig.lane, None)]
    pending = rig.module.swapper.swap_pending
    assert pending.refill and pending.step == "unload"
    assert pending.timer is False
    # printing_e_pos read after the runout pause: its retraction in it
    assert pending.e_pos == -12.0
    # everything sent from the timer through run_script, the INNER_RESUME variables included
    assert {runner for runner, _ in rig.runners} == {"run_script"}
    assert rig.sent[-1] == (
        "SET_GCODE_VARIABLE MACRO=INNER_RESUME VARIABLE=last_extruder_index VALUE=0"
    )
    # the unload and the roll back did not run: they wait for the RESUME
    assert not any(line.startswith("ACEK_SWAP_UNLOAD") for line in rig.sent)
    assert a.feed.started == []
    assert rig.lane.hold is False


def test_resume_runs_the_unload_not_run_then_waits_for_the_new_spool(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    pause_not_taken(rig)
    tail(rig)
    run_pauses(rig)
    a.feed.on_start = ends("runout")
    rig.sent.clear()
    assert resume(rig) == []  # the U1's RESUME not run: the new spool comes first
    assert rig.sent[:2] == [
        "T0 A0",
        "ACEK_SWAP_UNLOAD HEAD=0 TEMP=250 SOFT=0 NOZZLE_DIAMETER=0.4 LOAD_TEMP=0 MATERIAL=PLA",
    ]
    assert [mode for _l, mode, *_ in a.feed.started] == ["rollback", "rollback"]  # free, rest
    assert rig.gcode.lines[-1] == LINE
    assert rig.module.swapper.swap_pending.step == "hand over"
    assert rig.print_stats.state == "paused"
    # printing_e_pos holds the value the pause left, not one from before it
    assert rig.printer.objects["extruder"].printing_e_pos == -12.0


def test_the_resume_rereads_printing_e_pos_before_an_unload_not_run(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    pause_not_taken(rig)
    tail(rig)
    run_pauses(rig)
    rig.printer.objects["extruder"].printing_e_pos = -15.0  # moved since, while paused
    a.feed.on_start = ends("runout")
    resume(rig)
    assert rig.module.swapper.swap_pending.e_pos == -15.0
    assert rig.printer.objects["extruder"].printing_e_pos == -15.0


def test_the_rest_is_taken_out_only_with_the_print_paused(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    pause_not_taken(rig)
    tail(rig)
    run_pauses(rig)
    pending = rig.module.swapper.swap_pending
    rig.pause_resume.is_paused = False  # the print running again (a RESUME by the U1 itself)
    rig.print_stats.state = "printing"
    rig.sent.clear()
    assert rig.module.refiller.refill_ready(pending) is False
    assert "PAUSE it first" in rig.gcode.lines[-1]
    assert rig.sent == [] and a.feed.started == []
    assert pending.step == "unload"


def test_a_runout_pause_declined_too_drops_the_refill_and_says_the_print_runs_on(
    tmp_path: Path,
) -> None:
    rig, a = refill_rig(tmp_path)
    pause_not_taken(rig)
    rig.adapter.pause_runner.runout_pause = lambda h: (
        None
    )  # the head sensor disabled, say: no pause
    tail(rig)
    run_pauses(rig)
    assert rig.module.swapper.swap_pending is None
    line = (
        "ace2k_u1: head 1: a:1 ran out at its bay — the lane is stopped and the print was NOT"
        " paused: pause it by hand"
    )
    assert rig.gcode.lines[-1] == line
    assert ("stop", 0) in a.feed.log and a.feed.started == []
    # the end stays taken and the lane held: the bay watch takes it no more
    assert rig.lane.hold is True and rig.lane.end_taken is True


def declined(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:
    """A runout whose plain pause and runout pause were both declined: the print goes on."""
    rig, a = refill_rig(tmp_path)
    pause_not_taken(rig)
    rig.adapter.pause_runner.runout_pause = lambda h: None
    tail(rig)
    run_pauses(rig)
    a.lanes[0]["tail"] = True  # its status still shows the tail
    return rig, a


def test_ticks_after_a_declined_fallback_do_nothing_more(tmp_path: Path) -> None:
    rig, a = declined(tmp_path)
    stops, lines, sent = a.feed.log.count(("stop", 0)), len(rig.gcode.lines), len(rig.sent)
    for _ in range(3):
        rig.adapter.watch(rig.reactor.now)
        run_pauses(rig)
    assert rig.adapter.pause_runner.pause_pending == []
    assert a.feed.log.count(("stop", 0)) == stops
    assert len(rig.gcode.lines) == lines and len(rig.sent) == sent
    assert rig.lane.hold is True and rig.lane.end_taken is True


def test_a_declined_end_is_given_back_once_the_bay_reads_full_again(tmp_path: Path) -> None:
    rig, a = declined(tmp_path)
    rig.adapter.watch(rig.reactor.now)  # the bay empty
    a.lanes[0]["tail"] = False
    a.lanes[0]["insert"] = True  # a new spool in its bay
    a.lanes[0]["mode"] = "idle"
    rig.adapter.watch(rig.reactor.now)
    assert rig.lane.hold is False and rig.lane.end_taken is False


def test_a_declined_end_is_given_back_at_the_prints_end(tmp_path: Path) -> None:
    rig, a = declined(tmp_path)
    rig.module.print_over()
    assert rig.lane.hold is False and rig.lane.end_taken is False


# --- the bay watch as a backup trigger -----------------------------------------------------------


def test_a_missed_tail_notice_is_caught_by_the_bay_watch(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    a.lanes[0]["tail"] = True
    rig.adapter.watch(rig.reactor.now)
    assert ("stop", 0) in a.feed.log
    assert rig.lane.hold is True and rig.lane.follow_wanted is False
    assert rig.adapter.pause_runner.pause_pending == [("shared runout", rig.lane, "tail")]


def test_an_empty_bay_while_following_is_caught_by_the_bay_watch(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)  # following, its bay empty, no tail flag
    rig.adapter.watch(rig.reactor.now)
    assert rig.adapter.pause_runner.pause_pending == [("shared runout", rig.lane, "tail")]


def test_a_notice_and_the_watch_run_it_once(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    a.lanes[0]["tail"] = True
    tail(rig)
    rig.adapter.watch(rig.reactor.now)
    assert rig.adapter.pause_runner.pause_pending == [("shared runout", rig.lane, "tail")]
    assert a.feed.log.count(("stop", 0)) == 1
    a.feed.on_start = ends("runout")
    run_pauses(rig)
    rig.adapter.watch(rig.reactor.now)  # the refill pending: nothing again
    assert rig.adapter.pause_runner.pause_pending == []
    assert rig.gcode.lines.count(LINE) == 1
    assert [mode for _l, mode, *_ in a.feed.started] == ["rollback", "rollback"]  # free, rest


def test_the_watch_takes_nothing_while_a_swap_is_pending(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.module.swapper.swap_pending = SwapState(
        slot, slot.current, slot.lanes[-1], None, step="push"
    )
    a.lanes[0]["tail"] = True
    rig.adapter.watch(rig.reactor.now)
    assert ("stop", 0) not in a.feed.log
    assert rig.adapter.pause_runner.pause_pending == []


def test_the_watch_takes_nothing_during_a_live_swap_of_the_head(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    rig.adapter.swapping.add(0)
    a.lanes[0]["tail"] = True
    rig.adapter.watch(rig.reactor.now)
    assert ("stop", 0) not in a.feed.log
    assert rig.adapter.pause_runner.pause_pending == []


# --- the final review's fixes ---------------------------------------------------------------------


def no_plan(rig: SimpleNamespace) -> None:
    """The print runs with no plan (a file of one tool per head, started from the screen)."""
    rig.module.tracker.plan = None
    rig.module.tracker.plan_started = False
    rig.module.tracker.plan_seen_on = False


@pytest.mark.parametrize("end", ["cancelled", "complete", "error"])
def test_a_refill_with_no_plan_ends_with_its_print(tmp_path: Path, end: str) -> None:
    rig, a = refill_rig(tmp_path)
    no_plan(rig)
    rig.module.watch_print(rig.reactor.now)  # the print seen on
    tail(rig)
    a.feed.on_start = ends("runout")
    run_pauses(rig)
    assert rig.module.swapper.swap_pending.refill
    rig.print_stats.state = end
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.swapper.swap_pending is None and rig.module.swapper.tools_owed == []
    assert rig.lane.end_taken is False and rig.lane.hold is False
    # the next print's T runs as any T
    rig.print_stats.state = "printing"
    calls = []
    rig.gcode.commands_orig["T0"] = calls.append
    rig.gcode.commands["T0"](Gcmd({}, "T0"))
    assert len(calls) == 1
    assert not [ln for ln in rig.gcode.lines if "refused" in ln]


def test_the_refill_line_with_no_plan_names_the_head(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    no_plan(rig)
    tail(rig)
    a.feed.on_start = ends("runout")
    run_pauses(rig)
    a.lanes[0]["insert"] = True
    new_spool(rig)  # the new spool set (its record dropped with the run-out one)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    resume(rig)
    said = [ln for ln in rig.gcode.lines if "no printing temperature" in ln]
    assert said == [
        "ace2k_u1: head 1: no printing temperature known for head 1: the load temperature, 230 °C,"
        " is kept"
    ]
    assert not [ln for ln in rig.gcode.lines if "TNone" in ln]
    # no tool: the U1's pre-extrusion macro itself, at the load temperature kept
    pre = [ln for ln in rig.sent if ln.startswith("INNER_PREEXTRUDE_FILAMENT SOFT=")]
    assert pre == ["INNER_PREEXTRUDE_FILAMENT SOFT=0 TEMP=230 LENGTH=0"]
    assert not [ln for ln in rig.sent if ln.startswith("SM_PRINT_PREEXTRUDE_FILAMENT")]
    assert rig.gcode.lines.count("ace2k_u1: head 1: pre-extrusion after the refill") == 1
    assert rig.module.tracker.preextrude_after is None  # no file pre-extrusion follows a refill
    assert rig.module.tracker.wait_skip_tool is None  # and no wait is skipped


def test_the_refill_pre_extrudes_at_the_temperature_its_load_left(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    no_plan(rig)
    tail(rig)
    a.feed.on_start = ends("runout")
    run_pauses(rig)
    a.lanes[0]["insert"] = True
    new_spool(rig)  # PLA: the load runs at 230 °C
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    loaded = rig.module.swapper.steps["loaded"]

    def loaded_then_respooled(state: object) -> None:
        loaded(state)
        new_spool(rig, kind="PETG")  # the record changes before the return: 245 °C now

    rig.module.swapper.steps["loaded"] = loaded_then_respooled
    resume(rig)
    pre = [ln for ln in rig.sent if ln.startswith("INNER_PREEXTRUDE_FILAMENT SOFT=")]
    assert pre == ["INNER_PREEXTRUDE_FILAMENT SOFT=0 TEMP=230 LENGTH=0"]


def second_runout(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k, Hooked]:
    """Head 1's refill pending, the print paused; head 4's lane then runs out too (its notice
    taken while the print was still printing). Head 4's sensor is rig.head4 (True: set)."""
    rig, a = ran_out(tmp_path)
    slot4 = rig.adapter.slot_of_head(3)
    h4 = slot4.current or slot4.lanes[0]
    rig.adapter.set_current(slot4, h4)
    rig.head4 = {"set": True}
    real = rig.adapter.head_set
    rig.adapter.head_set = lambda h: rig.head4["set"] if h.head == 3 else real(h)
    rig.module.refiller.shared_runout(h4)
    return rig, a, h4


def test_a_second_runout_during_a_pause_blocks_resume_until_its_head_is_loaded_again(
    tmp_path: Path,
) -> None:
    rig, a, h4 = second_runout(tmp_path)
    n = rig.adapter.lane_text(h4)
    recovery = (
        "RESUME waits for head 4: unload head 4, roll"
        f" {n} back out of its bay, put a new spool in and load head 4 from the U1 screen"
    )
    assert rig.gcode.lines[-1] == (
        f"ace2k_u1: head 4: {n} ran out at its bay while head 1: refill of a:1 is pending"
        f" — {recovery}"
    )
    a.lanes[0]["insert"] = True  # head 1's new spool in
    new_spool(rig)  # the new spool set (its record dropped with the run-out one)
    started = len(a.feed.started)
    # the rest still in head 4: refused
    assert resume(rig) == []
    assert rig.gcode.lines[-1] == f"ace2k_u1: head 4: {n} ran out — {recovery}; still paused"
    assert len(a.feed.started) == started
    # unloaded by hand: still refused (empty)
    rig.head4["set"] = False
    rig.module.watch_print(rig.reactor.now)
    assert resume(rig) == []
    assert "still paused" in rig.gcode.lines[-1]
    # loaded again from the screen: RESUME goes on
    rig.head4["set"] = True
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig)[-1] == "RESUME"
    assert rig.module.refiller.runout_stopped == {}


def test_a_runout_stopped_during_a_pause_keeps_its_lane_held_until_its_head_is_loaded_again(
    tmp_path: Path,
) -> None:
    rig, a = ran_out(tmp_path)
    slot4 = rig.adapter.slot_of_head(3)
    h4 = slot4.current or slot4.lanes[0]
    rig.adapter.set_current(slot4, h4)
    head4 = {"set": True}
    real = rig.adapter.head_set
    rig.adapter.head_set = lambda h: head4["set"] if h.head == 3 else real(h)
    h4.end_taken, h4.hold = True, True  # as the notice took it (GripWatch.shared_end)
    rig.module.refiller.shared_runout(h4)
    assert (h4.end_taken, h4.hold) == (True, True)  # stopped: no follow, the watch takes it no more
    a.lanes[0]["insert"] = True  # head 1's new spool in
    new_spool(rig)  # the new spool set (its record dropped with the run-out one)
    assert resume(rig) == []
    assert (h4.end_taken, h4.hold) == (True, True)
    head4["set"] = False  # unloaded by hand
    assert resume(rig) == []
    assert (h4.end_taken, h4.hold) == (True, True)
    head4["set"] = True  # loaded again from the screen: given back as RESUME goes on
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert resume(rig)[-1] == "RESUME"
    assert (h4.end_taken, h4.hold) == (False, False)


def test_a_head_seen_empty_only_at_resume_counts_as_unloaded(tmp_path: Path) -> None:
    rig, a, h4 = second_runout(tmp_path)
    rig.head4["set"] = False
    assert resume(rig) == []
    rig.head4["set"] = True
    assert rig.module.refiller.runout_stopped[3]["emptied"] is True


def test_a_stopped_runout_ends_with_its_print(tmp_path: Path) -> None:
    rig, a, h4 = second_runout(tmp_path)
    rig.module.watch_print(rig.reactor.now)
    rig.print_stats.state = "cancelled"
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.refiller.runout_stopped == {}


def test_a_tail_notice_during_a_live_swap_is_left_to_the_watch_after_it(tmp_path: Path) -> None:
    rig, a = refill_rig(tmp_path)
    rig.adapter.swapping.add(0)
    tail(rig)
    assert rig.adapter.pause_runner.pause_pending == []
    assert rig.lane.end_taken is False and rig.lane.hold is False
    rig.adapter.swapping.discard(0)
    a.lanes[0]["tail"] = True
    rig.adapter.watch(rig.reactor.now)  # the swap over: the watch takes it
    assert rig.adapter.pause_runner.pause_pending == [("shared runout", rig.lane, "tail")]


def test_a_new_spool_for_a_shared_heads_pending_refill_is_left_to_the_units_load(
    tmp_path: Path,
) -> None:
    rig, a = ran_out(tmp_path)  # the rest out of bay a:1, head 1 empty
    a.feed.grips.clear()
    started = len(a.feed.started)
    a.lanes[0]["insert"] = True  # the new spool in the run-out lane's bay
    rig.adapter.watch(rig.reactor.now)
    assert not [g for g in a.feed.grips if g[0] == 0]  # the unit's own load, with its search
    assert len(a.feed.started) == started


def test_the_current_lane_of_an_empty_shared_head_has_no_grip(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    rig.left.runout_sensor[1].detected = False  # head 1 empty
    rig.adapter.watch(rig.reactor.now)
    assert not [g for g in a.feed.grips if g[0] in (0, 1, 2, 3)]


def test_the_current_lane_of_an_empty_shared_head_with_the_wait_in_the_image_has_no_grip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    rig.left.runout_sensor[1].detected = False  # head 1 empty
    rig.adapter.watch(rig.reactor.now)
    assert not [g for g in a.feed.grips if g[0] in (0, 1, 2, 3)]


def test_a_shared_heads_current_lane_behind_a_piece_is_gripped_with_the_wait_in_the_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the wait is for a lone head only: a shared head keeps its grip
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    rig.left.runout_sensor[1].detected = True
    slot.lanes[0].after_tail = True  # head 1 holds a piece the lane no longer owns
    rig.adapter.watch(rig.reactor.now)
    assert (0, 40.0) in a.feed.grips
    assert not [g for g in a.feed.grips if g[1] == "wait"]


def test_no_grip_reaches_a_swaps_new_lane_between_its_hand_over_and_its_push(
    tmp_path: Path,
) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[1])  # a:2 handed the head, not pushed yet
    rig.left.runout_sensor[1].detected = False  # head 1 empty
    rig.adapter.swapping.add(0)
    rig.adapter.watch(rig.reactor.now)
    assert not [g for g in a.feed.grips if g[0] in (0, 1)]
    rig.adapter.swapping.discard(0)  # the swap over: the grip as the head stands, none
    rig.adapter.watch(rig.reactor.now)
    assert not [g for g in a.feed.grips if g[0] in (0, 1)]


# --- the run-out's guards (characterisation) -----------------------------------------------------


def test_no_tag_read_with_the_heads_sensor_unreadable_stays_paused(tmp_path: Path) -> None:
    rig, a = ran_out(tmp_path)
    rig.module.spool_store.records.pop("a:1", None)
    a.lanes[0]["insert"] = True
    rig.adapter.head_set = lambda h: (_ for _ in ()).throw(RuntimeError("no sensor"))
    assert rig.module.refiller._head_empty_now(rig.lane) is None
    sent = []
    rig.gcode.run_script_from_command = sent.append
    assert rig.module.refiller.spool_known(rig.module.swapper.swap_pending) is False
    assert not [x for x in sent if x.startswith("ACEK_RFID_READ")]
    assert rig.gcode.lines[-1] == (
        "ace2k_u1: head 1: its sensor cannot be read: the new spool's tag is not read — set the"
        " spool (Edit spool, or ACEK_SPOOL), then RESUME; still paused"
    )
    assert rig.adapter.pause_reason == rig.gcode.lines[-1]


@pytest.mark.parametrize("state", ["standby", "complete"])
def test_a_runout_with_the_print_neither_printing_nor_paused_only_says_so(
    tmp_path: Path, state: str
) -> None:
    rig, a = refill_rig(tmp_path)
    tail(rig)
    h = rig.lane
    rig.print_stats.state = state
    del rig.sent[:]
    rig.module.refiller.shared_runout(h)
    assert rig.gcode.lines[-1] == (
        f"ace2k_u1: head 1: a:1 ran out at its bay: the print is {state} — not paused"
    )
    assert rig.sent == [] and rig.module.swapper.swap_pending is None
    assert h.hold is False and h.end_taken is False  # given back at the end


def test_a_runout_whose_refill_cannot_be_built_is_a_line_and_gives_the_hold_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, a = refill_rig(tmp_path)
    tail(rig)
    h = rig.lane
    rig.module.swapper.pause = lambda *args, **kwargs: None

    def broken(lane: Hooked) -> NoReturn:
        raise RuntimeError("no state")

    rig.module.refiller._refill_state = broken
    with caplog.at_level("ERROR"):
        rig.module.refiller.shared_runout(h)
    assert rig.gcode.lines[-1] == "ace2k_u1: head 1: a:1 ran out at its bay — no state"
    assert any(
        r.getMessage() == "ace2k_u1: head 1: a:1 ran out at its bay" and r.exc_info
        for r in caplog.records
    )
    assert rig.module.swapper.swap_pending is None and h.hold is False and h.end_taken is False


def test_resume_with_a_stopped_runout_whose_sensor_cannot_be_read_stays_paused(
    tmp_path: Path,
) -> None:
    rig, a = refill_rig(tmp_path)
    rig.module.refiller.runout_stopped = {0: {"lane": rig.lane, "emptied": False}}
    rig.adapter.head_set = lambda h: (_ for _ in ()).throw(RuntimeError("no sensor"))
    assert rig.module.refiller.stopped_runouts_clear() is False
    line = "ace2k_u1: head 1: its sensor cannot be read — no sensor; still paused"
    assert rig.gcode.lines[-1] == line and rig.adapter.pause_reason == line
    assert (
        0 in rig.module.refiller.runout_stopped
    )  # kept: the head is judged again at the next RESUME
