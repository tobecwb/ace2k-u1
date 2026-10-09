"""The push of a lane to the head's filament sensor: its outcomes, the wait for the unit's own
load, and the U1's preload and load running through it."""

import fakes
import pytest
from test_presence import Console, build

ERR_EVENT = {"kind": "stuck", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0, "seq": 1}


def sensor_after(reactor, sensor, seconds):
    start = reactor.now
    reactor.on_pause = [lambda now: setattr(sensor, "detected", now - start >= seconds)]


def at(reactor, seconds, action):
    """Run action once, the first pause at or after seconds from now."""
    start = reactor.now
    fired = []

    def hook(now):
        if not fired and now - start >= seconds:
            fired.append(now)
            action()

    reactor.on_pause = getattr(reactor, "on_pause", []) + [hook]


def lane1(adapter):
    adapter.hook_all()
    return next(x for x in adapter.hooked if x.lane == 0)


def test_push_stops_at_the_head_sensor():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    sensor = h.ff.runout_sensor[h.ch]
    sensor_after(reactor, sensor, 1.0)
    assert adapter.push_to_head(h) is None
    lane, mode, length, speed, move = ace.feed.started[0]
    assert (lane, mode, length, speed) == (0, "feed", 2000.0, 30.0)
    assert ace.feed.stopped == [0]


def test_push_with_the_sensor_already_set_moves_nothing():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    h.ff.runout_sensor[h.ch].detected = True
    assert adapter.push_to_head(h) is None
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
def test_push_maps_the_final_event(kind, error):
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    reactor.on_pause = [
        lambda now: setattr(ace.feed.started[0][4], "event", dict(ERR_EVENT, kind=kind))
    ]
    assert adapter.push_to_head(h) == error


@pytest.mark.parametrize(
    "reason,error",
    [("no_filament", "no_filament"), ("in_error", "motor_speed"), ("no_link", "motor_speed")],
)
def test_push_maps_a_refusal(reason, error):
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.feed.refuse = reason
    assert adapter.push_to_head(h) == error
    assert ace.feed.attempts == 1


def test_push_times_out_and_stops():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    start = reactor.now
    assert adapter.push_to_head(h) == "timeout"
    assert ace.feed.stopped == [0]
    assert reactor.now - start == pytest.approx(2000.0 / 30.0 * 2.0 + 5.0, abs=0.1)


def test_push_waits_for_the_units_load_then_pushes():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0].update(insert=True, mode="loading")
    at(reactor, 10.0, lambda: ace.lanes[0].update(mode="idle"))
    at(reactor, 11.0, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    start = reactor.now
    assert adapter.push_to_head(h) is None
    assert len(ace.feed.started) == 1
    assert ace.feed.stopped == [0]
    assert reactor.now - start >= 10.0


def test_push_waiting_on_a_load_that_ends_in_error_is_motor_speed():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0].update(insert=True, mode="loading")
    at(reactor, 5.0, lambda: ace.lanes[0].update(mode="error"))
    assert adapter.push_to_head(h) == "motor_speed"
    assert ace.feed.started == []


def test_push_waiting_on_a_load_whose_filament_leaves_is_no_filament():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0].update(insert=True, mode="loading")
    at(reactor, 5.0, lambda: ace.lanes[0].update(insert=False))
    assert adapter.push_to_head(h) == "no_filament"
    assert ace.feed.started == []


def test_push_waiting_on_a_load_gives_up_at_the_cap():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0].update(insert=True, mode="loading")
    start = reactor.now
    assert adapter.push_to_head(h) == "timeout"
    assert ace.feed.started == []
    assert ace.feed.stopped == [0]
    assert reactor.now - start == pytest.approx(120.0, abs=0.1)


def test_push_refused_busy_once_retries_and_completes():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0]["insert"] = True
    ace.feed.refusals = ["busy"]
    start = reactor.now
    retried = []
    at(reactor, 0.9, lambda: retried.append(ace.feed.attempts))
    at(reactor, 1.5, lambda: setattr(h.ff.runout_sensor[h.ch], "detected", True))
    assert adapter.push_to_head(h) is None
    assert retried == [1]  # no second start before one report period (1 s)
    assert ace.feed.attempts == 2
    assert reactor.now - start >= 1.5
    assert len(ace.feed.started) == 1
    assert ace.feed.stopped == [0]


def test_push_refused_busy_until_the_cap_times_out():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    ace.lanes[0]["insert"] = True
    ace.feed.refuse = "busy"
    start = reactor.now
    assert adapter.push_to_head(h) == "timeout"
    assert ace.feed.started == []
    assert ace.feed.stopped == [0]
    assert reactor.now - start == pytest.approx(120.0, abs=0.1)
    assert 115 <= ace.feed.attempts <= 121  # one start per second, not one per local poll


def test_load_success_returns():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    sensor_after(reactor, h.ff.runout_sensor[h.ch], 0.5)
    assert left._put_into_drive(1) is None
    assert ("put_into_drive", 1) not in left.calls


