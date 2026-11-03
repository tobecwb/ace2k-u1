"""ACEK_FREE_CHECK: the swap's free check on the lane in a shared head, outside a print, for the
bench."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest
from ace2k_u1_swap import SwapState
from test_probe import end, modes, probe_rig, reports_tick
from test_tools import run

if TYPE_CHECKING:
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked, Slot
    from fakes import FakeAce2k, FakeMove


def started(a: FakeAce2k) -> list[tuple[int, str, float, float]]:
    return [(lane, mode, length, speed) for lane, mode, length, speed, _m in a.feed.started]


def check_rig(tmp_path: Path, current: int = 0) -> tuple[SimpleNamespace, FakeAce2k, Slot]:
    """probe_rig with lane current (0-based, of a) named in head 1."""
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[current])
    return rig, a, slot


def held_lane(rig: SimpleNamespace, a: FakeAce2k, lane: int, mm: float | None) -> None:
    """lane's roll back goes taut; stopped, its final event carries mm (None: no event)."""
    opened = {}

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[lane]["pushed"] = True  # the gear holds it: its plunger goes taut
            opened[move.lane] = move
        else:
            a.lanes[lane]["pushed"] = False
            end(move, mm)  # the feed back runs its whole length

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(n: int) -> None:
        stop(n)
        move = opened.pop(n, None)
        if move is not None and mm is not None:
            end(move, -mm, kind="stopped")

    a.feed.stop = stopped
    reports_tick(rig, a)


def test_a_held_lane_is_said_held_and_fed_back_what_it_rolled_back(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)
    held_lane(rig, a, 0, 31.5)
    gcmd = run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "1", "LENGTH": "100", "SPEED": "30"})
    assert started(a) == [(0, "rollback", 100.0, 30.0), (0, "feed", 31.5, 50.0)]
    assert gcmd.lines == [
        "ace2k_u1: a:1 free check, 100 mm at 30 mm/s: held by head 1, taut after 31.5 mm; fed back"
    ]


def test_a_held_lane_with_no_distance_is_not_fed_back(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)
    held_lane(rig, a, 0, None)  # the stopped move's final event never comes
    gcmd = run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "1", "SPEED": "30"})
    assert modes(a) == [(0, "rollback")]
    assert gcmd.lines == [
        "ace2k_u1: a:1 free check, 100 mm at 30 mm/s: held by head 1, taut"
        " (no distance reported; not fed back)"
    ]


def test_a_free_lane_is_said_free_with_its_distance(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path, current=1)
    a.feed.on_start = lambda move: end(move, -100.0)
    gcmd = run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "2", "SPEED": "50"})
    # fed back what it rolled back, at probe_speed: the lane is where it was
    assert started(a) == [(1, "rollback", 100.0, 50.0), (1, "feed", 100.0, 50.0)]
    assert gcmd.lines == [
        "ace2k_u1: a:2 free check, 100 mm at 50 mm/s: free, 100.0 mm rolled back; fed back"
    ]


def test_a_free_roll_back_that_ran_on_with_its_tail_says_its_whole_distance(tmp_path: Path) -> None:
    """The roll back passed its deadline with the plunger at rest and went on with a tail move:
    its final event carries the whole roll back, 130 mm, and the line says so. The feed back is
    those 130 mm, not the check's length: the lane is where it was."""
    rig, a, _slot = check_rig(tmp_path, current=1)
    a.feed.on_start = lambda move: end(move, -130.0 if move.mode == "rollback" else 130.0)
    gcmd = run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "2", "SPEED": "50"})
    assert started(a) == [(1, "rollback", 100.0, 50.0), (1, "feed", 130.0, 50.0)]
    assert gcmd.lines == [
        "ace2k_u1: a:2 free check, 100 mm at 50 mm/s: free, 130.0 mm rolled back; fed back"
    ]


def test_the_heads_other_lanes_are_stopped_and_their_holds_given_back(tmp_path: Path) -> None:
    rig, a, slot = check_rig(tmp_path)
    seen = []
    a.feed.on_start = lambda move: (seen.append([x.hold for x in slot.lanes]), end(move, -100.0))
    run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "1"})
    assert seen == [[True, True], [True, True]]  # both held while a:1 moves, back included
    assert ("stop", 1) in a.feed.log  # a:2 stopped
    assert [x.hold for x in slot.lanes] == [False, False]


def test_without_length_and_speed_the_swaps_own_are_used(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)
    a.feed.on_start = lambda move: end(move, -100.0)
    run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "1"})
    assert started(a) == [(0, "rollback", 100.0, 50.0), (0, "feed", 100.0, 50.0)]


