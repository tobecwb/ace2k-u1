"""Keeps the history of the unit on disk, for the web page.

Every `interval` seconds it records one sample of the chamber, the humidity, both heater outlets
and the dryer. It also records events: every console line from ace2k or the adapter, every change
of the state of the dryer, and every lane that enters or leaves an error. There is one JSON-lines
file for each local day under `path`, and `keep_days` of them are kept.

The side of Klipper only builds lines and queues them. One writer thread does the disk I/O. A
failure stops the writing and never reaches Klipper. Nothing here runs a G-code.
"""

from __future__ import annotations

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
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer, Reactor
    from .ace2k_u1_printer_types import Config, Unit

try:
    from .ace2k_u1_common import finite
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import finite  # type: ignore[import-not-found, no-redef]

SAMPLE_KEYS = (("ch", "chamber"), ("rh", "humidity"), ("hl", "ptc_left"), ("hr", "ptc_right"))
# The lines of ace2k ("ace2k:", or "ace2k <name>:" with several units) and of the adapter. The name
# must be the name of a declared unit, so "ace2k dryer:" is not the line of any unit.
PREFIX_RE = re.compile(r"^(?:ace2k|ace2k_u1):")
# The first line of an ACEK_STATUS answer with several units ("ace2k a: <version>, link proven:
# yes"): a reply, not an event. It is tested on the line in lower case, as unit_of reads the unit.
# With one unit the header is "ace2k <version>, link proven: …", which PREFIX_RE leaves out.
STATUS_HEAD_RE = re.compile(r"^ace2k [a-z0-9_-]+: \S+, link proven: (?:yes|no)$")
# These are the same words as in web/js/history.js. web/tests/fixtures/event-lines.json pins both.
UNIT_RE = re.compile(r"^ace2k ([a-z0-9_-]+):")  # "ace2k <word>:" is a unit only if it is declared
LANE_RE = re.compile(r"\blane (?:([a-z0-9_-]+):)?(\d)\b")  # "lane 3", or "lane b:3"
DRYER_RE = re.compile(r"\b(dryer|drying|heater|fans?|flaps?)\b")
ERR_RE = re.compile(
    r"\b(refused|fault|stuck|tangled|motor_stalled|timeout|failed|assist_overrun"
    r"|unload_incomplete|stopped_sensors|error)\b|sensors stale"
)
WARN_RE = re.compile(
    r"\b(runout|stopped_link|stopped_shutdown|lowered|interrupted|notices?)\b"
    r"|not written|no answer|no spool set"
)
DAY_FILE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.jsonl$")
SEVERAL_UNITS = 2  # from this many declared units on, a line names its unit


class EventSnap(TypedDict):
    """The last event of a lane, as the transitions compare it.

    mm is filament_mm as _mm writes it. The keys of these snapshots are read by name, which
    vulture does not see. The noqa on a key says so.
    """

    seq: object  # noqa: V107
    kind: object  # noqa: V107
    mode: object  # noqa: V107
    mm: str | None  # noqa: V107


class SeenSnap(TypedDict):
    """The last event that was seen on a lane. It holds the seq of the event."""

    seq: object  # noqa: V107


class LaneSnap(TypedDict):
    """The state of a lane, as the transitions compare it."""

    err: object  # noqa: V107
    insert: bool | None  # noqa: V107
    event: EventSnap | None  # noqa: V107
    tag: object  # noqa: V107
    brand: str | None  # noqa: V107
    material: str | None  # noqa: V107
    seen: SeenSnap | None  # noqa: V107


class Snap(TypedDict):
    """The state of a unit, as the transitions compare it.

    It has the state and the fault of its dryer, and its lanes.
    """

    dryer: object  # noqa: V107
    fault: object  # noqa: V107
    lanes: list[LaneSnap]  # noqa: V107


# What the detector of a lane takes: (time, source, lane text, was, now). It returns an event line
# or None.
Detector = Callable[[float, str, str, LaneSnap, LaneSnap], "str | None"]


def _nan(value: float) -> bool:
    """Tells whether value (an int or a float) is a float NaN."""
    return isinstance(value, float) and math.isnan(value)


def _num(value: object) -> float | None:
    """Returns value rounded to two decimals, or None when it is not a finite number.

    JSON has no NaN or Infinity, and the page drops a line that carries one.
    """
    if not finite(value):
        return None
    return round(float(value), 2)


