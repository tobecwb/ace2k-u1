"""install.sh's start checks: nothing changes before them, and each stop says why."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fake_u1 import PORT_B, UID_A, UID_B, ace2k_state, local_release

if TYPE_CHECKING:
    from fake_u1 import FakeU1


def _installed(u1: FakeU1, state: str | None = "ace2k=v0.12.0\nace2k_u1=v0.3.0\n") -> None:
    """ace2k's files on disk; state: the state file's text (None: no state file)."""
    (u1.extras / "ace2k.py").write_text("# old\n")
    (u1.kcfg / "ace2k.cfg").write_text(f"[mcu ace2k]\nserial: {u1.port}\n\n[ace2k]\n")
    (u1.kcfg / "ace2k-u1.cfg").write_text("[ace2k_u1]\n")
    if state is not None:
        u1.state_dir.mkdir(parents=True)
        (u1.state_dir / "state").write_text(state)


def test_no_paxx_stops(u1: FakeU1) -> None:
    (u1.root / "etc" / "BUILD_VERSION").write_text("1.6.0\n")
    p = u1.run("--remove")
    assert p.returncode == 1 and "paxx extended firmware" in p.out and "nothing changed" in p.out


def test_another_paxx_version_warns_and_asks(u1: FakeU1) -> None:
    (u1.root / "etc" / "BUILD_VERSION").write_text("1.7.0-paxx13-1-abc\n")
    p = u1.run("--remove", answers=["n"])
    assert p.returncode == 1 and "only 1.6.0-paxx12-22 was tested" in p.out
    p = u1.run("--remove", answers=["y"])
    assert p.returncode == 0 and "nothing to remove" in p.out


def test_moonraker_silent_stops(u1: FakeU1) -> None:
    p = u1.run("--remove", ACE2K_MOONRAKER="http://127.0.0.1:9")
    assert p.returncode == 1 and "Moonraker does not answer" in p.out


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_a_print_stops(u1: FakeU1, state: str) -> None:
    u1.moonraker(
        {
            "klippy_state": "ready",
            "status": {"print_stats": {"state": state}, "configfile": {"settings": {}}},
        }
    )
    p = u1.run("--remove")
    assert p.returncode == 1 and "A print is running or paused" in p.out


@pytest.mark.parametrize("section", ["ace", "ace second"])
def test_another_ace_driver_stops(u1: FakeU1, section: str) -> None:
    u1.moonraker(
        {
            "klippy_state": "ready",
            "status": {
                "print_stats": {"state": "standby"},
                "configfile": {"settings": {section: {}}},
            },
        }
    )
    p = u1.run("--remove")
    assert p.returncode == 1 and f"[{section}]" in p.out and "its own uninstaller" in p.out


def test_installed_without_a_terminal_or_an_option_stops(u1: FakeU1) -> None:
    _installed(u1)
    before = u1.digest()
    p = u1.run(tty=False)
    assert p.returncode == 1 and "no terminal" in p.out
    assert u1.digest() == before


def test_installed_and_cancelled(u1: FakeU1) -> None:
    _installed(u1)
    p = u1.run(answers=["c"])
    assert p.returncode == 1 and "canceled" in p.out


def test_installed_says_what_it_found(u1: FakeU1) -> None:
    _installed(u1)
    u1.moonraker(ace2k_state("0.12.0", u1.port))
    p = u1.run(answers=["c"])
    assert "ace2k is installed" in p.out and "firmware on the unit: 0.12.0" in p.out


def _installed_two(u1: FakeU1) -> None:
    _installed(u1)
    b = u1.add_port(PORT_B)
    (u1.kcfg / "ace2k.cfg").write_text(
        f"[mcu ace2k_a]\nserial: {u1.port}\n[ace2k a]\nmcu: ace2k_a\n"
        f"[mcu ace2k_b]\nserial: {b}\n[ace2k b]\nmcu: ace2k_b\n"
    )
    units = [("a", u1.port, UID_A), ("b", b, UID_B)]
    u1.moonraker(ace2k_state("0.13.0", "", units=units, versions={"b": "0.12.0"}))


def test_installed_names_each_units_firmware(u1: FakeU1) -> None:
    _installed_two(u1)
    p = u1.run(answers=["c"])
    assert "firmware on the units: a 0.13.0, b 0.12.0." in p.out, p.out


def test_installed_units_klipper_was_not_asked_for_are_left_to_the_plan(u1: FakeU1) -> None:
    # on the printer the start checks have no cfg tool yet: their query names no unit, and
    # Moonraker answers only the objects it is asked for
    _installed_two(u1)
    b = str(u1.root / "dev" / "serial" / "by-id" / PORT_B)
    state = ace2k_state("0.13.0", "", units=[("a", u1.port, UID_A), ("b", b, UID_B)])
    del state["status"]["ace2k a"], state["status"]["ace2k b"]
    u1.moonraker(state)
    p = u1.run(answers=["c"], ACE2K_CFGTOOL="")
    said = "firmware on the units: not read yet (Klipper was not asked for them by name)."
    assert said in p.out, p.out
    assert "unknown (Klipper is" not in p.out


def test_update_with_nothing_installed_stops(u1: FakeU1) -> None:
    p = u1.run("--update")
    assert p.returncode == 1 and "not installed" in p.out


def test_remove_with_nothing_installed_is_done(u1: FakeU1) -> None:
    p = u1.run("--remove")
    assert p.returncode == 0 and "nothing to remove" in p.out


def test_a_missing_debug_flag_is_asked_about_and_no_stops(u1: FakeU1) -> None:
    (u1.root / "oem" / ".debug").unlink()
    p = u1.run("--from", u1.root / "none", answers=["n"])
    assert p.returncode == 1 and "/oem/.debug" in p.out
    assert not (u1.root / "oem" / ".debug").exists()


def test_remove_never_asks_about_the_debug_flag(u1: FakeU1) -> None:
    (u1.root / "oem" / ".debug").unlink()
    p = u1.run("--remove", tty=False)
    assert p.returncode == 0 and ".debug" not in p.out


UNFINISHED = "ace2k=v0.12.0\nace2k_u1=v0.3.0\nin_progress=install\n"


def test_a_finished_install_asks_update_or_remove(u1: FakeU1) -> None:
    _installed(u1)
    p = u1.run(answers=["c"])
    assert "Update (u), remove (r)" in p.out and "did not finish" not in p.out
    assert "ace2k-u1 0.3.0," in p.out


def test_a_manual_install_says_so_and_asks_update_or_remove(u1: FakeU1) -> None:
    _installed(u1, state=None)
    p = u1.run(answers=["c"])
    assert "ace2k is installed, but not by this installer (a manual install)." in p.out
    assert "Update (u), remove (r)" in p.out and "did not finish" not in p.out


def test_an_unfinished_run_is_offered_to_continue_and_no_stops(u1: FakeU1) -> None:
    _installed(u1, state=UNFINISHED)
    before = u1.digest()
    p = u1.run(answers=["n"])
    assert p.returncode == 1
    assert "A previous run did not finish." in p.out and "Continue it?" in p.out
    assert "Update (u)" not in p.out and "--remove" in p.out
    assert u1.digest() == before


def test_an_unfinished_run_with_its_cfgs_continues_as_an_update(u1: FakeU1, tmp_path: Path) -> None:
    _installed(u1, state=UNFINISHED)
    rel = local_release(tmp_path / "rel")
    p = u1.run("--from", rel, answers=["y", "n"])
    assert "Continue it?" in p.out and "Update? [y/N]" in p.out


def test_an_unfinished_run_without_its_cfgs_continues_as_a_new_install(
    u1: FakeU1, tmp_path: Path
) -> None:
    _installed(u1, state=UNFINISHED)
    (u1.kcfg / "ace2k.cfg").unlink()
    rel = local_release(tmp_path / "rel")
    p = u1.run("--from", rel, answers=["y", "", "", "", "", "n"])
    assert "Continue it?" in p.out and f"unit a: {u1.port}" in p.out
    assert "write ace2k.cfg" in p.out


def test_a_legacy_phase_key_is_ignored(u1: FakeU1) -> None:
    _installed(u1, state="phase=flashing\nace2k=v0.12.0\nace2k_u1=v0.3.0\n")
    p = u1.run(answers=["c"])
    assert "Update (u), remove (r)" in p.out and "did not finish" not in p.out
