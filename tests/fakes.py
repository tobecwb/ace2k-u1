"""A fake of the U1's side-feeder module (filament_feed) with the interface the adapter relies on
(as in the U1's own klippy/extras/filament_feed.py, Snapmaker Klipper 1.6.0) and a fake ace2k.
No Snapmaker code."""

import shlex
import types

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


def fake_module():
    return types.SimpleNamespace(**CONSTANTS)


class Port:
    def __init__(self):
        self.detected = False

    def get_filament_detected(self):
        return self.detected


class Wheel:
    def __init__(self, ppr=6):
        self.ppr = ppr

    def get_counts(self):
        return 0

    def get_rpm(self):
        return 0.0

    def get_last_report_time(self):
        return 0.0


class HeadSensor:
    def __init__(self):
        self.detected = False

    def get_status(self, eventtime):
        return {"filament_detected": self.detected, "enabled": True}


class Motor:
    """The U1's side feed motor: run_one_cycle(dir, value, time), a short pulse, recorded."""

    def __init__(self):
        self.calls = []

    def run_one_cycle(self, dir, value, time):  # the U1's own parameter names
        self.calls.append((dir, value, time))


class ExceptionManager:
    def __init__(self):
        self.raised = []
        self.list = types.SimpleNamespace(
            MODULE_ID_TOOLHEAD=523, MODULE_ID_FEEDING=525, CODE_TOOLHEAD_FILAMENT_RUNOUT=0
        )

    def raise_exception_async(self, **kw):
        self.raised.append(kw)


class FakeFeed:
    """Interface-compatible with FilamentFeed: the same attribute names and method parameter
    names; methods record their calls."""

    def __init__(self, reactor, filament_ch=(1, 0)):
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

    def _port_event_handler(self, detected, channel):
        self.calls.append(("port_event", detected, channel))
        if not detected:
            # the U1's FEED_ACT_REMOVE_FILAMENT, from an async callback as in the U1 (run by
            # reactor.run_async): wait_insert, or preload_finish while its port (the hooked
            # inlet getter) still reads a filament — a loaded head's channel leaves load_finish
            self.reactor.register_async_callback(lambda et, ch=channel: self._remove(ch))

    def _remove(self, channel):
        port = self._port[channel].get_filament_detected()
        self._set_channel_state(channel, "preload_finish" if port else "wait_insert", True)

    def _put_into_drive(self, channel):
        self.calls.append(("put_into_drive", channel))

    def _set_channel_state(self, channel, state, save=False):
        self.channel_state[channel] = state
        self.calls.append(("state", channel, state, save))

    def _do_feed(self, ch, action=None, stage=None, auto_mode=None):
        self.calls.append(("do_feed", ch, action, stage, auto_mode))

    def _hang_neutral(self, channel):
        self.calls.append(("hang_neutral", channel))


class FakeReactor:
    NEVER = 9.0e99
    NOW = 0.0

    def __init__(self):
        self.now = 100.0
        self.async_callbacks = []
        self.timers = []

    def monotonic(self):
        return self.now

    def pause(self, waketime):
        self.now = max(self.now, waketime)
        for hook in list(getattr(self, "on_pause", [])):
            hook(self.now)
        return self.now

    def register_async_callback(self, cb):
        self.async_callbacks.append(cb)

    def run_async(self):
        while self.async_callbacks:
            self.async_callbacks.pop(0)(self.now)

    def register_timer(self, cb, waketime=NEVER):
        self.timers.append([cb, waketime])
        return self.timers[-1]

    def update_timer(self, timer, waketime):
        timer[1] = waketime

    def unregister_timer(self, timer):
        if timer in self.timers:
            self.timers.remove(timer)

    def run_timers(self, until=None):
        """Run the due timers, advancing the clock to each waketime up to until (default: now)."""
        until = self.now if until is None else until
        while True:
            due = [t for t in self.timers if t[1] <= until]
            if not due:
                return
            timer = min(due, key=lambda t: t[1])
            self.now = max(self.now, timer[1])
            timer[1] = timer[0](self.now)


class FakeMove:
    def __init__(self, lane, mode, seq):
        self.lane, self.mode, self.seq = lane, mode, seq
        self.event = None

    def done(self):
        return self.event is not None

    def result(self):
        return self.event


