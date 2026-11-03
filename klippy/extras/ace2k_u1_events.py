"""The feed events of the unit, as they act on the lanes of the adapter.

This module handles the notices of the tail. It also handles what each final event ends: the
motions of this adapter, the grip and the follow. A lane error in the follow pauses a print. The
adapter keeps the lanes and their motions, and FeedEvents runs these steps for it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import FeedEvent, Reactor
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_eject import Ejector
    from .ace2k_u1_grip import GripWatch
    from .ace2k_u1_pause import PauseRunner
    from .ace2k_u1_tag_search import TagSearch
    from .ace2k_u1_tags import TagReader

try:
    from .ace2k_u1_common import ARMED_MODES, MOVE_MODES, SEATED_MM, finite, forward_mm
    from .ace2k_u1_grip import OUT_KINDS, OUT_MODES
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        ARMED_MODES,
        MOVE_MODES,
        SEATED_MM,
        finite,
        forward_mm,
    )
    from ace2k_u1_grip import OUT_KINDS, OUT_MODES  # type: ignore[import-not-found, no-redef]

# These kinds are notices. They never end a move.
NOTICE_KINDS = ("behind", "snag", "tail")
# Final events of a lane in the follow that pause a print, because the lane can no longer feed.
# stopped_sensors: the unit cannot read its insert sensors (its ADC scan stopped), so it stopped
# the lane and refuses every start. The print pauses: no swap or refill may act on that unit.
STALE_SENSORS = "stopped_sensors"
PAUSE_KINDS = ("stuck", "tangled", "timeout", "motor_stalled", STALE_SENSORS)


def moved_any(event: FeedEvent | None) -> float:
    """Return the farthest travel (mm) that a final event reports, of its filament or its motor.

    A lane whose encoder read nothing while its motor ran has still moved.
    """
    far = 0.0
    for key in ("filament_mm", "motor_mm"):
        value = (event or {}).get(key)
        if finite(value):
            far = max(far, abs(value))
    return far


class EventsAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the feed events use it."""

    @property
    def reactor(self) -> Reactor: ...
    @property
    def grip_watch(self) -> GripWatch: ...
    @property
    def ejector(self) -> Ejector: ...
    @property
    def tag_reader(self) -> TagReader: ...
    @property
    def tag_search(self) -> TagSearch: ...
    @property
    def pause_runner(self) -> PauseRunner: ...
    def hooked_lane(self, unit: object, lane: int) -> Hooked | None: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    def lane_text(self, h: Hooked) -> str: ...
    def lane_args(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...
    def moved_out(self, h: Hooked) -> None: ...
    def present_or_not(self, h: Hooked) -> bool: ...
    def shared(self, h: Hooked) -> bool: ...
    def print_state(self) -> str | None: ...
    def u1_pause_reason(self, line: str) -> None: ...


class FeedEvents:
    """The feed events of the lanes of one adapter."""

    def __init__(self, adapter: EventsAdapter) -> None:
        self.adapter = adapter

    def on_feed_event(self, lane: int, event: FeedEvent) -> None:
        """Handle the "ace2k:feed_event" event of a lane. It never raises.

        An event belongs to a head when its unit and its lane are the head's. The "unit" of the
        event is None for the unnamed [ace2k]. Other events are not this adapter's.

        What the handler does:
        - A follow (or an assist) that the unit ended is no longer counted as armed. A later pull
          state, load_finish or the standing re-arm arms it again.
        - A stop that this adapter did not cause (a stop by hand) ends the standing of the follow.
        - A lane error in the follow pauses a print.
        - The tail notice is told.
        - The end of the tail (tail_out) is told. It ends the standing of the follow.
        - An eject that ended in error is a console notice. Any other end of an eject is silent.
        """
        try:
            self._on_feed_event(lane, event)
        except Exception:
            unit = event.get("unit") if isinstance(event, dict) else None
            where = f"{unit}:{lane + 1}" if unit is not None else f"{lane + 1}"
            logging.exception("ace2k_u1: lane %s feed event", where)

    def _on_feed_event(self, lane: int, event: FeedEvent) -> None:
        kind = event.get("kind")
        h = self.adapter.hooked_lane(event.get("unit"), lane)
        if h is None:
            return  # not a hooked head's lane
        if kind == "tail":
            self.adapter.grip_watch.on_tail(h)
        if kind in NOTICE_KINDS:
            return
        slot = self.adapter.slot_of(h)
        if slot is not None and event.get("mode") in MOVE_MODES:
            slot.loaded = None  # a lane of the head moved: what is in the head may have changed
        if kind in OUT_KINDS or event.get("mode") in OUT_MODES:
            self.adapter.moved_out(h)
        self._on_final(h, kind, event)
        self.adapter.ejector.on_final(h, event)

    def _on_final(self, h: Hooked, kind: str | None, event: FeedEvent) -> None:
        """Handle a final event of the lane of h: it ends motions of this adapter and the follow."""
        seq = event.get("seq")
        search = self._search_load(h, event)
        if moved_any(event) >= 1.0 and not search:
            h.at_grip = False  # the filament moved since its gripped load, even if the end is late
        _note_seat(h, event, seq)
        stale, own = self._final_seq(h, kind, seq)
        if stale:
            # An earlier motion of this adapter ended late. Nothing current ends with it.
            h.old_seqs.remove(cast(int, seq))  # stale, so it is one of old_seqs
            return
        if seq is not None and seq == h.motion_seq:
            h.motion_open = False
        if h.push_end is not None and seq != h.push_end_seq:
            h.push_end = None  # another motion ended, so the lane has moved since that push
        h.ended_at = self.adapter.reactor.monotonic()
        self._final_grip(h, kind, event, search)
        if h.port_held:
            h.tail_ended = True  # the end of the tail, whatever the next report or the insert says
        self._final_follow(h, kind, event, own)
        if kind in PAUSE_KINDS and event.get("mode") == "following":
            self._on_lane_error(h, kind)
        if kind == "tail_out":
            self.adapter.grip_watch.on_tail_out(h)

    def _final_seq(self, h: Hooked, kind: str | None, seq: int | None) -> tuple[bool, bool]:
        """Compare a final event (its kind and seq) with the motions of this adapter.

        Return (stale, own). ``stale`` is True when the event ends an earlier motion, late.
        ``own`` is True when the event answers a stop by this adapter. An expected stop is no
        longer expected once the event answers it, or shows that this adapter should not have
        expected it.
        """
        # The final event of the motion that this adapter stopped answers that stop. (halt expects
        # only a motion whose seq it knows.) The late end of an older motion does not.
        stale = seq is not None and seq in h.old_seqs
        answers = h.expect_stopped and seq == h.expect_seq
        own = answers and kind == "stopped"
        if answers or (h.expect_stopped and not stale):
            h.expect_stopped = False
            h.expect_seq = None
        return stale, own

    def _final_grip(self, h: Hooked, kind: str | None, event: FeedEvent, search: bool) -> None:
        """Handle what the final event ends of the grip of the lane.

        The end of a gripped load marks the filament as being at the gear, and asks for its tag.
        No grip is left on the unit. ``search`` is True when the event ends a read with motion.
        """
        seq = event.get("seq")
        gripped = (
            kind in ("loaded", "blocked")  # blocked means the grip met the old end at the gear
            and seq == 0
            and h.grip_live
            and h.grip_down
            and not h.grip_wait  # the wait starts no load of its own
        )
        if gripped and h.grip_park:
            # The load of the park stops at the parking point. The roll back of the probe never
            # takes the filament out of the unit from there. If the load ended short (blocked,
            # or a pull cut early), the probe skips the lane, as it does for a grip.
            h.at_grip = self.adapter.grip_watch.park_short(event)
        elif gripped:
            # Only a load that met nothing counts. A blocked load has the old end ahead of it, and
            # that end may reach a head.
            h.at_grip = kind == "loaded"
        elif seq == 0 and event.get("mode") == "loading" and self.adapter.shared(h) and not search:
            # The unit's own load of a shared head's lane, which has no grip. It goes to the
            # parking point. If it ended short (blocked, or stopped early), the probe skips the
            # lane, as it does for a short park: its roll back would take the filament out of the
            # unit. The load of a read with motion goes out and comes back to where it started,
            # so it leaves the lane as the load before it did.
            h.at_grip = self.adapter.grip_watch.park_short(event)
        # Not when the pull of the park read the tag on its way. There is no second read at the
        # parking point.
        if (
            gripped
            and not (h.grip_park and self.adapter.grip_watch.tag_read_now(h))
            and self.adapter.present_or_not(h)
        ):
            # This is the unit's own load, started by an insert edge with the grip set. It is
            # the gripped load, and the filament is at the gear. Its tag is watched: the grip's
            # own move may have read it, and if not, the console says it is read when it moves.
            # This is judged here and not on the next tick of the bay watch, because a short grip
            # may beat that tick.
            self.adapter.tag_reader.queue(h)
        if seq == 0 and event.get("mode") == "loading":
            # The unit's own load, or the load of a read with motion. Unless it was a grip or the
            # park of a refill, it stops at the parking point by itself, where a tag still unread
            # gets one read with motion.
            self.adapter.tag_search.on_load_end(h, kind, not gripped, search)
        h.grip_live = False  # every motion was a start or the grip's own load: none is left
        h.grip_down = False

    def _search_load(self, h: Hooked, event: FeedEvent) -> bool:
        """True when the final event ends a read with motion of the lane.

        ace2k says so in the event (tag_search). A read that this adapter started counts too,
        for an ace2k whose event does not say it.
        """
        return bool(event.get("tag_search")) or self.adapter.tag_search.in_flight(h)

    def _final_follow(self, h: Hooked, kind: str | None, event: FeedEvent, own: bool) -> None:
        """Handle what the final event ends of the follow of the lane.

        The follow is no longer armed, and a stop by hand ends its standing. ``own`` is True when
        the stop was this adapter's.
        """
        seq = event.get("seq")
        if event.get("mode") in ARMED_MODES or (seq is not None and seq == h.assist_seq):
            h.armed = False
            h.assist_seq = None
            # A stop that a host module sent (this adapter, or ace2k itself) is never a stop by
            # hand. This holds whether or not its sequence was known here (see stop_by).
            by_host = event.get("stop_by") == "host"
            if kind == "stopped" and not own and not by_host and h.follow_wanted:
                h.follow_wanted = False
                self.adapter.say(
                    f"ace2k_u1: lane {self.adapter.lane_text(h)} follow stopped by hand; left off"
                )

    def _on_lane_error(self, h: Hooked, kind: str) -> None:
        """Handle a lane error in the follow.

        During a print this queues the tangle pause of the U1 for its head, run from the pause
        timer. Outside a print it prints a console line.
        """
        n = self.adapter.lane_text(h)
        if (
            self.adapter.print_state() == "printing"
            and self.adapter.pause_runner.pause_timer is not None
        ):
            line = f"ace2k_u1: lane {n} {kind} in the follow — pausing the print (e{h.head})"
            self.adapter.u1_pause_reason(line)
            self.adapter.say(line)
            self.adapter.pause_runner.queue_pause("tangle", h, kind)
        elif kind == STALE_SENSORS:  # the lane is idle, not in error
            self.adapter.say(
                f"ace2k_u1: lane {n} {kind} in the follow — the unit's insert sensors are stale;"
                " nothing to clear"
            )
        else:
            self.adapter.say(
                f"ace2k_u1: lane {n} {kind} in the follow — ACEK_CLEAR {self.adapter.lane_args(h)}"
            )


def _note_seat(h: Hooked, event: FeedEvent, seq: int | None) -> None:
    """Mark the filament of h that waited as seated in the gear when the final event moved it.

    Only once it waited: the end of the old piece's tail (tail_out) comes before the wait. A late
    end counts only if it is the seat's: an earlier motion's never moved this one.
    """
    late = seq is not None and seq in h.old_seqs
    if h.waited and forward_mm(event) >= SEATED_MM and (not late or seq == h.seat_seq):
        h.seated = True  # the filament that waited is in the gear
