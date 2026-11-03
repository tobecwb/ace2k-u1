"""The wait: a lone head's new filament that the unit does not pull while the head holds a piece.

On the bench (2026-10-06) the grip pulled a filament inserted behind a leftover into the
leftover's end at the gear, and the U1's load after the leftover ran out failed. The unit now
waits instead: nothing moves until the U1's load, which seats the filament with the unit's load
from the bay. A unit whose image has no wait keeps the grip.
"""

from __future__ import annotations

import pytest
from ace2k_u1_common import SEATED_MM
from fakes import NO_TAG, FakeFeedApi
from test_standing import (
    END_OUT_AGAIN,
    GRIP,
    NOT_READ,
    UNGRIP,
    cut,
    event,
    follows,
    loaded,
    setup,
    tail_out_on_occupied_head,
    ticks,
)
from test_states import pausing

WAIT = (0, "wait")


@pytest.fixture(autouse=True)
def has_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeFeedApi, "has_wait", True)


def test_a_tail_out_on_an_occupied_lone_head_sets_the_wait_at_once() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ticks(adapter, reactor, 5)
    adapter.on_feed_event(0, event("tail_out"))
    assert ace.feed.grips == [WAIT]  # before any tick: an insert edge may follow
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor)
    assert ace.feed.grips == [WAIT] and h.grip_wait and h.grip_live
    assert adapter.grip_watch.grip_text(h) == "the wait"


def test_without_the_wait_in_the_image_the_lone_head_is_gripped_and_told_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FakeFeedApi, "has_wait", False)
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ticks(adapter, reactor, 3)
    assert ace.feed.grips == [GRIP] and not h.grip_wait
    line = (
        "ace2k_u1: the unit's firmware has no wait; flash v0.13.0 or later — a lone head's new"
        " filament is gripped"
    )
    assert adapter.gcode.lines.count(line) == 1


def test_the_wait_is_cleared_when_the_heads_sensor_clears() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    ticks(adapter, reactor)
    left.runout_sensor[1].detected = False  # the old piece taken out by hand
    ticks(adapter, reactor)
    assert ace.feed.grips == [WAIT, UNGRIP] and not h.grip_wanted and not h.grip_wait


def test_the_filament_taken_out_and_put_in_again_keeps_the_wait() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    for insert in (True, False, True):
        ace.lanes[0]["insert"] = insert
        ticks(adapter, reactor)
    assert ace.feed.grips == [WAIT] and h.grip_live  # never used up, never sent again


def test_a_start_on_the_lane_ends_the_wait_and_the_next_tick_sends_it_again() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ticks(adapter, reactor)
    adapter.started(h, ace.feed.start_move(0, "rollback", 5.0, 20.0))
    assert not h.grip_live
    ace.lanes[0]["mode"] = "idle"
    ticks(adapter, reactor)
    assert ace.feed.grips == [WAIT, WAIT] and h.grip_live


def test_a_load_of_seq_0_under_the_wait_is_not_a_gripped_load() -> None:
    # a hand read with motion after an insert under the wait: the bay was empty, then filled, but
    # the wait starts no load, so this load is not the grip's and leaves no filament at the grip
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ace.lanes[0]["insert"] = False
    ticks(adapter, reactor)
    ace.lanes[0].update(insert=True, mode="loading")
    ticks(adapter, reactor, 2)
    assert not h.grip_down
    adapter.on_feed_event(0, event("loaded", seq=0, mode="loading"))
    reactor.run_timers()
    assert not h.at_grip and ace.rfid.reads == []


# Under the wait no load runs, so nothing can read the tag: the line comes at once.
NEW_SPOOL = (
    "ace2k_u1: lane 1: the new spool's tag is read when it moves — e0 keeps its filament setting"
    " until then"
)


def test_a_filament_put_in_on_a_waiting_lane_asks_no_read_and_says_so_at_once() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ticks(adapter, reactor)
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)  # the rise settles
    assert adapter.gcode.lines.count(NEW_SPOOL) == 1
    reactor.run_timers(reactor.now + 20.0)
    assert ace.rfid.reads == [] and not adapter.tag_reader.read_waits
    assert adapter.gcode.lines.count(NEW_SPOOL) == 1 and NOT_READ not in adapter.gcode.lines
    assert [s for s in ace.feed.started if s[1] == "load"] == []


def test_a_waiting_lane_whose_tag_is_already_read_says_nothing() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ticks(adapter, reactor)
    restored = dict(state="read", uid="04AB", source="restored", record={"uid": "04AB"})
    ace.lanes[0].update(insert=True, tag=restored)
    ticks(adapter, reactor)
    assert NEW_SPOOL not in adapter.gcode.lines


