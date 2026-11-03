"""Measures how far a lane of a shared head rolls back to leave its splitter
(ACEK_SPLITTER_MEASURE).

Lane A is pushed to the head's sensor and backs off it until the sensor is empty. Lane B pushes
until it meets A's filament at the splitter, and then waits a little behind that point. A rolls
back x mm and B tries to pass. A search on x finds the smallest roll back that lets B pass. The
largest result of the head's pairs, plus a margin, is staged as headN_splitter_mm for SAVE_CONFIG.
ace2k_u1.py registers the command.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_bench import Bench
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_config import SwapConfig
    from .ace2k_u1_printer_types import GCodeCommand, Printer
    from .ace2k_u1_probe import HeadProbe

try:
    from .ace2k_u1_common import INSIST_MIN_MM, carried, move_max_mm
    from .ace2k_u1_config import LANES, SPLITTER_MAX_MM
    from .ace2k_u1_probe import EASE_MM, STOP_SET, ProbeError, holding
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        INSIST_MIN_MM,
        carried,
        move_max_mm,
    )
    from ace2k_u1_config import LANES, SPLITTER_MAX_MM  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_probe import (  # type: ignore[import-not-found, no-redef]
        EASE_MM,
        STOP_SET,
        ProbeError,
        holding,
    )

# B's speed wherever it may meet A: its whole first approach and every try. A slow meeting with
# another filament is a soft one.
MEASURE_SPEED = 20.0
# How far past the point where it was blocked a push again may go (it first takes back its ease).
# The motor fills the plunger and stops, with no travel left to press on.
REPUSH_MAX_MM = 30.0
REPUSH_TRIES = 3  # pushes again against a snag at the entrance or the exit of the splitter
WAIT_BACK_MM = 50.0  # how far B waits behind the point where it met A
# After the sensor clears, A rolls back this much more, so A's own tip does not set the sensor
# again during B's approach.
SENSOR_CLEAR_MM = 10.0
# A's feeds shorter than this are not checked for carried: a few mm may report one encoder count.
# They count what they report, which leaves the tip no further back than believed (the safe side).
CARRY_CHECK_MIN_MM = 10.0
# A try pushes B this far: the wait plus 50 mm past the point where it met A. The whole of it is a
# pass, because A's tip has left the shared tube.
TRY_MM = 100.0
SEARCH_STEP_MM = 5.0  # the search stops when the interval is under this
SPLITTER_MARGIN_MM = 50.0  # added to the measured point
STAGED_STEP_MM = 10.0  # the staged value is rounded up to this, so the roll back only grows
WARN_DIFF_MM = 150.0  # a result this far from the value in effect gets a warning
# A push's verdict when its insisting could not tell a snag from the other lane's filament: the
# pushes again were spent, or an ease did not reach rest.
UNSURE = "unsure"


class HasLane(Protocol):
    """What pick_pairs reads of a lane: its unit and its number in the unit."""

    @property
    def unit(self) -> object: ...

    @property
    def lane(self) -> int: ...


T = TypeVar("T", bound=HasLane)


class SearchError(Exception):
    """The search found no roll back that lets B pass."""


class PairError(Exception):
    """A unit on the head has no pair of lanes to measure with. The message is the refusal."""


def search(test: Callable[[float], bool], lo: float, hi: float, first: float, step: float) -> float:
    """Return the smallest x in (lo, hi] for which test(x) is True, within step.

    test(lo) is known to be False. The first x tried is ``first``, clamped into [lo + step, hi];
    when hi < lo + step, the first x is hi. If it is False, hi is tried next, and hi False raises
    SearchError. Then the interval between the largest False and the smallest True halves until it
    is under step. The smallest True is the result.
    """
    if hi <= lo:
        raise SearchError(f"no room to search: {lo:g} to {hi:g} mm")
    x = min(max(first, lo + step), hi)
    passed: float | None = None
    while True:
        if test(x):
            passed = x
        else:
            lo = x
            if passed is None:
                if x >= hi:
                    raise SearchError(f"still blocked at {hi:g} mm")
                x = hi
                continue
        if passed - lo < step:
            return passed
        x = (lo + passed) / 2.0


def pick_pairs(
    lanes: Sequence[T], loaded: Callable[[T], bool], lane_text: Callable[[T], str]
) -> list[tuple[T, T]]:
    """Return one (A, B) pair per unit on the head, in the order of the units in ``lanes``.

    A is the unit's lowest-numbered lane with filament in its bay. B is the unit's next such lane,
    else the first such lane of another unit. A unit with no lane with filament, or a head with one
    such lane in all, raises PairError with the refusal.
    """
    units: list[object] = []
    for h in lanes:
        if not any(h.unit is u for u in units):
            units.append(h.unit)
    with_filament = [h for h in lanes if loaded(h)]  # loaded is read once per lane
    full = sorted(with_filament, key=lambda h: h.lane)
    pairs: list[tuple[T, T]] = []
    for unit in units:
        mine = [h for h in full if h.unit is unit]
        if not mine:
            first = next(h for h in lanes if h.unit is unit)
            raise PairError(f"no lane of {lane_text(first)}'s unit has filament: load one")
        others = [h for h in with_filament if h.unit is not unit]
        partner = mine[1:] + others
        if not partner:
            raise PairError(
                f"{lane_text(mine[0])} is the only lane with filament: load a second one"
            )
        pairs.append((mine[0], partner[0]))
    return pairs


class MeasureAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the measure uses it."""

    @property
    def head_budget_mm(self) -> float: ...
    def lane_text(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...


class MeasureError(Exception):
    """A pair could not be measured. The message says why and where each lane is."""


@dataclass
class PairResult:
    """One pair's measure: the point (mm back from the head's sensor) where A's tip leaves the
    splitter, and how far B pushed before it met A."""

    a: Hooked
    b: Hooked
    point: float
    met: float


class SplitterMeasure:
    """Measures one pair (A, B) of a shared head.

    ``probe`` gives the moves (HeadProbe's public ones), ``adapter`` the lane names, the console
    and head_budget_mm, ``max_move`` the longest move of a lane's unit.
    """

    def __init__(
        self,
        probe: HeadProbe,
        adapter: MeasureAdapter,
        max_move: Callable[[Hooked], float],
    ) -> None:
        self.probe, self.adapter, self.max_move = probe, adapter, max_move
        self.a_pushed = 0.0  # how far A was pushed from its parking point toward the sensor
        self.a_back: float | None = None  # how far A's tip is behind the head's sensor, once known
        self.a_between: tuple[float, float] | None = None  # A's range after a move that failed
        self.b_out = 0.0  # how far B's tip is ahead of its parking point
        self.b_wait: float | None = None  # B's wait point, once it met A

    def measure_pair(self, slot: Slot, a: Hooked, b: Hooked, first: float) -> PairResult:
        """Run the measure of a and b; ``first`` is the value in effect, tried first."""
        self.a_pushed, self.a_back, self.a_between = 0.0, None, None
        self.b_out, self.b_wait = 0.0, None
        try:
            result = self._measure(slot, a, b, first)
        except Exception as err:  # B never stays in the shared tube, whatever went wrong
            self._b_out_of_the_way(b)
            if isinstance(err, (ProbeError, MeasureError, SearchError)):
                raise MeasureError(f"{err}; {self._where(a, b)}") from err
            raise
        self.adapter.say(
            f"ace2k_u1: head {slot.head + 1}: {self.adapter.lane_text(a)} leaves the splitter at"
            f" {result.point:.0f} mm ({self.adapter.lane_text(b)} met it after {result.met:.0f} mm)"
        )
        return result

    def _measure(self, slot: Slot, a: Hooked, b: Hooked, first: float) -> PairResult:
        ta, tb = self.adapter.lane_text(a), self.adapter.lane_text(b)
        speed = self.probe.cfg.probe_speed
        verdict, pushed = self._push(slot, a, self.adapter.head_budget_mm, speed, self._add_a)
        if verdict != "set":
            raise MeasureError(
                f"{ta} did not reach the head's sensor ({verdict} after {pushed:.0f} mm):"
                " a lane may be left in the shared tube"
            )
        lo = self._off_sensor(a)  # known blocked: B's approach meets A there
        # The point + margin must be a roll back A can make from where it was parked. The check
        # and the final try below use that value as found; the staged value is then rounded up to
        # STAGED_STEP_MM, which is at most STAGED_STEP_MM more.
        bound = pushed - SPLITTER_MARGIN_MM
        verdict, met = self._push(slot, b, self.adapter.head_budget_mm, MEASURE_SPEED, self._add_b)
        if verdict == "set":
            raise MeasureError(
                f"{tb} reached the head's sensor with {ta} just behind it: {ta} is not in its path"
            )
        if verdict == "pass":
            raise MeasureError(f"{tb} pushed {met:.0f} mm and met nothing")
        if verdict == UNSURE:
            raise MeasureError(f"{tb} could not tell a snag from {ta} after {met:.0f} mm")
        if met < WAIT_BACK_MM + INSIST_MIN_MM:  # no room for B's wait point ahead of its parking
            raise MeasureError(
                f"{tb} met {ta} within {WAIT_BACK_MM + INSIST_MIN_MM:.0f} mm of its parking point:"
                f" unload {tb} to its bay and measure again"
            )
        self.b_wait = met - WAIT_BACK_MM
        self._b_to(b, self.b_wait)

        at: dict[float, float] = {}  # x -> where A's tip was for that try (within INSIST_MIN_MM)

        def passes(x: float) -> bool:
            self._a_to(a, x)
            assert self.a_back is not None
            at[x] = self.a_back
            return self._passes(slot, b)

        try:
            found = search(passes, lo, bound, first, SEARCH_STEP_MM)
        except SearchError:
            if bound <= lo:  # no room to search: its own error
                raise
            raise MeasureError(
                f"{tb} is blocked with {ta} rolled back {bound:.0f} mm: check the tube after"
                " the splitter"
            ) from None
        point = max(found, at[found])  # B passed with A's tip there: never less
        if found <= lo + SEARCH_STEP_MM:  # B passed with A just off the sensor
            raise MeasureError(
                f"{tb} passed with {ta} {point:.0f} mm back from the head's sensor: it does not"
                f" meet {ta} at a splitter"
            )
        if point + SPLITTER_MARGIN_MM > pushed:  # A cannot roll back to the value it stages
            raise MeasureError(
                f"{ta} was parked too close to the splitter to check the value: unload it to its"
                " bay and measure again"
            )
        self._a_to(a, point + SPLITTER_MARGIN_MM)
        if not self._passes(slot, b):
            raise MeasureError(f"{tb} is blocked with {ta} rolled back the new value")
        self._b_to(b, 0.0)
        return PairResult(a, b, point, met)

    def _off_sensor(self, a: Hooked) -> float:
        """Roll A back off the head's sensor, at probe_speed, at most EASE_MM, stopped when the
        sensor goes empty, then SENSOR_CLEAR_MM more. Return how far back A's tip is (a_back).

        Only B can set the sensor after that. A roll back that ends otherwise (the sensor still
        set after EASE_MM, another end, or no distance) is a MeasureError, and nothing more moves.
        """
        ta = self.adapter.lane_text(a)
        self.a_back, self.a_between = None, (0.0, EASE_MM)
        why, kind, moved = self.probe.move(
            a, "rollback", EASE_MM, frozenset({"empty"}), speed=self.probe.cfg.probe_speed
        )
        if why != "empty":
            if kind == "done":
                raise MeasureError(
                    f"{ta}'s tip did not leave the head's sensor within {EASE_MM:.0f} mm"
                )
            raise MeasureError(f"{ta}'s roll back off the head's sensor ended {kind}")
        if moved <= 0.0:
            raise MeasureError(
                f"{ta}'s roll back off the head's sensor has no distance: where its tip is is not"
                " known"
            )
        self.a_back, self.a_between = moved, (moved, moved + SENSOR_CLEAR_MM)
        why, kind, more = self.probe.move(
            a, "rollback", SENSOR_CLEAR_MM, speed=self.probe.cfg.probe_speed
        )
        if kind != "done":
            raise MeasureError(f"{ta}'s roll back behind the head's sensor ended {kind or why}")
        # no distance: the whole step, as a roll back too far leaves the tip no closer than believed
        self.a_back = moved + (more if more >= 1.0 else SENSOR_CLEAR_MM)
        self.a_between = None
        return self.a_back

    def _passes(self, slot: Slot, b: Hooked) -> bool:
        """One try of B from the wait point: True when it passes. B is back at the wait point after.

        A try whose insisting could not tell a snag from A counts as blocked: the value comes out
        larger, the safe side.
        """
        verdict, _pushed = self._push(slot, b, TRY_MM, MEASURE_SPEED, self._add_b)
        if verdict == "set":
            raise MeasureError(f"{self.adapter.lane_text(b)} reached the head's sensor in a try")
        assert self.b_wait is not None
        self._b_to(b, self.b_wait)
        return verdict == "pass"

    def _add_a(self, mm: float) -> None:
        self.a_pushed += mm

    def _add_b(self, mm: float) -> None:
        self.b_out += mm

    def _push(
        self, slot: Slot, h: Hooked, length: float, speed: float, add: Callable[[float], None]
    ) -> tuple[str, float]:
        """Push lane h up to length mm at speed, insisting against a snag.

        Return (verdict, mm pushed): "pass" for the whole length, "set" (the head's sensor; the
        stop reads the distance from the move's final event), "blocked" (a push again that gains
        under INSIST_MIN_MM over its ease: the other lane's filament), or UNSURE (the pushes again
        are spent, or an ease did not reach rest: a snag cannot be told from the other filament).
        ``add`` counts each move as it ends, so an error later still knows where h is. Each push
        again runs at MEASURE_SPEED, whatever ``speed`` is. A push again that ends blocked under
        1 mm is the other filament, counted as the ease before it (at most its step): the tip
        cannot pass the point where it was blocked before that ease. A move that ends otherwise, or
        a "set" or a first "blocked" end with no distance, is a MeasureError; such a move (or one
        that raises) is counted as its whole step, because a roll back too far only takes the tip
        toward its bay.
        """
        n, th = slot.head + 1, self.adapter.lane_text(h)
        pushed, tries, eased, repush = 0.0, 0, 0.0, False
        while True:
            left = length - pushed
            if left < INSIST_MIN_MM:
                return "pass", pushed
            step = min(left, self.max_move(h))
            if repush:  # back over its ease, and at most REPUSH_MAX_MM past the block
                step = min(step, eased + REPUSH_MAX_MM)
            end, moved = self._feed(h, step, MEASURE_SPEED if repush else speed, add)
            if moved < 1.0 and end in ("set", "blocked"):
                gained = self._unmeasured(th, step, end, eased if repush else None, add)
                return "blocked", pushed + gained  # it does not give at all: the other filament
            pushed += moved
            add(moved)
            if end == "set":
                return "set", pushed
            if end == "done":
                self._check_carried(th, step, moved)
                repush = False
                continue
            if end != "blocked":
                raise MeasureError(f"{th}'s push ended {end}")
            if repush and moved - eased < INSIST_MIN_MM:
                return "blocked", pushed  # it does not give: it is the other lane's filament
            rested, eased = self._ease(h, th, n, pushed, tries + 1)
            add(-eased)
            pushed -= eased
            if not rested:
                return UNSURE, pushed
            tries += 1
            repush = True

    @staticmethod
    def _check_carried(th: str, step: float, moved: float) -> None:
        """A push of step mm that ended done must have carried the filament, or it is an error."""
        if not carried(step, moved):
            raise MeasureError(f"{th}'s push of {step:.0f} mm carried {moved:.0f} mm")

    def _feed(
        self, h: Hooked, step: float, speed: float, add: Callable[[float], None]
    ) -> tuple[str | None, float]:
        """Feed h one step of the push. Return (how it ended, mm moved).

        It ends "set" for the head's sensor, else as the probe reports. A feed that raises is
        counted as its whole step first.
        """
        try:
            why, kind, moved = self.probe.feed_free(h, step, STOP_SET, speed=speed)
        except Exception:
            add(step)
            raise
        return ("set" if why == "set" else kind), moved

    def _ease(self, h: Hooked, th: str, n: int, pushed: float, tries: int) -> tuple[bool, float]:
        """Ease h to rest before push again number ``tries``, after ``pushed`` mm in all.

        Return (whether it reached rest, mm eased). The console says the
        push again only when the ease reached rest. When the pushes again are spent (``tries`` is
        over REPUSH_TRIES), nothing moves and it returns (False, 0.0).
        """
        if tries > REPUSH_TRIES:
            return False, 0.0
        rested, eased = self.probe.ease_to_rest(h)
        if rested:
            self.adapter.say(
                f"ace2k_u1: head {n}: {th} blocked after {pushed:.0f} mm: eased"
                f" {eased:.0f} mm, pushed again ({tries}/{REPUSH_TRIES})"
            )
        return rested, eased

    @staticmethod
    def _unmeasured(
        th: str, step: float, end: str, eased: float | None, add: Callable[[float], None]
    ) -> float:
        """A push of step mm ended set or blocked under 1 mm. Return what it is counted.

        ``eased`` is the ease before a push again, None for a first push. A push again that ended
        blocked is the other lane's filament: it is counted as that ease, at most its step, because
        the tip cannot pass the point where it was blocked before that ease. Any other such end
        counts its whole step (a roll back too far only takes the tip toward its bay) and is a
        MeasureError, because where the tip is is not known.
        """
        if eased is not None and end == "blocked":
            gained = min(step, eased)
            add(gained)
            return gained
        add(step)
        raise MeasureError(
            f"{th}'s push of {step:.0f} mm ended {end} with no distance: how far it went, and"
            " so where its tip is, is not known"
        )

    def _b_to(self, b: Hooked, target: float) -> None:
        """Roll B back to target mm ahead of its parking point.

        B more than 1 mm behind target stays where it is (it is never pushed to get there).
        """
        back = self.b_out - target
        if back >= 1.0:
            self.probe.back(b, back, speed=self.probe.cfg.probe_speed)
            self.b_out = target
        elif back > -1.0:
            self.b_out = target

    def _a_to(self, a: Hooked, x: float) -> None:
        """Move A so its tip is x mm behind the head's sensor, at probe_speed.

        A move under INSIST_MIN_MM is not sent: a_back keeps where the tip is, and the search
        reads it. A roll back goes by probe.back (moves of at most the unit's longest move),
        counted by its length: a slip leaves the tip further forward, the safe side. A feed goes
        in moves of the same size, counted by the distance each reports, until x is within
        INSIST_MIN_MM; it must not reach the head's sensor, and a feed of CARRY_CHECK_MIN_MM or
        more that carries under CHUNK_CARRIED of its step is a MeasureError.
        """
        assert self.a_back is not None
        ta, speed, before = self.adapter.lane_text(a), self.probe.cfg.probe_speed, self.a_back
        diff = x - before
        if abs(diff) < INSIST_MIN_MM:
            return
        self.a_back, self.a_between = None, (min(before, x), max(before, x))
        if diff > 0:
            self.probe.back(a, diff, speed=speed)
            self.a_back = x
        else:
            left, fed = -diff, 0.0
            while left >= INSIST_MIN_MM:
                step = min(left, self.max_move(a))
                why, kind, moved = self.probe.feed_free(a, step, STOP_SET, speed=speed)
                if why == "set":
                    raise MeasureError(f"{ta} reached the head's sensor feeding to {x:.0f} mm")
                if kind != "done" or (step >= CARRY_CHECK_MIN_MM and not carried(step, moved)):
                    raise MeasureError(
                        f"{ta}'s feed of {step:.0f} mm to {x:.0f} mm ended {kind} after"
                        f" {moved:.0f} mm"
                    )
                fed += moved
                left = -diff - fed  # a slip leaves the rest to feed
                self.a_between = (min(x, before - fed), before - fed)
                if step < CARRY_CHECK_MIN_MM:
                    break  # the last, short feed: it counts what it reports
            self.a_back = before - fed  # what the feeds carried, within INSIST_MIN_MM of x
        self.a_between = None

    def _b_out_of_the_way(self, b: Hooked) -> None:
        """After an error, roll B back past the wait point (or to its parking point before it met
        A). Best effort: a failure here is said, never raised over the error."""
        target = 0.0 if self.b_wait is None else min(self.b_wait, self.b_out)
        try:
            self._b_to(b, target)
        except Exception as err:  # the error of the measure is what goes up
            self.adapter.say(
                f"ace2k_u1: {self.adapter.lane_text(b)} not rolled back after the error: {err}"
            )

    def _where(self, a: Hooked, b: Hooked) -> str:
        ta, tb = self.adapter.lane_text(a), self.adapter.lane_text(b)
        if self.a_between is not None:
            lo, hi = self.a_between
            at_a = f"{ta} is between {lo:.0f} and {hi:.0f} mm back from the head's sensor"
        elif self.a_back is None:
            at_a = f"{ta} pushed {self.a_pushed:.0f} mm from its parking point"
        else:
            at_a = f"{ta} is {self.a_back:.0f} mm back from the head's sensor"
        if self.b_out >= 1.0:
            at_b = f"{self.b_out:.0f} mm ahead of its parking point"
        elif self.b_out <= -1.0:
            at_b = f"{-self.b_out:.0f} mm behind its parking point"
        else:
            at_b = "at its parking point"
        return f"{at_a}, {tb} is {at_b}"


class SplitterHost(Protocol):
    """The ACE2kU1, as the splitter command reads it."""

    @property
    def printer(self) -> Printer: ...
    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def probe(self) -> HeadProbe | None: ...
    @property
    def swap_cfg(self) -> SwapConfig: ...


class SplitterCommand:
    """ACEK_SPLITTER_MEASURE. ``host`` is the ACE2kU1, ``bench`` its Bench (the refusals it shares
    with the other bench commands) and ``section`` the name of its config section."""

    def __init__(self, host: SplitterHost, bench: Bench, section: str) -> None:
        self.host, self.bench, self.section = host, bench, section

    def cmd_ACEK_SPLITTER_MEASURE(self, gcmd: GCodeCommand) -> None:
        """Measure the splitter of shared HEAD and stage headN_splitter_mm."""
        head = gcmd.get_int("HEAD", minval=1, maxval=LANES) - 1
        adapter, probe = self.host.adapter, self.host.probe
        slot = adapter.slot_of_head(head) if adapter is not None else None
        why, pairs = self._refusal(slot, adapter)
        if why is not None:
            raise gcmd.error(f"ace2k_u1: head {head + 1} splitter measure refused: {why}")
        assert slot is not None and adapter is not None and probe is not None  # else refused
        cfg = self.host.swap_cfg
        in_effect, default = cfg.splitter_mm[head], head in cfg.splitter_default
        results = self._run(gcmd, (slot, adapter, probe), pairs, in_effect)
        # Rounded to 3 decimals first, so float noise (840.0000000000001) does not add a step.
        raw = round(max(r.point for r in results) + SPLITTER_MARGIN_MM, 3)
        value = round(math.ceil(raw / STAGED_STEP_MM) * STAGED_STEP_MM)
        key = f"head{head + 1}_splitter_mm"
        if value > SPLITTER_MAX_MM:
            raise gcmd.error(
                f"ace2k_u1: head {head + 1}: the new value {value} mm is over the"
                f" {SPLITTER_MAX_MM:g} mm that {key} allows: nothing staged, check the tubes"
            )
        self.host.printer.lookup_object("configfile").set(self.section, key, str(value))
        if default:
            gcmd.respond_info(
                f"ace2k_u1: head {head + 1}: {key} {value} staged (was the default"
                f" {in_effect:g}); run SAVE_CONFIG"
            )
        else:  # Klipper refuses to save a key that an included file sets: the line says so
            gcmd.respond_info(
                f"ace2k_u1: head {head + 1}: {key} {value} staged (was {in_effect:g}); run"
                f" SAVE_CONFIG (if {key} is set in an included file such as ace2k-u1.cfg, delete"
                " or comment out that line first: Klipper refuses to save a key that an included"
                " file sets)"
            )
        diff = abs(value - in_effect)
        if diff > WARN_DIFF_MM:
            gcmd.respond_info(
                f"ace2k_u1: head {head + 1}: the new value {value} mm is {diff:.0f} mm from the"
                f" {in_effect:g} mm in effect: check the tubes before SAVE_CONFIG"
            )

    def _refusal(
        self, slot: Slot | None, adapter: Adapter | None
    ) -> tuple[str | None, list[tuple[Hooked, Hooked]]]:
        """Return (why the command is refused, or None; the pairs to measure)."""
        why = self.bench.bench_refusal(slot)
        if why is not None or slot is None or adapter is None:  # bench_refusal checked all three
            return why, []
        why = self._head_refusal(slot)
        if why is not None:
            return why, []
        loaded: dict[int, bool] = {}  # id of each lane -> its bay has filament
        for h in slot.lanes:
            insert, read = self.bench.bay_insert(h)
            if not read:
                return f"the bay of {adapter.lane_text(h)} cannot be read", []
            loaded[id(h)] = insert is True
        try:
            pairs = pick_pairs(slot.lanes, lambda h: loaded[id(h)], adapter.lane_text)
        except PairError as err:
            return str(err), []
        return None, pairs

    def _head_refusal(self, slot: Slot) -> str | None:
        """Return why the head is not ready: its sensor set or unread, or a lane moving."""
        is_set, why = self.bench.sensor_refusal(slot)
        if why is None and is_set:
            why = "the head's sensor sees filament: unload the head first"
        return why or self.bench.moving_refusal(slot)

    def _run(
        self,
        gcmd: GCodeCommand,
        head: tuple[Slot, Adapter, HeadProbe],
        pairs: list[tuple[Hooked, Hooked]],
        first: float,
    ) -> list[PairResult]:
        """Measure every pair with the head's lanes stopped and held.

        The head is given back after (see _give_back) when every pair passed, or when the pair
        that failed never moved (its hand-over was refused). Otherwise its A may still be in the
        shared tube: A stays the head's lane, so no load pushes another lane into its tip, and the
        console says so.
        """
        slot, adapter, probe = head
        before = slot.current
        was_armed = before is not None and before.armed
        measure = SplitterMeasure(probe, adapter, move_max_mm)
        results: list[PairResult] = []
        failed: tuple[type[Exception], ...] = (MeasureError, ProbeError, *adapter.refusals())
        moving: Hooked | None = None  # A of the pair whose measure began and did not end
        with holding(slot.lanes):
            try:
                for x in slot.lanes:
                    adapter.stop(x, "the splitter measure")
                for a, b in pairs:
                    adapter.head_watch.hand_head(slot, a)
                    moving = a
                    results.append(measure.measure_pair(slot, a, b, first))
                    moving = None
            except failed as err:
                raise gcmd.error(
                    f"ace2k_u1: head {slot.head + 1}: splitter measure failed: {err}"
                ) from None
            finally:
                if moving is None:
                    self._give_back(slot, adapter, before, was_armed)
                else:
                    adapter.say(
                        f"ace2k_u1: head {slot.head + 1}: the measure failed;"
                        f" {adapter.lane_text(moving)} stays the head's lane"
                    )
        return results

    def _give_back(
        self, slot: Slot, adapter: Adapter, before: Hooked | None, was_armed: bool
    ) -> None:
        """Name the head for the lane it had before, and arm that lane again if it followed."""
        if before is not None and slot.current is not before:
            try:
                adapter.head_watch.hand_head(slot, before)
            except adapter.refusals() as err:
                adapter.say(
                    f"ace2k_u1: head {slot.head + 1}: not given back to"
                    f" {adapter.lane_text(before)}: {err}"
                )
                return
        self.bench.rearm(slot, before, was_armed)
