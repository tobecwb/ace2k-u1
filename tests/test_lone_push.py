"""The load push into a head that one lane feeds: a push that ends blocked is pushed again.

On the bench (2026-10-06) a strand gripped behind a leftover met the leftover's end, and the U1's
replenish after the leftover ran out ended blocked with the strand still: the load failed. The
operator's resume ran the same push again, and it took. A shared head already pushes again (the
probe's insisting); a load into a lone head now does the same, with the same rules. Its preload
still waits where it stopped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import ace2k_u1_probe
import fakes
import pytest
from test_probe import moves, reports_tick, rollbacks
from test_tools import tools_rig

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked
    from fakes import FakeAce2k, FakeMove

LONE_CH = 0  # a:3 feeds head 2 alone: channel 0 of the left feed
LONE_LANE = 2


def lone_rig(
    tmp_path: Path, blocks: Sequence[float], lanes: str | None = None
) -> tuple[SimpleNamespace, FakeAce2k, list[FakeMove]]:
    """Head 2 = a:3 alone, its sensor empty. a:3's pushes end blocked after each length of blocks
    in turn, and the one after sets the head's sensor."""
    rig, by_name = tools_rig(tmp_path) if lanes is None else tools_rig(tmp_path, lanes=lanes)
    a = by_name["a"]
    a.link_proven = True
    sensor = rig.left.runout_sensor[LONE_CH]
    sensor.detected = False
    a.lanes[LONE_LANE]["insert"] = True
    pushes: list[FakeMove] = []

    def on_start(move: FakeMove) -> None:
        if move.lane == LONE_LANE and move.mode == "feed":
            pushes.append(move)
            if len(pushes) <= len(blocks):
                a.lanes[LONE_LANE]["rest"] = True
                move.event = {
                    "kind": "blocked",
                    "seq": move.seq,
                    "filament_mm": blocks[len(pushes) - 1],
                }
            else:
                sensor.detected = True
        elif move.mode == "rollback":
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    reports_tick(rig, a)
    return rig, a, pushes


def test_a_lone_heads_load_blocked_with_the_strand_still_is_pushed_again(tmp_path: Path) -> None:
    rig, a, pushes = lone_rig(tmp_path, [0.0])
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    assert rig.left._put_into_drive(LONE_CH) is None
    assert len(pushes) == 2
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "feed"), (LONE_LANE, "feed")]
    assert rollbacks(a) == []  # its plunger at rest: nothing eased, nothing rolled back
    assert (
        "ace2k_u1: head 2: the push of a:3 stopped blocked after 0 mm: eased 0 mm to its"
        " plunger's rest, pushed again (1/3)"
    ) in rig.gcode.lines
    assert rig.left.channel_error[LONE_CH] == "ok"


def test_a_lone_heads_load_that_gains_nothing_again_is_the_u1s_failure_35(tmp_path: Path) -> None:
    rig, a, pushes = lone_rig(tmp_path, [0.0, 0.0, 0.0])
    with pytest.raises(RuntimeError, match="lane a:3 push to e1: distance"):
        rig.left._put_into_drive(LONE_CH)
    assert len(pushes) == 2  # the second gained nothing over the first: a hard stop
    assert rollbacks(a) == []  # a lone head's tube is its own: nothing to clear for another lane
    assert rig.left.channel_error[LONE_CH] == "distance"
    assert rig.left.exception_code[LONE_CH] == 35


def test_a_lone_heads_load_pushes_again_at_most_three_times(tmp_path: Path) -> None:
    rig, a, pushes = lone_rig(tmp_path, [300.0, 40.0, 30.0, 20.0])
    with pytest.raises(RuntimeError, match="lane a:3 push to e1: distance"):
        rig.left._put_into_drive(LONE_CH)
    assert len(pushes) == 4  # the push and three tries
    assert rollbacks(a) == []


