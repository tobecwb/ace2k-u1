"""ace2k_u1_history: the unit's history on disk, for the web page.

Every `interval` seconds one sample of the chamber, the humidity, both heater outlets and the
dryer; every console line from ace2k or the adapter, every change of the dryer's state and every
lane entering or leaving an error, as an event. One JSON-lines file per local day under `path`,
`keep_days` of them kept. Klipper's side only builds lines and queues them; one writer thread does
the disk I/O, and a failure stops the writing without ever reaching Klipper. Nothing here runs a
G-code."""

import collections
import contextlib
import datetime
import json
import logging
import math
import os
import re
import threading
import time

SAMPLE_KEYS = (("ch", "chamber"), ("rh", "humidity"), ("hl", "ptc_left"), ("hr", "ptc_right"))
PREFIXES = ("ace2k:", "ace2k_u1:")
# the same words as web/js/history.js; web/tests/fixtures/event-lines.json pins both
LANE_RE = re.compile(r"\blane (\d)\b")
DRYER_RE = re.compile(r"\b(dryer|drying|heater|fans?|flaps?)\b")
ERR_RE = re.compile(
    r"\b(refused|fault|stuck|tangled|motor_stalled|timeout|failed|assist_overrun"
    r"|unload_incomplete|error)\b"
)
WARN_RE = re.compile(
    r"\b(runout|stopped_link|stopped_shutdown|lowered|interrupted|notices?)\b"
    r"|not written|no answer"
)
DAY_FILE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.jsonl$")


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return round(float(value), 2)


