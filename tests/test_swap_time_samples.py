"""The samples the adapter gives the swap times (ace2k_u1_swap_times).

The swapper and the background swap each give a sample when a swap or a dock step ends by itself
in a print. The rigs are the ones of test_tools (swap_rig) and test_background (bg_rig); the fake
clock moves by hand, so each duration is known.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import ace2k_u1_history
import ace2k_u1_swap_times as st
import fakes
import pytest
from ace2k_u1_swap import SwapState
from test_background import bg_rig, finished, no_gcode, started
from test_tools import ends, freed_then, probe_finds, resume, swap_rig, tool
from test_units import Gcmd

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from ace2k_u1_background import BgRun
# The seconds each dock step takes in these tests (their sum is the dock's 67 s).
DOCK_S = {
    "heat": 3.0,
    "unload": 26.0,
    "free": 1.0,
    "roll back": 11.0,
    "hand over": 0.0,
    "push": 12.0,
    "grip": 14.0,
    "loaded": 0.0,
}
STEP_S = 10.0  # one inline swap step


def times(rig: SimpleNamespace) -> dict[str, object]:
    return rig.module.swap_times.status()


def count(rig: SimpleNamespace, key: str) -> object:
    return times(rig)[key]["n"]  # type: ignore[index]


def step_clock(rig: SimpleNamespace) -> list[str]:
    """Every swap step of the swapper takes STEP_S; returns the steps that ran."""
    swapper = rig.module.swapper
    real, ran = swapper.swap_step, []

    def step(name: str, state: SwapState) -> None:
        ran.append(name)
        rig.reactor.now += STEP_S
        real(name, state)

    swapper.swap_step = step
    return ran


def dock_clock(rig: SimpleNamespace) -> None:
    """The dock steps are stubs: each moves the fake clock by DOCK_S[step] and nothing else."""
    bg = rig.bg
    marks = {"unload": "unload", "free": "free", "roll back": "roll back", "hand over": "hand over",
             "push": "push", "loaded": "load"}  # fmt: skip

    def stub(run: BgRun) -> bool:
        rig.reactor.now += DOCK_S[run.step]
        if run.step in marks:
            run.done.add(marks[run.step])
        return True

    for name in ("_heat", "_unload", "_free", "_unit_step", "_grip", "_loaded"):
        setattr(bg, name, stub)


def stop_in(rig: SimpleNamespace, step: str, after: float) -> None:
    """The unit step `step` runs `after` seconds, then a stop is asked and it ends."""
    stub = rig.bg._unit_step

    def unit(run: BgRun) -> bool:
        if run.step != step:
            return stub(run)
        rig.reactor.now += after
        run.stop_asked = True
        return False

    rig.bg._unit_step = unit


def arrival_takes(rig: SimpleNamespace, seconds: float, result: bool = True) -> None:
    """The swap an arrival runs takes `seconds` and returns result."""
    swapper = rig.module.swapper
    real = swapper.resume_background

    def go_on(gcmd: object, state: SwapState) -> bool:
        end = rig.reactor.now + seconds
        ok = real(gcmd, state) if result else False
        rig.reactor.now = end  # exactly `seconds`, whatever the real steps waited
        return ok

    swapper.resume_background = go_on


# --- the inline swap -------------------------------------------------------------------------


def test_an_inline_swap_in_a_print_gives_one_inline_sample(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    ran = step_clock(rig)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    began = rig.reactor.now
    assert tool(rig, 2) == [{}]
    assert len(ran) > 3
    took = rig.reactor.now - began  # the figure of the console line, whole seconds
    assert times(rig)["inline"] == {"s": round(took, 1), "n": 1}
    assert took >= STEP_S * len(ran)
    assert f"({took:.0f} s)" in rig.gcode.lines[-1]


def inline_state(rig: SimpleNamespace, **fields: object) -> SwapState:
    slot = rig.adapter.slot_of_head(0)
    base: dict[str, object] = {
        "slot": slot,
        "old": slot.current,
        "new": slot.lanes[1],
        "tool": 2,
    }
    base.update(fields)
    return SwapState(**base)  # type: ignore[arg-type]


def run_state(rig: SimpleNamespace, a: fakes.FakeAce2k, **fields: object) -> bool:
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    state = inline_state(rig, **fields)
    return rig.module.swapper._run_swap(Gcmd({}, "T2"), state)


def test_no_inline_sample_for_a_refill_a_probe_or_a_console_t(tmp_path: Path) -> None:
    for fields, state in (
        ({"refill": True, "tool": None}, "printing"),
        ({"probed": True}, "printing"),
        ({"old": None}, "printing"),
        ({}, "standby"),  # a console T: the print is not printing
    ):
        rig, a = swap_rig(tmp_path)
        step_clock(rig)
        rig.print_stats.state = state
        probe_finds(rig, 0)
        run_state(rig, a, **fields)  # an unknown old lane may pause to probe: no sample then
        assert count(rig, "inline") == 0, fields


def sd_line(rig: SimpleNamespace, from_file: bool) -> None:
    """The U1's virtual_sdcard tells whether the T is a line of the file (False: the console)."""
    rig.printer.objects["virtual_sdcard"] = types.SimpleNamespace(is_cmd_from_sd=lambda: from_file)


