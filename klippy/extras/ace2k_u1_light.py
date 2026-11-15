"""The departure of a light head (light_background_heads), before it docks.

A light head keeps the stock dock wiper, which covers its nozzle: nothing is extruded in its dock.
So it forms its tip and frees its sensor before it leaves, at the cleaning area, with the print
stopped. In the T wrapper, before the U1's T, the active head A departs when:

- A is in light_background_heads and several lanes feed it, and the T of the file picks another
  head;
- the print is printing (no pause taken or asked), past its start (layer 1 or later), with its
  plan set and started;
- A's next T in the file needs a lane that is not the lane in A, and that lane's bay holds
  filament;
- A has no pending swap, no swap or refill running and no background run kept.

Then A's SwapState runs the swap's own unload and free steps, with A active: the unload macro
takes A to the cleaning area and forms the tip there. The unload ends by heating A toward the new
filament's load temperature, and A may stay parked for minutes: A is set to the file's cool-down
at its end. The slicer cools A just before the T, which would cool the nozzle the tip needs: that
cool-down is held for the departure (PrintTracker.keep_cool_down) and applied at its end;
otherwise A is set back to its target before the departure. Then a light run of the background
swap is kept for A (Background.runs): once A is docked, the unit rolls the old lane back and pushes
the new one to A's sensor while another head prints. A's T goes on from there (Background.arrive).

A departure never pauses the print. One that fails keeps the run at its unload, which A's T runs
inline: a tip the gear held after the second unload is unloaded again there.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_background import Background
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_printer_types import GCodeCommand

try:
    from .ace2k_u1_background import BackgroundHost, BgRun
    from .ace2k_u1_common import error_line
    from .ace2k_u1_swap import SwapState
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_background import (  # type: ignore[import-not-found, no-redef]
        BackgroundHost,
        BgRun,
    )
    from ace2k_u1_common import error_line  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_swap import SwapState  # type: ignore[import-not-found, no-redef]

STATE = "ACEK_DEPART"  # the G-code state the departure saves and restores
DEPART_STEPS = ("unload", "free")  # the swap's steps the departure runs, in order


class DepartureHost(BackgroundHost, Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the departure reads it."""

    @property
    def background(self) -> Background: ...


