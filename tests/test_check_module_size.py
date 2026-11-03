"""check_module_size.py: fails on a module with too many code lines; the total only warns."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

Q = '"""'
THREE = 3
FOUR = 4
TWO = 2
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_module_size.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_module_size", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(tmp_path: Path, source: str, limit: int, target: int) -> subprocess.CompletedProcess[str]:
    (tmp_path / "mod.py").write_text(source)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--max-code-lines",
            str(limit),
            "--target-lines",
            str(target),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_module_at_the_code_limit_passes_with_a_warning(tmp_path: Path) -> None:
    source = Q + "\n" * 98 + Q + "\n" + "x = 1\n" * 1000 + "# c\n" * 100 + "\n" * 100
    result = run(tmp_path, source, 1000, 1000)
    assert result.returncode == 0, result.stdout
    assert "lines (target 1000), warning only" in result.stdout


def test_a_module_over_the_code_limit_fails_and_is_named(tmp_path: Path) -> None:
    result = run(tmp_path, "x = 1\n" * 1001, 1000, 1000)
    assert result.returncode == 1
    assert "mod.py: 1001 code lines (limit 1000)" in result.stdout


def test_docstrings_comments_and_blank_lines_are_not_counted() -> None:
    source = (
        Q + "Module\ndocstring." + Q + "\n\n# comment\n"
        "def f():\n    " + Q + "Many\n    lines\n    here." + Q + "\n    # inside\n    return 1\n"
        "class C:\n    'One.'\n"
    )
    assert load_script().code_lines(source) == THREE


def test_a_string_that_is_not_a_docstring_is_counted() -> None:
    source = "def f():\n    x = 1\n    'not a docstring'\n    return x\n"
    assert load_script().code_lines(source) == FOUR
    assert load_script().code_lines("x = 'a'\n'b'\n") == TWO


def test_an_inline_comment_does_not_hide_its_code_line() -> None:
    assert load_script().code_lines("x = 1  # note\n") == 1


def test_the_total_warning_does_not_change_the_exit_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    check_module_size = load_script()
    (tmp_path / "wide.py").write_text("# c\n" * 5 + "x = 1\n")
    argv = ["--max-code-lines", "1", "--target-lines", "3", str(tmp_path)]
    assert check_module_size.main(argv) == 0
    out = capsys.readouterr().out
    assert "wide.py: 6 lines (target 3), warning only" in out
    assert "code lines" not in out
    (tmp_path / "long.py").write_text("x = 1\n" * 3)
    argv = ["--max-code-lines", "2", "--target-lines", "9", str(tmp_path)]
    assert check_module_size.main(argv) == 1
    assert "long.py: 3 code lines (limit 2)" in capsys.readouterr().out


def test_a_shell_part_counts_lines_that_do_not_start_with_a_hash(tmp_path: Path) -> None:
    part = tmp_path / "part.sh"
    part.write_text("#!/bin/sh\n# note\n\n  # indented note\nf() {\n    echo hi # kept\n}\n")
    assert load_script().shell_code_lines(part.read_text()) == THREE
    args = ["--max-code-lines", "2", "--target-lines", "99", str(part)]
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "part.sh: 3 code lines (limit 2)" in result.stdout
