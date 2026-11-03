"""The recorder: pure helpers first, then the writer and the glue."""

from __future__ import annotations

import copy
import datetime
import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1_history as h
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    # the history fixture: the recorder, its printer, and the clock its time.time reads
    HistoryRig = tuple[h.History, "FakePrinter", dict[str, float]]

FIXTURE = Path(__file__).resolve().parents[1] / "web" / "tests" / "fixtures" / "event-lines.json"
TRANSITIONS = FIXTURE.with_name("transitions.json")


def status(**kw: object) -> dict[str, object]:
    base = dict(
        chamber=27.123,
        humidity=52.0,
        ptc_left=29.1,
        ptc_right=float("nan"),
        dryer=dict(state="idle", target=None, fault=None, remaining=None),
        lanes=[dict(mode="idle", error=None) for _ in range(4)],
    )
    base.update(kw)
    return base


def test_sample_line_has_exactly_the_keys_rounded_and_null_for_missing() -> None:
    rec = json.loads(h.sample_line(status(), 1791460661.7))
    assert list(rec) == ["t", "k", "ch", "rh", "hl", "hr", "dry", "tgt", "rem"]
    assert rec == {
        "t": 1791460661,
        "k": "s",
        "ch": 27.12,
        "rh": 52.0,
        "hl": 29.1,
        "hr": None,
        "dry": "idle",
        "tgt": None,
        "rem": None,
    }


def no_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is no JSON")


def test_sample_line_writes_an_infinite_reading_as_null() -> None:
    """Infinity is no JSON: the page's parser drops a line carrying it."""
    dryer = dict(state="heating", target=float("inf"), fault=None, remaining=float("-inf"))
    line = h.sample_line(status(chamber=float("inf"), humidity=float("-inf"), dryer=dryer), 1.0)
    rec = json.loads(line, parse_constant=no_constant)
    assert (rec["ch"], rec["rh"], rec["tgt"], rec["rem"]) == (None, None, None, None)


def test_sample_line_target_only_while_starting_or_heating() -> None:
    for state, want in (
        ("starting", 45.0),
        ("heating", 45.0),
        ("cooldown", None),
        ("idle", None),
        ("fault", None),
    ):
        rec = json.loads(h.sample_line(status(dryer=dict(state=state, target=45)), 5))
        assert rec["tgt"] == want, state


def test_sample_line_carries_the_remaining_minutes() -> None:
    rec = json.loads(h.sample_line(status(dryer=dict(state="heating", target=45, remaining=44)), 5))
    assert rec["rem"] == 44.0 and rec["tgt"] == 45.0


def test_sample_line_with_no_dryer_and_a_bool() -> None:
    rec = json.loads(h.sample_line({"chamber": True}, 5))
    assert rec["ch"] is None and rec["dry"] is None and rec["tgt"] is None


def test_output_events_match_the_shared_fixture() -> None:
    for case in json.loads(FIXTURE.read_text(encoding="utf-8")):
        unit, units = case.get("unit"), case.get("units", [])
        got = [json.loads(line) for line in h.output_events(case["raw"], 100, unit, units)]
        want = [dict(t=100, k="e", **e) for e in case["events"]]
        assert got == want, case["raw"]


# A fixture status whose `lanes` is an object keyed by lane index ("0" to "3") is a shorthand: the
# four lanes of an idle, empty unit, the listed ones with their own fields over it.  The page's
# tests (web/tests/helpers.js expandStatus) read the same rule.
IDLE_LANE: dict[str, object] = dict(
    insert=False, mode="idle", error=None, last_event=None, tag=dict(state="unknown", record=None)
)


def expand_status(st: dict[str, object] | None) -> dict[str, object] | None:
    over = st.get("lanes") if st else None
    if not isinstance(over, dict) or not set(over) <= {"0", "1", "2", "3"}:
        return st
    assert st is not None
    return {**st, "lanes": [copy.deepcopy({**IDLE_LANE, **over.get(str(i), {})}) for i in range(4)]}


def test_transitions_match_the_shared_fixture() -> None:
    for case in json.loads(TRANSITIONS.read_text(encoding="utf-8")):
        prev = None
        if case["prev_status"] is not None:
            _, prev = h.transitions(None, expand_status(case["prev_status"]), 100)
        for between in case.get("between", []):
            _, prev = h.transitions(prev, expand_status(between), 100)
        events, _ = h.transitions(prev, expand_status(case["status"]), 100)
        want = [dict(t=100, k="e", **e) for e in case["events"]]
        assert [json.loads(line) for line in events] == want, case["name"]


