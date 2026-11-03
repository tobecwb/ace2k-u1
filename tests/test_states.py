"""The adapter's channel states: the follow (the both-way assist) armed from the U1's extrusion
on, on a loaded head and through the unload's tip forming, the lane stopped on every failure and
at an unload's end, the eject after that stop and its error notice; the pauses: a lane error in
the follow during a print (the U1's tangle pause) and the tail's end (tail_out) by runout_source
(the U1's runout pause, or a line); the tail notice and every tail_out's line."""

from __future__ import annotations

import types
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1
import ace2k_u1_adapter
import ace2k_u1_common
import ace2k_u1_eject
import ace2k_u1_hooks
import ace2k_u1_pause
import fakes
import pytest
from test_check import Config, Printer
from test_check import Gcode as CheckGcode
from test_presence import build

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import SimpleNamespace

    from ace2k_klipper_types import FeedEvent
    from ace2k_u1_adapter import Adapter
    from ace2k_u1_common import Hooked
    from fakes import FakeAce2k, FakeFeed, FakePrintStats, FakeReactor


class Gcode:
    def __init__(self) -> None:
        self.lines = []

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)


def setup() -> tuple[Adapter, FakeReactor, FakeFeed, FakeFeed, FakeAce2k]:
    adapter, reactor, left, right, ace = build()
    adapter.gcode = Gcode()
    adapter.hook_all()
    return adapter, reactor, left, right, ace


def unloaded(feed: FakeFeed, ch: int, reactor: FakeReactor) -> None:
    """The U1's unload finished; the eject's timer then runs."""
    feed._set_channel_state(ch, "unload_finish", True)
    reactor.run_timers()


def event(kind: str, seq: int, mode: str = "unloading") -> FeedEvent:
    return {"kind": kind, "mode": mode, "motor_mm": 1.0, "filament_mm": 0.0, "seq": seq}


def test_load_finish_arms_the_follow() -> None:
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "load_finish", True)
    assert ("state", 1, "load_finish", True) in left.calls
    assert ace.feed.started[-1][:4] == (0, "assist_both", 0.0, 30.0)


def test_the_original_call_runs_first_with_the_same_arguments() -> None:
    adapter, reactor, left, right, ace = setup()
    seen = []
    ace.feed.start_move = lambda *a: seen.append(list(left.calls)) or fakes.FakeMove(0, "x", 1)
    left._set_channel_state(1, "load_finish", True)
    assert seen == [[("state", 1, "load_finish", True)]]
    left._set_channel_state(1, "wait_insert")
    assert left.calls[-1] == ("state", 1, "wait_insert", False)


def test_a_follow_refusal_is_a_line_not_an_error() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "load_finish", True)
    assert any("not armed" in line for line in adapter.gcode.lines)


def test_a_busy_refusal_on_a_following_lane_is_silent() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.lanes[0]["mode"] = "following"
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "load_finish", True)
    assert adapter.gcode.lines == []
    ace.feed.refuse = "no_filament"  # any other refusal is still told
    left.channel_state[1] = "load_flushing"  # a load again (load_finish over itself is none)
    left._set_channel_state(1, "load_finish", True)
    assert len(adapter.gcode.lines) == 1 and "not armed" in adapter.gcode.lines[0]


def test_a_value_or_runtime_error_is_a_line_too() -> None:
    adapter, reactor, left, right, ace = setup()
    for exc in (ValueError("bad lane"), RuntimeError("no link")):

        def start_move(*a: object, exc: Exception = exc, **_kw: object) -> NoReturn:
            raise exc

        ace.feed.start_move = start_move
        left._set_channel_state(1, "load_finish", True)
        unloaded(left, 1, reactor)
    assert sum("not armed" in line for line in adapter.gcode.lines) == 2
    assert sum("eject not started" in line for line in adapter.gcode.lines) == 2
    assert adapter.ejector.ejects == {}


def test_failures_stop_the_lane_and_unload_prepare_does_not() -> None:
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "unload_prepare")
    assert ace.feed.stopped == []
    left._set_channel_state(1, "load_fail")
    left._set_channel_state(1, "unload_fail")
    assert ace.feed.stopped == [0, 0]
    for state in ("unload_fail", "manual_sta_fail", "preload_fail", "manual_sta_flush_fail"):
        right._set_channel_state(0, state)
    assert ace.feed.stopped == [0, 0, 2, 2, 2, 2]  # right channel 0 -> lane 3


def loaded(feed: FakeFeed, ch: int) -> None:
    """A head that finished a load (its assist armed)."""
    feed._set_channel_state(ch, "load_finish", True)


def test_a_head_runout_after_a_load_stops_the_lane() -> None:
    adapter, reactor, left, right, ace = setup()
    loaded(left, 1)
    left._set_channel_state(1, "preload_finish", True)  # runout, the filament still in the bay
    assert ace.feed.stopped == [0]


def test_the_manual_feeds_finish_on_an_empty_head_stops_the_lane() -> None:
    adapter, reactor, left, right, ace = setup()
    loaded(left, 1)
    left._set_channel_state(1, "wait_insert", True)
    loaded(right, 0)
    right._set_channel_state(0, "none", True)
    assert ace.feed.stopped == [0, 2]


def test_load_finish_set_again_does_not_stop() -> None:
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


def assists(ace: FakeAce2k) -> list[tuple[object, ...]]:
    return [s[:2] for s in ace.feed.started if s[1] == "assist_both"]


def test_the_pull_states_are_checked_constants() -> None:
    for name in ace2k_u1_hooks.PULL_STATES:
        assert name in ace2k_u1_hooks.CONSTANTS
    assert tuple(ace2k_u1_hooks.CONSTANTS[n] for n in ace2k_u1_hooks.PULL_STATES) == PULL


def test_each_pull_state_arms_the_follow_once() -> None:
    for state in PULL:
        adapter, reactor, left, right, ace = setup()
        left._set_channel_state(1, state)
        assert left.calls[-1] == ("state", 1, state, False), state
        assert ace.feed.started[-1][:4] == (0, "assist_both", 0.0, 30.0), state
        assert assists(ace) == [(0, "assist_both")], state
        assert ace.feed.stopped == [], state


def test_a_pull_state_set_twice_sends_one_start() -> None:
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_extruding")
    assert assists(ace) == [(0, "assist_both")]


def test_the_preload_alone_never_arms() -> None:
    adapter, reactor, left, right, ace = setup()
    for state in ("preload_prepare", "preload_feeding", "preload_finish"):
        left._set_channel_state(1, state, True)
    assert ace.feed.started == [] and ace.feed.stopped == []


