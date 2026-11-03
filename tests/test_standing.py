"""The follow as the standing state of a loaded head: follow_wanted set when the adapter arms
the follow and cleared on a failure, the unload's end, the filament leaving its bay outside the
tail and a stop by hand; the re-arm of a wanted idle lane, once per idle episode; the clear of a
wanted lane's error on the U1's resume."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import ace2k_u1_hooks
import ace2k_u1_pause
import fakes
import pytest
from test_presence import SETTLE_S, build, events

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from ace2k_klipper_types import FeedEvent
    from ace2k_u1_adapter import Adapter
    from ace2k_u1_common import Hooked
    from ace2k_u1_printer_types import LaneView, Status, TagRecord
    from fakes import FakeAce2k, FakeFeed, FakeMove, FakePrintStats, FakeReactor

    # the feed's start_move: the lane, mode, length and speed, and its keywords (free_tip)
    StartMove = Callable[..., FakeMove]


class Gcode:
    def __init__(self) -> None:
        self.lines = []
        self.scripts = []

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)

    def run_script(self, script: str) -> None:
        self.scripts.append(script)


def setup(
    state: str = "standby",
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeAce2k, FakePrintStats, Hooked]:
    adapter, reactor, left, right, ace = build()
    adapter.gcode = Gcode()
    assert adapter.hook_all() == []
    stats = fakes.FakePrintStats(state)
    adapter.pause_runner.setup_pause(
        ace2k_u1_pause.PauseObjects(
            stats, fakes.FakePauseResume(), fakes.ExceptionManager(), lambda *a: None, "head"
        )
    )
    h = next(h for h in adapter.hooked if h.lane == 0)  # lane 1 -> e0 -> left channel 1
    return adapter, reactor, left, ace, stats, h


def loaded(adapter: Adapter, reactor: FakeReactor, left: FakeFeed, ace: FakeAce2k) -> None:
    """Lane 1 loaded on its head: the filament in the bay and at the head's sensor, load_finish
    (the follow armed), the lane following."""
    ace.lanes[0]["insert"] = True
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)  # the U1 told of the filament (its head empty)
    left.runout_sensor[1].detected = True  # its preload: the filament at the head's sensor
    left._set_channel_state(1, "load_finish", True)
    ace.lanes[0]["mode"] = "following"
    adapter.watch(reactor.now)


def event(kind: str, seq: int = 999, mode: str = "following") -> FeedEvent:
    return {"kind": kind, "mode": mode, "motor_mm": 1.0, "filament_mm": 0.0, "seq": seq}


def follows(ace: FakeAce2k) -> list[tuple[object, ...]]:
    return [s[:2] for s in ace.feed.started if s[1] == "assist_both"]


def ended(
    adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k, kind: str = "stopped_link"
) -> None:
    """The unit ended lane 1's follow (not by the adapter, not by hand); the lane is idle."""
    adapter.on_feed_event(0, event(kind))
    ace.lanes[0]["mode"] = "idle"


# --- follow_wanted ----------------------------------------------------------------------------


def test_arming_sets_follow_wanted() -> None:
    for state in ("load_finish", "load_extruding", "manual_sta_flushing", "unload_doing"):
        adapter, reactor, left, ace, stats, h = setup()
        assert not h.follow_wanted
        left._set_channel_state(1, state)
        assert h.follow_wanted, state


def test_a_refused_arm_still_wants_the_follow() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.feed.refuse = "no_filament"
    left._set_channel_state(1, "load_finish", True)
    assert h.follow_wanted and not h.armed


def test_every_fail_clears_follow_wanted_and_still_stops() -> None:
    for state in ("load_fail", "unload_fail", "manual_sta_fail", "preload_fail"):
        adapter, reactor, left, ace, stats, h = setup()
        loaded(adapter, reactor, left, ace)
        left._set_channel_state(1, state)
        assert not h.follow_wanted, state
        assert ace.feed.stopped == [0], state


def test_unload_finish_clears_follow_wanted() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    left._set_channel_state(1, "unload_doing")
    assert h.follow_wanted
    left._set_channel_state(1, "unload_finish", True)
    assert not h.follow_wanted


def test_the_insert_falling_outside_the_tail_clears_follow_wanted() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ended(adapter, reactor, ace)
    ace.lanes[0]["insert"] = False
    adapter.watch(reactor.now)
    assert not h.follow_wanted
    ace.lanes[0]["insert"] = True  # back in the bay: the head no longer counts as wanted
    adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")]


def test_the_insert_falling_in_the_tail_keeps_follow_wanted() -> None:
    for tail in (True, False):  # with the notice in, or the lane still reported following
        adapter, reactor, left, ace, stats, h = setup()
        loaded(adapter, reactor, left, ace)
        adapter.on_feed_event(0, event("tail"))
        ace.lanes[0].update(insert=False, tail=tail)
        adapter.watch(reactor.now)
        assert h.follow_wanted and h.armed, tail
        assert ace.feed.stopped == [], tail


def test_the_u1_told_of_the_empty_bay_keeps_the_tail_running() -> None:
    # the U1's own remove-filament handling moves a loaded head to wait_insert: in the tail the
    # lane is not stopped, outside it the head left load_finish and is
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ace.lanes[0].update(insert=False, tail=True)
    left._set_channel_state(1, "wait_insert", True)
    assert ace.feed.stopped == [] and h.follow_wanted


def test_the_tail_is_stopped_once_its_end_passes_the_heads_sensor() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, event("tail"))
    ace.lanes[0].update(insert=False, tail=True)
    adapter.watch(reactor.now)
    assert ace.feed.stopped == []
    left.runout_sensor[1].detected = False  # the filament's end at the head
    for _ in range(5):  # the stop's report still on its way: one stop
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert ace.feed.stopped == [0] and not h.armed
    assert adapter.gcode.lines[-1] == "ace2k_u1: lane 1: the tail passed e0's sensor — stopped"
    adapter.on_feed_event(0, event("stopped"))  # that stop's own event
    ace.lanes[0].update(mode="idle", tail=False)
    adapter.watch(reactor.now)
    assert not h.follow_wanted  # the bay empty outside the tail: wanted no more
    assert follows(ace) == [(0, "assist_both")]


def test_the_tails_end_at_the_heads_sensor_ends_the_follows_standing() -> None:
    # a filament put in during the tail rests behind the old end, not at the gear: once the
    # adapter ends the tail itself (its end past the head's sensor) no follow is armed for it
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True  # a new filament in the bay; the tail ignores it
    adapter.watch(reactor.now)
    left.runout_sensor[1].detected = False  # the old end past the head
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [0] and not h.follow_wanted
    adapter.on_feed_event(0, event("stopped", seq=h.expect_seq))  # that stop's own event
    ace.lanes[0].update(mode="idle", tail=False)
    for _ in range(5):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")]  # the load's only: no follow behind the end


def test_the_u1_told_of_the_empty_bay_after_the_tails_end_stops_the_lane() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ace.lanes[0].update(insert=False, tail=True)
    left.runout_sensor[1].detected = False
    left._set_channel_state(1, "wait_insert", True)
    assert ace.feed.stopped == [0]


def test_a_stop_by_hand_clears_follow_wanted() -> None:
    for mode in ("following", "assisting", "assisting_back"):  # ACEK_ASSIST OFF=1, ACEK_STOP
        adapter, reactor, left, ace, stats, h = setup()
        loaded(adapter, reactor, left, ace)
        reactor.now += 10.0
        adapter.on_feed_event(0, event("stopped", mode=mode))
        assert not h.follow_wanted and not h.armed, mode
        assert adapter.gcode.lines == ["ace2k_u1: lane 1 follow stopped by hand; left off"]
        ace.lanes[0]["mode"] = "idle"
        adapter.watch(reactor.now)
        assert follows(ace) == [(0, "assist_both")], mode  # not armed again


def test_the_stopped_event_of_the_follows_own_seq_is_a_stop_by_hand_too() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    reactor.now += 10.0
    adapter.on_feed_event(0, event("stopped", seq=h.assist_seq, mode="idle"))
    assert not h.follow_wanted


def test_the_adapters_own_stop_keeps_follow_wanted() -> None:
    # the extrude retry stops the follow, feeds and re-arms: its stopped event is its own
    adapter, reactor, left, ace, stats, h = setup()
    left._set_channel_state(1, "load_extruding")
    ace.lanes[0]["mode"] = "following"
    seq = ace.feed.started[-1][4].seq
    ace.feed.start_move = _start_and_end(ace.feed.start_move)
    ace2k_u1_hooks.nudge(adapter, h)
    adapter.on_feed_event(0, event("stopped", seq=seq))
    assert h.follow_wanted and adapter.gcode.lines == []


def _start_and_end(start: StartMove) -> StartMove:
    def start_move(
        lane: int, mode: str, length: float, speed: float, **_kw: bool | str
    ) -> FakeMove:
        move = start(lane, mode, length, speed, **_kw)
        if mode == "feed":
            move.event = {"kind": "done"}
        return move

    return start_move


def test_a_stopped_event_long_after_the_adapters_stop_is_by_hand() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    left._set_channel_state(1, "preload_finish", True)  # a head runout: the adapter stops it
    assert ace.feed.stopped == [0]
    adapter.on_feed_event(0, event("stopped", seq=h.assist_seq))  # that stop's own event
    assert h.follow_wanted
    loaded(adapter, reactor, left, ace)
    reactor.now += 10.0
    adapter.on_feed_event(0, event("stopped"))  # a later one, not the adapter's
    assert not h.follow_wanted


def test_the_tail_notice_does_not_disarm() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, event("tail", seq=h.assist_seq))
    assert h.armed and h.follow_wanted


# --- the standing re-arm ------------------------------------------------------------------------


def test_a_wanted_idle_lane_is_armed_again_once_per_idle_episode() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ended(adapter, reactor, ace)
    assert not h.armed
    adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")] * 2
    for _ in range(3):  # still reported idle: not again
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")] * 2
    ace.lanes[0]["mode"] = "following"
    adapter.watch(reactor.now)
    ended(adapter, reactor, ace)  # a new idle episode
    adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")] * 3


