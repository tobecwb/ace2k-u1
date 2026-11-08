"""The material of a lane, before its load, its eject and a print.

A lane's material is set when its tag was read, or when Edit spool set its record (ACEK_SPOOL).
On a head of one lane, the head's setting on the U1 counts too, first, when it is set (not NONE)
and speaks for the spool in the bay: it was written or changed for that spool, or it was there
when Klipper started with the bay full, and no newer tag read or record came since
(LaneSpools.lone_setting). The page shows it first too. On a shared head the setting counts only
when it was written for the lane. A lane whose bay reads empty has no material, unless it is still
loaded in its head: then its tag or its record tells what its rest is. The U1 takes the
temperature of a load from the head's setting when the load starts. So:

- The U1's load pushes the lane to the head's sensor, and the tag is read on the way. Past the
  sensor, a lane with no material set is rolled back out of the head and the load is refused. A
  material whose type differs from the head's setting is written on the head, the lane is rolled
  back, and the load is refused: the next load heats for it (after_load_push). The roll back goes
  only as far as needed (back_out): until the head's sensor clears, then a little more on a head
  of one lane, or behind the splitter on a shared head, whose lane is then forgotten.
- The page's Load into head N runs that push before the U1's load (ACEK_HEAD_PREPARE): the tag
  is read on the way, and the head is set before the U1 takes its temperature (prepare).
- A print with a plan refuses a lane with no material set (refusal). ACEK_EJECT refuses it up
  front only when the lane is known loaded in its head (eject_refusal): that eject needs the
  U1's unload, which heats the head. Otherwise the unit rolls it back to its bay, cold. An eject
  whose probe finds the filament held by the head's gear stops before the U1's unload heats
  (refusal, checked by the eject).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_common import Hooked
    from .ace2k_u1_config import SwapConfig
    from .ace2k_u1_plan import LaneSpool
    from .ace2k_u1_printer_types import LaneView
    from .ace2k_u1_probe import HeadProbe
    from .ace2k_u1_spools import HeadSettings, LaneSpools, UnitLanes
    from .ace2k_u1_swap import Swapper

try:
    from .ace2k_u1_probe import ProbeError
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_probe import ProbeError  # type: ignore[import-not-found, no-redef]

NO_MATERIAL = "no material set — set it with Edit spool"
# A refused load rolls a lane of a head of one lane back until the head's sensor clears, then
# this much more, so its tip leaves the head. A lane of a shared head goes its headN_splitter_mm
# more instead, behind its splitter.
BACK_OUT_MM = 50.0
# After a refused load on a shared head, its lane is forgotten (HeadWatch.forget_head).
FORGET_CAUSE = "a load was refused and rolled back behind the splitter"
FORGET_TAIL = "a print or a swap finds it, or name it with ACEK_LANE_ACTIVE"


class MaterialError(Exception):
    """A load refused for its material. The text says why and what to do."""


class MaterialHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the material check uses it."""

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def spool_lanes(self) -> LaneSpools: ...
    @property
    def swapper(self) -> Swapper: ...
    @property
    def swap_cfg(self) -> SwapConfig: ...


