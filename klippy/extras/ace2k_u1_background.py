"""Swaps the lane of a parked head in its dock, while another head prints.

A head that several lanes feed changes its lane when its T comes (ace2k_u1_swap). The heads in
background_heads may do most of that work earlier, while they are parked. After a T of the file
docks such a head, the adapter looks ahead in the file. When the head's next tool needs another
lane, a background swap starts, the moment the head is docked: after any T, the U1's own T
inside another head's swap included, and on each watch tick as a backstop. Each docked head has
its own (several may run at once). It runs in a reactor callback, while the print goes on:

- heat: the head heats to the old filament's unload temperature;
- unload: the U1's tip for that head is queued whole as parked moves (ace2k_u1_parked), and the
  old lane follows them through the feed-forward. Every parked move runs through
  ace2k_u1_dock_moves, which holds the U1's tangle check of the head off while the moves are
  queued and run (a check that cannot be turned off stops the background swap at that step);
- free, roll back, hand over, push: the swap's own steps, which move the unit only;
- grip: the head heats to the new tool's printing temperature, and the new filament goes 40 mm
  through the gear and background_purge_mm more (the load's purge) with one parked move;
- loaded: the head's sensor must see the new filament.

The purge falls through the dock's opening, so the head arrives ready to print. When the head's T
comes, the background swap stops at its next safe point. The swap of today then goes on from the
step it reached (Swapper.resume_background), with the head picked before any unit step: at the end
of a finished one, only the spool, the loaded check and the return are left (the head is picked,
heats to its printing temperature at the cleaning area, and the U1's pre-extrusion cuts and cleans).
With background_purge_mm 0, the load without its grip (the purge and the clean) runs before the
loaded check.

The background swap never runs a G-code. The T that waits for it holds Klipper's G-code lock, so
a G-code from the background would wait for that lock forever. It only calls objects: the
heaters, the unit, the extruder's stepper. A step that needs a G-code runs at the T.

While it runs, the background swap owns the head's heater. A temperature call of the file for
that head is recorded and not applied. At the end, the head gets the newest call, else the target
it had at the start; 0 once the print has ended.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_bench import Bench
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_config import SwapConfig
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_printer_types import (
        Extruder,
        GCodeCommand,
        Heater,
        MovingExtruder,
        Reactor,
    )
    from .ace2k_u1_swap import Swapper, ToolParams
    from .ace2k_u1_swap_times import SwapTimes
    from .ace2k_u1_tracker import PrintTracker

try:
    from . import ace2k_u1_parked as parked_module
    from . import ace2k_u1_probe
    from .ace2k_u1_adapter import PRINT_ON
    from .ace2k_u1_common import PAUSE_WAIT_S, StoppedError, error_line
    from .ace2k_u1_config import LANES
    from .ace2k_u1_dock_moves import DockMoves
    from .ace2k_u1_printer import TangleOffError
    from .ace2k_u1_swap import DISCARD_POSITION, SWAP_STEPS, OwedTool, SwapState
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_parked as parked_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_probe  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_adapter import PRINT_ON  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        PAUSE_WAIT_S,
        StoppedError,
        error_line,
    )
    from ace2k_u1_config import LANES  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_dock_moves import DockMoves  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_printer import TangleOffError  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_swap import (  # type: ignore[import-not-found, no-redef]
        DISCARD_POSITION,
        SWAP_STEPS,
        OwedTool,
        SwapState,
    )

# The steps of a background swap, in order. Each of the first five swap steps that it finishes is
# skipped when the swap goes on at the T (SwapState.done_in_dock), and so is the load when the
# grip purged.
DOCK_STEPS = ("heat", "unload", "free", "roll back", "hand over", "push", "grip", "loaded")
# The swap steps a background swap can finish, in order. The swap goes on from the first one that
# is not done, else from its spool.
DOCK_SWAP_STEPS = SWAP_STEPS[: SWAP_STEPS.index("push") + 1]
HEAT_TIMEOUT_S = 240.0  # the longest wait for the head's heater, at the heat and at the grip
HEAT_WITHIN_C = 4.0  # a heater this close to its target has reached it
HEAT_POLL_S = 0.25  # how often a heat wait reads the heater, and a stop request
WAIT_POLL_S = 0.1  # how often the T reads a background swap it waits for
GRIP_MM = 40.0  # the load macro's move through the gear
GRIP_SPEED_MM_S = 6.0  # its speed (F360)
# The T waits over the part for at most this long. A longer wait takes the active head to the
# cleaning area first.
NEAR_WAIT_S = 1.0
# The T gives up on a background swap that has not stopped after this long. That never happens:
# each step of the dock ends by itself.
STOP_WAIT_MAX_S = 900.0
WAIT_STATE = "ACEK_BG_WAIT"  # the G-code state the T saves while it waits at the cleaning area
# The U1's own nozzle clean at the cleaning area, as its unload runs it after the cut. A head that
# waited there, hot, is cleaned with it before it leaves.
WAIT_CLEAN = "INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2"


# The U1's heaters.py: at most this many heads climb at once (MAX_HEATING_EXTRUDERS, read from
# the module when it can be), and a head within CLIMB_DELTA_C of its target does not climb
# (INACTIVE_EXTRUDER_TEMP_DELTA): it is set at once and dropped from the climbing list.
U1_MAX_CLIMBING = 2
CLIMB_DELTA_C = 2.0
HAND_BACK_POLL_S = 1.0  # how often a hand-back that would climb tries again for a slot


class Heaters(Protocol):
    """The heaters object of the U1 (extras/heaters.py), as the background swap uses it.

    set_temperature with wait False returns at once. A head already climbing, or within 2 °C of
    its new target, is set at once; with two heads climbing (active_heating_extruders), any other
    is queued in pending_extruders, and its heater is left as it is until a slot frees. A 1 Hz
    timer drops the heads within 2 °C from the climbing list and starts the queued ones.
    """

    def set_temperature(self, heater: object, temp: float, wait: bool = False) -> None: ...


class BackgroundHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the background swap reads it."""

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def printer(self) -> Printer: ...
    @property
    def reactor(self) -> Reactor: ...
    @property
    def u1(self) -> U1Printer: ...
    @property
    def tracker(self) -> PrintTracker: ...
    @property
    def swapper(self) -> Swapper: ...
    @property
    def swap_cfg(self) -> SwapConfig: ...
    @property
    def bench(self) -> Bench: ...
    @property
    def swap_times(self) -> SwapTimes: ...