def test_a_filament_put_in_under_the_grip_is_not_read_at_its_rise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FakeFeedApi, "has_wait", False)
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ticks(adapter, reactor)
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    reactor.run_timers()
    assert ace.rfid.reads == [] and not adapter.tag_reader.read_waits  # watched at the load's end


WAIT_LINE = (
    "ace2k_u1: lane 1: the end is out of the unit — lane 1's new filament waits in the bay"
    " until e0's piece runs out; push it in until it stops"
)


def test_the_tail_out_line_says_to_push_the_filament_in_until_it_stops() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True  # put in during the tail
    adapter.on_feed_event(0, event("tail_out"))
    assert WAIT_LINE in adapter.gcode.lines


def test_the_tail_out_line_without_the_wait_is_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FakeFeedApi, "has_wait", False)
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True
    adapter.on_feed_event(0, event("tail_out"))
    assert WAIT_LINE not in adapter.gcode.lines
    assert END_OUT_AGAIN in adapter.gcode.lines


def test_with_the_unit_as_runout_source_a_tail_out_sets_the_wait_and_never_pauses() -> None:
    adapter, reactor, left, ace, stats, u1 = pausing("printing", "unit")
    left.runout_sensor[1].detected = True  # the old piece still in e0
    ace.lanes[0]["insert"] = True  # put in during the tail
    adapter.on_feed_event(0, event("tail_out", 999, mode="following"))
    reactor.run_timers()
    assert ace.feed.grips == [WAIT]
    # the filament was already in when the wait was set: its tag line comes first
    assert u1.lines == [NEW_SPOOL, WAIT_LINE] and u1.log == []


def test_a_filament_put_in_during_the_tail_is_told_once_the_wait_is_set() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True  # put in during the tail
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    ticks(adapter, reactor, 1)
    reactor.run_timers()
    assert ace.feed.grips == [WAIT] and ace.rfid.reads == []
    assert adapter.gcode.lines.count(NEW_SPOOL) == 1
    ticks(adapter, reactor)
    reactor.run_timers(reactor.now + 20.0)
    assert adapter.gcode.lines.count(NEW_SPOOL) == 1  # once for this filament


def test_an_unknown_insert_reading_keeps_the_seat() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    assert h.seat_due
    ace.lanes[0]["insert"] = None  # no report of the sensors
    ticks(adapter, reactor, 1)
    assert h.seat_due
    ace.lanes[0]["insert"] = False  # a real removal
    ticks(adapter, reactor, 1)
    assert not h.seat_due


def test_a_waiting_filament_out_of_the_auto_mode_is_not_followed_when_the_head_clears() -> None:
    # the lane still waits behind the old piece when the held rise is let go, so the follow is not
    # armed: the U1's load seats the filament, and its extrusion arms the follow
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head("standby")
    left.config["auto_mode"][1] = False
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    assert h.seat_due and h.rise_held
    before = len(follows(ace))  # the old piece's follow
    left.runout_sensor[1].detected = False  # the old piece taken out
    ticks(adapter, reactor)
    assert len(follows(ace)) == before and h.seat_due and h.present


def test_a_filament_put_in_during_the_tail_out_of_the_auto_mode_is_not_followed() -> None:
    adapter, reactor, left, ace, stats, h = setup("standby")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    left.config["auto_mode"][1] = False
    ace.lanes[0]["insert"] = True  # put in during the tail
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    before = len(follows(ace))
    left.runout_sensor[1].detected = False  # the old piece taken out
    ticks(adapter, reactor)
    assert len(follows(ace)) == before and h.seat_due


def test_a_refused_wait_grips_instead_and_is_not_asked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)

    def refuse(lane: int) -> None:
        raise RuntimeError("ace2k: lane 1 wait refused")

    monkeypatch.setattr(ace.feed, "set_lane_wait", refuse)
    adapter.on_feed_event(0, event("tail_out"))
    assert ace.feed.grips == [GRIP] and h.grip_live and not h.grip_wait and h.wait_refused
    ace.lanes[0].update(mode="idle", tail=False)
    adapter.started(h, ace.feed.start_move(0, "rollback", 5.0, 20.0))  # the grip used up
    ace.lanes[0]["mode"] = "idle"
    ticks(adapter, reactor)
    assert ace.feed.grips == [GRIP, GRIP]  # the grip again, the wait not asked again
    assert not [line for line in adapter.gcode.lines if "grip not set" in line]


