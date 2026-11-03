"""The hooks into the U1's own objects (its filament_feed).

This module lists what the hooks need, checks that against the U1's tree, and has the functions that
replace the U1's methods on a hooked head. Each function takes the adapter, and the replaced methods
close over its state.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, NoReturn, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Reactor
    from .ace2k_u1_common import Hooked, Route, Slot
    from .ace2k_u1_printer_types import FeedModule, FeedMove, FilamentFeed, LaneView

try:
    from .ace2k_u1_common import (
        BUSY_RETRY_POLL_S,
        MOVE_MARGIN_S,
        NUDGE_BUSY_S,
        PUSH_POLL_S,
        unit_move_wait_s,
    )
    from .ace2k_u1_push import CODE_OFFSET
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        BUSY_RETRY_POLL_S,
        MOVE_MARGIN_S,
        NUDGE_BUSY_S,
        PUSH_POLL_S,
        unit_move_wait_s,
    )
    from ace2k_u1_push import CODE_OFFSET  # type: ignore[import-not-found, no-redef]

CONSTANTS = {
    "FEED_OK": "ok",
    "FEED_ERR": "general",
    "FEED_ERR_TIMEOUT": "timeout",
    "FEED_ERR_NO_FILAMENT": "no_filament",
    "FEED_ERR_RESIDUAL_FILAMENT": "residual_filament",
    "FEED_ERR_MOTOR_SPEED": "motor_speed",
    "FEED_ERR_WHEEL_SPEED": "wheel_speed",
    "FEED_ERR_DISTANCE": "distance",
    "FEED_ACT_PRELOAD": "preload",
    "FEED_ACT_UPDATE_AUTO_MODE": "update_auto_mode",
    "FEED_ACT_UNLOAD": "unload",
    "FEED_ACT_LOAD": "load",
    "FEED_UNLOAD_STAGE_PREPARE": "prepare",
    "FEED_UNLOAD_STAGE_DOING": "doing",
    "FEED_UNLOAD_STAGE_CANCEL": "cancel",
    "FEED_STA_NONE": "none",
    "FEED_STA_WAIT_INSERT": "wait_insert",
    "FEED_STA_PRELOAD_PREPARE": "preload_prepare",
    "FEED_STA_PRELOAD_FEEDING": "preload_feeding",
    "FEED_STA_PRELOAD_FINISH": "preload_finish",
    "FEED_STA_PRELOAD_FAIL": "preload_fail",
    "FEED_STA_LOAD_EXTRUDING": "load_extruding",
    "FEED_STA_LOAD_FLUSHING": "load_flushing",
    "FEED_STA_LOAD_FINISH": "load_finish",
    "FEED_STA_LOAD_FAIL": "load_fail",
    "FEED_STA_UNLOAD_PREPARE": "unload_prepare",
    "FEED_STA_UNLOAD_HOMING": "unload_homing",
    "FEED_STA_UNLOAD_PICKING": "unload_picking",
    "FEED_STA_UNLOAD_HEATING": "unload_heating",
    "FEED_STA_UNLOAD_HEAT_FINISH": "unload_heat_finish",
    "FEED_STA_UNLOAD_DOING": "unload_doing",
    "FEED_STA_UNLOAD_FINISH": "unload_finish",
    "FEED_STA_UNLOAD_FAIL": "unload_fail",
    "FEED_STA_MANUAL_EXTRUDING": "manual_sta_extruding",
    "FEED_STA_MANUAL_FLUSHING": "manual_sta_flushing",
    "FEED_WHEEL_CIRCUMFERENCE": 31.4159,
    "FEED_CHANNEL_1": 0,
    "FEED_CHANNEL_2": 1,
    "FEED_MOTOR_DIR_A": 1,
    "FEED_MOTOR_DIR_B": 2,
}
METHODS = {
    "_port_event_handler": ("detected", "channel"),
    "_put_into_drive": ("channel",),
    "_set_channel_state": ("channel", "state", "save"),
    "_do_feed": ("ch", "action", "stage", "auto_mode"),
    "_hang_neutral": ("channel",),
}
CHANNEL_LISTS = (
    "_port",
    "wheel",
    "wheel_2",
    "module_exist",
    "filament_ch",
    "runout_sensor",
    "channel_error",
    "exception_code",
    "channel_state",
    "channel_error_state",
    "manual_feeding",
)
ATTRIBUTES = ("reactor", "config", "channel_active", "exception_manager", "motor")
MOTOR_PULSE = ("dir", "value", "time")  # the parameters of run_one_cycle of the side motor


# The channels of one side-feeder module (filament_feed) of the U1.
FEED_CHANNELS = 2
# The states that a channel leaves for load_finish at the end of a load of the U1: its load
# (load_prepare .. load_flushing) and its manual feed (manual_sta_*). Any other way into
# load_finish puts no lane in the head that was checked there (Slot.loaded).
U1_LOAD_STEPS = ("load_", "manual_")


class HeadFlows(Protocol):
    """The head watch of the adapter (ace2k_u1_heads.HeadWatch), as far as the hooks use it."""

    def fail_channel(
        self, ff: FilamentFeed, ch: int, error: str, code: int, fail_state: str | None = None
    ) -> None: ...
    def flow_unknown(
        self, ff: FilamentFeed, ch: int, slot: Slot, flow: str, stage: str | None = None
    ) -> None: ...


class GripHolds(Protocol):
    """The grip watch of the adapter (ace2k_u1_grip.GripWatch), as far as the hooks use it."""

    def clear_waits_behind(self, h: Hooked, why: str | None = None) -> None: ...
    def end_hold(self, h: Hooked) -> None: ...
    def in_tail(self, h: Hooked, lane: LaneView | None = None) -> bool: ...


class PushRunning(Protocol):
    """The push runner of the adapter (ace2k_u1_push.PushRunner), as far as the hooks use it.

    That is the push of a load (its error, None when done) and the preload.
    """

    def push_lane(self, h: Hooked, load: bool = False) -> str | None: ...
    def preload_declined(self, slot: Slot) -> object: ...
    def preload(self, h: Hooked) -> object: ...


class FollowArm(Protocol):
    """The follow of the adapter (ace2k_u1_follow), as far as the hooks use it."""

    def default_on(self, h: Hooked) -> bool: ...
    def arm(self, h: Hooked, busy_wait_s: float = 0.0, lane: LaneView | None = None) -> object: ...


class EjectSchedule(Protocol):
    """The ejector of the adapter (ace2k_u1_eject), as far as the hooks use it."""

    def cancel_eject(self, h: Hooked) -> None: ...
    def drop_eject(self, h: Hooked) -> None: ...
    def eject_running(self, h: Hooked) -> bool: ...
    def moving_lane(self, h: Hooked, mode: object) -> bool: ...
    def schedule_eject(self, h: Hooked) -> None: ...


class ScreenUnloads(Protocol):
    """The cold path of the screen's unload (ace2k_u1_screen_unload.ScreenUnload)."""

    def prepare(self, h: Hooked) -> bool: ...
    def doing(self, h: Hooked) -> bool: ...


class HooksAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the replaced methods use it."""

    @property
    def reactor(self) -> Reactor: ...
    @property
    def module(self) -> FeedModule: ...
    @property
    def swapping(self) -> set[int]: ...  # the heads that a swap is on
    @property
    def retry_push_mm(self) -> float: ...
    @property
    def feed_speed_mm_s(self) -> float: ...
    @property
    def head_watch(self) -> HeadFlows: ...
    @property
    def grip_watch(self) -> GripHolds: ...
    @property
    def push_runner(self) -> PushRunning: ...
    @property
    def follow(self) -> FollowArm: ...
    @property
    def ejector(self) -> EjectSchedule: ...
    @property
    def screen_unload(self) -> ScreenUnloads: ...
    def lane_text(self, h: Hooked) -> str: ...
    def lane_status(self, h: Hooked, eventtime: float | None = None) -> LaneView: ...
    def lane_or_none(self, h: Hooked) -> LaneView | None: ...
    def present(self, h: Hooked) -> bool: ...
    def head_set(self, h: Hooked) -> bool: ...
    def head_holds(self, h: Hooked) -> bool: ...
    def say(self, msg: str) -> None: ...
    def feed_error(self, name: str) -> str: ...
    def u1_pause_reason(self, line: str) -> None: ...
    def stop(self, h: Hooked, why: str, lane: LaneView | None = None) -> None: ...
    def halt(self, h: Hooked, lane: LaneView | None = None) -> None: ...
    def load_material(self, h: Hooked, held: bool) -> str | None: ...
    def retry_busy_start(
        self,
        h: Hooked,
        start: Callable[[], FeedMove],
        wait_s: float = ...,
        period: float | None = None,
    ) -> FeedMove: ...


def check_feed(module: object, ff: object) -> list[str]:
    """Return [] when ff (a filament_feed object) and its module have every hook point.

    The hook points are those this adapter uses, in the expected form. Otherwise return one reason
    per difference.
    """
    problems = [
        f"constant {name} is not {value!r}"
        for name, value in CONSTANTS.items()
        if getattr(module, name, None) != value
    ]
    problems += _method_problems(ff)
    problems += [
        f"{name} is not a two-channel list"
        for name in CHANNEL_LISTS
        if not _channel_list(getattr(ff, name, None))
    ]
    problems += [f"attribute {name} missing" for name in ATTRIBUTES if not hasattr(ff, name)]
    config = getattr(ff, "config", None)
    if not isinstance(config, dict) or "auto_mode" not in config:
        problems.append("config['auto_mode'] missing")
    problems += _pulse_problems(ff)
    return problems


def _channel_list(value: object) -> bool:
    """True when value is a list with one entry per channel of the module."""
    return isinstance(value, list) and len(value) == FEED_CHANNELS


def _method_problems(ff: object) -> list[str]:
    """Return the reasons of check_feed for the METHODS of ff.

    A method is a problem when it is missing or takes other first parameters.
    """
    problems: list[str] = []
    for name, params in METHODS.items():
        method = getattr(ff, name, None)
        if method is None:
            problems.append(f"method {name} missing")
            continue
        got = tuple(inspect.signature(method).parameters)[: len(params)]
        if got != params:
            problems.append(f"method {name} takes {got}, expected {params}")
    return problems


def _pulse_problems(ff: object) -> list[str]:
    """Return the reason of check_feed for the pulse of the side motor of ff, if any.

    The pulse is motor.run_one_cycle.
    """
    pulse = getattr(getattr(ff, "motor", None), "run_one_cycle", None)
    if pulse is None:
        return ["motor.run_one_cycle missing"]
    got = tuple(inspect.signature(pulse).parameters)[: len(MOTOR_PULSE)]
    if got != MOTOR_PULSE:
        return [f"motor.run_one_cycle takes {got}, expected {MOTOR_PULSE}"]
    return []


# The U1's own state that the U1Printer (ace2k_u1_printer) reads or writes. Each entry gives
# the name of a fact as the tree of the U1 defines it. tests/test_structure.py checks the names
# against that tree.
PRINT_TASK_KEYS = {  # from print_task_config.get_status(): one list per key, an entry per head
    "map_table": "extruder_map_table",
    "filament_exist": "filament_exist",
    "vendor": "filament_vendor",
    "type": "filament_type",
    "subtype": "filament_sub_type",
    "rgba": "filament_color_rgba",
}
EXTRUDER_ATTRIBUTES = {  # of a PrinterExtruder
    "e_pos": "printing_e_pos",
    "nozzle": "nozzle_diameter",
    "volume": "nozzle_volume_type",
}
PAUSE_FLAGS = {"paused": "is_paused", "sent": "pause_command_sent"}  # of pause_resume
PRINT_STATS_LAYER = ("info", "current_layer")  # print_stats.get_status()
TOOLHEAD_HOMED = "homed_axes"  # in the status of the toolhead
# The resume macro of the U1, whose variables the pause records.
INNER_RESUME = "INNER_RESUME"
INNER_RESUME_VARIABLES = ("last_extruder_index", "last_extruder_temp", "extruder%d_temp")
# A command of the U1, registered per extruder.
IDLE_EXIT = "MOVE_TO_XY_IDLE_POSITION_EXTRUDER"
PREEXTRUDE = "SM_PRINT_PREEXTRUDE_FILAMENT"  # the U1's pre-extrusion of a tool, by INDEX
INNER_PREEXTRUDE = "INNER_PREEXTRUDE_FILAMENT"  # its macro: LENGTH purge, fan, cut, clean, idle
END_UNLOAD = "SM_PRINT_END_AUTO_UNLOAD_FILAMENT"  # the U1's end-of-print unload, a macro
FILAMENT_PARAMETERS = {  # methods of filament_parameters
    "load_temp": "get_load_temp",
    "unload_temp": "get_unload_temp",
    "soft": "get_is_soft",
}


class EncoderWheel:
    """A stand-in for the tachometer of the side-feeder wheel of the U1.

    It is fed by the filament encoder of a lane. The counts grow with the absolute travel, at the
    own resolution of the wheel. That resolution is ppr pulses per revolution, counted on both
    edges, and one revolution is FEED_WHEEL_CIRCUMFERENCE mm. The travel is kept in fractional
    counts. A step of a sample is often under one count (at ppr 6, one count is about 2.6 mm), and
    rounding each step would read a slow extrusion as a stalled wheel.
    """

    def __init__(self, ppr: int, circumference_mm: float) -> None:
        self.ppr = ppr
        self.counts_per_mm = 2.0 * ppr / circumference_mm
        self._travel = 0.0  # in counts, fractional
        self._last_mm: float | None = None
        self._last_time = 0.0
        self._rpm = 0.0

    def sample(self, encoder_mm: float | None, eventtime: float) -> None:
        if encoder_mm is None:
            return
        if self._last_mm is not None:
            step = abs(encoder_mm - self._last_mm) * self.counts_per_mm
            dt = eventtime - self._last_time
            self._travel += step
            self._rpm = step / (2.0 * self.ppr) * 60.0 / dt if dt > 0 else 0.0
        self._last_mm = encoder_mm
        self._last_time = eventtime

    def rebase(self, mm: float | None, eventtime: float) -> None:
        """Start from a new source (the encoder of the lane or the extruder of the head).

        Its reading is taken as the base, and nothing has travelled. The counts stay where they
        are, so they never jump.
        """
        self._last_mm = mm
        self._last_time = eventtime
        self._rpm = 0.0

    def get_counts(self) -> int:
        # The whole counts travelled. The margin absorbs float error at an exact count.
        return int(self._travel + 1e-6)

    def get_rpm(self) -> float:
        return self._rpm

    def get_last_report_time(self) -> float:
        return self._last_time


BUSY_MODES = ("loading", "error")  # a lane that the unit is still loading, or whose load failed
# The states of the U1 in which its extruder moves the filament. Its stock side motor free-wheels
# then. The follow of the lane is armed on entering any of them, because the geared motor of the
# unit cannot be back-driven, and the extruder would grind the filament against an idle lane. The
# tip forming of the unload (unload_doing) extrudes forward and retracts in turn, and the follow
# serves both ways.
PULL_STATES = (
    "FEED_STA_LOAD_EXTRUDING",
    "FEED_STA_LOAD_FLUSHING",
    "FEED_STA_MANUAL_EXTRUDING",
    "FEED_STA_MANUAL_FLUSHING",
    "FEED_STA_UNLOAD_DOING",
)
# The steps of the unload of the U1 before its tip forming. A loaded head that enters them keeps
# its follow.
UNLOAD_STEPS = (
    "FEED_STA_UNLOAD_PREPARE",
    "FEED_STA_UNLOAD_HOMING",
    "FEED_STA_UNLOAD_PICKING",
    "FEED_STA_UNLOAD_HEATING",
    "FEED_STA_UNLOAD_HEAT_FINISH",
)

# The exception code of a failed load of the U1 (the base of its flow).
LOAD_CODE = 30
# The wait of the retry feed is at least twice its travel time plus this margin.
NUDGE_MARGIN_S = MOVE_MARGIN_S


def hook_feed(adapter: HooksAdapter, ff: FilamentFeed, by_ch: Route) -> None:
    orig_hang = ff._hang_neutral

    def hang_neutral(channel: int) -> None:
        if channel not in by_ch:
            orig_hang(channel)

    ff._hang_neutral = hang_neutral
    orig_port = ff._port_event_handler

    def port_event_handler(detected: bool, channel: int) -> object:
        # On a hooked channel the inlet is the insert of the lane, told by this adapter only. The
        # own inlet sensor of the U1 (that of its side feeder, which is empty because the unit
        # feeds the head) still reports through its ADC callback. If that were passed on, its
        # settling to "no filament" after a restart would take a loaded head to preload_finish and
        # stop its follow.
        h = by_ch.get(channel)
        if h is None and channel in by_ch:
            return None  # a head with several lanes and none in it has no inlet to tell
        if h is not None and not h.telling:
            if not h.inlet_said:
                h.inlet_said = True
                logging.info(
                    "ace2k_u1: lane %s: the U1's own inlet reading (%s) not passed on",
                    adapter.lane_text(h),
                    detected,
                )
            return None
        return orig_port(detected, channel)

    ff._port_event_handler = port_event_handler
    now = adapter.reactor.monotonic()
    for ch, slot in by_ch.slots.items():
        # The inlet of the U1 reads the current lane of the head. With none in it, there is no
        # filament.
        def detected(slot: Slot = slot) -> bool:
            return slot.current is not None and adapter.present(slot.current)

        ff._port[ch].get_filament_detected = detected
        ff.module_exist[ch] = True
        ppr = ff.wheel[ch].ppr
        for h in slot.lanes:
            lane = adapter.lane_status(h, now)
            h.wheel = EncoderWheel(ppr, adapter.module.FEED_WHEEL_CIRCUMFERENCE)
            h.wheel.sample(lane.get("encoder_mm"), now)
            # A filament that is already in the bay at boot is present at once. The state of the
            # U1 is derived again below, and not raised as an insert.
            insert = lane.get("insert") is True
            h.present = insert and lane.get("mode") not in BUSY_MODES
            h.rise_at = now if insert else None
            h.bay_full = insert
        # The wheel of the U1. A head of one lane has the wheel of its lane. A head of several
        # lanes has one wheel of its own, fed by the current lane (the first when there is none
        # yet). See _wheel_from.
        if not slot.shared:
            slot.wheel = cast("EncoderWheel", slot.lanes[0].wheel)  # set just above
        else:
            feeder = slot.current or slot.lanes[0]
            slot.wheel = EncoderWheel(ppr, adapter.module.FEED_WHEEL_CIRCUMFERENCE)
            slot.wheel.sample(adapter.lane_status(feeder, now).get("encoder_mm"), now)
            slot.wheel_key = (id(feeder), "encoder")
        ff.wheel[ch] = ff.wheel_2[ch] = slot.wheel

        def update_auto_mode(_eventtime: float, ff: FilamentFeed = ff, ch: int = ch) -> object:
            return ff._do_feed(
                ch,
                adapter.module.FEED_ACT_UPDATE_AUTO_MODE,
                auto_mode=ff.config["auto_mode"][ch],
            )

        adapter.reactor.register_async_callback(update_auto_mode)
    hook_push(adapter, ff, by_ch)
    hook_states(adapter, ff, by_ch)
    hook_nudge(adapter, ff, by_ch)


def hook_push(adapter: HooksAdapter, ff: FilamentFeed, by_ch: Route) -> None:
    """Hook the push of the load into the drive, and the preload, on hooked channels.

    Both go through the unit. Everything else reaches the U1's own code.
    """
    orig_put = ff._put_into_drive
    orig_do_feed = ff._do_feed

    def put_into_drive(channel: int) -> object:
        h = by_ch.get(channel)
        if h is not None:
            _push_into_drive(adapter, ff, channel, h)
        elif channel in by_ch:  # a head of several lanes and none in it: nothing is pushed
            _no_lane_in_head(adapter, ff, channel, by_ch.slots[channel])
        else:
            return orig_put(channel)
        return None

    def do_feed(
        ch: int,
        action: str | None = None,
        stage: str | None = None,
        auto_mode: bool | None = None,
    ) -> object:
        h = by_ch.get(ch)
        if action == adapter.module.FEED_ACT_PRELOAD and h is None and ch in by_ch:
            return adapter.push_runner.preload_declined(by_ch.slots[ch])
        if action == adapter.module.FEED_ACT_UNLOAD and h is None and ch in by_ch:
            # It probes, or it raises a refusal.
            adapter.head_watch.flow_unknown(ff, ch, by_ch.slots[ch], "unload", stage)
        if action == adapter.module.FEED_ACT_LOAD and h is None and ch in by_ch:
            # It probes, or it raises a refusal.
            adapter.head_watch.flow_unknown(ff, ch, by_ch.slots[ch], "load")
        if action == adapter.module.FEED_ACT_UNLOAD and h is not None and _cold(adapter, h, stage):
            return None  # the screen's unload of a free lane, cold (ace2k_u1_screen_unload)
        if h is None or action != adapter.module.FEED_ACT_PRELOAD:
            return orig_do_feed(ch, action, stage, auto_mode)
        return adapter.push_runner.preload(h)

    ff._put_into_drive = put_into_drive
    ff._do_feed = do_feed


def _no_lane_in_head(adapter: HooksAdapter, ff: FilamentFeed, channel: int, slot: Slot) -> None:
    """The U1's load into a head of several lanes when no lane is known in it.

    In a print (the head is set) the U1's load goes on with what is there. Otherwise the load is
    refused: it raises.
    """
    if adapter.head_set(slot.lanes[0]):
        # A load in a print, which flow_unknown leaves alone (a print starts with every head that
        # it uses known). The load of the U1 goes on with what is there.
        return
    names = ", ".join(adapter.lane_text(x) for x in slot.lanes)
    adapter.say(
        f"ace2k_u1: head {slot.head + 1} is shared by lanes {names}: load refused "
        "with no lane known in it and its sensor empty — choose the lane with "
        "ACEK_LANE_ACTIVE, then load"
    )
    line = f"ace2k_u1: push to head {slot.head + 1}: no lane in the head"
    _refuse_channel(adapter, ff, channel, line)


def _push_into_drive(adapter: HooksAdapter, ff: FilamentFeed, channel: int, h: Hooked) -> None:
    """The U1's load into the drive of lane h's head: the unit pushes the lane to the head.

    A failed push, or a lane whose material refuses the load, fails the channel: it raises.
    """
    at_head = adapter.head_holds(h)
    if at_head:
        if h.waits_behind:
            # The head is already set by the piece ahead of the waiting filament of this lane.
            # The load of the U1 goes on with that piece, and the lane does not take the head.
            logging.info(
                "ace2k_u1: lane %s waits behind e%d's piece: its load uses that piece",
                adapter.lane_text(h),
                h.head,
            )
            return
    else:
        # The push brings the lane's own filament to the head.
        adapter.grip_watch.clear_waits_behind(h)
    # The lane's filament at the head before this load, and not known to wait short of the head's
    # gear: it may be in the gear, so a refusal does not pull it back.
    held = at_head and not h.parked_at_sensor
    error = adapter.push_runner.push_lane(h, load=True)
    if error is not None:
        line = f"ace2k_u1: lane {adapter.lane_text(h)} push to e{h.head}: {error}"
        _refuse_channel(adapter, ff, channel, line, error)
    # The U1 took the temperature of this load from the head's setting: a lane with no material
    # set, or another type than the head's, is rolled back and the load refused.
    refused = adapter.load_material(h, held)
    if refused is not None:
        adapter.say(refused)
        _refuse_channel(adapter, ff, channel, refused)
    adapter.grip_watch.end_hold(h)


def _refuse_channel(
    adapter: HooksAdapter, ff: FilamentFeed, channel: int, line: str, error: str | None = None
) -> NoReturn:
    """Fail the U1's load on channel, give the U1 line as the reason of its pause, and raise it.

    With error (a push error), the channel gets that error and its own code; without, the U1's
    FEED_ERR and the load's code. The raise is how the U1's load fails.
    """
    if error is None:
        adapter.head_watch.fail_channel(ff, channel, adapter.feed_error("FEED_ERR"), LOAD_CODE)
    else:
        adapter.head_watch.fail_channel(ff, channel, error, LOAD_CODE + CODE_OFFSET[error])
    adapter.u1_pause_reason(line)
    raise RuntimeError(line)


def _cold(adapter: HooksAdapter, h: Hooked, stage: str | None) -> bool:
    """Run a stage of the U1's unload cold, when it may be. True when it was run cold."""
    m = adapter.module
    if stage == m.FEED_UNLOAD_STAGE_PREPARE:
        return adapter.screen_unload.prepare(h)
    if stage == m.FEED_UNLOAD_STAGE_DOING:
        return adapter.screen_unload.doing(h)
    return False