def test_load_failure_sets_the_code_and_raises():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.feed.refuse = "no_filament"
    with pytest.raises(RuntimeError, match="no_filament"):
        left._put_into_drive(1)  # lane 1
    assert left.channel_error[1] == "no_filament" and left.exception_code[1] == 33


def test_a_load_push_that_ends_blocked_is_the_u1s_failure_35():
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


def test_a_load_pushes_a_loose_leftover_ahead_to_the_head():
    # a loose piece in the tube moves: the push runs on, the piece reaches the head's sensor
    # first and the load succeeds — no blocked, no failure
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    sensor_after(reactor, left.runout_sensor[1], 3.0)
    assert left._put_into_drive(1) is None
    assert ace.feed.started[0][:3] == (0, "feed", 2000.0) and ace.feed.stopped == [0]
    assert left.channel_error[1] == "ok"


def test_load_on_an_unhooked_channel_calls_the_original():
    adapter, reactor, left, right, ace = build(lanes=(1,))
    adapter.hook_all()
    left._put_into_drive(0)  # lane 2, not hooked
    assert ("put_into_drive", 0) in left.calls
    assert ace.feed.started == []


def test_preload_success_walks_the_u1_states():
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


def test_preload_without_a_filament_is_13():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    left._do_feed(1, "preload")
    assert left.exception_code[1] == 13 and left.channel_error[1] == "no_filament"
    assert left.channel_state[1] == "preload_fail"
    assert left.exception_manager.raised[-1]["code"] == 13
    assert ace.feed.started == []
    assert left.channel_active is None


def test_preload_residual_filament_is_15_and_async_notice():
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
def test_preload_push_failure_is_10_plus_n(kind, code):
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


def test_a_preload_push_that_ends_blocked_waits_at_wait_insert():
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


def test_a_blocked_preloads_line_follows_the_print():
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


def test_a_preload_after_a_blocked_one_fails_normally():
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


def test_preload_waits_for_the_channel_lock():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    left.channel_active = 0
    at(reactor, 1.0, lambda: setattr(left, "channel_active", None))
    at(reactor, 2.0, lambda: setattr(left.runout_sensor[1], "detected", True))
    left._do_feed(1, "preload")
    assert left.channel_state[1] == "preload_finish"
    assert left.channel_active is None


def test_other_actions_reach_the_original():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    left._do_feed(1, "load")
    assert ("do_feed", 1, "load", None, None) in left.calls


def test_preload_on_an_unhooked_channel_reaches_the_original():
    adapter, reactor, left, right, ace = build(lanes=(1,))
    adapter.hook_all()
    left._do_feed(0, "preload")
    assert ("do_feed", 0, "preload", None, None) in left.calls


def test_a_successful_preload_clears_a_stale_error_state():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    left._do_feed(1, "preload")  # no filament: fails
    assert left.channel_error_state[1] == "preload_fail"
    ace.lanes[0]["insert"] = True
    sensor_after(reactor, left.runout_sensor[1], 0.5)
    left._do_feed(1, "preload")
    assert left.channel_state[1] == "preload_finish"
    assert left.channel_error_state[1] == "none"


def test_an_unexpected_error_in_the_preload_is_a_general_failure():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    sensor_after(reactor, left.runout_sensor[1], 0.5)

    def broken_stop(lane):
        raise RuntimeError("link gone")

    ace.feed.stop = broken_stop
    left._do_feed(1, "preload")
    assert left.channel_error[1] == "general" and left.exception_code[1] == 10
    assert left.channel_state[1] == "preload_fail"
    assert left.channel_error_state[1] == "preload_fail"
    raised = left.exception_manager.raised[-1]
    assert raised["code"] == 10 and raised["message"] == "preload fail: general"
    assert left.channel_active is None


def break_head_sensor_after(reactor, sensor, seconds):
    """The head sensor's reading raises once seconds have passed (mid-poll)."""
    start = reactor.now
    real = sensor.get_status

    def get_status(eventtime):
        if reactor.now - start >= seconds:
            raise OSError("sensor read failed")
        return real(eventtime)

    sensor.get_status = get_status


def test_an_exception_mid_push_stops_the_lane_and_reaches_the_caller():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    break_head_sensor_after(reactor, h.ff.runout_sensor[h.ch], 1.0)
    with pytest.raises(OSError):
        adapter.push_to_head(h)
    assert len(ace.feed.started) == 1
    assert ace.feed.stopped == [0]


def test_a_failing_stop_does_not_mask_the_original_exception():
    adapter, reactor, left, right, ace = build()
    h = lane1(adapter)
    break_head_sensor_after(reactor, h.ff.runout_sensor[h.ch], 1.0)

    def broken_stop(lane):
        raise RuntimeError("link gone")

    ace.feed.stop = broken_stop
    with pytest.raises(OSError):
        adapter.push_to_head(h)


def test_an_exception_mid_preload_push_stops_and_is_a_general_failure():
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


def test_an_exception_mid_load_push_stops_and_raises():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    break_head_sensor_after(reactor, left.runout_sensor[1], 1.0)
    with pytest.raises(OSError):
        left._put_into_drive(1)
    assert ace.feed.stopped == [0]
