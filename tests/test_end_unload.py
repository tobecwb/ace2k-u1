"""The U1's end-of-print unload takes every used head in turn and heats each one only when its
turn comes. The adapter tells every head it will unload to heat first, in its order (S7); the U1
heats two at a time and queues the rest."""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from ace2k_u1_hooks import FILAMENT_PARAMETERS
from test_tools import tools_rig
from test_units import Gcmd

NAME = "SM_PRINT_END_AUTO_UNLOAD_FILAMENT"


def end_unload(rig: SimpleNamespace) -> list[str]:
    """Runs the wrapped macro; returns what was sent, the U1's macro marked as 'U1'."""
    sent: list[str] = []
    rig.gcode.run_script_from_command = sent.append
    rig.gcode.commands_orig[NAME] = lambda gcmd: sent.append("U1")
    rig.gcode.commands[NAME](Gcmd({}, NAME))
    return sent


def unloading(rig: SimpleNamespace, temps: dict[int, object]) -> None:
    rig.module.adapter.print_end_unloads = lambda h: h in temps

    def temperature(ext: object, h: int, method: str) -> object:
        assert method == FILAMENT_PARAMETERS["load_temp"]  # the U1's load temperature is asked
        return temps.get(h)

    rig.module.u1.u1_filament = temperature


def test_every_unloading_head_heats_first_in_order(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    unloading(rig, {0: 250.0, 2: 240.0})
    assert end_unload(rig) == ["M104 S250 T0 A0", "M104 S240 T2 A0", "U1"]


def test_a_head_with_no_temperature_is_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = tools_rig(tmp_path)
    unloading(rig, {0: None, 1: 220.0})
    caplog.set_level(logging.INFO)
    assert end_unload(rig) == ["M104 S220 T1 A0", "U1"]
    assert sum("head 1: no temperature" in r.getMessage() for r in caplog.records) == 1


def test_a_failing_pre_heat_never_stops_the_unload(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)

    def broken(h: int) -> bool:
        raise RuntimeError("table gone")

    rig.module.adapter.print_end_unloads = broken
    assert end_unload(rig) == ["U1"]


def test_the_wrap_is_there_with_one_lane_heads(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1, a:2, a:3, a:4")
    assert rig.gcode.commands[NAME].__name__ == "end_unload"


def test_a_failing_heat_command_never_stops_the_unload(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = tools_rig(tmp_path)
    unloading(rig, {0: 250.0, 2: 240.0})

    def broken(script: str, timer: bool = False) -> None:
        raise RuntimeError("no gcode")

    rig.module.u1.script = broken
    assert end_unload(rig) == ["U1"]
    assert any("pre-heat" in r.getMessage() for r in caplog.records)
