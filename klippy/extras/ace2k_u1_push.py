"""The push to the head, and the preload.

The push is the push of the filament of a lane by the unit to the sensor of its head. The preload
is the PRELOAD of the U1, run through that push. The adapter keeps the lanes, the objects of the
U1 and the settings of the push (feed_speed_mm_s, head_budget_mm, busy_retry_s). PushRunner runs
the pushes.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, NamedTuple, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Reactor
    from .ace2k_u1_common import Slot
    from .ace2k_u1_follow import Follow
    from .ace2k_u1_grip import GripWatch
    from .ace2k_u1_printer_types import FeedModule, FeedMove, FilamentFeed, LaneView
    from .ace2k_u1_probe import HeadProbe

try:
    from . import ace2k_u1_probe
    from .ace2k_u1_common import (
        ARMED_MODES,
        CHANNEL_WAIT_S,
        INSIST_MIN_MM,
        NUDGE_BUSY_S,
        PUSH_POLL_S,
        SEATED_MM,
        SPEED_API,
        STOPPED_KINDS,
        Hooked,
        api_at_least,
        carried,
        finite,
        forward_mm,
        move_max_mm,
        moved_mm,
        tag_read_live,
        tag_state,
        unit_move_wait_s,
    )
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_probe  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        ARMED_MODES,
        CHANNEL_WAIT_S,
        INSIST_MIN_MM,
        NUDGE_BUSY_S,
        PUSH_POLL_S,
        SEATED_MM,
        SPEED_API,
        STOPPED_KINDS,
        Hooked,
        api_at_least,
        carried,
        finite,
        forward_mm,
        move_max_mm,
        moved_mm,
        tag_read_live,
        tag_state,
        unit_move_wait_s,
    )

# The longest wait (s) for the unit's own load of a lane before a push.
LOAD_WAIT_S = 120.0
# The tag search that a push cuts on a head that one lane feeds (see PushRunner._cut_search). An
# ace2k from host interface SPEED_API on changes the speed of a running move (feed.set_speed). The
# load's parking distance and the search's reach are ace2k's defaults when its feed does not carry
# them.
PARK_MM = 300.0
SEARCH_MM = 750.0
# The longest wait (s) for the final event of a seat that the push stopped: its travel is in it.
SEAT_END_S = 2.0
# The speed (mm/s) a pull to the head after a wait starts at, whatever the unit's load speed: the
# gear must catch a tip that rests at the bay (seen on the bench: at 70 mm/s it spun past it).
SEAT_SPEED_MM_S = 30.0
# The final events of a seat to the head that ended by itself with nothing wrong: it ran its
# length short of the head, and the push goes on from there.
CLEAN_SEAT_KINDS = frozenset({"loaded", "done"})


def _park_mm(api: object) -> float:
    """Return the parking distance (mm) of the unit's load: its feed's, else ace2k's default."""
    return cast(float, getattr(api, "load_park_mm", None) or PARK_MM)


def search_reach_mm(api: object) -> float:
    """Return the reach (mm) of the load's tag search: its feed's, else ace2k's default.

    An ace2k with no tag search carries None, and the default stands in for it.
    """
    return cast(float, getattr(api, "search_mm", None) or SEARCH_MM)


class PreloadEnd(Enum):
    """A push of the preload that ended without a failure, short of the head."""

    BLOCKED = "blocked"  # the push met what cannot move, so the filament waits
    DECLINED = "declined"  # empty head, extruder shared by several lanes: nothing was pushed


BLOCKED: Final = PreloadEnd.BLOCKED
DECLINED: Final = PreloadEnd.DECLINED

# Final event of a push to the error of the U1. A kind that is not listed is a timeout.
EVENT_ERRORS = {
    "stuck": "FEED_ERR_WHEEL_SPEED",
    "tangled": "FEED_ERR_WHEEL_SPEED",
    "motor_stalled": "FEED_ERR_MOTOR_SPEED",
    "runout": "FEED_ERR_NO_FILAMENT",
    "unload_incomplete": "FEED_ERR_WHEEL_SPEED",
    "timeout": "FEED_ERR_TIMEOUT",
    **dict.fromkeys(STOPPED_KINDS, "FEED_ERR_TIMEOUT"),  # stopped from outside: no U1 error fits
    "done": "FEED_ERR_DISTANCE",
    "blocked": "FEED_ERR_DISTANCE",  # the push met what cannot move, so the head was not reached
}
# The exception code of the U1 is the base of its flow (10 preload, 30 load) plus this offset.
CODE_OFFSET = {
    "general": 0,
    "motor_speed": 1,
    "wheel_speed": 2,
    "no_filament": 3,
    "timeout": 4,
    "residual_filament": 5,
    "distance": 5,
}


@dataclass(frozen=True)
class PushResult:
    """How one move of a push ended.

    ``error`` is the error name of the U1, or None when the move ended at the sensor of the head.
    ``started`` says whether the move ran, and was not refused at its start. When a final event
    ended the move, ``end``, ``end_seq`` and ``mm`` hold its kind, its seq and the travel that it
    reports.
    """

    error: str | None
    started: bool
    end: str | None = None
    end_seq: int | None = None
    mm: float = 0.0


class PushAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the pushes use it."""

    @property
    def reactor(self) -> Reactor: ...
    @property
    def module(self) -> FeedModule: ...
    @property
    def feed_speed_mm_s(self) -> float: ...
    @property
    def head_budget_mm(self) -> float: ...
    @property
    def tag_push_speed_mm_s(self) -> float: ...
    @property
    def busy_retry_s(self) -> float: ...
    @property
    def probe(self) -> HeadProbe | None: ...
    @property
    def insister(self) -> HeadProbe | None: ...
    @property
    def follow(self) -> Follow: ...
    @property
    def grip_watch(self) -> GripWatch: ...
    def lane_text(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...
    def feed_error(self, name: str) -> str: ...
    def in_print(self) -> bool: ...
    def present(self, h: Hooked) -> bool: ...
    def head_set(self, h: Hooked) -> bool: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    def lane_status(self, h: Hooked, eventtime: float | None = None) -> LaneView: ...
    def motion(self, h: Hooked, lane: LaneView | None = None) -> str | None: ...
    def stop(self, h: Hooked, why: str, lane: LaneView | None = None) -> None: ...
    def started(self, h: Hooked, move: FeedMove) -> None: ...
    def channel_lock(
        self, ff: FilamentFeed, ch: int, wait_s: float | None
    ) -> AbstractContextManager[bool]: ...


class LoadWait(NamedTuple):
    """A wait for the unit's own load of a lane, carried from _await_search to _start_push."""

    deadline: float  # reactor time
    seen: bool  # the unit was seen loading
    seat: FeedMove | None = None  # the seat's load, when the push started one (see _seat_short)


