"""The read with motion of a spool whose tag the unit's own load left unread at the parking point:
once per spool, never during a print, skipped when the bay empties or a move is pending, one
console line at its start and one with its outcome, one retry when another lane moves."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ace2k_u1_tag_search as search_module
import fakes
from test_standing import event, setup, ticks

if TYPE_CHECKING:
    from ace2k_u1_adapter import Adapter
    from fakes import FakeAce2k, FakeReactor

READ = "ACEK_RFID_READ LANE=1 MOVE=1 WAIT=0"
START = "ace2k_u1: lane 1: the tag was not read at the parking point — reading it with motion"
PENDING = dict(fakes.NO_TAG, state="pending")
TAG = dict(
    state="read",
    uid="04AB",
    source="tag",
    record={"uid": "04AB", "brand": "Bambu", "material": "PLA", "color_rgba": "000000FF"},
)


class Gcode:
    """The console and run_script; fail: refusals raised by the next run_script calls."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.scripts: list[str] = []
        self.fail: list[Exception] = []

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)

    def run_script(self, script: str) -> None:
        self.scripts.append(script)
        if self.fail:
            raise self.fail.pop(0)


def rig(state: str = "standby") -> tuple[Adapter, FakeReactor, FakeAce2k, Gcode]:
    adapter, reactor, _left, ace, _stats, _h = setup(state)
    gcode = Gcode()
    adapter.gcode = gcode
    return adapter, reactor, ace, gcode


def own_load(
    adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k, tag: dict[str, object]
) -> None:
    """A spool put into lane 1: the unit's own load (seq 0) ends loaded at the parking point."""
    ace.lanes[0].update(insert=True, mode="loading")
    ticks(adapter, reactor, 2)
    ace.lanes[0]["tag"] = dict(tag)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    ace.lanes[0]["mode"] = "idle"


def settle(reactor: FakeReactor, s: float = search_module.SEARCH_SETTLE_S + 0.1) -> None:
    reactor.run_timers(reactor.now + s)


def test_a_load_ended_at_the_park_with_its_tag_pending_reads_it_once_with_motion() -> None:
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    reactor.run_timers(reactor.now + 1.0)
    assert gcode.scripts == []  # the tag state may still settle
    settle(reactor)
    assert gcode.scripts == [READ]
    assert gcode.lines.count(START) == 1
    # the read's own load: it searches, finds the tag and comes back
    ace.lanes[0].update(mode="loading", tag=dict(PENDING, state="searching"))
    settle(reactor, 1.0)
    ace.lanes[0]["tag"] = dict(TAG)
    settle(reactor, 1.0)
    assert "ace2k_u1: lane 1 tag read with motion: found — Bambu PLA 000000FF" in gcode.lines
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))  # its end: nothing new
    ace.lanes[0]["mode"] = "idle"
    settle(reactor, 10.0)
    assert gcode.scripts == [READ]
    assert len([x for x in gcode.lines if "tag read with motion" in x]) == 1


def test_a_read_that_finds_no_tag_says_so_at_its_end() -> None:
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor)
    ace.lanes[0]["tag"] = dict(PENDING, state="searching")
    settle(reactor, 5.0)
    assert not [x for x in gcode.lines if "tag read with motion:" in x]  # still out
    ace.lanes[0]["tag"] = dict(PENDING, state="no_tag")
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    settle(reactor)
    assert gcode.lines.count("ace2k_u1: lane 1 tag read with motion: no tag") == 1
    assert gcode.scripts == [READ]


def test_a_tag_read_during_the_load_starts_no_read() -> None:
    for tag in (TAG, dict(PENDING, state="no_tag")):
        adapter, reactor, ace, gcode = rig()
        own_load(adapter, reactor, ace, tag)
        settle(reactor, 30.0)
        assert gcode.scripts == [] and START not in gcode.lines


def test_no_read_during_a_print_it_looks_again_then_gives_up_with_a_line() -> None:
    for state in ("printing", "paused"):
        adapter, reactor, ace, gcode = rig(state)
        own_load(adapter, reactor, ace, PENDING)
        settle(reactor, 30.0)
        assert gcode.scripts == [] and not gcode.lines, state  # still looking
        settle(reactor, search_module.SEARCH_RECHECK_MAX_S)
        assert gcode.scripts == [], state
        assert gcode.lines == ["ace2k_u1: lane 1: tag read with motion given up — a print is on"]
        assert not adapter.tag_search.searches