def test_transitions_nan_filament_mm_is_missing() -> None:
    _, snap = h.transitions(None, status(), 1)
    lane = dict(
        mode="idle", last_event=dict(kind="loaded", mode="loading", seq=1, filament_mm=float("nan"))
    )
    events, _ = h.transitions(snap, status(lanes=[lane]), 2)
    assert [json.loads(e)["msg"] for e in events] == ["lane 1 loaded (loading)"]


def test_transitions_show_the_whole_travel_of_an_event_of_several_moves() -> None:
    """An assist's link loss ends a mode of two bursts. Its event carries both, 33.323 mm, and
    the line shows that figure, not the burst that the loss cut."""
    _, snap = h.transitions(None, status(), 1)
    ev = dict(kind="stopped_link", mode="assisting", seq=4, filament_mm=33.323, motor_mm=8.744)
    lane = dict(mode="idle", last_event=ev)
    events, _ = h.transitions(snap, status(lanes=[lane]), 2)
    assert [json.loads(e)["msg"] for e in events] == ["lane 1 stopped_link (assisting, 33.3 mm)"]


def test_transitions_first_call_is_silent_and_returns_a_snapshot() -> None:
    events, snap = h.transitions(None, status(), 1)
    assert events == [] and snap["dryer"] == "idle"


def test_transitions_dryer_change_and_fault() -> None:
    _, snap = h.transitions(None, status(), 1)
    events, snap = h.transitions(snap, status(dryer=dict(state="heating", target=55)), 2)
    assert [json.loads(e) for e in events] == [
        {"t": 2, "k": "e", "lvl": "info", "src": "dryer", "msg": "dryer idle → heating"}
    ]
    events, _ = h.transitions(snap, status(dryer=dict(state="fault", fault="not_heating")), 3)
    assert [json.loads(e)["msg"] for e in events] == ["dryer fault: not_heating"]
    assert json.loads(events[0])["lvl"] == "err"


def test_transitions_lane_error_enter_and_leave() -> None:
    _, snap = h.transitions(None, status(), 1)
    lanes = [dict(mode="idle", error=None) for _ in range(4)]
    lanes[3] = dict(mode="error", error="stuck")
    events, snap = h.transitions(snap, status(lanes=lanes), 2)
    assert [json.loads(e) for e in events] == [
        {"t": 2, "k": "e", "lvl": "err", "src": "lane4", "msg": "lane 4 error: stuck"}
    ]
    events, _ = h.transitions(snap, status(), 3)
    assert [json.loads(e)["msg"] for e in events] == ["lane 4 error cleared"]
    assert json.loads(events[0])["lvl"] == "info"


def test_day_name_is_the_local_date() -> None:
    t = datetime.datetime(2026, 10, 8, 23, 59).timestamp()
    assert h.day_name(t) == "2026-10-08.jsonl"


def test_stale_files_keeps_keep_days_including_today_and_ignores_other_names() -> None:
    names = ["2026-10-08.jsonl", "2026-10-02.jsonl", "2026-10-01.jsonl", "notes.txt", "x.jsonl"]
    assert h.stale_files(names, datetime.date(2026, 10, 8), 7) == ["2026-10-01.jsonl"]


def test_writer_appends_by_day_and_creates_the_directory(tmp_path: Path) -> None:
    w = h.Writer(str(tmp_path / "ace2k"), keep_days=7)
    assert w.flush(
        [("2026-10-08.jsonl", "a"), ("2026-10-08.jsonl", "b"), ("2026-10-09.jsonl", "c")]
    )
    assert (tmp_path / "ace2k" / "2026-10-08.jsonl").read_text() == "a\nb\n"
    assert (tmp_path / "ace2k" / "2026-10-09.jsonl").read_text() == "c\n"
    assert w.written == 3


def test_writer_prunes_stale_days_when_a_new_day_starts(tmp_path: Path) -> None:
    d = tmp_path / "ace2k"
    d.mkdir()
    (d / "2026-09-30.jsonl").write_text("old\n")
    (d / "2026-10-05.jsonl").write_text("kept\n")
    w = h.Writer(str(d), keep_days=7)
    w.flush([("2026-10-08.jsonl", "x")])
    assert sorted(os.listdir(d)) == ["2026-10-05.jsonl", "2026-10-08.jsonl"]


