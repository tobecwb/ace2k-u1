"""Wraps the G-codes of the U1 that the adapter needs to change.

The wrapped commands are ACEK_CLEAR, ACEK_LANE_ACTIVE, ACEK_FEED, SET_PRINT_FILAMENT_CONFIG, the two
print starts, T0…T31, SM_PRINT_PREEXTRUDE_FILAMENT, M104, M109 and RESUME. The module also holds the
checks that a print start runs, and the RESUME that runs the T commands it owes. The print starts,
M104 and M109 are wrapped once more for the temperature check (wrap_temp_checks). ace2k_u1.py
installs the wraps at the hook (Wraps).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_material import MaterialGate
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_printer_types import Coordinator, GCode, GCodeCommand, Handler, Reactor
    from .ace2k_u1_probe import HeadProbe
    from .ace2k_u1_refill import Refiller
    from .ace2k_u1_spools import LaneSpools
    from .ace2k_u1_swap import OwedTool, Swapper, SwapState
    from .ace2k_u1_temps import TempCheck
    from .ace2k_u1_tracker import Plan, PrintTracker

try:
    from . import ace2k_u1_plan, ace2k_u1_probe
    from .ace2k_u1_adapter import PRINT_ON
    from .ace2k_u1_common import STILL_MODES, error_line, finite
    from .ace2k_u1_config import LANES
    from .ace2k_u1_hooks import END_UNLOAD, FILAMENT_PARAMETERS
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_plan  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_probe  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_adapter import PRINT_ON  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        STILL_MODES,
        error_line,
        finite,
    )
    from ace2k_u1_config import LANES  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_hooks import (  # type: ignore[import-not-found, no-redef]
        END_UNLOAD,
        FILAMENT_PARAMETERS,
    )

TOOLS = 32  # the logical tools of the U1 (T0-T31), each mapped to a head by its extruder_map_table
MAP_PAIR = re.compile(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]")  # one [tool,head] of a MAP_TABLE=
QUOTES = 2  # a quoted name's two quotes


def _unquoted(text: str | None) -> str:
    """Returns a file name from a G-code parameter, stripped and without surrounding quotes."""
    name = (text or "").strip()
    if len(name) >= QUOTES and name[0] == name[-1] and name[0] in "\"'":
        name = name[1:-1].strip()
    return name


class WrapsHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the wraps read it.

    It gives the adapter and the probe. They are built at the hook, before any wrap is installed.
    The probe exists only with a head that several lanes feed, and only such heads are probed. It
    also gives the printer, the U1, the plan, the Swapper and the Refiller. It gives the
    coordinator of ace2k, a way to read the sensor of a head, and a way to clear the pending state
    at the end of a print.
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
    def swapper(self) -> Swapper: ...
    @property
    def material(self) -> MaterialGate: ...
    @property
    def spool_lanes(self) -> LaneSpools: ...
    @property
    def refiller(self) -> Refiller: ...
    @property
    def temps(self) -> TempCheck: ...
    def units(self) -> Coordinator | None: ...
    def head_set_or(
        self, h: Hooked, default: bool, unread: str | Callable[[], str] | None = None
    ) -> bool: ...
    def sensor_text(self, h: Hooked) -> str: ...
    def print_over(self) -> None: ...


class Wraps:
    """Wraps the G-codes of the U1.

    host is the ACE2kU1. Its adapter and probe are built at the hook. Its Swapper, Refiller, U1,
    plan and spools are read when used.
    """

    def __init__(self, host: WrapsHost) -> None:
        self.host = host
        # Maps n to the T<n> handler that the U1 had before the wrap (wrap_tools).
        self.tool_handlers: dict[int, Handler] = {}

    def _adapter(self) -> Adapter:
        """Returns the adapter of the host.

        It is built at the hook, before any wrap is installed.
        """
        return cast("Adapter", self.host.adapter)

    def _probe(self) -> HeadProbe:
        """Returns the probe of the host.

        It is built at the hook when a head is fed by several lanes. Only such heads are probed.
        """
        return cast("HeadProbe", self.host.probe)

    @staticmethod
    def _wrap(gcode: GCode, name: str, make_handler: Callable[[Handler], Handler]) -> bool:
        """Replaces a command the way Klipper does.

        The old handler is taken out (register_command(name, None) hands it back). The wrapper
        that make_handler(old) builds is registered with the same description and readiness.
        Returns False, and registers nothing, when the printer has no such command.
        """
        desc: str | None = (getattr(gcode, "gcode_help", {}) or {}).get(name)
        when_not_ready = name in (getattr(gcode, "base_gcode_handlers", {}) or {})
        old = gcode.register_command(name, None)
        if old is None:
            return False
        gcode.register_command(name, make_handler(old), when_not_ready=when_not_ready, desc=desc)
        return True

    def wrap_head_setting(self, gcode: GCode) -> None:
        """Wraps the U1's SET_PRINT_FILAMENT_CONFIG: a type it writes on a head that one lane
        feeds, with the bay full, is that spool's setting (LaneSpools.setting_set), even with the
        same value as before. A write of the colour alone, or of no type (NONE), is not. Without
        the command, nothing is wrapped."""
        self._wrap(gcode, "SET_PRINT_FILAMENT_CONFIG", self._head_setting_handler)

    def _head_setting_handler(self, old: Handler) -> Handler:
        def cmd_head_setting(gcmd: GCodeCommand) -> None:
            old(gcmd)
            try:
                head = gcmd.get_int("CONFIG_EXTRUDER", None)
                if head is not None:
                    kind = gcmd.get("FILAMENT_TYPE", "") or ""
                    self.host.spool_lanes.setting_set(head, kind)
            except Exception:  # the write itself went through
                logging.exception("ace2k_u1: the head setting's owner")

        return cmd_head_setting

    def wrap_ace_clear(self, gcode: GCode) -> None:
        """Wraps ACEK_CLEAR of ace2k so that it also tells the adapter.

        The command is the escape of the operator from a lane that is counted as waiting behind a
        piece. The wrapper (_wrap) runs the command and then releases the lane, whatever the
        command raised. That includes a link that is down and a firmware without the feed. The
        original error is raised after that. Without the command, the escape is unavailable. The
        console and the log say so once.
        """
        if not self._wrap(gcode, "ACEK_CLEAR", self._ace_clear_handler):
            why = (
                "ace2k_u1: no ACEK_CLEAR to wrap: the escape from a lane waiting behind a piece is "
                "unavailable; copy ace2k's host module of the same release"
            )
            logging.warning(why)
            gcode.respond_info(why, log=False)  # logged once, by the warning above

    def _ace_clear_handler(self, old: Handler) -> Handler:
        adapter = self._adapter()
        units = adapter.units

        def cmd_ace_clear(gcmd: GCodeCommand) -> None:
            try:
                old(gcmd)
            finally:
                # This is deliberately in a finally, and it also covers a refusal of ace2k.
                # ACEK_CLEAR is the escape of the operator from a lane that waits behind a piece.
                # It must work with the link of the unit down, or with a firmware without the
                # feed. ace2k then refuses the clear, the lane is released all the same, and the
                # refusal is raised after. Only a clear that names no unit releases nothing,
                # because there is no lane to release.
                try:
                    lane = gcmd.get_int("LANE", None)
                    unit = None
                    if lane is not None:
                        # The unit that UNIT= names. A missing or unknown one was already refused by
                        # the handler of ace2k, so there is nothing to release and nothing to say.
                        try:
                            unit = units.unit_for(gcmd)
                        except Exception:
                            logging.info("ace2k_u1: ACEK_CLEAR names no unit: no lane released")
                            lane = None
                    if lane is not None:
                        adapter.grip_watch.on_ace_clear(lane - 1, unit)
                except Exception:  # it never hides the original error
                    logging.exception("ace2k_u1: ACEK_CLEAR's release")

        return cmd_ace_clear

    def wrap_lane_active(self, gcode: GCode) -> None:
        """Wraps ACEK_LANE_ACTIVE of ace2k so that it also moves the lane in each shared head.

        Once the original has run, whatever it raised, every head that several lanes feed takes
        the choice of the coordinator for its extruder (adopt_active). A lane of such a head is
        refused while a planned print is on (_check_lane_active). The wrap (_wrap) is installed
        only with a + group or runout_source unit. Without them, no head has a lane to choose.
        """
        adapter = self._adapter()

        def handler(old: Handler) -> Handler:
            def cmd_lane_active(gcmd: GCodeCommand) -> None:
                self._check_lane_active(gcmd)
                try:
                    old(gcmd)
                finally:
                    try:
                        adapter.follow.adopt_active()
                    except Exception:  # it never hides the original error
                        logging.exception("ace2k_u1: ACEK_LANE_ACTIVE's head lanes")

            return cmd_lane_active

        if not self._wrap(gcode, "ACEK_LANE_ACTIVE", handler):
            logging.warning("ace2k_u1: no ACEK_LANE_ACTIVE to wrap: a head's lane is the hook's")

    def _named_lane(self, gcmd: GCodeCommand) -> Hooked | None:
        """Returns the hooked lane that LANE= (and UNIT=) of a command names, or None.

        A lane that it cannot name is left to the handler of ace2k, which refuses it.
        """
        lane = gcmd.get_int("LANE", None)
        if lane is None:
            return None
        try:
            unit = self._adapter().units.unit_for(gcmd)
        except Exception:
            return None
        return self._adapter().hooked_lane(unit, lane - 1)

    def _check_lane_active(self, gcmd: GCodeCommand) -> None:
        """Refuses ACEK_LANE_ACTIVE on a lane of a shared head while a planned print runs.

        The refusal holds while the print is printing or paused. The swaps and probes of the print
        decide the lane of the head then, so the lane of the head belongs to them.
        """
        if self.host.tracker.plan is None or not self.host.tracker.plan_started:
            return
        if self.host.u1.print_state() not in PRINT_ON:
            return
        h = self._named_lane(gcmd)
        if h is None or not self._adapter().shared(h):
            return
        raise gcmd.error(
            f"ace2k_u1: head {h.head + 1}: ACEK_LANE_ACTIVE refused: the planned print's swaps "
            "choose the head's lane until it ends"
        )

    def wrap_manual_feed(self, gcode: GCode) -> None:
        """Wraps ACEK_FEED of ace2k to refuse a feed that would meet another filament.

        A lane that is parked outside a head that several lanes feed is refused while the sensor of
        the head (its raw switch) is set. The push would meet the filament of another lane in the
        shared tube. The wrap (_wrap) is installed only with a + group or runout_source unit. The
        lane that is in the head and every lane of a head with one lane reach ace2k unchanged.
        """

        def handler(old: Handler) -> Handler:
            def cmd_feed(gcmd: GCodeCommand) -> None:
                self._check_manual_feed(gcmd)
                old(gcmd)

            return cmd_feed

        if not self._wrap(gcode, "ACEK_FEED", handler):
            logging.warning("ace2k_u1: no ACEK_FEED to wrap: a parked lane's feed is unchecked")

    def _check_manual_feed(self, gcmd: GCodeCommand) -> None:
        adapter = self._adapter()
        h = self._named_lane(gcmd)
        if h is None or not adapter.parked(h):
            return
        # A sensor that cannot be read counts as holding, as for the grip.
        if self.host.head_set_or(h, True, lambda: self.host.sensor_text(h)):
            raise gcmd.error(
                f"ace2k_u1: head {h.head + 1} holds another lane's filament: unload it first"
            )

    def wrap_print_start(self, gcode: GCode) -> None:
        """Wraps SDCARD_PRINT_FILE and SDCARD_PRINT_FILE_WITH_PARAMETERS of the U1.

        A file that puts two of its tools on a head that several lanes feed starts only with its
        plan set. With the plan, each such head first takes the lane of the first tool that it
        prints. The wrap (_wrap) is installed only with a + group or runout_source unit. Without
        one, both commands reach the U1 unchanged.
        """
        for name in ("SDCARD_PRINT_FILE", "SDCARD_PRINT_FILE_WITH_PARAMETERS"):
            if not self._wrap(gcode, name, self._start_handler):
                logging.warning("ace2k_u1: no %s to wrap: its start is not checked", name)

    def wrap_temp_checks(self, gcode: GCode) -> None:
        """Wraps the two print starts, M104, M109 and SET_HEATER_TEMPERATURE for the temperature
        check (TempCheck).

        It is installed with or without a + group, after the other wraps, so it runs first. The
        check only warns. A start is checked once the U1 took it, and only when no print was on,
        from a reactor timer: the file is read then, after the start command returned.
        """
        for name in ("SDCARD_PRINT_FILE", "SDCARD_PRINT_FILE_WITH_PARAMETERS"):
            if not self._wrap(gcode, name, self._start_temps_handler):
                logging.warning("ace2k_u1: no %s to wrap: its temperatures are not checked", name)

        def handler(old: Handler) -> Handler:
            def temperature(gcmd: GCodeCommand) -> None:
                if gcmd.get_command() == "SET_HEATER_TEMPERATURE":
                    self.host.temps.check_heater(gcmd)
                else:
                    self.host.temps.check_command(gcmd)
                old(gcmd)

            return temperature

        for name in ("M104", "M109", "SET_HEATER_TEMPERATURE"):
            if not self._wrap(gcode, name, handler):
                logging.warning("ace2k_u1: no %s to wrap: its temperature is not checked", name)

    def _start_temps_handler(self, old: Handler) -> Handler:
        def start(gcmd: GCodeCommand) -> None:
            on = self.host.u1.print_state() == "printing"
            old(gcmd)
            if on:
                return
            try:
                path, table = self._start_path(gcmd), self._map_table(gcmd)
            except Exception as err:  # no G-code folder: as an unread file, after the start
                logging.info("ace2k_u1: temperatures of the start not checked (%s)", err)
                return
            reactor = self.host.reactor

            def check(_eventtime: float) -> float:
                reactor.unregister_timer(timer)
                self._check_start_temps(path, table)
                return reactor.NEVER

            timer = reactor.register_timer(check, reactor.NOW)

        return start

    def _start_path(self, gcmd: GCodeCommand) -> str:
        """Returns the path of the file of a print start, in the G-code folder.

        Without a G-code folder the join fails, which the callers take as an unread file.
        """
        sd = self.host.printer.lookup_object("virtual_sdcard", None)
        root: str | None = getattr(sd, "sdcard_dirname", None)
        return os.path.join(cast(str, root), self.file_name(root, gcmd.get("FILENAME", "")))

    def _check_start_temps(self, path: str, table: list[int]) -> None:
        """Checks the temperature of each tool of the started file against its lane.

        The lane is the one that the plan chose for the tool, or else the lane in the head that
        the tool table sends it to. A file that cannot be planned (another slicer's) is not
        checked. It never raises.
        """
        try:
            parsed = self.host.tracker.read_plan(path)
        except Exception as err:  # PlanError among them: an ordinary file, or none
            logging.info("ace2k_u1: temperatures of the start not checked (%s)", err)
            return
        tracker = self.host.tracker
        plan = tracker.plan
        choice = plan.choice if plan is not None and plan.set and tracker.plan_started else None
        adapter = self._adapter()

        def lane_of(n: int) -> tuple[int, Hooked | None]:
            h = (choice or {}).get(n)
            if h is not None:
                return h.head, h
            head = self._head_of_tool(table, n)
            slot = adapter.slot_of_head(head)
            return head, None if slot is None else slot.current

        used = self.used_tools(parsed.order, len(parsed.tools))
        self.host.temps.check_start(parsed.tools, used, lane_of)

    def _start_handler(self, old: Handler) -> Handler:
        def start(gcmd: GCodeCommand) -> None:
            # With a print running, the U1 refuses the start itself (SD busy). The start is not
            # checked, and no head of that print is handed another lane before it. If the print
            # is paused, the U1 loads the new file, and the start is checked like any other.
            if self.host.u1.print_state() == "printing":
                old(gcmd)
                return
            before = (
                self.host.tracker.plan_started,
                self.host.tracker.plan_seen_on,
                self.host.tracker.t_index,
                self.host.tracker.t_raw,
            )
            # The print of the plan is this one only when it is the planned file with its plan
            # set. This is decided before the start of the U1, so the first T of the file already
            # swaps.
            self.host.tracker.plan_started = self._check_start(gcmd)
            self.host.tracker.plan_seen_on = False
            self.host.tracker.reset_print()
            try:
                old(gcmd)
            except Exception:
                # Nothing started.
                (
                    self.host.tracker.plan_started,
                    self.host.tracker.plan_seen_on,
                    self.host.tracker.t_index,
                    self.host.tracker.t_raw,
                ) = before
                raise
            # This is a new print. What the print before it (paused) left pending does not belong
            # to it. That is the swap, the owed T commands, the stopped runouts and the taken ends
            # of the lanes (print_over).
            self.host.print_over()

        return start

    @staticmethod
    def missing_macro_text(names: str | Sequence[str]) -> str:
        """Returns the console line for missing macros of the swap.

        names is one name, or the list of names.
        """
        if isinstance(names, str) or len(names) == 1:
            name = names if isinstance(names, str) else names[0]
            return (
                f"ace2k_u1: {name} is not defined: copy it from the release's config/ace2k-u1.cfg "
                "— a print that uses a shared head is refused"
            )
        return (
            f"ace2k_u1: {' and '.join(names)} are not defined: copy them from the release's "
            "config/ace2k-u1.cfg — a print that uses a shared head is refused"
        )

    def _check_start(self, gcmd: GCodeCommand) -> bool:
        """Checks a print start, and refuses it when it is not safe.

        The start is refused in two cases.
        - The tools of the file share a head that several lanes feed, and the plan of that file
          is not set. With the plan, each such head takes the lane of its first tool.
        - The file uses a head that several lanes feed (or any head, with runout_source unit), and
          a swap macro is missing. This holds planned or not, because a swap or a refill on that
          head needs both macros.

        Returns True when the start is the planned file, with its plan set.
        """
        shared = {slot.head for slot in self._adapter().groups()}
        refill = self._adapter().refill_heads()  # a swap or a refill needs both macros
        sd = self.host.printer.lookup_object("virtual_sdcard", None)
        root: str | None = getattr(sd, "sdcard_dirname", None)
        name = self.file_name(root, gcmd.get("FILENAME", ""))
        plan = self.host.tracker.plan
        if plan is not None and plan.set and plan.file == name and self._plan_unchanged(plan):
            self._refuse_missing_macros(refill & set(plan.heads), gcmd)
            self._check_plan_heads(plan, gcmd)
            self._take_first_lanes(plan, gcmd)
            return True
        try:
            # Without a G-code folder (root is None) the join fails, so the file is unread and
            # the failure is logged below.
            parsed = self.host.tracker.read_plan(os.path.join(cast(str, root), name))
        except ace2k_u1_plan.PlanError as err:  # an ordinary file of another slicer: one log line
            logging.info("ace2k_u1: %s: not planned (%s): started unchecked", name, err)
            return False
        except Exception:
            logging.exception("ace2k_u1: %s unread at its start: not checked", name)
            return False
        table = self._map_table(gcmd)
        used = self.used_tools(parsed.order, len(parsed.tools))
        on_head: dict[int, set[int]] = {}
        for n in used:
            on_head.setdefault(self._head_of_tool(table, n), set()).add(n)
        if any(len(on_head.get(head, ())) > 1 for head in shared):
            raise gcmd.error(
                "ace2k_u1: this file shares heads between tools: start it from the ace2k page"
            )
        self._refuse_empty_heads(on_head, gcmd)
        self._refuse_missing_macros(refill & set(on_head), gcmd)
        self._name_unknown_lanes(sorted(shared & set(on_head)), gcmd)
        return False

    def _refuse_empty_heads(self, on_head: dict[int, set[int]], gcmd: GCodeCommand) -> None:
        """Refuses a start with no plan when a tool lands on a head with no filament and no lane.

        The head has no filament in the record that the U1 keeps, and no lane is hooked to it. A
        file meant for a shared head could be started outside the page, with the table of the U1
        at one tool per head. It would print on that head. This was seen when starting from
        Fluidd.
        on_head maps a head to the tools of the file on it. A record that cannot be read refuses
        nothing, and the failure is logged.
        """
        try:
            exist = self.host.u1.filament_exist()
        except Exception:
            logging.exception("ace2k_u1: the U1's filament record unread: no empty head refused")
            return
        for head in sorted(on_head):
            if head >= len(exist) or exist[head] or self._adapter().slot_of_head(head) is not None:
                continue
            tool = min(on_head[head])
            raise gcmd.error(
                f"ace2k_u1: T{tool} prints on head {head + 1}, which has no filament and no lane: "
                "start it from the ace2k page"
            )

    def _refuse_missing_macros(self, heads: set[int], gcmd: GCodeCommand) -> None:
        """Refuses a start whose file uses heads that swap or refill, when a swap macro is missing.

        This is checked before anything is probed, handed or heated. The heads are the ones that
        several lanes feed, and with runout_source unit every head. If heads is empty, nothing is
        refused.
        """
        missing = self.host.swapper.missing_swap_macros() if heads else []
        if missing:
            raise gcmd.error(self.missing_macro_text(missing))

    def _name_unknown_lanes(self, heads: Sequence[int], gcmd: GCodeCommand) -> None:
        """Names the lane of a shared head when a print with no plan starts and the lane is unknown.

        After a restart, nothing names the lane. This runs before the start of the U1 heats
        anything. These are the cases.
        - The sensor of the head is set, or cannot be read. The probe names the lane in it, and
          that lane becomes the lane of the head. A probe that fails refuses the start.
        - The sensor is empty. No lane is chosen, and the start is refused with the way to name
          one.
        Every head is read before any head is handed over.
        """
        adapter = self._adapter()
        take = []
        for head in heads:
            slot = adapter.slot_of_head(head)
            if slot is None or not slot.shared or slot.current is not None:
                continue
            # The lane is unknown. The probe reads the sensor again, and refuses.
            unread = f"ace2k_u1: head {head + 1}'s sensor unread at a start"
            if not self.host.head_set_or(slot.lanes[0], True, unread):
                raise gcmd.error(
                    f"ace2k_u1: head {head + 1}: no lane is known for it: choose one with "
                    "ACEK_LANE_ACTIVE"
                )
            with self.as_gcode_error(gcmd, head):
                take.append((slot, self._probe().which_lane(slot)))
        for slot, h in take:
            with self.as_gcode_error(gcmd, slot.head):
                adapter.head_watch.hand_head(slot, h)

    @staticmethod
    def used_tools(order: Sequence[int], count: int) -> list[int]:
        """Returns the tools that a file prints (its T lines), in number order.

        If none was read, it returns every tool of its config block.
        """
        return sorted(set(order) or range(count))

    def _check_plan_heads(self, plan: Plan, gcmd: GCodeCommand) -> None:
        """Refuses a planned start whose tool table sends a tool to the wrong head.

        The wrong head is another head than the one that the chosen lane of the tool feeds. The
        table is the one that the start prints with.
        """
        table = self._map_table(gcmd)
        choice = cast("dict[int, Hooked]", plan.choice)  # a plan that is set has a choice
        for n in self.used_tools(plan.order, len(plan.tools)):
            h = choice.get(n)
            head = self._head_of_tool(table, n)
            why = None if h is None else self.host.material.refusal(h)
            if h is not None and why is not None:
                lane = self._adapter().lane_text(h)
                raise gcmd.error(f"ace2k_u1: T{n}: lane {lane}: {why}")
            if h is not None and head != h.head:
                # The adapter is hooked, so the coordinator of ace2k is there.
                lane = cast("Coordinator", self.host.units()).lane_text(h.unit, h.lane)
                raise gcmd.error(
                    f"ace2k_u1: T{n} goes to head {head + 1} but its lane {lane} feeds head "
                    f"{h.head + 1}: start it from the ace2k page"
                )

    @classmethod
    def file_name(cls, root: str | None, raw: str | None) -> str:
        """Returns a file name as the G-code folder knows it.

        The name is as typed (FILENAME= of a start, FILE= of ACEK_PLAN and ACEK_PLAN_SET). Quotes
        and a leading / are taken off, because the start of the U1 reads / as the root of the
        folder. ./ and a/../ are folded. The case is the one of the folder (_sd_name). The plan
        and the start compare these names.
        """
        name = _unquoted(raw or "").lstrip("/")
        if name:
            name = os.path.normpath(name)
        return cls._sd_name(root, name)

    @staticmethod
    def _sd_name(root: str | None, name: str) -> str:
        """Returns the name that the G-code folder uses for a file, as the U1 finds it.

        If the folder lists the name as given, that name is returned. Otherwise the result is the
        file of that folder whose name matches without case. If none does, or the folder cannot be
        listed, the name as given is returned. Only the folder of the file is listed, and its
        listing decides, not the file system. A file system without case would answer for any
        case.
        """
        if root is None:
            return name
        folder, base = os.path.split(name)
        try:
            path = os.path.join(root, folder)
            listed = os.listdir(path)
            if base in listed and os.path.isfile(os.path.join(path, base)):
                return name
            wanted = base.lower()
            for f in sorted(listed):
                if f.lower() == wanted and os.path.isfile(os.path.join(path, f)):
                    return os.path.join(folder, f)
        except Exception:
            logging.exception("ace2k_u1: the folder of %s unread at a start", name)
        return name

    @staticmethod
    def _plan_unchanged(plan: Plan) -> bool:
        """Tells whether the planned file has not changed since its plan.

        A file that cannot be read counts as changed.
        """
        try:
            return os.path.getmtime(plan.path) == plan.mtime
        except Exception:
            return False

    @staticmethod
    def _head_of_tool(table: Sequence[int], n: int) -> int:
        """Returns the head that the tool table sends tool n to.

        A tool that the table lacks goes to head 1 (index 0).
        """
        return table[n] if n < len(table) else 0

    def _map_table(self, gcmd: GCodeCommand) -> list[int]:
        """Returns the tool -> head table that the start will print with.

        SDCARD_PRINT_FILE_WITH_PARAMETERS resets the table of the U1 to its default and applies
        its MAP_TABLE=[[tool,head],...]. So the table is read from there. SDCARD_PRINT_FILE keeps
        the table as it is set (in print_task_config).
        """
        default = list(range(LANES)) + [0] * (TOOLS - LANES)
        if gcmd.get_command() == "SDCARD_PRINT_FILE_WITH_PARAMETERS":
            table = list(default)
            for tool, head in MAP_PAIR.findall(gcmd.get("MAP_TABLE", None) or ""):
                if int(tool) < TOOLS:
                    table[int(tool)] = int(head)
            return table
        try:
            return self.host.u1.map_table()
        except Exception:
            logging.exception("ace2k_u1: the U1's tool table unread at a start: the default")
            return default

    @contextlib.contextmanager
    def as_gcode_error(
        self, gcmd: GCodeCommand, head: int, undo: Callable[[], object] | None = None
    ) -> Iterator[None]:
        """Raises the failure of the probe and the refusals of the core as a G-code error.

        The refusals are Adapter.refusals. They are raised as the G-code error of the command. If
        undo is given, it is called first. Anything else goes up as it came. head is 0-based.
        """
        try:
            yield
        except ace2k_u1_probe.ProbeError as err:
            if undo is not None:
                undo()
            raise gcmd.error(f"ace2k_u1: {err}") from None
        except self._adapter().refusals() as err:
            if undo is not None:
                undo()
            raise gcmd.error(f"ace2k_u1: head {head + 1}: {err}") from None

    def _take_first_lanes(self, plan: Plan, gcmd: GCodeCommand) -> None:
        """Gives each shared head that the plan uses the lane of its first tool.

        The heads are the ones that several lanes feed. They are taken in the T order of the
        file, before the start of the U1 heats anything. There are two cases.
        - The sensor of the head is set, or cannot be read. The probe tells which lane is in it,
          and that lane becomes the lane of the head. The first T of the head swaps it when it is
          not the lane of that tool.
        - The sensor is empty. The lane of the first tool becomes the lane of the head. The load
          at the start of the U1 pushes it through the push of the probe.
        Every head is probed before any head is handed over. A probe that fails refuses the
        start. So does a head that is not handed over (a follower that does not end). So the
        choice of the core and the current lane of the head agree.

        Each used head is then set for the lane it starts with (_start_settings), so the U1's
        load at the start heats for it.

        A missing swap macro has already refused the start (_check_start).
        """
        adapter = self._adapter()
        first: dict[int, Hooked] = {}  # head -> the lane that the head starts with
        take: list[tuple[Slot, Hooked]] = []  # (slot, the lane that the head takes)
        choice = cast("dict[int, Hooked]", plan.choice)  # a plan that is set has a choice
        for n in plan.order:
            h = choice.get(n)
            if h is None or h.head in first:
                continue
            first[h.head] = h
            slot = adapter.slot_of(h)
            if slot is None or not slot.shared:
                continue
            # The lane is unknown. The probe reads the sensor again, and refuses.
            unread = f"ace2k_u1: head {h.head + 1}'s sensor unread at a start"
            if self.host.head_set_or(h, True, unread):
                h = self._known_or_probed(slot, gcmd)
            first[h.head] = h
            take.append((slot, h))
        writes = self._start_settings(list(first.values()))  # decided before the handing
        for slot, h in take:
            with self.as_gcode_error(gcmd, h.head):
                adapter.head_watch.hand_head(slot, h)
        self._write_start_settings(writes)

    def _start_settings(self, lanes: Sequence[Hooked]) -> list[Hooked]:
        """Returns the lanes of a planned start whose head's setting is to be written.

        lanes holds the lane that each used head starts with. The U1 heats its load at the start
        from the head's setting, and the load refuses a lane whose type the setting does not have
        (MaterialGate.after_load_push), which paused the print. A head is written when its
        setting is unset (NONE), or, on a shared head, when it was not written for the lane
        (setting_for): the plan hands the head to that lane, and the lane's spool is what loads.
        A head of one lane that is set keeps its setting. With the heads' settings unread
        nothing is written.
        """
        settings = self.host.spool_lanes.head_settings()
        if not settings:
            return []
        adapter = self._adapter()
        out = []
        for h in lanes:
            slot = adapter.slot_of(h)
            if slot is None or h.head >= len(settings):
                continue
            if settings[h.head] is None or (slot.shared and slot.setting_for is not h):
                out.append(h)
        return out

    def _write_start_settings(self, lanes: Sequence[Hooked]) -> None:
        """Writes the head of each lane from its spool once the heads are handed (_start_settings).

        Each lane's status is read here, after the handing; an unread one still has its record.
        The spool is the lane's tag, else its Edit spool record. A lane with neither is not
        written and nothing is said: the plan and the load have their own words. A type that the
        U1's filament table does not list writes the colour only, and a line names where the type
        came from and what to do. The setting stays written if the start then fails: the operator
        chose that spool for the lane. A setting not written is said, and the start goes on.
        """
        if not lanes:
            return
        adapter = self._adapter()
        spools = self.host.spool_lanes
        table = spools.catalogue()  # once per start
        for h in lanes:
            lane = spools.lane_read(h)
            source, record, _colour = spools.own_record(h, lane=lane)
            spool = spools.own_spool(h, lane, table)
            if spool is None:
                continue
            why = self.host.swapper.set_head_spool(h, new_lane=False, spool=spool)
            if why is None and spool[1] is None and table[0]:
                why = self._unlisted_line(h, source, record)
            if why is not None:
                adapter.say(why)

    def _unlisted_line(self, h: Hooked, source: str | None, record: Mapping[str, object]) -> str:
        """The line for a lane whose type the U1's filament table does not list (colour only)."""
        n = self._adapter().lane_text(h)
        todo = "set a listed type with Edit spool"
        kind = record.get("type")
        if source == "tag":
            kind, todo = record.get("material"), f"forget the tag (Read tag) and {todo}"
        return (
            f"ace2k_u1: head {h.head + 1}: {n}'s {'tag' if source == 'tag' else 'record'} says"
            f" {str(kind or '').strip()}, which the U1's filament table does not list: only its"
            f" color is set — {todo}"
        )

    def _known_or_probed(self, slot: Slot, gcmd: GCodeCommand) -> Hooked:
        """Return the lane in the head of slot, whose sensor is set, at a start with a plan.

        The lane that the last load or swap put in the head (slot.loaded) is the answer while it is
        still the head's lane and no lane of the head is moving (only a follow may run), and the
        console says so. Otherwise the probe names it.
        """
        known = slot.loaded
        if known is not None and known is slot.current and self._lanes_still(slot):
            adapter = self._adapter()
            adapter.say(
                f"ace2k_u1: head {slot.head + 1}: {adapter.lane_text(known)} in it since its last"
                " load: no probe"
            )
            return known
        with self.as_gcode_error(gcmd, slot.head):
            return self._probe().which_lane(slot)

    def _lanes_still(self, slot: Slot) -> bool:
        """True when no lane of slot runs a move other than a follow. An unread lane is moving."""
        adapter = self._adapter()
        for h in slot.lanes:
            lane = adapter.lane_or_none(h)  # an unread lane is logged
            if lane is None or lane.get("mode") not in STILL_MODES:
                return False
        return True

    def wrap_tools(self, gcode: GCode) -> None:
        """Wraps T0…T31, which are commands of the U1 and macros of its config.

        A tool whose lane is not in its head swaps the lane first, and then the T of the U1 runs.
        The wrap (_wrap) is installed only with a + group or runout_source unit. Without one, every
        T reaches the U1 unchanged. A name that the printer lacks is skipped, and the log says so
        once.
        """
        missing = []
        for n in range(TOOLS):

            def handler(old: Handler, n: int = n) -> Handler:
                self.tool_handlers[n] = old  # run by the RESUME of a swap that this T paused

                def tool(gcmd: GCodeCommand) -> None:
                    # A failed swap pauses the print plainly, and the T of the U1 is not run. The
                    # print goes on from the next line, so the RESUME of the adapter runs the T once
                    # the swap is done (_run_tool).
                    if gcmd.get_int("A", 1) != 0:  # a T of the file, so what came before is past
                        self.host.tracker.preextrude_after = None
                        self.host.tracker.clear_wait_skip()
                    self.host.tracker.count_tool(gcmd, n)
                    owed = self.host.tracker.take_owed_heat(gcmd, n)
                    if self._tool_swap(gcmd, n):
                        old(gcmd)
                        self._heat_docked(owed)

                return tool

            if not self._wrap(gcode, f"T{n}", handler):
                missing.append(f"T{n}")
        if missing:
            logging.info("ace2k_u1: no %s to wrap", ", ".join(missing))

    def wrap_end_unload(self, gcode: GCode) -> None:
        """Wraps the U1's end-of-print unload so every head it unloads heats first.

        The U1 unloads the used heads one at a time and heats each only when its turn comes. The
        wrap tells each of them to heat before the U1's macro runs, in the U1's order. The U1
        heats two heads at a time and queues the rest. Without the macro, nothing is wrapped.
        """
        if not self._wrap(gcode, END_UNLOAD, self._end_unload_handler):
            logging.info("ace2k_u1: no %s to wrap", END_UNLOAD)

    def _end_unload_handler(self, old: Handler) -> Handler:
        def end_unload(gcmd: GCodeCommand) -> None:
            try:
                self._end_unload_heat()
            except Exception:  # the unload itself must run
                logging.exception("ace2k_u1: the end unload's pre-heat")
            old(gcmd)

        return end_unload

    def _end_unload_heat(self) -> None:
        """Sends M104 for every head the U1's end unload takes, at the U1's load temperature."""
        adapter = self._adapter()
        method = FILAMENT_PARAMETERS["load_temp"]
        extruders = adapter.extruders or {}
        for head in sorted(extruders):
            if not adapter.print_end_unloads(head):
                continue
            ext = self.host.u1.extruder(extruders.get(head))
            value = self.host.u1.u1_filament(ext, head, method)
            if not finite(value) or value <= 0:
                logging.info(
                    "ace2k_u1: head %d: no temperature for the end unload's pre-heat", head + 1
                )
                continue
            self.host.u1.script(f"M104 S{float(value):g} T{head} A0")

    def wrap_preextrude(self, gcode: GCode) -> None:
        """Wraps SM_PRINT_PREEXTRUDE_FILAMENT, which the U1 runs after each T of a slicer file.

        The swap ends with the U1's own pre-extrusion of its head. So the file's call right after
        a swap of that head is skipped (_skip_after_swap). Every other call reaches the U1. The
        wrap is installed only with a + group or runout_source unit. Without the command, nothing
        is wrapped.
        """

        def handler(old: Handler) -> Handler:
            def preextrude(gcmd: GCodeCommand) -> None:
                if not self._skip_after_swap(gcmd):
                    old(gcmd)

            return preextrude

        if not self._wrap(gcode, "SM_PRINT_PREEXTRUDE_FILAMENT", handler):
            logging.info("ace2k_u1: no SM_PRINT_PREEXTRUDE_FILAMENT to wrap")

    def _skip_after_swap(self, gcmd: GCodeCommand) -> bool:
        """Tells whether the file's pre-extrusion is the one right after a swap of its head.

        The swap ended with the U1's own pre-extrusion, so this call is skipped, forced or not.
        The mark is spent by it. A call for another head, or after another T, reaches the U1.
        A call that cannot be read reaches the U1, and the mark is spent.
        """
        head = self.host.tracker.preextrude_after
        if head is None:
            return False
        try:
            index = gcmd.get_int("INDEX", None)
            if index is None or self.host.u1.map_table()[index] != head:
                return False
        except Exception:
            logging.exception("ace2k_u1: the pre-extrusion after a swap")
            self.host.tracker.preextrude_after = None
            return False
        self.host.tracker.preextrude_after = None
        logging.info("ace2k_u1: head %d: the file's pre-extrusion skipped after the swap", head + 1)
        return True

    def wrap_temperatures(self, gcode: GCode) -> None:
        """Wraps M104 and M109 of the U1.

        A call for a tool whose lane is not in its head is kept for that tool. The tracker says
        whether it is also applied, and with which S (PrintTracker.keep_temperature). The new S
        goes to the U1's handler in a new call, never back through this wrapper. The wrap is
        installed only with a + group or runout_source unit. Every other call reaches the U1
        unchanged. If the printer lacks one of the two commands, a log line names it.
        """

        olds: dict[str, Handler] = {}  # the U1's M104 is kept: an M109 runs it to skip its wait

        def handler(old: Handler, name: str) -> Handler:
            olds[name] = old

            def temperature(gcmd: GCodeCommand) -> None:
                call = self.host.tracker.keep_temperature(gcmd)
                if call.kept:
                    return
                if call.s is not None:  # a docked head's pre-heat, capped
                    gcmd = self._with_s(gcmd, call.s)
                m104 = olds.get("M104")
                if name == "M109" and m104 is not None and self.host.tracker.skip_wait(gcmd):
                    logging.info(
                        "ace2k_u1: the wait after the swap is skipped: %s", gcmd.get_commandline()
                    )
                    m104(gcmd)  # the same S and T, set without the wait
                    return
                old(gcmd)

            return temperature

        def handler_for(name: str) -> Callable[[Handler], Handler]:
            return lambda old: handler(old, name)

        for name in ("M104", "M109"):
            if not self._wrap(gcode, name, handler_for(name)):
                logging.warning(
                    "ace2k_u1: no %s to wrap: a tool's temperature for a lane not in its head is "
                    "applied, not kept for its swap",
                    name,
                )

    def _with_s(self, gcmd: GCodeCommand, s: float) -> GCodeCommand:
        """Returns a copy of a temperature call with S set to s. The U1's handler runs it."""
        params = dict(gcmd.get_command_parameters())
        params["S"] = f"{s:g}"
        command = gcmd.get_command()
        line = " ".join([command] + [f"{k}{v}" for k, v in params.items()])
        logging.info("ace2k_u1: %s capped at the tip temperature: %s", command, line)
        gcode = self.host.printer.lookup_object("gcode")
        return cast("GCodeCommand", gcode.create_gcode_command(command, line, params))

    def _heat_docked(self, owed: list[tuple[int, float, str]]) -> None:
        """Sends the pre-heat that each head owes, now that the U1's T has docked it.

        It runs after the U1's T, so the head is docked for certain and nothing in the T sets
        its temperature after this. It is an M104 with A0 for the head itself, so nothing waits.
        A failure is logged, and the next head is still sent.
        """
        for head, s, line in owed:
            try:
                self.host.u1.script(f"M104 S{s:g} T{head} A0")
            except Exception:
                logging.exception("ace2k_u1: head %d's owed pre-heat", head + 1)
                continue
            logging.info("ace2k_u1: head %d docked: %s: M104 S%g T%d A0", head + 1, line, s, head)

    def _tool_swap(self, gcmd: GCodeCommand, n: int) -> bool:
        """Runs what T<n> needs before the T of the U1. Returns True when the T of the U1 is to run.

        With a swap pending (a failure paused it), a T of its head goes on with it. Any other T is
        refused with a plain pause. A new swap never starts over a pending one. Otherwise the swap
        that swap_for asks for runs, if there is one. Returns False when the swap or the refusal
        paused the print.
        """
        if gcmd.get_int("A", 1) == 0:
            return True  # the T calls of the U1 itself (its unload and load among them)
        pending = self.host.swapper.swap_pending
        if pending is not None:  # swap refuses a T of another head
            h = self.host.tracker.tool_lane(n)
            return self.host.swapper.swap(
                gcmd, None if h is None else self._adapter().slot_of(h), h, n
            )
        swap = self.host.tracker.swap_for(n)
        if swap is not None:
            return self.host.swapper.swap(gcmd, *swap, n)
        return True

    def wrap_resume(self, gcode: GCode) -> None:
        """Wraps RESUME of the U1.

        A swap that a failure paused is finished first, then the T that asked for it. A refill
        goes on the same way. The RESUME of the U1 runs only once both have run. The wrap is
        installed only with a + group, or with runout_source unit.
        """
        if not self._wrap(gcode, "RESUME", self._resume_handler):
            logging.warning("ace2k_u1: no RESUME to wrap: a failed swap is not restarted")

    def _resume_handler(self, old: Handler) -> Handler:
        def resume(gcmd: GCodeCommand) -> None:
            if not self.host.refiller.stopped_runouts_clear():
                return  # a head that a runout stopped during the pause is not loaded again yet
            # The Resume button of the page waits for the whole RESUME, which can take minutes.
            self.host.swapper.resuming = True
            try:
                self._resume_all(gcmd, old)
            finally:
                self.host.swapper.resuming = False

        return resume

    def _resume_all(self, gcmd: GCodeCommand, old: Handler) -> None:
        """Runs the RESUME of the adapter, after the check of its runouts.

        It goes on with a pending swap, then runs the owed T commands, then runs the RESUME of the
        U1. It returns early, with the print still paused, at the first step that does not go on.
        """
        pending = self.host.swapper.swap_pending
        if pending is not None:
            if not self.host.swapper.go_on(gcmd, pending):
                return  # the head unread, or failed again: still paused
            # This is the T that the print asked for and that the pause skipped.
            self.host.swapper.owe_pending(pending)
        if not self._owed_tools(gcmd):
            return  # still paused: the next RESUME runs the rest again
        old(gcmd)
        self._adapter().pause_reason = None  # the RESUME of the U1 took it, and the print goes on

    def _owed_tools(self, gcmd: GCodeCommand) -> bool:
        """Runs the owed T commands at RESUME, in order (_owed_tool).

        Returns True once they all ran. Returns False at the first one that did not run. That T
        and the rest stay owed, and the print stays paused.
        """
        while self.host.swapper.tools_owed:
            if not self._owed_tool(gcmd, self.host.swapper.tools_owed[0]):
                return False
        return True

    def _owed_tool(self, gcmd: GCodeCommand, state: OwedTool | SwapState) -> bool:
        """Runs one owed T at RESUME, through the T path of the adapter.

        It is not counted again, because count_tool counted it when the file ran it. First its
        lane is put in its head, when another lane is in it. That happens when a T typed while
        paused swapped the lane, or after a T of another shared head. Then the T of the U1 runs
        (_run_tool). Returns False while the print is paused. A swap that fails is pending, and
        its T is this owed one.
        """
        n = state.tool
        swap = self.host.tracker.swap_for(n) if n is not None else None
        if (
            swap is not None
            and n is not None
            and not self.host.swapper.swap(gcmd, *swap, n, params=state.tool_params)
        ):
            if self.host.swapper.swap_pending is not None:
                self.host.swapper.swap_pending.owed = True  # its T is this one, owed already
            return False
        return self._run_tool(state)

    def _drop_owed(self, state: OwedTool | SwapState) -> None:
        self.host.swapper.tools_owed = [e for e in self.host.swapper.tools_owed if e is not state]

    def _run_tool(self, state: OwedTool | SwapState) -> bool:
        """Runs the T of the U1 for the tool that a pending swap was for.

        It uses the parameters of that T. If they were not read, it runs a plain T<n>, and the
        console says so. The print went on past this T at the pause. The T stays owed
        (tools_owed) until it runs, and then the method returns True. A T that fails is reported
        and stays owed, and the print stays paused. The method then returns False, and the next
        RESUME runs the T again before the RESUME of the U1.
        """
        adapter = self._adapter()
        # The tool of an owed T. A refill has none, so it is never owed (Swapper.owe_pending).
        n, kept = cast(int, state.tool), state.tool_params
        old = self.tool_handlers.get(n)
        if old is None:  # never wrapped, so there is nothing of the U1 to run
            adapter.say(f"ace2k_u1: T{n} after the swap: the printer has no T{n} — not run")
            self._drop_owed(state)
            return True
        if kept is None:
            adapter.say(f"ace2k_u1: T{n}'s parameters were not read: run as T{n}")
            kept = {"command": f"T{n}", "commandline": f"T{n}", "params": {}}
        gcode = self.host.printer.lookup_object("gcode")
        try:
            params = dict(kept["params"])  # a copy, so a retried owed T gets the original ones
            old(gcode.create_gcode_command(kept["command"], kept["commandline"], params))
        except Exception as err:
            logging.exception("ace2k_u1: T%d after the swap", n)
            why = error_line(err)
            adapter.alert(
                f"ace2k_u1: T{n} after the swap failed: {why} — still paused; RESUME runs it"
            )
            return False
        self._drop_owed(state)
        return True
