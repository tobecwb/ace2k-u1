from pathlib import Path

import ace2k_u1
import fakes
import pytest
from test_config_macros import SPLITTERS, klipper_command


def test_a_feed_like_the_real_one_has_no_problems():
    ff = fakes.FakeFeed(fakes.FakeReactor())
    assert ace2k_u1.check_feed(fakes.fake_module(), ff) == []


def test_a_changed_constant_is_a_problem():
    mod = fakes.fake_module()
    mod.FEED_STA_LOAD_FINISH = "loaded"
    problems = ace2k_u1.check_feed(mod, fakes.FakeFeed(fakes.FakeReactor()))
    assert any("FEED_STA_LOAD_FINISH" in p for p in problems)


def test_a_renamed_parameter_is_a_problem():
    ff = fakes.FakeFeed(fakes.FakeReactor())
    ff._put_into_drive = lambda ch: None  # parameter renamed
    problems = ace2k_u1.check_feed(fakes.fake_module(), ff)
    assert any("_put_into_drive" in p for p in problems)


def test_a_missing_attribute_is_a_problem():
    ff = fakes.FakeFeed(fakes.FakeReactor())
    del ff.wheel_2
    assert any("wheel_2" in p for p in ace2k_u1.check_feed(fakes.fake_module(), ff))


class Gcode:
    def __init__(self):
        self.lines = []
        self.commands = {}
        self.gcode_help = {}
        self.base_gcode_handlers = {}

    def register_command(self, name, func, when_not_ready=False, desc=None):
        if func is None:  # Klipper's removal: the old handler handed back
            self.base_gcode_handlers.pop(name, None)
            return self.commands.pop(name, None)
        self.commands[name] = func
        if when_not_ready:
            self.base_gcode_handlers[name] = func
        if desc is not None:
            self.gcode_help[name] = desc
        return None

    def respond_info(self, msg):
        self.lines.append(msg)


class Printer:
    def __init__(self, reactor, objects, feeds):
        self.reactor = reactor
        self.objects = objects
        self.feeds = feeds
        self.events = []  # (name, args) per send_event, in call order

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, name, cb):
        pass

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def send_event(self, name, *args):
        self.events.append((name, args))

    def lookup_objects(self, module):
        return [(f"{module} {i}", ff) for i, ff in enumerate(self.feeds)]


# a printer without the U1's print_task_config: the tags are off, the hooks are not
TAGS_ABSENT = "ace2k_u1: tags not applied: [print_task_config] absent"


class Config:
    error = ValueError

    def __init__(self, printer, values=None):
        self.printer = printer
        self.values = values or {}

    def get(self, name, default):
        return self.values.get(name, default)

    def get_printer(self):
        return self.printer

    def getlist(self, name, default):
        return default

    def getfloat(self, name, default, above=None, minval=None, maxval=None):
        return self.values.get(name, default)

    def getint(self, name, default, minval=None, maxval=None):
        return self.values.get(name, default)

    def getboolean(self, name, default):
        return default


def test_a_problem_is_reported_and_status_says_not_hooked():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    del right.wheel_2
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left, right])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    assert left.module_exist == [False, False]
    assert gcode.lines and gcode.lines[0].startswith("ace2k_u1: not hooked: ")
    assert "wheel_2" in gcode.lines[0]
    status = Gcode()
    gcode.commands["ACE_ADAPTER_STATUS"](status)
    assert status.lines[0].startswith("ace2k_u1: not hooked: ") and "wheel_2" in status.lines[0]


def test_hooking_reports_the_lanes_and_starts_the_watch():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    gcode = Gcode()
    objects = {"gcode": gcode, "ace2k": fakes.FakeAce2k(), "extruder_list": fakes.extruder_list()}
    printer = Printer(reactor, objects, [left, right])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    assert gcode.lines == ["ace2k_u1: hooked lanes [1, 2, 3, 4]", TAGS_ABSENT]
    assert [t[0] for t in reactor.timers] == [u1.adapter._run_pauses, u1.adapter.watch]
    assert reactor.timers[0][1] == reactor.NEVER  # the pause timer waits for a pause
    assert u1.adapter.gcode is gcode
    status = Gcode()
    gcode.commands["ACE_ADAPTER_STATUS"](status)
    assert status.lines[0] == "ace2k_u1: tags off: [print_task_config] absent"
    assert len(status.lines) == 5 and status.lines[1].startswith("lane ")


def _hook_with(feed_api):
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    ace.feed = feed_api
    gcode = Gcode()
    objects = {"gcode": gcode, "ace2k": ace, "extruder_list": fakes.extruder_list()}
    printer = Printer(reactor, objects, [left, right])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    return gcode, left


