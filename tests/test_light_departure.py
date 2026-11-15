"""The departure of a light head (ace2k_u1_light.Departure), spec section 3.1.

The rig is bg_rig_light of test_light_background with head 1 (index 0) the active one, at layer
1, its target 170 °C, in a print at T0 of [0, 1, 2]: T1 (head 2) docks head 1, whose next T (T2)
needs a:2, whose bay holds filament. The U1's T1 makes head 2's extruder the active one. At T1,
head 1 forms its tip and frees its sensor at the cleaning area, then docks with a kept light run.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import pytest
from ace2k_u1_background import BgRun
from ace2k_u1_swap import SwapState
from test_background import no_gcode, temp_call
from test_dock_wipe import make_active, u1_picks
from test_light_background import bg_rig_light
from test_tools import order_is, tool
from test_units import Gcmd

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from fakes import FakeMove

DEPARTS = "ace2k_u1: head 1: leaves with its tip formed: the unit swaps it to a:2 while parked"
U1_T1 = "(the U1's T1)"  # what the U1's T1 adds to rig.sent, to read its place among the lines


def dep_rig(tmp_path: Path) -> SimpleNamespace:
    rig, a = bg_rig_light(tmp_path)
    rig.a = a
    make_active(rig, 0)  # head 1 prints
    rig.u1.active = rig.printer.objects["extruder_list"][0]
    rig.print_stats.current_layer = 1
    rig.ext.heater.temp = rig.ext.heater.target = 170.0  # head 1's target before the T
    u1_picks(rig)  # the swap's T0 A0 makes head 1's extruder the active one
    rig.sent.clear()
    return rig


def fail_on(rig: SimpleNamespace, prefix: str) -> None:
    """Makes every script that starts with prefix raise a heater fault, after it ran."""
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        real(script)
        if script.startswith(prefix):
            raise RuntimeError("heater fault")

    rig.gcode.run_script_from_command = run_script


def t1(rig: SimpleNamespace) -> list[dict[str, str]]:
    """T1 of the file: the U1's T1 picks head 2 (and docks head 1)."""
    calls = []

    def u1_t1(gcmd: Gcmd) -> None:
        calls.append(gcmd.params)
        rig.sent.append(U1_T1)
        make_active(rig, 1)
        rig.u1.active = rig.printer.objects["extruder_list"][1]

    rig.gcode.commands_orig["T1"] = u1_t1
    rig.gcode.commands["T1"](Gcmd({}, "T1"))
    return calls