def test_a_lone_heads_push_again_that_fails_otherwise_is_stopped_and_not_rolled_back(
    tmp_path: Path,
) -> None:
    rig, a, pushes = lone_rig(tmp_path, [0.0])
    on_start = a.feed.on_start

    def stuck_again(move: FakeMove) -> None:
        if move.mode == "feed" and pushes:
            pushes.append(move)
            move.event = {"kind": "stuck", "seq": move.seq, "filament_mm": 3.0}
            return
        on_start(move)

    a.feed.on_start = stuck_again
    with pytest.raises(RuntimeError, match="lane a:3 push to e1"):
        rig.left._put_into_drive(LONE_CH)
    assert len(pushes) == 2
    assert rollbacks(a) == []
    assert LONE_LANE in a.feed.stopped


def test_a_lone_heads_preload_blocked_still_waits_with_one_push(tmp_path: Path) -> None:
    rig, a, pushes = lone_rig(tmp_path, [0.0])
    rig.left._do_feed(LONE_CH, "preload")
    assert len(pushes) == 1  # the preload waits where it stopped (the tail brief's §11)


def test_with_no_shared_head_a_lone_heads_load_is_pushed_again_too(tmp_path: Path) -> None:
    rig, a, pushes = lone_rig(tmp_path, [0.0], lanes="a:1, a:3, a:2, a:4")
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    assert rig.module.probe is None  # no shared head: no probe of a shared head
    assert rig.left._put_into_drive(LONE_CH) is None
    assert len(pushes) == 2


def test_a_lone_heads_load_holds_the_lane_while_it_insists(tmp_path: Path) -> None:
    rig, a, pushes = lone_rig(tmp_path, [0.0])
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    h = next(x for x in rig.adapter.hooked if x.unit is a and x.lane == LONE_LANE)
    held = []
    on_start = a.feed.on_start

    def watch(move: FakeMove) -> None:
        held.append(h.hold)
        on_start(move)

    a.feed.on_start = watch
    before = h.hold
    assert rig.left._put_into_drive(LONE_CH) is None
    assert held == [True, True]  # no follow re-arms the lane between its pushes
    assert h.hold == before  # given back as it was


def test_a_lone_heads_probe_error_is_the_u1s_general_error(tmp_path: Path) -> None:
    rig, a, pushes = lone_rig(tmp_path, [0.0])

    def push_alone(h: Hooked, speed: float | None) -> NoReturn:
        raise ace2k_u1_probe.ProbeError("head 2: a:3: the unit's status not read — gone")

    rig.module.adapter.insister.push_alone = push_alone
    with pytest.raises(RuntimeError, match="lane a:3 push to e1: general"):
        rig.left._put_into_drive(LONE_CH)
    assert rig.left.channel_error[LONE_CH] == "general"
    assert "ace2k_u1: head 2: a:3: the unit's status not read — gone" in rig.gcode.lines


def test_a_lone_blocked_end_with_no_distance_leaves_the_tip_unknown(tmp_path: Path) -> None:
    rig, a, pushes = lone_rig(tmp_path, [0.0, 0.0])
    h = next(x for x in rig.adapter.hooked if x.unit is a and x.lane == LONE_LANE)
    probe = rig.module.adapter.insister
    error, _pushed, known = probe._push_insisting(None, h, 70.0)
    assert error == "distance" and len(pushes) == 2
    assert known is False  # a 0 reads as no distance: the tip may be past it


