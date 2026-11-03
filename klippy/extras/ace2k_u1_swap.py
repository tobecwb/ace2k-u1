"""Swaps the lane of a head that several lanes feed.

It holds the steps of the swap. When a step fails, the print pauses plainly with the swap
pending, and the RESUME goes on with it. It also holds the macros, the temperatures and the roll
back of the swap. The refill of a spool that ran out in such a head belongs to ace2k_u1_refill.
ace2k_u1.py wraps the commands of the U1 and calls the Swapper.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_config import SwapConfig
    from .ace2k_u1_printer import Spool, U1Printer
    from .ace2k_u1_printer_types import Extruder, GCodeCommand, Reactor
    from .ace2k_u1_probe import HeadProbe
    from .ace2k_u1_spools import LaneSpools
    from .ace2k_u1_tracker import PrintTracker

try:
    from . import ace2k_u1_probe
    from .ace2k_u1_common import (
        PAUSE_WAIT_S,
        Hooked,
        Slot,
        error_line,
        finite,
        setting_written,
    )
    from .ace2k_u1_config import material_param
    from .ace2k_u1_hooks import INNER_PREEXTRUDE, PREEXTRUDE
    from .ace2k_u1_rollback import Rollback
    from .ace2k_u1_tags import TAG_QUOTED, TagWriter
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_probe  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        PAUSE_WAIT_S,
        Hooked,
        Slot,
        error_line,
        finite,
        setting_written,
    )
    from ace2k_u1_config import material_param  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_hooks import (  # type: ignore[import-not-found, no-redef]
        INNER_PREEXTRUDE,
        PREEXTRUDE,
    )
    from ace2k_u1_rollback import Rollback  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_tags import TAG_QUOTED, TagWriter  # type: ignore[import-not-found, no-redef]

# The steps of a lane swap, in order. A failure pauses the print, and RESUME restarts at the step
# that failed. The work at the nozzle is done by two macros of the config, which the operator may
# edit. They never move the unit. The steps are these.
# - The unload forms the tip.
# - The free check and the roll back of the old lane follow.
# - The push of the new lane.
# - The spool.
# - The load (heated to the printing temperature, grip, purge, the U1's clean).
# - A read of the sensor of the head.
# - The U1's own pre-extrusion: one clean and its idle exit.
# The swap makes no retraction of its own. It makes no move to the part either, because the next
# print move takes the head there.
SWAP_STEPS = (
    "unload",
    "free",
    "roll back",
    "hand over",
    "push",
    "spool",
    "load",
    "loaded",
    "return",
)
SWAP_UNLOAD_MACRO = "ACEK_SWAP_UNLOAD"
SWAP_LOAD_MACRO = "ACEK_SWAP_LOAD"
# The steps after the load of the swap. A failure at one of them goes on from that step at RESUME,
# with no probe, while the head reads set (the load put the new lane in the head).
LOADED_STEPS = SWAP_STEPS[SWAP_STEPS.index("loaded") :]
# The push and the steps after it, up to the loaded check. A failure at one of them, with the head
# set at RESUME and the push's record (SwapState.pushed_alone), goes on from the spool, unprobed.
PUSHED_STEPS = ("push", "spool", "load")
# For the end of a spool in a shared head. The rest of the spool rolls back out of the bay, and the
# tip of the new spool is pushed from the bay to the head. That is farther than the push of
# head_budget_mm from behind the splitter. This is why REFILL_EXTRA_MM is 500: a push of 1 999 mm
# stopped short of the sensor, after 1 986 mm out.
REFILL_EXTRA_MM = 500.0
DEFAULT_NOZZLE_MM = 0.4  # for a nozzle whose extruder does not say its diameter
# The U1's own pre-extrusion length. The swap runs it with LENGTH=0, and adds this to
# printing_e_pos itself, as the U1 counted it when the pre-extrusion purged.
U1_PREEXTRUDE_MM = 20.0
U1_UNLOAD_TEMP = 250.0  # the unload temperature of the U1 when its filament table cannot tell
U1_LOAD_TEMP = 250.0  # the load temperature of the U1 when its filament table cannot tell


class _Unread(Enum):
    """The value of params in swap that means the parameters are read from the gcmd of the T."""

    UNREAD = "unread"


UNREAD: Final = _Unread.UNREAD


class ToolParams(TypedDict):
    """The command, line and parameters of a T, so that it can be built again.

    Swapper._tool_params makes it. The keys are read by name, which vulture does not see. The
    noqa on a key says so.
    """

    command: str  # noqa: V107
    commandline: str  # noqa: V107
    params: dict[str, str]  # noqa: V107


class NoSpoolLine(str):
    """The line that set_head_spool returns for a lane with no spool set (no_spool_line).

    Its own type tells this notice from a setting that failed, whatever the wording.
    """

    __slots__ = ()


@dataclass
class SwapState:
    """A lane swap on the head of slot for T<tool>: the old lane goes out and the new lane comes in.

    The swap is pending (swap_pending) while a failure has paused it. A refill is a swap of a
    lane for itself.
    """

    slot: Slot
    old: Hooked | None  # None means the lane of the head is not known (it is probed first)
    new: Hooked
    tool: int | None  # None means a refill with no tool of the plan on the lane
    # The step it runs or failed at. "restore" means that only the restore is left.
    step: str = SWAP_STEPS[0]
    saved: bool = False  # the G-code state of the print is saved (once, before the first step)
    e_pos: float | None = None  # printing_e_pos of the head, saved with it; None means unread
    started: float | None = None  # when the swap started (reactor time), for its duration
    # The T that asked for the swap. The RESUME after a failure runs it (_run_tool). None means it
    # was not read.
    tool_params: ToolParams | None = None
    refill: bool = False  # a refill after a spool ran out (ace2k_u1_refill)
    timer: bool = False  # run by the pause timer; RESUME waits for it (refill_ready)
    probed: bool = False  # its head was probed at RESUME; a swap not yet saved does not probe again
    owed: bool = False  # started by an owed T at RESUME, so its T is owed already
    unloaded: bool = False  # the unload macro of this attempt ran; the free check follows it
    freed_mm: float = 0.0  # how far the free check of this attempt rolled the old lane back
    lane_error: str | None = None  # the error of the new lane after its load, once (_lane_fit)
    print_temp: float | None = None  # the PRINT_TEMP its load macro left the head at
    # The head read empty before the last push of the new lane, and that push ended with only the
    # new lane moved toward the head since. A sensor set after it is the new lane (_probe_head).
    pushed_alone: bool = False
    # When the rest of a refill ended with its tail at the sensor of the bay (reactor time). RESUME
    # then waits for the bay to empty (refill_ready).
    tail_at_bay_s: float | None = None


@dataclass
class OwedTool:
    """A T that was refused while a swap was pending. It is owed to RESUME (tools_owed).

    It keeps its own command (tool_params, as in SwapState). The state of a pending swap is owed
    the same way.
    """

    tool: int
    tool_params: ToolParams | None


class SwapRefiller(Protocol):
    """The Refiller (ace2k_u1_refill), as a swap that goes on at its RESUME asks it."""

    def refill_ready(self, pending: SwapState) -> bool: ...
    def spool_known(self, pending: SwapState) -> bool: ...


class SwapHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the swap reads it.

    It gives the adapter and the probe. They are built at the hook, before any swap. The probe
    exists only with a head that several lanes feed, and only such heads swap. It also gives the
    printer, the reactor, the U1, the plan, the spools, the swap config and the Refiller.
    """

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def probe(self) -> HeadProbe | None: ...
    @property
    def printer(self) -> Printer: ...
    @property
    def reactor(self) -> Reactor: ...
    @property
    def u1(self) -> U1Printer: ...
    @property
    def tracker(self) -> PrintTracker: ...
    @property
    def spool_lanes(self) -> LaneSpools: ...
    @property
    def swap_cfg(self) -> SwapConfig: ...
    @property
    def refiller(self) -> SwapRefiller: ...
    def head_set_or(
        self, h: Hooked, default: bool, unread: str | Callable[[], str] | None = None
    ) -> bool: ...


