"""Removing ace2k: everything it put on the printer goes, the unit optionally back to factory."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fake_u1 import (
    ACE2K_V,
    NEW_ONE,
    PORT_B,
    PRINTER_CFG,
    STUB_FLASHER,
    ace2k_state,
    installed_two,
    local_release,
    unit_a,
)

if TYPE_CHECKING:
    from typing import TypedDict

    from fake_u1 import FakeU1

    class FactoryEnv(TypedDict):
        """The stock image's checksum and size, as the environment install.sh reads."""

        ACE2K_FACTORY_SHA256: str
        ACE2K_FACTORY_SIZE: str
        PYTHONPATH: str


UNVERIFIED = {"exit": 2, "print": "Klipper's config has no [ace2k] on this port."}
NO_ACE2K = {
    "klippy_state": "ready",
    "status": {"print_stats": {"state": "standby"}, "configfile": {"settings": {}}},
}


# An older install's flasher: it lists no --identify, and cannot reach ace2k with Klipper
# stopped, so it restores the one unit the way it always did.
OLD_STUB_FLASHER = STUB_FLASHER.replace(', "--config-follows", "--identify"]', "]")
# One from before several units that already takes --config-follows: still no cable reach.
BENCH_STUB_FLASHER = STUB_FLASHER.replace(', "--identify"]', "]")


@pytest.fixture
def installed(u1: FakeU1, tmp_path: Path) -> FakeU1:
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    assert u1.run("--from", local_release(tmp_path / "rel"), answers=NEW_ONE).returncode == 0
    (u1.cfg / "printer.cfg").write_text(PRINTER_CFG)
    (u1.cfg / "ace2k_tags_cache.json").write_text("{}")
    u1.logs.mkdir(parents=True)
    (u1.logs / "2026-10-11.jsonl").write_text("{}\n")
    (u1.fake / "klipper.log").unlink()
    (u1.fake / "flasher.argv").unlink()
    (u1.fake / "nginx.log").unlink()
    u1.after_start(NO_ACE2K)
    return u1


@pytest.fixture
def stock(tmp_path: Path) -> tuple[Path, FactoryEnv]:
    f = tmp_path / "ACE2_V1.1.31.bin"
    f.write_bytes(b"the original image")
    pylib = tmp_path / "pylib"
    pylib.mkdir()
    (pylib / "serial.py").write_text("")  # the preflight's pyserial, which Klipper brings on a U1
    return f, {
        "ACE2K_FACTORY_SHA256": hashlib.sha256(f.read_bytes()).hexdigest(),
        "ACE2K_FACTORY_SIZE": str(f.stat().st_size),
        "PYTHONPATH": str(pylib),
    }


def test_strip_removes_the_section_and_its_separator(u1: FakeU1, tmp_path: Path) -> None:
    f = tmp_path / "printer.cfg"
    f.write_text(PRINTER_CFG)
    out = u1.source(f'strip_ace2k_saved "{f}"').stdout
    assert "[ace2k]" not in out and "lane1_encoder_scale" not in out
    assert out == PRINTER_CFG.replace("#*#\n#*# [ace2k]\n#*# lane1_encoder_scale = 1.2424\n", "")


def test_strip_removes_a_first_section_too(u1: FakeU1, tmp_path: Path) -> None:
    f = tmp_path / "printer.cfg"
    text = (
        "[printer]\n#*# <---------------------- SAVE_CONFIG ---------------------->\n"
        "#*# DO NOT EDIT\n#*#\n#*# [ace2k]\n#*# lane2_encoder_scale = 1.3\n#*#\n"
        "#*# [bed_mesh default]\n#*# version = 1\n"
    )
    f.write_text(text)
    out = u1.source(f'strip_ace2k_saved "{f}"').stdout
    assert out == text.replace("#*#\n#*# [ace2k]\n#*# lane2_encoder_scale = 1.3\n", "")


