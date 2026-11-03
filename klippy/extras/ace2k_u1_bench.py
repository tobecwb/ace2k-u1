"""Holds the bench commands for a head that several lanes feed, used outside a print.

ACEK_HEAD_PROBE runs the probe of a print start on the head alone, or pushes a lane to its empty
sensor. ACEK_FREE_CHECK runs the free check of a swap on the lane of the head alone. The module
also says why each command is refused. ace2k_u1.py registers the commands and calls the Bench.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_config import SwapConfig
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_printer_types import Coordinator, GCodeCommand, Unit
    from .ace2k_u1_probe import HeadProbe
    from .ace2k_u1_swap import Swapper
    from .ace2k_u1_wraps import Wraps

try:
    from . import ace2k_u1_probe
    from .ace2k_u1_adapter import PRINT_ON
    from .ace2k_u1_common import MOVE_MODES, PAUSE_WAIT_S
    from .ace2k_u1_config import LANES, SWAP_LENGTH_MAX_MM, SWAP_SPEED_MAX_MM_S
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_probe  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_adapter import PRINT_ON  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        MOVE_MODES,
        PAUSE_WAIT_S,
    )
    from ace2k_u1_config import (  # type: ignore[import-not-found, no-redef]
        LANES,
        SWAP_LENGTH_MAX_MM,
        SWAP_SPEED_MAX_MM_S,
    )


class BenchHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the bench commands read it.

    It gives the adapter and the probe. They are built at the hook, and the probe exists only
    with a head that several lanes feed. It tells why nothing was hooked. It also gives the U1,
    the wraps, the swap config and the Swapper, and the coordinator of ace2k.
    """

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def probe(self) -> HeadProbe | None: ...
    @property
    def problems(self) -> list[str] | None: ...
    @property
    def u1(self) -> U1Printer: ...
    @property
    def wraps(self) -> Wraps: ...
    @property
    def swap_cfg(self) -> SwapConfig: ...
    @property
    def swapper(self) -> Swapper: ...
    def units(self) -> Coordinator | None: ...


