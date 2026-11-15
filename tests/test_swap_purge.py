"""The swap's purge: sized by the slicer's flush for the colour pair, else the macro's default.

The plan reads the file's flush matrix. When the tool that leaves the head and the tool that
comes in are both known, the load macro gets PURGE=<mm>: purge_fill_mm plus purge_wash_factor
times the slicer's volume for the pair over the new filament's section, capped at purge_max_mm.
When the pair is not known (a refill, a head whose last T is not on the lane that leaves, a file
with no matrix), no PURGE is passed and the macro purges its own default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fakes import SWAP_LOAD
from test_background import bg_rig, no_gcode, started
from test_light_departure import dep_rig, t1
from test_shared_runout import new_spool, ran_out
from test_shared_runout import resume as refill_resume
from test_swap_retract import rig_at
from test_tools import ends, tool

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from ace2k_u1_background import BgRun

DEFAULT = "ace2k_u1: head 1: purge: the macro's default (no colour pair known)"


def loads(rig: SimpleNamespace) -> list[str]:
    return [ln for ln in rig.sent if ln.startswith("ACEK_SWAP_LOAD")]


def purge_lines(rig: SimpleNamespace) -> list[str]:
    return [ln for ln in rig.gcode.lines if ln.startswith("ace2k_u1: head 1: purge")]


def test_a_swap_to_white_purges_the_pairs_length(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2])
    tool(rig, 2)
    assert loads(rig) == [f"{SWAP_LOAD} PURGE=47.5"]
    assert purge_lines(rig) == ["ace2k_u1: head 1: purge 47.5 mm (T0 → T2: 211 mm³)"]


def test_the_swap_back_purges_its_own_pair(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2, 0])
    tool(rig, 2)
    rig.sent.clear()
    tool(rig, 0)
    assert loads(rig) == [f"{SWAP_LOAD} PURGE=37"]
    assert purge_lines(rig)[-1] == "ace2k_u1: head 1: purge 37 mm (T2 → T0: 84 mm³)"


@pytest.mark.parametrize(
    ("key", "value", "purge"), [("purge_wash_factor", 0.0, 30), ("purge_max_mm", 40.0, 40)]
)
def test_the_purge_is_the_fill_with_no_wash_and_the_ceiling_at_most(
    tmp_path: Path, key: str, value: float, purge: int
) -> None:
    rig, _a = rig_at(tmp_path, [0, 2])
    setattr(rig.module.swap_cfg, key, value)
    tool(rig, 2)
    assert loads(rig) == [f"{SWAP_LOAD} PURGE={purge}"]


def test_a_refill_purges_the_macros_default(tmp_path: Path) -> None:
    rig, a = ran_out(tmp_path)
    a.lanes[0]["insert"] = True  # the new spool in the bay
    new_spool(rig)
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.sent.clear()
    refill_resume(rig)
    load = loads(rig)
    assert len(load) == 1 and "PURGE" not in load[0]
    assert purge_lines(rig) == [DEFAULT]


def test_a_swap_whose_head_last_ran_another_lane_purges_the_macros_default(
    tmp_path: Path,
) -> None:
    """The file's last T on head 1 is T2 (a:2), but the head holds a:1: the pair is not known."""
    rig, _a = rig_at(tmp_path, [2, 2])
    tool(rig, 2)
    load = loads(rig)
    assert len(load) == 1 and "PURGE" not in load[0]
    assert purge_lines(rig) == [DEFAULT]


def test_a_swap_with_no_matrix_purges_the_macros_default(tmp_path: Path) -> None:
    rig, _a = rig_at(tmp_path, [0, 2])
    rig.module.tracker.plan.flush = None
    tool(rig, 2)
    assert loads(rig) == [SWAP_LOAD]
    assert purge_lines(rig) == [DEFAULT]


def test_a_gripped_background_swap_passes_purge_after_grip(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    run: BgRun = started(rig)
    real = rig.bg._grip

    def grip(r: BgRun) -> bool:
        ok = real(r)
        r.stop_asked = True  # the T comes before the loaded check
        return ok

    rig.bg._grip = grip
    no_gcode(rig)
    assert run.state.gripped
    rig.sent.clear()
    tool(rig, 2)
    assert loads(rig) == [f"{SWAP_LOAD} GRIP=0 PURGE=47.5"]


def test_pair_purge_returns_the_tool_that_left_the_length_and_the_slicers_volume(
    tmp_path: Path,
) -> None:
    rig, _a = rig_at(tmp_path, [0, 2])
    tracker, cfg = rig.module.tracker, rig.module.swap_cfg
    slot = rig.adapter.slot_of_head(0)
    tracker.t_raw = 1  # the print at the T of T2
    assert tracker.pair_purge(0, slot.lanes[0], 2, cfg) == (0, 47.5, 211.0)
    assert tracker.pair_purge(0, slot.lanes[0], None, cfg) is None  # a refill: no tool
    assert tracker.pair_purge(0, slot.lanes[1], 2, cfg) is None  # T0 did not leave on a:2
    assert tracker.pair_purge(0, slot.lanes[0], 0, cfg) is None  # T0 again: no colour pair
    cfg.purge_wash_factor = 0.0
    assert tracker.pair_purge(0, slot.lanes[0], 2, cfg) == (0, 30.0, 211.0)  # the fill alone
    cfg.purge_wash_factor = 0.2
    cfg.purge_max_mm = 40.0
    assert tracker.pair_purge(0, slot.lanes[0], 2, cfg) == (0, 40.0, 211.0)  # the ceiling
    cfg.purge_max_mm = 80.0
    tracker.plan.diameters = [None] * 8
    assert tracker.pair_purge(0, slot.lanes[0], 2, cfg) == (0, 47.5, 211.0)  # default 1.75
    flush = tracker.plan.flush
    tracker.plan.flush = tuple(row[:2] for row in flush[:2])
    assert tracker.pair_purge(0, slot.lanes[0], 2, cfg) is None  # a tool outside the matrix
    tracker.plan.flush = None
    assert tracker.pair_purge(0, slot.lanes[0], 2, cfg) is None  # no matrix
    tracker.plan = None
    assert tracker.pair_purge(0, slot.lanes[0], 2, cfg) is None  # no plan


def test_a_light_arrival_purges_the_pairs_length(tmp_path: Path) -> None:
    rig = dep_rig(tmp_path)
    t1(rig)
    no_gcode(rig)
    rig.sent.clear()
    tool(rig, 2)
    assert loads(rig) == [f"{SWAP_LOAD} PURGE=47.5"]
