"""The wipe of a head in background_heads that a plain T takes from its dock.

The docks of the listed heads have an opening for the purge, so a parked head may ooze. A T with
a swap cleans its head in the return; a T with no swap gets one wipe on the pads with arrival_wipe:
the U1's move to the cleaning area, its wipe, its idle exit, in a saved G-code state.

The rig is the background rig: head 1 (index 0) is listed and parked, head 2 is active, head 1
holds a:1 (T0). The U1's T and its T<h> A0 make head h's extruder the active one.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import ace2k_u1_wraps
import fakes
import pytest
from test_background import bg_rig
from test_units import Gcmd

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

WIPE_LINES = [
    "SAVE_GCODE_STATE NAME=ACEK_DOCK_WIPE",
    "MOVE_TO_DISCARD_FILAMENT_POSITION",
    "INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=4",
    "MOVE_TO_XY_IDLE_POSITION_EXTRUDER",
    "RESTORE_GCODE_STATE NAME=ACEK_DOCK_WIPE MOVE=0",
]
SAID = "ace2k_u1: head 1: leaves its dock: one wipe"
TOOL_HEAD = {0: 0, 1: 1, 2: 0}  # the rig's map: T0 and T2 on head 1, T1 on head 2


def make_active(rig: SimpleNamespace, head: int) -> None:
    """Head's extruder becomes the active one; the homed axes stay as they were."""
    homed = rig.printer.objects["toolhead"].homed_axes
    name = rig.printer.objects["extruder_list"][head].name
    rig.printer.objects["toolhead"] = fakes.FakeToolhead(name)
    rig.printer.objects["toolhead"].homed_axes = homed


def u1_picks(rig: SimpleNamespace) -> None:
    """The U1's T<h> A0, sent as a script, makes head h's extruder the active one."""
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        real(script)
        for line in script.split("\n"):
            words = line.split()
            if len(words) == 2 and words[0][:1] == "T" and words[1] == "A0":
                make_active(rig, int(words[0][1:]))

    rig.gcode.run_script_from_command = run_script


def run_t(rig: SimpleNamespace, n: int, params: dict[str, str] | None = None) -> None:
    """T<n> through the wrap; the U1's T makes its head's extruder the active one."""

    def u1_tool(gcmd: Gcmd) -> None:
        make_active(rig, TOOL_HEAD[n])

    rig.gcode.commands_orig[f"T{n}"] = u1_tool
    rig.gcode.commands[f"T{n}"](Gcmd(dict(params or {}), f"T{n}"))


