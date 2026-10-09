"""The adapter's channel states: the follow (the both-way assist) armed from the U1's extrusion
on, on a loaded head and through the unload's tip forming, the lane stopped on every failure and
at an unload's end, the eject after that stop and its error notice; the pauses: a lane error in
the follow during a print (the U1's tangle pause) and the tail's end (tail_out) by runout_source
(the U1's runout pause, or a line); the tail notice and every tail_out's line."""

import types

import ace2k_u1
import fakes
from test_check import Config, Printer
from test_check import Gcode as CheckGcode
from test_presence import build


class Gcode:
    def __init__(self):
        self.lines = []

    def respond_info(self, msg):
        self.lines.append(msg)


def setup():
    adapter, reactor, left, right, ace = build()
    adapter.gcode = Gcode()
    adapter.hook_all()
    return adapter, reactor, left, right, ace


def unloaded(feed, ch, reactor):
    """The U1's unload finished; the eject's timer then runs."""
    feed._set_channel_state(ch, "unload_finish", True)
    reactor.run_timers()


def event(kind, seq, mode="unloading"):
    return {"kind": kind, "mode": mode, "motor_mm": 1.0, "filament_mm": 0.0, "seq": seq}


def test_load_finish_arms_the_follow():
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "load_finish", True)
    assert ("state", 1, "load_finish", True) in left.calls
    assert ace.feed.started[-1][:4] == (0, "assist_both", 0.0, 30.0)


def test_the_original_call_runs_first_with_the_same_arguments():
    adapter, reactor, left, right, ace = setup()
    seen = []
    ace.feed.start_move = lambda *a: seen.append(list(left.calls)) or fakes.FakeMove(0, "x", 1)
    left._set_channel_state(1, "load_finish", True)
    assert seen == [[("state", 1, "load_finish", True)]]
    left._set_channel_state(1, "wait_insert")
    assert left.calls[-1] == ("state", 1, "wait_insert", False)


def test_a_follow_refusal_is_a_line_not_an_error():
    adapter, reactor, left, right, ace = setup()
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "load_finish", True)
    assert any("not armed" in line for line in adapter.gcode.lines)


def test_a_busy_refusal_on_a_following_lane_is_silent():
    adapter, reactor, left, right, ace = setup()
    ace.lanes[0]["mode"] = "following"
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "load_finish", True)
    assert adapter.gcode.lines == []
    ace.feed.refuse = "no_filament"  # any other refusal is still told
    left._set_channel_state(1, "load_finish", True)
    assert len(adapter.gcode.lines) == 1 and "not armed" in adapter.gcode.lines[0]


def test_a_value_or_runtime_error_is_a_line_too():
    adapter, reactor, left, right, ace = setup()
    for exc in (ValueError("bad lane"), RuntimeError("no link")):

        def start_move(*a, exc=exc):
            raise exc

        ace.feed.start_move = start_move
        left._set_channel_state(1, "load_finish", True)
        unloaded(left, 1, reactor)
    assert sum("not armed" in line for line in adapter.gcode.lines) == 2
    assert sum("eject not started" in line for line in adapter.gcode.lines) == 2
    assert adapter.ejects == {}


def test_failures_stop_the_lane_and_unload_prepare_does_not():
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "unload_prepare")
    assert ace.feed.stopped == []
    left._set_channel_state(1, "load_fail")
    left._set_channel_state(1, "unload_fail")
    assert ace.feed.stopped == [0, 0]
    for state in ("unload_fail", "manual_sta_fail", "preload_fail", "manual_sta_flush_fail"):
        right._set_channel_state(0, state)
    assert ace.feed.stopped == [0, 0, 2, 2, 2, 2]  # right channel 0 -> lane 3


def loaded(feed, ch):
    """A head that finished a load (its assist armed)."""
    feed._set_channel_state(ch, "load_finish", True)


def test_a_head_runout_after_a_load_stops_the_lane():
    adapter, reactor, left, right, ace = setup()
    loaded(left, 1)
    left._set_channel_state(1, "preload_finish", True)  # runout, the filament still in the bay
    assert ace.feed.stopped == [0]


