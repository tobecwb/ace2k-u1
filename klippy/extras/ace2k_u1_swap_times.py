"""Keeps the long-term average of each swap time, measured on the printer, for the web page.

The swapper and the background swap give it one sample per swap or dock step (seconds). Each
average moves a share SWAP_TIME_WEIGHT of the way to a new sample. The first sample of a
quantity replaces its seed. The averages are kept in swap-times.json in the history's folder, so
they survive a restart, and the status of [ace2k_u1] publishes them (swap_times). One writer
thread does the disk I/O. A failure is logged once and never reaches Klipper. Nothing here runs a
G-code.

PrintStops counts the stops of the print that runs, measured, for the page's time left (status
print_stops). It keeps nothing on disk.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer
    from .ace2k_u1_printer_types import Reactor

SWAP_TIME_WEIGHT = 0.2  # a new sample moves an average this share of the way to it
SAMPLE_MAX_S = 600.0  # a sample over this is a failure or a wait, not a swap: it is dropped
FILE_NAME = "swap-times.json"
FORMAT = 1  # the file's "v"
STOP_WAIT_S = 1.0  # how long stop() waits for the writer thread
KINDS = ("inline", "ready", "dock", "resume")
# The dock's steps, in order (ace2k_u1_background.DOCK_STEPS; a test pins them equal).
STEPS = ("heat", "unload", "free", "roll back", "hand over", "push", "grip", "loaded")
# The values measured on the printer on 2026-11-10 (bench B7), used until a quantity is measured.
# web/tests/fixtures/swap-time-seeds.json pins them for both the adapter and the page.
SEEDS: dict[str, float] = {"inline": 94.0, "ready": 15.0, "dock": 65.0, "resume": 15.0}
STEP_SEEDS: dict[str, float] = {
    "heat": 0.0,
    "unload": 27.0,
    "free": 1.0,
    "roll back": 11.0,
    "hand over": 0.0,
    "push": 12.0,
    "grip": 14.0,
    "loaded": 0.0,
}


def _sample_ok(value: float) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        value = float(value)  # an int too large for a float raises OverflowError
    except OverflowError:
        return False
    return math.isfinite(value) and 0 <= value <= SAMPLE_MAX_S


def _read_avg(entry: object, seed: float) -> list[float]:
    """Returns [s, n] from a file's entry, or the seed with n 0 when the entry is not valid."""
    if isinstance(entry, dict):
        s, n = entry.get("s"), entry.get("n")
        if (
            isinstance(s, (int, float))
            and _sample_ok(s)
            and isinstance(n, int)
            and not isinstance(n, bool)
            and n >= 0
        ):
            return [float(s), n]
    return [seed, 0]