def test_the_api_version_is_read_from_the_feed_object():
    # the adapter holds only printer.lookup_object("ace2k").feed: the version must be readable
    # there (ace2k carries it on the feed object's class, as the fake does); a feed object
    # without it is an interface too old to drive, whatever ace2k's module says
    assert "API_VERSION" in vars(fakes.FakeFeedApi)

    class WithoutVersion:
        move_max_mm = 2000.0
        report_hz = 1.0

    gcode, left = _hook_with(WithoutVersion())
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 6"]
    assert left.module_exist == [False, False]
    gcode, left = _hook_with(fakes.FakeFeedApi())
    assert gcode.lines == ["ace2k_u1: hooked lanes [1, 2, 3, 4]", TAGS_ABSENT]
    assert left.module_exist == [True, True]


def test_an_interface_without_the_follow_is_not_hooked_with_its_reason():
    # API_VERSION 1: an ace2k host from before the follow (assist_both)
    old = fakes.FakeFeedApi()
    old.API_VERSION = 1
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 6"]
    assert left.module_exist == [False, False]


def test_an_interface_without_the_lane_map_is_not_hooked_with_its_reason():
    # API_VERSION 2: an ace2k host from before the feed-forward (set_lane_extruder)
    old = fakes.FakeFeedApi()
    old.API_VERSION = 2
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 6"]
    assert left.module_exist == [False, False]
    assert old.extruders == []


def test_an_interface_without_the_tail_is_not_hooked_with_its_reason():
    # API_VERSION 3: an ace2k host from before the follow's tail ("tail", "tail_out")
    old = fakes.FakeFeedApi()
    old.API_VERSION = 3
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 6"]
    assert left.module_exist == [False, False]
    assert old.extruders == []


def test_an_interface_without_blocked_is_not_hooked_with_its_reason():
    # API_VERSION 4: an ace2k host from before the kind "blocked" and the tail ignoring the bay
    old = fakes.FakeFeedApi()
    old.API_VERSION = 4
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 6"]
    assert left.module_exist == [False, False]
    assert old.extruders == []


def test_an_interface_without_the_grip_is_not_hooked_with_its_reason():
    # API_VERSION 5: an ace2k host from before the grip (set_lane_grip)
    old = fakes.FakeFeedApi()
    old.API_VERSION = 5
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 6"]
    assert left.module_exist == [False, False]
    assert old.extruders == [] and old.grips == []


def test_api_min_is_six():
    assert ace2k_u1.API_MIN == 6
    assert fakes.FakeFeedApi.API_VERSION >= ace2k_u1.API_MIN


def test_a_firmware_without_the_grip_is_not_hooked_with_its_reason():
    # a current host on a unit whose image predates the grip: ace2k reads its constants at
    # connect; without it a filament put in behind an old piece would be chased by a full load
    no_grip = fakes.FakeFeedApi()
    no_grip.has_grip = False
    no_grip.grip_mm = no_grip.grip_min_mm = no_grip.grip_max_mm = None
    gcode, left = _hook_with(no_grip)
    line = "ace2k_u1: not hooked: the unit's firmware has no grip; flash v0.11.0"
    assert gcode.lines == [line]
    assert left.module_exist == [False, False]
    assert _status(gcode) == [line]
    assert no_grip.grips == [] and no_grip.started == []


def test_without_grip_mm_the_grip_is_the_units_own_default():
    # no option: the dictionary's default, as ace2k read it at connect — not a copy of it here
    assert _u1({}).grip_mm is None
    api = fakes.FakeFeedApi()
    api.grip_mm = 35.0  # another image's default
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    ace = fakes.FakeAce2k()
    ace.feed = api
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": ace}, [fakes.FakeFeed(reactor)])
    u1 = ace2k_u1.ACE2kU1(Config(printer, {}))
    u1._hook(reactor.now)
    assert "ace2k_u1: hooked lanes [1, 2]" in gcode.lines
    assert u1.adapter.grip_mm == 35.0


@pytest.mark.parametrize("grip", [9.9, 45.5, 50.0, 120.0])
def test_a_grip_outside_the_units_bounds_is_not_hooked_with_its_reason(grip):
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    ace = fakes.FakeAce2k()
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": ace}, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer, {"grip_mm": grip}))
    u1._hook(reactor.now)
    line = f"ace2k_u1: not hooked: grip_mm {grip:g} is outside the unit's 10..45 mm"
    assert gcode.lines == [line]
    assert left.module_exist == [False, False] and ace.feed.grips == []


@pytest.mark.parametrize("grip", [10.0, 25.0, 45.0])
def test_a_grip_within_the_units_bounds_hooks_and_is_the_adapters(grip):
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer, {"grip_mm": grip}))
    u1._hook(reactor.now)
    assert gcode.lines[-2] == "ace2k_u1: hooked lanes [1, 2]"
    assert u1.adapter.grip_mm == grip