def test_the_manual_feeds_finish_on_an_empty_head_stops_the_lane():
    adapter, reactor, left, right, ace = setup()
    loaded(left, 1)
    left._set_channel_state(1, "wait_insert", True)
    loaded(right, 0)
    right._set_channel_state(0, "none", True)
    assert ace.feed.stopped == [0, 2]


def test_load_finish_set_again_does_not_stop():
    adapter, reactor, left, right, ace = setup()
    loaded(left, 1)
    loaded(left, 1)
    assert ace.feed.stopped == []
    assert [s[:2] for s in ace.feed.started] == [(0, "assist_both")]  # armed already: one start


PULL = (
    "load_extruding",
    "load_flushing",
    "manual_sta_extruding",
    "manual_sta_flushing",
    "unload_doing",
)


def assists(ace):
    return [s[:2] for s in ace.feed.started if s[1] == "assist_both"]


def test_the_pull_states_are_checked_constants():
    for name in ace2k_u1.PULL_STATES:
        assert name in ace2k_u1.CONSTANTS
    assert tuple(ace2k_u1.CONSTANTS[n] for n in ace2k_u1.PULL_STATES) == PULL


def test_each_pull_state_arms_the_follow_once():
    for state in PULL:
        adapter, reactor, left, right, ace = setup()
        left._set_channel_state(1, state)
        assert left.calls[-1] == ("state", 1, state, False), state
        assert ace.feed.started[-1][:4] == (0, "assist_both", 0.0, 30.0), state
        assert assists(ace) == [(0, "assist_both")], state
        assert ace.feed.stopped == [], state


def test_a_pull_state_set_twice_sends_one_start():
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_extruding")
    assert assists(ace) == [(0, "assist_both")]


def test_the_preload_alone_never_arms():
    adapter, reactor, left, right, ace = setup()
    for state in ("preload_prepare", "preload_feeding", "preload_finish"):
        left._set_channel_state(1, state, True)
    assert ace.feed.started == [] and ace.feed.stopped == []


def test_a_load_failing_while_extruding_disarms():
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_fail")
    assert assists(ace) == [(0, "assist_both")]
    assert ace.feed.stopped == [0]
    left._set_channel_state(1, "load_extruding")  # the next load arms again
    assert assists(ace) == [(0, "assist_both"), (0, "assist_both")]


def test_a_load_arms_once_from_the_extrusion_and_never_stops():
    adapter, reactor, left, right, ace = setup()
    for state in ("load_prepare", "load_heating", "load_extruding", "load_flushing"):
        left._set_channel_state(1, state)
    left._set_channel_state(1, "load_finish", True)
    assert assists(ace) == [(0, "assist_both")]
    assert ace.feed.stopped == []


def test_the_manual_feed_keeps_the_follow_armed_from_its_extrusion():
    adapter, reactor, left, right, ace = setup()
    for state, save in (
        ("manual_sta_heating", True),
        ("manual_sta_extruding", False),
        ("manual_sta_extrude_finish", False),
        ("manual_sta_flushing", True),
        ("manual_sta_flush_finish", False),
        ("manual_sta_finish", True),
        ("load_finish", True),
    ):
        left._set_channel_state(1, state, save)
    assert assists(ace) == [(0, "assist_both")]
    assert ace.feed.stopped == []


def test_a_refusal_while_arming_is_a_line_not_an_error():
    adapter, reactor, left, right, ace = setup()
    ace.feed.refuse = "no_filament"
    left._set_channel_state(1, "load_extruding")
    assert left.calls[-1] == ("state", 1, "load_extruding", False)
    assert any("not armed" in line for line in adapter.gcode.lines)
    ace.feed.refuse = None  # not armed: the next pull state tries again
    left._set_channel_state(1, "load_flushing")
    assert assists(ace) == [(0, "assist_both")]


def test_a_busy_refusal_on_a_following_lane_is_silent_on_a_pull_state_too():
    adapter, reactor, left, right, ace = setup()
    ace.lanes[0]["mode"] = "following"
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_flushing")
    assert adapter.gcode.lines == []