def test_a_refused_re_arm_is_one_line_and_no_retry_storm() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ended(adapter, reactor, ace)
    ace.feed.refuse = "no_filament"
    attempts = ace.feed.attempts
    for _ in range(10):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert ace.feed.attempts == attempts + 1
    assert [line for line in adapter.gcode.lines if "not armed" in line] == [
        "ace2k_u1: lane 1 follow not armed — refused: no_filament"
    ]


def test_no_re_arm_without_its_conditions() -> None:
    cases = {
        "not wanted": lambda a, h, left, ace: setattr(h, "follow_wanted", False),
        "an error": lambda a, h, left, ace: ace.lanes[0].update(mode="error", error="stuck"),
        "no filament": lambda a, h, left, ace: ace.lanes[0].update(insert=None),
        "head not loaded": lambda a, h, left, ace: left.channel_state.__setitem__(1, "none"),
        "the retry running": lambda a, h, left, ace: setattr(h, "hold", True),
        "not idle": lambda a, h, left, ace: ace.lanes[0].update(mode="feeding"),
    }
    for name, change in cases.items():
        adapter, reactor, left, ace, stats, h = setup()
        loaded(adapter, reactor, left, ace)
        ended(adapter, reactor, ace)
        change(adapter, h, left, ace)
        adapter.watch(reactor.now)
        assert follows(ace) == [(0, "assist_both")], name


def test_after_the_tail_out_the_lane_waits_for_a_new_load() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, event("tail"))
    ace.lanes[0].update(insert=False, tail=True)
    adapter.watch(reactor.now)
    left._set_channel_state(1, "wait_insert", True)  # the U1 told of the empty bay
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")] and not h.armed
    ace.lanes[0]["insert"] = True  # a new filament: the U1 preloads it, the follow keeps off
    for _ in range(20):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")]
    # the old piece still in the head: a load that finds it set uses it, the lane waits
    left._set_channel_state(1, "load_finish", True)
    assert follows(ace) == [(0, "assist_both")] and not h.follow_wanted
    # the old piece out, the U1's load pushes this filament to the head: armed, wanted
    left.runout_sensor[1].detected = False
    reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
    assert left._put_into_drive(1) is None
    reactor.on_pause = []
    left.channel_state[1] = "load_flushing"  # the load's own steps (load_finish over itself
    left._set_channel_state(1, "load_finish", True)  # is no load): loaded again, armed, wanted
    assert follows(ace) == [(0, "assist_both")] * 2 and h.follow_wanted


# --- the clear on the U1's resume ---------------------------------------------------------------


def errored(
    adapter: Adapter, reactor: FakeReactor, left: FakeFeed, ace: FakeAce2k, stats: FakePrintStats
) -> None:
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, event("stuck"))
    ace.lanes[0].update(mode="error", error="stuck")
    stats.state = "paused"
    adapter.watch(reactor.now)


def test_the_resume_clears_a_wanted_lane_in_error_and_the_follow_returns() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    errored(adapter, reactor, left, ace, stats)
    assert ace.feed.cleared == []
    stats.state = "printing"
    adapter.watch(reactor.now)
    assert ace.feed.cleared == [0]
    ace.lanes[0].update(mode="idle", error=None)
    adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")] * 2


def test_the_resume_leaves_a_lane_not_wanted_or_not_in_error() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    errored(adapter, reactor, left, ace, stats)
    h.follow_wanted = False
    stats.state = "printing"
    adapter.watch(reactor.now)
    assert ace.feed.cleared == []
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    stats.state = "paused"
    adapter.watch(reactor.now)
    stats.state = "printing"
    adapter.watch(reactor.now)
    assert ace.feed.cleared == []


def test_only_the_resume_clears() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    errored(adapter, reactor, left, ace, stats)
    for state in ("paused", "paused", "standby", "printing", "printing"):
        stats.state = state
        adapter.watch(reactor.now)
    assert ace.feed.cleared == []  # standby -> printing is a new print, not a resume


def test_a_failing_clear_is_a_line() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    errored(adapter, reactor, left, ace, stats)

    def clear(lane: int) -> NoReturn:
        raise RuntimeError("no link")

    ace.feed.clear = clear
    stats.state = "printing"
    adapter.watch(reactor.now)
    assert "ace2k_u1: lane 1 not cleared on the resume — no link" in adapter.gcode.lines


# --- the tail: the bay's emptying held back from the U1 ------------------------------------------


def cut(adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k) -> None:
    """Lane 1's filament cut at the bay while following: the tail."""
    adapter.on_feed_event(0, event("tail"))
    ace.lanes[0].update(insert=False, tail=True)
    adapter.watch(reactor.now)


def test_no_stop_in_the_tail_until_the_head_clears_or_the_tail_out() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    for _ in range(20):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    left._set_channel_state(1, "wait_insert", True)  # the U1's own read of the empty inlet
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [] and h.armed and h.follow_wanted
    assert left.channel_state[1] == "wait_insert"  # the U1's state, never written here


END_OUT = "ace2k_u1: lane 1: the end is out of the unit — a new filament can go in"
END_OUT_AGAIN = (
    "ace2k_u1: lane 1: the end is out of the unit — pull lane 1's new filament back out of the bay"
    " and push it in again until the gear takes it"
)


def ticks(adapter: Adapter, reactor: FakeReactor, n: int = 20) -> None:
    """n bay-watch periods, the U1's async callbacks (its answer to a port event) run in each."""
    for _ in range(n):
        reactor.now += 0.2
        adapter.watch(reactor.now)
        reactor.run_async()


def test_an_insert_during_the_tail_changes_nothing_until_the_tail_out() -> None:
    # the tail ignores the bay: a filament put in during it rests behind the old end; the empty
    # bay stays held, the follow runs on as the tail, nothing is told or stopped
    for tail_flag in (True, False):  # the tail's flag reported, or the notice still on its way
        adapter, reactor, left, ace, stats, h = setup()
        loaded(adapter, reactor, left, ace)
        cut(adapter, reactor, ace)
        ace.lanes[0].update(insert=True, tail=tail_flag)  # mode still "following"
        ticks(adapter, reactor)
        assert events(left) == [("port_event", True, 1)], tail_flag  # the load's only
        assert ace.feed.stopped == [] and follows(ace) == [(0, "assist_both")], tail_flag
        assert h.armed and h.follow_wanted and h.port_held and h.present, tail_flag
        assert not [line for line in adapter.gcode.lines if "end is out" in line], tail_flag


def test_after_a_tail_out_a_filament_put_in_during_the_tail_waits_for_the_head_to_clear() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True  # put in during the tail
    ticks(adapter, reactor, 5)
    adapter.on_feed_event(0, event("tail_out"))
    assert adapter.gcode.lines[-1] == END_OUT_AGAIN
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor)
    # the empty bay told once; the filament in the bay not, the old piece still in the head
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    assert not h.follow_wanted and follows(ace) == [(0, "assist_both")]
    left.runout_sensor[1].detected = False  # the old piece's end has passed the head
    ticks(adapter, reactor)
    assert events(left) == [
        ("port_event", True, 1),
        ("port_event", False, 1),
        ("port_event", True, 1),
    ]
    assert adapter.gcode.lines.count(END_OUT_AGAIN) == 1


def test_a_load_of_the_head_by_the_u1_ends_the_hold() -> None:
    # a filament in the bay at the tail_out; the U1's replenish (it reads the bay) loads the same
    # head, its push starting with the head clear: when the head clears later (an unload, a gap
    # mid-print) no insert is raised. A load that finds the head still set while the lane waits
    # behind its piece ends nothing (the tests below)
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor)
    assert h.after_tail and not h.present
    left.runout_sensor[1].detected = False  # the old piece out; then the U1's load
    reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
    left._put_into_drive(1)
    reactor.on_pause = []
    left._set_channel_state(1, "load_finish", True)
    assert not h.after_tail and h.present
    left.runout_sensor[1].detected = False  # the head clears later
    ticks(adapter, reactor)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]


def test_a_load_finding_the_head_set_ends_the_hold_of_a_lane_not_waiting_behind_a_piece() -> None:
    # the window between the head sensor's clear (the flag released) and the U1's push: the
    # filament already at the head's sensor when its load starts — the lane's own, the hold ends
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("printing")
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    assert h.waits_behind and h.rise_held
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor, 6)  # clear past the debounce
    assert not h.waits_behind
    left.runout_sensor[1].detected = True  # the filament reached the sensor before the push
    assert left._put_into_drive(1) is None
    left._set_channel_state(1, "load_finish", True)
    assert h.present and not h.rise_held and not h.after_tail
    assert follows(ace)[-1] == (0, "assist_both") and h.follow_wanted


def test_the_no_follow_line_is_logged_once_per_episode(caplog: pytest.LogCaptureFixture) -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("printing")
    with caplog.at_level("INFO"):
        for _ in range(3):
            left._set_channel_state(1, "load_extruding")
            left._set_channel_state(1, "load_finish", True)
        adapter.grip_watch.on_ace_clear(0)
        adapter.grip_watch._set_waits_behind(h, "test")
        left._set_channel_state(1, "load_extruding")
    said = [r for r in caplog.records if "no follow" in r.getMessage()]
    assert len(said) == 2


def test_in_a_print_the_held_insert_is_dropped_and_the_replenish_loads() -> None:
    for state in ("printing", "paused"):
        adapter, reactor, left, ace, stats, h = setup(state)
        loaded(adapter, reactor, left, ace)
        cut(adapter, reactor, ace)
        ace.lanes[0]["insert"] = True
        adapter.on_feed_event(0, event("tail_out"))
        ace.lanes[0].update(mode="idle", tail=False)
        ticks(adapter, reactor)
        left.runout_sensor[1].detected = False  # the old piece has run out
        ticks(adapter, reactor)
        assert events(left) == [("port_event", True, 1), ("port_event", False, 1)], state
        assert not h.after_tail and h.present, state
        # the U1's runout replenish: its load pushes the filament to the head
        reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
        assert left._put_into_drive(1) is None, state
        reactor.on_pause = []
        assert ace.feed.started[-1][:2] == (0, "feed"), state
        ticks(adapter, reactor)
        assert len(events(left)) == 2, state