def test_saved_lines_without_a_classic_diff(u1: FakeU1, tmp_path: Path) -> None:
    # The U1's diff is BusyBox's, which prints only the unified format; the lines must not
    # depend on any diff.
    bin_dir = tmp_path / "bb"
    bin_dir.mkdir()
    (bin_dir / "diff").write_text("#!/bin/sh\necho '--- a'; echo '+++ b'; exit 1\n")
    (bin_dir / "diff").chmod(0o755)
    f = tmp_path / "printer.cfg"
    f.write_text(PRINTER_CFG)
    out = u1.source(f'CFG="{tmp_path}"; saved_lines', PATH=f"{bin_dir}:{os.environ['PATH']}").stdout
    assert out == "#*#\n#*# [ace2k]\n#*# lane1_encoder_scale = 1.2424\n"


def test_strip_leaves_a_file_without_it_alone(u1: FakeU1, tmp_path: Path) -> None:
    f = tmp_path / "printer.cfg"
    f.write_text(
        "[printer]\n#*# <---------------------- SAVE_CONFIG ---------------------->\n"
        "#*# [ace2k_note]\n"
    )
    assert u1.source(f'strip_ace2k_saved "{f}"').stdout == f.read_text()


def test_remove_without_the_restore(installed: FakeU1) -> None:
    u1 = installed
    p = u1.run("--remove", answers=["n", "y"])
    assert p.returncode == 0, p.out
    for gone in (
        u1.extras / "ace2k.py",
        u1.extras / "ace2k_u1.py",
        u1.extras / "ace2k_tags",
        u1.kcfg / "ace2k.cfg",
        u1.kcfg / "ace2k_tags.cfg",
        u1.kcfg / "ace2k-u1.cfg",
        u1.cfg / "ace2k_tags_cache.json",
        u1.web,
        u1.nginx_conf,
    ):
        assert not gone.exists(), gone
    assert "[ace2k]" not in (u1.cfg / "printer.cfg").read_text()
    assert "[bed_mesh default]" in (u1.cfg / "printer.cfg").read_text()
    assert (u1.fake / "nginx.log").read_text().splitlines()[-1] == "-s reload"
    assert u1.klipper_log() == ["restart"] and u1.flasher_calls() == []
    assert not (u1.state_dir / "state").exists()
    assert (u1.logs / "2026-10-11.jsonl").exists()
    bk = u1.backups()[-1]
    assert (bk / str(u1.extras / "ace2k.py").lstrip("/")).exists()
    assert "[ace2k]" in (bk / str(u1.cfg / "printer.cfg").lstrip("/")).read_text()
    for kept in (
        "/home/lava/printer_data/logs/ace2k/",
        "/home/lava/printer_data/ace2k/calibration/",
        "/home/lava/printer_data/ace2k/backup/",
    ):
        assert kept in p.out


def test_remove_with_the_restore(installed: FakeU1, stock: tuple[Path, FactoryEnv]) -> None:
    u1 = installed
    f, env = stock
    (u1.root / "root" / "ace-calibration").mkdir()
    (u1.root / "root" / "ace-calibration" / "ace-calibration-X-1.txt").write_text("pairs")
    u1.flasher({"exit": 0})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    assert p.returncode == 0, p.out
    [argv] = u1.flasher_calls()
    assert argv[:5] == [str(f), "--version", "1.1.31", "--port", u1.port]
    assert u1.klipper_log() == ["stop", "start"]
    assert (u1.state_dir / "calibration" / "ace-calibration-X-1.txt").exists()
    assert not (u1.extras / "ace2k.py").exists()


def test_a_restore_after_an_older_install_runs_its_flasher_offline(
    installed: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    # an older installer left ace2k_flash.py; nothing is published, so a fetch would fail
    u1 = installed
    f, env = stock
    flash = u1.state_dir / "flash"
    (flash / "ace2k_flash.pyz").unlink()
    (flash / "ace2k_flash.py").write_text(OLD_STUB_FLASHER)
    u1.flasher({"exit": 0, "klipper": ["stop"], "moonraker": {"klippy_state": "disconnected"}})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], ACE2K_FLASHER="", **env)
    assert p.returncode == 0, p.out
    # the same command line as when the installer ran ace2k_flash.py itself
    calibration = str(u1.state_dir / "calibration")
    assert u1.flasher_calls() == [[str(f), "--version", "1.1.31", "--calibration-dir", calibration]]
    assert u1.klipper_log() == ["flasher-stop", "start"]  # it stops Klipper itself, as before
    assert "Release:" not in p.out and not (u1.fake / "github").exists()
    assert not (u1.extras / "ace2k.py").exists()