def test_a_filament_leaving_its_bay_disarms():
    adapter, reactor, left, right, ace = setup()
    h = next(h for h in adapter.hooked if h.lane == 0)
    h.present = True
    left._set_channel_state(1, "load_extruding")
    ace.lanes[0].update(insert=False, mode="assisting")  # the follow's own fall is the tail
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [0] and not h.armed
    left._set_channel_state(1, "load_extruding")
    assert assists(ace) == [(0, "assist_both"), (0, "assist_both")]


def u1_update_auto_mode(feed):
    """The U1's own UPDATE_AUTO_MODE re-derivation (its _do_feed), for the hook-time call: a
    head left in load_finish is set to load_finish again, whatever the mode."""
    orig = feed._do_feed

    def do_feed(ch, action=None, stage=None, auto_mode=None):
        orig(ch, action, stage, auto_mode)
        if action != "update_auto_mode":
            return
        feed.config["auto_mode"][ch] = bool(auto_mode)
        if feed.channel_state[ch] == "load_finish":
            feed._set_channel_state(ch, "load_finish", True)
        elif not auto_mode or not feed.module_exist[ch]:
            feed._set_channel_state(ch, "none", True)
        elif feed._port[ch].get_filament_detected():
            feed._set_channel_state(ch, "preload_finish", True)
        else:
            feed._set_channel_state(ch, "wait_insert", True)

    feed._do_feed = do_feed


def test_the_hook_time_re_derivation_arms_a_loaded_heads_follow():
    for auto_mode in (True, False):
        adapter, reactor, left, right, ace = build()
        adapter.gcode = Gcode()
        left.config["auto_mode"] = [auto_mode, auto_mode]
        left.channel_state[1] = "load_finish"  # lane 1's head kept loaded across the restart
        u1_update_auto_mode(left)
        assert adapter.hook_all() == []
        reactor.run_async()
        assert [s[:2] for s in ace.feed.started] == [(0, "assist_both")], auto_mode
        assert ace.feed.stopped == [], auto_mode


def test_a_failing_stop_is_a_line_not_an_error():
    adapter, reactor, left, right, ace = setup()

    def stop(lane):
        raise RuntimeError("no link")

    ace.feed.stop = stop
    left._set_channel_state(1, "load_fail")
    assert left.calls[-1] == ("state", 1, "load_fail", False)
    assert any("not stopped" in line for line in adapter.gcode.lines)


def test_unload_finish_ejects_and_an_error_event_is_a_notice():
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    assert ace.feed.started[-1][:4] == (0, "unload", 0.0, 30.0)
    seq = adapter.ejects[0].seq
    adapter.on_feed_event(0, event("stuck", seq))
    assert adapter.gcode.lines == ["ace2k_u1: lane 1 eject failed: stuck — ACE_CLEAR LANE=1"]
    assert 0 not in adapter.ejects


def test_a_clean_eject_is_silent():
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    seq = adapter.ejects[0].seq
    adapter.on_feed_event(0, event("unloaded", seq))
    assert adapter.gcode.lines == []
    assert 0 not in adapter.ejects


def test_a_stopped_eject_is_dropped_silently():
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    seq = adapter.ejects[0].seq
    adapter.on_feed_event(0, event("stopped", seq))
    assert adapter.gcode.lines == []
    assert 0 not in adapter.ejects


def test_notices_and_other_moves_leave_the_eject_waiting():
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    seq = adapter.ejects[0].seq
    adapter.on_feed_event(0, event("behind", seq))
    adapter.on_feed_event(0, event("snag", seq))
    adapter.on_feed_event(0, event("stuck", seq + 100))
    adapter.on_feed_event(1, event("stuck", seq))
    assert 0 in adapter.ejects and adapter.gcode.lines == []
    adapter.on_feed_event(0, event("timeout", seq))
    assert adapter.gcode.lines == ["ace2k_u1: lane 1 eject failed: timeout — ACE_CLEAR LANE=1"]


def test_a_second_eject_replaces_the_first():
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    first = adapter.ejects[0].seq
    unloaded(left, 1, reactor)
    second = adapter.ejects[0].seq
    assert second != first
    adapter.on_feed_event(0, event("stuck", first))
    assert adapter.gcode.lines == [] and adapter.ejects[0].seq == second