def test_in_a_print_a_lane_in_error_is_told_before_the_drop() -> None:
    # the head clears in a print while the lane's load is in error: the error line comes, and
    # the insert is dropped only once the lane is cleared and settled — never raised
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="error", tail=False)
    ticks(adapter, reactor)
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor)
    line = "ace2k_u1: lane 1: the unit's load ended in error — ACEK_CLEAR LANE=1, then re-insert"
    assert adapter.gcode.lines.count(line) == 1
    assert h.after_tail and not h.present
    ace.lanes[0]["mode"] = "idle"  # ACEK_CLEAR
    adapter.watch(reactor.now)
    assert h.after_tail and not h.present  # the settle window first
    ticks(adapter, reactor)
    assert not h.after_tail and h.present
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]


def test_outside_a_print_the_held_insert_is_released_once() -> None:
    adapter, reactor, left, ace, stats, h = setup("standby")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor)
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor)
    ticks(adapter, reactor)
    assert events(left) == [
        ("port_event", True, 1),
        ("port_event", False, 1),
        ("port_event", True, 1),
    ]


def test_the_held_insert_is_dropped_when_the_filament_leaves() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    adapter.on_feed_event(0, event("tail_out"))
    assert adapter.gcode.lines[-1] == END_OUT
    ace.lanes[0].update(mode="idle", tail=False)
    ace.lanes[0]["insert"] = True  # a new filament, the old piece still in the head
    ticks(adapter, reactor)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    ace.lanes[0]["insert"] = False  # taken out again before the head cleared
    ticks(adapter, reactor, 2)
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]  # nothing held
    assert not h.after_tail
    ace.lanes[0]["insert"] = True  # in again, the head clear: the stock insert
    ticks(adapter, reactor)
    assert events(left)[-1] == ("port_event", True, 1) and len(events(left)) == 3


def test_a_tail_out_with_the_head_already_clear_holds_nothing() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    left.runout_sensor[1].detected = False  # the stop at the head's sensor
    adapter.watch(reactor.now)
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor, 2)
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    assert events(left)[-1] == ("port_event", True, 1) and len(events(left)) == 3


def test_the_tail_out_ends_the_follows_standing() -> None:
    # a filament put in during the tail is not at the gear: no follow is armed for it
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor)
    assert not h.follow_wanted and not h.armed
    assert follows(ace) == [(0, "assist_both")]  # the load's only


def test_the_tail_out_sends_the_held_empty_bay_once() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    assert events(left) == [("port_event", True, 1)] and h.port_held
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    for _ in range(10):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    assert not h.port_held and not h.present and ace.feed.stopped == []


def test_the_head_clearing_ends_the_tail_and_sends_the_held_empty_bay_once() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    left.runout_sensor[1].detected = False
    for _ in range(5):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert ace.feed.stopped == [0]
    assert events(left) == [("port_event", True, 1)]  # the stop's report not in yet
    adapter.on_feed_event(0, event("stopped"))
    ace.lanes[0].update(mode="idle", tail=False)
    for _ in range(5):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    assert ace.feed.stopped == [0] and not h.follow_wanted


def test_the_preload_of_a_fresh_filament_is_not_raced_by_the_follow() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    adapter.watch(reactor.now)
    left._set_channel_state(1, "wait_insert", True)  # the U1 told of the empty bay
    left.runout_sensor[1].detected = False
    h.follow_wanted = True  # even were it still wanted
    ace.lanes[0]["insert"] = True  # a fresh filament
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)
    assert events(left)[-1] == ("port_event", True, 1)
    ticks = []

    def tick(
        now: float,
    ) -> None:  # the bay watch runs while the push polls; the filament reaches the head
        ticks.append(now)
        adapter.watch(now)
        if len(ticks) == 10:
            left.runout_sensor[1].detected = True

    reactor.on_pause = [tick]
    adapter.push_runner.preload(h)
    reactor.on_pause = []
    for _ in range(10):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert left.channel_state[1] == "preload_finish"
    # the load's follow, then the push alone, then the insertion's follow once the preload ended
    # (ace2k leaves the adapter's lanes' insertions to it): never a follow during the push
    assert [s[:2] for s in ace.feed.started[-2:]] == [(0, "feed"), (0, "assist_both")]
    assert follows(ace) == [(0, "assist_both")] * 2
    assert ace.feed.attempts == len(ace.feed.started)
    assert not [line for line in adapter.gcode.lines if "not armed" in line]


# --- the adapter's own stop, exactly --------------------------------------------------------------


def test_a_slow_stopped_after_the_adapters_stop_is_still_its_own() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    left._set_channel_state(1, "preload_finish", True)  # a head runout: the adapter stops it
    assert ace.feed.stopped == [0] and h.expect_stopped
    reactor.now += 5.0
    adapter.on_feed_event(0, event("stopped", seq=h.assist_seq))
    assert h.follow_wanted and not h.expect_stopped and adapter.gcode.lines == []


def test_a_late_stopped_after_the_re_arm_keeps_the_new_follow_armed() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    left._set_channel_state(1, "load_extruding")
    ace.lanes[0]["mode"] = "following"
    old = h.assist_seq
    ace.feed.start_move = _start_and_end(ace.feed.start_move)
    ace2k_u1_hooks.nudge(adapter, h)  # stop, the retry feed, the follow armed again
    assert h.armed and h.assist_seq != old
    reactor.now += 5.0
    adapter.on_feed_event(0, event("stopped", seq=old))  # the old follow's stop, late
    assert h.armed and h.assist_seq is not None and h.follow_wanted


def test_an_idle_lanes_fail_stop_expects_nothing_and_a_hand_stop_is_still_seen() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    left._set_channel_state(1, "load_fail")  # lane idle: the stop ends nothing
    assert ace.feed.stopped == [0] and not h.expect_stopped
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, event("stopped"))  # ACEK_STOP by hand
    assert not h.follow_wanted


def test_any_other_final_event_answers_the_adapters_stop() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    left._set_channel_state(1, "preload_finish", True)
    adapter.on_feed_event(0, event("stuck"))  # the stopped lane ended otherwise
    assert not h.expect_stopped
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, event("stopped"))  # a later stop: by hand
    assert not h.follow_wanted


def test_a_filament_inserted_after_the_tail_out_is_a_new_one() -> None:
    # the tail_out came, the report still says following: the fresh filament's rise is not a
    # re-insert during the tail — the held empty bay goes first, then the stock insert once the
    # old piece has left the head
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(insert=True, tail=False)  # mode still "following": no report yet
    adapter.watch(reactor.now)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    assert not h.port_held and not h.present
    ace.lanes[0]["mode"] = "idle"
    ticks(adapter, reactor, int(SETTLE_S / 0.2) + 2)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor, 1)
    assert events(left) == [
        ("port_event", True, 1),
        ("port_event", False, 1),
        ("port_event", True, 1),
    ]


def test_a_stop_after_a_final_event_the_report_has_not_caught_up_with_expects_nothing() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0]["mode"] = "feeding"  # a push, as last reported
    adapter.on_feed_event(0, event("stuck", mode="feeding"))  # it ended; no report since
    left._set_channel_state(1, "load_fail")  # the U1's answer: the *_fail stop
    assert ace.feed.stopped == [0] and not h.expect_stopped
    left._set_channel_state(1, "load_finish", True)  # armed later: a running follow
    ace.lanes[0]["mode"] = "following"
    adapter.on_feed_event(0, event("stopped"))  # ACEK_STOP by hand
    assert not h.follow_wanted


def test_the_report_catching_up_makes_a_running_lane_count_again() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    adapter.on_feed_event(0, event("stuck", mode="feeding"))
    ace.lanes[0]["mode"] = "idle"
    adapter.watch(reactor.now)  # the report has caught up
    ace.lanes[0]["mode"] = "following"  # a motion started by hand, reported
    assert adapter.running(h)
    adapter.halt(h)  # stopped, but not this adapter's motion: its seq unknown, nothing expected
    assert ace.feed.stopped == [0] and not h.expect_stopped


def test_a_stop_on_a_stale_report_expects_nothing_and_a_later_hand_stop_is_seen() -> None:
    # nothing of the adapter's open, the report saying a one-way assist that has in fact ended
    # (its end not seen): the bay's emptying stops the lane on that report — an expectation then
    # would answer the hand stop of a follow armed later, kept wanted and re-armed against the
    # operator
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    h.armed, h.assist_seq, h.motion_open = False, None, False
    ace.lanes[0]["mode"] = "assisting"  # stale: the lane is idle
    ace.lanes[0]["insert"] = False  # out of the bay
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [0] and not h.expect_stopped
    ace.lanes[0]["mode"] = "idle"
    adapter.watch(reactor.now)
    ace.lanes[0]["insert"] = True  # back in; the U1 loads the head again: the follow armed
    left.channel_state[1] = "load_flushing"
    left._set_channel_state(1, "load_finish", True)
    assert h.armed and h.follow_wanted
    adapter.on_feed_event(0, event("stopped", seq=h.assist_seq))  # ACEK_STOP by hand
    assert not h.follow_wanted
    assert [line for line in adapter.gcode.lines if "by hand" in line]


def test_a_stop_right_after_the_arm_is_the_adapters_own() -> None:
    # the follow just armed, the report still idle: the adapter's stop is still its own
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0]["insert"] = True
    left._set_channel_state(1, "load_finish", True)
    assert ace.lanes[0]["mode"] == "idle" and h.armed
    left._set_channel_state(1, "preload_finish", True)  # a head runout: the adapter stops it
    assert ace.feed.stopped == [0] and h.expect_stopped
    adapter.on_feed_event(0, event("stopped", seq=h.assist_seq))
    assert h.follow_wanted and adapter.gcode.lines == []


def test_a_retry_right_after_the_arm_keeps_its_new_follow() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    left._set_channel_state(1, "load_extruding")  # the report still idle
    old = h.assist_seq
    ace.feed.start_move = _start_and_end(ace.feed.start_move)
    ace2k_u1_hooks.nudge(adapter, h)
    assert h.armed and h.assist_seq != old
    adapter.on_feed_event(0, event("stopped", seq=old))  # the old follow's stop
    assert h.armed and h.assist_seq is not None and h.follow_wanted
    assert adapter.gcode.lines == []


