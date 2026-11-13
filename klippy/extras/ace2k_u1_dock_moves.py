"""Runs the parked moves of a docked head, with the U1's tangle check of that head held off.

Every parked move comes here: the background swap's tip and its grip with the purge
(ace2k_u1_background), and the bench's ACEK_BG_MOVE. Each run goes in this order:

1. the wait for the extruder's own steps (a parked move is refused until the T that docked the
   head has made them), with the tangle check still on;
2. the U1's tangle check of the head held off (U1Printer.tangle_check_off). A check that cannot
   be turned off raises TangleOffError here, before anything is queued: the parked moves never
   run unguarded, and the background swap stops at that step;
3. every move queued back to back (ace2k_u1_parked.ParkedMover); queued() is called once the
   first is, so a caller records a queued move only when there is one;
4. the wait for the end of the last one on the MCU clock. It runs also when a move fails
   part-way, since the moves already queued cannot be taken back. Only then is the check on
   again: it reads the head as before the moves once they have all run. The wait is bounded by
   the end of the moves queued, plus WAIT_MARGIN_S: the check is never held off longer.

Why the check is held off rather than the extruder's past position kept right inside the parked
move (by compensating the stepper's position offset while the move runs): that offset is the
U1's own position bookkeeping of the extruder, which its print moves, its own tip and its load
use too; changing it from outside risks those, while holding the check off uses the U1's own
switch, the way its load and unload do.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .ace2k_u1_parked import Note, ParkedMover, TipStep
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_printer_types import MovingExtruder, Reactor

try:
    from .ace2k_u1_parked import DEFAULT_ACCEL, OwnStepsError
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_parked import (  # type: ignore[import-not-found, no-redef]
        DEFAULT_ACCEL,
        OwnStepsError,
    )

MOVE_POLL_S = 0.5  # the longest pause in the wait for the end of a parked move
# The longest wait for the steps of an extruder's own moves before a parked move of it.
OWN_STEPS_WAIT_S = 10.0
# The wait for the end of the parked moves gives up this long after the end they were queued for.
WAIT_MARGIN_S = 5.0


class DockMoves:
    """The parked moves of the docked heads, in the order of this module's description.

    mover is the ParkedMover; a test may set its own.
    """

    def __init__(self, reactor: Reactor, u1: U1Printer, mover: ParkedMover) -> None:
        self._reactor = reactor
        self._u1 = u1
        self.mover = mover

    def run(
        self,
        head: int,
        ext: MovingExtruder,
        steps: Sequence[TipStep],
        note: Note | None,
        queued: Callable[[], object] | None = None,
    ) -> tuple[float, float]:
        """Queues the steps as back-to-back parked moves of ext, the head's extruder, and waits
        for their end. Returns the print time at which the first starts and the last ends.

        Each move gets the extruder's max_e_accel, as the U1's own tip moves do, else the
        mover's DEFAULT_ACCEL. A move is noted through note for the feed-forward, when given.
        queued is called once, right after the first step is queued.
        """
        accel = self._accel(ext)
        self._own_steps_done(head, ext)
        first, last = 0.0, 0.0
        with self._u1.tangle_check_off(head):
            try:
                for i, step in enumerate(steps):
                    if step.kind == "dwell":
                        last = self.mover.dwell(ext, step.value / 1000.0)
                        start = last - step.value / 1000.0
                    else:
                        speed = step.feed / 60.0
                        start, last = self.mover.move(ext, step.value, speed, accel, note)
                    if i == 0:
                        first = start
                        if queued is not None:
                            queued()
            except Exception:
                self._wait_after_error(head, ext)
                raise
            self.wait(head, ext)
        return first, last

    @staticmethod
    def _accel(ext: MovingExtruder) -> float:
        value = getattr(ext, "max_e_accel", None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return DEFAULT_ACCEL

    def _wait_after_error(self, head: int, ext: MovingExtruder) -> None:
        """The wait for the moves already queued when a step failed. A wait that fails too is
        logged: the step's error is the one raised."""
        try:
            self.wait(head, ext)
        except Exception:
            logging.exception(
                "ace2k_u1: head %d: waiting for its parked moves after a failed one", head + 1
            )

    def _own_steps_done(self, head: int, ext: MovingExtruder) -> None:
        """Waits while the extruder's own moves still have steps to be made (the T that docked
        it has just ended), at most OWN_STEPS_WAIT_S. Raises OwnStepsError after that."""
        reactor = self._reactor
        give_up = reactor.monotonic() + OWN_STEPS_WAIT_S
        while self.mover.own_steps_left(ext):
            if reactor.monotonic() >= give_up:
                raise OwnStepsError(
                    f"head {head + 1}'s extruder has steps of its own still to make"
                )
            reactor.pause(reactor.monotonic() + MOVE_POLL_S)

    def wait(self, head: int, ext: MovingExtruder) -> None:
        """Waits until the last parked move of ext has ended on the MCU clock.

        A stop request does not cut it: a queued move cannot be taken back. It gives up after
        the time left at its start plus WAIT_MARGIN_S (an MCU clock that no longer advances),
        and logs it.
        """
        reactor = self._reactor
        left = self.mover.remaining_s(ext)
        give_up = reactor.monotonic() + left + WAIT_MARGIN_S
        while left > 0.0:
            if reactor.monotonic() >= give_up:
                logging.error(
                    "ace2k_u1: head %d: its parked moves have not ended %.1f s after their end",
                    head + 1,
                    WAIT_MARGIN_S,
                )
                return
            reactor.pause(reactor.monotonic() + min(left, MOVE_POLL_S))
            left = self.mover.remaining_s(ext)