class Begin(NamedTuple):
    """How a push begins (see PushRunner._begin)."""

    wait: LoadWait | None = None  # the wait for the unit's load, for the first move
    slow: float = 0.0  # the speed of a slow stretch (after a cut, or after a seat)
    extra: float = 0.0  # mm added to the budget by a cut
    pushed: float = 0.0  # mm the seat surely took
    slack: float = 0.0  # mm the seat may have taken beyond pushed (its travel not known)
    done: bool = False  # the push ended at its seat
    error: str | None = None  # how it ended then (None at the head's sensor)


class SeatRun(NamedTuple):
    """A seat that pulls to the head (see PushRunner._seat_to_head)."""

    length: float  # mm, the push's first move
    speed: float  # mm/s, the push's speed once the tag is read or out of reach
    base: float | None  # the lane's encoder (mm) before the seat began, or None when not read


class SeatEnd(NamedTuple):
    """How a seat to the head ended (see PushRunner._seat_to_head)."""

    result: PushResult | None  # how the push ended, or None when it goes on from the seat
    low: float  # mm the seat surely took
    high: float  # mm the seat may have taken (its length when its travel is not known)


class PushRunner:
    """The pushes of one adapter: the push of a lane to its head, and the preload."""

    def __init__(self, adapter: PushAdapter) -> None:
        self.adapter = adapter

    def _start_push(
        self,
        h: Hooked,
        speed: float,
        budget: float,
        wait: LoadWait | None = None,
    ) -> tuple[FeedMove | None, PushResult | None]:
        """Start the push at speed. Return (move, None), or (None, how the push ended).

        While the unit is still loading the lane itself, the push waits for up to LOAD_WAIT_S. The
        unit is loading when the mode says so, or when it refuses the start as busy (the mode
        report is slower than the unit). ``wait`` carries on a wait that _await_search began: its
        deadline, whether the unit was seen loading, and the seat's load if the push started one.
        """
        if self.adapter.motion(h) in ARMED_MODES:
            # The follow (the one of ace2k at the start, on a filament that is not at the head)
            # or an assist is running. The push needs the lane. The load of the U1 arms the
            # follow again at its extrusion.
            self.adapter.stop(h, "the push")
        give_up, waiting, seat = wait or LoadWait(
            self.adapter.reactor.monotonic() + LOAD_WAIT_S, False
        )
        # This is not retry_busy_start. A busy refusal here is the unit's own load, so each
        # wait also reads the lane (the load may fail, or the bay may empty), and the window is
        # LOAD_WAIT_S.
        next_try = 0.0  # a busy refusal defers the next start by busy_retry_s
        while True:
            lane = self.adapter.lane_status(h)
            mode = lane.get("mode")
            waiting = waiting or mode == "loading"
            ended = self._wait_ended(h, lane, waiting, seat)
            if ended is not None:
                return None, ended
            # Until the seat's final event, an idle report does not say the tip is in the gear.
            free = mode != "loading" and not _seat_open(seat)
            if free and self.adapter.reactor.monotonic() >= next_try:
                started = self._try_start(h, speed, budget)
                if started is not None:
                    move, error = started
                    return move, None if error is None else PushResult(error, False)
                waiting = True
                next_try = self.adapter.reactor.monotonic() + self.adapter.busy_retry_s
            now = self.adapter.reactor.pause(self.adapter.reactor.monotonic() + PUSH_POLL_S)
            if now >= give_up:
                logging.warning(
                    "ace2k_u1: lane %s still loading, push abandoned", self.adapter.lane_text(h)
                )
                h.api.stop(h.lane)
                return None, PushResult(self.adapter.feed_error("FEED_ERR_TIMEOUT"), False)

    def _wait_ended(
        self, h: Hooked, lane: Mapping[str, object], waiting: bool, seat: FeedMove | None
    ) -> PushResult | None:
        """Return how the push ended while it waits for the unit's load of h, else None.

        Once the unit was seen loading (``waiting``), a failed load ends it (see _load_failed);
        then a seat's load that ended short does (see _seat_short). ``lane`` is the status of h.
        """
        error = self._load_failed(h, lane) if waiting else None
        if error is not None:
            return PushResult(error, False)
        return self._seat_short(h, seat.result() if seat is not None else None)

    def _seat_short(self, h: Hooked, event: Mapping[str, object] | None) -> PushResult | None:
        """Return how the push ended if the seat's load of h ended short of the gear, else None.

        ``event`` is the seat's final event, None while it runs.

        A seat that took the filament SEATED_MM forward or more is behind the push (a seat is never
        cut: see _seat_to_head). One that ended shorter (blocked, stuck, a stop by
        hand) left the tip at the bay, where a feed has no grace. The push then ends as a blocked
        push, with the seat's travel: a preload waits, quietly, and a lone head's load pushes
        again, which seats again (see Hooked.seat_due).
        """
        if event is None:
            return None
        went = forward_mm(event)
        if went >= SEATED_MM:
            return None
        logging.warning(
            "ace2k_u1: lane %s seat ended %s after %.1f mm",
            self.adapter.lane_text(h),
            event.get("kind"),
            went,
        )
        seq = event.get("seq")
        return PushResult(
            self.adapter.feed_error("FEED_ERR_DISTANCE"),
            False,  # no feed ran: the travel is the seat's, known from its event
            "blocked",
            seq if isinstance(seq, int) else None,
            went,
        )

    def _load_failed(self, h: Hooked, lane: Mapping[str, object]) -> str | None:
        """Return the error name of the U1 if the load of the unit on h failed, else None.

        The load failed when it ended in error or the bay emptied. ``lane`` is the status of h.
        """
        if lane.get("mode") == "error":
            logging.warning("ace2k_u1: lane %s load ended in error", self.adapter.lane_text(h))
            return self.adapter.feed_error("FEED_ERR_MOTOR_SPEED")
        if lane.get("insert") is not True:
            return self.adapter.feed_error("FEED_ERR_NO_FILAMENT")
        return None

    def _try_start(
        self, h: Hooked, speed: float, budget: float
    ) -> tuple[FeedMove | None, str | None] | None:
        """Try once to start the move of the push.

        Return (move, None), or (None, the error name of the U1) when the start was refused. Return
        None when the unit refused the start as busy, so the caller tries again later.
        """
        try:
            # The tip goes to the head, and nothing holds it.
            move = h.api.start_move(h.lane, "feed", budget, speed, free_tip=True)
            self.adapter.started(h, move)
            return move, None
        except Exception as err:  # FeedRefused (with a reason), ValueError, RuntimeError
            reason = getattr(err, "reason", None)
            if reason == "busy":
                return None
            logging.warning("ace2k_u1: lane %s push refused: %s", self.adapter.lane_text(h), err)
            name = "FEED_ERR_NO_FILAMENT" if reason == "no_filament" else "FEED_ERR_MOTOR_SPEED"
            return None, self.adapter.feed_error(name)

    def push_to_head(
        self, h: Hooked, speed: float | None = None, budget: float | None = None
    ) -> str | None:
        """Push the lane until the filament sensor of the head sees the filament.

        The push runs at ``speed`` (default feed_speed) for at most ``budget`` mm (default
        head_budget_mm). It returns None on success, else the error name of the U1 (motor_speed,
        wheel_speed, no_filament, timeout, distance).

        A push that ended by a final event leaves that kind in h.push_end and h.push_last_end. It
        leaves in h.push_mm how far it went, which is the sum of the moves that ended. (At the
        sensor the last move has no final event, so it is not counted. A seat to the head is the
        exception: its travel is counted, from the encoder, see _seat_to_head.) h.push_known is
        False when the last move ran and ended, or raised, with no distance. Then the tip is past
        h.push_mm by up to that move, and h.push_max_mm holds that farthest point. h.push_moves
        is how many moves it tried.

        A budget over the longest move of the unit runs in moves of at most that length. (The bay
        can be over 2 000 mm from the head through two splitters.) A move that ends "done" short of
        the sensor goes on with the rest only if it carried the filament at least CHUNK_CARRIED of
        its length, and at least INSIST_MIN_MM of the budget is left. A move that slipped, or that
        reported no distance, ends the push. The error stands, so a tube that came loose spends
        the budget once and never more.
        """
        speed = speed or self.adapter.feed_speed_mm_s
        h.push_end = h.push_last_end = None
        h.push_mm = 0.0
        h.push_known = True
        h.push_moves = 0
        h.push_max_mm = 0.0
        h.push_slow_until = None
        budget = self.adapter.head_budget_mm if budget is None else budget
        if self.adapter.head_set(h):
            return None  # the head holds the filament of the lane that it loads
        error = self._shared_refusal(h)
        if error is not None:
            return error
        cap = move_max_mm(h)
        begin = self._begin(h, speed, budget, cap)
        if begin.done:
            return begin.error
        wait, slow, pushed, slack = begin.wait, begin.slow, begin.pushed, begin.slack
        # A seat whose travel is not known spends its whole length of the budget.
        budget += begin.extra - slack
        while True:
            step = min(budget - pushed, cap)
            h.push_end = None  # only the end of this move counts, once it has one
            move_speed = speed if h.push_slow_until is None else slow
            try:
                result = self._push_once(h, move_speed, step, speed, wait)
                wait = None  # only the first move carries on the wait for the unit's load
            except BaseException:
                # What the earlier moves took is kept for the roll back of the caller.
                _move_raised(h, pushed, step + slack)
                raise
            pushed += result.mm
            self._record_move(h, result, pushed, step)
            if slack > 0.0:  # the seat's travel is not known: neither is the push's
                h.push_known = False
                h.push_max_mm += slack
            if result.error is None:
                return None
            if not self._insists(h, result, budget, step):
                return result.error

    def _begin(self, h: Hooked, speed: float, budget: float, cap: float) -> Begin:
        """Start the push of h at a seat, or at the unit's own load, before its moves.

        A filament that waited is seated (see _seat). When a cut is possible, the seat pulls as
        far as the push's first move would, and is watched to the head (see _seat_to_head). A
        seat is never shorter than SEATED_MM: with less budget left, the push runs as if nothing
        waited. Otherwise the unit's own load may have its tag search cut (see _cut_search).
        """
        length = min(budget, cap)
        cut = self.cut_possible(h)
        through = cut and h.seat_due and length >= SEATED_MM
        base = None
        if through:
            base = self._seat_base(h)
            seat = self._seat(h, length, self._seat_speed(h, speed))
        elif not cut:
            seat = self._seat(h, _park_mm(h.api), self._seat_speed(h, speed))
        else:
            seat = None
        if seat is None:
            wait = self._await_search(h)
            slow, extra = self._cut_search(h, speed)
            return Begin(wait, slow, extra)
        if not through:
            # The seat to the parking point. The push waits for its end and goes on from there.
            # The report may still say idle just after the seat's start: the unit counts as
            # loading all the same, so a busy refusal of the push is that load.
            deadline = self.adapter.reactor.monotonic() + LOAD_WAIT_S
            return Begin(LoadWait(deadline, True, seat))
        end = self._seat_to_head(h, seat, SeatRun(length, speed, base))
        if self.adapter.head_set(h):
            # The filament reached the head as the seat ended, whatever its end said.
            h.push_end = h.push_last_end = None
            return Begin(done=True)
        if end.result is not None:
            return Begin(done=True, error=end.result.error)
        if budget - end.high < INSIST_MIN_MM:  # the seat spent the budget
            return Begin(done=True, error=self.adapter.feed_error("FEED_ERR_DISTANCE"))
        # The seat ended by itself past the gear, short of the head: the push goes on from there,
        # with what is left of the budget, still slow while the tag may be read (see _seat_to_head).
        deadline = self.adapter.reactor.monotonic() + LOAD_WAIT_S
        slow = self._tag_speed(speed) if h.push_slow_until is not None else 0.0
        return Begin(LoadWait(deadline, True), slow, pushed=end.low, slack=end.high - end.low)

    def _seat_base(self, h: Hooked) -> float | None:
        """Return the lane's encoder (mm) before a seat to the head starts, else None.

        A status that cannot be read is logged: the seat's travel then comes from its final event
        alone.
        """
        try:
            encoder = self.adapter.lane_status(h).get("encoder_mm")
        except Exception:
            logging.exception("ace2k_u1: lane %s status before its seat", self.adapter.lane_text(h))
            return None
        return encoder if finite(encoder) else None

    def _seat(self, h: Hooked, length: float, speed: float) -> FeedMove | None:
        """Seat a waiting filament with the unit's load from the bay, before the push.

        A waiting filament sits at the bay, not in the gear. A feed has no grace for the gear
        catching a tip: a feed on such a tip can end blocked with the filament still (measured).
        The unit's load has that grace. The load pulls ``length`` mm at ``speed``: to the head
        when the push can watch it there (see _seat_to_head), else to the parking point. Its final
        event, once it took the filament into the gear, sets h.seated, so a push again seats
        nothing. A refused load is a log line, and the push goes on as it would have. A head that
        several lanes feed is never seated.

        Return the load's move when it started, else None.
        """
        if not h.seat_due:
            return None
        slot = self.adapter.slot_of(h)
        if slot is not None and slot.shared:
            return None
        api = h.api
        try:
            move = api.start_move(h.lane, "load", length, speed)
        except Exception as err:  # FeedRefused (with a reason), ValueError, RuntimeError
            logging.warning("ace2k_u1: lane %s seat refused: %s", self.adapter.lane_text(h), err)
            return None
        self.adapter.started(h, move)
        seq = getattr(move, "seq", None)
        h.seat_seq = seq if isinstance(seq, int) else None
        logging.info("ace2k_u1: lane %s seated with the unit's load", self.adapter.lane_text(h))
        return move

    def _load_speed(self, h: Hooked) -> float:
        """Return the speed (mm/s) of the unit's load of h: its feed's, else feed_speed."""
        return cast(float, getattr(h.api, "load_speed_mm_s", None) or self.adapter.feed_speed_mm_s)

    def _tag_speed(self, speed: float) -> float:
        """Return the speed (mm/s) of a pull whose tag may still be read on the way.

        It is tag_push_speed, or speed when tag_push_speed is at or above it (see _cut_search).
        """
        return min(self.adapter.tag_push_speed_mm_s, speed)

    def _seat_speed(self, h: Hooked, speed: float) -> float:
        """Return the speed (mm/s) a seat of a filament that waited starts at.

        It is SEAT_SPEED_MM_S, at most the unit's load speed and the tag's: the gear must catch a
        tip that rests at the bay, so a higher load speed never raises it, and a lower one is
        kept. A tag_push_speed of 0 (no cut) does not count. A seat to the head speeds up to speed
        once the tag is read or the search's reach is behind (see _speed_up).
        """
        seat = min(SEAT_SPEED_MM_S, self._load_speed(h))
        if self.adapter.tag_push_speed_mm_s > 0.0:
            seat = min(seat, self._tag_speed(speed))
        return seat

    def _seat_to_head(self, h: Hooked, seat: FeedMove, run: SeatRun) -> SeatEnd:
        """Watch a seat that pulls toward the head, as the first move of the push.

        After a wait the filament is at the bay. The unit's load takes it from there in one pull,
        with its grace for the gear catching the tip, and reads the tag on the way. Nothing stops
        that pull early, and the filament never goes back to the parking point: the pull stops
        when the head's sensor sees the filament. It runs at the seat's speed (see _seat_speed:
        SEAT_SPEED_MM_S, at most the unit's load speed and tag_push_speed) until the tag is read or
        the filament has gone the search's reach since the seat began, then at speed (see
        _speed_up).

        The push ends here at the head's sensor, on a failed load or an emptied bay once the lane
        was seen loading (see _load_failed), at a timeout (a load still running is stopped), or
        on a seat that ended by itself in error or short of the gear (see _seat_end). A seat that
        ended by itself past the gear, with no error, leaves the push to go on from there: if the
        tag may still be read, h.push_slow_until stays set for the rest of the push. The seat is
        recorded as the push's first move.
        """
        speed = run.speed
        slow = self._seat_speed(h, speed)
        if slow < speed:
            # Until the encoder is read, the stretch has no end but the tag's read.
            reach = search_reach_mm(h.api)
            h.push_slow_until = run.base + reach if finite(run.base) else math.inf
        # A backstop: the unit ends a move that passes its own deadline (see unit_move_wait_s).
        deadline = self.adapter.reactor.monotonic() + unit_move_wait_s(h, run.length, slow)
        seen = False  # the lane was seen loading: its report is this seat's
        end: SeatEnd | None = None
        try:
            while end is None:
                now = self.adapter.reactor.pause(self.adapter.reactor.monotonic() + PUSH_POLL_S)
                lane = self.adapter.lane_status(h)
                run = self._seat_start(h, run, lane, slow < speed)
                seen = seen or lane.get("mode") == "loading"
                end = self._seat_poll(h, seat, run, lane if seen else None)
                if end is None and now >= deadline:
                    logging.warning("ace2k_u1: lane %s seat timed out", self.adapter.lane_text(h))
                    timeout = PushResult(self.adapter.feed_error("FEED_ERR_TIMEOUT"), True)
                    end = self._seat_stopped(h, seat, timeout, run)
                elif end is None:
                    self._speed_up(h, speed, lane)
        except BaseException:
            self._seat_raised(h, seat, run, end)
            raise
        finally:
            if end is None or end.result is not None:
                h.push_slow_until = None
            else:
                self._keep_slow(h, run, end)
        return end

    def _seat_start(self, h: Hooked, run: SeatRun, lane: LaneView, slow: bool) -> SeatRun:
        """Return run with its start, taken from lane when the start was not read before.

        The seat then begins at its first reading, and a ``slow`` stretch ends the search's reach
        past it.
        """
        encoder = lane.get("encoder_mm")
        if finite(run.base) or not finite(encoder):
            return run
        if slow:
            h.push_slow_until = encoder + search_reach_mm(h.api)
        return run._replace(base=encoder)

    def _seat_poll(
        self, h: Hooked, seat: FeedMove, run: SeatRun, lane: LaneView | None
    ) -> SeatEnd | None:
        """Return how the seat of h ended at this poll, else None while it runs.

        ``lane`` is the lane's status once the lane was seen loading in this seat, else None (a
        report from before the start says nothing about it). The head's sensor is read after the
        seat's event, so a filament at the head is a success whatever the event says.
        """
        event = seat.result()
        if self.adapter.head_set(h):
            return self._seat_stopped(h, seat, PushResult(None, True), run)
        error = self._load_failed(h, lane) if lane is not None else None
        if error is not None:
            return self._seat_stopped(h, seat, PushResult(error, True), run)
        if event is not None:
            return self._seat_end(h, event, run)
        return None

    def _seat_raised(self, h: Hooked, seat: FeedMove, run: SeatRun, end: SeatEnd | None) -> None:
        """Record a seat of h whose watch raised, and stop it if it still runs.

        A push toward a head always stops: whatever broke the watch, the seat must not run on to
        its length. A seat that already ended keeps the travel it recorded or its event gives.
        Otherwise how far it went is not known.
        """
        if end is not None:
            return  # recorded already
        event = seat.result()
        if event is not None and finite(event.get("filament_mm")):
            went = forward_mm(event)
            self._record_seat(h, PushResult(None, True), went, went)
            return
        if event is None:
            try:
                h.api.stop(h.lane)
            except BaseException:
                logging.exception(
                    "ace2k_u1: lane %s stop after a failed seat", self.adapter.lane_text(h)
                )
        _move_raised(h, 0.0, run.length)

    def _keep_slow(self, h: Hooked, run: SeatRun, end: SeatEnd) -> None:
        """Keep the slow stretch after a seat that ended past the gear, while the tag may be read.

        A fresh status of the lane decides: the stretch ends when the tag was read, or the encoder
        (else the start plus the seat's travel) already reads h.push_slow_until. A stretch with no
        known end (no encoder ever read) ends with the seat: the rest runs at speed.
        """
        until = h.push_slow_until
        if until is None or not finite(until):
            h.push_slow_until = None
            return
        try:
            lane = self.adapter.lane_status(h)
        except Exception:
            logging.exception("ace2k_u1: lane %s status after its seat", self.adapter.lane_text(h))
            h.push_slow_until = None
            return
        encoder = lane.get("encoder_mm")
        if not finite(encoder) and finite(run.base):
            encoder = run.base + end.low
        if tag_read_live(lane) or (finite(encoder) and encoder >= until):
            h.push_slow_until = None

    def _seat_end(self, h: Hooked, event: Mapping[str, object], run: SeatRun) -> SeatEnd:
        """Return how the push goes on after the seat of h ended by itself with event.

        A seat known to be short of the gear ends the push blocked (see _seat_short). One past
        it, or of unknown travel, that ended clean (loaded, done) leaves the push to go on. Any
        other kind ends the push with that kind, as a push move's final event does.
        """
        low, high = self._seat_went(h, event, run)
        # Short of the gear only by the event's own distance: one with none may have gone far.
        result = self._seat_short(h, event) if finite(event.get("filament_mm")) else None
        if result is None and event.get("kind") not in CLEAN_SEAT_KINDS:
            result = self._event_result(event, low)
        self._record_seat(h, result or PushResult(None, True), low, high)
        return SeatEnd(result, low, high)

    def _event_result(self, event: Mapping[str, object], mm: float) -> PushResult:
        """Return the PushResult of a move that ended by its final event before the head, which
        took the filament mm."""
        kind = event.get("kind")
        kind = kind if isinstance(kind, str) else None  # a missing kind stays None
        seq = event.get("seq")
        return PushResult(
            self.adapter.feed_error(EVENT_ERRORS.get(kind or "", "FEED_ERR_TIMEOUT")),
            True,
            kind,
            seq if isinstance(seq, int) else None,
            mm,
        )

    def _seat_stopped(self, h: Hooked, seat: FeedMove, result: PushResult, run: SeatRun) -> SeatEnd:
        """Stop the running seat of h, record it as ending with result, and return how it ended.

        Its travel is in its final event, which comes after the stop: the wait for it is at most
        SEAT_END_S.
        """
        if seat.result() is None:
            h.api.stop(h.lane)
        deadline = self.adapter.reactor.monotonic() + SEAT_END_S
        while seat.result() is None and self.adapter.reactor.monotonic() < deadline:
            self.adapter.reactor.pause(self.adapter.reactor.monotonic() + PUSH_POLL_S)
        low, high = self._seat_went(h, seat.result(), run)
        self._record_seat(h, result, low, high)
        return SeatEnd(result, low, high)

    def _seat_went(
        self, h: Hooked, event: Mapping[str, object] | None, run: SeatRun
    ) -> tuple[float, float]:
        """Return how far the seat of h surely took the filament, and how far it may have (mm).

        The seat's final event tells it. Without one, the encoder since the seat began gives what
        it surely took, and it may have gone its whole length.
        """
        if event is not None and finite(event.get("filament_mm")):
            went = forward_mm(event)
            return went, went
        encoder = self.adapter.lane_status(h).get("encoder_mm")
        low = 0.0
        if finite(encoder) and finite(run.base) and encoder > run.base:
            low = encoder - run.base
        return low, max(low, run.length)

    @staticmethod
    def _record_seat(h: Hooked, result: PushResult, low: float, high: float) -> None:
        """Record the seat of h, the push's first move, which ended as result.

        ``low`` is how far it surely went, ``high`` how far it may have: its travel is known when
        the two agree.
        """
        h.push_moves += 1
        h.push_end = h.push_last_end = result.end
        if result.end is not None:
            h.push_end_seq = result.end_seq
        h.push_mm, h.push_known, h.push_max_mm = low, low == high, high

    def cut_possible(self, h: Hooked) -> bool:
        """Whether a tag search of h could be cut: tag_push_speed on, an ace2k that changes the
        speed of a running move, and a head that h alone feeds."""
        if self.adapter.tag_push_speed_mm_s <= 0.0:
            return False
        if not api_at_least(h.api, SPEED_API):
            return False
        slot = self.adapter.slot_of(h)
        return slot is None or not slot.shared

    def _await_search(self, h: Hooked) -> LoadWait | None:
        """Wait, on a head that h alone feeds, until the unit's load of h reaches its tag search.

        The unit reports its load's tag as searching from the load's first mm. On a normal load
        the U1 asks for the push only once that load has run a while (about 52 mm on the bench),
        so the tip is in the gear when the cut (_cut_search) stops it. If the report does not say
        searching yet, the push waits for it, or for the load to end (the tag read on the way, or
        a load that searched nothing), for up to LOAD_WAIT_S. A failed load or an emptied bay
        ends the wait too: _start_push reports it. A filament that waited is never cut: its seat
        is the push (see _seat_to_head).

        Return the wait for _start_push to carry on (its deadline, and whether the unit was seen
        loading), or None when no cut is possible.
        """
        if not self.cut_possible(h):
            return None
        give_up = self.adapter.reactor.monotonic() + LOAD_WAIT_S
        seen = False
        while True:
            lane = self.adapter.lane_status(h)
            loading = lane.get("mode") == "loading"
            seen = seen or loading
            searching = tag_state(lane) == "searching"
            if not loading or searching or self._load_failed(h, lane):
                return LoadWait(give_up, seen)
            now = self.adapter.reactor.pause(self.adapter.reactor.monotonic() + PUSH_POLL_S)
            if now >= give_up:
                return LoadWait(give_up, seen)

    def _cut_search(self, h: Hooked, speed: float) -> tuple[float, float]:
        """Stop the unit's tag search on a head that h alone feeds, so the push goes on from there.

        The unit's load searches for the tag past the parking point, and then it comes back to that
        point, whatever it found. On a head that one lane feeds, nothing needs the filament back
        there, so the push goes on from where the load stopped. The tag is still read on the way:
        the push runs at tag_push_speed (a speed at which reads on the move were measured) until
        the filament has gone the search's reach since the load began, or the tag is read. Then it
        runs at speed (see _speed_up). A tag_push_speed at or above speed leaves no slow stretch:
        the whole push runs at speed, the tag read on the way.

        Return (the speed of that first stretch, the mm to add to the budget). The mm are the part
        of the pull to the parking point that the load did not run. Return (0, 0) when nothing is
        cut: a shared head, a lane that is not in the unit's load with its tag searched,
        tag_push_speed 0, or an ace2k that cannot change the speed of a running move.
        """
        slow = self.adapter.tag_push_speed_mm_s
        if not self.cut_possible(h):
            return 0.0, 0.0
        lane = self.adapter.lane_status(h)
        searching = tag_state(lane) == "searching"
        if lane.get("mode") != "loading" or not searching:
            return 0.0, 0.0
        before = lane.get("last_event")
        try:
            h.api.stop(h.lane)
        except Exception:
            # The load goes on to its own end, and the push waits for it, as before the cut.
            logging.exception("ace2k_u1: lane %s tag search stop", self.adapter.lane_text(h))
            return 0.0, 0.0
        lane = self._load_end(h)
        event = lane.get("last_event")
        # Only the stopped load's own event tells how far it went. An older one, or none, counts
        # nothing: the whole reach runs slow and the budget takes the whole pull.
        went = forward_mm(event) if event is not before else 0.0
        reach = max(0.0, search_reach_mm(h.api) - went)
        extra = max(0.0, _park_mm(h.api) - went)
        if slow >= speed:
            self.adapter.say(
                f"ace2k_u1: lane {self.adapter.lane_text(h)}: its tag not read yet: the unit's"
                f" search is stopped and the push goes on to e{h.head} at {speed:g} mm/s, the tag"
                " read on the way"
            )
            return speed, extra
        base = lane.get("encoder_mm")
        # With no encoder reading, the stretch runs slow until the tag is read.
        h.push_slow_until = base + reach if finite(base) else math.inf
        self.adapter.say(
            f"ace2k_u1: lane {self.adapter.lane_text(h)}: its tag not read yet: the unit's search"
            f" is stopped and the push goes on to e{h.head} at {slow:g} mm/s for the search's last"
            f" {reach:.0f} mm, then at {speed:g} mm/s"
        )
        return slow, extra

    def _load_end(self, h: Hooked) -> LaneView:
        """Wait up to LOAD_WAIT_S for the stopped load of h to end. Return the lane's status.

        A load that still runs then is left to the wait of _start_push.
        """
        give_up = self.adapter.reactor.monotonic() + LOAD_WAIT_S
        lane = self.adapter.lane_status(h)
        while lane.get("mode") == "loading" and self.adapter.reactor.monotonic() < give_up:
            self.adapter.reactor.pause(self.adapter.reactor.monotonic() + PUSH_POLL_S)
            lane = self.adapter.lane_status(h)
        return lane

    def _speed_up(self, h: Hooked, speed: float, lane: LaneView | None = None) -> None:
        """End the slow stretch of a push that cut the tag search (see _cut_search).

        It ends once the lane's tag is read, or its encoder reads h.push_slow_until. The move then
        runs at speed. A change that the unit refuses or does not answer is logged, and the push
        goes on at its slow speed. ``lane`` is the lane's status when the caller read it already.
        """
        until = h.push_slow_until
        if until is None:
            return
        if lane is None:
            lane = self.adapter.lane_status(h)
        read = tag_read_live(lane)
        encoder = lane.get("encoder_mm")
        if not read and not (finite(encoder) and encoder >= until):
            return
        h.push_slow_until = None
        try:
            h.api.set_speed(h.lane, speed)
        except Exception:
            logging.exception("ace2k_u1: lane %s push speed", self.adapter.lane_text(h))
            return
        logging.info(
            "ace2k_u1: lane %s: %s: the push goes on at %g mm/s",
            self.adapter.lane_text(h),
            "its tag read" if read else "the search's reach gone",
            speed,
        )

    def _shared_refusal(self, h: Hooked) -> str | None:
        """Return the error name of the U1 if the head is not a + group but its extruder is shared.

        That means several lanes share the extruder. The refusal is said. The result is None
        otherwise.
        """
        slot = self.adapter.slot_of(h)
        shared = self.adapter.follow.shared_with(h) if slot is None or not slot.shared else None
        if not shared:
            return None
        # Several lanes feed the extruder of the head. This adapter does not pick which filament
        # the head should get, so nothing is pushed into an empty head. A head of several lanes
        # (a + group) pushes its current lane only, the choice of the coordinator. With none in
        # it, the push never reaches here (see put_into_drive).
        self.adapter.say(
            f"ace2k_u1: e{h.head} is shared by lanes {shared}: load refused with its sensor "
            "empty — put the filament in the head by hand, then load"
        )
        return self.adapter.feed_error("FEED_ERR")

    @staticmethod
    def _record_move(h: Hooked, result: PushResult, pushed: float, step: float) -> None:
        """Record the push of h after one move of step mm ended (result).

        ``pushed`` is the travel of the push so far, including that move.
        """
        # A move that ran and ended without a distance (no final event, or none in it): its tip
        # is past push_mm by up to the whole move.
        known = result.error is None or not result.started or result.mm > 0.0
        h.push_end = h.push_last_end = result.end
        if result.end is not None:
            h.push_end_seq = result.end_seq
        h.push_moves += 1
        h.push_mm = pushed
        h.push_known = known
        # A move stopped at the head's sensor has no final event either: it went at most step.
        h.push_max_mm = pushed if known and result.error is not None else pushed + step

    def _insists(self, h: Hooked, result: PushResult, budget: float, step: float) -> bool:
        """True when a move of step mm that ended "done" short of the sensor is followed by another.

        ``result`` is the result of that move. The move must not be the last of the budget, at
        least INSIST_MIN_MM of the budget must be left, and the filament must have been carried
        (see carried). h.push_mm is the travel of the push so far.
        """
        cap = move_max_mm(h)
        if (
            result.error != self.adapter.feed_error("FEED_ERR_DISTANCE")
            or h.push_last_end != "done"
        ):
            return False
        if budget <= cap or step < cap or budget - h.push_mm < INSIST_MIN_MM:
            return False  # the last (or only) move of the budget: its end is the end of the push
        return carried(step, result.mm)  # no distance, or it slipped: not pushed again

    def _push_once(
        self,
        h: Hooked,
        speed: float,
        budget: float,
        fast: float | None = None,
        wait: LoadWait | None = None,
    ) -> PushResult:
        """Run one move of push_to_head at speed and return its PushResult.

        The move is at most the longest move of the unit. ``fast`` (default speed) is the speed it
        changes to when a slow stretch ends (see _speed_up). ``wait`` is the wait for the unit's
        load that _await_search began (see _start_push).
        """
        move, failed = self._start_push(h, speed, budget, wait)
        if failed is not None:
            return failed
        try:
            # No error, so the move started.
            return self._poll_push(h, cast("FeedMove", move), speed, budget, fast or speed)
        except BaseException:
            # A push toward a head always stops. Whatever broke the watch, the feed must not run
            # on to its budget behind a failure.
            try:
                h.api.stop(h.lane)
            except Exception:
                logging.exception(
                    "ace2k_u1: lane %s stop after a failed push", self.adapter.lane_text(h)
                )
            raise

    def push_lane(self, h: Hooked, load: bool = False) -> str | None:
        """Push the filament of h toward its head, as the load of the U1 does.

        On a shared head it is push_in of the probe, so a push that is blocked before the sensor
        looks for the lane in the way. On a head with one lane it is push_to_head; for a ``load``
        (not a preload, whose blocked push waits where it stopped) a push that ends blocked is
        pushed again under the rules of a shared head (HeadProbe.push_alone). A probe that fails is
        said, and it gives the general feed error of the U1.
        """
        slot = self.adapter.slot_of(h)
        speed = self.adapter.feed_speed_mm_s
        try:
            if slot is not None and slot.shared and self.adapter.probe is not None:
                return self.adapter.probe.push_in(slot, h, speed=speed)
            if load and self.adapter.insister is not None:
                return self.adapter.insister.push_alone(h, speed=speed)
            return self.push_to_head(h)
        except ace2k_u1_probe.ProbeError as err:
            # A push that ended blocked before the probe failed is the failure. It is not a block
            # that the filament waits at. The caller reports it (a BLOCKED preload is quiet).
            h.push_end = h.push_last_end = None
            self.adapter.say(f"ace2k_u1: {err}")
            return self.adapter.feed_error("FEED_ERR")

    def _poll_push(
        self, h: Hooked, move: FeedMove, speed: float, budget: float, fast: float
    ) -> PushResult:
        """Watch a started push. Return a PushResult, with error None at the sensor of the head.

        A slow stretch (see _cut_search) changes to ``fast`` when it ends.
        """
        # A backstop: the unit ends a move that passes its own deadline (see unit_move_wait_s).
        deadline = self.adapter.reactor.monotonic() + unit_move_wait_s(h, budget, speed)
        while True:
            now = self.adapter.reactor.pause(self.adapter.reactor.monotonic() + PUSH_POLL_S)
            if self.adapter.head_set(h):
                h.api.stop(h.lane)
                return PushResult(None, True)
            self._speed_up(h, fast)
            event = move.result()
            if event is not None:
                logging.warning(
                    "ace2k_u1: lane %s push ended %s before the head sensor",
                    self.adapter.lane_text(h),
                    event["kind"],
                )
                return self._event_result(event, moved_mm(event))
            if now >= deadline:
                logging.warning("ace2k_u1: lane %s push timed out", self.adapter.lane_text(h))
                h.api.stop(h.lane)
                return PushResult(self.adapter.feed_error("FEED_ERR_TIMEOUT"), True)

    def _preload_push(self, h: Hooked) -> str | PreloadEnd | None:
        """Run the checks and the push of the preload.

        Return None, BLOCKED, DECLINED or the error name of the U1.
        """
        ff, ch, m = h.ff, h.ch, self.adapter.module
        ff._set_channel_state(ch, m.FEED_STA_PRELOAD_PREPARE, True)
        if not self.adapter.present(h):
            return m.FEED_ERR_NO_FILAMENT
        if self.adapter.head_set(h):
            return m.FEED_ERR_RESIDUAL_FILAMENT
        shared = self.adapter.follow.shared_with(h)
        if shared:
            # The automatic preload at the insertion, into an empty head that several lanes feed.
            # This adapter does not pick which filament the head gets, and ace2k parks the filament
            # by itself. So nothing is pushed, and the preload of the U1 ends as a normal one. Only
            # an explicit load is refused (see push_to_head).
            logging.info(
                "ace2k_u1: lane %s: e%d is shared by lanes %s: the preload pushes nothing into it "
                "with its sensor empty",
                self.adapter.lane_text(h),
                h.head,
                shared,
            )
            return DECLINED
        # A push that starts with a clear head brings the lane's own filament, as a load does.
        self.adapter.grip_watch.clear_waits_behind(h, "a preload from a clear head")
        ff._set_channel_state(ch, m.FEED_STA_PRELOAD_FEEDING)
        error = self.push_lane(h)
        if error is not None and h.push_end == "blocked":
            return BLOCKED
        if error == m.FEED_ERR_DISTANCE:  # a whole budget without the sensor (error 14)
            return m.FEED_ERR_TIMEOUT
        return error

    def _say_blocked(self, h: Hooked) -> None:
        """Say the console line for a preload push of h that met what cannot move."""
        # The unit cannot tell what holds the filament (it stops at its encoder), so the line names
        # both places. In a print the replenish load of the U1 pushes it once the piece in the head
        # has run out. Outside a print nothing retries it.
        n = self.adapter.lane_text(h)
        cause = f"ace2k_u1: lane {n}: the filament does not move — something in the tube"
        if self.adapter.in_print():
            self.adapter.say(
                f"{cause} ahead or at the spool; it waits and goes to the head when the "
                "head's piece has run out"
            )
        else:
            self.adapter.say(
                f"{cause} ahead or at the spool; clear it, then pull the filament out and "
                "push it in again"
            )

    def preload_declined(self, slot: Slot) -> None:
        """Handle the PRELOAD of the U1 on a head with several lanes and none in it.

        Nothing is pushed, and the preload of the U1 ends as a normal one (as a declined preload
        on a shared head). It waits at most CHANNEL_WAIT_S for the channel lock of the U1. If the
        lock is still held, the channel is left as it is.
        """
        h = slot.lanes[0]
        ff, ch, m = h.ff, h.ch, self.adapter.module
        if not (ff.config["auto_mode"][ch] and ff.module_exist[ch]):
            return
        logging.info(
            "ace2k_u1: head %d has no lane in it: the preload pushes nothing", slot.head + 1
        )
        with self.adapter.channel_lock(ff, ch, CHANNEL_WAIT_S) as held:
            if not held:
                logging.warning(
                    "ace2k_u1: head %d: the U1's lock still held after %gs: the declined preload "
                    "leaves the channel as it is",
                    slot.head + 1,
                    CHANNEL_WAIT_S,
                )
                return
            ff._set_channel_state(ch, m.FEED_STA_PRELOAD_FINISH)

    def preload(self, h: Hooked) -> None:
        """Run the PRELOAD of the U1 on a hooked channel.

        It uses the same lock, states and reporting, but the push is the push of the unit. A push
        that met what cannot move (blocked, something in the tube ahead) is no failure. The channel
        goes back to wait_insert and the filament waits. When the filament reaches the sensor of
        the head, the lane is armed, outside the lock of the U1.
        """
        ff, ch = h.ff, h.ch
        if not (ff.config["auto_mode"][ch] and ff.module_exist[ch]):
            return
        at_head = self._preload_locked(h)
        if at_head and self.adapter.follow.default_on(h):
            # The filament is at the sensor of the head, so the lane follows. This is what ace2k
            # does at an insertion on a printer without the adapter. The stop of the push may still
            # be winding down, so a busy refusal is retried, as after the extrude retry.
            self.adapter.follow.arm(h, busy_wait_s=NUDGE_BUSY_S)
        if at_head:
            h.parked_at_sensor = True  # the tip waits at the sensor, short of the head's gear

    def _preload_locked(self, h: Hooked) -> bool:
        """Run the preload under the lock of the U1.

        Return True when it brought the filament to the head. It waits at most CHANNEL_WAIT_S
        for the lock. If the lock is still held, the preload is given up with a console line.
        Nothing is pushed and the channel is left as it is.
        """
        ff, ch, m = h.ff, h.ch, self.adapter.module
        with self.adapter.channel_lock(ff, ch, CHANNEL_WAIT_S) as held:
            if not held:
                self.adapter.say(
                    f"ace2k_u1: lane {self.adapter.lane_text(h)}: the U1 kept its channel busy for "
                    f"{CHANNEL_WAIT_S:g} s; the preload given up"
                )
                return False
            ff.channel_error[ch] = m.FEED_OK
            ff.exception_code[ch] = 10
            ff.channel_error_state[ch] = m.FEED_STA_NONE
            try:
                error = self._preload_push(h)
            except Exception:  # as the U1 does, anything unexpected is a general preload failure
                logging.exception("ace2k_u1: lane %s preload failed", self.adapter.lane_text(h))
                error = m.FEED_ERR
            if error is None:
                ff._set_channel_state(ch, m.FEED_STA_PRELOAD_FINISH)
                return True
            if error is DECLINED:
                # The U1's own rest state for a channel whose bay has filament. Nothing is
                # at the head, so there is nothing to follow.
                ff._set_channel_state(ch, m.FEED_STA_PRELOAD_FINISH)
                return False
            if error is BLOCKED:
                ff._set_channel_state(ch, m.FEED_STA_WAIT_INSERT, True)
                self._say_blocked(h)
                return False
            ff.channel_error[ch] = error
            ff.exception_code[ch] = 10 + CODE_OFFSET[error]
            ff._set_channel_state(ch, m.FEED_STA_PRELOAD_FAIL)
            ff.channel_error_state[ch] = ff.channel_state[ch]
            if ff.exception_manager is not None:
                ff.exception_manager.raise_exception_async(
                    id=ff.exception_manager.list.MODULE_ID_FEEDING,
                    index=h.head,
                    code=ff.exception_code[ch],
                    message=f"preload fail: {error}",
                    oneshot=1,
                    level=1,
                )
            return False


def _seat_open(seat: FeedMove | None) -> bool:
    """Return whether the seat's load (when the push started one) has had no final event yet."""
    return seat is not None and seat.result() is None


def _move_raised(h: Hooked, pushed: float, step: float) -> None:
    """Record a move of up to step mm that raised, after a push of pushed mm.

    Its travel is not known: the tip is past pushed by up to the whole move.
    """
    h.push_moves += 1
    h.push_mm, h.push_known, h.push_max_mm = pushed, False, pushed + step
