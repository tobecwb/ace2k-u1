"""Builds the status of the [ace2k_u1] printer object, for the web page.

The status shows these things.
- The lanes that feed each head, and the lane in each head.
- Why the print stays paused.
- The plan, the spools and the flags of each lane.
The copies of the spools and the plan, and the part about the lanes, are built again only after a
change. get_status in ace2k_u1.py hands the status out (Status).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict

if TYPE_CHECKING:
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_common import Hooked
    from .ace2k_u1_material import MaterialGate
    from .ace2k_u1_printer_types import SpoolRecord
    from .ace2k_u1_probe import HeadProbe
    from .ace2k_u1_spools import LaneSpools, SpoolStore
    from .ace2k_u1_swap import Swapper
    from .ace2k_u1_swap_times import SwapTimes
    from .ace2k_u1_tracker import Plan, PrintTracker

try:
    from .ace2k_u1_config import LANES
    from .ace2k_u1_probe import ProbeError
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_config import LANES  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_probe import ProbeError  # type: ignore[import-not-found, no-redef]


class LaneName(TypedDict):
    """A lane as the status names it for the page.

    It has its unit (its name, or None for the only unit) and its lane (1-4). The keys are read by
    name, which vulture does not see. The noqa on a key says so.
    """

    unit: str | None  # noqa: V107
    lane: int  # noqa: V107


class Copies(TypedDict):
    """The copies that the status keeps of the spools and of the plan.

    The spools are the ACEK_SPOOL record of each lane, by its key. The plan comes from
    plan_status of the tracker.
    """

    spools: dict[str, SpoolRecord]
    plan: dict[str, object] | None


# The part of the status for each lane: (its key, tag_forgotten, push_end, material_set,
# eject_no_material).
LaneRows = tuple[tuple[str, bool, "str | None", "bool | None", bool], ...]


class StatusHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as its status reads it.

    It gives the adapter (None until the hook builds it) and why nothing was hooked. It gives the
    heads that lanes: configures, the Swapper, the spools and the plan.
    """

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def problems(self) -> list[str] | None: ...
    @property
    def heads_status(self) -> list[list[LaneName] | None]: ...
    @property
    def swapper(self) -> Swapper: ...
    @property
    def spool_store(self) -> SpoolStore: ...
    @property
    def spool_lanes(self) -> LaneSpools: ...
    @property
    def material(self) -> MaterialGate: ...
    @property
    def tracker(self) -> PrintTracker: ...
    @property
    def probe(self) -> HeadProbe | None: ...
    @property
    def swap_times(self) -> SwapTimes: ...