def test_the_ease_speed_is_checked_against_the_held_window_with_no_shared_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lone head's load may ease at EASE_SPEED (10 mm/s), so the hook checks it even with no +
    group: a unit whose least speed is 12 mm/s does not hide it."""
    monkeypatch.setattr(fakes.FakeFeedApi, "has_stop_when", True)
    monkeypatch.setattr(fakes.FakeFeedApi, "held_restart_mm", 15.1)
    monkeypatch.setattr(fakes.FakeFeedApi, "speed_min", 12.0, raising=False)
    monkeypatch.setattr(fakes.FakeFeedApi, "speed_max", 70.0, raising=False)
    rig = tools_rig(tmp_path, lanes="a:1, a:3, a:2, a:4", values={"eject_probe_speed": 20.0})[0]
    assert any("held_mm: at 10 mm/s" in line for line in rig.gcode.lines)


def waiting(rig: SimpleNamespace, a: FakeAce2k) -> Hooked:
    """a:3's filament seen in its bay under the wait, the wait since cleared by the head."""
    h = next(x for x in rig.adapter.hooked if x.unit is a and x.lane == LONE_LANE)
    h.waited = True
    return h


def end(rig: SimpleNamespace, move: FakeMove, kind: str, filament_mm: float) -> None:
    """End a:3's move: its result now, its final event to the adapter at the next pause.

    ace2k completes the move and sends the event in one step. The fake completes the move at its
    start, before the adapter records it, so the event comes a pause later.
    """
    final = {"kind": kind, "mode": "loading", "seq": move.seq, "filament_mm": filament_mm}
    move.event = final

    def deliver(_now: float) -> None:
        rig.reactor.on_pause.remove(deliver)
        rig.adapter.on_feed_event(LONE_LANE, dict(final, unit="a"))

    rig.reactor.on_pause.append(deliver)


def seats(rig: SimpleNamespace, a: FakeAce2k, *ends: tuple[str, float]) -> list[FakeMove]:
    """a:3's loads from the bay end as ``ends`` in turn (kind, filament mm); later ones loaded."""
    on_start = a.feed.on_start
    loads: list[FakeMove] = []

    def seat(move: FakeMove) -> None:
        if move.mode == "load":
            loads.append(move)
            kind, mm = ends[len(loads) - 1] if len(loads) <= len(ends) else ("loaded", 300.0)
            end(rig, move, kind, mm)
            return
        on_start(move)

    a.feed.on_start = seat
    return loads


def seated(rig: SimpleNamespace, a: FakeAce2k) -> None:
    """a:3's load from the bay ends loaded, at the parking point."""
    seats(rig, a)


def ticks(rig: SimpleNamespace, n: int = 20) -> None:
    for _ in range(n):
        rig.reactor.now += 0.2
        rig.adapter.watch(rig.reactor.now)


def lone_grips(a: FakeAce2k) -> list[object]:
    return [g for lane, g in a.feed.grips if lane == LONE_LANE]


def lone(rig: SimpleNamespace, a: FakeAce2k) -> Hooked:
    return next(x for x in rig.adapter.hooked if x.unit is a and x.lane == LONE_LANE)


def tail_out_with_a_filament_in(rig: SimpleNamespace, a: FakeAce2k) -> Hooked:
    """a:3's old piece in e1 after its tail_out, a new filament put in during the tail."""
    h = lone(rig, a)
    rig.left.runout_sensor[LONE_CH].detected = True  # the old piece
    a.lanes[LONE_LANE].update(insert=True, mode="idle")
    rig.adapter.grip_watch.on_tail_out(h)
    assert lone_grips(a) == ["wait"] and h.seat_due
    ticks(rig)
    rig.left.runout_sensor[LONE_CH].detected = False  # the old piece ran out
    ticks(rig)
    assert lone_grips(a) == ["wait", 0]  # the head cleared the wait
    return h


def test_after_the_old_piece_ran_out_the_u1s_load_seats_the_waiting_filament(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    h = tail_out_with_a_filament_in(rig, a)
    assert h.seat_due  # it outlives the wait
    seated(rig, a)
    assert rig.left._put_into_drive(LONE_CH) is None
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "load"), (LONE_LANE, "feed")]
    assert h.seated and not h.seat_due  # the seat's end took it into the gear
    assert rig.left.channel_error[LONE_CH] == "ok"


