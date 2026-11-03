"""Connects the side-feeder flows of the Snapmaker U1 to an ace2k unit.

Use it when each head of the U1 is fed by one lane of an Anycubic ACE 2 Pro running ace2k.
The U1 then does its load, unload, preload, print-start feed, print-end unload, runout detection
and tangle detection through its side-feeder module (klippy/extras/filament_feed.py). That module
expects filament at the inlet of the side feeder.

This module attaches to the side-feeder module at run time, after klippy:ready. It uses instance
attributes only, so no file of the printer is replaced. It takes over these parts.

  inlet sensor           -> the lane's insert sensor (printer.ace2k lanes[i].insert)
  push to the head       -> an ace2k feed that stops at the head's filament sensor
  side wheels            -> a counter fed by the lane's filament encoder. While the head holds
                            a piece that is not the lane's filament, the head's extruder feeds
                            the counter instead. Then the U1's tangle check does not see a
                            waiting filament as stalled.
  channel states         -> the same states, plus the follow. The follow is the unit's two-way
                            assist. It keeps the lane's buffer at rest, whichever way the
                            extruder moves the filament. It runs from the first extrusion to
                            unload_finish, which brings the eject after an unload.
  inlet insert / remove  -> a watch on the lane's insert sensor. It raises the U1's port events.
                            The U1's own inlet sensor is empty, and it is no longer passed on.
  extrude retry nudge    -> in load_extruding the U1 pulses its side motor forward, but that
                            motor is not in the path of the filament. The pulse becomes a short
                            feed of the lane. The follow is stopped before it and armed again
                            after it.

Everything is checked first. If any hook point differs from what this module expects (for
example after an update of the U1), nothing is hooked and the console says why.

The module uses only the public interface of ace2k.

- The status of each unit.
- The "ace2k:feed_event" event, matched to a head by its unit and lane.
- The host interface on each unit's object.
- The coordinator ace2k_units (the units, UNIT=).
- The ACEK_CLEAR G-code of ace2k. The module wraps it the Klipper way, so that it also releases a
  lane that waits behind a piece in its head.

Config:
    [ace2k_u1]
    #lanes: 1, 2, 3, 4        # the lanes wired one-to-one to the heads (lane n -> extruder n-1);
                              # with several units, one <unit>:<lane> per head, in head order:
                              # a:1, a:2, b:1, b:2 (head 0 <- unit a lane 1 ... head 3 <- b 2)
    #feed_speed: 70           # mm/s, the speed of the push toward a head and of the follow
                              # (chosen from measurements on the unit)
    #head_budget_mm: 2000     # the longest push; the head's sensor must see the filament before
                              # it ends
    #poll_ms: 200             # the period of the bay watch
    #tag_push_speed: 70       # mm/s, a push to a head that one lane feeds while the lane's tag is
                              # not read: the unit's tag search is stopped, and the push runs at
                              # this speed for the rest of the search's reach, or at feed_speed if
                              # that is slower (0: the search runs and returns to the parking point
                              # first)
    #retry_push_mm: 10        # mm (1-40). The lane feeds this far in place of the extrude-retry
                              # pulse of the U1. It pushes the filament's tip into the extruder's
                              # gears.
    #apply_tags: True         # A spool tag read by the unit sets its head's filament on the U1
                              # (vendor, type, subtype, colour). The setting is cleared when the
                              # spool leaves, while the head is idle.
    #runout_source: head      # head: when a filament leaves the bay during the follow, the follow
                              # feeds it on as its tail. The head's sensor pauses the print at
                              # the end of the tail.
                              # unit: the end of a spool is taken at the unit's bay sensor, as
                              # in a head that several lanes feed (below).
                              # A head that several lanes feed handles the end of a spool at the
                              # unit's bay sensor, whatever runout_source says. The lane
                              # stops, the print pauses plainly, the head is unloaded, and the
                              # rest of the filament rolls out of the bay. With a new spool,
                              # RESUME pushes and loads the same lane.
    #grip_mm: 40              # mm, within the bounds of the unit (10-45). If absent, the default
                              # applies (40). It is the distance that the unit pulls a filament
                              # inserted while its head still holds a piece that the lane does
                              # not own: on a head that several lanes feed, or on any head with an
                              # image without the wait. On a head that one lane feeds, an image
                              # with the wait pulls nothing; the U1's load takes the filament.
    #eject_probe_mm: 80       # mm (20-150). ACEK_EJECT, with the sensor of the head set, first
                              # rolls the filament back up to this far. It stops when the lane's
                              # buffer plunger reads taut. The distance should be past the slack
                              # that a follow leaves (about 50 mm).
    #eject_probe_speed: 10    # mm/s (5-30, within the speeds of the unit): the speed of that
                              # rollback
    #eject_settle_ms: 500     # ms (200-2000). After the probe, the time to wait for the lane's
                              # buffer plunger to settle and be reported. At rest means the
                              # filament is free, and the unit unloads it. Moved means the head's
                              # gear holds it, and the U1 unloads the head first.

ACEK_EJECT [UNIT=u] LANE=n [WAIT=0|1] takes the filament of a lane out of its bay. UNIT= is
needed with several units. The command is refused, and nothing moves, if the lane is in use by a
print, busy, in error, without filament, or already ejecting.

If the head's sensor is clear, the follow is turned off and then the unit unloads the filament.

If the sensor is set, a rollback probes the filament. It rolls back far enough to take up the
slack between the unit and the head, and it stops when the lane's buffer plunger reads taut.
That result is "held". At the end of the probe, the plunger is read once it has settled.
- At rest: the filament is free, and the unit unloads it as above.
- Moved: the head's gear holds the filament. The same happens if the probe ended in a lane
  error. The filament's travel in the probe is fed back. Then the U1 starts its own unload of
  that head (AUTO_FEEDING ... UNLOAD=1, prepare and then doing, as its screen and its
  print-end unload run it). The unload_finish of that unload brings the eject.

There is a safety net, used once per eject. Suppose the unit unloads after a "free" probe, but
the filament ends stuck within EJECT_NET_MM and the head's sensor is still set. Then the
eject takes the held path. The lane is cleared, the filament that left is fed back, and the U1
unloads.

WAIT=1 waits for the decision (for a held filament, after the feed back). It never waits for the
unload of the U1. Everything runs from a reactor timer.

There are two kinds of pause.

- A lane error in the follow during a print (stuck, tangled, timeout, motor_stalled) pauses the
  print through the U1's tangle pause for the lane's head.
- The end of a spool during a print, in a head that several lanes feed, and with
  runout_source: unit in a head of one lane too. The tail notice stops the lane at once. Then
  the pause timer pauses the print plainly, unloads the head and rolls the rest of the lane out
  of its bay. RESUME, with a new spool in
  that bay, pushes, loads and finishes the lane as a swap.

All pauses run from a reactor timer, never from the event handler.

A loaded head keeps its follow on. Once the adapter arms it, the lane's follow is wanted until
one of these happens.

- A failure.
- The end of the unload.
- The filament leaves its bay outside the tail.
- The end of the tail.
- A stop by hand. That is a stopped event whose stop_by is not "host". A stop that this adapter
  or ace2k sent for its own reasons is never one.
A wanted lane that is found idle, with no error and with a filament, is armed again (once per
idle episode). The resume of the U1 clears the error of a wanted lane so that this can happen.
Only one start per lane is in flight at a time. A trigger during the round trip of an arm is
dropped.

The first arm of the follow belongs to ace2k (its follow_default, host interface 8). At
klippy:connect the adapter asks ace2k to arm the hooked lanes at feed_speed and to leave their
insertions to the adapter (set_lane_follow). Then ace2k arms every lane with filament at the
start. The bay watch adopts
that follow (default_move) as one that it armed itself. The hook re-derives the states of the U1,
and that arms nothing.

A new insertion is the preload of the U1. The lane is armed once the preload has brought the
filament to the head. For a channel outside the auto mode of the U1, it is armed at once. A push
toward a head stops a follow on the lane first. With follow_default off, the adapter arms the
lanes as it did before that option existed. The re-derivation arms a loaded head, and the end of
the preload does not. When the adapter is not hooked, the insertions go back to ace2k.

The bay is ignored during the tail. A filament put in during the tail rests behind the old end,
not at the motor's gear, and nothing loads it. While a lane is in its tail, the U1 is not told
that its bay emptied. That port event is held. It is sent when the tail ends, at its tail_out or
at the stop when the end of the filament passes the head's sensor. At every tail_out, one
console line tells the operator that a new filament can go in. If a filament was put in during
the tail, the line tells the operator to pull it out and push it in again until the gear takes
it.

The U1 is never told of a filament in the bay while its head's sensor sees filament. It
would preload against the loaded head, which fails on the stock U1 too. This holds after a
tail_out, after a tangle, and at any other time.

- Outside a print, that port event waits until the head's sensor clears.
- In a print, it is dropped. It is also dropped after a tail_out, even if the head is already
  clear. The filament then comes through the runout path of the U1 (its replenish load) or
  through a load from its screen.

A load of the head by the U1 ends the hold.

The grip is for a filament that the lane does not own. Sometimes a head's sensor sees such a
filament. This happens after a tail_out, after a tangle, and when the filament of a loaded
head was taken out of its bay. Then the grip of the lane is set. A filament inserted at that
time is only pulled until the gear holds it (grip_mm, no tag search), and it waits there. The
old piece is never touched. The runout path of the U1 (its replenish) pushes the new filament
to the head once the old piece has run out. The grip is cleared once that head's sensor
clears. It is never set while the lane owns the head, that is, while its follow is armed,
while it is in its tail, or while the channel is loaded.

On a head that one lane feeds, a unit whose image has the wait gets the wait instead of the
grip: it pulls nothing, and the filament waits in the bay. The U1's load then starts with the
unit's load from the bay, so the gear takes the tip, and the push goes on from there.

The unit reads a tag only while its lane moves, so a gripped or waiting spool's tag is not asked
for. At the end of the gripped load its tag is watched for 5 s from a reactor timer. If the
grip's move read it, the tags apply it like any read tag. If not, a console line says it is read
when it moves, and the lane stays pending. Under the wait no load runs: the console says so at
once when the filament goes in.

A preload whose push meets something that cannot move (the unit says "blocked") is not a
failure. The channel goes back to wait_insert and the filament waits. A "blocked" push in a head
load stays a failure of the U1, because a load must not hang.

The tag of each hooked lane, read by the unit, becomes its head's filament setting. This goes
through the U1's public command SET_PRINT_FILAMENT_CONFIG. TagPolicy in ace2k_u1_tags makes the
decisions, and the G-code runs from a reactor timer. This is checked first, like the hooks. If
the command of the U1 or its filament table differ, the tags are off and the console says why.
The feed hooks are not affected.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import FeedEvent
    from .ace2k_u1_adapter import HeadLane
    from .ace2k_u1_common import ExtruderPosition, Hooked
    from .ace2k_u1_config import LaneExtruder, LaneRef, SwapConfig
    from .ace2k_u1_printer_types import (
        Config,
        Coordinator,
        Extruder,
        FilamentFeed,
        GCode,
        GCodeCommand,
        Printer,
        Reactor,
        Unit,
        UnitFeed,
    )
    from .ace2k_u1_probe import HeadProbe
    from .ace2k_u1_status import LaneName
    from .ace2k_u1_tracker import Plan

try:
    # This is a module of the U1, not of this tree, so the type checker cannot find it.
    from . import filament_feed as feed_module  # type: ignore[attr-defined]
except ImportError:  # the host tests put klippy/extras and a fake filament_feed on the path
    import filament_feed as feed_module
try:
    from . import ace2k_u1_config as config_module
    from . import ace2k_u1_probe
    from .ace2k_u1_adapter import PRINT_ON, Adapter, AdapterSetup
    from .ace2k_u1_bench import Bench
    from .ace2k_u1_common import (
        API_MIN,
        HOLD_WHY_API,
        HostCallbacks,
        api_at_least,
        held_floor_problem,
        unit_prefix,
    )
    from .ace2k_u1_config import LANES, SPLITTER_DEFAULT_MM
    from .ace2k_u1_eject import EJECT_PROBE_MIN_MM, EJECT_PROBE_MM, EjectRefused
    from .ace2k_u1_material import MaterialGate
    from .ace2k_u1_page import PageCommands
    from .ace2k_u1_pause import PauseObjects
    from .ace2k_u1_printer import U1Printer
    from .ace2k_u1_refill import Refiller
    from .ace2k_u1_splitter import SplitterCommand
    from .ace2k_u1_spools import LaneSpools, SpoolStore
    from .ace2k_u1_status import Status
    from .ace2k_u1_swap import Swapper
    from .ace2k_u1_temps import TEMP_ENDPOINT, TempCheck
    from .ace2k_u1_tracker import PrintTracker
    from .ace2k_u1_wraps import Wraps
except ImportError:  # same as above, for the host tests
    import ace2k_u1_config as config_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_probe  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_adapter import (  # type: ignore[import-not-found, no-redef]
        PRINT_ON,
        Adapter,
        AdapterSetup,
    )
    from ace2k_u1_bench import Bench  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        API_MIN,
        HOLD_WHY_API,
        HostCallbacks,
        api_at_least,
        held_floor_problem,
        unit_prefix,
    )
    from ace2k_u1_config import (  # type: ignore[no-redef]
        LANES,
        SPLITTER_DEFAULT_MM,
    )
    from ace2k_u1_eject import (  # type: ignore[import-not-found, no-redef]
        EJECT_PROBE_MIN_MM,
        EJECT_PROBE_MM,
        EjectRefused,
    )
    from ace2k_u1_material import MaterialGate  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_page import PageCommands  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_pause import PauseObjects  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_printer import U1Printer  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_refill import Refiller  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_splitter import SplitterCommand  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_spools import (  # type: ignore[import-not-found, no-redef]
        LaneSpools,
        SpoolStore,
    )
    from ace2k_u1_status import Status  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_swap import Swapper  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_temps import (  # type: ignore[import-not-found, no-redef]
        TEMP_ENDPOINT,
        TempCheck,
    )
    from ace2k_u1_tracker import PrintTracker  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_wraps import Wraps  # type: ignore[import-not-found, no-redef]
# The spool of each lane (ACEK_SPOOL), saved beside printer.cfg as
# {"<unit>:<lane>": {vendor, type, color}}.
SPOOL_FILE = "ace2k_u1_spools.json"
# Said once at the start, with runout_source: head and a + group.
SHARED_RUNOUT_LINE = (
    "ace2k_u1: heads several lanes feed take a spool's end at the unit's bay sensor, whatever "
    "runout_source says"
)
# How _end_unload tries again an eject that was refused while the U1 still unloads that head.
# It tries again this often, for this long.
END_UNLOAD_RETRY_S = 5.0
END_UNLOAD_WAIT_S = 120.0
# The refusals of ejector.eject_refusal that pass on their own. The end unload waits them out.
END_UNLOAD_WAIT_WHY = (
    "an eject is already running",
    "the U1 is loading/unloading this head",
    "the lane is busy",
)

# Seconds after klippy:ready. The U1 sets module_exist from its inlet ADC at 0.6 s.
HOOK_DELAY_S = 1.0
# A lane outside lanes: feeds no head, so ace2k's follow_default is kept off it. From ace2k host
# interface HOLD_WHY_API on, the hold carries this reason, which ace2k's ACEK_STATUS shows.
HELD_WHY = "not in [ace2k_u1] lanes"
TAG_PUSH_SPEED_MIN = 9.0  # mm/s: the least speed a unit takes
# For a unit whose image does not read free_tip on a rollback (has_free_rollback of ace2k).
# The roll back of a swap then forgives one catch, as it did before.
NO_FREE_ROLLBACK_LINE = (
    "the unit's firmware forgives one catch in a lane swap's roll back, not every one; flash "
    "v0.12.0"
)
RUNOUT_SOURCES = ("head", "unit")


def extruder_names(extruders: Iterable[Extruder] | None) -> dict[int, str]:
    """Maps the head index to the section name of the extruder of that head.

    The printer object "extruder_list" of the U1 holds one extruder object per head. Each carries
    its index (extruder_num) and its section name (name).
    """
    return {e.extruder_num: e.name for e in extruders or ()}


class ACE2kU1:
    """The [ace2k_u1] section of the config.

    Each attribute that a helper reads has its type declared here. The helpers see the host
    through their Protocols. These are checked against the types declared here, when the helpers
    are built. An attribute typed only later, by inference, would pass unchecked.
    """

    def __init__(self, config: Config) -> None:
        self.printer: Printer = config.get_printer()
        self.reactor: Reactor = self.printer.get_reactor()
        self.lane_refs: list[LaneRef] = config_module.parse_lanes(config)
        # The laneN_extruder keys of the + groups. They are checked against the extruders of the
        # U1 once these are known, at klippy:connect (_check_extruders).
        self.config_error: type[Exception] = config.error
        counts = config_module.lane_counts(self.lane_refs)  # lanes per head, used by both readers
        self.lane_extruder_keys: list[LaneExtruder] = config_module.read_lane_extruders(
            config, self.lane_refs, counts
        )
        self.extruders_checked: bool = False
        self.swap_cfg: SwapConfig = config_module.read_swap_keys(config, counts)
        # The temperature range of each material, for the warning of the temperature check.
        self.temp_ranges: dict[str, tuple[float, float]] = config_module.read_temp_ranges(config)
        # For the web page (get_status) while the adapter is not hooked. Per head, the lanes that
        # the lanes: key names, each as a unit (its name, or None for the only unit) and a lane
        # (1-4). None for a head that no lane feeds. Fixed at start.
        self.heads_status: list[list[LaneName] | None] = [None] * LANES
        for head, name, lane in self.lane_refs:
            self.heads_status[head] = (self.heads_status[head] or []) + [
                {"unit": name, "lane": lane + 1}
            ]
        self.feed_speed: float = config.getfloat("feed_speed", 70.0, above=0.0)
        self.head_budget_mm: float = config.getfloat("head_budget_mm", 2000.0, above=0.0)
        self.poll_s: float = config.getint("poll_ms", 200, minval=50, maxval=1000) / 1000.0
        self.retry_push_mm: float = config.getfloat("retry_push_mm", 10.0, minval=1.0, maxval=40.0)
        self.tag_push_speed: float = config.getfloat("tag_push_speed", 70.0, minval=0.0)
        if 0.0 < self.tag_push_speed < TAG_PUSH_SPEED_MIN:
            raise config.error(
                f"ace2k_u1: tag_push_speed must be 0 or at least {TAG_PUSH_SPEED_MIN:g} mm/s"
            )
        self.apply_tags: bool = config.getboolean("apply_tags", True)
        self.runout_source: str = config.get("runout_source", "head").strip().lower()
        if self.runout_source not in RUNOUT_SOURCES:
            raise config.error("ace2k_u1: runout_source must be head or unit")
        # Checked against the bounds of the unit at the hook, once ace2k has read them. If absent,
        # the default of the unit applies (from its dictionary, read by ace2k at connect).
        self.grip_mm: float | None = config.getfloat("grip_mm", None, above=0.0)
        self._read_eject_probe(config)
        self.adapter: Adapter | None = None
        # Reads the state of the U1 itself.
        self.u1: U1Printer = U1Printer(lambda: self.printer, lambda: self.reactor)
        # The probe of a shared head (ace2k_u1_probe), built at the hook.
        self.probe: HeadProbe | None = None
        # Why nothing was hooked, when that was known before an adapter could be built.
        self.problems: list[str] | None = None
        # Whether "ace2k:feed_event" is registered. It is registered once and routed to the adapter.
        self.feed_events: bool = False
        self.printer.register_event_handler("klippy:connect", self._on_connect)
        self.printer.register_event_handler("klippy:ready", self._on_ready)
        gcode: GCode = self.printer.lookup_object("gcode")
        self._register_commands(gcode)
        # The status of the printer object, for the web page. Its copies are built again after a
        # change.
        self.status: Status = Status(self)
        self.spool_store: SpoolStore = SpoolStore(self._spool_path(), self.changed)
        self.spool_lanes: LaneSpools = LaneSpools(self, self.spool_store)
        self.tracker: PrintTracker = PrintTracker(self)
        # The lane swap, its pending state and the T commands it owes.
        self.swapper: Swapper = Swapper(self)
        # The check of a lane's material before its load, its eject and a print.
        self.material: MaterialGate = MaterialGate(self)
        # The end of a spool in a shared head, and the runouts it stopped.
        self.refiller: Refiller = Refiller(self)
        # The wrapped U1 G-codes, the checks at a print start, RESUME and the owed T commands.
        self.wraps: Wraps = Wraps(self)
        # The warning for a temperature outside the range of the material of the lane.
        self.temps: TempCheck = TempCheck(self)
        self.bench: Bench = Bench(self)  # ACEK_HEAD_PROBE and ACEK_FREE_CHECK
        self.splitter: SplitterCommand = SplitterCommand(self, self.bench, config.get_name())
        # The commands ACEK_SPOOL, ACEK_TAG_FORGET, ACEK_PLAN and ACEK_PLAN_SET.
        self.page: PageCommands = PageCommands(self)
        # True once a print has been seen printing or paused, planned or not. Its end clears what
        # it left pending.
        self.print_seen_on: bool = False
        self._register_page_commands(gcode)

    def _read_eject_probe(self, config: Config) -> None:
        """Reads the probe settings of ACEK_EJECT.

        A value of eject_probe_mm below its minimum is raised to the minimum, with a warning.
        The warning is kept in warnings and said again at the hook.
        """
        # The probe of ACEK_EJECT on a filament in a loaded head, 20-150 mm.
        # A value of the old range (5-30) below 20 is raised to 20, with a warning. The warning is
        # said again on the console at the hook, so an existing config keeps starting.
        self.eject_probe_mm: float = config.getfloat(
            "eject_probe_mm", EJECT_PROBE_MM, minval=5.0, maxval=150.0
        )
        self.warnings: list[str] = []
        if self.eject_probe_mm < EJECT_PROBE_MIN_MM:
            warning = (
                f"ace2k_u1: eject_probe_mm {self.eject_probe_mm:g} is below its minimum "
                f"{EJECT_PROBE_MIN_MM:g} — {EJECT_PROBE_MIN_MM:g} used"
            )
            logging.warning(warning)
            self.warnings.append(warning)
            self.eject_probe_mm = EJECT_PROBE_MIN_MM
        # A value below the unit's own floor (9 mm/s on ace2k's images) is accepted here. The
        # adapter is then not hooked: the unit refuses that speed.
        self.eject_probe_speed: float = config.getfloat(
            "eject_probe_speed", 10.0, minval=5.0, maxval=30.0
        )
        self.eject_settle_ms: int = config.getint("eject_settle_ms", 500, minval=200, maxval=2000)

    def _register_commands(self, gcode: GCode) -> None:
        """Registers the G-codes of the adapter: ACEK_ADAPTER_STATUS, ACEK_EJECT, bench commands."""
        gcode.register_command(
            "ACEK_ADAPTER_STATUS",
            self.cmd_ACEK_ADAPTER_STATUS,
            desc="Report what the ace2k U1 adapter hooked",
        )
        gcode.register_command(
            "ACEK_EJECT",
            self.cmd_ACEK_EJECT,
            desc="Take LANE's filament (of UNIT, with several units) out of its bay, unloading its"
            " head on the U1 first when the head holds it; WAIT=0 returns at once",
        )
        gcode.register_command(
            "ACEK_HEAD_PROBE",
            self.cmd_ACEK_HEAD_PROBE,
            desc="Run a print start's probe of shared HEAD alone (its sensor set, the head cold);"
            " with the sensor empty, push LANE (of UNIT) to it as a swap does",
        )
        gcode.register_command(
            "ACEK_SPLITTER_MEASURE",
            self.cmd_ACEK_SPLITTER_MEASURE,
            desc="Measure how far a lane of shared HEAD rolls back to leave its splitter, and stage"
            " headN_splitter_mm for SAVE_CONFIG",
        )
        gcode.register_command(
            "ACEK_FREE_CHECK",
            self.cmd_ACEK_FREE_CHECK,
            desc="Run a swap's free check on LANE (of UNIT) alone: roll it back up to LENGTH at"
            " SPEED, watching its plunger; says free or held",
        )

    def _spool_path(self) -> str | None:
        """Returns the path of SPOOL_FILE, beside printer.cfg.

        The spool of each lane (ACEK_SPOOL) is kept in that file. Returns None without the path
        of the config (the host tests). The spools are then kept only in memory.
        """
        # The start arguments of Klipper (config_file is a path). The host tests have none.
        start: dict[str, str] = getattr(self.printer, "get_start_args", lambda: {})()
        cfg = start.get("config_file")
        cfg_dir = os.path.dirname(os.path.abspath(cfg)) if cfg else None
        return os.path.join(cfg_dir, SPOOL_FILE) if cfg_dir else None

    def _register_page_commands(self, gcode: GCode) -> None:
        """Registers the G-codes that the web page sends (PageCommands)."""
        gcode.register_command(
            "ACEK_SPOOL",
            self.cmd_ACEK_SPOOL,
            desc="Set the spool of LANE (of UNIT): TYPE=, COLOR=rrggbb, VENDOR=",
        )
        gcode.register_command(
            "ACEK_TAG_FORGET",
            self.cmd_ACEK_TAG_FORGET,
            desc="Forget the tag read on a lane, without reading it again until its spool leaves"
            " the bay",
        )
        gcode.register_command(
            "ACEK_HEAD_SPOOL",
            self.cmd_ACEK_HEAD_SPOOL,
            desc="Set the filament of the head that LANE (of UNIT) is in from the lane's spool,"
            " as a swap does: its tag, else its ACEK_SPOOL record",
        )
        gcode.register_command(
            "ACEK_HEAD_PREPARE",
            self.cmd_ACEK_HEAD_PREPARE,
            desc="Before Load into head N: push LANE (of UNIT) to its head's sensor to read its tag"
            " when its material is not set; refused when it stays unset",
        )
        gcode.register_command(
            "ACEK_PLAN", self.cmd_ACEK_PLAN, desc="Read FILE's tools and suggest a lane for each"
        )
        gcode.register_command(
            "ACEK_PLAN_SET",
            self.cmd_ACEK_PLAN_SET,
            desc="Keep FILE's tool -> lane choice (T0=a:1 T1=b:3 ...) for its print",
        )

    def _check_extruders(self) -> str | None:
        """Checks the extruder keys against the extruder_list of the U1 (check_lane_extruders).

        Returns the text of the problem, or None. It also returns None when the list is not known
        yet. The hook checks again then.
        """
        if self.extruders_checked or not self.lane_extruder_keys:
            return None
        names = extruder_names(self.printer.lookup_object("extruder_list", None))
        if not names:
            return None
        self.extruders_checked = True
        return config_module.check_lane_extruders(self.lane_extruder_keys, names)

    def units(self) -> Coordinator | None:
        """Returns the coordinator of ace2k (ace2k_units).

        Returns None without it. That means there is no ace2k, or one from before several units
        were supported (host interface older than 9). The adapter needs API_MIN (12) anyway.
        """
        units: Coordinator | None = self.printer.lookup_object("ace2k_units", None)
        return units

    def _resolve(self, units: Coordinator) -> tuple[list[HeadLane], list[str]]:
        """Resolves the lanes: key against the loaded units.

        Returns the list of (head, unit, lane) and the list of problems. Each problem is listed
        once.
        """
        resolved, problems = [], []
        for head, name, lane in self.lane_refs:
            unit = units.find(name)
            if unit is None:
                if name is None:
                    why = "lanes: a bare lane needs <unit>:<lane> with several units"
                else:
                    why = f"lanes: no unit {name}"
                if why not in problems:
                    problems.append(why)
                continue
            resolved.append((head, unit, lane))
        return resolved, problems

    def _on_connect(self) -> None:
        """Takes the insertions of this adapter's lanes from ace2k, before follow_default acts.

        The follow_default of ace2k looks at the lanes from klippy:ready on. The lanes that this
        adapter drives are armed at feed_speed, and only at the start. An insertion on them is the
        preload of this adapter, which arms the lane at its end. An extruder key of a + group lane
        that does not match its head stops Klipper here, with a config error.
        """
        problem = self._check_extruders()
        if problem is not None:
            raise self.config_error(problem)
        self._take_insertions(True)

    def _take_insertions(self, take: bool) -> None:
        """Takes the configured lanes' insertions from ace2k's follow_default, or gives them back.

        With take set, ace2k leaves the insertions to this adapter. They are given back when
        nothing is hooked. ace2k then arms them as on any printer. Never raises.
        """
        units = self.units()
        if units is None or not getattr(units, "units", None):
            return  # the hook says why
        resolved, problems = self._resolve(units)
        if take and problems:
            return  # not hooked: the hook says why, nothing taken
        for _head, unit, lane in resolved:
            # A unit without its feed reads API_VERSION 0 just below, so it is skipped.
            feed = cast("UnitFeed", getattr(unit, "feed", None))
            if not api_at_least(feed, API_MIN):
                continue  # the hook says why
            try:
                if take:
                    feed.set_lane_follow(lane, self.feed_speed, on_insert=False)
                else:
                    feed.set_lane_follow(lane, None, on_insert=True)
            except Exception:
                logging.exception("ace2k_u1: lane %s follow_default", units.lane_text(unit, lane))
        for unit, lane in self._unwired(units, resolved):
            feed = unit.feed
            try:
                if take and api_at_least(feed, HOLD_WHY_API):
                    feed.hold_follow(lane, True, HELD_WHY)
                else:
                    feed.hold_follow(lane, take)
            except Exception:
                logging.exception("ace2k_u1: lane %s hold", units.lane_text(unit, lane))

    @staticmethod
    def _unwired(
        units: Coordinator, resolved: list[tuple[int, Unit, int]]
    ) -> list[tuple[Unit, int]]:
        """Return the lanes of every unit that lanes: does not wire to a head, as (unit, lane).

        A unit without its feed, or with a host interface under API_MIN, has none: the hook says
        why.
        """
        wired = {(id(unit), lane) for _head, unit, lane in resolved}
        return [
            (unit, lane)
            for unit in getattr(units, "units", [])
            if api_at_least(getattr(unit, "feed", None), API_MIN)
            for lane in range(LANES)
            if (id(unit), lane) not in wired
        ]

    def _stop_unwired(
        self, gcode: GCode, units: Coordinator, resolved: list[tuple[int, Unit, int]]
    ) -> None:
        """Stop the follow of a lane outside lanes: that still follows (one from before a restart).

        The hold at connect keeps follow_default from arming such a lane, but a follow that the
        unit kept running goes on until it is stopped. Never raises.
        """
        for unit, lane in self._unwired(units, resolved):
            text = units.lane_text(unit, lane)
            try:
                if unit.get_status(self.reactor.monotonic())["lanes"][lane].get("mode") != (
                    "following"
                ):
                    continue
                unit.feed.stop(lane)
            except Exception:
                logging.exception("ace2k_u1: lane %s stop", text)
                continue
            gcode.respond_info(
                f"ace2k_u1: lane {text} feeds no head (not in lanes:): its follow stopped"
            )

    def _on_ready(self) -> None:
        self.reactor.register_timer(self._hook, self.reactor.monotonic() + HOOK_DELAY_S)

    def _hook(self, _eventtime: float) -> float:
        gcode: GCode = self.printer.lookup_object("gcode")
        found = self.units()
        feeds: list[FilamentFeed] = [
            obj for name, obj in self.printer.lookup_objects("filament_feed")
        ]
        resolved, problems = self._hook_problems(found, feeds)
        if problems:
            return self._not_hooked(gcode, problems)
        units = cast("Coordinator", found)  # no problem: ace2k's coordinator, with its units
        self.adapter = self._build_adapter(units, feeds, resolved, gcode)
        problems = self.adapter.hook_all()
        if problems:
            return self._not_hooked(gcode, problems)
        # The probe of a shared head. It is built only when several lanes feed a head.
        self.probe = None
        if self.adapter.groups():
            self.probe = ace2k_u1_probe.HeadProbe(
                self.adapter, self.reactor, self.u1.script, self.swap_cfg
            )
        self.adapter.probe = self.probe
        self._feed_dryer_materials(self.adapter)
        # The load into a head that one lane feeds pushes again with the same rules.
        self.adapter.insister = self.probe or ace2k_u1_probe.HeadProbe(
            self.adapter, self.reactor, self.u1.script, self.swap_cfg
        )
        if not self.feed_events:
            self.feed_events = True
            self.printer.register_event_handler("ace2k:feed_event", self._on_feed_event)
        self._announce(gcode, units, resolved)
        self._stop_unwired(gcode, units, resolved)
        self.adapter.pause_runner.setup_pause(
            PauseObjects(
                self.printer.lookup_object("print_stats", None),
                self.printer.lookup_object("pause_resume", None),
                self.printer.lookup_object("exception_manager", None),
                self.printer.send_event,
                self.runout_source,
                self.printer.lookup_object("print_task_config", None),
            )
        )
        self._install_wraps(gcode)
        self.adapter.tag_writer.setup_tags(
            self.printer.lookup_object("print_task_config", None),
            self.printer.lookup_object("filament_parameters", None),
            self.printer.lookup_object("print_stats", None),
            self.apply_tags,
            self.printer.lookup_object("toolhead", None),
        )
        self.reactor.register_timer(self.adapter.watch, self.reactor.NOW)
        return self.reactor.NEVER

    def _feed_dryer_materials(self, adapter: Adapter) -> None:
        """Give each unit's dryer mode the material of its lanes from Edit spool.

        The profile of the automatic cycles is then the most fragile material in the box. A lane
        that no head uses is left to the tag (None). An older ace2k has no dryer mode. The profile
        asks every few seconds, so a spool that cannot be read is logged once per lane, until it
        reads again (LaneSpools.material's once).
        """
        for unit in {id(h.unit): h.unit for h in adapter.hooked}.values():
            mode = getattr(unit, "dryer_mode", None)
            if mode is None:
                continue
            by_lane = {h.lane: h for h in adapter.hooked if h.unit is unit}

            def source(lane: int, by_lane: dict[int, Hooked] = by_lane) -> str | None:
                h = by_lane.get(lane)
                return None if h is None else self.spool_lanes.material(h, once=True) or None

            mode.set_material_source(source)

    def _hook_problems(
        self, units: Coordinator | None, feeds: list[FilamentFeed]
    ) -> tuple[list[HeadLane], list[str]]:
        """Checks what the hook needs before it builds the adapter.

        Returns the lanes resolved on the loaded units, and the reasons why nothing can be hooked.
        The reasons are empty when it can. units is the coordinator. feeds are the filament_feed
        objects of the U1.
        """
        if units is None and self.printer.lookup_object("ace2k", None) is not None and feeds:
            # This is an ace2k without its coordinator. Every interface from 9 on has one.
            return [], [f"ace2k host interface older than {API_MIN}"]
        loaded = getattr(units, "units", None) or []
        if not loaded or any(not hasattr(u, "feed") for u in loaded) or not feeds:
            return [], ["[ace2k] or the U1 feed modules are absent"]
        units = cast("Coordinator", units)  # it has loaded units
        resolved, problems = self._resolve(units)
        if problems:
            return resolved, problems
        problem = self._check_extruders()  # the extruders of the U1 were not known at connect
        if problem is not None:
            # It is too late for a config error, so a shutdown stops Klipper instead.
            shutdown = getattr(self.printer, "invoke_shutdown", None)
            if shutdown is not None:
                shutdown(problem)
            return resolved, [problem]
        for unit in Adapter.units_in_use(resolved):
            problem = self._unit_problem(unit, units)
            if problem is not None:
                return resolved, [problem]
        return resolved, self._bound_problems(units, resolved)

    def _bound_problems(self, units: Coordinator, resolved: list[HeadLane]) -> list[str]:
        """Checks the speeds and lengths of the config against the bounds of each unit in use.

        The check is config_module.bound_problems. It always covers the probe speed of
        ACEK_EJECT, and the ease of a load's push again (a lone head's too). With a head that
        several lanes feed, it also covers the speeds and lengths of the swap and of the probe.
        """
        speeds = {"eject_probe_speed": self.eject_probe_speed}
        lengths: dict[str, float] = {}
        if len(resolved) > len({head for head, _unit, _lane in resolved}):  # a + group
            cfg = self.swap_cfg
            speeds.update(swap_speed=cfg.swap_speed, probe_speed=cfg.probe_speed)
            lengths["probe_mm"] = cfg.probe_mm
        # The slowest armed move: the eject's probe, the probe and the ease, or the unit's least
        # speed, which a slower free check is raised to. Its held window must keep its margin.
        armed = [self.eject_probe_speed, ace2k_u1_probe.EASE_SPEED]
        if lengths:
            armed.append(self.swap_cfg.probe_speed)
        problems: list[str] = []
        for unit in Adapter.units_in_use(resolved):
            where = unit_prefix(units, unit)
            problems += config_module.bound_problems(unit.feed, where, speeds, lengths)
            least = getattr(unit.feed, "speed_min", None)
            slowest = min([*armed, least] if isinstance(least, (int, float)) else armed)
            held = held_floor_problem(unit.feed, slowest)
            if held is not None:
                problems.append(where + held)
        return problems

    def _build_adapter(
        self,
        units: Coordinator,
        feeds: list[FilamentFeed],
        resolved: list[HeadLane],
        gcode: GCode,
    ) -> Adapter:
        """Builds the Adapter over the resolved lanes and the filament_feed objects (feeds).

        It gets the values of the config and the callbacks of the host. It is not hooked yet
        (Adapter.hook_all).
        """
        return Adapter(
            self.reactor,
            feed_module,
            feeds,
            resolved,
            AdapterSetup(
                feed_speed=self.feed_speed,
                head_budget_mm=self.head_budget_mm,
                poll_s=self.poll_s,
                retry_push_mm=self.retry_push_mm,
                tag_push_speed=self.tag_push_speed,
                extruders=extruder_names(self.printer.lookup_object("extruder_list", None)),
                grip_mm=self.grip_mm,
                extruder_position=self._extruder_position_reader(),
                eject_probe_mm=self.eject_probe_mm,
                eject_probe_speed=self.eject_probe_speed,
                eject_settle_s=self.eject_settle_ms / 1000.0,
                units=units,
                callbacks=HostCallbacks(
                    gcode=gcode,
                    lookup=self.printer.lookup_object,
                    command_error=getattr(self.printer, "command_error", RuntimeError),
                    on_bay_empty=self.spool_lanes.bay_empty,
                    on_lanes_watched=self.spool_lanes.watch_settings,
                    on_shared_runout=self.refiller.shared_runout,
                    swap_pending=lambda: self.swapper.swap_pending is not None,
                    swap_slot=lambda: (
                        None
                        if self.swapper.swap_pending is None
                        else self.swapper.swap_pending.slot
                    ),
                    refill_pending=lambda h: bool(
                        self.swapper.swap_pending is not None
                        and self.swapper.swap_pending.refill
                        and self.swapper.swap_pending.new is h
                    ),
                    on_tick=self.watch_print,
                    load_material=self.material.after_load_push,
                    material_refusal=self.material.refusal,
                ),
            ),
        )

    def _announce(self, gcode: GCode, units: Coordinator, resolved: list[HeadLane]) -> None:
        """Prints the console lines of the hook.

        First the hooked lanes, then what the config or the units leave for the operator to do.
        """
        gcode.respond_info(f"ace2k_u1: hooked lanes [{self._hooked_text(units)}]")
        for head in sorted(self.swap_cfg.splitter_default):
            gcode.respond_info(
                f"ace2k_u1: head {head + 1} rolls back the default {SPLITTER_DEFAULT_MM:g} mm: "
                f"run ACEK_SPLITTER_MEASURE HEAD={head + 1}, then SAVE_CONFIG"
            )
        for warning in self.warnings:
            gcode.respond_info(warning, log=False)  # logged once, when the config was read
        if self.runout_source == "head" and self._built().groups():
            gcode.respond_info(SHARED_RUNOUT_LINE)
        if self._refills():
            for name in self.swapper.missing_swap_macros():
                gcode.respond_info(self.wraps.missing_macro_text(name))
            for unit in Adapter.units_in_use(resolved):
                if not getattr(unit.feed, "has_free_rollback", False):
                    where = unit_prefix(units, unit)
                    gcode.respond_info(f"ace2k_u1: {where}{NO_FREE_ROLLBACK_LINE}")

    def _refills(self) -> bool:
        """Tells whether a head can refill: a head is shared, or runout_source is unit."""
        return bool(self._built().groups()) or self.runout_source == "unit"

    def _install_wraps(self, gcode: GCode) -> None:
        """Wraps the commands of the U1 and of ace2k that the adapter changes, in this order."""
        self.wraps.wrap_ace_clear(gcode)
        # Without a + group, no head has a lane to choose. With runout_source unit every head
        # refills, which is a swap of its lane for itself, so it gets the same guards.
        if self._refills():
            self.wraps.wrap_print_start(gcode)
            self.wraps.wrap_tools(gcode)
            self.wraps.wrap_preextrude(gcode)
            self.wraps.wrap_temperatures(gcode)
            self.wraps.wrap_resume(gcode)
            self.wraps.wrap_lane_active(gcode)
            self.wraps.wrap_manual_feed(gcode)
        self.wraps.wrap_end_unload(gcode)  # also with one-lane heads
        self.wraps.wrap_head_setting(gcode)  # a type written on a head is its spool's
        self.wraps.wrap_temp_checks(gcode)  # last, so it is the outermost wrap
        if not self.temps.wrap_endpoint():
            logging.warning(
                "ace2k_u1: no %s endpoint to wrap: the screen's temperature is not checked",
                TEMP_ENDPOINT,
            )

    @staticmethod
    def _unit_problem(unit: Unit, units: Coordinator) -> str | None:
        """Returns why a unit in use cannot be driven, or None.

        The reason starts with the name of the unit when there are several units.
        """
        where = unit_prefix(units, unit)
        feed = unit.feed
        if getattr(feed, "move_max_mm", None) is None:
            # The firmware was built without the feed. The host module loads but has no lane
            # bounds.
            return f"{where}the ace2k firmware has no feed support"
        if api_at_least(feed, API_MIN) and not getattr(feed, "has_follow", False):
            # This is known at connect, before klippy:ready. An image without it would refuse
            # every arm.
            return f"{where}the unit's firmware has no follow tail; flash v0.11.0"
        if api_at_least(feed, API_MIN) and not getattr(feed, "has_grip", False):
            # Same as the follow. A filament put in behind an old piece would be chased by a full
            # load.
            return f"{where}the unit's firmware has no grip; flash v0.11.0"
        return None

    def _built(self) -> Adapter:
        """Returns the adapter. It is built at the hook, before anything that reads it runs."""
        return cast("Adapter", self.adapter)

    def sensor_text(self, h: Hooked) -> str:
        """Returns the log line for the head sensor of a lane that could not be read."""
        return f"ace2k_u1: lane {self._built().lane_text(h)} head sensor"

    def head_set_or(
        self, h: Hooked, default: bool, unread: str | Callable[[], str] | None = None
    ) -> bool:
        """Tells whether the sensor of the head sees filament.

        Returns default when the sensor cannot be read. If unread is given, it is logged then.
        It is a message, or a function that builds the message only when needed. Without unread,
        nothing is logged.
        """
        try:
            return bool(self._built().head_set(h))
        except Exception:
            if unread is not None:
                logging.exception(unread() if callable(unread) else unread)
            return default

    def _extruder_position_reader(self) -> ExtruderPosition | None:
        """Returns a function from the head index to the position of the extruder of that head.

        It reads the position the way the tangle check of the U1 does: find_past_position at the
        estimated print time of the MCU. Returns None without the objects it needs.
        """
        extruders: dict[int, Extruder] = {
            e.extruder_num: e for e in self.printer.lookup_object("extruder_list", None) or ()
        }
        mcu = self.printer.lookup_object("mcu", None)
        if not extruders or mcu is None:
            return None

        def position(head: int) -> float | None:
            extruder = extruders.get(head)
            if extruder is None:
                return None
            return extruder.find_past_position(mcu.estimated_print_time(self.reactor.monotonic()))

        return position

    def _not_hooked(self, gcode: GCode, problems: list[str]) -> float:
        """Says why nothing is hooked and keeps the reason for ACEK_ADAPTER_STATUS.

        Ends the hook timer.
        """
        self.problems = problems
        msg = "ace2k_u1: not hooked: " + "; ".join(problems)
        logging.warning(msg)
        gcode.respond_info(msg, log=False)  # logged once, as the warning above
        self._take_insertions(False)
        return self.reactor.NEVER

    def _hooked_text(self, units: Coordinator) -> str:
        """Returns the hooked lanes in head order, as text.

        With a + group, the text is written like the lanes: key. The lanes of a head are joined by
        +, and a head that is not hooked, between hooked ones, is "-". Without a + group, only the
        hooked lanes are listed.
        """
        slots = sorted(self._built().slots.values(), key=lambda slot: slot.head)
        if not any(slot.shared for slot in slots):
            return ", ".join(units.lane_text(s.lanes[0].unit, s.lanes[0].lane) for s in slots)
        by_head = {slot.head: slot for slot in slots}
        entries = []
        for head in range(max(by_head) + 1):
            slot = by_head.get(head)
            if slot is None:
                entries.append("-")
            else:
                entries.append("+".join(units.lane_text(h.unit, h.lane) for h in slot.lanes))
        return ", ".join(entries)

    def _on_feed_event(self, lane: int, event: FeedEvent) -> None:
        if self.adapter is not None:
            self.adapter.on_feed_event(lane, event)

    def get_status(self, eventtime: float | None = None) -> dict[str, object]:
        """Returns the status of the printer object, for the web page (Status.get_status)."""
        return self.status.get_status(eventtime)

    def changed(self) -> None:
        """Tells the status that the spools or the plan changed in place.

        The status then builds its copies again.
        """
        self.status.changed()

    # --- the spools and the plan ------------------------------------------------------------

    def gcode_file(self, gcmd: GCodeCommand) -> tuple[str, str]:
        """Finds the file that FILE= names in the G-code folder.

        Returns the name as the folder knows it (file_name, as a print start reads it) and the
        path. FILE= may be quoted, for a name with a space. A name outside the folder (..) is
        refused. A refusal, or a missing file, is raised as a G-code error.
        """
        sd = self.printer.lookup_object("virtual_sdcard", None)
        root = getattr(sd, "sdcard_dirname", None)
        name = self.wraps.file_name(root, gcmd.get("FILE", ""))
        path = os.path.normpath(os.path.join(root or "", name))
        if (
            not name
            or root is None
            or os.path.isabs(name)
            or not path.startswith(os.path.normpath(root) + os.sep)
        ):
            raise gcmd.error(f"ace2k_u1: {name!r} is not a file of the G-code folder")
        if not os.path.isfile(path):
            raise gcmd.error(f"ace2k_u1: no file {name}")
        return name, path

    def cmd_ACEK_SPOOL(self, gcmd: GCodeCommand) -> None:
        """ACEK_SPOOL, sent by the web page (PageCommands.cmd_ACEK_SPOOL)."""
        self.page.cmd_ACEK_SPOOL(gcmd)

    def cmd_ACEK_TAG_FORGET(self, gcmd: GCodeCommand) -> None:
        """ACEK_TAG_FORGET, sent by the web page (PageCommands.cmd_ACEK_TAG_FORGET)."""
        self.page.cmd_ACEK_TAG_FORGET(gcmd)

    def cmd_ACEK_HEAD_SPOOL(self, gcmd: GCodeCommand) -> None:
        """ACEK_HEAD_SPOOL, sent by the web page (PageCommands.cmd_ACEK_HEAD_SPOOL)."""
        self.page.cmd_ACEK_HEAD_SPOOL(gcmd)

    def cmd_ACEK_HEAD_PREPARE(self, gcmd: GCodeCommand) -> None:
        """ACEK_HEAD_PREPARE, sent by the web page (PageCommands.cmd_ACEK_HEAD_PREPARE)."""
        self.page.cmd_ACEK_HEAD_PREPARE(gcmd)

    def cmd_ACEK_PLAN(self, gcmd: GCodeCommand) -> None:
        """ACEK_PLAN, sent by the web page (PageCommands.cmd_ACEK_PLAN)."""
        self.page.cmd_ACEK_PLAN(gcmd)

    def cmd_ACEK_PLAN_SET(self, gcmd: GCodeCommand) -> None:
        """ACEK_PLAN_SET, sent by the web page (PageCommands.cmd_ACEK_PLAN_SET)."""
        self.page.cmd_ACEK_PLAN_SET(gcmd)

    def watch_print(self, _eventtime: float) -> None:
        """Follows the print on each watch tick.

        The print of a plan is seen on (printing or paused) and then ended. The plan is dropped
        at that end. The plan is started by its start, never by a tick. The tick of the start
        itself may still read the end of the print before, which is not the end of this print.
        The end of any print, planned or not, clears what it left pending (print_over). A lane
        that a runout stopped during a pause is watched for its head going empty (watch_stopped).
        """
        state = self.u1.print_state()
        ended = state in ("complete", "cancelled", "error", "standby")
        if state in PRINT_ON:
            self.print_seen_on = True
            self.refiller.watch_stopped()
        elif self.print_seen_on and ended:
            self.print_seen_on = False
            self.print_over()
        if self.tracker.plan is None or not self.tracker.plan_started:
            return
        if state in PRINT_ON:
            self.tracker.plan_seen_on = True
        elif self.tracker.plan_seen_on and ended:
            plan = self.tracker.plan
            if plan.off_held:
                # Queued, as the pauses are. The tick never waits for the lock of the G-code.
                self.reactor.register_async_callback(
                    lambda _eventtime: self._heaters_off_queued(plan)
                )
            if plan.end_unload and state == "complete":
                self.reactor.register_async_callback(lambda _eventtime: self._end_unload(plan))
            self.tracker.plan = None
            self.tracker.plan_started = False
            self.tracker.plan_seen_on = False
            self.print_over()

    def print_over(self) -> None:
        """Clears what a print left pending, at its end, its cancel, or a new start over a pause.

        It works with or without a plan. A pending swap or refill, the owed T commands, the
        stopped runouts and the tool index belong to the print that ended, so nothing is left to
        resume. If a lane had taken the end of a spool, it is given back together with its hold,
        because no refill takes it any more.
        """
        self.swapper.swap_pending = None
        self.swapper.tools_owed = []
        self.tracker.reset_print()
        self.refiller.runout_stopped = {}
        if self.adapter is not None:
            self.adapter.pause_reason = None
        for h in getattr(self.adapter, "hooked", ()):
            if h.end_taken:
                h.end_taken = False
                h.end_declined = None
                h.hold = False  # the hold of the shared end is given back, its refill is gone

    def _end_unload(self, plan: Plan) -> None:
        """Ejects the parked lanes of the shared heads at the end of a print with END_UNLOAD=1.

        The end unload of the U1 itself takes the lane that is in each used head. Each other
        lane of a shared head that the print used is known to be parked behind its splitter.
        That lane is ejected out of its bay (the path of ACEK_EJECT, which is a plain unit
        unload for a parked lane). The lanes used are the ones in the plan. In a print, a lane
        reaches a shared head only through its tools (a refill keeps the same lane).

        A shared head with no known lane in it (after a restart) is left as it is, and the
        console says so. Any of its lanes may be the one that the U1 unloads.

        An eject that is refused only for a while is tried again every END_UNLOAD_RETRY_S, for
        END_UNLOAD_WAIT_S. That covers the U1 still unloading the head, the lane winding down,
        and an eject that is running. Each try happens only while no print is on and the lane is
        still parked. Any other refusal is said at once. Never raises.
        """
        adapter = self.adapter
        if adapter is None:
            return
        todo, unknown = [], set()
        for h in {h.key: h for h in (plan.choice or {}).values()}.values():
            slot = adapter.slot_of(h)
            if slot is None or not slot.shared:
                continue  # one lane per head: the end unload of the U1 handles it
            if adapter.parked_known(h):
                todo.append(h)
            elif slot.current is None and slot.head not in unknown:
                unknown.add(slot.head)
                adapter.say(
                    f"ace2k_u1: head {slot.head + 1}: no lane known in it at the print's end: its "
                    "other lanes stay — ACEK_EJECT each"
                )
        if not todo:
            return
        give_up = self.reactor.monotonic() + END_UNLOAD_WAIT_S
        timer: list[object] = []

        def tick(eventtime: float) -> float:
            again = []
            for h in todo:
                if adapter.print_state() in PRINT_ON or not adapter.parked_known(h):
                    continue  # a new print started, or the lane is no longer parked
                why = adapter.ejector.eject_refusal(h)
                waits = why is not None and why.startswith(END_UNLOAD_WAIT_WHY)
                if waits and eventtime + END_UNLOAD_RETRY_S <= give_up:
                    again.append(h)
                    continue
                try:
                    adapter.ejector.start_eject(h.lane, h.unit)
                    adapter.say(
                        f"ace2k_u1: lane {adapter.lane_text(h)} unloading at the print's end "
                        "(parked, used)"
                    )
                except EjectRefused as err:
                    adapter.say(str(err))
                except Exception:
                    logging.exception(
                        "ace2k_u1: lane %s unload at the print's end", adapter.lane_text(h)
                    )
            if again:
                todo[:] = again
                return eventtime + END_UNLOAD_RETRY_S
            self.reactor.unregister_timer(timer[0])
            return self.reactor.NEVER

        timer.append(self.reactor.register_timer(tick, self.reactor.NOW))

    def _heaters_off_queued(self, plan: Plan) -> None:
        """Runs _heaters_off from its own callback, outside the watch tick.

        Anything it raises is logged and never raised into the reactor.
        """
        try:
            self._heaters_off(plan)
        except Exception:
            logging.exception("ace2k_u1: the heaters off at the print's end")

    def _heaters_off(self, plan: Plan) -> None:
        """Turns off the heaters that were held back during the print, at its end.

        A heater-off command (S0) for a tool that was not in its head was held. The head is
        turned off now, and the console says so. Never raises.
        """
        held = plan.off_held or {}
        for head in sorted(held):
            try:
                self.u1.script(f"M104 S0 T{head} A0", timer=True)
            except Exception:
                logging.exception("ace2k_u1: head %d heater off", head + 1)
                continue
            self._built().say(
                f"ace2k_u1: head {head + 1}: turned off at the print's end (T{held[head]}'s M104 "
                "S0 was held)"
            )

    def cmd_ACEK_ADAPTER_STATUS(self, gcmd: GCodeCommand) -> None:
        adapter = self.adapter
        if self.problems:
            gcmd.respond_info("ace2k_u1: not hooked: " + "; ".join(self.problems))
            return
        if adapter is None:  # less than a second after klippy:ready, before the hook has run
            gcmd.respond_info("ace2k_u1: not hooked yet")
            return
        adapter.status_lines(gcmd.respond_info)

    def cmd_ACEK_EJECT(self, gcmd: GCodeCommand) -> None:
        """Takes the filament of a lane out of its bay.

        The command is ACEK_EJECT [UNIT=<name>] LANE=<1-4> [WAIT=0|1]. A refusal is raised as a
        G-code error, and nothing moves. UNIT= works as in the commands of ace2k and is needed
        with several units. WAIT=1 (the default) returns once the eject has decided. WAIT=0
        returns at once.
        """
        lane = gcmd.get_int("LANE", minval=1, maxval=LANES)
        wait = gcmd.get_int("WAIT", 1, minval=0, maxval=1)
        units = self.units()
        unit = None if units is None else units.unit_for(gcmd)  # its error raised as is
        adapter = self.adapter
        if self.problems or adapter is None or not adapter.hooked:
            lane_text = lane if unit is None or units is None else units.lane_text(unit, lane - 1)
            raise gcmd.error(f"ace2k_u1: lane {lane_text} eject refused: the adapter is not hooked")
        h = adapter.find(lane - 1, unit)
        why = None if h is None else self.material.eject_refusal(h)
        if h is not None and why is not None:
            raise gcmd.error(f"ace2k_u1: lane {adapter.lane_text(h)} eject refused: {why}")
        try:
            job = adapter.ejector.start_eject(lane - 1, unit, asked=True)
        except EjectRefused as err:
            raise gcmd.error(str(err)) from None
        if wait:
            adapter.ejector.wait_eject(job)

    def cmd_ACEK_FREE_CHECK(self, gcmd: GCodeCommand) -> None:
        """ACEK_FREE_CHECK, a bench command (Bench.cmd_ACEK_FREE_CHECK)."""
        self.bench.cmd_ACEK_FREE_CHECK(gcmd)

    def cmd_ACEK_SPLITTER_MEASURE(self, gcmd: GCodeCommand) -> None:
        """ACEK_SPLITTER_MEASURE (SplitterCommand.cmd_ACEK_SPLITTER_MEASURE)."""
        self.splitter.cmd_ACEK_SPLITTER_MEASURE(gcmd)

    def cmd_ACEK_HEAD_PROBE(self, gcmd: GCodeCommand) -> None:
        """ACEK_HEAD_PROBE, a bench command (Bench.cmd_ACEK_HEAD_PROBE)."""
        self.bench.cmd_ACEK_HEAD_PROBE(gcmd)


def load_config(config: Config) -> ACE2kU1:
    return ACE2kU1(config)