def pause_asked_in(rig: SimpleNamespace) -> None:
    """A PAUSE is asked (the U1's pause_command_sent) while the swap runs; the swap still ends."""
    rig.pause_resume = fakes.FakePauseResume()
    rig.printer.objects["pause_resume"] = rig.pause_resume
    real = rig.module.swapper.swap_step

    def step(name: str, state: SwapState) -> None:
        real(name, state)
        rig.pause_resume.pause_command_sent = True

    rig.module.swapper.swap_step = step


@pytest.mark.parametrize("from_file", [True, False])
def test_an_inline_t_of_the_file_is_timed_one_typed_at_the_console_is_not(
    tmp_path: Path, from_file: bool
) -> None:
    rig, a = swap_rig(tmp_path)
    step_clock(rig)
    sd_line(rig, from_file)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    assert tool(rig, 2) == [{}]
    assert count(rig, "inline") == (1 if from_file else 0)


def test_no_inline_sample_when_a_pause_was_asked_during_the_swap(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    step_clock(rig)
    pause_asked_in(rig)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    assert rig.module.swapper.swap_pending is None  # the swap itself ended
    assert count(rig, "inline") == 0


def test_no_inline_sample_for_a_failed_swap_that_resume_finished(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    step_clock(rig)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    assert tool(rig, 2) == []  # it failed and paused the print
    assert count(rig, "inline") == 0
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    probe_finds(rig, 0)
    resume(rig)
    assert rig.module.swapper.swap_pending is None
    assert count(rig, "inline") == 0


# --- the dock --------------------------------------------------------------------------------


def test_each_finished_dock_step_is_sampled_and_the_whole_run_too(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    dock_clock(rig)
    started(rig)
    no_gcode(rig)
    s = times(rig)
    assert s["steps"]["unload"] == {"s": 26.0, "n": 1}  # type: ignore[index]
    assert s["steps"]["push"] == {"s": 12.0, "n": 1}  # type: ignore[index]
    assert s["dock"] == {"s": 67.0, "n": 1}


def test_a_stopped_step_is_not_sampled_and_a_resumed_run_gives_no_dock_sample(
    tmp_path: Path,
) -> None:
    rig, _ = bg_rig(tmp_path)
    dock_clock(rig)
    run = started(rig)
    stop_in(rig, "roll back", 2.0)
    no_gcode(rig)
    s = times(rig)
    assert s["steps"]["roll back"]["n"] == 0  # type: ignore[index]
    assert s["steps"]["unload"]["n"] == 1  # type: ignore[index]
    assert s["dock"]["n"] == 0  # type: ignore[index]
    # it goes on later from the roll back and finishes: the steps count, the dock does not
    dock_clock(rig)
    run.stop_asked = False
    run.status = "running"
    rig.bg._run(run)
    assert times(rig)["steps"]["roll back"] == {"s": 11.0, "n": 1}  # type: ignore[index]
    assert times(rig)["dock"]["n"] == 0  # type: ignore[index]


def test_an_unload_and_free_check_with_the_head_empty_give_no_step_sample(tmp_path: Path) -> None:
    # With the head's sensor empty the unload has no tip to form and the free check nothing to
    # check: both return at once, and a 0 s sample would pull their averages down.
    rig, _ = bg_rig(tmp_path)
    dock_clock(rig)
    bg = rig.bg
    del bg._unload, bg._free  # the real ones
    run = started(rig)
    rig.head_sensor.detected = False
    no_gcode(rig)
    assert {"unload", "free"} <= run.done
    s = times(rig)
    assert s["steps"]["unload"] == {"s": st.STEP_SEEDS["unload"], "n": 0}  # type: ignore[index]
    assert s["steps"]["free"] == {"s": st.STEP_SEEDS["free"], "n": 0}  # type: ignore[index]
    assert s["steps"]["push"]["n"] == 1  # type: ignore[index]


def test_an_acek_bg_swap_run_gives_no_sample(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    dock_clock(rig)
    run = started(rig)
    run.in_print = False  # as ACEK_BG_SWAP starts one
    no_gcode(rig)
    s = times(rig)
    assert s["dock"]["n"] == 0  # type: ignore[index]
    assert all(v["n"] == 0 for v in s["steps"].values())  # type: ignore[attr-defined]


# --- the arrival -----------------------------------------------------------------------------


def test_a_ready_arrival_gives_a_ready_sample_from_its_t(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    arrival_takes(rig, 16.0)
    assert tool(rig, 2) == [{}]
    assert times(rig)["ready"] == {"s": 16.0, "n": 1}
    assert count(rig, "resume") == 0


def test_a_finished_run_without_its_load_gives_no_arrival_sample(tmp_path: Path) -> None:
    # no purge in the dock, or the head's sensor empty at the end: finished, not loaded
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    assert run.status == "finished"
    run.done.discard("load")
    arrival_takes(rig, 40.0)
    assert tool(rig, 2) == [{}]
    assert count(rig, "ready") == 0 and count(rig, "resume") == 0


def test_a_mid_way_arrival_gives_a_resume_sample(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    dock_clock(rig)
    started(rig)
    stop_in(rig, "roll back", 2.0)
    no_gcode(rig)
    arrival_takes(rig, 70.0)
    assert tool(rig, 2) == [{}]
    # left = (11 - 2) + 12 + 14 = 35; resume = 70 - 15 (ready seed) - 35 = 20
    assert times(rig)["resume"] == {"s": 20.0, "n": 1}
    assert count(rig, "ready") == 0


def test_a_t_during_the_grip_gives_a_resume_sample_with_the_grip_left(tmp_path: Path) -> None:
    # The T comes 10 s into a 14 s grip; the grip ends during the T's wait. What counts is the
    # run at the T: not ready, with the grip's 4 s still dock work left.
    rig, _ = bg_rig(tmp_path)
    run = finished(rig)
    rig.module.swap_times = st.SwapTimes()  # the seeds: the grip takes 14 s
    run.status, run.busy, run.step = "running", True, "grip"
    run.step_at = rig.reactor.now - 10.0
    run.done.discard("load")
    run.state.gripped = False
    t0 = rig.reactor.now

    def grip_ends(now: float) -> None:
        if run.busy and now >= t0 + 4.0:
            run.status, run.busy, run.in_step_s = "finished", False, 14.0
            run.state.gripped = True
            run.done.add("load")
            run.hand_over_steps()

    rig.reactor.on_pause.append(grip_ends)
    arrival_takes(rig, 30.0)
    assert tool(rig, 2) == [{}]
    took = rig.reactor.now - t0
    assert count(rig, "ready") == 0
    # left = (14 - 10) + 0 (loaded); resume = took - 15 (ready seed) - 4
    assert times(rig)["resume"] == {"s": round(took - 19.0, 1), "n": 1}


def test_a_t_before_a_resumed_run_enters_its_step_counts_no_time_of_its_earlier_run(
    tmp_path: Path,
) -> None:
    # Stopped 2 s into the roll back, then resumed 100 s later: the T comes before the resumed
    # callback ran. None of the earlier 102 s counts as time in the step.
    rig, _ = bg_rig(tmp_path)
    dock_clock(rig)
    run = started(rig)
    stop_in(rig, "roll back", 2.0)
    no_gcode(rig)
    rig.reactor.now += 100.0
    rig.bg._resume(run)
    assert run.busy
    # the callback runs during the T's wait: the T's stop request ends it before its first step
    rig.reactor.on_pause.append(lambda _now: rig.reactor.run_callbacks())
    t0 = rig.reactor.now
    arrival_takes(rig, 70.0)
    assert tool(rig, 2) == [{}]
    took = rig.reactor.now - t0
    # left = 11 (roll back, none of it done) + 12 + 14 = 37
    assert times(rig)["resume"] == {"s": round(took - 15.0 - 37.0, 1), "n": 1}


def test_a_stop_after_the_grip_counts_the_grip_as_done(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)

    def grip(run: BgRun) -> bool:  # it grips, then a stop is asked before the step ends
        rig.reactor.now += 5.0
        run.state.gripped = True
        run.stop_asked = True
        return False

    rig.bg._grip = grip
    started(rig)
    no_gcode(rig)
    arrival_takes(rig, 50.0)
    assert tool(rig, 2) == [{}]
    # left = 0 (the grip is done, the loaded step is 0 s); resume = 50 - 15 (ready seed) - 0
    assert times(rig)["resume"] == {"s": 35.0, "n": 1}


def test_a_stop_in_the_loaded_check_with_the_load_done_counts_it_as_done(tmp_path: Path) -> None:
    # run.done names the load "load"; its step is "loaded": with the load done, no work is left.
    rig, _ = bg_rig(tmp_path)

    def loaded(run: BgRun) -> bool:  # the load is done, then a stop ends the run at this step
        run.done.add("load")
        run.stop_asked = True
        return False

    rig.bg._loaded = loaded
    run = started(rig)
    no_gcode(rig)
    assert (run.status, run.step) == ("stopped", "loaded")
    rig.module.swap_times = st.SwapTimes()
    rig.module.swap_times.note_step("loaded", 5.0)  # a loaded check measured at 5 s
    arrival_takes(rig, 50.0)
    assert tool(rig, 2) == [{}]
    # left = 0; resume = 50 - 15 (ready seed) - 0
    assert times(rig)["resume"] == {"s": 35.0, "n": 1}


def test_a_stop_shorter_than_the_work_left_gives_a_resume_of_zero(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    dock_clock(rig)
    started(rig)
    stop_in(rig, "roll back", 2.0)
    no_gcode(rig)
    arrival_takes(rig, 30.0)  # 30 - 15 - 35 < 0: clamped to 0
    tool(rig, 2)
    assert times(rig)["resume"] == {"s": 0.0, "n": 1}


def test_no_arrival_sample_when_the_plan_changed_or_the_swap_failed(tmp_path: Path) -> None:
    # the plan changed: T2 wants the lane in the head, so a second swap ran
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.stop_asked = True
    no_gcode(rig)
    slot = run.state.slot
    plan = rig.module.tracker.plan
    plan.choice[5] = slot.lanes[1]
    plan.choice[2] = slot.lanes[0]
    arrival_takes(rig, 30.0)
    tool(rig, 2)
    assert count(rig, "ready") == 0 and count(rig, "resume") == 0
    # the swap returned False
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    arrival_takes(rig, 30.0, result=False)
    tool(rig, 2)
    assert count(rig, "ready") == 0 and count(rig, "resume") == 0


def test_no_arrival_sample_from_a_run_that_failed(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    dock_clock(rig)
    stub = rig.bg._unit_step

    def fails(run: BgRun) -> bool:
        if run.step == "roll back":
            raise RuntimeError("the unit did not answer")
        return stub(run)

    rig.bg._unit_step = fails
    run = started(rig)
    no_gcode(rig)
    assert run.status == "stopped"
    arrival_takes(rig, 70.0)
    assert tool(rig, 2) == [{}]
    assert count(rig, "ready") == 0 and count(rig, "resume") == 0


def test_a_run_the_t_stopped_before_any_dock_work_gives_a_resume_sample(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run = started(rig)
    run.stop_asked = True  # stopped before its heat: the whole dock is left
    no_gcode(rig)
    arrival_takes(rig, 100.0)
    assert tool(rig, 2) == [{}]
    assert count(rig, "resume") == 1


def test_no_arrival_sample_when_the_print_was_not_printing(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    arrival_takes(rig, 16.0)
    rig.print_stats.state = "standby"
    tool(rig, 2)
    assert count(rig, "ready") == 0 and count(rig, "resume") == 0


def test_no_arrival_sample_for_a_console_t_or_a_pause_during_the_arrival(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    sd_line(rig, False)  # T2 typed at the console while printing
    arrival_takes(rig, 16.0)
    assert tool(rig, 2) == [{}]
    assert count(rig, "ready") == 0 and count(rig, "resume") == 0
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    sd_line(rig, True)
    rig.pause_resume = fakes.FakePauseResume()
    rig.printer.objects["pause_resume"] = rig.pause_resume
    swapper = rig.module.swapper
    real = swapper.resume_background

    def paused_meanwhile(gcmd: object, state: SwapState) -> bool:
        rig.pause_resume.pause_command_sent = True  # a PAUSE asked while the T waited
        return real(gcmd, state)

    swapper.resume_background = paused_meanwhile
    tool(rig, 2)
    assert count(rig, "ready") == 0 and count(rig, "resume") == 0


# --- the file --------------------------------------------------------------------------------


def test_the_file_is_opened_in_the_history_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _ = bg_rig(tmp_path)
    module = rig.module
    folder = tmp_path / "hist"
    rig.printer.objects["ace2k_u1_history"] = types.SimpleNamespace(
        writer=types.SimpleNamespace(path=str(folder))
    )
    assert module._history_folder() == str(folder)
    del rig.printer.objects["ace2k_u1_history"]
    default = tmp_path / "default"
    monkeypatch.setattr("ace2k_u1.HISTORY_PATH", str(default))
    assert module._history_folder() == str(default)
    assert ace2k_u1_history.DEFAULT_PATH  # the real default exists
    module.swap_times = st.SwapTimes()
    module._on_ready()
    module.swap_times.note("dock", 70.0)
    module.swap_times.stop()
    assert (default / st.FILE_NAME).exists()
