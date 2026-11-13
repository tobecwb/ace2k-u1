"""Holds the commands that the web page sends.

They set the spool of a lane (ACEK_SPOOL), forget a tag (ACEK_TAG_FORGET), set a head from the
spool of its lane (ACEK_HEAD_SPOOL), plan a file (ACEK_PLAN) and keep its choice
(ACEK_PLAN_SET). ace2k_u1.py registers the commands and calls the PageCommands.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_config import SwapConfig
    from .ace2k_u1_material import MaterialGate
    from .ace2k_u1_plan import LaneSpool, Tool
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_printer_types import Coordinator, GCodeCommand, Unit
    from .ace2k_u1_spools import LaneSpools, SpoolStore
    from .ace2k_u1_swap import Swapper
    from .ace2k_u1_tracker import PlanTool, PrintTracker
    from .ace2k_u1_wraps import Wraps

try:
    from . import ace2k_u1_in_use as in_use_module
    from . import ace2k_u1_plan
    from .ace2k_u1_adapter import PRINT_ON
    from .ace2k_u1_common import own_spool_came, tag_state
    from .ace2k_u1_config import LANES
    from .ace2k_u1_material import MaterialError
    from .ace2k_u1_swap import NoSpoolLine
    from .ace2k_u1_tracker import Plan
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_in_use as in_use_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_plan  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_adapter import PRINT_ON  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        own_spool_came,
        tag_state,
    )
    from ace2k_u1_config import LANES  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_material import MaterialError  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_swap import NoSpoolLine  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_tracker import Plan  # type: ignore[import-not-found, no-redef]


class PageHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the commands of the page read it.

    It gives the adapter (None until the hook builds it) and the reason why nothing was hooked.
    It gives the spools, the plan, the U1, the swap config and the wraps. It gives the
    coordinator of ace2k, and the name and path of a G-code file. It also lets a command ask the
    status to build its copies again.
    """

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def problems(self) -> list[str] | None: ...
    @property
    def spool_store(self) -> SpoolStore: ...
    @property
    def spool_lanes(self) -> LaneSpools: ...
    @property
    def swapper(self) -> Swapper: ...
    @property
    def material(self) -> MaterialGate: ...
    @property
    def tracker(self) -> PrintTracker: ...
    @property
    def u1(self) -> U1Printer: ...
    @property
    def swap_cfg(self) -> SwapConfig: ...
    @property
    def wraps(self) -> Wraps: ...
    def units(self) -> Coordinator | None: ...
    def gcode_file(self, gcmd: GCodeCommand) -> tuple[str, str]: ...
    def changed(self) -> None: ...