def _mode(adapter: HooksAdapter, h: Hooked) -> object:
    """Return the lane's mode from its status, or None (logged) when it cannot be read."""
    lane = adapter.lane_or_none(h)
    return None if lane is None else lane.get("mode")


def _left_load_finish(adapter: HooksAdapter, h: Hooked, prev: str, state: str) -> None:
    """The head left load_finish, so there is no follow on it: stop the lane.

    The tail is the exception, whose filament left the bay (the U1 is told so and goes to
    wait_insert) and still feeds the head. So is the U1's runout while an eject moves the lane
    (Ejector.moving_lane): its roll back cleared the head's sensor, the U1 moved the channel to
    preload_finish or wait_insert, and the eject goes on. Any other state (a flow of the U1
    starting), or a head's sensor that still sees filament (or cannot be read), stops it, during
    an eject too.
    """
    m = adapter.module
    runout = state in (m.FEED_STA_PRELOAD_FINISH, m.FEED_STA_WAIT_INSERT)
    if adapter.grip_watch.in_tail(h) and adapter.head_holds(h):
        logging.info("ace2k_u1: lane %s in its tail: the follow kept", adapter.lane_text(h))
    elif runout and not adapter.head_holds(h) and adapter.ejector.moving_lane(h, _mode(adapter, h)):
        logging.info(
            "ace2k_u1: lane %s: e%d left %s in its eject: the eject goes on",
            adapter.lane_text(h),
            h.head,
            prev,
        )
    else:
        adapter.stop(h, state)


