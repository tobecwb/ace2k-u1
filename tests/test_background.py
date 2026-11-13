"""The background swap of a parked head (ace2k_u1_background).

The rig is the swap rig of test_tools: head 1 (index 0) is fed by a:1+a:2 and holds a:1 (T0),
T1 is a:3 on head 2, T2 is a:2 on head 1. Head 1 is listed in background_heads, and head 2 is the
active one, so head 1 is parked. The parked moves go to a fake mover, the heater to a fake
heaters object that reaches each target at once, and the tip is a fixed table.
"""

from __future__ import annotations

import contextlib
import types
from typing import TYPE_CHECKING

import ace2k_u1_background
import fakes
import pytest
from ace2k_u1_background import BgRun
from ace2k_u1_parked import OwnStepsError, ParkedMoveError, TipStep
from ace2k_u1_swap import OwedTool, SwapState
from test_tools import order_is, resume, swap_rig, tool, tools_rig
from test_units import Gcmd

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import SimpleNamespace

    from fakes import FakeAce2k

TIP = [TipStep("move", -10.0, 1800.0), TipStep("dwell", 500.0, 0.0), TipStep("move", -40.0, 3300.0)]


class FakeMover:
    """ParkedMover as the background swap uses it: each call recorded; a move lasts its length
    over its speed, a dwell its seconds, back to back from the reactor's now."""

    def __init__(self, reactor: fakes.FakeReactor) -> None:
        self.reactor = reactor
        self.calls: list[tuple[object, ...]] = []
        self.end = 0.0
        self.refuse: str | None = None
        self.own_left = 0  # moves refused first while the extruder's own steps are not made

    def _at(self) -> float:
        return max(self.end, self.reactor.now)

    def move(
        self,
        ext: fakes.FakeExtruder,
        distance: float,
        speed: float,
        accel: float = 300.0,
        note: object = None,
    ) -> tuple[float, float]:
        if self.refuse:
            raise ParkedMoveError(self.refuse)
        if self.own_left:
            self.own_left -= 1
            raise OwnStepsError(f"{ext.name} has steps of its own still to make")
        start = self._at()
        self.end = start + abs(distance) / speed
        self.calls.append(("move", ext.name, distance, speed, accel))
        if callable(note):
            note(ext.name, start, self.end - start, distance)
        return start, self.end

    def own_steps_left(self, ext: fakes.FakeExtruder) -> bool:
        if self.own_left:
            self.own_left -= 1
            return True
        return False

    def dwell(self, ext: fakes.FakeExtruder, seconds: float) -> float:
        self.end = self._at() + seconds
        self.calls.append(("dwell", ext.name, seconds))
        return self.end

    def remaining_s(self, ext: fakes.FakeExtruder) -> float:
        return max(0.0, self.end - self.reactor.now)

    def queued(self) -> bool:
        return self.end > self.reactor.now