def test_a_filament_taken_out_of_the_bay_is_not_seated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    h = tail_out_with_a_filament_in(rig, a)
    h.seat_seq = 5  # a seat of this filament started earlier
    a.lanes[LONE_LANE]["insert"] = False  # taken out
    ticks(rig, 1)
    assert not h.seat_due and h.seat_seq is None
    a.lanes[LONE_LANE]["insert"] = True  # put in again with the head clear: no wait now
    ticks(rig)
    assert not h.seat_due


def test_the_load_of_a_waiting_lane_starts_with_the_units_load_from_the_bay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    h = waiting(rig, a)
    seated(rig, a)
    assert rig.left._put_into_drive(LONE_CH) is None
    assert moves(a)[0] == (LONE_LANE, "load", 2000.0, 30.0)  # the unit's load, with its grace
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "load"), (LONE_LANE, "feed")]
    assert not h.seat_due  # the load took it into the gear: the seat is done
    assert rig.left.channel_error[LONE_CH] == "ok"


def test_a_seat_counts_the_unit_as_loading_while_its_report_still_says_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    waiting(rig, a)
    seated(rig, a)
    a.feed.refusals = [None, "busy", "busy"]  # the seat starts; the push meets the running load
    assert rig.left._put_into_drive(LONE_CH) is None
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "load"), (LONE_LANE, "feed")]
    assert rig.left.channel_error[LONE_CH] == "ok"


def test_a_refused_seat_leaves_the_push_as_it_was(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    waiting(rig, a)
    a.feed.refusals = ["busy"]  # the seat only: the push starts
    assert rig.left._put_into_drive(LONE_CH) is None
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "feed")]
    assert rig.left.channel_error[LONE_CH] == "ok"


def test_no_seat_without_a_filament_seen_under_the_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    assert rig.left._put_into_drive(LONE_CH) is None
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "feed")]


def test_a_seated_load_pushed_again_is_seated_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [0.0])
    fakes.give_materials(rig.module)
    waiting(rig, a)
    seated(rig, a)
    assert rig.left._put_into_drive(LONE_CH) is None
    assert [m[:2] for m in moves(a)] == [
        (LONE_LANE, "load"),
        (LONE_LANE, "feed"),
        (LONE_LANE, "feed"),
    ]
    assert rig.left.channel_error[LONE_CH] == "ok"


def test_a_seat_that_ends_in_error_fails_the_load_without_a_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    waiting(rig, a)
    on_start = a.feed.on_start

    def seat_fails(move: FakeMove) -> None:
        if move.mode == "load":
            a.lanes[LONE_LANE]["mode"] = "error"  # the report after the seat's start
            return
        on_start(move)

    a.feed.on_start = seat_fails
    with pytest.raises(RuntimeError, match="lane a:3 push to e1"):
        rig.left._put_into_drive(LONE_CH)
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "load")]  # the push counted it as its load


def test_a_seat_that_ends_blocked_is_pushed_again_and_seated_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    h = waiting(rig, a)
    a.lanes[LONE_LANE]["rest"] = True
    seats(rig, a, ("blocked", 10.0))  # blocked against an old end at the gear, 10 mm in
    assert rig.left._put_into_drive(LONE_CH) is None
    assert [m[:2] for m in moves(a)] == [
        (LONE_LANE, "load"),  # no feed on an unseated tip
        (LONE_LANE, "load"),  # the push again seats again
        (LONE_LANE, "feed"),
    ]
    assert (
        "ace2k_u1: head 2: the push of a:3 stopped blocked after 10 mm: eased 0 mm to its"
        " plunger's rest, pushed again (1/3)"
    ) in rig.gcode.lines
    assert h.seated and rig.left.channel_error[LONE_CH] == "ok"