def test_hooking_maps_the_lanes_to_the_printers_extruders():
    api = fakes.FakeFeedApi()
    gcode, left = _hook_with(api)
    assert gcode.lines == ["ace2k_u1: hooked lanes [1, 2, 3, 4]", TAGS_ABSENT]
    assert sorted(api.extruders) == [
        (0, "extruder"),
        (1, "extruder1"),
        (2, "extruder2"),
        (3, "extruder3"),
    ]


def test_a_failed_hook_maps_no_lane():
    # a problem hook_all itself finds (a feed module without wheel_2): nothing hooked, none mapped
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    del right.wheel_2
    ace = fakes.FakeAce2k()
    gcode = Gcode()
    objects = {"gcode": gcode, "ace2k": ace, "extruder_list": fakes.extruder_list()}
    u1 = ace2k_u1.ACE2kU1(Config(Printer(reactor, objects, [left, right])))
    u1._hook(reactor.now)
    assert u1.adapter.problems and "wheel_2" in gcode.lines[0]
    assert left.module_exist == [False, False]
    assert ace.feed.extruders == []


def test_a_firmware_without_the_follow_is_not_hooked_with_its_reason():
    # a current host on a unit whose image predates the follow's tail: ace2k reads the follow's
    # constants (the tail's among them) at connect, and every arm would be refused
    no_follow = fakes.FakeFeedApi()
    no_follow.has_follow = False
    gcode, left = _hook_with(no_follow)
    line = "ace2k_u1: not hooked: the unit's firmware has no follow tail; flash v0.11.0"
    assert gcode.lines == [line]
    assert left.module_exist == [False, False]
    assert _status(gcode) == [line]
    assert no_follow.started == []


def test_the_default_feed_speed_is_70():
    reactor = fakes.FakeReactor()
    printer = Printer(reactor, {"gcode": Gcode(), "ace2k": fakes.FakeAce2k()}, [])
    assert ace2k_u1.ACE2kU1(Config(printer)).feed_speed == 70.0


def _status(gcode):
    status = Gcode()
    gcode.commands["ACE_ADAPTER_STATUS"](status)
    return status.lines


def test_a_firmware_without_the_feed_is_not_hooked_with_its_reason():
    # ace2k loads its feed object even then, with no lane bounds (move_max_mm None)
    no_feed = fakes.FakeFeedApi()
    no_feed.move_max_mm = None
    gcode, left = _hook_with(no_feed)
    line = "ace2k_u1: not hooked: the ace2k firmware has no feed support"
    assert gcode.lines == [line]
    assert left.module_exist == [False, False]
    assert _status(gcode) == [line]


def test_an_absent_ace2k_is_not_hooked_and_status_repeats_why():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode}, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    assert _status(gcode) == ["ace2k_u1: not hooked yet"]
    assert u1._hook(reactor.now) == reactor.NEVER
    line = "ace2k_u1: not hooked: [ace2k] or the U1 feed modules are absent"
    assert gcode.lines == [line]
    assert _status(gcode) == [line]


@pytest.mark.parametrize("flavour", sorted(SPLITTERS))
def test_every_registered_command_is_its_own_name(flavour):
    reactor = fakes.FakeReactor()
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [])
    ace2k_u1.ACE2kU1(Config(printer))
    assert gcode.commands, "the adapter registers no G-code command"
    for name in gcode.commands:
        cmd = klipper_command(name, SPLITTERS[flavour])
        assert cmd == name.upper(), (
            f"G-code {name} is read by Klipper's {flavour} parser as command {cmd!r} — "
            "a digit in a command name starts a parameter; rename it without digits"
        )


def test_a_printer_without_the_extruder_list_hooks_every_lane_unmapped():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    ace = fakes.FakeAce2k()
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": ace}, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    assert ace.feed.extruders == []
    assert gcode.lines == [
        "ace2k_u1: lane 1: no extruder 0 on the printer; no feed-forward",
        "ace2k_u1: lane 2: no extruder 1 on the printer; no feed-forward",
        "ace2k_u1: hooked lanes [1, 2]",
        TAGS_ABSENT,
    ]


def _u1(values):
    reactor = fakes.FakeReactor()
    printer = Printer(reactor, {"gcode": Gcode(), "ace2k": fakes.FakeAce2k()}, [])
    return ace2k_u1.ACE2kU1(Config(printer, values))


def test_the_runout_source_defaults_to_the_head():
    assert _u1({}).runout_source == "head"


@pytest.mark.parametrize("value", ["head", "unit", "UNIT", " head "])
def test_the_runout_source_takes_head_or_unit(value):
    assert _u1({"runout_source": value}).runout_source == value.strip().lower()