@dataclass
class BgRun:
    """One background swap: its SwapState and where it is.

    status is running (its callback runs), stopped (it ended before its end) or finished. step
    is the step of DOCK_STEPS it runs or reached. The state stays until the head's T takes it, or
    the print ends.
    """

    state: SwapState
    in_print: bool  # started in a print (not by ACEK_BG_SWAP)
    status: str = "running"
    step: str = DOCK_STEPS[0]
    busy: bool = True  # its callback has not returned yet
    stop_asked: bool = False
    # Stopped by a pause or by a T of the U1 (A0) that picks its head: once the print prints and
    # the head is docked, it goes on in the dock (check). A stop by a T of the file, an inline
    # swap of its own head, a refill or ACEK_BG_STOP keeps it for its T.
    resume_ok: bool = False
    # Kept for its T by a pending swap or a refill of its own head, while that swap runs
    # (Swapper.giving_way clears it at its end): that swap unloads and loads the head itself, so a
    # load of the U1 meanwhile does not undo the dock's work (u1_loads).
    yielded: bool = False
    drop_asked: bool = False  # the print ended while it ran: the state goes at its end
    owns: bool = False  # it owns the head's heater (from the heat on)
    start_target: float = 0.0  # the heater's target when it took it
    called: float | None = None  # the newest temperature call of the file recorded meanwhile
    leave_heater: bool = False  # a T may take the heater over: it is not handed back at the end
    heater_left: bool = False  # the hand-back was left to that T, which gives it if it takes none
    step_end: float | None = None  # when the unit move of the step should end (reactor time)
    done: set[str] = field(default_factory=set)  # the swap steps finished in the dock
    tipped: bool = False  # a parked move of the tip was queued
    purged_mm: float = 0.0  # what the grip's move pushed past GRIP_MM in the dock
    grip_temp: float | None = None  # the temperature the grip's move ran at
    step_at: float | None = None  # when the current dock step started (reactor time)
    in_step_s: float = 0.0  # how long the run had been in its step when it ended
    failed: bool = False  # a step failed (_failed): its arrival gives no swap time sample
    # The current step returned at once with nothing to do (an empty head's unload and free
    # check): it gives no swap time sample, which would pull its average toward 0 s.
    skipped: bool = False

    def hand_over_steps(self) -> None:
        """Gives the swap's state the steps the dock finished and the step to go on from."""
        self.state.done_in_dock = frozenset(self.done)
        self.state.step = next((s for s in DOCK_SWAP_STEPS if s not in self.done), "spool")