def test_a_t_of_another_head_departs_a_light_head(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    assert t1(rig) == [{}]
    sent = rig.sent
    unload = next(i for i, ln in enumerate(sent) if ln.startswith("ACEK_SWAP_UNLOAD HEAD=0 "))
    order = [
        sent.index("SAVE_GCODE_STATE NAME=ACEK_DEPART"),
        unload,
        sent.index("M104 S170 T0 A0"),
        sent.index("RESTORE_GCODE_STATE NAME=ACEK_DEPART MOVE=0"),
        sent.index(U1_T1),
    ]
    assert order == sorted(order)
    assert DEPARTS in rig.gcode.lines
    run = rig.bg.runs[0]
    assert run.mode == "light" and run.in_print
    assert run.done >= {"unload", "free"}
    assert 0 in rig.adapter.swapping
    assert rig.pause_resume.is_paused is False and rig.module.swapper.swap_pending is None


def test_the_departure_is_kept_as_a_light_run_that_goes_on_in_the_dock(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    real_check = rig.bg.check
    seen = []

    def check() -> None:  # the run as the T left it, before the start check goes on with it
        run = rig.bg.runs.get(0)
        if run is not None and not seen:
            seen.append((run.status, run.busy, run.resume_ok, set(run.done), run.state.step))
        real_check()

    rig.bg.check = check
    t1(rig)
    assert seen == [("stopped", False, True, {"unload", "free"}, "roll back")]


def test_the_unit_swaps_it_once_docked(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    t1(rig)
    run = rig.bg.runs[0]
    no_gcode(rig)
    assert run.status == "finished" and run.state.step == "spool"
    assert run.done >= {"roll back", "hand over", "push"}
    assert rig.heaters.calls == [] and rig.mover.calls == []
    rig.sent.clear()
    assert tool(rig, 2) == [{}]
    assert any(ln.startswith("ACEK_SWAP_LOAD HEAD=0 ") for ln in rig.sent)
    assert not any(ln.startswith("ACEK_SWAP_UNLOAD") for ln in rig.sent)
    assert rig.bg.runs == {}


def test_a_departure_gives_a_depart_sample(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    t1(rig)
    assert rig.module.swap_times.kinds["depart"][1] == 1


def test_a_departure_counts_as_one_swap_of_its_stop(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    t1(rig)
    assert rig.module.swap_times.stops.n == 1


def test_an_empty_head_departs_without_a_sample(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    rig.head_sensor.detected = False
    t1(rig)
    assert not any(ln.startswith("ACEK_SWAP_UNLOAD") for ln in rig.sent)
    assert rig.module.swap_times.kinds["depart"][1] == 0
    run = rig.bg.runs[0]
    assert run.mode == "light" and run.done >= {"unload", "free"}


def held(rig: SimpleNamespace) -> None:
    """Every roll back of a:1 finds it held by the gear: the free check holds, twice."""
    a = rig.a

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True  # the gear still holds a:1: its plunger goes taut
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start


def test_a_held_tip_leaves_the_unload_to_the_t(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    held(rig)
    assert t1(rig) == [{}]  # the T goes on: no pause
    assert rig.print_stats.state == "printing" and rig.pause_resume.is_paused is False
    assert rig.module.swapper.swap_pending is None
    assert sum(ln.startswith("ACEK_SWAP_UNLOAD") for ln in rig.sent) == 2
    run = rig.bg.runs[0]
    assert run.done == set() and run.resume_ok is False and run.failed is True
    assert run.status == "stopped" and not run.busy
    assert run.state.step == "unload" and run.state.unloaded is False
    assert "M104 S170 T0 A0" in rig.sent
    assert "RESTORE_GCODE_STATE NAME=ACEK_DEPART MOVE=0" in rig.sent
    said = [ln for ln in rig.gcode.lines if "its departure failed" in ln]
    assert said == [
        "ace2k_u1: head 1: its departure failed at free: a:1 is still held by head 1 after a "
        "second unload: its swap runs at its T"
    ]
    assert rig.module.swap_times.kinds["depart"][1] == 0
    assert DEPARTS not in rig.gcode.lines
    no_gcode(rig)
    assert not run.busy  # never goes on in the dock
    # its T swaps inline from the unload
    rig.a.feed.on_start = lambda move: setattr(move, "event", {"kind": "done", "seq": move.seq})
    rig.a.lanes[0]["pushed"] = False
    rig.sent.clear()
    make_active(rig, 1)
    assert tool(rig, 2) == [{}]
    assert any(ln.startswith("ACEK_SWAP_UNLOAD HEAD=0 ") for ln in rig.sent)
    assert rig.bg.runs == {}


def test_an_empty_head_leaves_empty_not_with_a_tip(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    rig.head_sensor.detected = False
    t1(rig)
    assert DEPARTS not in rig.gcode.lines
    assert (
        "ace2k_u1: head 1: leaves empty: the unit swaps it to a:2 while parked"
    ) in rig.gcode.lines


def held_then_released(rig: SimpleNamespace) -> None:
    """A departure whose free check held twice; the gear lets a:1 go afterwards."""
    held(rig)
    t1(rig)
    rig.a.feed.on_start = lambda move: setattr(move, "event", {"kind": "done", "seq": move.seq})
    rig.a.lanes[0]["pushed"] = False


def test_a_failed_departure_is_not_dropped_by_a_plan_that_keeps_the_old_lane(
    tmp_path: Path,
) -> None:
    """The tip is formed and out of the melt zone: a T that keeps a:1 unloads and loads it."""
    rig = dep_rig(tmp_path)
    held_then_released(rig)
    slot = rig.adapter.slot_of_head(0)
    rig.module.tracker.plan.choice[2] = slot.lanes[0]  # T2 now wants a:1, the lane in head 1
    rig.a.lanes[0]["insert"] = True
    rig.sent.clear()
    make_active(rig, 1)
    assert tool(rig, 2) == [{}]
    assert not any("keeps its lane" in ln for ln in rig.gcode.lines)
    unload = next(i for i, ln in enumerate(rig.sent) if ln.startswith("ACEK_SWAP_UNLOAD HEAD=0 "))
    load = next(i for i, ln in enumerate(rig.sent) if ln.startswith("ACEK_SWAP_LOAD HEAD=0 "))
    assert unload < load
    assert rig.bg.runs == {}
    assert slot.current is slot.lanes[0]


def test_a_failed_departure_at_the_end_of_the_print_leaves_the_lane_unknown(
    tmp_path: Path,
) -> None:
    rig = dep_rig(tmp_path)
    held_then_released(rig)
    rig.module.print_over()
    assert rig.bg.runs == {}
    assert rig.adapter.slot_of_head(0).current is None
    assert "ace2k_u1: head 1's lane is now unknown: its next swap probes it" in rig.gcode.lines


def test_a_failed_unload_keeps_the_run_for_its_t(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    fail_on(rig, "ACEK_SWAP_UNLOAD")
    assert t1(rig) == [{}]
    assert rig.pause_resume.is_paused is False and rig.module.swapper.swap_pending is None
    run = rig.bg.runs[0]
    assert run.done == set() and run.resume_ok is False and run.failed is True
    assert run.state.step == "unload"
    assert 0 in rig.adapter.swapping
    assert (
        "ace2k_u1: head 1: its departure failed at unload: heater fault: its swap runs at its T"
    ) in rig.gcode.lines
    assert "M104 S170 T0 A0" in rig.sent
    assert "RESTORE_GCODE_STATE NAME=ACEK_DEPART MOVE=0" in rig.sent


def test_a_failed_cool_down_still_restores_the_gcode_state(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    fail_on(rig, "M104 ")
    assert t1(rig) == [{}]
    assert rig.sent.index("M104 S170 T0 A0") < rig.sent.index(
        "RESTORE_GCODE_STATE NAME=ACEK_DEPART MOVE=0"
    )
    assert rig.pause_resume.is_paused is False and rig.print_stats.state == "printing"
    assert rig.module.swapper.swap_pending is None
    assert "ace2k_u1: head 1: its target after its departure: heater fault" in rig.gcode.lines
    assert DEPARTS in rig.gcode.lines
    assert rig.bg.runs[0].mode == "light" and not rig.bg.runs[0].failed  # it goes on in the dock


def kept(rig: SimpleNamespace) -> None:
    slot = rig.adapter.slot_of_head(0)
    state = SwapState(slot, slot.current, slot.lanes[1], 2)
    rig.bg.runs[0] = BgRun(state, True, mode="light", status="stopped", busy=False)
    rig.adapter.swapping.add(0)


def _case(rig: SimpleNamespace, change: str) -> None:  # noqa: PLR0912 — one rig per case
    if change == "layer_0":
        rig.print_stats.current_layer = 0
    elif change == "paused":
        rig.pause_resume.pause_command_sent = True
    elif change == "console":
        rig.printer.objects["virtual_sdcard"] = types.SimpleNamespace(is_cmd_from_sd=lambda: False)
    elif change == "full_head":
        rig.bg.light = frozenset()
        rig.bg.full = rig.bg.heads = frozenset({0})
    elif change == "empty_bay":
        rig.a.lanes[1]["insert"] = False
    elif change == "same_lane":
        order_is(rig, [0, 1, 0], 0)
    elif change == "pending":
        slot = rig.adapter.slot_of_head(0)
        rig.module.swapper.swap_pending = SwapState(slot, slot.current, slot.lanes[1], 2)
    elif change == "kept_run":
        kept(rig)
    elif change == "unknown_t_head":  # the T's head cannot be read: it may be head 1's own
        rig.module.background._tool_head = lambda n: None
    elif change == "unknown_lane":
        rig.adapter.set_current(rig.adapter.slot_of_head(0), None)


@pytest.mark.parametrize(
    "change",
    [
        "layer_0",
        "paused",
        "console",
        "own_head",
        "full_head",
        "empty_bay",
        "same_lane",
        "pending",
        "kept_run",
        "unknown_lane",
        "unknown_t_head",
        "u1_pick",
    ],
)
def test_no_departure(tmp_path: Path, change: str) -> None:
    rig = dep_rig(tmp_path)
    _case(rig, change)
    before = rig.bg.runs.get(0)
    if change == "own_head":
        tool(rig, 0)
    elif change == "u1_pick":
        rig.gcode.commands_orig["T1"] = lambda gcmd: None
        rig.gcode.commands["T1"](Gcmd({"A": "0"}, "T1"))
    else:
        t1(rig)
    assert not any("ACEK_DEPART" in ln for ln in rig.sent)
    assert DEPARTS not in rig.gcode.lines
    run = rig.bg.runs.get(0)
    if change == "kept_run":
        assert run is before
    elif change == "full_head":  # its own background swap starts in the dock, as today
        assert run is None or run.mode == "full"
    else:
        assert run is None


def test_a_light_head_gets_no_dock_wipe(tmp_path: Path) -> None:
    """A plain T0 of the file takes head 1 from its dock: a light head gets no dock wipe."""
    rig = dep_rig(tmp_path)
    make_active(rig, 1)
    rig.u1.active = rig.printer.objects["extruder_list"][1]
    rig.print_stats.current_layer = 3

    def u1_t0(gcmd: Gcmd) -> None:
        make_active(rig, 0)

    rig.gcode.commands_orig["T0"] = u1_t0
    rig.gcode.commands["T0"](Gcmd({}, "T0"))
    assert rig.printer.objects["toolhead"].name == "extruder"
    assert not any("ACEK_DOCK_WIPE" in ln for ln in rig.sent)
    assert "ace2k_u1: head 1: leaves its dock: one wipe" not in rig.gcode.lines


def test_a_full_head_still_gets_its_dock_wipe(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    rig.bg.light = frozenset()
    rig.bg.full = rig.bg.heads = frozenset({0})
    make_active(rig, 1)
    rig.u1.active = rig.printer.objects["extruder_list"][1]
    rig.print_stats.current_layer = 3
    rig.gcode.commands_orig["T0"] = lambda gcmd: make_active(rig, 0)
    rig.gcode.commands["T0"](Gcmd({}, "T0"))
    assert "SAVE_GCODE_STATE NAME=ACEK_DOCK_WIPE" in rig.sent


def test_a_departure_never_raises_into_the_t(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    rig.module.departure.host = types.SimpleNamespace()  # every read raises
    assert t1(rig) == [{}]
    assert 0 not in rig.bg.runs


# --- the parked head stays at its cool-down (spec 3.1, decision 7) ----------------------------


def test_a_pre_heat_owed_to_the_departed_head_is_dropped(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    rig.module.tracker.owed_heat = {0: (220.0, "T0's cool-down raised to T2's kept pre-heat")}
    t1(rig)
    assert "M104 S220 T0 A0" not in rig.sent  # it unloaded at its departure: no dock pre-heat


def test_the_files_pre_heat_of_the_parked_light_head_is_applied(tmp_path: Path) -> None:
    """The slicer heats the head again before its T (50 s ahead): it arrives hot."""
    rig = dep_rig(tmp_path)
    t1(rig)
    assert temp_call(rig, "M104", {"S": "215", "T": "2"}) != []
    assert rig.bg.runs[0].called is None


KEPT_COOL = "ace2k_u1: T0's cool-down kept: head 1 forms its tip as it leaves"


@pytest.mark.parametrize("s", ["0", "120"])
def test_the_files_cool_down_before_a_departure_is_held_for_its_end(tmp_path: Path, s: str) -> None:
    """The slicer cools T0 just before T1, and the tip needs the nozzle hot."""
    rig = dep_rig(tmp_path)
    assert temp_call(rig, "M104", {"S": s, "T": "0"}) == []  # not applied
    assert rig.module.tracker.light_cool == {0: float(s)}
    assert KEPT_COOL in rig.gcode.lines
    t1(rig)
    sent = rig.sent
    assert f"M104 S{s} T0 A0" in sent and "M104 S170 T0 A0" not in sent
    unload = next(i for i, ln in enumerate(sent) if ln.startswith("ACEK_SWAP_UNLOAD HEAD=0 "))
    assert unload < sent.index(f"M104 S{s} T0 A0") < sent.index(U1_T1)
    assert rig.module.tracker.light_cool == {}
    assert DEPARTS in rig.gcode.lines


def test_a_held_cool_down_is_applied_at_the_t_when_the_head_does_not_depart(
    tmp_path: Path,
) -> None:
    rig = dep_rig(tmp_path)
    temp_call(rig, "M104", {"S": "0", "T": "0"})
    rig.print_stats.current_layer = 0  # no departure at layer 0
    t1(rig)
    assert DEPARTS not in rig.gcode.lines
    assert "M104 S0 T0 A0" in rig.sent
    assert rig.sent.index("M104 S0 T0 A0") < rig.sent.index(U1_T1)
    assert rig.module.tracker.light_cool == {}


def test_a_cool_down_is_applied_when_the_head_will_not_depart(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    rig.print_stats.current_layer = 0
    assert temp_call(rig, "M104", {"S": "0", "T": "0"}) != []  # applied as sent
    assert rig.module.tracker.light_cool == {}
    assert KEPT_COOL not in rig.gcode.lines


def test_a_held_cool_down_is_dropped_at_the_end_of_a_print(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    temp_call(rig, "M104", {"S": "0", "T": "0"})
    rig.module.tracker.reset_print()
    assert rig.module.tracker.light_cool == {}


def test_a_held_cool_down_is_applied_when_the_departure_fails(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    temp_call(rig, "M104", {"S": "0", "T": "0"})
    fail_on(rig, "ACEK_SWAP_UNLOAD")
    t1(rig)
    assert rig.bg.runs[0].failed is True
    assert "M104 S0 T0 A0" in rig.sent and "M104 S170 T0 A0" not in rig.sent
    assert rig.module.tracker.light_cool == {}


def test_a_failed_release_of_a_held_cool_down_names_no_departure(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    temp_call(rig, "M104", {"S": "0", "T": "0"})
    rig.print_stats.current_layer = 0  # no departure
    fail_on(rig, "M104 ")
    assert t1(rig) == [{}]
    said = "ace2k_u1: head 1: its cool-down, kept for a departure: heater fault"
    assert said in rig.gcode.lines


def test_the_files_wait_for_the_next_head_runs_after_the_departure(tmp_path: Path) -> None:
    """The slicer's M109 for T1 before T1 held head 1 over the purge tower while head 2 heated:
    it is applied without its wait, and the wait runs once head 1 has departed."""
    rig = dep_rig(tmp_path)
    calls = temp_call(rig, "M109", {"S": "220", "T": "1"})
    assert calls == []  # the U1's M109 did not run: no wait over the tower
    assert rig.module.tracker.wait_after_depart == (1, 220.0)
    t1(rig)
    sent = rig.sent
    unload = next(i for i, ln in enumerate(sent) if ln.startswith("ACEK_SWAP_UNLOAD HEAD=0 "))
    assert unload < sent.index("M109 S220 T1 A0") < sent.index(U1_T1)
    assert rig.module.tracker.wait_after_depart is None
    assert DEPARTS in rig.gcode.lines


def test_the_files_wait_runs_as_sent_when_no_head_departs(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    rig.print_stats.current_layer = 0  # no departure at layer 0
    assert temp_call(rig, "M109", {"S": "220", "T": "1"}) == [{"S": "220", "T": "1"}]
    assert rig.module.tracker.wait_after_depart is None


def test_a_wait_for_a_tool_that_is_not_the_next_t_runs_as_sent(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    assert temp_call(rig, "M109", {"S": "220", "T": "0"}) != []  # the active head's own
    assert rig.module.tracker.wait_after_depart is None


def test_a_deferred_wait_is_dropped_at_the_end_of_a_print(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    temp_call(rig, "M109", {"S": "220", "T": "1"})
    rig.module.tracker.reset_print()
    assert rig.module.tracker.wait_after_depart is None


def test_a_failed_deferred_wait_is_said_and_the_t_goes_on(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    temp_call(rig, "M109", {"S": "220", "T": "1"})
    fail_on(rig, "M109 S220 T1 A0")
    assert t1(rig) == [{}]
    assert "ace2k_u1: T1's wait after the departure: heater fault" in rig.gcode.lines