def test_a_seat_stopped_by_hand_before_the_gear_is_no_seat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    waiting(rig, a)
    a.lanes[LONE_LANE]["rest"] = True
    seats(rig, a, ("stopped", 5.0))  # a stop by hand, the tip not caught yet
    assert rig.left._put_into_drive(LONE_CH) is None
    assert [m[:2] for m in moves(a)] == [
        (LONE_LANE, "load"),
        (LONE_LANE, "load"),
        (LONE_LANE, "feed"),
    ]


def test_a_preload_whose_seat_ends_blocked_waits_quietly_with_its_travel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    h = waiting(rig, a)
    seats(rig, a, ("blocked", 10.0))
    rig.left._do_feed(LONE_CH, "preload")
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "load")]
    assert h.push_end == "blocked" and h.push_mm == 10.0
    assert rig.left.channel_state[LONE_CH] == "wait_insert"  # the quiet BLOCKED, no failure
    assert any("the filament does not move" in line for line in rig.gcode.lines)
    assert h.seat_due  # still at the bay: the next push seats it


def test_a_seat_given_up_and_stopped_short_is_seated_by_the_next_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    h = waiting(rig, a)
    on_start = a.feed.on_start
    loads: list[FakeMove] = []

    def seat(move: FakeMove) -> None:
        if move.mode == "load":
            loads.append(move)
            if len(loads) == 1:
                a.lanes[LONE_LANE]["mode"] = "loading"  # a load that never ends by itself
            else:
                end(rig, move, "loaded", 300.0)
            return
        on_start(move)

    def stop(lane: int) -> None:
        a.lanes[LONE_LANE]["mode"] = "idle"
        if loads and not loads[0].done():
            end(rig, loads[0], "stopped", 5.0)  # the stop of the push given up, short

    a.feed.on_start = seat
    monkeypatch.setattr(a.feed, "stop", stop)
    assert rig.adapter.push_runner.push_to_head(h) == "timeout"
    rig.reactor.pause(rig.reactor.now + 0.2)  # the stop's final event
    assert h.seat_due
    assert rig.adapter.push_runner.push_to_head(h) is None
    assert [m[:2] for m in moves(a)] == [
        (LONE_LANE, "load"),
        (LONE_LANE, "load"),
        (LONE_LANE, "feed"),
    ]


def test_a_seat_whose_bay_empties_is_not_seated_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    h = waiting(rig, a)
    on_start = a.feed.on_start

    def seat(move: FakeMove) -> None:
        if move.mode == "load":
            a.lanes[LONE_LANE].update(insert=False, mode="loading")  # the filament pulled out
            ticks(rig, 1)  # the bay watch sees it before the push does
            return
        on_start(move)

    a.feed.on_start = seat
    assert rig.adapter.push_runner.push_to_head(h) == "no_filament"
    assert not h.seat_due
    a.feed.on_start = on_start
    a.lanes[LONE_LANE].update(insert=True, mode="idle")  # a new filament, the head clear
    ticks(rig)
    assert rig.adapter.push_runner.push_to_head(h) is None
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "load"), (LONE_LANE, "feed")]


def test_a_push_that_raises_after_its_seat_started_seats_again_next_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    h = waiting(rig, a)
    seats(rig, a, ("stopped", 0.0))
    lane_status = rig.adapter.lane_status
    calls: list[int] = []

    def broken(*args: object, **kw: object) -> object:
        if a.feed.started and not calls:
            calls.append(1)
            raise RuntimeError("ace2k: no answer")
        return lane_status(*args, **kw)

    monkeypatch.setattr(rig.adapter, "lane_status", broken)
    with pytest.raises(RuntimeError, match="no answer"):
        rig.adapter.push_runner.push_to_head(h)
    rig.reactor.pause(rig.reactor.now + 0.2)  # the seat's final event, short
    assert h.seat_due
    assert rig.adapter.push_runner.push_to_head(h) is None
    assert [m[:2] for m in moves(a)] == [
        (LONE_LANE, "load"),
        (LONE_LANE, "load"),
        (LONE_LANE, "feed"),
    ]


