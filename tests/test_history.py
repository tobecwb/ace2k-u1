"""The recorder: pure helpers first, then the writer and the glue."""

import datetime
import json
import os
from pathlib import Path

import ace2k_u1_history as h
import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "web" / "tests" / "fixtures" / "event-lines.json"
TRANSITIONS = FIXTURE.with_name("transitions.json")


def status(**kw):
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


def test_sample_line_has_exactly_the_keys_rounded_and_null_for_missing():
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


def test_sample_line_target_only_while_starting_or_heating():
    for state, want in (
        ("starting", 45.0),
        ("heating", 45.0),
        ("cooldown", None),
        ("idle", None),
        ("fault", None),
    ):
        rec = json.loads(h.sample_line(status(dryer=dict(state=state, target=45)), 5))
        assert rec["tgt"] == want, state


def test_sample_line_carries_the_remaining_minutes():
    rec = json.loads(h.sample_line(status(dryer=dict(state="heating", target=45, remaining=44)), 5))
    assert rec["rem"] == 44.0 and rec["tgt"] == 45.0


def test_sample_line_with_no_dryer_and_a_bool():
    rec = json.loads(h.sample_line({"chamber": True}, 5))
    assert rec["ch"] is None and rec["dry"] is None and rec["tgt"] is None


def test_output_events_match_the_shared_fixture():
    for case in json.loads(FIXTURE.read_text(encoding="utf-8")):
        got = [json.loads(line) for line in h.output_events(case["raw"], 100)]
        want = [dict(t=100, k="e", **e) for e in case["events"]]
        assert got == want, case["raw"]


def test_transitions_match_the_shared_fixture():
    for case in json.loads(TRANSITIONS.read_text(encoding="utf-8")):
        prev = None
        if case["prev_status"] is not None:
            _, prev = h.transitions(None, case["prev_status"], 100)
        for between in case.get("between", []):
            _, prev = h.transitions(prev, between, 100)
        events, _ = h.transitions(prev, case["status"], 100)
        want = [dict(t=100, k="e", **e) for e in case["events"]]
        assert [json.loads(line) for line in events] == want, case["name"]


def test_transitions_nan_filament_mm_is_missing():
    _, snap = h.transitions(None, status(), 1)
    lane = dict(
        mode="idle", last_event=dict(kind="loaded", mode="loading", seq=1, filament_mm=float("nan"))
    )
    events, _ = h.transitions(snap, status(lanes=[lane]), 2)
    assert [json.loads(e)["msg"] for e in events] == ["lane 1 loaded (loading)"]


def test_transitions_first_call_is_silent_and_returns_a_snapshot():
    events, snap = h.transitions(None, status(), 1)
    assert events == [] and snap["dryer"] == "idle"


def test_transitions_dryer_change_and_fault():
    _, snap = h.transitions(None, status(), 1)
    events, snap = h.transitions(snap, status(dryer=dict(state="heating", target=55)), 2)
    assert [json.loads(e) for e in events] == [
        {"t": 2, "k": "e", "lvl": "info", "src": "dryer", "msg": "dryer idle → heating"}
    ]
    events, _ = h.transitions(snap, status(dryer=dict(state="fault", fault="not_heating")), 3)
    assert [json.loads(e)["msg"] for e in events] == ["dryer fault: not_heating"]
    assert json.loads(events[0])["lvl"] == "err"


def test_transitions_lane_error_enter_and_leave():
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


def test_day_name_is_the_local_date():
    t = datetime.datetime(2026, 10, 8, 23, 59).timestamp()
    assert h.day_name(t) == "2026-10-08.jsonl"


def test_stale_files_keeps_keep_days_including_today_and_ignores_other_names():
    names = ["2026-10-08.jsonl", "2026-10-02.jsonl", "2026-10-01.jsonl", "notes.txt", "x.jsonl"]
    assert h.stale_files(names, datetime.date(2026, 10, 8), 7) == ["2026-10-01.jsonl"]


