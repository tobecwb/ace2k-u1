"""Handles a spool that runs out in a shared head.

The pause timer pauses the print plainly. The refill is then pending. It is a swap of the lane for
itself, and the rest of the lane that ran out is taken out of its bay. The new spool is asked for
(its tag, or its record) before RESUME goes on with it as a swap. The module also handles a runout
that happens while a swap is already pending. The steps of the swap itself belong to
ace2k_u1_swap. ace2k_u1.py calls the Refiller.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Reactor
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_spools import LaneSpools, SpoolStore
    from .ace2k_u1_swap import Swapper
    from .ace2k_u1_tracker import PrintTracker

try:
    from . import ace2k_u1_plan
    from .ace2k_u1_adapter import PRINT_ON
    from .ace2k_u1_common import PUSH_POLL_S, TAG_UNSETTLED, error_line, tag_state
    from .ace2k_u1_swap import SWAP_STEPS, SwapState
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_plan  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_adapter import PRINT_ON  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        PUSH_POLL_S,
        TAG_UNSETTLED,
        error_line,
        tag_state,
    )
    from ace2k_u1_swap import SWAP_STEPS, SwapState  # type: ignore[import-not-found, no-redef]

# The steps that take the rest of a run-out lane out of its bay. They are the steps of the swap,
# up to its hand over.
REFILL_CLEAR_STEPS = ("unload", "free", "roll back")
TAG_WAIT_S = 20.0  # the longest that the RESUME of a refill waits for a tag search that is running


class StoppedRunout(TypedDict):
    """A lane of a shared head that ran out while a swap or a refill was pending (runout_stopped).

    It has the lane, and whether its head has been seen empty since. The keys are read by name,
    which vulture does not see. The noqa on a key says so.
    """

    lane: Hooked  # noqa: V107
    emptied: bool  # noqa: V107


class RefillHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the refill reads it.

    It gives the adapter (built at the hook, before any runout of a shared head), the reactor,
    the U1, the plan, the spools and the Swapper. It also gives a way to read the sensor of a
    head.
    """

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def reactor(self) -> Reactor: ...
    @property
    def u1(self) -> U1Printer: ...
    @property
    def tracker(self) -> PrintTracker: ...
    @property
    def spool_lanes(self) -> LaneSpools: ...
    @property
    def spool_store(self) -> SpoolStore: ...
    @property
    def swapper(self) -> Swapper: ...
    def head_set_or(
        self, h: Hooked, default: bool, unread: str | Callable[[], str] | None = None
    ) -> bool: ...
    def sensor_text(self, h: Hooked) -> str: ...
    def changed(self) -> None: ...


