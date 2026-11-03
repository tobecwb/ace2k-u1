"""The U1's extrude retry: when its check says the filament did not extrude, the U1 pulses its own
side feed motor forward (run_one_cycle) in load_extruding. On a hooked channel that motor is not
in the filament's path: the pulse becomes a short feed of the lane, between a stop and a re-arm of
its follow. Every other pulse reaches the U1's motor unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import ace2k_u1
import ace2k_u1_common
import ace2k_u1_hooks
import fakes
import pytest
from test_check import Config, Printer
from test_check import Gcode as CheckGcode
from test_presence import build

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ace2k_u1_adapter import Adapter
    from ace2k_u1_common import Hooked
    from fakes import FakeAce2k, FakeFeed, FakeReactor

DIR_A, DIR_B = 1, 2  # the U1's FEED_MOTOR_DIR_A / _B; channel 1 (index 0) feeds with A


class Gcode:
    def __init__(self) -> None:
        self.lines = []

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)


def setup(
    lanes: Iterable[int] = (1, 2, 3, 4), ends: str | None = "done"
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeFeed, FakeAce2k]:
    """The left module's channel 2 (index 1, lane 1) in load_extruding, its follow armed, its
    motor pulses recorded. A feed started on the lane ends with ends (None: never) at the next
    pause."""
    adapter, reactor, left, right, ace = build(lanes)
    adapter.gcode = Gcode()
    assert adapter.hook_all() == []
    left.channel_active = 1
    left._set_channel_state(1, "load_extruding")
    left.motor.calls.clear()  # the hook replaced run_one_cycle on the instance; the fake records

    def end_feeds(now: float) -> None:
        for _lane, mode, _length, _speed, move in ace.feed.started:
            if mode == "feed" and move.event is None and ends is not None:
                move.event = {"kind": ends, "mode": "feeding", "seq": move.seq}

    reactor.on_pause = [end_feeds]
    return adapter, reactor, left, right, ace


def originals(feed: FakeFeed) -> list[tuple[int, float, float]]:
    return feed.motor.calls


def test_the_retry_pulse_feeds_the_lane_between_a_stop_and_a_rearm() -> None:
    adapter, reactor, left, right, ace = setup()
    assert ace.feed.log == [("start", 0, "assist_both", 0.0)]
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert ace.feed.log == [
        ("start", 0, "assist_both", 0.0),
        ("stop", 0),
        ("start", 0, "feed", 10.0),
        ("start", 0, "assist_both", 0.0),
    ]
    assert ace.feed.started[1][3] == 30.0  # at feed_speed
    assert originals(left) == []
    assert next(h for h in adapter.hooked if h.lane == 0).armed is True
    assert adapter.gcode.lines == []


def test_retry_push_mm_sets_the_feed_length() -> None:
    adapter, reactor, left, right, ace = setup()
    adapter.retry_push_mm = 25.0
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert ("start", 0, "feed", 25.0) in ace.feed.log


def test_a_non_hooked_channel_reaches_the_motor() -> None:
    adapter, reactor, left, right, ace = setup(lanes=(2, 3, 4))  # lane 1 (left channel 2) not
    left.channel_state[1] = "load_extruding"
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert originals(left) == [(DIR_B, 0.85, 0.1)]
    assert ace.feed.log == []


def test_the_hang_neutral_pulse_reaches_the_motor() -> None:
    # the reversed direction: channel 2 hangs neutral with A
    adapter, reactor, left, right, ace = setup()
    left.motor.run_one_cycle(DIR_A, 0.5, 0.2)
    assert originals(left) == [(DIR_A, 0.5, 0.2)]
    assert ace.feed.log == [("start", 0, "assist_both", 0.0)]


def test_a_pulse_in_another_state_reaches_the_motor() -> None:
    adapter, reactor, left, right, ace = setup()
    for state in ("load_feeding", "load_flushing", "manual_sta_extruding", "unload_doing"):
        left.channel_state[1] = state
        left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert len(originals(left)) == 4
    assert ace.feed.log == [("start", 0, "assist_both", 0.0)]


def test_a_pulse_with_no_active_channel_reaches_the_motor() -> None:
    # FEED_MOTOR_ONE_CYCLE from the console, nothing running
    adapter, reactor, left, right, ace = setup()
    left.channel_active = None
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert originals(left) == [(DIR_B, 0.85, 0.1)]


def test_the_other_channel_of_the_module_is_told_apart() -> None:
    # left channel 1 (index 0, lane 2, forward A) extruding: its own pulse feeds lane 2
    adapter, reactor, left, right, ace = setup()
    left.channel_active = 0
    left._set_channel_state(0, "load_extruding")
    left.motor.run_one_cycle(DIR_A, 0.85, 0.1)
    assert ("start", 1, "feed", 10.0) in ace.feed.log
    assert ("stop", 1) in ace.feed.log
    assert ("stop", 0) not in ace.feed.log
    assert originals(left) == []


def test_a_refused_feed_is_a_line_and_the_follow_is_rearmed() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refusals = ["no_filament"]
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert any("retry push not started" in line for line in adapter.gcode.lines)
    assert ace.feed.log[-1] == ("start", 0, "assist_both", 0.0)
    assert ace.feed.log[1] == ("stop", 0)


def test_a_busy_feed_is_retried_while_the_follow_winds_down() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refusals = ["busy", "busy"]
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert ace.feed.log[2:] == [("start", 0, "feed", 10.0), ("start", 0, "assist_both", 0.0)]
    assert adapter.gcode.lines == []


def test_a_feed_busy_past_the_cap_is_a_line() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refusals = ["busy"] * 1000
    start = reactor.now
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert reactor.now - start <= 2 * ace2k_u1_common.NUDGE_BUSY_S + 1.0
    assert any("retry push not started" in line for line in adapter.gcode.lines)


def test_a_feed_that_never_ends_is_stopped_and_the_follow_rearmed() -> None:
    adapter, reactor, left, right, ace = setup(ends=None)
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert any("retry push timed out" in line for line in adapter.gcode.lines)
    assert ace.feed.log[2:] == [
        ("start", 0, "feed", 10.0),
        ("stop", 0),
        ("start", 0, "assist_both", 0.0),
    ]


def test_a_feed_ending_in_error_is_a_line_and_the_follow_rearmed() -> None:
    adapter, reactor, left, right, ace = setup(ends="stuck")
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert any("retry push ended stuck" in line for line in adapter.gcode.lines)
    assert ace.feed.log[-1] == ("start", 0, "assist_both", 0.0)


def test_a_busy_rearm_is_retried() -> None:
    adapter, reactor, left, right, ace = setup()
    # the feed starts, then the re-arm is refused busy twice (the feed winding down)
    ace.feed.refusals = [None, "busy", "busy"]
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert ace.feed.log[-1] == ("start", 0, "assist_both", 0.0)
    assert adapter.gcode.lines == []


def test_a_stale_following_mode_does_not_pass_for_a_rearm() -> None:
    # the last report still says following (the follow the nudge stopped): the busy re-arm is
    # retried, not taken for an armed follow
    adapter, reactor, left, right, ace = setup()
    ace.lanes[0]["mode"] = "following"
    ace.feed.refusals = [None, "busy"]
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert ace.feed.log[-1] == ("start", 0, "assist_both", 0.0)
    assert next(h for h in adapter.hooked if h.lane == 0).armed is True
    assert adapter.gcode.lines == []


def test_a_rearm_busy_past_the_wait_is_a_line() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.lanes[0]["mode"] = "following"
    ace.feed.refusals = [None] + ["busy"] * 1000
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert any("follow not armed" in line for line in adapter.gcode.lines)
    assert next(h for h in adapter.hooked if h.lane == 0).armed is False


def test_a_rearm_refused_for_good_is_a_line() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refusals = [None, "in_error"]
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert any("follow not armed" in line for line in adapter.gcode.lines)


def test_nothing_raises_into_the_u1s_loop() -> None:
    adapter, reactor, left, right, ace = setup()

    def boom(*a: object) -> NoReturn:
        raise RuntimeError("no link")

    ace.feed.start_move = boom
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)  # no exception
    assert any("retry push not started" in line for line in adapter.gcode.lines)
    assert any("follow not armed" in line for line in adapter.gcode.lines)


def test_a_failed_stop_skips_the_retry_and_leaves_the_follow() -> None:
    adapter, reactor, left, right, ace = setup()

    def boom(*a: object) -> NoReturn:
        raise RuntimeError("no link")

    ace.feed.stop = boom
    start = reactor.now
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)  # no exception
    assert reactor.now == start  # back to the U1 at once: no wait, no retry
    assert ace.feed.log == [("start", 0, "assist_both", 0.0)]  # no feed, no re-arm
    assert len(adapter.gcode.lines) == 1 and "retry push skipped" in adapter.gcode.lines[0]
    assert next(h for h in adapter.hooked if h.lane == 0).armed is True


def test_the_status_lookup_failing_still_rearms(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, reactor, left, right, ace = setup()
    calls = []

    def broken(adapter: Adapter, h: Hooked) -> NoReturn:
        calls.append(h.lane)
        raise KeyError("lanes")

    monkeypatch.setattr(ace2k_u1_hooks, "_feed_and_wait", broken)
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert calls == [0]
    assert ace.feed.log[-1] == ("start", 0, "assist_both", 0.0)
    assert any("retry push failed" in line for line in adapter.gcode.lines)


def test_a_motor_without_run_one_cycle_is_not_hooked() -> None:
    adapter, reactor, left, right, ace = build()
    left.motor = object()
    problems = adapter.hook_all()
    assert any("run_one_cycle" in p for p in problems)


def test_retry_push_mm_defaults_to_ten() -> None:
    reactor = fakes.FakeReactor()
    printer = Printer(reactor, {"gcode": CheckGcode(), "ace2k": fakes.FakeAce2k()}, [])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    assert u1.retry_push_mm == 10.0


def test_a_unit_with_host_interface_17_bounds_the_retry_push_by_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry push's wait is ace2k's budget of the move (feed.move_wait_s)."""
    monkeypatch.setattr(fakes.FakeFeedApi, "API_VERSION", 17)
    adapter, reactor, left, right, ace = setup()
    left.motor.run_one_cycle(DIR_B, 0.85, 0.1)
    assert ace.feed.waits == [(0, 10.0, 30.0)]