class Departure:
    """The departure of the active light head at a T of the file that docks it.

    host is the ACE2kU1. With no light head, before_tool returns at once.
    """

    def __init__(self, host: DepartureHost) -> None:
        self.host = host
        tracker = getattr(host, "tracker", None)
        if tracker is not None:
            tracker.departs_at = self.departs_at  # a cool-down before a departure is held

    def _adapter(self) -> Adapter:
        return cast("Adapter", self.host.adapter)

    def u1_loads(self, h: Hooked) -> None:
        """The U1 loads the head of lane h (Adapter.on_u1_load). Never raises into the U1's load.

        A light run's head: before the hand over the load pushed the old lane, and the dock cannot
        form a tip, so the run ends and its T swaps inline from the unload. After it, the load
        pushed the new lane: the run is finished. It never goes on in the dock. A run that a
        pending swap or a refill keeps (yielded), and every full run, go to Background.u1_loads.
        """
        bg = self.host.background
        run = bg.runs.get(h.head)
        if run is None or run.mode != "light" or run.yielded:
            bg.u1_loads(h)
            return
        try:
            if run.busy:
                bg.stop_and_wait(None, h.head, run, leave_heater=False, u1_pick=True)
            handed = "hand over" in run.done
            run.done = run.done | {"push"} if handed else run.done - {"unload", "free", "roll back"}
            if not handed:
                run.state.unloaded, run.state.freed_mm = False, 0.0
            run.status, run.resume_ok = ("finished" if handed else "stopped"), False
            run.hand_over_steps()
        except Exception:
            logging.exception("ace2k_u1: head %d: the U1's load of a light run", h.head + 1)
            return
        what = "new lane: its light swap is done" if handed else "old lane: its T swaps from unload"
        self._adapter().say(f"ace2k_u1: head {h.head + 1}: the U1 loaded its {what}")

    def before_tool(self, gcmd: GCodeCommand, n: int) -> int | None:
        """Runs in T<n>, before the T's own swap and the U1's T. Never raises.

        It departs the active head when the rules of the module allow it (_departs), and returns
        that head (else None): a pre-heat the head owed for its dock is dropped (Wraps._tool), as
        a swap drops it, since the head has unloaded already. The file's own pre-heat of the head
        before its T is applied as for any docked head.

        A cool-down of the file held for a departure (PrintTracker.light_cool) is applied by the
        departure, or here when the head does not depart after all.
        """
        own = True  # a T of the U1 itself (the unload's T<A> A0 among them) touches nothing
        try:
            own = gcmd.get_int("A", 1) == 0
            if not own and self.host.background.light:
                found = self._departs(n)
                if found is not None:
                    self._depart(*found)
                    return found[0].head
        except Exception:
            logging.exception("ace2k_u1: the departure of a light head at T%d", n)
        finally:
            if not own:
                self._release_cool()
                self._wait_deferred()
        return None

    def departs_at(self, n: int, head: int) -> bool:
        """Tells whether head departs at T<n> of the file, as things stand (PrintTracker)."""
        try:
            found = self._departs(n) if self.host.background.light else None
        except Exception:
            logging.exception("ace2k_u1: whether head %d departs at T%d", head + 1, n)
            return False
        return found is not None and found[0].head == head

    def _wait_deferred(self) -> None:
        """Waits for the file's M109 that defer_wait moved past the departure. Never raises.

        The tool heated during the departure, so this wait is short or none. It runs whether the
        head departed or not, before the U1's T, where the file's own wait would have ended.
        """
        try:
            tracker = self.host.tracker
            wait, tracker.wait_after_depart = tracker.wait_after_depart, None
        except Exception:
            logging.exception("ace2k_u1: the wait after the departure")
            return
        if wait is None:
            return
        tool, s = wait
        try:
            self.host.u1.script(f"M109 S{s:g} T{tool} A0")
        except Exception as err:
            logging.exception("ace2k_u1: T%d's wait after the departure", tool)
            self._adapter().say(f"ace2k_u1: T{tool}'s wait after the departure: {error_line(err)}")

    def _release_cool(self) -> None:
        """Applies each cool-down still held for a departure that did not happen. Never raises."""
        try:
            held = self.host.tracker.light_cool
            for head in list(held):
                self._cool_down(head, held.pop(head), "its cool-down, kept for a departure")
        except Exception:
            logging.exception("ace2k_u1: a cool-down held for a departure")

    def _departs(self, n: int) -> tuple[Slot, Hooked, int] | None:
        """Returns (A's slot, the lane of A's next T, that T) when A departs at T<n>, else None."""
        bg, u1 = self.host.background, self.host.u1
        head = self._adapter().active_head(u1)
        t_head = bg._tool_head(n)  # None: the T's head is unknown, so it may be A's own
        if head is None or head not in bg.light or t_head is None or t_head == head:
            return None
        slot = self._adapter().slot_of_head(head)
        if slot is None or not slot.shared or slot.current is None or not self._printing():
            return None
        plan = self.host.tracker.plan
        if plan is None or plan.choice is None:
            return None
        nxt = bg._next_on(head, plan.raw, plan.choice)
        if nxt is None:
            return None
        _, tool, lane = nxt
        if lane is slot.current or not self._free(head) or not bg._bay_full(lane):
            return None
        return slot, lane, tool

    def _printing(self) -> bool:
        """A T of the file, printing with no pause taken or asked, past layer 0, the plan set and
        started."""
        u1 = self.host.u1
        return (
            u1.from_file()
            and self.host.swapper.printing_on()
            and u1.current_layer() != 0  # an unknown layer (None) counts, as for the dock wipe
            and self.host.background._may_start()
        )

    def _free(self, head: int) -> bool:
        """No pending swap, no swap or refill of the head running, and no run of it kept."""
        return (
            self.host.swapper.swap_pending is None
            and head not in self._adapter().swapping
            and head not in self.host.background.runs
        )

    def _target(self, head: int) -> float | None:
        """The head's heater target now, None when it cannot be read."""
        try:
            return self.host.tracker._active_target(head)  # the departing head is the active one
        except Exception:
            logging.exception("ace2k_u1: head %d: its target before its departure", head + 1)
            return None

    def _depart(self, slot: Slot, lane: Hooked, tool: int) -> None:
        """Runs the unload and the free check of the head with it active, then keeps its light run.

        The run is kept only once both steps ran, so the unload's T<A> A0 meets no run of A. The
        departure is one swap of the T's stop (PrintStops.swap), and ends well only when both
        steps did. A's target before it is set back at its end, and the G-code state restored,
        whatever happened.
        """
        swapper, reactor, u1 = self.host.swapper, self.host.reactor, self.host.u1
        head = slot.head
        state = SwapState(slot=slot, old=slot.current, new=lane, tool=tool)
        target = self.host.tracker.light_cool.pop(head, None)  # the file's cool-down, held
        if target is None:
            target = self._target(head)
        began = reactor.monotonic()
        failed: tuple[str, Exception] | None = None
        formed = False  # the unload formed a tip (the head's sensor saw the old filament)
        entered = False  # the head's sensor saw the old filament as the unload began
        took_s = 0.0
        stops = self.host.swap_times.stops
        with stops.swap(reactor, swapper.printing_on, self.host.printer) as run:
            u1.script(f"SAVE_GCODE_STATE NAME={STATE}")
            try:
                failed, formed, entered = self._steps(state)
                took_s = reactor.monotonic() - began
                self._keep(state, failed, formed, entered)
            finally:
                try:
                    self._cool_down(head, target)
                finally:
                    u1.script(f"RESTORE_GCODE_STATE NAME={STATE} MOVE=0")
            run.ok = failed is None
        if failed is None and formed and swapper.printing_on():
            self.host.swap_times.note("depart", took_s)  # an empty head formed no tip: no sample

    def _cool_down(
        self, head: int, target: float | None, what: str = "its target after its departure"
    ) -> None:
        """Sets the head to the file's cool-down: the one held for the departure, else its target
        before it (the file's cool-down, applied already).

        A failure is logged and said, and never stops the restore of the G-code state.
        """
        if target is None:
            return  # none known: the load temperature stays
        try:
            self.host.u1.script(f"M104 S{target:g} T{head} A0")
        except Exception as err:
            logging.exception("ace2k_u1: head %d: %s", head + 1, what)
            self._adapter().say(f"ace2k_u1: head {head + 1}: {what}: {error_line(err)}")

    def _steps(self, state: SwapState) -> tuple[tuple[str, Exception] | None, bool, bool]:
        """Runs the unload and the free check. Returns (the step and its error on a failure, else
        None; whether the unload formed a tip; whether the head's sensor saw the old filament as
        the unload began)."""
        swapper = self.host.swapper
        step = DEPART_STEPS[0]
        formed = entered = False
        try:
            with swapper.swapping(state.slot.head):
                state.e_pos = swapper.e_pos(state.slot.head)
                entered = bool(self._adapter().head_set(state.new))
                for step in DEPART_STEPS:
                    state.step = step
                    swapper.swap_step(step, state)
                    if step == DEPART_STEPS[0]:
                        formed = state.unloaded  # read after the unload, before the free check
        except Exception as err:
            logging.exception("ace2k_u1: head %d: its departure at %s", state.slot.head + 1, step)
            state.unloaded, state.freed_mm = False, 0.0  # its T unloads again, inline
            return (step, err), formed, entered
        return None, formed, entered

    def _keep(
        self,
        state: SwapState,
        failed: tuple[str, Exception] | None,
        formed: bool,
        entered: bool,
    ) -> None:
        """Keeps the light run of the head: it goes on in the dock, or after a failure it waits
        for the head's T, which swaps inline from the unload.

        A failure after the unload began with filament in the head may have formed the tip: the
        run is then marked tipped, so a T that keeps the old lane still unloads and reloads it
        (Background._take_over), and a dropped run leaves the lane unknown (Background.drop).
        """
        head = state.slot.head
        ok = failed is None
        run = BgRun(
            state,
            True,
            mode="light",
            status="stopped",
            busy=False,
            resume_ok=ok,
            failed=not ok,
            done=set(DEPART_STEPS) if ok else set(),
        )
        run.step = failed[0] if failed is not None else DEPART_STEPS[-1]
        run.tipped = not ok and entered
        adapter = self._adapter()
        self.host.background.runs[head] = run
        adapter.swapping.add(head)
        run.hand_over_steps()
        if failed is None:
            lane = adapter.lane_text(state.new)
            how = "with its tip formed" if formed else "empty"
            adapter.say(
                f"ace2k_u1: head {head + 1}: leaves {how}: the unit swaps it to {lane} while parked"
            )
            return
        step, err = failed
        adapter.say(
            f"ace2k_u1: head {head + 1}: its departure failed at {step}: {error_line(err)}: its "
            "swap runs at its T"
        )