def test_a_load_failing_while_extruding_disarms() -> None:
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_fail")
    assert assists(ace) == [(0, "assist_both")]
    assert ace.feed.stopped == [0]
    left._set_channel_state(1, "load_extruding")  # the next load arms again
    assert assists(ace) == [(0, "assist_both"), (0, "assist_both")]


def test_a_load_arms_once_from_the_extrusion_and_never_stops() -> None:
    adapter, reactor, left, right, ace = setup()
    for state in ("load_prepare", "load_heating", "load_extruding", "load_flushing"):
        left._set_channel_state(1, state)
    left._set_channel_state(1, "load_finish", True)
    assert assists(ace) == [(0, "assist_both")]
    assert ace.feed.stopped == []


def test_the_manual_feed_keeps_the_follow_armed_from_its_extrusion() -> None:
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


def test_a_refusal_while_arming_is_a_line_not_an_error() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refuse = "no_filament"
    left._set_channel_state(1, "load_extruding")
    assert left.calls[-1] == ("state", 1, "load_extruding", False)
    assert any("not armed" in line for line in adapter.gcode.lines)
    ace.feed.refuse = None  # not armed: the next pull state tries again
    left._set_channel_state(1, "load_flushing")
    assert assists(ace) == [(0, "assist_both")]


def test_a_busy_refusal_on_a_following_lane_is_silent_on_a_pull_state_too() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.lanes[0]["mode"] = "following"
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_flushing")
    assert adapter.gcode.lines == []


def test_a_filament_leaving_its_bay_disarms() -> None:
    adapter, reactor, left, right, ace = setup()
    h = next(h for h in adapter.hooked if h.lane == 0)
    h.present = True
    left._set_channel_state(1, "load_extruding")
    ace.lanes[0].update(insert=False, mode="assisting")  # the follow's own fall is the tail
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [0] and not h.armed
    left._set_channel_state(1, "load_extruding")
    assert assists(ace) == [(0, "assist_both"), (0, "assist_both")]


def u1_update_auto_mode(feed: FakeFeed) -> None:
    """The U1's own UPDATE_AUTO_MODE re-derivation (its _do_feed), for the hook-time call: a
    head left in load_finish is set to load_finish again, whatever the mode."""
    orig = feed._do_feed

    def do_feed(
        ch: int, action: str | None = None, stage: str | None = None, auto_mode: bool | None = None
    ) -> None:
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


def test_the_hook_time_re_derivation_arms_nothing() -> None:
    # the start is ace2k's (follow_default): the U1's state derived again at the hook puts a loaded
    # head back to load_finish, and no follow is started for it here
    for auto_mode in (True, False):
        adapter, reactor, left, right, ace = build()
        adapter.gcode = Gcode()
        left.config["auto_mode"] = [auto_mode, auto_mode]
        left.channel_state[1] = "load_finish"  # lane 1's head kept loaded across the restart
        u1_update_auto_mode(left)
        assert adapter.hook_all() == []
        reactor.run_async()
        assert left.channel_state[1] == "load_finish", auto_mode
        assert ace.feed.started == [] and ace.feed.stopped == [], auto_mode
        left._set_channel_state(1, "load_flushing")  # a load after the hook still arms
        left._set_channel_state(1, "load_finish", True)
        assert [s[:2] for s in ace.feed.started] == [(0, "assist_both")], auto_mode


def test_a_failing_stop_is_a_line_not_an_error() -> None:
    adapter, reactor, left, right, ace = setup()

    def stop(lane: int) -> NoReturn:
        raise RuntimeError("no link")

    ace.feed.stop = stop
    left._set_channel_state(1, "load_fail")
    assert left.calls[-1] == ("state", 1, "load_fail", False)
    assert any("not stopped" in line for line in adapter.gcode.lines)


def test_unload_finish_ejects_and_an_error_event_is_a_notice() -> None:
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    assert ace.feed.started[-1][:4] == (0, "unload", 0.0, 30.0)
    seq = adapter.ejector.ejects[0].seq
    adapter.on_feed_event(0, event("stuck", seq))
    assert adapter.gcode.lines == ["ace2k_u1: lane 1 eject failed: stuck — ACEK_CLEAR LANE=1"]
    assert 0 not in adapter.ejector.ejects


def test_a_clean_eject_is_silent() -> None:
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    seq = adapter.ejector.ejects[0].seq
    adapter.on_feed_event(0, event("unloaded", seq))
    assert adapter.gcode.lines == []
    assert 0 not in adapter.ejector.ejects


def test_a_stopped_eject_is_dropped_silently() -> None:
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    seq = adapter.ejector.ejects[0].seq
    adapter.on_feed_event(0, event("stopped", seq))
    assert adapter.gcode.lines == []
    assert 0 not in adapter.ejector.ejects


def test_notices_and_other_moves_leave_the_eject_waiting() -> None:
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    seq = adapter.ejector.ejects[0].seq
    adapter.on_feed_event(0, event("behind", seq))
    adapter.on_feed_event(0, event("snag", seq))
    adapter.on_feed_event(0, event("stuck", seq + 100))
    adapter.on_feed_event(1, event("stuck", seq))
    assert 0 in adapter.ejector.ejects and adapter.gcode.lines == []
    adapter.on_feed_event(0, event("timeout", seq))
    assert adapter.gcode.lines == ["ace2k_u1: lane 1 eject failed: timeout — ACEK_CLEAR LANE=1"]


def test_a_second_eject_replaces_the_first() -> None:
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    first = adapter.ejector.ejects[0].seq
    unloaded(left, 1, reactor)
    second = adapter.ejector.ejects[0].seq
    assert second != first
    adapter.on_feed_event(0, event("stuck", first))
    assert adapter.gcode.lines == [] and adapter.ejector.ejects[0].seq == second


def test_unhooked_channels_get_the_original_only() -> None:
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
    def __init__(self, *args: object) -> None:  # the arguments of test_check.Printer
        super().__init__(*args)
        self.handlers = []

    def register_event_handler(self, name: str, cb: Callable[..., object]) -> None:
        self.handlers.append((name, cb))


def hooked_u1() -> tuple[ace2k_u1.ACE2kU1, EventPrinter, CheckGcode, FakeFeed]:
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    gcode = CheckGcode()
    printer = EventPrinter(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left, right])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    return u1, printer, gcode, left


def feed_handlers(printer: EventPrinter) -> list[Callable[..., object]]:
    return [cb for name, cb in printer.handlers if name == "ace2k:feed_event"]