@pytest.mark.parametrize("value", ["", "both", "sensor", "heads"])
def test_any_other_runout_source_is_a_config_error(value):
    with pytest.raises(ValueError, match="runout_source must be head or unit"):
        _u1({"runout_source": value})


def test_the_hook_hands_the_pause_its_u1_objects():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    objects = {
        "gcode": gcode,
        "ace2k": fakes.FakeAce2k(),
        "print_stats": fakes.FakePrintStats(),
        "pause_resume": fakes.FakePauseResume(),
        "exception_manager": fakes.ExceptionManager(),
    }
    printer = Printer(reactor, objects, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer, {"runout_source": "unit"}))
    u1._hook(reactor.now)
    a = u1.adapter
    assert a.print_stats is objects["print_stats"]
    assert a.pause_resume is objects["pause_resume"]
    assert a.exception_manager is objects["exception_manager"]
    assert a.send_event == printer.send_event
    assert a.runout_source == "unit"
    assert a.pause_timer in reactor.timers


def test_the_config_file_carries_the_runout_source_commented():
    cfg = (Path(__file__).resolve().parents[1] / "config" / "ace2k-u1.cfg").read_text()
    assert "\n#runout_source: head" in cfg


def test_the_config_file_carries_the_grip_commented():
    cfg = (Path(__file__).resolve().parents[1] / "config" / "ace2k-u1.cfg").read_text()
    assert "\n#grip_mm: 40" in cfg


class EstimatingMcu:
    def estimated_print_time(self, eventtime):
        return eventtime + 1000.0


def test_the_hook_reads_each_heads_extruder_position_as_the_u1_does():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    extruders = fakes.extruder_list()
    extruders[1].position = 123.5
    objects = {
        "gcode": Gcode(),
        "ace2k": fakes.FakeAce2k(),
        "extruder_list": extruders,
        "mcu": EstimatingMcu(),
    }
    u1 = ace2k_u1.ACE2kU1(Config(Printer(reactor, objects, [left])))
    u1._hook(reactor.now)
    assert u1.adapter.extruder_position(1) == 123.5
    assert extruders[1].asked == [reactor.now + 1000.0]  # at the MCU's estimated print time
    assert u1.adapter.extruder_position(7) is None


def test_without_the_mcu_the_wheel_stays_on_the_encoder():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    objects = {"gcode": Gcode(), "ace2k": fakes.FakeAce2k(), "extruder_list": fakes.extruder_list()}
    u1 = ace2k_u1.ACE2kU1(Config(Printer(reactor, objects, [left])))
    u1._hook(reactor.now)
    assert u1.adapter.extruder_position is None


class Gcmd:
    def __init__(self, params):
        self.params = params

    def get_int(self, name, default=None, minval=None, maxval=None):
        value = self.params.get(name)
        return default if value is None else int(value)


def test_ace_clear_is_wrapped_to_tell_the_adapter_after_ace2ks_own_handler():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    ran = []
    gcode.register_command("ACE_CLEAR", lambda gcmd: ran.append(gcmd.params["LANE"]))
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    told = []
    u1.adapter.on_ace_clear = told.append
    gcode.commands["ACE_CLEAR"](Gcmd({"LANE": "2"}))
    assert ran == ["2"] and told == [1]

    def refuse(gcmd):
        raise ValueError("ace2k: no link")

    gcode.commands.pop("ACE_CLEAR")
    gcode.register_command("ACE_CLEAR", refuse)
    u1._wrap_ace_clear(gcode)
    with pytest.raises(ValueError, match="no link"):  # the original error, raised after
        gcode.commands["ACE_CLEAR"](Gcmd({"LANE": "1"}))
    assert told == [1, 0]  # the lane released all the same: the escape works with the link down


def test_the_ace_clear_wrapper_keeps_the_description_and_the_readiness():
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    gcode.register_command("ACE_CLEAR", lambda gcmd: None, when_not_ready=True, desc="Leave it")
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    ace2k_u1.ACE2kU1(Config(printer))._hook(reactor.now)
    wrapped = gcode.commands["ACE_CLEAR"]
    assert wrapped.__name__ == "cmd_ace_clear"
    assert gcode.gcode_help["ACE_CLEAR"] == "Leave it"
    assert gcode.base_gcode_handlers["ACE_CLEAR"] is wrapped


def test_without_ace_clear_nothing_is_wrapped_and_the_log_says_so(caplog):
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    with caplog.at_level("WARNING"):
        ace2k_u1.ACE2kU1(Config(printer))._hook(reactor.now)
    assert "ACE_CLEAR" not in gcode.commands
    said = [r for r in caplog.records if "no ACE_CLEAR to wrap" in r.getMessage()]
    assert len(said) == 1