class Swapper:
    """Runs the lane swap of a head that several lanes feed.

    host is the ACE2kU1. Its adapter and probe are built at the hook. Its U1, its plan and its
    spools are read when used.
    """

    def __init__(self, host: SwapHost) -> None:
        self.host = host
        # The roll back of the old lane of a swap, or of the rest of a refill.
        self.rollback: Rollback = Rollback(host)
        # The swap that a failure paused. RESUME probes its head and finishes the swap from what the
        # probe reads. Then RESUME runs the T that asked for the swap.
        self.swap_pending: SwapState | None = None
        self.resuming = False  # a RESUME is running a pending swap; the status says so
        # The T commands that are owed, in the order they came. They are the T of a pending swap and
        # any T that was refused while a swap was pending. Each is an OwedTool. The SwapState of a
        # pending swap counts as one, because it has the same two fields. RESUME runs them in order
        # through the T path (_owed_tools), before the RESUME of the U1.
        self.tools_owed: list[OwedTool | SwapState] = []
        # Each step of SWAP_STEPS and its method, in order (swap_step).
        self.steps: dict[str, Callable[[SwapState], None]] = {
            "unload": self._step_unload,
            "free": self._step_free,
            "roll back": self._step_roll_back,
            "hand over": self._step_hand_over,
            "push": self._step_push,
            "spool": self._step_spool,
            "load": self._step_load,
            "loaded": self._step_loaded,
            "return": self._step_return,
        }

    def _adapter(self) -> Adapter:
        """Returns the adapter of the host. It is built at the hook, before any swap runs."""
        return cast("Adapter", self.host.adapter)

    def _probe(self) -> HeadProbe:
        """Returns the probe of the host.

        It is built at the hook when a head is fed by several lanes. A head of one lane refills
        with runout_source set to unit, and uses the insister of the adapter, a probe as well.
        """
        return cast("HeadProbe", self.host.probe or self._adapter().insister)

    def missing_swap_macros(self) -> list[str]:
        """Returns the macros of the swap that the config of the printer does not define.

        The unload macro comes first. A macro is its command. Klipper registers the command in
        upper case, whatever the case of the name of its section. The macro object under that
        name is the fallback.
        """
        gcode = self.host.printer.lookup_object("gcode", None)
        handlers = getattr(gcode, "ready_gcode_handlers", None)
        if not isinstance(handlers, dict):
            handlers = {}
        return [
            name
            for name in (SWAP_UNLOAD_MACRO, SWAP_LOAD_MACRO)
            if name not in handlers
            and self.host.printer.lookup_object(f"gcode_macro {name}", None) is None
        ]

    def _refuse(self, gcmd: GCodeCommand, n: int, pending: SwapState) -> bool:
        """Refuses a T while a swap is pending. The console says so, and the print pauses plainly.

        Returns False. A T of the file (while the print is printing) is owed, and RESUME runs it
        after the swap. A T typed at the console while paused is not owed. A T is never owed twice
        in a row, because the same T again changes nothing.
        """
        if not self.host.u1.paused():
            params = self._tool_params(gcmd)
            last = self.tools_owed[-1] if self.tools_owed else None
            if last is None or (last.tool, last.tool_params) != (n, params):
                self.tools_owed.append(OwedTool(n, params))
        line = (
            f"T{n} refused: {self.swap_text(pending)} is pending at {pending.step} — RESUME it "
            "first"
        )
        self._adapter().say(f"ace2k_u1: {line}")
        self.pause(gcmd, line)
        return False

    def pause(self, gcmd: GCodeCommand | None, line: str, timer: bool = False) -> None:
        """Pauses the print plainly after a failure of the swap.

        The head parks at the discard position. The print goes on from the next line, and there
        is no homing at the resume.

        The PAUSE runs from the command that failed, under the G-code lock that this command
        holds (run_script would wait for that lock forever). With timer set, it runs from a
        reactor timer instead, outside the lock (U1Printer.script). It is not sent again while
        the print is paused.

        A PAUSE can fail, or the U1 may not take it. That happens when its pause_resume is
        neither paused nor pausing, for example for a print in the region of an excluded object.
        Then the call falls back to the G-code error that pauses the print, with the line. From a
        timer there is no command to fail, so it raises RuntimeError with the line instead.
        """
        if self.host.u1.u1_paused():
            self._adapter().alert(f"ace2k_u1: {line}")
            return
        try:
            self.host.u1.script("PAUSE", timer)
        except Exception as err:
            logging.exception("ace2k_u1: the swap's PAUSE")
            self._adapter().pause_reason = f"ace2k_u1: {line}"  # the error below says it
            if gcmd is None:
                raise RuntimeError(f"{line} — its PAUSE failed: {err}") from None
            raise self._pause_error(gcmd, f"{line} (its PAUSE failed: {err})") from None
        if self.host.u1.pause_refused():
            self._adapter().pause_reason = f"ace2k_u1: {line}"  # the error below says it
            if gcmd is None:
                raise RuntimeError(f"{line} — the U1 did not take its PAUSE")
            # Known cost: PAUSE ON_ERROR=1 of the U1 records the variables of INNER_RESUME again.
            # On this path its resume then uses the live head and target, not the ones set here.
            raise self._pause_error(gcmd, line)
        # After the lines of the PAUSE itself, so the reason is the last line on the console.
        self._adapter().alert(f"ace2k_u1: {line}")

    @staticmethod
    def _pause_error(gcmd: GCodeCommand, msg: str) -> Exception:
        """Returns the G-code error that pauses the print (action="pause" of the U1).

        It is used only when the plain PAUSE did not take.
        """
        error: Callable[..., Exception] = gcmd.error  # the error of the U1 takes an action keyword
        try:
            return error(f"ace2k_u1: {msg}", action="pause")
        except TypeError:  # a G-code error without the action of the U1
            return error(f"ace2k_u1: {msg}")

    def owe_pending(self, pending: SwapState) -> None:
        """Owes the T of a pending swap, as the first one.

        The print asked for it before any T that was refused while the swap was pending. A refill
        has no such T, because the print asked for none. It is not owed twice either, neither for
        the same state nor for a swap that an owed T started (its T is owed already).
        """
        if pending.refill or pending.owed:
            return
        if not any(e is pending for e in self.tools_owed):
            self.tools_owed.insert(0, pending)

    def go_on(self, gcmd: GCodeCommand, pending: SwapState) -> bool:
        """Goes on with a pending swap during its pause. Returns True when the swap is done.

        First the head is probed (_probe_pending). Then printing_e_pos of the head is read again.
        It now holds the retraction of the pause itself, which the resume of the U1 compensates.
        So the swap puts that value back. Then the steps run, from the step that the probe read.

        A refill goes on only once its rest is out and a new spool is in its bay (refill_ready).
        """
        if pending.refill and not self.host.refiller.refill_ready(pending):
            return False
        if not self._probe_pending(pending):
            return False
        # Goes on from the step that the probe read, not from the step that failed.
        if not self.host.refiller.spool_known(pending):
            return False
        if pending.saved:
            e_pos = self.e_pos(pending.slot.head)
            if e_pos is not None:
                pending.e_pos = e_pos
        return self._run_swap(gcmd, pending)

    def _probe_pending(self, state: SwapState) -> bool:
        """Reads the head to find the step from which a pending swap goes on.

        If the sensor of the head is set, there are four cases.
        - The swap failed at or after its loaded check. It goes on from that step without a
          probe, because its load put the new lane there.
        - The swap failed at its push or later, and that push returned. The head was empty before
          the push and only the new lane moved toward it, so it goes on from its spool without a
          probe.
        - The swap failed at its unload or its free check, the old lane is known, and the swap is
          not a refill. The old lane never left the head, so the swap starts again from its
          unload without a probe. The probe would wait for the head to cool.
        - Otherwise the probe names the lane that is in the head. If it is the target of the
          swap, the swap goes on from its spool. Any other lane is the old lane of the swap, and
          the swap starts again from its unload.

        If the sensor is empty, the old lane is behind the splitter, or past it and short of the
        sensor. The push finds it there. The swap goes on from its hand over.

        A lane that is found and is not the lane of the head is handed the head. Returns False,
        and the console says why, when the head cannot be read or the lane that was found cannot
        be handed the head. Nothing else moves then.
        """
        adapter = self._adapter()
        stops: tuple[type[Exception], ...] = (ace2k_u1_probe.ProbeError, *adapter.refusals())
        try:
            self._adapter().head_watch.hand_head(state.slot, self._probe_head(state))
        except stops as err:
            adapter.alert(f"ace2k_u1: {self.swap_text(state)}: {err} — still paused")
            return False
        state.probed = True  # a swap not yet saved does not probe again at its start
        return True

    def _probe_head(self, state: SwapState) -> Hooked | None:
        """Reads the head for _probe_pending and sets the step of the swap.

        Returns the lane that was found, or None when the sensor of the head is empty. Raises
        what stopped it.
        """
        adapter = self._adapter()
        slot, new = state.slot, state.new
        try:
            head_set = adapter.head_set(new)
        except Exception as err:
            raise ace2k_u1_probe.ProbeError(
                f"head {slot.head + 1}: its sensor cannot be read — {err}"
            ) from err
        if not head_set:
            state.step = "hand over"
            return None
        if state.step in LOADED_STEPS:
            # The swap failed at or after its loaded check. Its own load put the new lane in the
            # head, which still reads set. So it goes on from that step, with no probe (which would
            # wait for the head to cool) and no load again.
            return new
        if state.step in PUSHED_STEPS and state.pushed_alone:
            # The push or a step after it failed. The head was empty before the push, and only the
            # new lane moved toward it, so the lane in the head is the new one. A probe would only
            # wait for the head to cool and find it.
            adapter.say(
                f"ace2k_u1: head {slot.head + 1}: {adapter.lane_text(new)} is in it (the head was "
                "empty before its push): on from its spool, no probe"
            )
            state.step = "spool"
            return new
        old = state.old
        if state.step in ("unload", "free") and old is not None and not state.refill:
            # The swap failed before its head emptied. The old lane never left it, so there is no
            # probe (which would wait for the head to cool). The unload and the free check run
            # again. A probe would only wait for the head to cool and find the old lane.
            adapter.say(
                f"ace2k_u1: head {slot.head + 1}: {adapter.lane_text(old)} is still in it (the "
                "swap failed before the head emptied): its unload again, no probe"
            )
            state.step = "unload"
            return old
        adapter.say(
            f"ace2k_u1: head {slot.head + 1}: its sensor sees filament: the probe tells which lane "
            "is in it (another may have been put back during the pause); it waits for the head to "
            "cool first"
        )
        found = self._probe().which_lane(slot)
        if found is new:
            state.step = "spool"
        else:
            state.old = found
            state.step = "unload"
        return found

    def swap(
        self,
        gcmd: GCodeCommand,
        slot: Slot | None,
        new: Hooked | None,
        tool: int,
        params: ToolParams | None | _Unread = UNREAD,
    ) -> bool:
        """Puts lane new in the head of slot for T<tool>.

        Returns True when the lane is in, and False when the print is paused. These are the
        steps.
        - The unload macro.
        - The free check of the old lane, and its roll back past the splitter.
        - The new lane is handed the head, pushed to its sensor, and its follow is armed.
        - The head takes its spool.
        - The load macro, with printing_e_pos of the head at 0.
        - A read of the sensor of the head.
        - The idle exit of the U1, with no retraction.
        A head whose lane is not known is probed first.

        A failure puts the head back and pauses the print plainly. RESUME finishes the swap.

        With a swap pending, a swap on its head goes on from what the probe reads, and then this
        swap runs if its lane is still not in. A swap on any other head is refused with the same
        pause.

        params holds the parameters of the T itself (an owed T that RESUME runs). Otherwise they
        are read from gcmd.
        """
        pending = self.swap_pending
        if pending is not None:
            if slot is None or pending.slot is not slot:
                return self._refuse(gcmd, tool, pending)
            if not self.go_on(gcmd, pending):
                return False
            if tool != pending.tool and self.host.u1.paused():
                # A T typed while paused. The T of the print itself was skipped at the pause and
                # stays owed. RESUME runs it (_owed_tools) after the swap of this T.
                self.owe_pending(pending)
            if slot.current is new:
                return True
        # The head and lane of a swap (Tracker.swap_for), or a lane of the head of the pending swap.
        slot, new = cast("Slot", slot), cast("Hooked", new)
        state = SwapState(
            slot=slot,
            old=slot.current,
            new=new,
            tool=tool,
            tool_params=self._tool_params(gcmd) if params is UNREAD else params,
        )
        return self._run_swap(gcmd, state)

    @staticmethod
    def _tool_params(gcmd: GCodeCommand) -> ToolParams | None:
        """Returns the command, line and parameters of a T, so that it can be built again.

        Returns None for another command.
        """
        try:
            return {
                "command": gcmd.get_command(),
                "commandline": gcmd.get_commandline(),
                "params": dict(gcmd.get_command_parameters()),
            }
        except Exception:
            return None

    def _run_swap(self, gcmd: GCodeCommand, state: SwapState) -> bool:
        """Runs the steps of the swap, from state.step on.

        Returns True when they all ran. Otherwise the head is put back, the console says why, the
        print pauses plainly, and it returns False.
        """
        adapter = self._adapter()
        slot, new = state.slot, state.new
        head = slot.head
        first = not state.saved
        step = state.step
        with self.swapping(head):
            try:
                if first:
                    adapter.say(f"ace2k_u1: {self.swap_text(state)}")
                    state.started = self.host.reactor.monotonic()
                    self.host.tracker.drop_owed_heat(head)  # the head swaps: it docks no more
                    self.host.u1.script("SAVE_GCODE_STATE NAME=ACEK_SWAP")
                    state.e_pos = self.e_pos(head)
                    state.saved = True
                    if step == SWAP_STEPS[0]:
                        self._xy_homed_or_raise(head)  # before the probe moves any lane
                    if state.old is None and not state.probed:  # not known, so read it
                        if self._probe_would_wait(state):
                            return self._pause_to_probe(gcmd, state)
                        found = self._probe_head(state)
                        step = "hand over"  # an internal error of the core is raised as this step's
                        self._adapter().head_watch.hand_head(slot, found)
                        step = state.step
                # "restore" means that every step is done and only the G-code restore is left.
                at = state.step
                steps = SWAP_STEPS[SWAP_STEPS.index(at) :] if at in SWAP_STEPS else ()
                for step in steps:
                    state.step = step
                    self.swap_step(step, state)
                step = state.step = "restore"
                self.host.u1.script("RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0")
            except Exception as err:
                if step == "hand over" and not isinstance(err, adapter.refusals()):
                    raise  # an internal error of the core: Klipper shuts down on it, never a pause
                state.step = step  # the step the line names is the one RESUME reads
                why = error_line(err)
                line = f"{self.swap_text(state)} failed at {step}: {why}"
                return self._fail_swap(gcmd, state, line, self.host.u1.paused())
        self.swap_pending = None
        if not state.refill:  # a T asked for it: the file's pre-extrusion right after is skipped
            self.host.tracker.after_swap(head, state.tool, state.print_temp)
        took_s = self.host.reactor.monotonic() - (state.started or self.host.reactor.monotonic())
        adapter.say(f"ace2k_u1: head {head + 1}: {adapter.lane_text(new)} in ({took_s:.0f} s)")
        return True

    def _probe_would_wait(self, state: SwapState) -> bool:
        """Tells whether the probe of a head with an unknown lane would run inside a printing T.

        That is the case when the print is printing and the sensor of the head is set. The wait
        of the probe for the head to cool would hold the nozzle over the part with the heater
        off. An empty head is pushed to without a probe. A sensor that cannot be read is a
        failure of the probe itself.
        """
        adapter = self._adapter()
        if adapter.print_state() != "printing":
            return False
        return self.host.head_set_or(state.new, False)

    def _pause_to_probe(self, gcmd: GCodeCommand, state: SwapState) -> bool:
        """Pauses the print when a swap starts while printing and the lane of its head is unknown.

        Nothing has moved. The head is put back as after a failure, a line says why, and the
        plain PAUSE follows. The swap stays pending at its unload, and the RESUME probes it
        (_probe_pending). Returns False.
        """
        head = state.slot.head
        state.step = SWAP_STEPS[0]
        line = f"head {head + 1}: its lane is not known: the print pauses to probe it"
        return self._fail_swap(gcmd, state, line, self.host.u1.paused())

    def _fail_swap(self, gcmd: GCodeCommand, state: SwapState, line: str, paused: bool) -> bool:
        """Handles a swap that cannot go on. Returns False.

        The swap stays pending at its step. The head is put back (put_back), and the plain PAUSE
        follows. The PAUSE says the line, as an error. The PAUSE records the temperatures of the
        heads, so the temperatures for the resume are set after it.
        """
        self.host.tracker.clear_wait_skip()
        self.host.tracker.drop_owed_heat()
        self.swap_pending = state
        self.put_back(state, paused)
        try:
            self.pause(gcmd, line)  # it says the line, as an error, after the PAUSE
        finally:
            self.resume_temps(state)  # after the PAUSE, which records the heads' temperatures
        return False

    @contextlib.contextmanager
    def swapping(self, head: int) -> Iterator[None]:
        """Marks the head as swapping (or clearing a refill) while the block runs.

        The rollback of its lane then belongs to the swap.
        """
        self._adapter().swapping.add(head)
        try:
            yield
        finally:
            self._adapter().swapping.discard(head)

    @staticmethod
    def refill_clearing(state: SwapState) -> bool:
        """Tells whether the state is a refill whose run-out lane is still to be cleared.

        The lane is still the lane of the head.
        """
        return bool(state.refill) and state.old is state.new

    def _xy_homed(self) -> bool:
        axes = self.host.u1.homed_axes()
        return "x" in axes and "y" in axes

    def _xy_homed_or_raise(self, head: int) -> None:
        """Raises if a T of the file comes before the homing.

        The macros of the U1 move the head in X and Y, and the U1 refuses that while it is not
        homed. So the swap starts nothing, and neither a lane nor the head moves.
        """
        if not self._xy_homed():
            raise RuntimeError(
                f"head {head + 1}: XY not homed: the swap waits for the print's homing"
            )

    def put_back(self, state: SwapState, paused: bool) -> None:
        """Puts the head of a failed swap back, before the pause.

        - The lane in the head stops following and stays off.
        - printing_e_pos goes back to the saved value. The resume of the U1 retracts by it.
        - The head leaves the cleaning area by the idle exit of the U1 and stays there. It is
          never moved over the print, because a hot nozzle that may hold filament oozes onto the
          part.
        - The G-code state is restored with no move.

        The RESUME of the U1 brings the head back to that idle position, where a swap that
        succeeds also ends. The next print move takes it to the part. If the print is paused, or
        X and Y are not homed, nothing moves. Never raises. The failure of each part is logged,
        and the pause still comes.
        """
        adapter = self._adapter()
        slot = state.slot
        head = slot.head
        h = slot.current
        if h is not None:
            h.follow_wanted = False  # the follow stays off in the pause; RESUME arms the new lane
            adapter.stop(h, "the swap's failure")
        self.set_e_pos(head, state.e_pos)
        if not state.saved:
            return
        if not paused and self._xy_homed():
            try:
                self.host.u1.idle_exit()
            except Exception as err:
                logging.exception("ace2k_u1: head %d: the failed swap's idle exit", head + 1)
                adapter.say(
                    f"ace2k_u1: head {head + 1}: the idle exit failed ({err}): the head stays "
                    "where it is"
                )
        try:
            self.host.u1.script("RESTORE_GCODE_STATE NAME=ACEK_SWAP MOVE=0")
        except Exception:
            logging.exception("ace2k_u1: head %d: the failed swap's G-code state", head + 1)

    def resume_temps(self, state: SwapState) -> None:
        """Sets the temperatures and the head that the resume of the U1 uses.

        The resume of the U1 heats each head back to the temperature that its pause recorded. It
        selects the head that its pause found active. For the head of a failed swap, this method
        sets the printing temperature of the new tool and selects that head. A failure before the
        T<head> of the unload could leave another head active.

        It runs after the PAUSE, which records the values of the heads. Never raises.
        """
        head = state.slot.head
        index_name, last_temp_name, head_temp_name = self.host.u1.resume_variables(head)
        values = []
        try:
            temp = self._swap_temp(state.tool)
            if temp:  # if no printing temperature is known, the ones the pause recorded stay
                values = [
                    (head_temp_name, f"{temp:g}"),
                    (last_temp_name, f"{temp:g}"),
                ]
        except Exception:
            logging.exception("ace2k_u1: head %d: the resume temperature", head + 1)
            values = []
        values.append((index_name, str(head)))  # whatever the temperature
        for name, value in values:
            self._set_resume_value(head, name, value, state.timer)

    def _set_resume_value(self, head: int, name: str, value: str, timer: bool) -> None:
        """Sets one variable of INNER_RESUME for head (resume_temps). Never raises."""
        try:
            self.host.u1.set_inner_resume(name, value, timer)
        except Exception:
            logging.exception("ace2k_u1: head %d: INNER_RESUME's %s", head + 1, name)

    def swap_text(self, state: SwapState) -> str:
        """Returns the text that describes the swap, such as "head N: <old> → <new> (T<tool>)".

        An old lane that is not known is written as "-". For a refill whose rest is still in the
        head, the text is "head N: refill of <lane>".
        """
        lane_text = self._adapter().lane_text
        old, new = state.old, state.new
        head = state.slot.head + 1
        if self.refill_clearing(state):
            return f"head {head}: refill of {lane_text(new)}"
        old_text = lane_text(old) if old else "-"
        tool = f" (T{state.tool})" if state.tool is not None else ""
        return f"head {head}: {old_text} → {lane_text(new)}{tool}"

    def swap_step(self, step: str, state: SwapState) -> None:
        """Runs step `step` of SWAP_STEPS on state, with its method in self.steps.

        A step that is not in the table runs nothing.
        """
        run = self.steps.get(step)
        if run is not None:
            run(state)

    def _step_unload(self, state: SwapState) -> None:
        adapter = self._adapter()
        old, new = state.old, state.new
        head = state.slot.head
        self._xy_homed_or_raise(head)
        if old is None:
            # This never happens after a probe, which names the lane or goes on from the hand
            # over. The filament cannot be rolled back past the splitter, so nothing moves.
            raise RuntimeError(f"the lane in head {head + 1} is unknown")
        # The free check runs only after the macro of this unload. A head that is empty here has
        # no filament in its gear to hold. A swap that restarts from its unload asks again. The
        # freed length of an earlier attempt does not count for this one.
        state.unloaded = False
        state.freed_mm = 0.0
        if adapter.head_set(new):  # the sensor of the head: the filament of the old lane is in it
            # The unload macro forms the tip at the temperature of the old material. It takes the
            # filament out of the gear and leaves its tip on the sensor of the head. Only the roll
            # back of the old lane clears the tip. By default the macro ends by heating toward the
            # load temperature of the new lane. A refill passes 0, so the heater stays off. The new
            # spool of the operator may take long to come, and the resume of the U1 heats the head
            # back (resume_temps).
            self._macro_defined(SWAP_UNLOAD_MACRO)  # before the arm and the T, so nothing changes
            timer = state.timer
            refill = self.refill_clearing(state)
            if not refill:
                # The old lane follows the extrusion of the macro. It is armed here, as the push
                # arms the new lane. A swap that restarts from its unload finds the lane stopped.
                # That happens after the put back of a failure, or after a probe that stopped
                # every lane. The lane of a refill stays stopped, because its end must not leave
                # the motor of the unit.
                adapter.follow.arm(old, PAUSE_WAIT_S)
            load_temp = 0.0 if refill else self._load_temp_for(state)
            nozzle = self.host.u1.nozzle(self._extruder(head)) or DEFAULT_NOZZLE_MM
            # The material is read once and used twice.
            material = self.host.spool_lanes.material(old, head_setting=True)
            self.host.u1.script(f"T{head} A0", timer)
            self._macro(
                state,
                SWAP_UNLOAD_MACRO,
                f"HEAD={head} TEMP={self._unload_temp(old, material, eject=refill):g} "
                f"SOFT={self._soft(head)} NOZZLE_DIAMETER={nozzle:g} LOAD_TEMP={load_temp:g} "
                f"MATERIAL={material_param(material)}",
                zero_e=False,
            )
            if not refill:
                # The moves of the macro run before the free check stops the follow of the old
                # lane. So the reverse of the feed-forward has dosed the whole retraction. The unit
                # may still run the dose of the last period, and the free check watches for that.
                # The lane of a refill does not follow, so there is nothing to wait for.
                self.host.u1.script("M400", timer)
            state.unloaded = True

    @staticmethod
    def _old_lane(state: SwapState) -> Hooked:
        """Returns the old lane of the swap, from its unload on.

        The unload refuses a lane that is not known.
        """
        return cast("Hooked", state.old)

    def _step_free(self, state: SwapState) -> None:
        # If the gear holds the filament after the unload, the probe raises a ProbeError, which
        # is a failure of the swap. If no unload ran (the head was empty), there is nothing to
        # check, and the roll back runs its whole length.
        if state.unloaded:
            try:
                state.freed_mm = self._probe().free_check(self._old_lane(state))
            except ace2k_u1_probe.HeldError as err:
                self._free_check_twice(state, err)

    def _free_check_twice(self, state: SwapState, err: ace2k_u1_probe.HeldError) -> None:
        """Handles a free check that held (err): the unload once more, then the free check again."""
        # This happens once, before the pause, because a second unload often frees a held tip. If
        # the tip is held again, the swap fails here.
        head = state.slot.head
        self._adapter().say(f"ace2k_u1: head {head + 1}: {err}: unloading it again")
        try:
            self._step_unload(state)
        except Exception as again:
            why = error_line(again)
            raise RuntimeError(f"its unload again — {why}") from again
        # What the held check rolled back is not counted. It only took up the slack of the
        # buffer, with the tip held at the gear. The follow of a swap, armed by the unload, gives
        # it back. The rest of a refill rolls back until its bay empties, so going that much
        # further only ends sooner. This runs only after an unload that ran.
        if state.unloaded:
            try:
                state.freed_mm = self._probe().free_check(self._old_lane(state))
            except ace2k_u1_probe.HeldError as held:
                raise ace2k_u1_probe.HeldError(
                    f"{held} after a second unload", held.moved_mm
                ) from held

    def _step_roll_back(self, state: SwapState) -> None:
        adapter = self._adapter()
        self._swap_rollback(state)
        if adapter.head_set(state.new):  # the new lane may move only with the head empty
            old = adapter.lane_text(self._old_lane(state))
            raise RuntimeError(f"the head's sensor still sees filament after {old}'s roll back")

    def _step_hand_over(self, state: SwapState) -> None:
        adapter = self._adapter()
        new = state.new
        state.pushed_alone = False
        self._head_empty(new)
        adapter.units.hand_over(new.unit, new.lane, PAUSE_WAIT_S)
        adapter.set_current(state.slot, new)

    def _step_push(self, state: SwapState) -> None:
        adapter = self._adapter()
        new = state.new
        state.lane_error = None  # this is a new load, so an error after it pauses again
        # A head whose sensor already sees the filament of the new lane is not pushed into. A push
        # that is blocked before the sensor looks for the lane in the way. The ProbeError of the
        # probe is a failure of the step.
        budget_mm = self._push_budget(state)
        state.pushed_alone = False
        error = self._probe().push_in(
            state.slot, new, speed=self.host.swap_cfg.swap_speed, budget=budget_mm
        )
        # The push returned, so the new lane moved last, after the head read empty. A push that
        # raised may have moved another lane of the head in its search, and leaves this False.
        state.pushed_alone = True
        if error == "distance":
            raise RuntimeError(
                f"{adapter.lane_text(new)} did not reach the head's sensor within its "
                f"{budget_mm:g} mm"
            )
        if error is not None:
            raise RuntimeError(f"{adapter.lane_text(new)} did not reach the head ({error})")
        # The filament is at the sensor of the head. The unit follows the load macro from its first
        # move on.
        adapter.follow.arm(new, PAUSE_WAIT_S)

    def _step_spool(self, state: SwapState) -> None:
        why = self.set_head_spool(state.new)
        if why is not None:
            self._adapter().say(why)

    def _step_load(self, state: SwapState) -> None:
        adapter = self._adapter()
        new = state.new
        head = state.slot.head
        self._macro_defined(SWAP_LOAD_MACRO)  # before the arm and the T, so nothing changes
        # The lane follows the extruder from its first move. It is armed at the push, or here when
        # the push did not run (the lane was already in the head). If it is armed already, nothing
        # happens.
        adapter.follow.arm(new, PAUSE_WAIT_S)
        temp = self._swap_temp(state.tool)
        if not temp:
            temp = self._load_temp(new)
            what = f"T{state.tool}" if state.tool is not None else f"head {head + 1}"
            adapter.say(
                f"ace2k_u1: head {head + 1}: no printing temperature known for {what}: the "
                f"load temperature, {temp:g} °C, is kept"
            )
        state.print_temp = temp
        # The moves and extrusions of the macro act on the active head. The head is selected here
        # every time. The T<head> of the unload runs only when there is filament to unload. A swap
        # or refill that goes on at its RESUME finds whatever head the pause left active.
        timer = state.timer
        self.host.u1.script(f"T{head} A0", timer)
        self._macro(
            state,
            SWAP_LOAD_MACRO,
            f"HEAD={head} LOAD_TEMP={temp:g} PRINT_TEMP={temp:g} "
            f"MATERIAL={material_param(self.host.spool_lanes.material(new))}",
        )
        # The kept temperature is used up. A later swap takes a newer one, else the one of the plan.
        # A refill with no tool has none.
        plan = self.host.tracker.plan
        if plan is not None and state.tool is not None:
            plan.kept.pop(state.tool, None)

    def _step_loaded(self, state: SwapState) -> None:
        adapter = self._adapter()
        new = state.new
        if not adapter.head_set(new):
            raise RuntimeError(
                f"the head's sensor sees no filament after {adapter.lane_text(new)}'s load"
            )
        self._lane_fit(state, new)
        state.slot.loaded = new  # the swap ended with this lane in the head

    def _step_return(self, state: SwapState) -> None:
        # The U1's own pre-extrusion with no purge (LENGTH=0): the load macro has purged and
        # cleaned once, so this adds its fan, its cut, a second clean and its idle exit out of
        # the cleaning area: one purge, two cleans. A soft filament purges nothing here either:
        # the U1's macro sets its 10 mm only when SOFT is True, and SOFT=1 reaches it as the
        # string "1". For a tool it runs forced, and the U1 records the tool as pre-extruded,
        # so the file's call right after the swap is skipped. A refill with no tool runs the
        # same macro directly, with no purge too: its load macro has purged. The restore then
        # puts back the G-code state with no move, and the next print move takes the head to
        # the part. If the print is paused, the RESUME of the U1 moves back by itself.
        head, timer = state.slot.head, state.timer
        # A mark left from an earlier swap must not skip this swap's own pre-extrusion.
        self.host.tracker.preextrude_after = None
        # The head is selected first, with the U1's own T. Otherwise its pre-extrusion sends a T
        # of the file's kind, which would come back here at a RESUME with another head active.
        self.host.u1.script(f"T{head} A0", timer)
        self._primed_e_pos(head)
        if state.tool is not None:
            self.host.u1.script(f"{PREEXTRUDE} INDEX={state.tool} FORCE=1 LENGTH=0", timer)
            self._adapter().say(
                f"ace2k_u1: head {head + 1}: pre-extrusion after the swap (T{state.tool})"
            )
            return
        # The temperature the load left the head at. With no tool, it is the U1's load temperature.
        temp = state.print_temp if state.print_temp is not None else self._load_temp(state.new)
        soft = self._soft(head)
        self.host.u1.script(f"{INNER_PREEXTRUDE} SOFT={soft} TEMP={temp:g} LENGTH=0", timer)
        self._adapter().say(f"ace2k_u1: head {head + 1}: pre-extrusion after the refill")

    def _primed_e_pos(self, head: int) -> None:
        """Counts the purge of the load macro in printing_e_pos of the head, before the return.

        The U1 counts a G1 E in printing_e_pos only while the print is printing, capped at 0.
        The purge of the swap is the load macro's, which the U1 does not count, and the U1's
        pre-extrusion runs with LENGTH=0. So U1_PREEXTRUDE_MM is added here, as the U1 would
        count its own purge. It brings the saved value (the slicer's retraction) back up.
        Otherwise a later flush or resume of the U1 would retract the slicer's retraction
        again. While the print is paused, or the value is unread, it stays as it is.
        """
        if self._adapter().print_state() != "printing":
            return
        e = self.e_pos(head)
        if e is not None:
            self.set_e_pos(head, min(0.0, e + U1_PREEXTRUDE_MM))

    def _lane_fit(self, state: SwapState, h: Hooked) -> None:
        """Checks the new lane after its load, before the print goes on.

        A lane in error is a failure of the swap, and the print stays paused. An error can come
        from a knot on the spool, which makes the follow time out during the load. Without the
        pause, the print would go on with no follow and the extruder would pull alone. This was
        seen on the unit.

        At the RESUME after it, the operator has seen to the spool. The error is cleared, the
        follow is armed again, and the swap goes on. If it fails again, it pauses again.
        """
        adapter = self._adapter()
        try:
            lane = adapter.lane_status(h)
        except Exception:  # the status was not read: the load stands, the watch sees the lane later
            logging.exception("ace2k_u1: %s after its load", adapter.lane_text(h))
            return
        if lane.get("mode") != "error":
            return
        kind = lane.get("error") or "error"
        if not state.lane_error:
            state.lane_error = kind
            raise RuntimeError(
                f"{adapter.lane_text(h)} is in error ({kind}) after its load: check its spool (a "
                "knot, a tangle), then RESUME"
            )
        h.api.clear(h.lane)  # if this raises, the swap fails here and the next RESUME tries again
        state.lane_error = None
        adapter.follow.arm(h, PAUSE_WAIT_S)
        if not h.armed:
            raise RuntimeError(
                f"{adapter.lane_text(h)}'s error ({kind}) cleared, but its follow did not arm"
            )
        adapter.say(
            f"ace2k_u1: {adapter.lane_text(h)}: its error ({kind}) cleared, its follow armed again"
        )

    def _extruder(self, head: int) -> Extruder | None:
        """Returns the extruder object of the head, or None when it is not known."""
        return self.host.u1.extruder((self._adapter().extruders or {}).get(head))

    def e_pos(self, head: int) -> float | None:
        """Returns printing_e_pos of the head, or None when it cannot be read."""
        return self.host.u1.e_pos(self._extruder(head))

    def _macro_defined(self, name: str) -> None:
        """Fails the step if the config lacks a macro of the swap.

        The step checks this first, before it arms a lane or sends its T, so nothing is sent or
        changed.
        """
        if name in self.missing_swap_macros():
            raise RuntimeError(f"{name} is not defined")

    def _macro(self, state: SwapState, name: str, args: str, zero_e: bool = True) -> None:
        """Runs one macro of the swap, name with its parameters args, on the head of state.

        It runs from the timer of the swap when the swap runs from one. The load macro runs with
        printing_e_pos of the head at 0. The unload macro (zero_e False) leaves it untouched, as
        the unload of the U1 itself runs. After either macro, the saved value is put back,
        however the macro ended.

        The step has checked that the macro is defined (_macro_defined). It is checked again
        here, before printing_e_pos is touched.
        """
        head, timer = state.slot.head, state.timer
        self._macro_defined(name)
        if zero_e:
            self.set_e_pos(head, 0.0)
        try:
            self.host.u1.script(f"{name} {args}", timer)
        finally:
            self.set_e_pos(head, state.e_pos)

    def set_e_pos(self, head: int, value: float | None) -> None:
        """Sets printing_e_pos of the head. The flush and the resume of the U1 retract by it.

        None (not read) leaves it as it is.
        """
        self.host.u1.set_e_pos(self._extruder(head), value)

    def _unload_temp(self, h: Hooked, material: str | None = None, eject: bool = False) -> float:
        """Returns the temperature at which the tip of lane h is formed.

        The first choice is the material of the lane in unload_temps. The material comes from
        _material with the setting of the head, or from material if it is already read. The next
        choice is the unload temperature of the U1 itself for the setting of the head. The last
        is U1_UNLOAD_TEMP. The fallbacks decide, and a failed swap never does.

        eject is set for the end of a spool in a shared head. That unload works as the eject of
        the U1 itself does. It uses the unload temperature of its table (250 °C for PLA), not
        the one in unload_temps. This is needed because at 220 °C the tip of the run-out lane
        stayed held.
        """
        if material is None:
            material = self.host.spool_lanes.material(h, head_setting=True)
        temp = None if eject else self.host.swap_cfg.unload_temps.get(material_param(material))
        if temp:
            return temp
        value = self.host.u1.unload_temp(self._extruder(h.head), h.head)
        return self._temp_or(value, U1_UNLOAD_TEMP)

    def tip_temp(self, h: Hooked) -> float:
        """Returns the temperature at which a swap's unload forms the tip of lane h.

        It reads the material of the lane with the setting of the head (_unload_temp).
        """
        return self._unload_temp(h)

    def _load_temp_for(self, state: SwapState) -> float:
        """Returns the temperature the swap loads the new filament at.

        It is the new tool's printing temperature when it is known, so the whole load runs at one
        temperature. Else it is the U1's load temperature for the new spool.
        """
        return self._swap_temp(state.tool) or self._load_temp(state.new)

    def _load_temp(self, h: Hooked) -> float:
        """Returns the load temperature of the U1 for the spool of lane h.

        It asks the filament table the way the load of the U1 asks it. It returns U1_LOAD_TEMP
        for a lane with no spool, for a type that the table does not know, and for a table that
        cannot be asked.
        """
        ext = self._extruder(h.head)
        try:
            value = self.host.u1.load_temp(
                self.host.spool_lanes.own_spool(h), ext, DEFAULT_NOZZLE_MM
            )
        except Exception:
            logging.exception("ace2k_u1: %s's load temperature", self._adapter().lane_text(h))
            return U1_LOAD_TEMP
        return self._temp_or(value, U1_LOAD_TEMP)

    @staticmethod
    def _temp_or(value: object, default: float) -> float:
        """Returns a temperature that the filament table of the U1 gave.

        It returns value when that is a finite positive number (not a bool, NaN or infinite).
        Otherwise it returns default.
        """
        if finite(value) and value > 0:
            return float(value)
        return default

    def _soft(self, head: int) -> int:
        """Returns 1 when the filament table of the U1 calls the setting of the head soft, else 0.

        An unknown setting gives 0.
        """
        return self.host.u1.soft(self._extruder(head), head)

    def _head_empty(self, h: Hooked) -> None:
        """Raises if the sensor of the head sees filament.

        The new lane moves only with the head empty.
        """
        if self._adapter().head_set(h):
            raise RuntimeError("the head's sensor sees filament")

    def _swap_rollback(self, state: SwapState) -> None:
        """Rolls the old lane back at swap_speed.

        The length is the fixed headN_splitter_mm less what its free check already rolled back
        (Rollback.roll_back). The rest of a refill rolls back head_budget_mm plus REFILL_EXTRA_MM,
        less the same amount. That takes the whole of it out of its bay, whether the spool is done
        or ran out.
        """
        head = state.slot.head
        refill = self.refill_clearing(state)
        length_mm = self._push_budget(state) if refill else self.host.swap_cfg.splitter_mm[head]
        self.rollback.roll_back(state, length_mm, refill)

    def _push_budget(self, state: SwapState) -> float:
        """Returns how far the new lane of a swap may be pushed toward its head.

        It is head_budget_mm, plus REFILL_EXTRA_MM for a refill. The new tip of a refill starts
        at the bay, and its rest rolls back as far. This is longer than the longest move of the
        unit, so it runs in several moves.
        """
        extra = REFILL_EXTRA_MM if state.refill else 0.0
        return self._adapter().head_budget_mm + extra

    def set_head_spool(
        self, h: Hooked, new_lane: bool = True, spool: Spool | None = None
    ) -> str | None:
        """Makes the filament setting of the head on the U1 match the spool of the lane.

        The spool comes from the tag of the lane (vendor, type, subtype and colour, as the tags
        translate it), or else from its ACEK_SPOOL record. A type that the filament table of the
        U1 does not know is not sent, only the colour, as the tags write it. With neither a tag
        nor a record, nothing is sent. The setting that the head had for the lane before is never
        used.

        Returns None once the setting is written. Otherwise it returns the line that says why
        not, and the setting stays as it was: a lane with no spool (no_spool_line), a value that
        the G-code cannot carry, or a setting that the U1 refuses. The swap's spool step says the
        line, and never fails on it.

        new_lane is set by the swap, whose head has just taken a new lane: the setting speaks for
        no lane until it is written. ACEK_HEAD_SPOOL clears it: the lane was already in the head,
        so a setting that is not written keeps speaking for the lane it was written for. spool is
        the lane's own_spool when the caller already read it; with None it is read here.
        """
        slot = cast("Slot", self._adapter().slot_of(h))  # the head of the lane
        if new_lane:
            slot.setting_for = None
        if spool is None:
            spool = self.host.spool_lanes.own_spool(h)
        if spool is None:
            return self.no_spool_line(h)
        vendor, kind, subtype, rgba = spool
        params = {"CONFIG_EXTRUDER": str(h.head)}
        if kind is not None:
            params.update(VENDOR=vendor, FILAMENT_TYPE=kind, FILAMENT_SUBTYPE=subtype)
        params.update(FILAMENT_COLOR_RGBA=rgba, FORCE="1")
        problems = TagWriter.check_params(params, TAG_QUOTED)
        if problems:
            return (
                f"ace2k_u1: head {h.head + 1}: {self._adapter().lane_text(h)}'s spool not set on "
                f"the head — {'; '.join(problems)}"
            )
        try:
            self.host.u1.script(TagWriter.format_params(params, TAG_QUOTED))
        except Exception as err:
            why = error_line(err)
            return f"ace2k_u1: head {h.head + 1}: spool setting refused: {why}"
        if slot.current is h:  # the setting speaks only for the lane in the head
            slot.setting_for = h
        # A colour-only write (a type the U1 does not know) leaves the head's type as it was.
        if kind is not None and (slot.current is h or not slot.shared):
            setting_written(h)  # written from the spool in the bay
        return None

    def no_spool_line(self, h: Hooked) -> NoSpoolLine:
        """Returns the line that set_head_spool returns for a lane with no spool set."""
        return NoSpoolLine(
            f"ace2k_u1: head {h.head + 1}: {self._adapter().lane_text(h)} has no spool set: "
            "set it with Edit spool"
        )

    def _swap_temp(self, tool: int | None) -> float | None:
        """Returns the temperature of the swap for T<tool>.

        It is the one kept for the tool, or else the one of the plan. The temperature of the
        first layer is used while print_stats says the layer is the first one (or no layer is
        known yet).
        """
        plan = self.host.tracker.plan
        if plan is None or tool is None:
            return None
        kept = plan.kept.get(tool)
        if kept:
            return kept
        t = plan.tools[tool]
        layer = self.host.u1.current_layer()
        if layer is not None and layer <= 1 and t.get("first_temp"):
            return t["first_temp"]
        return t.get("temp")