def moved(seq: int, mode: str, filament_mm: float, kind: str = "done") -> dict[str, object]:
    return dict(event(kind, seq=seq, mode=mode), filament_mm=filament_mm)


def test_a_final_event_that_moved_nothing_keeps_the_seat_and_a_resent_wait_says_nothing() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    reactor.run_timers()
    assert h.seat_due and ace.rfid.reads == [] and not adapter.tag_reader.read_waits
    assert adapter.gcode.lines.count(NEW_SPOOL) == 1
    # an unrelated end (a stop, a follow's end) that left the filament at the bay
    adapter.on_feed_event(0, moved(7, "following", 0.0, kind="stopped"))
    assert h.seat_due and not h.grip_live
    ticks(adapter, reactor)
    reactor.run_timers()
    assert ace.feed.grips == [WAIT, WAIT]  # the wait sent again
    reactor.run_timers(reactor.now + 20.0)
    assert h.seat_due and ace.rfid.reads == []
    assert adapter.gcode.lines.count(NEW_SPOOL) == 1  # no second line for the same filament


def test_a_final_event_that_took_the_filament_forward_seats_it() -> None:
    adapter, reactor, left, ace, stats, h = tail_out_on_occupied_head()
    ace.lanes[0]["insert"] = True
    ticks(adapter, reactor)
    adapter.on_feed_event(0, moved(7, "feeding", SEATED_MM - 1.0))  # short of the gear
    assert h.seat_due
    adapter.on_feed_event(0, moved(8, "feeding", SEATED_MM))  # a feed by hand into the gear
    assert h.waited and h.seated and not h.seat_due
    ace.lanes[0]["insert"] = False  # taken out: the next filament is a new one
    ticks(adapter, reactor, 1)
    assert not h.waited and not h.seated


def test_a_filament_put_in_during_the_tail_is_not_seated_by_the_tails_own_end() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    cut(adapter, reactor, ace)
    ace.lanes[0]["insert"] = True  # put in during the tail
    adapter.on_feed_event(0, moved(999, "following", 400.0, kind="tail_out"))  # the old end's
    assert h.waited and not h.seated


@pytest.mark.parametrize("grip", ["none", "wait", "grip"])
def test_the_tag_before_is_taken_as_the_bay_empties_not_on_every_tick(
    monkeypatch: pytest.MonkeyPatch, grip: str
) -> None:
    monkeypatch.setattr(FakeFeedApi, "has_wait", grip == "wait")
    adapter, reactor, left, ace, stats, h = setup("standby" if grip == "none" else "printing")
    old = dict(state="read", uid="04AB", source="tag", record={"uid": "04AB"})
    ace.lanes[0]["tag"] = old
    loaded(adapter, reactor, left, ace)
    if grip == "none":
        ace.lanes[0]["insert"] = False  # taken out, no grip on the lane
        ticks(adapter, reactor, 1)
    else:
        cut(adapter, reactor, ace)  # the bay empties in the tail
        adapter.on_feed_event(0, event("tail_out"))  # the old piece still in the head
        ace.lanes[0].update(mode="idle", tail=False)
        ticks(adapter, reactor, 2)
    assert h.grip_live == (grip != "none") and h.grip_wait == (grip == "wait")
    assert h.tag_before == ("read", "04AB")
    ace.lanes[0]["tag"] = dict(NO_TAG)  # the unit's record changes while the bay is empty
    ticks(adapter, reactor)
    assert h.tag_before == ("read", "04AB")  # the record as the bay emptied


def test_a_filament_put_in_during_the_tail_is_not_taken_for_the_old_spool() -> None:
    adapter, reactor, left, ace, stats, h = setup("printing")
    loaded(adapter, reactor, left, ace)
    old = dict(state="read", uid="04AB", source="tag", record={"uid": "04AB"})
    ace.lanes[0]["tag"] = old  # the old spool's record, kept by the unit
    ticks(adapter, reactor, 2)
    cut(adapter, reactor, ace)
    ticks(adapter, reactor, 2)  # the bay read empty in the tail
    ace.lanes[0]["insert"] = True  # put in during the tail
    adapter.on_feed_event(0, event("tail_out"))
    ace.lanes[0].update(mode="idle", tail=False)
    reactor.run_timers()
    assert ace.rfid.reads == [] and not adapter.tag_reader.read_waits
    assert NEW_SPOOL in adapter.gcode.lines  # the old spool's record is not this spool's