def test_a_flasher_from_before_several_units_restores_one_unit_the_old_way(
    installed: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    # it takes --config-follows but not --identify: it cannot reach ace2k with Klipper stopped
    u1 = installed
    f, env = stock
    (u1.state_dir / "flash" / "ace2k_flash.pyz").write_text(BENCH_STUB_FLASHER)
    u1.flasher({"exit": 0, "klipper": ["stop"], "moonraker": {"klippy_state": "disconnected"}})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], ACE2K_FLASHER="", **env)
    assert p.returncode == 0, p.out
    [argv] = u1.flasher_calls()
    assert "--port" not in argv and u1.klipper_log() == ["flasher-stop", "start"]


def test_another_factory_file_is_refused(installed: FakeU1, stock: tuple[Path, FactoryEnv]) -> None:
    u1 = installed
    f, _ = stock
    before = u1.digest()
    p = u1.run("--remove", "--restore-stock", f, answers=["y"])
    assert p.returncode == 1 and "--force accepts it" in p.out
    assert u1.digest() == before and u1.flasher_calls() == []


def test_force_accepts_another_factory_file_with_a_warning(
    installed: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    u1 = installed
    f, _ = stock
    u1.flasher({"exit": 0, "klipper": ["stop"], "moonraker": {"klippy_state": "disconnected"}})
    p = u1.run("--remove", "--restore-stock", f, "--force", answers=["y"])
    assert p.returncode == 0, p.out
    assert "WARNING" in p.out and len(u1.flasher_calls()) == 1


def test_a_failed_restore_removes_nothing(
    installed: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    u1 = installed
    f, env = stock
    before = u1.digest()
    u1.flasher({"exit": 1, "print": "The update failed: chunk 2/9: no answer."})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    # the restore may have reset the unit: Klipper cannot reach it, so Klipper stays stopped
    assert p.returncode == 1 and "Klipper is left stopped" in p.out, p.out
    assert "Nothing was removed" in p.out and u1.klipper_log() == ["stop"]
    assert u1.digest() == before
    assert "in_progress" not in u1.state()  # nothing was removed: no removal to continue


def test_remove_without_a_terminal_needs_yes(installed: FakeU1) -> None:
    before = installed.digest()
    p = installed.run("--remove", tty=False)
    assert p.returncode == 1 and installed.digest() == before


def _unreadable(u1: FakeU1) -> Path:
    if os.geteuid() == 0:
        pytest.skip("root reads a file of mode 000")
    f = u1.web / "index.html"
    f.chmod(0)
    return f


def test_a_failed_backup_stops_the_removal_before_klipper_is_touched(installed: FakeU1) -> None:
    u1 = installed
    f = _unreadable(u1)
    try:
        p = u1.run("--remove", answers=["n", "y"])
    finally:
        f.chmod(0o644)
    assert p.returncode == 1, p.out
    assert "cannot back up" in p.out and "run the installer again" in p.out
    bk = u1.backups()[-1]
    assert str(bk) in p.out
    assert f.exists() and u1.klipper_log() == []


def test_a_failed_removal_after_the_restore_says_the_unit_is_on_the_original_firmware(
    installed: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    u1 = installed
    stock_file, env = stock
    u1.flasher({"exit": 0})
    f = _unreadable(u1)
    try:
        p = u1.run("--remove", "--restore-stock", stock_file, answers=["y"], **env)
    finally:
        f.chmod(0o644)
    assert p.returncode == 1, p.out
    assert "Every unit runs the original Anycubic firmware" in p.out
    assert "Klipper is stopped" in p.out and "continues the removal" in p.out
    assert u1.state()["in_progress"] == "remove"
    assert u1.klipper_log() == ["stop"]


def test_the_removal_waits_until_klipper_is_ready_without_ace2k(installed: FakeU1) -> None:
    # the first look after the restart still sees [ace2k]; the next one does not
    u1 = installed
    still = ace2k_state(ACE2K_V, u1.port)
    still["on_info"] = [{}, {"status": NO_ACE2K["status"]}]
    u1.after_start(still)
    p = u1.run("--remove", answers=["n", "y"])
    assert p.returncode == 0, p.out
    assert "not ready without ace2k" not in p.out


def test_a_removal_that_never_loses_ace2k_warns(installed: FakeU1) -> None:
    u1 = installed
    u1.after_start(ace2k_state(ACE2K_V, u1.port))
    p = u1.run("--remove", answers=["n", "y"])
    assert p.returncode == 0 and "not ready without ace2k" in p.out


def test_an_interrupted_restore_leaves_no_removal_to_continue(
    installed: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    # a continued removal never flashes: a stop while a unit is flashed leaves no removal mark,
    # so the same command again restores, then removes
    u1 = installed
    f, env = stock
    (u1.fake / "kill.flasher").write_text("SIGTERM")
    u1.flasher({"exit": 0}, {"exit": 0})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    assert p.returncode != 0 and "Interrupted" in p.out
    assert (u1.extras / "ace2k.py").exists() and "in_progress" not in u1.state()
    u1.after_start(NO_ACE2K)
    p = u1.run("--remove", "--restore-stock", f, "--yes", tty=False, **env)
    assert p.returncode == 0, p.out
    assert "A previous removal did not finish." not in p.out
    assert len(u1.flasher_calls()) == 2
    assert not (u1.extras / "ace2k.py").exists() and not (u1.kcfg / "ace2k.cfg").exists()
    assert not (u1.state_dir / "state").exists()


def test_a_removal_continued_after_the_restore_never_flashes(
    installed: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    u1 = installed
    stock_file, env = stock
    u1.flasher({"exit": 0})
    f = _unreadable(u1)
    try:
        p = u1.run("--remove", "--restore-stock", stock_file, answers=["y"], **env)
    finally:
        f.chmod(0o644)
    assert p.returncode == 1 and u1.state()["in_progress"] == "remove", p.out
    u1.after_start(NO_ACE2K)
    p = u1.run("--yes", tty=False, **env)
    assert p.returncode == 0, p.out
    assert "A previous removal did not finish." in p.out
    assert len(u1.flasher_calls()) == 1  # no second flash, of any image
    assert not (u1.extras / "ace2k.py").exists() and not (u1.kcfg / "ace2k.cfg").exists()
    assert not (u1.state_dir / "state").exists()


# --- several units ---


def _two(u1: FakeU1, **kw: bool) -> str:
    """Units a and b installed (b on its own cable), each with its tag cache; Klipper ready
    without ace2k after its next start.  Returns b's cable."""
    b = u1.add_port(PORT_B)
    installed_two(u1, b, **kw)
    for name in ("ace2k_tags_cache.json", "ace2k_tags_cache_a.json", "ace2k_tags_cache_b.json"):
        (u1.cfg / name).write_text("{}")
    u1.after_start(NO_ACE2K)
    return b


def test_strip_removes_every_ace2k_section_with_its_separator(u1: FakeU1, tmp_path: Path) -> None:
    f = tmp_path / "printer.cfg"
    keep = "#*#\n#*# [bed_mesh default]\n#*# version = 1\n#*#\n#*# [ace2k_note]\n#*# x = 1\n"
    text = (
        "[printer]\n#*# <---------------------- SAVE_CONFIG ---------------------->\n"
        "#*# DO NOT EDIT\n#*#\n#*# [ace2k]\n#*# lane2_encoder_scale = 1.3\n"
        "#*#\n#*# [ace2k a]\n#*# lane1_encoder_scale = 1.2\n"
        + keep
        + "#*#\n#*# [ace2k b]\n#*# lane1_encoder_scale = 1.4\n"
    )
    f.write_text(text)
    out = u1.source(f'strip_ace2k_saved "{f}"').stdout
    head = "[printer]\n#*# <---------------------- SAVE_CONFIG ---------------------->\n"
    assert out == head + "#*# DO NOT EDIT\n" + keep


def test_saved_drop_still_drops_one_unit_only(u1: FakeU1, tmp_path: Path) -> None:
    f = tmp_path / "printer.cfg"
    a = "#*#\n#*# [ace2k a]\n#*# lane1_encoder_scale = 1.2\n"
    b = "#*#\n#*# [ace2k b]\n#*# lane1_encoder_scale = 1.4\n"
    f.write_text(
        "[printer]\n#*# <---------------------- SAVE_CONFIG ---------------------->\n" + a + b
    )
    assert u1.source(f'WORK="{tmp_path}"; saved_drop "{f}" b').returncode == 0
    assert f.read_text().endswith(a)


def test_remove_restores_both_units_with_one_stop(
    u1: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    f, env = stock
    b = _two(u1, saved_b=True)
    u1.flasher({"exit": 0}, {"exit": 0})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    assert p.returncode == 0, p.out
    calls = u1.flasher_calls()
    assert [c[:5] for c in calls] == [
        [str(f), "--version", "1.1.31", "--port", u1.port],
        [str(f), "--version", "1.1.31", "--port", b],
    ]
    assert u1.klipper_log() == ["stop", "start"]
    assert f"flash unit a ({u1.port}) with the original Anycubic firmware 1.1.31" in p.out
    assert f"flash unit b ({b}) with the original Anycubic firmware 1.1.31" in p.out
    assert not (u1.kcfg / "ace2k.cfg").exists()
    assert not list(u1.cfg.glob("ace2k_tags_cache*.json"))
    printer = (u1.cfg / "printer.cfg").read_text()
    assert "#*# [ace2k" not in printer and "#*# [bed_mesh default]" in printer
    assert printer == PRINTER_CFG.replace(
        "#*#\n#*# [ace2k]\n#*# lane1_encoder_scale = 1.2424\n", ""
    )
    assert not (u1.state_dir / "state").exists()


def test_remove_of_two_units_without_the_restore_restarts_once(u1: FakeU1) -> None:
    _two(u1, saved_b=True)
    p = u1.run("--remove", answers=["n", "y"])
    assert p.returncode == 0, p.out
    assert u1.klipper_log() == ["restart"] and u1.flasher_calls() == []
    assert not (u1.kcfg / "ace2k.cfg").exists()
    assert not list(u1.cfg.glob("ace2k_tags_cache*.json"))
    assert "#*# [ace2k" not in (u1.cfg / "printer.cfg").read_text()


def test_a_failed_restore_of_b_removes_nothing(u1: FakeU1, stock: tuple[Path, FactoryEnv]) -> None:
    # a runs the original firmware now: Klipper's config cannot reach it, so Klipper stays stopped
    f, env = stock
    _two(u1)
    before = u1.digest()
    u1.flasher({"exit": 0}, {"exit": 1, "print": "The update failed: chunk 2/9: no answer."})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    assert p.returncode == 1, p.out
    assert "unit a is restored" in p.out and "restore of unit b did not finish" in p.out
    assert "Nothing was removed" in p.out and "Klipper is left stopped" in p.out
    assert (u1.kcfg / "ace2k.cfg").exists() and u1.digest() == before
    assert u1.klipper_log() == ["stop"]
    assert "in_progress" not in u1.state()  # the same command again restores, then removes


def test_a_restore_refused_on_the_first_unit_starts_klipper_again(
    u1: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    # nothing changed on any unit: Klipper runs again on its config, as before the run
    f, env = stock
    _two(u1)
    before = u1.digest()
    u1.flasher({"exit": 3, "print": "A lane is moving; nothing changed."})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    assert p.returncode == 1, p.out
    assert "Starting Klipper again" in p.out and "Klipper runs again on its config" in p.out
    assert u1.klipper_log() == ["stop", "start"] and len(u1.flasher_calls()) == 1
    assert u1.digest() == before and "in_progress" not in u1.state()


def test_restore_refuses_a_unit_gone(u1: FakeU1, stock: tuple[Path, FactoryEnv]) -> None:
    f, env = stock
    b = _two(u1)
    u1.remove_port(b)
    before = u1.digest()
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    assert p.returncode == 1, p.out
    assert f"Unit b ({b}) is not plugged in" in p.out
    assert "plug it in, or remove without --restore-stock" in p.out
    assert u1.digest() == before and u1.flasher_calls() == [] and u1.klipper_log() == []


def test_an_older_flasher_refuses_to_restore_two_units(
    u1: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    f, env = stock
    _two(u1)
    (u1.state_dir / "flash").mkdir(parents=True, exist_ok=True)
    (u1.state_dir / "flash" / "ace2k_flash.pyz").write_text(OLD_STUB_FLASHER)
    before = u1.digest()
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], ACE2K_FLASHER="", **env)
    assert p.returncode == 1, p.out
    assert "restores one unit only" in p.out and "--update" in p.out
    assert u1.digest() == before and u1.flasher_calls() == [] and u1.klipper_log() == []


def test_a_signal_after_the_first_unit_leaves_both_to_the_same_command_again(
    u1: FakeU1, stock: tuple[Path, FactoryEnv]
) -> None:
    f, env = stock
    b = _two(u1)
    (u1.fake / "kill.flasher").write_text("SIGTERM")  # as a's flash ends
    u1.flasher({"exit": 0}, {"exit": 0}, {"exit": 0})
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    assert p.returncode != 0 and "Interrupted" in p.out
    assert len(u1.flasher_calls()) == 1 and "in_progress" not in u1.state()
    assert (u1.kcfg / "ace2k.cfg").exists()
    p = u1.run("--remove", "--restore-stock", f, answers=["y"], **env)
    assert p.returncode == 0, p.out
    ports = [c[c.index("--port") + 1] for c in u1.flasher_calls()]
    assert ports == [u1.port, u1.port, b]
    assert not (u1.kcfg / "ace2k.cfg").exists()


def test_cfg_mcu_serials_reads_headers_with_a_comment(u1: FakeU1, tmp_path: Path) -> None:
    (tmp_path / "ace2k.cfg").write_text(
        "[mcu ace2k_b]  # the second unit\nserial: /dev/b  ; its cable\n\n"
        "[mcu ace2k_a] \nserial: /dev/a\n\n[mcu ace2k]\t; old\nserial: /dev/x\n"
        "[mcu other]\nserial: /dev/o\n"
    )
    out = u1.source(f'KCFG="{tmp_path}"; cfg_mcu_serials').stdout
    assert out.splitlines() == ["\x1f/dev/x", "a\x1f/dev/a", "b\x1f/dev/b"]


def test_a_signal_before_the_first_flash_leaves_no_removal_mark(
    installed: FakeU1, stock: tuple[Path, FactoryEnv], tmp_path: Path
) -> None:
    # the window between the confirmation and the first flash: the calibration copies are taken
    u1 = installed
    f, env = stock
    (u1.root / "root" / "ace-calibration").mkdir(parents=True)
    (u1.root / "root" / "ace-calibration" / "ace-calibration-X-1.txt").write_text("pairs")
    bin_dir = tmp_path / "sig"
    bin_dir.mkdir()
    real_cp = shutil.which("cp")
    (bin_dir / "cp").write_text(
        "#!/bin/sh\n"
        'case "$*" in *ace-calibration-*) kill -TERM $PPID ;; esac\n'
        f'exec {real_cp} "$@"\n'
    )
    (bin_dir / "cp").chmod(0o755)
    u1.flasher({"exit": 0})
    p = u1.run(
        "--remove",
        "--restore-stock",
        f,
        answers=["y"],
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        **env,
    )
    assert p.returncode != 0 and "Interrupted" in p.out
    assert u1.flasher_calls() == [] and "in_progress" not in u1.state()
    assert (u1.kcfg / "ace2k.cfg").exists()
