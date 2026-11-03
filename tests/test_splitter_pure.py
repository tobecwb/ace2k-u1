"""ACEK_SPLITTER_MEASURE's pure parts: the search on A's roll back, and the pairs of a head."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from ace2k_u1_splitter import PairError, SearchError, pick_pairs, search


def splitter_at(point: float, tried: list[float]) -> Callable[[float], bool]:
    """A test that passes once A is rolled back at least point mm; it records each x."""

    def test(x: float) -> bool:
        tried.append(x)
        return x >= point

    return test


@pytest.mark.parametrize("point", [37.0, 612.0, 862.0, 899.0, 1400.0, 1795.0])
def test_the_search_finds_the_point_within_the_step(point: float) -> None:
    tried: list[float] = []
    found = search(splitter_at(point, tried), 0.0, 1800.0, 900.0, 5.0)
    assert point <= found < point + 5.0
    assert tried[0] == 900.0  # the value in effect first


def test_a_first_try_that_blocks_tries_the_bound_next() -> None:
    tried: list[float] = []
    search(splitter_at(1200.0, tried), 0.0, 1800.0, 900.0, 5.0)
    assert tried[:2] == [900.0, 1800.0]


def test_the_bound_blocked_is_an_error() -> None:
    tried: list[float] = []
    with pytest.raises(SearchError, match="still blocked at 1800 mm"):
        search(splitter_at(5000.0, tried), 0.0, 1800.0, 900.0, 5.0)
    assert tried == [900.0, 1800.0]


def test_a_first_try_past_the_bound_is_clamped_to_it() -> None:
    tried: list[float] = []
    search(splitter_at(300.0, tried), 0.0, 600.0, 950.0, 5.0)
    assert tried[0] == 600.0


def test_no_room_is_an_error() -> None:
    with pytest.raises(SearchError, match="no room"):
        search(lambda x: True, 0.0, 0.0, 900.0, 5.0)


def test_the_search_takes_about_ten_rounds() -> None:
    tried: list[float] = []
    search(splitter_at(862.0, tried), 0.0, 1800.0, 900.0, 5.0)
    assert len(tried) <= 11


UNIT_A, UNIT_B = object(), object()


def lane(unit: object, name: str, index: int) -> SimpleNamespace:
    return SimpleNamespace(unit=unit, lane=index, text=name)


def text(h: SimpleNamespace) -> str:
    return h.text


def test_one_unit_gives_one_pair_of_its_two_lowest_loaded_lanes() -> None:
    a1, a2, a3, a4 = (lane(UNIT_A, f"a:{i + 1}", i) for i in range(4))
    pairs = pick_pairs([a1, a2, a3, a4], lambda h: h is not a1, text)
    assert pairs == [(a2, a3)]


def test_two_units_give_one_pair_each() -> None:
    a1, a2 = lane(UNIT_A, "a:1", 0), lane(UNIT_A, "a:2", 1)
    b1, b2 = lane(UNIT_B, "b:1", 0), lane(UNIT_B, "b:2", 1)
    assert pick_pairs([a1, a2, b1, b2], lambda h: True, text) == [(a1, a2), (b1, b2)]


def test_a_unit_with_one_loaded_lane_takes_b_from_another_unit() -> None:
    a1, a2, b1 = lane(UNIT_A, "a:1", 0), lane(UNIT_A, "a:2", 1), lane(UNIT_B, "b:1", 0)
    assert pick_pairs([a1, a2, b1], lambda h: True, text) == [(a1, a2), (b1, a1)]


def test_one_loaded_lane_on_the_whole_head_is_refused() -> None:
    a1, a2 = lane(UNIT_A, "a:1", 0), lane(UNIT_A, "a:2", 1)
    with pytest.raises(PairError, match="a:1 is the only lane with filament: load a second one"):
        pick_pairs([a1, a2], lambda h: h is a1, text)


def test_a_unit_with_no_loaded_lane_is_refused() -> None:
    a1, a2, b1 = lane(UNIT_A, "a:1", 0), lane(UNIT_A, "a:2", 1), lane(UNIT_B, "b:1", 0)
    with pytest.raises(PairError, match="no lane of b:1's unit has filament: load one"):
        pick_pairs([a1, a2, b1], lambda h: h is not b1, text)


def test_the_fallback_b_follows_the_order_of_lanes_not_the_lane_number() -> None:
    unit_c = object()
    a1, c4, b2 = lane(UNIT_A, "a:1", 0), lane(unit_c, "c:4", 3), lane(UNIT_B, "b:2", 1)
    pairs = pick_pairs([a1, c4, b2], lambda h: True, text)
    assert pairs == [(a1, c4), (c4, a1), (b2, a1)]


def test_loaded_is_read_once_per_lane() -> None:
    a1, a2, b1, b2 = (
        lane("a", "a:1", 0),
        lane("a", "a:2", 1),
        lane("b", "b:1", 0),
        lane("b", "b:2", 1),
    )
    calls: list[str] = []

    def loaded(h: SimpleNamespace) -> bool:
        calls.append(h.text)
        return True

    pick_pairs([a1, a2, b1, b2], loaded, text)
    assert sorted(calls) == ["a:1", "a:2", "b:1", "b:2"]
