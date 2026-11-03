"""Finding, downloading and checking a release: nothing is used before its checksum.

Each run here stops at the plan's question (answered no), so nothing is written on the printer.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fake_u1 import ACE2K_V, U1_V, local_release, release_files, sums, tarball

if TYPE_CHECKING:
    from typing import Any

    from fake_u1 import FakeU1, Ran


def _github(
    u1: FakeU1,
    ace2k_v: str = ACE2K_V,
    u1_v: str = U1_V,
    prerelease: bool = False,
    **kw: Any,  # noqa: ANN401 — forwarded unchanged to release_files
) -> None:
    ace2k, adapter = release_files(ace2k_v=ace2k_v, u1_v=u1_v, **kw)
    u1.publish("ace2k", f"v{ace2k_v}", ace2k, prerelease=prerelease)
    u1.publish("ace2k-u1", f"v{u1_v}", adapter, prerelease=prerelease)


def _stop_at_plan(u1: FakeU1, *args: object) -> Ran:
    return u1.run(*args, answers=["", "", "", "", "n"])  # the four heads, then no


def _checked(p: Ran) -> bool:
    """The run got as far as the plan's question: every file was found and checked."""
    return p.returncode == 1 and "Not confirmed; nothing changed." in p.out


def test_a_local_release_is_checked_and_nothing_is_written(u1: FakeU1, tmp_path: Path) -> None:
    rel = local_release(tmp_path / "rel")
    before = u1.digest()
    p = _stop_at_plan(u1, "--from", rel)
    assert _checked(p), p.out
    for name in (
        f"ace2k-{ACE2K_V}.bin",
        f"ace2k-host-{ACE2K_V}.tar.gz",
        f"ace2k-u1-{U1_V}.tar.gz",
        f"ace2k-u1-{U1_V}.requires",
    ):
        assert f"{name}: checksum OK" in p.out
    assert u1.digest() == before and not u1.state_dir.exists()


def test_a_changed_byte_is_refused(u1: FakeU1, tmp_path: Path) -> None:
    rel = local_release(tmp_path / "rel")
    (rel / f"ace2k-{ACE2K_V}.bin").write_bytes(b"tampered")
    p = _stop_at_plan(u1, "--from", rel)
    assert p.returncode == 1 and "checksum mismatch" in p.out and "Plan:" not in p.out


def test_a_tarball_does_not_depend_on_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests build a release more than once and compare checksums: a gzip header stamped
    with the time would make two builds a second apart differ."""
    built = []
    for now in (1000.0, 2000.0):
        with monkeypatch.context() as m:
            m.setattr(time, "time", lambda now=now: now)
            built.append(tarball({"a.py": "x"}))
    assert built[0] == built[1]


def test_a_tarball_that_climbs_out_is_refused(u1: FakeU1, tmp_path: Path) -> None:
    rel = local_release(tmp_path / "rel")
    bad = tarball({"../evil.py": "x"})
    (rel / f"ace2k-u1-{U1_V}.tar.gz").write_bytes(bad)
    ace2k, adapter = release_files()
    adapter[f"ace2k-u1-{U1_V}.tar.gz"] = bad
    (rel / "SHA256SUMS").write_text(sums(ace2k) + sums(adapter))
    p = _stop_at_plan(u1, "--from", rel)
    assert p.returncode == 1 and "paths outside its own tree" in p.out


def test_github_takes_the_newest_release(u1: FakeU1) -> None:
    _github(u1, ace2k_v="0.12.0", u1_v="0.3.0")
    _github(u1)
    p = _stop_at_plan(u1)
    assert _checked(p), p.out
    assert f"ace2k {ACE2K_V}" in p.out and f"ace2k-u1 {U1_V}" in p.out


def test_a_pre_release_is_taken_only_with_pre(u1: FakeU1) -> None:
    _github(u1)
    _github(u1, ace2k_v="0.14.0-rc.1", u1_v="0.5.0-rc.1", prerelease=True)
    assert f"ace2k-u1 {U1_V}" in _stop_at_plan(u1).out
    assert "ace2k-u1 0.5.0-rc.1" in _stop_at_plan(u1, "--pre").out


def test_only_pre_releases_and_no_pre(u1: FakeU1) -> None:
    _github(u1, prerelease=True)
    p = _stop_at_plan(u1)
    assert p.returncode == 1 and "--pre" in p.out


def test_drafts_are_never_taken(u1: FakeU1) -> None:
    _github(u1)
    ace2k, adapter = release_files(ace2k_v="0.14.0", u1_v="0.5.0")
    u1.publish("ace2k-u1", "v0.5.0", adapter, draft=True)
    assert f"ace2k-u1 {U1_V}" in _stop_at_plan(u1).out


def test_a_release_without_bundles_is_refused(u1: FakeU1) -> None:
    u1.publish("ace2k-u1", "v0.3.0-rc.1", {}, prerelease=True, with_sums=False)
    p = _stop_at_plan(u1, "--pre")
    assert p.returncode == 1
    assert "cannot be installed by install.sh" in p.out and "docs/install.md" in p.out


def test_ace2k_version_checks_that_release_first(u1: FakeU1) -> None:
    ace2k, _ = release_files(ace2k_v="0.12.0-rc.1")
    del ace2k["ace2k-host-0.12.0-rc.1.tar.gz"]
    u1.publish("ace2k", "v0.12.0-rc.1", ace2k, prerelease=True)
    u1.publish("ace2k-u1", "v0.3.0-rc.1", {}, prerelease=True, with_sums=False)
    p = _stop_at_plan(u1, "--pre", "--ace2k-version", "v0.12.0-rc.1")
    assert "ace2k-0.12.0-rc.1.bin: checksum OK" in p.out
    assert p.returncode == 1 and "ace2k-host-0.12.0-rc.1.tar.gz" in p.out


def test_version_takes_that_tag(u1: FakeU1) -> None:
    _github(u1, ace2k_v="0.12.0", u1_v="0.3.0")
    _github(u1)
    assert "ace2k-u1 0.3.0" in _stop_at_plan(u1, "--version", "v0.3.0").out


def test_an_api_that_does_not_answer_is_reported_as_such(u1: FakeU1) -> None:
    p = _stop_at_plan(u1)
    assert p.returncode == 1 and "GitHub's API did not answer" in p.out and "HTTP 404" in p.out


def test_a_rate_limited_api_says_so_and_nothing_else(u1: FakeU1) -> None:
    d = u1.fake / "github" / "ace2k-u1"
    d.mkdir(parents=True)
    (d / "releases.status").write_text("403")
    p = _stop_at_plan(u1)
    assert p.returncode == 1 and "hourly limit" in p.out
    assert "--pre" not in p.out and "No release" not in p.out


def test_version_with_a_local_source_must_match_it(u1: FakeU1, tmp_path: Path) -> None:
    rel = local_release(tmp_path / "rel")
    p = _stop_at_plan(u1, "--from", rel, "--version", "v0.3.0")
    assert p.returncode == 1 and "v0.3.0" in p.out and f"v{U1_V}" in p.out
    assert "does not match" in p.out and "nothing changed" in p.out
    for same in (f"v{U1_V}", U1_V):
        assert _checked(_stop_at_plan(u1, "--from", rel, "--version", same))
