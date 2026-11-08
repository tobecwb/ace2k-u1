"""Tracks a print against its plan.

It holds the plan of a file (ACEK_PLAN, with its choice once ACEK_PLAN_SET has set it). It knows
whether the print of the plan has started and has been seen. It knows where the print is in the T
order of the plan. From these it decides what the T and temperature wrappers do. There is no
G-code here. ace2k_u1.py wraps the commands of the U1 and calls this module.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, Protocol, TypedDict, Union, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import GCodeCommand, Reactor
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_printer_types import Coordinator

try:
    from . import ace2k_u1_plan
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_plan  # type: ignore[import-not-found, no-redef]

# What the reader thread of the plan hands back: the plan of the file, or what its read raised.
ReadResult = Union["ace2k_u1_plan.ParsedPlan", BaseException]

PRINTING_BELOW_C = 40.0
PRINTING_FLOOR_C = 150.0
# How far an M109 may be from the swap's temperature to skip its wait: the U1's settle delta.
WAIT_SKIP_DELTA = 1.5
# The longest retraction a swap copies from the slicer. The slicer's own retraction before a T is
# 10 mm in the files seen. A value past this is not a retraction, so it counts as unknown.
RETRACT_MAX_MM = 15.0


def _next_index(seq: Sequence[int], start: int, n: int) -> int | None:
    """Returns the first index from start on whose entry is n, or None."""
    return next((i for i in range(start, len(seq)) if seq[i] == n), None)


class PlanTool(TypedDict):
    """A tool of the plan, as the page shows it.

    It has the fields that the slicer gives (the fields of ace2k_u1_plan.Tool), the lane that is
    suggested for it (its text, or None for none) and the reason. The keys are read by name,
    which vulture does not see. The noqa on a key says so.
    """

    colour: str | None  # noqa: V107
    material: str | None  # noqa: V107
    temp: int | None  # noqa: V107
    first_temp: int | None  # noqa: V107
    lane: str | None  # noqa: V107
    why: str  # noqa: V107


class TrackerAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as the tracker uses it."""

    def lane_text(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    def head_of_extruder(self, name: str) -> int | None: ...


class TrackerSwapper(Protocol):
    """The Swapper (ace2k_u1_swap.Swapper), as the tracker uses it."""

    def tip_temp(self, h: Hooked) -> float: ...


class TempCall(NamedTuple):
    """What the temperature wrapper does with a call (PrintTracker.keep_temperature).

    kept: the call is not applied. Else it reaches the U1, with s in place of its S when s is set.
    """

    kept: bool
    s: float | None = None


class TrackerHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the tracker reads it.

    It gives the adapter (None until it is built), the reactor, the U1Printer and the swapper. It
    also gives the coordinator of ace2k (None without it).
    """

    @property
    def adapter(self) -> TrackerAdapter | None: ...
    @property
    def swapper(self) -> TrackerSwapper: ...
    @property
    def reactor(self) -> Reactor: ...
    @property
    def u1(self) -> U1Printer: ...
    def units(self) -> Coordinator | None: ...


@dataclass
class Plan:
    """The plan of a file.

    It holds the tools (dicts with the data of the slicer, the lane that is suggested and the
    reason), the T order and the raw T lines, the lane swaps for each head, and the spools of the
    lanes as the page shows them. choice and set are filled once ACEK_PLAN_SET has kept the lane
    that the operator chose for each tool.
    """

    file: str
    path: str
    mtime: float
    tools: list[PlanTool]
    order: list[int]
    raw: list[int]
    heads: dict[int, int]
    lanes: list[dict[str, object]]
    kept: dict[int, float] = field(default_factory=dict)  # tool -> the temperature kept for it
    # tool -> t_raw when its temperature was kept. Only a value kept since the last T of the file
    # is fresh enough to hold a head hot (_pre_heat_for).
    kept_at: dict[int, int] = field(default_factory=dict)
    # Tools whose kept line was said. This is per print, so it is dropped with the plan.
    kept_said: set[int] = field(default_factory=set)
    cool_said: set[int] = field(default_factory=set)  # tools whose cool-down line was said
    order_at: list[int] = field(default_factory=list)  # file offset of each order entry (timeline)
    size: int = 0  # the file size the offsets refer to
    # head -> the first tool whose heater-off command was held (_heaters_off)
    off_held: dict[int, int] = field(default_factory=dict)
    end_unload: bool | None = None  # None until the plan is set
    choice: dict[int, Hooked] | None = None
    set: bool = False  # last: the fields above build their sets before this name hides the builtin


class PrintTracker:
    """Tracks a print against its plan.

    host is the ACE2kU1. Its adapter is built later, and its print state is read when used.
    """

    def __init__(self, host: TrackerHost) -> None:
        self.host = host
        # The plan of a file (ACEK_PLAN), with its choice once ACEK_PLAN_SET has run.
        self.plan: Plan | None = None
        # The print of the plan has started. It is set by the start of its file with its plan set,
        # never by another print. The watch drops the plan once it has seen that print on and then
        # ended.
        self.plan_started = False
        self.plan_seen_on = False
        # The head that a swap just prepared with the U1's pre-extrusion. The file's pre-extrusion
        # right after it is skipped. It is cleared by that call, by the next T of the file, and at
        # the start or end of a print.
        self.preextrude_after: int | None = None
        # The tool that a swap just loaded. The slicer's first M109 for it after the swap runs
        # without the wait: the U1's pre-extrusion at the end of the swap already waited, and
        # its fan leaves the nozzle a degree off, so the wait held the head parked and it
        # dripped. It is cleared by that call, by the next T of the file, by a failed swap, and
        # at the start or end of a print. wait_skip_temp is the temperature the swap left the head
        # at. Only an M109 for about that temperature is skipped.
        self.wait_skip_tool: int | None = None
        self.wait_skip_temp: float | None = None
        # Where the running print is in the order of its plan. It is the index of the last T that
        # ran, or -1 before the first. The T wrapper counts it (count_tool). t_raw is the same in
        # the raw T lines of the file, repeats included.
        self.t_index = -1
        self.t_raw = -1
        # head -> (S, line for the log): the pre-heat that the head owes once it docks. The cool-
        # down of its outgoing tool was applied as sent (_owe_pre_heat). The next T of the file
        # takes it (take_owed_heat). It is dropped at the start or end of a print, by a failed
        # swap and when a swap onto the head starts (drop_owed_heat).
        self.owed_heat: dict[int, tuple[float, str]] = {}
        # tool -> mm: what the slicer retracted when the tool last left its shared head. The slicer
        # pushes it back when the tool comes back, so the swap that brings the tool back retracts
        # as much. A swap records it (record_retract) and spends it at its return step. It is
        # dropped at the start or end of a print.
        self.retract_owed: dict[int, float] = {}

    def read_plan(self, path: str) -> ace2k_u1_plan.ParsedPlan:
        """Runs ace2k_u1_plan.read_file(path) in a thread of its own.

        A large G-code file takes seconds to scan on the U1, and Klipper must keep running in the
        meantime. The caller (a G-code) waits on a completion. The thread completes it through the
        reactor. What the read raises is raised here, as it was. The cache of the plan is the one
        of read_file, unchanged.
        """
        reactor = self.host.reactor
        done = reactor.completion()

        def read() -> None:
            result: ReadResult
            try:
                result = ace2k_u1_plan.read_file(path)
            except BaseException as err:  # handed to the caller, which raises it
                result = err
            reactor.register_async_callback(lambda _eventtime: done.complete(result))

        threading.Thread(target=read, name="ace2k_u1_plan", daemon=True).start()
        result: ReadResult = done.wait()
        if isinstance(result, BaseException):
            raise result
        return result

    def _adapter(self) -> TrackerAdapter:
        """Returns the adapter of the host. It is built once anything is tracked."""
        return cast("TrackerAdapter", self.host.adapter)

    def _known_plan(self) -> Plan:
        """Returns the plan, in places where the caller has found one.

        That is while its print runs, or while a swap or a T of it is counted.
        """
        return cast("Plan", self.plan)

    def skip_wait(self, gcmd: GCodeCommand) -> bool:
        """Tells whether an M109 is the first one for the tool a swap just loaded, and spends it.

        The calls of the swap itself (A0) and calls without T are never skipped. An M109 for a
        temperature more than WAIT_SKIP_DELTA from the swap's waits, and the mark is spent.
        """
        if self.wait_skip_tool is None or gcmd.get_int("A", 1) == 0:
            return False
        if gcmd.get_int("T", -1) != self.wait_skip_tool:
            return False
        swap_temp = self.wait_skip_temp
        self.clear_wait_skip()
        if swap_temp is None:
            return False
        return abs(gcmd.get_float("S", 0.0) - swap_temp) <= WAIT_SKIP_DELTA

    def keep_temperature(self, gcmd: GCodeCommand) -> TempCall:
        """Tells what to do with a call for a tool whose lane is not in its head.

        A printing temperature (printing_temp) is kept for its swap. For a docked head it is also
        applied, at most at the tip temperature of the filament now in that head (_docked_cap).
        For the printing head it is kept only. A standby or cool-down value is not applied and not
        kept, so the swap never waits for it. The calls of the swap itself are never kept (they
        carry A0). A cool-down for a tool whose lane is in its head may leave a pre-heat owed
        (_owe_pre_heat).
        """
        if gcmd.get_int("A", 1) == 0 or gcmd.get("T", None) is None:
            return TempCall(False)
        n = gcmd.get_int("T")
        swap = self.swap_for(n)
        if swap is None:
            value = gcmd.get_float("S", 0.0)
            if self.keep_cool_down(n, value):
                return TempCall(True)
            self._owe_pre_heat(n, value)
            return TempCall(False)
        slot = swap[0]
        plan = self._known_plan()
        value = gcmd.get_float("S", 0.0)
        if value <= 0.0:
            # A heater-off command for a tool that is not in its head is held, like any other
            # command for such a tool. It is sent at the end of the print (_heaters_off), so the
            # per-tool off of an end G-code never leaves a head hot.
            plan.off_held.setdefault(slot.head, n)
        if not self.printing_temp(n, value):
            return TempCall(True)  # a standby or cool-down value: not applied, not kept, not said
        plan.kept[n] = value  # the swap prints at the slicer's value, never at the cap
        plan.kept_at[n] = self.t_raw
        if self._docked(slot.head):
            # The slicer's pre-heat for a docked head: the U1 heats it, as it does without the
            # adapter. It stops at the tip temperature of the filament in the head, which the
            # swap's unload forms the tip at.
            cap = self._docked_cap(slot)
            if cap is not None:
                return TempCall(False, cap if value > cap else None)
        self._say_kept(plan, n, slot)
        return TempCall(True)

    def _owe_pre_heat(self, n: int, value: float) -> None:
        """Records the pre-heat that the head of T<n> owes once it docks, if any (_pre_heat_for).

        The cool-down itself is applied as the slicer sent it, so the head that still prints
        never changes. A cool-down that owes nothing drops what an earlier one of the head owed.
        """
        h, slot = self._shared_tool(n)  # a Slot only with a running plan
        if slot is None or slot.current is not h:
            return
        owed = self._pre_heat_for(n, slot, value)
        if owed is None:
            self.owed_heat.pop(slot.head, None)
        else:
            self.owed_heat[slot.head] = owed

    def _pre_heat_for(self, n: int, slot: Slot, value: float) -> tuple[float, str] | None:
        """Returns (S, line for the log) of the pre-heat that the head of slot owes, or None.

        T<n>'s lane is in that head, and value is a standby or cool-down value (not an off). The
        next T of the file on the head names another lane, and a printing temperature was kept
        for that tool since the last T of the file (kept_at). S is that value, at most the tip
        temperature of the filament in the head, and above value. Anything else returns None.
        """
        if value <= 0.0 or self.printing_temp(n, value):
            return None
        nxt = self._next_on_head(slot.head)
        if nxt is None or nxt[1] is slot.current:
            return None
        plan = self._known_plan()
        m = nxt[0]
        kept = plan.kept.get(m)
        if not kept or plan.kept_at.get(m) != self.t_raw:
            return None
        cap = self._docked_cap(slot)
        if cap is None:
            return None
        s = min(kept, cap)
        if s <= value:
            return None
        return s, f"T{n}'s cool-down of {value:g} °C raised to T{m}'s kept pre-heat"

    def take_owed_heat(self, gcmd: GCodeCommand, n: int) -> list[tuple[int, float, str]]:
        """Takes what the heads owe at T<n> of the file: [(head, S, line for the log)].

        A T of the file (A not 0) ends the tool segment, so everything owed is taken. The heads
        other than the one of T<n> dock at this T and are returned. The head of T<n> stays the
        active one, or swaps, and owes nothing. A T whose head is not known returns nothing.
        """
        if gcmd.get_int("A", 1) == 0 or not self.owed_heat:
            return []
        owed, self.owed_heat = self.owed_heat, {}
        h = self.tool_lane(n)
        if h is None:
            return []
        return [(head, s, line) for head, (s, line) in sorted(owed.items()) if head != h.head]

    def clear_wait_skip(self) -> None:
        """Forgets the M109 that a swap would let pass."""
        self.wait_skip_tool = self.wait_skip_temp = None

    def reset_print(self) -> None:
        """Clears what a print leaves here: its T index, the marks of a swap, the owed heat and the
        owed retractions."""
        self.t_index = self.t_raw = -1
        self.preextrude_after = None
        self.drop_owed_heat()
        self.clear_wait_skip()
        self.retract_owed = {}

    def after_swap(self, head: int, tool: int | None, temp: float | None) -> None:
        """Marks a swap's end: the file's pre-extrusion and its first M109 are skipped."""
        self.preextrude_after = head
        self.wait_skip_tool = tool
        self.wait_skip_temp = temp

    def record_retract(self, head: int, old: Hooked | None, tool: int, e_pos: float | None) -> None:
        """Records what the slicer retracted the tool that left head, before the T of tool.

        A swap calls this once, when the lane that leaves the head is known: old. e_pos is the
        head's printing_e_pos read as the swap started, while the print was printing; the
        slicer's retraction before the T is -e_pos. The tool that left is the last T of the file
        on head (_left_head). A reading of 0 means the slicer retracted nothing, so that tool
        owes nothing. A reading up to RETRACT_MAX_MM replaces the tool's record. Anything else
        (no number, too far, the tool not known) leaves the record as it was: the slicer
        retracts a tool the same way at each T, so its earlier value is the better guess.
        """
        left = self._left_head(head, old, tool)
        if left is None or e_pos is None:
            return
        mm = -e_pos
        if mm <= 0.0:
            self.retract_owed.pop(left, None)
        elif mm <= RETRACT_MAX_MM:
            self.retract_owed[left] = round(mm, 2)

    def retract_for(self, tool: int) -> float | None:
        """Returns what the slicer retracted tool when it last left its head, or None."""
        return self.retract_owed.get(tool)

    def spend_retract(self, tool: int) -> None:
        """Forgets the value of tool once a swap has retracted it."""
        self.retract_owed.pop(tool, None)

    def _left_head(self, head: int, old: Hooked | None, tool: int) -> int | None:
        """Returns the tool of the last T line of the file on head, before the T of tool.

        The print must be at the T of tool in the file's T lines (t_raw). The tool found must
        have old, the lane that leaves the head, as its lane. Otherwise the tool that left is not
        known for sure, and this returns None. So does a print with no running plan.
        """
        plan = self._plan_running()
        if plan is None or old is None:
            return None
        raw = plan.raw or ()
        i = self.t_raw
        if not (0 <= i < len(raw) and raw[i] == tool):
            return None
        choice = cast("dict[int, Hooked]", plan.choice)
        for j in range(i - 1, -1, -1):
            lane = choice.get(raw[j])
            if lane is not None and lane.head == head:
                return raw[j] if lane is old else None
        return None

    def drop_owed_heat(self, head: int | None = None) -> None:
        """Drops the pre-heat that a head owes, or that every head owes when head is None."""
        if head is None:
            self.owed_heat = {}
        else:
            self.owed_heat.pop(head, None)

    def _raw_after(self) -> Iterator[int]:
        """Yields the raw T lines of the file after the one being run, repeats included."""
        raw = self._known_plan().raw or ()
        for i in range(self.t_raw + 1, len(raw)):
            yield raw[i]

    def _next_on_head(self, head: int) -> tuple[int, Hooked] | None:
        """Returns (tool, lane) of the next T line of the file whose lane is in head, or None."""
        choice = cast("dict[int, Hooked]", self._known_plan().choice)
        for m in self._raw_after():
            lane = choice.get(m)
            if lane is not None and lane.head == head:
                return m, lane
        return None

    def _docked_cap(self, slot: Slot) -> float | None:
        """Returns the tip temperature of the lane in a docked head, or None.

        None when the lane in the head is not known or its tip temperature cannot be read. The
        call is then kept and not applied.
        """
        if slot.current is None:
            return None
        try:
            return self.host.swapper.tip_temp(slot.current)
        except Exception:
            logging.exception("ace2k_u1: head %d's tip temperature", slot.head + 1)
            return None

    def _docked(self, head: int) -> bool:
        """Tells whether a head is docked: another head is the toolhead's active one.

        An active extruder that cannot be read counts as not docked, so the call is kept.
        """
        name = self.host.u1.active_extruder_name()
        if name is None:
            return False
        active = self._adapter().head_of_extruder(name)
        return active is not None and active != head

    def _say_kept(self, plan: Plan, n: int, slot: Slot) -> None:
        """Prints the console line for the temperature of T<n>, kept for its swap on slot.

        It is printed once per tool.
        """
        if n in plan.kept_said:
            return
        plan.kept_said.add(n)
        now = self._adapter().lane_text(slot.current) if slot.current else "nothing"
        self._adapter().say(
            f"ace2k_u1: T{n}'s temperature kept for its swap: head {slot.head + 1} prints {now}"
        )

    def count_tool(self, gcmd: GCodeCommand, n: int) -> None:
        """Counts T<n> of the running planned print in the order of the plan.

        The T wrapper calls this before its swap. The calls of the swap itself (A0) are not a T
        of the file. The owed T that a RESUME runs (_run_tool) goes to the handler of the U1 and
        not through here, so it is the same T that was counted when the print paused. So t_index
        is the index of the T that is being run, and the next T is the one after it
        (next_t_lane).

        The order merges a T that is repeated on consecutive lines. So a T of the entry where the
        print is moves nothing. That covers the repeated T{initial_extruder} of the start and the
        pre-extrude of the U1. Any other T moves to the next entry of the order for its tool. If
        none is found, the index stays.

        Only a T that the file runs counts, that is, while the print is printing. A T typed while
        paused is not a line of the file. t_raw counts the same T in the raw T lines of the file
        (count_raw).
        """
        if gcmd.get_int("A", 1) == 0:
            return
        plan = self._plan_running()
        if plan is None or self.host.u1.print_state() != "printing":
            return
        self.count_raw(n)
        order = plan.order
        if 0 <= self.t_index < len(order) and order[self.t_index] == n:
            return
        nxt = _next_index(order, self.t_index + 1, n)
        if nxt is not None:
            self.t_index = nxt

    def count_raw(self, n: int) -> None:
        """Moves t_raw for T<n> of the file.

        A T of the line where the print is goes to the next line only when the file repeats it
        there (the T{initial_extruder} lines of the start). Otherwise it is a T that the U1 adds
        (its pre-extrude) and it moves nothing. Any other T goes to its next line in the file. If
        none is found, the index stays.
        """
        raw = self._known_plan().raw or ()
        i = self.t_raw
        if 0 <= i < len(raw) and raw[i] == n:
            if i + 1 < len(raw) and raw[i + 1] == n:
                self.t_raw = i + 1
            return
        nxt = _next_index(raw, i + 1, n)
        if nxt is not None:
            self.t_raw = nxt

    def next_t_lane(self) -> Hooked | None:
        """Returns the lane of the very next T line of the file, after the one being run.

        Repeated lines are included. So the cool-down at the start, before a repeated T0, is not a
        cool-down of an outgoing tool. Returns None when there is no next line, or when the plan
        chose no lane for it.
        """
        m = next(self._raw_after(), None)
        if m is None:
            return None
        return cast("dict[int, Hooked]", self._known_plan().choice).get(m)

    def keep_cool_down(self, n: int, value: float) -> bool:
        """Tells whether the call is the cool-down of the outgoing tool on a shared head.

        The head is about to swap. The call is a value under the printing temperature of a tool
        whose lane is in its head. The very next T of the file must be on that head and must name
        another lane. By design, head 1 a:1 followed by head 2 b:1 leaves head 1 to cool, even if
        it is used again later.

        Such a call is not applied, so nothing records it as the temperature of the head (neither
        the unload of the U1 nor a pause). It is not stored either. It is said once for each tool
        and print.

        Any other next T (another head, the same lane, or none) leaves the head to rest, and the
        call is applied.
        """
        h, slot = self._shared_tool(n)
        if slot is None or slot.current is not h:
            return False
        if value > 0.0 and self.printing_temp(n, value):
            return False
        nxt = self.next_t_lane()
        if nxt is None or nxt.head != slot.head or nxt is h:
            return False
        self._hold_cool_down(n, slot, value)
        return True

    def _hold_cool_down(self, n: int, slot: Slot, value: float) -> None:
        """Keeps back the cool-down of T<n> on slot.

        An off command is held for the end of the print. The console line is printed once for
        each tool.
        """
        plan = self._known_plan()
        if value <= 0.0:
            plan.off_held.setdefault(slot.head, n)  # sent at the end of the print, as any held off
        if n not in plan.cool_said:
            plan.cool_said.add(n)
            self._adapter().say(
                f"ace2k_u1: T{n}'s cool-down kept: head {slot.head + 1} is about to swap"
            )

    def printing_temp(self, n: int, value: float) -> bool:
        """Tells whether a temperature for T<n> is a printing temperature.

        It is one if it is at most PRINTING_BELOW_C under the temperature of the plan for the
        tool. When the plan has none, it must be at least PRINTING_FLOOR_C. The standby and
        cool-down values that the slicer gives for an idle tool are lower.
        """
        tools = self._known_plan().tools
        plan_temp = tools[n].get("temp") if n < len(tools) else None
        if plan_temp:
            return value >= plan_temp - PRINTING_BELOW_C
        return value >= PRINTING_FLOOR_C

    def tool_lane(self, n: int) -> Hooked | None:
        """Returns the lane that the set plan chose for T<n> in the running print, or None."""
        plan = self._plan_running()
        return None if plan is None else cast("dict[int, Hooked]", plan.choice).get(n)

    def _plan_running(self) -> Plan | None:
        """Returns the plan when it is set and its print has started, else None."""
        plan = self.plan
        if plan is None or not plan.set or not self.plan_started:
            return None
        return plan

    def _shared_tool(self, n: int) -> tuple[Hooked | None, Slot | None]:
        """Returns the lane of T<n> in the running plan, and the Slot of its head.

        The Slot is None when the tool has no lane, or when its head is fed by one lane only.
        """
        h = self.tool_lane(n)
        slot = None if h is None else self._adapter().slot_of(h)
        if slot is None or not slot.shared:
            return h, None
        return h, slot

    def swap_for(self, n: int) -> tuple[Slot, Hooked] | None:
        """Returns (slot, lane of the tool) when T<n> must first swap the lane of its head.

        Otherwise it returns None. A swap is needed only with a plan that is set and whose print
        has started (tool_lane), for a lane of a shared head that is not the lane now in that
        head. The caller has left out the T calls of the U1 itself (A0).
        """
        h, slot = self._shared_tool(n)
        if h is None or slot is None or slot.current is h:  # a Slot only with a lane
            return None
        return slot, h

    def plan_status(self) -> dict[str, object] | None:
        """Returns the plan for the page, or None without a plan.

        It leaves out the path and the kept temperatures. It has the T order of the file, because
        the page recounts the swaps when the choice changes. The choice is {tool: lane text}, or
        None while it is not set.
        """
        plan = self.plan
        if plan is None:
            return None
        # These are copies, so a later change of the plan never alters what was published.
        out: dict[str, object] = {
            "file": plan.file,
            "mtime": plan.mtime,
            "tools": [dict(t) for t in plan.tools],
            "order": list(plan.order),
            "raw": list(plan.raw),
            "order_at": list(plan.order_at),
            "size": plan.size,
            "heads": dict(plan.heads),
            "set": plan.set,
            "choice": self._choice_text(plan.choice),
            "lanes": [dict(ln) for ln in plan.lanes],
        }
        if plan.end_unload is not None:  # it is set with the choice, and absent before
            out["end_unload"] = plan.end_unload
        return out

    def _choice_text(self, choice: dict[int, Hooked] | None) -> dict[int, str] | None:
        """Returns the choice of the plan as {tool: lane text}, or None while it is not set."""
        if choice is None:
            return None
        units = cast("Coordinator", self.host.units())
        return {n: units.lane_text(h.unit, h.lane) for n, h in choice.items()}