def _dump(record: Mapping[str, object]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def sample_line(status: Mapping[str, object], t: float, unit: str | None = None) -> str:
    """Returns one sample line, made from get_status() of ace2k at unix time t.

    With several units, unit is the name of the unit that was sampled, and the line carries it as
    "u".
    """
    dryer = _dict(status.get("dryer"))
    record: dict[str, object] = {"t": int(t), "k": "s"}
    if unit is not None:
        record["u"] = unit
    for key, field in SAMPLE_KEYS:
        record[key] = _num(status.get(field))
    record["dry"] = dryer.get("state")
    # ace2k keeps the target of the last cycle after it ends. Only a running cycle has a target.
    running = dryer.get("state") in ("starting", "heating")
    record["tgt"] = _num(dryer.get("target")) if running else None
    record["rem"] = _num(dryer.get("remaining"))
    return _dump(record)


def unit_of(text: str, units: Sequence[str | None] = ()) -> str | None:
    """Returns the declared unit that the text is a line of, in lower case, or None.

    An example of such a line is "ace2k b: …", in any case. This works only with several units.
    With one unit, named or not, a line belongs to the unit only as "ace2k:".
    """
    if len(units) < SEVERAL_UNITS:
        return None
    named = UNIT_RE.match(text.lower())
    declared = {str(u).lower() for u in units if u}
    return named.group(1) if named and named.group(1) in declared else None


def is_event(text: str, units: Sequence[str | None] = ()) -> bool:
    """Tells whether a line is from ace2k or from the adapter.

    A line of ace2k starts with "ace2k:", or with "ace2k <name>:" for a declared unit. The first
    line of an ACEK_STATUS answer (STATUS_HEAD_RE) is not one.
    """
    if STATUS_HEAD_RE.match(text.lower()):
        return False
    return bool(PREFIX_RE.match(text)) or unit_of(text, units) is not None


def classify(
    text: str, error: bool = False, unit: str | None = None, units: Sequence[str | None] = ()
) -> tuple[str, str]:
    """Returns the level and the source of a console line.

    The level is err, warn or info. The source is lane1..4, dryer or unit.

    unit is the unit that the page shows (its name, or None for [ace2k]). units holds the names
    that the printer declares (None for [ace2k]), and has several names when there is more than
    one unit.

    A line is filed under a lane or the dryer only when it belongs to that unit. A line of a
    unit starts with its declared name ("ace2k b:"). A lane is named with its unit ("lane b:3").
    A bare lane in "ace2k:" or "ace2k_u1:" belongs to the only unit. A line of another unit has
    the source unit.
    """
    several = len(units) > 1
    low = text.lower()
    page = unit.lower() if unit else None
    owned = unit_of(text, units)
    named = owned is not None
    # Whose line it is: the name of its unit; None for the only unit; "" with several units when
    # the line is of no one unit.
    owner = owned if named else (page if not several else "")
    lane = LANE_RE.search(low)
    lane_owner = None if lane is None else (lane.group(1) or owner)
    if (named and owner != page) or (lane and lane.group(1) and lane_owner != page):
        source = "unit"  # the line of another unit
    elif lane and lane_owner == page:
        source = f"lane{lane.group(2)}"
    else:
        source = "dryer" if DRYER_RE.search(low) else "unit"
    if error or ERR_RE.search(low):
        level = "err"
    elif WARN_RE.search(low):
        level = "warn"
    else:
        level = "info"
    return level, source


def event_line(t: float, level: str, source: str, msg: str) -> str:
    return _dump({"t": int(t), "k": "e", "lvl": level, "src": source, "msg": msg})


def output_events(
    raw: str, t: float, unit: str | None = None, units: Sequence[str | None] = ()
) -> list[str]:
    """Returns the event lines that a Klipper response (respond_raw text) carries.

    These are the lines from ace2k or from the adapter. '!! ' marks an error, and '// ' marks an
    info line. unit and units are the same as in classify.
    """
    events: list[str] = []
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        error = line.startswith("!!")
        body = line[2:].strip() if error or line.startswith("//") else line
        if is_event(body, units):
            level, source = classify(body, error, unit, units)
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
    "stopped_sensors",
)
WARN_KINDS = ("stopped_link", "stopped_shutdown", "runout", "behind", "snag", "blocked", "tail")


