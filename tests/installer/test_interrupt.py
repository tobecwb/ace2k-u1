"""An interrupted run stops, names its backup, and leaves no lock or work directory behind."""

from pathlib import Path

import pytest
from fake_u1 import NEW_ONE, FakeU1, local_release

UNVERIFIED = {"exit": 2, "print": "Klipper's config has no [ace2k] on this port."}


@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_an_interrupt_while_the_files_are_written_stops_and_names_the_backup(
    u1: FakeU1, tmp_path: Path, sig: str
) -> None:
    (u1.fake / "kill.nginx").write_text(sig)  # nginx -t: the last step of the file layer
    u1.flasher(UNVERIFIED)
    p = u1.run("--from", local_release(tmp_path / "rel"), answers=NEW_ONE)
    assert p.returncode != 0
    assert "Interrupted" in p.out and "Run the installer again" in p.out
    assert "strobe" in p.out and "--remove --restore-stock" in p.out
    [bk] = u1.backups()
    assert str(bk) in p.out
    assert u1.flasher_calls() == [] and u1.klipper_log() == []
    assert not (u1.root / "tmp" / "ace2k-install.lock").exists()
