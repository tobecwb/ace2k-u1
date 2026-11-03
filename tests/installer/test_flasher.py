"""The flasher install.sh copies and runs: ace2k's ace2k_flash.pyz.

The fake release's host bundle is tied to the real one that ace2k's `make host-bundle` writes, and
the installer asks the real flasher which options it takes.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fake_u1 import (
    ACE2K_TREE,
    ACE2K_V,
    NEW_ONE,
    STUB_FLASHER,
    FakeU1,
    host_bundle,
    local_release,
    unit_a,
)

if TYPE_CHECKING:
    from fake_u1 import Ran

UNVERIFIED = {"exit": 2, "print": "Klipper's config has no [ace2k] on this port."}
FLASHER = "firmware/tools/ace2k_flash.pyz"


@pytest.fixture(scope="module")
def real_bundle(tmp_path_factory: pytest.TempPathFactory) -> tuple[set[str], Path]:
    """(the file names of ace2k's real host bundle, its flasher unpacked)."""
    build = tmp_path_factory.mktemp("ace2k-build")
    p = subprocess.run(
        [
            "make",
            "-C",
            str(ACE2K_TREE / "firmware"),
            "host-bundle",
            "ACE2K_VERSION=9.9.9-test",
            f"BUILD={build}",
            f"PYTHON={sys.executable}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    with tarfile.open(build / "ace2k-host-9.9.9-test.tar.gz") as tar:
        names = {m.name for m in tar.getmembers() if m.isfile()}
        flasher = tar.extractfile(FLASHER)
        assert flasher is not None
        pyz = build / "ace2k_flash.pyz"
        pyz.write_bytes(flasher.read())
    return names, pyz


def test_the_fake_host_bundle_holds_the_real_ones_files(real_bundle: tuple[set[str], Path]) -> None:
    real, _ = real_bundle
    fake = set(host_bundle())
    assert fake <= real
    # every file install.sh takes by name is in the fake; only host modules may be left out
    assert {n for n in real if not n.startswith("klippy/extras/")} <= fake


def test_the_real_flasher_says_it_takes_config_follows(
    u1: FakeU1, real_bundle: tuple[set[str], Path]
) -> None:
    _, pyz = real_bundle
    p = u1.source(
        "flasher_takes --config-follows && echo takes; "
        "flasher_takes --identify && echo takes; "
        "flasher_takes --no-such-option || echo lacks",
        ACE2K_FLASHER=str(pyz),
        ACE2K_PYTHON=sys.executable,
    )
    assert p.stdout.split() == ["takes", "takes", "lacks"], p.out


def test_a_new_install_runs_the_flasher_it_copied(u1: FakeU1, tmp_path: Path) -> None:
    # no ACE2K_FLASHER: the installer runs its own copy, the one from the release's bundle
    pylib = tmp_path / "pylib"
    pylib.mkdir()
    (pylib / "serial.py").write_text("")  # the preflight's pyserial, which Klipper brings on a U1
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run(
        "--from",
        local_release(tmp_path / "rel"),
        answers=NEW_ONE,
        ACE2K_FLASHER="",
        PYTHONPATH=str(pylib),
    )
    assert p.returncode == 0, p.out
    assert (u1.state_dir / "flash" / "ace2k_flash.pyz").exists()
    [argv] = u1.flasher_calls()
    assert "--config-follows" in argv  # its --help was asked, and listed the option


def _ensure(u1: FakeU1, tmp_path: Path) -> Ran:
    rel = local_release(tmp_path / "rel")
    work = tmp_path / "work"
    work.mkdir()
    script = (
        f'WORK="{work}"; FROM="{rel}"; ensure_flasher; echo "flasher: $FLASHER"; '
        "flasher_takes --config-follows && echo takes"
    )
    return u1.source(script, ACE2K_FLASHER="")


def test_a_restore_with_no_flasher_takes_the_releases(u1: FakeU1, tmp_path: Path) -> None:
    p = _ensure(u1, tmp_path)
    assert p.returncode == 0, p.out
    assert "No flasher in" in p.out
    assert f"/host/{FLASHER}\n" in p.stdout and "takes" in p.stdout.split()


def test_a_restore_after_an_older_install_takes_its_flasher(u1: FakeU1, tmp_path: Path) -> None:
    # the older install's one-file flasher restores the factory firmware with no network
    flash = u1.state_dir / "flash"
    flash.mkdir(parents=True)
    (flash / "ace2k_flash.py").write_text(STUB_FLASHER)
    p = _ensure(u1, tmp_path)
    assert p.returncode == 0, p.out
    assert f"flasher: {flash / 'ace2k_flash.py'}\n" in p.stdout and "takes" in p.stdout.split()
    assert "No flasher in" not in p.out and "Release:" not in p.out  # nothing fetched


def test_a_restore_takes_the_installs_own_flasher(u1: FakeU1, tmp_path: Path) -> None:
    flash = u1.state_dir / "flash"
    flash.mkdir(parents=True)
    (flash / "ace2k_flash.pyz").write_bytes(host_bundle()[FLASHER])
    (flash / "ace2k_flash.py").write_text("raise SystemExit('the older flasher ran')\n")
    p = _ensure(u1, tmp_path)
    assert p.returncode == 0, p.out
    assert "flasher: \n" in p.stdout and "takes" in p.stdout.split()
    assert "Release:" not in p.out  # nothing fetched


def test_a_release_whose_bundle_has_no_pyz_is_refused(u1: FakeU1, tmp_path: Path) -> None:
    rel = local_release(tmp_path / "rel", host_without=[FLASHER])
    before = u1.digest()
    p = u1.run("--from", rel, answers=["y"])
    assert p.returncode == 1
    assert "has no ace2k_flash.pyz in ace2k-host-" in p.out
    assert "needs a release whose ace2k carries ace2k_flash.pyz" in p.out
    assert "Nothing changed." in p.out
    assert u1.digest() == before and u1.flasher_calls() == []