class Refiller:
    """Handles the end of a spool in a shared head.

    host is the ACE2kU1. Its adapter is built at the hook. The pending swap and the steps of its
    Swapper, its U1, its plan and its spools are read when used.
    """

    def __init__(self, host: RefillHost) -> None:
        self.host = host
        # Maps a head to a lane of a shared head that ran out while a swap or a refill was
        # pending. The lane is stopped. RESUME waits until the head was seen empty and then set
        # again (loaded by hand). Each entry is {"lane": h, "emptied": bool}.
        self.runout_stopped: dict[int, StoppedRunout] = {}

    def _adapter(self) -> Adapter:
        """Returns the adapter of the host.

        It is built at the hook, before any runout of a shared head.
        """
        return cast("Adapter", self.host.adapter)

    def _slot(self, h: Hooked) -> Slot:
        """Returns the head of h, which is a lane of a shared head. Every hooked lane has one."""
        return cast("Slot", self._adapter().slot_of(h))

    def _tool_of(self, h: Hooked) -> int | None:
        """Returns the tool that the running print prints with lane h.

        It is the T that ran last, when its lane is h. Otherwise it is the first tool that the
        plan chose h for. Returns None when there is none.
        """
        # The first one chosen counts. Tools that share a lane share its spool, and only the
        # temperature is read.
        plan = self.host.tracker.plan
        if plan is None or not plan.set:
            return None
        # A plan that is set has a choice.
        order, choice = plan.order, cast("dict[int, Hooked]", plan.choice)
        if (
            0 <= self.host.tracker.t_index < len(order)
            and choice.get(order[self.host.tracker.t_index]) is h
        ):
            return order[self.host.tracker.t_index]
        return next((n for n in sorted(choice) if choice[n] is h), None)

    def shared_runout(self, h: Hooked) -> None:
        """Does the part of the pause timer when a spool runs out in a shared head.

        The lane is already stopped and held (GripWatch.shared_end). These are the steps.
        1. The plain PAUSE, through run_script (a timer, outside the G-code lock). It is checked
           as the PAUSE of the swap is.
        2. With the print paused, the refill becomes pending. It is a swap of the lane for
           itself. Its rest is taken out of the bay (_refill_clear).
        3. The INNER_RESUME variables are set after the PAUSE.

        A PAUSE that the U1 does not take falls back to the runout pause of the U1 for the head
        (_runout_fallback). If the print is neither printing nor paused, or a swap is already
        pending, only a line is said.

        Never raises. The hold of the lane is given back at the end. But with a swap pending,
        the lane stays stopped, held, and with its end taken, until its head is loaded again by
        hand (stopped_runouts_clear).
        """
        adapter = self._adapter()
        slot = self._slot(h)
        line = f"head {slot.head + 1}: {adapter.lane_text(h)} ran out at its bay"
        stopped = False
        try:
            state = adapter.print_state()
            if state not in PRINT_ON:
                adapter.say(f"ace2k_u1: {line}: the print is {state} — not paused")
                return
            if self.host.swapper.swap_pending is not None:
                # The lane stays stopped, with its rest in the tube. RESUME waits until its head
                # has been unloaded and loaded again by hand (stopped_runouts_clear).
                text = self.host.swapper.swap_text(self.host.swapper.swap_pending)
                self.runout_stopped[slot.head] = {"lane": h, "emptied": False}
                stopped = True
                adapter.say(f"ace2k_u1: {line} while {text} is pending — {self._stopped_text(h)}")
                return
            try:
                self.host.swapper.pause(None, line, timer=True)
            except Exception as err:
                self._runout_fallback(h, line, err)
                return
            refill = self._set_refill(h)
            try:
                self._refill_clear(refill)
            finally:
                # After the PAUSE, which records the values of the heads.
                self.host.swapper.resume_temps(refill)
                refill.timer = False
        except Exception as err:
            logging.exception("ace2k_u1: %s", line)
            adapter.say(f"ace2k_u1: {line} — {err}")
        finally:
            if h.end_declined is None and not stopped:
                h.hold = False
                # The refill is pending (or a line was said), so the watch takes the lane no more.
                h.end_taken = False

    def _stopped_text(self, h: Hooked) -> str:
        """Returns what the operator must do before RESUME, after a runout that stopped a pause."""
        head, lane_text = self._slot(h).head + 1, self._adapter().lane_text(h)
        return (
            f"RESUME waits for head {head}: unload head {head}, roll {lane_text} back out of its "
            f"bay, put a new spool in and load head {head} from the U1 screen"
        )

    def watch_stopped(self) -> None:
        """Marks a head that a runout stopped as emptied, once its sensor reads empty.

        It runs on each tick while a print is on. The head is empty once it is unloaded by hand.
        Never raises.
        """
        for entry in self.runout_stopped.values():
            if entry["emptied"]:
                continue
            lane = entry["lane"]

            def unread(lane: Hooked = lane) -> str:
                return self.host.sensor_text(lane)

            if not self.host.head_set_or(lane, True, unread):
                entry["emptied"] = True

    def stopped_runouts_clear(self) -> bool:
        """Tells whether RESUME may go on after runouts that stopped during a pause.

        It may go on only when each such head has been seen empty and reads set again (it is
        loaded by hand). The lane then gets back its hold and its end. A head that reads empty
        now is marked as emptied. Returns False, and the console says why, while one head has
        not met this.
        """
        adapter = self._adapter()
        for head in sorted(self.runout_stopped):
            entry = self.runout_stopped[head]
            try:
                head_set = adapter.head_set(entry["lane"])
            except Exception as err:
                adapter.alert(
                    f"ace2k_u1: head {head + 1}: its sensor cannot be read — {err}; still paused"
                )
                return False
            if not head_set:
                entry["emptied"] = True
            if not (head_set and entry["emptied"]):
                adapter.alert(
                    f"ace2k_u1: head {head + 1}: {adapter.lane_text(entry['lane'])} ran out — "
                    f"{self._stopped_text(entry['lane'])}; still paused"
                )
                return False
            del self.runout_stopped[head]
            entry["lane"].hold = False
            entry["lane"].end_taken = False
        return True

    def _refill_state(self, h: Hooked) -> SwapState:
        """Returns the pending state of a refill: a swap of lane h for itself, from its unload.

        printing_e_pos is read now, after the pause, so it holds the retraction of the pause. The
        pause timer runs the refill until its timer is cleared.
        """
        slot = self._slot(h)
        # The spool ran out, so its record no longer names what the bay will hold. The new spool is
        # read from its tag or set (spool_known). If the file cannot be written, the record stays
        # there, but never in memory. The refill must not take the old spool for the new one.
        if not self.host.spool_lanes.bay_empty(h):
            self.host.spool_store.records.pop(self.host.spool_store.key(h.unit, h.lane), None)
            self.host.changed()
        return SwapState(
            slot=slot,
            old=h,
            new=h,
            tool=self._tool_of(h),
            step=REFILL_CLEAR_STEPS[0],
            saved=False,  # it is saved at RESUME, which runs it as a swap from its hand over
            # Read before the unload, with the retraction of the pause in it.
            e_pos=self.host.swapper.e_pos(slot.head),
            refill=True,
            timer=True,  # run by the pause timer; RESUME waits for it (refill_ready)
        )

    def _set_refill(self, h: Hooked) -> SwapState:
        """Makes the refill of lane h (_refill_state) the pending swap, and returns it."""
        refill = self._refill_state(h)
        self.host.swapper.swap_pending = refill
        return refill

    def _runout_fallback(self, h: Hooked, line: str, err: Exception) -> None:
        """Handles a plain PAUSE that the U1 did not take, or that failed.

        The print must not go on with the lane stopped. So the runout pause of the U1 for the head
        runs, as its head sensor raises it (PauseRunner.runout_pause).

        If the U1 takes it, the refill becomes pending from its unload. It is made visible only
        now, so no T of the head goes on with it before the print is paused. printing_e_pos is
        read after that pause, and the INNER_RESUME variables are set from the timer. The unload
        and the roll back run at the RESUME.

        The U1 may decline too. That happens when the head sensor is disabled, when its pause on
        runout is off, or because of the end action of the print. Then nothing is pending, the
        lane stays stopped, and a line says that the print was not paused.
        """
        adapter = self._adapter()
        adapter.say(f"ace2k_u1: {line} — {err}: the U1's runout pause instead")
        adapter.pause_runner.runout_pause(h)
        if not self.host.u1.pause_taken():
            adapter.say(
                f"ace2k_u1: {line} — the lane is stopped and the print was NOT paused: pause it by "
                "hand"
            )
            h.end_declined = "waiting"  # its end stays taken, so the bay watch takes it no more
            return
        refill = self._set_refill(h)
        try:
            self.host.swapper.resume_temps(refill)
        finally:
            refill.timer = False

    def _refill_clear(self, state: SwapState) -> bool:
        """Takes the rest of a refill out of its bay.

        It runs the unload of the swap (LOAD_TEMP=0), the free check, and the roll back of the
        lane, from the pending step on. A held tip is unloaded and checked once more, as in a
        swap. The roll back is head_budget_mm long, less what the free check rolled back.
        printing_e_pos is put back after these steps.

        Returns True, and the console says so, when the rest is out and the step is the hand
        over. On a failure it puts the head back, says the step, and leaves the refill pending
        there. It returns False then. If the free check fails, the pending step becomes the
        unload, because the gear held the rest and a free check alone would find it held again.
        """
        adapter, swapper = self._adapter(), self.host.swapper
        head, h = state.slot.head, state.new
        step = state.step
        with swapper.swapping(head):
            try:
                for step in REFILL_CLEAR_STEPS[REFILL_CLEAR_STEPS.index(state.step) :]:
                    state.step = step
                    swapper.swap_step(step, state)
            except Exception as err:
                why = error_line(err)
                swapper.put_back(state, True)
                if step == "free":
                    state.step = "unload"
                adapter.alert(
                    f"ace2k_u1: {swapper.swap_text(state)} failed at {step}: {why} — still "
                    "paused; RESUME takes the rest out again"
                )
                return False
        state.step = "hand over"
        self.host.swapper.set_e_pos(head, state.e_pos)
        adapter.say(
            f"ace2k_u1: head {head + 1}: {adapter.lane_text(h)} ran out — its rest is out of bay "
            f"{adapter.lane_text(h)}: take it out, put a new spool in and RESUME"
        )
        return True

    def spool_known(self, pending: SwapState) -> bool:
        """Tells whether a pending swap or refill may go on, when its spool step is still to come.

        It may go on only when the U1 will know the filament of the head after it, and when that
        filament is the one in the file.

        For a swap, the U1 knows the filament from the spool of the new lane (its tag or its
        ACEK_SPOOL record), or from the setting that is still in the head. With neither, the
        RESUME of the U1 itself refuses at its end ("e0 not edit filament"), after the whole load.

        For a refill, only the spool of the new lane counts (its tag or its record), because
        the setting of the head is the spool that ran out. Its tag is read first when it is not
        read yet and the head is empty (ACEK_RFID_READ MOVE=1, which feeds the filament out of
        its bay and back). So the operator types only what no tag tells.

        A spool of another material than the one that the plan's tools on that lane want is
        refused, as the plan refuses it at the start.

        A refusal is said on the console, the print stays paused, and nothing more moves. This
        was seen on the unit.
        """
        if pending.step not in SWAP_STEPS[: SWAP_STEPS.index("spool") + 1]:
            return True  # its spool step ran (a step after it, or the restore of the G-code state)
        h, head = pending.new, pending.slot.head
        adapter = self._adapter()
        refill = bool(pending.refill)
        spools = self.host.spool_lanes
        own = spools.own_spool(h)
        if refill and own is None and not self._refill_tag(h, head):
            return False
        # The new spool's tag or record, or the setting of a head that it feeds alone while that
        # setting speaks for it (LaneSpools.lone_setting: written since the bay was seen empty).
        # The setting of a shared head is the spool that ran out, whatever lane it was for.
        if own is None:  # the tag check above may have read it
            own = spools.own_spool(h)
        known = own is not None or spools.lone_setting(h) is not None
        if not known:
            if refill:
                line = (
                    f"the new spool in bay {adapter.lane_text(h)} is not known (no tag read, no "
                    "spool set): set it (Edit spool, or ACEK_SPOOL), then RESUME; still paused"
                )
            else:
                line = (
                    f"{adapter.lane_text(h)} has no spool set and the head no filament setting: "
                    "the U1's RESUME would refuse it — set it (Edit spool, or ACEK_SPOOL), then "
                    "RESUME; still paused"
                )
            adapter.alert(f"ace2k_u1: head {head + 1}: {line}")
            return False
        wrong = self._material_refused(h, own=refill)
        if wrong is not None:
            adapter.alert(
                f"ace2k_u1: head {head + 1}: {wrong}: put a spool of that material in bay "
                f"{adapter.lane_text(h)} (or set its spool if it is one), then RESUME; still paused"
            )
            return False
        return True

    def _refill_tag(self, h: Hooked, head: int) -> bool:
        """Reads the tag of the new spool of a refill in the bay of lane h.

        This is done when the spool of the lane is not known yet. The tag is read
        (ACEK_RFID_READ MOVE=1) when head is empty and the tag is not read yet. Returns False,
        and the console says why, when that cannot be done. That happens when the sensor of the
        head cannot be read, when it is set, or when a read is still running.
        """
        adapter = self._adapter()
        empty = self._head_empty_now(h)
        if empty is None:
            adapter.alert(
                f"ace2k_u1: head {head + 1}: its sensor cannot be read: the new spool's tag is "
                "not read — set the spool (Edit spool, or ACEK_SPOOL), then RESUME; still "
                "paused"
            )
            return False
        if not empty:
            adapter.alert(
                f"ace2k_u1: head {head + 1}: its sensor sees filament, so the new spool's tag "
                "is not read (that moves the filament toward the head): set the spool (Edit "
                "spool, or ACEK_SPOOL), or clear the head, then RESUME; still paused"
            )
            return False
        tag = self._tag_unread(h)
        if tag == "pending":
            self._read_tag(h)
        elif tag in TAG_UNSETTLED:
            adapter.alert(
                f"ace2k_u1: head {head + 1}: the new spool's tag in bay {adapter.lane_text(h)} "
                "is still being read — RESUME again in a moment; still paused"
            )
            return False
        return True

    def _head_empty_now(self, h: Hooked) -> bool | None:
        """Tells whether the sensor of the head reads empty. Returns None when it cannot be read."""
        try:
            return not self._adapter().head_set(h)
        except Exception:
            logging.exception("ace2k_u1: %s's head sensor unread", self._adapter().lane_text(h))
            return None

    def _tag_unread(self, h: Hooked) -> str | None:
        """Returns the tag state of lane h, after a search or session that is already running ends.

        It waits for that up to TAG_WAIT_S. A second read is not started over one that is in
        flight. The result is one of these.
        - "pending": there is filament in its bay, and its tag is not read yet.
        - "searching" or "reading": it is still running at the end of the wait.
        - None: anything else.
        """
        give_up = self.host.reactor.monotonic() + TAG_WAIT_S
        while True:
            try:
                lane = self._adapter().lane_status(h)
            except Exception:
                return None
            state = tag_state(lane)
            if state in TAG_UNSETTLED:
                if self.host.reactor.monotonic() >= give_up:
                    return state
                self.host.reactor.pause(self.host.reactor.monotonic() + PUSH_POLL_S)
                continue
            return "pending" if lane.get("insert") is True and state == "pending" else None

    def _read_tag(self, h: Hooked) -> None:
        """Reads the tag of the spool of lane h by moving its filament (ACEK_RFID_READ MOVE=1).

        It waits for the read. A failure is only a line on the console. The spool is then asked
        for.
        """
        adapter = self._adapter()
        adapter.say(f"ace2k_u1: {adapter.lane_text(h)}: reading the new spool's tag")
        try:
            self.host.u1.script(f"ACEK_RFID_READ {adapter.lane_args(h)} MOVE=1")
        except Exception as err:
            logging.exception("ace2k_u1: %s's tag read", adapter.lane_text(h))
            adapter.say(f"ace2k_u1: {adapter.lane_text(h)}: its tag was not read — {err}")

    def _material_refused(self, h: Hooked, own: bool = False) -> str | None:
        """Returns why the spool of lane h is not the material of a tool of the plan, or None.

        An example of the text is "T0 wants PLA, a:4 holds ABS". The result is None when no plan
        is set, when no tool is on h, or when a material is not known. With own set, only the
        spool of the lane counts (its tag or record), never the setting of the head. The setting
        of a refill is the spool that ran out.
        """
        plan = self.host.tracker.plan
        if plan is None or not plan.set or not plan.choice:
            return None
        have = self._own_material(h) if own else self.host.spool_lanes.material(h)
        for n, x in sorted(plan.choice.items()):
            want = plan.tools[n].get("material") if x is h else None
            if ace2k_u1_plan.other_material(want, have):
                return f"T{n} wants {want}, {self._adapter().lane_text(h)} holds {have}"
        return None

    def _own_material(self, h: Hooked) -> str:
        """Returns the material of lane h from its spool (tag, else ACEK_SPOOL record).

        Returns "" when there is neither.
        """
        try:
            kind, record, _colour = self.host.spool_lanes.own_record(h)
        except Exception:
            logging.exception("ace2k_u1: %s's own spool", self._adapter().lane_text(h))
            return ""
        if kind == "tag":
            return str(record.get("material") or "").strip().upper()
        if kind == "spool":
            return str(record.get("type") or "").strip().upper()
        return ""

    def refill_ready(self, pending: SwapState) -> bool:
        """Tells whether a refill may go on, as a swap from its hand over.

        It may go on only with its rest out and a new spool in the bay of the lane. If its rest
        is still in the head (after a failed unload, free check or roll back), the rest is taken
        out again here, and then the print stays paused for the new spool. Returns False, and the
        console says why, while the refill cannot go on.
        """
        adapter = self._adapter()
        h = pending.new
        head = pending.slot.head + 1
        if pending.timer:
            adapter.alert(
                f"ace2k_u1: head {head}: {adapter.lane_text(h)}'s rest is still being rolled back "
                "— RESUME"
            )
            return False
        if self.host.swapper.refill_clearing(pending) and pending.step in REFILL_CLEAR_STEPS:
            # The unload and the roll back run only with the print paused.
            if not self.host.u1.u1_paused():
                adapter.say(
                    f"ace2k_u1: head {head}: {adapter.lane_text(h)}'s rest is taken out only with "
                    "the print paused — PAUSE it first"
                )
                return False
            if pending.step == "unload":
                # The unload has not run, or it runs again after a free check that failed.
                # printing_e_pos is read again, as the pause (and the put back after the failure)
                # left it.
                e_pos = self.host.swapper.e_pos(pending.slot.head)
                if e_pos is not None:
                    pending.e_pos = e_pos
            self._refill_clear(pending)
            return False
        tail_at = pending.tail_at_bay_s
        if tail_at is not None and not h.bay_emptied_at > tail_at:
            adapter.alert(
                f"ace2k_u1: head {head}: the old filament's tail is still in bay "
                f"{adapter.lane_text(h)}: take it out, put the new spool in, then RESUME; still "
                "paused"
            )
            return False
        if not adapter.present_or_not(h):
            adapter.alert(
                f"ace2k_u1: head {head}: put a new spool in bay {adapter.lane_text(h)} first — "
                "still paused"
            )
            return False
        return True