def test_a_print_that_ends_within_the_recheck_lets_the_read_start() -> None:
    adapter, reactor, ace, gcode = rig("printing")
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor, 20.0)
    assert gcode.scripts == []
    adapter.print_stats.state = "standby"  # type: ignore[union-attr]
    settle(reactor, search_module.SEARCH_RECHECK_S + 0.1)
    assert gcode.scripts == [READ]


def test_once_per_spool_and_again_after_the_bay_was_seen_empty() -> None:
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor)
    ace.lanes[0]["tag"] = dict(PENDING)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))  # the read's end
    settle(reactor)
    assert gcode.lines[-1] == "ace2k_u1: lane 1 tag read with motion: tag not read"
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))  # another load, same spool
    settle(reactor, 10.0)
    assert gcode.scripts == [READ]
    ace.lanes[0]["insert"] = False  # the spool taken out
    ticks(adapter, reactor, 2)
    own_load(adapter, reactor, ace, PENDING)  # a spool put in again
    settle(reactor)
    assert gcode.scripts == [READ, READ]


def test_a_gripped_load_starts_no_read_with_motion() -> None:
    adapter, reactor, ace, gcode = rig()
    h = next(x for x in adapter.hooked if x.lane == 0)
    ace.lanes[0].update(insert=True, mode="loading")
    h.grip_live = h.grip_down = True  # the grip was set and the load took it
    ace.lanes[0]["tag"] = dict(PENDING)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    settle(reactor, 10.0)
    assert READ not in gcode.scripts


def test_skipped_when_the_bay_emptied_and_waits_while_a_move_is_pending() -> None:
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    ace.lanes[0]["insert"] = False
    settle(reactor, 90.0)
    assert gcode.scripts == [] and not gcode.lines  # moot: no read, no line
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    adapter.swapping.add(0)  # a swap of its head runs its lanes
    settle(reactor, 10.0)
    assert gcode.scripts == []
    adapter.swapping.discard(0)
    settle(reactor, search_module.SEARCH_RECHECK_S + 0.1)
    assert gcode.scripts == [READ]


def test_the_lanes_own_follow_is_no_move_for_the_read() -> None:
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    h = next(x for x in adapter.hooked if x.lane == 0)
    h.armed = True  # its follow armed again after the load: at rest
    ace.lanes[0]["mode"] = "following"
    settle(reactor)
    assert gcode.scripts == [READ]


def test_a_session_in_flight_is_waited_for_and_a_passive_watch_is_not() -> None:
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    h = next(x for x in adapter.hooked if x.lane == 0)
    adapter.tag_reader.read_pending.append(h)  # a gripped spool's watch: it reads nothing
    adapter.tag_search.searches[h.key].due = reactor.now
    adapter.tag_search._run(reactor.now)
    assert gcode.scripts == [READ]  # the watch does not hold back the read with motion
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    ace.lanes[0]["tag"] = dict(PENDING, state="reading")
    settle(reactor, 5.0)
    assert gcode.scripts == []  # the read on command still runs
    ace.lanes[0]["tag"] = dict(PENDING)
    settle(reactor, search_module.SEARCH_RECHECK_S + 0.1)
    assert gcode.scripts == [READ]


def test_a_park_of_a_pending_refill_gets_no_read_with_motion() -> None:
    adapter, reactor, ace, gcode = rig()
    h = next(x for x in adapter.hooked if x.lane == 0)
    ace.lanes[0].update(insert=True, mode="loading", tag=dict(PENDING))
    h.grip_live = h.grip_down = h.grip_park = True  # the park took the load
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    settle(reactor, 10.0)
    assert READ not in gcode.scripts


def test_the_load_of_a_read_with_motion_leaves_at_grip_as_the_park_left_it() -> None:
    adapter, reactor, ace, gcode = rig()
    h = next(x for x in adapter.hooked if x.lane == 0)
    adapter.shared = lambda x: True  # type: ignore[method-assign]
    search_end = dict(event("loaded", seq=0, mode="loading"), motor_mm=600.0, tag_search=True)
    for at_grip in (True, False):
        h.at_grip = at_grip
        adapter.on_feed_event(0, search_end)  # type: ignore[arg-type]
        assert h.at_grip is at_grip
    # a read this adapter started counts, also from an ace2k whose event does not say it
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor)
    assert gcode.scripts == [READ]
    h.at_grip = True
    adapter.on_feed_event(0, dict(event("loaded", seq=0, mode="loading"), motor_mm=600.0))
    assert h.at_grip is True
    # the unit's own load of a shared lane still judges it: a short one counts as short
    h.at_grip = False
    adapter.on_feed_event(0, dict(event("loaded", seq=0, mode="loading"), motor_mm=5.0))
    assert h.at_grip is adapter.grip_watch.park_short(event("loaded")) is True


