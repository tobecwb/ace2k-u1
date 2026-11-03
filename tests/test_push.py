"""The push of a lane to the head's filament sensor: its outcomes, the wait for the unit's own
load, and the U1's preload and load running through it."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import ace2k_u1
import ace2k_u1_common
import ace2k_u1_push
import fakes
import pytest
from test_check import Config, Printer
from test_check import Gcode as CheckGcode
from test_presence import Console, build

if TYPE_CHECKING:
    from collections.abc import Callable

    from ace2k_u1_adapter import Adapter
    from ace2k_u1_common import Hooked
    from fakes import FakeAce2k, FakeMove, FakeReactor, HeadSensor

ERR_EVENT = {"kind": "stuck", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0, "seq": 1}


def sensor_after(reactor: FakeReactor, sensor: HeadSensor, seconds: float) -> None:
    start = reactor.now
    reactor.on_pause = [lambda now: setattr(sensor, "detected", now - start >= seconds)]


def at(reactor: FakeReactor, seconds: float, action: Callable[[], object]) -> None:
    """Run action once, the first pause at or after seconds from now."""
    start = reactor.now
    fired = []

    def hook(now: float) -> None:
        if not fired and now - start >= seconds:
            fired.append(now)
            action()

    reactor.on_pause = getattr(reactor, "on_pause", []) + [hook]


def lane1(adapter: Adapter) -> Hooked:
    adapter.hook_all()
    return next(x for x in adapter.hooked if x.lane == 0)


def test_push_stops_at_the_head_sensor() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    sensor = h.ff.runout_sensor[h.ch]
    sensor_after(reactor, sensor, 1.0)
    assert adapter.push_runner.push_to_head(h) is None
    lane, mode, length, speed, move = ace.feed.started[0]
    assert (lane, mode, length, speed) == (0, "feed", 2000.0, 30.0)
    assert ace.feed.stopped == [0]


def test_push_with_the_sensor_already_set_moves_nothing() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    h.ff.runout_sensor[h.ch].detected = True
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started == []


@pytest.mark.parametrize(
    "kind,error",
    [
        ("stuck", "wheel_speed"),
        ("tangled", "wheel_speed"),
        ("motor_stalled", "motor_speed"),
        ("runout", "no_filament"),
        ("timeout", "timeout"),
        ("stopped_link", "timeout"),
        ("done", "distance"),
        ("blocked", "distance"),
        ("something_new", "timeout"),
    ],
)
def test_push_maps_the_final_event(kind: str, error: str) -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    reactor.on_pause = [
        lambda now: setattr(ace.feed.started[0][4], "event", dict(ERR_EVENT, kind=kind))
    ]
    assert adapter.push_runner.push_to_head(h) == error


@pytest.mark.parametrize(
    "reason,error",
    [("no_filament", "no_filament"), ("in_error", "motor_speed"), ("no_link", "motor_speed")],
)
def test_push_maps_a_refusal(reason: str, error: str) -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.feed.refuse = reason
    assert adapter.push_runner.push_to_head(h) == error
    assert ace.feed.attempts == 1


def test_push_times_out_and_stops() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    start = reactor.now
    assert adapter.push_runner.push_to_head(h) == "timeout"
    assert ace.feed.stopped == [0]
    assert reactor.now - start == pytest.approx(2000.0 / 30.0 * 2.0 + 5.0, abs=0.1)


def test_push_waits_for_the_units_load_then_pushes() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0].update(insert=True, mode="loading")
    at(reactor, 10.0, lambda: ace.lanes[0].update(mode="idle"))
    at(reactor, 11.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    start = reactor.now
    assert adapter.push_runner.push_to_head(h) is None
    assert len(ace.feed.started) == 1
    assert ace.feed.stopped == [0]
    assert reactor.now - start >= 10.0


def test_push_waiting_on_a_load_that_ends_in_error_is_motor_speed() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0].update(insert=True, mode="loading")
    at(reactor, 5.0, lambda: ace.lanes[0].update(mode="error"))
    assert adapter.push_runner.push_to_head(h) == "motor_speed"
    assert ace.feed.started == []


def test_push_waiting_on_a_load_whose_filament_leaves_is_no_filament() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0].update(insert=True, mode="loading")
    at(reactor, 5.0, lambda: ace.lanes[0].update(insert=False))
    assert adapter.push_runner.push_to_head(h) == "no_filament"
    assert ace.feed.started == []


def test_push_waiting_on_a_load_gives_up_at_the_cap() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0].update(insert=True, mode="loading")
    start = reactor.now
    assert adapter.push_runner.push_to_head(h) == "timeout"
    assert ace.feed.started == []
    assert ace.feed.stopped == [0]
    assert reactor.now - start == pytest.approx(120.0, abs=0.1)


def test_push_refused_busy_once_retries_and_completes() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0]["insert"] = True
    ace.feed.refusals = ["busy"]
    start = reactor.now
    retried = []
    at(reactor, 0.9, lambda: retried.append(ace.feed.attempts))
    at(reactor, 1.5, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert retried == [1]  # no second start before one report period (1 s)
    assert ace.feed.attempts == 2
    assert reactor.now - start >= 1.5
    assert len(ace.feed.started) == 1
    assert ace.feed.stopped == [0]


def test_push_refused_busy_until_the_cap_times_out() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0]["insert"] = True
    ace.feed.refuse = "busy"
    start = reactor.now
    assert adapter.push_runner.push_to_head(h) == "timeout"
    assert ace.feed.started == []
    assert ace.feed.stopped == [0]
    assert reactor.now - start == pytest.approx(120.0, abs=0.1)
    assert 115 <= ace.feed.attempts <= 121  # one start per second, not one per local poll


def test_load_success_returns() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    sensor_after(reactor, h.ff.runout_sensor[h.ch], 0.5)
    assert left._put_into_drive(1) is None
    assert ("put_into_drive", 1) not in left.calls


def test_load_failure_sets_the_code_and_raises() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.feed.refuse = "no_filament"
    with pytest.raises(RuntimeError, match="no_filament"):
        left._put_into_drive(1)  # lane 1
    assert left.channel_error[1] == "no_filament" and left.exception_code[1] == 33


def test_a_load_push_that_ends_blocked_is_the_u1s_failure_35() -> None:
    # a head load must not hang: what cannot move ahead of it fails the load as before
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    adapter.gcode = Console()
    reactor.on_pause = [
        lambda now: setattr(ace.feed.started[0][4], "event", dict(ERR_EVENT, kind="blocked"))
    ]
    with pytest.raises(RuntimeError, match="distance"):
        left._put_into_drive(1)
    assert left.channel_error[1] == "distance" and left.exception_code[1] == 35
    assert adapter.gcode.lines == []


def test_a_load_pushes_a_loose_leftover_ahead_to_the_head() -> None:
    # a loose piece in the tube moves: the push runs on, the piece reaches the head's sensor
    # first and the load succeeds — no blocked, no failure
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    sensor_after(reactor, left.runout_sensor[1], 3.0)
    assert left._put_into_drive(1) is None
    assert ace.feed.started[0][:3] == (0, "feed", 2000.0) and ace.feed.stopped == [0]
    assert left.channel_error[1] == "ok"


def test_load_on_an_unhooked_channel_calls_the_original() -> None:
    adapter, reactor, left, right, ace = build(lanes=(1,))
    adapter.hook_all()
    left._put_into_drive(0)  # lane 2, not hooked
    assert ("put_into_drive", 0) in left.calls
    assert ace.feed.started == []


def test_preload_success_walks_the_u1_states() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    sensor_after(reactor, left.runout_sensor[1], 0.5)
    left._do_feed(1, "preload")
    states = [c[2] for c in left.calls if c[0] == "state" and c[1] == 1]
    assert states[-3:] == ["preload_prepare", "preload_feeding", "preload_finish"]
    assert ("state", 1, "preload_prepare", True) in left.calls
    assert left.channel_active is None
    assert left.exception_code[1] == 10 and left.channel_error[1] == "ok"


def test_preload_without_a_filament_is_13() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    left._do_feed(1, "preload")
    assert left.exception_code[1] == 13 and left.channel_error[1] == "no_filament"
    assert left.channel_state[1] == "preload_fail"
    assert left.exception_manager.raised[-1]["code"] == 13
    assert ace.feed.started == []
    assert left.channel_active is None


def test_preload_residual_filament_is_15_and_async_notice() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    left.runout_sensor[1].detected = True
    left._do_feed(1, "preload")
    assert left.exception_code[1] == 15
    assert left.channel_state[1] == "preload_fail"
    assert left.channel_error_state[1] == "preload_fail"
    raised = left.exception_manager.raised[-1]
    assert raised["code"] == 15 and raised["level"] == 1 and raised["oneshot"] == 1
    assert raised["id"] == 525 and raised["index"] == 0


@pytest.mark.parametrize(
    "kind,code",
    [("stuck", 12), ("motor_stalled", 11), ("runout", 13), ("timeout", 14), ("done", 14)],
)
def test_preload_push_failure_is_10_plus_n(kind: str, code: int) -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    reactor.on_pause = [
        lambda now: setattr(ace.feed.started[0][4], "event", dict(ERR_EVENT, kind=kind))
    ]
    left._do_feed(1, "preload")
    assert left.exception_code[1] == code
    assert left.channel_state[1] == "preload_fail"
    assert left.exception_manager.raised[-1]["code"] == code
    assert left.channel_active is None


def test_a_preload_push_that_ends_blocked_waits_at_wait_insert() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    adapter.gcode = Console()
    ace.lanes[0]["insert"] = True
    reactor.on_pause = [
        lambda now: setattr(ace.feed.started[0][4], "event", dict(ERR_EVENT, kind="blocked"))
    ]
    left._do_feed(1, "preload")
    states = [c[2:] for c in left.calls if c[0] == "state" and c[1] == 1]
    assert states[-3:] == [
        ("preload_prepare", True),
        ("preload_feeding", False),
        ("wait_insert", True),
    ]
    assert ("preload_fail", False) not in states
    assert left.exception_manager.raised == []
    assert left.channel_error[1] == "ok" and left.channel_error_state[1] == "none"
    assert left.channel_active is None
    assert adapter.gcode.lines == [BLOCKED_IDLE]


BLOCKED_IDLE = (
    "ace2k_u1: lane 1: the filament does not move — something in the tube ahead or at the spool;"
    " clear it, then pull the filament out and push it in again"
)
BLOCKED_PRINTING = (
    "ace2k_u1: lane 1: the filament does not move — something in the tube ahead or at the spool;"
    " it waits and goes to the head when the head's piece has run out"
)


def test_a_blocked_preloads_line_follows_the_print() -> None:
    for state, line in (
        ("printing", BLOCKED_PRINTING),
        ("paused", BLOCKED_PRINTING),
        ("standby", BLOCKED_IDLE),
        ("complete", BLOCKED_IDLE),
    ):
        adapter, reactor, left, right, ace = build()
        adapter.hook_all()
        adapter.gcode = Console()
        adapter.print_stats = fakes.FakePrintStats(state)
        ace.lanes[0]["insert"] = True
        reactor.on_pause = [
            lambda now, ace=ace: setattr(
                ace.feed.started[0][4], "event", dict(ERR_EVENT, kind="blocked")
            )
        ]
        left._do_feed(1, "preload")
        assert left.channel_state[1] == "wait_insert", state
        assert adapter.gcode.lines == [line], state


def test_a_preload_after_a_blocked_one_fails_normally() -> None:
    # the blocked mark is the last push's only: a later preload's own failure is reported
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    reactor.on_pause = [
        lambda now: setattr(ace.feed.started[-1][4], "event", dict(ERR_EVENT, kind="blocked"))
    ]
    left._do_feed(1, "preload")
    assert left.channel_state[1] == "wait_insert"
    reactor.on_pause = [
        lambda now: setattr(ace.feed.started[-1][4], "event", dict(ERR_EVENT, kind="stuck"))
    ]
    left._do_feed(1, "preload")
    assert left.channel_state[1] == "preload_fail" and left.exception_code[1] == 12
    left.runout_sensor[1].detected = True  # residual: no push, no stale blocked
    left._do_feed(1, "preload")
    assert left.exception_code[1] == 15


def test_preload_waits_for_the_channel_lock() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    left.channel_active = 0
    at(reactor, 1.0, lambda: setattr(left, "channel_active", None))
    at(reactor, 2.0, lambda: setattr(left.runout_sensor[1], "detected", True))
    left._do_feed(1, "preload")
    assert left.channel_state[1] == "preload_finish"
    assert left.channel_active is None


def test_a_preload_gives_up_when_the_u1_keeps_its_channel_busy() -> None:
    """The U1's lock held past CHANNEL_WAIT_S: one console line, nothing pushed, the
    channel's state left as it was, no exception into the U1."""
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    adapter.gcode = Console()
    ace.lanes[0]["insert"] = True
    left.channel_active = 0  # the other channel holds the U1's lock and never lets it go
    state, error, code = left.channel_state[1], left.channel_error[1], left.exception_code[1]
    start = reactor.now
    left._do_feed(1, "preload")
    assert 300.0 <= reactor.now - start < 301.0
    assert adapter.gcode.lines == [
        "ace2k_u1: lane 1: the U1 kept its channel busy for 300 s; the preload given up"
    ]
    assert ace.feed.started == [] and left.channel_active == 0
    assert (left.channel_state[1], left.channel_error[1], left.exception_code[1]) == (
        state,
        error,
        code,
    )


