"""make quality, web-lint and web-quality need the page's tools from `npm ci --prefix web`.

Without web/node_modules they stop before any step runs, with one line that says what to do.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

TREE = Path(__file__).resolve().parents[1]
MESSAGE = "web/node_modules is missing: run npm ci --prefix web first"


def _make(tmp: Path, target: str) -> subprocess.CompletedProcess[str]:
    shutil.copy(TREE / "Makefile", tmp / "Makefile")
    (tmp / "REQUIRES").write_text("0.0.0\n")
    # PYTHON=false: a step that runs before the check fails, so the test sees it.
    return subprocess.run(
        ["make", "-C", str(tmp), target, "PYTHON=false"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_each_target_that_needs_the_tools_stops_first_without_them(tmp_path: Path) -> None:
    for target in ("quality", "web-lint", "web-quality"):
        run = _make(tmp_path, target)
        assert run.returncode != 0, target
        assert MESSAGE in run.stdout + run.stderr, target
        assert "false" not in run.stdout, target  # no step ran before the check


def test_the_check_passes_with_the_tools(tmp_path: Path) -> None:
    (tmp_path / "web" / "node_modules").mkdir(parents=True)
    run = _make(tmp_path, "web-deps")
    assert run.returncode == 0, run.stderr
    assert MESSAGE not in run.stdout + run.stderr
