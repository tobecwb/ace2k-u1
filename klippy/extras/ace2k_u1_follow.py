"""The follow of the hooked lanes.

This module arms the follow, with one start per lane at a time. It adopts the follow that ace2k
armed. It applies ace2k's rule for a shared extruder: only the active lane follows. It re-arms a
wanted lane (the standing re-arm). It also stops the follow of a head with no lane known in it. The
adapter keeps the lanes and their motions, and Follow runs these steps for it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .ace2k_klipper_types import Reactor
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_hooks import EjectSchedule
    from .ace2k_u1_printer_types import Coordinator, FeedMove, LaneView

try:
    from .ace2k_u1_common import BUSY_RETRY_POLL_S
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import BUSY_RETRY_POLL_S  # type: ignore[import-not-found, no-redef]


class FollowAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the follow uses it."""

    @property
    def reactor(self) -> Reactor: ...
    @property
    def units(self) -> Coordinator: ...
    @property
    def extruders(self) -> dict[int, str] | None: ...  # Maps a head to its extruder's name.
    @property
    def feed_speed_mm_s(self) -> float: ...
    @property
    def ejector(self) -> EjectSchedule: ...
    def lane_text(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...
    def stop(self, h: Hooked, why: str, lane: LaneView | None = None) -> None: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    def set_current(self, slot: Slot, h: Hooked | None) -> None: ...
    def groups(self) -> list[Slot]: ...
    def lane_or_none(self, h: Hooked) -> LaneView | None: ...
    def get_loaded_states(self) -> set[str]: ...
    def started(self, h: Hooked, move: FeedMove) -> None: ...
    def retry_busy_start(
        self,
        h: Hooked,
        start: Callable[[], FeedMove],
        wait_s: float = ...,
        period: float | None = None,
    ) -> FeedMove: ...


class Follow:
    """The follow of the lanes of one adapter."""

    def __init__(self, adapter: FollowAdapter) -> None:
        self.adapter = adapter
        # Maps the heads that were said to have no lane known (see stop_unchosen).
        self.unchosen_said: set[int] = set()

    def adopt_active(self) -> None:
        """Let a head with several lanes take the lane that the coordinator chose for its extruder.

        The choice is made by ACEK_LANE_ACTIVE. This runs at the hook and after each
        ACEK_LANE_ACTIVE. A choice outside the lanes of the head, or no choice, leaves the current
        lane as it is.
        """
        for slot in self.adapter.groups():
            chosen = [h for h in slot.lanes if self._chosen(h)]
            if chosen and chosen[0] is not slot.current:
                self.adapter.set_current(slot, chosen[0])

    def stop_unchosen(self, h: Hooked, lane: LaneView) -> None:
        """Stop the follow of a lane in a head of several lanes that has no lane known in it.

        Only one lane follows per head, so such a lane must not follow. The follow is stopped once
        per follow. Its standing is dropped, so the re-arm does not bring it back. This is needed
        because ace2k arms the follow of every lane by default before the groups are given to it,
        and its own check refuses a start on such a lane only after that.

        A lane that this adapter runs itself (hold: a probe, a swap, a refill) is left alone. There
        is one console line per head, and another one when the head has had a lane since. It never
        raises.
        """
        slot = self.adapter.slot_of(h)
        if slot is None or not slot.shared:
            return
        if slot.current is not None:
            self.unchosen_said.discard(slot.head)
            h.unchosen_stopped = False
            return
        if lane.get("mode") != "following":
            h.unchosen_stopped = False
            return
        if h.hold or h.arming or h.unchosen_stopped:
            return
        if self._chosen(h) or self._choosing(h):
            return  # the coordinator chose it, and arms it before slot.current is synced again
        h.unchosen_stopped = True
        h.follow_wanted = False
        self.adapter.stop(h, "a head with no lane known in it", lane)
        if slot.head not in self.unchosen_said:
            self.unchosen_said.add(slot.head)
            self.adapter.say(
                f"ace2k_u1: head {slot.head + 1}: no lane is known in it: its lanes' follow is "
                "stopped until one is chosen (a print start probes it, or ACEK_LANE_ACTIVE)"
            )

    def _choosing(self, h: Hooked) -> bool:
        """True when ACEK_LANE_ACTIVE is arming the lane of h now (the choosing key)."""
        choosing = getattr(self.adapter.units, "choosing", None)
        index = getattr(self.adapter.units, "index", None) or {}
        return choosing is not None and choosing == (index.get(h.unit), h.lane)

    def _chosen(self, h: Hooked) -> bool:
        """True when the active lane of the coordinator for the extruder of h is h.

        The key there is (unit index, lane). A key of (unit name, lane) is taken too.
        """
        active = getattr(self.adapter.units, "active", None) or {}
        name = self.adapter.extruders.get(h.head) if self.adapter.extruders else None
        want = active.get(name)
        if want is None:
            return False
        index = getattr(self.adapter.units, "index", None) or {}
        return want in ((getattr(h.unit, "name", None), h.lane), (index.get(h.unit), h.lane))

    def arm(self, h: Hooked, busy_wait_s: float = 0.0, lane: LaneView | None = None) -> None:
        """Arm the follow of the lane, once.

        The follow is not armed again while this adapter armed it and has not stopped it since.
        busy_wait_s is used for a re-arm right after a stop by this adapter. A busy refusal is then
        retried for that long, whatever mode the last report gave, because the lane is winding down.
        A refusal is a notice, and never an error into the U1. ``lane`` is the view of the bay watch
        of the lane in this tick (None: read it when needed). A busy refusal is still judged on a
        fresh read, because it answers a start that was sent after that view.
        """
        self.adapter.ejector.cancel_eject(h)  # a pending eject must not start under a new pull
        if h.waits_behind:
            # The head runs on the piece ahead, and the filament of the lane cannot move behind
            # it. A follow would only time out.
            if not h.behind_said:  # once in an episode
                h.behind_said = True
                logging.info(
                    "ace2k_u1: lane %s waits behind e%d's piece: no follow",
                    self.adapter.lane_text(h),
                    h.head,
                )
            return
        h.follow_wanted = True
        if h.armed or h.arming:
            return  # armed, or an arm in flight: only one start per lane at a time
        if self._ace2k_pauses(h):
            return  # ace2k holds the lane for a G-code move. Its end re-arms the lane or not
        if self.adopt(h, lane):
            return
        if not self._may_follow(h):
            # The rule of ace2k: one lane per extruder is in the follow, the one that
            # ACEK_LANE_ACTIVE chose. The follow stays wanted. Once the lane is chosen, ace2k
            # arms it and it is adopted.
            self._not_active(h)
            return
        h.arming = True
        try:
            self._arm_now(h, busy_wait_s)
        finally:
            h.arming = False

    def _may_follow(self, h: Hooked) -> bool:
        """True when the lane may follow, as the coordinator of ace2k says (host interface 9).

        A lane of a shared extruder may not follow unless it is the active lane. It never raises.
        If the answer cannot be read, the start is left to ace2k, which refuses it as not_active
        all the same.
        """
        ask = getattr(self.adapter.units, "may_follow", None)
        if ask is None:
            return True
        try:
            return bool(ask(h.unit, h.lane))
        except Exception:
            logging.exception("ace2k_u1: lane %s may_follow", self.adapter.lane_text(h))
            return True

    def shared_with(self, h: Hooked) -> str | None:
        """Return the lanes that feed the extruder of the head, if it is shared, else None.

        The lanes are named as the console names them ("a:4, b:1"). The extruder is shared when
        several lanes on any unit feed it (shared_lanes of the coordinator of ace2k). It never
        raises. If the answer cannot be read, the head counts as its lane's own head, as before.
        """
        shared_lanes = getattr(self.adapter.units, "shared_lanes", None)
        if shared_lanes is None:
            return None
        try:
            shared: str | None = shared_lanes(h.unit, h.lane)
        except Exception:
            logging.exception("ace2k_u1: lane %s extruder group", self.adapter.lane_text(h))
            return None
        return shared

    def _not_active(self, h: Hooked) -> None:
        """Note that a lane of a shared extruder is not its active lane, so it does not follow.

        This is logged once per episode. It is never an error into the U1.
        """
        if not h.not_active_said:
            h.not_active_said = True
            logging.info(
                "ace2k_u1: lane %s shares e%d and is not its active lane: no follow",
                self.adapter.lane_text(h),
                h.head,
            )

    def _arm_now(self, h: Hooked, busy_wait_s: float) -> None:
        n = self.adapter.lane_text(h)
        try:
            move = self.adapter.retry_busy_start(
                h,
                lambda: h.api.start_move(h.lane, "assist_both", 0.0, self.adapter.feed_speed_mm_s),
                wait_s=busy_wait_s,
                period=BUSY_RETRY_POLL_S,
            )
        except Exception as err:  # FeedRefused (with a reason), ValueError, RuntimeError
            reason = getattr(err, "reason", None)
            if reason == "not_active":
                self._not_active(h)  # the choice moved since the question, so no follow
                return
            # A head that already follows is refused as busy. There is nothing to tell. It is
            # ace2k's own start in flight, or its follow (adopted on the next tick of the bay
            # watch), or a follow that the report shows. This does not apply after a stop by this
            # adapter (busy_wait_s): the reported mode is up to one report old, and it may still say
            # following while the lane winds down.
            if reason == "busy" and not busy_wait_s and self._ace2k_follows(h):
                logging.info("ace2k_u1: lane %s follow already armed", n)
                return
            self.adapter.say(f"ace2k_u1: lane {n} follow not armed — {err}")
            return
        self._mark_armed(h, move)

    def _mark_armed(self, h: Hooked, move: FeedMove) -> None:
        """Mark the lane as following, by a start of this adapter or the adopted start of ace2k."""
        h.armed = True
        h.armed_at = self.adapter.reactor.monotonic()
        h.assist_seq = getattr(move, "seq", None)
        h.not_active_said = False  # the lane follows, so a later "no follow" is said again

    def default_on(self, h: Hooked) -> bool:
        """True when follow_default of ace2k is on.

        Then the lanes are followed from the start and after each insertion. When it is off, this
        adapter arms only as it did before. That is in the load of the U1, its extrusion and the
        tip forming of its unload, and at the hook for a head that the U1 holds loaded.
        """
        return bool(getattr(h.api, "follow_default", True))

    def _ace2k_follows(self, h: Hooked) -> bool:
        """True when the lane already follows, as the report shows it.

        It also counts when ace2k's own arm is in flight or done (adopted on the next tick of the
        bay watch). It never raises.
        """
        lane = self.adapter.lane_or_none(h)
        if lane is None:
            return False
        return lane.get("mode") == "following" or lane.get("follow_default") in ("arming", "armed")

    def _ace2k_pauses(self, h: Hooked) -> bool:
        """True when ace2k stopped the follow of the lane for a G-code move that has not ended.

        This is follow_paused of host interface 8. This adapter arms nothing on the lane
        meanwhile. It never raises.
        """
        try:
            return bool(h.api.follow_paused(h.lane))
        except Exception:
            logging.exception("ace2k_u1: lane %s follow_paused", self.adapter.lane_text(h))
            return False

    def adopt(self, h: Hooked, lane: LaneView | None = None) -> bool:
        """Take the follow that ace2k armed on the lane as this adapter's own follow.

        The follow comes from follow_default of ace2k, at the start. Taking it sets armed, its
        sequence and wanted. Every flow then stops and re-arms it as if this adapter had armed it.
        It is not taken while this adapter runs the lane (the extrude retry, an eject) or arms it.
        The result is True when the lane runs the follow. ``lane`` is the view of the bay watch in
        this tick (None: read it when needed). It never raises.
        """
        if h.armed:
            return True
        if h.hold or h.arming:
            return False
        try:
            move = h.api.default_move(h.lane)
        except Exception:
            logging.exception("ace2k_u1: lane %s follow_default", self.adapter.lane_text(h))
            return False
        if move is None or move.done():
            return False
        if h.waits_behind:
            # The filament of the lane waits behind another piece, so there is no follow (as in
            # _arm). The follow of ace2k is stopped once.
            if h.unadopted_seq != getattr(move, "seq", None):
                h.unadopted_seq = getattr(move, "seq", None)
                logging.info(
                    "ace2k_u1: lane %s waits behind e%d's piece: ace2k's follow stopped",
                    self.adapter.lane_text(h),
                    h.head,
                )
                self.adapter.stop(h, "a lane waiting behind a piece", lane)
            return False
        self.adapter.started(h, move)
        self._mark_armed(h, move)
        h.follow_wanted = True
        # ace2k arms at the start, after an insertion and after a manual move, so no reason is given
        logging.info("ace2k_u1: lane %s follows (armed by ace2k)", self.adapter.lane_text(h))
        return True

    def clear_on_resume(self, h: Hooked, lane: LaneView) -> None:
        """Handle the resume of the U1: a wanted lane in error is cleared, then re-armed."""
        if lane.get("mode") != "error" or not h.follow_wanted:
            return
        n = self.adapter.lane_text(h)
        try:
            h.api.clear(h.lane)
        except Exception as err:
            logging.exception("ace2k_u1: lane %s clear on the resume", n)
            self.adapter.say(f"ace2k_u1: lane {n} not cleared on the resume — {err}")
            return
        self.adapter.say(f"ace2k_u1: lane {n} cleared on the resume; its follow is armed again")

    def watch_follow(self, h: Hooked, lane: LaneView) -> None:
        """Keep the standing follow.

        A wanted lane that is idle, with no error and a filament in its bay, on a head that the
        U1 holds loaded, is armed again. This is done once per idle episode.
        """
        if lane.get("mode") != "idle":
            h.rearm_tried = False
            return
        if self._rearm_due(h, lane):
            h.rearm_tried = True
            self.arm(h, lane=lane)

    def _rearm_due(self, h: Hooked, lane: LaneView) -> bool:
        """True when the standing follow of the idle lane h is to be armed again now.

        ``lane`` is its status. The follow must be wanted, and not tried in this idle episode. It
        must not be ace2k's, and nothing may wait behind. There must be no error, a filament in
        the bay, and the head must be loaded on the U1.
        """
        if h.rearm_tried or h.armed or h.arming or h.hold or not h.follow_wanted:
            return False
        if self._ace2k_pauses(h):
            return False  # not counted as tried: the episode is ace2k's until its move ends
        if h.waits_behind:
            return False
        if lane.get("error") is not None:
            return False
        if lane.get("insert") is not True:  # an idle lane has no tail, as the unit ends it
            return False
        return h.ff.channel_state[h.ch] in self.adapter.get_loaded_states()