def test_writer_failure_sets_error_and_stops(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    w = h.Writer(str(blocker), keep_days=7)  # a file where the directory should be
    assert w.flush([("2026-10-08.jsonl", "x")]) is False
    assert w.error
    w.put("y", 0)
    assert len(w.queue) == 0
    assert sum("writing stopped" in r.message for r in caplog.records) == 1


def test_writer_queue_drops_the_oldest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "QUEUE_MAX", 3)
    w = h.Writer(str(tmp_path), keep_days=7)
    for i in range(5):
        w.put(str(i), 0)
    assert [line for _, line in w.queue] == ["2", "3", "4"]
    assert w.dropped == 2


class FakeReactor:
    NOW = 0.0

    def __init__(self) -> None:
        self.timers = []

    def register_timer(
        self, cb: Callable[[float], float], waketime: float
    ) -> Callable[[float], float]:
        self.timers.append(cb)
        return cb


class FakeGcode:
    def __init__(self) -> None:
        self.handlers = []
        self.scripts = []

    def register_output_handler(self, cb: Callable[[str], None]) -> None:
        self.handlers.append(cb)

    def run_script(self, script: str) -> None:  # must never be called
        self.scripts.append(script)


class FakeAce2k:
    def __init__(self) -> None:
        self.status = status()
        self.fail = False

    def get_status(self, eventtime: float) -> dict[str, object]:
        if self.fail:
            raise RuntimeError("boom")
        return self.status


class FakePrinter:
    def __init__(self) -> None:
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.ace2k = FakeAce2k()
        self.handlers = {}
        self.units = None

    def get_reactor(self) -> FakeReactor:
        return self.reactor

    def register_event_handler(self, name: str, cb: Callable[[], None]) -> None:
        self.handlers[name] = cb

    def lookup_object(self, name: str, default: object = None) -> object:
        return {"gcode": self.gcode, "ace2k": self.ace2k, "ace2k_units": self.units}.get(
            name, default
        )


class FakeConfig:
    def __init__(self, printer: FakePrinter, path: str) -> None:
        self.printer = printer
        self.values = {"path": path}

    def get_printer(self) -> FakePrinter:
        return self.printer

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.values.get(name, default)

    def getint(
        self,
        name: str,
        default: int | None = None,
        minval: int | None = None,
        maxval: int | None = None,
    ) -> int:
        return int(self.values.get(name, default))


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HistoryRig:
    printer = FakePrinter()
    clock = {"t": 1791460000.0}
    monkeypatch.setattr(h.time, "time", lambda: clock["t"])
    hist = h.load_config(FakeConfig(printer, str(tmp_path / "ace2k")))
    hist.writer.start = lambda: None  # no thread in the tests: flush by hand
    printer.handlers["klippy:ready"]()
    return hist, printer, clock


def queued(hist: h.History) -> list[dict[str, object]]:
    return [json.loads(line) for _, line in hist.writer.queue]


def test_history_samples_once_per_interval_and_ticks_every_second(history: HistoryRig) -> None:
    hist, printer, clock = history
    assert hist._tick(0.0) == 1.0
    clock["t"] += 10
    hist._tick(10.0)
    clock["t"] += 25
    hist._tick(35.0)
    assert [r["k"] for r in queued(hist)] == ["s", "s"]


def test_history_records_transitions_between_samples(history: HistoryRig) -> None:
    hist, printer, clock = history
    hist._tick(0.0)
    printer.ace2k.status = status(dryer=dict(state="heating", target=55))
    clock["t"] += 1
    hist._tick(1.0)
    assert [r["msg"] for r in queued(hist) if r["k"] == "e"] == ["dryer idle → heating"]


def test_history_writes_a_sample_at_each_dryer_state_change(history: HistoryRig) -> None:
    hist, printer, clock = history
    hist._tick(0.0)
    printer.ace2k.status = status(dryer=dict(state="starting", target=45, remaining=45))
    clock["t"] += 1
    hist._tick(1.0)
    recs = queued(hist)
    assert [r["k"] for r in recs] == ["s", "e", "s"]
    assert recs[2]["dry"] == "starting" and recs[2]["rem"] == 45.0
    clock["t"] += 1
    hist._tick(2.0)  # no change, inside the interval: nothing more
    assert len(queued(hist)) == 3