def hook_states(adapter: HooksAdapter, ff: FilamentFeed, by_ch: Route) -> None:
    """Hook the channel states of the U1.

    This is called under the lock of the U1, and it never raises. The states are kept. On hooked
    channels there is more. The follow is armed from the extrusion of the U1 on (any of PULL_STATES:
    its extruder moves the filament then, the tip forming of the unload included), and on a loaded
    head it is kept through the steps of the unload. The lane is stopped when anything fails and
    when an unload ends, and the eject (the unload of the unit, which is not waited for) follows
    that stop. A head that leaves load_finish in any other way (a head runout, the finish of the
    manual feed) stops the lane too, except the U1's runout while an eject moves the lane
    (_left_load_finish).
    """
    orig = ff._set_channel_state
    m = adapter.module
    pull = {getattr(m, name) for name in PULL_STATES}
    keep = pull | {getattr(m, name) for name in UNLOAD_STEPS}

    def set_channel_state(channel: int, state: str, save: bool = False) -> None:
        prev = ff.channel_state[channel]
        orig(channel, state, save)
        h = by_ch.get(channel)
        if h is None:
            return
        slot = by_ch.slots[channel]
        if h.cold_unload and _cold_state(h, state, m):
            return
        _track_loaded(slot, h, prev, state, m)
        if state == m.FEED_STA_LOAD_FINISH:
            adapter.grip_watch.end_hold(h)
        if state == m.FEED_STA_LOAD_FINISH or state in pull:
            # A load_finish set again over load_finish is the U1 asserting a state it holds, the
            # re-derivation of the channel by the hook among them. It is not a load. It arms only
            # with follow_default of ace2k off (as before it). Otherwise the start is ace2k's.
            again = state == m.FEED_STA_LOAD_FINISH and prev == m.FEED_STA_LOAD_FINISH
            if not again or not adapter.follow.default_on(h):
                adapter.follow.arm(h)
        elif state.endswith("_fail"):
            h.follow_wanted = False
            adapter.ejector.drop_eject(h)
            adapter.stop(h, state)
        elif state == m.FEED_STA_UNLOAD_FINISH:
            h.follow_wanted = False
            adapter.stop(h, state)
            if h.head not in adapter.swapping:  # a swap rolls the lane back itself
                adapter.ejector.schedule_eject(h)
        elif prev == m.FEED_STA_LOAD_FINISH and state not in keep:
            _left_load_finish(adapter, h, prev, state)

    ff._set_channel_state = set_channel_state


