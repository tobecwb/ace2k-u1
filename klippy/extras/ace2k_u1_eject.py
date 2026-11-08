"""ACEK_EJECT: take the filament of a lane out of its bay, whatever the head holds.

The adapter keeps the lanes, the objects of the U1 and the settings of the eject. Ejector runs the
ejects step by step from their reactor timers, with the unit unloads that end them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import FeedEvent, GCode
    from .ace2k_u1_common import Hooked, LaneKey, Slot
    from .ace2k_u1_follow import Follow
    from .ace2k_u1_printer_types import Coordinator, FeedMove, LaneView, Reactor, Unit

try:
    from .ace2k_u1_common import (
        ARMED_MODES,
        BUSY_RETRY_POLL_S,
        CHUNK_CARRIED,
        INSIST_MIN_MM,
        MOVE_MARGIN_S,
        NUDGE_BUSY_S,
        PAUSE_WAIT_S,
        PUSH_POLL_S,
        TAUT_READINGS,
        UNIT_STOPS,
        TautWatch,
        carried,
        error_line,
        finite,
        held_window_ms,
        move_max_mm,
        moved_mm,
        parked_slot,
        start_armed,
        unit_move_wait_s,
        unit_stops,
    )
    from .ace2k_u1_eject_job import (
        STEP_BACK,
        STEP_BACK_SETTLE,
        STEP_BACK_WAIT,
        STEP_BEGIN,
        STEP_DONE,
        STEP_NET,
        STEP_PROBE,
        STEP_PROBE_WAIT,
        STEP_SETTLE,
        STEP_U1,
        EjectJob,
    )
    from .ace2k_u1_unload_watch import UnloadWatch
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        ARMED_MODES,
        BUSY_RETRY_POLL_S,
        CHUNK_CARRIED,
        INSIST_MIN_MM,
        MOVE_MARGIN_S,
        NUDGE_BUSY_S,
        PAUSE_WAIT_S,
        PUSH_POLL_S,
        TAUT_READINGS,
        UNIT_STOPS,
        TautWatch,
        carried,
        error_line,
        finite,
        held_window_ms,
        move_max_mm,
        moved_mm,
        parked_slot,
        start_armed,
        unit_move_wait_s,
        unit_stops,
    )
    from ace2k_u1_eject_job import (  # type: ignore[import-not-found, no-redef]
        STEP_BACK,
        STEP_BACK_SETTLE,
        STEP_BACK_WAIT,
        STEP_BEGIN,
        STEP_DONE,
        STEP_NET,
        STEP_PROBE,
        STEP_PROBE_WAIT,
        STEP_SETTLE,
        STEP_U1,
        EjectJob,
    )
    from ace2k_u1_unload_watch import UnloadWatch  # type: ignore[import-not-found, no-redef]

# The steps of an eject that move the lane: its probe, the settle after it and its feed back.
EJECT_MOVING_STEPS = frozenset(
    {STEP_PROBE_WAIT, STEP_SETTLE, STEP_BACK, STEP_BACK_WAIT, STEP_BACK_SETTLE}
)
EJECT_AGAIN = "again"
EJECT_AT_BAY = "at_bay"  # the two meanings of unload_incomplete (see _eject_incomplete)
EJECT_SAY = {  # console lines of the normal path; {n} is the lane number
    "head_now_empty": "ace2k_u1: lane {n} eject: head now empty — unloading",
    "head_empty": "ace2k_u1: lane {n} eject: head empty — unloading",
    "parked": "ace2k_u1: lane {n} eject: parked outside its head — unloading",
    "filament_free": "ace2k_u1: lane {n} eject: filament free (plunger at rest) — unloading",
    "held": "ace2k_u1: lane {n} eject: held by the head ({why}) — unloading the head on the U1",
    "net": (
        "ace2k_u1: lane {n} eject: the unload met the head's gear {mm:.1f} mm in — the lane"
        " cleared, held by the head after all — unloading the head on the U1"
    ),
    "plunger_not_at_rest": (
        "ace2k_u1: lane {n} eject: plunger not at rest after the feed back — going on, the U1's"
        " unload settles it"
    ),
    "no_decision": (
        "ace2k_u1: lane {n} eject: no decision after {s:g} s — ACEK_EJECT returns,"
        " the eject runs on"
    ),
}
# The bound of the probe. A follow can leave slack between the unit and the head. Measured on the
# U1: 9.9 + 39.5 = 49.4 mm of filament came out at the unit before the gear of the head held it,
# including the plunger travel from rest to taut (about 10 to 18 mm). A held tip turns the plunger
# taut within that distance. A filament that comes 80 mm with the plunger at rest is free.
EJECT_PROBE_MM = 80.0
# The minimum of eject_probe_mm. Its old range, 5 to 30, is raised to it.
EJECT_PROBE_MIN_MM = 20.0
# Without a proven link, no taut reading can stop the probe. So the probe is kept short (10 mm) and
# always counts as held. This avoids pushing 80 mm against a tip that the head holds.
EJECT_PROBE_SHORT_MM = 10.0
# The taut watch polls at the rate (10 Hz) at which the unit sends its switches on a proven link. A
# reading counts only from a report newer than the probe's start (sensors_reports of ace2k) and two
# report periods after it, as a report sampled before the motion may arrive just after its start.
# Two such reports in a row that read taut stop the probe. The host runs this watch only with a
# unit that cannot stop on its plunger. A unit that can is armed for "held" (EJECT_UNIT_STOP): it
# ends the probe itself when the plunger is taut and the filament stands still, and the host reads
# no status while the probe runs. The timer then only waits for the final event, at the same rate.
EJECT_TAUT_POLL_S = 0.1
EJECT_UNIT_STOP = frozenset({UNIT_STOPS["taut"]})
# Why the eject counts a probe as held when the plunger was taut: the host's taut watch, or the
# unit's "held" end.
EJECT_TAUT_WHY = "plunger taut"
EJECT_STOP_TRIES = 3
# The safety net. A "free" filament whose unit unload ends stuck within this much travel is held by
# the head after all (it is the slack the probe missed, about 50 mm, three times over). A stuck end
# past it is far from the head (the path on the U1 is about 1900 mm): a real failure.
EJECT_NET_MM = 150.0
# The ends that a held gear gives to an unload: the comparator toward the spool (stuck), a motor
# stall, and a trip with the buffer not full (tangled). unload_incomplete is a standstill with the
# plunger at rest, with nothing pulling on the buffer: a real failure.
EJECT_NET_KINDS = ("stuck", "motor_stalled", "tangled")
# An eject unload that ended stuck this close to the lane's known length to its head (its last
# push) has its tail at the bay (see Ejector._stuck_at_bay). It covers what the U1's unload of the
# head already pulled back (about 40-80 mm on the bench) and the gear-to-sensor stretch.
EJECT_TAIL_SLACK_MM = 150.0
# An eject's unload run again (EJECT_AGAIN) that ends stuck within this much of its own travel
# (mm) did not move the filament: a failure, never the tail at the bay.
EJECT_RERUN_MIN_MM = 30.0
# How long (s) an eject refused as busy (the stopped follow winding down) is retried.
EJECT_RETRY_S = 5.0
# The wait for the probe and the feed back is at least twice their travel time plus this margin.
EJECT_MARGIN_S = MOVE_MARGIN_S
# ACEK_EJECT WAIT=1 waits at most this long (s) for the decision of the eject.
EJECT_WAIT_MAX_S = 60.0
FEED_BACK_MIN_MM = 0.5
# The final events that leave a lane in error (kinds 7 to 13 of ace2k).
LANE_ERROR_KINDS = (
    "stuck",
    "tangled",
    "motor_stalled",
    "timeout",
    "assist_stall",
    "assist_overrun",
    "unload_incomplete",
)
# The kinds that an unload move can end with in error (deliberately not the full list of ace2k).
EJECT_UNLOAD_FAIL_KINDS = {
    "stuck",
    "tangled",
    "motor_stalled",
    "timeout",
    "unload_incomplete",
    "runout",
}
EJECT_FREE_KINDS = ("done", "blocked")
# The channel states of the U1 at rest, by the names of its constants. An eject is allowed only in
# one of them. Every other state is a flow under way on the head: a step of a preload, a load, an
# unload or a manual feed, the U1 waiting inside a flow (unload_heat_finish, the stage finishes of
# the manual flow), its test state, or a state that a later U1 adds.
EJECT_REST_STATES = (
    "FEED_STA_NONE",
    "FEED_STA_INITED",
    "FEED_STA_WAIT_INSERT",
    "FEED_STA_PRELOAD_FINISH",
    "FEED_STA_PRELOAD_FAIL",
    "FEED_STA_LOAD_FINISH",
    "FEED_STA_LOAD_FAIL",
    "FEED_STA_UNLOAD_FINISH",
    "FEED_STA_UNLOAD_FAIL",
    "FEED_STA_MANUAL_FINISH",
    "FEED_STA_MANUAL_FAIL",
    "FEED_STA_MANUAL_PREPARE_FAIL",
    "FEED_STA_MANUAL_EXTRUDE_FAIL",
    "FEED_STA_MANUAL_FLUSH_FAIL",
)
# The U1's own unload of a head, as its screen and its print-end unload start it.
U1_UNLOAD_SCRIPT = (
    "AUTO_FEEDING EXTRUDER={head} UNLOAD=1 STAGE=prepare\n"
    "AUTO_FEEDING EXTRUDER={head} UNLOAD=1 STAGE=doing"
)


class HeadForget(Protocol):
    """The head watch of the adapter (ace2k_u1_heads.HeadWatch), as far as the ejects use it."""

    def forget_head(self, slot: Slot, cause: str, tail: str, state: str = ...) -> None: ...


# An eject that took the lane in a shared head out of its bay leaves no lane known in the head
# (HeadWatch.forget_head).
EJECTED_CAUSE = "its lane was ejected"
EJECTED_STATE = "no lane is known in it now"
EJECTED_TAIL = "a print or a swap finds it, or name it with ACEK_LANE_ACTIVE"


class EjectAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the ejects use it."""

    @property
    def reactor(self) -> Reactor: ...
    @property
    def units(self) -> Coordinator: ...
    @property
    def gcode(self) -> GCode | None: ...
    @property
    def follow(self) -> Follow: ...
    @property
    def head_watch(self) -> HeadForget: ...
    @property
    def feed_speed_mm_s(self) -> float: ...
    @property
    def head_budget_mm(self) -> float: ...
    @property
    def eject_probe_mm(self) -> float: ...
    @property
    def eject_probe_speed(self) -> float: ...
    @property
    def eject_settle_s(self) -> float: ...
    def lane_text(self, h: Hooked) -> str: ...
    def lane_args(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...
    def find(self, lane: int, unit: Unit | None = None) -> Hooked | None: ...
    def in_print(self) -> bool: ...
    def print_end_unloads(self, head: int) -> bool: ...
    def print_state(self) -> str | None: ...
    def lane_status(self, h: Hooked, eventtime: float | None = None) -> LaneView: ...
    def lane_view(
        self, h: Hooked, eventtime: float | None = None
    ) -> tuple[LaneView, bool, int | None]: ...
    def head_set(self, h: Hooked) -> bool: ...
    def head_holds(self, h: Hooked) -> bool: ...
    def parked_known(self, h: Hooked) -> bool: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    def set_current(self, slot: Slot, h: Hooked | None) -> None: ...
    def stop(self, h: Hooked, why: str, lane: LaneView | None = None) -> None: ...
    def halt(self, h: Hooked, lane: LaneView | None = None) -> None: ...
    def started(self, h: Hooked, move: FeedMove) -> None: ...
    def get_loaded_states(self) -> set[str]: ...
    def get_rest_states(self) -> set[str]: ...
    @property
    def material_refusal(self) -> Callable[[Hooked], str | None]: ...


class EjectRefused(Exception):  # noqa: N818 — a refusal, read by ACEK_EJECT as its G-code error
    """ACEK_EJECT was refused before anything moved.

    The text says why.
    """


class Ejector:
    """The ejects of one adapter: a job per lane, the unit unloads that end them, their timers."""

    def __init__(self, adapter: EjectAdapter) -> None:
        self.adapter = adapter
        # All tables are keyed by lane key. In order they hold: the running job; the unit unload
        # whose final event is to come; the filament that earlier unloads took out (for a head
        # farther than one unload budget, see _eject_incomplete); the length asked of the current
        # unload; the timer of an eject still to start; and the travel of a free probe, for the unit
        # unload after it (its early "stuck" is the net's, once; the next unload has no net).
        # eject_ends holds the lanes whose eject the U1's end unload of a print started (see
        # _eject_incomplete).
        self.eject_jobs: dict[LaneKey, EjectJob] = {}
        self.ejects: dict[LaneKey, FeedMove] = {}
        self.eject_spent: dict[LaneKey, float] = {}
        self.eject_asked: dict[LaneKey, float] = {}
        self.eject_timers: dict[LaneKey, object] = {}
        self.eject_nets: dict[LaneKey, float] = {}
        self.eject_ends: set[LaneKey] = set()
        # Each EjectJob step (job.step) and its method, which returns the timer's next waketime.
        self.steps: dict[str, Callable[[EjectJob, float], float]] = {
            STEP_BEGIN: self._step_begin,
            STEP_PROBE: self._step_probe,
            STEP_PROBE_WAIT: self._step_probe_wait,
            STEP_SETTLE: self._step_settle,
            STEP_BACK: self._step_back,
            STEP_BACK_WAIT: self._step_back_wait,
            STEP_BACK_SETTLE: self._step_back_settle,
            STEP_U1: self._step_u1,
            STEP_NET: self._step_net,
        }

    def cancel_eject(self, h: Hooked) -> None:
        """Cancel the unit unload of an eject that is still to start.

        With no job and no unit unload of the eject left, the eject is over: an eject that the user
        asked for did not end well, so the lane keeps its spool record.
        """
        self._cancel_start(h)
        if h.key not in self.eject_jobs and h.key not in self.ejects:
            h.eject_by_user = False

    def _cancel_start(self, h: Hooked) -> None:
        """Unregister the timer that starts the unit unload of an eject, if there is one."""
        timer = self.eject_timers.pop(h.key, None)
        if timer is not None:
            self.adapter.reactor.unregister_timer(timer)

    def drop_eject(self, h: Hooked) -> None:
        """Cancel an eject still to start, and drop what its earlier unloads kept.

        A unit unload of the eject that still runs keeps all of it (the travel spent, the length
        asked, its mark of an eject that the user asked for): a U1 failure on the head is not that
        unload's end, which is judged with them (on_final).
        """
        self.cancel_eject(h)
        self._eject_forget(h, keep=h.key in self.ejects)

    def schedule_eject(
        self,
        h: Hooked,
        net: float | None = None,
        spent: float = 0.0,
        clear: bool = False,
        length: float = 0.0,
    ) -> None:
        """Start the eject, which is the unit's unload (not waited for).

        It runs from a reactor timer, and not while the U1's lock is held. Right after the stop of
        the follow the unit may refuse it as busy, so it is retried every BUSY_RETRY_POLL_S for up
        to EJECT_RETRY_S. ``net`` is the travel of the free probe when the unload follows one (its
        early "stuck" is the net's). ``spent`` is what the earlier unloads took out. ``clear``
        clears the lane error first. ``length`` is the budget of this unload (see
        _eject_incomplete), 0 for the unit's own budget. A new eject reads whether the U1's end
        unload of a print takes this head (eject_ends). An unload run again (``clear``) keeps
        what its eject read. It is not started, at any of its tries, while the U1 runs a flow on
        the head.
        """
        # A lane has one completion slot. An earlier eject can no longer end, so it is replaced.
        self.ejects.pop(h.key, None)
        self.eject_nets.pop(h.key, None)
        self.eject_spent[h.key] = spent
        # The length asked: this unload's, or the unit's own budget (its longest move).
        self.eject_asked[h.key] = length or move_max_mm(h)
        if not clear:
            self.eject_ends.discard(h.key)
            if self.adapter.print_end_unloads(h.head):
                self.eject_ends.add(h.key)
        self._cancel_start(h)
        give_up = self.adapter.reactor.monotonic() + EJECT_RETRY_S
        n = self.adapter.lane_text(h)

        to_clear = [clear]  # cleared once, and not again at each busy retry

        def try_eject(eventtime: float) -> float:
            if clear and self.u1_flow(h):
                self._eject_forget(h)
                self.adapter.say(
                    f"ace2k_u1: lane {n} eject not started — the U1 is loading/unloading this head"
                    f" — ACEK_CLEAR {self.adapter.lane_args(h)}"
                )
                self._cancel_start(h)
                return self.adapter.reactor.NEVER
            try:
                if to_clear[0]:
                    h.api.clear(h.lane)
                    to_clear[0] = False
                move = h.api.start_move(h.lane, "unload", length, self.adapter.feed_speed_mm_s)
            except Exception as err:
                busy = getattr(err, "reason", None) == "busy"
                if busy and eventtime + BUSY_RETRY_POLL_S <= give_up:
                    return eventtime + BUSY_RETRY_POLL_S
                self._eject_forget(h)
                # The lane is still in error if its clear failed.
                hint = f" — ACEK_CLEAR {self.adapter.lane_args(h)}" if to_clear[0] else ""
                self.adapter.say(f"ace2k_u1: lane {n} eject not started — {err}{hint}")
            else:
                self.ejects[h.key] = move
                if net is not None:
                    self.eject_nets[h.key] = net
                self.adapter.started(h, move)
            self._cancel_start(h)
            return self.adapter.reactor.NEVER

        self.eject_timers[h.key] = self.adapter.reactor.register_timer(
            try_eject, self.adapter.reactor.monotonic()
        )

    def _eject_forget(self, h: Hooked, keep: bool = False) -> None:
        """Drop what the unloads of an eject kept: the travel spent, the length asked, its end.

        The eject is over, so it is no longer one that the user asked for (h.eject_by_user). A
        caller that needs that mark reads it first. With keep (a unit unload of the eject still
        runs), nothing is dropped.
        """
        if keep:
            return
        h.eject_by_user = False
        self.eject_spent.pop(h.key, None)
        self.eject_asked.pop(h.key, None)
        self.eject_ends.discard(h.key)

    def start_eject(
        self, lane: int, unit: Unit | None = None, for_screen: bool = False, asked: bool = False
    ) -> EjectJob:
        """Start ACEK_EJECT for a lane (unit None: the only unit in use) and return its EjectJob.

        EjectRefused is raised, with nothing moved, if the lane is in use by a print, busy, in
        error, has no filament in its bay, or has an eject running. The job runs from its own
        reactor timer. With the head sensor clear, the follow goes off and the unit unloads (the
        eject after a U1 unload). With it set, a rollback probes past the slack and stops on a taut
        plunger. A free filament gets the same unload, with the safety net watching its start. A
        filament held by the head gear is fed back by the probe travel, then the U1 unloads the
        head, and its end brings the eject. With for_screen (the Unload of the U1's screen, see
        ace2k_u1_screen_unload), a held filament is only fed back: the U1's unload that the screen
        asked for is the one that runs. With asked (ACEK_EJECT, from the page or the console), the
        user takes the spool out: once the unit's own unload of the eject ends, well or in error,
        the spool record of the lane goes when the bay reads empty (see _arm_spool_drop). An eject
        that never reached that unload, and the automatic ejects, keep it.
        """
        h = self.adapter.find(lane, unit)
        if h is None:
            n = lane + 1 if unit is None else self.adapter.units.lane_text(unit, lane)
            raise EjectRefused(f"ace2k_u1: lane {n} eject refused: the lane is not hooked")
        why = self.eject_refusal(h)
        if why is not None:
            raise EjectRefused(f"ace2k_u1: lane {self.adapter.lane_text(h)} eject refused: {why}")
        job = EjectJob(h)
        job.for_screen = for_screen
        h.eject_by_user = asked and not for_screen
        h.end_eject_drop()
        self.eject_jobs[h.key] = job

        def run(eventtime: float, job: EjectJob = job) -> float:
            return self._run_eject(job, eventtime)

        job.timer = self.adapter.reactor.register_timer(run, self.adapter.reactor.NOW)
        return job

    def eject_running(self, h: Hooked) -> bool:
        """True when an eject of the lane runs (its job, its start timer or its unit unload)."""
        return h.key in self.eject_jobs or h.key in self.eject_timers or h.key in self.ejects

    def moving_lane(self, h: Hooked, mode: object) -> bool:
        """True while an eject moves the lane: from its probe to its feed back, or the unit's
        unload while the lane's mode (its status) reads unloading."""
        job = self.eject_jobs.get(h.key)
        if job is not None and job.step in EJECT_MOVING_STEPS:
            return True
        return h.key in self.ejects and mode == "unloading"

    def eject_refusal(self, h: Hooked) -> str | None:
        """Return why ACEK_EJECT may not start on the lane now, or None."""
        n = self.adapter.lane_text(h)
        if self.eject_running(h):
            return "an eject is already running"  # the unit unload may still be in flight
        try:  # not lane_or_none, because the refusal text carries the error
            lane = self.adapter.lane_status(h)
        except Exception as err:
            logging.exception("ace2k_u1: lane %s status", n)
            return f"the lane's status cannot be read — {err}"
        return self._lane_refusal(h, lane)

    def _lane_refusal(self, h: Hooked, lane: LaneView) -> str | None:
        """Return the reasons of eject_refusal from the status of the lane, or None."""
        mode = lane.get("mode")
        idle = mode in (None, "idle") and not h.armed
        if self.adapter.in_print() and (not idle or self._eject_head_holds(h)):
            return "in use by the print"
        if mode == "error":
            return f"the lane is in error — ACEK_CLEAR {self.adapter.lane_args(h)} first"
        if not idle and not h.armed and mode not in ARMED_MODES:
            return f"the lane is busy ({mode})"
        if lane.get("insert") is not True:
            return "no filament in the bay"
        if self.u1_flow(h):
            return "the U1 is loading/unloading this head"
        return None

    def u1_flow(self, h: Hooked) -> bool:
        """True when the U1 runs a flow on the channel of the head.

        It never raises. The channel is in a flow when it is taken or its state is not in
        EJECT_REST_STATES. An unreadable channel counts as in a flow (the eject is refused, the safe
        side).
        """
        if h.cold_unload:
            return False  # the screen's cold unload (ace2k_u1_screen_unload) is the eject's own
        try:
            if h.ff.channel_active == h.ch:
                return True
            return h.ff.channel_state[h.ch] not in self.adapter.get_rest_states()
        except Exception:
            logging.exception("ace2k_u1: lane %s channel state", self.adapter.lane_text(h))
            return True

    def _eject_recheck(self, job: EjectJob) -> str | None:
        """Recheck before each step that moves (probe, feed back, unload of the U1).

        Return the reason to fail the eject if a print started or the U1 started a flow on the head,
        "empty" if the head sensor is clear by now (the plain unit unload), else None.
        """
        if self.adapter.in_print():
            return "a print started"
        if self.u1_flow(job.h):
            return "the U1 is loading/unloading this head"
        if not self._eject_head_holds(job.h):
            return "empty"
        return None

    def _eject_head_holds(self, h: Hooked) -> bool:
        """Return the head sensor as the eject reads it for h.

        A lane parked outside its shared head, with another lane known in it, is never what the
        sensor sees. Its eject is the plain unit unload (allowed in a print, never the U1 unload).
        With no lane known in the head, the sensor may be h's, and the probe tells.
        """
        return not self.adapter.parked_known(h) and self.adapter.head_holds(h)

    def known_loaded(self, h: Hooked) -> bool:
        """True when h is known loaded in its head, through the head's gear.

        It never raises. The U1 holds the head's channel loaded (a loaded state, as the follow
        reads it), no other lane is known in the head (on a shared head with none known, after a
        restart, the lane may well be in it), and the head's sensor sees filament. The eject of
        such a lane needs the U1's unload of the head, which heats it. ACEK_EJECT refuses a lane
        with no material set up front only then (MaterialGate.eject_refused). Any other eject
        finds out with its probe.
        """
        if self.adapter.parked_known(h):
            return False  # another lane is known in the head
        try:
            loaded = h.ff.channel_state[h.ch] in self.adapter.get_loaded_states()
        except Exception:
            if not h.channel_unread:  # logged once per episode, as the status reads it often
                h.channel_unread = True
                logging.exception("ace2k_u1: lane %s channel state", self.adapter.lane_text(h))
            return False  # the eject itself refuses an unreadable channel (u1_flow)
        h.channel_unread = False
        return loaded and self.adapter.head_holds(h)

    def _eject_rechecked(self, job: EjectJob) -> float | None:
        """Act on _eject_recheck.

        Return the waketime if the eject ended, else None.
        """
        why = self._eject_recheck(job)
        if why is None:
            return None
        if why == "empty":
            self.adapter.say(EJECT_SAY["head_now_empty"].format(n=self.adapter.lane_text(job.h)))
            return self._eject_unload(job)
        # A print or a U1 flow on a head whose follow the eject took. The follow is given back, as
        # the lane is at rest here (nothing moved yet, or the feed back ended done or blocked).
        self._eject_restore_follow(job)
        return self._eject_fail(job, why)

    def _eject_restore_follow(self, job: EjectJob) -> None:
        """Arm again the follow that the eject stopped, as the standing re-arm would.

        It never raises.

        It needs: the lane had the follow (wanted or armed at begin), the channel of the U1 holds
        the head loaded, the head sensor still sees filament, and the lane is at rest with no error
        and a filament in its bay. A report may still show the stopped follow, so the busy refusal
        of its winding down is waited out. The hold stays on across the arm, so the standing re-arm
        cannot run then.
        """
        h = job.h
        if not job.had_follow:
            return
        try:
            if h.ff.channel_state[h.ch] not in self.adapter.get_loaded_states():
                return
            lane = self.adapter.lane_status(h)
            mode = lane.get("mode")
            at_rest = mode in (None, "idle") or mode in ARMED_MODES
            if not (
                at_rest
                and lane.get("error") is None
                and lane.get("insert") is True
                and self.adapter.head_holds(h)
            ):
                return
            h.hold = True
            try:
                self.adapter.follow.arm(h, busy_wait_s=NUDGE_BUSY_S)
            finally:
                h.hold = False
        except Exception:
            logging.exception("ace2k_u1: lane %s follow after the eject", self.adapter.lane_text(h))

    def wait_eject(
        self, job: EjectJob, limit_s: float = EJECT_WAIT_MAX_S, say: bool = True
    ) -> None:
        """Wait for the decision of the eject (ACEK_EJECT WAIT=1), at most limit_s.

        It never waits for the U1 unload, which needs the G-code lock that this command holds.
        With say, an eject still to decide at the end of the wait is said.
        """
        give_up = self.adapter.reactor.monotonic() + limit_s
        while not job.decided and self.adapter.reactor.monotonic() < give_up:
            self.adapter.reactor.pause(self.adapter.reactor.monotonic() + PUSH_POLL_S)
        if not job.decided and say:
            self.adapter.say(
                EJECT_SAY["no_decision"].format(n=self.adapter.lane_text(job.h), s=limit_s)
            )

    def _run_eject(self, job: EjectJob, eventtime: float) -> float:
        """Run the eject timer.

        It never raises: an unexpected error ends the eject. The U1 unload waits for the G-code lock
        in run_script, so the reactor may re-enter this timer. The re-entered call leaves at once.
        """
        if job.running:
            return self.adapter.reactor.NEVER
        job.running = True
        try:
            return self._eject_step(job, eventtime)
        except Exception as err:
            logging.exception("ace2k_u1: lane %s eject", self.adapter.lane_text(job.h))
            return self._eject_fail(job, error_line(err))
        finally:
            job.running = False

    def _eject_start(
        self,
        job: EjectJob,
        mode: str,
        length: float,
        now: float,
        stop_when: frozenset[str] = frozenset(),
    ) -> tuple[str, FeedMove | str | None]:
        """Start the probe or the feed back.

        ``stop_when`` names the stops that the unit makes itself (EJECT_UNIT_STOP for the probe on
        a unit that has it). Empty, the start is sent as on a unit without them. Return ("started",
        move), ("retry", None) while a busy refusal may be retried, or ("failed", why).
        """
        try:
            # The tip is in the head, so free_tip is False. (A rollback ignores it.)
            move = start_armed(job.h, mode, length, self.adapter.eject_probe_speed, stop_when)
        except Exception as err:  # FeedRefused (with a reason), ValueError, RuntimeError
            busy = getattr(err, "reason", None) == "busy"
            if busy and now + BUSY_RETRY_POLL_S <= job.give_up:
                return "retry", None
            return "failed", error_line(err)
        self.adapter.started(job.h, move)
        job.move = move
        # A backstop: the unit ends a move that passes its own deadline (see unit_move_wait_s).
        # The probe is a roll back, so its wait counts the tail; the feed back is a forward move.
        speed = self.adapter.eject_probe_speed
        reverse = mode == "rollback"
        job.deadline = now + unit_move_wait_s(job.h, length, speed, EJECT_MARGIN_S, reverse=reverse)
        return "started", move

    def _eject_step(self, job: EjectJob, now: float) -> float:
        """Run one call of the timer through the method of the step (see self.steps).

        A job that is done, or at a step not in the table, is never called again.
        """
        step = self.steps.get(job.step)
        return self.adapter.reactor.NEVER if step is None else step(job, now)

    def _step_begin(self, job: EjectJob, now: float) -> float:
        """Turn the follow off and read the head sensor.

        A lane parked outside its head, or a head that holds nothing, gets the plain unit unload.
        Otherwise the probe starts at once.
        """
        h = job.h
        n = self.adapter.lane_text(h)
        job.had_follow = h.follow_wanted or h.armed
        h.follow_wanted = False  # the eject ends the standing of the follow
        h.hold = True  # and keeps the standing re-arm off while it runs the lane
        lane = self.adapter.lane_status(h)
        if h.armed or lane.get("mode") in ARMED_MODES:
            self.adapter.stop(h, "the eject", lane)
        if self.adapter.parked_known(h):
            self.adapter.say(EJECT_SAY["parked"].format(n=n))
            return self._eject_unload(job)
        if not self.adapter.head_holds(h):
            self.adapter.say(EJECT_SAY["head_empty"].format(n=n))
            return self._eject_unload(job)
        job.step = STEP_PROBE
        job.give_up = now + EJECT_RETRY_S
        return self.steps[STEP_PROBE](job, now)

    def _step_probe(self, job: EjectJob, now: float) -> float:
        """Start the rollback of the probe.

        A busy refusal is retried until job.give_up.
        """
        ended = self._eject_rechecked(job)
        if ended is not None:
            return ended
        # Go past the slack only where a taut plunger can stop the probe: a proven link whose
        # reports can be told apart. Otherwise use the short probe, judged held at its end.
        view = self._eject_view(job)
        proven = view is not None and view[1] and view[2] is not None
        length = (
            self.adapter.eject_probe_mm
            if proven
            else min(self.adapter.eject_probe_mm, EJECT_PROBE_SHORT_MM)
        )
        # A unit that can stop on its plunger judges the taut plunger itself, so the host does not
        # watch it (see EJECT_TAUT_POLL_S).
        unit = unit_stops(job.h)
        job.watch = proven and not unit
        stop_when = EJECT_UNIT_STOP if unit else frozenset()
        state, got = self._eject_start(job, "rollback", length, now, stop_when)
        if state == "retry":
            return now + BUSY_RETRY_POLL_S
        if state == "failed":
            return self._eject_fail(job, f"the probe not started — {got}")
        job.step = STEP_PROBE_WAIT
        job.fresh_at = now + TAUT_READINGS * EJECT_TAUT_POLL_S
        job.taut = TautWatch(job.fresh_at, view[2]) if view is not None and job.watch else None
        return now + EJECT_TAUT_POLL_S

    def _step_probe_wait(self, job: EjectJob, now: float) -> float:
        """Judge the end of the probe while it runs, stopping it on a taut plunger or lost link."""
        event = cast("FeedMove", job.move).result()  # started in _eject_start
        if event is not None:
            return self._eject_judge(job, event, now)
        if job.watch and job.stop_why is None:
            job.stop_why = self._probe_watch(job, now)
        if job.stop_why is not None and not job.stop_sent:
            self._probe_stop(job)
        return self._await_move(
            job, now, EJECT_TAUT_POLL_S, "the probe's timeout", "the probe got no end"
        )

    def _step_settle(self, job: EjectJob, now: float) -> float:
        """Judge the plunger after the settle of the probe (see _eject_settled)."""
        wait = self._await_settle(job, now)
        return wait if wait is not None else self._eject_settled(job, now)

    def _step_back(self, job: EjectJob, now: float) -> float:
        """Start the feed back.

        A busy refusal is retried until job.give_up.
        """
        ended = self._eject_rechecked(job)
        if ended is not None:
            return ended
        state, got = self._eject_start(job, "feed", job.back_mm, now)
        if state == "retry":
            return now + BUSY_RETRY_POLL_S
        if state == "failed":
            return self._eject_fail(job, f"the feed back not started — {got}")
        job.step = STEP_BACK_WAIT
        return now + PUSH_POLL_S

    def _step_back_wait(self, job: EjectJob, now: float) -> float:
        """Watch the feed back.

        Done or blocked settles the plunger; another end fails the eject.
        """
        h = job.h
        event = cast("FeedMove", job.move).result()  # started in _eject_start
        if event is None:
            return self._await_move(
                job, now, PUSH_POLL_S, "the feed back's timeout", "the feed back got no end"
            )
        kind = event.get("kind")
        if kind not in EJECT_FREE_KINDS and kind not in LANE_ERROR_KINDS:
            return self._eject_fail(job, f"the feed back ended {kind}")
        if kind in LANE_ERROR_KINDS:
            h.api.clear(h.lane)
        # The plunger is read after the same settle as the probe, as an older report says nothing
        # about the buffer.
        job.step = STEP_BACK_SETTLE
        job.settle_at = job.fresh_at = now + self.adapter.eject_settle_s
        return job.settle_at

    def _step_back_settle(self, job: EjectJob, now: float) -> float:
        """Say a plunger not at rest after the feed back settled, then run the U1 unload."""
        wait = self._await_settle(job, now)
        if wait is not None:
            return wait
        if not self._plunger_at_rest(job):
            # The unload of the U1 arms the follow at its tip forming, which settles the buffer.
            n = self.adapter.lane_text(job.h)
            self.adapter.say(EJECT_SAY["plunger_not_at_rest"].format(n=n))
        job.step = STEP_U1
        return self._eject_u1(job)

    def _step_u1(self, job: EjectJob, _now: float) -> float:
        """Run the unload of the U1 for the head (see _eject_u1)."""
        return self._eject_u1(job)

    def _step_net(self, job: EjectJob, now: float) -> float:
        """Start the safety net: say its line, then take the held path with what left the unit."""
        # The end of the unit unload, whose travel is a number (see _eject_net).
        event = cast("FeedEvent", job.probe_event)
        mm = abs(cast(float, event.get("filament_mm")))
        self.adapter.say(EJECT_SAY["net"].format(n=self.adapter.lane_text(job.h), mm=mm))
        return self._eject_held(job, None, now, back_mm=job.net_back)

    def _await_move(
        self, job: EjectJob, now: float, poll_s: float, timeout_text: str, no_end_text: str
    ) -> float:
        """Poll a started move with no final event yet, until its deadline.

        Past the deadline the move is stopped (``timeout_text``) and the eject fails
        (``no_end_text``).
        """
        if now < job.deadline:
            return now + poll_s
        self.adapter.stop(job.h, timeout_text)
        return self._eject_fail(job, no_end_text)

    def _await_settle(self, job: EjectJob, now: float) -> float | None:
        """Return the end of the settle while it is still to come, else None.

        None means the plunger can be read now.
        """
        return job.settle_at if now < job.settle_at else None

    def _eject_judge(self, job: EjectJob, event: FeedEvent, now: float) -> float:
        """Judge the end of the probe.

        A stop, or an end other than done, blocked or a lane error, and a travel that is not a
        number, end the eject. The exceptions are the unit's own "held" end and a stop of this
        adapter on a taut plunger. Both are held, as is any end after that stop, and a lane error.
        Done or blocked waits eject_settle_s for the plunger.
        """
        kind = event.get("kind")
        if kind in EJECT_UNIT_STOP:
            # The unit ended the probe itself: the plunger taut, the filament standing still.
            job.probe_event = event
            return self._eject_held(job, EJECT_TAUT_WHY, now)
        if job.stop_why and (kind == "stopped" or kind in EJECT_FREE_KINDS + LANE_ERROR_KINDS):
            # A stop by this adapter (taut plunger, lost link), or a unit end that beat it, counts
            # as held, whatever travel it carries.
            job.probe_event = event
            return self._eject_held(job, job.stop_why, now)
        if not finite(event.get("filament_mm")):
            return self._eject_fail(job, "the probe reported no filament travel")
        if kind not in EJECT_FREE_KINDS and kind not in LANE_ERROR_KINDS:
            return self._eject_fail(job, f"the probe ended {kind}")
        job.probe_event = event
        if kind in LANE_ERROR_KINDS:
            return self._eject_held(job, "the probe met resistance", now)
        job.step = STEP_SETTLE
        job.settle_at = job.fresh_at = now + self.adapter.eject_settle_s
        return job.settle_at

    def _eject_view(self, job: EjectJob) -> tuple[LaneView, bool, int | None] | None:
        """Return _lane_view for the eject, or None if the status cannot be read (logged once).

        It never raises.
        """
        try:
            return self.adapter.lane_view(job.h)
        except Exception:
            if not job.unread_said:
                job.unread_said = True
                logging.exception("ace2k_u1: lane %s plunger unread", self.adapter.lane_text(job.h))
            return None

    def _plunger_reading(self, job: EjectJob) -> LaneView | None:
        """Return the lane status for the judgement after a settle, or None if it cannot be trusted.

        It is not trusted before job.fresh_at (a report from before the motion may still be the last
        one), on a link that is not proven (a proven link sends its switches at 10 Hz, so a settle
        of 200 ms or more spans two reports), or when the status cannot be read.
        """
        if self.adapter.reactor.monotonic() < job.fresh_at:
            return None
        view = self._eject_view(job)
        if view is None or not view[1]:
            return None
        return view[0]

    def _plunger_at_rest(self, job: EjectJob) -> bool:
        """True when the plunger is at rest (rest, not pushed) after the settle.

        An untrusted reading counts as not at rest (held, the safe side).
        """
        lane = self._plunger_reading(job)
        return lane is not None and lane.get("rest") is True and lane.get("pushed") is not True

    def _probe_watch(self, job: EjectJob, now: float) -> str | None:
        """Poll a watched probe once, with one status read.

        Return "link lost" if the link is no longer proven or its report count is gone (no taut
        reading could stop the probe any more, so it counts as held). Return "plunger taut" once
        TAUT_READINGS different reports in a row, each newer than the probe start and past
        job.fresh_at, read the plunger pushed. Otherwise return None. An unreadable status decides
        nothing (logged once).
        """
        view = self._eject_view(job)
        if view is None:
            return None
        lane, proven, reports = view
        if not proven or reports is None:
            return "link lost"
        taut = cast(TautWatch, job.taut)  # set at the start of a watched probe
        if not taut.fresh(now, reports):
            return None  # a stale report (the count starts again), or the same report read again
        return EJECT_TAUT_WHY if taut.taut(lane.get("pushed") is True) else None

    def _probe_stop(self, job: EjectJob) -> None:
        """Stop the probe for job.stop_why (a taut plunger, or a lost link).

        A plunger pulled to its taut end after the slack is taken up means the head holds the tip,
        and the probe stops before the unit's own fault. A stop that raises is tried again on the
        next polls, EJECT_STOP_TRIES in all. If the stop still fails, the unit's own end decides,
        and the result counts as held.
        """
        h = job.h
        if job.stop_tries >= EJECT_STOP_TRIES:
            return
        try:
            self.adapter.halt(h)
            job.stop_sent = True
        except Exception:
            job.stop_tries += 1
            logging.exception(
                "ace2k_u1: lane %s probe stop (%s), try %d",
                self.adapter.lane_text(h),
                job.stop_why,
                job.stop_tries,
            )
        finally:
            h.armed = False

    def _eject_settled(self, job: EjectJob, now: float) -> float:
        """Judge the plunger after the settle.

        At rest means the filament came back whole: free, so the unit unloads. Moved (pushed, or off
        its rest) means the head gear held the tip: held. A print, a U1 flow or a cleared head
        during the settle is handled first (see _eject_rechecked).
        """
        ended = self._eject_rechecked(job)
        if ended is not None:
            return ended
        n = self.adapter.lane_text(job.h)
        if self._plunger_at_rest(job):
            self.adapter.say(EJECT_SAY["filament_free"].format(n=n))
            event = cast("FeedEvent", job.probe_event)  # the end of the probe (see _eject_judge)
            return self._eject_unload(job, net=abs(event["filament_mm"]))
        return self._eject_held(job, "plunger moved", now)

    def _eject_held(
        self, job: EjectJob, why: str | None, now: float, back_mm: float | None = None
    ) -> float:
        """Handle a filament that the head holds: clear the lane, feed back, then unload.

        The lane is cleared if the probe (or, for the net, the unit unload) ended in error.

        ``why`` None means the line was said already (the net's). ``back_mm`` None means the
        filament travel of the probe, read by the encoder at the unit. The motor travel exceeds it
        once the drive slips, so the motor travel is used only when the end has no filament figure
        (a taut stop, before any slip). After the unit's own "held" end, the motor also ran its
        held window with the filament still, so that window's motor (held_window_ms at the probe's
        speed) is taken off. With neither figure, nothing is fed back.
        """
        h = job.h
        event = cast("FeedEvent", job.probe_event)  # the end of the probe, or of the net unload
        if why is not None:
            self.adapter.say(EJECT_SAY["held"].format(n=self.adapter.lane_text(h), why=why))
        if event.get("kind") in LANE_ERROR_KINDS:
            h.api.clear(h.lane)
        if back_mm is None:
            travel = event.get("filament_mm")
            motor = event.get("motor_mm")
            back_mm = abs(travel) if finite(travel) else abs(motor) if finite(motor) else 0.0
            if not finite(travel) and event.get("kind") in EJECT_UNIT_STOP:
                back_mm = max(0.0, back_mm - self._held_window_mm(h))
        job.back_mm = min(back_mm, move_max_mm(h))
        if job.back_mm < FEED_BACK_MIN_MM:
            job.step = STEP_U1
            return self._eject_u1(job)
        job.step = STEP_BACK
        job.give_up = now + EJECT_RETRY_S
        return now

    def _held_window_mm(self, h: Hooked) -> float:
        """The motor travel of the probe's held window: what the motor turned against a held tip
        before the unit ended the probe "held". 0 when the unit used its own window."""
        speed = self.adapter.eject_probe_speed
        window = held_window_ms(h, speed)
        return 0.0 if window is None else window * speed / 1000.0

    def _eject_unload(self, job: EjectJob, net: float | None = None) -> float:
        """Run the unit unload of the lane, retrying a busy refusal.

        This is the eject after a U1 unload. ``net`` is the filament travel after a "free" probe,
        for the safety net.
        """
        job.h.hold = False
        job.outcome = "cold"
        self.schedule_eject(job.h, net=net)
        self._eject_end(job)
        return self.adapter.reactor.NEVER

    def _eject_u1(self, job: EjectJob) -> float:
        """Run the U1 unload of the head, from this timer.

        Its G-code waits for the lock, so a WAIT=1 ACEK_EJECT has returned by then. Its
        unload_finish brings the eject. Its failure is the eject's line. Every held filament comes
        here, after the probe and after the net, once the feed back relaxed it. The U1's unload
        heats the head for the lane's material: a lane with no material set
        (adapter.material_refusal) fails the eject here instead, and nothing heats. The screen's
        Unload is not checked: its unload is the U1's own.
        """
        h = job.h
        ended = self._eject_rechecked(job)
        if ended is not None:
            return ended
        no_material = None if job.for_screen else self.adapter.material_refusal(h)
        if no_material is not None:
            return self._eject_fail(job, no_material)
        if job.for_screen:  # the screen's own unload of the U1 runs once the eject is decided
            job.outcome = "u1"
            self._eject_end(job)
            return self.adapter.reactor.NEVER
        job.step = STEP_DONE
        job.decided = True
        h.hold = False
        try:
            if self.adapter.gcode is None:
                raise RuntimeError("no G-code object")
            slot = self.adapter.slot_of(h)
            if parked_slot(slot, h):
                # No lane was known in the head and the probe found h held there. The head is handed
                # to it first, so the unload of the U1 acts on the lane of h (its channel hooks).
                self.adapter.units.hand_over(h.unit, h.lane, PAUSE_WAIT_S)
                self.adapter.set_current(slot, h)
            # The lane is logged once a second while the U1 unloads the head (UnloadWatch).
            watch = UnloadWatch(self.adapter.reactor, h, self.adapter.lane_text(h))
            watch.begin()
            try:
                self.adapter.gcode.run_script(U1_UNLOAD_SCRIPT.format(head=h.head))
            finally:
                watch.end()
        except Exception as err:
            return self._eject_fail(job, f"the U1's unload of e{h.head} — {err}")
        job.outcome = "head"  # the end of the U1's unload brings the unit unload
        self._eject_end(job)
        return self.adapter.reactor.NEVER

    def unload_starting(self, h: Hooked) -> bool:
        """True while the start of the unit's unload of an eject is still being tried."""
        return h.key in self.eject_timers

    def unload_started(self, h: Hooked) -> bool:
        """True when the unit's unload of an eject was started and has not ended yet."""
        return h.key in self.ejects

    def abandon(self, job: EjectJob, why: str) -> None:
        """End an eject that is still to decide: its lane stopped, then failed with why.

        Adapter.stop never raises: a stop that fails is its own notice.
        """
        self.adapter.stop(job.h, why)
        self._eject_fail(job, why)

    def _eject_fail(self, job: EjectJob, why: str) -> float:
        """End the eject short, with its line.

        A print or U1 flow found at a recheck has already given the follow back (see
        _eject_restore_follow), and the line says so. Any other failure leaves the follow off,
        because the state of the lane is not known well enough to pull on the filament. A head still
        loaded gets the follow back at its next load (load_finish arms it), and the line says so.
        """
        job.failed = why  # noqa: V101 — keeps EjectJob.failed counted as used once the probe helper is gone
        h = job.h
        line = f"ace2k_u1: lane {self.adapter.lane_text(h)} eject failed: {why}"
        if h.armed:
            line += "; its follow armed again"
        elif self.adapter.head_holds(h):
            line += f"; the follow stays off until e{h.head}'s next load"
        self.adapter.say(line)
        self._eject_end(job)
        return self.adapter.reactor.NEVER

    def _eject_end(self, job: EjectJob) -> None:
        """End the job. Unless the unit unload follows it (job.outcome "cold" or "head"), the eject
        is over, and an eject that the user asked for keeps the spool record of the lane."""
        if job.outcome not in ("cold", "head"):
            job.h.eject_by_user = False
        job.step = STEP_DONE
        job.decided = True
        job.h.hold = False
        if self.eject_jobs.get(job.h.key) is job:
            del self.eject_jobs[job.h.key]
        if job.timer is not None:
            self.adapter.reactor.unregister_timer(job.timer)
            job.timer = None

    def on_final(self, h: Hooked, event: FeedEvent) -> None:
        """Handle a final event of the lane of h, after the adapter's own handling.

        It acts on the end of the unit unload of an eject, when it is that unload's. Whether the
        lane's spool record goes with it is _arm_spool_drop's rule.
        """
        kind = event.get("kind")
        move = self.ejects.get(h.key)
        if move is None or event.get("seq") != move.seq:
            return
        del self.ejects[h.key]
        net = self.eject_nets.pop(h.key, None)
        if net is not None and self._eject_net(h, event, net):
            return
        end: str | None = None
        if kind == "unload_incomplete":
            end = self._eject_incomplete(h, event)
        elif kind == "stuck":
            end = self._stuck_at_bay(h, event)
        if end == EJECT_AGAIN:
            return
        # Ended "unloaded", or with the tail at the bay (EJECT_AT_BAY, taken out by hand next).
        took_out = kind == "unloaded" or end is not None
        # Both read before _eject_forget clears them (eject_ends among them), so the drop is
        # judged on the state that judged the end.
        by_user, judged = h.eject_by_user, self._judged_here(h)
        back = self.eject_spent.get(h.key, 0.0) + moved_mm(event)  # the whole eject's
        self._eject_forget(h)
        self._arm_spool_drop(h, by_user and judged)
        if took_out:
            self._head_emptied(h)
        if end is not None:  # this is EJECT_AT_BAY
            self._eject_at_bay(h, back)
            return
        if kind in EJECT_UNLOAD_FAIL_KINDS:
            n = self.adapter.lane_text(h)
            self.adapter.say(
                f"ace2k_u1: lane {n} eject failed: {kind} — ACEK_CLEAR {self.adapter.lane_args(h)}"
            )

    @staticmethod
    def _arm_spool_drop(h: Hooked, armed: bool) -> None:
        """The one rule for an eject's drop of the lane's spool record (h.eject_drop).

        It is armed (armed) at the end of the unit's own unload of an eject that the user asked
        for and that judged that end itself (_judged_here: no U1 flow on the head, no print),
        whatever the end: "unloaded", the tail at the bay, or any failure of the unload (such as
        unload_incomplete with the tip left on the bay's sensor, which the user then pulls out by
        hand). An unload run again or taken over by the safety net is not an end. Then the record
        and the mark of a forgotten tag go once the bay reads empty (GripWatch.bay_watch), seconds
        or minutes later. It is disarmed (Hooked.end_eject_drop) only by a real move of the lane:
        a move this adapter starts (Adapter.started), its mode out of idle and error in its status
        (a move of the unit, GripWatch.eject_drop_ends), or a load of the head that the U1 starts
        (hook_states). A filament put into the empty bay and a new spool record disarm it too. A
        push or a pull by hand never does. Anything else keeps the record: an eject that never
        reached the unit's unload, the automatic ejects, a failed eject whose bay stays full.
        """
        h.eject_drop = armed

    def _head_emptied(self, h: Hooked) -> None:
        """Forget the lane of a shared head once the eject took that lane out of its bay.

        The filament has left the head and the bay, so the head holds no lane it knows of. A head
        of one lane keeps naming its lane, as it does from the hook on. Under a print (the eject
        that the U1's end unload of a print started, eject_ends) the print's own flow owns the
        head, so nothing is forgotten.
        """
        if self.adapter.in_print():
            return
        slot = self.adapter.slot_of(h)
        if slot is not None and slot.shared and slot.current is h:
            self.adapter.head_watch.forget_head(slot, EJECTED_CAUSE, EJECTED_TAIL, EJECTED_STATE)

    def _eject_incomplete(self, h: Hooked, event: FeedEvent) -> str | None:
        """Return what an eject unload that ended unload_incomplete means.

        Return EJECT_AGAIN, EJECT_AT_BAY, or None (a failure, with its usual line). The result is
        None under a U1 flow on the head, and under a print, but for an eject that the U1's end
        unload of a print started (eject_ends). That one runs while the print still reads
        printing, and it ends as the eject from the page does, unless the print has paused.

        - EJECT_AGAIN: the unload carried the filament a whole move (see carried). The unload budget
          is spent, and this is not a tail past the drive gear: the head is farther from the bay
          than one unload (a head behind two splitters can be about 2 000 mm from the bay). The lane
          error is cleared and the unload starts again, while the eject total stays within
          head_budget_mm.
        - EJECT_AT_BAY: the unload is short of a whole move, its motor is short of the budget, and
          the head sensor is not on the filament of this lane. The tail is past the drive gear,
          under the sensor of the bay (see _eject_at_bay). With the sensor on it, the filament still
          runs to the head. With the motor through the budget, the unload ran out of length. Both
          fail.
        """
        if not self._judged_here(h):
            return None
        moved = moved_mm(event)
        cap = move_max_mm(h)
        asked = self.eject_asked.get(h.key, cap)
        if carried(asked, moved):
            return self._eject_again(h, moved, cap)
        return self._tail_at_bay(h, event, asked)

    def _judged_here(self, h: Hooked) -> bool:
        """Return True when the eject may read its unload's end itself (see _eject_incomplete).

        Not under a U1 flow on the head, and not under a print, but for an eject that the U1's end
        unload of a print started (eject_ends) while the print has not paused.
        """
        if self.u1_flow(h):
            return False
        at_end = h.key in self.eject_ends and self.adapter.print_state() != "paused"
        return not self.adapter.in_print() or at_end

    def _stuck_at_bay(self, h: Hooked, event: FeedEvent) -> str | None:
        """Return EJECT_AT_BAY for an unload that ended stuck with its tail at the bay, else None.

        Bench 2026-10-31: once the tail leaves the drive gear going back, the gear cannot pull it
        the last few cm off the bay's sensor (the gear sits about 50 mm past the inlet, the sensor
        10-20 mm past it), and the unit ends the unload stuck.

        The filament that came back is the whole eject's: this unload's and those before it
        (eject_spent, when an unload ran again). An unload run again that sticks within its first
        EJECT_RERUN_MIN_MM is a failure (the filament did not move), unless the whole eject has
        come back the lane's length to its head (see _rerun_stalled). It is the tail at the bay
        when:

        - the filament came back more than EJECT_NET_MM, past any hold of the head (a stuck within
          that is the safety net's, or a real failure);
        - the head sensor is not on the filament of this lane (as in _tail_at_bay);
        - when the lane's length to its head is known (its last push, h.push_mm), the filament came
          back at least that length less EJECT_TAIL_SLACK_MM. A stuck short of it is far from the
          bay, a real failure. With no known length, the two tests above decide.
        """
        if not self._judged_here(h):
            return None
        own = moved_mm(event)
        back = self.eject_spent.get(h.key, 0.0) + own
        if self._rerun_stalled(h, own) or back <= EJECT_NET_MM:
            return None
        if self._short_of_head(h, back):
            return None
        if self._eject_head_holds(h):
            return None
        return EJECT_AT_BAY

    def _eject_again(self, h: Hooked, moved: float, cap: float) -> str | None:
        """Schedule the unload again (EJECT_AGAIN) while the total is within head_budget_mm.

        Return None once the budget is spent, and the error stands.
        """
        spent = self.eject_spent.get(h.key, 0.0) + moved
        left = self.adapter.head_budget_mm - spent
        if left < INSIST_MIN_MM:
            return None  # the budget is spent, so the error stands
        logging.info(
            "ace2k_u1: lane %s eject's unload spent %.1f mm: unloading again",
            self.adapter.lane_text(h),
            moved,
        )
        self.schedule_eject(h, spent=spent, clear=True, length=min(cap, left))
        return EJECT_AGAIN

    def _short_of_head(self, h: Hooked, back: float) -> bool:
        """True when the lane's length to its head is known (its last push, h.push_mm) and the
        whole eject came back less than that length less EJECT_TAIL_SLACK_MM: far from the bay."""
        return h.push_known and h.push_mm > 0.0 and back < h.push_mm - EJECT_TAIL_SLACK_MM

    def _rerun_stalled(self, h: Hooked, own: float) -> bool:
        """True for an unload run again (eject_spent) that moved less than EJECT_RERUN_MIN_MM of
        its own while the whole eject is still short of the lane's known length to its head
        (_short_of_head): the filament did not move, a failure. With the whole eject past that
        length, the tail was already at the bay: it is the tail. With no known length, the other
        tests decide."""
        spent = self.eject_spent.get(h.key, 0.0)
        if spent <= 0.0 or own >= EJECT_RERUN_MIN_MM:
            return False
        return self._short_of_head(h, spent + own)

    def _tail_at_bay(self, h: Hooked, event: FeedEvent, asked: float) -> str | None:
        """Return EJECT_AT_BAY for a tail past the drive gear, else None.

        It is EJECT_AT_BAY if the unload motor stopped short of the move asked and the head sensor
        is not on the filament of this lane. An unload run again that brought back next to nothing
        is judged on the whole eject, as in _stuck_at_bay (_rerun_stalled).
        """
        motor = event.get("motor_mm")
        if not finite(motor):
            return None  # no motor travel to tell a tail from a spent budget
        if motor >= asked * CHUNK_CARRIED:
            return None
        if self._rerun_stalled(h, moved_mm(event)):
            return None
        if self._eject_head_holds(h):
            return None
        return EJECT_AT_BAY

    def _eject_at_bay(self, h: Hooked, moved: float) -> None:
        """Finish the eject for a tail past the drive gear, under the sensor of the bay.

        moved is what the whole eject brought back, every unload of it (as _stuck_at_bay judges).

        The tail is out of the reach of the unit, which is not a fault (see _eject_incomplete). The
        lane error is cleared and the operator is told to take the tail out. If nothing came back,
        the line says the tail was already there (an eject run again).
        """
        n = self.adapter.lane_text(h)
        if moved < 1.0:
            done = f"lane {n}: nothing came back: its tail is already"
        else:
            done = f"lane {n} ejected ({moved:.0f} mm back): its tail is"
        line = f"ace2k_u1: {done} past the unit's drive gear, at its bay: take it out by hand"
        try:
            h.api.clear(h.lane)
        except Exception as err:
            logging.exception("ace2k_u1: lane %s cleared after its eject", n)
            line += f" — ACEK_CLEAR {self.adapter.lane_args(h)} (its error not cleared: {err})"
        self.adapter.say(line)

    def _eject_net(self, h: Hooked, event: FeedEvent, probe_mm: float) -> bool:
        """Run the safety net, once per eject.

        Return True when it took over. It applies when the unit unload after a "free" probe ended in
        one of EJECT_NET_KINDS within EJECT_NET_MM of travel, and the head sensor was read and is
        still set. The probe took slack for freedom, and the head gear holds the tip. The net does
        not apply if the head sensor cannot be read, or if a print or a U1 flow is running on the
        head. The eject checks this before each step, and then the unload end is a real failure. The
        held path runs from an eject job's timer, never from this handler. The lane is cleared, the
        filament that left the unit (the probe_mm and the travel of the unload) is fed back, and the
        U1 unloads the head. The end of that unload brings the unit unload, with no net.
        """
        travel = event.get("filament_mm")
        if (
            event.get("kind") not in EJECT_NET_KINDS
            or not finite(travel)
            or abs(travel) > EJECT_NET_MM
        ):
            return False
        if h.key in self.eject_jobs:
            return False
        if self.adapter.in_print() or self.u1_flow(h):
            return False  # the rechecks of the eject: no clear, no hold under a print or a U1 flow
        try:
            head_set = bool(self.adapter.head_set(h))
        except Exception:
            logging.exception(
                "ace2k_u1: lane %s head sensor unread: no safety net", self.adapter.lane_text(h)
            )
            return False
        if not head_set:
            return False
        job = EjectJob(h)
        job.step = STEP_NET
        job.probe_event = event
        job.net_back = abs(travel) + probe_mm
        job.decided = True  # no ACEK_EJECT waits on it, as its command returned at the free verdict
        h.hold = True  # the standing re-arm is kept off while the job runs the lane
        self.eject_jobs[h.key] = job

        def run(eventtime: float, job: EjectJob = job) -> float:
            return self._run_eject(job, eventtime)

        job.timer = self.adapter.reactor.register_timer(run, self.adapter.reactor.NOW)
        return True