def test_a_late_end_of_an_earlier_motion_does_not_seat_the_waiting_filament(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    h = waiting(rig, a)
    a.feed.on_start = None
    earlier = a.feed.start_move(LONE_LANE, "feed", 50.0, 30.0)
    rig.adapter.started(h, earlier)
    rig.adapter.started(h, a.feed.start_move(LONE_LANE, "rollback", 5.0, 20.0))  # supersedes it
    final = {"kind": "done", "mode": "feeding", "seq": earlier.seq, "filament_mm": 300.0}
    rig.adapter.on_feed_event(LONE_LANE, dict(final, unit="a"))  # late: stale
    assert h.seat_due


def test_a_late_end_of_the_seats_own_load_seats_the_waiting_filament(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    h = waiting(rig, a)
    a.feed.on_start = None
    seat = rig.adapter.push_runner._seat(h, 300.0, 30.0)
    assert seat is not None
    rig.adapter.started(h, a.feed.start_move(LONE_LANE, "feed", 50.0, 30.0))  # the cut's push
    final = {"kind": "stopped", "mode": "loading", "seq": seat.seq, "filament_mm": 300.0}
    rig.adapter.on_feed_event(LONE_LANE, dict(final, unit="a"))  # late, after the push started
    assert h.seated and not h.seat_due


def test_a_report_idle_before_the_seats_short_end_starts_no_feed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    h = waiting(rig, a)
    on_start = a.feed.on_start

    def seat(move: FakeMove) -> None:
        if move.mode != "load":
            on_start(move)
            return

        def later(_now: float) -> None:  # the report is idle; the final comes a poll later
            rig.reactor.on_pause.remove(later)
            end(rig, move, "blocked", 10.0)

        rig.reactor.on_pause.append(later)

    a.feed.on_start = seat
    rig.adapter.tag_push_speed_mm_s = 0.0  # no cut: nothing waits for the seat's report
    rig.left._do_feed(LONE_CH, "preload")
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "load")]  # no feed on the unseated tip
    assert h.push_end == "blocked" and h.push_mm == 10.0
    assert rig.left.channel_state[LONE_CH] == "wait_insert"


def pull_after_the_wait(rig: SimpleNamespace, a: FakeAce2k) -> list[FakeMove]:
    """a:3's load from the bay, as on the bench (2026-10-29): its tag reads "searching" from the
    load's first mm, the tag is read 2 s in, and the filament reaches e1's sensor 4 s in. A stop
    ends the load where it is: 7.4 mm in before the head (the gear not holding the tip yet), at
    the head 900 mm in."""
    lane = a.lanes[LONE_LANE]
    sensor = rig.left.runout_sensor[LONE_CH]
    on_start = a.feed.on_start
    loads: list[FakeMove] = []
    start = [0.0]

    def seat(move: FakeMove) -> None:
        if move.mode != "load":
            on_start(move)
            return
        loads.append(move)
        start[0] = rig.reactor.now
        lane.update(mode="loading", tag=dict(fakes.NO_TAG, state="searching"))

    def report(now: float) -> None:
        if not loads or loads[-1].done():
            return
        if now - start[0] >= 2.0:
            lane["tag"] = dict(fakes.NO_TAG, state="read")
        if now - start[0] >= 4.0:
            sensor.detected = True

    def stop(n: int) -> None:
        a.feed.stopped.append(n)
        a.feed.log.append(("stop", n))
        if loads and not loads[-1].done():
            lane["mode"] = "idle"
            end(rig, loads[-1], "stopped", 900.0 if sensor.detected else 7.4)

    a.feed.on_start = seat
    a.feed.stop = stop
    rig.reactor.on_pause.append(report)
    return loads


