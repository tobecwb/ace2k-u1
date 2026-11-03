"""Each feed the adapter starts says whether its front tip is free (ace2k host interface 12):
the pushes toward a head with nothing holding the tip True, the pushes with the tip in a head
False, every other mode without the keyword."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import ace2k_u1_probe
import pytest
import test_eject
import test_nudge
import test_probe
import test_push
from fakes import NOT_PASSED
from test_probe import probe_rig

if TYPE_CHECKING:
    from fakes import FakeFeedApi, FakeMove


def feeds(api: FakeFeedApi) -> list[bool | str]:
    return [free for (_lane, mode, free) in api.free_tips if mode == "feed"]


def explicit_false(values: list[bool | str], count: int) -> None:
    """count entries, each the False the adapter passed (not NOT_PASSED)."""
    assert len(values) == count and all(v is False for v in values), values


def lane_feeds(api: FakeFeedApi, lane: int) -> list[bool | str]:
    return [free for (n, mode, free) in api.free_tips if mode == "feed" and n == lane]


def test_free_tip_push_from_the_bay_to_the_head() -> None:
    adapter, reactor, left, right, ace = test_push.build()
    h = test_push.lane1(adapter)
    test_push.sensor_after(reactor, h.ff.runout_sensor[h.ch], 1.0)
    assert adapter.push_runner.push_to_head(h) is None
    assert feeds(ace.feed) == [True]


def test_free_tip_probe_on_an_empty_head(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "rollback":
            rig.head_sensor.detected = False  # a:1 was at the sensor, not in the gear
        if move.lane == 0 and move.mode == "feed":
            rig.head_sensor.detected = True
        if move.lane == 1:  # a:2 is free: its roll back is fed back
            test_probe.end(move, -100.0 if move.mode == "rollback" else 100.0)

    a.feed.on_start = on_start
    assert rig.module.probe.which_lane(slot) is slot.lanes[0]
    assert lane_feeds(a.feed, 0) == [True]  # the push to the sensor: the tip is free
    explicit_false(lane_feeds(a.feed, 1), 1)  # a free lane's push back: its tip is in the head


def test_free_tip_search_pushes_of_the_other_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ace2k_u1_probe, "INSIST_TRIES", 0)
    rig, b, slot = test_probe.four_lane_rig(tmp_path)
    pushes = {0: 0}

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            if move.lane == 2:
                rig.head_sensor.detected = False
            test_probe.end(move, -1.0)
        elif move.lane == 0:
            pushes[0] += 1
            if pushes[0] == 1:
                test_probe.end(move, 500.0, kind="blocked")
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1:
            test_probe.end(move, 120.0, kind="blocked")
        elif move.lane == 2:
            rig.head_sensor.detected = True

    b.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert lane_feeds(b.feed, 1) == [True]  # the other lanes of the search
    assert lane_feeds(b.feed, 2) == [True]
    assert feeds(b.feed) == [True, True, True, True]  # and the lane's own pushes


def test_free_tip_ease_pushes_back_to_the_sensor(tmp_path: Path) -> None:
    rig, a, slot = test_probe.ease_rig(tmp_path, rest=False)

    def tick(now: float) -> None:
        a.reports += 1
        modes_now = [m[1] for m in a.feed.started]
        if modes_now[-1] == "rollback":
            rig.head_sensor.detected = False  # the tip left the sensor before the plunger rested
        elif len(modes_now) > 2:
            rig.head_sensor.detected = True

    rig.reactor.on_pause.append(tick)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert [m[1] for m in a.feed.started] == ["feed", "rollback", "feed"]
    assert feeds(a.feed) == [True, True]  # the push, then the push back after the ease


def test_free_tip_probe_push_back_after_a_taut_roll_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    free = test_probe.free_lane(0)
    opened = {}

    def on_start(move: FakeMove) -> None:
        free(move)
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True
            opened[1] = move
        elif move.lane == 1 and move.mode == "feed":
            test_probe.end(move, 37.0)

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(lane: int) -> None:
        stop(lane)
        move = opened.pop(lane, None)
        if move is not None:
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -37.0}

    a.feed.stop = stopped
    test_probe.reports_tick(rig, a)
    assert rig.module.probe.which_lane(slot) is slot.lanes[1]
    explicit_false(lane_feeds(a.feed, 1), 1)  # the taut lane's tip is in the head
    explicit_false(lane_feeds(a.feed, 0), 1)  # and so is the free lane's


def test_free_tip_retry_push() -> None:
    adapter, reactor, left, right, ace = test_nudge.setup()
    left.motor.run_one_cycle(test_nudge.DIR_B, 0.85, 0.1)
    assert ("start", 0, "feed", 10.0) in ace.feed.log
    explicit_false(feeds(ace.feed), 1)


def test_free_tip_eject_push_back() -> None:
    adapter, reactor, left, ace = test_eject.setup(head=True)
    adapter.ejector.start_eject(test_eject.LANE)
    reactor.run_timers()
    test_eject.end_probe(ace, "done", -3.0, -10.4, at_rest=False)
    test_eject.advance(reactor)
    assert test_eject.last_start(ace)[1] == "feed"
    explicit_false(feeds(ace.feed), 1)
    explicit_false([t[2] for t in ace.feed.free_tips if t[1] == "rollback"], 1)


def test_free_tip_assist_carries_no_keyword() -> None:
    adapter, reactor, left, right, ace = test_nudge.setup()  # the follow: an assist
    assert ace.feed.free_tips == [(0, "assist_both", NOT_PASSED)]


def test_free_tip_unload_carries_no_keyword() -> None:
    adapter, reactor, left, ace = test_eject.setup(head=False)
    adapter.ejector.start_eject(test_eject.LANE)
    reactor.run_timers()
    assert test_eject.last_start(ace)[1] == "unload"
    assert ace.feed.free_tips == [(test_eject.LANE, "unload", NOT_PASSED)]
    assert feeds(ace.feed) == []