def test_a_hand_read_with_motion_ending_starts_no_second_read() -> None:
    adapter, reactor, ace, gcode = rig()
    ace.lanes[0].update(insert=True, tag=dict(PENDING))
    ticks(adapter, reactor, 2)
    adapter.on_feed_event(0, dict(event("loaded", seq=0, mode="loading"), tag_search=True))
    settle(reactor, 10.0)
    assert gcode.scripts == []


def test_a_forgotten_or_new_tag_and_an_insert_edge_arm_the_read_again() -> None:
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor)
    ace.lanes[0]["tag"] = dict(PENDING)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))  # the read's end
    settle(reactor)
    ticks(adapter, reactor, 1)
    ace.lanes[0]["tag"] = dict(fakes.NO_TAG, state="no_tag")
    ticks(adapter, reactor, 1)
    ace.lanes[0]["tag"] = dict(PENDING)  # forgotten (Read tag)
    ticks(adapter, reactor, 1)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    settle(reactor)
    assert gcode.scripts == [READ, READ]
    # an insert edge seen without an empty bay polled (None, then in)
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    settle(reactor)
    ace.lanes[0]["insert"] = None
    ticks(adapter, reactor, 1)
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor, 1)
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    settle(reactor)
    assert gcode.scripts == [READ, READ]


def test_the_wait_is_ace2ks_own_when_it_tells_it() -> None:
    adapter, reactor, ace, gcode = rig()
    ace.rfid._search_mm = lambda: 500.0  # type: ignore[attr-defined]
    ace.rfid._move_timeout = lambda mm: mm / 10.0 + 5.0  # type: ignore[attr-defined]
    h = next(x for x in adapter.hooked if x.lane == 0)
    assert search_module.search_wait_s(h) == 55.0
    del ace.rfid._move_timeout  # type: ignore[attr-defined]
    assert round(search_module.search_wait_s(h), 1) == round((2 * 750 + 60) / 9 + 15, 1)


def test_a_refusal_for_another_lane_is_tried_once_more_then_given_up() -> None:
    refused = RuntimeError("ace2k: lane 1 tag read refused: other_lane_moving")
    adapter, reactor, ace, gcode = rig()
    gcode.fail = [refused, refused]
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor)
    assert gcode.scripts == [READ]
    refused_at = reactor.now
    reactor.run_timers(refused_at + search_module.SEARCH_RETRY_S - 0.5)
    assert gcode.scripts == [READ]
    reactor.run_timers(refused_at + search_module.SEARCH_RETRY_S + 0.5)
    assert gcode.scripts == [READ, READ]
    assert gcode.lines.count(START) == 1
    assert gcode.lines[-1] == (
        "ace2k_u1: lane 1: tag read with motion given up — ace2k: lane 1 tag read refused:"
        " other_lane_moving"
    )
    settle(reactor, 30.0)
    assert gcode.scripts == [READ, READ]
    # a retry that is accepted goes on as any read
    adapter, reactor, ace, gcode = rig()
    gcode.fail = [refused]
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor, search_module.SEARCH_SETTLE_S + search_module.SEARCH_RETRY_S + 0.5)
    assert gcode.scripts == [READ, READ] and gcode.lines.count(START) == 1
    assert search_module.TagSearch is type(adapter.tag_search)
    assert adapter.tag_search.searches  # started, waiting for its outcome


def test_a_busy_refusal_is_tried_once_more_and_any_other_given_up_at_once() -> None:
    adapter, reactor, ace, gcode = rig()
    gcode.fail = [RuntimeError("ace2k: lane 1 tag read refused: busy")]
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor, search_module.SEARCH_SETTLE_S + search_module.SEARCH_RETRY_S + 0.5)
    assert gcode.scripts == [READ, READ] and adapter.tag_search.searches
    adapter, reactor, ace, gcode = rig()
    gcode.fail = [RuntimeError("ace2k: lane 1 tag read refused: no_link")]
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor, 30.0)
    assert gcode.scripts == [READ]
    assert gcode.lines[-1].endswith("given up — ace2k: lane 1 tag read refused: no_link")


def test_a_read_with_no_end_has_no_outcome_at_the_deadline() -> None:
    adapter, reactor, ace, gcode = rig()
    own_load(adapter, reactor, ace, PENDING)
    settle(reactor)
    ace.lanes[0]["tag"] = dict(PENDING, state="searching")
    reactor.run_timers(reactor.now + 189.0)
    assert gcode.lines[-1] == "ace2k_u1: lane 1 tag read with motion: no outcome within 188 s"
    assert not adapter.tag_search.searches
