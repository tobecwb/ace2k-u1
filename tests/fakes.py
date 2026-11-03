"""A fake of the U1's side-feeder module (filament_feed) with the interface the adapter relies on
(as in the U1's own klippy/extras/filament_feed.py, Snapmaker Klipper 1.6.0) and a fake ace2k.
No Snapmaker code."""

from __future__ import annotations

import shlex
import time
import types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from typing import Any

    from ace2k_klipper_types import FeedEvent, GCodeCommand
    from ace2k_u1_printer_types import Status

    # a reactor timer: the mutable [callback, waketime] that run_timers rewrites
    Timer = list[Any]
    # a lane as ace2k's coordinator keys it: (unit name, lane index)
    UnitLane = tuple[str | None, int]

CONSTANTS = dict(
    FEED_CHANNEL_NUMS=2,
    FEED_CHANNEL_1=0,
    FEED_CHANNEL_2=1,
    FEED_MOTOR_DIR_A=1,
    FEED_MOTOR_DIR_B=2,
    FEED_OK="ok",
    FEED_ERR="general",
    FEED_ERR_TIMEOUT="timeout",
    FEED_ERR_NO_FILAMENT="no_filament",
    FEED_ERR_RESIDUAL_FILAMENT="residual_filament",
    FEED_ERR_MOTOR_SPEED="motor_speed",
    FEED_ERR_WHEEL_SPEED="wheel_speed",
    FEED_ERR_DISTANCE="distance",
    FEED_ACT_PRELOAD="preload",
    FEED_ACT_LOAD="load",
    FEED_ACT_UNLOAD="unload",
    FEED_UNLOAD_STAGE_PREPARE="prepare",
    FEED_UNLOAD_STAGE_DOING="doing",
    FEED_UNLOAD_STAGE_CANCEL="cancel",
    FEED_ACT_UPDATE_AUTO_MODE="update_auto_mode",
    FEED_STA_NONE="none",
    FEED_STA_INITED="inited",
    FEED_STA_WAIT_INSERT="wait_insert",
    FEED_STA_PRELOAD_PREPARE="preload_prepare",
    FEED_STA_PRELOAD_FEEDING="preload_feeding",
    FEED_STA_PRELOAD_FINISH="preload_finish",
    FEED_STA_PRELOAD_FAIL="preload_fail",
    FEED_STA_LOAD_PREPARE="load_prepare",
    FEED_STA_LOAD_HOMING="load_homing",
    FEED_STA_LOAD_PICKING="load_picking",
    FEED_STA_LOAD_HEATING="load_heating",
    FEED_STA_LOAD_FEEDING="load_feeding",
    FEED_STA_LOAD_EXTRUDING="load_extruding",
    FEED_STA_LOAD_FLUSHING="load_flushing",
    FEED_STA_LOAD_FINISH="load_finish",
    FEED_STA_LOAD_FAIL="load_fail",
    FEED_STA_UNLOAD_PREPARE="unload_prepare",
    FEED_STA_UNLOAD_HOMING="unload_homing",
    FEED_STA_UNLOAD_PICKING="unload_picking",
    FEED_STA_UNLOAD_HEATING="unload_heating",
    FEED_STA_UNLOAD_HEAT_FINISH="unload_heat_finish",
    FEED_STA_UNLOAD_DOING="unload_doing",
    FEED_STA_UNLOAD_FINISH="unload_finish",
    FEED_STA_UNLOAD_FAIL="unload_fail",
    FEED_STA_MANUAL_PREPARE="manual_sta_prepare",
    FEED_STA_MANUAL_HOMING="manual_sta_homing",
    FEED_STA_MANUAL_PICKING="manual_sta_picking",
    FEED_STA_MANUAL_PREPARE_FINISH="manual_sta_prepare_finish",
    FEED_STA_MANUAL_PREPARE_FAIL="manual_sta_prepare_fail",
    FEED_STA_MANUAL_HEATING="manual_sta_heating",
    FEED_STA_MANUAL_EXTRUDING="manual_sta_extruding",
    FEED_STA_MANUAL_EXTRUDE_FINISH="manual_sta_extrude_finish",
    FEED_STA_MANUAL_EXTRUDE_FAIL="manual_sta_extrude_fail",
    FEED_STA_MANUAL_FLUSHING="manual_sta_flushing",
    FEED_STA_MANUAL_FLUSH_FINISH="manual_sta_flush_finish",
    FEED_STA_MANUAL_FLUSH_FAIL="manual_sta_flush_fail",
    FEED_STA_MANUAL_FINISH="manual_sta_finish",
    FEED_STA_MANUAL_FAIL="manual_sta_fail",
    FEED_STA_TEST="test",
    FEED_WHEEL_CIRCUMFERENCE=31.4159,
)


def fake_module() -> types.SimpleNamespace:
    return types.SimpleNamespace(**CONSTANTS)


class Port:
    def __init__(self) -> None:
        self.detected = False

    def get_filament_detected(self) -> bool:
        return self.detected


class Wheel:
    def __init__(self, ppr: int = 6) -> None:
        self.ppr = ppr

    def get_counts(self) -> int:
        return 0

    def get_rpm(self) -> float:
        return 0.0

    def get_last_report_time(self) -> float:
        return 0.0


class HeadSensor:
    """The U1's head sensor: detected is its switch (runout_buttun_state, the U1's spelling);
    latched, when set, is what its runout helper reports instead. While printing, the U1 keeps
    filament_detected True after the switch empties until the extruder has moved on."""

    def __init__(self) -> None:
        self.detected = False
        self.latched = None

    @property
    def runout_buttun_state(self) -> bool:
        # unlatched, the switch is read through get_status, so a test that breaks or counts the
        # sensor's reads (by replacing get_status) still sees each one
        if self.latched is None:
            return self.get_status(None)["filament_detected"]
        return self.detected

    def get_status(self, eventtime: float | None) -> dict[str, bool]:
        shown = self.detected if self.latched is None else self.latched
        return {"filament_detected": shown, "enabled": True}