def test_history_output_handler_queues_ace2k_lines_only(history: HistoryRig) -> None:
    hist, printer, clock = history
    (handler,) = printer.gcode.handlers
    handler("// ace2k: drying at 55 °C for 4 h\n// echo: other")
    assert [r["msg"] for r in queued(hist)] == ["ace2k: drying at 55 °C for 4 h"]


def test_history_never_raises_and_runs_no_gcode(history: HistoryRig) -> None:
    hist, printer, clock = history
    printer.ace2k.fail = True
    assert hist._tick(0.0) == 1.0
    printer.gcode.handlers[0](None)  # not a string: caught
    assert printer.gcode.scripts == []


def test_history_status(history: HistoryRig, tmp_path: Path) -> None:
    hist, printer, clock = history
    assert hist.get_status(0) == {
        "path": str(tmp_path / "ace2k"),
        "written": 0,
        "dropped": 0,
        "error": None,
        "keep_days": 7,
    }


def test_history_logs_a_persistent_failure_once_until_it_recovers(
    history: HistoryRig, caplog: pytest.LogCaptureFixture
) -> None:
    hist, printer, clock = history
    printer.ace2k.fail = True
    hist._tick(0.0)
    hist._tick(1.0)
    assert sum("tick was not recorded" in r.message for r in caplog.records) == 1
    printer.ace2k.fail = False
    hist._tick(2.0)
    printer.ace2k.fail = True
    hist._tick(3.0)
    assert sum("tick was not recorded" in r.message for r in caplog.records) == 2
    printer.gcode.handlers[0](None)
    printer.gcode.handlers[0](None)
    assert sum("output line" in r.message for r in caplog.records) == 1


def test_writer_stop_writes_the_rest_and_ends_the_thread(tmp_path: Path) -> None:
    w = h.Writer(str(tmp_path / "ace2k"), keep_days=7)
    w.start()
    t = datetime.datetime(2026, 10, 8, 12).timestamp()
    w.put("last", t)
    w.stop()
    w.thread.join(timeout=5)
    assert not w.thread.is_alive()
    assert (tmp_path / "ace2k" / "2026-10-08.jsonl").read_text() == "last\n"
    w.put("after", t)
    assert len(w.queue) == 0


def slow_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release: threading.Event
) -> h.Writer:
    """A started Writer whose flush waits for release (an Event), as a slow disk would, with one
    line queued: (writer, the flush's start)."""
    w = h.Writer(str(tmp_path / "ace2k"), keep_days=7)
    started = threading.Event()
    real = w.flush

    def flush(batch: list[tuple[str, str]]) -> bool:
        started.set()
        release.wait(5.0)
        return real(batch)

    monkeypatch.setattr(w, "flush", flush)
    w.start()
    w.put("slow", datetime.datetime(2026, 10, 8, 12).timestamp())
    assert started.wait(5.0)
    return w


def test_writer_stop_waits_for_the_old_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call stop() returns once the old thread has written what was queued and ended, so a
    restart's new writer never shares its day file."""
    release = threading.Event()
    w = slow_writer(tmp_path, monkeypatch, release)
    threading.Timer(0.2, release.set).start()
    w.stop()
    assert not w.thread.is_alive()
    assert (tmp_path / "ace2k" / "2026-10-08.jsonl").read_text() == "slow\n"


def test_writer_stop_leaves_a_stuck_thread_after_its_wait_with_a_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(h, "STOP_WAIT_S", 0.05)
    release = threading.Event()
    w = slow_writer(tmp_path, monkeypatch, release)
    w.stop()
    try:
        assert w.thread.is_alive()
        assert "the old writer still writes after 0.05 s: left running" in caplog.text
    finally:
        release.set()
        w.thread.join(5.0)


def test_writer_stop_before_its_start_waits_for_nothing(tmp_path: Path) -> None:
    w = h.Writer(str(tmp_path / "ace2k"), keep_days=7)
    w.stop()
    assert w.thread is None


def test_history_registers_the_writer_stop_on_disconnect(history: HistoryRig) -> None:
    hist, printer, clock = history
    assert printer.handlers["klippy:disconnect"] == hist.writer.stop