def dock_rig(tmp_path: Path) -> tuple[SimpleNamespace, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    rig, a = bg_rig(tmp_path)
    rig.print_stats.current_layer = 3
    u1_picks(rig)
    return rig, a


def wiped(rig: SimpleNamespace) -> bool:
    return "SAVE_GCODE_STATE NAME=ACEK_DOCK_WIPE" in rig.sent or SAID in rig.gcode.lines


def plain_t(tmp_path: Path) -> SimpleNamespace:
    """T0 of the file takes head 1 from its dock, with a:1 in it already: no swap."""
    rig, _ = dock_rig(tmp_path)
    rig.sent.clear()
    run_t(rig, 0)
    return rig


def test_a_plain_t_that_takes_a_listed_head_from_its_dock_wipes_it_once(tmp_path: Path) -> None:
    rig = plain_t(tmp_path)
    assert rig.sent == WIPE_LINES
    assert SAID in rig.gcode.lines


def _case(tmp_path: Path, change: str) -> SimpleNamespace:  # noqa: PLR0912 — one rig per case
    rig, _ = dock_rig(tmp_path)
    n, params = 0, None
    if change == "swap":
        n = 2  # a:2 for head 1, which holds a:1: an inline swap, whose return cleans
    elif change == "active":
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")
    elif change == "unlisted":
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")
        n = 1  # head 2, not in background_heads
    elif change == "not_printing":
        rig.print_stats.state = "standby"
    elif change == "paused":
        rig.pause_resume.pause_command_sent = True
    elif change == "no_arrival_wipe":
        rig.module.swap_cfg.arrival_wipe = False
    elif change == "print_start":
        rig.print_stats.current_layer = 0
    elif change == "u1_pick":
        params = {"A": "0"}
    elif change == "not_homed":
        rig.printer.objects["toolhead"].homed_axes = "z"
    elif change == "console":  # a T typed at the console during the print
        rig.printer.objects["virtual_sdcard"] = types.SimpleNamespace(is_cmd_from_sd=lambda: False)
    rig.sent.clear()
    run_t(rig, n, params)
    return rig


@pytest.mark.parametrize(
    "change",
    [
        "swap",
        "active",
        "unlisted",
        "not_printing",
        "paused",
        "no_arrival_wipe",
        "print_start",
        "u1_pick",
        "not_homed",
        "console",
    ],
)
def test_no_dock_wipe(tmp_path: Path, change: str) -> None:
    """Each case is the plain T above with one thing changed; that alone leaves out the wipe."""
    assert wiped(plain_t(tmp_path / "plain"))
    rig = _case(tmp_path / change, change)
    assert not wiped(rig)


def test_a_swap_t_still_cleans_in_its_return(tmp_path: Path) -> None:
    """The swap's return picks the head (T0 A0), so the U1's T picks nothing and adds no wipe."""
    rig = _case(tmp_path, "swap")
    assert "T0 A0" in rig.sent
    assert not wiped(rig)
    assert wiped(plain_t(tmp_path / "plain"))


def test_a_wipe_that_fails_is_said_and_the_print_goes_on(tmp_path: Path) -> None:
    rig, _ = dock_rig(tmp_path)
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        real(script)
        if script.startswith("INNER_ROUGHLY_CLEAN"):
            raise RuntimeError("not homed")

    rig.gcode.run_script_from_command = run_script
    rig.sent.clear()
    run_t(rig, 0)
    assert rig.sent[-1] == "RESTORE_GCODE_STATE NAME=ACEK_DOCK_WIPE MOVE=0"
    assert "ace2k_u1: head 1: its wipe after the dock failed — not homed" in rig.gcode.lines
    assert SAID not in rig.gcode.lines


class UnreadToolhead(fakes.FakeToolhead):
    """A toolhead whose active extruder cannot be read."""

    def get_extruder(self) -> types.SimpleNamespace:
        raise RuntimeError("no extruder")


def dock_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "no dock wipe" in r.getMessage()]


def test_a_pick_from_an_unread_head_is_logged_and_not_wiped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The head before the T unread, the listed head 1 after it: a wipe could have run."""
    caplog.set_level("INFO")
    assert wiped(plain_t(tmp_path / "plain"))
    rig, _ = dock_rig(tmp_path / "unread")
    rig.printer.objects["toolhead"] = UnreadToolhead("extruder1")
    run_t(rig, 0)
    assert not wiped(rig)
    assert dock_lines(caplog) == [
        "ace2k_u1: head 1: no dock wipe: the active head was unread before the U1's T"
    ]


@pytest.mark.parametrize("case", ["unlisted_after", "unread_after", "unlisted", "active"])
def test_no_unread_line_when_no_wipe_could_have_run(
    tmp_path: Path, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Not after a T to a head not listed, nor with the head after the T unread (debug only),
    nor for a T of an unlisted or of the active head."""
    caplog.set_level("INFO")
    rig, _ = dock_rig(tmp_path)
    if case == "unlisted_after":
        rig.printer.objects["toolhead"] = UnreadToolhead("extruder")
        run_t(rig, 1)  # head 2, not listed
    elif case == "unread_after":
        rig.gcode.commands_orig["T0"] = lambda gcmd: rig.printer.objects.__setitem__(
            "toolhead", UnreadToolhead("extruder")
        )
        rig.gcode.commands["T0"](Gcmd({}, "T0"))
    elif case == "unlisted":
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")
        run_t(rig, 1)
    else:
        rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder")
        run_t(rig, 0)
    assert not wiped(rig)
    assert dock_lines(caplog) == []