class Background:
    """The background swaps of the heads in background_heads, one per docked head.

    host is the ACE2kU1. With no head listed, nothing here runs: every call returns at once.
    """

    def __init__(self, host: BackgroundHost) -> None:
        self.host = host
        self.heads: frozenset[int] = host.swap_cfg.background_heads
        self.runs: dict[int, BgRun] = {}  # head -> its background swap, until its T takes it
        # every parked move, with the head's tangle check held off (its mover: self.mover)
        self.moves = DockMoves(host.reactor, host.u1, parked_module.ParkedMover(host.printer))
        # tip_moves of ace2k_u1_parked; a test gives its own
        self.tip: Callable[[Printer, int, int], list[parked_module.TipStep] | str] = (
            parked_module.tip_moves
        )
        self.no_ff_said = False  # the line for an ace2k host without note_move, once per print
        # The heads a T of the U1 (A0) is picking now: never docked, though not active yet.
        self.picks: list[int] = []

    # --- the parts of the host ---------------------------------------------------------------

    @property
    def mover(self) -> parked_module.ParkedMover:
        """The ParkedMover of every parked move (self.moves'); a test may set its own."""
        return self.moves.mover

    @mover.setter
    def mover(self, mover: parked_module.ParkedMover) -> None:
        self.moves.mover = mover

    def _adapter(self) -> Adapter:
        return cast("Adapter", self.host.adapter)

    def _say(self, msg: str) -> None:
        self._adapter().say(f"ace2k_u1: {msg}")

    def _extruder(self, head: int) -> Extruder | None:
        return self.host.u1.extruder((self._adapter().extruders or {}).get(head))

    def _moving(self, head: int) -> MovingExtruder:
        return cast("MovingExtruder", self._extruder(head))

    # --- the start (§3.1) --------------------------------------------------------------------

    def after_tool(self, gcmd: GCodeCommand) -> None:
        """Starts the background swaps a T allows, once the U1's T has run. Never raises.

        Every T counts, the U1's own (A0) too: the U1's T inside another head's swap docks the
        head that was active, and its background swap starts at once.
        """
        del gcmd
        self.check()

    def check(self) -> None:
        """Starts or goes on with every background swap the start rule allows now. Never raises.

        It runs after each T and on each watch tick, never runs a G-code, and only registers
        each run's callback. The one whose T comes first is started first.
        """
        if not self.heads or self.host.adapter is None:
            return
        try:
            for found in self.candidates():
                if isinstance(found, BgRun):
                    self._resume(found)
                else:
                    self.start(*found, in_print=True)
        except Exception:
            logging.exception("ace2k_u1: the start of a background swap")

    def candidates(self) -> list[BgRun | tuple[Slot, Hooked, int]]:
        """Returns the background swaps to start or go on with now, the earliest T first.

        The print is printing with its plan started. A listed head that is docked (not the active
        one, nor the head of the T being run), not in another swap and whose lane is known, is a
        candidate when the next T of the file on it needs another lane whose bay holds filament. A
        head with a kept run that a pause or a T of the U1 (A0) stopped goes on when that lane is
        still the run's new lane, its bay holds filament and its lanes can record a parked move, as
        for a new run; any other kept run waits for its T.
        """
        plan = self.host.tracker.plan
        if plan is None or plan.choice is None or not self._may_start():
            return []
        found: list[tuple[int, BgRun | tuple[Slot, Hooked, int]]] = []
        for head in sorted(self.heads - self._not_docked()):
            nxt = self._next_on(head, plan.raw, plan.choice)
            slot = self._adapter().slot_of_head(head)
            if nxt is None or slot is None or not slot.shared:
                continue
            index, tool, lane = nxt
            entry = self._candidate(slot, lane, tool)
            if entry is not None:
                found.append((index, entry))
        return [entry for _, entry in sorted(found, key=lambda e: e[0])]

    def _candidate(
        self, slot: Slot, lane: Hooked, tool: int
    ) -> BgRun | tuple[Slot, Hooked, int] | None:
        """The run of slot's head that goes on, or the new one to start for lane, or None."""
        run = self.runs.get(slot.head)
        if run is not None:
            if not run.resume_ok or run.busy or lane is not run.state.new:
                return None
        elif slot.current is None or lane is slot.current or slot.head in self._adapter().swapping:
            return None
        pushed = run is not None and "push" in run.done  # the lane is out of its bay
        if (not pushed and not self._bay_full(lane)) or not self._has_ff(slot):
            return None  # a run that goes on is checked as a new one
        return run if run is not None else (slot, lane, tool)

    def _may_start(self) -> bool:
        """Printing, the plan set and its print started."""
        tracker = self.host.tracker
        return (
            self.host.u1.print_state() == "printing"
            and tracker.plan is not None
            and tracker.plan.set
            and tracker.plan_started
        )

    def _not_docked(self) -> set[int | None]:
        """The heads no background swap starts on now: the active head, the head of the T being
        run (its own swap is about to start), a head a T of the U1 (A0) is picking (picks: the
        active head changes only at the end of that T), and the head of a pending swap."""
        heads: set[int | None] = {self._adapter().active_head(self.host.u1), *self.picks}
        tracker = self.host.tracker
        raw = (tracker.plan.raw if tracker.plan is not None else None) or ()
        if 0 <= tracker.t_raw < len(raw):
            heads.add(self._tool_head(raw[tracker.t_raw]))
        pending = self.host.swapper.swap_pending
        if pending is not None:
            heads.add(pending.slot.head)
        return heads

    def _next_on(
        self, head: int, raw: list[int], choice: dict[int, Hooked]
    ) -> tuple[int, int, Hooked] | None:
        """Returns (index in raw, tool, lane) of the next T of the file on head, after the T run."""
        for i in range(self.host.tracker.t_raw + 1, len(raw)):
            lane = choice.get(raw[i])
            if lane is not None and lane.head == head:
                return i, raw[i], lane
        return None

    def _bay_full(self, h: Hooked) -> bool:
        insert, read = self.host.bench.bay_insert(h)
        return read and insert is True

    def _has_ff(self, slot: Slot) -> bool:
        """Tells whether every lane of the head can record a parked move (FeedForward.note_move).

        An older ace2k host has no note_move. The background swap then does not start, and the
        console says so once per print.
        """
        if all(self._note(h) is not None for h in slot.lanes):
            return True
        if not self.no_ff_said:
            self.no_ff_said = True
            logging.info("ace2k_u1: the ace2k host has no FeedForward.note_move")
            self._say(
                f"head {slot.head + 1}: no background swap: the ace2k host cannot record a "
                "parked move (update ace2k)"
            )
        return False

    @staticmethod
    def _note(h: Hooked) -> Callable[[str, float, float, float], object] | None:
        """Returns the note_move of the lane's feed-forward, or None when the host has none."""
        note = getattr(getattr(h.api, "ff", None), "note_move", None)
        return (
            cast("Callable[[str, float, float, float], object]", note) if callable(note) else None
        )

    def start(self, slot: Slot, new: Hooked, tool: int | None, in_print: bool) -> BgRun:
        """Starts the background swap of slot's head to lane new, for T<tool>, in a callback.

        The head is held in adapter.swapping until its T takes the state, or the state is
        dropped.
        """
        head = slot.head
        state = SwapState(slot=slot, old=slot.current, new=new, tool=tool)
        run = self.runs[head] = BgRun(state, in_print)
        self._adapter().swapping.add(head)
        self._say(f"{self.host.swapper.swap_text(state)}: background swap in its dock")
        self.host.reactor.register_callback(lambda _eventtime: self._run(run))
        return run

    def _resume(self, run: BgRun) -> None:
        """Goes on in the dock with a run a pause or a T of the U1 stopped, from its step."""
        run.stop_asked = run.resume_ok = run.failed = False
        run.status, run.busy = "running", True
        run.step_at = self.host.reactor.monotonic()  # its step starts again: no earlier time counts
        head = run.state.slot.head
        step = next(iter(self._todo(run)), "loaded")
        lane = self._adapter().lane_text(run.state.new)
        self._say(f"head {head + 1}: background swap goes on in its dock from {step} (to {lane})")
        self.host.reactor.register_callback(lambda _eventtime: self._run(run))

    @staticmethod
    def _todo(run: BgRun) -> list[str]:
        """The steps of DOCK_STEPS still to run: all of them for a new run.

        A step done is skipped, the heat with the unload (it heats for the tip), and the grip
        once gripped.
        """
        skip = set(run.done)
        if "unload" in skip:
            skip.add("heat")
        if run.state.gripped:
            skip.add("grip")
        return [step for step in DOCK_STEPS if step not in skip]

    # --- the run in the dock (§3.2) ----------------------------------------------------------

    def _run(self, run: BgRun) -> None:
        """Runs the steps of DOCK_STEPS, until the end, a stop request, or a step that ends it.

        Each step returns False when the background swap ends at it. A failure is said and
        logged, and never pauses the print: the swap goes on from that step at the T.
        """
        steps: dict[str, Callable[[BgRun], bool]] = {
            "heat": self._heat,
            "unload": self._unload,
            "free": self._free,
            "roll back": self._unit_step,
            "hand over": self._unit_step,
            "push": self._unit_step,
            "grip": self._grip,
            "loaded": self._loaded,
        }
        head = run.state.slot.head
        todo = self._todo(run)
        whole = todo[:1] == ["heat"] and run.in_print  # the whole dock, in a print: timed
        began = self.host.reactor.monotonic()
        try:
            if "heat" not in todo:
                self._own(run)  # a run that goes on: the heater is its own again
            for step in todo:
                if run.stop_asked:
                    break
                run.step, run.skipped = step, False
                run.step_at = self.host.reactor.monotonic()
                logging.info(  # one line per step: the dock's time in a print, in klippy.log
                    "ace2k_u1: head %d: background swap step %s (to %s)",
                    head + 1,
                    step,
                    self._adapter().lane_text(run.state.new),
                )
                if not steps[step](run):
                    break
                if run.in_print and not run.stop_asked and not run.skipped:  # it ended by itself
                    took_s = self.host.reactor.monotonic() - run.step_at
                    self.host.swap_times.note_step(step, took_s)
            else:
                run.status = "finished"
                if whole and not run.stop_asked:
                    self.host.swap_times.note("dock", self.host.reactor.monotonic() - began)
        except Exception as err:
            logging.exception("ace2k_u1: head %d: the background swap at %s", head + 1, run.step)
            self._failed(run, err)
        finally:
            run.step_end = None
            self._end(run)

    def _heat(self, run: BgRun) -> bool:
        """Heats the head to the old filament's unload temperature and waits for it."""
        state = run.state
        self._own(run)
        target = self.host.swapper._unload_temp(cast("Hooked", state.old))
        return self._heat_to(run, target)

    def _own(self, run: BgRun) -> None:
        """The run owns the head's heater from now on; its target now is the one to hand back."""
        heater = self._heater(run.state.slot.head)
        run.start_target = heater.get_temp(self.host.reactor.monotonic())[1]
        run.owns = True

    def _heat_to(self, run: BgRun, target: float) -> bool:
        """Sets the head's target and waits until it is within HEAT_WITHIN_C of it.

        A target the head must climb to waits first for a free heating slot (_slot_free), so the
        U1 always keeps one for the printing head. The slot and the climb share one budget,
        HEAT_TIMEOUT_S. Returns False when a stop request ends a wait (at once), or when the
        budget runs out first: the background swap ends here, and its swap goes on at its T.
        """
        head = run.state.slot.head
        reactor = self.host.reactor
        heater = self._heater(head)
        give_up = reactor.monotonic() + HEAT_TIMEOUT_S
        said = False
        while self._climber(head, target) and not self._slot_free(head):
            if run.stop_asked:
                return False
            if reactor.monotonic() >= give_up:
                return self._heat_late(run, target)
            if not said:
                said = True
                logging.info("ace2k_u1: head %d: waits for a free heating slot", head + 1)
            reactor.pause(reactor.monotonic() + HEAT_POLL_S)
        self.set_temp(head, target)
        while not run.stop_asked:
            temp = heater.get_temp(reactor.monotonic())[0]
            if abs(temp - target) <= HEAT_WITHIN_C:
                return True
            if reactor.monotonic() >= give_up:
                return self._heat_late(run, target)
            reactor.pause(reactor.monotonic() + HEAT_POLL_S)
        return False

    def _heat_late(self, run: BgRun, target: float) -> bool:
        """Ends a heat whose budget ran out. Returns False."""
        self._say(
            f"head {run.state.slot.head + 1}: did not reach {target:g} °C within "
            f"{HEAT_TIMEOUT_S:g} s: its swap goes on at its T"
        )
        run.status = "stopped"
        return False

    # --- the U1's heating slots ----------------------------------------------------------------

    def _heaters(self) -> Heaters:
        return cast("Heaters", self.host.printer.lookup_object("heaters"))

    def _climber(self, head: int, target: float) -> bool:
        """Tells whether setting the head to target makes it climb on the U1. No side effect.

        Not when it is within CLIMB_DELTA_C of target (the U1 sets it at once), nor when it climbs
        already (it keeps its slot), nor for a target of 0 (cooling).
        """
        if target <= 0.0:
            return False
        temp = self._heater(head).get_temp(self.host.reactor.monotonic())[0]
        if abs(temp - target) <= CLIMB_DELTA_C:
            return False
        return self._heater_name(head) not in self._climbing_names()

    def _slot_free(self, head: int) -> bool:
        """Tells whether one heating slot stays free for a printing head once this head climbs.

        It counts the U1's climbing and queued heads, other than this one: fewer than the U1's
        limit less one. Lists the U1 does not have count as empty.
        """
        heaters = self._heaters()
        name = self._heater_name(head)
        busy = [
            n
            for n in self._climbing_names()
            + [self._entry_name(e) for e in getattr(heaters, "pending_extruders", ()) or ()]
            if n != name
        ]
        return len(busy) < self._max_climbing() - 1

    def _climbing_names(self) -> list[str | None]:
        entries = getattr(self._heaters(), "active_heating_extruders", ()) or ()
        return [self._entry_name(e) for e in entries]

    def _max_climbing(self) -> int:
        """The U1's MAX_HEATING_EXTRUDERS, from its heaters module, else U1_MAX_CLIMBING."""
        module = sys.modules.get(type(self._heaters()).__module__)
        value = getattr(module, "MAX_HEATING_EXTRUDERS", None)
        return value if isinstance(value, int) and value > 0 else U1_MAX_CLIMBING

    @staticmethod
    def _entry_name(entry: object) -> str | None:
        """The extruder name of an entry of the U1's lists: a name, (name, temp), or a heater."""
        if isinstance(entry, str):
            return entry
        if isinstance(entry, (tuple, list)) and entry:
            return Background._entry_name(entry[0])
        get_name = getattr(entry, "get_name", None)
        name = get_name() if callable(get_name) else getattr(entry, "name", None)
        return name if isinstance(name, str) else None

    def _heater_name(self, head: int) -> str | None:
        """The name the U1's lists know the head's heater by (its extruder's), or None."""
        name = (self._adapter().extruders or {}).get(head)
        if name is not None:
            return name
        return self._entry_name(self._heater(head))

    def _unload(self, run: BgRun) -> bool:
        """Forms the tip in the dock, as the swap's unload, with no G-code.

        Only when the head's sensor sees filament. The U1's tip for the head is queued whole, at
        once, and the old lane follows it. A tip that the dock cannot run ends the background
        swap here, and the unload runs at the T.
        """
        state = run.state
        adapter, swapper = self._adapter(), self.host.swapper
        head, old = state.slot.head, cast("Hooked", state.old)
        state.unloaded = False
        state.freed_mm = 0.0
        run.skipped = not adapter.head_set(state.new)
        if run.skipped:
            run.done.add("unload")  # an empty head has no tip to form
            return True
        tip = self.tip(self.host.printer, head, swapper._soft(head))
        why = tip if isinstance(tip, str) else self._tip_problem(tip)
        if why is not None:
            self._say(f"head {head + 1}: {why}: its unload runs at its T")
            run.status = "stopped"
            return False
        steps = cast("list[parked_module.TipStep]", tip)

        def queued() -> None:  # once the tip's first move is queued, not before
            adapter.follow.arm(old, PAUSE_WAIT_S)  # the old lane follows the tip, as in the swap
            run.tipped = True

        self.moves.run(head, self._moving(head), steps, self._note(old), queued)
        # as ACEK_SWAP_UNLOAD ends: toward the temperature the new filament loads at
        self.set_temp(head, swapper._load_temp_for(state))
        state.unloaded = True
        run.done.add("unload")
        return True

    @staticmethod
    def _tip_problem(tip: list[parked_module.TipStep]) -> str | None:
        """Returns why a parked move would refuse a step of the tip, or None.

        The whole tip is checked before any of it is queued, so it is never cut.
        """
        for step in tip:
            if step.kind == "dwell" and step.value / 1000.0 > parked_module.MAX_DWELL_S:
                return f"the tip pauses {step.value:g} ms"
            if step.kind == "move" and (
                abs(step.value) > parked_module.MAX_DISTANCE_MM
                or step.feed / 60.0 > parked_module.MAX_SPEED_MM_S
            ):
                return f"the tip moves {step.value:g} mm at F{step.feed:g}"
        return None

    def _free(self, run: BgRun) -> bool:
        """The swap's free check, without its second unload (a G-code).

        A tip that the gear still holds ends the background swap here: the unload runs again at
        the T, as the swap's own free check would run it.
        """
        state = run.state
        run.skipped = not state.unloaded  # no unload ran: nothing to check
        if state.unloaded:
            self._expect(run, self.host.swap_cfg.probe_mm, self.host.swap_cfg.probe_speed)
            try:
                state.freed_mm = self.host.swapper._probe().free_check(cast("Hooked", state.old))
            except ace2k_u1_probe.HeldError as err:
                self._say(f"head {state.slot.head + 1}: {err}: its unload runs again at its T")
                run.done.discard("unload")
                state.unloaded = False
                run.status = "stopped"
                return False
        run.done.add("free")
        return True

    def _expect(self, run: BgRun, length: float, speed: float) -> None:
        """Records when the unit move of the step should end, for the wait of the T."""
        run.step_end = self.host.reactor.monotonic() + length / max(speed, 1.0)

    def _unit_step(self, run: BgRun) -> bool:
        """Runs a step that moves the unit only (roll back, hand over, push), unchanged."""
        state, step = run.state, run.step
        cfg = self.host.swap_cfg
        speed = cfg.swap_speed_for(state.new.api)
        if step == "roll back":
            self._expect(run, cfg.splitter_mm[state.slot.head], speed)
        elif step == "push":
            self._expect(run, self._adapter().head_budget_mm, speed)
        self.host.swapper.swap_step(step, state)
        run.step_end = None
        run.done.add(step)
        return True

    def _grip(self, run: BgRun) -> bool:
        """Heats to the new tool's printing temperature, then takes the filament through the gear.

        One parked move of GRIP_MM, the load macro's own, plus background_purge_mm (none when 0),
        as one move with no stop between them, noted for the new lane, whose follow the push
        armed. It is queued whole, so it waits for the print's moves only once, at its start. The
        purge falls through the dock's opening as a strand. A purge covers the swap's load once
        the loaded check sees the filament (_loaded). With no purge, the load at the T purges at
        the cleaning area, without its grip.
        """
        state, swapper = run.state, self.host.swapper
        temp = run.grip_temp = swapper.load_print_temp(state)
        if not self._heat_to(run, temp):
            return False
        head = state.slot.head
        purge = max(0.0, self.host.swap_cfg.background_purge_mm)
        grip = parked_module.TipStep("move", GRIP_MM + purge, GRIP_SPEED_MM_S * 60.0)
        self.moves.run(head, self._moving(head), [grip], self._note(state.new))
        run.purged_mm = purge
        state.gripped = True
        return True

    def _loaded(self, run: BgRun) -> bool:
        """Reads the head's sensor after the grip. Empty, the whole load runs at the T.

        Set after a grip that purged, the load is done in the dock: the T heats the head and
        waits, and runs no load (Swapper._load_done). print_temp is the grip's temperature then,
        as the load sets it. A run stopped before this check leaves the load to the T.
        Only the sensor: the check of the new lane (Swapper._lane_fit) runs at the T, so a lane
        in error there still pauses the print.
        """
        state = run.state
        try:
            seen = self._adapter().head_set(state.new)
            why = "the head's sensor sees no filament after the grip"
        except Exception as err:
            seen, why = False, error_line(err)
        if not seen:
            state.gripped = False
            state.print_temp = None
            run.done.discard("load")
            self._say(f"head {state.slot.head + 1}: {why}: it loads at its T")
        elif state.gripped and run.purged_mm > 0.0:
            state.print_temp = run.grip_temp
            run.done.add("load")
        return True

    def _failed(self, run: BgRun, err: Exception) -> None:
        """Says why a step failed, and, for a move stopped from outside, the stop request that
        came meanwhile. The swap goes on from that step at the T.

        A failed free check or roll back with the head's sensor empty goes on from the hand
        over, as a swap that failed there goes on at its RESUME: the push finds the old lane if
        it is in the way.
        """
        run.status, run.failed = "stopped", True
        run.resume_ok = False  # it would fail again: the swap goes on at the T
        state = run.state
        if run.step in ("free", "roll back") and not self._head_set_or(state, True):
            run.done.update(("free", "roll back"))
        # A step whose move was stopped from outside while a stop request came: the line says why.
        why = ""
        if isinstance(err, StoppedError) and (run.drop_asked or run.stop_asked):
            why = " (the print ended)" if run.drop_asked else " (asked to stop)"
        self._say(
            f"head {state.slot.head + 1}: the background swap stopped at {run.step}: "
            f"{error_line(err)}{why}"
        )

    def _head_set_or(self, state: SwapState, default: bool) -> bool:
        try:
            return self._adapter().head_set(state.new)
        except Exception:
            return default

    def _end(self, run: BgRun) -> None:
        """Ends the callback: the swap's step for the T, the heater, a drop asked meanwhile."""
        state = run.state
        now = self.host.reactor.monotonic()
        run.in_step_s = 0.0 if run.step_at is None else now - run.step_at
        # Only a run that a stop request ended may go on in the dock: never one that finished,
        # failed, or that a step ended (a heater too slow, a tip the gear held).
        run.resume_ok = run.resume_ok and run.status == "running"
        if run.status == "running":
            run.status = "stopped"
        run.hand_over_steps()
        self._hand_back(run)
        run.busy = False
        head = state.slot.head
        logging.info(
            "ace2k_u1: head %d: the background swap %s at %s; its swap goes on from %s",
            head + 1,
            run.status,
            run.step,
            state.step,
        )
        if run.status == "finished":
            what = "it is ready" if "load" in run.done else "it loads"
            self._say(f"head {head + 1}: background swap done: {what} at its T")
        if run.drop_asked:
            self.drop(head)

    # --- the heater (§3.3) -------------------------------------------------------------------

    def _heater(self, head: int) -> Heater:
        ext = self._extruder(head)
        if ext is None:
            raise RuntimeError(f"head {head + 1} has no extruder")
        return ext.get_heater()

    def set_temp(self, head: int, temp: float) -> None:
        """Sets the head's target through the U1's heaters, with no wait and no G-code.

        A head that the U1 keeps queued (two heads climb already) is said in the log.
        """
        heater = self._heater(head)
        heaters: Heaters = self.host.printer.lookup_object("heaters")
        heaters.set_temperature(heater, temp, False)
        name = self._heater_name(head)
        pending = getattr(heaters, "pending_extruders", ()) or ()
        if name is not None and any(self._entry_name(entry) == name for entry in pending):
            logging.info("ace2k_u1: head %d: the U1 queues its heater (%g °C)", head + 1, temp)

    def owns(self, head: int) -> bool:
        """Tells whether a background swap owns the head's heater now."""
        run = self.runs.get(head)
        return run is not None and run.owns

    def held(self, gcmd: GCodeCommand, s: float | None) -> bool:
        """Tells whether a temperature call of the file is for a head a background swap owns.

        Such a call is recorded (its S, or s when the wraps capped it) and not applied.
        """
        if not self.runs or gcmd.get_int("A", 1) == 0 or gcmd.get("T", None) is None:
            return False
        head = self._tool_head(gcmd.get_int("T"))
        if head is None or not self.owns(head):
            return False
        value = s if s is not None else gcmd.get_float("S", 0.0)
        return self.hold(head, value, gcmd.get_commandline())

    def hold(self, head: int, value: float, what: str) -> bool:
        """Records value as the newest call for the head when a background swap owns its heater.

        Returns False, and records nothing, when none owns it. what names the call in the log.
        """
        if not self.owns(head):
            return False
        self.runs[head].called = value
        logging.info("ace2k_u1: head %d: %s recorded for after its background swap", head + 1, what)
        return True

    def _hand_back(self, run: BgRun) -> None:
        """Gives the head's heater back at the end of the callback.

        The newest call recorded, else the target at the start; 0 once the print has ended. A T
        that takes the state over sets its own target, so it is left as it is.
        """
        if not run.owns:
            return
        run.owns = False
        if run.leave_heater:
            run.heater_left = True
            return
        self._give_heater(run)

    def _heater_back(self, run: BgRun) -> None:
        """Gives the heater back when its hand-back was left to a T that did not take it."""
        run.leave_heater = False
        if run.heater_left:
            run.heater_left = False
            self._give_heater(run)

    def _give_heater(self, run: BgRun) -> None:
        """Sets the head to the newest call recorded, else its target at the start; 0 once the
        print it started in has ended."""
        head = run.state.slot.head
        temp = run.called if run.called is not None else run.start_target
        if run.in_print and self.host.u1.print_state() not in PRINT_ON:
            temp = 0.0  # the print it started in has ended
        try:
            if self._climber(head, temp) and not self._slot_free(head):
                self._hand_back_later(run, temp)  # a climb waits for a slot, as the heat does
                return
            self.set_temp(head, temp)
        except Exception:
            logging.exception("ace2k_u1: head %d: the heater after its background swap", head + 1)

    def _hand_back_later(self, run: BgRun, temp: float) -> None:
        """Tries the hand-back again each HAND_BACK_POLL_S until a heating slot is free.

        It is dropped when anything else sets the head's target meanwhile (a T, a swap, the
        file), when a background swap owns the head again, or when the print ends (the head is
        then turned off).
        """
        head, reactor = run.state.slot.head, self.host.reactor
        then = self._heater(head).get_temp(reactor.monotonic())[1]
        logging.info(
            "ace2k_u1: head %d: waits for a free heating slot to go to %g °C", head + 1, temp
        )

        def retry(eventtime: float) -> float:
            try:
                if self.owns(head) or self._heater(head).get_temp(eventtime)[1] != then:
                    return reactor.NEVER
                if run.in_print and self.host.u1.print_state() not in PRINT_ON:
                    self.set_temp(head, 0.0)
                    return reactor.NEVER
                if self._climber(head, temp) and not self._slot_free(head):
                    return eventtime + HAND_BACK_POLL_S
                self.set_temp(head, temp)
            except Exception:
                logging.exception(
                    "ace2k_u1: head %d: the heater after its background swap", head + 1
                )
            return reactor.NEVER

        reactor.register_timer(retry, reactor.monotonic() + HAND_BACK_POLL_S)

    # --- the stop, the arrival, the inline swap (§3.5-§3.7) ----------------------------------

    def _tool_head(self, n: int) -> int | None:
        """Returns the head of tool n: its lane's in the plan, else the U1's tool table."""
        h = self.host.tracker.tool_lane(n)
        if h is not None:
            return h.head
        try:
            return self.host.u1.map_table()[n]
        except Exception:
            return None

    def _left_s(self, head: int, run: BgRun | None) -> float:
        """Returns how long a T would wait for the head: its run's unit move, its parked moves."""
        ext = self._extruder(head)
        left = 0.0 if ext is None else self.mover.remaining_s(cast("MovingExtruder", ext))
        if run is not None and run.busy and run.step_end is not None:
            left = max(left, run.step_end - self.host.reactor.monotonic())
        return left

    def _idle(self, head: int, run: BgRun | None) -> bool:
        """No callback runs for the head and no parked move of it has still to end."""
        if run is not None and run.busy:
            return False
        ext = self._extruder(head)
        return ext is None or self.mover.remaining_s(cast("MovingExtruder", ext)) <= 0.0

    def stop_and_wait(
        self,
        gcmd: GCodeCommand | None,
        head: int,
        run: BgRun | None,
        leave_heater: bool,
        *,
        u1_pick: bool = False,
    ) -> None:
        """Asks the head's run to stop at its next safe point and waits for it, and for the end of
        the head's last parked move.

        u1_pick: a T of the U1 (A0) picks the head. It waits where it is (it runs inside the U1's
        own macros, which place the head themselves), and the run goes on in the dock once the head
        is parked again (ask_stop). Otherwise, with a G-code (gcmd), X and Y homed and the
        print not paused, a wait that is expected to last over NEAR_WAIT_S, or that has lasted that
        long, takes the active head to the cleaning area first, so it never waits over the part. The
        wait is read again at each poll: the steps can last longer than they were expected to. From
        a timer (no gcmd) nothing moves. A head that went there is kept at its printing temperature
        while it waits (_keep_hot) when the file's cool-down came just before the T, and waits for
        it (M109) when the wait ends. A head that went there and is hot is then cleaned there
        (WAIT_CLEAN), before it leaves; never after a wait that ended in an error, and a clean that
        fails is only said. The file's cool-down is set again at the end, error or not.
        """
        if run is not None:
            self.ask_stop(run, leave_heater, resume_after=u1_pick)
        if self._idle(head, run):
            return
        u1, reactor = self.host.u1, self.host.reactor
        # Never in a paused print, or one being paused (it leaves the part), nor without X and Y
        # homed (a head typed at the bench may not be). Otherwise the head waits at the cleaning
        # area, in a print or not, and never over the bed.
        axes = u1.homed_axes()
        homed = "x" in axes and "y" in axes
        move = not u1_pick and gcmd is not None and not u1.pause_taken() and homed
        since = reactor.monotonic()
        give_up = since + STOP_WAIT_MAX_S
        moved = False
        hot: tuple[int, float, float] | None = None  # (active head, printing, the file's target)
        try:
            while not self._idle(head, run):
                now = reactor.monotonic()
                if move and not moved and max(self._left_s(head, run), now - since) > NEAR_WAIT_S:
                    u1.script(f"SAVE_GCODE_STATE NAME={WAIT_STATE}")
                    moved = True
                    u1.script(DISCARD_POSITION)
                    hot = self._keep_hot()
                if now >= give_up:
                    line = (
                        f"ace2k_u1: head {head + 1}: its background swap did not stop within "
                        f"{STOP_WAIT_MAX_S:g} s"
                    )
                    raise RuntimeError(line) if gcmd is None else gcmd.error(line)
                reactor.pause(now + WAIT_POLL_S)
            if hot is not None:
                u1.script(f"M109 S{hot[1]:g} T{hot[0]} A0")  # hot before the clean
            if moved and (hot is not None or self._active_hot()):
                self._clean_after_wait(head)
        finally:
            try:
                if hot is not None:  # it docks at the slicer's standby, as without the wait
                    u1.script(f"M104 S{hot[2]:g} T{hot[0]} A0")
            finally:
                if moved:
                    u1.script(f"RESTORE_GCODE_STATE NAME={WAIT_STATE} MOVE=0")

    @staticmethod
    def ask_stop(run: BgRun, leave_heater: bool = False, resume_after: bool = False) -> None:
        """Asks the run to stop at its next safe point.

        A T or a swap of its own head takes it, or keeps it for its T, and so does a stop by
        hand. With resume_after (a pause, or a T of the U1, A0, that picks the head: its
        print-start check, its own load), a run that this request stops goes on in the dock once
        the print prints and the head is docked (check); a run stopped already keeps what its
        stop decided.
        """
        if resume_after:
            if run.busy and not run.stop_asked:
                run.resume_ok = True
        else:
            run.resume_ok = False
        run.stop_asked = True
        run.leave_heater = run.leave_heater or leave_heater

    def _keep_hot(self) -> tuple[int, float, float] | None:
        """Sets the active head back to its printing temperature while it waits, with no wait.

        The file's cool-down of the tool that leaves comes just before the T. A head that waits
        at the cleaning area would cool there, and dock with a cold blob, uncleaned. The
        temperature is the target the head had before that cool-down (PrintTracker.
        printing_before). Returns (head, that temperature, the cool-down), or None when no
        cool-down of the file is known for the head's target (outside a print, or none sent):
        the target is then left as it is. Only the active head climbs for it.
        """
        try:
            active = self._adapter().active_head(self.host.u1)
            if active is None:
                return None
            target = self._heater(active).get_temp(self.host.reactor.monotonic())[1]
        except Exception:
            logging.exception("ace2k_u1: the active head's target while it waits")
            return None
        temp = self.host.tracker.printing_before(active, target)
        if temp is None:
            return None
        self.host.u1.script(f"M104 S{temp:g} T{active} A0")
        logging.info(
            "ace2k_u1: head %d waits at the cleaning area at %g °C, not at the file's %g °C",
            active + 1,
            temp,
            target,
        )
        return active, temp, target

    def _active_hot(self) -> bool:
        """Tells whether the active head may extrude (above min_extrude_temp). Unread: False.

        A cold nozzle is never dragged across the wiper.
        """
        try:
            name = self.host.printer.lookup_object("toolhead").get_extruder().get_name()
            ext = self.host.u1.extruder(name)
            return bool(ext is not None and getattr(ext.get_heater(), "can_extrude", False))
        except Exception:
            return False

    def _clean_after_wait(self, head: int) -> None:
        """Cleans the active head at the cleaning area, where it waited. A failure is only said."""
        try:
            self.host.u1.script(WAIT_CLEAN)
        except Exception as err:
            logging.exception("ace2k_u1: head %d: the clean after the wait", head + 1)
            self._say(f"head {head + 1}: the clean after the wait failed: {error_line(err)}")

    def _take(self, head: int) -> SwapState:
        """Takes the state of the head's background swap: the swap of today owns it now."""
        run = self.runs.pop(head)
        self._adapter().swapping.discard(head)
        return run.state

    def before_tool(
        self, gcmd: GCodeCommand, n: int, owed: OwedTool | SwapState | None = None
    ) -> bool | None:
        """Runs first in every T<n>, the U1's own (A0) included. None: the T goes on as today.

        A T never picks a head whose parked moves have not ended, nor one whose background swap
        still runs: it stops the run and waits. For a T of the U1 (A0, n is the head itself) the
        state is kept for the head's own T. A T of the file whose head has a background swap
        takes its state (arrive). owed is the owed T that a RESUME runs, whose own parameters are
        kept. With no background swap and no parked move queued, it returns at once.
        """
        if not self.runs and not self.mover.queued():
            return None
        if gcmd.get_int("A", 1) == 0:
            if 0 <= n < LANES:
                run = self.runs.get(n)
                self.stop_and_wait(gcmd, n, run, leave_heater=False, u1_pick=True)
            return None
        head = self._tool_head(n)
        if head is None:
            return None
        run = self.runs.get(head)
        if run is None:
            self.stop_and_wait(gcmd, head, None, leave_heater=False)
            return None
        return self.arrive(gcmd, n, run, owed)

    def u1_loads(self, h: Hooked) -> None:
        """The U1 loads the head of lane h (its FEED_AUTO LOAD=1, ace2k_u1_hooks._push_into_drive).

        That load pushes the head's current lane to the head, and before the hand over that is
        still the old lane: after the tip, and after the roll back too (bench B7: the old filament
        was held by the gear again at the T). So the unload, the free check and the roll back of
        a kept run are not done any more: it goes on from the unload, in the dock once the head is
        parked again, or at its T. A pick of the head alone moves no filament (the U1's T moves X,
        Y and Z only) and keeps the run's progress. A run that a pending swap or a refill of its
        head keeps (yielded) is left as it is: that swap unloads and loads the head itself. A run
        still busy is stopped first and waited for. It never raises into the U1's load.
        """
        run = self.runs.get(h.head)
        if run is None or run.yielded or "unload" not in run.done or "hand over" in run.done:
            return
        try:
            if run.busy:
                self.stop_and_wait(None, h.head, run, leave_heater=False, u1_pick=True)
            state = run.state
            run.done -= {"unload", "free", "roll back"}
            state.unloaded, state.freed_mm = False, 0.0
            run.hand_over_steps()
        except Exception:
            logging.exception("ace2k_u1: head %d: the U1's load of a kept background swap", h.head)
            return
        self._say(
            f"head {h.head + 1}: loaded by the U1 after its tip: the tip is formed again (its "
            "swap goes on from unload)"
        )

    def yield_ended(self, head: int) -> None:
        """The pending swap or refill that the head's run gave way to has ended (or failed)."""
        run = self.runs.get(head)
        if run is not None:
            run.yielded = False

    @contextlib.contextmanager
    def picking(self, gcmd: GCodeCommand, n: int) -> Iterator[None]:
        """Marks the head that a T of the U1 (A0) picks while the T runs.

        The U1's T moves before the active head changes, and the watch tick may run meanwhile:
        no background swap starts or goes on on that head then (_not_docked).
        """
        head = n if self.heads and gcmd.get_int("A", 1) == 0 and 0 <= n < LANES else None
        if head is not None:
            self.picks.append(head)
        try:
            yield
        finally:
            if head is not None:
                self.picks.remove(head)

    def arrive(
        self, gcmd: GCodeCommand, n: int, run: BgRun, owed: OwedTool | SwapState | None = None
    ) -> bool | None:
        """Runs a T of the file whose head has a background swap. None: the T goes on as today.

        The background swap stops and the T waits for it. Its state then goes on as the swap of
        today (Swapper.resume_background). If the plan now wants another lane for the T, a state
        before its hand over takes that lane; a later one finishes first, and the T then swaps as
        today. Returns what the swap returns: False when it paused the print.
        """
        swapper = self.host.swapper
        params = owed.tool_params if owed is not None else swapper._tool_params(gcmd)
        called = self.host.reactor.monotonic()
        # A T of the file, while printing: a T typed at the console is not timed.
        printing = self._adapter().print_state() == "printing" and self.host.u1.from_file()
        # The run as the T finds it, before the wait: a step that ends during the wait is dock
        # work left at the T, not a head that came ready.
        finished = run.status == "finished"  # a finished run without its load gives no sample
        ready = finished and "load" in run.done
        # The steps done are the ones _todo skips: the heat with the unload, the grip once gripped;
        # and the loaded check once the load is done (run.done names it "load").
        loaded = {"loaded"} if "load" in run.done else set()
        done = (frozenset(DOCK_STEPS) - set(self._todo(run))) | loaded
        step = run.step
        in_step = called - run.step_at if run.busy and run.step_at is not None else run.in_step_s
        self.stop_and_wait(gcmd, run.state.slot.head, run, leave_heater=True)
        took = self._take_over(gcmd, run, n, params, owed is not None)
        if not took:
            return took
        slot, lane = run.state.slot, self.host.tracker.tool_lane(n)
        if lane is not None and slot.current is not lane:
            return swapper.swap(gcmd, slot, lane, n, params=params)
        if printing and run.in_print and swapper.printing_on():  # not paused meanwhile: timed
            took_s = self.host.reactor.monotonic() - called
            if ready:
                self.host.swap_times.note("ready", took_s)
            elif not finished and not run.failed:  # a failure's stop holds its recovery
                self.host.swap_times.note_resume(took_s, step, in_step, done)
        return True

    def _take_over(
        self,
        gcmd: GCodeCommand,
        run: BgRun,
        n: int,
        params: ToolParams | None,
        owed: bool,
    ) -> bool | None:
        """The swap of today takes the run's state for T<n>, and its lane. None: nothing ran.

        With a swap pending, the state is kept for after it. A T that keeps the old lane before
        anything was done drops it. Either way the heater is given back. owed tells that T<n> is
        owed already (a RESUME runs it), so a failure owes it no second time.
        """
        swapper = self.host.swapper
        head = run.state.slot.head
        if swapper.swap_pending is not None:
            self._heater_back(run)
            return None  # the T is refused as today; the state is kept for after it
        state = self._take(head)
        lane = self.host.tracker.tool_lane(n)
        finish_first = False
        if lane is not None and lane is not state.new:
            if "hand over" in state.done_in_dock:
                finish_first = True  # its own lane first, then the T swaps as today
            elif lane is state.old and not state.done_in_dock:
                self._heater_back(run)
                self._say(f"head {head + 1}: T{n} keeps its lane: the background swap is dropped")
                return None
            else:
                state.new = lane
        self._keep_called(run, n)
        state.owed = finish_first or owed  # a failure then owes T<n>, not the run's own tool
        if not finish_first:
            state.tool, state.tool_params = n, params
        if not swapper.resume_background(gcmd, state):
            if finish_first and not owed:
                swapper.tools_owed.append(OwedTool(n, params))
            return False
        return True

    def _keep_called(self, run: BgRun, n: int) -> None:
        """Keeps the call recorded meanwhile for T<n>, when it is a printing temperature of it.

        The swap of T<n> then loads at it (Swapper._swap_temp), as at a call the tracker kept.
        """
        tracker, value = self.host.tracker, run.called
        plan = tracker.plan
        if plan is None or value is None or not tracker.printing_temp(n, value):
            return
        plan.kept[n] = value
        plan.kept_at[n] = tracker.t_raw

    def before_inline(
        self,
        gcmd: GCodeCommand | None,
        slot: Slot | None,
        tool: int | None,
        params: ToolParams | None,
        owed: bool,
    ) -> bool | None:
        """Runs before an inline swap (Swapper.swap), a pending swap and a refill of slot's head:
        the background swap of that head gives way. The runs of the other heads go on.

        The head's run stops and is waited for. For a swap with a T<tool>, its state goes on
        first, for that T and lane as at an arrival. Otherwise (a refill, a pending swap) it is
        kept for its head's T, and yielded until that swap ends (yield_ended). Returns what that
        swap returned, or None when there was none.
        """
        run = None if slot is None else self.runs.get(slot.head)
        if slot is None or run is None:
            return None
        run.yielded = True  # that swap unloads and loads the head itself
        try:
            self.stop_and_wait(gcmd, slot.head, run, leave_heater=False)
            if gcmd is None or tool is None:
                return None
            return self._take_over(gcmd, run, tool, params, owed)
        finally:
            if gcmd is not None and tool is not None:
                run.yielded = False  # a run the T's swap did not take is a run as before

    def on_pause(self) -> None:
        """A paused print: every running background swap stops at its next safe point.

        Its state is kept. Once the print prints again, it goes on in the dock from the step it
        reached (check), or its head's T takes it.
        """
        for run in self.runs.values():
            if run.busy:
                self.ask_stop(run, resume_after=True)

    def print_over(self) -> None:
        """The print ended: every background swap stops, and its state is dropped."""
        self.no_ff_said = False
        for head, run in list(self.runs.items()):
            if not run.in_print:
                continue
            if run.busy:
                self.ask_stop(run)
                run.drop_asked = True
            else:
                self.drop(head)

    def drop(self, head: int) -> None:
        """Drops the state of the head's background swap.

        When its steps moved anything (a step done, or a tip queued), the head may be half
        swapped: its lane is set unknown, so the next swap of that head probes it first.
        """
        run = self.runs.get(head)
        if run is None:
            return
        self._take(head)
        self._say(
            f"head {head + 1}: background swap dropped at {run.step} "
            f"({self.host.swapper.swap_text(run.state)})"
        )
        if run.done or run.tipped:
            self._adapter().set_current(run.state.slot, None)
            self._say(f"head {head + 1}'s lane is now unknown: its next swap probes it")

    # --- the status and the commands (§4) ----------------------------------------------------

    def status(self) -> list[dict[str, object] | None]:
        """Per head, None for a head not listed, else {state, step, lane} (get_status)."""
        out: list[dict[str, object] | None] = [None] * LANES
        for head in self.heads:
            run = self.runs.get(head)
            if run is None:
                out[head] = {"state": "idle", "step": None, "lane": None}
                continue
            step = run.step if run.busy else run.state.step
            lane = self._adapter().lane_text(run.state.new)
            out[head] = {"state": run.status, "step": step, "lane": lane}
        return out

    def status_lines(self) -> list[str]:
        """The lines of ACEK_BG_STATUS, one per listed head."""
        if not self.heads:
            return ["ace2k_u1: no head swaps in the background (background_heads is empty)"]
        lines = []
        for head, entry in enumerate(self.status()):
            if entry is None:
                continue
            state = entry["state"]
            if state == "idle":
                what = "idle"
            elif state == "running":
                what = f"running at {entry['step']}, to {entry['lane']}"
            elif state == "finished" and self.runs[head].state.gripped:
                purge = self.runs[head].purged_mm
                if "load" in self.runs[head].state.done_in_dock:
                    done = f"gripped and purged {purge:g} mm: it is ready at its T"
                else:
                    done = "gripped: it purges at its T"
                what = f"finished, {entry['lane']} {done}"
            elif state == "finished":
                what = f"finished, to {entry['lane']}, not gripped: it grips at its T"
            else:
                what = f"stopped, to {entry['lane']}: its T goes on from {entry['step']}"
            lines.append(f"ace2k_u1: head {head + 1}: background swap {what}")
        return lines

    def cmd_ACEK_BG_STATUS(self, gcmd: GCodeCommand) -> None:
        for line in self.status_lines():
            gcmd.respond_info(line)

    def _bench_head(self, gcmd: GCodeCommand, what: str) -> int:
        """Reads HEAD= (1-4) of a bench command.

        It is refused in a print, on the active head, on a head not in background_heads, and on
        a head with a background swap kept or running (another head's may run).
        """
        head = gcmd.get_int("HEAD", minval=1, maxval=LANES) - 1
        if self.host.adapter is None:
            raise gcmd.error(f"ace2k_u1: {what} refused: the adapter is not hooked")
        if self.host.u1.print_state() in PRINT_ON:
            raise gcmd.error(f"ace2k_u1: {what} refused: a print is printing or paused")
        if head == self._adapter().active_head(self.host.u1):
            raise gcmd.error(f"ace2k_u1: {what} refused: head {head + 1} is the active head")
        if head not in self.heads:
            raise gcmd.error(
                f"ace2k_u1: {what} refused: head {head + 1} is not in background_heads"
            )
        if head in self.runs:
            raise gcmd.error(f"ace2k_u1: {what} refused: a background swap is kept or running")
        return head

    def cmd_ACEK_BG_SWAP(self, gcmd: GCodeCommand) -> None:
        """ACEK_BG_SWAP HEAD=n LANE=u:l: a background swap by hand, outside a print (bench)."""
        what = "ACEK_BG_SWAP"
        head = self._bench_head(gcmd, what)
        slot = self._adapter().slot_of_head(head)
        if slot is None or slot.current is None:
            raise gcmd.error(f"ace2k_u1: {what} refused: head {head + 1}'s lane is not known")
        text = (gcmd.get("LANE", "") or "").strip().lower()
        new = next((h for h in slot.lanes if self._lane_names(h) & {text}), None)
        if new is None:
            raise gcmd.error(f"ace2k_u1: {what} refused: {text!r} is not a lane of head {head + 1}")
        if new is slot.current:
            raise gcmd.error(f"ace2k_u1: {what} refused: {text} is already in head {head + 1}")
        if not self._bay_full(new):
            raise gcmd.error(f"ace2k_u1: {what} refused: {text}'s bay is empty")
        if not self._has_ff(slot):
            raise gcmd.error(f"ace2k_u1: {what} refused: the ace2k host has no note_move")
        self.start(slot, new, None, in_print=False)

    def _lane_names(self, h: Hooked) -> set[str]:
        """The names a lane may be given by: as the console writes it, and as unit:lane."""
        name = getattr(h.unit, "name", None) or "a"
        return {self._adapter().lane_text(h).lower(), f"{name}:{h.lane + 1}".lower()}

    def cmd_ACEK_BG_STOP(self, gcmd: GCodeCommand) -> None:
        """ACEK_BG_STOP HEAD=n: stops a running background swap, or drops a kept one."""
        head = gcmd.get_int("HEAD", minval=1, maxval=LANES) - 1
        run = self.runs.get(head)
        if run is None:
            raise gcmd.error(f"ace2k_u1: head {head + 1} has no background swap")
        if run.busy:
            self.ask_stop(run)  # stopped by hand: it waits for its T
            gcmd.respond_info(f"ace2k_u1: head {head + 1}: background swap stops at its next step")
            return
        self.drop(head)

    def cmd_ACEK_BG_MOVE(self, gcmd: GCodeCommand) -> None:
        """ACEK_BG_MOVE HEAD=n DISTANCE=mm [VELOCITY=mm/s]: one parked move (bench).

        It runs as every parked move does (ace2k_u1_dock_moves), so it answers once the move has
        ended.
        """
        what = "ACEK_BG_MOVE"
        head = self._bench_head(gcmd, what)
        distance = gcmd.get_float("DISTANCE")
        speed = gcmd.get_float("VELOCITY", 5.0)
        ext = self._extruder(head)
        if ext is None:
            raise gcmd.error(f"ace2k_u1: {what} refused: head {head + 1} has no extruder")
        step = parked_module.TipStep("move", distance, speed * 60.0)
        try:
            start, end = self.moves.run(head, cast("MovingExtruder", ext), [step], None)
        except (parked_module.ParkedMoveError, TangleOffError) as err:
            raise gcmd.error(f"ace2k_u1: {what} refused: {err}") from None
        gcmd.respond_info(
            f"ace2k_u1: head {head + 1}: parked move of {distance:g} mm at {speed:g} mm/s, "
            f"{end - start:.1f} s"
        )