class FakeHeaters:
    """The U1's heaters: set_temperature sets the target, and the heater reaches it at once
    unless stuck is set; pending_extruders as the U1 keeps it."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, float, bool]] = []
        self.pending_extruders: list[tuple[str, float]] = []
        self.stuck = False

    def set_temperature(self, heater: fakes.FakeHeater, temp: float, wait: bool = False) -> None:
        self.calls.append((id(heater), temp, wait))
        heater.target = temp
        if not self.stuck:
            heater.temp = temp


def bg_rig(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:
    """The swap rig with head 1 listed, parked (head 2 active), the print at T0 of [0, 1, 2]."""
    rig, a = swap_rig(tmp_path)
    order_is(rig, [0, 1, 2], 0)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    a.lanes[1]["insert"] = True  # a:2's bay holds filament
    bg = rig.module.background
    bg.heads = frozenset({0})
    rig.module.swapper.before_swap = bg.before_inline
    rig.module.swapper.yield_ended = bg.yield_ended
    rig.adapter.on_u1_load = bg.u1_loads
    rig.mover = bg.mover = FakeMover(rig.reactor)
    bg.tip = lambda printer, head, soft: list(TIP)
    rig.heaters = FakeHeaters()
    rig.printer.objects["heaters"] = rig.heaters
    rig.notes = []
    a.feed.ff = types.SimpleNamespace(note_move=lambda *args: rig.notes.append(args) or True)
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    rig.u1.active = rig.printer.objects["extruder_list"][1]
    rig.ext = rig.printer.objects["extruder_list"][0]
    for ext in rig.printer.objects["extruder_list"]:  # every head above min_extrude_temp
        ext.heater.can_extrude = True
        rig.printer.objects.setdefault(ext.name, ext)
    rig.ext.heater.temp = rig.ext.heater.target = 170.0  # the docked head's standby
    rig.bg = bg
    return rig, a


def no_gcode(rig: SimpleNamespace) -> None:
    """Runs the background callbacks with every G-code path raising: the run never runs one."""
    gcode = rig.gcode
    saved = gcode.run_script_from_command, getattr(gcode, "run_script", None)

    tried = []

    def refuse(script: str) -> None:
        tried.append(script)
        raise AssertionError(f"G-code from the background: {script}")

    gcode.run_script_from_command = gcode.run_script = refuse
    try:
        rig.reactor.run_callbacks()
    finally:
        gcode.run_script_from_command, gcode.run_script = saved
    assert tried == []  # a step that caught the error still fails the test here


def started(rig: SimpleNamespace) -> BgRun:
    """T1 of the file docks head 1; its background swap to a:2 (for T2) starts."""
    tool(rig, 1)
    return rig.bg.runs[0]


def finished(rig: SimpleNamespace) -> BgRun:
    run = started(rig)
    rig.sent.clear()
    no_gcode(rig)
    return run


# --- the start rule -------------------------------------------------------------------------


def test_a_t_that_docks_a_listed_head_starts_its_background_swap(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    state = run.state
    assert (state.slot.head, state.old.lane, state.new.lane, state.tool) == (0, 0, 1, 2)
    assert run.in_print and run.busy and run.status == "running"
    assert 0 in rig.adapter.swapping
    assert "ace2k_u1: head 1: a:1 → a:2 (T2): background swap in its dock" in rig.gcode.lines
    assert len(rig.reactor.callbacks) == 1


@pytest.mark.parametrize(
    "change",
    [
        "not_printing",
        "no_plan",
        "pending",
        "active",
        "empty_bay",
        "same_lane",
        "no_note_move",
        "unlisted",
        "unknown_lane",
    ],
)
def test_no_background_swap_starts(tmp_path: Path, change: str) -> None:
    rig, a = bg_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    if change == "not_printing":
        rig.print_stats.state = "paused"
    elif change == "no_plan":
        rig.module.tracker.plan_started = False
    elif change == "pending":
        rig.module.swapper.swap_pending = ace2k_u1_background.SwapState(
            slot, None, slot.lanes[1], 2
        )
    elif change == "active":
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")
    elif change == "empty_bay":
        a.lanes[1]["insert"] = False
    elif change == "same_lane":
        order_is(rig, [0, 1, 0], 0)
    elif change == "no_note_move":
        del a.feed.ff
    elif change == "unlisted":
        rig.bg.heads = frozenset()
    elif change == "unknown_lane":
        rig.adapter.set_current(slot, None)
    tool(rig, 1)
    assert 0 not in rig.bg.runs
    assert rig.reactor.callbacks == []


def test_no_note_move_is_said_once_per_print(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)
    del a.feed.ff
    tool(rig, 1)
    tool(rig, 0)
    tool(rig, 1)
    said = [ln for ln in rig.gcode.lines if "cannot record a parked move" in ln]
    assert len(said) == 1
    rig.module.print_over()
    assert rig.bg.no_ff_said is False


def two_heads(rig: SimpleNamespace) -> int:
    """Head 4 listed too, holding its first lane (on unit b); returns a tool on its other lane."""
    rig.bg.heads = frozenset({0, 3})
    plan = rig.module.tracker.plan
    on_four = [n for n, h in plan.choice.items() if h.head == 3]
    head4 = rig.adapter.slot_of_head(3)
    rig.adapter.set_current(head4, head4.lanes[0])
    other = next(n for n in on_four if plan.choice[n] is not head4.lanes[0])
    unit_b = plan.choice[other].unit
    unit_b.feed.ff = types.SimpleNamespace(note_move=lambda *args: True)
    unit_b.lanes[plan.choice[other].lane]["insert"] = True
    return other


def test_every_docked_head_starts_the_one_whose_t_comes_first_first(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    other = two_heads(rig)
    order_is(rig, [0, 1, other, 2], 0)
    tool(rig, 1)
    assert list(rig.bg.runs) == [3, 0]  # head 4's T comes before head 1's: it starts first
    order_is(rig, [0, 1, 2, other], 0)
    rig.bg.runs.clear()
    rig.adapter.swapping.clear()
    rig.reactor.callbacks.clear()
    tool(rig, 1)
    assert list(rig.bg.runs) == [0, 3]


def test_a_head_with_a_kept_state_does_not_start_again(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.busy = False
    run.status = "stopped"
    rig.reactor.callbacks.clear()
    tool(rig, 1)
    assert rig.bg.runs[0] is run and rig.reactor.callbacks == []


# --- the run in the dock --------------------------------------------------------------------


def test_the_dock_runs_its_steps_with_no_gcode(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)
    run = finished(rig)
    state = run.state
    assert run.status == "finished" and not run.busy
    assert state.step == "spool" and state.gripped
    # the grip's purge covers the load: the head arrives ready to print
    assert state.done_in_dock == frozenset(
        {"unload", "free", "roll back", "hand over", "push", "load"}
    )
    assert state.print_temp == 220.0  # the temperature the grip purged at
    assert rig.sent == []  # no G-code at all
    # heat to the tip temperature (PLA 220), the load temperature after the tip, the grip's
    # printing temperature, then the heater handed back to its standby
    assert [t for _, t, _ in rig.heaters.calls] == [220.0, 220.0, 220.0, 170.0]
    assert all(not wait for _, _, wait in rig.heaters.calls)
    # the tip queued whole at the extruder's accel, then the grip and the purge as one move
    assert rig.mover.calls == [
        ("move", "extruder", -10.0, 30.0, 300.0),
        ("dwell", "extruder", 0.5),
        ("move", "extruder", -40.0, 55.0, 300.0),
        ("move", "extruder", 50.0, 6.0, 300.0),
    ]
    assert [n[0] for n in rig.notes] == ["extruder"] * 3
    assert [n[3] for n in rig.notes] == [-10.0, -40.0, 50.0]
    slot = state.slot
    assert slot.current is slot.lanes[1]
    assert slot.loaded is None  # the loaded check of the T sets it, with the lane's own check
    modes = [(lane, mode) for lane, mode, *_ in a.feed.started]
    assert modes[0] == (0, "assist_both")  # the old lane follows the tip
    assert (1, "feed") in modes  # the push of a:2
    assert 0 in rig.adapter.swapping  # until its T takes it
    assert "ace2k_u1: head 1: background swap done: it is ready at its T" in rig.gcode.lines


def test_the_tip_gets_the_extruders_max_e_accel(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.ext.max_e_accel = 1500.0
    finished(rig)
    assert {c[4] for c in rig.mover.calls if c[0] == "move"} == {1500.0}


def test_the_tip_and_the_grip_are_noted_on_their_lanes(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    lanes = []
    rig.bg._note = lambda h: lambda *args: lanes.append((h.lane, args[3]))
    finished(rig)
    assert lanes == [(0, -10.0), (0, -40.0), (1, 50.0)]


def test_the_grip_and_the_purge_are_one_move_with_no_stop(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.module.swap_cfg.background_purge_mm = 5.0
    run = finished(rig)
    assert rig.mover.calls[-1][2:4] == (45.0, 6.0)
    assert [n[3] for n in rig.notes][-1] == 45.0  # noted on the new lane
    assert run.purged_mm == 5.0
    # the status tells what the run did, not what the config says now
    rig.module.swap_cfg.background_purge_mm = 20.0
    lines = command(rig, "ACEK_BG_STATUS", {})
    assert lines == [
        "ace2k_u1: head 1: background swap finished, a:2 gripped and purged 5 mm: "
        "it is ready at its T"
    ]


def test_a_purge_of_0_queues_the_grip_only_and_leaves_the_load(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.module.swap_cfg.background_purge_mm = 0.0
    run = finished(rig)
    assert run.status == "finished" and run.state.gripped
    assert [c[2] for c in rig.mover.calls if c[0] == "move"][-1] == 40.0
    assert [n[3] for n in rig.notes][-1] == 40.0
    assert "load" not in run.state.done_in_dock and run.state.print_temp is None
    assert "ace2k_u1: head 1: background swap done: it loads at its T" in rig.gcode.lines
    lines = command(rig, "ACEK_BG_STATUS", {})
    assert lines == ["ace2k_u1: head 1: background swap finished, a:2 gripped: it purges at its T"]


class FakeTangleCheck:
    """The U1's tangle check of a head (filament_entangle_detect eN_filament): each
    skip_entangle_check recorded with the moves queued so far and whether they had all ended."""

    def __init__(self, rig: SimpleNamespace) -> None:
        self.rig = rig
        self.skip_check_flag = False
        self.calls: list[tuple[bool, int, bool]] = []

    def skip_entangle_check(self, skip: bool = False) -> None:
        mover = self.rig.mover
        self.calls.append((skip, len(mover.calls), not mover.queued()))
        self.skip_check_flag = bool(skip)


def tangle_rig(tmp_path: Path) -> tuple[SimpleNamespace, FakeTangleCheck]:
    """The rig with head 1's tangle check, each parked move recorded with the check's flag."""
    rig, _ = bg_rig(tmp_path)
    check = FakeTangleCheck(rig)
    rig.printer.objects["filament_entangle_detect e0_filament"] = check
    rig.flags = []
    move = rig.mover.move

    def flagged(*args: object, **kwargs: object) -> tuple[float, float]:
        rig.flags.append(check.skip_check_flag)
        return move(*args, **kwargs)

    rig.mover.move = flagged
    return rig, check


def test_the_tangle_check_is_off_while_the_tip_and_the_grip_are_queued_and_run(
    tmp_path: Path,
) -> None:
    rig, check = tangle_rig(tmp_path)
    run = finished(rig)
    assert run.status == "finished"
    assert rig.flags == [True, True, True]  # the two moves of the tip, then the grip
    # off before the tip's moves, on once they have ended; the same around the grip and purge
    assert check.calls == [(True, 0, True), (False, 3, True), (True, 3, True), (False, 4, True)]
    assert check.skip_check_flag is False


@pytest.mark.parametrize("at", ["tip", "grip"])
def test_a_parked_move_that_raises_turns_the_tangle_check_on_again(tmp_path: Path, at: str) -> None:
    rig, check = tangle_rig(tmp_path)
    move = rig.mover.move

    def failing(ext: object, distance: float, *args: object, **kwargs: object) -> object:
        if (distance > 0) == (at == "grip"):
            raise ParkedMoveError("extruder is below min_extrude_temp")
        return move(ext, distance, *args, **kwargs)

    rig.mover.move = failing
    run = finished(rig)
    assert run.status == "stopped"
    assert check.skip_check_flag is False
    assert [c[0] for c in check.calls][-2:] == [True, False]


def test_a_tip_that_fails_part_way_turns_the_check_on_once_its_queued_moves_end(
    tmp_path: Path,
) -> None:
    rig, check = tangle_rig(tmp_path)
    move = rig.mover.move

    def second_fails(ext: object, distance: float, *args: object, **kwargs: object) -> object:
        if distance == -40.0:  # the tip's second move, after its first move and its pause
            raise ParkedMoveError("extruder is below min_extrude_temp")
        return move(ext, distance, *args, **kwargs)

    rig.mover.move = second_fails
    run = finished(rig)
    assert run.status == "stopped" and run.state.step == "unload"
    # on again only once the first move and the pause, already queued, have ended
    assert check.calls == [(True, 0, True), (False, 2, True)]


def test_a_wait_that_fails_after_a_failed_move_keeps_the_moves_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, check = tangle_rig(tmp_path)
    move = rig.mover.move

    def second_fails(ext: object, distance: float, *args: object, **kwargs: object) -> object:
        if distance == -40.0:
            rig.mover.remaining_s = broken  # the wait that follows fails too
            raise ParkedMoveError("extruder is below min_extrude_temp")
        return move(ext, distance, *args, **kwargs)

    def broken(ext: object) -> float:
        raise RuntimeError("no MCU clock")

    rig.mover.move = second_fails
    with caplog.at_level("ERROR"):
        run = finished(rig)
    assert run.status == "stopped" and run.failed
    assert any("below min_extrude_temp" in ln for ln in rig.gcode.lines if "stopped at" in ln)
    assert any("waiting for its parked moves" in r.getMessage() for r in caplog.records)
    assert check.skip_check_flag is False


def test_the_wait_for_parked_moves_is_bounded(tmp_path: Path) -> None:
    rig, check = tangle_rig(tmp_path)
    rig.mover.remaining_s = lambda ext: 2.0  # an MCU clock that never advances
    t0 = rig.reactor.now
    run = finished(rig)
    assert run.status == "finished"
    assert rig.reactor.now < t0 + 60.0  # two waits of at most 2 s + WAIT_MARGIN_S each
    assert check.skip_check_flag is False


def test_a_tangle_check_that_cannot_be_turned_off_stops_it_before_any_move(
    tmp_path: Path,
) -> None:
    rig, check = tangle_rig(tmp_path)

    def refuse(skip: bool = False) -> None:
        raise RuntimeError("no wheel")

    check.skip_entangle_check = refuse  # type: ignore[method-assign]
    armed: list[object] = []
    rig.adapter.follow.arm = lambda *args: armed.append(args)
    run = finished(rig)
    assert run.status == "stopped" and run.state.step == "unload"
    assert rig.mover.calls == []
    assert not run.tipped and armed == []  # no tip ran: no follow, no lane made unknown
    assert (
        "ace2k_u1: head 1: the background swap stopped at unload: the tangle check of head 1 "
        "could not be turned off: no wheel"
    ) in rig.gcode.lines


def test_the_wait_for_the_extruders_own_steps_leaves_the_tangle_check_on(
    tmp_path: Path,
) -> None:
    rig, check = tangle_rig(tmp_path)
    run = started(rig)
    rig.mover.own_left = 2
    seen: list[bool] = []  # the check's flag at each pause of the unload before its first move

    def note(now: float) -> None:
        if run.step == "unload" and not rig.mover.calls:
            seen.append(check.skip_check_flag)

    rig.reactor.on_pause.append(note)
    t0 = rig.reactor.now
    no_gcode(rig)
    assert run.status == "finished" and rig.reactor.now >= t0 + 1.0
    assert len(seen) == 2 and not any(seen)  # the two pauses of the wait: the check is on
    assert check.calls[0] == (True, 0, True)


def test_a_tip_the_dock_cannot_run_ends_it_before_any_move(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.bg.tip = lambda printer, head, soft: "the tip has a line the dock cannot run: M104 S0"
    run = finished(rig)
    assert run.status == "stopped" and run.state.step == "unload"
    assert rig.mover.calls == []
    assert "M104 S0: its unload runs at its T" in rig.gcode.lines[-1]


def test_a_tip_over_the_limits_is_refused_whole(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.bg.tip = lambda printer, head, soft: [
        TipStep("move", -5.0, 600.0),
        TipStep("dwell", 2e4, 0),
    ]
    run = finished(rig)
    assert run.state.step == "unload" and rig.mover.calls == []


def test_an_empty_head_skips_the_tip(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.head_sensor.detected = False
    run = finished(rig)
    assert run.status == "finished"
    assert [c[0] for c in rig.mover.calls] == ["move"]  # the grip and the prime only, one move
    assert not run.state.unloaded


def test_a_heater_that_does_not_reach_its_target_ends_it_at_the_heat(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.heaters.stuck = True
    rig.heaters.pending_extruders = [("extruder", 220.0)]
    run = finished(rig)
    assert run.status == "stopped" and run.step == "heat" and run.state.step == "unload"
    assert any("did not reach 220 °C within 240 s" in ln for ln in rig.gcode.lines)
    assert rig.mover.calls == []
    assert rig.heaters.calls[-1][1] == 170.0  # handed back


def test_a_held_tip_ends_it_and_the_unload_runs_again_at_the_t(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)

    def on_start(move: fakes.FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True  # the gear still holds a:1
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    run = finished(rig)
    assert run.status == "stopped" and run.step == "free"
    assert run.state.step == "unload" and not run.state.unloaded


def test_a_failed_step_never_pauses_and_goes_on_from_it(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.bg.host.swapper.steps["push"] = lambda state: (_ for _ in ()).throw(
        RuntimeError("a:2 did not reach the head")
    )
    run = finished(rig)
    assert run.status == "stopped" and run.state.step == "push"
    assert rig.module.swapper.swap_pending is None
    assert rig.pause_resume.is_paused is False
    assert "stopped at push: a:2 did not reach the head" in rig.gcode.lines[-1]


def test_a_failed_roll_back_with_the_head_empty_goes_on_from_the_hand_over(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)

    def roll(state: object) -> None:
        rig.head_sensor.detected = False
        raise RuntimeError("a:1 roll back timed out")

    rig.module.swapper.steps["roll back"] = roll
    run = finished(rig)
    assert run.state.step == "hand over"


@pytest.mark.parametrize(
    ("at", "step", "done", "gripped"),
    [
        ("heat", "unload", set(), False),
        ("free", "free", {"unload"}, False),
        ("hand over", "hand over", {"unload", "free", "roll back"}, False),
        ("push", "push", {"unload", "free", "roll back", "hand over"}, False),
        ("grip", "spool", {"unload", "free", "roll back", "hand over", "push"}, False),
        # the load is done in the dock only once the loaded check sees the filament
        ("loaded", "spool", {"unload", "free", "roll back", "hand over", "push"}, True),
    ],
)
def test_a_stop_before_each_step_hands_over_its_state(
    tmp_path: Path, at: str, step: str, done: set[str], gripped: bool
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    steps = ace2k_u1_background.DOCK_STEPS

    def asking(fn: Callable[[BgRun], bool]) -> Callable[[BgRun], bool]:
        """The step, then a stop request when the next step is `at`."""

        def wrapped(r: BgRun) -> bool:
            result = fn(r)
            nxt = steps.index(r.step) + 1
            if nxt < len(steps) and steps[nxt] == at:
                r.stop_asked = True
            return result

        return wrapped

    if at == "heat":
        run.stop_asked = True
    for name in ("_heat", "_unload", "_free", "_unit_step", "_grip", "_loaded"):
        setattr(rig.bg, name, asking(getattr(rig.bg, name)))
    no_gcode(rig)
    assert run.status == "stopped"
    assert run.state.step == step
    assert set(run.state.done_in_dock) == done
    assert run.state.gripped is gripped


def test_a_stop_cuts_a_heat_wait_at_once(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.heaters.stuck = True
    t0 = rig.reactor.now
    rig.reactor.on_pause.append(lambda now: setattr(run, "stop_asked", now >= t0 + 1.0))
    no_gcode(rig)
    assert run.status == "stopped" and run.step == "heat"
    assert rig.reactor.now < t0 + 2.0


def test_a_stop_never_cuts_the_tip(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.reactor.on_pause.append(
        lambda now: setattr(run, "stop_asked", run.stop_asked or run.step == "unload")
    )
    no_gcode(rig)
    assert len(rig.mover.calls) == 3  # the whole tip, then no grip
    assert rig.mover.remaining_s(rig.ext) == 0.0
    assert run.state.step == "free" and run.state.unloaded


# --- the heater ----------------------------------------------------------------------------


def temp_call(rig: SimpleNamespace, name: str, params: dict[str, str]) -> list[dict[str, str]]:
    calls = []
    rig.gcode.commands_orig[name] = lambda gcmd: calls.append(gcmd.params)
    rig.gcode.commands[name](Gcmd(dict(params), name))
    return calls


def test_a_temperature_of_the_file_for_an_owned_head_is_recorded_and_handed_back(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.owns = True
    # T2's pre-heat (its lane is a:2, on head 1): recorded, not applied
    assert temp_call(rig, "M104", {"S": "200", "T": "2"}) == []
    assert temp_call(rig, "M109", {"S": "210", "T": "2"}) == []
    assert run.called == 210.0
    assert temp_call(rig, "M104", {"S": "150", "T": "0", "A": "0"}) != []  # the U1's own
    assert temp_call(rig, "M104", {"S": "200", "T": "1"}) != []  # another head
    run.owns = False
    run.called = None
    no_gcode(rig)
    assert rig.heaters.calls[-1][1] == 170.0


def test_the_newest_call_is_handed_back(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)

    def call_mid_run(now: float) -> None:
        if run.owns and run.called is None:
            temp_call(rig, "M104", {"S": "205", "T": "2"})

    rig.reactor.on_pause.append(call_mid_run)
    rig.heaters.stuck = True
    rig.ext.heater.temp = 219.0  # within 4 °C of every target
    no_gcode(rig)
    assert rig.heaters.calls[-1][1] == 205.0


def test_a_print_that_ended_gets_the_heater_off(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.owns, run.start_target = True, 170.0
    rig.print_stats.state = "cancelled"
    rig.bg._hand_back(run)
    assert rig.heaters.calls[-1][1] == 0.0


def test_a_stop_for_the_arrival_leaves_the_heater(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.owns, run.leave_heater = True, True
    rig.bg._hand_back(run)
    assert rig.heaters.calls == []


# --- the arrival -----------------------------------------------------------------------------


LOAD_GRIPPED = "ACEK_SWAP_LOAD HEAD=0 LOAD_TEMP=220 PRINT_TEMP=220 MATERIAL=PLA GRIP=0"


def test_the_t_of_a_finished_one_runs_the_spool_the_loaded_check_and_the_return(
    tmp_path: Path,
) -> None:
    """The dock purged: the arrival heats and waits, then runs no load, only the U1's
    pre-extrusion (its cut and its clean)."""
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    plan = rig.module.tracker.plan
    plan.kept[2] = 225.0  # a call kept for T2 after the grip: the newest, used up as by a load
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    assert tool(rig, 2) == [{}]
    assert rig.sent[0] == "SAVE_GCODE_STATE NAME=ACEK_SWAP"
    assert rig.sent[1] == "M104 S225 T0 A0"  # the target only: the head is not picked yet
    assert rig.sent[2].startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 ")
    assert rig.sent[3:] == [
        "T0 A0",
        "MOVE_TO_DISCARD_FILAMENT_POSITION",  # picked, it heats and waits at the cleaning area
        "M109 S225 T0 A0",
        "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0",
        "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0",
    ]
    assert not [ln for ln in rig.sent if ln.startswith(("ACEK_SWAP_", "M400"))]
    assert not [ln for ln in rig.sent if "ACEK_BG_WAIT" in ln]  # nothing to wait for
    assert 2 not in plan.kept
    slot = rig.adapter.slot_of_head(0)
    assert slot.loaded is slot.lanes[1]  # the loaded check ran at the T
    # as after an inline load: the file's next pre-extrusion and its M109 at 225 are skipped
    tracker = rig.module.tracker
    assert (tracker.preextrude_after, tracker.wait_skip_tool, tracker.wait_skip_temp) == (
        0,
        2,
        225.0,
    )
    assert rig.bg.runs == {} and 0 not in rig.adapter.swapping
    assert rig.module.swapper.swap_pending is None


def test_the_arrival_after_a_purge_waits_for_the_heat_before_any_retraction(
    tmp_path: Path,
) -> None:
    """The head may be at the standby the dock handed back: it is picked first, then heats and
    waits at the cleaning area, and only then retracts. No head waits over the wipe tower."""
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    rig.ext.heater.temp = rig.ext.heater.target = 170.0  # the standby handed back
    rig.print_stats.state = "printing"
    rig.module.tracker.retract_owed[2] = 10.0  # what the slicer retracted when T2 last left
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    rig.sent.clear()
    assert tool(rig, 2) == [{}]
    sequence = [ln for ln in rig.sent if not ln.startswith("SET_PRINT_FILAMENT_CONFIG")]
    assert sequence == [
        "SAVE_GCODE_STATE NAME=ACEK_SWAP",
        "M104 S220 T0 A0",
        "T0 A0",
        "MOVE_TO_DISCARD_FILAMENT_POSITION",
        "M109 S220 T0 A0",
        "SAVE_GCODE_STATE NAME=ACEK_SWAP_RETRACT",
        "M83",
        "G1 E-9.5 F1800",
        "RESTORE_GCODE_STATE NAME=ACEK_SWAP_RETRACT",
        "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0",
        "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0",
    ]
    assert rig.module.tracker.wait_skip_temp == 220.0  # the M109 that waited
    pick = sequence.index("T0 A0")
    assert not [ln for ln in sequence[:pick] if ln.startswith("M109")]  # nothing waits unpicked
    wait = sequence.index("M109 S220 T0 A0")
    assert sequence[wait - 1] == "MOVE_TO_DISCARD_FILAMENT_POSITION"
    assert wait < sequence.index("G1 E-9.5 F1800")


def test_a_stop_between_the_grip_and_the_loaded_check_loads_at_the_t(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    real = rig.bg._grip

    def grip(r: BgRun) -> bool:
        ok = real(r)
        r.stop_asked = True  # the T comes before the loaded check
        return ok

    rig.bg._grip = grip
    no_gcode(rig)
    assert run.status == "stopped" and run.state.gripped
    assert "load" not in run.state.done_in_dock and run.state.print_temp is None
    rig.sent.clear()
    tool(rig, 2)
    assert [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD")] == [LOAD_GRIPPED]
    assert not [ln for ln in rig.sent if ln.startswith("M109")]  # the load macro waits itself
    assert rig.module.swapper.swap_pending is None


def test_a_grip_with_no_printing_temperature_known_says_the_load_temperature(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.module.tracker.plan.tools[2]["temp"] = None  # no printing temperature known
    rig.module.tracker.plan.tools[2]["first_temp"] = None
    run = finished(rig)
    assert run.state.print_temp == 230.0  # the rig's load temperature for PLA
    assert (
        "ace2k_u1: head 1: no printing temperature known for T2: the load temperature, "
        "230 °C, is kept"
    ) in rig.gcode.lines


def test_the_t_of_one_finished_with_no_purge_runs_the_load_without_grip(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.module.swap_cfg.background_purge_mm = 0.0
    finished(rig)
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    assert tool(rig, 2) == [{}]
    assert rig.sent[2:4] == ["T0 A0", LOAD_GRIPPED]
    assert "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0" in rig.sent
    assert not [ln for ln in rig.sent if ln.startswith(("ACEK_SWAP_UNLOAD", "M400"))]
    assert rig.module.swapper.swap_pending is None


def test_the_t_of_a_stopped_one_goes_on_from_its_step_and_grips(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.bg._grip = lambda r: False  # stopped in the grip's heat
    no_gcode(rig)
    assert run.state.step == "spool"
    rig.sent.clear()
    tool(rig, 2)
    load = next(ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD"))
    assert "GRIP" not in load


def test_the_t_of_one_stopped_at_the_heat_runs_the_whole_swap(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.stop_asked = True
    no_gcode(rig)
    rig.sent.clear()
    tool(rig, 2)
    assert any(ln.startswith("ACEK_SWAP_UNLOAD") for ln in rig.sent)
    assert rig.module.swapper.swap_pending is None


def test_the_resume_skips_the_dock_steps_once(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    ran = []
    swapper = rig.module.swapper
    for name in list(swapper.steps):
        swapper.steps[name] = lambda state, name=name: ran.append(name)
    state = run.state
    state.step = "free"  # as if the dock had stopped after the tip
    assert swapper.resume_background(Gcmd({}, "T2"), state)
    assert ran == ["spool", "loaded", "return"]  # the dock's purge covered the load
    assert state.done_in_dock == frozenset() and state.gripped is False


def test_the_arrival_waits_at_the_cleaning_area_when_it_waits_long(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)  # its callback not run yet: busy
    run.step_end = rig.reactor.now + 5.0
    t0 = rig.reactor.now
    rig.reactor.on_pause.append(lambda now: setattr(run, "busy", now < t0 + 3.0))
    rig.sent.clear()
    tool(rig, 2)
    assert rig.sent[:2] == [
        "SAVE_GCODE_STATE NAME=ACEK_BG_WAIT",
        "MOVE_TO_DISCARD_FILAMENT_POSITION",
    ]
    assert rig.sent[2:4] == [
        "INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2",
        "RESTORE_GCODE_STATE NAME=ACEK_BG_WAIT MOVE=0",
    ]
    assert rig.reactor.now >= t0 + 3.0
    assert run.stop_asked and run.leave_heater


def test_a_short_wait_stays_where_it_is(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    t0 = rig.reactor.now
    rig.reactor.on_pause.append(lambda now: setattr(run, "busy", now < t0 + 0.3))
    rig.sent.clear()
    tool(rig, 2)
    assert not [ln for ln in rig.sent if "ACEK_BG_WAIT" in ln]


def test_the_wait_covers_the_last_parked_move(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    rig.mover.end = rig.reactor.now + 4.0  # the grip still runs on the MCU
    t0 = rig.reactor.now
    rig.sent.clear()
    tool(rig, 2)
    assert rig.sent[0] == "SAVE_GCODE_STATE NAME=ACEK_BG_WAIT"
    assert rig.reactor.now >= t0 + 4.0
    del run


def test_a_plan_changed_before_the_hand_over_takes_the_ts_lane(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.stop_asked = True
    no_gcode(rig)
    slot = run.state.slot
    plan = rig.module.tracker.plan
    plan.choice[5] = slot.lanes[1]
    plan.choice[2] = slot.lanes[0]  # T2 now wants a:1 again: the lane in the head
    rig.sent.clear()
    assert tool(rig, 2) == [{}]
    assert "background swap is dropped" in rig.gcode.lines[-1]
    assert not [ln for ln in rig.sent if ln.startswith("ACEK_SWAP")]


def test_a_plan_changed_after_the_hand_over_finishes_then_swaps(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)
    run = finished(rig)
    slot = run.state.slot
    plan = rig.module.tracker.plan
    plan.choice[2] = slot.lanes[0]  # T2 now wants a:1
    a.lanes[0]["insert"] = True
    rig.sent.clear()
    tool(rig, 2)
    loads = [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD")]
    assert len(loads) == 1 and "GRIP" not in loads[0]  # a:2 was ready: only a:1 loads
    assert slot.current is slot.lanes[0]


# --- the inline swap, the pause, the end ----------------------------------------------------


def test_an_inline_swap_of_another_head_does_not_stop_it(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    other = rig.adapter.slot_of_head(3)
    ran = []
    rig.module.swapper._run_swap = lambda gcmd, state: ran.append(state.slot.head) or True
    assert rig.module.swapper.swap(Gcmd({}, "T5"), other, other.lanes[1], 5)
    assert not run.stop_asked and run.busy and rig.bg.runs[0] is run
    assert ran == [3]
    no_gcode(rig)
    assert run.status == "finished"


def test_an_inline_swap_of_its_own_head_goes_on_with_its_state(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    slot = run.state.slot
    resumed = []
    rig.module.swapper.resume_background = lambda gcmd, state: resumed.append(state) or True
    assert rig.module.swapper.swap(Gcmd({}, "T2"), slot, slot.lanes[1], 2)
    assert resumed == [run.state] and rig.bg.runs == {}


def test_a_pause_stops_it_and_keeps_its_state(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.print_stats.state = "paused"
    rig.module.watch_print(rig.reactor.now)
    assert run.stop_asked
    no_gcode(rig)
    assert rig.bg.runs[0] is run and run.status == "stopped"


def test_the_end_of_the_print_drops_it(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    rig.print_stats.state = "cancelled"
    rig.module.watch_print(rig.reactor.now)
    assert rig.bg.runs == {} and 0 not in rig.adapter.swapping
    assert any("background swap dropped at loaded" in ln for ln in rig.gcode.lines)


def test_the_end_of_the_print_while_it_runs_drops_it_at_its_end(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.module.print_over()
    assert run.stop_asked and run.drop_asked and rig.bg.runs[0] is run
    no_gcode(rig)
    assert rig.bg.runs == {}


# --- the status and the commands -------------------------------------------------------------


def command(rig: SimpleNamespace, name: str, params: dict[str, str]) -> list[str]:
    """Runs a command; returns what it said, on the console and as its reply."""
    before = len(rig.gcode.lines)
    gcmd = Gcmd(params, name)
    rig.gcode.commands[name](gcmd)
    return rig.gcode.lines[before:] + gcmd.lines


def test_the_status_per_listed_head(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    assert rig.module.get_status()["background"][0] == {"state": "idle", "step": None, "lane": None}
    run = started(rig)
    assert rig.module.get_status()["background"] == [
        {"state": "running", "step": "heat", "lane": "a:2"},
        None,
        None,
        None,
    ]
    no_gcode(rig)
    assert rig.module.get_status()["background"][0]["state"] == "finished"
    lines = command(rig, "ACEK_BG_STATUS", {})
    assert lines == [
        "ace2k_u1: head 1: background swap finished, a:2 gripped and purged 10 mm: "
        "it is ready at its T"
    ]
    del run


def test_no_head_listed_changes_nothing(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    assert rig.module.background.heads == frozenset()
    assert rig.module.swapper.before_swap is None
    assert "background" not in rig.module.get_status()
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    a.lanes[1]["insert"] = True
    order_is(rig, [0, 1, 2], 0)
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    tool(rig, 1)
    assert rig.reactor.callbacks == [] and rig.module.background.runs == {}
    assert command(rig, "ACEK_BG_STATUS", {}) == [
        "ace2k_u1: no head swaps in the background (background_heads is empty)"
    ]


def test_the_config_key_lists_the_head(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, values={"background_heads": "1"})
    assert rig.module.background.heads == frozenset({0})
    assert rig.module.swapper.before_swap == rig.module.background.before_inline


def bench(rig: SimpleNamespace) -> None:
    rig.print_stats.state = "standby"


@pytest.mark.parametrize(
    ("params", "why"),
    [
        ({"HEAD": "2", "LANE": "a:3"}, "head 2 is the active head"),
        ({"HEAD": "4", "LANE": "b:2"}, "head 4 is not in background_heads"),
        ({"HEAD": "1", "LANE": "a:3"}, "'a:3' is not a lane of head 1"),
        ({"HEAD": "1", "LANE": "a:1"}, "a:1 is already in head 1"),
    ],
)
def test_the_bench_swap_refusals(tmp_path: Path, params: dict[str, str], why: str) -> None:
    rig, _ = bg_rig(tmp_path)
    bench(rig)
    with pytest.raises(Exception, match=why):
        command(rig, "ACEK_BG_SWAP", params)


def test_the_bench_swap_is_refused_in_a_print(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    with pytest.raises(Exception, match="ACEK_BG_SWAP refused: a print is printing or paused"):
        command(rig, "ACEK_BG_SWAP", {"HEAD": "1", "LANE": "a:2"})


def test_the_bench_swap_runs_the_dock_and_its_t_takes_it(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    bench(rig)
    command(rig, "ACEK_BG_SWAP", {"HEAD": "1", "LANE": "a:2"})
    run = rig.bg.runs[0]
    assert not run.in_print and run.state.tool is None
    no_gcode(rig)
    assert run.status == "finished"
    rig.module.print_over()  # not a print's: it stays
    assert rig.bg.runs[0] is run
    with pytest.raises(Exception, match="a background swap is kept or running"):
        command(rig, "ACEK_BG_SWAP", {"HEAD": "1", "LANE": "a:2"})
    command(rig, "ACEK_BG_STOP", {"HEAD": "1"})
    assert rig.bg.runs == {}


def test_the_bench_stop_stops_a_running_one(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    lines = command(rig, "ACEK_BG_STOP", {"HEAD": "1"})
    assert run.stop_asked and lines[-1].endswith("stops at its next step")
    with pytest.raises(Exception, match="head 2 has no background swap"):
        command(rig, "ACEK_BG_STOP", {"HEAD": "2"})


def test_the_bench_move(tmp_path: Path) -> None:
    rig, check = tangle_rig(tmp_path)
    bench(rig)
    t0 = rig.reactor.now
    lines = command(rig, "ACEK_BG_MOVE", {"HEAD": "1", "DISTANCE": "5", "VELOCITY": "2"})
    assert rig.mover.calls == [("move", "extruder", 5.0, 2.0, 300.0)]
    assert rig.flags == [True]  # through the tangle check's hold, as every parked move
    assert check.calls[-1] == (False, 1, True) and rig.reactor.now >= t0 + 2.5
    assert lines[-1] == "ace2k_u1: head 1: parked move of 5 mm at 2 mm/s, 2.5 s"
    rig.mover.refuse = "extruder is below min_extrude_temp"
    with pytest.raises(Exception, match="ACEK_BG_MOVE refused: extruder is below"):
        command(rig, "ACEK_BG_MOVE", {"HEAD": "1", "DISTANCE": "5"})
    with pytest.raises(Exception, match="head 2 is the active head"):
        command(rig, "ACEK_BG_MOVE", {"HEAD": "2", "DISTANCE": "5"})
    rig.print_stats.state = "printing"
    with pytest.raises(Exception, match="ACEK_BG_MOVE refused: a print is printing or paused"):
        command(rig, "ACEK_BG_MOVE", {"HEAD": "1", "DISTANCE": "5"})


# --- the review's cases: every path to the head, the heater, the waits ------------------------


def test_the_first_move_waits_for_the_extruders_own_steps(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.mover.own_left = 2
    t0 = rig.reactor.now
    no_gcode(rig)
    assert run.status == "finished" and len(rig.mover.calls) == 4
    assert rig.reactor.now >= t0 + 1.0


def test_own_steps_that_never_end_stop_it_at_the_unload(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.mover.own_left = 10**6
    armed: list[object] = []
    rig.adapter.follow.arm = lambda *args: armed.append(args)
    no_gcode(rig)
    assert run.status == "stopped" and run.state.step == "unload"
    assert rig.mover.calls == []
    assert not run.tipped and armed == []


def test_a_u1_t_of_the_head_waits_for_its_parked_moves_and_keeps_the_state(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    rig.mover.end = rig.reactor.now + 3.0  # the grip still runs on the MCU
    t0 = rig.reactor.now
    assert tool(rig, 0, {"A": "0"}) == [{"A": "0"}]
    assert rig.reactor.now >= t0 + 3.0
    assert rig.bg.runs[0] is run and 0 in rig.adapter.swapping
    assert not [ln for ln in rig.sent if "ACEK_BG_WAIT" in ln]  # inside the U1's own macro


def test_a_u1_t_of_the_head_stops_a_running_one_and_keeps_its_state(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    t0 = rig.reactor.now
    rig.reactor.on_pause.append(lambda now: setattr(run, "busy", now < t0 + 0.5))
    assert tool(rig, 0, {"A": "0"}) == [{"A": "0"}]
    assert run.stop_asked and not run.busy and rig.bg.runs[0] is run


def test_a_u1_t_of_another_head_does_not_wait(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    t0 = rig.reactor.now
    assert tool(rig, 1, {"A": "0"}) == [{"A": "0"}]
    assert rig.reactor.now == t0 and not run.stop_asked


def owed_t2(rig: SimpleNamespace) -> list[dict[str, str]]:
    """The print paused with T2 owed; RESUME runs it. Returns the U1's T2 calls."""
    rig.print_stats.state = "paused"
    params = {"command": "T2", "commandline": "T2", "params": {}}
    rig.module.swapper.tools_owed = [OwedTool(2, params)]  # type: ignore[arg-type]
    calls: list[dict[str, str]] = []
    rig.gcode.commands_orig["T2"] = lambda gcmd: calls.append(gcmd.params)
    resume(rig)
    return calls