def test_unhooked_channels_get_the_original_only():
    adapter, reactor, left, right, ace = build(lanes=(1,))
    adapter.gcode = Gcode()
    adapter.hook_all()
    left._set_channel_state(0, "load_finish", True)  # lane 2, not hooked
    left._set_channel_state(0, "unload_prepare")
    left._set_channel_state(0, "load_fail")
    unloaded(left, 0, reactor)
    assert ace.feed.started == [] and ace.feed.stopped == []
    assert left.calls[-1] == ("state", 0, "unload_finish", True)


class EventPrinter(Printer):
    def __init__(self, *args):
        super().__init__(*args)
        self.handlers = []

    def register_event_handler(self, name, cb):
        self.handlers.append((name, cb))


def hooked_u1():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    gcode = CheckGcode()
    printer = EventPrinter(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left, right])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    return u1, printer, gcode, left


def feed_handlers(printer):
    return [cb for name, cb in printer.handlers if name == "ace2k:feed_event"]


def test_the_hook_wires_the_console_and_the_feed_events():
    u1, printer, gcode, left = hooked_u1()
    assert u1.adapter.gcode is gcode
    assert len(feed_handlers(printer)) == 1
    left._set_channel_state(1, "unload_finish", True)
    eject = u1.adapter.eject_timers[0]
    eject[1] = eject[0](0.0)
    seq = u1.adapter.ejects[0].seq
    feed_handlers(printer)[0](0, event("motor_stalled", seq))
    assert gcode.lines[-1] == "ace2k_u1: lane 1 eject failed: motor_stalled — ACE_CLEAR LANE=1"


def test_the_feed_events_are_registered_once():
    u1, printer, gcode, left = hooked_u1()
    u1._hook(0.0)
    assert len(feed_handlers(printer)) == 1


UNLOAD_STEPS = ("unload_prepare", "unload_homing", "unload_picking", "unload_heating")


def test_a_loaded_heads_unload_keeps_the_follow_through_its_tip_forming():
    adapter, reactor, left, right, ace = setup()
    loaded(left, 1)
    for state in UNLOAD_STEPS + ("unload_heat_finish", "unload_doing"):
        left._set_channel_state(1, state)
    assert ace.feed.stopped == []
    assert assists(ace) == [(0, "assist_both")]  # armed at load_finish: no second start


def test_unload_doing_arms_a_lane_not_armed_before():
    adapter, reactor, left, right, ace = setup()
    for state in UNLOAD_STEPS + ("unload_heat_finish",):
        left._set_channel_state(1, state)
    assert ace.feed.started == []
    left._set_channel_state(1, "unload_doing")
    assert assists(ace) == [(0, "assist_both")]


def test_unload_finish_stops_then_ejects():
    adapter, reactor, left, right, ace = setup()
    order = []
    stop, start = ace.feed.stop, ace.feed.start_move
    ace.feed.stop = lambda lane: order.append(("stop", lane)) or stop(lane)
    ace.feed.start_move = lambda *a: order.append(("start", a[0], a[1])) or start(*a)
    left._set_channel_state(1, "unload_doing")
    order.clear()
    left._set_channel_state(1, "unload_finish", True)
    assert order == [("stop", 0)]  # the eject is not started from under the U1's lock
    reactor.run_timers()
    assert order == [("stop", 0), ("start", 0, "unload")]
    assert adapter.ejects[0].mode == "unload" and adapter.eject_timers == {}
    assert not next(h for h in adapter.hooked if h.lane == 0).armed


def test_an_eject_refused_busy_is_retried_until_accepted():
    adapter, reactor, left, right, ace = setup()
    ace.feed.refusals = ["busy", "busy"]
    left._set_channel_state(1, "unload_finish", True)
    reactor.run_timers(reactor.now + ace2k_u1.EJECT_RETRY_S)
    assert ace.feed.attempts == 3
    assert ace.feed.started[-1][:2] == (0, "unload") and 0 in adapter.ejects
    assert adapter.gcode.lines == [] and reactor.timers == []


