"""The temperature check: a temperature that does not fit the material of the lane in the head.

The range of each material comes from temp_ranges (ace2k_u1_config.read_temp_ranges). The check
only warns, with a console line that the page shows among its events. It never refuses, and the
head heats as it was asked. It runs in these places:

- At the start of a print, for each tool that the file prints, against the lane that the tool
  prints with: the lane that the plan chose, or else the lane in the head that the tool table of
  the U1 sends the tool to (Wraps.wrap_temp_checks).
- For M104, M109 and SET_HEATER_TEMPERATURE (what Fluidd sends) outside a print, against the
  lane in the head (Wraps.wrap_temp_checks).
- For the temperature control of the U1's screen, outside a print, the same way. That control is
  an endpoint of Klipper's webhooks, not a G-code (wrap_endpoint).

In a print, the temperatures of the file were checked at its start, so its M104 calls are not
checked again. The calls with A0, and every call while a flow of the U1 runs (its load, unload or
manual feed heat the head for the head's setting), are the U1's own or the adapter's, and are not
checked. A temperature under PRINTING_FLOOR_C (a standby, a warm-up) never warns, and neither does
a material that is not known or not in the table.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_common import Hooked
    from .ace2k_u1_plan import Tool
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_printer_types import GCodeCommand
    from .ace2k_u1_spools import LaneSpools

try:
    from .ace2k_u1_adapter import PRINT_ON
    from .ace2k_u1_config import material_param
    from .ace2k_u1_tracker import PRINTING_FLOOR_C
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_adapter import PRINT_ON  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_config import material_param  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_tracker import PRINTING_FLOOR_C  # type: ignore[import-not-found, no-redef]

# The temperature control of the U1's screen (Moonraker's printer.control.extruder_temp). It takes
# S (°C), T (the head, or the tool with A other than 0) and A, as M104 does.
TEMP_ENDPOINT = "control/extruder_temp"


class WebRequest(Protocol):
    """A request of Klipper's webhooks, as far as the check reads it."""

    def get_float(self, name: str, default: float | None = ...) -> float | None: ...
    def get_int(self, name: str, default: int | None = ...) -> int | None: ...


class TempsHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the temperature check reads it."""

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def printer(self) -> Printer: ...
    @property
    def u1(self) -> U1Printer: ...
    @property
    def spool_lanes(self) -> LaneSpools: ...
    @property
    def temp_ranges(self) -> dict[str, tuple[float, float]]: ...


class TempCheck:
    """Warns about a temperature outside the range of the material of a lane."""

    def __init__(self, host: TempsHost) -> None:
        self.host = host

    def line(self, head: int, temp: float, h: Hooked | None, tool: int | None = None) -> str | None:
        """Return the warning for temp on head, printed with lane h (T<tool> of a file), or None.

        None when there is no lane, the temperature is under PRINTING_FLOOR_C, the material is not
        known or not in the table, or the temperature is within its range.
        """
        if h is None or temp < PRINTING_FLOOR_C:
            return None
        found = self._range(self.host.spool_lanes.material(h))
        if found is None or found[1][0] <= temp <= found[1][1]:
            return None
        material, (low, high) = found
        where = "above" if temp > high else "below"
        lane = self._adapter().lane_text(h) + ("" if tool is None else f", T{tool}")
        return (
            f"ace2k_u1: head {head + 1}: {temp:g} °C is {where} {material}'s {low:g}–{high:g} °C "
            f"(lane {lane})"
        )

    def _range(self, material: str) -> tuple[str, tuple[float, float]] | None:
        """Return (key, range) of a material in the table, or None.

        The material is looked up whole (PLA SILK as PLASILK), then by its first word (PLA), so a
        subtype that the table does not list takes the range of its base material.
        """
        ranges = self.host.temp_ranges
        for key in (material_param(material), material_param(material.split(" ", 1)[0])):
            if key and key in ranges:
                return key, ranges[key]
        return None

    def say(self, lines: Iterable[str | None]) -> None:
        """Say each warning once, in order."""
        said: set[str] = set()
        for text in lines:
            if text is not None and text not in said:
                said.add(text)
                self._adapter().say(text)

    def check_command(self, gcmd: GCodeCommand) -> None:
        """Check an M104 or M109. It never raises."""
        self._guarded(
            lambda: (
                None
                if gcmd.get_int("A", 1) == 0
                else self._check_set(gcmd.get_float("S", 0.0), gcmd.get_int("T", None), 1)
            )
        )

    def check_heater(self, gcmd: GCodeCommand) -> None:
        """Check a SET_HEATER_TEMPERATURE of a head's heater. It never raises."""

        def check() -> None:
            name = (gcmd.get("HEATER", "") or "").strip()
            head = self._adapter().head_of_extruder(name)
            if head is not None:
                self._check_set(gcmd.get_float("TARGET", 0.0), head, 0)

        self._guarded(check)

    def check_request(self, request: WebRequest) -> None:
        """Check a request of the temperature control. It never raises."""
        self._guarded(
            lambda: self._check_set(
                request.get_float("S", 0.0) or 0.0,
                request.get_int("T", None),
                request.get_int("A", 1),
            )
        )

    def check_start(self, tools: list[Tool], used: Iterable[int], lane_of: LaneOf) -> None:
        """Check the temperatures of the tools that a starting file prints. It never raises.

        lane_of(n) gives (head, lane) for T<n>: the head that it prints on, and its lane there or
        None. Each tool is checked at its temperature and at that of its first layer. A T of the
        file with no tool in its config block is left out.
        """

        def lines() -> list[str | None]:
            out: list[str | None] = []
            for n in used:
                if n >= len(tools):
                    continue
                head, h = lane_of(n)
                values = (tools[n].temp, tools[n].first_temp)
                out.extend(self.line(head, float(v), h, n) for v in values if v)
            return out

        self._guarded(lambda: self.say(lines()))

    def wrap_endpoint(self) -> bool:
        """Wrap the temperature control of the screen with the check.

        Klipper's webhooks has no call to replace an endpoint, so its table is changed in place.
        Returns False, and wraps nothing, when the printer has no such endpoint.
        """
        webhooks = self.host.printer.lookup_object("webhooks", None)
        table = getattr(webhooks, "_endpoints", None)
        old = table.get(TEMP_ENDPOINT) if isinstance(table, dict) else None
        if old is None:
            return False

        def control(request: WebRequest) -> object:
            self.check_request(request)
            return old(request)

        table[TEMP_ENDPOINT] = control  # type: ignore[index]  # a dict, checked above
        return True

    def _check_set(self, temp: float, index: int | None, mapped: int | None) -> None:
        """Check a temperature set outside a print on the head that index and mapped name."""
        if temp < PRINTING_FLOOR_C or self.host.u1.print_state() in PRINT_ON or self._u1_flow():
            return
        head = self._head(index, mapped)
        if head is None:
            return
        slot = self._adapter().slot_of_head(head)
        self.say([self.line(head, temp, None if slot is None else slot.current)])

    def _u1_flow(self) -> bool:
        """True while a flow of the U1 (a load, an unload, a manual feed) holds a channel."""
        return any(getattr(ff, "channel_active", None) is not None for ff in self._adapter().feeds)

    def _head(self, index: int | None, mapped: int | None) -> int | None:
        """Return the head of a temperature call, as the U1 picks its heater, or None.

        With no index, the active head. With mapped other than 0, the index is a tool that the
        tool table of the U1 sends to a head. Otherwise it is the head itself. None when the U1
        would refuse the call (a negative index, a tool past its table) or the active head cannot
        be told.
        """
        if index is None:
            return self._adapter().active_head(self.host.u1)  # None when unread
        if index < 0:
            return None
        if mapped == 0:
            return index
        try:
            table = self.host.u1.map_table()
        except Exception:  # no tool table: the U1 takes the index as it is
            return index
        return table[index] if index < len(table) else None

    def _adapter(self) -> Adapter:
        return self.host.adapter  # type: ignore[return-value]  # the check is wired at the hook

    @staticmethod
    def _guarded(check: Callable[[], object]) -> None:
        """Run a check. A failure is logged, and the temperature goes on as asked."""
        try:
            check()
        except Exception:
            logging.exception("ace2k_u1: the temperature check")


LaneOf = Callable[[int], "tuple[int, Hooked | None]"]