class PlainHeadSensor:
    """A head sensor with only the runout helper's status (another Klipper build)."""

    def __init__(self) -> None:
        self.detected = False

    def get_status(self, eventtime: float | None) -> dict[str, bool]:
        return {"filament_detected": self.detected, "enabled": True}


class Motor:
    """The U1's side feed motor: run_one_cycle(dir, value, time), a short pulse, recorded."""

    def __init__(self) -> None:
        self.calls = []

    # the U1's own parameter names
    def run_one_cycle(self, dir: int, value: float, time: float) -> None:
        self.calls.append((dir, value, time))


class ExceptionManager:
    def __init__(self) -> None:
        self.raised = []
        self.list = types.SimpleNamespace(
            MODULE_ID_TOOLHEAD=523, MODULE_ID_FEEDING=525, CODE_TOOLHEAD_FILAMENT_RUNOUT=0
        )

    def raise_exception_async(self, **kw: object) -> None:
        self.raised.append(kw)


class FakeFeed:
    """Interface-compatible with FilamentFeed: the same attribute names and method parameter
    names; methods record their calls."""

    def __init__(self, reactor: FakeReactor, filament_ch: Iterable[int] = (1, 0)) -> None:
        self.reactor = reactor
        self.filament_ch = list(filament_ch)
        self._port = [Port(), Port()]
        self.wheel = [Wheel(), Wheel()]
        self.wheel_2 = [Wheel(), Wheel()]
        self.module_exist = [False, False]
        self.runout_sensor = [HeadSensor(), HeadSensor()]
        self.motor = Motor()
        self.config = {"auto_mode": [True, True], "load_finish": [False, False]}
        self.channel_active = None
        self.channel_error = ["ok", "ok"]
        self.exception_code = [0, 0]
        self.channel_state = ["wait_insert", "wait_insert"]
        self.channel_error_state = ["none", "none"]
        self.manual_feeding = [False, False]
        self.exception_manager = ExceptionManager()
        self.calls = []

    def _port_event_handler(self, detected: bool, channel: int) -> None:
        self.calls.append(("port_event", detected, channel))
        if not detected:
            # the U1's FEED_ACT_REMOVE_FILAMENT, from an async callback as in the U1 (run by
            # reactor.run_async): wait_insert, or preload_finish while its port (the hooked
            # inlet getter) still reads a filament — a loaded head's channel leaves load_finish
            self.reactor.register_async_callback(lambda et, ch=channel: self._remove(ch))

    def _remove(self, channel: int) -> None:
        port = self._port[channel].get_filament_detected()
        self._set_channel_state(channel, "preload_finish" if port else "wait_insert", True)

    def _put_into_drive(self, channel: int) -> None:
        self.calls.append(("put_into_drive", channel))

    def _set_channel_state(self, channel: int, state: str, save: bool = False) -> None:
        self.channel_state[channel] = state
        self.calls.append(("state", channel, state, save))

    def _do_feed(
        self,
        ch: int,
        action: str | None = None,
        stage: str | None = None,
        auto_mode: bool | None = None,
    ) -> None:
        self.calls.append(("do_feed", ch, action, stage, auto_mode))

    def _hang_neutral(self, channel: int) -> None:
        self.calls.append(("hang_neutral", channel))


class FakeReactor:
    NEVER = 9.0e99
    NOW = 0.0

    def __init__(self) -> None:
        self.now = 100.0
        self.async_callbacks = []
        self.timers = []
        self.on_pause = []  # hooks called with the new time after each pause (tests append)

    def monotonic(self) -> float:
        return self.now

    def pause(self, waketime: float) -> float:
        self.now = max(self.now, waketime)
        for hook in list(getattr(self, "on_pause", [])):
            hook(self.now)
        return self.now

    def register_async_callback(self, cb: Callable[[float], object]) -> None:
        self.async_callbacks.append(cb)  # a list's append: safe from another thread

    def run_async(self) -> None:
        while self.async_callbacks:
            self.async_callbacks.pop(0)(self.now)

    def completion(self) -> FakeCompletion:
        return FakeCompletion(self)

    def register_timer(self, cb: Callable[[float], float], waketime: float = NEVER) -> Timer:
        self.timers.append([cb, waketime])
        return self.timers[-1]

    def update_timer(self, timer: Timer, waketime: float) -> None:
        timer[1] = waketime

    def unregister_timer(self, timer: Timer) -> None:
        if timer in self.timers:
            self.timers.remove(timer)

    def run_timers(self, until: float | None = None) -> None:
        """Run the due timers, advancing the clock to each waketime up to until (default: now)."""
        until = self.now if until is None else until
        while True:
            due = [t for t in self.timers if t[1] <= until]
            if not due:
                return
            timer = min(due, key=lambda t: t[1])
            self.now = max(self.now, timer[1])
            timer[1] = timer[0](self.now)


class FakeCompletion:
    """reactor.completion(): wait() pauses the reactor (its clock left: a thread is what it
    waits for) and runs the async callbacks until complete() is called, or a waketime given
    passes."""

    def __init__(self, reactor: FakeReactor) -> None:
        self.reactor = reactor
        self.done = False
        self.result = None

    def test(self) -> bool:
        return self.done

    def complete(self, result: object) -> None:
        self.done, self.result = True, result

    def wait(self, waketime: float = FakeReactor.NEVER, waketime_result: object = None) -> object:
        while not self.done:
            if self.reactor.now >= waketime:
                return waketime_result
            self.reactor.pause(self.reactor.now)
            self.reactor.run_async()
            if not self.done:
                time.sleep(0.001)  # the thread's turn
        return self.result


