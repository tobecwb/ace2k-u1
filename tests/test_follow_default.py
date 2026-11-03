"""The follow on by default on the U1 (ace2k's follow_default, host interface 8): the start is
ace2k's, the adapter takes its lanes' insertions and adopts the follow ace2k armed; and the
start-up race seen at a Klipper start during the release's long run: one start in flight per
lane, a host stop never read as a stop by hand, each line logged once."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import ace2k_u1
import fakes
import pytest
from test_check import TAGS_ABSENT, Config, Gcode, Printer
from test_presence import SETTLE_S, build, events
from test_presence import build as build_lanes
from test_standing import event, follows, loaded, setup
from test_states import u1_update_auto_mode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ace2k_u1_adapter import Adapter
    from fakes import FakeAce2k, FakeFeed, FakeFeedApi, FakeMove, FakeReactor


def by_hand(adapter: Adapter) -> list[str]:
    return [line for line in adapter.gcode.lines if "by hand" in line]


def not_armed(adapter: Adapter) -> list[str]:
    return [line for line in adapter.gcode.lines if "not armed" in line]


# --- item 59: the start-up burst ------------------------------------------------------------------


def test_the_start_up_burst_sends_one_start_for_the_lane() -> None:
    # the release burn-in's drill: the arm's start yields for the round trip; meanwhile the bay
    # watch's standing re-arm sees a wanted, idle, not yet armed lane, and the U1 sets load_finish
    # again — each a trigger that used to send a start of its own
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0]["insert"] = True
    left.runout_sensor[1].detected = True
    left.channel_state[1] = "load_flushing"  # the U1's load ending: a real load_finish
    start = ace.feed.start_move
    in_flight = []

    def start_move(lane: int, mode: str, length: float, speed: float, **_kw: object) -> FakeMove:
        if not in_flight:
            in_flight.append(True)
            for _ in range(5):  # Klipper's query helper: five retries over 0.31 s
                reactor.now += 0.06
                adapter.watch(reactor.now)
                left._set_channel_state(1, "load_finish", True)
        return start(lane, mode, length, speed, **_kw)

    ace.feed.start_move = start_move
    left._set_channel_state(1, "load_finish", True)
    assert follows(ace) == [(0, "assist_both")]
    assert ace.feed.attempts == 1
    assert h.armed and not h.arming and not_armed(adapter) == []


def test_a_busy_while_ace2ks_own_start_is_in_flight_is_no_failure() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0].update(insert=True, follow_default="arming")
    ace.feed.refuse = "busy"  # the host's one-in-flight refusal
    left._set_channel_state(1, "load_finish", True)
    assert not_armed(adapter) == [] and h.follow_wanted and not h.armed
    # ace2k's follow comes in: the bay watch adopts it
    ace.feed.refuse = None
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 7)
    ace.lanes[0].update(mode="following", follow_default="armed")
    adapter.watch(reactor.now)
    assert h.armed and h.assist_seq == 7 and ace.feed.started == []


# --- item 59: a stop the host caused is never by hand ---------------------------------------------


def test_a_stop_the_host_sent_is_never_a_stop_by_hand() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    reactor.now += 10.0  # long after: no expectation of the adapter's own would answer it
    host = dict(event("stopped"), stop_by="host")  # ace2k's stop after a start with no answer
    adapter.on_feed_event(0, host)
    assert h.follow_wanted and by_hand(adapter) == []
    assert not h.armed  # the follow did end
    loaded(adapter, reactor, left, ace)
    reactor.now += 10.0
    adapter.on_feed_event(0, dict(event("stopped"), stop_by="command"))  # ACEK_STOP typed
    assert not h.follow_wanted
    assert by_hand(adapter) == ["ace2k_u1: lane 1 follow stopped by hand; left off"]


# --- item 59: each line once in the log -----------------------------------------------------------


class KlipperGcode:
    """The console as Klipper's gcode object: respond_info logs what it prints, unless told not
    to."""

    def __init__(self) -> None:
        self.lines = []

    def respond_info(self, msg: str, log: bool = True) -> None:
        if log:
            logging.info(msg)
        self.lines.append(msg)

    def run_script(self, script: str) -> None:
        pass


def test_each_adapter_line_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    adapter, reactor, left, ace, stats, h = setup()
    adapter.gcode = KlipperGcode()
    with caplog.at_level(logging.INFO):
        adapter.say("ace2k_u1: lane 1 something to say")
    logged = [r.getMessage() for r in caplog.records]
    assert logged.count("ace2k_u1: lane 1 something to say") == 1
    assert adapter.gcode.lines == ["ace2k_u1: lane 1 something to say"]


def test_without_a_console_the_line_is_still_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    adapter, reactor, left, ace, stats, h = setup()
    adapter.gcode = None
    with caplog.at_level(logging.INFO):
        adapter.say("ace2k_u1: quiet")
    assert [r.getMessage() for r in caplog.records].count("ace2k_u1: quiet") == 1


class LoggingGcode(Gcode):
    def respond_info(self, msg: str, log: bool = True) -> None:
        if log:
            logging.info(msg)
        self.lines.append(msg)


def test_the_not_hooked_warning_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    reactor = fakes.FakeReactor()
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    ace.feed.API_VERSION = 7
    gcode = LoggingGcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": ace}, [left, right])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    with caplog.at_level(logging.INFO):
        u1._hook(reactor.now)
    line = "ace2k_u1: not hooked: ace2k host interface older than 12"
    assert [r.getMessage() for r in caplog.records].count(line) == 1
    assert gcode.lines == [line]


# --- the start is ace2k's -------------------------------------------------------------------------


def u1_with(
    feed_api: FakeFeedApi | None = None, values: Mapping[str, object] | None = None
) -> tuple[ace2k_u1.ACE2kU1, FakeReactor, FakeAce2k, Gcode, FakeFeed]:
    reactor = fakes.FakeReactor()
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    if feed_api is not None:
        ace.feed = feed_api
    gcode = Gcode()
    objects = {"gcode": gcode, "ace2k": ace, "extruder_list": fakes.extruder_list()}
    printer = Printer(reactor, objects, [left, right])
    return ace2k_u1.ACE2kU1(Config(printer, values)), reactor, ace, gcode, right


def test_at_connect_the_adapter_takes_its_lanes_insertions_at_its_speed() -> None:
    u1, reactor, ace, gcode, right = u1_with(values={"feed_speed": 70.0})
    u1._on_connect()
    assert ace.feed.lane_follow == {lane: (70.0, False) for lane in range(4)}
    u1._hook(reactor.now)
    assert gcode.lines[0] == "ace2k_u1: hooked lanes [1, 2, 3, 4]" and TAGS_ABSENT in gcode.lines
    assert ace.feed.lane_follow == {lane: (70.0, False) for lane in range(4)}  # kept


def test_not_hooked_the_insertions_are_given_back_to_ace2k() -> None:
    u1, reactor, ace, gcode, right = u1_with()
    u1._on_connect()
    del right.wheel_2  # a U1 update: nothing hooked
    u1._hook(reactor.now)
    assert gcode.lines[0].startswith("ace2k_u1: not hooked: ")
    assert ace.feed.lane_follow == {lane: (None, True) for lane in range(4)}


HELD_WHY = "not in [ace2k_u1] lanes"


def test_at_connect_the_lanes_outside_lanes_are_held_out_of_the_follow() -> None:
    api = fakes.FakeFeedApi()
    api.API_VERSION = 21
    u1, reactor, ace, gcode, right = u1_with(api, values={"lanes": "1, 2"})
    u1._on_connect()
    assert api.lane_follow == {0: (70.0, False), 1: (70.0, False)}
    assert api.holds == [(2, True, HELD_WHY), (3, True, HELD_WHY)]


def test_an_ace2k_before_interface_21_holds_them_with_no_reason() -> None:
    u1, reactor, ace, gcode, right = u1_with(values={"lanes": "1, 2"})  # interface 15
    u1._on_connect()
    assert ace.feed.holds == [(2, True), (3, True)]


def test_not_hooked_the_held_lanes_are_given_back_too() -> None:
    u1, reactor, ace, gcode, right = u1_with(values={"lanes": "1, 2"})
    u1._on_connect()
    del right.wheel_2  # a U1 update: nothing hooked
    u1._hook(reactor.now)
    assert ace.feed.holds == [(2, True), (3, True), (2, False), (3, False)]


def test_at_the_hook_a_lane_outside_lanes_that_still_follows_is_stopped() -> None:
    u1, reactor, ace, gcode, right = u1_with(values={"lanes": "1, 2"})
    u1._on_connect()
    ace.lanes[2].update(insert=True, mode="following")  # its follow from before a restart
    ace.lanes[3].update(insert=True, mode="idle")
    u1._hook(reactor.now)
    assert ace.feed.stopped == [2]
    assert "ace2k_u1: lane 3 feeds no head (not in lanes:): its follow stopped" in gcode.lines


def test_an_older_ace2k_is_asked_nothing_at_connect() -> None:
    old = fakes.FakeFeedApi()
    old.API_VERSION = 7
    u1, reactor, ace, gcode, right = u1_with(old)
    u1._on_connect()
    assert old.lane_follow == {}


def test_ace2ks_start_up_follow_is_adopted_and_stopped_as_the_adapters_own() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    left.runout_sensor[1].detected = True
    left.channel_state[1] = "load_finish"
    ace.lanes[0].update(insert=True, mode="following", follow_default="armed")
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 7)
    adapter.watch(reactor.now)
    assert h.armed and h.assist_seq == 7 and h.follow_wanted
    assert ace.feed.started == []  # nothing sent: the follow is ace2k's
    left._set_channel_state(1, "unload_finish", True)  # the U1's unload ends
    assert ace.feed.stopped[0] == 0 and h.expect_stopped and h.expect_seq == 7
    adapter.on_feed_event(0, dict(event("stopped", seq=7), stop_by="host"))
    assert by_hand(adapter) == []


def test_a_follow_not_ace2ks_is_not_adopted() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0].update(insert=True, mode="following")  # by hand: ACEK_ASSIST DIR=BOTH
    adapter.watch(reactor.now)
    assert not h.armed and not h.follow_wanted


def test_no_adoption_while_the_adapter_runs_the_lane() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0].update(insert=True, mode="following")
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 7)
    h.hold = True  # the extrude retry, an eject
    adapter.watch(reactor.now)
    assert not h.armed


def test_the_push_stops_a_follow_before_it_starts() -> None:
    # ace2k followed a lane whose filament is in the bay but not at the head: the U1's load pushes
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0].update(insert=True, mode="following")
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 7)
    adapter.watch(reactor.now)
    assert h.armed
    ace.feed.refusals = ["busy"]  # the stopped follow winding down
    polls = []

    def tick(now: float) -> None:
        polls.append(now)
        if len(polls) == 1:
            ace.lanes[0]["mode"] = "idle"
        if len(polls) == 40:
            left.runout_sensor[1].detected = True

    reactor.on_pause = [tick]
    assert adapter.push_runner.push_to_head(h) is None
    reactor.on_pause = []
    assert ace.feed.log[0] == ("stop", 0)
    assert ("start", 0, "feed", 2000.0) in ace.feed.log
    assert h.follow_wanted  # the load arms it again at its extrusion


def test_the_preload_arms_the_insertion_once_the_filament_is_at_the_head() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0]["insert"] = True
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)
    assert events(left)[-1] == ("port_event", True, 1)
    ace.feed.refusals = [None, "busy"]  # the push's stop winding down
    polls = []

    def tick(now: float) -> None:
        polls.append(now)
        if len(polls) == 5:
            left.runout_sensor[1].detected = True

    reactor.on_pause = [tick]
    adapter.push_runner.preload(h)
    reactor.on_pause = []
    assert left.channel_state[1] == "preload_finish"
    assert [s[:2] for s in ace.feed.started] == [(0, "feed"), (0, "assist_both")]
    assert h.armed and h.follow_wanted and not_armed(adapter) == []


def test_a_preload_that_fails_or_is_blocked_arms_nothing() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0]["insert"] = False  # the filament gone: no_filament
    adapter.push_runner.preload(h)
    assert follows(ace) == [] and left.channel_state[1] == "preload_fail"


def test_a_channel_out_of_the_u1s_auto_mode_is_armed_when_the_insert_is_told() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    left.config["auto_mode"][1] = False
    ace.lanes[0]["insert"] = True
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)
    assert events(left)[-1] == ("port_event", True, 1)
    assert follows(ace) == [(0, "assist_both")]


def test_the_status_line_shows_an_adopted_follow_as_wanted() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0].update(insert=True, mode="following")
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 3)
    adapter.watch(reactor.now)
    lines = []
    adapter.status_lines(lines.append)
    assert "follow=wanted" in next(line for line in lines if line.startswith("lane 1 "))


def test_with_ace2ks_follow_default_off_the_adapter_arms_as_before() -> None:
    # follow_default: False restores the earlier behaviour: the re-derivation arms a loaded head,
    # the preload's end and an insertion arm nothing
    adapter, reactor, left, right, ace = build()
    ace.feed.follow_default = False
    adapter.gcode = Gcode()
    left.channel_state[1] = "load_finish"
    u1_update_auto_mode(left)
    assert adapter.hook_all() == []
    reactor.run_async()
    assert follows(ace) == [(0, "assist_both")]
    adapter, reactor, left, ace, stats, h = setup()
    ace.feed.follow_default = False
    ace.lanes[0]["insert"] = True
    polls = []

    def tick(now: float) -> None:
        polls.append(now)
        if len(polls) == 5:
            left.runout_sensor[1].detected = True

    reactor.on_pause = [tick]
    adapter.push_runner.preload(h)
    reactor.on_pause = []
    assert left.channel_state[1] == "preload_finish" and follows(ace) == []


# --- ace2k's manual-move pause and active lane on the U1 ------------------------------------------


def test_each_hooked_lane_has_its_own_extruder_so_ace2k_forms_no_shared_group() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    names = dict(ace.feed.extruders)
    assert sorted(names) == [0, 1, 2, 3]
    assert len(set(names.values())) == 4  # one extruder per lane: ACEK_LANE_ACTIVE never needed


def test_a_manual_feed_pausing_the_follow_and_ace2ks_re_arm_are_taken_quietly() -> None:
    # ACEK_FEED typed on a following lane: ace2k stops the follow (stop_by host), runs the feed,
    # and arms the follow again at its end; the adapter neither calls it a stop by hand nor
    # starts a follow of its own over ace2k's
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    seq = h.assist_seq
    adapter.on_feed_event(0, dict(event("stopped", seq=seq), stop_by="host"))
    assert by_hand(adapter) == [] and h.follow_wanted and not h.armed
    ace.lanes[0]["mode"] = "feeding"
    reactor.now += 1.0
    adapter.watch(reactor.now)
    adapter.on_feed_event(0, dict(event("done", seq=50, mode="feeding")))
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 51)  # ace2k's re-arm
    ace.lanes[0]["mode"] = "idle"  # the report not yet showing it
    reactor.now += 1.0
    adapter.watch(reactor.now)
    assert h.armed and h.assist_seq == 51
    assert follows(ace) == [(0, "assist_both")]  # only the load's, never a second start
    assert not_armed(adapter) == []


# --- review 7: the re-derivation arms nothing, by its state, not by its timing --------------------


def test_a_real_load_finish_during_the_re_derivations_wait_is_armed() -> None:
    # the U1's _do_feed waits for its channel lock (a reactor pause) before it re-derives: a
    # load of the same head finishing meanwhile is a real load_finish and is armed
    adapter, reactor, left, right, ace = build()
    adapter.gcode = Gcode()
    left.channel_state[1] = "load_flushing"  # a U1 load of head 0 in flight at the hook
    ace.lanes[0]["insert"] = True

    def do_feed(
        ch: int, action: str | None = None, stage: str | None = None, auto_mode: bool | None = None
    ) -> None:
        if action != "update_auto_mode" or ch != 1:
            return
        reactor.pause(reactor.now + 0.1)  # the lock: the load's flow runs meanwhile
        left._set_channel_state(1, left.channel_state[1], True)  # the re-derivation

    left._do_feed = do_feed
    hooks = []

    def other_flow(now: float) -> None:
        if not hooks:
            hooks.append(now)
            left._set_channel_state(1, "load_finish", True)  # the load's own end

    reactor.on_pause = [other_flow]
    assert adapter.hook_all() == []
    reactor.run_async()
    reactor.on_pause = []
    assert follows(ace) == [(0, "assist_both")]


def test_a_load_finish_set_again_is_never_an_arm() -> None:
    # load_finish -> load_finish is the U1 asserting a state it holds (its re-derivation, its
    # auto-mode update), not a load: nothing is armed, whenever it comes
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0]["insert"] = True
    left.channel_state[1] = "load_finish"
    left._set_channel_state(1, "load_finish", True)
    assert follows(ace) == []
    left._set_channel_state(1, "load_extruding")  # a real pull state still arms
    assert follows(ace) == [(0, "assist_both")]


# --- second review --------------------------------------------------------------------------------


def test_the_follow_gone_rule_is_ace2ks_span(monkeypatch: pytest.MonkeyPatch) -> None:
    # review 1+2 (b): the adapter counts an armed follow reported idle as gone after ace2k's own
    # span for a lost final event (gone_after_s), plus a watch period — one rule, not two
    monkeypatch.setattr(fakes.FakeFeedApi, "gone_after_s", 4.0, raising=False)
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ace.lanes[0]["mode"] = "idle"  # its final event never seen
    reactor.now += 2.5
    adapter.watch(reactor.now)
    assert h.armed and follows(ace) == [(0, "assist_both")]
    reactor.now += 2.0
    adapter.watch(reactor.now)  # gone: the standing follow arms it again
    assert follows(ace) == [(0, "assist_both")] * 2


def test_no_arm_of_the_adapters_while_ace2k_pauses_the_lane() -> None:
    # review 3: ace2k stopped the follow for a G-code move; an idle report comes before the
    # move's start; the bay watch must not arm the lane
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, dict(event("stopped", seq=h.assist_seq), stop_by="host"))
    ace.feed.paused_lanes.add(0)
    ace.lanes[0]["mode"] = "idle"
    reactor.now += 3.0
    adapter.watch(reactor.now)
    left._set_channel_state(1, "load_extruding")  # any other trigger too
    assert follows(ace) == [(0, "assist_both")] and h.follow_wanted
    ace.feed.paused_lanes.clear()  # the move ended, ace2k's re-arm is its own business
    reactor.now += 3.0
    adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")] * 2  # or adopted, had ace2k armed first


def test_a_busy_while_ace2ks_follow_is_armed_but_not_yet_reported_is_silent() -> None:
    # review 6
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0].update(insert=True, follow_default="armed")  # the report still idle
    ace.feed.refuse = "busy"
    left._set_channel_state(1, "load_extruding")
    assert not_armed(adapter) == []


def test_ace2ks_follow_on_a_lane_waiting_behind_a_piece_is_stopped_not_adopted() -> None:
    # review 7
    adapter, reactor, left, ace, stats, h = setup()
    h.waits_behind = True
    ace.lanes[0].update(insert=True, mode="following")
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 9)
    adapter.watch(reactor.now)
    assert not h.armed and ace.feed.stopped == [0]


def test_waiting_behind_a_piece_holds_ace2ks_default_on_the_lane() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    adapter.grip_watch._set_waits_behind(h, "a test")
    assert ace.feed.holds == [(0, True)]
    adapter.grip_watch.clear_waits_behind(h)
    assert ace.feed.holds == [(0, True), (0, False)]


def test_the_adoption_line_does_not_claim_the_start(caplog: pytest.LogCaptureFixture) -> None:
    # ace2k arms again after a manual move too, so the line names no reason
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0].update(insert=True, mode="following")
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 12)
    with caplog.at_level(logging.INFO):
        adapter.watch(reactor.now)
    logged = [r.getMessage() for r in caplog.records]
    assert "ace2k_u1: lane 1 follows (armed by ace2k)" in logged
    assert not [m for m in logged if "at the start" in m]


# --- the U1's own inlet after a restart ----------------------------------------------------------


def test_the_u1s_own_inlet_settling_after_a_restart_stops_no_loaded_lane() -> None:
    # A print left heads 1-2 load_finish (cancelled from the screen: the U1 keeps load_finish).
    # After the restart ace2k follows every lane. ~0.45 s after the hook the U1's own inlet ADC
    # (its side feeder's, empty: the unit feeds the head) settles to "no filament" and calls the
    # U1's port handler, which would set preload_finish and the adapter stop the follow. The
    # inlet is the lane's insert on a hooked channel: the U1's own reading is not passed on.
    adapter, reactor, left, ace, stats, h = setup()
    left.runout_sensor[1].detected = True
    left.channel_state[1] = "load_finish"
    ace.lanes[0].update(insert=True, mode="following")
    ace.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 13)
    adapter.watch(reactor.now)
    assert h.armed and h.follow_wanted
    left._port_event_handler(False, 1)  # the U1's ADC callback, as the U1 calls it
    reactor.run_async()
    assert left.channel_state[1] == "load_finish"
    assert ace.feed.stopped == [] and h.armed and h.follow_wanted
    assert ("port_event", False, 1) not in left.calls


def test_the_adapters_own_port_events_still_reach_the_u1() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0]["insert"] = True
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)
    assert ("port_event", True, 1) in left.calls  # the lane's insert told the U1, as before
    ace.lanes[0]["insert"] = False
    adapter.watch(reactor.now)
    assert ("port_event", False, 1) in left.calls


def test_an_unhooked_channels_inlet_events_reach_the_u1_unchanged() -> None:
    adapter, reactor, left, right, ace = build_lanes(lanes=(2, 3, 4))  # lane 1 (e0) the U1's own
    assert adapter.hook_all() == []
    left._port_event_handler(False, 1)
    assert ("port_event", False, 1) in left.calls