def test_the_hook_wires_the_console_and_the_feed_events() -> None:
    u1, printer, gcode, left = hooked_u1()
    assert u1.adapter.gcode is gcode
    assert len(feed_handlers(printer)) == 1
    left._set_channel_state(1, "unload_finish", True)
    eject = u1.adapter.ejector.eject_timers[0]
    eject[1] = eject[0](0.0)
    seq = u1.adapter.ejector.ejects[0].seq
    feed_handlers(printer)[0](0, event("motor_stalled", seq))
    assert gcode.lines[-1] == "ace2k_u1: lane 1 eject failed: motor_stalled — ACEK_CLEAR LANE=1"


def test_the_feed_events_are_registered_once() -> None:
    u1, printer, gcode, left = hooked_u1()
    u1._hook(0.0)
    assert len(feed_handlers(printer)) == 1


UNLOAD_STEPS = ("unload_prepare", "unload_homing", "unload_picking", "unload_heating")


def test_a_loaded_heads_unload_keeps_the_follow_through_its_tip_forming() -> None:
    adapter, reactor, left, right, ace = setup()
    loaded(left, 1)
    for state in UNLOAD_STEPS + ("unload_heat_finish", "unload_doing"):
        left._set_channel_state(1, state)
    assert ace.feed.stopped == []
    assert assists(ace) == [(0, "assist_both")]  # armed at load_finish: no second start


def test_unload_doing_arms_a_lane_not_armed_before() -> None:
    adapter, reactor, left, right, ace = setup()
    for state in UNLOAD_STEPS + ("unload_heat_finish",):
        left._set_channel_state(1, state)
    assert ace.feed.started == []
    left._set_channel_state(1, "unload_doing")
    assert assists(ace) == [(0, "assist_both")]


def test_unload_finish_stops_then_ejects() -> None:
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
    assert adapter.ejector.ejects[0].mode == "unload" and adapter.ejector.eject_timers == {}
    assert not next(h for h in adapter.hooked if h.lane == 0).armed


def test_an_eject_refused_busy_is_retried_until_accepted() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refusals = ["busy", "busy"]
    left._set_channel_state(1, "unload_finish", True)
    reactor.run_timers(reactor.now + ace2k_u1_eject.EJECT_RETRY_S)
    assert ace.feed.attempts == 3
    assert ace.feed.started[-1][:2] == (0, "unload") and 0 in adapter.ejector.ejects
    assert adapter.gcode.lines == [] and reactor.timers == []


def test_an_eject_still_busy_past_the_cap_is_a_line() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "unload_finish", True)
    reactor.run_timers(reactor.now + 60.0)
    assert ace.feed.attempts <= ace2k_u1_eject.EJECT_RETRY_S / ace2k_u1_common.BUSY_RETRY_POLL_S + 1
    assert len(adapter.gcode.lines) == 1 and "eject not started" in adapter.gcode.lines[0]
    assert adapter.ejector.ejects == {} and reactor.timers == []


def test_any_other_eject_refusal_is_a_line_at_once() -> None:
    adapter, reactor, left, right, ace = setup()
    ace.feed.refuse = "no_filament"
    unloaded(left, 1, reactor)
    assert ace.feed.attempts == 1
    assert len(adapter.gcode.lines) == 1 and "eject not started" in adapter.gcode.lines[0]


def test_unload_fail_stops_the_lane_and_drops_a_pending_eject() -> None:
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


def test_a_follow_the_unit_ends_is_armed_again_by_the_next_pull_state() -> None:
    for kind, mode, by_seq in (
        ("assist_stall", "following", False),
        ("stopped", "idle", True),
        ("stopped_link", "following", False),
        ("stopped", "assisting_back", False),  # a one-way assist left by a manual ACEK_ASSIST
        ("stopped", "assisting", False),
    ):
        adapter, reactor, left, right, ace = setup()
        left._set_channel_state(1, "load_extruding")
        seq = ace.feed.started[-1][4].seq if by_seq else 999
        adapter.on_feed_event(0, event(kind, seq, mode=mode))
        left._set_channel_state(1, "load_extruding")
        assert assists(ace) == [(0, "assist_both"), (0, "assist_both")], kind


def test_notices_and_other_lanes_events_keep_the_follow_armed() -> None:
    adapter, reactor, left, right, ace = setup()
    left._set_channel_state(1, "load_extruding")
    seq = ace.feed.started[-1][4].seq
    adapter.on_feed_event(0, event("snag", seq, mode="following"))
    adapter.on_feed_event(1, event("stopped", seq, mode="following"))
    adapter.on_feed_event(0, event("done", seq + 50, mode="feeding"))
    left._set_channel_state(1, "load_flushing")
    assert assists(ace) == [(0, "assist_both")]


def test_the_eject_retry_does_not_depend_on_the_report_rate() -> None:
    adapter, reactor, left, right, ace = build()
    ace.feed.report_hz = 0.1  # one report per 10 s, longer than the whole retry window
    adapter = ace2k_u1_adapter.Adapter(
        reactor,
        adapter.module,
        adapter.feeds,
        fakes.one_unit_heads(ace, (1, 2, 3, 4)),
        ace2k_u1_adapter.AdapterSetup(
            feed_speed=30.0,
            head_budget_mm=2000.0,
            poll_s=0.2,
            units=fakes.FakeUnits([ace]),
        ),
    )
    adapter.gcode = Gcode()
    assert adapter.hook_all() == []
    ace.feed.refusals = ["busy", "busy"]
    left._set_channel_state(1, "unload_finish", True)
    reactor.run_timers(reactor.now + ace2k_u1_eject.EJECT_RETRY_S)
    assert ace.feed.attempts == 3
    assert ace.feed.started[-1][:2] == (0, "unload") and 0 in adapter.ejector.ejects
    assert adapter.gcode.lines == []


def test_the_adapter_never_arms_the_forward_assist() -> None:
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

    def __init__(self, reactor: FakeReactor) -> None:
        self.reactor = reactor
        self.log = []
        self.lines = []
        outer = self

        class PauseResume:
            def send_pause_command(self) -> None:
                outer.log.append(("send_pause_command", outer.reactor.now))

        class Exceptions(fakes.ExceptionManager):
            def raise_exception_async(self, **kw: object) -> None:
                outer.log.append(("raise_exception_async", kw))

        self.pause_resume = PauseResume()
        self.exceptions = Exceptions()

    def send_event(self, name: str, *args: object) -> None:
        self.log.append(("send_event", name, args))

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)

    def run_script(self, script: str) -> None:
        self.log.append(("run_script", script, self.reactor.now))


