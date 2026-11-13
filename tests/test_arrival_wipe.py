"""arrival_wipe: one more wipe on the pads at the end of each swap's arrival.

The return runs the U1's pre-extrusion as always. Its macro (INNER_PREEXTRUDE_FILAMENT) goes to
the cleaning area with MOVE_TO_DISCARD_FILAMENT_POSITION, which calls the U1's idle exit
MOVE_TO_XY_IDLE_POSITION_EXTRUDER with X_OFFSET and Y_OFFSET; it cuts, wipes, drops the purge in
the bin, then calls the idle exit with no offset. With arrival_wipe, the adapter's wrap of the
idle exit runs the U1's wipe once more just before that last call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import fakes
import pytest
import test_shared_runout
from test_tools import swap_rig, tool
from test_units import Gcmd

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import SimpleNamespace

INNER = "INNER_PREEXTRUDE_FILAMENT"
PREEXTRUDE = "SM_PRINT_PREEXTRUDE_FILAMENT"
IDLE = "MOVE_TO_XY_IDLE_POSITION_EXTRUDER"
WIPE = "INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=4"
IDLE_RAN = "<the U1's idle exit>"
# The U1's macro, as the rig plays it: its move to the cleaning area, the cut, its wipe, the bin
# drop; then the bare idle exit (IDLE_RAN when the U1's handler runs).
MACRO = [
    "MOVE_TO_DISCARD_FILAMENT_POSITION",
    "INNER_CUTOFF_BASE_DISCARD",
    WIPE,
    "INNER_DISCARD_FILAMENT_BASE_DISCARD",
]
SWAP_PRE = f"{PREEXTRUDE} INDEX=2 FORCE=1 LENGTH=0"
SWAP_END = "RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0"


def idle(rig: SimpleNamespace, **params: str) -> None:
    """The U1's macro calls its idle exit, through the G-code table (where the wrap sits)."""
    rig.gcode.commands[IDLE](Gcmd(dict(params), IDLE))


def u1_plays_its_macro(
    rig: SimpleNamespace, before_idle: Callable[[SimpleNamespace], None] | None = None
) -> None:
    """Every pre-extrusion sent plays the U1's macro: its move to the cleaning area (the idle
    exit with offsets), cut, wipe, bin drop, then the bare idle exit. before_idle(rig) runs just
    before the bare idle exit."""
    real = rig.gcode.run_script_from_command
    rig.gcode.commands_orig[IDLE] = lambda gcmd: rig.sent.append(
        f"{IDLE_RAN} offset" if gcmd.params else IDLE_RAN
    )

    def run_script(script: str) -> None:
        real(script)
        words = script.split()
        if not words or words[0] not in (PREEXTRUDE, INNER):
            return
        rig.sent.append(MACRO[0])
        idle(rig, X_OFFSET="3.5", Y_OFFSET="50.5")
        rig.sent.extend(MACRO[1:])
        if before_idle is not None:
            before_idle(rig)
        idle(rig)

    rig.gcode.run_script_from_command = run_script


def after(sent: list[str], first: str, last: str) -> list[str]:
    start = sent.index(first)
    return sent[start : sent.index(last, start) + 1]


def swapped(
    tmp_path: Path, wipe: bool, before_idle: Callable[[SimpleNamespace], None] | None = None
) -> SimpleNamespace:
    rig, a = swap_rig(tmp_path)
    rig.module.swap_cfg.arrival_wipe = wipe
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    u1_plays_its_macro(rig, before_idle)
    tool(rig, 2)
    return rig


def test_with_arrival_wipe_the_arrival_wipes_once_more_before_the_idle_position(
    tmp_path: Path,
) -> None:
    rig = swapped(tmp_path, wipe=True)
    assert (
        after(rig.sent, SWAP_PRE, SWAP_END)
        == [
            SWAP_PRE,
            MACRO[0],
            f"{IDLE_RAN} offset",  # the move to the cleaning area: no wipe there
            *MACRO[1:],
            WIPE,  # the one more wipe, after the bin drop
            IDLE_RAN,
            SWAP_END,
        ]
    )
    # the U1's own command ran as today, and recorded the tool
    assert rig.print_stats._config["print_job"]["preextrude_filament"][2] is True
    assert rig.module.swapper.arrival is None


def test_without_arrival_wipe_the_arrival_is_as_today(tmp_path: Path) -> None:
    rig = swapped(tmp_path, wipe=False)
    assert after(rig.sent, SWAP_PRE, SWAP_END) == [
        SWAP_PRE,
        MACRO[0],
        f"{IDLE_RAN} offset",
        *MACRO[1:],
        IDLE_RAN,
        SWAP_END,
    ]


def test_an_idle_exit_outside_a_return_is_the_u1s(tmp_path: Path) -> None:
    rig, _a = swap_rig(tmp_path)
    before = list(rig.sent)
    idle(rig)
    assert rig.sent == before
    assert len(rig.gcode.orig_calls(IDLE)) == 1


def test_the_wipe_runs_once_per_return(tmp_path: Path) -> None:
    """A second bare idle exit in the same return wipes no more."""
    rig = swapped(tmp_path, wipe=True, before_idle=idle)
    assert after(rig.sent, SWAP_PRE, SWAP_END).count(WIPE) == 2  # the macro's own, and one more


def test_an_idle_exit_of_another_head_does_not_take_the_wipe(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Keyed to the return's head: a bare idle exit while another head is active passes, says
    why once, and the return's own idle exit still wipes."""
    caplog.set_level("INFO")

    def other_head_first(rig: SimpleNamespace) -> None:
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder1")
        idle(rig)
        idle(rig)
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")

    rig = swapped(tmp_path, wipe=True, before_idle=other_head_first)
    assert after(rig.sent, SWAP_PRE, SWAP_END)[-5:] == [
        IDLE_RAN,
        IDLE_RAN,
        WIPE,
        IDLE_RAN,
        SWAP_END,
    ]
    said = [
        r.getMessage() for r in caplog.records if "no wipe before this idle exit" in r.getMessage()
    ]
    assert said == [
        "ace2k_u1: head 1: no wipe before this idle exit: the active extruder is extruder1"
    ]


def test_a_return_that_fails_drops_its_mark(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        if script.startswith(PREEXTRUDE):
            raise RuntimeError("heater fault")
        real(script)

    rig.gcode.run_script_from_command = run_script
    assert tool(rig, 2) == []
    assert rig.module.swapper.swap_pending.step == "return"
    assert rig.module.swapper.arrival is None


def test_a_refill_arrival_wipes_once_more_too(tmp_path: Path) -> None:
    rig = test_shared_runout.refilled_with_no_plan(tmp_path, u1_plays_its_macro)
    pre = "INNER_PREEXTRUDE_FILAMENT SOFT=0 TEMP=230 LENGTH=0"
    assert after(rig.sent, pre, IDLE_RAN)[-3:] == [MACRO[-1], WIPE, IDLE_RAN]