def test_other_actions_reach_the_original() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    left._do_feed(1, "load")
    assert ("do_feed", 1, "load", None, None) in left.calls


def test_preload_on_an_unhooked_channel_reaches_the_original() -> None:
    adapter, reactor, left, right, ace = build(lanes=(1,))
    adapter.hook_all()
    left._do_feed(0, "preload")
    assert ("do_feed", 0, "preload", None, None) in left.calls


def test_a_successful_preload_clears_a_stale_error_state() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    left._do_feed(1, "preload")  # no filament: fails
    assert left.channel_error_state[1] == "preload_fail"
    ace.lanes[0]["insert"] = True
    sensor_after(reactor, left.runout_sensor[1], 0.5)
    left._do_feed(1, "preload")
    assert left.channel_state[1] == "preload_finish"
    assert left.channel_error_state[1] == "none"


def test_an_unexpected_error_in_the_preload_is_a_general_failure() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    sensor_after(reactor, left.runout_sensor[1], 0.5)

    def broken_stop(lane: int) -> NoReturn:
        raise RuntimeError("link gone")

    ace.feed.stop = broken_stop
    left._do_feed(1, "preload")
    assert left.channel_error[1] == "general" and left.exception_code[1] == 10
    assert left.channel_state[1] == "preload_fail"
    assert left.channel_error_state[1] == "preload_fail"
    raised = left.exception_manager.raised[-1]
    assert raised["code"] == 10 and raised["message"] == "preload fail: general"
    assert left.channel_active is None