def test_the_cheap_checks_come_before_the_active_head_is_read(tmp_path: Path) -> None:
    """A T of the U1 (A0), no head listed, a print not printing or a T typed at the console:
    the T wrapper does not read the active head for the dock wipe."""
    rig, _ = dock_rig(tmp_path)
    wraps = rig.module.wraps
    reads = []
    rig.module.adapter.active_head = lambda u1: reads.append(1)
    assert wraps._dock_watch(Gcmd({"A": "0"}, "T0")) is None
    rig.bg.heads = frozenset()
    assert wraps._dock_watch(Gcmd({}, "T0")) is None
    rig.bg.heads = frozenset({0})
    rig.print_stats.state = "standby"
    assert wraps._dock_watch(Gcmd({}, "T0")) is None
    rig.print_stats.state = "printing"
    rig.printer.objects["virtual_sdcard"] = types.SimpleNamespace(is_cmd_from_sd=lambda: False)
    assert wraps._dock_watch(Gcmd({}, "T0")) is None
    assert reads == []
    del rig.printer.objects["virtual_sdcard"]
    assert wraps._dock_watch(Gcmd({}, "T0")) == ace2k_u1_wraps.DockWatch(None)
    assert reads == [1]


def test_a_restore_that_fails_does_not_hide_the_wipes_own_failure(tmp_path: Path) -> None:
    rig, _ = dock_rig(tmp_path)
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        real(script)
        if script.startswith("INNER_ROUGHLY_CLEAN"):
            raise RuntimeError("wipe fault")
        if script.startswith("RESTORE_GCODE_STATE NAME=ACEK_DOCK_WIPE"):
            raise RuntimeError("no saved state")

    rig.gcode.run_script_from_command = run_script
    rig.sent.clear()
    run_t(rig, 0)
    assert rig.sent[-1] == "RESTORE_GCODE_STATE NAME=ACEK_DOCK_WIPE MOVE=0"  # still tried
    lines = [ln for ln in rig.gcode.lines if ln.startswith("ace2k_u1: head 1:")]
    assert lines[-2:] == [
        "ace2k_u1: head 1: its wipe after the dock failed — wipe fault",
        "ace2k_u1: head 1: the G-code state after its wipe was not restored — no saved state",
    ]
    assert SAID not in rig.gcode.lines


def test_a_restore_that_fails_after_a_good_wipe_is_said_and_not_done(tmp_path: Path) -> None:
    rig, _ = dock_rig(tmp_path)
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        real(script)
        if script.startswith("RESTORE_GCODE_STATE NAME=ACEK_DOCK_WIPE"):
            raise RuntimeError("no saved state")

    rig.gcode.run_script_from_command = run_script
    run_t(rig, 0)
    assert (
        "ace2k_u1: head 1: the G-code state after its wipe was not restored — no saved state"
        in rig.gcode.lines
    )
    assert SAID not in rig.gcode.lines  # not said done when its state was not put back


def test_a_save_that_fails_moves_nothing(tmp_path: Path) -> None:
    rig, _ = dock_rig(tmp_path)
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        real(script)
        if script.startswith("SAVE_GCODE_STATE NAME=ACEK_DOCK_WIPE"):
            raise RuntimeError("busy")

    rig.gcode.run_script_from_command = run_script
    rig.sent.clear()
    run_t(rig, 0)
    assert rig.sent == ["SAVE_GCODE_STATE NAME=ACEK_DOCK_WIPE"]
    assert "ace2k_u1: head 1: its wipe after the dock failed — busy" in rig.gcode.lines