def test_a_tail_out_past_the_head_sensor_is_not_stopped_again() -> None:
    # the tail_out came, the filament's end already past the head; the report still says
    # following: the held empty bay goes out, no "tail passed" stop of the idle lane
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    left.runout_sensor[1].detected = False
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0]["tail"] = False  # mode still "following", insert False
    lines = list(adapter.gcode.lines)
    for _ in range(5):
        reactor.now += 0.2
        adapter.watch(reactor.now)
    assert ace.feed.stopped == []
    assert adapter.gcode.lines == lines
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]


# --- the lane's current motion: one source for every stop and suppression ------------------------


def test_the_bay_emptying_right_after_the_arm_is_the_armed_follows_tail() -> None:
    # load_finish armed the follow; the filament leaves the bay before any report shows it
    # following: the unit is in the follow, so in its tail — the lane is not left running
    # unseen, nor stopped: still armed, wanted, the empty bay held
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0]["insert"] = True
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)  # the U1 told of the filament (its head empty)
    left.runout_sensor[1].detected = True  # its preload: the filament at the head's sensor
    left._set_channel_state(1, "load_finish", True)
    assert ace.lanes[0]["mode"] == "idle" and h.armed
    ace.lanes[0]["insert"] = False
    adapter.watch(reactor.now)
    assert h.armed and h.follow_wanted and h.port_held
    assert events(left) == [("port_event", True, 1)]
    left.runout_sensor[1].detected = False  # its end at the head: the backstop still acts
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [0] and h.expect_stopped


def test_a_hand_assist_reported_when_the_bay_empties_is_stopped() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    reactor.now += 10.0
    adapter.on_feed_event(0, event("stopped"))  # the follow stopped by hand
    reactor.now += SETTLE_S  # reports since: a one-way assist started by hand
    ace.lanes[0]["mode"] = "assisting"
    adapter.watch(reactor.now)
    ace.lanes[0]["insert"] = False
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [0] and h.expect_stopped


def test_an_old_follows_late_stop_leaves_the_tail_backstop_armed() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    old = h.assist_seq
    ace.feed.start_move = _start_and_end(ace.feed.start_move)
    ace2k_u1_hooks.nudge(adapter, h)  # the follow stopped and armed again
    adapter.on_feed_event(0, event("stopped", seq=old))  # the old follow's stop, late
    assert h.armed and h.motion_seq == h.assist_seq
    cut(adapter, reactor, ace)
    stopped = list(ace.feed.stopped)
    left.runout_sensor[1].detected = False
    adapter.watch(reactor.now)
    assert ace.feed.stopped == stopped + [0]  # the tail's end still stops the lane


def test_a_hand_follow_soon_after_an_end_gets_the_tail_backstop_once_believed() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    reactor.now += 10.0
    adapter.on_feed_event(0, event("stopped"))  # by hand; then a follow by hand at once
    assert not h.armed
    cut(adapter, reactor, ace)  # mode still "following": the hand follow's tail
    left.runout_sensor[1].detected = False
    adapter.watch(reactor.now)
    assert ace.feed.stopped == []  # the end just came: the report not believed yet
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [0]


def test_an_armed_follow_reported_idle_long_after_is_gone() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ace.lanes[0]["mode"] = "idle"  # its final event never seen
    adapter.watch(reactor.now)
    assert h.armed  # just armed: the report may not show it yet
    assert follows(ace) == [(0, "assist_both")]
    reactor.now += SETTLE_S + 0.01
    adapter.watch(reactor.now)  # counted gone: the standing follow arms it again at once
    assert follows(ace) == [(0, "assist_both")] * 2 and h.armed


def test_a_hand_assist_started_inside_a_fresh_end_is_stopped_when_the_bay_empties() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    reactor.now += 10.0
    adapter.on_feed_event(0, event("stopped"))  # by hand; a one-way assist by hand at once
    ace.lanes[0].update(mode="assisting", insert=False)
    adapter.watch(reactor.now)  # the end still fresh: motion says none
    assert ace.feed.stopped == [0] and not h.expect_stopped


def test_an_older_motions_late_end_does_not_answer_the_stop_of_the_newer() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    a = h.assist_seq
    ace.feed.start_move = _start_and_end(ace.feed.start_move)
    ace2k_u1_hooks.nudge(adapter, h)  # follow A stopped (its end still to come), follow B armed
    b = h.assist_seq
    reactor.now += 10.0
    left._set_channel_state(1, "preload_finish", True)  # the adapter stops B
    assert h.expect_stopped and h.expect_seq == b
    adapter.on_feed_event(0, event("stopped", seq=a))  # A's late end, first
    assert h.expect_stopped and h.follow_wanted
    adapter.on_feed_event(0, event("stopped", seq=b))  # B's own stop
    assert not h.expect_stopped and h.follow_wanted
    assert not [line for line in adapter.gcode.lines if "by hand" in line]


# --- the bay's insert held while the head's sensor sees filament (any time) ---------------------


def bench_tangle(
    state: str,
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeAce2k, FakePrintStats, Hooked]:
    """The sequence met on the unit: lane 1's follow ends tangled (no tail: the old end still at the
    insert sensor), the print paused; the filament pulled, ACEK_CLEAR, put in again; the unit's
    load ends blocked behind the old piece — the head's sensor still sees it."""
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, event("tangled"))
    ace.lanes[0].update(mode="error", error="tangled")
    stats.state = "paused"
    ticks(adapter, reactor, 2)
    ace.lanes[0]["insert"] = False  # pulled
    ticks(adapter, reactor, 2)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    ace.lanes[0].update(mode="idle", error=None)  # ACEK_CLEAR
    ace.lanes[0].update(insert=True, mode="loading")  # in again: the unit's load
    ticks(adapter, reactor, 3)
    adapter.on_feed_event(0, event("blocked", mode="loading"))
    ace.lanes[0]["mode"] = "idle"
    ticks(adapter, reactor)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]  # no preload
    assert h.rise_held and not h.present
    stats.state = state
    return adapter, reactor, left, ace, stats, h


def test_the_bench_tangle_insert_is_held_then_dropped_in_a_print() -> None:
    for state in ("paused", "printing"):
        adapter, reactor, left, ace, stats, h = bench_tangle(state)
        left.runout_sensor[1].detected = False  # the old piece has run out
        ticks(adapter, reactor)
        assert events(left) == [("port_event", True, 1), ("port_event", False, 1)], state
        assert h.present and not h.rise_held, state


def test_the_bench_tangle_insert_is_released_once_outside_a_print() -> None:
    adapter, reactor, left, ace, stats, h = bench_tangle("standby")
    left.runout_sensor[1].detected = False  # the old piece unloaded
    ticks(adapter, reactor)
    ticks(adapter, reactor)
    assert events(left) == [
        ("port_event", True, 1),
        ("port_event", False, 1),
        ("port_event", True, 1),
    ]


def test_a_u1_load_ends_a_held_insert() -> None:
    adapter, reactor, left, ace, stats, h = bench_tangle("paused")
    left._set_channel_state(1, "load_finish", True)  # a load that found the old piece set
    assert not h.present and h.rise_held  # this filament still waits behind it
    left.runout_sensor[1].detected = False  # the old piece unloaded; the U1 loads from its screen
    reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
    assert left._put_into_drive(1) is None
    reactor.on_pause = []
    left._set_channel_state(1, "load_finish", True)
    assert h.present and not h.rise_held
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor)
    assert len(events(left)) == 2


def test_an_insert_into_an_empty_heads_bay_is_told_at_once() -> None:
    # regression: the head's sensor clear, the stock insert after the settle window, as before
    adapter, reactor, left, ace, stats, h = setup("printing")
    ace.lanes[0]["insert"] = True
    adapter.watch(reactor.now)
    assert events(left) == []
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)
    assert events(left) == [("port_event", True, 1)] and h.present and not h.rise_held


def test_a_loaded_head_with_its_filament_announced_holds_nothing() -> None:
    # the head loaded by this very lane: the filament is told, no rise is pending, nothing fires
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    for state in ("load_finish", "unload_prepare", "unload_doing", "load_finish"):
        left._set_channel_state(1, state)
        ticks(adapter, reactor, 3)
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor)
    assert events(left) == [("port_event", True, 1)] and not h.rise_held


def test_a_reseat_on_a_loaded_head_is_held_until_the_head_clears() -> None:
    # the U1 answers the removal by leaving load_finish (its remove-filament, run async): the
    # filament pushed straight back is held while the head's sensor sees it — no preload against
    # the loaded head — and released once the head clears outside a print
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ace.lanes[0].update(mode="idle", insert=False)
    h.armed = False
    ticks(adapter, reactor, 2)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    assert left.channel_state[1] == "wait_insert"
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    assert h.rise_held and not h.present and len(events(left)) == 2
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor)
    assert events(left)[-1] == ("port_event", True, 1) and len(events(left)) == 3


# --- the grip: a filament put in while the head holds a piece the lane does not own ---------------

GRIP = (0, 40.0)
UNGRIP = (0, 0)
NOT_READ = (
    "ace2k_u1: lane 1: the gripped spool's tag is read when it moves — e0 keeps its filament"
    " setting until then"
)


def tail_out_on_occupied_head(
    state: str = "printing", tag: TagRecord | None = None
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeAce2k, FakePrintStats, Hooked]:
    """Lane 1 loaded, cut, its tail out with the old piece still in the head; the bay empty.

    ``tag`` is the old spool's record, shown by the lane from before its bay emptied."""
    adapter, reactor, left, ace, stats, h = setup(state)
    if tag is not None:
        ace.lanes[0]["tag"] = tag
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    return adapter, reactor, left, ace, stats, h


def gripped_load(
    adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k, tag: TagRecord | None = None
) -> None:
    """A filament put in: the unit's own load (seq 0), with the grip set, ends loaded."""
    ace.lanes[0].update(insert=True, mode="loading")
    ticks(adapter, reactor, 2)
    if tag is not None:
        ace.lanes[0]["tag"] = tag
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    ace.lanes[0]["mode"] = "idle"