NOT_PASSED = "absent"  # what free_tips records for a start that gave no free_tip keyword


class FakeMove:
    def __init__(self, lane: int, mode: str, seq: int) -> None:
        self.lane, self.mode, self.seq = lane, mode, seq
        self.event = None

    def done(self) -> bool:
        return self.event is not None

    def result(self) -> FeedEvent | None:
        return self.event


# the tail ace2k's move_wait_s counts with reverse: the unit's ACE2K_FEED_UNLOAD_TAIL_UM, in mm
TAIL_MM = 100.0


class FakeFeedApi:
    API_VERSION = 15
    move_max_mm = 2000.0
    has_follow = True  # the unit's image carries the follow (read by ace2k at connect)
    follow_default = True  # ace2k's [ace2k] follow_default (API_VERSION 8)
    has_grip = True  # and the grip, with its default and bounds (the dictionary's)
    has_free_rollback = True  # and a rollback's free_tip (API_VERSION 14)
    has_park = True  # and the park, in the grip's slot (API_VERSION 15)
    has_wait = False  # the wait, in the grip's slot (API_VERSION 26); the tests of an older image
    has_stop_when = False  # the unit can end a move on its plunger itself (API_VERSION 20)
    # the held window's bounds, and the filament travel that restarts it at held_mm 3 (the
    # defaults of ace2k: three counts of 1 481 µm)
    held_ms_min = 50
    held_ms_max = 2000
    held_restart_mm = 4.443
    grip_mm = 40.0
    grip_min_mm = 10.0
    grip_max_mm = 45.0
    report_hz = 1.0
    gone_after_s = 2.0  # ace2k's span for a motion reported idle without its final event
    tail_mm = TAIL_MM  # the tail the unit may run after a roll back (ace2k's tail_mm)
    load_park_mm = 300.0  # the unit's load to the parking point (ace2k's [ace2k] load_park_mm)
    load_speed_mm_s = 60.0  # the unit's load speed (ace2k's [ace2k] load_speed)
    search_mm = 750.0  # the reach of the load's tag search (ace2k's rfid_search_mm)

    def __init__(self) -> None:
        self.started = []
        self.stopped = []
        self.cleared = []
        self.refuse = None  # a reason string makes start_move raise FeedRefused-like
        self.refusals = []  # reasons consumed one per start_move, before refuse applies
        self.attempts = 0
        self.free_tips = []  # (lane, mode, free_tip or NOT_PASSED) per start
        self.stop_whens = []  # (lane, mode, frozenset of stop_when or NOT_PASSED) per start
        self.held_mss = []  # (lane, mode, held_ms or NOT_PASSED) per start
        self.log = []  # ("start", lane, mode, length) and ("stop", lane), in call order
        self.extruders = []  # (lane, extruder name) per set_lane_extruder, in call order
        # (lane, mm) per set_lane_grip, (lane, "park") per set_lane_park, (lane, "wait") per
        # set_lane_wait, in order
        self.grips = []
        self.grip_fail = None  # an exception set_lane_grip raises
        self.lane_follow = {}  # lane -> (speed, on_insert) per set_lane_follow (API_VERSION 8)
        self.default_moves = {}  # lane -> the Move ace2k's follow_default armed (default_move)
        self.paused_lanes = set()  # lanes ace2k pauses for a G-code move (follow_paused)
        self.holds = []  # (lane, held) per hold_follow, in call order
        self.on_start = None  # called with each new FakeMove (a test sets its final event)
        self.on_push = None  # called on each "feed" start (a test lets the push reach the head)
        self.waits = []  # (lane, length, speed) per move_wait_s (API_VERSION 17), in call order
        self.reverse_waits = []  # the same, for each one asked with reverse (the tail counted)
        self.wait_s = None  # what move_wait_s returns (None: ace2k's budget, landing left out)
        self.speeds = []  # (lane, speed) per set_speed (API_VERSION 13), in call order
        self.speed_fail = None  # an exception set_speed raises

    def move_wait_s(
        self, lane_index: int, length_mm: float, speed_mm_s: float, *, reverse: bool = False
    ) -> float:
        self.waits.append((lane_index, length_mm, speed_mm_s))
        if reverse:
            self.reverse_waits.append((lane_index, length_mm, speed_mm_s))
        if self.wait_s is not None:
            return self.wait_s
        return 1.5 * (length_mm + (TAIL_MM if reverse else 0.0)) / speed_mm_s + 5.0

    def start_move(
        self,
        lane_index: int,
        mode: str,
        length_mm: float,
        speed_mm_s: float,
        free_tip: bool | str = NOT_PASSED,
        *,
        stop_when: Iterable[str] | str = NOT_PASSED,
        held_ms: int | str | None = NOT_PASSED,
    ) -> FakeMove:
        passed = stop_when != NOT_PASSED
        if passed and stop_when and not self.has_stop_when:
            raise RuntimeError("ace2k: this image cannot stop on the plunger")  # as ace2k does
        if held_ms not in (NOT_PASSED, None):  # as ace2k checks it, before anything is sent
            if not passed or "held" not in stop_when:
                raise ValueError(f"ace2k: held_ms {held_ms!r} without held")
            if not self.held_ms_min <= held_ms <= self.held_ms_max:
                raise ValueError(f"ace2k: held_ms {held_ms}: out of bounds")
        self.attempts += 1
        self.free_tips.append((lane_index, mode, free_tip))
        self.stop_whens.append((lane_index, mode, frozenset(stop_when) if passed else NOT_PASSED))
        self.held_mss.append((lane_index, mode, held_ms))
        reason = self.refusals.pop(0) if self.refusals else self.refuse
        if reason:
            err = Exception(f"refused: {reason}")
            err.reason = reason
            raise err
        move = FakeMove(lane_index, mode, len(self.started) + 1)
        self.log.append(("start", lane_index, mode, length_mm))
        self.started.append((lane_index, mode, length_mm, speed_mm_s, move))
        if self.on_start is not None:
            self.on_start(move)
        if mode == "feed" and self.on_push is not None:
            self.on_push()
        return move

    def stop(self, lane_index: int) -> None:
        self.stopped.append(lane_index)
        self.log.append(("stop", lane_index))

    def set_speed(self, lane_index: int, speed_mm_s: float) -> None:
        if self.speed_fail is not None:
            raise self.speed_fail
        self.speeds.append((lane_index, speed_mm_s))
        self.log.append(("speed", lane_index, speed_mm_s))

    def clear(self, lane_index: int) -> None:
        self.cleared.append(lane_index)

    def set_lane_grip(self, lane_index: int, grip_mm: float) -> None:
        if self.grip_fail is not None:
            raise self.grip_fail
        if not 0 <= lane_index < 4 or (grip_mm and not 10.0 <= grip_mm <= 45.0):
            raise ValueError(f"no lane {lane_index} / grip {grip_mm}")
        self.grips.append((lane_index, grip_mm))

    def set_lane_park(self, lane_index: int) -> None:
        if self.grip_fail is not None:
            raise self.grip_fail
        if not 0 <= lane_index < 4:
            raise ValueError(f"no lane {lane_index}")
        self.grips.append((lane_index, "park"))

    def set_lane_wait(self, lane_index: int) -> None:
        if self.grip_fail is not None:
            raise self.grip_fail
        if not 0 <= lane_index < 4:
            raise ValueError(f"no lane {lane_index}")
        self.grips.append((lane_index, "wait"))

    def set_lane_follow(
        self, lane_index: int, speed_mm_s: float | None = None, on_insert: bool = True
    ) -> None:
        if not 0 <= lane_index < 4:
            raise ValueError(f"no lane {lane_index}")
        self.lane_follow[lane_index] = (speed_mm_s, on_insert)

    def follow_paused(self, lane_index: int) -> bool:
        return lane_index in self.paused_lanes

    def hold_follow(self, lane_index: int, held: bool, why: str = NOT_PASSED) -> None:
        # a reason (API_VERSION 21) is recorded as a third item
        self.holds.append((lane_index, held) if why == NOT_PASSED else (lane_index, held, why))

    def default_move(self, lane_index: int) -> FakeMove | None:
        move = self.default_moves.get(lane_index)
        return None if move is None or move.done() else move

    def set_lane_extruder(self, lane_index: int, extruder_name: str | None) -> None:
        if not 0 <= lane_index < 4:
            raise ValueError(f"no lane {lane_index}")
        self.extruders.append((lane_index, extruder_name))