class MaterialGate:
    """The check of a lane's material before its load, its eject and a print."""

    def __init__(self, host: MaterialHost) -> None:
        self.host = host

    def _adapter(self) -> Adapter:
        return cast("Adapter", self.host.adapter)  # built at the hook, before any lane is used

    def defined(
        self,
        h: Hooked,
        units: UnitLanes | None = None,
        settings: HeadSettings | None = None,
        loaded: bool | None = None,
    ) -> bool | None:
        """True when the lane's material is known: lane_spool gives one, as the page reads it.

        That is the head's setting on a head of one lane while it speaks for the spool in the bay,
        its tag read (a material counts with or without a colour), its Edit spool record, or on a
        shared head the setting written for this lane. A lane whose bay reads empty has none,
        unless it is still loaded in its head (its spool ran out, see lane_spool). None
        when the lane's status cannot be read: a read that fails is not a lane with no material.
        units holds the statuses of the units already read (Adapter.read_units), and settings the
        heads' settings (LaneSpools.head_settings); with None each is read here. loaded is the
        lane's Ejector.known_loaded when the caller already has it (see lane_spool).
        """
        spools = self.host.spool_lanes
        lane = spools.lane_read(h, units)
        if not lane:
            return None
        return self.defined_from(lane, spools.lane_spool(h, units, settings, lane, loaded))

    @staticmethod
    def defined_from(lane: LaneView | None, spool: LaneSpool) -> bool | None:
        """Return defined for a lane's status and the LaneSpool already computed from it.

        None when the status is unread (lane is None or empty), else True when the spool has a
        material.
        """
        if not lane:
            return None
        return bool(spool.material)

    def refusal(self, h: Hooked) -> str | None:
        """Return why a lane with no material set may not be loaded, printed or heated, or None.

        The reason does not name the lane: each caller says the lane and what it refused. A lane
        whose status cannot be read is not refused here (see defined).
        """
        if self.defined(h) is not False:
            return None
        return NO_MATERIAL

    def eject_refused(self, h: Hooked, defined: bool | None, loaded: bool | None = None) -> bool:
        """True when ACEK_EJECT refuses the lane up front for its material.

        defined is the lane's defined(). The lane has no material set, and it is known loaded in
        its head (Ejector.known_loaded): its eject needs the U1's unload, which heats the head. A
        lane with its material set needs no sensor read. Any other eject goes on. If its probe
        then finds the filament held by the head's gear, it stops before the U1's unload for a
        lane with no material (refusal, checked by the eject). loaded is the lane's known_loaded
        when the caller already has it, so one decision reads it once.
        """
        if defined is not False:
            return False
        return self._adapter().ejector.known_loaded(h) if loaded is None else loaded

    def eject_refusal(self, h: Hooked) -> str | None:
        """Return why ACEK_EJECT may not take the lane out for its material, or None.

        See eject_refused. A lane not loaded in its head is rolled back to its bay with no heat.
        """
        loaded = self._adapter().ejector.known_loaded(h)  # read once for both
        refused = self.eject_refused(h, self.defined(h, loaded=loaded), loaded)
        return NO_MATERIAL if refused else None

    def after_load_push(self, h: Hooked, held: bool) -> str | None:
        """Check the lane once the U1's load pushed it to its head's sensor.

        held says that the head's sensor already saw this lane before the push, and this
        adapter's own push did not leave it there (Hooked.parked_at_sensor): its filament may be
        in the head's gear, so it is never pulled back then, as nothing may pull a filament
        against the head. Return None when the load goes on. Otherwise the line says why the load
        is refused: no material set, or a material that the head's setting did not have, which is
        written on the head now (load again). The lane is rolled back out of the head (back_out),
        unless held.

        This runs inside the U1's load, which a G-code (FEED_AUTO) runs, so the setting written
        here goes through the G-code as a swap's does.
        """
        why = self.refusal(h)
        n = self._adapter().lane_text(h)
        line = None if why is None else f"ace2k_u1: lane {n}: {why}: its load refused"
        if line is None:
            line = self._head_differs(h)
        if line is not None and not held:
            self.back_out(h)
        return line

    def _head_differs(self, h: Hooked) -> str | None:
        """Write the lane's spool on its head when the head's type differs, and say so.

        Return None when the head already has the lane's type. A spool whose type the U1 does not
        know is not compared (the U1 cannot heat for it anyway). A tag with a material and no
        colour gives its material: it differs from the head's, but nothing can be written for it,
        so the load is refused.
        """
        spools = self.host.spool_lanes
        spool = spools.own_spool(h)
        if spool is None:
            material = spools.own_record(h)[1].get("material")
            kind = str(material).strip() if material else None
        else:
            kind = spool[1]
        setting = spools.head_setting(h.head)
        head_kind = None if setting is None else setting[1]
        if kind is None or (head_kind or "").upper() == kind.upper():
            return None
        n = self._adapter().lane_text(h)
        fail = self.host.swapper.set_head_spool(h, new_lane=False)
        if fail is not None:
            return f"{fail}: {n}'s load refused"
        return (
            f"ace2k_u1: head {h.head + 1}: {n} is {kind}, the head was set for "
            f"{head_kind or 'none'}: set now — load it again"
        )

    def prepare(self, h: Hooked) -> str:
        """Run the page's push before the U1's load of h, and return the line to say.

        A lane whose material is set needs no push. Otherwise the lane is pushed to its head's
        sensor, its tag read on the way. With the material still not set, the lane is rolled back
        out of the head (back_out) and MaterialError is raised. A push that fails raises it too.
        """
        adapter = self._adapter()
        n = adapter.lane_text(h)
        if self.defined(h) is not False:
            return f"ace2k_u1: lane {n}: its material is set"
        error = adapter.push_runner.push_lane(h)
        if error is not None:
            raise MaterialError(f"ace2k_u1: lane {n} push to e{h.head}: {error}")
        found = self.defined(h)
        if found:
            h.parked_at_sensor = True  # the tip waits at the sensor, short of the head's gear
            return f"ace2k_u1: lane {n}: its tag read on the way to e{h.head}"
        self.back_out(h)
        why = NO_MATERIAL if found is False else "its status not read after the push"
        raise MaterialError(f"ace2k_u1: lane {n}: {why}: its load refused")

    def back_out(self, h: Hooked) -> None:
        """Roll h back out of its head after a refused load, only as far as needed.

        It rolls back until the head's sensor clears, then BACK_OUT_MM more on a head of one lane,
        or the head's headN_splitter_mm more at swap_speed on a shared head, which leaves the tip
        behind the splitter. The probe runs it (HeadProbe.back_out): a sensor that never clears,
        or cannot be read, stops it at how far the push may have gone (h.push_max_mm, at least
        the second part). A roll back that fails is only said: the load is refused either way.

        On a shared head the lane of the head is then forgotten, as after a restart: the next
        print, swap, unload or eject probes the head first, and the page names the lane again
        with ACEK_LANE_ACTIVE.
        """
        adapter = self._adapter()
        cfg = self.host.swap_cfg
        shared = adapter.shared(h)
        if shared:
            then_mm, speed = cfg.splitter_mm[h.head], cfg.swap_speed_for(h.api)
        else:
            then_mm, speed = BACK_OUT_MM, adapter.feed_speed_mm_s
        probe = cast("HeadProbe", adapter.insister)  # built at the hook, as the adapter
        try:
            probe.back_out(h, max(then_mm, h.push_max_mm), then_mm, speed)
        except ProbeError as err:
            adapter.say(f"ace2k_u1: lane {adapter.lane_text(h)} roll back out of e{h.head}: {err}")
        slot = adapter.slot_of(h)
        if shared and slot is not None:
            adapter.head_watch.forget_head(slot, FORGET_CAUSE, FORGET_TAIL)
