"""UnloadWatch logs the lane once a second while the U1 unloads the head in an eject, and sums it
up at the end. It only logs, and it changes nothing that the eject or the follow does.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

try:
    from .ace2k_u1_common import finite
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import finite  # type: ignore[import-not-found, no-redef]

if TYPE_CHECKING:
    from .ace2k_u1_common import Hooked
    from .ace2k_u1_printer_types import LaneView, Reactor

# The watch of the lane while the U1 unloads the head. Its tach counts pulses either way, and the
# speed in its state report is the setpoint, never signed. An image with the motor report (ace2k
# host interface 25) gives the travel each way: the watch turns that report on while it runs, and
# reads the motor_fwd_mm and motor_back_mm of the lane. With an older image, or a second whose
# readings lack them, the direction comes from the follow's corrections, which the unit counts each
# way. A taut catch-up or a forward dose goes toward the head. A full take-up or a reverse dose
# goes back. A second with no new correction carries the direction of the last one, as a move runs
# on until the plunger reads rest.
#
# The unit's report and the watch's timer each run at 1 Hz, on phases of their own that drift. So
# when ace2k counts the reports (motor_reports), the watch follows them: it reads the status every
# WATCH_POLL_S and logs a line when a new report came, so each line holds one report. A line that
# holds more (the reactor was held) counts as that many seconds. With no report for
# WATCH_REPORT_LATE_S, the corrections give the motor of that time.
WATCH_PERIOD_S = 1.0
WATCH_POLL_S = 0.25
WATCH_REPORT_LATE_S = 2.0
# The unit's tach: about 12.35 pulses per mm of filament (its hardware guide, "Counters").
TACH_PULSES_PER_MM = 12.35
WATCH_STILL_MM = 0.5  # less motor than this in a second is still
WATCH_MIN_S = 0.5  # the last reading at the end counts only this long after the one before
FORWARD_COUNTS = ("ff_taut_fixes", "ff_doses")
BACK_COUNTS = ("ff_full_fixes", "ff_back_doses")
SIGNS = {"fwd": "+", "back": "-", "unsure": "±", "still": ""}


class Reading(NamedTuple):
    """One reading of the watch."""

    at: float  # its time
    lane: LaneView
    full: bool | None  # the shared full reading
    reports: int | None  # the unit's motor report count (None without one)


def _counter_delta(new: object, old: object) -> int | None:
    """Return how much a counter of the unit went up, or None when it is not read (not an int).

    A counter that went down started again: all of its new count is the rise.
    """
    if not isinstance(new, int):
        return None
    if not isinstance(old, int) or new < old:
        return new
    return new - old


def _number(value: object) -> float | None:
    """The reading as a float, or None when it is not a finite number (NaN reads unknown)."""
    return float(value) if finite(value) else None


def _text(value: object, fmt: str) -> str:
    number = _number(value)
    return "unknown" if number is None else fmt.format(number)


class UnloadWatch:
    """The log of a lane, once a second, while the U1 unloads its head in an eject."""

    def __init__(self, reactor: Reactor, h: Hooked, name: str) -> None:
        self.reactor = reactor
        self.h = h
        self.name = name  # the lane as the console names it
        self.timer: object | None = None
        self.start = 0.0
        self.last: Reading | None = None
        self.keyed = False  # the lines follow the unit's reports (motor_reports)
        self.direction: str | None = None  # the direction of the last second that moved
        self.unread_said = False
        self.watching = False  # the unit's motor report was turned on for this watch
        self.sources = {"unit": 0, "counted": 0}  # the seconds of each source of the direction
        # The motor travel and the seconds of each direction, and of a forward motion while the
        # filament did not go forward ("against").
        self.mm = {"fwd": 0.0, "back": 0.0, "unsure": 0.0, "against": 0.0}
        self.seconds = {"fwd": 0, "back": 0, "unsure": 0, "against": 0}
        self.filament: float | None = None

    def begin(self) -> None:
        """Turn the unit's motor report on, take the first reading and start the timer."""
        self.watching = self._motor_watch(True)
        self.start = self.reactor.monotonic()
        self.last = self._read(self.start)
        self.keyed = self.watching and self.last is not None and self.last.reports is not None
        period = WATCH_POLL_S if self.keyed else WATCH_PERIOD_S
        self.timer = self.reactor.register_timer(self._tick, self.start + period)

    def end(self) -> None:
        """Stop the timer, count the time since the last reading, and log the summary."""
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
        now = self.reactor.monotonic()
        reading = self._read(now)
        if self.last is None or now - self.last.at >= WATCH_MIN_S or self._new_reports(reading):
            self._step(now, reading)
        if self.watching:
            self._motor_watch(False)
            self.watching = False
        mm, s = self.mm, self.seconds
        filament = "unknown" if self.filament is None else f"{self.filament:+.1f} mm"
        logging.info(
            "ace2k_u1: lane %s eject, U1 unload over after %.0f s: motor forward %.1f mm in %d s,"
            " back %.1f mm in %d s, unsure %.1f mm in %d s; filament %s; forward while the"
            " filament did not go forward: %.1f mm in %d s; direction from the unit in %d s, from"
            " the follow's corrections in %d s",
            self.name,
            now - self.start,
            mm["fwd"],
            s["fwd"],
            mm["back"],
            s["back"],
            mm["unsure"],
            s["unsure"],
            filament,
            mm["against"],
            s["against"],
            self.sources["unit"],
            self.sources["counted"],
        )

    def _motor_watch(self, on: bool) -> bool:
        """Turn ace2k's motor report on or off. False with an older ace2k. It never raises."""
        watch = getattr(self.h.api, "motor_watch", None)
        if not callable(watch) or getattr(self.h.api, "has_motor_travel", False) is not True:
            return False
        try:
            return watch(on) is True
        except Exception:
            logging.exception("ace2k_u1: lane %s eject, U1 unload: motor report", self.name)
            return False

    def _tick(self, eventtime: float) -> float:
        if not self.keyed:
            self._step(eventtime)
            return eventtime + WATCH_PERIOD_S
        reading = self._read(eventtime)
        late = self.last is None or eventtime - self.last.at >= WATCH_REPORT_LATE_S
        if late or self._new_reports(reading):
            self._step(eventtime, reading)
        return eventtime + WATCH_POLL_S

    def _new_reports(self, reading: Reading | None) -> int:
        """The unit's motor reports since the last line (0 without a count to compare)."""
        if reading is None or self.last is None:
            return 0
        new, old = reading.reports, self.last.reports
        return new - old if new is not None and old is not None and new > old else 0

    def _read(self, eventtime: float) -> Reading | None:
        """Read the lane, the shared full reading and the report count once. It never raises
        (logged once)."""
        try:
            status = self.h.unit.get_status(eventtime)
            pulled = status.get("pulled_any")
            reports = status.get("motor_reports")
            count = reports if isinstance(reports, int) and not isinstance(reports, bool) else None
            return Reading(eventtime, dict(status["lanes"][self.h.lane]), pulled is True, count)
        except Exception:
            if not self.unread_said:
                self.unread_said = True
                logging.exception("ace2k_u1: lane %s eject, U1 unload: status unread", self.name)
            return None

    def _step(self, eventtime: float, reading: Reading | None = None) -> None:
        """Log the time since the last reading, and add it to the summary.

        A line read from the unit's reports counts one second per report. Any other line counts
        as many seconds as it lasted, rounded, and at least one."""
        if reading is None:
            reading = self._read(eventtime)
        reports = self._new_reports(reading)
        before, self.last = self.last, reading
        if reading is None or before is None:
            return
        old, new = before.lane, reading.lane
        fwd = sum(_counter_delta(new.get(k), old.get(k)) or 0 for k in FORWARD_COUNTS)
        back = sum(_counter_delta(new.get(k), old.get(k)) or 0 for k in BACK_COUNTS)
        filament = self._delta_mm(new.get("encoder_mm"), old.get("encoder_mm"))
        self._add_filament(filament)
        # Without a new report, the unit's travel is that of the last line: none to read.
        unit = None if self.keyed and not reports else self._unit_travel(new, old)
        if unit is not None:
            motor_text = self._add_unit(unit, filament, max(1, reports))
        else:
            motor = self._motor_mm(new.get("fg"), old.get("fg"))
            direction = self._direction(fwd, back, motor)
            seconds = max(1, round((eventtime - before.at) / WATCH_PERIOD_S))
            self._add(direction, motor, filament, seconds)
            motor_text = (
                "motor unknown"
                if motor is None or direction is None
                else f"motor {SIGNS[direction]}{motor:.1f} mm ({direction}, counted)"
            )
        logging.info(
            "ace2k_u1: lane %s eject, U1 unload %.0f s: %s, %s, filament %s, buffer %s, starts fwd"
            " %d back %d, setpoint %s, duty %s, ff rate %s",
            self.name,
            eventtime - self.start,
            new.get("mode") or "unknown",
            motor_text,
            "unknown" if filament is None else f"{filament:+.1f} mm",
            self._buffer(new, reading.full),
            fwd,
            back,
            _text(new.get("speed"), "{:.1f} mm/s"),
            _text(new.get("duty"), "{:.0f} %"),
            _text(new.get("ff_rate"), "{:+.1f} mm/s"),
        )

    @staticmethod
    def _motor_mm(new: object, old: object) -> float | None:
        """The motor travel from the tach. A tach that went down was reset."""
        pulses = _counter_delta(new, old)
        return None if pulses is None else pulses / TACH_PULSES_PER_MM

    @staticmethod
    def _delta_mm(new: object, old: object) -> float | None:
        """The change of a reading in mm from old to new, or None when either is not read."""
        a, b = _number(new), _number(old)
        return None if a is None or b is None else a - b

    def _direction(self, fwd: int, back: int, motor: float | None) -> str | None:
        """The direction of the second: by the corrections that started, else the last one."""
        if motor is None:
            return None
        if motor < WATCH_STILL_MM:
            return "still"
        if fwd and back:
            direction = "unsure"
        elif fwd or back:
            direction = "fwd" if fwd else "back"
        else:
            direction = self.direction or "unsure"
        self.direction = direction
        return direction

    @staticmethod
    def _unit_travel(new: LaneView, old: LaneView) -> tuple[float, float] | None:
        """The motor travel forward and back in the second, as the unit counted it, or None."""
        travel = [
            UnloadWatch._delta_mm(new.get(key), old.get(key))
            for key in ("motor_fwd_mm", "motor_back_mm")
        ]
        if travel[0] is None or travel[1] is None:
            return None
        return max(travel[0], 0.0), max(travel[1], 0.0)

    def _add_unit(self, travel: tuple[float, float], filament: float | None, seconds: int) -> str:
        """Add the seconds that the unit's motor reports measured (one per report), and return
        their text."""
        fwd, back = travel
        self.sources["unit"] += seconds
        for key, mm in (("fwd", fwd), ("back", back)):
            if mm >= WATCH_STILL_MM:
                self.mm[key] += mm
                self.seconds[key] += seconds
        if fwd >= WATCH_STILL_MM:
            if filament is not None and filament <= 0.0:
                self.mm["against"] += fwd
                self.seconds["against"] += seconds
            self.direction = "fwd" if fwd >= back else "back"
        elif back >= WATCH_STILL_MM:
            self.direction = "back"
        reports = "" if seconds == 1 else f", {seconds} reports"
        return f"motor fwd {fwd:.1f} mm back {back:.1f} mm (unit{reports})"

    def _add_filament(self, filament: float | None) -> None:
        if filament is not None:
            self.filament = (self.filament or 0.0) + filament

    def _add(
        self, direction: str | None, motor: float | None, filament: float | None, seconds: int
    ) -> None:
        if motor is None or direction is None:
            return
        self.sources["counted"] += seconds
        if direction == "still":
            return
        self.mm[direction] += motor
        self.seconds[direction] += seconds
        if direction == "fwd" and filament is not None and filament <= 0.0:
            self.mm["against"] += motor
            self.seconds["against"] += seconds

    @staticmethod
    def _buffer(lane: LaneView, full: bool | None) -> str:
        if full:
            return "full"
        if lane.get("pushed") is True:
            return "taut"
        return "rest" if lane.get("rest") is True else "mid"