class Bench:
    """Runs the bench commands.

    host is the ACE2kU1. Its adapter and probe are built at the hook. Its wraps, swap config and
    pending swap are read when used.
    """

    def __init__(self, host: BenchHost) -> None:
        self.host = host

    def _adapter(self) -> Adapter:
        """Returns the adapter of the host.

        It is built and hooked once a bench command gets past its refusals.
        """
        return cast("Adapter", self.host.adapter)

    def _probe(self) -> HeadProbe:
        """Returns the probe of the host.

        It is built with the head that several lanes feed. A bench command runs on that head.
        """
        return cast("HeadProbe", self.host.probe)

    def cmd_ACEK_FREE_CHECK(self, gcmd: GCodeCommand) -> None:
        """Runs the free check of a swap on a lane, outside a print, for the bench.

        The command is ACEK_FREE_CHECK [UNIT=<name>] LANE=<1-4> [LENGTH=<mm>] [SPEED=<mm/s>]. The
        lane must be in a head that several lanes feed (HeadProbe.check_alone). It rolls back up to
        LENGTH (default probe_mm) at SPEED (default probe_speed, within the speed bounds of the
        unit). Klipper watches the plunger. A line says free, and how far, or held, and how far
        it moved until it was taut, and then the filament is fed back.

        If the command is refused, nothing moves (see _free_check_refusal). Any other end of the
        check is raised as a G-code error.
        """
        lane = gcmd.get_int("LANE", minval=1, maxval=LANES)
        units = self.host.units()
        unit = None if units is None else units.unit_for(gcmd)  # its error is raised as it is
        lane_text = lane if unit is None or units is None else units.lane_text(unit, lane - 1)
        found, why = self._free_check_refusal(lane, unit)
        if why is not None:
            raise gcmd.error(f"ace2k_u1: lane {lane_text} free check refused: {why}")
        h = cast("Hooked", found)  # without a refusal, the lane is hooked
        adapter = self._adapter()
        cfg = self._probe().cfg
        length_mm = gcmd.get_float("LENGTH", cfg.probe_mm, minval=10.0, maxval=SWAP_LENGTH_MAX_MM)
        speed_mm_s = adapter.within_speed(
            h, gcmd.get_float("SPEED", cfg.probe_speed, minval=5.0, maxval=SWAP_SPEED_MAX_MM_S)
        )
        back_speed_mm_s = adapter.within_speed(h, cfg.probe_speed)
        what = (
            f"ace2k_u1: {adapter.lane_text(h)} free check, {length_mm:g} mm at {speed_mm_s:g} mm/s"
        )
        armed = h.armed
        try:
            verdict, moved_mm, fed = self._probe().check_alone(
                cast("Slot", adapter.slot_of(h)), h, length_mm, speed_mm_s, back_speed_mm_s
            )
        except ace2k_u1_probe.ProbeError as err:
            raise gcmd.error(f"ace2k_u1: {err}") from None
        finally:
            if armed:  # the follow that the check stopped is armed again (a refusal is a notice)
                adapter.follow.arm(h, PAUSE_WAIT_S)
        gcmd.respond_info(f"{what}: {self._free_check_found(h, verdict, moved_mm, fed)}")

    @staticmethod
    def _free_check_found(h: Hooked, verdict: str, moved_mm: float, fed: bool) -> str:
        """Returns what ACEK_FREE_CHECK found on h.

        It is the text of the console line that follows the text of the lane.
        """
        if not fed:
            found = "free" if verdict == "free" else f"held by head {h.head + 1}, taut"
            return f"{found} (no distance reported; not fed back)"
        found = (
            f"free, {moved_mm:.1f} mm rolled back"
            if verdict == "free"
            else f"held by head {h.head + 1}, taut after {moved_mm:.1f} mm"
        )
        return f"{found}; fed back"

    def cmd_ACEK_HEAD_PROBE(self, gcmd: GCodeCommand) -> None:
        """Probes a head that several lanes feed, outside a print, for the bench.

        The command is ACEK_HEAD_PROBE HEAD=<1-4> [UNIT=<name>] [LANE=<1-4>]. There are two cases.
        - The sensor of the head is set. The probe that a print start runs on the head runs
          (HeadProbe.which_lane, every move at probe_speed). The lane that it finds is named the
          lane of the head, as the start names it (HeadWatch.hand_head), and it is armed.
        - The sensor is empty. The other lanes of the head are stopped. LANE is handed the head
          and pushed to the sensor like the push of a swap (HeadProbe.push_in at swap_speed, and
          when the push is blocked, the lane in the way is looked for). Then LANE is armed. A push
          that fails gives the head back to the lane that it had.

        If the command is refused, nothing moves (see _head_probe_refusal). A probe or push that
        fails is raised as a G-code error.
        """
        head = gcmd.get_int("HEAD", minval=1, maxval=LANES) - 1
        given = gcmd.get("LANE", None) is not None
        lane = gcmd.get_int("LANE", minval=1, maxval=LANES) if given else None
        found, h, why = self._head_probe_refusal(gcmd, head, lane)
        if why is not None:
            raise gcmd.error(f"ace2k_u1: head {head + 1} probe refused: {why}")
        slot = cast("Slot", found)  # without a refusal, this is a head that several lanes feed
        if h is None:
            self._probe_head(gcmd, slot)
        else:
            self._push_head(gcmd, slot, h)

    def _probe_head(self, gcmd: GCodeCommand, slot: Slot) -> None:
        """Runs ACEK_HEAD_PROBE on a head whose sensor is set.

        The probe names the lane in the head, and that lane follows. A probe that fails arms the
        lane that the head had before (rearm).
        """
        adapter = self._adapter()
        head = slot.head
        before = slot.current
        was_armed = before is not None and before.armed
        with self.host.wraps.as_gcode_error(
            gcmd, head, lambda: self.rearm(slot, before, was_armed)
        ):
            found = self._probe().which_lane(slot)
            adapter.head_watch.hand_head(slot, found)
        adapter.follow.arm(found, PAUSE_WAIT_S)  # the probe stopped every lane of the head
        what = "was the head's already" if found is before else "named the head's"
        gcmd.respond_info(
            f"ace2k_u1: head {head + 1}: {adapter.lane_text(found)} {what}, following"
        )

    def _push_head(self, gcmd: GCodeCommand, slot: Slot, h: Hooked) -> None:
        """Runs ACEK_HEAD_PROBE on a head whose sensor is empty.

        Lane h is handed the head, pushed to its sensor, and then armed. A push that fails gives
        the head back (_give_head_back).
        """
        adapter = self._adapter()
        head = slot.head
        before = slot.current
        was_armed = before is not None and before.armed
        with self.host.wraps.as_gcode_error(
            gcmd, head, lambda: self._give_head_back(slot, h, before, was_armed)
        ):
            for x in slot.lanes:
                if x is not h:
                    adapter.stop(x, "the head probe's push")
            adapter.head_watch.hand_head(slot, h)
            error = self._probe().push_in(slot, h, speed=self.host.swap_cfg.swap_speed)
        if error is not None:
            self._give_head_back(slot, h, before, was_armed)
            raise gcmd.error(
                f"ace2k_u1: head {head + 1}: {adapter.lane_text(h)} did not reach the head "
                f"({error})"
            )
        adapter.follow.arm(h, PAUSE_WAIT_S)
        gcmd.respond_info(
            f"ace2k_u1: head {head + 1}: {adapter.lane_text(h)} pushed to its sensor, following"
        )

    def _give_head_back(
        self, slot: Slot, h: Hooked, before: Hooked | None, was_armed: bool
    ) -> None:
        """Gives the head back after the push of h in ACEK_HEAD_PROBE failed.

        The head is named for the lane that it had before. That lane follows again if it followed
        before (rearm). The console says so. If there was no lane before, h stays the lane of the
        head (it is also the choice of the core), with its filament short of the sensor, and the
        console says so. A refusal of the hand back is said, and it is never raised over the
        error of the push.
        """
        adapter = self._adapter()
        n = slot.head + 1
        if before is None or before is h:
            if slot.current is h:
                adapter.say(
                    f"ace2k_u1: head {n}: the push failed; {adapter.lane_text(h)} stays the head's "
                    "lane"
                )
            return
        if slot.current is h:
            try:
                self._adapter().head_watch.hand_head(slot, before)
            except adapter.refusals() as err:
                adapter.say(
                    f"ace2k_u1: head {n}: not given back to {adapter.lane_text(before)}: {err}"
                )
                return
            adapter.say(
                f"ace2k_u1: head {n}: the push failed, the head is back to "
                f"{adapter.lane_text(before)}"
            )
        self.rearm(slot, before, was_armed)

    def rearm(self, slot: Slot, before: Hooked | None, was_armed: bool) -> None:
        """Arms again the lane that the head had before a bench command that failed.

        This happens when that lane followed and is still the lane of the head. The stops of the
        command left it idle. A refusal is only a notice.
        """
        if was_armed and before is not None and slot.current is before:
            self._adapter().follow.arm(before, PAUSE_WAIT_S)

    def bench_refusal(self, slot: Slot | None) -> str | None:
        """Returns why a bench command may not move a lane of the head of slot now, or None.

        The commands are ACEK_HEAD_PROBE, ACEK_FREE_CHECK and ACEK_SPLITTER_MEASURE. These are the
        reasons.
        - No head fed by several lanes is hooked.
        - Slot is not such a head.
        - A print is printing or paused.
        - A swap is pending.
        - An eject is running on one of its lanes.
        """
        why = self._setup_refusal(slot)
        if why is not None or slot is None:  # without a slot, the first half refused it
            return why
        return self._busy_refusal(slot)

    def _setup_refusal(self, slot: Slot | None) -> str | None:
        """Checks the first half of bench_refusal.

        It refuses when no head fed by several lanes is hooked, or when slot is not such a head.
        """
        adapter = self.host.adapter
        if self.host.problems or adapter is None or not adapter.hooked or self.host.probe is None:
            return "no head fed by several lanes is hooked"
        if slot is None or not slot.shared:
            return "its head is fed by one lane alone"
        return None

    def _busy_refusal(self, slot: Slot) -> str | None:
        """Checks the second half of bench_refusal.

        It refuses when a print is on, a swap is pending or an eject runs on a lane.
        """
        if self.host.u1.print_state() in PRINT_ON:
            return "a print is printing or paused"
        if self.host.swapper.swap_pending:
            return "a swap is pending"
        if any(self._adapter().ejector.eject_running(x) for x in slot.lanes):
            return "an eject is running on one of its lanes"
        return None

    def sensor_refusal(self, slot: Slot) -> tuple[bool | None, str | None]:
        """Returns (whether the sensor of the head is set, None).

        Returns (None, the reason) when the sensor cannot be read.
        """
        try:
            return self._adapter().head_set(slot.lanes[0]), None
        except Exception:
            logging.exception(
                "ace2k_u1: head %d's sensor unread for a bench command", slot.head + 1
            )
            return None, f"head {slot.head + 1}'s sensor cannot be read"

    def _empty_bay(self, h: Hooked) -> str | None:
        """Returns why the bay of a lane refuses a bench move, or None.

        The bay refuses when it is empty, or when its state cannot be read.
        """
        insert, read = self.bay_insert(h)
        if not read:
            return "the lane's state cannot be read"
        return "its bay is empty" if insert is False else None

    def bay_insert(self, h: Hooked) -> tuple[bool | None, bool]:
        """Returns (the bay's insert reading of h, whether it was read), for the bench commands.

        The reading is the unit's lane state (True: filament in the bay, False: empty, None: not
        known). A state that cannot be read is logged and returns (None, False).
        """
        adapter = self._adapter()
        try:
            insert = adapter.lane_status(h).get("insert")
            return (insert if isinstance(insert, bool) else None), True
        except Exception:
            logging.exception(
                "ace2k_u1: %s's state unread for a bench command", adapter.lane_text(h)
            )
            return None, False

    def _head_probe_refusal(
        self, gcmd: GCodeCommand, head: int, lane: int | None
    ) -> tuple[Slot | None, Hooked | None, str | None]:
        """Tells whether ACEK_HEAD_PROBE may run on head.

        Returns (slot, the lane to push or None, None) when it may. Otherwise it returns (slot,
        None, the reason). These are the reasons.
        - The reasons of bench_refusal.
        - One of the lanes of the head is in a move (moving_refusal).
        - The sensor of the head cannot be read.
        - The sensor is set, and LANE is given or the head is not cold (HeadProbe.is_cold).
        - The sensor is empty, and there is no LANE, LANE is not one of the lanes of the head, or
          its bay is empty.
        A UNIT= that is given is checked the way the commands of ace2k check it, whether LANE is
        given or not.
        """
        adapter = self.host.adapter
        slot = adapter.slot_of_head(head) if adapter is not None else None
        why = self.bench_refusal(slot)
        if why is not None or slot is None:  # without a slot, it was refused above
            return slot, None, why
        why = self.moving_refusal(slot)
        if why is not None:
            return slot, None, why
        unit = self._probe_unit(gcmd, lane)
        head_set, why = self.sensor_refusal(slot)
        if why is not None:
            return slot, None, why
        if head_set:
            return slot, None, self._set_head_refusal(slot, lane)
        h, why = self._empty_head_lane(slot, lane, unit)
        return slot, h, why

    def moving_refusal(self, slot: Slot) -> str | None:
        """Returns why ACEK_HEAD_PROBE waits for a lane of the head of slot, or None.

        The probe moves every lane of the head, so it refuses while one of them is in a move: a
        feed, a rollback, a load, an unload or an assist (MOVE_MODES). A lane that follows counts
        as at rest: the probe stops its follow itself, as at a print start. The web page says
        the same words.
        """
        adapter = self._adapter()
        if any(adapter.motion(x) in MOVE_MODES for x in slot.lanes):
            return "one of its lanes is moving: wait for it to end"
        return None

    def _probe_unit(self, gcmd: GCodeCommand, lane: int | None) -> Unit | None:
        """Returns the unit that UNIT= of ACEK_HEAD_PROBE names.

        It is read as the commands of ace2k read it. This is done when LANE or UNIT is given and
        ace2k has its coordinator. Otherwise it returns None. Its error is raised as it is.
        """
        units = self.host.units()
        if units is not None and (lane is not None or gcmd.get("UNIT", None) is not None):
            return units.unit_for(gcmd)
        return None

    def _set_head_refusal(self, slot: Slot, lane: int | None) -> str | None:
        """Returns why ACEK_HEAD_PROBE may not probe the head of slot, whose sensor is set.

        The reasons are that LANE is given, or that the head is not cold (HeadProbe.is_cold).
        Returns None when it may.
        """
        if lane is not None:
            return "its sensor sees filament: probe it without LANE"
        try:
            cold = self._probe().is_cold(slot)
        except ace2k_u1_probe.ProbeError as err:
            return str(err)
        if not cold:
            limit = self._probe().cfg.probe_max_temp
            return f"it is heating or at {limit:g} °C or more: let it cool"
        return None

    def _empty_head_lane(
        self, slot: Slot, lane: int | None, unit: Unit | None
    ) -> tuple[Hooked | None, str | None]:
        """Finds the lane to push for ACEK_HEAD_PROBE on the head of slot, whose sensor is empty.

        Returns (the lane to push, None), or (None, the reason). These are the reasons: there is
        no LANE, LANE is not one of the lanes of the head, or its bay is empty.
        """
        if lane is None:
            return None, "its sensor sees no filament: name the LANE to push"
        h = self._adapter().find(lane - 1, unit)
        if h is None or h not in slot.lanes:
            return None, "LANE is not one of its lanes"
        why = self._empty_bay(h)
        if why is not None:
            return None, why
        return h, None

    def _free_check_refusal(self, lane: int, unit: Unit | None) -> tuple[Hooked | None, str | None]:
        """Tells whether ACEK_FREE_CHECK may run on lane (1-based) of unit.

        Returns (the Hooked of the lane, None) when it may. Otherwise it returns (the Hooked or
        None, the reason). These are the reasons.
        - The lane is not hooked.
        - The reasons of bench_refusal for its head.
        - The lane is not the one that its head holds (ACEK_LANE_ACTIVE names it).
        - The sensor of the head is empty or cannot be read.
        - The bay is empty, or its state cannot be read.
        """
        adapter = self.host.adapter
        h = adapter.find(lane - 1, unit) if adapter is not None else None
        if h is None and self._probe_ready():
            return None, "the lane is not hooked"
        slot = adapter.slot_of(h) if adapter is not None and h is not None else None
        why = self.bench_refusal(slot)
        if why is not None or slot is None or h is None:  # without a slot or lane, refused above
            return h, why
        return h, self._held_lane_refusal(slot, h)

    def _probe_ready(self) -> bool:
        """Tells whether the adapter is hooked and the probe of a shared head is built."""
        adapter = self.host.adapter
        return bool(
            not self.host.problems and adapter is not None and adapter.hooked and self.host.probe
        )

    def _held_lane_refusal(self, slot: Slot, h: Hooked) -> str | None:
        """Returns why ACEK_FREE_CHECK may not check h on the head of slot, or None when it may.

        The reasons are that h is not the lane of the head, that the sensor of the head is empty
        or cannot be read, and that the bay of h is empty or its state cannot be read.
        """
        if slot.current is not h:
            return f"it is not the lane in head {h.head + 1}: name it with ACEK_LANE_ACTIVE first"
        head_set, why = self.sensor_refusal(slot)
        if why is not None:
            return why
        if not head_set:
            return f"head {h.head + 1}'s sensor sees no filament: nothing to check"
        return self._empty_bay(h)
