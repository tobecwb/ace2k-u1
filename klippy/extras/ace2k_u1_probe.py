"""The probe of a head that several lanes feed: which lane is in it, read from the sensors and never
remembered.

Nothing about where a filament is is kept between Klipper runs. The head sensor (its raw switch),
the plungers of the lanes and the ends of the moves tell. Every move runs at probe_speed, one lane
at a time, so the switches of the unit belong to the lane that moves. Every call blocks the G-code
that runs it (reactor pauses), as the moves of the swap do. ace2k_u1.py builds and runs it.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, NoReturn, Protocol

if TYPE_CHECKING:
    from .ace2k_klipper_types import FeedEvent, Reactor
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_config import SwapConfig
    from .ace2k_u1_printer_types import Fan, FeedMove, LaneView

try:
    from .ace2k_u1_common import (
        CHUNK_CARRIED,
        INSIST_MIN_MM,
        MOVE_MARGIN_S,
        STOPPED_KINDS,
        UNIT_STOPS,
        FreshWait,
        StoppedError,
        TautWatch,
        carried,
        finite,
        move_max_mm,
        moved_mm,
        start_armed,
        unit_move_wait_s,
        unit_stops,
        wait_fresh_lane,
    )
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        CHUNK_CARRIED,
        INSIST_MIN_MM,
        MOVE_MARGIN_S,
        STOPPED_KINDS,
        UNIT_STOPS,
        FreshWait,
        StoppedError,
        TautWatch,
        carried,
        finite,
        move_max_mm,
        moved_mm,
        start_armed,
        unit_move_wait_s,
        unit_stops,
        wait_fresh_lane,
    )

PROBE_POLL_S = 0.05
PROBE_COOL_WAIT_S = 900.0  # the longest time (s) that a probe waits for a hot head to cool
PROBE_COOL_POLL_S = 1.0
PROBE_MARGIN_S = MOVE_MARGIN_S  # the wait for a move is at least twice its travel time plus this
# The taut watch is the same as in ACEK_EJECT. A report counts only when it is newer than the start
# of the move and two report periods after it, because a report sampled before the motion may arrive
# just after its start. TAUT_READINGS such different reports in a row that read the plunger pushed
# mean a taut plunger.
PROBE_FRESH_S = 0.2
# A push that reached the sensor of the head and went on into the gear leaves the plunger pressed. A
# slow roll back eases it until it reads at rest, and never further than this.
EASE_MM = 40.0
EASE_SPEED = 10.0
# The push back after an ease that emptied the sensor is what it eased plus this.
EASE_MARGIN_MM = 10.0
EASE_REPORT_WAIT_S = 1.0  # the longest (s) that the ease waits for a report newer than the stop
EASE_FRESH = FreshWait(PROBE_FRESH_S, EASE_REPORT_WAIT_S, PROBE_POLL_S)
# A push that ends blocked is pushed again from where it stopped, up to this many times, before the
# lane in the way is looked for. The unit forgives one tip snag per move, and a thick tip may catch
# twice on its way. A push again that gains less than INSIST_MIN_MM over the ease before it is a
# hard stop and ends the insisting. Each one first eases back to the rest of the plunger (see
# ease_to_rest), because a tip held by resistance keeps the plunger pressed.
INSIST_TRIES = 3
# A free check that stopped taut is watched this long (s) for the head sensor to empty. A tip that
# caught above the gear springs past the sensor as the plunger lets go. A filament that the gear
# holds keeps the sensor set.
SNAG_WAIT_S = 3.0
SNAG_EMPTY_READINGS = 3  # empty this many reads in a row (PROBE_POLL_S apart), so no switch bounce
# A lane that the search pushes, and that reaches the sensor of the head having pushed at least the
# splitter_mm of its head less this margin, came from behind the splitter, as a parked lane does. It
# was not in the way.
PARKED_MARGIN_MM = 50.0
FREE_KINDS = ("done",)  # a roll back that ran the whole length means free
# The push back of the probe re-seats a tip that the head holds, so meeting the gear is its normal
# end.
FEED_BACK_KINDS = ("done", "blocked")
RUNOUT_KIND = "runout"  # a roll back that ran out of the bay means far from the head
# What stops a polled move (see _poll): the plunger reading taut, the head sensor going empty or
# set, and a fresh report that reads the plunger at rest. A unit that can stop on its plunger ends
# the move itself on "taut" and "rest" (see _split_stops). The head sensor belongs to the U1, and
# the unit cannot see it, so the host always watches "empty" and "set" itself.
STOP_WATCH = frozenset({"taut", "empty"})
STOP_SET = frozenset({"set"})
STOP_REST = frozenset({"rest", "taut"})  # the head sensor going empty ends it too (add "empty")
# The final kind of a move that the unit ended on its plunger, to the name of the stop (see
# UNIT_STOPS).
UNIT_ENDS = {kind: why for why, kind in UNIT_STOPS.items()}


# The end of a polled move: (why it ended, the kind of the final event or None on a stop of the
# probe, the mm it moved).
MoveEnd = tuple[str, "str | None", float]
# One move of an insisting push (see _insist): (left mm, mm pushed before, tries) to (verdict, mm).
OnceMove = Callable[[float, float, int], tuple[str, float]]


# The tail a roll back may run past its length, when ace2k does not give its own (tail_mm).
TAIL_MM = 100.0


class PushToHead(Protocol):
    """The push runner of the adapter (ace2k_u1_push.PushRunner), as far as the probe uses it."""

    def push_to_head(
        self, h: Hooked, speed: float | None = None, budget: float | None = None
    ) -> str | None: ...


class ProbeAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the probe uses it."""

    @property
    def command_error(self) -> type[Exception]: ...
    @property
    def head_budget_mm(self) -> float: ...
    @property
    def push_runner(self) -> PushToHead: ...
    def lane_text(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...
    def stop(self, h: Hooked, why: str, lane: LaneView | None = None) -> None: ...
    def head_heater(self, head: int) -> tuple[float, float] | None: ...
    def head_fan(self, head: int) -> Fan | None: ...
    def head_set(self, h: Hooked) -> bool: ...
    def lane_view(
        self, h: Hooked, eventtime: float | None = None
    ) -> tuple[LaneView, bool, int | None]: ...
    def retry_busy_start(
        self,
        h: Hooked,
        start: Callable[[], FeedMove],
        wait_s: float = ...,
        period: float | None = None,
    ) -> FeedMove: ...


class ProbeError(Exception):
    """The probe could not tell the lane in the head.

    The message is the console line. ``reason`` is the reason of the unit when a start was refused
    (no_filament: the bay is empty), else None.
    """

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


class StoppedProbeError(ProbeError, StoppedError):
    """A probe move that was stopped from outside (STOPPED_KINDS): a ProbeError, and a
    StoppedError, so a background swap's line says the stop request that came meanwhile."""


def ended(message: str, kind: object) -> ProbeError:
    """The error of a probe move that ended kind: a StoppedProbeError for a stopped kind."""
    return StoppedProbeError(message) if kind in STOPPED_KINDS else ProbeError(message)


class HeldError(ProbeError):
    """The free check found the lane still held by the gear of its head.

    ``moved_mm`` is how far it rolled back before its plunger read taut.
    """

    def __init__(self, message: str, moved_mm: float) -> None:
        super().__init__(message)
        self.moved_mm = moved_mm


@contextlib.contextmanager
def holding(lanes: list[Hooked]) -> Iterator[None]:
    """Stop the lanes from re-arming for the block: each is held.

    Its hold is given back as it was at the end, so the hold of another step outlives the call.
    """
    held = [(h, h.hold) for h in lanes]
    try:
        for h in lanes:
            h.hold = True
        yield
    finally:
        for h, hold in held:
            h.hold = hold


class HeadProbe:
    """The probe of a shared head.

    ``adapter`` has its lanes, sensors and moves, ``run_script`` runs a G-code line, and ``cfg`` has
    the swap keys (probe_mm, probe_speed, probe_max_temp).
    """

    def __init__(
        self,
        adapter: ProbeAdapter,
        reactor: Reactor,
        run_script: Callable[[str], object],
        cfg: SwapConfig,
    ) -> None:
        self.a, self.reactor, self.run_script, self.cfg = adapter, reactor, run_script, cfg

    def wait_cold(self, slot: Slot) -> None:
        """Turn the heater of the head off and wait until the head is below probe_max_temp.

        A heater with a target is turned off first, even below the limit, because a head that is
        still heating is not probed. A hotter head is waited for, up to PROBE_COOL_WAIT_S. A head
        that does not cool, or whose heater cannot be read, is a ProbeError. The cold gear and
        plastic hold the filament, which is what the probe reads. While it waits, the head's print
        fan (the U1's fan bound to the extruder) runs at full speed, and it gets its speed back at
        the end of the wait, whatever the end.
        """
        head, limit = slot.head, self.cfg.probe_max_temp
        temp, target = self._heater(head)
        if not self._cold(temp, target):
            self.run_script(f"M104 S0 T{head} A0")
        if not self._hot(temp):
            return
        fan = self.a.head_fan(head)
        line = f"ace2k_u1: head {head + 1}: probe waits for the head below {limit:g} °C"
        self.a.say(line if fan is None else f"{line}, its fan on")
        if fan is None:
            self._wait_cool(head, limit)
            return
        # The status gives the speed times the fan's max_power, and a command is multiplied by it.
        power = float(getattr(fan, "max_power", 1.0)) or 1.0
        speed = float(fan.get_status(self.reactor.monotonic()).get("speed", 0.0)) / power
        fan.set_speed_from_command(1.0)
        try:
            self._wait_cool(head, limit)
        finally:
            try:
                fan.set_speed_from_command(min(1.0, speed))
            except Exception:  # the end of the wait (a ProbeError, a shutdown) is what is said
                logging.exception("ace2k_u1: head %d: its fan speed not given back", head + 1)

    def _wait_cool(self, head: int, limit: float) -> None:
        """Wait up to PROBE_COOL_WAIT_S for the head below limit, else raise ProbeError."""
        give_up = self.reactor.monotonic() + PROBE_COOL_WAIT_S
        while True:
            self.reactor.pause(self.reactor.monotonic() + PROBE_COOL_POLL_S)
            if not self._hot(self._heater(head)[0]):
                return
            if self.reactor.monotonic() >= give_up:
                raise ProbeError(f"head {head + 1} did not cool below {limit:g} °C")

    def is_cold(self, slot: Slot) -> bool:
        """True when the head of slot is cold enough to probe as it is.

        That is: heater off and below probe_max_temp. A heater that cannot be read is a ProbeError.
        """
        return self._cold(*self._heater(slot.head))

    def too_hot(self, slot: Slot) -> bool:
        """True when the head of slot is at or above probe_max_temp, whatever its heater target.

        A probe would wait for it to cool (wait_cold turns a target off itself). An unreadable
        heater is a ProbeError.
        """
        return self._hot(self._heater(slot.head)[0])

    def _hot(self, temp: float) -> bool:
        """True when a head at temp is too hot for a probe.

        It is the only comparison with the limit.
        """
        return temp >= self.cfg.probe_max_temp

    def _cold(self, temp: float, target: float) -> bool:
        return target <= 0.0 and not self._hot(temp)

    def _heater(self, head: int) -> tuple[float, float]:
        heater = self.a.head_heater(head)
        if heater is None:
            raise ProbeError(f"head {head + 1}: its temperature cannot be read")
        return heater

    def which_lane(self, slot: Slot) -> Hooked:
        """Return the lane whose filament is in the head of slot (the head sensor is set).

        On a cold head every lane in turn rolls back up to probe_mm. A plunger that goes taut means
        the gear holds the filament: it is in the head, and the lane is fed back what it rolled
        back, so its plunger is at rest again. A head sensor that goes empty means the filament sat
        at the sensor: it is in the head, and the lane is pushed back until the sensor is set. The
        whole length rolled back freely, with the sensor still set, means not this lane, and it is
        pushed back as far. A lane with an empty bay, or one just put in its bay at its grip
        (h.at_grip), is not moved. Exactly one lane found is the answer. None, or more than one, is
        a ProbeError, as is any move that ends otherwise, and nothing else moves then. A head whose
        sensor sees no filament is not probed here.

        Every lane of the head is stopped and held for the whole probe, so no follow re-arms a lane
        that was already tried while the next one moves. The hold of each lane is given back as it
        was.
        """
        n = slot.head + 1
        if not self._sensor(slot.lanes[0]):
            raise ProbeError(f"head {n}: its sensor sees no filament")
        self.wait_cold(slot)
        with holding(slot.lanes):
            for h in slot.lanes:
                self.a.stop(h, "the probe")
            found = [h for h in slot.lanes if self._try(h)]
        if not found:
            raise ProbeError(f"head {n}: no lane of the head holds its filament")
        if len(found) > 1:
            names = ", ".join(self.a.lane_text(h) for h in found)
            raise ProbeError(f"head {n}: more than one lane holds its filament: {names}")
        self.a.say(f"ace2k_u1: head {n}: probe: {self.a.lane_text(found[0])} is in the head")
        return found[0]

    def free_check(
        self, h: Hooked, length: float | None = None, speed: float | None = None
    ) -> float:
        """Roll the old lane of a swap back after the unload macro of the swap (the "free" step).

        It rolls back up to ``length`` (probe_mm) at ``speed`` (probe_speed), stopped by the first
        of: the plunger reading taut, the head sensor going empty (the tip left the sensor), or the
        end of the move. When taut, the head sensor is watched for SNAG_WAIT_S. If it goes empty,
        the tip caught above the gear (it sprang past the sensor) and the lane is free. If it stays
        set, the gear holds the lane (a HeldError, nothing fed back). For a free lane the result is
        the distance rolled back, which the roll back of the swap subtracts. The head is hot here
        and is not waited for: the plunger tells, not the cold gear. The lane is held for the move,
        and its hold is given back as it was. ACEK_FREE_CHECK runs it alone, with a length and speed
        of its own.
        """
        a = self.a
        with holding([h]):
            a.stop(h, "the swap's free check")
            self._link(h)  # no move at all without a taut reading to stop it
            length = length or self.cfg.probe_mm
            speed = speed or self.cfg.probe_speed
            # A roll back, but the tip may still be in the head here: the wait counts no tail.
            wait_s = unit_move_wait_s(h, length, speed, PROBE_MARGIN_S)
            armed, polled = self._split_stops(h, STOP_WATCH)
            move = self._start_armed(h, "rollback", length, speed, armed)
            why, kind, moved = self._watch(h, move, wait_s, polled)
            snag = why == "taut" and self._empties(h)
        if snag:
            a.say(
                f"ace2k_u1: head {h.head + 1}: {a.lane_text(h)}'s plunger went taut after "
                f"{moved:.1f} mm"
                " with the head's sensor then empty: its tip caught above the gear, not held"
            )
            return moved
        if why == "taut":
            raise HeldError(f"{a.lane_text(h)} is still held by head {h.head + 1}", moved)
        if why == "end" and kind not in FREE_KINDS + (RUNOUT_KIND,):
            raise ended(f"head {h.head + 1}: {a.lane_text(h)}'s free check ended {kind}", kind)
        return moved

    def _empties(self, h: Hooked) -> bool:
        """Wait for the head sensor to read empty.

        Return True once it reads empty SNAG_EMPTY_READINGS times in a row within SNAG_WAIT_S, else
        False. A sensor that cannot be read is a ProbeError (see _sensor).
        """
        give_up = self.reactor.monotonic() + SNAG_WAIT_S
        empty = 0
        while True:
            empty = 0 if self._sensor(h) else empty + 1
            if empty >= SNAG_EMPTY_READINGS:
                return True
            if self.reactor.monotonic() >= give_up:
                return False
            self.reactor.pause(self.reactor.monotonic() + PROBE_POLL_S)

    def check_alone(
        self,
        slot: Slot,
        h: Hooked,
        length: float,
        speed: float | None,
        back_speed: float | None = None,
    ) -> tuple[str, float, bool]:
        """Run free_check for ACEK_FREE_CHECK on h, the lane in the head of slot.

        Every lane of the head is held for the call and the others are stopped, so only h moves
        while its plunger is watched. The result is (verdict, mm, fed). The verdict is "free" (mm
        rolled back) or "held" (mm before taut). Either way h is fed back what it rolled back, at
        back_speed (probe_speed), so the lane is where it was, with its plunger at rest. ``fed`` is
        False when the final event gave no distance, and then nothing is fed back. Any other end is
        the ProbeError of free_check. The hold of each lane is given back as it was.
        """
        with holding(slot.lanes):
            for x in slot.lanes:
                if x is not h:
                    self.a.stop(x, "the free check")
            try:
                verdict, moved = "free", self.free_check(h, length, speed)
            except HeldError as err:
                verdict, moved = "held", err.moved_mm
            return verdict, moved, self._feed_back(h, moved, length, back_speed)

    def _feed_back(
        self, h: Hooked, moved: float, length: float, speed: float | None = None
    ) -> bool:
        """Feed h back what a probe rolled back (``moved`` mm, of a roll back of ``length``).

        Return False if there is no distance to feed back. A roll back that went on with a tail
        move reports more than it was asked for, and all of it is fed back, so the tip is where it
        was. A distance over length plus the unit's tail is not the filament's (an event with only
        the motor's figure), and the feed back stops there, so it never drives the tip past where
        it was by more than that.

        Done and blocked (the tip back against the gear of the head) are its ends. Any other end
        raises ProbeError.
        """
        if moved <= 0.0:
            return False
        # The tip is in the head.
        tail = getattr(h.api, "tail_mm", None)
        reach = length + (tail if finite(tail) else TAIL_MM)
        _why, kind, _moved = self.move(h, "feed", min(moved, reach), speed=speed)
        if kind not in FEED_BACK_KINDS:
            raise ended(f"head {h.head + 1}: {self.a.lane_text(h)}'s push back ended {kind}", kind)
        return True

    def _try(self, h: Hooked) -> bool:
        """Run the roll back of one lane and what follows it.

        Return True when the lane is in the head. The lane is stopped and held by which_lane.
        """
        a = self.a
        lane, _reports = self._link(h)  # no move at all without a taut reading to stop it
        if lane.get("insert") is False:
            # An empty bay holds nothing for the head. Its roll back could end in an
            # unload_incomplete that leaves the lane in error, so it is not moved.
            a.say(f"ace2k_u1: head {h.head + 1}: probe skips {a.lane_text(h)}: its bay is empty")
            return False
        if h.at_grip:
            # The lane was just put in its bay and loaded to its grip only. It is in no head,
            # and a roll back of probe_mm would take it out of its unit. The unit's own load
            # leaves it at the parking point, and that is never skipped.
            a.say(
                f"ace2k_u1: head {h.head + 1}: probe skips {a.lane_text(h)}: just put in its "
                "bay,"
                " at its grip"
            )
            return False
        why, kind, moved = self.move(h, "rollback", self.cfg.probe_mm, STOP_WATCH)
        if why == "taut":
            # Stopped under tension. It is fed back what it rolled back, so its plunger is at
            # rest again before the unload of the swap forms the tip.
            if not self._feed_back(h, moved, self.cfg.probe_mm):
                a.say(
                    f"ace2k_u1: head {h.head + 1}: {a.lane_text(h)}'s roll back gave no "
                    "distance:"
                    " not fed back"
                )
            return True
        if why == "empty":
            # The tip goes to the head, and nothing holds it.
            why, kind, _moved = self.feed_free(h, self.cfg.probe_mm, STOP_SET)
            if why != "set":
                raise ended(
                    f"head {h.head + 1}: {a.lane_text(h)} pushed back {self.cfg.probe_mm:g} mm"
                    f" without reaching the head's sensor (ended {kind})",
                    kind,
                )
            return True
        if kind == RUNOUT_KIND:
            return False  # out of its bay, far from the head, and left there
        if kind not in FREE_KINDS:
            raise ended(f"head {h.head + 1}: {a.lane_text(h)}'s probe roll back ended {kind}", kind)
        self._feed_back(h, moved, self.cfg.probe_mm)  # what it rolled back, no more
        return False

    def push_in(
        self, slot: Slot, h: Hooked, speed: float | None, budget: float | None = None
    ) -> str | None:
        """Push h to the sensor of the head of slot at ``speed``.

        Return None if h reaches the sensor, else the U1 error name (as push_to_head does).

        At the sensor, a plunger that the stop left pressed is eased (see _ease_after_set). A push
        that stops blocked is pushed again from there, up to INSIST_TRIES times (see
        _push_insisting). If it is still blocked, a lane may sit past the splitter, short of the
        sensor. Then h rolls back all it pushed in (see _back_pushed), and each other lane of the
        head in turn pushes toward the head at probe_speed. A lane that ends blocked met the
        filament in the way: it is not the lane in the way, and it rolls back the same way. The lane
        that sets the head sensor was in the way. Its tip is at the head, so it rolls back the fixed
        headN_splitter_mm at swap_speed, which leaves it behind the splitter. Then h is pushed
        again, insisting the same way. There is only one search. If no lane sets the sensor, the
        tube itself blocks: a ProbeError, and nothing more moves.

        Every lane of the head is held for the whole call, so no follow re-arms one while another
        moves. The hold of each lane is given back as it was.
        """
        with holding(slot.lanes):
            was_set = self._sensor(
                h
            )  # already set: push_to_head moves nothing, so there is nothing to ease
            attempt, again = 0, ""
            while True:
                error, pushed, known = self._push_insisting(slot, h, speed, budget)
                if error is None:
                    if attempt or not was_set:
                        self._ease_after_set(slot, h)
                    return None
                if h.push_last_end != "blocked":
                    return error
                if attempt:
                    self._give_up(slot, h, pushed, known, again)
                again = self._clear_the_way(slot, h, pushed, known)
                attempt += 1

    def _clear_the_way(self, slot: Slot, h: Hooked, pushed: float, known: bool) -> str:
        """Handle a push of h that ended blocked.

        Roll h back, find the lane in the way, and prepare to push h again. The lane in the way, if
        any, is rolled back behind the splitter. Return the text that ends the second push if it is
        blocked again. A tube blocked on its own, or a sensor still set, raises ProbeError.
        """
        a, n = self.a, slot.head + 1
        a.say(
            f"ace2k_u1: head {n}: the push of {a.lane_text(h)} is blocked before the head's "
            "sensor:"
            " looking for the lane in the way"
        )
        for x in slot.lanes:
            a.stop(x, "the probe")
        self._back_pushed(slot, h, pushed, known)
        in_way, clear = self._search(slot, h)
        if in_way is None and not clear:
            raise ProbeError(
                f"head {n}: the push of {a.lane_text(h)} is blocked in the tube, not by "
                "another lane:"
                " check the tube after the splitter"
            )
        if in_way is not None:
            self.back(
                in_way, self.cfg.splitter_mm[slot.head], speed=self.cfg.swap_speed_for(in_way.api)
            )
            a.say(f"ace2k_u1: head {n}: {a.lane_text(in_way)} was past the splitter: rolled back")
            again = f"blocked again after {a.lane_text(in_way)} rolled back"
        else:
            # Another lane reached the head from behind the splitter. The shared path is clear, and
            # the block was h's own (its tip, its tube). h is pushed again, once.
            a.say(
                f"ace2k_u1: head {n}: no lane in the way, the path to the head is clear:"
                f" {a.lane_text(h)} pushed again"
            )
            again = (
                "blocked again with the path to the head clear: check its tip (cut it"
                " straight) and its tube"
            )
        if self._sensor(h):  # h moves only with the head sensor empty
            raise ProbeError(
                f"head {n}: the head's sensor still sees filament before {a.lane_text(h)}'s push"
            )
        return again

    def _give_up(self, slot: Slot, h: Hooked, pushed: float, known: bool, again: str) -> NoReturn:
        """Handle a second push that ended blocked too.

        Roll h out of the shared tube, then raise the ProbeError of the push. ``again`` says how it
        ended.
        """
        a, n = self.a, slot.head + 1
        for x in slot.lanes:
            a.stop(x, "the probe")
        try:
            self._back_pushed(slot, h, pushed, known)  # it is never left in the shared tube
        except ProbeError as err:
            a.say(f"ace2k_u1: head {n}: {err}")
        raise ProbeError(f"head {n}: the push of {a.lane_text(h)} is {again}")

    def _insist(
        self, h: Hooked, once: OnceMove, length: float, label: str, n: int
    ) -> tuple[str, float, bool, int]:
        """Run the insisting loop of a push toward the sensor of the head.

        ``once(left, pushed, tries)`` makes one move of at most ``left`` mm and answers (verdict, mm
        moved). "blocked" means it ended blocked with its distance, and may be pushed again.
        "unknown" means it ended blocked with no distance. "abort" means the caller has the outcome
        and nothing moved counts. Anything else ends the loop. A blocked move is pushed again from
        where it stopped, up to INSIST_TRIES times, after the lane eased back to the rest of its
        plunger (see ease_to_rest; if it did not reach rest, there are no more tries). It is pushed
        again only while at least INSIST_MIN_MM of ``length`` is left, and a try that gains less
        than INSIST_MIN_MM over the ease before it is a hard stop. ``label`` names the push in the
        line said at each try. The result is (the last verdict, the mm pushed in all, whether all of
        it is known, the tries).
        """
        a = self.a
        pushed, known, tries, eased = 0.0, True, 0, 0.0
        while True:
            verdict, moved = once(length - pushed, pushed, tries)
            if verdict == "abort":
                return verdict, pushed, known, tries
            pushed += moved
            if verdict == "unknown":
                return verdict, pushed, False, tries
            hard = tries > 0 and moved - eased < INSIST_MIN_MM  # it gained nothing over the ease
            if (
                verdict != "blocked"
                or hard
                or tries >= INSIST_TRIES
                or length - pushed < INSIST_MIN_MM
            ):
                return verdict, pushed, known, tries
            rested, eased = self.ease_to_rest(h)
            pushed -= eased  # what it went back counts, rested or not, as the tip is there
            if not rested:
                return verdict, pushed, known, tries
            tries += 1
            a.say(
                f"ace2k_u1: head {n}: {label} stopped blocked after"
                f" {pushed + eased:.0f} mm: eased {eased:.0f} mm to its plunger's rest, pushed"
                f" again ({tries}/{INSIST_TRIES})"
            )

    def push_alone(self, h: Hooked, speed: float | None) -> str | None:
        """Push h to the sensor of a head that it feeds alone, pushed again while it ends blocked.

        Return None if h reaches the sensor, else the U1 error name (as push_to_head does). The
        rules are those of a shared head (see _insist), with nothing to roll back: the tube is the
        lane's own, so a push that fails is only stopped. A strand gripped behind a leftover meets
        the leftover's end, so the first push after the leftover ran out can end blocked with the
        strand still, and the same push again takes it (measured). The lane is held for the whole
        call, so no follow re-arms it between its pushes.
        """
        with holding([h]):
            error, _pushed, _known = self._push_insisting(None, h, speed)
        return error

    def back_out(self, h: Hooked, cap: float, then_mm: float, speed: float) -> None:
        """Roll h back out of its head after a refused load (MaterialGate.back_out).

        It rolls back until the head's sensor goes empty, for at most ``cap`` mm, then ``then_mm``
        more. Both parts run at ``speed``, in moves of at most the unit's longest move. A sensor
        that does not go empty within ``cap`` ends it there: the tip went back as far as the push
        may have brought it. A sensor that cannot be read at the start rolls back ``cap`` with no
        watch. The lane's follow is not armed again (as a swap's roll back), and the lane is held
        while it runs.

        A move that ends otherwise, a sensor lost while watching, or a stop whose final event does
        not come is a ProbeError, and nothing more moves.
        """
        h.follow_wanted = False  # the lane leaves the head, so its follow is not armed again
        with holding([h]):
            self.a.stop(h, "the material check")
            try:
                watch = self._sensor(h)
            except ProbeError:
                self.back(h, cap, speed)
                return
            if watch and not self._back_until_empty(h, cap, speed):
                return
            self.back(h, then_mm, speed)

    def _back_until_empty(self, h: Hooked, cap: float, speed: float) -> bool:
        """Roll h back until the head's sensor goes empty, for at most ``cap`` mm (see back_out).

        Return True once the sensor is empty, False when the roll back ran ``cap`` (or out of the
        bay) with the sensor still set. A move stopped on the empty sensor must bring its final
        event, or this raises ProbeError, so no other move starts behind a stop that failed.
        """
        n = f"head {h.head + 1}: {self.a.lane_text(h)}"
        left = cap
        while left >= 1.0:
            step = min(left, move_max_mm(h))
            wait_s = unit_move_wait_s(h, step, speed, PROBE_MARGIN_S, reverse=True)
            move = self._start(h, "rollback", step, speed)
            why, kind, _moved = self._watch(h, move, wait_s, frozenset({"empty"}))
            if why == "empty":
                if move.result() is None:
                    raise ProbeError(f"{n}: its roll back was not seen to stop")
                return True
            if kind == RUNOUT_KIND:
                return False  # out of the bay: back as far as it goes
            if kind not in FREE_KINDS:
                raise ended(f"{n}'s roll back ended {kind}", kind)
            left -= step
        return not self._sensor(h)

    def _push_insisting(
        self, slot: Slot | None, h: Hooked, speed: float | None, budget: float | None = None
    ) -> tuple[str | None, float, bool]:
        """Run push_to_head(h), pushed again while it ends blocked (see _insist).

        The unit forgives one tip snag per move, so a tip that catches twice ends a move blocked
        with nothing in the way. Only what is left of ``budget`` (head_budget_mm by default) is
        pushed, with what the ease took back counted out. A budget over the longest move of the unit
        runs in moves inside push_to_head, and these are not counted as tries. A try, or a later
        move of a push in moves, that fails otherwise (refused, timed out) is stopped. On a shared
        head (``slot``), all the pushes took is rolled back before its error is returned; with no
        ``slot`` (a lone head) nothing is rolled back. The result is (its error, the mm
        pushed in all, whether all of it is known). A blocked end with no distance leaves the tip's
        place unknown.
        """
        a = self.a
        budget = a.head_budget_mm if budget is None else budget
        error: str | None = None
        unknown = False  # a lone head's blocked end with no distance, pushed again all the same

        def move(left: float, before: float) -> str | None:
            """Run push_to_head for ``left`` mm.

            ``before`` mm were already pushed by the earlier tries. A move that raises after part of
            it ran is stopped and rolled back with the earlier tries.
            """
            try:
                return a.push_runner.push_to_head(h, speed=speed, budget=left)
            except Exception:
                if slot is not None and (before > 0.0 or h.push_moves > 1):
                    try:
                        a.stop(h, "the push")
                        self._back_failed(slot, h, before)
                    except Exception:
                        logging.exception(
                            "ace2k_u1: lane %s roll back after a push", a.lane_text(h)
                        )
                raise

        def failed(before: float) -> None:
            """Handle a push that ended otherwise (refused, timed out, stuck).

            It is stopped and what it took is rolled back with the earlier tries, so nothing is left
            in the tube.
            """
            a.stop(h, "the push")
            if slot is not None:  # a lone head's tube is the lane's own: nothing to clear
                self._back_failed(slot, h, before)

        def once(left: float, pushed: float, tries: int) -> tuple[str, float]:
            nonlocal error, unknown
            error = move(left, pushed)
            if error is None:
                return "end", h.push_mm
            if h.push_last_end not in ("blocked", "done") and (tries > 0 or h.push_moves > 1):
                failed(pushed)
                return "abort", 0.0
            if not h.push_known and (slot is not None or h.push_last_end != "blocked"):
                return "unknown", h.push_mm  # the part known; the tip is past it
            # A lone head rolls nothing back, so a blocked end with no distance (a strand that did
            # not move reads so) is pushed again all the same. The tip's place stays unknown.
            unknown = unknown or not h.push_known
            return ("blocked" if h.push_last_end == "blocked" else "end"), h.push_mm

        label = f"the push of {a.lane_text(h)}"
        verdict, pushed, known, _tries = self._insist(h, once, budget, label, h.head + 1)
        return error, pushed, (known and not unknown) or verdict == "abort"

    def _roll_to_rest(self, h: Hooked, empty_ends: bool) -> MoveEnd:
        """Roll h back until a fresh report reads its plunger at rest.

        It runs at EASE_SPEED for at most EASE_MM. It also stops on a taut plunger (something holds
        it) and, with ``empty_ends``, on the head sensor going empty. Return the (why, kind, mm) of
        the move.
        """
        stops = STOP_REST | {"empty"} if empty_ends else STOP_REST
        return self.move(h, "rollback", EASE_MM, stops, speed=EASE_SPEED)

    def ease_to_rest(self, h: Hooked) -> tuple[bool, float]:
        """Roll the lane back until its plunger is at rest, before it is pushed again.

        The roll back runs at EASE_SPEED, at most EASE_MM, until a fresh report reads the plunger at
        rest. A tip that met resistance (a thick tip, a tight tube) leaves the plunger pressed, and
        it does not come back by itself. A plunger already at rest is not moved, and a link that is
        not proven moves nothing. Return (rested, the mm it went back). ``rested`` is False if the
        move ended otherwise, on a taut plunger (something holds it), or failed, and this is said.
        The mm count either way, using the time at EASE_SPEED when the end carries no distance,
        because the tip moved back that much. The unit's own end (held, at rest) always carries
        its distance, and it is used even when short.
        """
        a, n = self.a, f"head {h.head + 1}: {self.a.lane_text(h)}"
        try:
            lane, _reports = self._link(h)
        except ProbeError as err:
            a.say(f"ace2k_u1: {n} not eased — {err}")
            return False, 0.0
        if lane.get("rest") is True:
            return True, 0.0
        t0 = self.reactor.monotonic()
        kind: object = None
        try:
            why, kind, moved = self._roll_to_rest(h, empty_ends=False)
        except ProbeError as err:
            why, moved = f"failed — {err}", 0.0
        # A stop of the host carries no distance it can trust, so its time at its speed counts.
        # The unit's own end carries the distance, even a short one (at rest a tick or two in).
        if moved < 1.0 and kind not in UNIT_ENDS:
            moved = min(EASE_MM, (self.reactor.monotonic() - t0) * EASE_SPEED)
        if why == "rest":
            return True, moved
        a.say(f"ace2k_u1: {n} not eased to its plunger's rest ({why}): no push again")
        return False, moved

    def _search(self, slot: Slot, h: Hooked) -> tuple[Hooked | None, bool]:
        """Return (the lane in the way, clear) for the head of slot.

        The lane in the way is a lane other than h whose push sets the head sensor short of the
        splitter_mm of its head (less PARKED_MARGIN_MM), or None. A lane that sets it only after
        pushing that far came from behind the splitter. It is not in the way. It rolls back what it
        pushed (at least splitter_mm), and (None, True) is returned at once, as the path to the head
        is clear. A lane whose start is refused for an empty bay holds no filament in the way. Each
        push that stops blocked is pushed again under the rules of _insist, so a tip that catches
        twice is not taken for a lane met at the junction. A try that fails otherwise rolls back
        what the lane pushed before its error goes up.
        """
        for x in slot.lanes:
            if x is not h:
                found = self._search_lane(slot, x)
                if found is not None:
                    return found
        return None, False

    def _search_lane(self, slot: Slot, x: Hooked) -> tuple[Hooked | None, bool] | None:
        """Push one other lane ``x`` toward the head, for _search.

        Return its answer when it ends the search: (x, False) if x is in the way, or (None, True) if
        x came from behind the splitter. Return None to go on with the next lane. What x pushed is
        rolled back before this returns or raises.
        """
        a, n = self.a, slot.head + 1
        # In the worst case each other lane pushes one move (head_budget_mm, at most the longest
        # move of the unit) at probe_speed before it ends, and then rolls as far back. One move at
        # most is enough, because a lane in the way sits past its splitter, well within one.
        speed = self.cfg.probe_speed
        length = min(a.head_budget_mm, move_max_mm(x))
        outcome: list[tuple[Hooked | None, bool]] = []

        def once(left: float, pushed: float, tries: int) -> tuple[str, float]:
            try:
                # The tip goes to the head, and nothing holds it.
                wait_s = unit_move_wait_s(x, left, speed, PROBE_MARGIN_S)
                move = self._start(x, "feed", left, speed, free_tip=True)
                why, kind, moved = self._watch(x, move, wait_s, STOP_SET)
            except ProbeError as err:
                if tries == 0 and err.reason == "no_filament":
                    return "abort", 0.0  # an empty bay: nothing in the way, and nothing moved
                if pushed > 0.0:
                    self._back_pushed(slot, x, pushed, True)
                raise
            if why == "set":
                outcome.append(self._reached(slot, x, pushed + moved))
                return "abort", 0.0
            if kind == "done" and carried(length, pushed + moved):
                # A whole move with neither the sensor nor a block: its tip was far back (at its
                # bay, two splitters away), not in the way. It rolls back what it pushed, once,
                # after the loop.
                return "end", moved
            if kind != "blocked":
                # The earlier tries rolled back, but the move that ended this way did not. (A lane
                # that is stuck or slipping would likely end its roll back the same way.)
                if pushed > 0.0:
                    self._back_pushed(slot, x, pushed, True)
                raise ended(f"head {n}: {a.lane_text(x)}'s push toward the head ended {kind}", kind)
            return ("blocked", moved) if moved > 0.0 else ("unknown", 0.0)

        _verdict, pushed, known, tries = self._insist(x, once, length, a.lane_text(x), n)
        if outcome:
            return outcome[0]
        if tries == 0 and pushed == 0.0 and known:
            return None
        self._back_pushed(slot, x, pushed, known)
        return None

    def _reached(self, slot: Slot, x: Hooked, pushed: float) -> tuple[Hooked | None, bool]:
        """Handle a push of x that set the head sensor after ``pushed`` mm.

        Return (x, False) if x is in the way, else roll it back and return (None, True): it came
        from behind the splitter, as a parked lane does.
        """
        a, n = self.a, slot.head + 1
        if pushed < self.cfg.splitter_mm[slot.head] - PARKED_MARGIN_MM:
            return x, False
        # It came from behind the splitter, as a parked lane does. It is not in the way, the path to
        # the head is clear, and no other lane needs trying. It rolls back what it pushed (never
        # less than splitter_mm), so its tip is where it was, behind the splitter.
        back = max(self.cfg.splitter_mm[slot.head], pushed)
        self.back(x, back, speed=self.cfg.swap_speed_for(x.api))
        a.say(
            f"ace2k_u1: head {n}: {a.lane_text(x)} reached the head from behind the "
            "splitter"
            f" ({pushed:.0f} mm): not in the way, rolled back"
        )
        return None, True

    def _back_failed(self, slot: Slot, h: Hooked, before: float) -> None:
        """Roll h back after a failed push_to_head, with ``before`` mm of earlier tries.

        It rolls back what the push took when that is known. For a later move whose travel is not
        known, it rolls back as far as the move may have gone (h.push_max_mm), because going back
        too far only takes the tip toward its bay. For a first move, _back_pushed takes an unknown
        distance.
        """
        if h.push_known:
            self._back_pushed(slot, h, before + h.push_mm, True)
        elif h.push_moves > 1:
            self._back_pushed(slot, h, before + h.push_max_mm, True)
        else:
            self._back_pushed(slot, h, before + h.push_mm, False)

    def _back_pushed(self, slot: Slot, h: Hooked, pushed: float | None, known: bool = True) -> None:
        """Roll back a push that ended blocked, at swap_speed, by what it pushed.

        Its tip is then out of the shared tube and the junction of the splitter, wherever it met the
        block. When its distance is not all known (a blocked end with none), the roll back is at
        least splitter_mm of the head, and never less than the part known. The tip is at or before
        where that part took it, and at worst it runs out of its bay, which counts as done. The
        speed is swap_speed because at probe_speed this was the slowest part of the search. A fixed
        shorter back-off is not enough: a block past the junction would leave the lane in the shared
        tube, and every other lane would meet its filament at the junction.
        """
        if known and pushed is not None and pushed >= 1.0:
            length = pushed
        else:
            length = max(pushed or 0.0, self.cfg.splitter_mm[slot.head])
        self.back(h, length, speed=self.cfg.swap_speed_for(h.api))

    def _ease_after_set(self, slot: Slot, h: Hooked) -> None:
        """Ease the plunger after the push of h reached the sensor of the head.

        If a report newer than the stop shows the plunger pressed (not at rest, not taut, so the
        stop went on into the gear), h rolls back at EASE_SPEED until a fresh report reads it at
        rest, at most EASE_MM. It also stops on a taut plunger or the head sensor emptying (see
        _roll_to_rest). The roll back is best effort: one that cannot run, or ends without reading
        rest, leaves the plunger as it is with a line, and the push stands. If the tip is off the
        sensor, though, h is pushed back at EASE_SPEED until the sensor is set, never further than
        it eased plus EASE_MARGIN_MM. A push back that does not reach the sensor is a ProbeError,
        because the push failed.
        """
        a, n = self.a, slot.head + 1
        try:
            lane = self._report_after(h)
            if lane is None or lane.get("rest") is not False or lane.get("pushed") is True:
                return
            why, kind, moved = self._roll_to_rest(h, empty_ends=True)
        except ProbeError as err:
            a.say(f"ace2k_u1: head {n}: {a.lane_text(h)} not eased: {err}")
            return
        if why == "empty":
            a.say(
                f"ace2k_u1: head {n}: {a.lane_text(h)} eased {moved:.1f} mm, off the head's sensor"
            )
            back = min(EASE_MM, moved + EASE_MARGIN_MM)
            # The tip goes back to the sensor, and nothing holds it.
            why, kind, _moved = self.feed_free(h, back, STOP_SET, speed=EASE_SPEED)
            if why != "set":
                raise ended(
                    f"head {n}: {a.lane_text(h)} eased off the head's sensor and did not reach it"
                    f" again (ended {kind})",
                    kind,
                )
            return
        if why != "rest":
            what = "its plunger went taut" if why == "taut" else f"its roll back ended {kind}"
            a.say(f"ace2k_u1: head {n}: {a.lane_text(h)} not eased: {what}")
            return
        a.say(f"ace2k_u1: head {n}: {a.lane_text(h)} eased {moved:.1f} mm")

    def _report_after(self, h: Hooked) -> LaneView | None:
        """Wait for a fresh sensors report and return the lane of h from it.

        The report arrives at least PROBE_FRESH_S after now (the stop), and the wait is up to
        EASE_REPORT_WAIT_S. Return None with no proven link or no such report.
        """
        try:
            _lane, start = self._link(h)
        except ProbeError:
            return None

        def read() -> tuple[LaneView, int | None]:
            return self._link(h)

        return wait_fresh_lane(self.reactor, read, start, EASE_FRESH)

    def back(self, h: Hooked, length: float, speed: float | None = None) -> None:
        """Roll h back ``length`` mm in moves of at most the unit's longest move.

        The speed is ``speed`` (default probe_speed). A run out of the bay counts as done. A roll
        back shorter than 1 mm is not sent.
        """
        cap = move_max_mm(h)
        several = length > cap
        while length >= 1.0:
            step = min(length, cap)
            _why, kind, moved = self.move(h, "rollback", step, speed=speed)
            if kind == RUNOUT_KIND:
                return
            if kind not in FREE_KINDS:
                raise ended(
                    f"head {h.head + 1}: {self.a.lane_text(h)}'s roll back ended {kind}", kind
                )
            if not several:
                return  # one move, so its end is the end of the roll back
            # Of several moves, count what it carried when its end tells it (a slip carries less),
            # but never under CHUNK_CARRIED of the step, which bounds the moves. With no distance,
            # count the step.
            step_carried_mm = min(step, max(moved, step * CHUNK_CARRIED)) if moved >= 1.0 else step
            length -= step_carried_mm

    def _sensor(self, h: Hooked) -> bool:
        """Return the head sensor (its raw switch).

        A sensor that cannot be read is a ProbeError, as neither set nor empty can be taken from it.
        """
        try:
            return bool(self.a.head_set(h))
        except Exception as err:
            raise ProbeError(f"head {h.head + 1}: its sensor cannot be read — {err}") from err

    def _link(self, h: Hooked) -> tuple[LaneView, int]:
        """Read the unit of the lane and return (lane dict, sensors_reports).

        A link that is not proven, a status with no report count, or a status that cannot be read is
        a ProbeError.
        """
        a = self.a
        try:
            lane, proven, reports = a.lane_view(h)
        except Exception as err:
            raise ProbeError(
                f"head {h.head + 1}: {a.lane_text(h)}: the unit's status not read — {err}"
            ) from err
        if not proven or reports is None:
            raise ProbeError(
                f"head {h.head + 1}: {a.lane_text(h)}: the unit's link is not proven, no plunger "
                "reading"
                " could stop the probe"
            )
        return lane, reports

    def move(
        self,
        h: Hooked,
        mode: str,
        length: float,
        stops: frozenset[str] = frozenset(),
        speed: float | None = None,
    ) -> MoveEnd:
        """Run one move at ``speed`` (default probe_speed), polling until it ends.

        Return (why, kind, mm moved).

        ``stops`` is what ends it early (see _poll). A stop of the host stops the lane with the
        kind None, and ``why`` is its name. A stop that the unit makes itself (see _split_stops)
        has its name too, with the kind of the final event ("held" or "at_rest"). Otherwise ``why``
        is "end", with the kind of the final event. A link lost while watching, a sensor that
        cannot be read, or a move past its time stops the lane and is a ProbeError. A refused start
        is raised as it came.
        """
        speed = speed or self.cfg.probe_speed
        # A roll back may take the filament out of the motor, so its wait counts the unit's tail.
        reverse = mode == "rollback"
        wait_s = unit_move_wait_s(h, length, speed, PROBE_MARGIN_S, reverse=reverse)
        armed, polled = self._split_stops(h, stops)
        move = self._start_armed(h, mode, length, speed, armed)
        return self._watch(h, move, wait_s, polled)

    @staticmethod
    def _split_stops(h: Hooked, stops: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
        """Return (the stops the unit makes, the stops the host polls) for a move of h.

        A unit that can stop on its plunger (see unit_stops) makes "taut" and "rest" itself, as
        "held" and "at_rest". The host then reads no status while the move runs. The head sensor
        ("empty", "set") is the U1's, and the unit cannot see it, so the host polls it on every
        unit. A unit without the stop makes none, and the host polls every stop.
        """
        if not unit_stops(h):
            return frozenset(), stops
        armed = frozenset(UNIT_STOPS[why] for why in stops if why in UNIT_STOPS)
        return armed, stops - UNIT_STOPS.keys()

    def feed_free(
        self, h: Hooked, length: float, stops: frozenset[str], speed: float | None = None
    ) -> MoveEnd:
        """Run a feed of h whose front tip nothing holds, as move runs a move.

        The feed is the free_tip one (ace2k host interface 12). Return (why, kind, mm moved).
        """
        speed = speed or self.cfg.probe_speed
        wait_s = unit_move_wait_s(h, length, speed, PROBE_MARGIN_S)
        move = self._start(h, "feed", length, speed, free_tip=True)
        return self._watch(h, move, wait_s, stops)

    def _start(
        self, h: Hooked, mode: str, length: float, speed: float, free_tip: bool = False
    ) -> FeedMove:
        """Start the move, retrying busy refusals (see _begin)."""
        return self._begin(
            h, mode, lambda: h.api.start_move(h.lane, mode, length, speed, free_tip=free_tip)
        )

    def _start_armed(
        self, h: Hooked, mode: str, length: float, speed: float, stop_when: frozenset[str]
    ) -> FeedMove:
        """Start the move with the stops that the unit makes itself (see _split_stops).

        Empty ``stop_when`` sends the start as _start does. Busy refusals are retried (see _begin).
        """
        return self._begin(h, mode, lambda: start_armed(h, mode, length, speed, stop_when))

    def _begin(self, h: Hooked, mode: str, start: Callable[[], FeedMove]) -> FeedMove:
        """Run ``start``, retrying busy refusals.

        Any other refusal of the unit, or busy past the wait, is a ProbeError that names the lane
        and the refusal and keeps its reason. A failure of the probe is a G-code error, never an
        internal one.
        """
        try:
            return self.a.retry_busy_start(h, start)
        except (RuntimeError, ValueError, self.a.command_error) as err:
            raise self._refused(h, mode, err) from err
        except Exception as err:
            if not hasattr(err, "reason"):  # not a refusal of the unit, so an internal error
                raise
            raise self._refused(h, mode, err) from err

    def _refused(self, h: Hooked, mode: str, err: Exception) -> ProbeError:
        what = "push" if mode == "feed" else "roll back"
        return ProbeError(
            f"head {h.head + 1}: {self.a.lane_text(h)}'s {what} was refused — {err}",
            reason=getattr(err, "reason", None),
        )

    def _watch(
        self,
        h: Hooked,
        move: FeedMove,
        wait_s: float,
        stops: frozenset[str] = frozenset(),
    ) -> MoveEnd:
        """Poll a started move for move, for at most wait_s.

        The caller takes wait_s from unit_move_wait_s before it starts the move. It is a backstop:
        the unit ends a move that passes its own deadline. Whatever breaks the polling stops the
        lane first, because the move must not run on behind a failure.
        """
        try:
            return self._poll(h, move, wait_s, stops)
        except Exception:
            self.a.stop(h, "the probe's failure")
            raise

    def _final(self, move: FeedMove) -> FeedEvent | None:
        """Return the final event of a stopped move (its distance is in it).

        It waits up to PROBE_MARGIN_S. Return None when it does not come.
        """
        deadline = self.reactor.monotonic() + PROBE_MARGIN_S
        while move.result() is None and self.reactor.monotonic() < deadline:
            self.reactor.pause(self.reactor.monotonic() + PROBE_POLL_S)
        return move.result()

    def _stopped(self, h: Hooked, move: FeedMove, why: str) -> MoveEnd:
        """Stop the lane for ``why`` and return the move end with its distance from the final event.

        The result read before the stop has no distance (the search tells a parked lane by it).
        """
        self.a.stop(h, "the probe")
        return why, None, moved_mm(self._final(move))

    def _poll(self, h: Hooked, move: FeedMove, wait_s: float, stops: frozenset[str]) -> MoveEnd:
        """Poll the move until it ends.

        ``stops`` can hold any of: "empty" (the head sensor went empty), "set" (it is set), "rest"
        (a fresh report reads the plunger at rest) and "taut" (fresh reports read it pushed, see
        TautWatch). A move that the unit ended on its plunger ends with the name of that stop (see
        UNIT_ENDS) and the kind of its final event.
        """
        start = self.reactor.monotonic()
        deadline = start + wait_s
        reports_wanted = bool(stops & STOP_REST)
        # A report counts only when it is newer than the start of the move and not read before.
        watch_taut = TautWatch(start + PROBE_FRESH_S, None)
        while True:
            now = self.reactor.monotonic()
            result = move.result()
            if "empty" in stops and not self._sensor(h):
                return self._stopped(h, move, "empty")
            if "set" in stops and self._sensor(h):
                return self._stopped(h, move, "set")
            if reports_wanted and result is None:
                lane, reports = self._link(h)
                if watch_taut.fresh(now, reports):
                    if "rest" in stops and lane.get("rest") is True:
                        return self._stopped(h, move, "rest")
                    if watch_taut.taut(lane.get("pushed") is True):
                        return self._stopped(h, move, "taut")
            if result is not None:
                kind = result.get("kind")
                return UNIT_ENDS.get(str(kind), "end"), kind, moved_mm(result)
            if now >= deadline:
                raise ProbeError(f"head {h.head + 1}: {self.a.lane_text(h)}'s probe move timed out")
            self.reactor.pause(now + PROBE_POLL_S)