def pausing(
    state: str = "printing",
    source: str = "head",
    task_config: Backups | SimpleNamespace | None = None,
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeAce2k, FakePrintStats, PauseLog]:
    adapter, reactor, left, right, ace = build()
    u1 = PauseLog(reactor)
    adapter.gcode = u1
    assert adapter.hook_all() == []
    stats = fakes.FakePrintStats(state)
    adapter.pause_runner.setup_pause(
        ace2k_u1_pause.PauseObjects(
            stats, u1.pause_resume, u1.exceptions, u1.send_event, source, task_config
        )
    )
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


def untimed(log: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    """The log without the times (the pause command and the scripts carry them)."""
    return [e[:-1] + (None,) if e[0] in ("send_pause_command", "run_script") else e for e in log]


def test_a_lane_error_in_the_follow_during_a_print_is_the_u1s_tangle_pause() -> None:
    for kind in ("stuck", "tangled", "timeout", "motor_stalled"):
        adapter, reactor, left, ace, stats, u1 = pausing()
        adapter.on_feed_event(0, event(kind, 999, mode="following"))
        assert u1.log == [], kind  # from the pause timer, not from the event handler
        line = f"ace2k_u1: lane 1 {kind} in the follow — pausing the print (e0)"
        assert u1.lines == [line]
        assert adapter.pause_reason == line  # the page's top bar shows the U1's pause too
        reactor.run_timers()
        assert untimed(u1.log) == TANGLE, kind


def test_a_lane_error_outside_a_print_is_one_line() -> None:
    for state in ("standby", "paused", "complete", None):
        adapter, reactor, left, ace, stats, u1 = pausing(state)
        adapter.on_feed_event(0, event("stuck", 999, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == ["ace2k_u1: lane 1 stuck in the follow — ACEK_CLEAR LANE=1"], state


def test_a_follow_stopped_for_stale_insert_sensors_pauses_a_print() -> None:
    # The unit stopped the follow of the active head's lane because it cannot read its insert
    # sensors (the ADC scan stopped). The head starves, and nothing may act on that unit, so the
    # print pauses as for a lane error: no swap, no refill.
    adapter, reactor, left, ace, stats, u1 = pausing()
    adapter.on_feed_event(0, event("stopped_sensors", 999, mode="following"))
    line = "ace2k_u1: lane 1 stopped_sensors in the follow — pausing the print (e0)"
    assert u1.lines == [line]
    assert adapter.pause_reason == line
    reactor.run_timers()
    assert untimed(u1.log) == TANGLE


def test_a_follow_stopped_for_stale_insert_sensors_outside_a_print_is_one_line() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("standby")
    adapter.on_feed_event(0, event("stopped_sensors", 999, mode="following"))
    reactor.run_timers()
    assert u1.log == []
    assert u1.lines == [
        "ace2k_u1: lane 1 stopped_sensors in the follow — the unit's insert sensors are stale;"
        " nothing to clear"
    ]


def test_an_error_outside_the_follow_or_a_notice_does_not_pause() -> None:
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


def test_a_second_lane_error_after_the_pause_is_a_line() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing()
    adapter.on_feed_event(0, event("stuck", 999, mode="following"))
    adapter.on_feed_event(1, event("tangled", 999, mode="following"))

    def paused(script: str) -> None:  # the U1's PAUSE moves print_stats to paused
        u1.log.append(("run_script", script, reactor.now))
        stats.state = "paused"

    u1.run_script = paused
    reactor.run_timers()
    assert untimed(u1.log) == TANGLE
    assert u1.lines[-1] == "ace2k_u1: lane 2 tangled: the print is paused, not paused again"


def test_the_event_handler_never_raises() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing()

    class Broken:
        @property
        def state(self) -> NoReturn:
            raise RuntimeError("print_stats")

    adapter.print_stats = Broken()
    adapter.on_feed_event(0, event("stuck", 999, mode="following"))
    adapter.on_feed_event(0, event("tail", 999, mode="following"))
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))


def test_a_failing_pause_is_a_line_not_an_error() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing()

    def send_event(name: str, *args: object) -> NoReturn:
        raise RuntimeError("no listener")

    adapter.send_event = send_event
    adapter.on_feed_event(0, event("stuck", 999, mode="following"))
    reactor.run_timers()
    assert u1.lines[-1] == "ace2k_u1: lane 1 stuck: the pause failed — no listener"
    assert reactor.timers[0][1] == reactor.NEVER


def test_the_tail_notice_with_the_head_as_runout_source_is_a_line() -> None:
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


def test_with_no_refill_the_unit_source_falls_back_to_the_u1s_runout_pause() -> None:
    # no refill to run (on_shared_runout unset): the lone head's lane feeds its rest on as the
    # tail, and the U1's runout pause pauses the print, once
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    assert adapter.on_shared_runout is None
    h = next(h for h in adapter.hooked if h.lane == 0)
    adapter.on_feed_event(0, event("tail", 999, mode="following"))
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT
    assert u1.lines[0] == (
        "ace2k_u1: lane 1 ran out at the bay — its tail feeds on to e0; pausing the print"
    )
    assert h.armed and ace.feed.stopped == [] and h.end_taken
    adapter.on_feed_event(0, event("tail", 999, mode="following"))  # a second notice: no pause
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT


def test_the_unit_source_keeps_the_tail_when_the_u1_would_not_pause() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    left.runout_sensor[1].runout_helper = types.SimpleNamespace(
        name="e0_filament", pause_delay=0.5, sensor_enabled=1, runout_pause=False
    )
    h = next(h for h in adapter.hooked if h.lane == 0)
    adapter.on_feed_event(0, event("tail", 999, mode="following"))
    reactor.run_timers()
    assert u1.log == []
    assert u1.lines == ["ace2k_u1: lane 1 ran out at the bay — its tail feeds on to e0"]
    assert h.armed and ace.feed.stopped == [] and not h.end_taken


def runout(adapter: Adapter) -> None:
    """The U1's runout pause for lane 1's head queued, as the refill's fallback runs it."""
    h = next(h for h in adapter.hooked if h.lane == 0)
    adapter.pause_runner.queue_pause("runout", h, "tail_out")


def test_the_runout_pause_is_the_u1s_runout_pause() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    runout(adapter)
    assert u1.log == []  # from the pause timer
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT
    # its pause delay between the pause command and PAUSE
    assert u1.log[2][2] - u1.log[1][1] == ace2k_u1_pause.RUNOUT_PAUSE_DELAY_S


def test_the_runout_pause_reads_the_head_sensors_name_and_delay() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    left.runout_sensor[1].runout_helper = types.SimpleNamespace(name="e0_sensor", pause_delay=2.0)
    runout(adapter)
    reactor.run_timers()
    assert u1.log[0][2][3] == "e0_sensor runout"
    assert u1.log[2][2] - u1.log[1][1] == 2.0


def test_a_replenish_takes_the_runout_exception_away() -> None:
    cfg = types.SimpleNamespace(perform_auto_replenish=True, is_exec_print_end_action=False)
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    runout(adapter)
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT[:-1]


def test_a_failing_replenish_still_raises_the_runout_exception() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    run = u1.run_script

    def run_script(script: str) -> None:
        run(script)
        if "REPLENISH" in script:
            raise RuntimeError("replenish")

    u1.run_script = run_script
    runout(adapter)
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT


def test_no_runout_pause_during_the_prints_end() -> None:
    cfg = types.SimpleNamespace(perform_auto_replenish=False, is_exec_print_end_action=True)
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    runout(adapter)
    reactor.run_timers()
    assert u1.log == []
    assert u1.lines[-1] == "ace2k_u1: lane 1 ran out during the print's end; not paused"


def test_the_tail_out_with_the_head_as_runout_source_is_a_line_only() -> None:
    for state in ("printing", "standby"):
        adapter, reactor, left, ace, stats, u1 = pausing(state, "head")
        adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == [END_OUT], state


def test_the_tail_out_outside_a_print_is_a_line_with_the_unit_as_source() -> None:
    for state in ("standby", "paused"):
        adapter, reactor, left, ace, stats, u1 = pausing(state, "unit")
        adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == [END_OUT], state


def test_the_tail_outs_line_follows_the_insert_at_that_moment() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("standby", "head")
    ace.lanes[0]["insert"] = True  # a filament put in during the tail
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    assert u1.lines == [END_OUT_AGAIN]
    ace.lanes[0]["insert"] = False
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    assert u1.lines == [END_OUT_AGAIN, END_OUT]  # one line per tail_out


def test_a_tail_out_never_pauses_with_the_unit_as_source() -> None:
    # the end of a print's spool is taken at the bay then (the refill); a tail_out that still
    # comes (the end not taken) is a line only
    for insert, line in ((True, END_OUT_AGAIN), (False, END_OUT)):
        adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
        ace.lanes[0]["insert"] = insert
        adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
        reactor.run_timers()
        assert u1.lines == [line] and u1.log == [], insert


def counted_tail(
    state: str = "printing", source: str = "head", encoder_mm: float = 60.0, insert: bool = False
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeAce2k, FakePrintStats, PauseLog, dict[int, float]]:
    """Lane 1's bay empties, its encoder runs encoder_mm on, then its tail_out comes.

    ``insert`` puts a filament into the bay before the tail_out. The extruder of e0 reads from
    the returned positions.
    """
    adapter, reactor, left, ace, stats, u1 = pausing(state, source)
    positions = {0: 500.0, 1: 0.0, 2: 0.0, 3: 0.0}
    adapter.extruder_position = lambda head: positions[head]
    ace.lanes[0].update(insert=True, encoder_mm=1000.0)
    adapter.watch(reactor.now)
    ace.lanes[0].update(insert=False, tail=True)  # the bay empties: the count starts
    adapter.watch(reactor.now)
    ace.lanes[0]["encoder_mm"] = 1000.0 + encoder_mm
    adapter.watch(reactor.now)
    ace.lanes[0]["insert"] = insert
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    ace.lanes[0].update(mode="idle", tail=False)
    return adapter, reactor, left, ace, stats, u1, positions


def extrude_to(
    adapter: Adapter, reactor: FakeReactor, positions: dict[int, float], mm: float
) -> None:
    positions[0] = 500.0 + mm
    reactor.now += 0.2
    adapter.watch(reactor.now)


def test_the_tail_outs_line_waits_for_100_mm_from_the_encoder_then_the_extruder() -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail()
    assert END_OUT not in u1.lines  # 60 mm only: the old end may still be in the gear
    extrude_to(adapter, reactor, positions, 39.0)
    assert END_OUT not in u1.lines  # 99 mm
    extrude_to(adapter, reactor, positions, 40.0)
    assert u1.lines.count(END_OUT) == 1  # 100 mm
    extrude_to(adapter, reactor, positions, 80.0)
    assert u1.lines.count(END_OUT) == 1  # once


def test_the_tail_outs_line_ignores_a_retraction() -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail()
    extrude_to(adapter, reactor, positions, 30.0)
    extrude_to(adapter, reactor, positions, 0.0)  # pulled back 30 mm: nothing counts
    extrude_to(adapter, reactor, positions, 39.0)  # and the 30 mm again count once
    assert END_OUT not in u1.lines
    extrude_to(adapter, reactor, positions, 40.0)
    assert END_OUT in u1.lines


def test_the_tail_outs_line_is_at_once_when_100_mm_are_counted_by_then() -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail(encoder_mm=100.0)
    assert u1.lines[-1] == END_OUT


def test_a_paused_print_holds_the_line_until_the_count_is_reached() -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail()
    stats.state = "paused"  # the U1's own runout, say
    for _ in range(5):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert END_OUT not in u1.lines
    stats.state = "printing"
    extrude_to(adapter, reactor, positions, 40.0)  # the print resumed and pulled on
    assert u1.lines.count(END_OUT) == 1


def test_a_filament_in_the_bay_at_the_tail_out_waits_for_the_count_too() -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail(insert=True)
    assert END_OUT_AGAIN not in u1.lines
    extrude_to(adapter, reactor, positions, 39.0)
    assert END_OUT_AGAIN not in u1.lines
    extrude_to(adapter, reactor, positions, 40.0)
    assert u1.lines.count(END_OUT_AGAIN) == 1 and END_OUT not in u1.lines


def test_a_filament_put_in_before_the_count_gets_the_pull_out_line() -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail()
    ace.lanes[0]["insert"] = True
    extrude_to(adapter, reactor, positions, 40.0)
    assert u1.lines.count(END_OUT_AGAIN) == 1 and END_OUT not in u1.lines


def test_a_failing_extruder_reader_says_the_line_at_once(caplog: pytest.LogCaptureFixture) -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "head")

    def broken(head: int) -> float:
        raise RuntimeError("no extruder")

    adapter.extruder_position = broken
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    assert u1.lines[-1] == END_OUT
    assert len([r for r in caplog.records if r.funcName == "_extruder_at"]) == 1