class SwapTimes:
    """The averages: [s, n] per quantity (KINDS) and per dock step (STEPS)."""

    def __init__(self) -> None:
        self.kinds: dict[str, list[float]] = {k: [SEEDS[k], 0] for k in KINDS}
        self.steps: dict[str, list[float]] = {k: [STEP_SEEDS[k], 0] for k in STEPS}
        self.path: str | None = None
        self.dropped_said: set[str] = set()
        self.published: dict[str, object] | None = None
        self.cond = threading.Condition()
        self.pending: str | None = None  # the latest text to write
        self.stopping = False
        self.write_failed = False
        self.thread: threading.Thread | None = None
        self.stops = PrintStops()  # the stops of the print that runs (status print_stops)

    # --- the samples -------------------------------------------------------------------------

    def note(self, key: str, sample: float) -> None:
        """Adds a sample of one of KINDS."""
        self._add(self.kinds[key], key, sample)

    def note_step(self, step: str, sample: float) -> None:
        """Adds a sample of a dock step."""
        self._add(self.steps[step], f"step {step}", sample)

    def left(self, step: str, elapsed: float, done: Iterable[str]) -> float:
        """The dock work left (s) when a run stops in step after elapsed s in it.

        done names the steps already done: they are not counted, the step itself included.
        """
        skip = set(done)
        at = STEPS.index(step)
        rest = sum(self.steps[s][0] for s in STEPS[at + 1 :] if s not in skip)
        here = 0.0 if step in skip else max(0.0, self.steps[step][0] - elapsed)
        return here + rest

    def note_resume(self, stop: float, step: str, elapsed: float, done: Iterable[str]) -> None:
        """Adds the resume cost of an arrival that stopped a run mid-way."""
        if not _sample_ok(stop):
            self._drop("resume", stop)
            return
        cost = stop - self.kinds["ready"][0] - self.left(step, elapsed, done)
        self.note("resume", max(0.0, cost))

    def _add(self, avg: list[float], name: str, sample: float) -> None:
        if not _sample_ok(sample):
            self._drop(name, sample)
            return
        s, n = avg
        avg[0] = float(sample) if n == 0 else s + SWAP_TIME_WEIGHT * (sample - s)
        avg[1] = n + 1
        self.published = None
        self._queue()

    def _drop(self, name: str, sample: float) -> None:
        if name not in self.dropped_said:
            self.dropped_said.add(name)
            logging.info("ace2k_u1: swap time %s of %r s dropped (not a swap's)", name, sample)

    # --- the status ----------------------------------------------------------------------------

    def status(self) -> dict[str, object]:
        """The status key swap_times, built again only after a sample."""
        if self.published is None:

            def one(avg: list[float]) -> dict[str, object]:
                return {"s": round(avg[0], 1), "n": int(avg[1])}

            out: dict[str, object] = {k: one(self.kinds[k]) for k in KINDS}
            out["steps"] = {k: one(self.steps[k]) for k in STEPS}
            self.published = out
        return self.published

    # --- the file ----------------------------------------------------------------------------

    def open(self, folder: str) -> None:
        """Reads swap-times.json in folder and starts the writer. Never raises."""
        if self.thread is not None:
            return  # already opened: a second writer would leak the first
        self.path = os.path.join(folder, FILE_NAME)
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = None
        except Exception as e:  # a broken file gives the seeds
            logging.warning("ace2k_u1: %s cannot be read (%s): the seeds are used", FILE_NAME, e)
            data = None
        if isinstance(data, dict) and data.get("v") == FORMAT:
            for k in KINDS:
                self.kinds[k] = _read_avg(data.get(k), SEEDS[k])
            steps = data.get("steps")
            steps = steps if isinstance(steps, dict) else {}
            for k in STEPS:
                self.steps[k] = _read_avg(steps.get(k), STEP_SEEDS[k])
        elif data is not None:
            logging.warning("ace2k_u1: %s has another format: the seeds are used", FILE_NAME)
        self.published = None
        self.thread = threading.Thread(target=self._run, name="ace2k_u1_swap_times", daemon=True)
        self.thread.start()

    def _text(self) -> str:
        def one(avg: list[float]) -> dict[str, object]:
            return {"s": avg[0], "n": int(avg[1])}

        data: dict[str, object] = {"v": FORMAT}
        data.update({k: one(self.kinds[k]) for k in KINDS})
        data["steps"] = {k: one(self.steps[k]) for k in STEPS}
        return json.dumps(data, separators=(",", ":"))

    def _queue(self) -> None:
        if self.thread is None:
            return  # not opened (a test, or before klippy:ready): memory only
        with self.cond:
            self.pending = self._text()
            self.cond.notify()

    def _run(self) -> None:
        while True:
            with self.cond:
                while self.pending is None and not self.stopping:
                    self.cond.wait()
                text, self.pending = self.pending, None
                stopping = self.stopping
            if text is not None:
                self._write(text)
            if stopping:
                return

    def _write(self, text: str) -> None:
        path = self.path
        if path is None:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except Exception as e:  # the averages stay in memory; Klipper never sees this
            if not self.write_failed:
                logging.warning("ace2k_u1: %s not written: %s", FILE_NAME, e)
            self.write_failed = True

    def stop(self) -> None:
        """Ends the writer once the latest values are written (klippy:disconnect)."""
        with self.cond:
            self.stopping = True
            self.cond.notify_all()
        if self.thread is not None:
            self.thread.join(STOP_WAIT_S)


class StopRun:
    """One swap timed by PrintStops.swap: the swap sets ok once it ended well."""

    ok = False


@dataclass
class Stop:
    """A stop being timed: a T of the file that swaps, or a swap with no T around it.

    gen is the print it belongs to (PrintStops.gen), start its reactor time, swaps the swaps that
    ended well in it, and counts False once something rules it out: a failed swap, a pause, a
    swap while the print did not print or before the print's clock started.
    """

    gen: int
    start: float
    swaps: int = 0
    counts: bool = True


def _print_s(printer: Printer, eventtime: float) -> float | None:
    """The print's print_duration now (s), or None when it cannot be read."""
    try:
        stats = printer.lookup_object("print_stats", None)
        value = stats.get_status(eventtime)["print_duration"]
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