def test_a_tail_out_on_an_occupied_head_sets_the_grip_at_once() -> None:
    for state in ("printing", "standby"):
        adapter, reactor, left, ace, stats, h = setup(state)
        loaded(adapter, reactor, left, ace)
        cut(adapter, reactor, ace)
        ticks(adapter, reactor, 5)
        assert ace.feed.grips == [], state  # the tail: the lane owns the head
        adapter.on_feed_event(0, event("tail_out"))
        assert ace.feed.grips == [GRIP], state  # before any tick: an insert edge may follow
        ace.lanes[0].update(mode="idle", tail=False)
        ticks(adapter, reactor)
        assert ace.feed.grips == [GRIP] and h.grip_wanted, state  # sent once


def test_a_tail_out_with_the_head_clear_sets_no_grip() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    left.runout_sensor[1].detected = False
    adapter.watch(reactor.now)
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor)
    assert ace.feed.grips == [] and not h.grip_wanted


def test_no_grip_while_the_lane_owns_its_head() -> None:
    # loaded and following, its tail, the U1's loaded and preloaded states, the follow armed
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ticks(adapter, reactor)
    for state in ("load_finish", "unload_prepare", "unload_doing", "load_extruding"):
        left._set_channel_state(1, state)
        ticks(adapter, reactor, 3)
    ended(adapter, reactor, ace)  # the follow ended, the channel still loaded: still its own
    ticks(adapter, reactor)
    left._set_channel_state(1, "preload_finish")
    ticks(adapter, reactor)
    cut(adapter, reactor, ace)
    ticks(adapter, reactor, 5)
    assert ace.feed.grips == []


def test_the_grip_is_set_before_the_insert_once_the_lanes_filament_leaves_a_loaded_head() -> None:
    # the filament taken out of a loaded head's bay: the U1 leaves load_finish, the head's sensor
    # still sees the filament — no longer the lane's; set before a filament can be put in again
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ace.lanes[0].update(mode="idle", insert=False)
    h.armed = False
    ticks(adapter, reactor, 2)
    assert left.channel_state[1] == "wait_insert"
    assert ace.feed.grips == [GRIP]
    ace.lanes[0]["insert"] = True  # in again: held, as before, and still gripped
    ticks(adapter, reactor)
    assert h.rise_held and ace.feed.grips == [GRIP]


def test_the_grip_is_set_for_the_bench_tangle_before_the_filament_goes_in_again() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    adapter.on_feed_event(0, event("tangled"))
    ace.lanes[0].update(mode="error", error="tangled")
    stats.state = "paused"
    ticks(adapter, reactor, 2)
    assert ace.feed.grips == []  # in error, the channel still loaded: the lane's own
    ace.lanes[0]["insert"] = False  # pulled
    ticks(adapter, reactor, 2)
    assert ace.feed.grips == [GRIP]  # before the filament goes in again


def test_the_grip_is_cleared_when_the_heads_sensor_clears() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    ticks(adapter, reactor)
    left.runout_sensor[1].detected = False  # the old piece unloaded
    ticks(adapter, reactor)
    assert ace.feed.grips == [GRIP, UNGRIP] and not h.grip_wanted
    ticks(adapter, reactor)
    assert ace.feed.grips == [GRIP, UNGRIP]  # once


def test_the_insert_falling_with_the_head_still_occupied_keeps_the_grip() -> None:
    # the operator takes the gripped filament out and puts it in again while the old piece is
    # still in the head: the next load must grip as well — never chase the old piece
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    ace.lanes[0]["insert"] = False
    ticks(adapter, reactor)
    assert UNGRIP not in ace.feed.grips and h.grip_wanted and h.grip_live


def test_the_gripped_load_asks_no_read_without_motion_and_the_grip_set_again() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    read = dict(state="read", uid="04AB", source="tag", record={"uid": "04AB"})
    gripped_load(adapter, reactor, ace, tag=read)
    assert not h.grip_live  # consumed by that load on the unit
    reactor.run_timers()
    assert ace.rfid.reads == []  # a tag is read only while its lane moves
    assert not adapter.tag_reader.read_waits  # read already: done at the first look
    assert not [line for line in adapter.gcode.lines if "is read when it moves" in line]
    ticks(adapter, reactor)
    # the filament waits at the gear behind the old piece: held, and the grip set again for a
    # filament taken out and put in again
    assert h.rise_held and not h.present
    assert ace.feed.grips == [GRIP, GRIP] and h.grip_live
    assert len(events(left)) == 2  # no preload against the old piece


def test_an_unreadable_status_at_the_gripped_loads_end_still_resets_the_lane() -> None:
    # the bay read at the gripped load's end fails: counted as no filament (no read asked), and
    # every reset after it still runs — the grip gone on the unit, the end noted
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ace.lanes[0].update(insert=True, mode="loading")
    ticks(adapter, reactor, 2)
    assert h.grip_live and h.grip_down

    def broken(eventtime: float) -> NoReturn:
        raise RuntimeError("no status")

    ace.get_status = broken
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    assert not h.grip_live and not h.grip_down and h.ended_at is not None
    assert not adapter.tag_reader.read_pending


def test_a_gripped_load_whose_tag_is_not_read_is_one_console_line() -> None:
    for tag in (dict(fakes.NO_TAG, state="no_tag"), dict(fakes.NO_TAG)):
        adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
        gripped_load(adapter, reactor, ace, tag=tag)
        start = reactor.now
        reactor.run_timers()
        assert ace.rfid.reads == [] and adapter.gcode.scripts == []
        reactor.run_timers(start + 4.9)  # a read on the grip's move may still settle: no line yet
        assert NOT_READ not in adapter.gcode.lines
        reactor.run_timers(start + 5.5)  # the deadline: the line, once
        assert adapter.gcode.lines.count(NOT_READ) == 1
        reactor.run_timers(start + 20.0)
        assert ace.rfid.reads == [] and not adapter.tag_reader.read_waits
        assert adapter.gcode.lines.count(NOT_READ) == 1


def test_a_session_running_at_the_deadline_extends_the_watch_without_a_line() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    gripped_load(adapter, reactor, ace, tag=dict(fakes.NO_TAG, state="pending"))
    start = reactor.now
    reactor.run_timers()
    ace.lanes[0]["tag"] = dict(fakes.NO_TAG, state="reading")  # a session runs at the deadline
    reactor.run_timers(start + 6.0)
    assert NOT_READ not in adapter.gcode.lines and adapter.tag_reader.read_waits
    ace.lanes[0]["tag"] = dict(state="read", uid="04AB", source="tag", record={"uid": "04AB"})
    reactor.run_timers(start + 7.0)
    assert not adapter.tag_reader.read_waits and NOT_READ not in adapter.gcode.lines
    # a session that ends without a read: the line, once
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    gripped_load(adapter, reactor, ace, tag=dict(fakes.NO_TAG, state="pending"))
    start = reactor.now
    reactor.run_timers()
    ace.lanes[0]["tag"] = dict(fakes.NO_TAG, state="reading")
    reactor.run_timers(start + 6.0)
    ace.lanes[0]["tag"] = dict(fakes.NO_TAG, state="pending")
    reactor.run_timers(start + 7.0)
    assert adapter.gcode.lines.count(NOT_READ) == 1 and not adapter.tag_reader.read_waits


def test_a_tag_read_after_the_start_ends_the_wait_without_a_line() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    gripped_load(adapter, reactor, ace, tag=dict(fakes.NO_TAG, state="pending"))
    start = reactor.now
    reactor.run_timers()
    assert adapter.tag_reader.read_waits  # watched, no read asked
    assert ace.rfid.reads == []
    ace.lanes[0]["tag"] = dict(state="read", uid="04AB", source="tag", record={"uid": "04AB"})
    reactor.run_timers(start + 1.0)
    assert not adapter.tag_reader.read_waits
    reactor.run_timers(start + 20.0)
    assert not [line for line in adapter.gcode.lines if "is read when it moves" in line]


def test_no_reader_is_called_and_a_missing_one_changes_nothing() -> None:
    # no read without motion is asked: a unit without a tag reader gives the same one line
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    del ace.rfid
    gripped_load(adapter, reactor, ace)
    start = reactor.now
    reactor.run_timers()
    assert adapter.gcode.scripts == [] and NOT_READ not in adapter.gcode.lines
    reactor.run_timers(start + 5.5)
    assert adapter.gcode.lines.count(NOT_READ) == 1


def test_only_the_gripped_load_asks_for_the_tag() -> None:
    # a plain automatic load (no grip), a load this adapter started, a gripped lane's other ends
    adapter, reactor, left, ace, stats, h = setup()
    ace.lanes[0].update(insert=True, mode="loading")
    ticks(adapter, reactor, 2)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    adapter.on_feed_event(0, event("blocked", seq=0, mode="loading"))
    ticks(adapter, reactor)
    adapter.on_feed_event(0, event("loaded", seq=7, mode="loading"))
    reactor.run_timers()
    assert ace.rfid.reads == [] and adapter.gcode.scripts == []


def test_a_start_by_the_adapter_counts_the_grip_as_gone_on_the_unit() -> None:
    # the U1's replenish once the old piece has run out: the push is a start (it clears the
    # grip on the unit); the head clear, the grip is no longer wanted
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    left.runout_sensor[1].detected = False  # the old piece has run out
    ticks(adapter, reactor)
    assert ace.feed.grips[-1] == UNGRIP
    reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
    assert left._put_into_drive(1) is None
    reactor.on_pause = []
    assert not h.grip_live
    left._set_channel_state(1, "load_finish", True)
    ace.lanes[0]["mode"] = "following"
    ticks(adapter, reactor)
    assert ace.feed.grips[-1] == UNGRIP and not h.grip_wanted


def test_a_refused_grip_is_said_once_and_tried_again_when_the_want_changes() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.feed.grip_fail = RuntimeError("the link is down")
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor)
    line = "ace2k_u1: lane 1 grip not set — the link is down"
    assert adapter.gcode.lines.count(line) == 1 and ace.feed.grips == []
    ace.feed.grip_fail = None
    left.runout_sensor[1].detected = False  # the want changes: nothing to clear, none sent
    ticks(adapter, reactor)
    left.runout_sensor[1].detected = True  # occupied again: tried again
    ticks(adapter, reactor)
    assert ace.feed.grips == [GRIP]