def break_head_sensor_after(reactor: FakeReactor, sensor: HeadSensor, seconds: float) -> None:
    """The head sensor's reading raises once seconds have passed (mid-poll)."""
    start = reactor.now
    real = sensor.get_status

    def get_status(eventtime: float | None) -> dict[str, bool]:
        if reactor.now - start >= seconds:
            raise OSError("sensor read failed")
        return real(eventtime)

    sensor.get_status = get_status


def test_an_exception_mid_push_stops_the_lane_and_reaches_the_caller() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    break_head_sensor_after(reactor, h.ff.runout_sensor[h.ch], 1.0)
    with pytest.raises(OSError):
        adapter.push_runner.push_to_head(h)
    assert len(ace.feed.started) == 1
    assert ace.feed.stopped == [0]


def test_a_failing_stop_does_not_mask_the_original_exception() -> None:
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    break_head_sensor_after(reactor, h.ff.runout_sensor[h.ch], 1.0)

    def broken_stop(lane: int) -> NoReturn:
        raise RuntimeError("link gone")

    ace.feed.stop = broken_stop
    with pytest.raises(OSError):
        adapter.push_runner.push_to_head(h)


def test_an_exception_mid_preload_push_stops_and_is_a_general_failure() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    break_head_sensor_after(reactor, left.runout_sensor[1], 1.0)
    left._do_feed(1, "preload")
    assert ace.feed.stopped == [0, 0]  # the push's own stop, then the preload_fail state's
    assert left.channel_error[1] == "general" and left.exception_code[1] == 10
    assert left.channel_state[1] == "preload_fail"
    assert left.exception_manager.raised[-1]["code"] == 10
    assert left.channel_active is None


def test_an_exception_mid_load_push_stops_and_raises() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    break_head_sensor_after(reactor, left.runout_sensor[1], 1.0)
    with pytest.raises(OSError):
        left._put_into_drive(1)
    assert ace.feed.stopped == [0]


def chunks(ace: FakeAce2k) -> list[tuple[int, str, float]]:
    return [(lane, mode, length) for lane, mode, length, speed, move in ace.feed.started]


def test_a_budget_over_the_units_longest_move_is_pushed_in_moves() -> None:
    """The path from the bay to the head passes 2 000 mm, the unit's
    longest move. head_budget_mm is no longer capped by it: a move that ends done short of the
    sensor, having carried the filament, goes on with the rest."""
    adapter, reactor, left, right, ace = build(head_budget_mm=3000.0)
    assert adapter.head_budget_mm == 3000.0
    h = lane1(adapter)
    sensor = h.ff.runout_sensor[h.ch]

    def on_pause(now: float) -> None:
        started = ace.feed.started
        if len(started) == 1 and started[0][4].event is None:
            started[0][4].event = {"kind": "done", "seq": 1, "filament_mm": 1990.0}
        elif len(started) == 2:
            sensor.detected = True

    reactor.on_pause = [on_pause]
    assert adapter.push_runner.push_to_head(h) is None
    assert chunks(ace) == [(0, "feed", 2000.0), (0, "feed", 1010.0)]  # what the filament went


def test_a_free_tip_push_over_the_longest_move_goes_on_through_insists() -> None:
    """_insists is not a push again after a block. It runs the next move of a budget over the
    unit's longest move (2 000 mm), after a move that ended done short of the head's sensor. Every
    move of the push has a free tip."""
    adapter, reactor, left, right, ace = build(head_budget_mm=3000.0)
    h = lane1(adapter)
    sensor = h.ff.runout_sensor[h.ch]

    def on_pause(now: float) -> None:
        started = ace.feed.started
        if len(started) == 1 and started[0][4].event is None:
            started[0][4].event = {"kind": "done", "seq": 1, "filament_mm": 1990.0}
        elif len(started) == 2:
            sensor.detected = True

    reactor.on_pause = [on_pause]
    assert adapter.push_runner.push_to_head(h) is None
    assert [free for _lane, mode, free in ace.feed.free_tips if mode == "feed"] == [True, True]


def test_at_the_default_budget_a_done_short_of_the_sensor_ends_the_push() -> None:
    """head_budget_mm defaults to 2 000 mm, the unit's longest move, so _insists never goes on."""
    adapter, reactor, left, right, ace = build()
    assert adapter.head_budget_mm == 2000.0
    h = lane1(adapter)
    reactor.on_pause = [
        lambda now: setattr(
            ace.feed.started[0][4], "event", {"kind": "done", "seq": 1, "filament_mm": 1990.0}
        )
    ]
    assert adapter.push_runner.push_to_head(h) == "distance"
    assert chunks(ace) == [(0, "feed", 2000.0)]


def test_a_move_that_slipped_ends_the_push_in_moves() -> None:
    adapter, reactor, left, right, ace = build(head_budget_mm=3000.0)
    h = lane1(adapter)
    reactor.on_pause = [
        lambda now: setattr(
            ace.feed.started[0][4], "event", {"kind": "done", "seq": 1, "filament_mm": 300.0}
        )
    ]
    assert adapter.push_runner.push_to_head(h) == "distance"
    assert chunks(ace) == [(0, "feed", 2000.0)]


def test_the_budget_spent_in_moves_is_the_distance_error() -> None:
    adapter, reactor, left, right, ace = build(head_budget_mm=3000.0)
    h = lane1(adapter)

    def on_pause(now: float) -> None:
        for i, (_, _, length, _, move) in enumerate(ace.feed.started):
            if move.event is None:
                move.event = {"kind": "done", "seq": i + 1, "filament_mm": length}

    reactor.on_pause = [on_pause]
    assert adapter.push_runner.push_to_head(h) == "distance"
    assert chunks(ace) == [(0, "feed", 2000.0), (0, "feed", 1000.0)]
    assert h.push_mm == 3000.0  # the whole push, for the caller's roll back