def test_a_reader_that_fails_later_says_the_line_then_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail()
    assert END_OUT not in u1.lines

    def broken(head: int) -> float:
        raise RuntimeError("no extruder")

    adapter.extruder_position = broken
    for _ in range(3):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert u1.lines.count(END_OUT) == 1
    assert len([r for r in caplog.records if r.funcName == "_extruder_at"]) == 1


@pytest.mark.parametrize(
    "drop",
    ["unload", "roll back", "print cancelled", "print complete"],
)
def test_the_waiting_line_is_dropped_when_the_piece_is_no_longer_the_lanes(drop: str) -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail()
    if drop == "unload":
        left._set_channel_state(1, "unload_heating", True)
    elif drop == "roll back":
        ace.lanes[0]["mode"] = "rolling_back"
    else:
        stats.state = drop.split()[1]
    reactor.now += 0.2
    adapter.watch(reactor.now)
    ace.lanes[0]["mode"] = "idle"
    left._set_channel_state(1, "load_finish", True)
    stats.state = "printing"
    extrude_to(adapter, reactor, positions, 200.0)
    assert END_OUT not in u1.lines


def test_a_lane_out_of_its_shared_head_drops_its_waiting_line() -> None:
    adapter, reactor, left, ace, stats, u1, positions = counted_tail()
    h = next(h for h in adapter.hooked if h.lane == 0)
    slot = types.SimpleNamespace(shared=True, current=None, wheel=None, lanes=[h], setting_for=None)
    adapter.slot_of = lambda lane: slot  # another lane is in e0 now
    extrude_to(adapter, reactor, positions, 200.0)
    assert END_OUT not in u1.lines and not h.out_due