@pytest.mark.parametrize("refused", [None, "this want", "the other want"])
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("wanted", [False, True])
@pytest.mark.parametrize("holds", [False, True])
def test_the_grip_is_sent_unless_there_or_refused_for_this_want(
    holds: bool, wanted: bool, live: bool, refused: str | None
) -> None:
    # sync_grip's dedupe, (want == grip_wanted and (grip_live or not want)) or refused, over
    # every combination: a refusal of the other want is dropped first (tried again)
    adapter, reactor, left, ace, stats, h = setup()
    want = holds  # a head of one lane, idle, owned by nothing: the grip is wanted while it holds
    h.grip_wanted, h.grip_live = wanted, live
    h.grip_refused = {None: None, "this want": want, "the other want": not want}[refused]
    adapter.grip_watch.sync_grip(h, holds=holds)
    skipped = (want == wanted and (live or not want)) or refused == "this want"
    assert ace.feed.grips == ([] if skipped else [GRIP if want else UNGRIP])


def test_the_status_line_shows_the_grip() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    lines = []
    adapter.status_lines(lines.append)
    (lane1,) = [line for line in lines if line.startswith("lane 1 ")]
    assert " grip=40 mm " in lane1
    (lane2,) = [line for line in lines if line.startswith("lane 2 ")]
    assert " grip=off " in lane2
    # the grip consumed by its load on the unit, before the next tick sets it again
    gripped_load(adapter, reactor, ace)
    lines = []
    adapter.status_lines(lines.append)
    (lane1,) = [line for line in lines if line.startswith("lane 1 ")]
    assert " grip=off (wanted, to be set again) " in lane1


def test_an_unreadable_head_sensor_counts_as_holding_and_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace, stats, h = setup()

    def broken(eventtime: float) -> NoReturn:
        raise OSError("sensor gone")

    left.runout_sensor[1].get_status = broken
    with caplog.at_level("ERROR"):
        ticks(adapter, reactor, 5)
    assert ace.feed.grips == [GRIP]
    said = [r for r in caplog.records if "head sensor unreadable" in r.getMessage()]
    assert len(said) == 1


def test_no_grip_through_a_u1_load_of_the_head_failed_or_not() -> None:
    # the U1's load after the old piece ran out: its push brings this lane's filament to the head's
    # sensor, then it heats (minutes) before extruding — the filament is the lane's own all along,
    # and stays so when the load fails on the way
    for last in ("load_extruding", "load_fail"):
        adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
        gripped_load(adapter, reactor, ace)
        ticks(adapter, reactor)
        left.runout_sensor[1].detected = False  # the old piece has run out
        ticks(adapter, reactor)
        assert ace.feed.grips == [GRIP, GRIP, UNGRIP], last
        left._set_channel_state(1, "load_prepare")
        reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
        assert left._put_into_drive(1) is None, last
        reactor.on_pause = []
        for state in ("load_heating", "load_feeding", last):
            left._set_channel_state(1, state)
            ace.lanes[0]["mode"] = "idle"
            ticks(adapter, reactor)
        assert ace.feed.grips == [GRIP, GRIP, UNGRIP], last
        assert not h.grip_wanted, last
        # the older image's line on the wait, said once, is not a grip line
        said = [line for line in adapter.gcode.lines if "grip" in line and "no wait" not in line]
        assert not said, last


def test_a_read_with_motion_on_a_gripped_lane_asks_no_motionless_read() -> None:
    # ACEK_RFID_READ LANE=1 MOVE=1 by hand on a filament waiting at the gear: the unit clears the
    # grip and ends its search loaded, seq 0, mode loading — no insert edge: not the gripped load
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    read = dict(state="read", uid="04AB", source="tag", record={"uid": "04AB"})
    gripped_load(adapter, reactor, ace, tag=read)
    reactor.run_timers()
    ticks(adapter, reactor)
    assert ace.rfid.reads == [] and h.grip_live  # set again for the next filament
    ace.lanes[0].update(mode="loading", tag=dict(fakes.NO_TAG, state="searching"))
    ticks(adapter, reactor, 3)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    ace.lanes[0].update(mode="idle", tag=dict(fakes.NO_TAG, state="no_tag"))
    start = reactor.now
    reactor.run_timers(start + 20.0)
    assert ace.rfid.reads == []
    assert not [line for line in adapter.gcode.lines if "is read when it moves" in line]


def test_a_stale_read_record_is_not_taken_for_the_gripped_spools_tag() -> None:
    # the lane still shows the old spool's record when the new filament goes in: the same record
    # after the gripped load is no read of the new spool — the line at the deadline
    old = dict(state="read", uid="0411", source="tag", record={"uid": "0411"})
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head(tag=old)
    ticks(adapter, reactor, 1)  # the bay empty, the old record shown
    gripped_load(adapter, reactor, ace)
    start = reactor.now
    reactor.run_timers(start + 20.0)
    assert adapter.gcode.lines.count(NOT_READ) == 1
    # a new uid read instead is the new spool's: no line
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head(tag=old)
    ticks(adapter, reactor, 1)
    gripped_load(adapter, reactor, ace, tag=dict(old, uid="0422", record={"uid": "0422"}))
    reactor.run_timers(reactor.now + 20.0)
    assert not [line for line in adapter.gcode.lines if "is read when it moves" in line]


def test_the_owned_states_are_built_once_at_the_hook() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    calls = []
    build = adapter.build_owned_states
    adapter.build_owned_states = lambda: calls.append(1) or build()
    ticks(adapter, reactor, 5)
    left.runout_sensor[1].detected = True
    ticks(adapter, reactor, 5)
    assert calls == [] and "load_heating" in adapter.owned_states  # built at the hook


def test_a_gripped_load_ending_before_the_bay_watch_sees_the_insert_is_still_read() -> None:
    # a short grip and a slow watch: the unit's loaded comes before any tick saw the insert up
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ticks(adapter, reactor, 2)
    ace.lanes[0].update(insert=True, mode="idle")  # no tick since
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    assert adapter.tag_reader.read_pending == [h]


def test_the_same_spool_read_again_after_the_read_started_counts() -> None:
    # the old record shown when the filament went in, the same spool: the state leaves "read"
    # (reading) after the read starts and comes back with the same uid — this read's outcome
    same = dict(state="read", uid="0411", source="tag", record={"uid": "0411"})
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head(tag=same)
    ticks(adapter, reactor, 1)
    gripped_load(adapter, reactor, ace)
    start = reactor.now
    reactor.run_timers()
    assert adapter.tag_reader.read_waits
    ace.lanes[0]["tag"] = dict(same, state="reading")
    reactor.run_timers(start + 0.5)
    ace.lanes[0]["tag"] = dict(same)
    reactor.run_timers(start + 1.0)
    assert not adapter.tag_reader.read_waits
    reactor.run_timers(start + 20.0)
    assert not [line for line in adapter.gcode.lines if "is read when it moves" in line]


def test_an_empty_bay_seen_under_a_cleared_grip_is_forgotten() -> None:
    # the grip set with the bay empty, cleared when the head clears; a filament put in (no
    # gripped load), the head occupied again: the grip set again with the filament present — a
    # read with motion then is not the gripped load
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    ticks(adapter, reactor)
    assert h.grip_down
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor)
    assert ace.feed.grips == [GRIP, UNGRIP] and not h.grip_down
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    left.runout_sensor[1].detected = True
    ticks(adapter, reactor)
    assert ace.feed.grips == [GRIP, UNGRIP, GRIP] and not h.grip_down
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))  # ACEK_RFID_READ MOVE=1
    assert adapter.tag_reader.read_pending == []


def test_a_start_by_the_adapter_forgets_the_empty_bay_seen_under_the_grip() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ticks(adapter, reactor)
    assert h.grip_down
    adapter.started(h, fakes.FakeMove(0, "feed", 99))
    assert not h.grip_live and not h.grip_down


# --- the wheel while the head holds a piece that is not the lane's ------------------------------


def u1_tangled(delta_position: float, delta_count: int) -> bool:
    """The U1's filament_entangle_detect at its defaults (detection length 6 mm, factor 1, high
    sensitivity): tangled when the wheel moved fewer counts than one per 6 mm extruded."""
    unit = 6.0 * 1.0 * 1.0
    return delta_position >= unit and delta_count < int(delta_position / unit)


def extrude(
    adapter: Adapter,
    reactor: FakeReactor,
    positions: dict[int, float],
    head: int,
    mm: float,
    step: float = 2.0,
) -> None:
    """The head's extruder advances mm, a step per bay-watch tick."""
    done = 0.0
    while done < mm:
        positions[head] += step
        done += step
        ticks(adapter, reactor, 1)


def test_a_waiting_filament_behind_the_old_piece_does_not_stall_the_u1s_wheel() -> None:
    # the bench: a tail_out in a print, a new filament put in and gripped behind the old piece;
    # the extruder pulls the old piece on — the U1's tangle check (on: the bay reads a filament)
    # must see the wheel keep pace, not the waiting filament's still encoder
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("printing")
    positions = {0: 1000.0, 1: 0.0, 2: 0.0, 3: 0.0}
    adapter.extruder_position = lambda head: positions[head]
    ticks(adapter, reactor)
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    assert h.wheel_source == "extruder"
    counts, encoder = h.wheel.get_counts(), ace.lanes[0]["encoder_mm"]
    extrude(adapter, reactor, positions, 0, 50.0)
    assert ace.lanes[0]["encoder_mm"] == encoder  # the waiting filament never moved
    delta = h.wheel.get_counts() - counts
    assert delta >= int(50.0 / 6.0) and not u1_tangled(50.0, delta)
    # without the extruder as the source the same 50 mm read as a tangle
    assert u1_tangled(50.0, 0)