def _track_loaded(slot: Slot, h: Hooked, prev: str, state: str, m: FeedModule) -> None:
    """Keep Slot.loaded of the head of h through a change of its channel from prev to state."""
    if state != m.FEED_STA_LOAD_FINISH:
        slot.loaded = None  # any other flow on the channel may change what is in the head
    elif prev != m.FEED_STA_LOAD_FINISH and prev.startswith(U1_LOAD_STEPS):
        slot.loaded = h  # a load of the U1 (its load or its manual feed) ended with this lane


def _cold_state(h: Hooked, state: str, m: FeedModule) -> bool:
    """Handle a state of a cold unload of the screen (Hooked.cold_unload).

    Return True when nothing else is to be done. Its unload_doing arms no follow, and its
    unload_finish stops nothing and brings no eject, as the unit already takes the lane back to
    its bay. Its unload_fail (the screen's cancel) ends it, and is handled as any failure: the lane
    stops. Any state outside the unload ends it too.
    """
    if state == m.FEED_STA_UNLOAD_DOING:
        return True
    ends = (m.FEED_STA_UNLOAD_FINISH, m.FEED_STA_UNLOAD_FAIL)
    if state in ends or not state.startswith("unload_"):
        h.cold_unload = False
        return state == m.FEED_STA_UNLOAD_FINISH
    return False