def test_the_tail_notice_outside_a_print_is_a_line_whatever_the_source() -> None:
    for state in ("standby", "paused"):
        adapter, reactor, left, ace, stats, u1 = pausing(state, "unit")
        adapter.on_feed_event(0, event("tail", 999, mode="following"))
        reactor.run_timers()
        assert u1.log == [], state
        assert u1.lines == ["ace2k_u1: lane 1 ran out at the bay — its tail feeds on to e0"]


def test_a_failing_runout_pause_script_still_replenishes_and_raises() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    run = u1.run_script

    def run_script(script: str) -> None:
        run(script)
        if script.startswith("PAUSE"):
            raise RuntimeError("pause")

    u1.run_script = run_script
    runout(adapter)
    reactor.run_timers()
    assert untimed(u1.log) == RUNOUT


def test_a_re_entered_pause_timer_runs_each_pause_once() -> None:
    # Klipper runs a timer again when update_timer(NOW) comes while its callback waits in a
    # G-code: the re-entered call must leave the queue to the running one
    adapter, reactor, left, ace, stats, u1 = pausing()
    run = u1.run_script
    reentered = []

    def run_script(script: str) -> None:
        run(script)
        if not reentered:
            reentered.append(True)
            adapter.on_feed_event(1, event("tangled", 999, mode="following"))
            before = len(u1.log)
            assert (
                adapter.pause_runner._run_pauses(reactor.now) == reactor.NEVER
            )  # the reactor's re-entry
            reentered.append(len(u1.log) - before)  # nothing run inside lane 1's G-code

    u1.run_script = run_script
    adapter.on_feed_event(0, event("stuck", 999, mode="following"))
    reactor.run_timers()
    scripts = [e for e in u1.log if e[0] == "run_script"]
    assert len(scripts) == 2  # lane 1's pause, then lane 2's, each once
    assert reentered == [True, 0]
    heads = [e[2][1] for e in u1.log if e[0] == "send_event" and e[1].startswith("print_stats")]
    assert heads == [0, 1]
    assert not adapter.pause_runner.pause_running and adapter.pause_runner.pause_pending == []


class Backups:
    def __init__(self, **kw: object) -> None:
        self.backups = []
        self.perform_auto_replenish = False
        self.is_exec_print_end_action = False
        self.__dict__.update(kw)

    def backup_filament_info(self, extruder_index: int | None = None) -> None:
        self.backups.append(extruder_index)


def test_the_runout_pause_backs_up_the_heads_filament_first() -> None:
    cfg = Backups()
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    runout(adapter)
    reactor.run_timers()
    assert cfg.backups == [0]
    assert untimed(u1.log) == RUNOUT


def test_a_disabled_head_sensor_means_no_runout_pause() -> None:
    cfg = Backups()
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    left.runout_sensor[1].runout_helper = types.SimpleNamespace(
        name="e0_filament", pause_delay=0.5, sensor_enabled=0, runout_pause=True
    )
    runout(adapter)
    reactor.run_timers()
    assert u1.log == [] and cfg.backups == []
    assert u1.lines[-1] == "ace2k_u1: lane 1 ran out; e0's sensor is disabled: not paused"


def test_pause_on_runout_off_means_a_line_after_the_backup() -> None:
    cfg = Backups()
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit", cfg)
    left.runout_sensor[1].runout_helper = types.SimpleNamespace(
        name="e0_filament", pause_delay=0.5, sensor_enabled=1, runout_pause=False
    )
    runout(adapter)
    reactor.run_timers()
    assert u1.log == [] and cfg.backups == [0]
    assert u1.lines[-1] == "ace2k_u1: lane 1 ran out; e0's pause on runout is off"


def spent(kind: str, seq: int, mm: float) -> FeedEvent:
    return {"kind": kind, "mode": "unloading", "motor_mm": mm, "filament_mm": -mm, "seq": seq}