def _mm(value: object) -> str | None:
    """Returns filament_mm with one decimal, or None.

    Halves round up, with the same arithmetic as the page.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or _nan(value):
        return None
    if value in (float("inf"), float("-inf")):
        return None
    return f"{math.floor(value * 10 + 0.5) / 10:.1f}"


def _dict(value: object) -> dict[str, object]:
    """Returns value when it is a dict, else an empty dict.

    A field of the status that is not a dict is read as absent.
    """
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _lane_snapshot(lane: object) -> LaneSnap:
    fields = _dict(lane)
    last = fields.get("last_event")
    event = last if isinstance(last, dict) else None
    tag = _dict(fields.get("tag"))
    record = _dict(tag.get("record"))
    insert = fields.get("insert")
    return {
        "err": (fields.get("error") or "error") if fields.get("mode") == "error" else None,
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


def snapshot(status: Mapping[str, object], prev: Snap | None = None) -> Snap:
    """Returns the state that the transitions compare.

    With prev (the previous snapshot), a lane whose last_event is missing this time keeps the
    last event that was seen, so a dropout does not replay it.
    """
    dryer = _dict(status.get("dryer"))
    lanes = status.get("lanes")
    snaps = [_lane_snapshot(lane) for lane in (lanes if isinstance(lanes, list) else [])]
    if prev is not None:
        for now, was in zip(snaps, prev["lanes"]):
            if now["seen"] is None:
                now["seen"] = was["seen"]
    return {"dryer": dryer.get("state"), "fault": dryer.get("fault"), "lanes": snaps}


def _spool_event(t: float, src: str, lane: str, was: LaneSnap, now: LaneSnap) -> str | None:
    if None in (was["insert"], now["insert"]) or was["insert"] == now["insert"]:
        return None
    word = "inserted" if now["insert"] else "removed"
    return event_line(t, "info", src, f"lane {lane} spool {word}")


def _last_event_event(t: float, src: str, lane: str, was: LaneSnap, now: LaneSnap) -> str | None:
    ev = now["event"]
    if ev is None or not (was["seen"] is None or was["seen"]["seq"] != ev["seq"]):
        return None
    kind = ev["kind"] if ev["kind"] is not None else "?"
    mode = ev["mode"] if ev["mode"] is not None else "?"
    level = "err" if kind in ERR_KINDS else "warn" if kind in WARN_KINDS else "info"
    detail = mode if ev["mm"] is None else f"{mode}, {ev['mm']} mm"
    return event_line(t, level, src, f"lane {lane} {kind} ({detail})")


def _error_event(t: float, src: str, lane: str, was: LaneSnap, now: LaneSnap) -> str | None:
    if was["err"] == now["err"]:
        return None
    if now["err"] is not None:
        return event_line(t, "err", src, f"lane {lane} error: {now['err']}")
    return event_line(t, "info", src, f"lane {lane} error cleared")


def _tag_event(t: float, src: str, lane: str, was: LaneSnap, now: LaneSnap) -> str | None:
    if was["tag"] is None or now["tag"] == was["tag"]:
        return None
    if now["tag"] == "read":
        who = " ".join(str(x) for x in (now["brand"], now["material"]) if x)
        msg = f"lane {lane} tag read: {who}" if who else f"lane {lane} tag read"
        return event_line(t, "info", src, msg)
    if now["tag"] == "no_tag":
        return event_line(t, "info", src, f"lane {lane} no tag")
    return None


def _lane_events(t: float, src: str, lane: str, was: LaneSnap, now: LaneSnap) -> list[str]:
    """Returns the event lines of a lane between two snapshots (was, now) at time t.

    src is the source that the lines are filed under. lane is the text of the lane.
    """
    detectors: tuple[Detector, ...] = (_spool_event, _last_event_event, _error_event, _tag_event)
    found = (detect(t, src, lane, was, now) for detect in detectors)
    return [line for line in found if line is not None]


def dryer_changed(prev: Snap, snap: Snap) -> bool:
    """Tells whether the state of the dryer differs between two snapshots.

    A state that appears from nothing (a status that is still filling in) is not a change. A state
    that vanishes is not a change either.
    """
    return snap["dryer"] != prev["dryer"] and None not in (snap["dryer"], prev["dryer"])


def transitions(
    prev: Snap | None,
    status: Mapping[str, object],
    t: float,
    unit: str | None = None,
    own: bool = True,
) -> tuple[list[str], Snap]:
    """Returns the event lines and the new snapshot.

    The events are the changes since prev, which is the snapshot of the previous call. With None
    there are no events. The events are these.
    - The state change of the dryer.
    - For each lane: a spool inserted or removed, a new last_event, an error entered or left,
      and a tag read or not found.
    A state that appears from nothing (a status that is still filling in), or that vanishes, is
    not a transition.

    unit is the name of one of several units. The text names its lanes like "b:3". own tells
    whether it is the unit that the page shows.
    """
    snap = snapshot(status, prev)
    events: list[str] = []
    if prev is None:
        return events, snap
    dsrc = "dryer" if own else "unit"
    label = "" if unit is None else f"ace2k {unit}: "  # with several units, whose dryer it is
    if dryer_changed(prev, snap):
        if snap["dryer"] == "fault":
            msg = f"{label}dryer fault: {snap['fault'] or '?'}"
            events.append(event_line(t, "err", dsrc, msg))
        else:
            msg = f"{label}dryer {prev['dryer']} → {snap['dryer']}"
            events.append(event_line(t, "info", dsrc, msg))
    for n, (was, now) in enumerate(zip(prev["lanes"], snap["lanes"]), 1):
        # With several units, a lane is written like "b:3" in the text. Its events belong to the
        # unit of the page (src lane3) only for the unit that the page shows. The events of another
        # unit have the source unit.
        src = f"lane{n}" if own else "unit"
        lane = f"{unit}:{n}" if unit is not None else str(n)
        events.extend(_lane_events(t, src, lane, was, now))
    return events, snap


def day_name(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t)) + ".jsonl"


def stale_files(names: Iterable[str], today: datetime.date, keep_days: int) -> list[str]:
    """Returns the day files among names that are dated more than keep_days days before today.

    The last keep_days days end today, and they are kept.
    """
    first_kept = today - datetime.timedelta(days=keep_days - 1)
    stale: list[str] = []
    for name in sorted(names):
        match = DAY_FILE.match(name)
        if match and datetime.date(*map(int, match.groups())) < first_kept:
            stale.append(name)
    return stale


QUEUE_MAX = 10000
TICK_S = 1.0
STOP_WAIT_S = 1.0  # how long stop() waits for the writer thread to end
DEFAULT_PATH = "/home/lava/printer_data/logs/ace2k"


class Writer:
    """Appends queued lines to the day files, with one thread.

    The thread appends queued (day file, line) pairs under path. The queue is bounded and drops
    its oldest entries. The first write failure is kept in .error, logged once, and ends the
    writing. A failure to delete an old day file never stops the writing. It is logged once.
    """

    def __init__(self, path: str, keep_days: int) -> None:
        self.path = path
        self.keep_days = keep_days
        self.queue: collections.deque[tuple[str, str]] = collections.deque(maxlen=QUEUE_MAX)
        self.cond = threading.Condition()
        self.written = 0
        self.dropped = 0
        self.error: str | None = None
        self.pruned_day: str | None = None
        self.prune_failed = False
        self.thread: threading.Thread | None = None
        self.stopping = False

    def put(self, line: str, t: float) -> None:
        if self.error is not None:
            return
        with self.cond:
            if self.stopping:
                return
            if len(self.queue) == self.queue.maxlen:
                self.dropped += 1
            self.queue.append((day_name(t), line))
            self.cond.notify()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="ace2k_u1_history", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Ends the thread once what is queued is written.

        It waits at most STOP_WAIT_S for the thread to end. The restart of Klipper builds a new
        writer, which must not append to the same day file beside the old one. A thread that is
        still writing then is left running, and a log line says so.
        """
        with self.cond:
            self.stopping = True
            self.cond.notify_all()
        thread = self.thread
        if thread is None:
            return
        thread.join(STOP_WAIT_S)
        if thread.is_alive():
            logging.warning(
                "ace2k_u1_history: the old writer still writes after %g s: left running",
                STOP_WAIT_S,
            )

    def _run(self) -> None:
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

    def flush(self, batch: list[tuple[str, str]]) -> bool:
        """Writes batch. Returns False after a failure."""
        try:
            os.makedirs(self.path, exist_ok=True)
            by_day: dict[str, list[str]] = {}
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
        except Exception as e:  # any failure ends the writing, and never reaches Klipper
            self.error = str(e)
            with self.cond:
                self.queue.clear()
            logging.warning("ace2k_u1_history: writing stopped: %s", e)
            return False

    def _prune_safely(self, day: str) -> None:
        try:
            self._prune(day)
        except Exception as e:  # old files left behind cost disk space, but never the history
            if not self.prune_failed:
                logging.warning("ace2k_u1_history: pruning old day files failed: %s", e)
            self.prune_failed = True

    def _prune(self, day: str) -> None:
        today = datetime.date.fromisoformat(day[:10])
        names = os.listdir(self.path)
        if any(
            m and datetime.date(*map(int, m.groups())) > today for m in map(DAY_FILE.match, names)
        ):
            return  # a file from the future means that the clock is wrong, so nothing is deleted
        for name in stale_files(names, today, self.keep_days):
            with contextlib.suppress(FileNotFoundError):  # already gone, so nothing to do
                os.remove(os.path.join(self.path, name))


