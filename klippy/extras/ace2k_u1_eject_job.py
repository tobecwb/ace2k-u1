"""The record of one ACEK_EJECT (EjectJob): its steps, in order, and what it carries from one step
to the next. The eject (ace2k_u1_eject) runs it from its reactor timer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ace2k_klipper_types import FeedEvent
    from .ace2k_u1_common import Hooked, TautWatch
    from .ace2k_u1_printer_types import FeedMove

# The steps of an EjectJob, in order. "net" is the start of the safety net, which goes on at back.
STEP_BEGIN = "begin"
STEP_PROBE = "probe"
STEP_PROBE_WAIT = "probe_wait"
STEP_SETTLE = "settle"
STEP_BACK = "back"
STEP_BACK_WAIT = "back_wait"
STEP_BACK_SETTLE = "back_settle"
STEP_U1 = "u1"
STEP_NET = "net"
STEP_DONE = "done"


class EjectJob:
    """One ACEK_EJECT on a lane, run step by step from its reactor timer.

    The steps are: begin (the follow goes off and the sensor of the head is read), probe,
    probe_wait, settle (the plunger settles after the probe), back (the feed goes back),
    back_wait, back_settle, u1 (the unload of the head by the U1) and done. The safety net starts
    at net and then goes on at back. ``decided`` is True once the decision of the eject is taken
    (WAIT=1 returns then). ``failed`` says why the eject ended short, or is None.
    """

    def __init__(self, h: Hooked) -> None:
        self.h = h
        self.step = STEP_BEGIN
        self.timer: object | None = None
        # The call of the timer is in progress. run_script may let it re-enter.
        self.running = False
        self.decided = False
        # Vulture counts the nested failed(...) call of the probe as a use of this name today. The
        # V101 marks keep the attribute counted as used once that probe helper is gone.
        self.failed: str | None = None  # noqa: V101
        self.move: FeedMove | None = None
        self.give_up = 0.0  # a busy start is retried until this time
        self.deadline = 0.0  # a started move with no final event by this time is stopped
        self.back_mm = 0.0
        # The final event of the probe, kept across the settle.
        self.probe_event: FeedEvent | None = None
        self.settle_at = 0.0  # when the plunger is read after the probe
        # At begin: the follow was wanted or armed. A restore gives it back.
        self.had_follow = False
        # Why the probe is stopped and held ("plunger taut", "link lost"), or None.
        self.stop_why: str | None = None
        self.stop_sent = False  # that stop reached ace2k
        self.stop_tries = 0  # the attempts of the stop that raised
        # The host watched the probe. That needs a proven link with a report count at its start,
        # and a unit that cannot stop on its plunger itself.
        self.watch = False
        # The taut watch of the watched probe (TautWatch). It is made from the sensors_reports of
        # ace2k at the start of the probe and from job.fresh_at. It is None when the probe is not
        # watched.
        self.taut: TautWatch | None = None
        # A plunger reading before this time may predate the motion that it judges.
        self.fresh_at = 0.0
        # A plunger status could not be read. This is logged once.
        self.unread_said = False
        # The safety net: the filament that left the unit, fed back.
        self.net_back: float | None = None
        # The eject runs for the Unload of the U1's screen: a held filament is only fed back.
        self.for_screen = False
        # How it ended: "cold" (the unit's unload started), "head" (the U1's unload of the head
        # ran, and its end starts the unit's unload), "u1" (held, for the screen's unload of the
        # U1), or None (failed, or not decided yet).
        self.outcome: str | None = None