def test_the_speed_is_kept_within_the_units_bounds(tmp_path: Path) -> None:
    rig, a, slot = check_rig(tmp_path)
    slot.lanes[0].api.speed_min = 12.0
    slot.lanes[0].api.speed_max = 40.0
    a.feed.on_start = lambda move: end(move, -100.0)
    gcmd = run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "1", "SPEED": "5"})
    # the roll back and the feed back (probe_speed, 50) both within the unit's bounds
    assert started(a) == [(0, "rollback", 100.0, 12.0), (0, "feed", 100.0, 40.0)]
    assert "at 12 mm/s" in gcmd.lines[0]


@pytest.mark.parametrize("armed", [True, False])
def test_a_follow_the_check_stopped_is_armed_again(tmp_path: Path, armed: bool) -> None:
    rig, a, slot = check_rig(tmp_path)
    slot.lanes[0].armed = armed
    armed_again = []
    rig.adapter.follow.arm = lambda h, *args, **kwargs: armed_again.append(h)
    a.feed.on_start = lambda move: end(move, -100.0)
    run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "1"})
    assert armed_again == ([slot.lanes[0]] if armed else [])


def test_refused_with_the_heads_sensor_unread(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)

    def unread(h: Hooked) -> NoReturn:
        raise RuntimeError("no switch")

    rig.adapter.head_set = unread
    refused(rig, a, 1, "head 1's sensor cannot be read")


def test_refused_with_the_lanes_state_unread(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)

    def unread(h: Hooked, eventtime: float | None = None) -> NoReturn:
        raise RuntimeError("no report")

    rig.adapter.lane_status = unread
    refused(rig, a, 1, "the lane's state cannot be read")


def refused(rig: SimpleNamespace, a: FakeAce2k, lane: int, reason: str) -> None:
    with pytest.raises(ValueError, match=f"lane a:{lane} free check refused: {reason}"):
        run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": str(lane)})
    assert modes(a) == []


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_refused_during_a_print(tmp_path: Path, state: str) -> None:
    rig, a, _slot = check_rig(tmp_path)
    rig.print_stats.state = state
    refused(rig, a, 1, "a print is printing or paused")


def test_refused_with_a_swap_pending(tmp_path: Path) -> None:
    rig, a, slot = check_rig(tmp_path)
    rig.module.swapper.swap_pending = SwapState(
        slot, slot.current, slot.lanes[-1], None, step="free"
    )
    refused(rig, a, 1, "a swap is pending")


def test_refused_for_a_lane_not_hooked(tmp_path: Path) -> None:
    rig, a, slot = check_rig(tmp_path)
    keys = [k for k, v in rig.adapter.by_key.items() if v is slot.lanes[0]]
    for k in keys:
        del rig.adapter.by_key[k]
    refused(rig, a, 1, "the lane is not hooked")


def test_refused_for_a_lane_alone_on_its_head(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)  # a:3 alone feeds head 2
    refused(rig, a, 3, "its head is fed by one lane alone")


def test_refused_for_a_lane_not_in_its_head(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path, current=1)
    refused(rig, a, 1, "it is not the lane in head 1: name it with ACEK_LANE_ACTIVE first")


def test_refused_with_the_heads_sensor_empty(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)
    rig.head_sensor.detected = False
    refused(rig, a, 1, "head 1's sensor sees no filament: nothing to check")


def test_refused_with_the_bay_empty(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)
    a.lanes[0]["insert"] = False
    refused(rig, a, 1, "its bay is empty")


def test_refused_with_no_shared_head_hooked(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)
    rig.module.probe = None
    refused(rig, a, 1, "no head fed by several lanes is hooked")


def test_another_end_is_the_commands_error(tmp_path: Path) -> None:
    rig, a, _slot = check_rig(tmp_path)
    a.feed.on_start = lambda move: end(move, -3.0, kind="stuck")
    with pytest.raises(ValueError, match="free check ended stuck"):
        run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "1"})


def test_a_roll_back_reported_by_its_motor_alone_is_fed_back_at_most_its_length_and_tail(
    tmp_path: Path,
) -> None:
    """With no filament figure the distance is the motor's, which may be far more than the
    filament went: the feed back stops at the length asked plus the unit's tail (100 + 100)."""
    rig, a, _slot = check_rig(tmp_path, current=1)

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            move.event = {"kind": "done", "seq": move.seq, "motor_mm": -450.0}
        else:
            end(move, 200.0)

    a.feed.on_start = on_start
    run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "2", "SPEED": "50"})
    assert started(a) == [(1, "rollback", 100.0, 50.0), (1, "feed", 200.0, 50.0)]
