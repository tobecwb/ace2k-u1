"""make dist: the files an ace2k-u1 release carries for install.sh."""

import hashlib
import subprocess
import tarfile
from pathlib import Path

TREE = Path(__file__).resolve().parents[1]


def _dist(tmp_path: Path, *extra: str) -> Path:
    out = tmp_path / "dist"
    subprocess.run(
        ["make", "-C", str(TREE), "dist", "U1_VERSION=9.9.9-test", f"DIST={out}", *extra],
        check=True,
        capture_output=True,
    )
    return out


def test_dist_writes_the_bundle_the_requires_file_the_installer_and_their_sums(
    tmp_path: Path,
) -> None:
    out = _dist(tmp_path)
    files = sorted(p.name for p in out.iterdir())
    assert files == [
        "SHA256SUMS",
        "ace2k-u1-9.9.9-test.requires",
        "ace2k-u1-9.9.9-test.tar.gz",
        "install.sh",
    ]
    sums = dict(reversed(line.split()) for line in (out / "SHA256SUMS").read_text().splitlines())
    for name in files[1:]:
        assert sums[name] == hashlib.sha256((out / name).read_bytes()).hexdigest()
    assert (out / "ace2k-u1-9.9.9-test.requires").read_text() == (TREE / "REQUIRES").read_text()


def test_the_bundle_holds_the_adapter_and_the_page_only(tmp_path: Path) -> None:
    out = _dist(tmp_path)
    with tarfile.open(out / "ace2k-u1-9.9.9-test.tar.gz") as tar:
        names = {m.name for m in tar.getmembers() if m.isfile()}
    modules = {f"klippy/extras/{p.name}" for p in (TREE / "klippy" / "extras").glob("ace2k_u1*.py")}
    assert "klippy/extras/ace2k_u1.py" in modules and "klippy/extras/ace2k_u1_common.py" in modules
    assert modules <= names  # every file of the adapter, the ones the split added included
    assert "config/ace2k-u1.cfg" in names
    assert "web/index.html" in names and "web/nginx/ace2k.conf" in names
    assert "LICENSE" in names
    assert not any(n.startswith(("web/tests/", "web/dev/")) for n in names)
    assert "web/package.json" not in names
    assert not any("__pycache__" in n or "/._" in n or n.endswith(".DS_Store") for n in names)


def test_the_requires_line_can_be_given(tmp_path: Path) -> None:
    out = _dist(tmp_path, "ACE2K_REQUIRES=ace2k v9.8.7")
    assert (out / "ace2k-u1-9.9.9-test.requires").read_text() == "ace2k v9.8.7\n"


def test_ci_checks_the_installer_and_releases_the_dist_files() -> None:
    ci = (TREE / ".github" / "workflows" / "ci.yml").read_text()
    assert "make installer-check" in ci
    release = ci[ci.index("  release:") :]
    assert "make dist" in release and "draft: true" in release
    for f in (
        "dist/ace2k-u1-*.tar.gz",
        "dist/ace2k-u1-*.requires",
        "dist/install.sh",
        "dist/SHA256SUMS",
    ):
        assert f in release