def test_after_a_wait_the_load_pulls_from_the_bay_to_the_head_in_one_go(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bench's lane a:3 into head 2 after a wait: the unit's load is not cut at 7 mm. It
    pulls the new filament from the bay to the head's sensor in one go, the tag read on the way,
    with no push again and no return to the parking point."""
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    h = waiting(rig, a)
    loads = pull_after_the_wait(rig, a)
    assert rig.left._put_into_drive(LONE_CH) is None
    # one pull, to the head, starting at 30 mm/s so the gear catches the tip
    assert moves(a) == [(LONE_LANE, "load", 2000.0, 30.0)]
    assert len(loads) == 1 and pushes == []  # no push again, no feed
    assert [e for e in a.feed.log if e[0] != "start"] == [
        ("speed", LONE_LANE, 70.0),  # up to the push's speed once the tag is read
        ("stop", LONE_LANE),  # at the head's sensor, and only there
    ]
    assert not any("pushed again" in line for line in rig.gcode.lines)
    assert rig.left.channel_error[LONE_CH] == "ok"
    rig.reactor.pause(rig.reactor.now + 0.2)  # the stop's final event
    assert h.seated and not h.seat_due


def test_a_pull_after_a_wait_that_ends_short_of_the_head_goes_on_as_a_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The load ends by itself before the head, past the gear: the push goes on from there."""
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    h = waiting(rig, a)
    seated(rig, a)  # the load ends loaded, 300 mm in
    assert rig.left._put_into_drive(LONE_CH) is None
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "load"), (LONE_LANE, "feed")]
    assert moves(a)[1][3] == 70.0  # the push's own speed
    assert h.seated and rig.left.channel_error[LONE_CH] == "ok"


def test_a_load_with_no_wait_still_cuts_the_units_search_and_pushes_from_there(
    tmp_path: Path,
) -> None:
    """No wait: the U1 asks the load while the unit's own load searches the tag, 52 mm in, the
    tip held. The push stops that search where it is and pushes on to the head, as before."""
    rig, a, pushes = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    h = lone(rig, a)
    lane = a.lanes[LONE_LANE]
    lane.update(mode="loading", encoder_mm=100.0, tag=dict(fakes.NO_TAG, state="searching"))
    stopped = {"kind": "stopped", "mode": "loading", "seq": 0, "filament_mm": 52.0}

    def stop(n: int) -> None:
        a.feed.stopped.append(n)
        a.feed.log.append(("stop", n))
        if lane["mode"] == "loading":
            lane.update(mode="idle", tag=dict(fakes.NO_TAG, state="pending"), last_event=stopped)

    a.feed.stop = stop
    assert rig.left._put_into_drive(LONE_CH) is None
    assert a.feed.log[0] == ("stop", LONE_LANE)  # the cut, before any move of the push
    assert [m[:2] for m in moves(a)] == [(LONE_LANE, "feed")]  # no seat, one push
    assert len(pushes) == 1 and not h.seat_due
    assert any("the unit's search is stopped" in line for line in rig.gcode.lines)
    assert rig.left.channel_error[LONE_CH] == "ok"


@pytest.mark.parametrize(("load_speed", "seat_speed"), [(20.0, 20.0), (60.0, 30.0), (70.0, 30.0)])
def test_the_pull_after_a_wait_starts_at_30_or_a_lower_load_speed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, load_speed: float, seat_speed: float
) -> None:
    """ace2k's load_speed (60 by default) never raises the seat's speed above 30 mm/s; a lower
    one is kept."""
    monkeypatch.setattr(fakes.FakeFeedApi, "has_wait", True)
    monkeypatch.setattr(fakes.FakeFeedApi, "load_speed_mm_s", load_speed)
    rig, a, _ = lone_rig(tmp_path, [])
    fakes.give_materials(rig.module)
    waiting(rig, a)
    pull_after_the_wait(rig, a)
    assert rig.left._put_into_drive(LONE_CH) is None
    assert moves(a) == [(LONE_LANE, "load", 2000.0, seat_speed)]
