"""install.sh's skeleton: options, prompts on the terminal, the lock, the truncation guard."""

import os
import subprocess

import pytest
from fake_u1 import INSTALL_SH, FakeU1, shell_available

OPTIONS = (
    "--update",
    "--remove",
    "--yes",
    "--port",
    "--from",
    "--pre",
    "--version",
    "--ace2k-version",
    "--restore-stock",
    "--force",
)


def test_help_names_every_option(u1: FakeU1) -> None:
    p = u1.run("--help")
    assert p.returncode == 0
    for option in OPTIONS:
        assert option in p.out


def test_an_unknown_option_stops(u1: FakeU1) -> None:
    p = u1.run("--frobnicate")
    assert p.returncode == 1 and "unknown option: --frobnicate" in p.out


def test_the_dropped_options_are_unknown(u1: FakeU1) -> None:
    for option in ("--download-only", "--download-dir", "--dry-run", "--reflash"):
        p = u1.run(option)
        assert p.returncode == 1 and f"unknown option: {option}" in p.out


def test_an_option_without_its_value_stops(u1: FakeU1) -> None:
    p = u1.run("--port")
    assert p.returncode == 1 and "--port needs a value" in p.out


@pytest.mark.parametrize("shell", ["sh", "dash"])
def test_it_parses_under_posix_shells(shell: str) -> None:
    if not shell_available(shell):
        pytest.skip(f"{shell} is not installed")
    assert subprocess.run([shell, "-n", str(INSTALL_SH)], check=False).returncode == 0


def test_sourcing_it_runs_nothing(u1: FakeU1) -> None:
    p = subprocess.run(
        ["sh", "-c", f'ACE2K_SOURCE_ONLY=1 . "{INSTALL_SH}" && echo sourced'],
        env=u1.env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.stdout == "sourced\n"


def test_a_question_without_a_terminal_stops_and_names_the_options(u1: FakeU1) -> None:
    p = u1.source('open_tty; confirm "Go on?"; echo went-on', tty=False)
    assert p.returncode == 1 and "went-on" not in p.out
    assert "no terminal" in p.out and "--yes" in p.out


def test_a_question_reads_the_terminal(u1: FakeU1) -> None:
    p = u1.source(
        'open_tty; confirm "Go on?" && echo yes-said; confirm "Again?" || echo no-said',
        answers=["y", "n"],
    )
    assert "yes-said" in p.out and "no-said" in p.out


def test_yes_answers_without_a_terminal(u1: FakeU1) -> None:
    p = u1.source('YES=1; open_tty; confirm "Go on?" && echo yes-said', tty=False)
    assert "yes-said" in p.out


def test_a_second_run_stops_while_the_first_holds_the_lock(u1: FakeU1) -> None:
    lock = u1.root / "tmp" / "ace2k-install.lock"
    lock.mkdir()
    (lock / "pid").write_text(str(os.getpid()))
    p = u1.source("take_lock; echo locked")
    assert p.returncode == 1 and "another install.sh runs" in p.out


def test_a_stale_lock_is_taken_over(u1: FakeU1) -> None:
    lock = u1.root / "tmp" / "ace2k-install.lock"
    lock.mkdir()
    (lock / "pid").write_text("999999")
    p = u1.source("take_lock; echo locked")
    assert p.returncode == 0 and "locked" in p.out
