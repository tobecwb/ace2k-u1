"""The Unload of the U1's screen, cold when the lane in the head is free (ace2k_u1_screen_unload).

A lane that the U1 has not loaded is judged as ACEK_EJECT judges it: free (or the head empty), the
unit takes it back to its bay with no home, pick or heating, and the screen reads the unload's
steps through to unload_finish; held, the probe's travel is fed back and the U1's own unload runs.
A loaded lane, a print, a swap, and everything ACEK_EJECT refuses get the U1's own unload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ace2k_u1_screen_unload
import pytest
from test_eject import LANE, last_start, setup

if TYPE_CHECKING:
    from ace2k_u1_adapter import Adapter
    from ace2k_u1_common import Hooked
    from fakes import FakeAce2k, FakeFeed, FakeMove, FakeReactor

CH = 1  # lane 1 feeds head 1, the left feed's channel 1


def rig(
    head: bool = True, state: str = "preload_finish"
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeAce2k, Hooked]:
    adapter, reactor, left, ace = setup(head=head)
    left.channel_state[CH] = state
    # The wait of the prepare pauses the reactor: the eject's timer runs then, as in Klipper.
    reactor.on_pause.append(lambda _now: reactor.run_timers())
    h = next(x for x in adapter.hooked if x.lane == LANE)
    return adapter, reactor, left, ace, h


def probe_ends(ace: FakeAce2k, filament_mm: float, at_rest: bool) -> None:
    """Every roll back of the probe ends done, with the plunger at rest (free) or moved (held);
    a feed back ends blocked."""

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            ace.lanes[LANE].update(rest=at_rest, pushed=not at_rest)
            move.event = {
                "kind": "done",
                "mode": "rolling_back",
                "motor_mm": -10.0,
                "filament_mm": filament_mm,
            }
        elif move.mode == "feed":
            move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 3.0, "filament_mm": 3.0}

    ace.feed.on_start = on_start


def u1_calls(left: FakeFeed) -> list[tuple[object, ...]]:
    return [c for c in left.calls if c[0] == "do_feed"]


def states(left: FakeFeed) -> list[tuple[str, bool]]:
    return [(c[2], c[3]) for c in left.calls if c[0] == "state" and c[1] == CH]


def test_a_free_preloaded_lane_is_unloaded_cold() -> None:
    adapter, reactor, left, ace, h = rig()
    probe_ends(ace, -9.6, at_rest=True)
    left._do_feed(CH, "unload", "prepare")
    assert u1_calls(left) == []  # no home, no pick, no heating
    assert adapter.gcode.from_command == ["M104 S0 T0 A0"]  # the screen's preheat turned off
    assert states(left) == [("unload_prepare", True), ("unload_heat_finish", False)]
    assert [m for _, m, *_ in ace.feed.started] == ["rollback", "unload"]
    assert ace2k_u1_screen_unload.COLD_LINE.format(n="1") in adapter.gcode.lines
    assert left.channel_active is None and left.exception_code[CH] == 70
    left._do_feed(CH, "unload", "doing")
    assert u1_calls(left) == []
    assert states(left)[2:] == [("unload_doing", False), ("unload_finish", False)]
    assert adapter.gcode.from_command == ["M104 S0 T0 A0"] * 2  # off again, as the U1's doing
    reactor.run_timers()
    # The unit's unload goes on: not stopped, not started again, and no follow armed.
    assert ace.feed.stopped == [] and len(ace.feed.started) == 2 and not h.armed
    assert not h.cold_unload


def test_an_empty_head_is_unloaded_cold_with_no_probe() -> None:
    adapter, reactor, left, ace, h = rig(head=False)
    left._do_feed(CH, "unload", "prepare")
    assert [m for _, m, *_ in ace.feed.started] == ["unload"]
    assert states(left)[-1] == ("unload_heat_finish", False) and u1_calls(left) == []


def test_a_held_lane_is_fed_back_and_the_u1_unloads_it() -> None:
    adapter, reactor, left, ace, h = rig()
    probe_ends(ace, -3.0, at_rest=False)
    left._do_feed(CH, "unload", "prepare")
    assert [m for _, m, *_ in ace.feed.started] == ["rollback", "feed"]
    assert u1_calls(left) == [("do_feed", CH, "unload", "prepare", None)]
    assert adapter.gcode.scripts == []  # the eject does not start a U1 unload of its own
    assert not h.cold_unload and adapter.ejector.eject_jobs == {}
    left._do_feed(CH, "unload", "doing")
    assert u1_calls(left)[-1] == ("do_feed", CH, "unload", "doing", None)


def test_a_lane_loaded_by_the_u1_gets_its_own_unload() -> None:
    adapter, reactor, left, ace, h = rig(state="load_finish")
    left._do_feed(CH, "unload", "prepare")
    assert u1_calls(left) == [("do_feed", CH, "unload", "prepare", None)]
    assert ace.feed.started == [] and adapter.gcode.from_command == []


def test_what_the_eject_refuses_gets_the_u1_unload() -> None:
    adapter, reactor, left, ace, h = rig()
    ace.lanes[LANE]["insert"] = False  # no filament in the bay
    left._do_feed(CH, "unload", "prepare")
    assert u1_calls(left) == [("do_feed", CH, "unload", "prepare", None)]
    assert ace.feed.started == []


def test_a_head_that_a_swap_is_on_gets_the_u1_unload() -> None:
    adapter, reactor, left, ace, h = rig()
    adapter.swapping.add(h.head)
    left._do_feed(CH, "unload", "prepare")
    assert u1_calls(left) == [("do_feed", CH, "unload", "prepare", None)]


def test_a_failed_eject_gets_the_u1_unload() -> None:
    adapter, reactor, left, ace, h = rig()
    ace.feed.refuse = "error"  # the probe is refused: the eject fails with its line
    left._do_feed(CH, "unload", "prepare")
    assert any("eject failed" in ln for ln in adapter.gcode.lines)
    assert u1_calls(left) == [("do_feed", CH, "unload", "prepare", None)]


def test_a_doing_without_a_cold_prepare_is_the_u1s() -> None:
    adapter, reactor, left, ace, h = rig()
    left._do_feed(CH, "unload", "doing")
    assert u1_calls(left) == [("do_feed", CH, "unload", "doing", None)]


def test_a_doing_out_of_step_is_left_to_the_u1() -> None:
    adapter, reactor, left, ace, h = rig()
    probe_ends(ace, -9.6, at_rest=True)
    left._do_feed(CH, "unload", "prepare")
    left._set_channel_state(CH, "preload_finish")  # anything else moved the channel since
    left._do_feed(CH, "unload", "doing")
    assert u1_calls(left) == [("do_feed", CH, "unload", "doing", None)]
    assert not h.cold_unload


def test_a_cancel_after_a_cold_prepare_stops_the_lane() -> None:
    adapter, reactor, left, ace, h = rig()
    probe_ends(ace, -9.6, at_rest=True)
    left._do_feed(CH, "unload", "prepare")
    reactor.run_timers()
    left._set_channel_state(CH, "unload_fail", True)  # the U1's cancel
    assert ("stop", LANE) in ace.feed.log and not h.cold_unload


def test_the_whole_unload_in_one_call_is_the_u1s() -> None:
    adapter, reactor, left, ace, h = rig()
    left._do_feed(CH, "unload", None)
    assert u1_calls(left) == [("do_feed", CH, "unload", None, None)]
    assert last_start(ace) if ace.feed.started else True


def test_an_eject_that_never_decides_fails_the_unload() -> None:
    adapter, reactor, left, ace, h = rig()
    reactor.on_pause.clear()  # the eject's timer never runs: no decision ever comes
    with pytest.raises(RuntimeError, match="its eject did not decide"):
        left._do_feed(CH, "unload", "prepare")
    assert any("eject failed: no decision after 120 s" in ln for ln in adapter.gcode.lines)
    assert u1_calls(left) == [] and states(left)[-1] == ("unload_fail", False)
    assert left.channel_error[CH] == "general" and left.exception_code[CH] == 70
    assert adapter.ejector.eject_jobs == {} and not h.cold_unload


def test_a_unit_unload_that_does_not_start_fails_the_unload() -> None:
    adapter, reactor, left, ace, h = rig(head=False)
    ace.feed.refuse = "error"  # the unit refuses the unload itself
    with pytest.raises(RuntimeError, match="the unit's unload did not start"):
        left._do_feed(CH, "unload", "prepare")
    assert any("eject not started" in ln for ln in adapter.gcode.lines)
    assert u1_calls(left) == [] and states(left)[-1] == ("unload_fail", False)


def test_a_unit_unload_start_that_never_ends_fails_the_unload() -> None:
    adapter, reactor, left, ace, h = rig(head=False)
    # The start of the unit's unload is still being tried, and nothing started.
    adapter.ejector.unload_starting = lambda h: True  # type: ignore[method-assign]
    adapter.ejector.unload_started = lambda h: False  # type: ignore[method-assign]
    start = reactor.now
    with pytest.raises(RuntimeError, match="the unit's unload did not start"):
        left._do_feed(CH, "unload", "prepare")
    assert reactor.now - start >= ace2k_u1_screen_unload.START_WAIT_S
    assert u1_calls(left) == [] and states(left)[-1] == ("unload_fail", False)


def test_a_busy_unit_unload_is_waited_for() -> None:
    adapter, reactor, left, ace, h = rig(head=False)
    ace.feed.refusals = ["busy", "busy"]  # the stopped follow winding down
    left._do_feed(CH, "unload", "prepare")
    assert states(left)[-1] == ("unload_heat_finish", False)
    assert [m for _, m, *_ in ace.feed.started] == ["unload"]


def test_a_cold_prepare_whose_doing_never_came_is_forgotten_by_the_next() -> None:
    adapter, reactor, left, ace, h = rig(head=False)
    left._do_feed(CH, "unload", "prepare")
    assert h.cold_unload
    left._set_channel_state(CH, "load_finish")  # the head loaded by the U1 since
    left._do_feed(CH, "unload", "prepare")
    assert not h.cold_unload
    left._do_feed(CH, "unload", "doing")
    assert u1_calls(left)[-1] == ("do_feed", CH, "unload", "doing", None)


def test_the_unit_unload_of_a_cold_prepare_keeps_its_safety_net() -> None:
    adapter, reactor, left, ace, h = rig(head=False)
    left._do_feed(CH, "unload", "prepare")
    # Before the screen's doing the channel reads unload_heat_finish, which is not a rest state.
    # The cold unload is the eject's own, so it does not count as a flow of the U1.
    assert not adapter.ejector.u1_flow(h)