class Status:
    """Builds the status of the printer object.

    host is the ACE2kU1. Its adapter is built at the hook. Its Swapper, spools and plan are read
    when used.
    """

    def __init__(self, host: StatusHost) -> None:
        self.host = host
        # The copies of the spools and the plan, with the plan they were built from. They are
        # built again only after a change (changed) or for another plan.
        self.published: tuple[Plan | None, Copies] | None = None
        # The part about the lanes, with the values it was built from.
        self.lanes_pub: tuple[LaneRows, dict[str, dict[str, object]]] | None = None

    def get_status(self, _eventtime: float | None = None) -> dict[str, object]:
        """Returns the status of the printer object, for the web page.

        These are the keys.
        - heads: the lanes that feed each head. Each head has a list of {unit, lane}, or None for
          a head that no lane feeds.
        - current: the lane that is in each head now ({unit, lane} or None).
        - setting_for: the lane for which the filament setting of each head on the U1 was
          written ({unit, lane} or None). On a shared head, the setting belongs to that lane
          only.
        - pause_reason: why the print paused, or stays paused, for the adapter. It is None once
          the print goes on.
        - hooked: whether the adapter is hooked.
        - spools: a copy of the ACEK_SPOOL record of each lane, by its key ("a:2", or "-:2" for
          the unnamed unit). The page uses it to name the spool of a lane that has no tag.
        - lanes: for each hooked lane (with the same keys), tag_forgotten (ACEK_TAG_FORGET),
          push_end and material_set. push_end is the kind of the final event that ended its last
          push short of the head. It is None once its next move starts. material_set is True
          when the lane's material is known (MaterialGate.defined, as the page reads it), False
          when it is not, and None when the lane's status cannot be read: the page refuses Eject
          and Print only on False.
        - probe_cold: for each head that several lanes feed, whether ACEK_HEAD_PROBE finds it cold
          enough to probe (HeadProbe.is_cold: heater off and below probe_max_temp). None for any
          other head, and for a head whose temperature cannot be read.
        - probe_max_temp: the probe's limit in °C (probe_max_temp), or None without a probe.
        - swapping: the heads (0-3, sorted) whose lane changes now: a swap of the head runs (its
          T, a RESUME, or a refill's clearing), or its background swap runs or waits for its T.
          Only while hooked.
        - swap_times: the long-term average of each swap time, measured on the printer
          (ace2k_u1_swap_times): inline, ready, dock, resume and steps (one per dock step), each
          {s: seconds, n: samples}; n 0 is the seed. Only while hooked.
        - print_stops: the swap stops of the print that runs, measured (PrintStops): s, their
          seconds; n, their count; since, the print_duration at which the stop that runs now
          began (None when none runs). A print start clears them. Only while hooked.

        When the adapter is hooked, heads is the map of the heads that are actually hooked. A
        head whose channel is missing has none. When it is not hooked, heads is the map that
        lanes: configures, and there is no current lane. The heads are always published, so the
        page keeps its in-use locks during a print either way. The page offers Edit spool only
        while hooked.
        """
        adapter = self.host.adapter
        copies = self._copies()
        spools = copies["spools"]
        if self.host.problems or adapter is None or not adapter.hooked:
            return {
                "heads": self.host.heads_status,
                "current": [None] * LANES,
                "setting_for": [None] * LANES,
                "pause_reason": None,
                "hooked": False,
                "plan": None,
                "spools": spools,
            }
        heads: list[list[LaneName] | None] = [None] * LANES
        current: list[LaneName | None] = [None] * LANES
        setting_for: list[LaneName | None] = [None] * LANES

        def named(h: Hooked) -> LaneName:
            return {"unit": getattr(h.unit, "name", None), "lane": h.lane + 1}

        for slot in adapter.slots.values():
            heads[slot.head] = [named(h) for h in slot.lanes]
            if slot.current is not None:
                current[slot.head] = named(slot.current)
            if slot.setting_for is not None:
                setting_for[slot.head] = named(slot.setting_for)
        probe = self.host.probe
        return {
            "heads": heads,
            "current": current,
            "setting_for": setting_for,
            "pause_reason": adapter.pause_reason,
            "resuming": self.host.swapper.resuming,
            "swapping": sorted(adapter.swapping),
            "hooked": True,
            "plan": copies["plan"],
            "spools": spools,
            "lanes": self._lanes_status(adapter),
            "probe_cold": self._probe_cold(adapter),
            "probe_max_temp": None if probe is None else probe.cfg.probe_max_temp,
            "swap_times": self.host.swap_times.status(),
            "print_stops": self.host.swap_times.stops.status(),
        }

    def _probe_cold(self, adapter: Adapter) -> list[bool | None]:
        """Returns, for each head, whether ACEK_HEAD_PROBE finds it cold (see get_status).

        It flips only when a head crosses the limit or its heater is turned on or off, so the
        page is not sent the temperature itself.
        """
        cold: list[bool | None] = [None] * LANES
        probe = self.host.probe
        if probe is None:
            return cold
        for slot in adapter.slots.values():
            if slot.shared:
                try:
                    cold[slot.head] = probe.is_cold(slot)
                except ProbeError:
                    cold[slot.head] = None
        return cold

    def _lanes_status(self, adapter: Adapter) -> dict[str, dict[str, object]]:
        """Returns the part of the status for each lane.

        It is built again only when the values of a lane changed.
        """
        material = self.host.material
        units = adapter.read_units(adapter.reactor.monotonic())  # each unit read once
        settings = self.host.spool_lanes.head_settings()  # and the heads' settings
        loaded = {h.key: adapter.ejector.known_loaded(h) for h in adapter.hooked}  # read once
        defined = {
            h.key: material.defined(h, units, settings, loaded[h.key]) for h in adapter.hooked
        }
        rows = tuple(
            (
                self.host.spool_store.key(h.unit, h.lane),
                h.tag_forgotten,
                h.push_end,
                defined[h.key],
                material.eject_refused(h, defined[h.key], loaded[h.key]),
            )
            for h in adapter.hooked
        )
        pub = self.lanes_pub
        if pub is None or pub[0] != rows:
            # eject_no_material: ACEK_EJECT refuses the lane for its material (the page's Eject
            # reads it).
            out: dict[str, dict[str, object]] = {
                k: {"tag_forgotten": f, "push_end": e, "material_set": m, "eject_no_material": x}
                for k, f, e, m, x in rows
            }
            pub = self.lanes_pub = (rows, out)
        return pub[1]

    def changed(self) -> None:
        """Tells the status that the spools or the plan changed in place.

        The status then builds its copies again.
        """
        self.published = None

    def _copies(self) -> Copies:
        """Returns the copies of the spools and the plan. They are built once per change."""
        tracker = self.host.tracker
        pub = self.published
        if pub is None or pub[0] is not tracker.plan:
            spools = {k: dict(v) for k, v in self.host.spool_store.records.items()}
            pub = self.published = (
                tracker.plan,
                {"spools": spools, "plan": tracker.plan_status()},
            )
        return pub[1]