def test_the_wheel_goes_back_to_the_encoder_after_the_u1_loads_the_head_without_a_jump() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("printing")
    positions = {0: 1000.0, 1: 0.0, 2: 0.0, 3: 0.0}
    adapter.extruder_position = lambda head: positions[head]
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    extrude(adapter, reactor, positions, 0, 30.0)
    ace.lanes[0]["encoder_mm"] = 5000.0  # the encoder's reading, far from the extruder's
    ticks(adapter, reactor, 1)
    assert h.wheel_source == "extruder"
    before = h.wheel.get_counts()
    left.runout_sensor[1].detected = False  # the old piece has run out: the lane's own again
    ticks(adapter, reactor)
    assert h.wheel_source == "encoder"
    assert h.wheel.get_counts() == before  # re-based: no jump either way
    reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
    assert left._put_into_drive(1) is None  # the U1's replenish
    reactor.on_pause = []
    left._set_channel_state(1, "load_finish", True)
    ace.lanes[0].update(mode="following", encoder_mm=5030.0)  # the push, then the follow
    ticks(adapter, reactor)
    assert h.wheel_source == "encoder"
    assert h.wheel.get_counts() - before == int(30.0 * h.wheel.counts_per_mm + 1e-6)
    extrude(adapter, reactor, positions, 0, 20.0)  # the extruder alone moves nothing now
    assert h.wheel.get_counts() - before == int(30.0 * h.wheel.counts_per_mm + 1e-6)


def test_a_lane_owning_its_head_still_feeds_the_wheel_from_its_encoder() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    positions = {0: 1000.0, 1: 0.0, 2: 0.0, 3: 0.0}
    adapter.extruder_position = lambda head: positions[head]
    loaded(adapter, reactor, left, ace)
    ticks(adapter, reactor)
    counts = h.wheel.get_counts()
    extrude(adapter, reactor, positions, 0, 50.0)
    assert h.wheel_source == "encoder" and h.wheel.get_counts() == counts
    ace.lanes[0]["encoder_mm"] = 50.0
    ticks(adapter, reactor)
    assert h.wheel.get_counts() - counts == int(50.0 * h.wheel.counts_per_mm + 1e-6)


def test_a_failed_extruder_reading_skips_the_sample_and_loses_no_travel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("printing")
    positions = {0: 1000.0, 1: 0.0, 2: 0.0, 3: 0.0}
    broken = []

    def position(head: int) -> float:
        if broken:
            raise RuntimeError("print time unknown")
        return positions[head]

    adapter.extruder_position = position
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    assert h.wheel_source == "extruder"
    counts = h.wheel.get_counts()
    broken.append(1)
    with caplog.at_level("ERROR"):
        extrude(adapter, reactor, positions, 0, 20.0)  # ten ticks, every reading failing
    assert h.wheel_source == "extruder" and h.wheel.get_counts() == counts  # no re-base
    said = [r for r in caplog.records if "e0 position" in r.getMessage()]
    assert len(said) == 1
    broken.clear()
    ticks(adapter, reactor, 1)  # the next good reading: the 20 mm since the last one counted
    assert h.wheel.get_counts() - counts == int(20.0 * h.wheel.counts_per_mm + 1e-6)


def test_a_failed_extruder_reading_on_the_encoder_keeps_sampling_the_encoder() -> None:
    # the head counted foreign before the wheel ever switched (the reading failing from the
    # start): the wheel stays on the encoder and keeps counting it — never frozen
    adapter, reactor, left, ace, stats, h = setup("printing")

    def broken(head: int) -> NoReturn:
        raise RuntimeError("print time unknown")

    adapter.extruder_position = broken
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor)
    assert h.wheel_source == "encoder" and adapter.grip_watch._head_foreign(h)
    counts, seen = h.wheel.get_counts(), h.wheel.get_last_report_time()
    ace.lanes[0]["encoder_mm"] = 40.0  # the lane feeds (the U1's next load)
    ticks(adapter, reactor, 2)
    assert h.wheel.get_counts() - counts == int(40.0 * h.wheel.counts_per_mm + 1e-6)
    assert h.wheel.get_last_report_time() > seen


def test_a_failed_reading_is_logged_again_in_a_later_episode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    positions = {0: 1000.0, 1: 0.0, 2: 0.0, 3: 0.0}
    broken = []

    def position(head: int) -> float:
        if broken:
            raise RuntimeError("print time unknown")
        return positions[head]

    adapter.extruder_position = position
    ticks(adapter, reactor)
    broken.append(1)
    with caplog.at_level("ERROR"):
        ticks(adapter, reactor, 3)  # episode one: one line
        left.runout_sensor[1].detected = False  # the head clears: no longer foreign
        ticks(adapter, reactor, 3)
        left.runout_sensor[1].detected = True  # occupied again, the lane not owning it
        ticks(adapter, reactor, 3)  # episode two: one line again
    said = [r for r in caplog.records if "e0 position" in r.getMessage()]
    assert len(said) == 2


# --- a filament waiting behind a piece the U1 loads ---------------------------------


def leftover_in_head(
    state: str = "standby",
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeAce2k, FakePrintStats, Hooked]:
    """After a power cycle: lane 1's head holds a leftover piece, its bay empty; the grip set."""
    adapter, reactor, left, ace, stats, h = setup(state)
    left.runout_sensor[1].detected = True
    ace.lanes[0]["insert"] = False
    ticks(adapter, reactor)
    # the grip set (the channel does not hold the head); the lane not yet counted behind a
    # piece: nothing at the hook or by a tick says so — the held insert below does
    assert ace.feed.grips == [GRIP] and not h.waits_behind
    return adapter, reactor, left, ace, stats, h


def test_the_bench_leftover_the_u1s_print_load_uses_the_piece_and_the_lane_waits() -> None:
    adapter, reactor, left, ace, stats, h = leftover_in_head()
    positions = {0: 1000.0, 1: 0.0, 2: 0.0, 3: 0.0}
    adapter.extruder_position = lambda head: positions[head]
    # inserted: the gripped load ends blocked behind the old end, no preload
    ace.lanes[0].update(insert=True, mode="loading")
    ticks(adapter, reactor, 2)
    adapter.on_feed_event(0, event("blocked", seq=0, mode="loading"))
    ace.lanes[0]["mode"] = "idle"
    ticks(adapter, reactor)
    assert events(left) == [] and h.rise_held and h.waits_behind
    # the print's start: the U1's load finds the head set — its push returns at once, nothing
    # started, its extrusion and flush run on the old piece, no follow
    stats.state = "printing"
    starts = len(ace.feed.started)
    for state in ("load_prepare", "load_heating"):
        left._set_channel_state(1, state)
    assert left._put_into_drive(1) is None
    for state in ("load_extruding", "load_flushing", "load_finish"):
        left._set_channel_state(1, state)
        ticks(adapter, reactor, 2)
    left.motor.run_one_cycle(1, 0.5, 0.2)  # an extrude retry: not pushed into the old end
    assert len(ace.feed.started) == starts and follows(ace) == []
    assert not h.follow_wanted and h.waits_behind and h.rise_held and not h.present
    assert ace.feed.grips[-1] == GRIP and h.grip_wanted
    extrude(adapter, reactor, positions, 0, 30.0)
    assert h.wheel_source == "extruder"
    assert (
        adapter.pause_runner.pause_pending == []
    )  # no follow, so no follow error to pause the print on
    # the old piece runs out in the print: the U1's runout, its replenish pushes this filament
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor)
    assert not h.waits_behind and ace.feed.grips[-1] == UNGRIP
    reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
    assert left._put_into_drive(1) is None
    reactor.on_pause = []
    assert ace.feed.started[-1][:2] == (0, "feed")
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_finish", True)
    assert follows(ace) == [(0, "assist_both")] and h.follow_wanted
    ace.lanes[0]["mode"] = "following"
    ticks(adapter, reactor)
    assert h.wheel_source == "encoder" and h.present


def test_a_gripped_load_that_ends_blocked_still_asks_for_the_tag() -> None:
    adapter, reactor, left, ace, stats, h = leftover_in_head()
    ace.lanes[0].update(insert=True, mode="loading")
    ticks(adapter, reactor, 2)
    adapter.on_feed_event(0, event("blocked", seq=0, mode="loading"))
    assert adapter.tag_reader.read_pending == [h]
    reactor.run_timers()
    assert ace.rfid.reads == [] and adapter.tag_reader.read_waits


# --- the foreign flag: set at transitions only ----------------------------------------------------


def test_the_replenish_push_with_ticks_running_during_it_leaves_the_lane_owning() -> None:
    # the old piece out, the U1's replenish starts at once (no tick between): the push starts
    # with the head clear and clears the flag; the watch runs during the push and the filament
    # reaches the head's sensor — nothing sets the flag again, the follow is armed
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("printing")
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    assert h.waits_behind
    left.runout_sensor[1].detected = False
    pauses = []

    def during_push(now: float) -> None:
        pauses.append(now)
        if len(pauses) == 3:
            left.runout_sensor[1].detected = True  # the filament at the head's sensor
        adapter.watch(now)

    reactor.on_pause = [during_push]
    assert left._put_into_drive(1) is None
    reactor.on_pause = []
    assert len(pauses) >= 3 and not h.waits_behind and h.present
    left._set_channel_state(1, "load_extruding")
    assert follows(ace) == [(0, "assist_both"), (0, "assist_both")] and h.follow_wanted


def test_a_u1_load_right_after_the_tail_out_arms_no_follow() -> None:
    # the window before any tick: the tail_out itself set the flag (the head's sensor set)
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    adapter.on_feed_event(0, event("tail_out"))
    assert h.waits_behind
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_finish", True)
    assert follows(ace) == [(0, "assist_both")] and not h.follow_wanted


def test_after_a_restart_a_loaded_lane_owns_its_head_and_is_followed_at_print_start() -> None:
    # Klipper restarted on a loaded head: the U1's channel restored to load_finish, the filament
    # in the bay and at the head's sensor — nothing marks it as waiting behind a piece
    adapter, reactor, left, ace, stats, h = setup()
    left.channel_state[1] = "load_finish"
    left.runout_sensor[1].detected = True
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    assert not h.waits_behind and ace.feed.grips == []
    stats.state = "printing"
    assert left._put_into_drive(1) is None  # the print's start: the head already set
    left._set_channel_state(1, "load_extruding")
    assert follows(ace) == [(0, "assist_both")] and h.follow_wanted