class FakeHeater:
    """An extruder's heater as the adapter reads it: get_temp(eventtime) -> (temp, target)."""

    def __init__(self, temp: float = 25.0) -> None:
        self.temp, self.target = temp, 0.0
        self.cooling = 0.0  # °C lost per reactor second when the target is 0 (tests set it)

    def get_temp(self, eventtime: float) -> tuple[float, float]:
        return self.temp, self.target


class FakeFan:
    """The U1's binding fan of an extruder: set_speed_from_command(value) and get_status."""

    def __init__(self, max_power: float = 1.0) -> None:
        self.max_power = max_power
        self.speed = 0.0  # what the command asked, before max_power
        self.set = []  # the speeds asked, in order
        self.fail = False  # set_speed_from_command raises (a shutdown)

    def set_speed_from_command(self, value: float) -> None:
        if self.fail:
            raise RuntimeError("Klipper shut down")
        self.set.append(value)
        self.speed = value

    def get_status(self, eventtime: float) -> dict[str, float]:
        return {"speed": self.speed * self.max_power, "rpm": 0.0}  # as Klipper reports it


class FakeExtruder:
    """The U1's PrinterExtruder as the adapter reads it: its section name, its index, its
    position at a print time, its heater and the U1's own attributes of it."""

    def __init__(self, extruder_num: int) -> None:
        self.extruder_num = extruder_num
        self.name = f"extruder{extruder_num}" if extruder_num else "extruder"
        self.position = 0.0  # mm, what find_past_position reads
        self.asked = []  # the print times asked
        self.heater = FakeHeater()
        self.binding_fan: FakeFan | None = FakeFan()
        self.printing_e_pos = 0.0
        self.nozzle_diameter = 0.4
        self.nozzle_volume_type = "standard"

    def get_heater(self) -> FakeHeater:
        return self.heater

    def find_past_position(self, print_time: float) -> float:
        self.asked.append(print_time)
        return self.position


def extruder_list(count: int = 4) -> list[FakeExtruder]:
    """The U1's printer object "extruder_list": its extruders in index order."""
    return [FakeExtruder(i) for i in range(count)]


class FakeRfid:
    """ace2k's tag reader as the adapter uses it: start_read (API_VERSION 7) recorded; fail: an
    exception it raises instead."""

    def __init__(self) -> None:
        self.reads = []
        self.fail = None

    def start_read(self, lane_index: int) -> None:
        self.reads.append(lane_index)
        if self.fail is not None:
            raise self.fail