class PrintStops:
    """The swap stops of the print that runs, measured: their seconds (s) and their swaps (n).

    A stop is a T of the file (or of the console) that ran a swap: it runs from the moment the T
    reached the adapter's T wrapper (of_t) to the moment that wrapper returns and the print goes
    on, the wait for a background swap, the U1's own T and the dock wipe included. A T of the U1
    itself (A0), and any T while a swap runs, belongs to the stop around it. A swap with no T
    around it (a swap typed at the console, a pending swap that RESUME finishes) is a stop of its
    own. Every swap that ends well counts in n: inline, arrival, typed at the console, and the
    ones the averages leave out; two swaps in one T are one stop's time and two in n. A stop does
    not count when a swap in it failed, when a pause came into it (note_pause), when the print
    was not printing as it ended, or when a swap in it started while the print did not print or
    before the print's clock started (print_duration 0, before the first extrusion): Klipper's
    print_duration, which the page subtracts these stops from, leaves those out. since is the
    print_duration at which the stop that runs now began, None when no swap runs, when the stop
    will not count, or when print_duration cannot be read. The print's start clears them (reset):
    the print start wrapper and Klipper's print_stats:new_task_start both call it.
    """

    def __init__(self) -> None:
        self.s = 0.0
        self.n = 0
        self.since: float | None = None
        self.gen = 0  # the print, moved on by reset: an old stop never adds into a new print
        self.stop: Stop | None = None  # the stop open now
        self.running = False  # a swap runs now
        self.published: dict[str, object] | None = None

    def reset(self) -> None:
        """Clears the stops at a print's start, and drops any stop still open."""
        self.s, self.n, self.since, self.published = 0.0, 0, None, None
        self.gen += 1
        self.stop, self.running = None, False

    def note_pause(self) -> None:
        """The print paused (print_stats:paused): the stop open now does not count."""
        if self.stop is not None:
            self._rule_out(self.stop)

    def _rule_out(self, stop: Stop) -> None:
        """The stop will not count: since goes at once, so the page's clock does not wait for it."""
        stop.counts = False
        if self.stop is stop:
            self.since, self.published = None, None

    def _close(self, stop: Stop, end: float, printing: Callable[[], bool]) -> None:
        """Ends a stop: it adds its time and swaps when it counts and the print prints."""
        if self.stop is stop:
            self.stop, self.since, self.published = None, None, None
        if stop.gen == self.gen and stop.counts and stop.swaps and printing():
            self.s += end - stop.start
            self.n += stop.swaps
            self.published = None

    @contextlib.contextmanager
    def of_t(self, reactor: Reactor, printing: Callable[[], bool], u1_t: bool) -> Iterator[None]:
        """Times a whole T, from its wrapper's start to its end, when a swap ran in it.

        u1_t is a T of the U1 itself (A0): it and any T while a stop is open or a swap runs
        belong to that stop, and time nothing.
        """
        if u1_t or self.stop is not None or self.running:
            yield
            return
        stop = self.stop = Stop(self.gen, reactor.monotonic())
        try:
            yield
        finally:
            self._close(stop, reactor.monotonic(), printing)

    @contextlib.contextmanager
    def swap(
        self, reactor: Reactor, printing: Callable[[], bool], printer: Printer
    ) -> Iterator[StopRun]:
        """Times one swap, in the open stop or as a stop of its own. The swap sets run.ok.

        printing tells whether the print prints, with no pause taken or asked. A swap inside
        another is the outer one's.
        """
        run = StopRun()
        if self.running:
            yield run
            return
        now = reactor.monotonic()
        stop, own = self.stop, self.stop is None
        if stop is None:
            stop = self.stop = Stop(self.gen, now)
        on = printing()
        ran = _print_s(printer, now) if on else None
        if not on or (ran is not None and ran <= 0):
            self._rule_out(stop)
        elif stop.counts and self.since is None:  # a second swap of the T keeps the first's
            self.since = None if ran is None else round(max(0.0, ran - (now - stop.start)), 1)
        self.running, self.published = True, None
        try:
            yield run
        finally:
            self.running, self.published = False, None
            if run.ok:
                stop.swaps += 1
            else:
                self._rule_out(stop)
            if own:
                self._close(stop, reactor.monotonic(), printing)

    def status(self) -> dict[str, object]:
        """The status key print_stops, built again only after a change."""
        if self.published is None:
            self.published = {"s": round(self.s, 1), "n": self.n, "since": self.since}
        return self.published
