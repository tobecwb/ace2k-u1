"""ACEK_SPLITTER_MEASURE: one pair measured against a simulated shared tube, and the command."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import ace2k_u1_config
import ace2k_u1_splitter
import pytest
from ace2k_u1_common import Hooked, move_max_mm
from ace2k_u1_probe import ProbeError
from ace2k_u1_splitter import (
    MEASURE_SPEED,
    REPUSH_MAX_MM,
    SENSOR_CLEAR_MM,
    SPLITTER_MARGIN_MM,
    WAIT_BACK_MM,
    MeasureError,
    PairResult,
    SplitterMeasure,
)
from fakes import FakeAce2k, FakeMove
from test_probe import end, probe_rig
from test_tools import run

EASE_MM = 40.0


@dataclass
class Tube:
    """A shared head: A's tip is a_back mm behind the head's sensor; B's tip is b_out mm ahead of
    its parking point. B meets A's filament after meet mm while A's tip is less than point mm back
    from the sensor; past that, B passes freely up to the sensor at head mm. The sensor is set by
    whichever tip is at it: A at a_back <= 0, or B at head. A rolled back off it clears it once
    its tip is ``clear`` mm back (never, with stuck)."""

    point: float = 862.0
    meet: float = 1041.0
    head: float = 1900.0
    pushed_a: float = 1700.0  # A's tip starts this far behind the head's sensor (its parking)
    snags: list[float] = field(default_factory=list)  # B positions that catch once each
    a_back: float = 0.0
    b_out: float = 0.0
    # (lane, mode, mm, speed)
    log: list[tuple[str, str, float, float | None]] = field(default_factory=list)
    a_moves_with_b_at: list[float] = field(default_factory=list)
    clear: float = 3.0
    stuck: bool = False

    def sensor(self) -> bool:
        return self.a_back <= 0.0 or self.b_out >= self.head


class FakeProbe:
    """The probe's public moves (Task 1), over a Tube. Lane "A" and lane "B" are strings."""

    def __init__(self, tube: Tube, move_max: float = 2000.0) -> None:
        self.t, self.move_max = tube, move_max
        self.cfg = SimpleNamespace(probe_speed=50.0, swap_speed=70.0)
        self.rests = True  # what ease_to_rest says
        self.ease_mm = 2.0  # how far ease_to_rest rolls B back

    def feed_free(
        self, h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        t = self.t
        t.log.append((h, "feed", length, speed))
        if "set" in stops and t.sensor():  # the other tip holds it set: the stop at once
            return "set", None, 0.0
        if h == "A":
            t.a_moves_with_b_at.append(t.b_out)
            if "set" in stops and length >= t.a_back:  # reaches the head's sensor
                moved, t.a_back = t.a_back, 0.0
                return "set", None, moved
            t.a_back -= length
            return "end", "done", length
        return self._feed_b(length, stops)

    def _feed_b(self, length: float, stops: frozenset[str]) -> tuple[str, str | None, float]:
        t = self.t
        start = t.b_out
        stop = start + length
        for s in list(t.snags):
            if start < s <= stop:
                t.snags.remove(s)
                t.b_out = s
                return "end", "blocked", s - start
        if t.a_back < t.point and stop > t.meet:
            t.b_out = max(start, t.meet)
            return "end", "blocked", t.b_out - start
        if stop >= t.head and "set" in stops:
            t.b_out = t.head
            return "set", None, t.head - start
        t.b_out = stop
        return "end", "done", length

    def move(
        self,
        h: str,
        mode: str,
        length: float,
        stops: frozenset[str] = frozenset(),
        speed: float | None = None,
    ) -> tuple[str, str | None, float]:
        assert h == "A" and mode == "rollback"
        t = self.t
        t.log.append((h, "rollback", length, speed))
        t.a_moves_with_b_at.append(t.b_out)
        if "empty" in stops and t.sensor() and not t.stuck and t.a_back + length >= t.clear:
            moved, t.a_back = t.clear - t.a_back, t.clear
            return "empty", None, moved
        t.a_back += length
        return "end", "done", length

    def back(self, h: str, length: float, speed: float | None = None) -> None:
        self.t.log.append((h, "back", length, speed))
        if h == "A":
            self.t.a_moves_with_b_at.append(self.t.b_out)
            self.t.a_back += length
        else:
            self.t.b_out -= length

    def ease_to_rest(self, h: str) -> tuple[bool, float]:
        self.t.log.append((h, "ease", self.ease_mm, 10.0))
        self.t.b_out -= self.ease_mm
        return self.rests, self.ease_mm


class FakeAdapter:
    head_budget_mm = 3000.0

    def __init__(self) -> None:
        self.lines: list[str] = []

    def lane_text(self, h: str) -> str:
        return {"A": "a:1", "B": "a:2"}[h]

    def say(self, msg: str) -> None:
        self.lines.append(msg)


SLOT = SimpleNamespace(head=0)


def setup(tube: Tube) -> tuple[FakeProbe, FakeAdapter, SplitterMeasure]:
    tube.a_back = tube.pushed_a
    probe, adapter = FakeProbe(tube), FakeAdapter()
    return probe, adapter, SplitterMeasure(probe, adapter, max_move=lambda h: 2000.0)


def measure(tube: Tube, first: float = 900.0) -> tuple[float, FakeAdapter]:
    adapter = FakeAdapter()
    tube.a_back = tube.pushed_a
    m = SplitterMeasure(FakeProbe(tube), adapter, max_move=lambda h: 2000.0)
    result = m.measure_pair(SLOT, "A", "B", first)
    return result.point, adapter


def test_the_pair_finds_the_point_and_leaves_both_lanes_in_place() -> None:
    tube = Tube()
    point, adapter = measure(tube)
    assert tube.point <= point < tube.point + 5.0
    assert tube.b_out == pytest.approx(0.0)  # B back at its parking point
    assert tube.a_back == pytest.approx(point + SPLITTER_MARGIN_MM)  # A behind the splitter
    assert "ace2k_u1: head 1: a:1 leaves the splitter at" in adapter.lines[-1]


def test_b_waits_behind_the_meeting_point_before_every_move_of_a() -> None:
    tube = Tube()
    measure(tube)
    wait = tube.meet - WAIT_BACK_MM
    moves = tube.a_moves_with_b_at[3:]  # after A's push to the sensor and its two backs off
    assert moves  # A moved
    assert all(b == pytest.approx(wait) for b in moves)


def test_every_push_of_b_runs_at_the_measure_speed() -> None:
    tube = Tube()
    measure(tube)
    feeds = [speed for lane, mode, _mm, speed in tube.log if lane == "B" and mode == "feed"]
    assert feeds and all(speed == MEASURE_SPEED for speed in feeds)


def test_a_snag_that_gives_is_pushed_again_with_short_pushes() -> None:
    tube = Tube(snags=[700.0, 1000.0])  # the entrance and the exit of the splitter
    point, _adapter = measure(tube)
    assert tube.point <= point < tube.point + 5.0
    log = tube.log
    eases = [i for i, (lane, mode, _m, _s) in enumerate(log) if lane == "B" and mode == "ease"]
    assert len(eases) >= 2
    for i in eases:  # each push again after an ease is short: its ease, then REPUSH_MAX_MM
        lane, mode, mm, _speed = log[i + 1]
        assert (lane, mode) == ("B", "feed") and mm <= 2.0 + REPUSH_MAX_MM


def test_a_block_that_does_not_give_is_a_and_ends_the_insisting() -> None:
    tube = Tube()
    measure(tube)
    # Against A the first push again gains under 5 mm over its ease: no second push again in a row.
    log = tube.log
    for i, (lane, mode, _m, _s) in enumerate(log[:-2]):
        if lane == "B" and mode == "ease":
            assert not (log[i + 2][0] == "B" and log[i + 2][1] == "ease")


def test_a_never_rolls_back_past_where_it_was_parked() -> None:
    tube = Tube(point=1690.0, pushed_a=1700.0)
    tube.a_back = tube.pushed_a
    m = SplitterMeasure(FakeProbe(tube), FakeAdapter(), max_move=lambda h: 2000.0)
    with pytest.raises(MeasureError, match="blocked with a:1 rolled back 1650 mm"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.a_back <= 1700.0 - SPLITTER_MARGIN_MM + 1e-6  # P - 50: the value can be checked
    assert tube.b_out == pytest.approx(tube.meet - WAIT_BACK_MM)  # at the wait point


def test_b_reaching_the_sensor_with_a_at_it_is_an_error() -> None:
    tube = Tube(point=0.0)  # A is not in B's path
    tube.a_back = tube.pushed_a
    m = SplitterMeasure(FakeProbe(tube), FakeAdapter(), max_move=lambda h: 2000.0)
    with pytest.raises(MeasureError, match="a:2 reached the head's sensor with a:1 just behind it"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.b_out == pytest.approx(0.0)  # rolled back to its parking point


def test_an_error_in_a_try_rolls_b_back_to_the_wait_point() -> None:
    tube = Tube()
    tube.a_back = tube.pushed_a
    probe = FakeProbe(tube)
    feed = probe.feed_free
    calls = {"n": 0}

    def failing(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        if h == "B":
            calls["n"] += 1
            if calls["n"] == 3:  # the first try, after the approach and its push again: a snag
                tube.b_out += 40.0
                return "end", "blocked", 40.0
            if calls["n"] == 4:  # its push again goes 10 mm and fails without telling
                tube.b_out += 10.0
                tube.log.append(("B", "failed", length, speed))
                raise MeasureError("lane a:2's push ended stuck")
        return feed(h, length, stops, speed)

    probe.feed_free = failing  # type: ignore[method-assign]
    m = SplitterMeasure(probe, FakeAdapter(), max_move=lambda h: 2000.0)
    with pytest.raises(MeasureError, match="a:1 is .* mm back from the head's sensor"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    failed = tube.log.index(("B", "failed", 2.0 + REPUSH_MAX_MM, MEASURE_SPEED))
    # 40 in, 2 eased, and the whole 32 mm step of the push that raised
    assert tube.log[failed + 1 :] == [("B", "back", pytest.approx(70.0), 50.0)]
    assert tube.b_out == pytest.approx(tube.meet - WAIT_BACK_MM - 22.0)  # behind the wait point


def zero_distance(probe: FakeProbe, lane: str) -> None:
    """The first feed of lane ends as the fake says, but reports no distance."""
    feed = probe.feed_free
    calls = {"n": 0}

    def no_mm(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        why, kind, moved = feed(h, length, stops, speed)
        if h == lane:
            calls["n"] += 1
            if calls["n"] == 1:
                return why, kind, 0.0
        return why, kind, moved

    probe.feed_free = no_mm  # type: ignore[method-assign]


def test_b_set_with_no_distance_counts_the_whole_step() -> None:
    tube = Tube(point=0.0)
    probe, _adapter, m = setup(tube)
    zero_distance(probe, "B")
    with pytest.raises(MeasureError, match="a:2's push of 2000 mm ended set with no distance"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.log[-1] == ("B", "back", 2000.0, 50.0)
    assert tube.b_out <= 0.0  # toward its bay, never left in the shared tube


def test_b_blocked_with_no_distance_counts_the_whole_step() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    zero_distance(probe, "B")
    with pytest.raises(MeasureError, match="ended blocked with no distance"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.log[-1] == ("B", "back", 2000.0, 50.0)
    assert tube.b_out <= 0.0


def test_a_set_with_no_distance_is_an_error() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    zero_distance(probe, "A")
    with pytest.raises(MeasureError, match="a:1's push .* ended set with no distance"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert not [e for e in tube.log if e[0] == "B"]  # B never moved


def test_a_not_reaching_the_sensor_is_an_error() -> None:
    tube = Tube()
    _probe, adapter, m = setup(tube)
    adapter.head_budget_mm = 1000.0
    with pytest.raises(
        MeasureError,
        match="a:1 did not reach the head's sensor .*a:1 pushed 1000 mm from its parking point,"
        " a:2 is at its parking point",
    ):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert not [e for e in tube.log if e[0] == "B"]


def test_b_meeting_nothing_in_the_approach_is_an_error() -> None:
    tube = Tube(meet=4000.0, head=5000.0)
    _probe, _adapter, m = setup(tube)
    with pytest.raises(MeasureError, match="a:2 pushed 3000 mm and met nothing"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.b_out == pytest.approx(0.0)


def test_the_pushes_again_spent_in_the_approach_is_an_error() -> None:
    tube = Tube(snags=[700.0, 750.0, 800.0, 850.0])  # four snags that each give
    _probe, _adapter, m = setup(tube)
    with pytest.raises(MeasureError, match="a:2 could not tell a snag from a:1"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    eases = [e for e in tube.log if e[:2] == ("B", "ease")]
    assert len(eases) == 3  # REPUSH_TRIES
    assert tube.b_out == pytest.approx(0.0)


def test_an_ease_that_does_not_reach_rest_in_the_approach_is_an_error() -> None:
    tube = Tube(snags=[700.0])
    probe, _adapter, m = setup(tube)
    probe.rests = False
    with pytest.raises(MeasureError, match="a:2 could not tell a snag from a:1"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.b_out == pytest.approx(0.0)


def test_the_final_check_blocked_is_an_error() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    back = probe.back

    def closes(h: str, length: float, speed: float | None = None) -> None:
        back(h, length, speed)
        if h == "A" and tube.a_back >= tube.point + SPLITTER_MARGIN_MM - 1.0:
            tube.point = 5000.0  # the final roll back: now nothing passes

    probe.back = closes  # type: ignore[method-assign]
    with pytest.raises(MeasureError, match="a:2 is blocked with a:1 rolled back the new value"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.b_out == pytest.approx(tube.meet - WAIT_BACK_MM)  # at the wait point


def test_the_search_stops_where_the_staged_value_can_be_checked() -> None:
    tube = Tube(point=1652.0, pushed_a=1700.0)  # past P - 50: point + 50 is past where A was parked
    _probe, _adapter, m = setup(tube)
    with pytest.raises(MeasureError, match="a:2 is blocked with a:1 rolled back 1650 mm"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.a_back == pytest.approx(1650.0)  # never past P - 50
    assert tube.b_out == pytest.approx(tube.meet - WAIT_BACK_MM)  # at the wait point


def test_a_point_past_the_bound_is_refused_by_the_final_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tube = Tube(point=1652.0, pushed_a=1700.0)
    _probe, _adapter, m = setup(tube)

    def past_the_bound(
        test: Callable[[float], bool], lo: float, hi: float, first: float, step: float
    ) -> float:
        assert test(1660.0)  # a search that came out past its bound P - 50
        return 1660.0

    monkeypatch.setattr(ace2k_u1_splitter, "search", past_the_bound)
    with pytest.raises(MeasureError, match="a:1 was parked too close to the splitter to check"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.a_back == pytest.approx(1660.0)  # the final roll back was not made


def test_the_final_check_rolls_a_back_to_the_staged_value() -> None:
    tube = Tube(point=1600.0, pushed_a=1700.0)
    point, _adapter = measure(tube)
    assert tube.point <= point < tube.point + 5.0
    assert tube.a_back == pytest.approx(point + SPLITTER_MARGIN_MM)
    assert tube.b_out == pytest.approx(0.0)


def test_b_meeting_a_near_its_parking_point_is_an_error() -> None:
    tube = Tube(meet=40.0)
    _probe, _adapter, m = setup(tube)
    with pytest.raises(MeasureError, match="a:2 met a:1 within 55 mm of its parking point: unload"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.b_out == pytest.approx(0.0)  # rolled back to its parking point


def test_a_result_at_the_sensor_is_an_error() -> None:
    tube = Tube(point=15.0)  # B passes with A just off the sensor: no splitter between them
    _probe, _adapter, m = setup(tube)
    with pytest.raises(MeasureError, match="it does not meet a:1 at a splitter"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.b_out == pytest.approx(tube.meet - WAIT_BACK_MM)


def test_every_move_of_a_runs_at_the_probe_speed() -> None:
    tube = Tube()
    measure(tube)
    a_moves = [e for e in tube.log if e[0] == "A"]
    assert len(a_moves) > 2 and all(speed == 50.0 for _l, _m, _mm, speed in a_moves)
    assert any(mode == "feed" for _l, mode, _mm, _s in a_moves[1:])  # A also fed forward


def test_a_failed_move_of_a_says_where_a_may_be() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    back = probe.back

    def fails(h: str, length: float, speed: float | None = None) -> None:
        if h == "A":
            raise MeasureError("a:1's roll back ended stuck")
        back(h, length, speed)

    probe.back = fails  # type: ignore[method-assign]
    with pytest.raises(MeasureError, match="a:1 is between 13 and 900 mm back from the head"):
        m.measure_pair(SLOT, "A", "B", 900.0)


def test_any_error_rolls_b_back_and_goes_up_unchanged() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    back = probe.back

    def breaks(h: str, length: float, speed: float | None = None) -> None:
        if h == "A":
            raise RuntimeError("the link fell")
        back(h, length, speed)

    probe.back = breaks  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="the link fell"):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.b_out == pytest.approx(tube.meet - WAIT_BACK_MM)


def test_a_push_again_that_does_not_move_is_the_other_lane() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    feed = probe.feed_free
    calls = {"n": 0}

    def still(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        why, kind, moved = feed(h, length, stops, speed)
        if h == "B":
            calls["n"] += 1
            if calls["n"] == 2:  # the approach's push again: blocked, no distance reported
                return why, kind, 0.0
        return why, kind, moved

    probe.feed_free = still  # type: ignore[method-assign]
    result = m.measure_pair(SLOT, "A", "B", 900.0)
    # B counted the 2 mm ease: the tip cannot pass the point where it was blocked before it
    assert result.met == pytest.approx(tube.meet)
    assert tube.point <= result.point < tube.point + 5.0
    first_back = next(e for e in tube.log if e[:2] == ("B", "back"))
    assert first_back[2] == pytest.approx(WAIT_BACK_MM)
    assert tube.b_out == pytest.approx(0.0)  # at its parking point


def test_an_unsure_try_counts_as_blocked() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    ease = probe.ease_to_rest
    met = {"done": False}

    def no_rest(h: str) -> tuple[bool, float]:
        rested, mm = ease(h)
        return (rested if not met["done"] else False), mm

    def back_mark(h: str, length: float, speed: float | None = None) -> None:
        if h == "A":
            met["done"] = True
        FakeProbe.back(probe, h, length, speed)

    probe.ease_to_rest = no_rest  # type: ignore[method-assign]
    probe.back = back_mark  # type: ignore[method-assign]
    result = m.measure_pair(SLOT, "A", "B", 900.0)
    tries_eased = [e for e in tube.log if e[:2] == ("B", "ease")]
    assert len(tries_eased) > 1  # the approach's ease, then the tries' that did not reach rest
    assert tube.point <= result.point < tube.point + 5.0  # blocked tries kept the search right


def test_pushes_again_that_report_no_distance_keep_b_at_its_wait_point() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    feed = probe.feed_free

    def still(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        again = h == "B" and tube.log[-1][:2] == ("B", "ease")
        why, kind, moved = feed(h, length, stops, speed)
        return (why, kind, 0.0) if again and kind == "blocked" else (why, kind, moved)

    probe.feed_free = still  # type: ignore[method-assign]
    result = m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.point <= result.point < tube.point + 5.0
    wait = tube.meet - WAIT_BACK_MM
    assert all(b == pytest.approx(wait) for b in tube.a_moves_with_b_at[3:])  # B never drifted
    assert tube.b_out == pytest.approx(0.0)


def test_a_sensor_that_does_not_clear_within_the_ease_is_an_error() -> None:
    tube = Tube(stuck=True)
    _probe, _adapter, m = setup(tube)
    with pytest.raises(
        MeasureError,
        match="a:1's tip did not leave the head's sensor within 40 mm; a:1 is between 0 and 40 mm"
        " back from the head's sensor, a:2 is at its parking point",
    ):
        m.measure_pair(SLOT, "A", "B", 900.0)
    assert [e[:2] for e in tube.log] == [("A", "feed"), ("A", "rollback")]  # nothing more moved
    assert tube.log[1] == ("A", "rollback", EASE_MM, 50.0)


def test_a_backs_off_the_sensor_before_b_approaches() -> None:
    tube = Tube()
    measure(tube)
    assert tube.log[1] == ("A", "rollback", EASE_MM, 50.0)  # off the sensor: it clears 3 mm back
    assert tube.log[2] == ("A", "rollback", SENSOR_CLEAR_MM, 50.0)  # and 10 mm more
    assert tube.log[3][:2] == ("B", "feed")  # B's approach, with the sensor empty
    assert tube.a_moves_with_b_at[3] == pytest.approx(tube.meet - WAIT_BACK_MM)
    assert tube.log[4][:2] == ("B", "ease")


def test_the_roll_back_behind_the_sensor_counts_its_reported_distance() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    move = probe.move

    def short(
        h: str,
        mode: str,
        length: float,
        stops: frozenset[str] = frozenset(),
        speed: float | None = None,
    ) -> tuple[str, str | None, float]:
        if not stops and length == SENSOR_CLEAR_MM:  # it slips: 7 mm of the 10
            why, kind, _moved = move(h, mode, 7.0, stops, speed)
            return why, kind, 7.0
        return move(h, mode, length, stops, speed)

    probe.move = short  # type: ignore[method-assign]
    feed = probe.feed_free
    los: list[float] = []

    def watch(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        if h == "B" and not los:
            assert m.a_back is not None
            los.append(m.a_back)
        return feed(h, length, stops, speed)

    probe.feed_free = watch  # type: ignore[method-assign]
    m.measure_pair(SLOT, "A", "B", 900.0)
    assert los == [pytest.approx(3.0 + 7.0)]  # cleared 3 mm back, then 7 mm reported


def test_a_push_again_takes_back_a_long_ease_before_it_tests_the_gain() -> None:
    tube = Tube(snags=[700.0])  # a snag that gives
    probe, _adapter, m = setup(tube)
    probe.ease_mm = 35.0
    result = m.measure_pair(SLOT, "A", "B", 900.0)
    assert result.met == pytest.approx(tube.meet)  # the snag was not taken for A
    assert tube.point <= result.point < tube.point + 5.0
    again = [tube.log[i + 1] for i, e in enumerate(tube.log[:-1]) if e[:2] == ("B", "ease")]
    assert again and all(mm == pytest.approx(35.0 + REPUSH_MAX_MM) for _l, _m, mm, _s in again)


def test_short_feeds_of_a_that_report_one_count_are_not_errors() -> None:
    tube = Tube(point=870.0)  # with a first try of 863, the search feeds A 6 mm once
    probe, _adapter, m = setup(tube)
    feed = probe.feed_free
    short: list[float] = []

    def one_count(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        why, kind, moved = feed(h, length, stops, speed)
        if h == "A" and kind == "done" and length < 10.0:
            short.append(length)
            return why, kind, 1.24  # one encoder count, though it moved the whole length
        return why, kind, moved

    probe.feed_free = one_count  # type: ignore[method-assign]
    result = m.measure_pair(SLOT, "A", "B", 863.0)
    assert short  # a short feed ran
    assert all(mm >= 5.0 for mm in short)  # none under INSIST_MIN_MM was sent
    assert result.point >= tube.point


def test_a_feed_that_slips_is_fed_again_to_its_target() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    feed = probe.feed_free

    def slips(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        if h == "A" and length < tube.a_back:  # a feed short of the sensor carries 91 %
            return feed(h, length * 0.91, stops, speed)
        return feed(h, length, stops, speed)

    probe.feed_free = slips  # type: ignore[method-assign]
    result = m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.point <= result.point < tube.point + 5.0
    assert [e for e in tube.log if e[:2] == ("A", "feed")][2:]  # A fed forward, again and again


def test_a_feed_that_carries_too_little_is_an_error() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    feed = probe.feed_free

    def slips(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        if h == "A" and length < tube.a_back:
            return feed(h, length * 0.5, stops, speed)
        return feed(h, length, stops, speed)

    probe.feed_free = slips  # type: ignore[method-assign]
    with pytest.raises(MeasureError, match="a:1's feed of 444 mm to 456 mm ended done after 222"):
        m.measure_pair(SLOT, "A", "B", 900.0)


def test_every_push_again_runs_at_the_measure_speed() -> None:
    tube = Tube()
    probe, _adapter, m = setup(tube)
    feed, ease = probe.feed_free, probe.ease_to_rest
    caught = {"done": False}

    def snag(
        h: str, length: float, stops: frozenset[str], speed: float | None = None
    ) -> tuple[str, str | None, float]:
        if h == "A" and not caught["done"]:  # A's first push catches once, 300 mm in
            caught["done"] = True
            tube.log.append((h, "feed", length, speed))
            tube.a_back -= 300.0
            return "end", "blocked", 300.0
        return feed(h, length, stops, speed)

    def eases(h: str) -> tuple[bool, float]:
        if h == "A":
            tube.log.append((h, "ease", 2.0, 10.0))
            tube.a_back += 2.0
            return True, 2.0
        return ease(h)

    probe.feed_free = snag  # type: ignore[method-assign]
    probe.ease_to_rest = eases  # type: ignore[method-assign]
    result = m.measure_pair(SLOT, "A", "B", 900.0)
    assert tube.point <= result.point < tube.point + 5.0
    a_log = [e for e in tube.log if e[0] == "A"]
    assert a_log[:4] == [
        ("A", "feed", 2000.0, 50.0),
        ("A", "ease", 2.0, 10.0),
        ("A", "feed", 2.0 + REPUSH_MAX_MM, MEASURE_SPEED),  # the push again, at 20 mm/s
        ("A", "feed", 2000.0, 50.0),  # then at its own speed
    ]
    again = [tube.log[i + 1] for i, e in enumerate(tube.log[:-1]) if e[1] == "ease"]
    assert again and all(speed == MEASURE_SPEED for _l, _m, _mm, speed in again)


def test_the_real_probe_runs_the_measure(tmp_path: Path) -> None:
    """The measure over the probe's real moves: the stop at the sensor, the ease and the rolls back
    read their distances from the unit's final events."""
    rig, a = probe_rig(tmp_path)
    rig.head_sensor.detected = False
    slot = rig.adapter.slot_of_head(0)
    t = SimpleNamespace(a_back=1700.0, b_out=0.0, point=862.0, meet=1041.0)

    def on_start(move: FakeMove) -> None:
        length = a.feed.started[-1][2]
        if move.lane == 0 and move.mode == "feed":
            if length >= t.a_back:  # A reaches the head's sensor: the stop's event has the mm
                rig.head_sensor.detected = True
                end(move, t.a_back, "stopped")
                t.a_back = 0.0
            else:
                t.a_back -= length
                end(move, length)
        elif move.lane == 0:
            if rig.head_sensor.detected:  # A backs off the sensor: it clears 3 mm back
                rig.head_sensor.detected = False
                end(move, -3.0, "stopped")
                t.a_back = 3.0
            else:
                t.a_back += length
                end(move, -length)
        elif move.mode == "feed":
            assert not rig.head_sensor.detected  # B's moves start with the sensor empty
            stop = t.b_out + length
            if t.a_back < t.point and stop > t.meet:
                end(move, max(0.0, t.meet - t.b_out), "blocked")
                t.b_out = max(t.b_out, t.meet)
            else:
                t.b_out = stop
                end(move, length)
        else:
            t.b_out -= length
            end(move, -length)

    a.feed.on_start = on_start
    rig.reactor.on_pause.append(lambda now: setattr(a, "reports", a.reports + 1))
    m = SplitterMeasure(rig.module.probe, rig.adapter, max_move=move_max_mm)
    result = m.measure_pair(slot, slot.lanes[0], slot.lanes[1], 900.0)
    assert t.point <= result.point < t.point + 5.0
    assert result.met == pytest.approx(t.meet)
    assert t.b_out == pytest.approx(0.0)
    assert t.a_back == pytest.approx(result.point + SPLITTER_MARGIN_MM)


DEFAULT = ace2k_u1_config.SPLITTER_DEFAULT_MM


class FakeConfigfile:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, str, str]] = []

    def set(self, section: str, key: str, value: str) -> None:
        self.set_calls.append((section, key, value))


def command_rig(
    tmp_path: Path, points: list[float] | Exception, monkeypatch: pytest.MonkeyPatch
) -> tuple[SimpleNamespace, FakeAce2k, FakeConfigfile, list[tuple[str, str]]]:
    rig, a = probe_rig(tmp_path)
    rig.head_sensor.detected = False  # the head is empty
    for lane in a.lanes:
        lane["insert"] = True
    cf = FakeConfigfile()
    rig.module.printer.objects["configfile"] = cf
    calls: list[tuple[str, str]] = []

    def fake_pair(_self: object, _slot: object, x: Hooked, y: Hooked, _first: float) -> PairResult:
        calls.append((rig.adapter.lane_text(x), rig.adapter.lane_text(y)))
        if isinstance(points, Exception):
            raise points
        return PairResult(x, y, points[len(calls) - 1], 1041.0)

    monkeypatch.setattr(ace2k_u1_splitter.SplitterMeasure, "measure_pair", fake_pair)
    return rig, a, cf, calls


def test_the_command_stages_the_largest_point_plus_the_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _a, cf, calls = command_rig(tmp_path, [862.0], monkeypatch)
    gcmd = run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert calls == [("a:1", "a:2")]
    assert cf.set_calls == [("ace2k_u1", "head1_splitter_mm", "920")]
    assert gcmd.lines[-1] == (
        f"ace2k_u1: head 1: head1_splitter_mm 920 staged (was the default {DEFAULT:g});"
        " run SAVE_CONFIG"
    )


@pytest.mark.parametrize(
    ("point", "staged"),
    [(837.0, "890"), (840.0, "890"), (841.0, "900"), (836.4, "890"), (840.0000000000001, "890")],
)
def test_the_staged_value_is_rounded_up_to_10_mm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: float, staged: str
) -> None:
    rig, _a, cf, _calls = command_rig(tmp_path, [point], monkeypatch)
    run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert cf.set_calls == [("ace2k_u1", "head1_splitter_mm", staged)]


def test_a_rounded_value_at_the_ceiling_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _a, cf, _calls = command_rig(tmp_path, [1941.0], monkeypatch)  # 1991: 2000
    run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert cf.set_calls == [("ace2k_u1", "head1_splitter_mm", "2000")]


def test_a_value_set_in_the_config_says_how_to_save_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _a, cf, _calls = command_rig(tmp_path, [837.0], monkeypatch)
    rig.module.swap_cfg.splitter_mm[0] = 900.0
    rig.module.swap_cfg.splitter_default.discard(0)
    gcmd = run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert cf.set_calls == [("ace2k_u1", "head1_splitter_mm", "890")]
    assert gcmd.lines[-1] == (
        "ace2k_u1: head 1: head1_splitter_mm 890 staged (was 900); run SAVE_CONFIG (if"
        " head1_splitter_mm is set in an included file such as ace2k-u1.cfg, delete or comment"
        " out that line first: Klipper refuses to save a key that an included file sets)"
    )


def test_a_value_far_from_the_one_in_effect_is_staged_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _a, cf, _calls = command_rig(tmp_path, [1401.0], monkeypatch)  # 1451: 1460
    gcmd = run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert cf.set_calls == [("ace2k_u1", "head1_splitter_mm", "1460")]
    assert (
        f"ace2k_u1: head 1: the new value 1460 mm is {1460 - DEFAULT:.0f} mm from the"
        f" {DEFAULT:g} mm in effect: check the tubes before SAVE_CONFIG"
    ) in gcmd.lines


def test_a_failed_pair_stages_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    err = ace2k_u1_splitter.MeasureError("a:2 is blocked with a:1 rolled back 1650 mm")
    rig, _a, cf, _calls = command_rig(tmp_path, err, monkeypatch)
    with pytest.raises(ValueError, match="head 1: splitter measure failed: a:2 is blocked"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert cf.set_calls == []


def test_refused_with_the_heads_sensor_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig, a, cf, calls = command_rig(tmp_path, [862.0], monkeypatch)
    rig.head_sensor.detected = True
    with pytest.raises(ValueError, match="refused: the head's sensor sees filament: unload"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert calls == [] and a.feed.started == [] and cf.set_calls == []


def test_refused_with_one_lane_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig, a, _cf, calls = command_rig(tmp_path, [862.0], monkeypatch)
    a.lanes[1]["insert"] = False
    with pytest.raises(ValueError, match="refused: a:1 is the only lane with filament"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert calls == [] and a.feed.started == []


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_refused_during_a_print(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    rig, _a, _cf, calls = command_rig(tmp_path, [862.0], monkeypatch)
    rig.print_stats.state = state
    with pytest.raises(ValueError, match="refused: a print is printing or paused"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert calls == []


def test_refused_on_a_head_one_lane_feeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig, _a, _cf, calls = command_rig(tmp_path, [862.0], monkeypatch)
    with pytest.raises(ValueError, match="refused: its head is fed by one lane alone"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "2"})
    assert calls == []


def test_the_head_is_given_back_to_its_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _a, _cf, _calls = command_rig(tmp_path, [862.0], monkeypatch)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.head_watch.hand_head(slot, slot.lanes[1])
    run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert slot.current is slot.lanes[1]


def test_a_value_over_the_keys_ceiling_stages_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _a, cf, _calls = command_rig(tmp_path, [1951.0], monkeypatch)  # 2001: 2010
    with pytest.raises(ValueError, match="the new value 2010 mm is over the 2000 mm that head1"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert cf.set_calls == []


def test_a_refused_hand_over_is_a_failed_measure_and_the_head_goes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _a, cf, _calls = command_rig(tmp_path, [862.0], monkeypatch)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.head_watch.hand_head(slot, slot.lanes[1])
    real = rig.adapter.head_watch.hand_head

    def hand(s: object, h: Hooked) -> None:
        if h is slot.lanes[0]:
            raise RuntimeError("a follower did not end")
        real(s, h)

    monkeypatch.setattr(rig.adapter.head_watch, "hand_head", hand)
    with pytest.raises(ValueError, match="head 1: splitter measure failed: a follower did not end"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert cf.set_calls == [] and slot.current is slot.lanes[1]


def test_a_pair_that_failed_after_it_moved_keeps_a_as_the_heads_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    err = ace2k_u1_splitter.MeasureError("a:2 is blocked with a:1 rolled back 1650 mm")
    rig, _a, cf, _calls = command_rig(tmp_path, err, monkeypatch)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.head_watch.hand_head(slot, slot.lanes[1])
    with pytest.raises(ValueError, match="splitter measure failed"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert slot.current is slot.lanes[0]  # a:1 may be in the shared tube: no load of a:2
    assert "ace2k_u1: head 1: the measure failed; a:1 stays the head's lane" in rig.gcode.lines
    assert cf.set_calls == []


def test_a_probe_error_outside_the_pair_is_a_failed_measure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _a, cf, _calls = command_rig(tmp_path, [862.0], monkeypatch)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.head_watch.hand_head(slot, slot.lanes[1])
    real = rig.adapter.head_watch.hand_head

    def hand(s: object, h: Hooked) -> None:
        if h is slot.lanes[0]:
            raise ProbeError("head 1: a:1: the unit's status not read")
        real(s, h)

    monkeypatch.setattr(rig.adapter.head_watch, "hand_head", hand)
    with pytest.raises(ValueError, match="head 1: splitter measure failed: head 1: a:1: the unit"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert cf.set_calls == [] and slot.current is slot.lanes[1]  # nothing moved: given back


def test_refused_with_a_bay_that_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, a, _cf, calls = command_rig(tmp_path, [862.0], monkeypatch)
    slot = rig.adapter.slot_of_head(0)
    real = rig.adapter.lane_status

    def status(h: Hooked, eventtime: float | None = None) -> object:
        if h is slot.lanes[1]:
            raise RuntimeError("the link fell")
        return real(h, eventtime)

    monkeypatch.setattr(rig.adapter, "lane_status", status)
    with pytest.raises(ValueError, match="refused: the bay of a:2 cannot be read"):
        run(rig, "ACEK_SPLITTER_MEASURE", {"HEAD": "1"})
    assert calls == [] and a.feed.started == []