class FakeAce2k:
    """One unit as the adapter holds it: its name (None for the unnamed [ace2k]), its section,
    its feed and tag reader, its status."""

    def __init__(self, name: str | None = None) -> None:
        self.name = name
        self.section = "ace2k" if name is None else f"ace2k {name}"
        self.feed = FakeFeedApi()
        self.rfid = FakeRfid()
        self.link_proven = True
        self.lanes = [
            dict(
                insert=None, mode="idle", encoder_mm=0.0, rest=True, pushed=False, tag=dict(NO_TAG)
            )
            for _ in range(4)
        ]

        # the sensors reports received (ace2k's sensors_reports): with a clock, one per 100 ms of
        # its time (the unit's 10 Hz); without one, this fixed count (no report arriving)
        self.clock = None
        self.reports = 0
        self.has_reports = True  # an older ace2k's status carries no count
        # the motor reports received (ace2k's motor_reports), or None: an ace2k without the count
        self.motor_reports: int | None = None
        self.status_reads = 0  # the calls of get_status, so a test can tell nothing polled

    def get_status(self, eventtime: float) -> Status:
        self.status_reads += 1
        status = {"link_proven": self.link_proven, "lanes": [dict(lane) for lane in self.lanes]}
        if self.has_reports:
            clock = self.clock
            status["sensors_reports"] = (
                self.reports if clock is None else int(round(clock.now * 10.0, 6))
            )
        if self.motor_reports is not None:
            status["motor_reports"] = self.motor_reports
        return status


NO_TAG = dict(state=None, uid=None, source=None, record=None)


class FakeUnits:
    """ace2k's coordinator (the printer object ace2k_units) as the adapter uses it: the loaded
    units, find, unit_for (UNIT=, as ace2k resolves it), several, label, lane_text and
    lane_args."""

    def __init__(self, units: Iterable[FakeAce2k]) -> None:
        self.units = list(units)
        self.names = [u.name for u in self.units]
        self.index = {u: i for i, u in enumerate(self.units)}  # unit -> its place, as ace2k's
        self.shared = {}  # extruder -> [(unit name, lane index)] that feed it (share)
        self.active = {}  # extruder -> the (unit name, lane index) chosen (ACEK_LANE_ACTIVE)
        self.released = []  # (extruder, wait_s) per release
        self.asked = []  # (unit name, lane index) per may_follow, in call order
        self.handed = []  # (unit name, lane index) per hand_over, in call order
        self.hand_over_fail = None  # an exception the next hand_over raises (a follower late)

    def share(self, extruder: str, *keys: UnitLane) -> None:
        """Lanes ((unit name, lane index), …) that feed one extruder: a shared group."""
        self.shared[extruder] = list(keys)

    def shared_lanes(self, unit: FakeAce2k, lane: int) -> str | None:
        """The lanes ("a:4, b:1") that share the lane's extruder, or None."""
        key = (unit.name, lane)
        for keys in self.shared.values():
            if key in keys:
                return ", ".join(
                    f"{name}:{n + 1}" if self.several() else str(n + 1) for name, n in keys
                )
        return None

    def may_follow(self, unit: FakeAce2k, lane: int) -> bool:
        """A lane of a shared extruder follows only when it is the active lane, as in ace2k."""
        key = (unit.name, lane)
        self.asked.append(key)
        for extruder, keys in self.shared.items():
            if key in keys:
                return self.active.get(extruder) == key
        return True

    def hand_over(self, unit: FakeAce2k, lane: int, wait_s: float) -> None:
        """Units.hand_over: the lane chosen for its shared extruder (recorded), nothing armed."""
        if self.hand_over_fail is not None:
            raise self.hand_over_fail
        key = (unit.name, lane)
        self.handed.append(key)
        for extruder, keys in self.shared.items():
            if key in keys:
                self.active[extruder] = key

    def release(self, extruder: str, wait_s: float) -> None:
        """Units.release (host interface 13): the extruder's choice undone, the call recorded."""
        self.released.append((extruder, wait_s))
        self.active.pop(extruder, None)

    def several(self) -> bool:
        return len(self.units) > 1

    def label(self, unit: FakeAce2k) -> str:
        return unit.section if self.several() else "ace2k"

    def lane_text(self, unit: FakeAce2k, lane: int) -> str:
        return f"{unit.name}:{lane + 1}" if self.several() else str(lane + 1)

    def lane_args(self, unit: FakeAce2k, lane: int) -> str:
        args = f"LANE={lane + 1}"
        return f"{args} UNIT={unit.name}" if self.several() else args

    def find(self, name: str | None) -> FakeAce2k | None:
        if name is None or not str(name).strip():  # empty or blank: as if omitted
            return self.units[0] if len(self.units) == 1 else None
        wanted = name.strip().lower()
        return next((u for u in self.units if u.name == wanted), None)

    def unit_for(self, gcmd: GCodeCommand) -> FakeAce2k:
        raw = gcmd.get("UNIT", None)
        unit = self.find(raw)
        if unit is not None:
            return unit
        if raw is None or not str(raw).strip():
            names = "|".join(str(u.name) for u in self.units)
            raise gcmd.error(
                f"ace2k: {gcmd.get_command()} needs UNIT=<{names}>: this printer has several units"
            )
        names = ", ".join(str(u.name) for u in self.units)
        raise gcmd.error(f"ace2k: no unit {raw}; units: {names}")


def one_unit_heads(unit: FakeAce2k, lanes: Iterable[int]) -> list[tuple[int, FakeAce2k, int]]:
    """lanes: as the adapter takes it on one unit, from bare lane numbers (lane n feeding head
    n-1): (head, unit, lane index) per lane."""
    return [(n - 1, unit, n - 1) for n in lanes]


def two_units(names: Iterable[str] = ("a", "b")) -> tuple[FakeUnits, dict[str, FakeAce2k]]:
    """Two named units and their coordinator: (FakeUnits, {name: FakeAce2k})."""
    units = {name: FakeAce2k(name) for name in names}
    return FakeUnits(units.values()), units