def hook_nudge(adapter: HooksAdapter, ff: FilamentFeed, by_ch: Route) -> None:
    """Hook the extrude retry of the U1 to a feed of the lane.

    In load_extruding, when its check says that the filament did not extrude, the U1 pulses its side
    motor forward (run_one_cycle in the feed direction of the channel) to push the filament into the
    gears of the extruder. On a hooked channel that motor is not in the path of the filament, so the
    pulse becomes the retry feed of the lane. The channel is the U1's active channel, and the state
    is its own. The direction only tells the forward pulse of the retry from the hang-neutral pulse,
    which is reversed (and which the adapter already keeps from the motor on a hooked channel).
    Every other pulse reaches the motor of the U1 unchanged.
    """
    m = adapter.module
    motor = ff.motor
    orig = motor.run_one_cycle
    forward = {m.FEED_CHANNEL_1: m.FEED_MOTOR_DIR_A, m.FEED_CHANNEL_2: m.FEED_MOTOR_DIR_B}

    def run_one_cycle(dir: int, value: float, time: float) -> object:  # the names of the U1
        ch = ff.channel_active
        if ch is None or ch not in by_ch:
            return orig(dir, value, time)
        h = by_ch.get(ch)
        retry = ff.channel_state[ch] == m.FEED_STA_LOAD_EXTRUDING and dir == forward.get(ch)
        if not retry:
            return orig(dir, value, time)
        if h is None:
            return None  # a head of several lanes and none in it: no lane was pushed, no motor
        if h.waits_behind:
            return None  # the filament of the lane waits behind the piece of the head: not pushed
        nudge(adapter, h)
        return None

    motor.run_one_cycle = run_one_cycle