class FakeFeedApi:
    API_VERSION = 6
    move_max_mm = 2000.0
    has_follow = True  # the unit's image carries the follow (read by ace2k at connect)
    has_grip = True  # and the grip, with its default and bounds (the dictionary's)
    grip_mm = 40.0
    grip_min_mm = 10.0
    grip_max_mm = 45.0
    report_hz = 1.0

    def __init__(self):
        self.started = []
        self.stopped = []
        self.cleared = []
        self.refuse = None  # a reason string makes start_move raise FeedRefused-like
        self.refusals = []  # reasons consumed one per start_move, before refuse applies
        self.attempts = 0
        self.log = []  # ("start", lane, mode, length) and ("stop", lane), in call order
        self.extruders = []  # (lane, extruder name) per set_lane_extruder, in call order
        self.grips = []  # (lane, mm) per set_lane_grip, in call order (0: cleared)
        self.grip_fail = None  # an exception set_lane_grip raises

    def start_move(self, lane_index, mode, length_mm, speed_mm_s):
        self.attempts += 1
        reason = self.refusals.pop(0) if self.refusals else self.refuse
        if reason:
            err = Exception(f"refused: {reason}")
            err.reason = reason
            raise err
        move = FakeMove(lane_index, mode, len(self.started) + 1)
        self.log.append(("start", lane_index, mode, length_mm))
        self.started.append((lane_index, mode, length_mm, speed_mm_s, move))
        return move

    def stop(self, lane_index):
        self.stopped.append(lane_index)
        self.log.append(("stop", lane_index))

    def clear(self, lane_index):
        self.cleared.append(lane_index)

    def set_lane_grip(self, lane_index, grip_mm):
        if self.grip_fail is not None:
            raise self.grip_fail
        if not 0 <= lane_index < 4 or (grip_mm and not 10.0 <= grip_mm <= 45.0):
            raise ValueError(f"no lane {lane_index} / grip {grip_mm}")
        self.grips.append((lane_index, grip_mm))

    def set_lane_extruder(self, lane_index, extruder_name):
        if not 0 <= lane_index < 4:
            raise ValueError(f"no lane {lane_index}")
        self.extruders.append((lane_index, extruder_name))


class FakeExtruder:
    """The U1's PrinterExtruder as the adapter reads it: its section name, its index and its
    position at a print time."""

    def __init__(self, extruder_num):
        self.extruder_num = extruder_num
        self.name = f"extruder{extruder_num}" if extruder_num else "extruder"
        self.position = 0.0  # mm, what find_past_position reads
        self.asked = []  # the print times asked

    def find_past_position(self, print_time):
        self.asked.append(print_time)
        return self.position


def extruder_list(count=4):
    """The U1's printer object "extruder_list": its extruders in index order."""
    return [FakeExtruder(i) for i in range(count)]


class FakeAce2k:
    def __init__(self):
        self.feed = FakeFeedApi()
        self.link_proven = True
        self.lanes = [
            dict(
                insert=None, mode="idle", encoder_mm=0.0, rest=True, pushed=False, tag=dict(NO_TAG)
            )
            for _ in range(4)
        ]

    def get_status(self, eventtime):
        return {"link_proven": self.link_proven, "lanes": [dict(lane) for lane in self.lanes]}


NO_TAG = dict(state=None, uid=None, source=None, record=None)


class FakePrintTaskConfig:
    """The U1's print_task_config as the tags use it: its per-head filament setting in its
    status (lists, one entry per head; unset NONE / FFFFFFFF) and SET_PRINT_FILAMENT_CONFIG,
    applied by FakeGcode. Its handler reads the parameters the adapter's check looks for."""

    def __init__(self, heads=4):
        self.status = dict(
            filament_vendor=["NONE"] * heads,
            filament_type=["NONE"] * heads,
            filament_sub_type=["NONE"] * heads,
            filament_color_rgba=["FFFFFFFF"] * heads,
        )
        self.fail = None  # an exception raised by the next status read

    def get_status(self, eventtime=None):
        if self.fail is not None:
            raise self.fail
        return dict(self.status)

    def cmd_SET_PRINT_FILAMENT_CONFIG(self, gcmd):  # noqa: N802 — the U1's own name
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
    def __init__(self, params):
        self.params = params

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None):
        value = self.params.get(name)
        return default if value is None else int(value)


class FakeGcode:
    """Records run_script lines and the console; runs SET_PRINT_FILAMENT_CONFIG on ptc, its
    parameters split as the U1's parser does (quote-aware). fail: an exception to raise."""

    def __init__(self, ptc=None):
        self.ptc = ptc
        self.scripts = []
        self.lines = []
        self.fail = None

    def respond_info(self, msg):
        self.lines.append(msg)

    def run_script(self, script):
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
    <vendor>_<type>_<subtype>_<key>, plus the tables' own entries."""

    def __init__(self, keys=()):
        self._config_standard_04 = {"version": "1.0.0", "hard_filaments_max_flow_k": 0.4}
        self._config_standard_04.update({k: 0 for k in keys})

    def get_status(self, eventtime=None):
        return {}


class FakePrintStats:
    def __init__(self, state="standby"):
        self.state = state


class FakePauseResume:
    """The U1's pause_resume as the pauses use it: send_pause_command, recorded."""

    def __init__(self):
        self.pause_commands = 0

    def send_pause_command(self):
        self.pause_commands += 1


class FakeToolhead:
    """The U1's toolhead as the tags read it: get_extruder().get_name(), the active one."""

    def __init__(self, name="extruder"):
        self.name = name

    def get_extruder(self):
        return types.SimpleNamespace(get_name=lambda: self.name)