class FakePrintTaskConfig:
    """The U1's print_task_config as the tags use it: its per-head filament setting in its
    status (lists, one entry per head; unset NONE / FFFFFFFF) and SET_PRINT_FILAMENT_CONFIG,
    applied by FakeGcode. Its handler reads the parameters the adapter's check looks for."""

    def __init__(self, heads: int = 4) -> None:
        self.status = dict(
            filament_vendor=["NONE"] * heads,
            filament_type=["NONE"] * heads,
            filament_sub_type=["NONE"] * heads,
            filament_color_rgba=["FFFFFFFF"] * heads,
        )
        self.fail = None  # an exception raised by the next status read
        # the tool -> head table (32 logical tools): 0-3 on their heads, the others on head 0
        self.map_table = list(range(heads)) + [0] * (32 - heads)

    def get_status(self, eventtime: float | None = None) -> Status:
        if self.fail is not None:
            raise self.fail
        return dict(self.status, extruder_map_table=list(self.map_table))

    def cmd_SET_PRINT_FILAMENT_CONFIG(self, gcmd: Params) -> None:  # noqa: N802 — the U1's own name
        head = gcmd.get_int("CONFIG_EXTRUDER")
        vendor = gcmd.get("VENDOR", None)
        ftype = gcmd.get("FILAMENT_TYPE", None)
        subtype = gcmd.get("FILAMENT_SUBTYPE", None)
        rgba = gcmd.get("FILAMENT_COLOR_RGBA", None)
        gcmd.get_int("FORCE", False)
        if ftype is not None:
            if vendor is None or subtype is None:
                raise ValueError("incomplete parameters")
            self.status["filament_vendor"][head] = vendor
            self.status["filament_type"][head] = ftype
            self.status["filament_sub_type"][head] = subtype
        if rgba is not None:
            self.status["filament_color_rgba"][head] = rgba


class Params:
    def __init__(self, params: dict[str, str]) -> None:
        self.params = params

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.params.get(name, default)

    def get_int(self, name: str, default: int | None = None) -> int | None:
        value = self.params.get(name)
        return default if value is None else int(value)


class FakeGcode:
    """Records run_script lines and the console; runs SET_PRINT_FILAMENT_CONFIG on ptc, its
    parameters split as the U1's parser does (quote-aware). fail: an exception to raise."""

    def __init__(self, ptc: FakePrintTaskConfig | None = None) -> None:
        self.ptc = ptc
        self.scripts = []
        self.lines = []
        self.from_command = []  # run_script_from_command, in call order
        self.fail = None

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)

    def respond_raw(self, msg: str) -> None:
        # Klipper's error line ("!! "): kept in lines as the text, and in errors
        self.errors = getattr(self, "errors", [])
        self.errors.append(msg)
        self.lines.append(msg[3:] if msg.startswith("!! ") else msg)

    def run_script_from_command(self, script: str) -> None:
        """Inside a running G-code (FEED_AUTO of the U1): recorded in from_command."""
        self.from_command.append(script)

    def run_script(self, script: str) -> None:
        self.scripts.append(script)
        if self.fail is not None:
            raise self.fail
        # shlex.split stands in for the U1's _split_gcode_params: both split on blanks and drop
        # the quotes around a quoted value, which is all the adapter's G-code relies on; a script
        # of several lines runs line by line
        for line in script.splitlines():
            words = shlex.split(line)
            if not words:
                continue
            params = dict(w.split("=", 1) for w in words[1:])
            if words[0] == "SET_PRINT_FILAMENT_CONFIG" and self.ptc is not None:
                self.ptc.cmd_SET_PRINT_FILAMENT_CONFIG(Params(params))


class FakeFilamentParameters:
    """The U1's filament_parameters: an empty status, its tables in instance dicts keyed
    <vendor>_<type>_<subtype>_<key>, plus the tables' own entries. get_unload_temp,
    get_load_temp and get_is_soft take (vendor, type, subtype, nozzle_diameter,
    nozzle_volume_type), as the U1's, and are recorded; a type they do not know gives the U1's
    defaults (250 °C, not soft)."""

    def __init__(self, keys: Iterable[str] = ()) -> None:
        self._config_standard_04 = {"version": "1.0.0", "hard_filaments_max_flow_k": 0.4}
        self._config_standard_04.update({k: 0 for k in keys})
        self.unload_temps = {"PLA": 250.0, "PETG": 250.0}  # type -> the U1's unload_temp
        self.load_temps = {"PLA": 230.0, "PETG": 245.0}  # type -> the U1's load_temp
        self.soft = set()  # the types the U1 calls soft
        self.asked = []  # (method, args) per call, in call order

    def get_status(self, eventtime: float | None = None) -> Status:
        return {}

    def get_unload_temp(
        self, vendor: str, ftype: str, subtype: str, nozzle_diameter: float, nozzle_volume_type: str
    ) -> float:
        self.asked.append(
            ("unload_temp", (vendor, ftype, subtype, nozzle_diameter, nozzle_volume_type))
        )
        return self.unload_temps.get(ftype, 250.0)

    def get_load_temp(
        self, vendor: str, ftype: str, subtype: str, nozzle_diameter: float, nozzle_volume_type: str
    ) -> float:
        self.asked.append(
            ("load_temp", (vendor, ftype, subtype, nozzle_diameter, nozzle_volume_type))
        )
        return self.load_temps.get(ftype, 250.0)

    def get_is_soft(
        self, vendor: str, ftype: str, subtype: str, nozzle_diameter: float, nozzle_volume_type: str
    ) -> bool:
        self.asked.append(
            ("is_soft", (vendor, ftype, subtype, nozzle_diameter, nozzle_volume_type))
        )
        return ftype in self.soft