def test_an_eject_still_busy_past_the_cap_is_a_line():
    adapter, reactor, left, right, ace = setup()
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "unload_finish", True)
    reactor.run_timers(reactor.now + 60.0)
    assert ace.feed.attempts <= ace2k_u1.EJECT_RETRY_S / ace2k_u1.EJECT_RETRY_POLL_S + 1
    assert len(adapter.gcode.lines) == 1 and "eject not started" in adapter.gcode.lines[0]
    assert adapter.ejects == {} and reactor.timers == []


def test_any_other_eject_refusal_is_a_line_at_once():
    adapter, reactor, left, right, ace = setup()
    ace.feed.refuse = "no_filament"
    unloaded(left, 1, reactor)
    assert ace.feed.attempts == 1
    assert len(adapter.gcode.lines) == 1 and "eject not started" in adapter.gcode.lines[0]


def test_unload_fail_stops_the_lane_and_drops_a_pending_eject():
    adapter, reactor, left, right, ace = setup()
    loaded(left, 1)
    left._set_channel_state(1, "unload_prepare", True)
    left._set_channel_state(1, "unload_doing")
    left._set_channel_state(1, "unload_fail")
    assert ace.feed.stopped == [0]
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "unload_finish", True)
    left._set_channel_state(1, "unload_fail")
    assert reactor.timers == []


def test_a_follow_the_unit_ends_is_armed_again_by_the_next_pull_state():
    for kind, mode, by_seq in (
        ("assist_stall", "following", False),
        ("stopped", "idle", True),
        ("stopped_link", "following", False),
        ("stopped", "assisting_back", False),  # a one-way assist left by a manual ACE_ASSIST
        ("stopped", "assisting", False),
    ):
        adapter, reactor, left, right, ace = setup()
        left._set_channel_state(1, "load_extruding")
        seq = ace.feed.started[-1][4].seq if by_seq else 999
        adapter.on_feed_event(0, event(kind, seq, mode=mode))
        left._set_channel_state(1, "load_extruding")
        assert assists(ace) == [(0, "assist_both"), (0, "assist_both")], kind


def test_notices_and_other_lanes_events_keep_the_follow_armed():
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "load_extruding")
    seq = ace.feed.started[-1][4].seq
    adapter.on_feed_event(0, event("snag", seq, mode="following"))
    adapter.on_feed_event(1, event("stopped", seq, mode="following"))
    adapter.on_feed_event(0, event("done", seq + 50, mode="feeding"))
    left._set_channel_state(1, "load_flushing")
    assert assists(ace) == [(0, "assist_both")]


def test_the_eject_retry_does_not_depend_on_the_report_rate():
    adapter, reactor, left, right, ace = build()
    ace.feed.report_hz = 0.1  # one report per 10 s, longer than the whole retry window
    adapter = ace2k_u1.Adapter(
        reactor, adapter.module, ace, adapter.feeds, (1, 2, 3, 4), 30.0, 2000.0, 0.2
    )
    adapter.gcode = Gcode()
    assert adapter.hook_all() == []
    ace.feed.refusals = ["busy", "busy"]
    left._set_channel_state(1, "unload_finish", True)
    reactor.run_timers(reactor.now + ace2k_u1.EJECT_RETRY_S)
    assert ace.feed.attempts == 3
    assert ace.feed.started[-1][:2] == (0, "unload") and 0 in adapter.ejects
    assert adapter.gcode.lines == []


def test_the_adapter_never_arms_the_forward_assist():
    adapter, reactor, left, right, ace = setup()
    for state in ("load_extruding", "load_flushing", "load_finish", "unload_prepare"):
        left._set_channel_state(1, state)
    left._set_channel_state(1, "unload_doing")
    unloaded(left, 1, reactor)
    left._set_channel_state(1, "manual_sta_extruding")
    left._set_channel_state(1, "manual_sta_flushing")
    modes = {s[1] for s in ace.feed.started}
    assert modes == {"assist_both", "unload"}, modes


# --- the pauses: a lane error during a print, the runout source ------------------------------