def test_a_move_with_no_distance_ends_the_push_in_moves() -> None:
    """A done end with no distance is not taken as carried: a loose tube spends the budget once."""
    adapter, reactor, left, right, ace = build(head_budget_mm=3000.0)
    h = lane1(adapter)
    reactor.on_pause = [
        lambda now: setattr(ace.feed.started[0][4], "event", {"kind": "done", "seq": 1})
    ]
    assert adapter.push_runner.push_to_head(h) == "distance"
    assert chunks(ace) == [(0, "feed", 2000.0)]


def test_a_push_in_moves_counts_its_ended_moves_and_how_many_ran() -> None:
    adapter, reactor, left, right, ace = build(head_budget_mm=3000.0)
    h = lane1(adapter)
    sensor = h.ff.runout_sensor[h.ch]

    def on_pause(now: float) -> None:
        started = ace.feed.started
        if len(started) == 1 and started[0][4].event is None:
            started[0][4].event = {"kind": "done", "seq": 1, "filament_mm": 1990.0}
        elif len(started) == 2:
            sensor.detected = True

    reactor.on_pause = [on_pause]
    assert adapter.push_runner.push_to_head(h) is None
    assert (h.push_mm, h.push_moves, h.push_known) == (1990.0, 2, True)


def test_a_later_move_blocked_with_no_distance_leaves_the_tip_unknown() -> None:
    adapter, reactor, left, right, ace = build(head_budget_mm=3000.0)
    h = lane1(adapter)

    def on_pause(now: float) -> None:
        started = ace.feed.started
        if len(started) == 1 and started[0][4].event is None:
            started[0][4].event = {"kind": "done", "seq": 1, "filament_mm": 2000.0}
        elif len(started) == 2 and started[1][4].event is None:
            started[1][4].event = {"kind": "blocked", "seq": 2}

    reactor.on_pause = [on_pause]
    assert adapter.push_runner.push_to_head(h) == "distance"
    assert (h.push_mm, h.push_known) == (2000.0, False)


@pytest.mark.parametrize("state,shown", [("printing", True), ("paused", True), ("standby", False)])
def test_a_load_failure_in_a_print_is_the_pause_reason(state: str, shown: bool) -> None:
    """The U1 paused on "lane a:3 push to e0: distance", but the
    page's top bar showed nothing — the pause was the U1's own, not the adapter's. A load the
    adapter fails during a print is the status's pause_reason too."""
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    adapter.print_stats = fakes.FakePrintStats(state)
    ace.feed.refuse = "no_filament"
    with pytest.raises(RuntimeError):
        left._put_into_drive(1)
    line = "ace2k_u1: lane 1 push to e0: no_filament"
    assert adapter.pause_reason == (line if shown else None)


@pytest.mark.parametrize("state,kept", [("paused", True), ("printing", False)])
def test_a_load_failure_and_a_reason_already_set(state: str, kept: bool) -> None:
    """Paused: what paused the print stays. Printing: a reason no pause followed is stale."""
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    adapter.print_stats = fakes.FakePrintStats(state)
    adapter.pause_reason = "ace2k_u1: the first failure"
    ace.feed.refuse = "no_filament"
    with pytest.raises(RuntimeError):
        left._put_into_drive(1)
    new = "ace2k_u1: lane 1 push to e0: no_filament"
    assert adapter.pause_reason == ("ace2k_u1: the first failure" if kept else new)


@pytest.mark.parametrize(
    "budget_s, waited_s",
    [(200.0, 200.0), (10.0, ace2k_u1_common.move_wait_s(2000.0, 30.0))],
)
def test_a_unit_with_host_interface_17_bounds_the_push_by_the_longer_wait(
    monkeypatch: pytest.MonkeyPatch, budget_s: float, waited_s: float
) -> None:
    """The longer of the budget of ace2k for the move (feed.move_wait_s) and the old formula of
    the adapter: a longer budget lengthens the wait, a shorter one never shortens it."""
    monkeypatch.setattr(fakes.FakeFeedApi, "API_VERSION", 17)
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.feed.wait_s = budget_s
    start = reactor.now
    assert adapter.push_runner.push_to_head(h) == "timeout"
    assert reactor.now - start == pytest.approx(waited_s, abs=0.1)
    assert ace.feed.waits == [(0, 2000.0, 30.0)]


def test_a_push_that_ran_in_several_moves_records_the_whole_push() -> None:
    """The push's tip caught at 300 mm and went on after a touch; the motor then stalled 150 mm
    further. The final event carries the whole push, 450 mm: the push records all of it for the
    caller's roll back."""
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    end = {"kind": "motor_stalled", "seq": 1, "filament_mm": 450.0, "motor_mm": 470.0}
    reactor.on_pause = [lambda now: setattr(ace.feed.started[0][4], "event", end)]
    assert adapter.push_runner.push_to_head(h) == "motor_speed"
    assert (h.push_mm, h.push_known, h.push_end) == (450.0, True, "motor_stalled")


# --- a head that one lane feeds: the push cuts the unit's tag search (no return to the park) ---

SEARCH_STOPPED = {"kind": "stopped", "mode": "loading", "motor_mm": 0.0, "seq": 0}


def searching(adapter: Adapter, ace: FakeAce2k, travelled: float | None = 400.0) -> Hooked:
    """Lane 1 (head 1, its own) in the unit's load with its tag not read yet. A stop ends the
    load where it is: its final event carries how far it went (None: no new event comes)."""
    h = lane1(adapter)
    adapter.gcode = Console()
    adapter.feed_speed_mm_s = 70.0
    adapter.tag_push_speed_mm_s = 30.0  # a slow stretch, under the push's speed
    ace.lanes[0].update(
        insert=True, mode="loading", encoder_mm=1000.0, tag=dict(fakes.NO_TAG, state="searching")
    )
    stop = ace.feed.stop

    def stopped(n: int) -> None:
        stop(n)
        lane = ace.lanes[n]
        if lane["mode"] == "loading":
            lane.update(mode="idle", tag=dict(fakes.NO_TAG, state="pending"))
            if travelled is not None:
                lane["last_event"] = dict(SEARCH_STOPPED, filament_mm=travelled)

    ace.feed.stop = stopped
    return h


def test_a_lone_heads_push_cuts_the_tag_search_and_runs_slow_up_to_its_reach() -> None:
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=400.0)
    # the push goes on: 350 mm more (to the search's 750) at 30 mm/s, then 70 mm/s
    at(reactor, 5.0, lambda: ace.lanes[0].update(encoder_mm=1349.0))
    at(reactor, 6.0, lambda: ace.lanes[0].update(encoder_mm=1350.0))
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.log[0] == ("stop", 0)  # the unit's load first: no return to the park
    lane, mode, length, speed, _move = ace.feed.started[0]
    assert (lane, mode, length, speed) == (0, "feed", 2000.0, 30.0)
    assert ace.feed.speeds == [(0, 70.0)]
    # after the start, before the stop at the head
    assert ace.feed.log.index(("speed", 0, 70.0)) == 2
    assert adapter.gcode.lines == [
        "ace2k_u1: lane 1: its tag not read yet: the unit's search is stopped and the push goes on"
        " to e0 at 30 mm/s for the search's last 350 mm, then at 70 mm/s"
    ]