def test_writer_appends_by_day_and_creates_the_directory(tmp_path):
    w = h.Writer(str(tmp_path / "ace2k"), keep_days=7)
    assert w.flush(
        [("2026-10-08.jsonl", "a"), ("2026-10-08.jsonl", "b"), ("2026-10-09.jsonl", "c")]
    )
    assert (tmp_path / "ace2k" / "2026-10-08.jsonl").read_text() == "a\nb\n"
    assert (tmp_path / "ace2k" / "2026-10-09.jsonl").read_text() == "c\n"
    assert w.written == 3


def test_writer_prunes_stale_days_when_a_new_day_starts(tmp_path):
    d = tmp_path / "ace2k"
    d.mkdir()
    (d / "2026-09-30.jsonl").write_text("old\n")
    (d / "2026-10-05.jsonl").write_text("kept\n")
    w = h.Writer(str(d), keep_days=7)
    w.flush([("2026-10-08.jsonl", "x")])
    assert sorted(os.listdir(d)) == ["2026-10-05.jsonl", "2026-10-08.jsonl"]


def test_writer_failure_sets_error_and_stops(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("")
    w = h.Writer(str(blocker), keep_days=7)  # a file where the directory should be
    assert w.flush([("2026-10-08.jsonl", "x")]) is False
    assert w.error
    w.put("y", 0)
    assert len(w.queue) == 0
    assert sum("writing stopped" in r.message for r in caplog.records) == 1


def test_writer_queue_drops_the_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "QUEUE_MAX", 3)
    w = h.Writer(str(tmp_path), keep_days=7)
    for i in range(5):
        w.put(str(i), 0)
    assert [line for _, line in w.queue] == ["2", "3", "4"]
    assert w.dropped == 2


class FakeReactor:
    NOW = 0.0

    def __init__(self):
        self.timers = []

    def register_timer(self, cb, waketime):
        self.timers.append(cb)
        return cb


class FakeGcode:
    def __init__(self):
        self.handlers = []
        self.scripts = []

    def register_output_handler(self, cb):
        self.handlers.append(cb)

    def run_script(self, script):  # must never be called
        self.scripts.append(script)


class FakeAce2k:
    def __init__(self):
        self.status = status()
        self.fail = False

    def get_status(self, eventtime):
        if self.fail:
            raise RuntimeError("boom")
        return self.status


class FakePrinter:
    def __init__(self):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.ace2k = FakeAce2k()
        self.handlers = {}

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, name, cb):
        self.handlers[name] = cb

    def lookup_object(self, name, default=None):
        return {"gcode": self.gcode, "ace2k": self.ace2k}.get(name, default)


class FakeConfig:
    def __init__(self, printer, path):
        self.printer = printer
        self.values = {"path": path}

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getint(self, name, default=None, minval=None, maxval=None):
        return int(self.values.get(name, default))


@pytest.fixture
def history(tmp_path, monkeypatch):
    printer = FakePrinter()
    clock = {"t": 1791460000.0}
    monkeypatch.setattr(h.time, "time", lambda: clock["t"])
    hist = h.load_config(FakeConfig(printer, str(tmp_path / "ace2k")))
    hist.writer.start = lambda: None  # no thread in the tests: flush by hand
    printer.handlers["klippy:ready"]()
    return hist, printer, clock


def queued(hist):
    return [json.loads(line) for _, line in hist.writer.queue]


def test_history_samples_once_per_interval_and_ticks_every_second(history):
    hist, printer, clock = history
    assert hist._tick(0.0) == 1.0
    clock["t"] += 10
    hist._tick(10.0)
    clock["t"] += 25
    hist._tick(35.0)
    assert [r["k"] for r in queued(hist)] == ["s", "s"]


def test_history_records_transitions_between_samples(history):
    hist, printer, clock = history
    hist._tick(0.0)
    printer.ace2k.status = status(dryer=dict(state="heating", target=55))
    clock["t"] += 1
    hist._tick(1.0)
    assert [r["msg"] for r in queued(hist) if r["k"] == "e"] == ["dryer idle → heating"]


def test_history_writes_a_sample_at_each_dryer_state_change(history):
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


def test_history_output_handler_queues_ace2k_lines_only(history):
    hist, printer, clock = history
    (handler,) = printer.gcode.handlers
    handler("// ace2k: drying at 55 °C for 4 h\n// echo: other")
    assert [r["msg"] for r in queued(hist)] == ["ace2k: drying at 55 °C for 4 h"]


