"""The light runs of the background swap (ace2k_u1_background, mode "light").

The rig is bg_rig of test_background with head 1 (index 0) in light_background_heads instead of
background_heads. A light run is what the departure leaves: the head unloaded at the cleaning
area and the free check done, the run kept for the dock. In the dock it runs the unit's steps
only (roll back, hand over, push): no heater, no parked move, no note_move.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from ace2k_u1_background import BgRun
from ace2k_u1_swap import SwapState
from test_background import bench, bg_rig, command, no_gcode
from test_tools import tool

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from fakes import FakeAce2k


def bg_rig_light(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:
    """bg_rig with head 1 a light head (and not a full one)."""
    rig, a = bg_rig(tmp_path)
    rig.bg.heads = frozenset({0})
    rig.bg.light = frozenset({0})
    rig.bg.full = frozenset()
    rig.adapter.on_u1_load = rig.module.departure.u1_loads  # as the hook wires it
    return rig, a


def kept_light(rig: SimpleNamespace) -> BgRun:
    """T1 of the file has run (head 1 docked); the light run its departure keeps, to a:2."""
    tool(rig, 1)
    slot = rig.adapter.slot_of_head(0)
    state = SwapState(slot, slot.current, slot.lanes[1], 2)
    state.unloaded = True
    rig.head_sensor.detected = False  # its departure unloaded the head
    run = BgRun(
        state,
        True,
        mode="light",
        status="stopped",
        busy=False,
        resume_ok=True,
        done={"unload", "free"},
    )
    rig.bg.runs[0] = run
    rig.adapter.swapping.add(0)
    run.hand_over_steps()
    return run


def finish(rig: SimpleNamespace) -> BgRun:
    run = kept_light(rig)
    rig.bg.check()
    no_gcode(rig)
    assert run.status == "finished"
    return run


def test_a_kept_light_run_goes_on_in_the_dock_with_the_unit_only(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    run = kept_light(rig)
    rig.bg.check()
    no_gcode(rig)
    assert run.status == "finished" and run.done >= {"roll back", "hand over", "push"}
    assert run.state.step == "spool"
    assert rig.heaters.calls == [] and rig.mover.calls == []
    assert not run.owns


def test_no_light_run_starts_by_itself(tmp_path: Path) -> None:  # only a departure starts one
    rig, _ = bg_rig_light(tmp_path)
    tool(rig, 1)
    assert 0 not in rig.bg.runs
    assert rig.reactor.callbacks == []


def test_a_light_run_needs_no_note_move(tmp_path: Path) -> None:
    rig, a = bg_rig_light(tmp_path)
    del a.feed.ff
    run = kept_light(rig)
    rig.bg.check()
    no_gcode(rig)
    assert run.status == "finished"
    assert not any("cannot record a parked move" in ln for ln in rig.gcode.lines)


def test_a_light_run_times_its_unit_steps_never_the_dock(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    finish(rig)
    times = rig.module.swap_times
    for step in ("roll back", "hand over", "push"):
        assert times.steps[step][1] == 1
    assert times.steps["heat"][1] == 0 and times.steps["grip"][1] == 0
    assert times.kinds["dock"][1] == 0


def test_the_status_says_the_mode(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    assert rig.module.get_status()["background"][0] == {
        "state": "idle",
        "step": None,
        "lane": None,
        "mode": "light",
    }
    kept_light(rig)
    assert rig.module.get_status()["background"][0] == {
        "state": "stopped",
        "step": "roll back",
        "lane": "a:2",
        "mode": "light",
    }
    assert rig.bg.status_lines()[0].startswith("ace2k_u1: head 1: background swap (light)")


def test_a_full_head_says_full(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    assert rig.module.get_status()["background"][0]["mode"] == "full"
    assert "(light)" not in rig.bg.status_lines()[0]


def test_acek_bg_swap_refuses_a_light_head(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    bench(rig)
    with pytest.raises(
        Exception,
        match=r"ACEK_BG_SWAP refused: head 1 swaps in the light way \(light_background_heads\)",
    ):
        command(rig, "ACEK_BG_SWAP", {"HEAD": "1", "LANE": "a:2"})
    assert rig.bg.runs == {}


def test_acek_bg_stop_drops_a_kept_light_run(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    kept_light(rig)
    command(rig, "ACEK_BG_STOP", {"HEAD": "1"})
    assert rig.bg.runs == {}


def test_a_finished_light_arrival_gives_a_light_sample(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    finish(rig)
    assert tool(rig, 2) == [{}]
    times = rig.module.swap_times
    assert times.kinds["light"][1] == 1
    assert times.kinds["ready"][1] == 0 and times.kinds["resume"][1] == 0
    assert rig.bg.runs == {}


def test_a_light_arrival_before_its_run_ends_gives_no_sample(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    kept_light(rig)
    assert tool(rig, 2) == [{}]
    times = rig.module.swap_times
    assert times.kinds["light"][1] == 0
    assert times.kinds["ready"][1] == 0 and times.kinds["resume"][1] == 0


@pytest.mark.parametrize(
    ("handed", "status", "step"), [(False, "stopped", "unload"), (True, "finished", "spool")]
)
def test_a_u1_load_of_a_light_head(tmp_path: Path, handed: bool, status: str, step: str) -> None:
    rig, _ = bg_rig_light(tmp_path)
    run = kept_light(rig)
    if handed:
        run.done |= {"roll back", "hand over"}
        run.hand_over_steps()
    slot = run.state.slot
    rig.module.departure.u1_loads(slot.lanes[0])
    assert run.status == status and run.state.step == step and run.resume_ok is False
    rig.bg.check()
    assert not run.busy  # never goes on in the dock after that load


def test_acek_bg_move_refuses_a_light_head(tmp_path: Path) -> None:
    # nothing is ever extruded in a light head's dock: the stock wiper covers its nozzle
    rig, _ = bg_rig_light(tmp_path)
    bench(rig)
    with pytest.raises(
        Exception,
        match=r"ACEK_BG_MOVE refused: head 1 swaps in the light way \(light_background_heads\)",
    ):
        command(rig, "ACEK_BG_MOVE", {"HEAD": "1", "DISTANCE": "5"})
    assert rig.mover.calls == []


def test_a_u1_load_while_its_hand_over_ends_counts_as_after_it(tmp_path: Path) -> None:
    """The U1 loads the head while the run is in its hand over: u1_loads stops it and waits, the
    hand over ends during that wait, and the load is read as one after the hand over."""
    rig, _ = bg_rig_light(tmp_path)
    run = kept_light(rig)
    run.done.add("roll back")
    run.busy, run.status, run.step = True, "running", "hand over"  # its callback is in the step

    def hand_over_ends(now: float) -> None:
        rig.reactor.on_pause.clear()
        rig.bg._unit_step(run)  # the step under way ends ...
        rig.bg._end(run)  # ... and the callback returns at the stop asked

    rig.reactor.on_pause.append(hand_over_ends)
    rig.module.departure.u1_loads(run.state.slot.lanes[0])
    assert "hand over" in run.done and "push" in run.done
    assert run.status == "finished" and run.state.step == "spool" and run.resume_ok is False
    assert rig.heaters.calls == [] and rig.mover.calls == []


def test_a_light_run_paused_after_its_roll_back_goes_on_from_its_hand_over(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    run = kept_light(rig)
    swapper = rig.module.swapper
    real = swapper.steps["roll back"]

    def roll_then_pause(state: object) -> None:
        real(state)
        rig.print_stats.state = "paused"
        rig.module.watch_print(rig.reactor.now)

    swapper.steps["roll back"] = roll_then_pause
    rig.bg.check()
    no_gcode(rig)
    swapper.steps["roll back"] = real
    assert run.status == "stopped" and run.resume_ok and run.state.step == "hand over"
    rig.print_stats.state = "printing"
    rig.module.watch_print(rig.reactor.now)
    assert "ace2k_u1: head 1: background swap goes on in its dock from hand over (to a:2)" in (
        rig.gcode.lines
    )
    no_gcode(rig)
    assert run.status == "finished" and run.state.step == "spool"
    assert rig.heaters.calls == [] and rig.mover.calls == []


def test_the_end_of_the_print_drops_a_busy_light_run_at_its_end(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    run = kept_light(rig)
    rig.bg.check()
    assert run.busy
    rig.module.print_over()
    assert run.drop_asked and rig.bg.runs[0] is run
    no_gcode(rig)
    assert rig.bg.runs == {} and 0 not in rig.adapter.swapping
    assert run.state.slot.current is None  # its lane is unknown
    assert rig.heaters.calls == []


def test_the_end_of_the_print_drops_a_kept_light_run(tmp_path: Path) -> None:
    rig, _ = bg_rig_light(tmp_path)
    run = kept_light(rig)
    rig.module.print_over()
    assert rig.bg.runs == {} and 0 not in rig.adapter.swapping
    assert run.state.slot.current is None  # its lane is unknown
    assert "ace2k_u1: head 1's lane is now unknown: its next swap probes it" in rig.gcode.lines
    assert rig.heaters.calls == []