def test_an_owed_t_at_resume_takes_a_kept_state_after_its_parked_moves(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    rig.mover.end = rig.reactor.now + 3.0
    t0 = rig.reactor.now
    rig.sent.clear()
    assert owed_t2(rig) == [{}]
    assert rig.reactor.now >= t0 + 3.0
    assert "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0" in rig.sent
    assert not [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD")]  # purged in the dock
    assert not [ln for ln in rig.sent if "ACEK_BG_WAIT" in ln]  # paused: off the part already
    assert rig.bg.runs == {} and rig.module.swapper.tools_owed == []


def test_an_owed_t_whose_take_over_fails_stays_owed_once(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    rig.module.swapper.steps["loaded"] = lambda state: (_ for _ in ()).throw(RuntimeError("jam"))
    assert owed_t2(rig) == []
    pending = rig.module.swapper.swap_pending
    assert pending is not None and pending.tool == 2 and pending.owed
    assert [e.tool for e in rig.module.swapper.tools_owed] == [2]


def test_a_t_of_a_head_whose_parked_move_still_runs_waits_for_it(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    bench(rig)
    rig.mover.end = rig.reactor.now + 2.5  # a parked move still runs on the MCU
    t0 = rig.reactor.now
    rig.sent.clear()
    assert tool(rig, 0) == [{}]
    assert rig.reactor.now >= t0 + 2.5
    # outside a print, homed: the wait goes to the cleaning area, never over the bed
    assert rig.sent[:2] == [
        "SAVE_GCODE_STATE NAME=ACEK_BG_WAIT",
        "MOVE_TO_DISCARD_FILAMENT_POSITION",
    ]


@pytest.mark.parametrize(
    ("head", "why"),
    [("4", "head 4 is not in background_heads"), ("1", "a background swap is kept or running")],
)
def test_the_bench_move_refusals(tmp_path: Path, head: str, why: str) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    bench(rig)
    with pytest.raises(Exception, match=f"ACEK_BG_MOVE refused: {why}"):
        command(rig, "ACEK_BG_MOVE", {"HEAD": head, "DISTANCE": "5"})
    assert [c for c in rig.mover.calls if c[2] == 5.0] == []


def test_a_lane_in_error_in_the_dock_pauses_the_print_at_its_t(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)

    def push() -> None:
        rig.head_sensor.detected = True
        a.lanes[1].update(mode="error", error="timeout")  # a knot: its follow timed out

    a.feed.on_push = push
    run = finished(rig)
    assert run.state.lane_error is None and run.state.gripped
    assert tool(rig, 2) == []  # the U1's T2 does not run
    pending = rig.module.swapper.swap_pending
    assert pending is not None and pending.step == "loaded"
    assert "a:2 is in error (timeout) after its load" in rig.gcode.lines[-1]


def test_an_empty_sensor_after_the_grip_grips_again_at_the_t(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    real = rig.bg._grip

    def grip(r: BgRun) -> bool:
        ok = real(r)
        rig.head_sensor.detected = False
        return ok

    rig.bg._grip = grip
    no_gcode(rig)
    assert run.status == "finished" and not run.state.gripped
    assert "load" not in run.state.done_in_dock
    assert "the head's sensor sees no filament after the grip" in rig.gcode.lines[-2]
    rig.head_sensor.detected = True  # the arrival's load finds the filament
    rig.sent.clear()
    tool(rig, 2)
    loads = [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD")]
    assert len(loads) == 1 and "GRIP" not in loads[0]  # the full load, with its grip


def test_a_wait_that_grows_past_a_second_moves_to_the_cleaning_area(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)  # its estimate is 0 s: no unit move under way, no parked move
    t0 = rig.reactor.now
    moved_at = []

    def tick(now: float) -> None:
        run.busy = now < t0 + 2.5
        if "MOVE_TO_DISCARD_FILAMENT_POSITION" in rig.sent and not moved_at:
            moved_at.append(now)

    rig.reactor.on_pause.append(tick)
    rig.sent.clear()
    tool(rig, 2)
    assert rig.sent[:2] == [
        "SAVE_GCODE_STATE NAME=ACEK_BG_WAIT",
        "MOVE_TO_DISCARD_FILAMENT_POSITION",
    ]
    assert "RESTORE_GCODE_STATE NAME=ACEK_BG_WAIT MOVE=0" in rig.sent
    assert moved_at and t0 + 1.0 <= moved_at[0] <= t0 + 1.3
    # the head that waited at the cleaning area is cleaned there before it leaves
    clean = rig.sent.index("INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2")
    assert clean < rig.sent.index("RESTORE_GCODE_STATE NAME=ACEK_BG_WAIT MOVE=0")


@pytest.mark.parametrize(("homed", "moves"), [("xyz", True), ("", False), ("z", False)])
def test_a_wait_outside_a_print_moves_the_head_only_when_homed(
    tmp_path: Path, homed: str, moves: bool
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.print_stats.state = "standby"  # the bench: ACEK_BG_SWAP, then a T typed by hand
    rig.printer.objects["toolhead"].homed_axes = homed
    t0 = rig.reactor.now

    def tick(now: float) -> None:
        run.busy = now < t0 + 2.5

    rig.reactor.on_pause.append(tick)
    rig.sent.clear()
    rig.module.background.stop_and_wait(Gcmd({}, "T2"), run.state.slot.head, run, False)
    assert ("MOVE_TO_DISCARD_FILAMENT_POSITION" in rig.sent) is moves
    # cleaned only after a wait at the cleaning area
    assert ("INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2" in rig.sent) is moves
    assert any(line.startswith("SAVE_GCODE_STATE") for line in rig.sent) is moves


def test_a_cold_head_that_waited_at_the_cleaning_area_is_not_cleaned(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.printer.objects["extruder_list"][1].heater.can_extrude = False  # the active head, cold
    t0 = rig.reactor.now

    def tick(now: float) -> None:
        run.busy = now < t0 + 2.5

    rig.reactor.on_pause.append(tick)
    rig.sent.clear()
    rig.module.background.stop_and_wait(Gcmd({}, "T2"), run.state.slot.head, run, False)
    assert "MOVE_TO_DISCARD_FILAMENT_POSITION" in rig.sent
    assert "INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2" not in rig.sent


def test_a_clean_that_fails_does_not_fail_the_t(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    t0 = rig.reactor.now

    def tick(now: float) -> None:
        run.busy = now < t0 + 2.5

    rig.reactor.on_pause.append(tick)
    real = rig.module.background.host.u1.script

    def script(line: str, *args: object) -> None:
        if line.startswith("INNER_ROUGHLY_CLEAN"):
            raise RuntimeError("Move out of range")
        real(line, *args)

    rig.module.background.host.u1.script = script
    rig.sent.clear()
    rig.module.background.stop_and_wait(Gcmd({}, "T2"), run.state.slot.head, run, False)
    assert "RESTORE_GCODE_STATE NAME=ACEK_BG_WAIT MOVE=0" in rig.sent
    assert any("the clean after the wait failed" in line for line in rig.gcode.lines)


def test_a_wait_in_a_paused_print_never_moves_the_head(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.print_stats.state = "paused"
    t0 = rig.reactor.now

    def tick(now: float) -> None:
        run.busy = now < t0 + 2.5

    rig.reactor.on_pause.append(tick)
    rig.sent.clear()
    rig.module.background.stop_and_wait(Gcmd({}, "T2"), run.state.slot.head, run, False)
    assert "MOVE_TO_DISCARD_FILAMENT_POSITION" not in rig.sent
    assert not any(line.startswith("SAVE_GCODE_STATE") for line in rig.sent)


def test_an_inline_swap_of_its_own_head_takes_the_state_for_its_own_tool(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    slot = run.state.slot
    rig.module.tracker.plan.choice[6] = slot.lanes[1]
    resumed: list[SwapState] = []
    rig.module.swapper.resume_background = lambda gcmd, state: resumed.append(state) or True
    assert rig.module.swapper.swap(Gcmd({}, "T6"), slot, slot.lanes[1], 6)
    assert resumed == [run.state]
    assert run.state.tool == 6 and run.state.tool_params["command"] == "T6"


@pytest.mark.parametrize("why", ["drop", "pending"])
def test_the_heater_is_given_back_when_the_t_takes_no_state(tmp_path: Path, why: str) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.owns, run.start_target = True, 170.0

    def ends(now: float) -> None:
        if run.busy:
            run.status = "stopped"
            rig.bg._end(run)  # the stop cut the heat wait: the hand-back is left to the T

    rig.reactor.on_pause.append(ends)
    slot = run.state.slot
    if why == "drop":
        rig.module.tracker.plan.choice[2] = slot.lanes[0]  # T2 keeps a:1
    else:
        other = rig.adapter.slot_of_head(3)
        rig.module.swapper.swap_pending = SwapState(other, other.lanes[0], other.lanes[1], 5)
    tool(rig, 2)
    assert rig.heaters.calls[-1][1] == 170.0
    assert not run.leave_heater and not run.heater_left
    assert (0 in rig.bg.runs) is (why == "pending")


def test_a_t_that_takes_the_state_keeps_the_heater_to_itself(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.owns, run.start_target = True, 170.0
    rig.reactor.on_pause.append(lambda now: run.busy and rig.bg._end(run))
    tool(rig, 2)
    assert 170.0 not in [t for _, t, _ in rig.heaters.calls]


def test_a_pending_swap_of_another_head_at_resume_does_not_stop_it(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.print_stats.state = "paused"
    other = rig.adapter.slot_of_head(3)
    pending = SwapState(other, other.lanes[0], other.lanes[0], None, refill=True)
    rig.module.refiller.refill_ready = lambda state: False
    assert rig.module.swapper.go_on(Gcmd({}, "RESUME"), pending) is False
    assert not run.stop_asked and run.busy and rig.bg.runs[0] is run


def test_a_refill_of_another_head_does_not_stop_it(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.print_stats.state = "paused"
    seen = []
    swapper = rig.module.swapper
    for name in ("unload", "free", "roll back"):
        swapper.steps[name] = lambda state, name=name: seen.append((name, run.busy))
    other = rig.adapter.slot_of_head(3)
    state = SwapState(other, other.lanes[0], other.lanes[0], None, refill=True)
    assert rig.module.refiller._refill_clear(state)
    assert seen == [("unload", True), ("free", True), ("roll back", True)]
    assert not run.stop_asked and rig.bg.runs[0] is run


def test_a_refill_of_its_own_head_stops_it_and_keeps_it_for_its_t(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    t0 = rig.reactor.now
    rig.reactor.on_pause.append(lambda now: setattr(run, "busy", now < t0 + 0.5))
    rig.print_stats.state = "paused"
    seen = []
    swapper = rig.module.swapper
    for name in ("unload", "free", "roll back"):
        swapper.steps[name] = lambda state, name=name: seen.append((name, run.busy))
    slot = run.state.slot
    state = SwapState(slot, slot.lanes[0], slot.lanes[0], None, refill=True)
    assert rig.module.refiller._refill_clear(state)
    assert seen == [("unload", False), ("free", False), ("roll back", False)]
    assert run.stop_asked and not run.resume_ok and rig.bg.runs[0] is run


def test_no_head_listed_reads_nothing_in_a_t(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    order_is(rig, [0, 1, 2], 0)
    bg = rig.module.background

    def no_lookup(n: int) -> int:
        raise AssertionError("a lookup with no head listed")

    bg._tool_head = no_lookup
    assert bg.before_tool(Gcmd({}, "T0"), 0) is None
    assert bg.before_tool(Gcmd({"A": "0"}, "T0"), 0) is None
    assert tool(rig, 0) == [{}]


# --- the second review's cases ----------------------------------------------------------------


def test_a_drop_after_the_dock_moved_leaves_the_heads_lane_unknown(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    slot = run.state.slot
    command(rig, "ACEK_BG_STOP", {"HEAD": "1"})
    assert rig.bg.runs == {} and slot.current is None and slot.loaded is None
    assert "ace2k_u1: head 1's lane is now unknown: its next swap probes it" in rig.gcode.lines
    # every tool of the head swaps first, so the swap probes the head
    assert rig.module.tracker.swap_for(0) == (slot, slot.lanes[0])
    assert rig.module.tracker.swap_for(2) == (slot, slot.lanes[1])


def test_the_end_of_the_print_after_the_roll_back_leaves_the_lane_unknown(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.bg.host.swapper.steps["hand over"] = lambda state: (_ for _ in ()).throw(
        RuntimeError("the unit refused")
    )
    no_gcode(rig)
    assert run.state.slot.current is run.state.slot.lanes[0]  # the old lane, rolled back
    rig.print_stats.state = "cancelled"
    rig.module.watch_print(rig.reactor.now)
    assert run.state.slot.current is None
    assert any("lane is now unknown" in ln for ln in rig.gcode.lines)


def test_a_drop_after_a_tip_cut_by_a_refusal_leaves_the_lane_unknown(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)
    run = started(rig)

    def note(*args: object) -> bool:
        rig.mover.refuse = "extruder is below min_extrude_temp"  # the next move is refused
        return True

    a.feed.ff = types.SimpleNamespace(note_move=note)
    no_gcode(rig)
    assert run.status == "stopped" and run.done == set()
    command(rig, "ACEK_BG_STOP", {"HEAD": "1"})
    assert run.state.slot.current is None


def test_a_drop_before_any_move_keeps_the_heads_lane(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.stop_asked = True  # stopped at the heat
    no_gcode(rig)
    command(rig, "ACEK_BG_STOP", {"HEAD": "1"})
    assert run.state.slot.current is run.state.slot.lanes[0]
    assert not any("lane is now unknown" in ln for ln in rig.gcode.lines)


def test_an_owed_pre_heat_skips_a_head_the_background_swap_owns(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.owns = True
    rig.sent.clear()
    rig.module.wraps._heat_docked([(0, 215.0, "a pre-heat"), (3, 200.0, "another")])
    assert rig.sent == ["M104 S200 T3 A0"]
    assert run.called == 215.0


@pytest.mark.parametrize("purge", [0.0, 30.0])
def test_a_take_over_keeps_the_recorded_printing_temperature_for_the_load(
    tmp_path: Path, purge: float
) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.module.swap_cfg.background_purge_mm = purge
    run = finished(rig)
    run.called = 230.0  # T2's call, recorded after the hand over (its lane was in the head)
    rig.sent.clear()
    tool(rig, 2)
    loads = [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD")]
    if purge:  # the dock did the load: the arrival heats to the newest call and waits
        assert loads == [] and "M109 S230 T0 A0" in rig.sent
    else:
        assert len(loads) == 1 and "PRINT_TEMP=230 " in loads[0]
    assert rig.module.tracker.wait_skip_temp == 230.0
    assert 2 not in rig.module.tracker.plan.kept


def test_a_take_over_does_not_keep_a_standby_value(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.module.swap_cfg.background_purge_mm = 0.0  # the load runs at the arrival
    run = finished(rig)
    run.called = 150.0  # a standby, under the plan's 220 by more than 40 °C
    rig.sent.clear()
    tool(rig, 2)
    load = next(ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD"))
    assert "PRINT_TEMP=220 " in load


def test_the_status_of_a_finished_one_that_did_not_grip(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    run.state.gripped = False
    lines = command(rig, "ACEK_BG_STATUS", {})
    assert lines == [
        "ace2k_u1: head 1: background swap finished, to a:2, not gripped: it grips at its T"
    ]


# --- the head that waits keeps its heat (bench B7) -------------------------------------------


def cooled_wait(rig: SimpleNamespace) -> BgRun:
    """T1 prints on head 2 at 220 °C; the file's cool-down M104 S70 T1 comes before T2, and the
    U1 applies it. T2's head has a background swap that lasts 2.5 s more."""
    run = started(rig)
    rig.print_stats.state = "printing"
    rig.module.tracker.plan.tools[1]["temp"] = 220.0
    active = rig.printer.objects["extruder_list"][1].heater
    active.temp = active.target = 220.0
    assert temp_call(rig, "M104", {"S": "70", "T": "1"}) == [{"S": "70", "T": "1"}]
    active.target = 70.0  # as the U1 applies it
    t0 = rig.reactor.now
    rig.reactor.on_pause.append(lambda now: setattr(run, "busy", now < t0 + 2.5))
    rig.sent.clear()
    return run


def test_a_head_that_waits_at_the_cleaning_area_is_kept_at_its_printing_temperature(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = cooled_wait(rig)
    rig.module.background.stop_and_wait(Gcmd({}, "T2"), run.state.slot.head, run, False)
    assert rig.sent == [
        "SAVE_GCODE_STATE NAME=ACEK_BG_WAIT",
        "MOVE_TO_DISCARD_FILAMENT_POSITION",
        "M104 S220 T1 A0",  # back to what it printed at, no wait
        "M109 S220 T1 A0",  # hot before the clean
        "INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2",
        "M104 S70 T1 A0",  # the file's cool-down again: it docks at the slicer's standby
        "RESTORE_GCODE_STATE NAME=ACEK_BG_WAIT MOVE=0",
    ]


def test_a_failed_heat_wait_still_gives_back_the_cool_down(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = cooled_wait(rig)
    real = rig.module.background.host.u1.script

    def script(line: str, *args: object) -> None:
        if line.startswith("M109"):
            raise RuntimeError("Heater extruder1 not heating at expected rate")
        real(line, *args)

    rig.module.background.host.u1.script = script
    with pytest.raises(RuntimeError):
        rig.module.background.stop_and_wait(Gcmd({}, "T2"), run.state.slot.head, run, False)
    assert rig.sent[-2:] == ["M104 S70 T1 A0", "RESTORE_GCODE_STATE NAME=ACEK_BG_WAIT MOVE=0"]
    assert "INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2" not in rig.sent


def test_a_wait_with_no_cool_down_keeps_the_target(tmp_path: Path) -> None:
    """No cool-down of the file was seen (outside a print, or the slicer sent none)."""
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    t0 = rig.reactor.now
    rig.reactor.on_pause.append(lambda now: setattr(run, "busy", now < t0 + 2.5))
    rig.sent.clear()
    rig.module.background.stop_and_wait(Gcmd({}, "T2"), run.state.slot.head, run, False)
    assert "MOVE_TO_DISCARD_FILAMENT_POSITION" in rig.sent
    assert not [ln for ln in rig.sent if ln.startswith(("M104", "M109"))]


def test_a_cool_down_the_head_no_longer_has_is_not_undone(tmp_path: Path) -> None:
    """The head's target changed since the cool-down: it is left as it is."""
    rig, _ = bg_rig(tmp_path)
    run = cooled_wait(rig)
    rig.printer.objects["extruder_list"][1].heater.target = 0.0
    rig.module.background.stop_and_wait(Gcmd({}, "T2"), run.state.slot.head, run, False)
    assert not [ln for ln in rig.sent if ln.startswith(("M104", "M109"))]


def test_a_print_start_forgets_the_cool_downs(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    cooled_wait(rig)
    assert rig.module.tracker.cooled_from
    rig.module.tracker.reset_print()
    assert rig.module.tracker.cooled_from == {}


def test_each_dock_step_logs_one_line(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    rig, _ = bg_rig(tmp_path)
    caplog.set_level("INFO")
    lines_before = len(rig.gcode.lines)
    finished(rig)
    steps = [r.getMessage() for r in caplog.records if "background swap step" in r.getMessage()]
    assert steps == [
        f"ace2k_u1: head 1: background swap step {step} (to a:2)"
        for step in ace2k_u1_background.DOCK_STEPS
    ]
    assert not [ln for ln in rig.gcode.lines[lines_before:] if "background swap step" in ln]


# --- as early as possible, several at once (operator, bench B7) ------------------------------


def test_a_head_docked_inside_another_heads_arrival_starts_its_run_then(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)  # head 1 ready for T2
    other = two_heads(rig)
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder3")  # head 4 prints
    rig.u1.active = rig.printer.objects["extruder_list"][3]
    order_is(rig, [0, 1, 2, other], 1)
    run_script = rig.gcode.run_script_from_command
    started_at = []

    def script(text: str) -> None:
        run_script(text)
        if text == "T0 A0":  # the U1's pick of head 1 docks head 4: its T wrapper runs
            rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")
            rig.gcode.commands_orig["T0"] = lambda gcmd: None
            rig.gcode.commands["T0"](Gcmd({"A": "0"}, "T0"))
            started_at.append(3 in rig.bg.runs)

    rig.gcode.run_script_from_command = script
    rig.module.swapper.host.u1.script = lambda line, timer=False: script(line)
    tool(rig, 2)
    assert started_at and started_at[0]  # in the arrival, at the pick
    assert 0 not in rig.bg.runs and rig.bg.runs[3].busy
    assert rig.bg.runs[3].state.new is rig.module.tracker.plan.choice[other]


def test_two_runs_at_once_both_finish_and_each_arrival_takes_its_own(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    other = two_heads(rig)
    order_is(rig, [0, 1, 2, other], 0)
    for name in ("free", "roll back", "hand over", "push"):  # the unit moves: as in a fake unit
        rig.module.swapper.steps[name] = lambda state: None
    rig.module.probe.free_check = lambda h: 0.0
    rig.adapter.head_set = lambda h: True
    tool(rig, 1)
    runs = dict(rig.bg.runs)
    no_gcode(rig)
    assert [runs[h].status for h in (0, 3)] == ["finished", "finished"]
    taken = []
    rig.module.swapper.resume_background = lambda gcmd, state: taken.append(state) or True
    tool(rig, 2)
    assert taken == [runs[0].state] and list(rig.bg.runs) == [3]
    tool(rig, other)
    assert taken == [runs[0].state, runs[3].state] and rig.bg.runs == {}


def test_a_pause_stops_every_run(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    other = two_heads(rig)
    order_is(rig, [0, 1, 2, other], 0)
    tool(rig, 1)
    rig.print_stats.state = "paused"
    rig.module.watch_print(rig.reactor.now)
    assert all(r.stop_asked and r.resume_ok for r in rig.bg.runs.values())
    no_gcode(rig)
    assert [r.status for r in rig.bg.runs.values()] == ["stopped", "stopped"]


def paused_at(rig: SimpleNamespace, step: str) -> BgRun:
    """Head 1's run, stopped by a pause before step; the print prints again."""
    run = started(rig)

    def pause_before(now: float) -> None:
        if run.busy and step in run.done | {run.step} and not run.stop_asked:
            rig.bg.on_pause()

    rig.reactor.on_pause.append(pause_before)
    no_gcode(rig)
    rig.reactor.on_pause.remove(pause_before)
    assert run.status == "stopped" and run.resume_ok
    return run


def test_a_run_a_pause_stopped_goes_on_in_its_dock_from_its_step(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = paused_at(rig, "unload")  # the tip's wait on the MCU clock: stopped after it
    done = set(run.done)
    assert done == {"unload"}
    nxt = next(s for s in ace2k_u1_background.DOCK_STEPS if s not in done | {"heat"})
    caplog.set_level("INFO")
    rig.module.watch_print(rig.reactor.now)  # printing again: the watch tick
    assert run.busy and run.status == "running" and not run.stop_asked
    assert f"ace2k_u1: head 1: background swap goes on in its dock from {nxt} (to a:2)" in (
        rig.gcode.lines
    )
    no_gcode(rig)
    assert run.status == "finished" and run.owns is False
    steps = [
        r.getMessage().split(" step ")[1] for r in caplog.records if " step " in r.getMessage()
    ]
    assert steps[0] == f"{nxt} (to a:2)" and "unload (to a:2)" not in steps
    assert rig.u1.active is rig.printer.objects["extruder_list"][1]  # head 1 stayed parked
    # its T then arrives as after any finished run
    rig.sent.clear()
    assert tool(rig, 2) == [{}]
    assert "SM_PRINT_PREEXTRUDE_FILAMENT INDEX=2 FORCE=1 LENGTH=0" in rig.sent
    assert rig.bg.runs == {}


@pytest.mark.parametrize(
    "why", ["active", "paused", "lane_changed", "stopped_by_hand", "empty_bay", "no_note_move"]
)
def test_a_stopped_run_does_not_go_on(tmp_path: Path, why: str) -> None:
    rig, a = bg_rig(tmp_path)
    run = paused_at(rig, "unload")
    if why == "empty_bay":  # checked as for a new run
        a.lanes[1]["insert"] = False
    elif why == "no_note_move":
        del a.feed.ff
    elif why == "active":
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")
    elif why == "paused":
        rig.print_stats.state = "paused"
    elif why == "lane_changed":
        order_is(rig, [0, 1, 0], 1)  # head 1's next T keeps a:1 now
    else:
        run.resume_ok = False  # ACEK_BG_STOP or a T kept it
    rig.reactor.callbacks.clear()
    rig.module.watch_print(rig.reactor.now)
    assert not run.busy and run.status == "stopped" and rig.reactor.callbacks == []


def test_the_watch_tick_starts_a_run_without_a_t(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    rig.module.tracker.t_raw = 1  # T1 ran on head 2 before the background heads were known
    rig.module.watch_print(rig.reactor.now)
    assert 0 in rig.bg.runs and rig.bg.runs[0].busy and len(rig.reactor.callbacks) == 1
    rig.module.watch_print(rig.reactor.now)  # once
    assert len(rig.reactor.callbacks) == 1


def test_a_run_stopped_by_hand_during_a_pause_waits_for_its_t(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.bg.on_pause()
    command(rig, "ACEK_BG_STOP", {"HEAD": "1"})
    no_gcode(rig)
    rig.module.watch_print(rig.reactor.now)
    assert run.status == "stopped" and not run.busy and not run.resume_ok


def test_a_pause_that_comes_as_the_run_finishes_does_not_make_it_go_on(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    real = rig.bg._loaded

    def loaded(r: BgRun) -> bool:
        rig.bg.on_pause()  # the pause lands in the last step: no step is left to stop before
        return real(r)

    rig.bg._loaded = loaded
    no_gcode(rig)
    assert run.status == "finished" and not run.resume_ok
    rig.reactor.callbacks.clear()
    rig.module.watch_print(rig.reactor.now)
    assert rig.reactor.callbacks == [] and not run.busy  # no second loaded check, no purge


def test_a_return_after_a_resume_waits_for_a_head_below_its_printing_temperature(
    tmp_path: Path,
) -> None:
    """A swap that failed after the dock's load: RESUME runs the return with done_in_dock
    cleared, and the pause left the head cooling. It heats at the cleaning area first."""
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    state = rig.bg._take(0)
    del run
    state.done_in_dock = frozenset()
    state.print_temp = 220.0
    rig.ext.heater.temp, rig.ext.heater.target = 150.0, 170.0
    rig.module.tracker.retract_owed[2] = 10.0
    rig.sent.clear()
    rig.module.swapper._step_return(state)
    assert rig.sent[:3] == ["T0 A0", "MOVE_TO_DISCARD_FILAMENT_POSITION", "M109 S220 T0 A0"]
    assert rig.sent.index("M109 S220 T0 A0") < rig.sent.index("G1 E-9.5 F1800")


def test_a_return_with_the_head_at_its_printing_temperature_does_not_wait(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    state = rig.bg._take(0)
    state.done_in_dock = frozenset()
    state.print_temp = 220.0
    rig.ext.heater.temp = rig.ext.heater.target = 220.0
    rig.sent.clear()
    rig.module.swapper._step_return(state)
    assert not [ln for ln in rig.sent if ln.startswith(("M109", "MOVE_TO_DISCARD"))]


# --- the U1's heating slots (its heaters.py: two heads climb at once) -----------------------


class U1Heaters:
    """The U1's heaters as heaters.py does it (u1/bg-swap-study): a head that climbs already, or
    is within 2 °C of its new target, is set at once; with fewer than two climbing it joins
    active_heating_extruders; else it is queued in pending_extruders and its heater untouched.
    Each reactor pause the heaters ramp at rate °C/s and the U1's timer drops the heads within
    2 °C and starts the queued ones. Records the most background heads seen climbing at once."""

    def __init__(self, rig: SimpleNamespace, background: set[str], rate: float = 5.0) -> None:
        self.ext = {e.name: e for e in rig.printer.objects["extruder_list"]}
        self.by_heater = {id(e.heater): name for name, e in self.ext.items()}
        self.active_heating_extruders: list[str] = []
        self.pending_extruders: list[tuple[str, float]] = []
        self.queued: list[str] = []  # every head the U1 ever queued
        self.dropped: dict[str, float] = {}  # name -> when the timer dropped it
        self.set_at: list[tuple[str, float, float]] = []
        self.background, self.rate = background, rate
        self.most_background = 0
        self.reactor = rig.reactor
        self.last = rig.reactor.now
        rig.reactor.on_pause.append(self.tick)

    def set_temperature(self, heater: fakes.FakeHeater, temp: float, wait: bool = False) -> None:
        name = self.by_heater[id(heater)]
        self.set_at.append((name, temp, self.reactor.now))
        if temp <= 0.0:
            self.active_heating_extruders[:] = [
                n for n in self.active_heating_extruders if n != name
            ]
            heater.target = temp
        elif name in self.active_heating_extruders or abs(heater.temp - temp) <= 2.0:
            heater.target = temp
        elif len(self.active_heating_extruders) < 2:
            self.active_heating_extruders.append(name)
            heater.target = temp
        else:
            self.pending_extruders.append((name, temp))
            self.queued.append(name)
        self._count()

    def tick(self, now: float) -> None:
        dt, self.last = now - self.last, now
        for e in self.ext.values():
            h = e.heater
            step = min(abs(h.target - h.temp), self.rate * dt)
            h.temp += step if h.target > h.temp else -step
        for name in list(self.active_heating_extruders):
            h = self.ext[name].heater
            if abs(h.temp - h.target) <= 2.0:
                self.active_heating_extruders.remove(name)
                self.dropped.setdefault(name, now)
        while self.pending_extruders and len(self.active_heating_extruders) < 2:
            name, temp = self.pending_extruders.pop(0)
            self.ext[name].heater.target = temp
            self.active_heating_extruders.append(name)
        self._count()

    def _count(self) -> None:
        on = sum(n in self.background for n in self.active_heating_extruders)
        self.most_background = max(self.most_background, on)


def u1_heaters(rig: SimpleNamespace, rate: float = 5.0) -> U1Heaters:
    heaters = U1Heaters(rig, {"extruder", "extruder3"}, rate)
    rig.printer.objects["heaters"] = heaters
    return heaters


def test_background_runs_leave_a_heating_slot_for_the_printing_head(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    other = two_heads(rig)
    order_is(rig, [0, 1, 2, other], 0)
    for name in ("free", "roll back", "hand over", "push"):
        rig.module.swapper.steps[name] = lambda state: None
    rig.module.probe.free_check = lambda h: 0.0
    rig.adapter.head_set = lambda h: True
    heaters = u1_heaters(rig)
    head4 = heaters.ext["extruder3"].heater
    head4.temp = 100.0
    heaters.set_temperature(head4, 220.0)  # head 4's run climbs already
    printing = heaters.ext["extruder1"].heater
    printing.temp = printing.target = 150.0  # the file cooled it before a T
    t0 = rig.reactor.now
    asked = []

    def print_head_m109(now: float) -> None:
        if now >= t0 + 1.0 and not asked:
            asked.append(now)
            heaters.set_temperature(printing, 230.0, wait=True)  # the printing head's M109

    rig.reactor.on_pause.append(print_head_m109)
    tool(rig, 1)
    runs = dict(rig.bg.runs)
    no_gcode(rig)
    assert asked and "extruder1" not in heaters.queued  # the printing head never queued
    assert heaters.queued == [] and heaters.most_background <= 1
    assert [runs[h].status for h in (0, 3)] == ["finished", "finished"]
    # head 1 raised its target only once the U1 dropped the others (within 2 °C)
    first = min(t for name, _, t in heaters.set_at if name == "extruder")
    assert first >= max(heaters.dropped["extruder3"], heaters.dropped["extruder1"])


def test_a_head_within_2_c_of_its_target_is_set_at_once(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    heaters = u1_heaters(rig)
    heaters.active_heating_extruders[:] = ["extruder1", "extruder3"]  # both slots taken
    target = rig.module.swapper._unload_temp(run.state.old)
    rig.ext.heater.temp = rig.ext.heater.target = target - 1.0
    t0 = rig.reactor.now
    assert rig.bg._heat_to(run, target)
    assert heaters.set_at[0] == ("extruder", target, t0)  # no wait for a slot


def test_the_slot_and_the_climb_share_one_timeout(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    heaters = u1_heaters(rig, rate=0.0)  # nothing ever reaches its target
    heaters.active_heating_extruders[:] = ["extruder1", "extruder3"]
    heaters.ext["extruder1"].heater.target = heaters.ext["extruder3"].heater.target = 250.0
    t0 = rig.reactor.now
    assert not rig.bg._heat_to(run, 260.0)
    took = rig.reactor.now - t0
    assert ace2k_u1_background.HEAT_TIMEOUT_S <= took < ace2k_u1_background.HEAT_TIMEOUT_S + 1
    assert run.status == "stopped" and not [n for n, _, _ in heaters.set_at if n == "extruder"]
    assert any("did not reach 260 °C" in line for line in rig.gcode.lines)


def test_a_slot_wait_ends_at_a_stop_request(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    heaters = u1_heaters(rig, rate=0.0)
    heaters.active_heating_extruders[:] = ["extruder1", "extruder3"]
    t0 = rig.reactor.now
    rig.reactor.on_pause.append(lambda now: now >= t0 + 3.0 and setattr(run, "stop_asked", True))
    assert not rig.bg._heat_to(run, 260.0)
    assert rig.reactor.now - t0 < 4.0 and run.status != "stopped"


def test_a_hand_back_that_climbs_waits_for_a_slot(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    heaters = u1_heaters(rig, rate=0.0)
    heaters.active_heating_extruders[:] = ["extruder1"]  # the printing head climbs
    rig.ext.heater.temp = rig.ext.heater.target = 150.0
    run.called = 220.0  # the file's pre-heat, recorded while the dock ran
    rig.bg._give_heater(run)
    assert rig.ext.heater.target == 150.0 and heaters.set_at == []
    heaters.active_heating_extruders.clear()  # the printing head reached its target
    rig.reactor.run_timers(rig.reactor.now + 2.0)
    assert rig.ext.heater.target == 220.0


def test_a_hand_back_waiting_for_a_slot_is_dropped_when_the_target_changes(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    heaters = u1_heaters(rig, rate=0.0)
    heaters.active_heating_extruders[:] = ["extruder1"]
    rig.ext.heater.temp = rig.ext.heater.target = 150.0
    run.called = 220.0
    rig.bg._give_heater(run)
    rig.ext.heater.target = 230.0  # the arrival set its own
    heaters.active_heating_extruders.clear()
    rig.reactor.run_timers(rig.reactor.now + 2.0)
    assert rig.ext.heater.target == 230.0 and heaters.set_at == []


def test_a_resumed_run_past_its_push_does_not_need_its_bay(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)
    run = started(rig)
    run.busy, run.status, run.resume_ok = False, "stopped", True
    run.done.update(("unload", "free", "roll back", "hand over", "push"))
    a.lanes[1]["insert"] = False  # the lane left its bay: it is in the head's tube now
    assert rig.bg.candidates() == [run]


def test_an_unreadable_heater_makes_the_return_wait(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = bg_rig(tmp_path)
    caplog.set_level("INFO")
    swapper = rig.module.swapper
    swapper._extruder = lambda head: None
    assert swapper._below(0, 220.0)
    assert any("head 1: its heater cannot be read" in r.getMessage() for r in caplog.records)


# --- a head the U1 picks and parks again (bench B7, the print's start check) ----------------


def u1_picks_head_1(rig: SimpleNamespace, during: list[int]) -> None:
    """The U1's own T0 A0 (its print-start check): head 1 is picked. During the U1's T, before
    the pick has switched the active head, the watch tick runs; how many callbacks it registered
    is put in during."""

    def u1_t0(gcmd: object) -> None:
        before = len(rig.reactor.callbacks)
        rig.module.watch_print(rig.reactor.now)
        during.append(len(rig.reactor.callbacks) - before)
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")

    rig.gcode.commands_orig["T0"] = u1_t0
    rig.gcode.commands["T0"](Gcmd({"A": "0"}, "T0"))


def u1_loads_head_1(rig: SimpleNamespace) -> None:
    """The U1's own load of head 1 (FEED_AUTO LOAD=1): its hook pushes head 1's current lane."""
    rig.adapter.on_u1_load(rig.adapter.slot_of_head(0).current)


def test_a_run_the_u1_stopped_goes_on_in_its_dock_once_the_head_is_parked_again(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.reactor.on_pause.append(lambda now: rig.reactor.run_callbacks())
    during: list[int] = []
    u1_picks_head_1(rig, during)  # the run's callback runs during the T's wait for it
    rig.reactor.on_pause.clear()
    assert run.status == "stopped" and not run.busy and run.resume_ok
    assert during == [0]  # never resumed while the U1 picks the head
    rig.module.watch_print(rig.reactor.now)
    assert not run.busy  # head 1 is the active head: it waits
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")  # head 1 parked again
    rig.module.watch_print(rig.reactor.now)
    assert (
        run.busy
        and "ace2k_u1: head 1: background swap goes on in its dock from heat (to a:2)"
        in (rig.gcode.lines)
    )
    no_gcode(rig)
    assert run.status == "finished"


def test_a_u1_pick_after_the_tip_forms_the_tip_again_in_its_dock(tmp_path: Path) -> None:
    """The U1 picked the head after its tip (its print-start check, then its own load of that
    head): the old filament may be back in the gear. The run goes on from the unload, never from
    the free check, and once parked again it forms the tip again in its dock."""
    rig, _ = bg_rig(tmp_path)
    run = started(rig)

    def stop_after_tip(now: float) -> None:
        if run.step == "unload" and not run.stop_asked:
            rig.bg.ask_stop(run, resume_after=True)  # the U1's T0 A0 comes

    rig.reactor.on_pause.append(stop_after_tip)
    no_gcode(rig)
    rig.reactor.on_pause.clear()
    assert run.state.step == "free" and run.resume_ok  # the tip ended, as it always does
    tips = len(rig.mover.calls)
    u1_picks_head_1(rig, [])
    assert run.state.step == "free"  # the pick alone moves no filament
    u1_loads_head_1(rig)
    assert run.state.step == "unload" and "unload" not in run.done
    assert not run.state.unloaded and run.state.done_in_dock == frozenset()
    assert any("head 1: loaded by the U1 after its tip" in ln for ln in rig.gcode.lines)
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    rig.module.watch_print(rig.reactor.now)
    no_gcode(rig)
    assert run.status == "finished"
    assert rig.mover.calls[tips : tips + len(TIP)] == rig.mover.calls[:tips]  # the tip again


def test_a_u1_pick_of_a_run_stopped_by_hand_keeps_it_for_its_t(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.stop_asked, run.resume_ok = True, False  # ACEK_BG_STOP
    no_gcode(rig)
    u1_picks_head_1(rig, [])
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    rig.module.watch_print(rig.reactor.now)
    assert not run.busy and not run.resume_ok


def test_the_arrival_picks_its_head_before_a_step_that_moves_the_unit(tmp_path: Path) -> None:
    """A run stopped after its tip: at its T the free check, the roll back and the push run with
    the arriving head picked and at the cleaning area, never with the leaving head still over
    the print (bench B7: it sat 4 s over the wipe tower during the free check)."""
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    rig.reactor.on_pause.append(
        lambda now: setattr(run, "stop_asked", run.stop_asked or run.step == "unload")
    )
    no_gcode(rig)
    rig.reactor.on_pause.clear()
    assert run.state.step == "free"
    swapper = rig.module.swapper
    for name in ("free", "roll back", "hand over", "push"):
        real = swapper.steps[name]
        swapper.steps[name] = lambda state, name=name, real=real: (
            rig.sent.append(f"<{name}>"),
            real(state),
        )
    rig.sent.clear()
    assert tool(rig, 2) == [{}]
    assert rig.sent[:4] == [
        "SAVE_GCODE_STATE NAME=ACEK_SWAP",
        "T0 A0",
        "MOVE_TO_DISCARD_FILAMENT_POSITION",
        "<free>",
    ]
    assert rig.module.swapper.swap_pending is None


def test_the_arrival_of_a_finished_run_does_not_pick_early(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    rig.sent.clear()
    tool(rig, 2)
    assert rig.sent.index("T0 A0") > rig.sent.index("M104 S220 T0 A0")
    assert rig.sent.count("T0 A0") == 1


def stopped_before(rig: SimpleNamespace, step: str) -> BgRun:
    """Head 1's run, stopped by the U1's T0 A0 just before step; head 2 still active."""
    run = started(rig)
    steps = ace2k_u1_background.DOCK_STEPS

    def asking(fn: Callable[[BgRun], bool]) -> Callable[[BgRun], bool]:
        def wrapped(r: BgRun) -> bool:
            result = fn(r)
            nxt = steps.index(r.step) + 1
            if nxt < len(steps) and steps[nxt] == step:
                rig.bg.ask_stop(r, resume_after=True)
            return result

        return wrapped

    for name in ("_heat", "_unload", "_free", "_unit_step", "_grip", "_loaded"):
        setattr(rig.bg, name, asking(getattr(rig.bg, name)))
    no_gcode(rig)
    assert run.status == "stopped" and run.resume_ok
    return run


def test_a_u1_pick_after_the_roll_back_and_before_the_hand_over_goes_on_from_the_unload(
    tmp_path: Path,
) -> None:
    """Before the hand over the old lane is still the head's lane: the U1's load
    (FEED_AUTO LOAD=1) pushes it back to the head. The roll back is undone with the tip."""
    rig, _ = bg_rig(tmp_path)
    run = stopped_before(rig, "hand over")
    assert run.state.step == "hand over" and "roll back" in run.done
    u1_picks_head_1(rig, [])
    u1_loads_head_1(rig)
    assert run.state.step == "unload"
    assert not run.done & {"unload", "free", "roll back"}


def test_a_u1_pick_with_no_load_keeps_the_roll_back(tmp_path: Path) -> None:
    """The U1's T moves X, Y and Z only: with no load of the head after it, the old lane is
    where the roll back left it, and rolling it back again would take it ~2 x splitter_mm."""
    rig, _ = bg_rig(tmp_path)
    run = stopped_before(rig, "hand over")
    u1_picks_head_1(rig, [])
    assert run.state.step == "hand over" and {"unload", "free", "roll back"} <= run.done
    assert not any("by the U1 after its tip" in ln for ln in rig.gcode.lines)


def test_a_u1_pick_after_the_hand_over_keeps_the_runs_progress(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = stopped_before(rig, "push")
    u1_picks_head_1(rig, [])
    assert run.state.step == "push" and "hand over" in run.done


def test_a_pick_by_a_refill_of_its_head_does_not_undo_the_tip(tmp_path: Path) -> None:
    """A refill or a pending swap of the head keeps the run for its T and picks the head itself:
    that pick is the swap of today's, not the U1's."""
    rig, _ = bg_rig(tmp_path)
    run = stopped_before(rig, "free")
    with rig.module.swapper.giving_way(None, run.state.slot):
        u1_picks_head_1(rig, [])
        u1_loads_head_1(rig)
        assert run.state.step == "free" and "unload" in run.done
        assert run.yielded
    assert not any("by the U1 after its tip" in ln for ln in rig.gcode.lines)


def test_a_run_a_refill_kept_counts_a_u1_load_once_the_refill_ended(tmp_path: Path) -> None:
    """The run is yielded only while the swap it gave way to runs: a later load of the U1
    undoes its tip as for any kept run."""
    rig, _ = bg_rig(tmp_path)
    run = stopped_before(rig, "free")
    with contextlib.suppress(RuntimeError), rig.module.swapper.giving_way(None, run.state.slot):
        raise RuntimeError("the refill failed")
    assert not run.yielded
    u1_picks_head_1(rig, [])
    u1_loads_head_1(rig)
    assert run.state.step == "unload" and "unload" not in run.done


def test_an_inline_swap_that_keeps_the_run_leaves_it_not_yielded(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = stopped_before(rig, "free")
    rig.module.swapper.swap_pending = object()  # type: ignore[assignment]  # the T is refused
    try:
        took = rig.bg.before_inline(Gcmd({}, "T2"), run.state.slot, 2, None, False)
    finally:
        rig.module.swapper.swap_pending = None
    assert took is None and rig.bg.runs[0] is run and not run.yielded


def test_the_arrival_says_when_it_cannot_pick_first_unhomed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = bg_rig(tmp_path)
    stopped_before(rig, "free")
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    rig.printer.objects["toolhead"].homed_axes = ""
    caplog.set_level("INFO")
    swapper = rig.module.swapper
    rig.sent.clear()
    swapper._pick_first(rig.bg.runs[0].state, ["free"])
    assert rig.sent == []
    assert any("not homed" in r.getMessage() for r in caplog.records)


def test_the_arrival_does_not_pick_a_head_that_is_active_already(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    stopped_before(rig, "free")
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")  # head 1 active
    rig.sent.clear()
    rig.module.swapper._pick_first(rig.bg.runs[0].state, ["free"])
    assert rig.sent == ["MOVE_TO_DISCARD_FILAMENT_POSITION"]


def test_an_unreadable_sensor_before_the_unload_fails_it_before_any_unit_step(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.stop_asked = True  # stopped at the heat: the T starts at the unload
    no_gcode(rig)

    def unreadable(h: object) -> bool:
        raise RuntimeError("the head's sensor cannot be read")

    rig.adapter.head_set = unreadable
    ran = []
    for name in ("free", "roll back", "hand over", "push"):
        rig.module.swapper.steps[name] = lambda state, name=name: ran.append(name)
    tool(rig, 2)
    pending = rig.module.swapper.swap_pending
    assert pending is not None and pending.step == "unload" and ran == []


def test_a_fresh_swap_of_an_empty_head_picks_it_before_its_roll_back(tmp_path: Path) -> None:
    """Printing, the head's sensor empty: the unload picks nothing, so the swap picks the head
    and takes it to the cleaning area before the roll back."""
    rig, _ = bg_rig(tmp_path)
    rig.bg.heads = frozenset()
    rig.head_sensor.detected = False
    rig.print_stats.state = "printing"
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    swapper = rig.module.swapper
    real = swapper.steps["roll back"]
    swapper.steps["roll back"] = lambda state: (rig.sent.append("<roll back>"), real(state))
    rig.sent.clear()
    tool(rig, 2)
    assert rig.sent.index("T0 A0") < rig.sent.index("<roll back>")
    assert rig.sent[rig.sent.index("T0 A0") + 1] == "MOVE_TO_DISCARD_FILAMENT_POSITION"


def test_a_pending_swap_at_resume_does_not_pick_first(tmp_path: Path) -> None:
    """Paused, the head has left the part already: the RESUME's swap runs as before."""
    rig, _ = bg_rig(tmp_path)
    rig.pause_resume.is_paused = True
    rig.print_stats.state = "paused"
    swapper = rig.module.swapper
    state = SwapState(
        slot=rig.adapter.slot_of_head(0),
        old=rig.adapter.slot_of_head(0).lanes[0],
        new=rig.adapter.slot_of_head(0).lanes[1],
        tool=2,
    )
    state.step = "free"
    rig.sent.clear()
    swapper._pick_first(state, ["free"])
    assert rig.sent == []


def test_a_swap_outside_a_print_does_not_pick_first(tmp_path: Path) -> None:
    """A T typed at the console with no print: nothing picks or moves the head early."""
    rig, _ = bg_rig(tmp_path)
    stopped_before(rig, "free")
    rig.print_stats.state = "standby"
    rig.sent.clear()
    rig.module.swapper._pick_first(rig.bg.runs[0].state, ["free"])
    assert rig.sent == []


def test_an_unreadable_sensor_at_the_pick_names_the_head(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.stop_asked = True  # stopped at the heat: the T starts at the unload
    no_gcode(rig)

    def unreadable(h: object) -> bool:
        raise RuntimeError("link down")

    rig.adapter.head_set = unreadable
    tool(rig, 2)
    line = "failed at unload: head 1: its sensor cannot be read — link down"
    assert any(line in ln for ln in rig.gcode.lines)


def test_a_fresh_swap_of_an_unknown_empty_head_moves_nothing_before_its_pick(
    tmp_path: Path,
) -> None:
    """The lane in the head unknown, its sensor empty, printing: the read moves no lane (the
    head goes on from the hand over), and the pick comes before the unit's first move."""
    rig, a = bg_rig(tmp_path)
    rig.bg.heads = frozenset()
    rig.adapter.slot_of_head(0).current = None
    rig.head_sensor.detected = False
    starts: list[str] = []
    real_start = a.feed.start_move

    def start(
        lane: int, mode: str, length: float, speed: float, **kw: bool | str
    ) -> fakes.FakeMove:
        starts.append(f"<{mode}>")
        rig.sent.append(f"<{mode}>")
        return real_start(lane, mode, length, speed, **kw)

    a.feed.start_move = start
    rig.sent.clear()
    tool(rig, 2)
    assert starts  # the push ran
    assert rig.sent.index("T0 A0") < rig.sent.index(starts[0])
    assert rig.sent[rig.sent.index("T0 A0") + 1] == "MOVE_TO_DISCARD_FILAMENT_POSITION"


def test_the_u1s_load_tells_the_adapter_before_it_pushes(tmp_path: Path) -> None:
    """ace2k_u1_hooks._push_into_drive: on_u1_load(h) comes first, before any push."""
    rig, _ = bg_rig(tmp_path)
    seen: list[object] = []
    rig.adapter.on_u1_load = seen.append
    pushed: list[object] = []

    def no_push(h: object, load: bool = False) -> str:
        pushed.append((seen[:], h))
        return "stopped"

    rig.adapter.push_runner.push_lane = no_push
    with pytest.raises(Exception):  # noqa: B017, PT011 — the refused channel, as the U1's load fails
        rig.left._put_into_drive(1)  # head 1: the left feed's channel 1
    h = rig.adapter.slot_of_head(0).current
    assert seen == [h] and pushed == [([h], h)]


def test_a_push_the_print_end_stopped_says_so_not_a_timeout(tmp_path: Path) -> None:
    """Bench (a cancelled print): the push's lane was stopped from outside. The U1's error for
    a stopped move is a timeout; the line says the push was stopped, and why."""
    rig, a = bg_rig(tmp_path)
    run = started(rig)

    def push_stopped() -> None:
        rig.bg.print_over()  # the print is cancelled while the push runs

    a.feed.on_push = push_stopped
    real = a.feed.on_start

    def on_start(move: fakes.FakeMove) -> None:
        real(move)
        if move.mode == "feed":
            move.event = {"kind": "stopped", "seq": move.seq}

    a.feed.on_start = on_start
    no_gcode(rig)
    assert run.status == "stopped" and run.step == "push"
    lines = [ln for ln in rig.gcode.lines if "background swap stopped at push" in ln]
    assert lines == [
        "ace2k_u1: head 1: the background swap stopped at push: a:2's push was stopped before"
        " the head (stopped) (the print ended)"
    ]
    assert not any("timeout" in ln for ln in lines)


def test_an_inline_push_that_was_stopped_says_so(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)
    rig.bg.heads = frozenset()
    rig.head_sensor.detected = False
    a.feed.on_push = None
    real = a.feed.on_start

    def on_start(move: fakes.FakeMove) -> None:
        real(move)
        if move.mode == "feed":
            move.event = {"kind": "stopped_link", "seq": move.seq}

    a.feed.on_start = on_start
    tool(rig, 2)
    assert any(
        "failed at push: a:2's push was stopped before the head (stopped_link)" in ln
        for ln in rig.gcode.lines
    )


def push_ends(rig: SimpleNamespace, a: FakeAce2k, kind: str) -> None:
    """Every push move of the unit ends kind short of the head; the head stays empty."""
    rig.bg.heads = frozenset()
    rig.head_sensor.detected = False
    a.feed.on_push = None
    real = a.feed.on_start

    def on_start(move: fakes.FakeMove) -> None:
        real(move)
        if move.mode == "feed":
            move.event = {"kind": kind, "seq": move.seq}

    a.feed.on_start = on_start


def test_a_push_ended_by_a_sensor_fault_is_not_called_stopped(tmp_path: Path) -> None:
    """stopped_sensors is the unit's own fault, not a stop from outside."""
    rig, a = bg_rig(tmp_path)
    push_ends(rig, a, "stopped_sensors")
    tool(rig, 2)
    assert not any("was stopped" in ln for ln in rig.gcode.lines)
    assert any("failed at push: a:2 did not reach the head" in ln for ln in rig.gcode.lines)


def test_the_stopped_push_reads_the_end_of_its_own_move(tmp_path: Path) -> None:
    """The bay watch clears push_end once the lane moves again; the push's own end stays."""
    rig, a = bg_rig(tmp_path)
    push_ends(rig, a, "stopped")
    probe = rig.module.probe
    real = probe.push_in

    def push_in(*args: object, **kw: object) -> str | None:
        error = real(*args, **kw)
        for h in rig.adapter.hooked:
            h.push_end = None  # the next tick saw the lane move
        return error

    probe.push_in = push_in
    tool(rig, 2)
    assert any("a:2's push was stopped before the head (stopped)" in ln for ln in rig.gcode.lines)


def test_a_real_failure_during_a_stop_request_gets_no_stop_reason(tmp_path: Path) -> None:
    rig, a = bg_rig(tmp_path)
    run = started(rig)
    a.feed.on_push = rig.bg.print_over
    real = a.feed.on_start

    def on_start(move: fakes.FakeMove) -> None:
        real(move)
        if move.mode == "feed":
            move.event = {"kind": "stuck", "seq": move.seq}

    a.feed.on_start = on_start
    no_gcode(rig)
    assert run.step == "push"
    lines = [ln for ln in rig.gcode.lines if "background swap stopped at push" in ln]
    assert len(lines) == 1
    assert "(the print ended)" not in lines[0] and "(asked to stop)" not in lines[0]


def test_a_free_check_stopped_by_a_stop_request_says_why(tmp_path: Path) -> None:
    """The free check's roll back ended stopped while a stop request came: its ProbeError is a
    stopped move too, and the line says the reason."""
    rig, a = bg_rig(tmp_path)
    run = started(rig)
    real = a.feed.on_start

    def on_start(move: fakes.FakeMove) -> None:
        real(move)
        if move.mode == "rollback" and run.step == "free":
            rig.bg.ask_stop(run)  # ACEK_BG_STOP while the free check runs
            move.event = {"kind": "stopped", "seq": move.seq}

    a.feed.on_start = on_start
    no_gcode(rig)
    lines = [ln for ln in rig.gcode.lines if "background swap stopped at free" in ln]
    assert len(lines) == 1 and lines[0].endswith("free check ended stopped (asked to stop)")