class PauseLog:
    """The U1's objects a pause goes through, recording into one ordered log."""

    def __init__(self, reactor):
        self.reactor = reactor
        self.log = []
        self.lines = []
        outer = self

        class PauseResume:
            def send_pause_command(self):
                outer.log.append(("send_pause_command", outer.reactor.now))

        class Exceptions(fakes.ExceptionManager):
            def raise_exception_async(self, **kw):
                outer.log.append(("raise_exception_async", kw))

        self.pause_resume = PauseResume()
        self.exceptions = Exceptions()

    def send_event(self, name, *args):
        self.log.append(("send_event", name, args))

    def respond_info(self, msg):
        self.lines.append(msg)

    def run_script(self, script):
        self.log.append(("run_script", script, self.reactor.now))


def pausing(state="printing", source="head", task_config=None):
    adapter, reactor, left, right, ace = build()
    u1 = PauseLog(reactor)
    adapter.gcode = u1
    assert adapter.hook_all() == []
    stats = fakes.FakePrintStats(state)
    adapter.setup_pause(stats, u1.pause_resume, u1.exceptions, u1.send_event, source, task_config)
    left._set_channel_state(1, "load_finish", True)  # lane 1 -> e0, following
    ace.lanes[0]["mode"] = "following"
    return adapter, reactor, left, ace, stats, u1


TANGLE = [
    (
        "send_event",
        "print_stats:update_exception_info",
        (523, 0, 38, "detect filament tangled!", 2),
    ),
    ("send_pause_command", None),
    ("send_event", "filament_entangle_detect:tangled", (0,)),
    (
        "raise_exception_async",
        dict(id=523, index=0, code=38, message="detect filament tangled!", oneshot=1, level=2),
    ),
    ("run_script", "\nPAUSE\nM400\n", None),
]


def untimed(log):
    """The log without the times (the pause command and the scripts carry them)."""
    return [e[:-1] + (None,) if e[0] in ("send_pause_command", "run_script") else e for e in log]


def test_a_lane_error_in_the_follow_during_a_print_is_the_u1s_tangle_pause():
    for kind in ("stuck", "tangled", "timeout", "motor_stalled"):
        adapter, reactor, left, ace, stats, u1 = pausing()
        adapter.on_feed_event(0, event(kind, 999, mode="following"))
        assert u1.log == [], kind  # from the pause timer, not from the event handler
        assert u1.lines == [f"ace2k_u1: lane 1 {kind} in the follow — pausing the print (e0)"]
        reactor.run_timers()
        assert untimed(u1.log) == TANGLE, kind


def test_a_lane_error_outside_a_print_is_one_line():
    for state in ("standby", "paused", "complete", None):
        adapter, reactor, left, ace, stats, u1 = pausing(state)
        adapter.on_feed_event(0, event("stuck", 999, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == ["ace2k_u1: lane 1 stuck in the follow — ACE_CLEAR LANE=1"], state


def test_an_error_outside_the_follow_or_a_notice_does_not_pause():
    adapter, reactor, left, ace, stats, u1 = pausing()
    for kind, mode in (
        ("stuck", "feeding"),
        ("tangled", "unloading"),
        ("timeout", "loading"),
        ("runout", "following"),
        ("tail_out", "following"),
        ("snag", "following"),
        ("unload_incomplete", "following"),
    ):
        adapter.on_feed_event(0, event(kind, 999, mode=mode))
    reactor.run_timers()
    assert u1.log == []


def test_a_second_lane_error_after_the_pause_is_a_line():
    adapter, reactor, left, ace, stats, u1 = pausing()
    adapter.on_feed_event(0, event("stuck", 999, mode="following"))
    adapter.on_feed_event(1, event("tangled", 999, mode="following"))

    def paused(script):  # the U1's PAUSE moves print_stats to paused
        u1.log.append(("run_script", script, reactor.now))
        stats.state = "paused"

    u1.run_script = paused
    reactor.run_timers()
    assert untimed(u1.log) == TANGLE
    assert u1.lines[-1] == "ace2k_u1: lane 2 tangled: the print is paused, not paused again"


def test_the_event_handler_never_raises():
    adapter, reactor, left, ace, stats, u1 = pausing()

    class Broken:
        @property
        def state(self):
            raise RuntimeError("print_stats")

    adapter.print_stats = Broken()
    adapter.on_feed_event(0, event("stuck", 999, mode="following"))
    adapter.on_feed_event(0, event("tail", 999, mode="following"))
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))