def test_writer_prune_deletes_nothing_when_a_file_is_from_the_future(tmp_path: Path) -> None:
    d = tmp_path / "ace2k"
    d.mkdir()
    (d / "2026-09-01.jsonl").write_text("old\n")
    (d / "2030-01-01.jsonl").write_text("future\n")
    w = h.Writer(str(d), keep_days=7)
    w.flush([("2026-10-08.jsonl", "x")])
    assert sorted(os.listdir(d)) == ["2026-09-01.jsonl", "2026-10-08.jsonl", "2030-01-01.jsonl"]


def test_writer_prunes_across_midnight(tmp_path: Path) -> None:
    d = tmp_path / "ace2k"
    w = h.Writer(str(d), keep_days=2)
    assert w.flush([("2026-10-07.jsonl", "a")])
    assert w.flush([("2026-10-08.jsonl", "b")])
    assert sorted(os.listdir(d)) == ["2026-10-07.jsonl", "2026-10-08.jsonl"]
    assert w.flush([("2026-10-09.jsonl", "c")])
    assert sorted(os.listdir(d)) == ["2026-10-08.jsonl", "2026-10-09.jsonl"]


def test_transitions_a_dryer_state_appearing_is_not_a_transition() -> None:
    _, snap = h.transitions(None, status(dryer={}), 1)
    assert snap["dryer"] is None
    events, snap = h.transitions(snap, status(), 2)
    assert events == [] and snap["dryer"] == "idle"
    events, snap = h.transitions(snap, status(dryer={}), 3)
    assert events == []


def test_transitions_lanes_appearing_or_unchanged_errors_are_not_transitions() -> None:
    _, snap = h.transitions(None, status(lanes=[]), 1)
    lanes = [dict(mode="idle", error=None) for _ in range(4)]
    lanes[1] = dict(mode="error", error="stuck")
    events, snap = h.transitions(snap, status(lanes=lanes), 2)
    assert events == []
    events, snap = h.transitions(snap, status(lanes=lanes), 3)
    assert events == []
    lanes[1] = None
    events, _ = h.transitions(snap, status(lanes=lanes), 4)
    assert [json.loads(e)["msg"] for e in events] == ["lane 2 error cleared"]


def test_writer_prune_ignores_a_file_already_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "ace2k"
    d.mkdir()
    (d / "2026-09-01.jsonl").write_text("old\n")
    w = h.Writer(str(d), keep_days=7)
    real_remove = os.remove

    def gone(p: str) -> NoReturn:
        real_remove(p)
        raise FileNotFoundError(p)

    monkeypatch.setattr(h.os, "remove", gone)
    assert w.flush([("2026-10-08.jsonl", "x")]) is True
    assert w.error is None
    assert (d / "2026-10-08.jsonl").read_text() == "x\n"


def test_writer_prune_error_is_logged_once_and_writing_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    d = tmp_path / "ace2k"
    d.mkdir()
    (d / "2026-09-01.jsonl").write_text("old\n")

    def denied(p: str) -> NoReturn:
        raise PermissionError(p)

    monkeypatch.setattr(h.os, "remove", denied)
    w = h.Writer(str(d), keep_days=7)
    assert w.flush([("2026-10-08.jsonl", "a")]) is True
    assert w.flush([("2026-10-09.jsonl", "b")]) is True
    assert w.error is None and w.written == 2
    assert (d / "2026-10-09.jsonl").read_text() == "b\n"
    assert sum("pruning" in r.message for r in caplog.records) == 1


def test_history_a_failing_put_loses_no_transition(history: HistoryRig) -> None:
    hist, printer, clock = history
    hist._tick(0.0)
    printer.ace2k.status = status(dryer=dict(state="heating", target=55))
    real = hist.writer.put
    calls = []

    def flaky(line: str, t: float) -> None:
        calls.append(line)
        if len(calls) == 1:
            raise RuntimeError("boom")
        real(line, t)

    hist.writer.put = flaky
    clock["t"] += 1
    assert hist._tick(1.0) == 2.0  # raised inside, caught
    clock["t"] += 1
    hist._tick(2.0)
    assert [r["msg"] for r in queued(hist) if r["k"] == "e"] == ["dryer idle → heating"]