class U1Moves:
    """The U1's own macros as they move a head's extruder. While the print is
    printing every G1 E adds to the active head's printing_e_pos, capped at 0, and only G92
    resets it: an unload (INNER_FILAMENT_UNLOAD, or the unload flow's doing stage, which runs it)
    adds about -70 mm; a load's extrusion adds 20 mm, then its flush retracts |printing_e_pos|
    while the print is printing or paused (5 mm otherwise), recorded in flush_retract; the nozzle
    clean (ROUGHLY_CLEAN_NOZZLE_WITH_DISCARD) extrudes 5 mm; the U1's pre-extrusion
    (SM_PRINT_PREEXTRUDE_FILAMENT, which sets the tool's record, or INNER_PREEXTRUDE_FILAMENT)
    extrudes its LENGTH (20 mm by default), retracts 0.5 mm and cleans once on the active head,
    its printing_e_pos as it starts recorded in e_pos_at_preextrude. Its SOFT never changes the
    length: the U1's macro sets 10 mm only when SOFT == True, and a G-code parameter is a string.
    T<h> A0 makes head h the active one.
    The unload flow's STAGE=cancel sets the head's channel to unload_fail, then to preload_finish
    or wait_insert as its port reads. An AUTO_FEEDING without EXTRUDER= is ignored. The swap's
    macros as their defaults run: ACEK_SWAP_UNLOAD as INNER_FILAMENT_UNLOAD on the head it names
    (made active), ACEK_SWAP_LOAD as its two extrusions and its clean (SWAP_GRIP_E + PURGE_E +
    CLEAN_E; the swap's pre-extrusion cleans again) on the head it names (made active);
    swap_macros records each call's parameters, e_pos_at_macro the head's printing_e_pos as each
    starts, active_at_macro the index of the head active as each is called (before the macro
    makes its own head active).
    extruders: head -> FakeExtruder; print_stats: FakePrintStats; channels: head -> (FakeFeed,
    channel)."""

    UNLOAD_E = -70.0
    LOAD_E = 20.0  # the U1's own load flow
    CLEAN_E = 5.0
    SWAP_GRIP_E = 40.0  # the swap's load macro: from the head's sensor through the gear
    PURGE_E = 60.0  # the swap's load macro: its purge
    PREEXTRUDE_E = 20.0  # the U1's pre-extrusion, when no LENGTH is given
    PREEXTRUDE_RETRACT_E = -0.5  # the U1's pre-extrusion: its retraction before the fan

    def __init__(
        self,
        extruders: dict[int, FakeExtruder],
        print_stats: FakePrintStats,
        channels: dict[int, tuple[FakeFeed, int]] | None = None,
        gcode_move: FakeGcodeMove | None = None,
    ) -> None:
        self.extruders, self.print_stats = extruders, print_stats
        self.channels = channels or {}
        self.active = extruders[0]
        self.flush_retract = []
        self.gcode_move = gcode_move
        self.swap_macros = []  # (name, {param: value}) per call of the swap's macros
        self.e_pos_at_macro = []  # the named head's printing_e_pos as each macro starts
        self.active_at_macro = []  # the active head's index as each macro is called
        self.e_pos_at_preextrude = []  # the active head's printing_e_pos as each pre-extrusion runs

    def _add(self, ext: FakeExtruder, delta: float) -> None:
        if self.print_stats.state == "printing":
            ext.printing_e_pos = min(0.0, ext.printing_e_pos + delta)

    @staticmethod
    def _value(words: list[str], name: str) -> str | None:
        word = next((w for w in words if w.startswith(name + "=")), None)
        return None if word is None else word.split("=", 1)[1]

    def run(self, line: str) -> None:  # noqa: PLR0912 — one branch per U1 macro it plays
        words = line.split()
        if not words:
            return
        cmd = words[0]
        if cmd in ("ACEK_SWAP_UNLOAD", "ACEK_SWAP_LOAD"):
            self._swap_macro(cmd, words)
            return
        if cmd == "INNER_FILAMENT_UNLOAD":
            self._add(self.active, self.UNLOAD_E)
        elif cmd == "ROUGHLY_CLEAN_NOZZLE_WITH_DISCARD":
            self._add(self.active, self.CLEAN_E)
        elif cmd in ("SM_PRINT_PREEXTRUDE_FILAMENT", "INNER_PREEXTRUDE_FILAMENT"):
            self._preextrude(self._value(words, "INDEX"), self._value(words, "LENGTH"))
        elif cmd == "AUTO_FEEDING":
            head = self._value(words, "EXTRUDER")
            if head is None or not head.isdigit() or int(head) not in self.extruders:
                return
            ext = self.extruders[int(head)]
            stage = self._value(words, "STAGE")
            if "UNLOAD=1" in words and stage == "cancel":
                self._cancel(int(head))
            elif "UNLOAD=1" in words and stage == "doing":
                self.active = ext
                self._add(ext, self.UNLOAD_E)
            elif "LOAD=1" in words:
                self.active = ext
                self._add(ext, self.LOAD_E)
                printing = self.print_stats.state in ("printing", "paused")
                self.flush_retract.append(abs(ext.printing_e_pos) if printing else 5.0)
        elif cmd[0] == "T" and cmd[1:].isdigit() and "A0" in words:
            self.active = self.extruders[int(cmd[1:])]
        elif cmd == "G1" and any(w.startswith("E") for w in words[1:]):
            self._add(self.active, float(next(w for w in words[1:] if w.startswith("E"))[1:]))
        elif cmd == "G92" and any(w.startswith("E") for w in words[1:]):
            self.active.printing_e_pos = 0.0
        if cmd == "SAVE_GCODE_STATE" and self.gcode_move is not None:
            self.gcode_move.save(self._value(words, "NAME") or "default")

    def _swap_macro(self, cmd: str, words: list[str]) -> None:
        """One of the swap's macros, on the head it names, made active."""
        params = dict(w.split("=", 1) for w in words[1:] if "=" in w)
        self.swap_macros.append((cmd, params))
        ext = self.extruders[int(params["HEAD"])]
        self.e_pos_at_macro.append(ext.printing_e_pos)
        self.active_at_macro.append(self.active.extruder_num)
        self.active = ext
        if cmd == "ACEK_SWAP_UNLOAD":
            self._add(ext, self.UNLOAD_E)
        else:
            self._add(ext, self.SWAP_GRIP_E + self.PURGE_E)
            self._add(ext, self.CLEAN_E)

    def _preextrude(self, index: str | None, length: str | None = None) -> None:
        """The U1's pre-extrusion on the active head; with an INDEX, the tool's record set."""
        self.e_pos_at_preextrude.append(self.active.printing_e_pos)
        self._add(self.active, self.PREEXTRUDE_E if length is None else float(length))
        self._add(self.active, self.PREEXTRUDE_RETRACT_E)
        self._add(self.active, self.CLEAN_E)
        if index is not None and index.isdigit():
            self.print_stats._config["print_job"]["preextrude_filament"][int(index)] = True

    def _cancel(self, head: int) -> None:
        if head not in self.channels:
            return
        feed, ch = self.channels[head]
        feed._set_channel_state(ch, "unload_fail")
        port = feed._port[ch].get_filament_detected()
        feed._set_channel_state(ch, "preload_finish" if port else "wait_insert")