def test_a_failing_pause_is_a_line_not_an_error():
    adapter, reactor, left, ace, stats, u1 = pausing()

    def send_event(name, *args):
        raise RuntimeError("no listener")

    adapter.send_event = send_event
    adapter.on_feed_event(0, event("stuck", 999, mode="following"))
    reactor.run_timers()
    assert u1.lines[-1] == "ace2k_u1: lane 1 stuck: the pause failed — no listener"
    assert reactor.timers[0][1] == reactor.NEVER


def test_the_tail_notice_with_the_head_as_runout_source_is_a_line():
    for state in ("printing", "standby"):
        adapter, reactor, left, ace, stats, u1 = pausing(state, "head")
        h = next(h for h in adapter.hooked if h.lane == 0)
        adapter.on_feed_event(0, event("tail", h.assist_seq, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == ["ace2k_u1: lane 1 ran out at the bay — its tail feeds on to e0"]
        assert h.armed and ace.feed.stopped == [], state


RUNOUT = [
    ("send_event", "print_stats:update_exception_info", (523, 0, 0, "e0_filament runout", 2)),
    ("send_pause_command", None),
    ("run_script", "PAUSE IS_RUNOUT=1\n\nM400", None),
    ("run_script", "\nM400\nINNER_AUTO_REPLENISH_FILAMENT EXTRUDER=0\n", None),
    (
        "raise_exception_async",
        dict(id=523, index=0, code=0, message="e0_filament runout", oneshot=0, level=2),
    ),
]


END_OUT = "ace2k_u1: lane 1: the end is out of the unit — a new filament can go in"
END_OUT_AGAIN = (
    "ace2k_u1: lane 1: the end is out of the unit — pull lane 1's new filament back out of the bay"
    " and push it in again until the gear takes it"
)


def test_the_tail_notice_with_the_unit_as_runout_source_is_a_line_only():
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    h = next(h for h in adapter.hooked if h.lane == 0)
    adapter.on_feed_event(0, event("tail", 999, mode="following"))
    reactor.run_timers()
    assert u1.log == []
    assert u1.lines == ["ace2k_u1: lane 1 ran out at the bay — its tail feeds on to e0"]
    assert h.armed and ace.feed.stopped == []


def test_the_tail_out_with_the_unit_as_runout_source_is_the_u1s_runout_pause():
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    assert u1.log == []  # from the pause timer, not from the event handler
    assert u1.lines == [END_OUT + "; pausing the print (e0)"]  # one line per tail_out
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT
    # its pause delay between the pause command and PAUSE
    assert u1.log[2][2] - u1.log[1][1] == ace2k_u1.RUNOUT_PAUSE_DELAY_S


def test_the_runout_pause_reads_the_head_sensors_name_and_delay():
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    left.runout_sensor[1].runout_helper = types.SimpleNamespace(name="e0_sensor", pause_delay=2.0)
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert u1.log[0][2][3] == "e0_sensor runout"
    assert u1.log[2][2] - u1.log[1][1] == 2.0


def test_a_replenish_takes_the_runout_exception_away():
    cfg = types.SimpleNamespace(perform_auto_replenish=True, is_exec_print_end_action=False)
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT[:-1]


def test_a_failing_replenish_still_raises_the_runout_exception():
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    run = u1.run_script

    def run_script(script):
        run(script)
        if "REPLENISH" in script:
            raise RuntimeError("replenish")

    u1.run_script = run_script
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT


def test_no_runout_pause_during_the_prints_end():
    cfg = types.SimpleNamespace(perform_auto_replenish=False, is_exec_print_end_action=True)
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert u1.log == []
    assert u1.lines[-1] == "ace2k_u1: lane 1 ran out during the print's end; not paused"


def test_the_tail_out_with_the_head_as_runout_source_is_a_line_only():
    for state in ("printing", "standby"):
        adapter, reactor, left, ace, stats, u1 = pausing(state, "head")
        adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == [END_OUT], state


def test_the_tail_out_outside_a_print_is_a_line_with_the_unit_as_source():
    for state in ("standby", "paused"):
        adapter, reactor, left, ace, stats, u1 = pausing(state, "unit")
        adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == [END_OUT], state


def test_the_tail_outs_line_follows_the_insert_at_that_moment():
    adapter, reactor, left, ace, stats, u1 = pausing("standby", "head")
    ace.lanes[0]["insert"] = True  # a filament put in during the tail
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    assert u1.lines == [END_OUT_AGAIN]
    ace.lanes[0]["insert"] = False
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    assert u1.lines == [END_OUT_AGAIN, END_OUT]  # one line per tail_out


def test_the_tail_out_with_the_unit_as_source_and_a_filament_in_says_both():
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    ace.lanes[0]["insert"] = True
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert u1.lines[0] == END_OUT_AGAIN + "; pausing the print (e0)"
    assert END_OUT_AGAIN not in u1.lines
    assert untimed(u1.log) == RUNOUT


def test_the_tail_notice_outside_a_print_is_a_line_whatever_the_source():
    for state in ("standby", "paused"):
        adapter, reactor, left, ace, stats, u1 = pausing(state, "unit")
        adapter.on_feed_event(0, event("tail", 999, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == ["ace2k_u1: lane 1 ran out at the bay — its tail feeds on to e0"]


def test_a_failing_runout_pause_script_still_replenishes_and_raises():
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    run = u1.run_script

    def run_script(script):
        run(script)
        if script.startswith("PAUSE"):
            raise RuntimeError("pause")

    u1.run_script = run_script
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT


def test_a_re_entered_pause_timer_runs_each_pause_once():
    # Klipper runs a timer again when update_timer(NOW) comes while its callback waits in a
    # G-code: the re-entered call must leave the queue to the running one
    adapter, reactor, left, ace, stats, u1 = pausing()
    run = u1.run_script
    reentered = []

    def run_script(script):
        run(script)
        if not reentered:
            reentered.append(True)
            adapter.on_feed_event(1, event("tangled", 999, mode="following"))
            before = len(u1.log)
            assert adapter._run_pauses(reactor.now) == reactor.NEVER  # the reactor's re-entry
            reentered.append(len(u1.log) - before)  # nothing run inside lane 1's G-code

    u1.run_script = run_script
    adapter.on_feed_event(0, event("stuck", 999, mode="following"))
    reactor.run_timers()
    scripts = [e for e in u1.log if e[0] == "run_script"]
    assert len(scripts) == 2  # lane 1's pause, then lane 2's, each once
    assert reentered == [True, 0]
    heads = [e[2][1] for e in u1.log if e[0] == "send_event" and e[1].startswith("print_stats")]
    assert heads == [0, 1]
    assert not adapter.pause_running and adapter.pause_pending == []


class Backups:
    def __init__(self, **kw):
        self.backups = []
        self.perform_auto_replenish = False
        self.is_exec_print_end_action = False
        self.__dict__.update(kw)

    def backup_filament_info(self, extruder_index=None):
        self.backups.append(extruder_index)


def test_the_runout_pause_backs_up_the_heads_filament_first():
    cfg = Backups()
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert cfg.backups == [0]
    assert untimed(u1.log) == RUNOUT


def test_a_disabled_head_sensor_means_no_runout_pause():
    cfg = Backups()
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    left.runout_sensor[1].runout_helper = types.SimpleNamespace(
        name="e0_filament", pause_delay=0.5, sensor_enabled=0, runout_pause=True
    )
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert u1.log == [] and cfg.backups == []
    assert u1.lines[-1] == "ace2k_u1: lane 1 ran out; e0's sensor is disabled: not paused"


def test_pause_on_runout_off_means_a_line_after_the_backup():
    cfg = Backups()
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    left.runout_sensor[1].runout_helper = types.SimpleNamespace(
        name="e0_filament", pause_delay=0.5, sensor_enabled=1, runout_pause=False
    )
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert u1.log == [] and cfg.backups == [0]
    assert u1.lines[-1] == "ace2k_u1: lane 1 ran out; e0's pause on runout is off"