def _dump(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def sample_line(status, t):
    """One sample from ace2k's get_status() at unix time t."""
    dryer = status.get("dryer") or {}
    record = {"t": int(t), "k": "s"}
    for key, field in SAMPLE_KEYS:
        record[key] = _num(status.get(field))
    record["dry"] = dryer.get("state")
    # ace2k keeps the last cycle's target after it ends: only a running cycle has one
    running = dryer.get("state") in ("starting", "heating")
    record["tgt"] = _num(dryer.get("target")) if running else None
    record["rem"] = _num(dryer.get("remaining"))
    return _dump(record)


def classify(text, error=False):
    """(level, source) of a console line: err / warn / info; lane1..4 / dryer / unit."""
    low = text.lower()
    lane = LANE_RE.search(low)
    source = "dryer" if DRYER_RE.search(low) else "unit"
    if lane:
        source = f"lane{lane.group(1)}"
    if error or ERR_RE.search(low):
        level = "err"
    elif WARN_RE.search(low):
        level = "warn"
    else:
        level = "info"
    return level, source


def event_line(t, level, source, msg):
    return _dump({"t": int(t), "k": "e", "lvl": level, "src": source, "msg": msg})


def output_events(raw, t):
    """The event lines a Klipper response (respond_raw text) carries: each of its lines from ace2k
    or the adapter; '!! ' marks an error, '// ' an info line."""
    events = []
    for line in raw.split("\n"):
        line = line.strip()
        error = line.startswith("!!")
        body = line[2:].strip() if error or line.startswith("//") else line
        if body.startswith(PREFIXES):
            level, source = classify(body, error)
            events.append(event_line(t, level, source, body))
    return events


ERR_KINDS = (
    "stuck",
    "tangled",
    "motor_stalled",
    "timeout",
    "assist_stall",
    "assist_overrun",
    "unload_incomplete",
)
WARN_KINDS = ("stopped_link", "stopped_shutdown", "runout", "behind", "snag", "blocked", "tail")


def _mm(value):
    """filament_mm to one decimal (halves up, the same arithmetic as the page), or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    if value in (float("inf"), float("-inf")):
        return None
    return f"{math.floor(value * 10 + 0.5) / 10:.1f}"


def _text(value):
    return value if isinstance(value, str) and value else None


def _lane_snapshot(lane):
    lane = lane if isinstance(lane, dict) else {}
    event = lane.get("last_event")
    event = event if isinstance(event, dict) else None
    tag = lane.get("tag")
    tag = tag if isinstance(tag, dict) else {}
    record = tag.get("record")
    record = record if isinstance(record, dict) else {}
    insert = lane.get("insert")
    return {
        "err": (lane.get("error") or "error") if lane.get("mode") == "error" else None,
        "insert": insert if isinstance(insert, bool) else None,
        "event": (
            None
            if event is None
            else {
                "seq": event.get("seq"),
                "kind": event.get("kind"),
                "mode": event.get("mode"),
                "mm": _mm(event.get("filament_mm")),
            }
        ),
        "tag": tag.get("state"),
        "brand": _text(record.get("brand")),
        "material": _text(record.get("material")),
        "seen": None if event is None else {"seq": event.get("seq")},
    }


def snapshot(status, prev=None):
    """The state the transitions compare. With prev (the previous snapshot) a lane whose
    last_event is missing this time keeps the last event seen, so a dropout does not replay it."""
    dryer = status.get("dryer") or {}
    lanes = status.get("lanes")
    lanes = lanes if isinstance(lanes, list) else []
    snaps = [_lane_snapshot(lane) for lane in lanes]
    if prev is not None:
        for now, was in zip(snaps, prev["lanes"]):
            if now["seen"] is None:
                now["seen"] = was["seen"]
    return {"dryer": dryer.get("state"), "fault": dryer.get("fault"), "lanes": snaps}


def _lane_events(t, n, was, now):
    src = f"lane{n}"
    events = []
    if None not in (was["insert"], now["insert"]) and was["insert"] != now["insert"]:
        word = "inserted" if now["insert"] else "removed"
        events.append(event_line(t, "info", src, f"lane {n} spool {word}"))
    ev = now["event"]
    if ev is not None and (was["seen"] is None or was["seen"]["seq"] != ev["seq"]):
        kind = ev["kind"] if ev["kind"] is not None else "?"
        mode = ev["mode"] if ev["mode"] is not None else "?"
        level = "err" if kind in ERR_KINDS else "warn" if kind in WARN_KINDS else "info"
        detail = mode if ev["mm"] is None else f"{mode}, {ev['mm']} mm"
        events.append(event_line(t, level, src, f"lane {n} {kind} ({detail})"))
    if was["err"] != now["err"]:
        if now["err"] is not None:
            events.append(event_line(t, "err", src, f"lane {n} error: {now['err']}"))
        else:
            events.append(event_line(t, "info", src, f"lane {n} error cleared"))
    if was["tag"] is not None and now["tag"] != was["tag"]:
        if now["tag"] == "read":
            who = " ".join(str(x) for x in (now["brand"], now["material"]) if x)
            msg = f"lane {n} tag read: {who}" if who else f"lane {n} tag read"
            events.append(event_line(t, "info", src, msg))
        elif now["tag"] == "no_tag":
            events.append(event_line(t, "info", src, f"lane {n} no tag"))
    return events


def transitions(prev, status, t):
    """(event lines, snapshot): the dryer's state change and, per lane, a spool inserted or
    removed, a new last_event, an error entered or left and a tag read or not found since prev
    (the snapshot of the previous call; None → no events). A state appearing from nothing (a
    status still filling in) or vanishing is not a transition."""
    snap = snapshot(status, prev)
    events = []
    if prev is None:
        return events, snap
    if snap["dryer"] != prev["dryer"] and None not in (snap["dryer"], prev["dryer"]):
        if snap["dryer"] == "fault":
            events.append(event_line(t, "err", "dryer", f"dryer fault: {snap['fault'] or '?'}"))
        else:
            events.append(
                event_line(t, "info", "dryer", f"dryer {prev['dryer']} → {snap['dryer']}")
            )
    for i, (was, now) in enumerate(zip(prev["lanes"], snap["lanes"])):
        events.extend(_lane_events(t, i + 1, was, now))
    return events, snap


def day_name(t):
    return time.strftime("%Y-%m-%d", time.localtime(t)) + ".jsonl"


def stale_files(names, today, keep_days):
    """The day files among names dated before the keep_days that end today."""
    first_kept = today - datetime.timedelta(days=keep_days - 1)
    stale = []
    for name in sorted(names):
        match = DAY_FILE.match(name)
        if match and datetime.date(*map(int, match.groups())) < first_kept:
            stale.append(name)
    return stale


QUEUE_MAX = 10000
TICK_S = 1.0
DEFAULT_PATH = "/home/lava/printer_data/logs/ace2k"


class Writer:
    """One thread appends queued (day file, line) pairs under path. The queue is bounded and drops
    its oldest entries; the first write failure is kept in .error, logged once, and ends the
    writing. A failure to delete an old day file never stops it: it is logged once."""

    def __init__(self, path, keep_days):
        self.path = path
        self.keep_days = keep_days
        self.queue = collections.deque(maxlen=QUEUE_MAX)
        self.cond = threading.Condition()
        self.written = 0
        self.dropped = 0
        self.error = None
        self.pruned_day = None
        self.prune_failed = False
        self.thread = None
        self.stopping = False

    def put(self, line, t):
        if self.error is not None:
            return
        with self.cond:
            if self.stopping:
                return
            if len(self.queue) == self.queue.maxlen:
                self.dropped += 1
            self.queue.append((day_name(t), line))
            self.cond.notify()

    def start(self):
        self.thread = threading.Thread(target=self._run, name="ace2k_u1_history", daemon=True)
        self.thread.start()

    def stop(self):
        """End the thread once what is queued is written (Klipper's restart builds a new one)."""
        with self.cond:
            self.stopping = True
            self.cond.notify_all()

    def _run(self):
        while True:
            with self.cond:
                while not self.queue and not self.stopping:
                    self.cond.wait()
                batch = list(self.queue)
                self.queue.clear()
                stopping = self.stopping
            if batch and not self.flush(batch):
                return
            if stopping:
                return

    def flush(self, batch):
        """Write batch; False after a failure."""
        try:
            os.makedirs(self.path, exist_ok=True)
            by_day = {}
            for day, line in batch:
                by_day.setdefault(day, []).append(line)
            for day, lines in by_day.items():
                if day != self.pruned_day:
                    self._prune_safely(day)
                    self.pruned_day = day
                with open(os.path.join(self.path, day), "a", encoding="utf-8") as f:
                    f.write("".join(line + "\n" for line in lines))
                self.written += len(lines)
            return True
        except Exception as e:  # any failure ends the writing, never Klipper
            self.error = str(e)
            with self.cond:
                self.queue.clear()
            logging.warning("ace2k_u1_history: writing stopped: %s", e)
            return False

    def _prune_safely(self, day):
        try:
            self._prune(day)
        except Exception as e:  # old files left behind cost disk space, never the history
            if not self.prune_failed:
                logging.warning("ace2k_u1_history: pruning old day files failed: %s", e)
            self.prune_failed = True

    def _prune(self, day):
        today = datetime.date.fromisoformat(day[:10])
        names = os.listdir(self.path)
        if any(
            m and datetime.date(*map(int, m.groups())) > today for m in map(DAY_FILE.match, names)
        ):
            return  # a file from the future: the clock is wrong, delete nothing
        for name in stale_files(names, today, self.keep_days):
            with contextlib.suppress(FileNotFoundError):  # already gone: nothing to do
                os.remove(os.path.join(self.path, name))


class History:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.interval = config.getint("interval", 30, minval=5, maxval=600)
        keep_days = config.getint("keep_days", 7, minval=1, maxval=90)
        path = os.path.expanduser(config.get("path", DEFAULT_PATH))
        self.writer = Writer(path, keep_days)
        self.ace2k = None
        self.prev = None
        self.last_sample = None
        self.failing = {}
        self.printer.register_event_handler("klippy:ready", self._on_ready)
        self.printer.register_event_handler("klippy:disconnect", self.writer.stop)
        self.printer.lookup_object("gcode").register_output_handler(self._on_output)

    def _on_ready(self):
        self.ace2k = self.printer.lookup_object("ace2k", None)
        self.writer.start()
        self.reactor.register_timer(self._tick, self.reactor.NOW)

    def _on_output(self, msg):
        try:
            t = time.time()
            for line in output_events(msg, t):
                self.writer.put(line, t)
            self.failing["output"] = False
        except Exception:  # a listener of every response must never raise
            self._log_once("output", "an output line was not recorded")

    def _tick(self, eventtime):
        try:
            if self.ace2k is not None:
                status = self.ace2k.get_status(eventtime)
                t = time.time()
                events, snap = transitions(self.prev, status, t)
                changed = (
                    self.prev is not None
                    and None not in (snap["dryer"], self.prev["dryer"])
                    and snap["dryer"] != self.prev["dryer"]
                )
                # a dryer state change also writes a sample, so a cycle's first sample is its start
                due = self.last_sample is None or eventtime - self.last_sample >= self.interval
                for line in events:
                    self.writer.put(line, t)
                if changed or due:
                    self.writer.put(sample_line(status, t), t)
                    self.last_sample = eventtime
                self.prev = snap  # only once everything is queued: a failure retries the events
            self.failing["tick"] = False
        except Exception:  # the timer must keep running
            self._log_once("tick", "a tick was not recorded")
        return eventtime + TICK_S

    def _log_once(self, site, what):
        """Log a failure of site with its traceback only if the site's last call succeeded."""
        if not self.failing.get(site):
            logging.exception("ace2k_u1_history: %s", what)
        self.failing[site] = True

    def get_status(self, eventtime):
        w = self.writer
        return {
            "path": w.path,
            "written": w.written,
            "dropped": w.dropped,
            "error": w.error,
            "keep_days": w.keep_days,
        }


def load_config(config):
    return History(config)