def test_history_samples_the_first_unit_and_falls_back_to_ace2k(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Units:
        def __init__(self, units: list[FakeAce2k]) -> None:
            self.units = units

    first, second = FakeAce2k(), FakeAce2k()
    printer = FakePrinter()
    printer.units = Units([first, second])
    hist = h.load_config(FakeConfig(printer, str(tmp_path / "a")))
    hist.writer.start = lambda: None
    printer.handlers["klippy:ready"]()
    assert hist.ace2k is first

    plain = FakePrinter()
    hist = h.load_config(FakeConfig(plain, str(tmp_path / "b")))
    hist.writer.start = lambda: None
    plain.handlers["klippy:ready"]()
    assert hist.ace2k is plain.ace2k


def test_history_files_lanes_under_the_first_unit_only(tmp_path: Path) -> None:
    # several units: the page shows the first; another unit's lane line is the source unit
    class Units:
        def __init__(self, units: list[FakeAce2k]) -> None:
            self.units = units

    first, second = FakeAce2k(), FakeAce2k()
    first.name, second.name = "a", "b"
    printer = FakePrinter()
    printer.units = Units([first, second])
    hist = h.load_config(FakeConfig(printer, str(tmp_path / "a")))
    hist.writer.start = lambda: None
    printer.handlers["klippy:ready"]()
    (handler,) = printer.gcode.handlers
    handler("// ace2k a: lane 3 follow on\n// ace2k b: lane 3 follow on\n// ace2k_u1: lane b:1 x")
    assert [r["src"] for r in queued(hist)] == ["lane3", "unit", "unit"]


def test_history_records_a_unit_line_only_under_a_declared_name(tmp_path: Path) -> None:
    # "ace2k dryer:" names no unit: not recorded, as with one unit; "ace2k a: dryer:" is unit a's
    class Units:
        def __init__(self, units: list[FakeAce2k]) -> None:
            self.units = units

    first, second = FakeAce2k(), FakeAce2k()
    first.name, second.name = "a", "b"
    printer = FakePrinter()
    printer.units = Units([first, second])
    hist = h.load_config(FakeConfig(printer, str(tmp_path / "a")))
    hist.writer.start = lambda: None
    printer.handlers["klippy:ready"]()
    (handler,) = printer.gcode.handlers
    handler("// ace2k dryer: cycle done\n// ace2k a: dryer: cycle done\n// ace2k b: dryer: x")
    assert [(r["src"], r["msg"]) for r in queued(hist)] == [
        ("dryer", "ace2k a: dryer: cycle done"),
        ("unit", "ace2k b: dryer: x"),
    ]


def test_a_sample_of_one_of_several_units_carries_it() -> None:
    line = h.sample_line({"chamber": 25.0, "dryer": {}}, 1000.0, unit="b")
    assert json.loads(line)["u"] == "b"
    assert "u" not in json.loads(h.sample_line({"chamber": 25.0, "dryer": {}}, 1000.0))


def history_rig(tmp_path: Path, names: Iterable[str]) -> tuple[h.History, dict[str, FakeAce2k]]:
    class Units:
        def __init__(self, units: list[FakeAce2k]) -> None:
            self.units = units

    printer = FakePrinter()
    units = []
    for name in names:
        unit = FakeAce2k()
        unit.name = name
        units.append(unit)
    printer.units = Units(units)
    hist = h.load_config(FakeConfig(printer, str(tmp_path / "x")))
    hist.writer.start = lambda: None
    printer.handlers["klippy:ready"]()
    return hist, dict(zip(names, units))


def test_the_recorder_samples_every_unit_with_several(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(h.time, "time", lambda: 1791460000.0)
    hist, units = history_rig(tmp_path, ("a", "b"))
    units["b"].status = status(dryer=dict(state="heating", target=50))
    hist._tick(100.0)
    recs = queued(hist)
    assert sorted((r["u"], r["k"]) for r in recs) == [("a", "s"), ("b", "s")]
    assert [r["dry"] for r in recs if r["u"] == "b"] == ["heating"]


def test_each_unit_keeps_its_own_transitions_and_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"t": 1791460000.0}
    monkeypatch.setattr(h.time, "time", lambda: clock["t"])
    hist, units = history_rig(tmp_path, ("a", "b"))
    hist._tick(0.0)
    queued_before = len(queued(hist))
    lanes = [dict(mode="idle", error=None) for _ in range(4)]
    lanes[2] = dict(mode="error", error="tangled")
    units["b"].status = status(lanes=lanes, dryer=dict(state="heating", target=55))
    clock["t"] += 1
    hist._tick(1.0)
    new = queued(hist)[queued_before:]
    assert [(r["src"], r["msg"]) for r in new if r["k"] == "e"] == [
        ("unit", "ace2k b: dryer idle → heating"),
        ("unit", "lane b:3 error: tangled"),
    ]
    # b's dryer change writes b's sample, not a's
    assert [(r["u"], r["dry"]) for r in new if r["k"] == "s"] == [("b", "heating")]
    # the page's unit (a) keeps today's sources
    units["a"].status = status(dryer=dict(state="heating", target=55))
    clock["t"] += 1
    hist._tick(2.0)
    assert ("dryer", "ace2k a: dryer idle → heating") in [
        (r["src"], r["msg"]) for r in queued(hist)[queued_before:] if r["k"] == "e"
    ]


def test_one_failing_unit_leaves_the_others_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(h.time, "time", lambda: 1791460000.0)
    hist, units = history_rig(tmp_path, ("a", "b"))
    units["a"].fail = True
    assert hist._tick(0.0) == 1.0
    assert [(r["u"], r["k"]) for r in queued(hist)] == [("b", "s")]


def test_each_unit_logs_its_own_failure_once_until_it_recovers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    hist, units = history_rig(tmp_path, ("a", "b"))

    def logged(name: str) -> int:
        return sum(f"tick was not recorded (unit {name})" in r.message for r in caplog.records)

    units["a"].fail = True
    hist._tick(0.0)
    hist._tick(1.0)
    assert (logged("a"), logged("b")) == (1, 0)
    units["b"].fail = True  # a second unit failing while the first still does: its own line
    hist._tick(2.0)
    assert (logged("a"), logged("b")) == (1, 1)
    units["a"].fail = False  # a recovers on its own, b still failing
    hist._tick(3.0)
    units["a"].fail = True
    hist._tick(4.0)
    assert (logged("a"), logged("b")) == (2, 1)


def test_with_one_unit_the_written_lines_are_unchanged(
    history: HistoryRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    hist, printer, clock = history
    hist._tick(0.0)
    printer.ace2k.status = status(
        lanes=[dict(mode="error", error="tangled")] + [dict(mode="idle", error=None)] * 3,
        dryer=dict(state="heating", target=55),
    )
    clock["t"] += 1
    hist._tick(1.0)
    t = int(clock["t"])
    lines = [line for _, line in hist.writer.queue]
    assert lines[1:] == [
        f'{{"t":{t},"k":"e","lvl":"info","src":"dryer","msg":"dryer idle → heating"}}',
        f'{{"t":{t},"k":"e","lvl":"err","src":"lane1","msg":"lane 1 error: tangled"}}',
        h.sample_line(printer.ace2k.status, t),
    ]
    assert '"u"' not in "".join(lines)


def test_a_single_named_unit_keeps_its_lane_and_dryer_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # one unit named [ace2k a]: it is the page's unit, its lanes and dryer keep their sources
    clock = {"t": 1791460000.0}
    monkeypatch.setattr(h.time, "time", lambda: clock["t"])
    hist, units = history_rig(tmp_path, ("a",))
    hist._tick(0.0)
    before = len(queued(hist))
    units["a"].status = status(
        lanes=[dict(mode="error", error="tangled")] + [dict(mode="idle", error=None)] * 3,
        dryer=dict(state="heating", target=55),
    )
    clock["t"] += 1
    hist._tick(1.0)
    assert [r["src"] for r in queued(hist)[before:] if r["k"] == "e"] == ["dryer", "lane1"]


def test_a_heads_heater_off_at_the_prints_end_is_not_filed_under_the_dryer() -> None:
    # the adapter's line for a head turned off at a print's end (a held M104 S0): no dryer word
    line = "ace2k_u1: head 1: turned off at the print's end (T2's M104 S0 was held)"
    assert not h.DRYER_RE.search(line.lower())
    assert h.classify(line) == ("info", "unit")


def test_a_dryer_that_is_not_a_dict_reads_as_no_dryer() -> None:
    """A status whose dryer is not a dict (None, a string, a list) samples and snapshots as one
    with no dryer, instead of raising."""
    for dryer in ("heating", ["heating"], 3, None):
        rec = json.loads(h.sample_line({"dryer": dryer, "chamber_temp": 30.0}, 100))
        assert (rec["dry"], rec["tgt"], rec["rem"]) == (None, None, None), dryer
        snap = h.snapshot({"dryer": dryer, "lanes": []})
        assert (snap["dryer"], snap["fault"]) == (None, None), dryer
