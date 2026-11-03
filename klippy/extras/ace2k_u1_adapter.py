"""The adapter: the hooked lanes, the heads they feed, their motions and the final events.

It runs the tick of the bay watch. It needs no Klipper, so tests can build it from fakes. ACE2kU1
(ace2k_u1.py) wires it. Its parts run in helper objects beside it: the grip, tail and bay watch
(ace2k_u1_grip), the hand watch and the flows on a head with no lane known (ace2k_u1_heads), the
follow, the final events, the eject, the pushes, the pauses, the tags and the hooks.
"""

from __future__ import annotations

import contextlib
import logging
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import FeedEvent, GCode
    from .ace2k_u1_common import ExtruderPosition, LaneKey
    from .ace2k_u1_hooks import EncoderWheel
    from .ace2k_u1_printer_types import (
        Coordinator,
        ExceptionManager,
        Extruder,
        Fan,
        FeedModule,
        FeedMove,
        FilamentFeed,
        HeadSensor,
        LaneView,
        PauseResume,
        PrintStats,
        PrintTaskConfig,
        Reactor,
        Unit,
        UnitFeed,
    )
    from .ace2k_u1_probe import HeadProbe

    # A lane as the adapter takes it: (its head, its unit, the lane index in the unit).
    HeadLane = tuple[int, Unit, int]

try:
    from . import ace2k_u1_eject as eject_module
    from . import ace2k_u1_events as events_module
    from . import ace2k_u1_follow as follow_module
    from . import ace2k_u1_grip as grip_module
    from . import ace2k_u1_heads as heads_module
    from . import ace2k_u1_hooks as hooks_module
    from . import ace2k_u1_pause as pause_module
    from . import ace2k_u1_push as push_module
    from . import ace2k_u1_screen_unload as screen_module
    from . import ace2k_u1_tag_search as tag_search_module
    from . import ace2k_u1_tags as tags_module
    from .ace2k_u1_common import (
        API_MIN,
        IDLE_MODES,
        NUDGE_BUSY_S,
        Hooked,
        HostCallbacks,
        Route,
        Slot,
        api_at_least,
        parked_slot,
        unit_key,
    )
    from .ace2k_u1_eject import EJECT_PROBE_MM, EJECT_REST_STATES
    from .ace2k_u1_grip import OUT_START_MODES
    from .ace2k_u1_hooks import PULL_STATES, UNLOAD_STEPS, check_feed
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_eject as eject_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_events as events_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_follow as follow_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_grip as grip_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_heads as heads_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_hooks as hooks_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_pause as pause_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_push as push_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_screen_unload as screen_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_tag_search as tag_search_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_tags as tags_module  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        API_MIN,
        IDLE_MODES,
        NUDGE_BUSY_S,
        Hooked,
        HostCallbacks,
        Route,
        Slot,
        api_at_least,
        parked_slot,
        unit_key,
    )
    from ace2k_u1_eject import (  # type: ignore[no-redef]
        EJECT_PROBE_MM,
        EJECT_REST_STATES,
    )
    from ace2k_u1_grip import OUT_START_MODES  # type: ignore[no-redef]
    from ace2k_u1_hooks import (  # type: ignore[no-redef]
        PULL_STATES,
        UNLOAD_STEPS,
        check_feed,
    )

# The rate (Hz) of the feed state report of ace2k when its object does not say.
REPORT_HZ_DEFAULT = 1.0
# States of the U1 in which its channel holds the head with the filament of this lane, besides the
# loaded states. A preload brought the filament to the sensor of the head.
PRELOAD_STATES = (
    "FEED_STA_PRELOAD_PREPARE",
    "FEED_STA_PRELOAD_FEEDING",
    "FEED_STA_PRELOAD_FINISH",
)
# Every step of the load and of the manual feed of the U1 is owned too, from the push to the
# extrusion (a heating that may take minutes), and so is a load that failed on the way. The filament
# at the sensor of the head is then this lane's own filament. The constants of the module are found
# by these prefixes, whichever the module has.
OWNED_PREFIXES = ("FEED_STA_LOAD_", "FEED_STA_MANUAL_")
# The names of the channel states of the U1 for the steps of an unload.
UNLOAD_PREFIX = "FEED_STA_UNLOAD_"
# How many earlier motions of this adapter, whose final event is still to come, are kept.
OLD_SEQS_KEPT = 8
# A print is on in either of these states of print_stats.
PRINT_ON = ("printing", "paused")


def head_switch(sensor: HeadSensor, eventtime: float) -> bool:
    """Return the switch of a head sensor.

    This is runout_buttun_state when that is a plain state (as on the U1), else filament_detected.
    """
    state = getattr(sensor, "runout_buttun_state", None)
    if isinstance(state, (bool, int)):
        return bool(state)
    detected: bool = sensor.get_status(eventtime)["filament_detected"]
    return detected


@dataclass(frozen=True)
class AdapterSetup:
    """What an Adapter is built with, besides the objects of the U1 and the lanes.

    That is the coordinator of ace2k, the values of the config and the callbacks of the printer side
    (HostCallbacks). The defaults of the callbacks need no Klipper.
    """

    units: Coordinator  # the coordinator of ace2k (ace2k_units)
    feed_speed: float
    head_budget_mm: float
    poll_s: float
    retry_push_mm: float = 10.0
    # The push to a head that one lane feeds, while its tag is not read (0: the unit's search runs).
    tag_push_speed: float = 70.0
    # Maps head index to the U1's extruder name (see extruder_names). None: no lane is mapped.
    extruders: dict[int, str] | None = None
    # None means the default of each unit.
    grip_mm: float | None = None
    # Maps head index to that head's extruder position now. None means there is none.
    extruder_position: ExtruderPosition | None = None
    # The probe of ACEK_EJECT: its length and speed, and how long the plunger settles after it.
    eject_probe_mm: float = EJECT_PROBE_MM
    eject_probe_speed: float = 10.0
    eject_settle_s: float = 0.5
    callbacks: HostCallbacks = field(default_factory=HostCallbacks)