def nudge(adapter: HooksAdapter, h: Hooked) -> None:
    """Run the extrude retry on a hooked channel.

    This is called from the load loop of the U1, and it never raises. The follow of the lane is
    stopped. Then a feed of retry_push_mm at feed_speed runs to its end (bounded), and the follow is
    armed again whatever became of the feed. If the stop fails, the follow is still running. The
    retry is then skipped, nothing is started or re-armed, and the U1 gets its loop back at once.
    """
    n = adapter.lane_text(h)
    try:
        adapter.halt(h)
    except Exception as err:
        logging.exception("ace2k_u1: lane %s stop for the extrude retry", n)
        adapter.say(
            f"ace2k_u1: lane {n} not stopped for the extrude retry — {err}; retry push "
            "skipped, the follow left running"
        )
        return
    h.armed = False
    h.hold = True  # the standing re-arm keeps off the lane while the retry runs it
    try:
        try:
            _feed_and_wait(adapter, h)
        except Exception as err:
            logging.exception("ace2k_u1: lane %s retry push", n)
            adapter.say(f"ace2k_u1: lane {n} retry push failed — {err}")
        try:
            adapter.follow.arm(h, busy_wait_s=NUDGE_BUSY_S)
        except Exception as err:
            logging.exception("ace2k_u1: lane %s re-arm after the retry push", n)
            adapter.say(f"ace2k_u1: lane {n} follow not armed — {err}")
    finally:
        h.hold = False


