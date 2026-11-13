"""The long-term averages of the swap times (ace2k_u1_swap_times)."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import ace2k_u1_background
import ace2k_u1_swap_times as st
import pytest

SEEDS = json.loads(
    (Path(__file__).resolve().parents[1] / "web/tests/fixtures/swap-time-seeds.json").read_text()
)


def test_seeds_match_the_fixture() -> None:
    assert {k: float(SEEDS[k]) for k in st.KINDS} == st.SEEDS
    assert {k: float(v) for k, v in SEEDS["steps"].items()} == st.STEP_SEEDS
    assert SEEDS["max_s"] == st.SAMPLE_MAX_S
    assert st.STEPS == ace2k_u1_background.DOCK_STEPS


def test_status_holds_the_seeds() -> None:
    s = st.SwapTimes().status()
    assert s["inline"] == {"s": 94.0, "n": 0}
    assert s["steps"]["roll back"] == {"s": 11.0, "n": 0}
    assert set(s) == {"inline", "ready", "dock", "resume", "steps"}
    assert list(s["steps"]) == list(st.STEPS)


def test_first_sample_replaces_the_seed_then_the_average_moves_a_fifth() -> None:
    t = st.SwapTimes()
    t.note("inline", 80.0)
    assert t.status()["inline"] == {"s": 80.0, "n": 1}
    t.note("inline", 90.0)
    assert t.status()["inline"] == {"s": 82.0, "n": 2}


@pytest.mark.parametrize("bad", [-1.0, 600.1, math.nan, math.inf])
def test_a_bad_sample_is_dropped_and_logged_once(
    bad: float, caplog: pytest.LogCaptureFixture
) -> None:
    t = st.SwapTimes()
    with caplog.at_level(logging.INFO):
        t.note("ready", bad)
        t.note("ready", bad)
    assert t.status()["ready"] == {"s": 15.0, "n": 0}
    assert sum("ready" in r.getMessage() for r in caplog.records) == 1


def test_step_samples() -> None:
    t = st.SwapTimes()
    t.note_step("unload", 25.0)
    assert t.status()["steps"]["unload"] == {"s": 25.0, "n": 1}


def test_work_left_and_the_resume_cost() -> None:
    t = st.SwapTimes()
    done = {"heat", "unload", "free"}
    assert t.left("roll back", 4.0, done) == 33.0
    assert t.left("roll back", 20.0, done) == 26.0  # never below 0 for the current step
    t.note_resume(43.0, "roll back", 0.0, done)
    assert t.status()["resume"] == {"s": 0.0, "n": 1}
    t2 = st.SwapTimes()
    t2.note_resume(70.0, "roll back", 0.0, done)
    assert t2.status()["resume"] == {"s": 18.0, "n": 1}


def test_work_left_skips_the_steps_done_the_current_one_too() -> None:
    t = st.SwapTimes()
    done = {"heat", "unload", "free", "roll back", "hand over", "push", "grip"}
    assert t.left("grip", 4.0, done) == 0.0  # gripped already: only the loaded step (0) is left
    assert t.left("push", 2.0, {"heat", "unload", "free", "roll back", "hand over", "push"}) == 14.0


def test_open_missing_broken_and_bad_values(tmp_path: Path) -> None:
    t = st.SwapTimes()
    t.open(str(tmp_path))  # missing: the seeds
    assert t.status()["dock"] == {"s": 65.0, "n": 0}
    (tmp_path / st.FILE_NAME).write_text("{not json")
    t = st.SwapTimes()
    t.open(str(tmp_path))
    assert t.status()["dock"] == {"s": 65.0, "n": 0}
    (tmp_path / st.FILE_NAME).write_text(json.dumps({"v": 2, "dock": {"s": 50, "n": 3}}))
    t = st.SwapTimes()
    t.open(str(tmp_path))
    assert t.status()["dock"] == {"s": 65.0, "n": 0}
    good = {
        "v": 1,
        "dock": {"s": 50, "n": 3},
        "ready": {"s": -4, "n": 2},
        "inline": {"s": 90, "n": -1},
        "steps": {"push": {"s": 10.5, "n": 4}},
    }
    (tmp_path / st.FILE_NAME).write_text(json.dumps(good))
    t = st.SwapTimes()
    t.open(str(tmp_path))
    s = t.status()
    assert s["dock"] == {"s": 50.0, "n": 3}
    assert s["ready"] == {"s": 15.0, "n": 0}
    assert s["inline"] == {"s": 94.0, "n": 0}
    assert s["steps"]["push"] == {"s": 10.5, "n": 4}


def test_open_takes_a_huge_integer_as_not_valid(tmp_path: Path) -> None:
    huge = 10**400  # too large for a float: float() raises OverflowError
    text = json.dumps({"v": 1, "dock": {"s": huge, "n": 3}, "steps": {"push": {"s": huge, "n": 1}}})
    (tmp_path / st.FILE_NAME).write_text(text)
    t = st.SwapTimes()
    t.open(str(tmp_path))  # never raises
    t.stop()
    assert t.status()["dock"] == {"s": 65.0, "n": 0}
    assert t.status()["steps"]["push"] == {"s": 12.0, "n": 0}
    t.note("ready", huge)  # a sample too: dropped, not raised
    assert t.status()["ready"] == {"s": 15.0, "n": 0}


def test_a_note_is_written_atomically(tmp_path: Path) -> None:
    t = st.SwapTimes()
    t.open(str(tmp_path))
    t.note("ready", 16.0)
    t.stop()  # waits for the writer
    data = json.loads((tmp_path / st.FILE_NAME).read_text())
    assert data["v"] == 1 and data["ready"] == {"s": 16.0, "n": 1}
    assert not (tmp_path / (st.FILE_NAME + ".tmp")).exists()
    t2 = st.SwapTimes()
    t2.open(str(tmp_path))
    assert t2.status()["ready"] == {"s": 16.0, "n": 1}


def test_a_write_failure_is_logged_once_and_kept_in_memory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    t = st.SwapTimes()
    t.open(str(blocker / "sub"))  # a folder that cannot be made (its read warning is cleared)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        t.note("ready", 16.0)
        t.note("ready", 17.0)
        t.stop()
    assert t.status()["ready"]["n"] == 2
    assert sum("swap-times" in r.getMessage() for r in caplog.records) == 1


def test_status_is_the_same_object_until_a_note() -> None:
    t = st.SwapTimes()
    a = t.status()
    assert t.status() is a
    t.note("dock", 60.0)
    assert t.status() is not a


def test_a_bool_sample_is_dropped() -> None:
    t = st.SwapTimes()
    t.note("ready", True)  # type: ignore[arg-type]
    assert t.status()["ready"] == {"s": 15.0, "n": 0}


def test_a_second_open_keeps_the_first_writer(tmp_path: Path) -> None:
    t = st.SwapTimes()
    t.open(str(tmp_path))
    first = t.thread
    t.open(str(tmp_path))
    assert t.thread is first
    t.stop()