def test_an_eject_whose_unload_spends_its_budget_unloads_again() -> None:
    """The head is over 2 000 mm from the bay; the unit's unload
    ended unload_incomplete after 1 999.4 mm with the tip still in the tube. A whole move that
    carried the filament: the lane's error cleared and the unload started again, within
    head_budget_mm."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    assert ace.feed.cleared == [0]
    started = [s[:3] for s in ace.feed.started]
    assert started[0] == (0, "unload", 0.0)
    assert started[1][:2] == (0, "unload") and started[1][2] == pytest.approx(1000.6)  # the rest
    adapter.on_feed_event(0, spent("unloaded", adapter.ejector.ejects[0].seq, 130.0))
    assert adapter.gcode.lines == []
    assert 0 not in adapter.ejector.ejects


def test_an_eject_unload_past_its_budget_fails_as_before() -> None:
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    assert len(ace.feed.started) == 2  # 4 000 mm is past 3 000: no third
    assert adapter.gcode.lines == [
        "ace2k_u1: lane 1 eject failed: unload_incomplete — ACEK_CLEAR LANE=1"
    ]


def at_bay(mm: float) -> str:
    return (
        f"ace2k_u1: lane 1 ejected ({mm} mm back): its tail is past the unit's drive gear, at its"
        " bay: take it out by hand"
    )


def test_an_eject_whose_tail_stops_past_the_drive_gear_is_done_and_says_take_it_out() -> None:
    """A lane holding ~350 mm; the unload ended unload_incomplete after 353 mm, the tail past the
    drive gear under the bay's sensor. Out of the unit's reach, not a fault: the lane's error
    cleared, the operator told to take it out."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 353.0))
    reactor.run_timers()
    assert len(ace.feed.started) == 1  # not unloaded again
    assert ace.feed.cleared == [0]
    assert adapter.gcode.lines == [at_bay(353)]
    assert 0 not in adapter.ejector.ejects and 0 not in adapter.ejector.eject_spent


def test_an_eject_that_brings_nothing_back_says_the_tail_was_already_there() -> None:
    """An eject run again before the tail was taken out: the motor turns, nothing comes back."""
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    ev = {"kind": "unload_incomplete", "mode": "unloading", "motor_mm": 100.0}
    ev.update(filament_mm=0.0, seq=adapter.ejector.ejects[0].seq)
    adapter.on_feed_event(0, ev)
    assert ace.feed.cleared == [0]
    assert adapter.gcode.lines == [
        "ace2k_u1: lane 1: nothing came back: its tail is already past the unit's drive gear, at"
        " its bay: take it out by hand"
    ]


def test_a_second_unload_whose_tail_stops_at_the_bay_is_done_too() -> None:
    """The eject's first unload spent a whole move, the second ends short at the bay."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 130.0))
    reactor.run_timers()
    assert len(ace.feed.started) == 2 and ace.feed.cleared == [0, 0]
    assert adapter.gcode.lines == [at_bay(2129)]  # the whole eject's, both unloads


def test_a_second_unload_that_spends_its_short_budget_is_a_failure_not_the_bay() -> None:
    """head_budget_mm leaves 250 mm for the second unload; the unit spends it (motor 250) while
    the filament reads 220 (slip, the count): the budget ran out, the filament on its way."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 2249.4
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    assert ace.feed.started[1][2] == pytest.approx(250.0)
    ev = {"kind": "unload_incomplete", "mode": "unloading", "motor_mm": 250.0}
    ev.update(filament_mm=-220.0, seq=adapter.ejector.ejects[0].seq)
    adapter.on_feed_event(0, ev)
    assert adapter.gcode.lines == [
        "ace2k_u1: lane 1 eject failed: unload_incomplete — ACEK_CLEAR LANE=1"
    ]


@pytest.mark.parametrize("why", ["head_holds", "unreadable", "printing", "u1_flow", "no_motor"])
def test_a_short_unload_incomplete_is_a_failure_when_the_bay_is_not_sure(why: str) -> None:
    """The head's sensor on this lane's filament (or unread), a print outside its end, a U1 flow
    on the head, or no motor travel: the eject fails as before, nothing cleared."""
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    ev = spent("unload_incomplete", adapter.ejector.ejects[0].seq, 353.0)
    if why == "head_holds":
        adapter.head_set = lambda h: True
    elif why == "unreadable":

        def unread(h: Hooked) -> NoReturn:
            raise RuntimeError("no sensor")

        adapter.head_set = unread
    elif why == "printing":
        adapter.in_print = lambda: True
    elif why == "u1_flow":
        adapter.ejector.u1_flow = lambda h: True
    else:
        ev["motor_mm"] = None
    adapter.on_feed_event(0, ev)
    assert ace.feed.cleared == []
    assert adapter.gcode.lines[-1] == (
        "ace2k_u1: lane 1 eject failed: unload_incomplete — ACEK_CLEAR LANE=1"
    )


def test_a_parked_lane_at_the_bay_is_done_while_another_known_lane_sits_in_the_head() -> None:
    """A shared head: the sensor sees the current lane's filament, not this parked one's."""
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)
    adapter.head_set = lambda h: True
    adapter.parked_known = lambda h: True
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 353.0))
    assert ace.feed.cleared == [0] and adapter.gcode.lines == [at_bay(353)]


def test_an_eject_at_the_bay_whose_clear_fails_says_how_to_clear_it() -> None:
    adapter, reactor, left, right, ace = setup()
    unloaded(left, 1, reactor)

    def clear(lane: int) -> NoReturn:
        raise RuntimeError("no link")

    ace.feed.clear = clear
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 120.0))
    assert adapter.gcode.lines == [
        at_bay(120) + " — ACEK_CLEAR LANE=1 (its error not cleared: no link)"
    ]


def print_end(adapter: Adapter, on: bool = True, unloads: bool = True) -> None:
    """The U1 runs the end of a print (INNER_PRINT_END sets is_exec_print_end_action). Its end
    unload takes each head that the print used, not soft, with end_unload_filament set."""
    config = {
        "extruders_used": [True] * 4,
        "filament_soft": [False] * 4,
        "end_unload_filament": [unloads] * 4,
    }
    adapter.task_config = types.SimpleNamespace(  # type: ignore[assignment]
        is_exec_print_end_action=on, print_task_config=config
    )


def test_the_end_unload_of_a_print_unloads_again_when_its_unload_is_spent() -> None:
    """The U1's end unload: the print still reads printing. The unit's unload spent a whole move,
    so it is run again, as the eject from the page is."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    adapter.in_print = lambda: True
    print_end(adapter)
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    assert ace.feed.cleared == [0]
    assert len(ace.feed.started) == 2 and ace.feed.started[1][2] == pytest.approx(1000.6)


def test_the_end_unload_of_a_print_whose_tail_is_at_the_bay_is_done() -> None:
    """The second unit unload of the end unload brings nothing back: the tail was already past
    the drive gear. The eject is done, as the eject from the page is, though the print's end
    action is over by then."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    adapter.in_print = lambda: True
    print_end(adapter)
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    print_end(adapter, on=False)
    ev = {"kind": "unload_incomplete", "mode": "unloading", "motor_mm": 100.0}
    ev.update(filament_mm=0.0, seq=adapter.ejector.ejects[0].seq)
    adapter.on_feed_event(0, ev)
    assert ace.feed.cleared == [0, 0]
    assert adapter.gcode.lines == [at_bay(1999)]  # the whole eject's: the first unload's
    assert 0 not in adapter.ejector.eject_ends