class Adapter:
    """The hooks of the U1.

    It needs no Klipper, so tests can build it from fakes.
    """

    def __init__(
        self,
        reactor: Reactor,
        module: FeedModule,
        feeds: list[FilamentFeed],
        lanes: list[HeadLane],
        setup: AdapterSetup,
    ) -> None:
        """Build the adapter from the reactor, the module and feeds of the U1, the lanes and setup.

        ``lanes`` has (head, unit, lane) for each lane. ``setup`` is an AdapterSetup.
        """
        callbacks = setup.callbacks
        self.reactor = reactor
        # ACEK_EJECT with the head sensor set rolls back up to eject_probe_mm at eject_probe_speed.
        # It stops when the buffer plunger of the lane reads taut (held). At its end, after
        # eject_settle_s, a plunger at rest means free, and a plunger that moved means held. The
        # encoder cannot tell, because the slack and the buffer between the unit and the head take
        # up the travel of a held tip.
        self.eject_probe_mm = setup.eject_probe_mm
        self.eject_probe_speed = setup.eject_probe_speed
        self.eject_settle_s = setup.eject_settle_s
        self.module = module
        self.feeds = feeds
        # Maps head index to [(its unit, the lane index in the unit)], in ``lanes:`` order.
        self.heads = Adapter._heads_of(lanes)
        # Maps (id of the feed object, channel) to Slot.
        self.slots: dict[tuple[int, int], Slot] = {}
        self.units = setup.units  # the coordinator of ace2k (ace2k_units)
        # The G-code error class (printer.command_error of Klipper). The core refuses hand_over
        # with RuntimeError. Anything else it raises is an internal error and goes up as it came.
        self.command_error = callbacks.command_error
        # The units in use, in head order. The bay watch reads each once per tick.
        self.used = Adapter.units_in_use(lanes)
        apis = [u.feed for u in self.used]
        self.feed_speed_mm_s = setup.feed_speed
        # Not capped by the longest move of a unit: a longer push runs in several moves.
        self.head_budget_mm = setup.head_budget_mm
        self.poll_s = setup.poll_s
        self.retry_push_mm = setup.retry_push_mm
        self.tag_push_speed_mm_s = setup.tag_push_speed
        # The grip, the tail and the bay watch, with grip_mm as configured.
        self.grip_watch: grip_module.GripWatch = grip_module.GripWatch(self, setup.grip_mm)
        # The hand watch of the shared heads, and the U1 flows on a head with no lane known.
        self.head_watch: heads_module.HeadWatch = heads_module.HeadWatch(self)
        self.follow: follow_module.Follow = follow_module.Follow(self)
        # Units whose status could not be read (see read_units): logged once per episode.
        self.status_unread: set[int] = set()
        # Maps head index to that head's extruder position now (mm, as the U1 reads it). None: none.
        self.extruder_position: ExtruderPosition | None = setup.extruder_position
        # The next three sets are built at the hook.
        self.owned_states: set[str] | None = None  # from build_owned_states()
        self.loaded_states: set[str] | None = None  # from get_loaded_states()
        self.rest_states: set[str] | None = None  # from get_rest_states()
        self.unload_states: set[str] | None = None  # from get_unload_states()
        self._tag_parts()
        # Maps head index to the U1's extruder name (see extruder_names). None: no lane is mapped.
        self.extruders: dict[int, str] | None = setup.extruders
        # The lookup_object of the printer, used to find the heaters of the heads.
        self.lookup: Callable[[str, object], object] | None = callbacks.lookup
        self.settle_s: float
        self.busy_retry_s: float
        self.settle_s, self.busy_retry_s = Adapter._report_timing(apis, setup.poll_s)
        # Without a gcode object, console notices go only to the log.
        self.gcode: GCode | None = callbacks.gcode
        self.hooked: list[Hooked] = []
        self.by_key: dict[LaneKey, Hooked] = {}  # Maps a lane key to its Hooked.
        self.problems: list[str] | None = None
        self.ejector: eject_module.Ejector = eject_module.Ejector(self)
        # The cold path of the Unload of the U1's screen.
        self.screen_unload: screen_module.ScreenUnload = screen_module.ScreenUnload(self)
        self.events: events_module.FeedEvents = events_module.FeedEvents(self)
        self.push_runner: push_module.PushRunner = push_module.PushRunner(self)
        self.print_stats: PrintStats | None = None
        self.pause_runner: pause_module.PauseRunner = pause_module.PauseRunner(self)
        self._no_pause_objects()
        # Why the print paused, or stays paused (see alert). The status publishes it until the
        # print goes on or ends.
        self.pause_reason: str | None = None
        self.last_print_state: str | None = None
        # Called first by each watch tick (see watch_print), if set.
        self.on_tick: Callable[[float], object] | None = callbacks.on_tick
        # Heads in the middle of a swap. The end of their U1 unload ejects nothing.
        self.swapping: set[int] = set()
        self._take_callbacks(callbacks)

    def _tag_parts(self) -> None:
        """Build the parts of the tags: the writes to the heads and the two kinds of read."""
        self.tag_reader: tags_module.TagReader = tags_module.TagReader(self)
        self.tag_writer: tags_module.TagWriter = tags_module.TagWriter(self)
        # The read with motion of a spool that the unit's own load left unread at the park.
        self.tag_search: tag_search_module.TagSearch = tag_search_module.TagSearch(self)

    def _take_callbacks(self, callbacks: HostCallbacks) -> None:
        """Take the callbacks of the printer side (see HostCallbacks)."""
        # Called each tick with a lane whose bay reads empty (ACE2kU1 drops its spool record). It
        # never raises into the bay watch.
        self.on_bay_empty: Callable[[Hooked], object] | None = callbacks.on_bay_empty
        # Called once per tick after every lane's bay watch (ACE2kU1 notes the heads' settings
        # then, with each bay's full or empty already known). It never raises into the watch.
        self.on_lanes_watched: Callable[[], object] | None = callbacks.on_lanes_watched
        # The probe of a shared head (ace2k_u1_probe.HeadProbe). It needs this adapter, so it is
        # set at the hook with a + group. A load into such a head pushes through it.
        self.probe: HeadProbe | None = callbacks.probe
        # The probe for the push of a load into a head that one lane feeds (see
        # PushRunner.push_lane). The hook sets it, whatever the groups: the probe itself when there
        # is one, else one built for this.
        self.insister: HeadProbe | None = callbacks.probe
        # Called by the pause timer with the lane of a shared head whose spool ran out at its bay
        # (Refiller.shared_runout).
        self.on_shared_runout: Callable[[Hooked], object] | None = callbacks.on_shared_runout
        # All three come from Swapper: a swap or refill is pending, the head it is on (or None),
        # and a refill of lane h is pending.
        self.swap_pending: Callable[[], bool] = callbacks.swap_pending
        self.swap_slot: Callable[[], Slot | None] = callbacks.swap_slot
        self.refill_pending: Callable[[Hooked], bool] = callbacks.refill_pending
        self.load_material: Callable[[Hooked, bool], str | None] = callbacks.load_material
        self.material_refusal: Callable[[Hooked], str | None] = callbacks.material_refusal

    def _no_pause_objects(self) -> None:
        """Set the U1 objects of the pauses to none (see setup_pause).

        Without them a pause is a console line.
        """
        self.runout_source: str = "head"
        self.pause_resume: PauseResume | None = None
        self.exception_manager: ExceptionManager | None = None
        self.send_event: Callable[..., object] | None = None  # send_event of the printer
        # The print_task_config of the U1, for its auto replenish.
        self.task_config: PrintTaskConfig | None = None

    @staticmethod
    def _heads_of(lanes: list[HeadLane]) -> dict[int, list[tuple[Unit, int]]]:
        """Return a map from head index to its [(unit, lane index)] entries.

        The map is built from ``lanes:``. The entries are in ``lanes:`` order. Several entries mean
        that a head is fed by a + group.
        """
        heads: dict[int, list[tuple[Unit, int]]] = {}
        for head, unit, lane in lanes:
            heads.setdefault(head, []).append((unit, lane))
        return heads

    @staticmethod
    def _report_timing(apis: list[UnitFeed], poll_s: float) -> tuple[float, float]:
        """Return (settle_s, busy_retry_s) for the feed objects of the units.

        settle_s is how long a lane state must hold. busy_retry_s is the retry period of a busy
        refusal.
        """
        # insert arrives at 10 Hz, but the lane mode only with the feed state report. An insert is
        # told to the U1 once it has held for two reports (plus a watch period) and the mode then
        # says that the unit is not loading the filament itself. With several units, the slowest
        # report rules.
        rates = [getattr(api, "report_hz", None) or REPORT_HZ_DEFAULT for api in apis]
        report_hz = min(rates) if rates else REPORT_HZ_DEFAULT
        # ace2k's own span for a motion that is reported idle without its final event
        # (gone_after_s, host interface 8), plus a watch period. This is one rule for "gone
        # without its event".
        gone = [getattr(api, "gone_after_s", 2.0 / hz) for api, hz in zip(apis, rates)]
        settle_s = (max(gone) if gone else 2.0 / report_hz) + poll_s
        # A busy refusal is retried once per report, not once per local poll. Each try is a round
        # trip on the half-duplex link of the unit.
        return settle_s, 1.0 / report_hz

    def lane_view(
        self, h: Hooked, eventtime: float | None = None
    ) -> tuple[LaneView, bool, int | None]:
        """Read the status of the unit of h once.

        Return (lane dict, link_proven, sensors_reports). sensors_reports is None when the status of
        ace2k carries no count. It raises what the read raises, and the callers say how they log it.
        """
        status = h.unit.get_status(eventtime or self.reactor.monotonic())
        reports = status.get("sensors_reports")
        if isinstance(reports, bool) or not isinstance(reports, int):
            reports = None
        return status["lanes"][h.lane], status.get("link_proven") is True, reports

    def lane_status(self, h: Hooked, eventtime: float | None = None) -> LaneView:
        return self.lane_view(h, eventtime)[0]

    @contextlib.contextmanager
    def channel_lock(self, ff: FilamentFeed, ch: int, wait_s: float | None) -> Iterator[bool]:
        """Take the channel lock of the U1 for the block.

        It waits at most wait_s (None: as long as it takes), polling every 0.1 s. It yields whether
        the lock is held, and the block releases it. If the wait ran out, there is nothing to
        release, and the block decides.
        """
        deadline = None if wait_s is None else self.reactor.monotonic() + wait_s
        while ff.channel_active is not None:
            now = self.reactor.monotonic()
            if deadline is not None and now >= deadline:
                yield False
                return
            self.reactor.pause(now + 0.1)
        ff.channel_active = ch
        try:
            yield True
        finally:
            ff.channel_active = None

    def lane_or_none(self, h: Hooked) -> LaneView | None:
        """Return the lane status, or None when the read fails.

        A failure is logged and read as None. It never raises.
        """
        try:
            return self.lane_status(h)
        except Exception:
            logging.exception("ace2k_u1: lane %s status", self.lane_text(h))
            return None

    def lane_text(self, h: Hooked) -> str:
        """Return the lane as the console names it: "3" with one unit, "b:3" with several."""
        return self.units.lane_text(h.unit, h.lane)

    def lane_args(self, h: Hooked) -> str:
        """Return the lane as the parameters of a command, as ace2k prints them.

        This is "LANE=3", or "LANE=3 UNIT=b" with several units.
        """
        return self.units.lane_args(h.unit, h.lane)

    def key_text(self, key: LaneKey) -> str:
        h = self.by_key.get(key)
        if h is not None:
            return self.lane_text(h)
        return str(key + 1) if isinstance(key, int) else f"{key[0]}:{key[1] + 1}"

    def head_order(self, key: LaneKey) -> float:
        """Return the sort key of a lane key: its head.

        A key that is hooked to none sorts last.
        """
        h = self.by_key.get(key)
        return h.head if h is not None else math.inf

    def hooked_lane(self, unit: object, lane: int) -> Hooked | None:
        """Return the Hooked fed by lane (0-based) of a unit, or of the unit with that name."""
        return self.by_key.get(unit_key(unit, lane))

    def find(self, lane: int, unit: Unit | None = None) -> Hooked | None:
        """Return the Hooked fed by lane of unit.

        A unit of None means the only unit in use.
        """
        if unit is None:
            if len(self.used) != 1:
                return None
            unit = self.used[0]
        return self.hooked_lane(unit, lane)

    @staticmethod
    def units_in_use(lanes: list[HeadLane]) -> list[Unit]:
        """Return the units that the heads use, each once, in head order.

        ``lanes`` has (head, unit, lane) entries.
        """
        used: list[Unit] = []
        for _head, unit, _lane in sorted(lanes, key=lambda item: item[0]):
            if all(unit is not u for u in used):
                used.append(unit)
        return used

    def say(self, msg: str) -> None:
        """Send a notice to the log and, when wired, to the console.

        It is logged once either way, because respond_info of Klipper logs what it prints.
        """
        if self.gcode is not None:
            self.gcode.respond_info(msg)
        else:
            logging.info(msg)

    def alert(self, msg: str) -> None:
        """Send a line the operator must see: why the print paused, or stays paused.

        It is an error line on the console ("!! " of Klipper), so it stands out from the notices
        around it. It is also the pause_reason of the status until the print goes on or ends. It is
        logged as a notice is.
        """
        self.pause_reason = msg
        respond = getattr(self.gcode, "respond_raw", None)
        if respond is None:
            self.say(msg)
            return
        logging.info(msg)
        respond(f"!! {msg}")

    def present(self, h: Hooked) -> bool:
        return self.lane_status(h).get("insert") is True

    def cut_possible(self, h: Hooked) -> bool:
        """Whether a push of h could cut the unit's tag search (see PushRunner.cut_possible)."""
        return self.push_runner.cut_possible(h)

    def present_or_not(self, h: Hooked) -> bool:
        """Return present, but read a failure as not present (logged).

        It never raises, so the resets of the caller after it always run.
        """
        try:
            return self.present(h)
        except Exception:
            logging.exception("ace2k_u1: lane %s status", self.lane_text(h))
            return False

    def hook_all(self) -> list[str]:
        """Check everything, then hook every mapped channel, or nothing.

        Return the problems.
        """
        problems: list[str] = []
        # ace2k carries the version on the feed object of each unit (its .feed). It is the host
        # interface through which this adapter drives a unit.
        for unit in self.used:
            if not api_at_least(unit.feed, API_MIN):
                problems.append(f"{self.units.label(unit)} host interface older than {API_MIN}")
            else:
                problems.extend(self.grip_watch.check_grip(unit))
        for ff in self.feeds:
            problems.extend(check_feed(self.module, ff))
        if not problems:
            problems.extend(self._check_head_sensors())
        self.problems = problems
        if problems:
            return problems
        self.loaded_states = self.get_loaded_states()  # once, at the hook: the constants of the U1
        self.owned_states = self.build_owned_states()
        self.rest_states = self.get_rest_states()
        for ff in self.feeds:
            route: dict[int, Slot] = {}
            for ch in range(2):
                refs = self.heads.get(ff.filament_ch[ch])
                if not refs:
                    continue
                lanes = [Hooked(ff, ch, unit, lane) for unit, lane in refs]
                slot = Slot(ff.filament_ch[ch], lanes)
                self.slots[(id(ff), ch)] = slot
                route[ch] = slot
                self.hooked.extend(lanes)
                self.by_key.update((h.key, h) for h in lanes)
            if route:
                hooks_module.hook_feed(self, ff, Route(route))
        self._map_extruders()
        self.follow.adopt_active()
        return []

    def head_of_extruder(self, name: str) -> int | None:
        """Return the head whose extruder has the section name, or None (no such extruder)."""
        heads = {v: k for k, v in (self.extruders or {}).items()}
        return heads.get(name)

    def _head_extruder(self, head: int) -> Extruder | None:
        """Return the U1's extruder object of the head, or None."""
        name = (self.extruders or {}).get(head)
        if not name or self.lookup is None:
            return None
        return cast("Extruder | None", self.lookup(name, None))

    def head_fan(self, head: int) -> Fan | None:
        """Return the print fan that the U1 binds to the head's extruder, or None."""
        return cast("Fan | None", getattr(self._head_extruder(head), "binding_fan", None))

    def head_heater(self, head: int) -> tuple[float, float] | None:
        """Return the head's heater as (temperature, target) in °C, or None if unreadable."""
        ext = self._head_extruder(head)
        try:
            # No extruder (None) raises here too, which counts as not read.
            temp, target = cast("Extruder", ext).get_heater().get_temp(self.reactor.monotonic())
            return float(temp), float(target)
        except Exception:
            return None

    def slot_of_head(self, head: int) -> Slot | None:
        return next((s for s in self.slots.values() if s.head == head), None)

    def slot_of(self, h: Hooked) -> Slot | None:
        return self.slots.get((id(h.ff), h.ch))

    def set_current(self, slot: Slot, h: Hooked | None) -> None:
        """Set the lane that is in the head now.

        Every hook of the head's channel acts on it, and it feeds the U1's wheel. None means no lane
        is in the head, so the first lane feeds the wheel, as at the hook. The wheel object stays
        the same and is re-based on its next sample (see _wheel_from). In a head of several lanes,
        the old and the new lane start clean of what the sensor of the head gave them (see
        head_state_cleared).
        """
        old = slot.current
        slot.current = h
        if slot.loaded is not h:
            slot.loaded = None  # another lane named in the head is not a loaded one
        if slot.setting_for is not h:
            slot.setting_for = None  # the setting was written for no lane, or for another
        if slot.shared and old is not h:
            for lane in (old, h):
                if lane is not None:
                    self.grip_watch.head_state_cleared(lane)

    def parked(self, h: Hooked) -> bool:
        """True when the lane is parked outside a head that several lanes feed.

        The sensor of the head speaks for the current lane of the head, and not for this lane.
        """
        return parked_slot(self.slot_of(h), h)

    def parked_known(self, h: Hooked) -> bool:
        """True when the lane is parked outside a shared head whose own lane is known.

        With none known (after a restart), the sensor of the head may well be this lane's.
        """
        slot = self.slot_of(h)
        return parked_slot(slot, h) and slot.current is not None

    def groups(self) -> list[Slot]:
        """Return the slots of the heads that several lanes feed (the + groups)."""
        return [slot for slot in self.slots.values() if slot.shared]

    def refill_heads(self) -> set[int]:
        """Return the heads whose spool's end is refilled: the + groups, every head with unit."""
        return {
            slot.head for slot in self.slots.values() if slot.shared or self.runout_source == "unit"
        }

    def _map_extruders(self) -> None:
        """Tell ace2k which extruder each hooked lane feeds, for the feed-forward.

        The extruder is the one of the head of the lane. A laneN_extruder key in [ace2k] wins on the
        side of ace2k. A head with no extruder on the printer leaves its lane unmapped (hooked,
        without the feed-forward), and this is said.
        """
        if self.extruders is None:
            return
        for h in sorted(self.hooked, key=lambda h: h.head):
            name = self.extruders.get(h.head)
            if name is None:
                self.say(
                    f"ace2k_u1: lane {self.lane_text(h)}: no extruder {h.head} on the printer; no "
                    "feed-forward"
                )
                continue
            h.api.set_lane_extruder(h.lane, name)

    def _check_head_sensors(self) -> list[str]:
        """Return one problem per channel to be hooked whose head filament sensor is absent.

        The U1 looks the sensor up as optional, and the push toward the head stops on it.
        """
        problems: list[str] = []
        for ff in self.feeds:
            for ch in range(2):
                head = ff.filament_ch[ch]
                if head not in self.heads:
                    continue
                sensor = ff.runout_sensor[ch]
                if sensor is None or not callable(getattr(sensor, "get_status", None)):
                    problems.append(f"head sensor e{head}_filament missing")
        return problems

    def feed_error(self, name: str) -> str:
        error: str = getattr(self.module, name)
        return error

    def head_set(self, h: Hooked) -> bool:
        """Return whether the sensor of the head sees filament now, from its switch.

        The sensor of the U1 keeps the switch in runout_buttun_state (its spelling). Its
        filament_detected is the notion of the runout instead. While printing, that stays True after
        the switch empties, until the extruder has moved on, and a swap moves no extruder. A sensor
        without that attribute (or one that is not a plain state) is read through its status.
        """
        return head_switch(h.ff.runout_sensor[h.ch], self.reactor.monotonic())

    def retry_busy_start(
        self,
        h: Hooked,
        start: Callable[[], FeedMove],
        wait_s: float = NUDGE_BUSY_S,
        period: float | None = None,
    ) -> FeedMove:
        """Call start() (a start_move of the lane of h) and retry its busy refusals.

        A busy refusal means the lane is winding down from a stop. It is retried every ``period``
        (default busy_retry_s) for ``wait_s``. The move counts as one of this adapter's own moves.
        Any other refusal, or a busy one past that time, is raised.
        """
        # This is not feed.retry_busy of ace2k. That one is no host interface, and it retries every
        # 0.25 s. This adapter retries once per report, and each try is a round trip on the link of
        # the unit.
        period = self.busy_retry_s if period is None else period
        give_up = self.reactor.monotonic() + wait_s
        while True:
            try:
                move = start()
            except Exception as err:  # FeedRefused (with a reason), ValueError, RuntimeError
                busy = getattr(err, "reason", None) == "busy"
                if busy and self.reactor.monotonic() + period <= give_up:
                    self.reactor.pause(self.reactor.monotonic() + period)
                    continue
                raise
            self.started(h, move)
            return move

    # --- the current motion of the lane: the one source for every stop and suppression decision ---

    def _ended(self, h: Hooked) -> bool:
        """True when the final event of the current motion came and no report since is believed."""
        if h.ended_at is None:
            return False
        if self.reactor.monotonic() >= h.ended_at + self.settle_s:
            h.ended_at = None  # a report since the end has surely come: its mode is the lane's
            return False
        return True

    def motion(self, h: Hooked, lane: LaneView | None = None) -> str | None:
        """Return the mode of the current motion of the lane, or None when it runs none.

        The follow that this adapter armed counts, before any report shows it. Once the final event
        came, there is no motion until a report can be believed again. Otherwise the mode is what
        the last report says. It never raises.
        """
        if h.armed:
            return "following"
        if self._ended(h):
            return None
        if lane is None:
            lane = self.lane_or_none(h)
            if lane is None:
                return None
        mode = lane.get("mode")
        return None if mode in IDLE_MODES else mode

    def _current_seq(self, h: Hooked) -> int | None:
        """Return the seq of the current motion of the lane, if this adapter started it."""
        if h.armed:
            return h.assist_seq
        return h.motion_seq if h.motion_open else None

    def started(self, h: Hooked, move: FeedMove) -> None:
        """Note that this adapter started a motion on the lane, which is now the current one.

        An earlier open motion of this adapter becomes an old one, and its final event, if late,
        ends nothing current.
        """
        if h.motion_open and h.motion_seq is not None:
            h.old_seqs = (h.old_seqs + [h.motion_seq])[-OLD_SEQS_KEPT:]
        h.motion_seq = getattr(move, "seq", None)
        h.motion_open = h.motion_seq is not None
        h.parked_at_sensor = False  # the lane moves: where its tip stops is not known yet
        h.ended_at = None
        h.eject_drop = False  # a new move after an eject: the bay it empties is not the eject's
        if getattr(move, "mode", None) in OUT_START_MODES:
            self.moved_out(h)
        h.grip_live = False  # a start clears the grip of the lane on the unit
        h.grip_down = False  # and what the bay showed under it

    def head_holds(self, h: Hooked) -> bool:
        """True when the sensor of the head still sees the filament.

        It never raises.

        A sensor that cannot be read counts as seeing it. (The unit bounds the tail anyway, and
        while the sensor stays unreadable every automatic load of this lane is a grip, the safe
        side.) This is logged once per episode.
        """
        try:
            holds = bool(self.head_set(h))
        except Exception:
            if not h.head_unread:
                h.head_unread = True
                logging.exception(
                    "ace2k_u1: lane %s head sensor unreadable: counted as holding, its loads grip",
                    self.lane_text(h),
                )
            return True
        h.head_unread = False
        return holds

    def running(self, h: Hooked, lane: LaneView | None = None) -> bool:
        """True when the lane runs a motion (see motion).

        It never raises; unknown counts as not running.
        """
        return self.motion(h, lane) is not None

    def halt(self, h: Hooked, lane: LaneView | None = None) -> None:
        """Stop the lane for this adapter.

        If a motion of this adapter is still open and its seq is known, the final event of that
        motion answers the stop, so a "stopped" is not a stop by hand. Any other stop expects
        nothing. An idle lane brings no event, and an expectation that no event answers would take a
        later stop by hand (of a follow armed since) for a stop of this adapter.
        """
        seq = self._current_seq(h)
        if seq is not None and self.running(h, lane):
            h.expect_stopped = True
            h.expect_seq = seq
        h.api.stop(h.lane)

    def stop(self, h: Hooked, why: str, lane: LaneView | None = None) -> None:
        """Stop the lane.

        A failure is a notice, and never an error into the U1.
        """
        try:
            self.halt(h, lane)  # before the disarm, because an armed follow is a running lane
        except Exception as err:
            logging.exception("ace2k_u1: lane %s stop on %s", self.lane_text(h), why)
            self.say(f"ace2k_u1: lane {self.lane_text(h)} not stopped on {why} — {err}")
        finally:
            h.armed = False

    # --- the speed bounds and the feed events of the unit ----------------------------------------

    @staticmethod
    def within_speed(h: Hooked, speed: float) -> float:
        """Return speed limited to the speed bounds of the unit of h (when ace2k gives them)."""
        low = getattr(h.api, "speed_min", None)
        high = getattr(h.api, "speed_max", None)
        if isinstance(low, (int, float)) and speed < low:
            speed = low
        if isinstance(high, (int, float)) and speed > high:
            speed = high
        return speed

    def refusals(self) -> tuple[type[Exception], ...]:
        """Return what the hand_over of the core refuses with.

        These are RuntimeError (a follower that did not end) and the G-code error. Anything else is
        an internal error that the core raises so that Klipper shuts down. It is never caught.
        """
        return (RuntimeError, self.command_error)

    def on_feed_event(self, lane: int, event: FeedEvent) -> None:
        """Handle the "ace2k:feed_event" event (see FeedEvents.on_feed_event).

        It never raises.
        """
        self.events.on_feed_event(lane, event)

    # --- the print state and the lane errors ------------------------------------------------------

    def print_state(self) -> str | None:
        """Return the state of print_stats, from the object given at setup_pause.

        The module's own commands use U1Printer.print_state, which looks the state up
        afresh.
        """
        state: str | None = getattr(self.print_stats, "state", None)
        return state

    def u1_pause_reason(self, line: str) -> None:
        """Record a failure that this adapter hands to the U1, which pauses on it during a print.

        Examples are the push of its load and a lane error in the follow. The line becomes the
        pause_reason of the status, as the reason of an adapter pause does, so the top bar of the
        page shows it. Outside a print nothing pauses, so nothing is set. The RESUME that goes on,
        or the end of the print, clears it. While printing, a new failure is the reason, whatever
        came before it, because a reason that no pause followed is stale. In a print that is already
        paused, the failure is the reason only when there is none, so what paused the print stays.
        """
        state = self.print_state()
        if state == "printing" or (state == "paused" and self.pause_reason is None):
            self.pause_reason = line

    # --- the bay watch ---------------------------------------------------------------------

    def shared(self, h: Hooked) -> bool:
        """True when the lane feeds a head that several lanes feed."""
        slot = self.slot_of(h)
        return slot is not None and slot.shared

    def build_owned_states(self) -> set[str]:
        """Return the channel states of the U1 in which the head filament is this lane's.

        These are loaded, the steps of an unload, preloaded, and anywhere in a load or a manual feed
        (failed included).
        """
        m = self.module
        flows = {getattr(m, n) for n in dir(m) if n.startswith(OWNED_PREFIXES)}
        return self.get_loaded_states() | {getattr(m, n) for n in PRELOAD_STATES} | flows

    def get_rest_states(self) -> set[str]:
        """Return the channel states of the U1 for a head at rest (EJECT_REST_STATES).

        They are built once, like the loaded states.
        """
        if self.rest_states is None:
            m = self.module
            self.rest_states = {getattr(m, name) for name in EJECT_REST_STATES if hasattr(m, name)}
        return self.rest_states

    def get_unload_states(self) -> set[str]:
        """Return the channel states of the U1 for the steps of an unload.

        They are built once, at the first use.
        """
        if self.unload_states is None:
            m = self.module
            self.unload_states = {getattr(m, n) for n in dir(m) if n.startswith(UNLOAD_PREFIX)}
        return self.unload_states

    def get_loaded_states(self) -> set[str]:
        """Return the channel states of the U1 for a loaded head.

        They are built once, at the hook, or at the first use when the adapter is not hooked through
        hook_all, and read on every tick.
        """
        if self.loaded_states is None:
            m = self.module
            names = ("FEED_STA_LOAD_FINISH",) + PULL_STATES + UNLOAD_STEPS
            self.loaded_states = {getattr(m, name) for name in names}
        return self.loaded_states

    def moved_out(self, h: Hooked) -> None:
        """Note that a move of h that takes its filament out of the bay ran now (see bay_watch)."""
        h.out_at = self.reactor.monotonic()

    def watch(self, eventtime: float) -> float:
        """Run one tick of the bay watch and return the next waketime.

        A tick samples the wheels, tells the U1 the port events when they change, stops the follow
        when a filament leaves its bay outside the tail, clears the lane on the resume of the U1,
        keeps the standing follow, and makes the decisions of the tags.

        The status of each unit is read once per tick, and the view of the lane is passed down to
        every helper (their ``lane`` parameter). A helper reads afresh only outside the tick, or to
        judge a busy refusal of a start, which answers a query sent after the view.
        """
        # The status of each unit is read once per tick. ``views`` holds the dict of each hooked
        # lane, by its key. A unit whose status cannot be read skips this tick for its heads only.
        units = self.read_units(eventtime)
        views = {h.key: units[id(h.unit)][h.lane] for h in self.hooked if id(h.unit) in units}
        state = self.print_state()
        resumed = self.last_print_state == "paused" and state == "printing"
        self.last_print_state = state
        for slot in self.groups():
            self._tick_hand(slot, views, eventtime)
        for h in self.hooked:
            lane = views.get(h.key)
            if lane is not None:
                self._tick_lane(h, lane, eventtime, resumed)
        if self.on_lanes_watched is not None:
            self._guarded(lambda: "ace2k_u1: heads' settings", self.on_lanes_watched)
        # The host's decisions of the tick (the print's state, its end) come after the bays and
        # the heads' settings, so they read this tick's spools.
        if self.on_tick is not None:
            self._guarded(lambda: "ace2k_u1: watch tick", self.on_tick, eventtime)
        if self.tag_writer.tags is not None:
            self._guarded(lambda: "ace2k_u1: tags", self.tag_writer.watch_tags, views, eventtime)
        return eventtime + self.poll_s

    @staticmethod
    def _guarded(what: Callable[[], str], fn: Callable[..., object], *args: object) -> None:
        """Call fn(*args) and log an exception as what().

        It never raises into the bay watch.

        The text is built only when the exception comes.
        """
        try:
            fn(*args)
        except Exception:  # never into the bay watch
            logging.exception(what())

    def _tick_hand(self, slot: Slot, views: dict[LaneKey, LaneView], eventtime: float) -> None:
        """Run the hand watch of a head that several lanes feed, in this tick."""
        self._guarded(
            lambda: f"ace2k_u1: head {slot.head + 1} hand watch",
            self.head_watch.watch_hand,
            slot,
            views,
            eventtime,
        )

    def _tick_lane(self, h: Hooked, lane: LaneView, eventtime: float, resumed: bool) -> None:
        """Run the part of the bay watch tick for one lane.

        ``lane`` is its view in this tick. ``resumed`` is True when the U1 resumed since the last
        tick.
        """
        self.grip_watch.bay_watch(h, lane, eventtime)
        self.grip_watch.watch_out(h, lane)
        if h.push_end is not None and h.ended_at is None and lane.get("mode") not in IDLE_MODES:
            h.push_end = None  # the next move of the lane started, so its last push is history
        if lane.get("mode") in IDLE_MODES:
            h.ended_at = None  # the report has caught up with the last final event
            self._expire_follow(h, eventtime)
        self.follow.stop_unchosen(h, lane)
        if lane.get("insert") is False:
            h.at_grip = False  # the spool left its bay, so its gripped load no longer stands
        self.tag_search.watch_lane(h, lane)  # a new spool gets its own read with motion
        if self.parked(h):
            self._tick_parked(h, lane, eventtime)
        else:
            self._tick_owned(h, lane, eventtime, resumed)

    def _expire_follow(self, h: Hooked, eventtime: float) -> None:
        """End the follow of the lane, if it is reported idle and its arm is settle_s old."""
        if h.armed and eventtime >= h.armed_at + self.settle_s:
            # The follow is reported idle well after its arm, and its final event was never seen.
            logging.warning("ace2k_u1: lane %s follow gone without its event", self.lane_text(h))
            h.armed = False
            h.assist_seq = None

    def _tick_parked(self, h: Hooked, lane: LaneView, eventtime: float) -> None:
        """Run the tick for a lane parked outside its shared head."""
        # Only its bay counts here (its tag below). It is never armed or held from the sensor of
        # the head, which reads another lane. It is always gripped. A spool put into its bay is
        # only gripped, because the tag search could reach past the splitter.
        self.grip_watch.watch_parked(h, lane, eventtime)
        # This is in both ticks and not once above them. The one in _tick_owned comes after its
        # adopt, whose start (started) clears grip_live.
        if h.grip_live and lane.get("insert") is not True:
            self.grip_watch.grip_bay_empty(h)
        self._guarded(
            lambda: f"ace2k_u1: lane {self.lane_text(h)} grip",
            self.grip_watch.sync_grip,
            h,
            None,
            lane,
        )

    def _tick_owned(self, h: Hooked, lane: LaneView, eventtime: float, resumed: bool) -> None:
        """Run the tick for the lane of a head (its only lane, or its current one)."""
        self.grip_watch.end_declined_watch(h, lane)
        if self.grip_watch.shared_end_missed(h, lane):
            self.grip_watch.shared_end(h)  # its tail notice did not come, so it is taken here
        if lane.get("mode") == "following" and not h.armed:
            self.follow.adopt(h, lane)  # the follow_default of ace2k armed it
        holds = self.head_holds(h)  # one reading of the sensor of the head per tick
        self.grip_watch.sample_wheel(h, lane, eventtime, holds)
        # This comes after the adopt above, whose start (started) clears grip_live. So it is not
        # above the branch of the two ticks, and it is written in both.
        if h.grip_live and lane.get("insert") is not True:
            self.grip_watch.grip_bay_empty(h)
        self.grip_watch.watch_lane(h, lane, eventtime, holds)
        self._guarded(
            lambda: f"ace2k_u1: lane {self.lane_text(h)} grip",
            self._grip_tick,
            h,
            holds,
            lane,
            eventtime,
        )
        self._guarded(
            lambda: f"ace2k_u1: lane {self.lane_text(h)} follow",
            self._follow_tick,
            h,
            lane,
            resumed,
        )

    def _grip_tick(self, h: Hooked, holds: bool, lane: LaneView, eventtime: float) -> None:
        """Run the tick for the waits behind a piece and the grip of the lane of a head."""
        self.grip_watch.watch_head_clear(h, holds, eventtime)
        self.grip_watch.sync_grip(h, holds, lane)

    def _follow_tick(self, h: Hooked, lane: LaneView, resumed: bool) -> None:
        """Run the standing follow's tick for the current lane of a head.

        If the U1 resumed, its error is cleared first.
        """
        if resumed:
            self.follow.clear_on_resume(h, lane)
        self.follow.watch_follow(h, lane)

    def in_print(self) -> bool:
        """True when a print is on: printing or paused."""
        return self.print_state() in PRINT_ON

    def print_end_unloads(self, head: int) -> bool:
        """True while the U1 runs the end of a print and its end unload takes this head.

        INNER_PRINT_END sets is_exec_print_end_action, and only the end or the start of a print
        clears it. SM_PRINT_END_AUTO_UNLOAD_FILAMENT then unloads each head that the print used,
        that is not soft, and whose end_unload_filament is set. A table that cannot be read
        counts as no end unload of the head."""
        cfg = self.task_config
        if not getattr(cfg, "is_exec_print_end_action", False):
            return False
        try:
            table = cast("dict[str, list[bool]]", getattr(cfg, "print_task_config", None))
            return bool(
                table["extruders_used"][head]
                and not table["filament_soft"][head]
                and table["end_unload_filament"][head]
            )
        except (TypeError, KeyError, IndexError):
            return False

    # --- the reads of the units and the status lines --------------------------------------------

    def read_units(self, eventtime: float) -> dict[int, list[LaneView]]:
        """Return {id(unit): the lanes of its status} for every unit in use, each read once.

        A unit whose status cannot be read is left out, and this is logged once per episode. The
        reads of the bay watch and of ACEK_ADAPTER_STATUS count together, and a read that succeeds
        ends the episode.
        """
        units: dict[int, list[LaneView]] = {}
        for u in self.used:
            try:
                units[id(u)] = u.get_status(eventtime)["lanes"]
            except Exception:
                if id(u) not in self.status_unread:
                    self.status_unread.add(id(u))
                    logging.exception("ace2k_u1: %s status unread", self.units.label(u))
                continue
            self.status_unread.discard(id(u))
        return units

    def status_lines(self, say: Callable[[str], object]) -> None:
        """Pass the lines of ACEK_ADAPTER_STATUS, one per hooked lane, to ``say``.

        A line shows the bay, the state of the U1, the head sensor, the wheel, the tag of the lane
        and what the tag last did to the head. The status of each unit is read on its own, as the
        bay watch reads it. The heads of an unreadable unit say so.
        """
        units = self.read_units(self.reactor.monotonic())
        if self.tag_writer.tags_off:
            say(f"ace2k_u1: tags off: {self.tag_writer.tags_off}")
        for h in sorted(self.hooked, key=lambda h: h.head):
            if id(h.unit) not in units:
                say(
                    f"lane {self.lane_text(h)} -> e{h.head}: {self.units.label(h.unit)}'s status "
                    "could not be read"
                )
                continue
            sensor = head_switch(h.ff.runout_sensor[h.ch], 0)
            wheel = cast("EncoderWheel", h.wheel)  # it is set at the hook (hook_feed)
            say(
                f"lane {self.lane_text(h)} -> e{h.head}: bay={'yes' if h.present else 'no'} "
                f"state={h.ff.channel_state[h.ch]} head_sensor={sensor} "
                f"wheel={wheel.get_counts()} follow={'wanted' if h.follow_wanted else 'off'} "
                f"behind={f'e{h.head}' if h.waits_behind else 'no'} "
                f"grip={self.grip_watch.grip_text(h)} "
                f"tag={tags_module.TagWriter.tag_summary(units[id(h.unit)][h.lane].get('tag'))} "
                f"applied={self.tag_writer.applied.get(h.key, 'none')}"
            )
