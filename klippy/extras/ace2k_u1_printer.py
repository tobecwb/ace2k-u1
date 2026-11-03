"""The private state of the U1, in one place.

This is the only module that reads or writes that state. The state is the print state and the
pause flags of the U1, the position of the extruder of a head, its print_task_config and
filament_parameters (with their tables), the variables of the resume macro and its idle-position
command.

The names of those facts are in ace2k_u1_hooks (PRINT_TASK_KEYS and the lists after it).
tests/test_structure.py checks them against the tree of the U1. So if an update of the U1 moves
one, that test fails, not the printer.

Some public reads of the objects of the U1 are not here. They live where they are used. These
are its virtual_sdcard, its extruder list and the print_stats object that the Adapter is given
at the hook.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer, Reactor
    from .ace2k_u1_printer_types import (
        Extruder,
        FilamentParameters,
        PrintStats,
        PrintTaskConfig,
        Status,
    )

try:
    from . import ace2k_u1_hooks as hooks_module
    from .ace2k_u1_common import finite
except ImportError:  # the host tests put klippy/extras on the path
    import ace2k_u1_hooks as hooks_module  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import finite  # type: ignore[import-not-found, no-redef]

# A spool as the filament table of the U1 is asked for it: (vendor, type, subtype, rgba). The type
# is None when the table does not know it (ace2k_u1_spools.LaneSpools.own_spool).
Spool = tuple[str, "str | None", str, str]

TAG_KEYS = ("vendor", "type", "subtype")  # the three names of the filament setting, in U1 order


class U1Printer:
    """Reads the private state of the U1.

    printer and reactor are functions that return the Klipper printer and reactor objects. They
    are called when used, so a printer given to the module afterwards is the one that is read.
    """

    def __init__(self, printer: Callable[[], Printer], reactor: Callable[[], Reactor]) -> None:
        self._printer = printer
        self._reactor = reactor
        self._settings_unread = False  # a read of the heads' settings failed, and was logged

    def _lookup(self, name: str) -> object:
        """Returns the printer object called name, or None when it is not there.

        The caller says its type. The objects of the U1 are described in ace2k_u1_printer_types.
        """
        return self._printer().lookup_object(name, None)

    def _print_stats(self) -> PrintStats | None:
        return cast("PrintStats | None", self._lookup("print_stats"))

    def _task_config(self) -> PrintTaskConfig | None:
        return cast("PrintTaskConfig | None", self._lookup("print_task_config"))

    def _now(self) -> float:
        return self._reactor().monotonic()

    # --- the print and its pause -----------------------------------------------------------

    def print_state(self) -> str | None:
        """Returns the state of print_stats now, or None when it is not there.

        This is a fresh lookup, for the checks of this module (its commands and its watch).
        print_state of the Adapter reads the object that it was given at the hook, for the pause
        and tag code.
        """
        state: str | None = getattr(self._print_stats(), "state", None)
        return state

    def paused(self) -> bool:
        return self.print_state() == "paused"

    def u1_paused(self) -> bool:
        """Tells whether the print is paused.

        It is paused when print_stats says so, or when pause_resume of the U1 says so. A pause
        from an SD line changes print_stats only after the SD loop has stopped.
        """
        pr = self._lookup("pause_resume")
        return bool(getattr(pr, hooks_module.PAUSE_FLAGS["paused"], False)) or self.paused()

    def pause_taken(self) -> bool:
        """Tells whether the print is paused, or pause_resume of the U1 is pausing it."""
        pr = self._lookup("pause_resume")
        sent = getattr(pr, hooks_module.PAUSE_FLAGS["sent"], False)
        return self.u1_paused() or bool(sent)

    def pause_refused(self) -> bool:
        """Tells whether the PAUSE of the U1 did not take.

        That is the case when pause_resume is there and is neither paused nor pausing.
        """
        pr = self._lookup("pause_resume")
        flags = hooks_module.PAUSE_FLAGS
        return pr is not None and not (
            getattr(pr, flags["paused"], False) or getattr(pr, flags["sent"], False)
        )

    def current_layer(self) -> int | None:
        """Returns the current layer of print_stats (SET_PRINT_STATS_INFO), or None if unknown."""
        stats = self._print_stats()
        outer, inner = hooks_module.PRINT_STATS_LAYER
        try:
            layer: int | None = cast("PrintStats", stats).get_status(self._now())[outer][inner]
        except Exception:
            return None
        return layer

    # --- the toolhead and the extruders ----------------------------------------------------

    def active_extruder_name(self) -> str | None:
        """Returns the section name of the toolhead's active extruder, or None when unreadable."""
        try:
            toolhead = self._printer().lookup_object("toolhead")
            return str(toolhead.get_extruder().get_name())
        except Exception:
            logging.debug("ace2k_u1: the active extruder", exc_info=True)
            return None

    def homed_axes(self) -> str:
        """Returns the homed axes of the toolhead, as text such as "xyz".

        When the axes cannot be read it returns "xyz", as the code did before it read them.
        """
        try:
            toolhead = self._printer().lookup_object("toolhead")
            return str(toolhead.get_status(self._now())[hooks_module.TOOLHEAD_HOMED]).lower()
        except Exception:
            logging.exception("ace2k_u1: the toolhead's homed axes")
            return "xyz"

    def extruder(self, name: str | None) -> Extruder | None:
        """Returns the extruder object called name, or None when it is not known."""
        return cast("Extruder | None", self._lookup(name)) if name else None

    @staticmethod
    def e_pos(extruder: Extruder | None) -> float | None:
        """Returns printing_e_pos of the extruder, or None if it cannot be read or is not finite."""
        value = getattr(extruder, hooks_module.EXTRUDER_ATTRIBUTES["e_pos"], None)
        return float(value) if finite(value) else None

    @staticmethod
    def set_e_pos(extruder: Extruder | None, value: float | None) -> None:
        """Sets printing_e_pos of the extruder. The flush and the resume of the U1 retract by it.

        None (not read) leaves it as it is.
        """
        name = hooks_module.EXTRUDER_ATTRIBUTES["e_pos"]
        if extruder is not None and value is not None and hasattr(extruder, name):
            setattr(extruder, name, value)

    @staticmethod
    def nozzle(extruder: Extruder | None) -> float | None:
        """Returns the nozzle diameter of the extruder, or None when it does not say."""
        diameter: float | None = getattr(extruder, hooks_module.EXTRUDER_ATTRIBUTES["nozzle"], None)
        return diameter

    @staticmethod
    def nozzle_volume(extruder: Extruder | None) -> str:
        volume: str = getattr(extruder, hooks_module.EXTRUDER_ATTRIBUTES["volume"], "standard")
        return volume

    # --- G-code ----------------------------------------------------------------------------

    def script(self, script: str, timer: bool = False) -> None:
        """Runs G-code from the command that is running now, under the lock it holds.

        With timer set, it runs from a reactor timer, outside the lock (run_script takes it).
        """
        gcode = self._printer().lookup_object("gcode")
        if timer:
            gcode.run_script(script)
        else:
            gcode.run_script_from_command(script)

    def idle_exit(self, timer: bool = False) -> None:
        """Runs the idle exit of the U1 for the active extruder."""
        self.script(hooks_module.IDLE_EXIT, timer)

    @staticmethod
    def resume_variables(head: int) -> tuple[str, str, str]:
        """Returns the names of the variables of the resume macro for head.

        They are the head last active, the last temperature, and the temperature of the head
        itself.
        """
        index, last_temp, head_temp = hooks_module.INNER_RESUME_VARIABLES
        return index, last_temp, head_temp % head

    def set_inner_resume(self, name: str, value: str, timer: bool = False) -> None:
        """Sets one variable of the resume macro of the U1."""
        macro = hooks_module.INNER_RESUME
        self.script(f"SET_GCODE_VARIABLE MACRO={macro} VARIABLE={name} VALUE={value}", timer)

    # --- print_task_config and filament_parameters -----------------------------------------

    def _task_status(self) -> Status:
        return cast("PrintTaskConfig", self._task_config()).get_status(self._now())

    def map_table(self) -> list[int]:
        """Returns the tool -> head table of print_task_config, as a list."""
        table: list[int] = list(self._task_status()[hooks_module.PRINT_TASK_KEYS["map_table"]])
        return table

    def filament_exist(self) -> list[bool]:
        """Returns the record that the U1 itself keeps of the filament in each head."""
        exist: list[bool] = self._task_status()[hooks_module.PRINT_TASK_KEYS["filament_exist"]]
        return exist

    def head_setting(self, head: int) -> tuple[str, object] | None:
        """Returns (type, rgba) of the filament setting of a head, as the U1 stores them.

        Returns None when print_task_config is not there or the setting cannot be read.
        """
        settings = self.head_settings()
        if settings is None or head >= len(settings):
            return None
        return settings[head]

    def head_settings(self) -> list[tuple[str, object]] | None:
        """Returns (type, rgba) of the filament setting of every head, read once.

        Returns None when print_task_config is not there or cannot be read. A failed read is
        logged once, until a read works again.
        """
        if self._task_config() is None:
            return None
        keys = hooks_module.PRINT_TASK_KEYS
        try:
            status = self._task_status()
            kinds, colours = status[keys["type"]], status[keys["rgba"]]
            settings = [(str(k or "").strip(), c) for k, c in zip(kinds, colours)]
        except Exception:
            if not self._settings_unread:
                logging.exception("ace2k_u1: the heads' settings unreadable")
            self._settings_unread = True
            return None
        self._settings_unread = False
        return settings

    @staticmethod
    def filament_tables(fparams: FilamentParameters) -> list[dict[str, object]]:
        """Returns the tables of filament_parameters that are keyed by vendor, type and subtype.

        The keys are <vendor>_<type>_<subtype>_<key name>. The tables are its status and the
        private dicts that hold them.
        """
        tables: list[dict[str, object]] = []
        try:
            tables.append(fparams.get_status(0))
        except Exception:
            logging.exception("ace2k_u1: filament_parameters status")
        tables.extend(v for v in vars(fparams).values() if isinstance(v, dict))
        return tables

    def u1_filament(self, extruder: Extruder | None, head: int, method: str) -> object:
        """Asks filament_parameters.<method> about the filament setting of a head.

        It passes the vendor, type, subtype, nozzle diameter and nozzle volume type, as the flows
        of the U1 itself do. Returns None when it cannot be asked.
        """
        fparams = self._lookup("filament_parameters")
        ptc = self._task_config()
        if fparams is None or ptc is None or extruder is None or not hasattr(fparams, method):
            return None
        try:
            keys = hooks_module.PRINT_TASK_KEYS
            status = ptc.get_status(self._now())
            setting = [status[keys[k]][head] for k in TAG_KEYS]
            # No default for the diameter: an extruder that does not say it is unreadable.
            diameter = getattr(extruder, hooks_module.EXTRUDER_ATTRIBUTES["nozzle"])
            answer: object = getattr(fparams, method)(
                *setting, diameter, self.nozzle_volume(extruder)
            )
            return answer
        except Exception:
            logging.exception("ace2k_u1: head %d: filament_parameters %s", head + 1, method)
            return None

    def soft(self, extruder: Extruder | None, head: int) -> int:
        """Returns 1 when the filament table of the U1 calls the setting of the head soft, else 0.

        An unknown setting gives 0.
        """
        method = hooks_module.FILAMENT_PARAMETERS["soft"]
        return 1 if self.u1_filament(extruder, head, method) is True else 0

    def unload_temp(self, extruder: Extruder | None, head: int) -> object:
        method = hooks_module.FILAMENT_PARAMETERS["unload_temp"]
        return self.u1_filament(extruder, head, method)

    def load_temp(
        self, spool: Spool | None, extruder: Extruder | None, default_nozzle: float
    ) -> object:
        """Returns the load temperature that the U1 gives for a spool.

        The spool is (vendor, type, subtype, rgba), used in the nozzle of the extruder. Returns
        None when there is no filament table or the spool has no type. Raises when the table
        cannot be asked.
        """
        fparams = self._lookup("filament_parameters")
        if fparams is None or spool is None or spool[1] is None:
            return None
        vendor, kind, subtype, _rgba = spool
        nozzle = self.nozzle(extruder) or default_nozzle
        method = hooks_module.FILAMENT_PARAMETERS["load_temp"]
        temp: object = getattr(fparams, method)(
            vendor, kind, subtype, nozzle, self.nozzle_volume(extruder)
        )
        return temp