class History:
    def __init__(self, config: Config) -> None:
        self.printer: Printer = config.get_printer()
        self.reactor: Reactor = self.printer.get_reactor()
        self.interval = config.getint("interval", 30, minval=5, maxval=600)
        keep_days = config.getint("keep_days", 7, minval=1, maxval=90)
        path = os.path.expanduser(config.get("path", DEFAULT_PATH))
        self.writer = Writer(path, keep_days)
        self.ace2k: Unit | None = None
        self.unit: str | None = None  # the unit that the page shows: its name, None for [ace2k]
        self.units: list[str | None] = []  # the names of the printer's units (None for [ace2k])
        # One (unit, name) pair for each unit that is sampled. The name is None with one unit.
        self.sources: list[tuple[Unit | None, str | None]] = []
        self.prev: dict[str | None, Snap] = {}  # the previous snapshot, by name
        self.last_sample: dict[str | None, float] = {}  # the time of the last sample, by name
        self.failing: dict[str, bool] = {}
        self.printer.register_event_handler("klippy:ready", self._on_ready)
        self.printer.register_event_handler("klippy:disconnect", self.writer.stop)
        self.printer.lookup_object("gcode").register_output_handler(self._on_output)

    def _on_ready(self) -> None:
        units = self.printer.lookup_object("ace2k_units", None)
        listed: list[Unit] = list(units.units) if units is not None else []
        first = listed[0] if listed else None
        self.ace2k = first if first is not None else self.printer.lookup_object("ace2k", None)
        self.unit = getattr(first, "name", None)
        self.units = [getattr(u, "name", None) for u in listed]
        if len(listed) > 1:
            # ace2k_units refuses an unnamed unit among several, so every name here is a string.
            self.sources = [(u, getattr(u, "name", None)) for u in listed]
        else:
            self.sources = [(self.ace2k, None)]
        self.writer.start()
        self.reactor.register_timer(self._tick, self.reactor.NOW)

    def _on_output(self, msg: str) -> None:
        try:
            t = time.time()
            for line in output_events(msg, t, self.unit, self.units):
                self.writer.put(line, t)
            self.failing["output"] = False
        except Exception:  # a listener of every response must never raise
            self._log_once("output", "an output line was not recorded")

    def _tick(self, eventtime: float) -> float:
        for ace2k, name in self.sources:  # a unit that fails leaves the others recorded
            # Each unit has its own site. A failure is logged once for each unit, until that unit
            # recovers.
            site = "tick" if name is None else f"tick {name}"
            try:
                if ace2k is not None:
                    self._tick_unit(eventtime, ace2k, name)
            except Exception:  # the timer must keep running
                what = "a tick was not recorded"
                self._log_once(site, what if name is None else f"{what} (unit {name})")
                continue
            self.failing[site] = False
        return eventtime + TICK_S

    def _tick_unit(self, eventtime: float, ace2k: Unit, name: str | None) -> None:
        prev = self.prev.get(name)
        status = ace2k.get_status(eventtime)
        t = time.time()
        # One unit (name None, or named like [ace2k a]) is the unit of the page. With several
        # units, the first one is.
        own = name is None or name == self.unit
        events, snap = transitions(prev, status, t, name, own)
        changed = prev is not None and dryer_changed(prev, snap)
        # A change of the state of the dryer also writes a sample, so the first sample of a cycle
        # is its start.
        last = self.last_sample.get(name)
        due = last is None or eventtime - last >= self.interval
        for line in events:
            self.writer.put(line, t)
        if changed or due:
            self.writer.put(sample_line(status, t, name), t)
            self.last_sample[name] = eventtime
        self.prev[name] = snap  # only once everything is queued, so a failure retries the events

    def _log_once(self, site: str, what: str) -> None:
        """Logs a failure of site with its traceback, if the last call of the site succeeded."""
        if not self.failing.get(site):
            logging.exception("ace2k_u1_history: %s", what)
        self.failing[site] = True

    def get_status(self, _eventtime: float) -> dict[str, object]:
        w = self.writer
        return {
            "path": w.path,
            "written": w.written,
            "dropped": w.dropped,
            "error": w.error,
            "keep_days": w.keep_days,
        }


def load_config(config: Config) -> History:
    return History(config)
