"""ace2k_u1_common: the records the adapter and the head probe share. It is a leaf: it imports no
other module of the adapter at run time (the type checker's imports aside), so each of them can
import it."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import ace2k_u1_common as common
import ace2k_u1_eject
import ace2k_u1_hooks
import ace2k_u1_probe
import fakes
import pytest

COMMON = Path(__file__).resolve().parents[1] / "klippy" / "extras" / "ace2k_u1_common.py"


def _type_checking(node: ast.AST) -> list[ast.stmt]:
    """The body of an `if TYPE_CHECKING:` block (its imports are the type checker's, not run);
    [] for any other node. Its else: branch runs, so it is not part of it."""
    if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
        return node.body
    return []


def test_the_common_module_imports_no_sibling() -> None:
    tree = ast.parse(COMMON.read_text())
    checked = {id(n) for b in ast.walk(tree) for stmt in _type_checking(b) for n in ast.walk(stmt)}
    run = [n for n in ast.walk(tree) if id(n) not in checked]
    names = [a.name for n in run if isinstance(n, ast.Import) for a in n.names]
    names += [n.module or "" for n in run if isinstance(n, ast.ImportFrom)]
    assert not [n for n in names if n.startswith("ace2k")]


def test_every_move_wait_has_the_one_margin() -> None:
    margins = (
        ace2k_u1_eject.EJECT_MARGIN_S,
        ace2k_u1_hooks.NUDGE_MARGIN_S,
        ace2k_u1_probe.PROBE_MARGIN_S,
    )
    assert margins == (common.MOVE_MARGIN_S,) * 3
    assert (common.MOVE_MARGIN_S,) == (5.0,)  # the value every wait had


def test_move_wait_is_twice_the_travel_time_plus_the_margin() -> None:
    waits = (common.move_wait_s(100.0, 50.0), common.move_wait_s(100.0, 50.0, margin_s=1.0))
    assert waits == (9.0, 5.0)


def test_finite_is_a_real_number_only() -> None:
    assert common.finite(0) and common.finite(-2.5)
    for value in (True, False, None, "1", float("nan"), float("inf"), -float("inf")):
        assert not common.finite(value)


def test_moved_mm_is_the_filament_travel_else_the_motors() -> None:
    travels = (
        common.moved_mm({"filament_mm": -12.5, "motor_mm": 30.0}),
        common.moved_mm({"filament_mm": float("nan"), "motor_mm": -30}),
    )
    assert travels == (12.5, 30)
    assert common.moved_mm({"filament_mm": True, "motor_mm": None}) == 0.0
    assert common.moved_mm(None) == 0.0


def test_a_move_carried_the_filament_only_with_most_of_its_step() -> None:
    assert common.carried(100.0, 90.0) and common.carried(100.0, 120.0)
    assert not common.carried(100.0, 89.0)
    assert not common.carried(0.0, 0.0)  # no distance is not carried


def hooked(lane: int = 0, unit: fakes.FakeAce2k | None = None) -> common.Hooked:
    ff = fakes.FakeFeed(fakes.FakeReactor(), filament_ch=(1, 0))
    return common.Hooked(ff, 1, unit or fakes.FakeAce2k(), lane)


def test_a_hooked_lane_takes_its_head_interface_and_key_from_its_feed_and_unit() -> None:
    b = fakes.FakeAce2k("b")
    h = hooked(2, b)
    assert (h.ch, h.head, h.lane, h.unit) == (1, 0, 2, b)
    assert h.api is b.feed
    assert h.key == ("b", 2)
    unnamed = 3
    assert hooked(unnamed).key == unnamed


def test_a_hooked_lane_starts_as_it_always_did() -> None:
    h = hooked()
    taken = [f.name for f in dataclasses.fields(h) if f.init]
    assert taken == ["ff", "ch", "unit", "lane"]  # the constructor's four arguments, as before
    assert h.wheel is None and h.rise_at is None and h.grip_refused is None
    assert h.push_known is True and h.present is False and h.armed_at == 0.0
    assert h.wheel_source == "encoder" and h.tag_before == (None, None)
    assert h.old_seqs == []


def test_hooked_lanes_are_told_apart_by_identity_and_keep_their_own_lists() -> None:
    ace = fakes.FakeAce2k()
    ff = fakes.FakeFeed(fakes.FakeReactor(), filament_ch=(1, 0))
    a, b = common.Hooked(ff, 1, ace, 0), common.Hooked(ff, 1, ace, 0)
    assert a != b and b not in {a}
    a.old_seqs.append(7)
    assert b.old_seqs == []


def test_a_head_of_one_lane_has_it_and_is_not_shared() -> None:
    h = hooked()
    slot = common.Slot(0, [h])
    assert slot.current is h and not slot.shared


def test_a_head_of_several_lanes_has_none_known_and_is_shared() -> None:
    slot = common.Slot(0, [hooked(0), hooked(1)])
    assert slot.current is None and slot.shared


def test_a_route_reads_each_hooked_channels_current_lane() -> None:
    h = hooked()
    shared = common.Slot(1, [hooked(1), hooked(2)])
    route = common.Route({0: common.Slot(0, [h]), 1: shared})
    assert route.get(0) is h and route.get(1) is None and route.get(2) is None
    assert [c for c in range(3) if c in route] == [0, 1]


def test_taut_needs_two_distinct_fresh_reports_reading_pushed() -> None:
    w = common.TautWatch(fresh_at_s=1.0, start_reports=5)
    assert not w.fresh(0.5, 6)  # before the fresh time
    assert not w.fresh(1.5, 5)  # not newer than the start
    assert w.fresh(1.5, 6) and not w.taut(True)
    assert not w.fresh(1.6, 6)  # the same report again tells nothing
    assert w.fresh(1.7, 7) and w.taut(True)


def test_a_release_resets_the_count() -> None:
    w = common.TautWatch(fresh_at_s=0.0, start_reports=0)
    assert w.fresh(1.0, 1) and not w.taut(True)
    assert w.fresh(1.1, 2) and not w.taut(False)
    assert w.fresh(1.2, 3) and not w.taut(True)


def test_a_stale_time_resets_the_count() -> None:
    w = common.TautWatch(fresh_at_s=2.0, start_reports=0)
    w.count = 1
    assert not w.fresh(1.0, 9) and w.count == 0


def test_the_same_report_again_keeps_the_count() -> None:
    w = common.TautWatch(fresh_at_s=0.0, start_reports=0)
    assert w.fresh(1.0, 1) and not w.taut(True)
    assert not w.fresh(1.1, 1) and w.count == 1


def test_the_start_is_taken_from_the_first_report_when_unknown() -> None:
    w = common.TautWatch(fresh_at_s=0.0, start_reports=None)
    assert not w.fresh(1.0, 4)  # first read sets the start
    assert w.fresh(1.1, 5)


def test_the_readings_needed_are_two_by_default() -> None:
    assert common.TautWatch(0.0, 0).readings == common.TAUT_READINGS
    w = common.TautWatch(fresh_at_s=0.0, start_reports=0, readings=1)
    assert w.fresh(1.0, 1) and w.taut(True)


def test_the_host_callbacks_default_to_a_printer_with_nothing_pending() -> None:
    cb = common.HostCallbacks()
    assert cb.gcode is None and cb.lookup is None and cb.probe is None
    assert cb.command_error is RuntimeError
    assert cb.swap_pending() is False and cb.swap_slot() is None
    assert cb.refill_pending(hooked()) is False


UNIT_WAIT_S = 42.0  # what the unit's move_wait_s answers in these tests


def waited(
    api_version: int, wait_s: object = None, reverse: bool = False
) -> tuple[float, fakes.FakeFeedApi]:
    """unit_move_wait_s of a 100 mm move at 50 mm/s on lane 2 of a unit at api_version; wait_s:
    what the unit's move_wait_s returns (an exception: raised); reverse: a roll back's."""
    unit = fakes.FakeAce2k()
    api = unit.feed
    api.API_VERSION = api_version
    if isinstance(wait_s, Exception):

        def refuse(_lane: int, _length: float, _speed: float, **_kw: bool) -> float:
            raise wait_s

        api.move_wait_s = refuse
    else:
        api.wait_s = wait_s
    return common.unit_move_wait_s(hooked(1, unit), 100.0, 50.0, 3.0, reverse=reverse), api


def test_a_unit_with_host_interface_17_gives_the_moves_wait() -> None:
    """The budget of ace2k for the move (feed.move_wait_s), the one its G-codes use."""
    wait, api = waited(common.MOVE_WAIT_API, UNIT_WAIT_S)
    assert wait == UNIT_WAIT_S and api.waits == [(1, 100.0, 50.0)]


def test_an_older_unit_or_a_refused_estimate_is_twice_the_travel_time_and_the_margin() -> None:
    plain = common.move_wait_s(100.0, 50.0, 3.0)
    wait, api = waited(common.MOVE_WAIT_API - 1, UNIT_WAIT_S)
    assert wait == plain and api.waits == []
    assert waited(common.MOVE_WAIT_API, ValueError("no estimate"))[0] == plain
    assert waited(common.MOVE_WAIT_API, RuntimeError("no feed"))[0] == plain


def test_a_roll_backs_wait_on_host_interface_17_counts_the_units_tail() -> None:
    """With reverse, ace2k's budget counts the tail the unit runs after a roll back."""
    wait, api = waited(common.MOVE_WAIT_API, reverse=True)
    assert api.waits == api.reverse_waits == [(1, 100.0, 50.0)]
    assert wait == api.move_wait_s(1, 100.0, 50.0, reverse=True)
    assert wait > waited(common.MOVE_WAIT_API)[0]
    assert waited(common.MOVE_WAIT_API)[1].reverse_waits == []  # without it, as before


def test_a_roll_backs_wait_on_an_older_unit_is_the_plain_formula() -> None:
    plain = common.move_wait_s(100.0, 50.0, 3.0)
    wait, api = waited(common.MOVE_WAIT_API - 1, reverse=True)
    assert wait == plain and api.waits == []
    assert waited(common.MOVE_WAIT_API, ValueError("no estimate"), reverse=True)[0] == plain


PUSH_BUDGET_S = 35.0  # the fake's budget of a 1000 mm push at 50 mm/s: 1.5 × 20 s + 5 s
PUSH_OLD_WAIT_S = 45.0  # the old formula's wait of it: 2 × 20 s + 5 s


def test_a_push_on_host_interface_17_waits_no_less_than_the_old_formula() -> None:
    """1000 mm at 50 mm/s: ace2k's budget (the fake's 35 s) is shorter than the old formula's
    45 s, so the old 45 s stays."""
    unit = fakes.FakeAce2k()
    api = unit.feed
    api.API_VERSION = common.MOVE_WAIT_API
    assert api.move_wait_s(1, 1000.0, 50.0) == PUSH_BUDGET_S
    assert common.unit_move_wait_s(hooked(1, unit), 1000.0, 50.0) == PUSH_OLD_WAIT_S
    assert api.waits == [(1, 1000.0, 50.0), (1, 1000.0, 50.0)]


@pytest.mark.parametrize(
    "length, speed",
    [(100.0, 50.0), (1000.0, 50.0), (950.0, 70.0), (10.0, 9.0)],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_a_moves_wait_on_host_interface_17_is_never_below_either_term(
    length: float, speed: float, reverse: bool
) -> None:
    """The wait is the longer of the old formula (with the caller's margin) and ace2k's budget
    (with the tail for a roll back): never shorter than before, and never shorter than ace2k's."""
    unit = fakes.FakeAce2k()
    api = unit.feed
    api.API_VERSION = common.MOVE_WAIT_API
    plain = common.move_wait_s(length, speed, 3.0)
    budget = api.move_wait_s(1, length, speed, reverse=reverse)
    wait = common.unit_move_wait_s(hooked(1, unit), length, speed, 3.0, reverse=reverse)
    assert wait >= plain and wait >= budget
    assert wait == max(plain, budget)


@pytest.mark.parametrize(
    ("speed", "restart_mm", "low", "high", "window"),
    [
        (10.0, 4.443, 50, 2000, 600),  # about 6 mm of motor: floor 7.4 mm/s, 74 %
        (30.0, 4.443, 50, 2000, 200),
        (50.0, 4.443, 50, 2000, 120),  # floor 37.0 mm/s, 74 %
        (9.0, 4.443, 50, 2000, 667),
        (200.0, 4.443, 50, 2000, 50),  # 30 ms for 6 mm: the unit's least window
        (10.0, 7.405, 50, 2000, 988),  # held_mm 6: the floor's 75 % sets it, not the 6 mm
        (10.0, 4.443, 50, 500, 500),  # the unit's greatest window
    ],
)
def test_the_held_window_is_about_6_mm_of_motor_with_the_floor_at_75_percent(
    speed: float, restart_mm: float, low: int, high: int, window: int
) -> None:
    h = hooked()
    h.api.held_restart_mm, h.api.held_ms_min, h.api.held_ms_max = restart_mm, low, high
    assert common.held_window_ms(h, speed) == window


def test_a_window_inside_the_bounds_keeps_the_floor_at_or_under_75_percent() -> None:
    h = hooked()
    for speed in (9.0, 10.0, 15.0, 30.0, 50.0, 70.0, 100.0):
        window = common.held_window_ms(h, speed)
        assert window is not None
        assert h.api.held_restart_mm * 1000.0 / window <= common.HELD_FLOOR_SHARE * speed + 1e-9
        assert window * speed / 1000.0 >= common.HELD_PULL_MM - 0.01  # never under 6 mm


def test_a_unit_that_publishes_no_window_limits_gets_no_window() -> None:
    h = hooked()
    h.api.held_restart_mm = None
    assert common.held_window_ms(h, 10.0) is None


def stop_api(restart_mm: float, stop: bool = True) -> fakes.FakeFeedApi:
    """A unit's feed with the stop on the plunger (or not) and the given window restart."""
    api = hooked().api
    api.has_stop_when = stop
    api.held_restart_mm = restart_mm
    return api


def test_a_held_mm_whose_window_clamps_past_75_percent_is_a_problem() -> None:
    """held_mm 19 (13 counts, 19.253 mm) at 10 mm/s needs a window of 2 567 ms for 75 %; the
    unit's longest, 2 000 ms, lets friction end a move held below 9.6 mm/s."""
    assert common.held_floor_problem(stop_api(19.253), 10.0) == (
        "held_mm: at 10 mm/s its held window (2000 ms) lets friction end a move held below"
        " 9.6 mm/s, more than 75 % of the speed; keep held_mm at 14.8 mm or less"
    )


def test_the_held_mm_limit_at_the_least_speed_is_about_13_3_mm() -> None:
    assert common.held_floor_problem(stop_api(13.329), 9.0) is None  # nine counts: within 75 %
    why = common.held_floor_problem(stop_api(14.81), 9.0)  # ten counts
    assert why is not None and why.endswith("keep held_mm at 13.3 mm or less")


@pytest.mark.parametrize(
    ("restart_mm", "stop"), [(4.443, True), (19.253, False)], ids=["default", "no_stop"]
)
def test_a_window_within_75_percent_or_a_unit_without_the_stop_is_no_problem(
    restart_mm: float, stop: bool
) -> None:
    assert common.held_floor_problem(stop_api(restart_mm, stop), 9.0) is None


def test_a_fresh_lane_is_a_newer_report_after_the_minimum() -> None:
    reactor = fakes.FakeReactor()
    t0 = reactor.now
    reads = iter([({"n": 1}, 5), ({"n": 2}, 6), ({"n": 3}, 6), ({"n": 4}, 7)])
    wait = common.FreshWait(min_s=0.375, max_s=2.0, poll_s=0.125)
    # The report newer than the start at 0.25 s is too early; the next read, at 0.375 s, takes it.
    assert common.wait_fresh_lane(reactor, lambda: next(reads), 5, wait) == {"n": 3}
    assert reactor.now - t0 == 0.375


def test_a_fresh_lane_wait_gives_up_at_its_bound_or_when_read_gives_up() -> None:
    reactor = fakes.FakeReactor()
    t0 = reactor.now
    wait = common.FreshWait(min_s=0.1, max_s=1.0, poll_s=0.25)
    assert common.wait_fresh_lane(reactor, lambda: ({}, None), 3, wait) is None
    assert reactor.now - t0 == 1.0
    assert common.wait_fresh_lane(reactor, lambda: None, None, wait) is None
    # With no start count, the first read past the minimum is fresh.
    assert common.wait_fresh_lane(reactor, lambda: ({"n": 1}, None), None, wait) == {"n": 1}


def test_a_lanes_tag_state_is_none_with_no_tag() -> None:
    assert common.tag_state({"tag": {"state": "read"}}) == "read"
    assert common.tag_state({"tag": None}) is None
    assert common.tag_state({}) is None


def test_api_at_least_reads_the_feeds_version_and_zero_without_one() -> None:
    feed = type("Feed", (), {"API_VERSION": common.SPEED_API})()
    assert common.api_at_least(feed, common.SPEED_API)
    assert not common.api_at_least(feed, common.MOVE_WAIT_API)
    assert not common.api_at_least(object(), common.API_MIN)
    assert not common.api_at_least(None, 1)
    assert common.API_MIN < common.SPEED_API < common.MOVE_WAIT_API < common.HOLD_WHY_API