def test_the_tag_read_on_the_way_speeds_the_push_up_at_once() -> None:
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=400.0)
    at(reactor, 3.0, lambda: ace.lanes[0].update(tag=dict(fakes.NO_TAG, state="read")))
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[0][3] == 30.0
    assert ace.feed.speeds == [(0, 70.0)]


RESTORED = dict(fakes.NO_TAG, state="read", uid="04112233", source="restored", record={"uid": "x"})


def test_a_read_restored_after_a_restart_keeps_the_push_slow_until_a_live_read() -> None:
    """After a restart ace2k shows a full bay's read from before as read (source restored),
    while the unit has not seen the tag yet. The push still reads it on the way, slow."""
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=400.0)
    ace.lanes[0]["tag"] = dict(RESTORED, state="searching")
    stop = ace.feed.stop

    def stopped(n: int) -> None:
        stop(n)
        ace.lanes[n]["tag"] = dict(RESTORED)  # the unit's pending, as ace2k shows it

    ace.feed.stop = stopped
    at(reactor, 3.0, lambda: ace.lanes[0].update(encoder_mm=1200.0))
    checks: list[bool] = []
    at(reactor, 4.0, lambda: checks.append(ace.feed.speeds == []))
    at(reactor, 5.0, lambda: ace.lanes[0].update(tag=dict(RESTORED, source="tag")))  # read live
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[0][3] == 30.0
    assert checks == [True]  # still slow with only the restored read
    assert ace.feed.speeds == [(0, 70.0)]  # the live read sped it up, before the reach


def test_a_cut_before_the_parking_point_adds_what_the_load_did_not_pull_to_the_budget() -> None:
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=100.0)
    ace.feed.move_max_mm = 5000.0  # the whole budget in one move
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[0][2:4] == (2200.0, 30.0)  # 2000 + the 200 mm short of the park
    assert "last 650 mm" in adapter.gcode.lines[0]


def test_a_stopped_load_with_no_distance_runs_the_whole_reach_slow() -> None:
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=None)
    ace.feed.move_max_mm = 5000.0  # the whole budget in one move
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[0][2:4] == (2300.0, 30.0)  # as if nothing was pulled
    assert "last 750 mm" in adapter.gcode.lines[0]
    assert ace.feed.speeds == []  # the reach was never covered


def test_a_failed_speed_up_keeps_the_push_going_slow() -> None:
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=700.0)
    ace.feed.speed_fail = RuntimeError("ace2k: no answer")
    at(reactor, 2.0, lambda: ace.lanes[0].update(encoder_mm=1050.0))
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[0][3] == 30.0
    assert ace.feed.speeds == []


def test_a_push_asked_before_the_search_waits_for_it_and_cuts_it() -> None:
    """The U1 asks for the push as soon as the filament is in the bay, while the unit still pulls
    it to the parking point (bench 2026-10-24): the push waits for the search and cuts it."""
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=400.0)
    ace.lanes[0]["tag"] = dict(fakes.NO_TAG, state="pending")  # still on its way to the park
    at(reactor, 3.0, lambda: ace.lanes[0].update(tag=dict(fakes.NO_TAG, state="searching")))
    at(reactor, 5.0, lambda: ace.lanes[0].update(encoder_mm=1350.0))
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.log[0] == ("stop", 0)  # the unit's load first: no return to the park
    assert ace.feed.started[0][3] == 30.0
    assert "its tag not read yet: the unit's search is stopped" in adapter.gcode.lines[0]


def test_a_load_that_ends_before_any_search_is_waited_for_and_not_cut() -> None:
    """The tag was read on the way to the park: the load ends there, and the push follows it."""
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace)
    ace.lanes[0]["tag"] = dict(fakes.NO_TAG, state="pending")
    at(reactor, 3.0, lambda: ace.lanes[0].update(mode="idle", tag=dict(fakes.NO_TAG, state="read")))
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.log[0][0] == "start"  # no stop: nothing to cut
    assert ace.feed.started[0][3] == 70.0
    assert adapter.gcode.lines == []


@pytest.mark.parametrize("feed", [70.0, 30.0])
def test_a_tag_push_speed_at_or_above_the_push_cuts_the_search_and_pushes_at_full_speed(
    feed: float,
) -> None:
    """Reads on the move hold up to the unit's 70 mm/s (bench 2026-10-24): no slow stretch."""
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=400.0)
    adapter.tag_push_speed_mm_s = 70.0
    adapter.feed_speed_mm_s = feed
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.log[0] == ("stop", 0)  # the unit's load first: no return to the park
    assert ace.feed.started[0][3] == feed
    assert ace.feed.speeds == []  # no speed change: the whole push at feed_speed
    assert h.push_slow_until is None
    assert adapter.gcode.lines == [
        "ace2k_u1: lane 1: its tag not read yet: the unit's search is stopped and the push goes on"
        f" to e0 at {feed:g} mm/s, the tag read on the way"
    ]


@pytest.mark.parametrize("case", ["tag_unknown", "tag_pending", "off", "old_ace2k"])
def test_no_cut_where_it_does_not_apply(case: str) -> None:
    """The unit's load is waited for, as before: a tag state other than searching, the key at 0,
    and an ace2k before host interface 13 (no speed change)."""
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace)
    if case == "tag_unknown":
        ace.lanes[0]["tag"] = dict(fakes.NO_TAG)
    elif case == "tag_pending":
        ace.lanes[0]["tag"] = dict(fakes.NO_TAG, state="pending")
    elif case == "off":
        adapter.tag_push_speed_mm_s = 0.0
    else:
        ace.feed.API_VERSION = 12
    at(reactor, 10.0, lambda: ace.lanes[0].update(mode="idle"))
    at(reactor, 11.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.log[0][0] == "start"  # no stop before: the load ran to its own end
    assert ace.feed.started[0][3] == adapter.feed_speed_mm_s
    assert ace.feed.speeds == []
    assert adapter.gcode.lines == []


def test_tag_push_speed_defaults_to_seventy_and_reaches_the_adapter() -> None:
    reactor = fakes.FakeReactor()
    printer = Printer(reactor, {"gcode": CheckGcode(), "ace2k": fakes.FakeAce2k()}, [])
    assert ace2k_u1.ACE2kU1(Config(printer)).tag_push_speed == 70.0


def test_a_tag_push_speed_under_the_units_least_is_a_config_error() -> None:
    reactor = fakes.FakeReactor()
    printer = Printer(reactor, {"gcode": CheckGcode(), "ace2k": fakes.FakeAce2k()}, [])
    with pytest.raises(ValueError, match="tag_push_speed"):
        ace2k_u1.ACE2kU1(Config(printer, {"tag_push_speed": 5.0}))
    assert ace2k_u1.ACE2kU1(Config(printer, {"tag_push_speed": 0.0})).tag_push_speed == 0.0


def test_a_cut_whose_load_event_did_not_come_counts_nothing_pulled() -> None:
    """The report shows the lane idle, but the stopped load's own event has not come: the event
    there is the one from before. Nothing is counted as pulled: the whole reach runs slow."""
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=None)
    ace.lanes[0]["last_event"] = dict(SEARCH_STOPPED, filament_mm=700.0)  # an older load's
    ace.feed.move_max_mm = 5000.0
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[0][2:4] == (2300.0, 30.0)
    assert "last 750 mm" in adapter.gcode.lines[0]