class FakeGcodeMove:
    """The U1's gcode_move as a swap reads it: SAVE_GCODE_STATE keeps the last and base
    positions (toolhead frame) under its name in saved_states; a G-code coordinate is last minus
    base, as its G1 in absolute mode sets last = value + base."""

    def __init__(self, last: Iterable[float], base: Iterable[float]) -> None:
        self.last_position, self.base_position = list(last), list(base)
        self.saved_states = {}

    def save(self, name: str) -> None:
        self.saved_states[name] = {
            "last_position": list(self.last_position),
            "base_position": list(self.base_position),
        }


class FakePrintStats:
    def __init__(self, state: str = "standby") -> None:
        self.state = state
        self.current_layer = None  # the U1's info_current_layer (SET_PRINT_STATS_INFO)
        # the U1's per-print record of the tools pre-extruded (SM_PRINT_PREEXTRUDE_FILAMENT)
        self._config = {"print_job": {"preextrude_filament": [False] * 32}}

    def get_status(self, eventtime: float | None = None) -> Status:
        return {"state": self.state, "info": {"current_layer": self.current_layer}}


class FakePauseResume:
    """The U1's pause_resume as the pauses use it: send_pause_command, recorded; is_paused and
    pause_command_sent, its own flags (a swap's PAUSE is checked against them)."""

    def __init__(self) -> None:
        self.pause_commands = 0
        self.is_paused = False
        self.pause_command_sent = False

    def send_pause_command(self) -> None:
        self.pause_commands += 1


class FakeToolhead:
    """The U1's toolhead as the tags read it: get_extruder().get_name(), the active one; and
    as a swap reads it: get_status()["homed_axes"]."""

    def __init__(self, name: str = "extruder") -> None:
        self.name = name
        self.homed_axes = "xyz"  # the U1's get_status()["homed_axes"]

    def get_status(self, eventtime: float | None = None) -> Status:
        return {"homed_axes": self.homed_axes}

    def get_extruder(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(get_name=lambda: self.name)


def give_materials(u1: object, kind: str = "PLA") -> None:
    """Give every hooked lane of the adapter an Edit spool record, as ACEK_SPOOL does.

    A load, an eject or a print with a plan refuses a lane with no material set. Tests of
    something else give their lanes one."""
    store = u1.spool_store  # type: ignore[attr-defined]
    for h in u1.adapter.hooked:  # type: ignore[attr-defined]
        store.records[store.key(h.unit, h.lane)] = {
            "type": kind,
            "color": "#ffffff",
            "vendor": "Generic",
        }


class FakeWebRequest:
    """A web request of Klipper's webhooks, as the temperature control of the screen sends it:
    get_float and get_int by name with a default, and send (recorded in sent)."""

    def __init__(self, params: Mapping[str, object]) -> None:
        self.params = dict(params)
        self.sent: list[dict[str, object]] = []

    def get_float(self, name: str, default: float | None = None) -> float | None:
        value = self.params.get(name, default)
        return None if value is None else float(value)  # type: ignore[arg-type]

    def get_int(self, name: str, default: int | None = None) -> int | None:
        value = self.params.get(name, default)
        return None if value is None else int(value)  # type: ignore[call-overload]

    def send(self, data: dict[str, object]) -> None:
        self.sent.append(data)


class FakeWebhooks:
    """Klipper's webhooks with the U1's control/extruder_temp endpoint, which records the
    heating it was asked for (S, T, A) as its reply. call runs an endpoint as a request would."""

    def __init__(self) -> None:
        self._endpoints: dict[str, Callable[[FakeWebRequest], None]] = {
            "control/extruder_temp": self._extruder_temp
        }

    @staticmethod
    def _extruder_temp(request: FakeWebRequest) -> None:
        heated = (request.get_float("S", 0.0), request.get_int("T"), request.get_int("A", 1))
        request.send({"heated": tuple(int(x) if x is not None else None for x in heated)})

    def call(self, path: str, request: FakeWebRequest) -> None:
        self._endpoints[path](request)