def test_the_end_unload_of_a_print_with_the_head_holding_still_fails() -> None:
    adapter, reactor, left, right, ace = setup()
    adapter.in_print = lambda: True
    print_end(adapter)
    unloaded(left, 1, reactor)
    adapter.head_set = lambda h: True
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 353.0))
    assert ace.feed.cleared == []
    assert adapter.gcode.lines[-1] == (
        "ace2k_u1: lane 1 eject failed: unload_incomplete — ACEK_CLEAR LANE=1"
    )


@pytest.mark.parametrize("why", ["not_unloaded", "paused"])
def test_an_eject_at_the_print_end_that_is_not_its_end_unload_still_fails(why: str) -> None:
    """The print's end, but the U1's end unload does not take this head (an unload from the
    screen meanwhile), or the print paused since: the failure stands, nothing cleared."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    adapter.in_print = lambda: True
    print_end(adapter, unloads=why == "paused")
    unloaded(left, 1, reactor)
    if why == "paused":
        adapter.print_state = lambda: "paused"
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    assert len(ace.feed.started) == 1 and ace.feed.cleared == []
    assert adapter.gcode.lines[-1] == (
        "ace2k_u1: lane 1 eject failed: unload_incomplete — ACEK_CLEAR LANE=1"
    )


def test_an_unload_run_again_busy_after_its_clear_is_not_started_once_the_u1_works_the_head() -> (
    None
):
    """The clear went through and the unit answered busy. The U1 began a flow on the head before
    the retry: the retry starts nothing."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    unloaded(left, 1, reactor)
    ace.feed.refusals.append("busy")
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    assert ace.feed.cleared == [0] and len(ace.feed.started) == 1
    adapter.ejector.u1_flow = lambda h: True
    reactor.run_timers(reactor.now + 1.0)
    assert len(ace.feed.started) == 1
    assert adapter.gcode.lines[-1] == (
        "ace2k_u1: lane 1 eject not started — the U1 is loading/unloading this head"
        " — ACEK_CLEAR LANE=1"
    )


def test_a_u1_failure_drops_what_a_pending_unload_run_again_kept() -> None:
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    adapter.in_print = lambda: True
    print_end(adapter)
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    assert 0 in adapter.ejector.eject_ends and 0 in adapter.ejector.eject_timers
    left._set_channel_state(1, "unload_fail", True)
    ej = adapter.ejector
    assert 0 not in ej.eject_timers and 0 not in ej.eject_ends and 0 not in ej.eject_spent


@pytest.mark.parametrize("mm", [1999.4, 353.0])
def test_an_eject_unload_in_the_middle_of_a_print_still_fails(mm: float) -> None:
    """A U1 unload in a print or a pause, outside the print's end: nothing cleared, nothing run
    again, as before."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    adapter.in_print = lambda: True
    print_end(adapter, on=False)
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, mm))
    reactor.run_timers()
    assert len(ace.feed.started) == 1 and ace.feed.cleared == []
    assert adapter.gcode.lines[-1] == (
        "ace2k_u1: lane 1 eject failed: unload_incomplete — ACEK_CLEAR LANE=1"
    )


def test_an_unload_run_again_is_not_started_once_the_u1_works_the_head() -> None:
    """Between the end of a spent unload and the start of the next, the U1 began a flow on the
    head: nothing is cleared, and the line says how to clear the lane."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    adapter.ejector.u1_flow = lambda h: True
    reactor.run_timers()
    assert ace.feed.cleared == [] and len(ace.feed.started) == 1
    assert adapter.gcode.lines[-1] == (
        "ace2k_u1: lane 1 eject not started — the U1 is loading/unloading this head"
        " — ACEK_CLEAR LANE=1"
    )
    assert 0 not in adapter.ejector.eject_spent and 0 not in adapter.ejector.eject_ends


def test_an_eject_whose_clear_fails_says_how_to_clear_it() -> None:
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    unloaded(left, 1, reactor)

    def clear(lane: int) -> NoReturn:
        raise RuntimeError("no link")

    ace.feed.clear = clear
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    assert adapter.gcode.lines[-1] == (
        "ace2k_u1: lane 1 eject not started — no link — ACEK_CLEAR LANE=1"
    )
    assert 0 not in adapter.ejector.eject_spent


def test_an_eject_unload_judges_carried_against_what_it_was_asked() -> None:
    """head_budget_mm under the unit's move: the first unload is the unit's own (2 000 mm); one
    that ends after 950 mm did not carry a whole move — not unloaded again: its tail is at the
    bay, the operator's to take out."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 1000.0
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 950.0))
    reactor.run_timers()
    assert len(ace.feed.started) == 1
    assert adapter.gcode.lines == [at_bay(950)]


def test_an_eject_clears_once_across_busy_retries_and_hints_only_when_the_clear_failed() -> None:
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 3000.0
    unloaded(left, 1, reactor)
    ace.feed.refusals.extend(["busy", "busy", "no_filament"])
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    for _ in range(5):
        reactor.run_timers(reactor.now + 1.0)
    assert ace.feed.cleared == [0]
    assert adapter.gcode.lines[-1].startswith("ace2k_u1: lane 1 eject not started")
    assert "ACEK_CLEAR" not in adapter.gcode.lines[-1]


@pytest.mark.parametrize("push_mm, tail", [(3000.0, False), (2100.0, True)])
def test_a_second_unload_that_brings_nothing_back_is_judged_on_the_whole_eject(
    push_mm: float, tail: bool
) -> None:
    """The first unload spent 1 999.4 mm; the second ends unload_incomplete with nothing back.
    Short of the lane's known length to its head the filament did not move: a failure. Past
    it, less the slack, the tail was already at the bay (as _stuck_at_bay judges it)."""
    adapter, reactor, left, right, ace = setup()
    adapter.head_budget_mm = 4000.0
    h = next(h for h in adapter.hooked if h.lane == 0)
    h.push_mm, h.push_known = push_mm, True
    unloaded(left, 1, reactor)
    adapter.on_feed_event(0, spent("unload_incomplete", adapter.ejector.ejects[0].seq, 1999.4))
    reactor.run_timers()
    ev = {"kind": "unload_incomplete", "mode": "unloading", "motor_mm": 100.0}
    ev.update(filament_mm=0.0, seq=adapter.ejector.ejects[0].seq)
    adapter.on_feed_event(0, ev)
    assert (adapter.gcode.lines == [at_bay(1999)]) is tail