def test_a_cut_with_no_encoder_reading_runs_slow_until_the_tag_is_read() -> None:
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=400.0)
    ace.lanes[0]["encoder_mm"] = None
    at(reactor, 3.0, lambda: ace.lanes[0].update(encoder_mm=99999.0))  # far past: not a measure
    seen = []
    at(reactor, 4.0, lambda: seen.append(list(ace.feed.speeds)))
    at(reactor, 5.0, lambda: ace.lanes[0].update(tag=dict(fakes.NO_TAG, state="read")))
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert seen == [[]]  # still slow before the read
    assert ace.feed.speeds == [(0, 70.0)]


def test_a_stop_that_fails_leaves_the_load_to_its_own_end() -> None:
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace)

    def broken(n: int) -> None:
        raise RuntimeError("ace2k: no answer")

    ace.feed.stop = broken
    at(reactor, 10.0, lambda: ace.lanes[0].update(mode="idle"))
    at(reactor, 11.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", False))
    assert adapter.push_runner._cut_search(h, 70.0) == (0.0, 0.0)
    assert h.push_slow_until is None


def test_a_seats_tag_search_is_not_cut_the_seat_runs_to_the_head() -> None:
    """A seat's tag reads searching from its first mm. Nothing stops it there: the seat pulls on
    to the head's sensor, the tag read on the way, and no feed follows."""
    adapter, reactor, _left, _right, ace = build()
    h = searching(adapter, ace, travelled=7.0)
    h.waited = True  # a filament seen in the bay under the wait
    ace.lanes[0].update(mode="idle", tag=dict(fakes.NO_TAG, state="pending"))

    def on_start(move: FakeMove) -> None:
        if move.mode == "load":  # the seat: its report shows the tag search at once
            ace.lanes[0].update(mode="loading", tag=dict(fakes.NO_TAG, state="searching"))

    ace.feed.on_start = on_start
    at(reactor, 3.0, lambda: ace.lanes[0].update(tag=dict(fakes.NO_TAG, state="read")))
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert [s[1:4] for s in ace.feed.started] == [("load", 2000.0, 30.0)]
    assert ace.feed.log[1:] == [("speed", 0, 70.0), ("stop", 0)]  # stopped at the head only
    assert adapter.gcode.lines == []  # no cut said


def seat_feed_after(adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k) -> float:
    """Lane 1 seated, its report idle throughout. Return the seconds from the seat to the push."""
    h = lane1(adapter)
    adapter.gcode = Console()
    h.waited = True  # a filament seen in the bay under the wait
    ace.lanes[0].update(insert=True, mode="idle")
    times: dict[str, float] = {}

    def on_start(move: FakeMove) -> None:
        times.setdefault(move.mode, reactor.now)
        if move.mode == "load":  # the seat ends at once, loaded at the parking point
            move.event = {"kind": "loaded", "mode": "loading", "seq": move.seq, "filament_mm": 300}
        if move.mode == "feed":
            h.ff.runout_sensor[h.ch].detected = True

    ace.feed.on_start = on_start
    assert adapter.push_runner.push_to_head(h) is None
    assert [s[1] for s in ace.feed.started] == ["load", "feed"]
    return times["feed"] - times["load"]


def test_a_seat_with_no_cut_possible_does_not_wait_for_its_report() -> None:
    adapter, reactor, _left, _right, ace = build()
    adapter.tag_push_speed_mm_s = 0.0  # no cut
    assert seat_feed_after(adapter, reactor, ace) == 0.0
    assert ace.feed.started[0][1:4] == ("load", 300.0, 30.0)  # the seat to the park, at 30 mm/s


def seat_to_head(adapter: Adapter, ace: FakeAce2k) -> tuple[Hooked, list[FakeMove]]:
    """Lane 1 waited (its filament at the bay), its encoder at 1000 mm. Its seat pulls to the
    head at tag_push_speed 30, under the push's 70, its tag searching from its first mm."""
    h = lane1(adapter)
    adapter.gcode = Console()
    adapter.feed_speed_mm_s = 70.0
    adapter.tag_push_speed_mm_s = 30.0
    h.waited = True
    ace.lanes[0].update(
        insert=True, mode="idle", encoder_mm=1000.0, tag=dict(fakes.NO_TAG, state="pending")
    )
    seats: list[FakeMove] = []

    def on_start(move: FakeMove) -> None:
        if move.mode == "load":
            seats.append(move)
            ace.lanes[0].update(mode="loading", tag=dict(fakes.NO_TAG, state="searching"))

    ace.feed.on_start = on_start
    return h, seats


def test_a_seat_that_ends_short_of_the_head_leaves_the_push_only_the_rest_of_its_budget() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)

    def loaded() -> None:  # the load ends by itself, 300 mm in, past the gear
        ace.lanes[0].update(mode="idle", encoder_mm=1300.0)
        seats[0].event = {"kind": "loaded", "mode": "loading", "seq": 1, "filament_mm": 300.0}

    at(reactor, 2.0, loaded)
    ace.feed.on_push = lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True)
    assert adapter.push_runner.push_to_head(h) is None
    # the tag not read, 300 of the search's 750 mm gone: the push goes on slow
    assert [s[1:4] for s in ace.feed.started] == [("load", 2000.0, 30.0), ("feed", 1700.0, 30.0)]
    assert h.push_mm == 300.0 and h.push_known and h.push_moves == 2


def stop_ends_seat(ace: FakeAce2k, seats: list[FakeMove], filament_mm: float) -> None:
    """The stop of a seat brings its final event, filament_mm in."""
    stop = ace.feed.stop

    def stopped(n: int) -> None:
        stop(n)
        if seats and not seats[0].done():
            seats[0].event = dict(SEARCH_STOPPED, seq=seats[0].seq, filament_mm=filament_mm)

    ace.feed.stop = stopped