def test_ace_clear_escapes_a_lane_whose_head_sensor_stays_set() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    assert h.waits_behind  # the sensor never clears (stuck)
    adapter.grip_watch.on_ace_clear(0)
    assert not h.waits_behind
    line = (
        "ace2k_u1: lane 1: ACEK_CLEAR — no longer counted as waiting behind e0's piece; its next"
        " load takes the head"
    )
    assert adapter.gcode.lines.count(line) == 1
    adapter.grip_watch.on_ace_clear(0)  # nothing to clear: no second line
    assert adapter.gcode.lines.count(line) == 1
    assert left._put_into_drive(1) is None
    left._set_channel_state(1, "load_extruding")
    assert follows(ace)[-1] == (0, "assist_both") and h.follow_wanted
    assert h.present and not h.rise_held  # the load ended the hold


def test_setting_the_flag_ends_the_follows_standing() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    assert h.follow_wanted
    adapter.grip_watch._set_waits_behind(h, "test")
    assert h.waits_behind and not h.follow_wanted


def test_the_flag_clears_once_the_head_sensor_has_read_clear_for_a_second() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    ticks(adapter, reactor)
    assert h.waits_behind
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor, 4)  # 0.8 s clear: a glitch so far
    assert h.waits_behind
    left.runout_sensor[1].detected = True  # set again: the debounce starts over
    ticks(adapter, reactor, 1)
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor, 4)
    assert h.waits_behind
    ticks(adapter, reactor, 2)  # past 1 s clear: the piece ran out
    assert not h.waits_behind
    left.runout_sensor[1].detected = True  # set again: no tick sets the flag
    ticks(adapter, reactor)
    assert not h.waits_behind


def test_the_status_line_shows_the_lane_waiting_behind_its_heads_piece() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    lines = []
    adapter.status_lines(lines.append)
    (lane1,) = [line for line in lines if line.startswith("lane 1 ")]
    (lane2,) = [line for line in lines if line.startswith("lane 2 ")]
    assert " behind=e0 " in lane1 and " behind=no " in lane2


def test_a_waiting_lane_whose_head_sensor_cannot_be_read_says_ace_clear_once() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("printing")
    sensor = left.runout_sensor[1]
    good = sensor.get_status

    def broken(eventtime: float) -> NoReturn:
        raise OSError("sensor gone")

    sensor.get_status = broken
    ticks(adapter, reactor, 10)
    line = (
        "ace2k_u1: lane 1: e0's sensor cannot be read — lane 1 waits behind its piece until the"
        " sensor reads clear, or ACEK_CLEAR LANE=1"
    )
    assert adapter.gcode.lines.count(line) == 1 and h.waits_behind  # never released by it
    sensor.get_status = good  # readable again (still set): a later failure is a new episode
    ticks(adapter, reactor, 2)
    sensor.get_status = broken
    ticks(adapter, reactor, 2)
    assert adapter.gcode.lines.count(line) == 2


def test_the_head_sensor_is_read_once_per_tick_per_lane() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("printing")
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    sensor = left.runout_sensor[1]
    reads = []
    status = sensor.get_status
    sensor.get_status = lambda eventtime: reads.append(eventtime) or status(eventtime)
    for detected in (True, False):  # the piece in the head, then gone (the rise path runs)
        sensor.detected = detected
        reads.clear()
        reactor.now += 0.2
        adapter.watch(reactor.now)
        assert len(reads) == 1, detected


def test_ace_clear_ends_the_holds_so_the_next_load_takes_the_head() -> None:
    # the head's sensor stuck set: ACEK_CLEAR releases the lane and its holds — no rise held
    # again on the next tick, the wheel and the grip follow the U1's next load
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    assert h.waits_behind and h.after_tail and h.rise_held
    adapter.grip_watch.on_ace_clear(0)
    assert not (h.waits_behind or h.after_tail or h.rise_held) and h.present
    ticks(adapter, reactor)
    assert not h.waits_behind and not h.rise_held  # not set again by the next ticks
    assert left._put_into_drive(1) is None
    left._set_channel_state(1, "load_extruding")
    left._set_channel_state(1, "load_finish", True)
    ace.lanes[0]["mode"] = "following"
    ticks(adapter, reactor)
    assert h.follow_wanted and ace.feed.grips[-1] == UNGRIP and h.wheel_source == "encoder"


def test_a_preload_from_a_clear_head_releases_a_waiting_lane_within_the_debounce() -> None:
    # standby: the held insert released on the head's first clear tick, the U1 preloads at once
    # and its filament reaches the head's sensor before a second of clear has passed
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    assert h.waits_behind and h.rise_held
    left.runout_sensor[1].detected = False
    ticks(adapter, reactor, 1)  # 0.2 s clear: the port event raised, the flag still set
    assert events(left)[-1] == ("port_event", True, 1) and h.waits_behind
    reactor.on_pause = [lambda now, s=left.runout_sensor[1]: setattr(s, "detected", True)]
    left._do_feed(1, "preload")
    reactor.on_pause = []
    assert left.channel_state[1] == "preload_finish" and not h.waits_behind
    ticks(adapter, reactor)
    assert not h.waits_behind
    assert left._put_into_drive(1) is None  # the U1's load: the head already set by it
    left._set_channel_state(1, "load_extruding")
    assert follows(ace)[-1] == (0, "assist_both") and h.follow_wanted


def test_ace_clear_outside_a_print_with_the_head_clear_tells_the_u1_of_the_filament_once() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    gripped_load(adapter, reactor, ace)
    ticks(adapter, reactor)
    told = len(events(left))
    # the sensor clear but not yet past the debounce (or stuck reading set until just now)
    left.runout_sensor[1].detected = False
    adapter.grip_watch.on_ace_clear(0)
    assert events(left)[told:] == [("port_event", True, 1)] and h.present
    ticks(adapter, reactor)
    assert events(left)[told:] == [("port_event", True, 1)]  # once


def test_ace_clear_with_the_head_still_set_tells_the_u1_nothing() -> None:
    for state in ("standby", "printing"):
        adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head(state)
        gripped_load(adapter, reactor, ace)
        ticks(adapter, reactor)
        told = len(events(left))
        adapter.grip_watch.on_ace_clear(0)  # the head's sensor still set (stuck)
        ticks(adapter, reactor)
        assert events(left)[told:] == [] and h.present, state


# --- one status read per tick -------------------------------------------------------------------


def _count_status(ace: FakeAce2k) -> list[float]:
    calls = []
    real = ace.get_status

    def get_status(eventtime: float) -> Status:
        calls.append(eventtime)
        return real(eventtime)

    ace.get_status = get_status
    return calls


def test_the_bay_watch_reads_the_units_status_once_per_tick() -> None:
    # loaded and following; in the tail; the tail's end at the head's sensor (stopped); after a
    # tail_out on an occupied head (the grip set, the wheel on the extruder); the filament out
    adapter, reactor, left, ace, stats, h = setup()
    calls = _count_status(ace)
    loaded(adapter, reactor, left, ace)
    del calls[:]
    adapter.watch(reactor.now)
    assert len(calls) == 1
    cut(adapter, reactor, ace)
    del calls[:]
    adapter.watch(reactor.now)
    assert len(calls) == 1
    left.runout_sensor[1].detected = False
    del calls[:]
    adapter.watch(reactor.now)
    assert ace.feed.stopped == [0] and len(calls) == 1
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    calls = _count_status(ace)
    adapter.watch(reactor.now)
    assert h.grip_live and len(calls) == 1
    ace.lanes[0]["insert"] = False
    del calls[:]
    adapter.watch(reactor.now)
    assert len(calls) == 1


def test_the_loaded_states_are_built_once_at_the_hook() -> None:
    adapter, reactor, left, ace, stats, h = setup()
    assert adapter.loaded_states is not None
    assert adapter.get_loaded_states() is adapter.loaded_states
    assert adapter.get_loaded_states() is adapter.get_loaded_states()


def test_an_idle_lane_with_no_filament_is_not_armed_again_whatever_its_tail_flag() -> None:
    # mode idle means no tail (the unit ends it with the follow): only a filament in the bay
    # brings the standing re-arm
    adapter, reactor, left, ace, stats, h = setup()
    loaded(adapter, reactor, left, ace)
    ended(adapter, reactor, ace)
    ace.lanes[0].update(insert=False, tail=True)  # a report no unit sends
    for _ in range(5):
        reactor.now += SETTLE_S
        adapter.watch(reactor.now)
    assert follows(ace) == [(0, "assist_both")]


# --- the bay watch never raises (characterisation) -----------------------------------------------


def boom(*args: object, **kwargs: object) -> None:
    raise RuntimeError("boom")


def logged(caplog: pytest.LogCaptureFixture, text: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == text and r.exc_info]


def test_a_failing_on_tick_is_logged_and_the_watch_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace, stats, h = setup()
    adapter.on_tick = boom
    with caplog.at_level("ERROR"):
        assert adapter.watch(reactor.now) == reactor.now + adapter.poll_s
    assert len(logged(caplog, "ace2k_u1: watch tick")) == 1


def test_a_failing_grip_sync_is_logged_and_the_follow_still_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace, stats, h = setup()
    adapter.grip_watch.watch_head_clear = boom
    seen = []
    adapter.follow.watch_follow = lambda hh, lane: seen.append(hh.lane)
    with caplog.at_level("ERROR"):
        adapter.watch(reactor.now)
    assert len(logged(caplog, "ace2k_u1: lane 1 grip")) == 1
    assert 0 in seen


def test_a_failing_follow_watch_is_logged_and_the_next_lane_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace, stats, h = setup()
    seen = []

    def failing(hh: Hooked, lane: LaneView) -> NoReturn:
        seen.append(hh.lane)
        raise RuntimeError("boom")

    adapter.follow.watch_follow = failing
    with caplog.at_level("ERROR"):
        assert adapter.watch(reactor.now) == reactor.now + adapter.poll_s
    assert len(logged(caplog, "ace2k_u1: lane 1 follow")) == 1
    assert seen == [x.lane for x in adapter.hooked]  # every lane reached


def test_a_failing_tag_watch_is_logged_and_the_watch_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace, stats, h = setup()
    adapter.tag_writer.tags = object()
    adapter.tag_writer.watch_tags = boom
    with caplog.at_level("ERROR"):
        assert adapter.watch(reactor.now) == reactor.now + adapter.poll_s
    assert len(logged(caplog, "ace2k_u1: tags")) == 1