def test_history_never_raises_and_runs_no_gcode(history):
    hist, printer, clock = history
    printer.ace2k.fail = True
    assert hist._tick(0.0) == 1.0
    printer.gcode.handlers[0](None)  # not a string: caught
    assert printer.gcode.scripts == []


def test_history_status(history, tmp_path):
    hist, printer, clock = history
    assert hist.get_status(0) == {
        "path": str(tmp_path / "ace2k"),
        "written": 0,
        "dropped": 0,
        "error": None,
        "keep_days": 7,
    }


def test_history_logs_a_persistent_failure_once_until_it_recovers(history, caplog):
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


def test_writer_stop_writes_the_rest_and_ends_the_thread(tmp_path):
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


def test_history_registers_the_writer_stop_on_disconnect(history):
    hist, printer, clock = history
    assert printer.handlers["klippy:disconnect"] == hist.writer.stop


def test_writer_prune_deletes_nothing_when_a_file_is_from_the_future(tmp_path):
    d = tmp_path / "ace2k"
    d.mkdir()
    (d / "2026-09-01.jsonl").write_text("old\n")
    (d / "2030-01-01.jsonl").write_text("future\n")
    w = h.Writer(str(d), keep_days=7)
    w.flush([("2026-10-08.jsonl", "x")])
    assert sorted(os.listdir(d)) == ["2026-09-01.jsonl", "2026-10-08.jsonl", "2030-01-01.jsonl"]


def test_writer_prunes_across_midnight(tmp_path):
    d = tmp_path / "ace2k"
    w = h.Writer(str(d), keep_days=2)
    assert w.flush([("2026-10-07.jsonl", "a")])
    assert w.flush([("2026-10-08.jsonl", "b")])
    assert sorted(os.listdir(d)) == ["2026-10-07.jsonl", "2026-10-08.jsonl"]
    assert w.flush([("2026-10-09.jsonl", "c")])
    assert sorted(os.listdir(d)) == ["2026-10-08.jsonl", "2026-10-09.jsonl"]


def test_transitions_a_dryer_state_appearing_is_not_a_transition():
    _, snap = h.transitions(None, status(dryer={}), 1)
    assert snap["dryer"] is None
    events, snap = h.transitions(snap, status(), 2)
    assert events == [] and snap["dryer"] == "idle"
    events, snap = h.transitions(snap, status(dryer={}), 3)
    assert events == []


def test_transitions_lanes_appearing_or_unchanged_errors_are_not_transitions():
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


def test_writer_prune_ignores_a_file_already_gone(tmp_path, monkeypatch):
    d = tmp_path / "ace2k"
    d.mkdir()
    (d / "2026-09-01.jsonl").write_text("old\n")
    w = h.Writer(str(d), keep_days=7)
    real_remove = os.remove

    def gone(p):
        real_remove(p)
        raise FileNotFoundError(p)

    monkeypatch.setattr(h.os, "remove", gone)
    assert w.flush([("2026-10-08.jsonl", "x")]) is True
    assert w.error is None
    assert (d / "2026-10-08.jsonl").read_text() == "x\n"


def test_writer_prune_error_is_logged_once_and_writing_goes_on(tmp_path, monkeypatch, caplog):
    d = tmp_path / "ace2k"
    d.mkdir()
    (d / "2026-09-01.jsonl").write_text("old\n")

    def denied(p):
        raise PermissionError(p)

    monkeypatch.setattr(h.os, "remove", denied)
    w = h.Writer(str(d), keep_days=7)
    assert w.flush([("2026-10-08.jsonl", "a")]) is True
    assert w.flush([("2026-10-09.jsonl", "b")]) is True
    assert w.error is None and w.written == 2
    assert (d / "2026-10-09.jsonl").read_text() == "b\n"
    assert sum("pruning" in r.message for r in caplog.records) == 1


def test_history_a_failing_put_loses_no_transition(history):
    hist, printer, clock = history
    hist._tick(0.0)
    printer.ace2k.status = status(dryer=dict(state="heating", target=55))
    real = hist.writer.put
    calls = []

    def flaky(line, t):
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