def test_a_seat_at_the_head_records_the_travel_of_its_final_event() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    stop_ends_seat(ace, seats, 120.0)
    at(reactor, 3.0, lambda: ace.lanes[0].update(encoder_mm=1100.0))
    at(reactor, 4.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert (h.push_mm, h.push_known, h.push_max_mm) == (120.0, True, 120.0)
    assert ace.feed.log[-1] == ("stop", 0)
    assert h.push_slow_until is None


def test_a_stopped_seat_with_no_final_event_takes_the_encoder_as_the_least_it_went() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)
    at(reactor, 3.0, lambda: ace.lanes[0].update(encoder_mm=1100.0))
    at(reactor, 4.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    start = reactor.now
    assert adapter.push_runner.push_to_head(h) is None
    assert (h.push_mm, h.push_known, h.push_max_mm) == (100.0, False, 2000.0)
    assert reactor.now - start <= 4.0 + ace2k_u1_push.SEAT_END_S + 0.5  # the wait is bounded
    assert ace.feed.log[-1] == ("stop", 0)
    assert h.push_slow_until is None


def test_a_seat_at_the_head_with_no_encoder_reading_leaves_its_travel_unknown() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)
    ace.lanes[0]["encoder_mm"] = None
    at(reactor, 4.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert (h.push_mm, h.push_known, h.push_max_mm) == (0.0, False, 2000.0)


def test_a_seats_slow_stretch_ends_once_the_filament_has_gone_the_searchs_reach() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)
    seen = []
    at(reactor, 3.0, lambda: ace.lanes[0].update(encoder_mm=1749.0))
    at(reactor, 4.0, lambda: seen.append(list(ace.feed.speeds)))
    at(reactor, 5.0, lambda: ace.lanes[0].update(encoder_mm=1750.0))  # 750 mm since the seat
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert seen == [[]]  # still slow short of the reach
    assert ace.feed.speeds == [(0, 70.0)]  # no tag read, the reach gone: at speed


def test_a_seat_whose_bay_empties_is_stopped_with_its_travel() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)
    at(reactor, 3.0, lambda: ace.lanes[0].update(insert=False, encoder_mm=1040.0))
    assert adapter.push_runner.push_to_head(h) == "no_filament"
    assert ace.feed.log[-1] == ("stop", 0)
    assert (h.push_mm, h.push_known) == (40.0, False)  # no final event after the stop
    assert h.push_slow_until is None


def test_a_seat_that_never_ends_times_out_stopped_with_its_travel() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)
    at(reactor, 3.0, lambda: ace.lanes[0].update(encoder_mm=1500.0))
    assert adapter.push_runner.push_to_head(h) == "timeout"
    assert ace.feed.log[-1] == ("stop", 0)
    assert (h.push_mm, h.push_known) == (500.0, False)
    assert h.push_slow_until is None


def test_a_seat_at_the_head_as_the_bay_reads_empty_is_at_the_head() -> None:
    """The head's sensor is read first: a tail leaving the bay as the tip reaches the head is a
    load that reached it."""
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)

    def both() -> None:
        ace.lanes[0]["insert"] = False
        h.ff.runout_sensor[h.ch].detected = True

    at(reactor, 3.0, both)
    assert adapter.push_runner.push_to_head(h) is None


def test_a_seats_slow_stretch_goes_on_into_the_push_up_to_the_searchs_reach() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)

    def loaded() -> None:  # 300 mm in, past the gear, the tag not read
        ace.lanes[0].update(mode="idle", encoder_mm=1300.0)
        seats[0].event = {"kind": "loaded", "mode": "loading", "seq": 1, "filament_mm": 300.0}

    at(reactor, 2.0, loaded)
    at(reactor, 5.0, lambda: ace.lanes[0].update(encoder_mm=1750.0))  # the reach gone
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[1][1:4] == ("feed", 1700.0, 30.0)
    assert ace.feed.speeds == [(0, 70.0)]  # then at speed


def test_a_seat_with_no_encoder_keeps_the_push_slow_until_the_tag_is_read() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    ace.lanes[0]["encoder_mm"] = None

    def loaded() -> None:
        ace.lanes[0].update(mode="idle", encoder_mm=99999.0)  # far past: not a measure
        seats[0].event = {"kind": "loaded", "mode": "loading", "seq": 1, "filament_mm": 300.0}

    seen = []
    at(reactor, 2.0, loaded)
    at(reactor, 4.0, lambda: seen.append(list(ace.feed.speeds)))
    at(reactor, 5.0, lambda: ace.lanes[0].update(tag=dict(fakes.NO_TAG, state="read")))
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[1][3] == 30.0 and seen == [[]]
    assert ace.feed.speeds == [(0, 70.0)]


def test_a_seat_with_its_tag_read_hands_the_push_on_at_speed() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)

    def loaded() -> None:
        ace.lanes[0].update(mode="idle", encoder_mm=1300.0, tag=dict(fakes.NO_TAG, state="read"))
        seats[0].event = {"kind": "loaded", "mode": "loading", "seq": 1, "filament_mm": 300.0}

    at(reactor, 2.0, loaded)
    ace.feed.on_push = lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True)
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[1][1:4] == ("feed", 1700.0, 70.0)


@pytest.mark.parametrize(("kind", "error"), [("stuck", "wheel_speed"), ("blocked", "distance")])
def test_a_seat_that_ends_in_error_past_the_gear_keeps_its_kind(kind: str, error: str) -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)

    def failed() -> None:
        ace.lanes[0].update(mode="idle", encoder_mm=1300.0)
        seats[0].event = {"kind": kind, "mode": "loading", "seq": 7, "filament_mm": 300.0}

    at(reactor, 2.0, failed)
    assert adapter.push_runner.push_to_head(h) == error
    assert (h.push_end, h.push_end_seq, h.push_mm, h.push_known) == (kind, 7, 300.0, True)
    assert [s[1] for s in ace.feed.started] == ["load"]  # no feed after it


def test_a_seat_of_unknown_travel_spends_its_length_and_the_push_stays_unknown() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    ace.lanes[0]["encoder_mm"] = None

    def loaded() -> None:  # no distance in the event, no encoder
        ace.lanes[0].update(mode="idle", tag=dict(fakes.NO_TAG, state="read"))
        seats[0].event = {"kind": "loaded", "mode": "loading", "seq": 1}

    at(reactor, 2.0, loaded)
    ace.feed.on_push = lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True)
    assert adapter.push_runner.push_to_head(h, budget=3000.0) is None
    assert ace.feed.started[1][1:3] == ("feed", 1000.0)  # 3000 less the seat's 2000
    assert not h.push_known and h.push_max_mm == 3000.0


def test_a_budget_under_seated_mm_starts_no_seat() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)
    ace.feed.on_push = lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True)
    assert adapter.push_runner.push_to_head(h, budget=12.0) is None
    assert [s[1:3] for s in ace.feed.started] == [("feed", 12.0)]


