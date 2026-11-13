"""The grip, the tail and the bay watch of the lanes of the adapter.

This module handles these things:
- The grip (or the park, or the wait) that the unit of a lane holds while the sensor of its head
  sees a piece that the lane does not own, or while a lone head's refill is pending.
- The notices of the tail and its end.
- A filament that waits behind a piece in its head.
- The samples of the wheel stand-in.
- What the bay watch tells the U1 about each bay.

The adapter keeps the lanes and their motions, and GripWatch runs these steps for it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import FeedEvent
    from .ace2k_u1_common import ExtruderPosition, Slot
    from .ace2k_u1_eject import Ejector
    from .ace2k_u1_follow import Follow
    from .ace2k_u1_hooks import EncoderWheel
    from .ace2k_u1_pause import PauseRunner
    from .ace2k_u1_printer_types import Coordinator, LaneView, Unit, UnitFeed
    from .ace2k_u1_probe import HeadProbe
    from .ace2k_u1_tags import TagReader

try:
    from .ace2k_u1_common import (
        ARMED_MODES,
        IDLE_MODES,
        Hooked,
        moved_mm,
        spool_left,
        tag_read_live,
        tag_state,
        unit_prefix,
        watch_tag_seen,
    )
    from .ace2k_u1_hooks import BUSY_MODES
    from .ace2k_u1_tags import tag_identity
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        ARMED_MODES,
        IDLE_MODES,
        Hooked,
        moved_mm,
        spool_left,
        tag_read_live,
        tag_state,
        unit_prefix,
        watch_tag_seen,
    )
    from ace2k_u1_hooks import BUSY_MODES  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_tags import tag_identity  # type: ignore[import-not-found, no-redef]

# The probe skips the load of a park that ended with less filament in than the roll back of the
# probe plus this margin, as it skips a grip (see GripWatch.park_short).
PARK_SHORT_MARGIN_MM = 50.0
# The default of probe_mm, used when no probe is built.
PROBE_MM_DEFAULT = 100.0
# The bay must read empty this long, the lane at rest, before the spool record of the lane goes.
BAY_EMPTY_S = 2.0
# The modes of a move that takes the filament of the lane back toward its bay.
OUT_MODES = ("rolling_back", "unloading", "assisting_back")
# The sensor of the head must read clear this long before a waiting lane is released.
HEAD_CLEAR_S = 1.0
# After its bay empties, a lane must read out of its head (not known loaded, its channel read) for
# this long (s) before the head's setting stops belonging to its spool (GripWatch._setting_owner).
# A head sensor that blips clear for a tick, or a channel that cannot be read, never ends it.
SETTING_LEAVE_S = 2.0
# The line "a new filament can go in" waits until this much filament was pulled since the bay
# read empty. The drive gear of the unit sits about 50 mm past the inlet, and the old end may
# still be in it when the encoder loses it. 100 mm leaves a margin.
OUT_NOTICE_MM = 100.0
OUT_LINE = "ace2k_u1: lane {n}: the end is out of the unit — a new filament can go in"


class GripAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the grip watch uses it."""

    owned_states: set[str] | None  # from build_owned_states(), built at the hook

    def get_unload_states(self) -> set[str]: ...

    @property
    def units(self) -> Coordinator: ...
    @property
    def probe(self) -> HeadProbe | None: ...
    @property
    def pause_runner(self) -> PauseRunner: ...
    @property
    def tag_reader(self) -> TagReader: ...
    @property
    def ejector(self) -> Ejector: ...
    @property
    def follow(self) -> Follow: ...
    @property
    def swapping(self) -> set[int]: ...  # the heads that a swap is on
    @property
    def settle_s(self) -> float: ...
    @property
    def runout_source(self) -> str: ...
    @property
    def extruder_position(self) -> ExtruderPosition | None: ...
    @property
    def on_bay_empty(self) -> Callable[[Hooked], object] | None: ...
    @property
    def on_shared_runout(self) -> Callable[[Hooked], object] | None: ...
    @property
    def swap_pending(self) -> Callable[[], bool]: ...
    @property
    def refill_pending(self) -> Callable[[Hooked], bool]: ...
    def lane_text(self, h: Hooked) -> str: ...
    def lane_args(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...
    def stop(self, h: Hooked, why: str, lane: LaneView | None = None) -> None: ...
    def find(self, lane: int, unit: Unit | None = None) -> Hooked | None: ...
    def head_holds(self, h: Hooked) -> bool: ...
    def in_print(self) -> bool: ...
    def print_state(self) -> str | None: ...
    def present(self, h: Hooked) -> bool: ...
    def cut_possible(self, h: Hooked) -> bool: ...
    def parked(self, h: Hooked) -> bool: ...
    def shared(self, h: Hooked) -> bool: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    def lane_or_none(self, h: Hooked) -> LaneView | None: ...
    def motion(self, h: Hooked, lane: LaneView | None = None) -> str | None: ...
    def running(self, h: Hooked, lane: LaneView | None = None) -> bool: ...
    def build_owned_states(self) -> set[str]: ...


class GripWatch:
    """The grip, the tail and the bay watch of the lanes of one adapter."""

    def __init__(self, adapter: GripAdapter, grip_mm: float | None = None) -> None:
        self.adapter = adapter
        # grip_mm as configured. None means the default of each unit (see _grip), which ace2k
        # reads from the dictionary at connect. It is None too for an image without the grip,
        # which is not hooked.
        self.grip_cfg = grip_mm
        # The label of each unit whose image has no park. This is said once.
        self.park_missing_said: set[str] = set()
        # The label of each unit whose image has no wait. This is said once.
        self.wait_missing_said: set[str] = set()

    def head_state_cleared(self, h: Hooked) -> None:
        """Drop what the sensor of the head set on a lane of a shared head. It never raises.

        That is waiting behind a piece (with the follow of ace2k held for it), a rise held, and the
        grip. The sensor spoke for the lane in the head then, which this lane is no longer, or not
        yet.
        """
        self.clear_waits_behind(h)
        h.rise_held = False
        h.clear_since = None
        if h.grip_wanted or h.grip_live:
            try:
                h.api.set_lane_grip(h.lane, 0)
            except Exception:
                logging.exception("ace2k_u1: lane %s grip", self.adapter.lane_text(h))
        h.grip_wanted = h.grip_live = h.grip_down = h.grip_wait = False
        h.clear_wait()
        h.grip_refused = None

    def _grip(self, h: Hooked) -> float:
        """Return the grip of the lane of h: grip_mm as configured, else the default of its unit."""
        return cast(float, self._unit_grip(h.api))  # a hooked unit has one (see check_grip)

    def _unit_grip(self, api: UnitFeed) -> float | None:
        return self.grip_cfg if self.grip_cfg is not None else getattr(api, "grip_mm", None)

    def check_grip(self, unit: Unit) -> list[str]:
        """Check that grip_mm is within the bounds of the unit.

        ace2k reads the bounds from the dictionary at connect.
        """
        api = unit.feed
        low = getattr(api, "grip_min_mm", None)
        high = getattr(api, "grip_max_mm", None)
        if low is None or high is None:
            return []  # no grip on the image, and the hook says so first
        grip = self._unit_grip(api)
        where = unit_prefix(self.adapter.units, unit)
        if grip is None:
            return [f"{where}the unit's firmware gives no default grip"]
        if not low <= grip <= high:
            return [f"{where}grip_mm {grip:g} is outside the unit's {low:g}..{high:g} mm"]
        return []

    def in_tail(self, h: Hooked, lane: LaneView | None = None) -> bool:
        """Return _tail for the current status of the lane. It never raises.

        ``lane`` is the status of this tick, if it was already read.
        """
        if lane is None:
            lane = self.adapter.lane_or_none(h)
            if lane is None:
                return False
        return self._tail(h, lane)

    def _tail(self, h: Hooked, lane: LaneView) -> bool:
        """True when the lane is in its tail.

        That is the case when the unit said so. It is also the case when the filament is out of its
        bay while the current motion of the lane is the follow. The unit always enters the tail
        then, and its notice may still be on the way. An unknown insert (None, no report of the
        sensors) counts as down, and the lane is left running.
        """
        if lane.get("tail") is True:
            return True
        return self.adapter.motion(h, lane) == "following" and lane.get("insert") is not True

    def on_tail(self, h: Hooked) -> None:
        """Handle the tail notice of the unit: the filament left the bay while following.

        The filament feeds on as the tail. During a print, the end is taken here instead in a head
        that several lanes feed, and in any head with runout_source set to unit (see shared_end).
        Otherwise a console line is said.
        """
        if self.shared_end(h):
            return
        n = self.adapter.lane_text(h)
        self.adapter.say(f"ace2k_u1: lane {n} ran out at the bay — its tail feeds on to e{h.head}")

    def shared_end(self, h: Hooked) -> bool:
        """Take the end of a spool at the bay, for the refill. Return True if it was taken.

        This applies to a head that several lanes feed, and to a head of one lane with
        runout_source set to unit (see _refills).

        This applies to the lane that ran out at its bay during a print. The lane is stopped at
        once, before its end leaves the motor of the unit, because the unit can pull the rest back
        only while the end is in it. Its follow is wanted no more, and the lane is held. Then the
        plain pause, the unload macro of the swap, the free check and the roll back of the rest run
        from the pause timer.

        The notice is not taken for a parked lane, a head of one lane with runout_source set to
        head, or when no print runs. These keep the line of the console. A head in the middle of a
        swap runs its lanes itself, so the notice is left. The bay watch takes the end once the swap
        is over (see shared_end_missed).
        """
        if not self._refills(h):
            return False
        if h.head in self.adapter.swapping:
            return False
        if (
            self.adapter.print_state() != "printing"
            or self.adapter.pause_runner.pause_timer is None
        ):
            return False
        if h.end_taken:
            return True  # taken already, by the notice or the bay watch: only once
        if self.adapter.on_shared_runout is None:
            return self._lone_runout_pause(h)
        h.end_taken = True
        h.follow_wanted = False
        h.hold = True  # given back once the rest is out, or its roll back failed
        self.adapter.stop(h, "the spool's end")
        self.adapter.pause_runner.queue_pause("shared runout", h, "tail")
        return True

    def _lone_runout_pause(self, h: Hooked) -> bool:
        """Pause a head of one lane through the runout pause of the U1, when it cannot refill.

        That is the case with no refill to run (on_shared_runout is None). The lane is not stopped:
        it feeds its rest on as the tail, and the U1 pauses the print. A head that several lanes
        feed is left alone (False). Its end is taken once.
        """
        if self.adapter.shared(h):
            return False
        h.end_taken = True
        self.adapter.say(
            f"ace2k_u1: lane {self.adapter.lane_text(h)} ran out at the bay — its tail feeds on "
            f"to e{h.head}; pausing the print"
        )
        self.adapter.pause_runner.queue_pause("runout", h, "tail")
        return True

    def end_declined_watch(self, h: Hooked, lane: LaneView) -> None:
        """Give back a shared end whose pauses were declined, with its hold.

        The pauses are declined when the print goes on. The end is given back once the bay has read
        empty and then full again, which means a new spool is in it.
        """
        if h.end_declined is None:
            return
        insert = lane.get("insert")
        if insert is False:
            h.end_declined = "emptied"
        elif insert is True and h.end_declined == "emptied":
            h.end_declined = None
            h.end_taken = False
            h.hold = False

    def shared_end_missed(self, h: Hooked, lane: LaneView) -> bool:
        """Return True when the bay watch must take a shared end that the tail notice missed.

        This is the backup for a tail notice that did not come. It applies to the current lane of a
        head that refills (see _refills), during a print, whose status shows the tail, or whose bay
        reads empty while it follows. No refill or swap may be pending, and the end must not be
        taken yet.
        """
        if not self._refills(h):
            return False  # a head of one lane with the head's sensor, or a lane not in its head
        if (
            h.end_taken
            or self.adapter.print_state() != "printing"
            or h.head in self.adapter.swapping
        ):
            return False  # a live swap runs the head's lanes itself
        try:
            if self.adapter.swap_pending():
                return False
        except Exception:
            logging.exception("ace2k_u1: lane %s swap pending", self.adapter.lane_text(h))
            return False
        if lane.get("tail") is True:
            return True
        return lane.get("insert") is False and self.adapter.motion(h, lane) == "following"

    def _refills(self, h: Hooked) -> bool:
        """True when the end of the spool of h is taken at the bay and refilled.

        That is the current lane of a head that several lanes feed, and the lane of a head of one
        lane with runout_source set to unit. The unit can roll the rest back only while its end is
        in the motor, so the end is taken at the bay and not at the tail_out. A head of one lane
        whose runout the U1 would not pause (its sensor disabled, its pause on runout off, the end
        of the print running) prints its rest as the tail instead, as with runout_source head.
        """
        slot = self.adapter.slot_of(h)
        if slot is None or slot.current is not h:
            return False
        if slot.shared:
            return True
        return (
            self.adapter.runout_source == "unit" and self.adapter.pause_runner.declines(h) is None
        )

    def _refill_of(self, h: Hooked) -> bool:
        """True when a refill of lane h is pending. If that is unknown, log it and return False."""
        try:
            return bool(self.adapter.refill_pending(h))
        except Exception:
            logging.exception("ace2k_u1: lane %s refill pending", self.adapter.lane_text(h))
            return False

    def on_tail_out(self, h: Hooked) -> None:
        """Handle the end of the tail: the end of the filament is out of the unit.

        The gear of the unit is free then.

        - The follow is wanted no more. A filament put in during the tail rests behind the old end,
          not at the gear, so it must not follow.
        - If a filament is in the bay and the unit holds the wait for it, the operator is told at
          once that it waits there until the old piece runs out.
        - Otherwise the line waits until OUT_NOTICE_MM was pulled since the bay emptied (see
          watch_out). The old end may still be in the gear of the unit until then. With the bay
          empty, it says that a new filament can go in. With a filament in the bay, it says to
          pull it out and push it in again until the gear takes it.
        - A filament in the bay is not told to the U1 while the old piece is in the head.
        - Nothing pauses here. With runout_source set to unit, the end of a print's spool is taken
          at the bay (see shared_end), and its lane is stopped before any tail_out.
        """
        n = self.adapter.lane_text(h)
        h.follow_wanted = False
        h.after_tail = True
        if self.adapter.head_holds(h):
            self._set_waits_behind(h, "its tail_out with the head's sensor set")
        try:
            inserted = self.adapter.present(h)
        except Exception:  # the line, and the pause below, are still wanted
            logging.exception("ace2k_u1: lane %s status", n)
            inserted = False
        self.sync_grip(h)  # the old piece is still in the head, so a filament put in now is gripped
        wait = inserted and h.grip_live and h.grip_wait
        if not wait and self._out_pending(h):
            return  # said by watch_out once OUT_NOTICE_MM is counted
        self.adapter.say(self._end_line(h, inserted))

    def _end_line(self, h: Hooked, inserted: bool) -> str:
        """Return the line of the end of the tail, for a bay that holds a filament or not."""
        n = self.adapter.lane_text(h)
        if inserted and h.grip_live and h.grip_wait:
            return (
                f"ace2k_u1: lane {n}: the end is out of the unit — lane {n}'s new filament waits "
                f"in the bay until e{h.head}'s piece runs out; push it in until it stops"
            )
        if inserted:
            return (
                f"ace2k_u1: lane {n}: the end is out of the unit — pull lane {n}'s new filament "
                "back out of the bay and push it in again until the gear takes it"
            )
        return OUT_LINE.format(n=n)

    def _out_pending(self, h: Hooked) -> bool:
        """At a tail_out: True when the line must wait for watch_out.

        The count goes on from the encoder of the lane, which has lost the end, with the extruder
        of the head. The line is due at once when OUT_NOTICE_MM is counted already, or when the
        extruder cannot be read (no reader, or one that fails), so that it never waits for ever.
        """
        h.out_source = "extruder"
        ok, h.out_last = self._extruder_at(h)
        h.out_due = ok and h.out_mm < OUT_NOTICE_MM
        h.out_print = self.adapter.in_print()
        if h.out_due:
            logging.info(
                "ace2k_u1: lane %s: tail_out after %.0f mm; its line waits for %g mm",
                self.adapter.lane_text(h),
                h.out_mm,
                OUT_NOTICE_MM,
            )
        return h.out_due

    def _extruder_at(self, h: Hooked) -> tuple[bool, float | None]:
        """Read the extruder of the head once: (read, position). It never raises.

        ``read`` is False with no reader, or when the reader fails. A failure is logged once until
        a reading succeeds again.
        """
        reader = self.adapter.extruder_position
        if reader is None:
            return False, None
        try:
            position = reader(h.head)
        except Exception:
            if not h.out_unread:
                h.out_unread = True
                logging.exception(
                    "ace2k_u1: lane %s e%d position", self.adapter.lane_text(h), h.head
                )
            return False, None
        h.out_unread = False
        return True, position

    @staticmethod
    def _count_out(h: Hooked, value: float | None) -> None:
        """Add the travel past the furthest reading so far to out_mm.

        A move back counts nothing, and the same stretch forward again (a retraction undone)
        counts only once.
        """
        if value is None:
            return
        if h.out_last is None or value > h.out_last:
            h.out_mm += 0.0 if h.out_last is None else value - h.out_last
            h.out_last = value

    def _out_dropped(self, h: Hooked, lane: LaneView) -> str | None:
        """Return why a waiting line is wanted no more, or None.

        The head no longer holds the piece of this lane: another lane is in the head, the lane
        rolls back or unloads, or the U1 unloads the head. Or the print that was on at the
        tail_out has ended.
        """
        slot = self.adapter.slot_of(h)
        if slot is not None and slot.shared and slot.current is not h:
            return "another lane is in the head"
        if lane.get("mode") in OUT_MODES:
            return "the lane moves back"
        if h.ff.channel_state[h.ch] in self.adapter.get_unload_states():
            return f"e{h.head} unloads"
        if h.out_print and not self.adapter.in_print():
            return "the print ended"
        return None

    def watch_out(self, h: Hooked, lane: LaneView) -> None:
        """Count the filament pulled since the bay read empty, and say the line when it is due.

        Up to the tail_out the encoder of the lane counts. After it, the end has left the encoder,
        and the extruder of the head counts. Once out_mm reaches OUT_NOTICE_MM the line of the end
        is said, for the bay as it reads then. A reader of the extruder that fails says it at once.
        The line is dropped when the head no longer holds the piece of this lane, or the print
        ended (_out_dropped).
        """
        if h.out_source == "encoder":
            if lane.get("insert") is False:
                self._count_out(h, lane.get("encoder_mm"))
            return
        if not h.out_due:
            return
        why = self._out_dropped(h, lane)
        if why is not None:
            h.out_due = False
            logging.info("ace2k_u1: lane %s: end line dropped: %s", self.adapter.lane_text(h), why)
            return
        ok, position = self._extruder_at(h)
        self._count_out(h, position)
        if not ok or h.out_mm >= OUT_NOTICE_MM:
            h.out_due = False
            self.adapter.say(self._end_line(h, lane.get("insert") is True))

    # --- the grip --------------------------------------------------------------------------

    def _owns_head(self, h: Hooked, lane: LaneView | None = None) -> bool:
        """True when the lane owns what the sensor of its head sees.

        The lane owns it when its follow is armed, when it is in its tail, or when the U1's channel
        holds the head (see build_owned_states: loaded, preloaded, any step of a load). It never
        owns it after its tail_out, until the U1 loads the head again. It never raises.
        """
        if h.after_tail or h.waits_behind:
            return False
        if h.armed or self.in_tail(h, lane):
            return True
        return self._channel_holds(h)

    def sync_grip(self, h: Hooked, holds: bool | None = None, lane: LaneView | None = None) -> None:
        """Set or clear the grip of the lane, as the head stands.

        The grip is set while the sensor of the head sees a filament that the lane does not own.
        A sensor that cannot be read counts as seeing one. Otherwise the grip is cleared. It is
        left alone while the lane runs a motion, because that motion ends the grip on the unit
        anyway.

        Three cases are the bay of a shared head, whatever the sensor reads: a lane parked outside
        its shared head, the current lane of a shared head whose sensor reads empty, and a lane
        whose refill is pending. On a shared head they have no grip. The unit's own load pulls a
        spool put in to the parking point, searches for its tag past it (up to the search's
        reach, or until it meets the other lane's filament at the splitter), and comes back to
        the parking point. So the roll back of the probe never takes it out of the unit. Its load
        into the head is the job of the U1, through the push of the probe.

        A pending refill of a head that one lane feeds has the park, if the image has_park: the
        load goes to the parking point and reads the tag on the way, with no search past it. An
        older image grips it at grip_mm, and this is said once per unit.

        Any other grip is the lane of a lone head that holds a piece. It is the wait, if the image
        has_wait: an insert starts no load, and nothing moves until the U1's load. An older image
        grips it at grip_mm, and this is said once per unit.

        The grip is sent only on a change (of the want, or of its kind), and again after
        the unit dropped it. It is never sent during a swap of its head, because the swap runs the
        lanes of the head itself. (Its new lane is current, and the head is empty between the hand
        over and the push.) A refusal is a console line.
        """
        if h.head in self.adapter.swapping or self.adapter.running(h, lane):
            return
        want, why, refill = self._grip_want(h, holds, lane)
        park = refill and not h.park_refused and self._has_park(h)
        wait = (
            want
            and not refill
            and not h.wait_refused
            and not self.adapter.shared(h)
            and self._has_wait(h)
        )
        self._reopen_grip(h, want, park, wait)
        if self._grip_unchanged(h, want):
            return
        kind = self._send_grip(h, want, park, wait)
        if kind is None:
            return  # refused, and said once
        self._grip_sent(h, want, kind, why, lane)

    @staticmethod
    def _reopen_grip(h: Hooked, want: bool, park: bool, wait: bool) -> None:
        """Reopen the grip of h when a change asks for it.

        ``want``, ``park`` and ``wait`` are as sync_grip decides them. A want that changed tries a
        refused grip again. A kind that changed sends a live grip again.
        """
        if h.grip_refused is not None and h.grip_refused != want:
            h.grip_refused = None  # the want changed, so it is tried again
        if want and h.grip_live and (park != h.grip_park or wait != h.grip_wait):
            h.grip_live = False  # the kind changed, so it is sent again

    @staticmethod
    def _grip_unchanged(h: Hooked, want: bool) -> bool:
        """True when there is nothing to send for the grip of h.

        ``want`` is as sync_grip decides it, after _reopen_grip. Nothing is sent when the unit
        holds the grip already, or refused it while the want stays. This only reads h.
        """
        return (want == h.grip_wanted and (h.grip_live or not want)) or h.grip_refused is not None

    def _grip_sent(self, h: Hooked, want: bool, kind: str, why: str, lane: LaneView | None) -> None:
        """Record what a sent grip (or its clearing) leaves.

        ``kind`` is what _send_grip sent: "park", "wait" or "grip". That is the state of the lane,
        the bay seen empty under a new grip, and the console line.
        """
        h.grip_wanted = h.grip_live = want
        h.grip_park = kind == "park"
        h.grip_wait = want and kind == "wait"
        if not want:
            h.grip_down = False  # an empty bay seen under a grip that is now cleared tells nothing
        if want:
            if lane is None:
                lane = self.adapter.lane_or_none(h)
            if lane is not None and lane.get("insert") is not True:
                self.grip_bay_empty(h)
            elif lane is not None and h.grip_wait:
                self.wait_filled(h)  # a filament put in before the wait, during the tail
        if h.grip_park:
            what = f"park: its next load to the parking point, no tag search ({why})"
        elif h.grip_wait:
            what = f"wait: no automatic load until the U1's load ({why})"
        elif want:
            what = f"grip {self._grip(h):g} mm ({why})"
        else:
            what = "grip cleared"
        logging.info("ace2k_u1: lane %s %s", self.adapter.lane_text(h), what)

    def _grip_want(
        self, h: Hooked, holds: bool | None, lane: LaneView | None
    ) -> tuple[bool, str, bool]:
        """Decide the grip of the lane as the head stands, without sending it.

        The result is (want, why, refill). ``refill`` is True for the pending refill of a head
        that one lane feeds, whose grip may be the park. The three cases of the bay of a shared
        head (see sync_grip) want no grip.
        """
        if self.adapter.parked(h):
            return False, "parked outside its shared head", False
        if holds is None:
            holds = self.adapter.head_holds(h)
        refill = self._refill_of(h) and self._refills(h)
        if self.adapter.shared(h) and (refill or not holds):
            # A bay of a shared head has no grip: the unit's own load pulls the spool to the
            # parking point, searches for its tag past it (up to the search's reach, or until the
            # filament meets the other lane's at the splitter), and comes back to the parking
            # point.
            return False, "its refill is pending" if refill else "its shared head is empty", False
        if refill:
            return True, "its refill is pending", True
        return (
            holds and not self._owns_head(h, lane),
            f"e{h.head} holds a piece the lane does not own",
            False,
        )

    def _send_grip(self, h: Hooked, want: bool, park: bool, wait: bool) -> str | None:
        """Send the grip to the unit: the park (park), the wait (wait), else the grip or none.

        A park or a wait that the unit refuses is gripped instead, and is not asked again. The
        result is the kind in effect: "park", "wait" or "grip" (a clearing too). It is None when
        the unit refused: a console line is said once (grip_refused), and the grip is not retried
        every tick.
        """
        kind = "park" if park else "wait" if wait else "grip"
        try:
            if park or wait:
                kind = self._send_or_grip(h, kind)
            else:
                h.api.set_lane_grip(h.lane, self._grip(h) if want else 0)
        except Exception as err:
            h.grip_refused = want  # said once, and not retried every tick
            logging.exception("ace2k_u1: lane %s grip", self.adapter.lane_text(h))
            what = "set" if want else "cleared"
            self.adapter.say(f"ace2k_u1: lane {self.adapter.lane_text(h)} grip not {what} — {err}")
            return None
        return kind

    def _send_or_grip(self, h: Hooked, kind: str) -> str:
        """Send the park (kind "park") or the wait (kind "wait"), and return the kind in effect.

        A refusal is logged and gripped instead, so the bay is still kept from the search and the
        old piece from a load; it sets park_refused or wait_refused, so it is not asked again every
        tick, and the kind is then "grip". What the grip raises goes to the caller.
        """
        try:
            (h.api.set_lane_park if kind == "park" else h.api.set_lane_wait)(h.lane)
        except Exception:
            logging.exception(
                "ace2k_u1: lane %s %s, gripped instead", self.adapter.lane_text(h), kind
            )
            if kind == "park":
                h.park_refused = True
            else:
                h.wait_refused = True
            h.api.set_lane_grip(h.lane, self._grip(h))
            return "grip"
        return kind

    def park_short(self, event: FeedEvent) -> bool:
        """True when the load of a park ended short.

        That means less filament is in than the probe rolls back, plus a margin. The roll back
        of the probe would take such a filament out of its unit.
        """
        probe_mm = (
            self.adapter.probe.cfg.probe_mm if self.adapter.probe is not None else PROBE_MM_DEFAULT
        )
        return moved_mm(event) < probe_mm + PARK_SHORT_MARGIN_MM

    def tag_read_now(self, h: Hooked) -> bool:
        """True when the tag of the lane shows as read in its status now.

        An unreadable status counts as not read.
        """
        lane = self.adapter.lane_or_none(h)
        return lane is not None and tag_read_live(lane)

    def _has_park(self, h: Hooked) -> bool:
        """True when the unit of h can take the park (has_park of ace2k).

        Without it, this is said once per unit. The new spool of a lone head's pending refill is
        then gripped at grip_mm.
        """
        return self._has_feature(
            h,
            "has_park",
            self.park_missing_said,
            "the unit's firmware has no park: a pending refill's new spool is gripped at grip_mm"
            " (flash the release's image)",
        )

    def _has_wait(self, h: Hooked) -> bool:
        """True when the unit of h can take the wait (has_wait of ace2k).

        Without it, this is said once per unit. A lone head's new filament is then gripped at
        grip_mm, as before the wait.
        """
        return self._has_feature(
            h,
            "has_wait",
            self.wait_missing_said,
            "the unit's firmware has no wait; flash v0.13.0 or later — a lone head's new filament"
            " is gripped",
        )

    def _has_feature(self, h: Hooked, attr: str, said: set[str], line: str) -> bool:
        """True when the unit of h carries the feature flag attr of ace2k.

        Without it, line is said once per unit (said holds the units already told).
        """
        if getattr(h.api, attr, False):
            return True
        where = self.adapter.units.label(h.unit)
        if where not in said:
            said.add(where)
            self.adapter.say(f"ace2k_u1: {unit_prefix(self.adapter.units, h.unit)}{line}")
        return False

    def wait_filled(self, h: Hooked) -> None:
        """Note a filament in the bay of a lane whose wait is live. Once per filament.

        No load runs on a waiting lane, so nothing reads its tag until the U1's load moves it. The
        console says so now, once. The push of the lane is seated until the filament has left the
        bay (see Hooked.seat_due).
        """
        if h.waited:
            return
        h.waited = True
        self.adapter.tag_reader.tell_waiting(h)

    def grip_bay_empty(self, h: Hooked) -> None:
        """Note that the bay was seen empty while the grip is live.

        The next insert then loads with the grip. Under the wait an insert starts no load, so the
        empty bay is not noted for the load. (The tag to differ from is taken as the bay empties,
        see _bay_edge.)
        """
        if not h.grip_wait:
            h.grip_down = True

    # --- a filament waiting behind a piece in the head that is not its own ----------------------

    def _set_waits_behind(self, h: Hooked, why: str) -> None:
        """Note that a piece which is not of this lane is ahead of its filament, after a transition.

        Its follow is then wanted no more, so there is no standing re-arm once the head clears.
        """
        if not h.waits_behind:
            logging.info(
                "ace2k_u1: lane %s waits behind e%d's piece (%s)",
                self.adapter.lane_text(h),
                h.head,
                why,
            )
            self._hold_default(h, True)
        h.waits_behind = True
        h.follow_wanted = False
        h.clear_since = None  # a clear seen before the set is not the end of this piece
        self._say_unreadable(h)

    def clear_waits_behind(self, h: Hooked, why: str | None = None) -> None:
        if h.waits_behind and why is not None:
            logging.info(
                "ace2k_u1: lane %s no longer behind e%d's piece (%s)",
                self.adapter.lane_text(h),
                h.head,
                why,
            )
        if h.waits_behind:
            self._hold_default(h, False)
        h.waits_behind = False
        h.behind_said = False

    def _tell_port(self, h: Hooked, detected: bool) -> None:
        """Tell the U1 about the inlet of the lane (its port event).

        This is the one path that the hooked handler passes on.
        """
        h.telling = True
        try:
            h.ff._port_event_handler(detected, h.ch)
        finally:
            h.telling = False

    def _hold_default(self, h: Hooked, held: bool) -> None:
        """Hold the follow_default of ace2k for a lane that waits behind another piece.

        The default then arms nothing on the lane (hold_follow, host interface 8). It never raises.
        """
        try:
            h.api.hold_follow(h.lane, held)
        except Exception:
            logging.exception("ace2k_u1: lane %s hold_follow", self.adapter.lane_text(h))

    def _channel_holds(self, h: Hooked) -> bool:
        """True when the channel of the U1 holds the head (see build_owned_states)."""
        if self.adapter.owned_states is None:  # not hooked through hook_all
            self.adapter.owned_states = self.adapter.build_owned_states()
        return h.ff.channel_state[h.ch] in self.adapter.owned_states

    def watch_head_clear(self, h: Hooked, holds: bool, eventtime: float) -> None:
        """Release a waiting lane when the sensor of its head reads clear for HEAD_CLEAR_S.

        Then the piece ahead has run out. A shorter clear is a glitch. A sensor that cannot be
        read, or one that is stuck set, never releases the lane. The console then says that
        ACEK_CLEAR LANE=n does, once per episode.
        """
        if holds:
            h.clear_since = None
        elif h.clear_since is None:
            h.clear_since = eventtime
        if h.clear_since is not None and eventtime - h.clear_since >= HEAD_CLEAR_S:
            self.clear_waits_behind(h, "the head's sensor cleared")
        self._say_unreadable(h)

    def _say_unreadable(self, h: Hooked) -> None:
        """Say once per episode that the head sensor of a waiting lane cannot be read."""
        if not (h.waits_behind and h.head_unread):
            h.unread_said = False
            return
        if h.unread_said:
            return
        h.unread_said = True
        n = self.adapter.lane_text(h)
        self.adapter.say(
            f"ace2k_u1: lane {n}: e{h.head}'s sensor cannot be read — lane {n} waits behind its "
            f"piece until the sensor reads clear, or ACEK_CLEAR {self.adapter.lane_args(h)}"
        )

    def on_ace_clear(self, lane: int, unit: Unit | None = None) -> None:
        """Handle ACEK_CLEAR [UNIT=] LANE=n. Unit None means the only unit in use. It never raises.

        This is the escape of the operator, for example when a head sensor is stuck set. The lane
        no longer waits behind a piece. Its holds end as those of a U1 load would (see end_hold):
        the filament in the bay counts as told, and no rise is held again. Its next load takes the
        head.

        Outside a print, if the head reads clear and a filament is in the bay, the U1 is told of
        it once (its preload), as it is when a hold is released outside a print.
        """
        h = self.adapter.find(lane, unit)
        if h is None or not h.waits_behind:
            return
        try:
            tell = (
                not self.adapter.in_print()
                and not self.adapter.head_holds(h)
                and not h.present
                and self.adapter.present(h)
            )
        except Exception:
            logging.exception("ace2k_u1: lane %s status", self.adapter.lane_text(h))
            tell = False
        self.clear_waits_behind(h)
        self.end_hold(h)
        if tell:
            h.present = True
            self._tell_port(h, True)
        self.adapter.say(
            f"ace2k_u1: lane {self.adapter.lane_text(h)}: ACEK_CLEAR — no longer counted as "
            f"waiting behind e{h.head}'s piece; its next load takes the head"
        )

    def _head_foreign(
        self, h: Hooked, holds: bool | None = None, lane: LaneView | None = None
    ) -> bool:
        """True when the head holds a piece that is not the filament of this lane.

        That is the case after its tail_out until the U1 loads the head again. It is also the case
        whenever the sensor of the head sees a filament that the lane does not own. It never
        raises. A sensor that cannot be read counts as seeing one, as it does for the grip.
        """
        if h.after_tail:
            return True
        if holds is None:
            holds = self.adapter.head_holds(h)
        return holds and not self._owns_head(h, lane)

    def sample_wheel(
        self, h: Hooked, lane: LaneView, eventtime: float, holds: bool | None = None
    ) -> None:
        """Take a sample for the wheel stand-in.

        While the head holds a piece that is not the filament of this lane, the filament in the bay
        waits (gripped, or held) while the extruder pulls the old piece. The tangle check of the U1
        is on while its inlet reads a filament. It would see a stalled wheel and pause the print.
        So the wheel follows the extruder of the head then. It goes back to the encoder of the lane
        once the lane owns the head again. Each change of source re-bases the wheel, so its counts
        never jump.
        """
        source, value = "encoder", lane.get("encoder_mm")
        foreign = self.adapter.extruder_position is not None and self._head_foreign(h, holds, lane)
        if not foreign:
            h.position_unread = False  # a failure in a later episode is logged again
        else:
            try:
                # foreign is True, so the reader is there
                reader = cast("ExtruderPosition", self.adapter.extruder_position)
                position = reader(h.head)
            except Exception:
                if not h.position_unread:  # logged once per episode
                    h.position_unread = True
                    logging.exception(
                        "ace2k_u1: lane %s e%d position", self.adapter.lane_text(h), h.head
                    )
                if h.wheel_source == "extruder":
                    # This sample is skipped and the source is kept. There is no re-base, so
                    # the next good reading counts the travel since the last one.
                    return
                position = None  # still on the encoder, so it goes on sampling the encoder
            else:
                h.position_unread = False
            if position is not None:
                source, value = "extruder", position
        if source != h.wheel_source:
            logging.info(
                "ace2k_u1: lane %s wheel follows %s",
                self.adapter.lane_text(h),
                f"e{h.head}'s extruder (its piece is not the lane's)"
                if source == "extruder"
                else "the lane's encoder",
            )
        self._wheel_from(h, source, value, eventtime)

    def _wheel_from(self, h: Hooked, source: str, value: float | None, eventtime: float) -> None:
        """Give the wheel a sample from source ("encoder" or "extruder") that reads value.

        A change of source re-bases the wheel instead, so its counts never jump. In a head of
        several lanes, the wheel of the U1 (the one of the slot) takes the sample of the lane that
        feeds it. That is the current lane, or the first lane when there is none. It is re-based
        on a change of lane or of source.
        """
        wheel = cast("EncoderWheel", h.wheel)  # it is set at the hook (hook_feed)
        if source != h.wheel_source:
            h.wheel_source = source
            wheel.rebase(value, eventtime)
        else:
            wheel.sample(value, eventtime)
        slot = self.adapter.slot_of(h)
        if slot is None or slot.wheel is None or slot.wheel is h.wheel:
            return
        if (slot.current or slot.lanes[0]) is not h:
            return  # another lane feeds the wheel of the U1
        key = (id(h), source)
        if key != slot.wheel_key:
            slot.wheel_key = key
            slot.wheel.rebase(value, eventtime)
        else:
            slot.wheel.sample(value, eventtime)

    def _rise_settled(self, h: Hooked, lane: LaneView, eventtime: float) -> bool:
        """True when a filament that rose in the bay has settled.

        That means the load of the unit (or its error) ended settle_s ago, and not in error. The
        window starts again while the unit is busy. A load that ended in error is said once, until
        the lane leaves the error. On a head that h alone feeds, a load whose tag is searching
        counts as ended: the U1 is told then, so that its preload's push cuts the search (see
        PushRunner._cut_search) instead of waiting for it to come back. The unit reports the tag
        as searching from the load's first mm; the U1 asks for the push only later (the cut came
        about 52 mm in on the bench). A filament that waited is never cut: the push seats it
        (see PushRunner._seat_to_head).
        """
        mode = lane.get("mode")
        searching = (
            mode == "loading" and tag_state(lane) == "searching" and self.adapter.cut_possible(h)
        )
        if h.rise_at is None or (mode in BUSY_MODES and not searching):
            h.rise_at = eventtime  # the window starts when the load of the unit (or its error) ends
        if mode == "error":
            if not h.error_said:
                h.error_said = True
                self.adapter.say(
                    f"ace2k_u1: lane {self.adapter.lane_text(h)}: the unit's load ended in error — "
                    f"ACEK_CLEAR {self.adapter.lane_args(h)}, then re-insert"
                )
            return False
        h.error_said = False
        return (mode != "loading" or searching) and eventtime - h.rise_at >= self.adapter.settle_s

    def end_hold(self, h: Hooked) -> None:
        """Handle a head that the U1 loaded itself, by its replenish or by a load from its screen.

        Both read the bay. Any hold of the insert of the bay is over. A filament in the bay is the
        one that was loaded. It was told already, and is never raised again as an insert. This does
        not apply while the filament of the lane waits behind the piece that the load of the U1
        used instead (waits_behind). It never raises.
        """
        if h.waits_behind:
            return
        h.after_tail = False
        h.rise_held = False
        if h.present:
            return
        try:
            h.present = self.adapter.present(h)
        except Exception:
            logging.exception("ace2k_u1: lane %s status", self.adapter.lane_text(h))
            h.present = True  # a filament that the U1 just loaded: never raised as an insert

    def bay_watch(self, h: Hooked, lane: LaneView, eventtime: float) -> None:
        """Drop the spool record of the lane when its filament leaves the unit.

        That is the bay going from full to empty, whatever emptied it: an eject, an unload, a
        runout, a roll back, or a pull by hand. The record goes once the bay has read empty for
        BAY_EMPTY_S in a row with the lane at rest, so a single empty reading during a motion, or
        a roll back of this adapter that pushes the filament back within that time, keeps it.
        The mark of a forgotten tag (ACEK_TAG_FORGET) goes with the record. A record set on a bay
        that was already empty stays until the bay fills and empties again. If the write of a
        drop fails, it is tried again on the next tick (on_bay_empty returned False).
        """
        insert = lane.get("insert")
        was_empty = h.bay_empty
        self._bay_edge(h, lane)
        self._setting_owner(h, insert, was_empty, eventtime)
        watch_tag_seen(h, lane)
        if insert is True:
            if not h.bay_full and not h.drop_wait:
                h.tag_forgotten = False  # a filament put into an empty bay is a new spool
            h.bay_full = True
            h.drop_due = h.drop_wait = False
            h.empty_since = None
            return
        if insert is not False:
            h.empty_since = None  # no report of the sensors: the bay did not read empty
            return
        h.push_end = None  # no push of it is left to show
        if h.bay_full:
            h.bay_full = False
            h.bay_emptied_at = eventtime  # the old tail of a refill went out
            h.drop_wait = True
        if h.drop_wait:
            self._empty_at_rest(h, lane, eventtime)
        if h.drop_due and self.adapter.on_bay_empty is not None:
            try:
                h.drop_due = self.adapter.on_bay_empty(h) is False
            except Exception:  # never into the bay watch
                logging.exception("ace2k_u1: lane %s spool record", self.adapter.lane_text(h))

    @staticmethod
    def _empty_at_rest(h: Hooked, lane: LaneView, eventtime: float) -> None:
        """Make the record of h due to drop once its bay has read empty for BAY_EMPTY_S in a row
        with the lane at rest (idle or error, no motion of this adapter open)."""
        if lane.get("mode") not in IDLE_MODES or h.motion_open:
            h.empty_since = None
        elif h.empty_since is None:
            h.empty_since = eventtime
        elif eventtime - h.empty_since >= BAY_EMPTY_S:
            h.drop_wait, h.empty_since = False, None
            h.drop_due = True
            h.tag_forgotten = False

    def _setting_owner(self, h: Hooked, insert: object, was_empty: bool, eventtime: float) -> None:
        """Let the head's setting go with the spool it was written for (spool_left).

        A new spool put into an empty bay ends it at once. The edge of the bay from full to
        empty starts a watch (h.leave_watch): the setting goes once the lane has read out of its
        head (Ejector.known_loaded False, with its channel read) for SETTING_LEAVE_S in a row, as
        when an unload moved it out or the spool was taken out by hand. A run-out lane still
        loaded keeps it for its rest. On a shared head the setting written for the lane
        (setting_for) goes the same way.
        """
        if insert is True:
            h.leave_watch, h.leave_since = False, None
            if was_empty:
                self._setting_gone(h)
            return
        if insert is not False:
            return
        if not was_empty:
            h.leave_watch, h.leave_since = True, None  # the edge: watched from now on
        if not h.leave_watch:
            return
        if self.adapter.ejector.known_loaded(h) or h.channel_unread:
            h.leave_since = None
            return
        if h.leave_since is None:
            h.leave_since = eventtime
        elif eventtime - h.leave_since >= SETTING_LEAVE_S:
            h.leave_watch, h.leave_since = False, None
            self._setting_gone(h)

    def _setting_gone(self, h: Hooked) -> None:
        spool_left(h)
        slot = self.adapter.slot_of(h)
        if slot is not None and slot.setting_for is h:
            slot.setting_for = None

    @staticmethod
    def _bay_edge(h: Hooked, lane: LaneView) -> None:
        """Note a bay that reads empty or full. An unknown reading is neither."""
        insert = lane.get("insert")
        if insert is True:
            h.bay_empty = False
        elif insert is False:
            h.clear_wait()  # the filament left the bay
            if not h.bay_empty:
                # The count of watch_out starts here, from the encoder of the lane.
                h.out_mm, h.out_source, h.out_due = 0.0, "encoder", False
                h.out_last = lane.get("encoder_mm")
                # The tag the lane shows as its bay empties is the one that a read of a filament
                # put in later must differ from, in the tail too, whatever its grip. The same spool
                # out and in again reads as that record.
                h.tag_before = tag_identity(lane.get("tag"))
            h.bay_empty = True

    def watch_parked(self, h: Hooked, lane: LaneView, eventtime: float) -> None:
        """Watch the bay of a lane that is parked outside its shared head.

        It feeds the wheel from the encoder of the lane, and it tracks what is in its bay
        (h.present, settled as a rise is). The U1 is told nothing, because its inlet reads the
        current lane of the head. Nothing is armed.
        """
        self._wheel_from(h, "encoder", lane.get("encoder_mm"), eventtime)
        if lane.get("insert") is not True:
            h.rise_at = None
            h.error_said = False
            h.present = False
            return
        if not h.present and self._rise_settled(h, lane, eventtime):
            h.present = True

    def grip_text(self, h: Hooked) -> str:
        """Return the grip text for ACEK_ADAPTER_STATUS.

        It shows the grip as the unit holds it, as far as this adapter knows. When the grip is
        wanted but not there (consumed by its load, or cleared by a start), it says so.
        """
        if h.grip_live:
            if h.grip_wait:
                return "the wait"
            return "the park" if h.grip_park else f"{self._grip(h):g} mm"
        return "off (wanted, to be set again)" if h.grip_wanted else "off"

    def _watch_tail_end(self, h: Hooked, lane: LaneView, tail: bool, holds: bool) -> None:
        """Stop a lane once when the end of its tail has passed the sensor of the head.

        Nothing is left in the drive then, and the U1's own runout takes the head from there.
        This applies only while the current motion of the lane is the follow (see motion). It does
        not apply once that motion has ended.
        """
        if not tail:
            h.tail_stopped = False
            return
        if h.tail_stopped or self.adapter.motion(h, lane) != "following":
            return
        if holds:
            return
        h.tail_stopped = True
        # The tail ends here and not at a tail_out. The follow is wanted no more, as on_tail_out
        # has it: a filament put in during the tail rests behind the old end, not at the gear.
        # The sensor of the head reads clear by now, so nothing waits behind a piece in it.
        h.follow_wanted = False
        self.adapter.say(
            f"ace2k_u1: lane {self.adapter.lane_text(h)}: the tail passed e{h.head}'s sensor — "
            "stopped"
        )
        self.adapter.stop(h, "the tail's end", lane)

    def _watch_held(self, h: Hooked, lane: LaneView, holds: bool) -> None:
        """Tell the U1 about the emptying of the bay that was held back during the tail.

        This is done once the tail has ended: the lane is out of the follow, by its tail_out or by
        the stop at the sensor of the head. The tail ignores the bay. A filament put in meanwhile
        changes nothing (while the motion of the lane is the follow, it belongs to the tail). A
        final event since the hold ends the tail at once. A filament in the bay is then told the
        stock way on a later tick.
        """
        if h.tail_ended:
            self._send_held(h)
            return
        tail = self._tail(h, lane) or self.adapter.motion(h, lane) == "following"
        self._watch_tail_end(h, lane, tail, holds)
        if tail:
            return
        self._send_held(h)

    @staticmethod
    def _held_or_told(h: Hooked) -> bool:
        """True when the emptying of the bay is still held, or the U1 was told of a filament.

        This is read after _watch_held, which may end the hold.
        """
        return h.port_held or h.present

    def _send_held(self, h: Hooked) -> None:
        h.port_held = False
        h.tail_ended = False
        h.present = False
        self._tell_port(h, False)

    def watch_lane(self, h: Hooked, lane: LaneView, eventtime: float, holds: bool) -> None:
        """Compare the bay of the lane with what the U1 was told about it.

        First the emptying of the bay that is held in the tail is handled. Then comes a removal
        (see _watch_removal), or a rise once it has settled (see _watch_rise).
        """
        mode = lane.get("mode")
        if h.after_tail and not h.port_held and lane.get("insert") is not True and not holds:
            # The old piece has left the head with no filament in the bay, so there is nothing to
            # hold. A filament in the bay is decided at its rise below, after its own checks.
            h.after_tail = False
        if h.port_held:
            self._watch_held(h, lane, holds)
            if self._held_or_told(h):
                return
        if lane.get("insert") is not True:
            self._watch_removal(h, lane, mode, holds)
        elif not h.present and self._rise_settled(h, lane, eventtime):
            self._watch_rise(h, lane, holds)

    def _watch_removal(self, h: Hooked, lane: LaneView, mode: str | None, holds: bool) -> None:
        """Handle a bay that reads no filament. ``mode`` is the mode of the lane in this tick."""
        # A removal is told at once, but only if the U1 had been told about the filament. In the
        # tail the follow feeds on. It is not stopped and still wanted. The U1 is not told about
        # the empty bay until the tail ends.
        h.rise_at = None
        h.error_said = False
        h.rise_held = False  # a held insert is gone with its filament
        tail = self._tail(h, lane)
        if lane.get("insert") is False and not tail:
            h.follow_wanted = False
        if h.present:
            if tail:
                h.port_held = True
                h.tail_ended = False
                self._watch_held(h, lane, holds)
                return
            h.present = False
            # The reported mode counts too. A hand motion that started while a fresh end hides
            # it is stopped all the same. (A stop of no open motion of its own expects nothing.)
            if self.adapter.motion(h, lane) in ARMED_MODES or mode in ARMED_MODES:
                # Guarded: a stop that fails is said, and the U1 is told all the same.
                self.adapter.stop(h, "its filament leaving the bay", lane)
            h.armed = False
            self._tell_port(h, False)
        else:
            # a tail whose filament the U1 was never told about
            self._watch_tail_end(h, lane, tail, holds)

    def _watch_rise(self, h: Hooked, lane: LaneView, holds: bool) -> None:
        """Handle a filament that rose in the bay and settled, when the U1 was not told yet."""
        if holds:
            # The sensor of the head sees a filament (an old piece, or a filament behind it).
            # There is no preload against it, because it would fail on the loaded head, in the
            # stock U1 too.
            if not h.rise_held:
                h.rise_held = True
                logging.info(
                    "ace2k_u1: lane %s waits for e%d to clear", self.adapter.lane_text(h), h.head
                )
                if not self._channel_holds(h):
                    self._set_waits_behind(h, "a filament in the bay with the head set")
                # It is already set, as the head holds a piece, so it is kept.
                self.sync_grip(h, holds, lane)
                if h.grip_live and h.grip_wait:
                    self.wait_filled(h)
            return
        held = h.rise_held or h.after_tail
        h.rise_held = False
        h.after_tail = False
        if self._refill_of(h):
            # The new spool of a pending refill: RESUME pushes and loads it. The U1 is not told,
            # so its preload does not start during the pause.
            h.present = True
            logging.info(
                "ace2k_u1: lane %s's new spool left to the refill", self.adapter.lane_text(h)
            )
            return
        if held and self.adapter.in_print():
            # In a print a held insert is dropped and never raised. The U1's own runout path
            # brings the filament to the head: its replenish load, which reads the bay, or a load
            # from its screen.
            h.present = True
            logging.info(
                "ace2k_u1: lane %s's filament left to the U1's runout", self.adapter.lane_text(h)
            )
            return
        h.present = True
        self._tell_port(h, True)
        if (
            not h.ff.config["auto_mode"][h.ch]
            and not h.seat_due
            and self.adapter.follow.default_on(h)
        ):
            # The U1 runs no preload on a channel that is out of its auto mode. So the insertion,
            # which ace2k leaves to this adapter, is followed here at once. A filament that waited
            # is not: it sits at the bay, so the U1's load seats it, and its extrusion arms the
            # follow.
            self.adapter.follow.arm(h, lane=lane)
