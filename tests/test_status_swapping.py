"""The status's swapping: the heads whose lane changes now, for the page's printing view."""

from __future__ import annotations

from typing import TYPE_CHECKING

import fakes
from test_background import bg_rig, finished, started
from test_tools import planned, swap_rig, tool

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace


def spy(rig: SimpleNamespace) -> list[list[int]]:
    """Records the status's swapping at each G-code the swap runs."""
    seen: list[list[int]] = []
    run = rig.gcode.run_script_from_command

    def record(script: str) -> None:
        seen.append(list(rig.module.get_status()["swapping"]))
        run(script)

    rig.gcode.run_script_from_command = record
    return seen


def test_no_head_swaps_at_rest(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    assert rig.module.get_status()["swapping"] == []


def test_an_inline_swap_lists_its_head_while_it_runs(tmp_path: Path) -> None:
    rig, _ = swap_rig(tmp_path)
    seen = spy(rig)
    tool(rig, 2)  # head 1 swaps from a:1 to a:2
    assert seen and all(heads == [0] for heads in seen)
    assert rig.module.get_status()["swapping"] == []


def test_a_running_background_swap_lists_its_head(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    started(rig)
    assert rig.module.get_status()["swapping"] == [0]  # it runs while head 2 prints


def test_a_finished_background_swap_lists_its_head_until_its_t_ends(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    assert rig.module.get_status()["swapping"] == [0]  # finished, it waits for its T
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
    seen = spy(rig)
    tool(rig, 2)  # the arrival: the head heats and waits, then the spool and the return
    assert seen and all(heads == [0] for heads in seen)
    assert rig.module.get_status()["swapping"] == []