def _feed_and_wait(adapter: HooksAdapter, h: Hooked) -> None:
    """Run the feed of the retry and wait for its final event.

    The start is retried for NUDGE_BUSY_S when the unit refuses it as busy, because the stopped
    follow is winding down. Past the wait the feed is stopped. A refusal, a timeout and an end
    other than done are console lines.
    """
    n = adapter.lane_text(h)
    try:
        move = adapter.retry_busy_start(
            h,
            # The tip is in the head.
            lambda: h.api.start_move(
                h.lane, "feed", adapter.retry_push_mm, adapter.feed_speed_mm_s, free_tip=False
            ),
            period=BUSY_RETRY_POLL_S,
        )
    except Exception as err:  # FeedRefused (with a reason), ValueError, RuntimeError
        adapter.say(f"ace2k_u1: lane {n} retry push not started — {err}")
        return
    # A backstop: the unit ends a move that passes its own deadline (see unit_move_wait_s).
    deadline = adapter.reactor.monotonic() + unit_move_wait_s(
        h, adapter.retry_push_mm, adapter.feed_speed_mm_s, NUDGE_MARGIN_S
    )
    while (result := move.result()) is None:
        now = adapter.reactor.pause(adapter.reactor.monotonic() + PUSH_POLL_S)
        if move.result() is None and now >= deadline:
            adapter.say(f"ace2k_u1: lane {n} retry push timed out")
            adapter.stop(h, "the retry push's timeout")
            return
    kind = result.get("kind")
    if kind != "done":
        adapter.say(f"ace2k_u1: lane {n} retry push ended {kind}")
