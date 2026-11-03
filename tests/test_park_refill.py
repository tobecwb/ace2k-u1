"""A shared lane after the unit's own load (no grip), and the park of a lone head's refill."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from test_probe import probe_rig, reports_tick
from test_shared_runout import lone_ran_out, run_pauses

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from fakes import FakeAce2k, FakeMove


def own_load(rig: SimpleNamespace, lane: int, mm: float, kind: str = "loaded") -> None:
    """The final event of the unit's own load of lane (seq 0, no grip), mm of filament in."""
    rig.adapter.on_feed_event(
        lane, {"kind": kind, "mode": "loading", "unit": "a", "seq": 0, "filament_mm": mm}
    )


def test_a_shared_lanes_own_load_that_ended_short_is_skipped_by_the_probe(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a.lanes[0]["insert"] = True
    own_load(rig, 0, 60.0, kind="blocked")  # 60 mm in: under the probe's 100 mm roll back
    assert slot.lanes[0].at_grip

    def on_start(move: FakeMove) -> None:
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True

    a.feed.on_start = on_start
    reports_tick(rig, a)
    assert rig.module.probe.which_lane(slot) is slot.lanes[1]
    assert [lane for lane, *_ in a.feed.started] == [1]  # a:1 never moved
    assert "ace2k_u1: head 1: probe skips a:1: just put in its bay, at its grip" in rig.gcode.lines


def test_a_shared_lanes_own_load_to_the_parking_point_is_not_skipped(tmp_path: Path) -> None:
    rig, _a = probe_rig(tmp_path)
    own_load(rig, 1, 300.0)  # at the parking point: the probe rolls it back like any other
    assert not rig.adapter.slot_of_head(0).lanes[1].at_grip


def test_a_lone_heads_own_load_marks_nothing(tmp_path: Path) -> None:
    rig, _a = probe_rig(tmp_path)
    own_load(rig, 2, 60.0)  # a:3 alone in head 2: its load is the U1's, not a park
    assert not rig.adapter.slot_of_head(1).lanes[0].at_grip


def refill_waiting(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:
    """a:3, alone in head 2, ran out; its refill is pending and its bay waits for a spool."""
    rig, a, _h = lone_ran_out(tmp_path)
    run_pauses(rig)
    a.lanes[2].update(insert=False, mode="idle")
    a.feed.grips.clear()
    return rig, a


def test_a_lone_heads_pending_refill_has_the_park(tmp_path: Path) -> None:
    rig, a = refill_waiting(tmp_path)
    rig.adapter.watch(rig.reactor.now)
    assert [g for g in a.feed.grips if g[0] == 2] == [(2, "park")]


def test_a_refused_park_of_a_lone_heads_refill_grips_the_bay_instead(tmp_path: Path) -> None:
    rig, a = refill_waiting(tmp_path)

    def refuse(lane_index: int) -> NoReturn:
        raise RuntimeError("no park")

    a.feed.set_lane_park = refuse
    rig.adapter.watch(rig.reactor.now)
    assert [g for g in a.feed.grips if g[0] == 2] == [(2, 40.0)]
    h = rig.adapter.slot_of_head(1).lanes[0]
    assert h.park_refused and h.grip_live and not h.grip_park
    sent = len(a.feed.grips)
    rig.adapter.watch(rig.reactor.now)
    assert len(a.feed.grips) == sent  # not asked again every tick
