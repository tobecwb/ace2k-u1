"""Describes the objects of the U1 and of ace2k that the adapter uses, for the type checker only.

It is imported under ``if TYPE_CHECKING:``. Nothing here runs on the printer.

Each class lists the members that the adapter reads, writes or calls on that object. The names
come from one of two places.
- The tree of the U1 (klippy/extras/filament_feed.py, print_task_config.py, print_stats.py,
  pause_resume.py, filament_parameters.py, kinematics/extruder.py, toolhead.py).
- The host interface of ace2k (docs/protocol.md of ace2k).
tests/test_structure.py checks the names of the U1 against its tree. The version of the host
interface checks the names of ace2k.

A member that the adapter replaces on the object of the U1 (a hook) is an attribute here, not a
method.

Some values cross the boundary untyped. They are a status dict, the record of a tag and a feed
event. Their type is ``Any``. The aliases below name them, and no other module of the adapter
spells ``Any``.

The objects of Klipper itself (the printer, the reactor, G-code and a config section) are the
descriptions of ace2k (ace2k_klipper_types). Reactor, Config, GCodeCommand and GCode add the
members that the adapter uses beyond them. Printer and Config hand out the descriptions of the
U1.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

try:
    from . import ace2k_klipper_types as klipper
    from .ace2k_klipper_types import FeedEvent
except ImportError:  # the host tests put the klippy/extras of ace2k on the path
    import ace2k_klipper_types as klipper  # type: ignore[import-not-found, no-redef]
    from ace2k_klipper_types import FeedEvent  # type: ignore[no-redef]

# A status dict as get_status of an object hands it out. The objects are print_task_config,
# print_stats, a head sensor, the toolhead and an ace2k unit. The values are whatever each field
# holds.
Status = dict[str, Any]
# The status of a lane, as get_status of an ace2k unit lists it (sensors, mode, tag, last event).
LaneView = dict[str, Any]
# The record of a tag, as the tag status of ace2k carries it (brand, material, name, color_rgba,
# uid).
TagRecord = dict[str, Any]
# The tag status of a lane, as the lane status of ace2k carries it (state, uid, record).
TagView = dict[str, Any]
# An ACEK_SPOOL record (type, color, vendor), as the JSON file beside printer.cfg keeps it.
SpoolRecord = dict[str, Any]


class Reactor(klipper.Reactor, Protocol):
    """The reactor of Klipper, with the removal of a timer.

    The description of ace2k leaves that out.
    """

    def unregister_timer(self, timer_handler: object) -> None: ...


class GCodeCommand(klipper.GCodeCommand, Protocol):
    """gcode.GCodeCommand, with its line as typed.

    The description of ace2k leaves that line out. The line of a T command is built again for
    the RESUME that runs it. The error class of the command also takes the action of the U1
    ("pause") as a keyword. It is read through a callable where it is passed.
    """

    def get_commandline(self) -> str: ...


# The handler of a G-code command, as Klipper registers it.
Handler = Callable[[GCodeCommand], object]


class GCode(klipper.GCode, Protocol):
    """gcode.GCodeDispatch, as the adapter wraps its commands.

    register_command(name, None) takes a handler out and hands it back. A command is built again
    from its parts. The handler tables (gcode_help, base_gcode_handlers, ready_gcode_handlers)
    are read by name.
    """

    def register_command(
        self,
        cmd: str,
        func: Handler | None,
        when_not_ready: bool = ...,
        desc: str | None = ...,
    ) -> Handler | None: ...
    def create_gcode_command(
        self, command: str, commandline: str, params: dict[str, str]
    ) -> GCodeCommand: ...


class Printer(klipper.Printer, Protocol):
    """klippy.Printer. Its reactor is the one described above (Reactor)."""

    def get_reactor(self) -> Reactor: ...


class Config(klipper.Config, Protocol):
    """configfile.ConfigWrapper, with the reader of a list.

    The description of ace2k leaves that reader out. Its printer is the one described above
    (Printer).
    """

    def get_printer(self) -> Printer: ...
    def getlist(
        self,
        option: str,
        default: Sequence[str] | None = ...,
        sep: str = ...,
        count: int | None = ...,
        note_valid: bool = ...,
    ) -> Sequence[str]: ...


# --- the U1's side feeder (filament_feed) ------------------------------------------------------


class FeedModule(Protocol):
    """The module filament_feed of the U1.

    These are the constants that the adapter compares, sends and checks. ace2k_u1_hooks.CONSTANTS
    lists every one, with its value.
    """

    FEED_OK: str
    FEED_ERR: str
    FEED_ERR_TIMEOUT: str
    FEED_ERR_NO_FILAMENT: str
    FEED_ERR_RESIDUAL_FILAMENT: str
    FEED_ERR_MOTOR_SPEED: str
    FEED_ERR_WHEEL_SPEED: str
    FEED_ERR_DISTANCE: str
    FEED_ACT_PRELOAD: str
    FEED_ACT_UPDATE_AUTO_MODE: str
    FEED_ACT_UNLOAD: str
    FEED_ACT_LOAD: str
    FEED_UNLOAD_STAGE_PREPARE: str
    FEED_UNLOAD_STAGE_CANCEL: str
    FEED_UNLOAD_STAGE_DOING: str
    FEED_STA_NONE: str
    FEED_STA_WAIT_INSERT: str
    FEED_STA_PRELOAD_PREPARE: str
    FEED_STA_PRELOAD_FEEDING: str
    FEED_STA_PRELOAD_FINISH: str
    FEED_STA_PRELOAD_FAIL: str
    FEED_STA_LOAD_EXTRUDING: str
    FEED_STA_LOAD_FLUSHING: str
    FEED_STA_LOAD_FINISH: str
    FEED_STA_LOAD_FAIL: str
    FEED_STA_UNLOAD_PREPARE: str
    FEED_STA_UNLOAD_HOMING: str
    FEED_STA_UNLOAD_PICKING: str
    FEED_STA_UNLOAD_HEATING: str
    FEED_STA_UNLOAD_HEAT_FINISH: str
    FEED_STA_UNLOAD_DOING: str
    FEED_STA_UNLOAD_FINISH: str
    FEED_STA_UNLOAD_FAIL: str
    FEED_STA_MANUAL_EXTRUDING: str
    FEED_STA_MANUAL_FLUSHING: str
    FEED_WHEEL_CIRCUMFERENCE: float
    FEED_CHANNEL_1: int
    FEED_CHANNEL_2: int
    FEED_MOTOR_DIR_A: int
    FEED_MOTOR_DIR_B: int


class Port(Protocol):
    """The inlet sensor of a channel. The hook replaces its getter on a hooked channel."""

    get_filament_detected: Callable[[], bool]


class Wheel(Protocol):
    """The wheel tachometer of a channel (wheel, wheel_2).

    On a hooked channel it is a stand-in of the adapter (ace2k_u1_hooks.EncoderWheel). The U1
    calls these members on it. The assignment of the stand-in to wheel and wheel_2 in the hook is
    checked against this Protocol, so the stand-in keeps every member that the U1 reads.
    """

    ppr: int

    def get_counts(self) -> int: ...
    def get_rpm(self) -> float: ...
    def get_last_report_time(self) -> float: ...


class HeadSensor(Protocol):
    """The filament sensor of a head (runout_sensor).

    Its switch is runout_buttun_state. That is the spelling of the U1, and it is read by name,
    because another build has only the status. Its runout_helper (name, sensor_enabled,
    runout_pause, pause_delay) is read by name too.
    """

    def get_status(self, eventtime: float) -> Status: ...


class RunOneCycle(Protocol):
    """The pulse of the side motor, with the parameter names of the U1."""

    def __call__(self, dir: int, value: float, time: float) -> object: ...


class Motor(Protocol):
    """The side feed motor. The hook replaces its pulse."""

    run_one_cycle: RunOneCycle


class ExceptionIds(Protocol):
    """The module ids and codes of the exception manager (its list).

    The failure of the preload is raised under MODULE_ID_FEEDING. The other exceptions that the
    adapter raises use its own constants.
    """

    MODULE_ID_FEEDING: int


class ExceptionManager(Protocol):
    """The exception manager of the U1, which raises an exception to its screen.

    list holds its module ids and codes (ExceptionIds).
    """

    list: ExceptionIds

    def raise_exception_async(self, **kwargs: object) -> None: ...


class PortEventHandler(Protocol):
    def __call__(self, detected: bool, channel: int) -> object: ...


class ChannelCall(Protocol):
    """A call on one channel, such as _put_into_drive and _hang_neutral."""

    def __call__(self, channel: int) -> object: ...


class SetChannelState(Protocol):
    def __call__(self, channel: int, state: str, save: bool = False) -> object: ...


class DoFeed(Protocol):
    def __call__(
        self,
        ch: int,
        action: str | None = None,
        stage: str | None = None,
        auto_mode: bool | None = None,
    ) -> object: ...


class FilamentFeed(Protocol):
    """A FilamentFeed object of filament_feed.

    It is one side feeder with two channels. Each channel feeds the head in filament_ch. Its
    lists hold one entry per channel. The methods that the hooks replace are attributes here.
    ace2k_u1_hooks.METHODS, CHANNEL_LISTS and ATTRIBUTES list them.
    """

    reactor: Reactor
    config: dict[str, list[bool]]  # auto_mode, load_finish: one flag per channel
    channel_active: int | None
    exception_manager: ExceptionManager
    motor: Motor
    filament_ch: list[int]
    module_exist: list[bool]
    runout_sensor: list[HeadSensor]
    wheel: list[Wheel]
    wheel_2: list[Wheel]
    channel_error: list[str]
    exception_code: list[int]
    channel_state: list[str]
    channel_error_state: list[str]
    manual_feeding: list[bool]
    _port: list[Port]
    _port_event_handler: PortEventHandler
    _put_into_drive: ChannelCall
    _set_channel_state: SetChannelState
    _do_feed: DoFeed
    _hang_neutral: ChannelCall


# --- the U1's print objects --------------------------------------------------------------------


class PrintTaskConfig(Protocol):
    """print_task_config.

    Its status holds the filament setting of each head and the tool -> head table
    (ace2k_u1_hooks.PRINT_TASK_KEYS). Its SET_PRINT_FILAMENT_CONFIG handler,
    is_exec_print_end_action and perform_auto_replenish are read by name.
    """

    def get_status(self, eventtime: float) -> Status: ...
    def backup_filament_info(self, head: int) -> None: ...


class PrintStats(Protocol):
    """The print_stats of the U1: the state of the print and its status (the current layer)."""

    state: str

    def get_status(self, eventtime: float) -> Status: ...


class PauseResume(Protocol):
    """pause_resume and its pause. Its flags (is_paused, pause_command_sent) are read by name."""

    def send_pause_command(self) -> None: ...


class Heater(Protocol):
    """The heater of an extruder."""

    def get_temp(self, eventtime: float) -> tuple[float, float]: ...


class Fan(Protocol):
    """The print fan bound to an extruder (the PrinterExtruder's binding_fan)."""

    def set_speed_from_command(self, value: float) -> None: ...
    def get_status(self, eventtime: float) -> dict[str, float]: ...


class Extruder(Protocol):
    """The PrinterExtruder of the U1.

    It has an index and a section name, a heater, and its position at a print time.
    printing_e_pos, nozzle_diameter and nozzle_volume_type are read and written by name
    (ace2k_u1_hooks.EXTRUDER_ATTRIBUTES).
    """

    extruder_num: int
    name: str

    def get_name(self) -> str: ...
    def get_heater(self) -> Heater: ...
    def find_past_position(self, print_time: float) -> float: ...


class ToolHead(Protocol):
    """The toolhead, with its status (homed axes) and the active extruder."""

    def get_status(self, eventtime: float) -> Status: ...
    def get_extruder(self) -> Extruder: ...


class FilamentParameters(Protocol):
    """filament_parameters, with its status.

    Its tables are the dicts that it holds. Its methods (ace2k_u1_hooks.FILAMENT_PARAMETERS) are
    called by name.
    """

    def get_status(self, eventtime: float) -> Status: ...


# --- the objects of ace2k, as the adapter reaches them ------------------------------------------


class FeedMove(Protocol):
    """The Move of ace2k: a motion that has started, up to its final event.

    seq is the sequence number that the final event echoes.
    """

    seq: int

    def done(self) -> bool: ...
    def result(self) -> FeedEvent | None: ...


class UnitFeed(Protocol):
    """The host interface of a unit (ace2k_feed): the moves, the stops and the lane settings."""

    move_max_mm: float | None

    def start_move(
        self,
        lane_index: int,
        mode: str,
        length_mm: float,
        speed_mm_s: float,
        free_tip: bool = False,
    ) -> FeedMove: ...
    def stop(self, lane_index: int, by: str = "host") -> None: ...
    def set_speed(self, lane_index: int, speed_mm_s: float) -> None: ...
    def clear(self, lane_index: int) -> None: ...
    def set_lane_grip(self, lane_index: int, grip_mm: float) -> None: ...
    def set_lane_park(self, lane_index: int) -> None: ...
    # ace2k host interface 26 and later only (has_wait)
    def set_lane_wait(self, lane_index: int) -> None: ...
    def set_lane_follow(
        self, lane_index: int, speed_mm_s: float | None = None, on_insert: bool = True
    ) -> None: ...
    def follow_paused(self, lane_index: int) -> bool: ...
    # why: ace2k host interface 21 and later only
    def hold_follow(self, lane_index: int, held: bool, why: str | None = None) -> None: ...
    def default_move(self, lane_index: int) -> FeedMove | None: ...
    def set_lane_extruder(self, lane_index: int, extruder_name: str | None) -> None: ...
    def move_wait_s(
        self, lane_index: int, length_mm: float, speed_mm_s: float, *, reverse: bool = False
    ) -> float: ...


class ArmedStart(Protocol):
    """UnitFeed.start_move with a stop on the plunger (ace2k host interface 20, has_stop_when).

    ``stop_when`` names the stops that the unit makes itself: "held" and "at_rest". ``held_ms`` is
    the held window of this move (None: the unit's own). Only an ace2k with has_stop_when takes
    them, so the adapter calls it only through start_armed.
    """

    def __call__(  # noqa: PLR0913 — ace2k's start_move, which grows by keywords
        self,
        lane_index: int,
        mode: str,
        length_mm: float,
        speed_mm_s: float,
        *,
        stop_when: frozenset[str],
        held_ms: int | None = None,
    ) -> FeedMove: ...


class Unit(Protocol):
    """An ace2k unit (the printer object ace2k or ace2k <name>).

    It has a name (None for [ace2k]), a host interface and a status.
    """

    name: str | None
    feed: UnitFeed

    def get_status(self, eventtime: float) -> Status: ...


class Coordinator(Protocol):
    """The coordinator of ace2k (the printer object ace2k_units).

    It gives the names of the units for the console and finds a unit by its name. It also finds
    the unit that the UNIT= of a command names. Its G-code error is raised as it is. It returns
    None for a declared unit that is not loaded yet. Its loaded units (units) are read by name.
    """

    def unit_for(self, gcmd: klipper.GCodeCommand) -> Unit | None: ...
    def find(self, name: str | None) -> Unit | None: ...
    def several(self) -> bool: ...
    def label(self, unit: Unit) -> str: ...
    def lane_text(self, unit: Unit, lane: int) -> str: ...
    def lane_args(self, unit: Unit, lane: int) -> str: ...
    def hand_over(self, unit: Unit, index: int, pause_wait_s: float) -> None: ...
