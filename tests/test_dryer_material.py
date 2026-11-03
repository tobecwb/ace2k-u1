"""The dryer's profile reads Edit spool's materials, and the host's table equals the presets."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import ace2k_dryer_mode  # ace2k's extras are on the path for these tests (conftest.py)
import fakes
from test_tools import run
from test_units import make_rig

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import SimpleNamespace

    import pytest
    from ace2k_u1_common import Hooked
    from ace2k_u1_plan import LaneSpool
    from ace2k_u1_spools import UnitLanes

ROOT = Path(__file__).resolve().parents[1]


class FakeDryerMode:
    """The dryer_mode of a unit, recording the source it is given."""

    def __init__(self) -> None:
        self.source: Callable[[int], str | None] | None = None
        self.calls = 0

    def set_material_source(self, fn: Callable[[int], str | None] | None) -> None:
        self.source = fn
        self.calls += 1


def rig_with_modes(
    with_modes: bool,
) -> tuple[SimpleNamespace, dict[str, fakes.FakeAce2k], dict[str, FakeDryerMode]]:
    """The rig, its units, and the dryer_mode given to each unit (none without the modes)."""
    units, by_name = fakes.two_units()
    modes: dict[str, FakeDryerMode] = {}
    if with_modes:
        for name, unit in by_name.items():
            modes[name] = vars(unit)["dryer_mode"] = FakeDryerMode()
    return make_rig("a:1, a:2, b:1", units, by_name), by_name, modes


def test_the_hosts_table_equals_the_pages_presets() -> None:
    presets = json.loads((ROOT / "web" / "presets.json").read_text())
    page = {(p["material"], p["temp"], p["hours"]) for p in presets}
    host = {(m, t, ace2k_dryer_mode.PROFILE_MINUTES // 60) for m, t in ace2k_dryer_mode.PROFILES}
    assert page == host


def test_the_adapter_gives_each_unit_the_spool_material() -> None:
    rig, _units, modes = rig_with_modes(True)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PETG", "COLOR": "ffffff"})
    mode = modes["a"]
    assert mode.calls == 1
    assert mode.source is not None
    assert mode.source(0) == "PETG"
    assert mode.source(1) is None  # hooked, no record: left to the tag
    assert mode.source(3) is None  # a:4 is not hooked
    assert modes["b"].calls == 1


def test_an_older_ace2k_without_the_modes_is_skipped() -> None:
    rig, units, _modes = rig_with_modes(False)
    assert rig.adapter is not None and rig.adapter.hooked
    assert not hasattr(units["a"], "dryer_mode")


def test_an_unread_spool_is_logged_once_per_lane_until_it_reads_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig, _units, modes = rig_with_modes(True)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PETG", "COLOR": "ffffff"})
    source = modes["a"].source
    assert source is not None
    real = rig.module.spool_lanes.lane_spool
    broken = [True]

    def lane_spool(h: Hooked, units: UnitLanes | None = None) -> LaneSpool:
        if broken[0]:
            raise RuntimeError("status unread")
        return real(h, units)

    rig.module.spool_lanes.lane_spool = lane_spool
    with caplog.at_level(logging.WARNING):
        assert source(0) is None
        assert source(0) is None  # the profile asks every 5 s: said once
        assert source(1) is None  # another lane: its own line
    assert caplog.text.count("a:1's spool unread") == 1
    assert caplog.text.count("a:2's spool unread") == 1
    broken[0] = False
    assert source(0) == "PETG"  # read again: a later failure is said again
    broken[0] = True
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert source(0) is None
    assert caplog.text.count("a:1's spool unread") == 1


def test_the_other_readers_still_log_each_unread_spool(caplog: pytest.LogCaptureFixture) -> None:
    rig, _units, modes = rig_with_modes(True)
    source = modes["a"].source
    assert source is not None
    spools = rig.module.spool_lanes

    def lane_spool(h: Hooked, units: UnitLanes | None = None) -> LaneSpool:
        raise RuntimeError("status unread")

    spools.lane_spool = lane_spool
    h = rig.module.adapter.hooked[0]
    with caplog.at_level(logging.WARNING):
        assert source(h.lane) is None  # the dryer: once
        assert source(h.lane) is None
        assert spools.material(h) == ""  # a swap or a check: each time
        assert spools.material(h) == ""
    assert caplog.text.count("spool unread for its material") == 3
