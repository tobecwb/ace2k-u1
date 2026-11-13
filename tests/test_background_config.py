"""background_heads: the heads that swap their lane while parked."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ace2k_u1_config
import fakes
import pytest
from test_check import Printer
from test_units import LanesConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ace2k_u1_config import LaneRef, SwapConfig

# Head 1 is fed by two lanes (a + group), head 2 by one.
REFS: list[LaneRef] = [(0, "a", 0), (0, "a", 1), (1, "a", 2)]


def keys(values: Mapping[str, object], refs: list[LaneRef] = REFS) -> SwapConfig:
    config = LanesConfig(Printer(fakes.FakeReactor(), {}, []), values)
    return ace2k_u1_config.read_swap_keys(config, ace2k_u1_config.lane_counts(refs))


def test_no_head_swaps_in_the_background_by_default() -> None:
    cfg = keys({})
    assert cfg.background_heads == frozenset()


def test_a_head_that_a_group_feeds_may_swap_in_the_background() -> None:
    assert keys({"background_heads": "1"}).background_heads == frozenset({0})


def test_two_group_heads_are_stored_0_based() -> None:
    refs: list[LaneRef] = [(0, "a", 0), (0, "a", 1), (3, "b", 0), (3, "b", 1)]
    cfg = keys({"background_heads": " 4, 1 "}, refs)
    assert cfg.background_heads == frozenset({0, 3})


def test_a_head_that_one_lane_feeds_stops_klipper() -> None:
    with pytest.raises(Exception, match="ace2k_u1: background_heads: head 2 is fed by one lane"):
        keys({"background_heads": "2"})


def test_a_head_that_no_lane_feeds_stops_klipper() -> None:
    with pytest.raises(Exception, match="background_heads: head 3 is fed by one lane"):
        keys({"background_heads": "3"})


@pytest.mark.parametrize("value", ["5", "0", "x", "1.5", "-1"])
def test_a_head_out_of_range_stops_klipper(value: str) -> None:
    with pytest.raises(Exception, match="background_heads: .* is not a head 1-4"):
        keys({"background_heads": value})


def test_a_head_listed_twice_stops_klipper() -> None:
    with pytest.raises(Exception, match="background_heads: head 1 is listed twice"):
        keys({"background_heads": "1,1"})


# --- background_purge_mm: the new filament purged out of the nozzle in the dock --------------


def test_the_dock_purges_10_mm_by_default() -> None:
    assert keys({}).background_purge_mm == 10.0


@pytest.mark.parametrize("value", [0.0, 2.5, 30.0, 100.0])
def test_a_purge_from_0_to_100_mm_is_kept(value: float) -> None:
    assert keys({"background_purge_mm": value}).background_purge_mm == value


@pytest.mark.parametrize("value", [-1.0, 100.5])
def test_a_purge_out_of_0_to_100_mm_stops_klipper(value: float) -> None:
    with pytest.raises(Exception, match="background_purge_mm"):
        keys({"background_purge_mm": value})


def test_the_old_prime_key_is_not_read() -> None:
    assert keys({"background_prime_mm": 5.0}).background_purge_mm == 10.0


# --- arrival_wipe: one more wipe on the pads at the end of each swap's arrival ---------------


def test_the_arrival_wipes_once_more_by_default() -> None:
    assert keys({}).arrival_wipe is True


@pytest.mark.parametrize(("value", "kept"), [("False", False), ("0", False), ("True", True)])
def test_the_arrival_wipe_is_a_boolean(value: str, kept: bool) -> None:
    assert keys({"arrival_wipe": value}).arrival_wipe is kept


def test_an_arrival_wipe_that_is_no_boolean_stops_klipper() -> None:
    with pytest.raises(Exception, match="arrival_wipe"):
        keys({"arrival_wipe": "twice"})