class PageCommands:
    """Runs the commands that the web page sends.

    host is the ACE2kU1. Its adapter is built at the hook. Its spools, plan, U1 and wraps are read
    when used.
    """

    def __init__(self, host: PageHost) -> None:
        self.host = host

    def _units(self) -> Coordinator:
        """Returns the coordinator of ace2k. It is there once the adapter is hooked."""
        return cast("Coordinator", self.host.units())

    def _lane_ref(self, gcmd: GCodeCommand) -> tuple[Unit, int, str]:
        """Returns the unit, the lane index and the text of the lane that LANE= and UNIT= name.

        Raises a G-code error when they name no lane.
        """
        units = self.host.units()
        if units is None or not getattr(units, "units", None):
            raise gcmd.error("ace2k_u1: no ace2k unit loaded")
        lane = gcmd.get_int("LANE", minval=1, maxval=LANES) - 1
        unit = units.unit_for(gcmd)  # its error is raised as it is
        if unit is None:  # a unit that the config declares, but that is not loaded yet
            raise gcmd.error(f"ace2k_u1: lane {lane + 1}: its unit is not loaded yet")
        return unit, lane, units.lane_text(unit, lane)

    def cmd_ACEK_SPOOL(self, gcmd: GCodeCommand) -> None:
        """Sets the spool of a lane. The spool is kept across restarts.

        The command is ACEK_SPOOL [UNIT=u] LANE=n TYPE=<material> COLOR=rrggbb [VENDOR=Generic].
        The plan reads it for a lane whose spool has no tag.
        """
        unit, lane, text = self._lane_ref(gcmd)
        color, kind = self._spool_params(gcmd)
        key = self.host.spool_store.key(unit, lane)
        try:
            self.host.spool_store.set(
                key,
                {
                    "vendor": (gcmd.get("VENDOR", "Generic") or "Generic").strip(),
                    "type": kind,
                    "color": "#" + color,
                },
            )
        except OSError as err:
            raise gcmd.error(f"ace2k_u1: spool not saved: {err}") from None
        self._spool_set_on(unit, lane)
        in_use_module.tell_by_hand(unit, lane)
        gcmd.respond_info(f"ace2k_u1: lane {text} spool: {kind} #{color}")

    @staticmethod
    def _spool_params(gcmd: GCodeCommand) -> tuple[str, str]:
        """Returns COLOR= as rrggbb and TYPE= of ACEK_SPOOL, or raises a G-code error."""
        color = (gcmd.get("COLOR", "") or "").strip().lower().lstrip("#")
        if not re.fullmatch(r"[0-9a-f]{6}", color):
            raise gcmd.error("ace2k_u1: COLOR must be 6 hex digits (rrggbb)")
        kind = (gcmd.get("TYPE", "") or "").strip()
        if not kind:
            raise gcmd.error("ace2k_u1: TYPE= is the material (PLA, PETG, ...)")
        return color, kind

    def _spool_set_on(self, unit: object, lane: int) -> None:
        """Handles a spool record that was just set on the lane (of unit), when the lane is hooked.

        An empty bay waits to fill and empty before the record goes. The lane in its shared head
        speaks for the setting of the head.
        """
        # Edit spool on the lane in its shared head also writes the setting of the head (the page
        # sends it next). The setting then speaks for this lane.
        adapter = self.host.adapter
        h = adapter.hooked_lane(unit, lane) if adapter is not None and adapter.hooked else None
        if adapter is None or h is None:
            return
        # A record set on an empty bay waits until the bay fills and empties.
        h.drop_due = h.drop_wait = False
        # A newer word than the head's setting: the record speaks for the lane of a head that it
        # feeds alone until the head is written for it (the page's Edit spool writes the same).
        own_spool_came(h)
        slot = adapter.slot_of(h)
        if slot is not None and slot.current is h:
            slot.setting_for = h

    def cmd_ACEK_TAG_FORGET(self, gcmd: GCodeCommand) -> None:
        """Forgets the tag that was read on a lane.

        The command is ACEK_TAG_FORGET [UNIT=u] LANE=n. The forget works through ACEK_RFID_FORGET
        of ace2k. The adapter does not read the tag again until the spool of the lane leaves its
        bay. The tag policy writes nothing from the lane, and the spool of the lane is its
        ACEK_SPOOL record, which can be edited.

        The mark lives in memory only. It goes when the bay empties, as its record does. READ=1
        undoes the forget instead. The mark goes, and the unit reads the tag again on the next
        move of the lane.

        A lane with no tag read is refused, and nothing is marked.
        """
        unit, lane, text = self._lane_ref(gcmd)
        adapter = self._hooked_adapter(gcmd)
        h = adapter.hooked_lane(unit, lane)
        if h is None:
            raise gcmd.error(f"ace2k_u1: lane {text} feeds no head: nothing to forget")
        if gcmd.get_int("READ", 0):
            h.tag_forgotten = False
            h.setting_mark = None  # a setting written from the old read no longer speaks for it
            self.host.u1.script(f"ACEK_RFID_FORGET {adapter.lane_args(h)}")
            gcmd.respond_info(f"ace2k_u1: lane {text} will read its tag again")
            return
        if tag_state(self.host.spool_lanes.lane_read(h)) != "read":
            raise gcmd.error(f"ace2k_u1: lane {text} has no tag read: nothing to forget")
        self.host.u1.script(f"ACEK_RFID_FORGET {adapter.lane_args(h)}")
        h.tag_forgotten = True
        h.setting_mark = None  # a setting written from the forgotten read no longer speaks for it
        gcmd.respond_info(f"ace2k_u1: lane {text} tag forgotten: set its spool with Edit spool")

    def cmd_ACEK_HEAD_PREPARE(self, gcmd: GCodeCommand) -> None:
        """Gets a lane ready for the page's Load into head N, before the U1's load.

        The command is ACEK_HEAD_PREPARE [UNIT=u] LANE=n. The U1 takes the temperature of its load
        from the head's setting when the load starts. A lane whose material is set needs nothing.
        Otherwise the lane is pushed to its head's sensor, and its tag is read on the way. With the
        material still not set, the lane is rolled back out of the head (behind the splitter of a
        shared head) and the command is an error: set it with Edit spool. The lane must be the lane
        of its head, with the head empty and no print on.
        """
        ref = self._lane_ref(gcmd)
        text = ref[2]
        adapter = self._hooked_adapter(gcmd)
        self._no_print_on(gcmd)
        h, slot = self._head_lane(gcmd, adapter, ref)
        if slot.current is not h:
            raise gcmd.error(f"ace2k_u1: lane {text} is not the lane of head {h.head + 1}")
        if adapter.ejector.u1_flow(h):
            raise gcmd.error(
                f"ace2k_u1: lane {text}: the U1 is loading/unloading head {h.head + 1}"
            )
        if adapter.head_set(h) and self.host.material.defined(h) is False:
            raise gcmd.error(f"ace2k_u1: head {h.head + 1} already holds filament: unload it first")
        try:
            line = self.host.material.prepare(h)
        except MaterialError as err:
            raise gcmd.error(str(err)) from None
        gcmd.respond_info(line)

    def cmd_ACEK_HEAD_SPOOL(self, gcmd: GCodeCommand) -> None:
        """Sets the filament setting of a lane's head from the lane's spool, as a swap does.

        The command is ACEK_HEAD_SPOOL [UNIT=u] LANE=n. The page sends it before its head load. It
        runs the swap's own spool step (Swapper.set_head_spool): the lane's tag, in the names of
        the U1's filament table, or else its ACEK_SPOOL record. The lane must be the lane of its
        head (slot.current: the one lane, or the one ACEK_LANE_ACTIVE named; the head may still be
        empty), and no swap may be pending: a pending swap sets its head's spool itself when it
        goes on. A lane with no spool set is said in the console, not an error, and nothing is
        written. A setting that cannot be written is an error. A setting that is not written
        keeps speaking for the lane it was written for (set_head_spool with new_lane cleared).
        """
        ref = self._lane_ref(gcmd)
        text = ref[2]
        adapter = self._hooked_adapter(gcmd)
        h, slot = self._head_lane(gcmd, adapter, ref)
        if slot.current is not h:
            now = "no lane is" if slot.current is None else f"{adapter.lane_text(slot.current)} is"
            raise gcmd.error(
                f"ace2k_u1: lane {text} is not in head {h.head + 1}: {now} the head's lane"
            )
        swapper = self.host.swapper
        pending = swapper.swap_pending
        if pending is not None:
            raise gcmd.error(
                f"ace2k_u1: {swapper.swap_text(pending)} is pending at {pending.step} — RESUME it"
                " first"
            )
        why = swapper.set_head_spool(h, new_lane=False)
        if isinstance(why, NoSpoolLine):
            gcmd.respond_info(why)
            return
        if why is not None:
            raise gcmd.error(why)
        gcmd.respond_info(f"ace2k_u1: head {h.head + 1}: {text}'s spool set on the head")

    @staticmethod
    def _head_lane(
        gcmd: GCodeCommand, adapter: Adapter, ref: tuple[Unit, int, str]
    ) -> tuple[Hooked, Slot]:
        """Returns the hooked lane that ref (_lane_ref) names, and the slot of its head.

        Raises a G-code error when the lane feeds no head.
        """
        unit, lane, text = ref
        h = adapter.hooked_lane(unit, lane)
        if h is None:
            raise gcmd.error(f"ace2k_u1: lane {text} feeds no head")
        return h, cast("Slot", adapter.slot_of(h))

    @staticmethod
    def _lanes_in_order(adapter: Adapter) -> list[Hooked]:
        """Returns the hooked lanes by head.

        The lanes of a group follow the order of the lanes: key. Each hooked lane has the slot of
        its head.
        """
        return sorted(
            adapter.hooked, key=lambda h: (h.head, cast("Slot", adapter.slot_of(h)).lanes.index(h))
        )

    @staticmethod
    def _plan_lanes(
        hooked: Sequence[Hooked], spools: Sequence[LaneSpool]
    ) -> list[dict[str, object]]:
        """Returns the spool of each hooked lane, as the page shows it beside the plan."""
        return [
            {
                "key": s.key,
                "head": h.head,
                "colour": s.colour,
                "colour_name": ace2k_u1_plan.colour_name(s.colour),  # for the page's lists
                "material": s.material,
                "empty": s.empty,
            }
            for h, s in zip(hooked, spools)
        ]

    def _hooked_adapter(self, gcmd: GCodeCommand) -> Adapter:
        adapter = self.host.adapter
        if self.host.problems or adapter is None or not adapter.hooked:
            raise gcmd.error("ace2k_u1: the adapter is not hooked")
        return adapter

    def _no_print_on(self, gcmd: GCodeCommand) -> None:
        """Raises a G-code error while a print is printing or paused.

        The plan of that print is the one that its swaps and kept temperatures read. It is never
        replaced under them.
        """
        if self.host.u1.print_state() in PRINT_ON:
            raise gcmd.error("ace2k_u1: a print is on: plan the next file after it")

    def cmd_ACEK_PLAN(self, gcmd: GCodeCommand) -> None:
        """Plans a file and publishes the plan for the page.

        The command is ACEK_PLAN FILE=<name>. The plan has the tools of the file, a lane that is
        suggested for each (by its material and the closest colour), and the lane swaps for each
        head.

        The command is refused while a print is on, and the plan is kept. The new plan replaces
        the previous one only once it is read and suggested. A refused file leaves the previous
        plan as it was.
        """
        adapter = self._hooked_adapter(gcmd)
        self._no_print_on(gcmd)
        name, path = self.host.gcode_file(gcmd)
        # A file that was removed or became unreadable since the check above is a G-code error
        # as well.
        try:
            parsed = self.host.tracker.read_plan(path)
            mtime = os.path.getmtime(path)
        except (ace2k_u1_plan.PlanError, OSError, ValueError) as err:
            raise gcmd.error(f"ace2k_u1: {name}: {err}") from None
        hooked = self._lanes_in_order(adapter)
        lanes = self.host.spool_lanes.lane_spools(hooked)
        picks = ace2k_u1_plan.suggest(
            parsed.tools,
            lanes,
            self.host.swap_cfg.colour_max_distance,
            self.host.swap_cfg.colour_min_margin,
        )
        by_text = {self._units().lane_text(h.unit, h.lane): h for h in adapter.hooked}
        choice = {n: key for n, (key, _why) in enumerate(picks) if key is not None}
        heads = ace2k_u1_plan.swaps(parsed.order, choice, lambda key: by_text[key].head)
        self.host.tracker.plan_started = False
        self.host.tracker.plan_seen_on = False
        self.host.tracker.plan = Plan(
            file=name,
            path=path,
            mtime=mtime,
            tools=[self._plan_tool(t, key, why) for t, (key, why) in zip(parsed.tools, picks)],
            order=parsed.order,
            raw=parsed.raw,
            order_at=list(parsed.order_at),
            size=parsed.size,
            heads=heads,
            lanes=self._plan_lanes(hooked, lanes),
        )
        gcmd.respond_info(f"ace2k_u1: plan of {name}: {len(parsed.tools)} tools")

    @staticmethod
    def _plan_tool(t: Tool, key: str | None, why: str) -> PlanTool:
        """Returns a tool of the plan as the page shows it.

        It has the fields of the slicer, the lane that is suggested for it (its text, or None for
        none), and the reason.
        """
        return {
            "colour": t.colour,
            "material": t.material,
            "temp": t.temp,
            "first_temp": t.first_temp,
            "lane": key,
            "why": why,
        }

    def cmd_ACEK_PLAN_SET(self, gcmd: GCodeCommand) -> None:
        """Keeps the lane that the operator chose for each tool of the planned file, for its print.

        The command is ACEK_PLAN_SET FILE=<name> T0=<lane> T1=<lane> ... It is refused, and
        nothing is kept, in these cases.
        - The file is another file than the planned one.
        - The file changed since its plan.
        - A tool that the file prints has no lane. A tool that the file never prints needs none.
        - A lane is not hooked, or holds another material than its tool wants.
        - A print is on.
        """
        adapter = self._hooked_adapter(gcmd)
        self._no_print_on(gcmd)
        name, path = self.host.gcode_file(gcmd)
        plan = self._unchanged_plan(gcmd, name, path)
        units = self._units()
        by_text = {units.lane_text(h.unit, h.lane): h for h in adapter.hooked}
        # The spool of each lane is read once, for the material check and for the lanes of the
        # page.
        hooked = self._lanes_in_order(adapter)
        read = adapter.read_units(adapter.reactor.monotonic())
        spools = dict(zip(hooked, self.host.spool_lanes.lane_spools(hooked, read), strict=True))
        # The material check from the spools just read; a lane whose status is unread is not unset.
        unset = {
            h
            for h in hooked
            if self.host.material.defined_from(self.host.spool_lanes.lane_read(h, read), spools[h])
            is False
        }
        choice = self._read_choice(gcmd, plan, by_text, spools, unset)
        self.host.changed()  # from here the plan changes in place (the copies are built later)
        plan.choice = choice
        plan.set = True
        # This is the "Unload the filament at the end" option of the page. The U1 unloads the lane
        # that is in each used head. The adapter unloads the other lanes of a shared head that the
        # print used (_end_unload).
        plan.end_unload = bool(gcmd.get_int("END_UNLOAD", 0, minval=0, maxval=1))
        plan.heads = ace2k_u1_plan.swaps(
            plan.order,
            {n: units.lane_text(h.unit, h.lane) for n, h in choice.items()},
            lambda key: by_text[key].head,
        )
        # A spool that was edited since the plan shows its new colour.
        plan.lanes = self._plan_lanes(hooked, [spools[h] for h in hooked])
        # The line names each tool's lane and head, so the log keeps what the page chose.
        tools = ", ".join(
            f"T{n}={units.lane_text(h.unit, h.lane)} (head {h.head + 1})"
            for n, h in sorted(choice.items())
        )
        gcmd.respond_info(f"ace2k_u1: plan of {name} set: {tools}")

    def _unchanged_plan(self, gcmd: GCodeCommand, name: str, path: str) -> Plan:
        """Returns the plan of the file name (at path) for ACEK_PLAN_SET.

        Raises a G-code error when there is no plan of that file, when the file is gone, or when
        it changed since its plan.
        """
        plan = self.host.tracker.plan
        if plan is None or plan.file != name:
            raise gcmd.error(f"ace2k_u1: no plan of {name}: run ACEK_PLAN FILE={name} first")
        try:
            mtime = os.path.getmtime(path)
        except OSError as err:  # removed since the check above
            raise gcmd.error(f"ace2k_u1: {name}: {err}") from None
        if mtime != plan.mtime:
            raise gcmd.error(f"ace2k_u1: {name} changed since its plan: plan it again")
        return plan

    def _read_choice(
        self,
        gcmd: GCodeCommand,
        plan: Plan,
        by_text: dict[str, Hooked],
        spools: dict[Hooked, LaneSpool],
        unset: set[Hooked],
    ) -> dict[int, Hooked]:
        """Reads the T<n>= parameters of ACEK_PLAN_SET as {tool: Hooked}.

        Raises a G-code error in these cases.
        - A tool that the file prints has no lane.
        - A lane is not hooked. by_text holds the hooked lanes by their text.
        - The bay of a lane is empty.
        - A lane has no material set (unset: no tag read and no Edit spool record).
        - A lane holds another known material than its tool wants. spools holds the spool of each
          lane.
        """
        # Only the tools that the file prints need a lane. A lane that is given for another tool
        # is checked all the same.
        used = set(self.host.wraps.used_tools(plan.order, len(plan.tools)))
        choice: dict[int, Hooked] = {}
        for n in range(len(plan.tools)):
            text = (gcmd.get(f"T{n}", "") or "").strip().lower()
            if not text:
                if n not in used:
                    continue
                raise gcmd.error(f"ace2k_u1: T{n} has no lane")
            h = by_text.get(text)
            if h is None:
                raise gcmd.error(f"ace2k_u1: T{n}: no hooked lane {text}")
            if spools[h].empty:
                raise gcmd.error(f"ace2k_u1: T{n}: {text} has no spool in its bay")
            if h in unset:
                raise gcmd.error(
                    f"ace2k_u1: T{n}: {text} has no material set: set it with Edit spool"
                )
            # A lane of another known material is refused. A lane whose material is unknown is not.
            want = plan.tools[n].get("material")
            have = spools[h].material
            if ace2k_u1_plan.other_material(want, have):
                raise gcmd.error(f"ace2k_u1: T{n}: {text} holds {have}, the file wants {want}")
            choice[n] = h
        return choice