def test_a_stale_error_before_the_seat_is_seen_loading_does_not_end_it() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)
    on_start = ace.feed.on_start

    def stale(move: FakeMove) -> None:
        on_start(move)
        ace.lanes[0]["mode"] = "error"  # the report from before the start

    ace.feed.on_start = stale
    at(reactor, 1.0, lambda: ace.lanes[0].update(mode="loading"))
    at(reactor, 3.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert [s[1] for s in ace.feed.started] == ["load"]


def test_a_seat_whose_watch_breaks_is_stopped_and_its_travel_unknown() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    status = adapter.lane_status

    def broken(*args: object, **kw: object) -> object:
        if seats:
            raise RuntimeError("ace2k: no answer")
        return status(*args, **kw)

    adapter.lane_status = broken
    with pytest.raises(RuntimeError, match="no answer"):
        adapter.push_runner.push_to_head(h)
    assert ace.feed.log[-1] == ("stop", 0)
    assert (h.push_known, h.push_max_mm, h.push_moves) == (False, 2000.0, 1)
    assert h.push_slow_until is None


def test_a_seat_event_with_no_distance_keeps_its_kind_whatever_the_encoder_says() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)

    def stuck() -> None:  # the encoder past the seat's length, the event with no distance
        ace.lanes[0].update(mode="idle", encoder_mm=1100.0)
        seats[0].event = {"kind": "stuck", "mode": "loading", "seq": 3}

    at(reactor, 2.0, stuck)
    assert adapter.push_runner.push_to_head(h, budget=100.0) == "wheel_speed"
    assert (h.push_end, h.push_mm, h.push_known) == ("stuck", 100.0, True)


def test_a_seat_whose_start_encoder_cannot_be_read_still_runs() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    status = adapter.lane_status
    calls: list[int] = []

    def first_broken(*args: object, **kw: object) -> object:
        if not calls:
            calls.append(1)
            raise RuntimeError("ace2k: no answer")
        return status(*args, **kw)

    adapter.lane_status = first_broken
    stop_ends_seat(ace, seats, 400.0)
    at(reactor, 3.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert [s[1] for s in ace.feed.started] == ["load"]
    assert (h.push_mm, h.push_known) == (400.0, True)


def test_a_seat_that_ends_as_the_head_sees_the_filament_starts_no_feed() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)

    def loaded() -> None:  # the seat ends clean as the tip reaches the head's sensor
        ace.lanes[0].update(mode="idle", encoder_mm=1300.0)
        seats[0].event = {"kind": "loaded", "mode": "loading", "seq": 1, "filament_mm": 300.0}
        h.ff.runout_sensor[h.ch].detected = True

    at(reactor, 2.0, loaded)
    assert adapter.push_runner.push_to_head(h) is None
    assert [s[1] for s in ace.feed.started] == ["load"]
    assert h.push_mm == 300.0


def sensor_with_the_report(adapter: Adapter, h: Hooked, seats: list[FakeMove]) -> None:
    """The head's sensor sets in the same report that brings the seat's end."""
    status = adapter.lane_status

    def report(*args: object, **kw: object) -> object:
        if seats and seats[0].done():
            h.ff.runout_sensor[h.ch].detected = True
        return status(*args, **kw)

    adapter.lane_status = report


def test_a_seat_that_ends_stuck_with_the_filament_at_the_head_is_a_success() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    sensor_with_the_report(adapter, h, seats)

    def stuck() -> None:
        ace.lanes[0].update(mode="idle", encoder_mm=1300.0)
        seats[0].event = {"kind": "stuck", "mode": "loading", "seq": 1, "filament_mm": 300.0}

    at(reactor, 2.0, stuck)
    assert adapter.push_runner.push_to_head(h) is None
    assert [s[1] for s in ace.feed.started] == ["load"]
    assert h.push_end is None


def test_a_seat_whose_start_was_not_read_takes_the_reach_from_its_first_reading() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, _seats = seat_to_head(adapter, ace)
    status = adapter.lane_status
    calls: list[int] = []

    def first_broken(*args: object, **kw: object) -> object:
        if not calls:
            calls.append(1)
            raise RuntimeError("ace2k: no answer")
        return status(*args, **kw)

    adapter.lane_status = first_broken
    at(reactor, 3.0, lambda: ace.lanes[0].update(encoder_mm=1749.0))
    at(reactor, 5.0, lambda: ace.lanes[0].update(encoder_mm=1750.0))  # 750 mm from 1000
    at(reactor, 8.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.speeds == [(0, 70.0)]  # the reach gone, no tag read


def test_a_tagless_seat_with_no_encoder_hands_the_push_on_at_speed() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    ace.lanes[0]["encoder_mm"] = None

    def loaded() -> None:
        ace.lanes[0]["mode"] = "idle"
        seats[0].event = {"kind": "loaded", "mode": "loading", "seq": 1, "filament_mm": 300.0}

    at(reactor, 2.0, loaded)
    ace.feed.on_push = lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True)
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[1][1:4] == ("feed", 1700.0, 70.0)


def test_the_slow_stretch_after_a_seat_is_judged_from_a_fresh_status() -> None:
    """The start read 1000 mm; the seat's event says 760 mm, the encoder then unread: the
    search's reach is gone, so the push runs at speed."""
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)

    def loaded() -> None:
        ace.lanes[0].update(mode="idle", encoder_mm=None)
        seats[0].event = {"kind": "loaded", "mode": "loading", "seq": 1, "filament_mm": 760.0}

    at(reactor, 2.0, loaded)
    ace.feed.on_push = lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True)
    assert adapter.push_runner.push_to_head(h) is None
    assert ace.feed.started[1][1:4] == ("feed", 1240.0, 70.0)


def test_a_watch_that_breaks_after_the_seat_ended_keeps_its_travel_and_stops_nothing() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    head_set = adapter.head_set

    def broken(*args: object) -> bool:
        if seats and seats[0].done():
            raise RuntimeError("ace2k_u1: the head's sensor not read")
        return head_set(*args)

    adapter.head_set = broken
    at(reactor, 2.0, lambda: setattr(seats[0], "event", dict(SEARCH_STOPPED, filament_mm=300.0)))
    with pytest.raises(RuntimeError, match="sensor not read"):
        adapter.push_runner.push_to_head(h)
    assert ("stop", 0) not in ace.feed.log
    assert (h.push_mm, h.push_known, h.push_max_mm) == (300.0, True, 300.0)


class Interrupted(BaseException):
    pass


def test_a_stop_that_raises_after_a_broken_watch_still_records_the_seat() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)
    status = adapter.lane_status

    def broken(*args: object, **kw: object) -> object:
        if seats:
            raise RuntimeError("ace2k: no answer")
        return status(*args, **kw)

    def stop(n: int) -> None:
        raise Interrupted

    adapter.lane_status = broken
    ace.feed.stop = stop
    with pytest.raises(RuntimeError, match="no answer"):
        adapter.push_runner.push_to_head(h)
    assert (h.push_known, h.push_max_mm, h.push_moves) == (False, 2000.0, 1)


def test_a_seat_event_with_no_kind_keeps_no_kind() -> None:
    adapter, reactor, _left, _right, ace = build()
    h, seats = seat_to_head(adapter, ace)

    def ended() -> None:
        ace.lanes[0].update(mode="idle", encoder_mm=1300.0)
        seats[0].event = {"mode": "loading", "seq": 1, "filament_mm": 300.0}

    at(reactor, 2.0, ended)
    assert adapter.push_runner.push_to_head(h) == "timeout"
    assert h.push_end is None
