from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1
import ace2k_u1_adapter
import ace2k_u1_config
import ace2k_u1_hooks
import ace2k_units
import fakes
import pytest
from test_config_macros import SPLITTERS, klipper_command

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import Any

    import test_units
    from ace2k_u1_printer_types import GCodeCommand
    from fakes import FakeAce2k, FakeFeed, FakeReactor

    # a G-code handler: the rigs hand it any of the tests' gcmd fakes
    Handler = Callable[..., object]
    # a printer's objects by name: fakes of every kind, read by the tests as what they are
    PrinterObjects = dict[str, Any]


def test_a_feed_like_the_real_one_has_no_problems() -> None:
    ff = fakes.FakeFeed(fakes.FakeReactor())
    assert ace2k_u1_hooks.check_feed(fakes.fake_module(), ff) == []


def test_a_changed_constant_is_a_problem() -> None:
    mod = fakes.fake_module()
    mod.FEED_STA_LOAD_FINISH = "loaded"
    problems = ace2k_u1_hooks.check_feed(mod, fakes.FakeFeed(fakes.FakeReactor()))
    assert any("FEED_STA_LOAD_FINISH" in p for p in problems)


def test_a_renamed_parameter_is_a_problem() -> None:
    ff = fakes.FakeFeed(fakes.FakeReactor())
    ff._put_into_drive = lambda ch: None  # parameter renamed
    problems = ace2k_u1_hooks.check_feed(fakes.fake_module(), ff)
    assert any("_put_into_drive" in p for p in problems)


def test_a_missing_attribute_is_a_problem() -> None:
    ff = fakes.FakeFeed(fakes.FakeReactor())
    del ff.wheel_2
    assert any("wheel_2" in p for p in ace2k_u1_hooks.check_feed(fakes.fake_module(), ff))


class Gcode:
    def __init__(self) -> None:
        self.lines = []
        self.commands = {}
        # Klipper's: every command registered, ready or not, is in ready_gcode_handlers
        self.ready_gcode_handlers = self.commands
        self.gcode_help = {}
        self.base_gcode_handlers = {}
        self.commands_orig = {}  # name -> what a stub registered by stub() calls
        self.orig = {}  # name -> the gcmds that reached its stub, in call order
        # ace2k's own clear, registered before the adapter hooks (as [ace2k] loads first)
        self.register_command("ACEK_CLEAR", lambda gcmd: None)

    def register_command(
        self, name: str, func: Handler | None, when_not_ready: bool = False, desc: str | None = None
    ) -> Handler | None:
        if func is None:  # Klipper's removal: the old handler handed back
            self.base_gcode_handlers.pop(name, None)
            return self.commands.pop(name, None)
        self.commands[name] = func
        if when_not_ready:
            self.base_gcode_handlers[name] = func
        if desc is not None:
            self.gcode_help[name] = desc
        return None

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)

    def respond_raw(self, msg: str) -> None:
        # Klipper's error line ("!! "): kept in lines as the text, and in errors
        self.errors = getattr(self, "errors", [])
        self.errors.append(msg)
        self.lines.append(msg[3:] if msg.startswith("!! ") else msg)

    def run_script_from_command(self, script: str) -> None:
        self.lines.append(script)

    def create_gcode_command(
        self, command: str, commandline: str, params: dict[str, str]
    ) -> test_units.Gcmd:
        """Klipper's GCodeCommand for a command line: the rigs' Gcmd."""
        from test_units import Gcmd  # noqa: PLC0415 — test_units imports this module

        return Gcmd(params, command)

    def stub(self, name: str) -> Handler:
        """Register name as the U1's own command would be: a handler that records its gcmd and
        calls commands_orig[name] when set."""

        def handler(gcmd: GCodeCommand) -> None:
            self.orig.setdefault(name, []).append(gcmd)
            func = self.commands_orig.get(name)
            if func is not None:
                func(gcmd)

        self.register_command(name, handler)
        return handler

    def orig_calls(self, name: str) -> list[GCodeCommand]:
        return self.orig.get(name, [])


class Printer:
    """A printer's objects as the adapter looks them up. A unit given as "ace2k" or "ace2k <name>"
    comes with ace2k's coordinator ace2k_units over the units given, as ace2k creates it with its
    first unit; coordinator=False leaves it out (an ace2k from before several units)."""

    command_error = ValueError  # Klipper's G-code error class, as the rigs' Gcmd.error

    def __init__(
        self,
        reactor: FakeReactor,
        objects: PrinterObjects,
        feeds: list[FakeFeed],
        coordinator: bool = True,
    ) -> None:
        self.reactor = reactor
        units = [obj for name, obj in objects.items() if name.split(None, 1)[0] == "ace2k"]
        if coordinator and units and "ace2k_units" not in objects:
            objects["ace2k_units"] = fakes.FakeUnits(units)
        self.objects = objects
        self.feeds = feeds
        self.events = []  # (name, args) per send_event, in call order
        self.start_args = None  # Klipper's start arguments (config_file: printer.cfg's path)

    def get_reactor(self) -> FakeReactor:
        return self.reactor

    def get_start_args(self) -> dict[str, str]:
        return self.start_args or {}

    def register_event_handler(self, name: str, cb: Callable[..., object]) -> None:
        pass

    def lookup_object(self, name: str, default: object = None) -> object:
        return self.objects.get(name, default)

    def send_event(self, name: str, *args: object) -> None:
        self.events.append((name, args))

    def lookup_objects(self, module: str) -> list[tuple[str, FakeFeed]]:
        return [(f"{module} {i}", ff) for i, ff in enumerate(self.feeds)]


# a printer without the U1's print_task_config: the tags are off, the hooks are not
TAGS_ABSENT = "ace2k_u1: tags not applied: [print_task_config] absent"


class Section:
    def __init__(self, name: str) -> None:
        self.name = name

    def get_name(self) -> str:
        return self.name

    def get(self, key: str, default: object = None) -> object:
        return default  # no laneN_extruder keys


class Config:
    error = ValueError

    def __init__(self, printer: Printer, values: Mapping[str, object] | None = None) -> None:
        self.printer = printer
        self.values = values or {}

    def get(self, name: str, default: object) -> object:
        return self.values.get(name, default)

    def get_printer(self) -> Printer:
        return self.printer

    def get_name(self) -> str:
        return "ace2k_u1"

    def getlist(self, name: str, default: Sequence[str]) -> Sequence[str]:
        value = self.values.get(name)
        return default if value is None else [part.strip() for part in str(value).split(",")]

    def get_prefix_sections(self, prefix: str) -> list[Section]:
        """The config's sections by prefix, as Klipper's: here one per printer object named so
        (each unit's object carries its section's name)."""
        return [Section(name) for name in self.printer.objects if name.startswith(prefix)]

    def getfloat(
        self,
        name: str,
        default: float | None,
        above: float | None = None,
        minval: float | None = None,
        maxval: float | None = None,
    ) -> object:
        value = self.values.get(name, default)
        if isinstance(value, str):
            value = float(value)
        if value is None or not isinstance(value, (int, float)):
            return value
        if above is not None and value <= above:
            raise self.error(f"{name} must be above {above}")
        if minval is not None and value < minval:
            raise self.error(f"{name} must be at least {minval}")
        if maxval is not None and value > maxval:
            raise self.error(f"{name} must be at most {maxval}")
        return value

    def getint(
        self, name: str, default: int, minval: int | None = None, maxval: int | None = None
    ) -> object:
        return self.values.get(name, default)

    def getboolean(self, name: str, default: bool) -> bool:
        return default


def test_a_problem_is_reported_and_status_says_not_hooked() -> None:
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
    gcode.commands["ACEK_ADAPTER_STATUS"](status)
    assert status.lines[0].startswith("ace2k_u1: not hooked: ") and "wheel_2" in status.lines[0]


def test_hooking_reports_the_lanes_and_starts_the_watch() -> None:
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
    assert [t[0] for t in reactor.timers] == [u1.adapter.pause_runner._run_pauses, u1.adapter.watch]
    assert reactor.timers[0][1] == reactor.NEVER  # the pause timer waits for a pause
    assert u1.adapter.gcode is gcode
    status = Gcode()
    gcode.commands["ACEK_ADAPTER_STATUS"](status)
    assert status.lines[0] == "ace2k_u1: tags off: [print_task_config] absent"
    assert len(status.lines) == 5 and status.lines[1].startswith("lane ")


def _hook_with(feed_api: object) -> tuple[Gcode, FakeFeed]:
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


def test_the_api_version_is_read_from_the_feed_object() -> None:
    # the adapter holds only printer.lookup_object("ace2k").feed: the version must be readable
    # there (ace2k carries it on the feed object's class, as the fake does); a feed object
    # without it is an interface too old to drive, whatever ace2k's module says
    assert "API_VERSION" in vars(fakes.FakeFeedApi)

    class WithoutVersion:
        move_max_mm = 2000.0
        report_hz = 1.0

    gcode, left = _hook_with(WithoutVersion())
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    gcode, left = _hook_with(fakes.FakeFeedApi())
    assert gcode.lines == ["ace2k_u1: hooked lanes [1, 2, 3, 4]", TAGS_ABSENT]
    assert left.module_exist == [True, True]


def test_an_interface_without_the_follow_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 1: an ace2k host from before the follow (assist_both)
    old = fakes.FakeFeedApi()
    old.API_VERSION = 1
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]


def test_an_interface_without_the_lane_map_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 2: an ace2k host from before the feed-forward (set_lane_extruder)
    old = fakes.FakeFeedApi()
    old.API_VERSION = 2
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert old.extruders == []


def test_an_interface_without_the_tail_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 3: an ace2k host from before the follow's tail ("tail", "tail_out")
    old = fakes.FakeFeedApi()
    old.API_VERSION = 3
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert old.extruders == []


def test_an_interface_without_blocked_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 4: an ace2k host from before the kind "blocked" and the tail ignoring the bay
    old = fakes.FakeFeedApi()
    old.API_VERSION = 4
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert old.extruders == []


def test_an_interface_without_the_grip_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 5: an ace2k host from before the grip (set_lane_grip)
    old = fakes.FakeFeedApi()
    old.API_VERSION = 5
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert old.extruders == [] and old.grips == []


def test_an_interface_without_the_acek_names_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 6: an ace2k host from before the ACEK_* names (its ACE_CLEAR and ACE_RFID_READ
    # are not the commands this adapter wraps and runs) and rfid.start_read
    old = fakes.FakeFeedApi()
    old.API_VERSION = 6
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert old.extruders == [] and old.grips == []


def test_an_interface_without_the_follow_default_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 7: an ace2k host from before the follow on by default — nothing would arm a
    # lane at the start, the adapter no longer does
    old = fakes.FakeFeedApi()
    old.API_VERSION = 7
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert old.lane_follow == {}  # nothing taken from it either


def test_an_interface_without_several_units_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 8: an ace2k host from before several units (the events' "unit", ace2k_units)
    old = fakes.FakeFeedApi()
    old.API_VERSION = 8
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert old.extruders == [] and old.grips == []


def test_an_interface_without_the_free_tip_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 11: from before start_move's free_tip (12): a push toward a head would drag a
    # tip the unit counts as held
    old = fakes.FakeFeedApi()
    old.API_VERSION = 11
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert old.extruders == [] and old.grips == []


def test_an_interface_from_before_hand_over_is_not_hooked_with_its_reason() -> None:
    # API_VERSION 10: from before Units.hand_over (11): any version below the minimum is refused
    old = fakes.FakeFeedApi()
    old.API_VERSION = 10
    gcode, left = _hook_with(old)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]


def test_an_ace2k_without_its_coordinator_is_not_hooked_with_its_reason() -> None:
    # an ace2k from before several units has no ace2k_units, whatever its feed object says: the
    # adapter drives every unit through the coordinator, so the reason is the interface's age
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    gcode = Gcode()
    objects = {"gcode": gcode, "ace2k": ace, "extruder_list": fakes.extruder_list()}
    printer = Printer(reactor, objects, [left, right], coordinator=False)
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._on_connect()
    u1._hook(reactor.now)
    assert gcode.lines == ["ace2k_u1: not hooked: ace2k host interface older than 12"]
    assert left.module_exist == [False, False]
    assert ace.feed.lane_follow == {}  # nothing taken at connect either


def test_the_adapter_has_no_single_unit_stand_in() -> None:
    # every interface it accepts (9 and later) has the coordinator: no copy of it here
    assert not hasattr(ace2k_u1, "OneUnit")


def test_lanes_are_checked_with_ace2ks_own_unit_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    # the units a config declares are ace2k's to say (ace2k_units.unit_names), not a copy here
    assert ace2k_u1_config.ace2k_units is ace2k_units
    monkeypatch.setattr(ace2k_units, "unit_names", lambda config: ["x", "y"])
    reactor = fakes.FakeReactor()
    printer = Printer(reactor, {"gcode": Gcode(), "ace2k": fakes.FakeAce2k()}, [])
    with pytest.raises(ValueError, match="this printer has several units \\(x, y\\)"):
        ace2k_u1.ACE2kU1(Config(printer))


def test_api_min_is_twelve() -> None:
    assert ace2k_u1_adapter.API_MIN == 12
    assert fakes.FakeFeedApi.API_VERSION >= ace2k_u1_adapter.API_MIN


def test_a_firmware_without_the_grip_is_not_hooked_with_its_reason() -> None:
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


def test_without_grip_mm_the_grip_is_the_units_own_default() -> None:
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
    assert u1.adapter.grip_watch._grip(u1.adapter.hooked[0]) == 35.0


@pytest.mark.parametrize("grip", [9.9, 45.5, 50.0, 120.0])
def test_a_grip_outside_the_units_bounds_is_not_hooked_with_its_reason(grip: float) -> None:
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
def test_a_grip_within_the_units_bounds_hooks_and_is_the_adapters(grip: float) -> None:
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer, {"grip_mm": grip}))
    u1._hook(reactor.now)
    assert gcode.lines[-2] == "ace2k_u1: hooked lanes [1, 2]"
    assert u1.adapter.grip_watch._grip(u1.adapter.hooked[0]) == grip


def test_hooking_maps_the_lanes_to_the_printers_extruders() -> None:
    api = fakes.FakeFeedApi()
    gcode, left = _hook_with(api)
    assert gcode.lines == ["ace2k_u1: hooked lanes [1, 2, 3, 4]", TAGS_ABSENT]
    assert sorted(api.extruders) == [
        (0, "extruder"),
        (1, "extruder1"),
        (2, "extruder2"),
        (3, "extruder3"),
    ]


def test_a_failed_hook_maps_no_lane() -> None:
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


def test_a_firmware_without_the_follow_is_not_hooked_with_its_reason() -> None:
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


def test_the_default_feed_speed_is_70() -> None:
    reactor = fakes.FakeReactor()
    printer = Printer(reactor, {"gcode": Gcode(), "ace2k": fakes.FakeAce2k()}, [])
    assert ace2k_u1.ACE2kU1(Config(printer)).feed_speed == 70.0


def _status(gcode: Gcode) -> list[str]:
    status = Gcode()
    gcode.commands["ACEK_ADAPTER_STATUS"](status)
    return status.lines


def test_a_firmware_without_the_feed_is_not_hooked_with_its_reason() -> None:
    # ace2k loads its feed object even then, with no lane bounds (move_max_mm None)
    no_feed = fakes.FakeFeedApi()
    no_feed.move_max_mm = None
    gcode, left = _hook_with(no_feed)
    line = "ace2k_u1: not hooked: the ace2k firmware has no feed support"
    assert gcode.lines == [line]
    assert left.module_exist == [False, False]
    assert _status(gcode) == [line]


def test_an_absent_ace2k_is_not_hooked_and_status_repeats_why() -> None:
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
def test_every_registered_command_is_its_own_name(flavour: str) -> None:
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


def test_a_printer_without_the_extruder_list_hooks_every_lane_unmapped() -> None:
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


def _u1(values: Mapping[str, object]) -> ace2k_u1.ACE2kU1:
    reactor = fakes.FakeReactor()
    printer = Printer(reactor, {"gcode": Gcode(), "ace2k": fakes.FakeAce2k()}, [])
    return ace2k_u1.ACE2kU1(Config(printer, values))


def test_the_runout_source_defaults_to_the_head() -> None:
    assert _u1({}).runout_source == "head"


@pytest.mark.parametrize("value", ["head", "unit", "UNIT", " head "])
def test_the_runout_source_takes_head_or_unit(value: str) -> None:
    assert _u1({"runout_source": value}).runout_source == value.strip().lower()


@pytest.mark.parametrize("value", ["", "both", "sensor", "heads"])
def test_any_other_runout_source_is_a_config_error(value: str) -> None:
    with pytest.raises(ValueError, match="runout_source must be head or unit"):
        _u1({"runout_source": value})


def test_the_hook_hands_the_pause_its_u1_objects() -> None:
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
    assert a.pause_runner.pause_timer in reactor.timers


def cfg_text() -> str:
    return (Path(__file__).resolve().parents[1] / "config" / "ace2k-u1.cfg").read_text()


def test_the_config_file_warns_before_the_swap_macros() -> None:
    cfg = cfg_text()
    danger = cfg.index("# !!! DANGER !!!")
    unload = cfg.index("[gcode_macro ACEK_SWAP_UNLOAD]")
    load = cfg.index("[gcode_macro ACEK_SWAP_LOAD]")
    assert danger < unload < load
    block = cfg[danger:unload]
    for words in ("crash the head", "damage the printer and the extruder", "sure of what every"):
        assert words in block


def swap_macro_body(cfg: str, name: str) -> str:
    start = cfg.index(f"[gcode_macro {name}]")
    rest = cfg[start + 1 :]
    end = rest.find("\n[")
    return rest if end < 0 else rest[:end]


def test_the_swap_macros_never_move_the_unit() -> None:
    cfg = cfg_text()
    for name in ("ACEK_SWAP_UNLOAD", "ACEK_SWAP_LOAD"):
        body = swap_macro_body(cfg, name).split("\n", 1)[1]
        code = [ln.split("#")[0] for ln in body.splitlines() if not ln.lstrip().startswith("#")]
        assert not [ln for ln in code if "ACEK_" in ln or "ACE_" in ln], name


def test_the_default_swap_macros_are_the_documented_ones() -> None:
    cfg = cfg_text()
    unload = swap_macro_body(cfg, "ACEK_SWAP_UNLOAD")
    assert (
        "INNER_FILAMENT_UNLOAD TEMP={params.TEMP} SOFT={params.SOFT}"
        " NOZZLE_DIAMETER={params.NOZZLE_DIAMETER}\n" in unload
    )
    assert "M104 S{params.LOAD_TEMP} T{params.HEAD} A0" in unload
    load = swap_macro_body(cfg, "ACEK_SWAP_LOAD")
    order = [
        "MOVE_TO_DISCARD_FILAMENT_POSITION",
        "M109 S{params.LOAD_TEMP} T{params.HEAD} A0",
        "G1 E40 F360",
        "G1 E30 F360",
        "ROUGHLY_CLEAN_NOZZLE_WITH_DISCARD",  # the first clean; the pre-extrusion cleans again
    ]
    at = [load.index(line) for line in order]
    assert at == sorted(at)
    assert "M106" not in load and "{% if" not in load  # no fan step: PRINT_TEMP = LOAD_TEMP


def test_the_config_file_carries_the_runout_source_commented() -> None:
    cfg = cfg_text()
    assert "\n#runout_source: head" in cfg


def test_the_config_file_carries_the_grip_commented() -> None:
    cfg = cfg_text()
    assert "\n#grip_mm: 40" in cfg


class EstimatingMcu:
    def estimated_print_time(self, eventtime: float) -> float:
        return eventtime + 1000.0


def test_the_hook_reads_each_heads_extruder_position_as_the_u1_does() -> None:
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


def test_without_the_mcu_the_wheel_stays_on_the_encoder() -> None:
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    objects = {"gcode": Gcode(), "ace2k": fakes.FakeAce2k(), "extruder_list": fakes.extruder_list()}
    u1 = ace2k_u1.ACE2kU1(Config(Printer(reactor, objects, [left])))
    u1._hook(reactor.now)
    assert u1.adapter.extruder_position is None


class Gcmd:
    def __init__(self, params: dict[str, str]) -> None:
        self.params = params

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.params.get(name, default)

    def get_int(
        self,
        name: str,
        default: int | None = None,
        minval: int | None = None,
        maxval: int | None = None,
    ) -> int | None:
        value = self.params.get(name)
        return default if value is None else int(value)


def test_ace_clear_is_wrapped_to_tell_the_adapter_after_ace2ks_own_handler() -> None:
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    ran = []
    gcode.register_command("ACEK_CLEAR", lambda gcmd: ran.append(gcmd.params["LANE"]))
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    told, units = [], []

    def on_ace_clear(lane: int, unit: FakeAce2k | None) -> None:
        told.append(lane)
        units.append(unit)

    u1.adapter.grip_watch.on_ace_clear = on_ace_clear
    gcode.commands["ACEK_CLEAR"](Gcmd({"LANE": "2"}))
    assert ran == ["2"] and told == [1]
    assert units == [u1.adapter.used[0]]  # the unit unit_for names, the only one here

    def refuse(gcmd: Gcmd) -> NoReturn:
        raise ValueError("ace2k: no link")

    gcode.commands.pop("ACEK_CLEAR")
    gcode.register_command("ACEK_CLEAR", refuse)
    u1.wraps.wrap_ace_clear(gcode)
    with pytest.raises(ValueError, match="no link"):  # the original error, raised after
        gcode.commands["ACEK_CLEAR"](Gcmd({"LANE": "1"}))
    assert told == [1, 0]  # the lane released all the same: the escape works with the link down


def test_the_ace_clear_wrapper_keeps_the_description_and_the_readiness() -> None:
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    gcode.register_command("ACEK_CLEAR", lambda gcmd: None, when_not_ready=True, desc="Leave it")
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    ace2k_u1.ACE2kU1(Config(printer))._hook(reactor.now)
    wrapped = gcode.commands["ACEK_CLEAR"]
    assert wrapped.__name__ == "cmd_ace_clear"
    assert gcode.gcode_help["ACEK_CLEAR"] == "Leave it"
    assert gcode.base_gcode_handlers["ACEK_CLEAR"] is wrapped


def test_without_ace_clear_nothing_is_wrapped_and_the_console_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    gcode.commands.pop("ACEK_CLEAR")
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    with caplog.at_level("WARNING"):
        ace2k_u1.ACE2kU1(Config(printer))._hook(reactor.now)
    assert "ACEK_CLEAR" not in gcode.commands
    said = [r for r in caplog.records if "no ACEK_CLEAR to wrap" in r.getMessage()]
    assert len(said) == 1
    shown = [line for line in gcode.lines if line.startswith("ace2k_u1: no ACEK_CLEAR to wrap")]
    assert len(shown) == 1 and "copy ace2k's host module of the same release" in shown[0]


def test_no_command_keeps_the_old_ace_prefix() -> None:
    # every command the adapter registers or wraps is ACEK_*, none ACE_*: those names stay free
    # for other ACE modules, and Klipper stops on a command registered twice
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()  # with ace2k's own ACEK_CLEAR, wrapped at the hook
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [left])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1._hook(reactor.now)
    assert sorted(gcode.commands) == [
        "ACEK_ADAPTER_STATUS",
        "ACEK_CLEAR",
        "ACEK_EJECT",
        "ACEK_FREE_CHECK",
        "ACEK_HEAD_PREPARE",
        "ACEK_HEAD_PROBE",
        "ACEK_HEAD_SPOOL",
        "ACEK_PLAN",
        "ACEK_PLAN_SET",
        "ACEK_SPLITTER_MEASURE",
        "ACEK_SPOOL",
        "ACEK_TAG_FORGET",
    ]


def test_a_lane_on_a_unit_that_is_not_there_is_not_hooked_with_its_reason() -> None:
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    gcode = Gcode()
    objects = {"gcode": gcode, "ace2k": fakes.FakeAce2k(), "extruder_list": fakes.extruder_list()}
    printer = Printer(reactor, objects, [left, right])
    u1 = ace2k_u1.ACE2kU1(Config(printer))
    u1.lane_refs = [(0, "zz", 0)]
    assert u1._hook(reactor.now) == reactor.NEVER
    assert gcode.lines == ["ace2k_u1: not hooked: lanes: no unit zz"]
    assert u1.problems == ["lanes: no unit zz"]
    assert left.module_exist == [False, False]


def bounded_rig(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    values: Mapping[str, object],
    lanes: str | None = None,
    top: float = 80.0,
) -> SimpleNamespace:
    """test_tools' two-unit rig, its units' feeds with the dictionary's speed bounds (9..top
    mm/s, 80 by default; 70 is an older image), hooked with values ([ace2k_u1] keys)."""
    import test_tools  # noqa: PLC0415 — test_tools imports this module

    monkeypatch.setattr(fakes.FakeFeedApi, "speed_min", 9.0, raising=False)
    monkeypatch.setattr(fakes.FakeFeedApi, "speed_max", top, raising=False)
    if lanes is None:
        return test_tools.tools_rig(tmp_path, values=values)[0]
    return test_tools.tools_rig(tmp_path, lanes=lanes, values=values)[0]


@pytest.mark.parametrize(
    "key, value",
    [
        ("eject_probe_speed", 5.0),
        ("eject_probe_speed", 8.5),
        ("swap_speed", 5.0),
        ("swap_speed", 90.0),
        ("probe_speed", 8.0),
        ("probe_speed", 81.0),
    ],
)
def test_a_speed_outside_the_units_bounds_is_not_hooked_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str, value: float
) -> None:
    """Judged once at the hook against the unit's own bounds, as the grip is."""
    rig = bounded_rig(monkeypatch, tmp_path, {key: value})
    why = f"{key} {value:g} is outside the unit's 9..80 mm/s"
    assert f"ace2k_u1: not hooked: ace2k a: {why}; ace2k b: {why}" in rig.gcode.lines


@pytest.mark.parametrize("value", [5.0, 90.0])
def test_without_a_shared_head_any_probe_speed_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: float
) -> None:
    """Nothing uses probe_speed without a + group: out of the unit's bounds, even above 80
    (once a config error), it is accepted and the adapter hooks."""
    rig = bounded_rig(monkeypatch, tmp_path, {"probe_speed": value}, "a:1, a:2")
    assert not rig.module.problems and rig.adapter.hooked


@pytest.mark.parametrize(("value", "runs"), [(5.0, 9.0), (90.0, 80.0)])
def test_without_a_shared_head_a_swap_speed_set_out_of_bounds_is_warned_and_clamped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: float, runs: float
) -> None:
    """A lone head's push that a lane blocks rolls back at swap_speed. With no + group a value
    out of the unit's speeds still hooks: one warning, and the moves run within the unit's."""
    rig = bounded_rig(monkeypatch, tmp_path, {"swap_speed": value}, "a:1, a:2")
    assert not rig.module.problems and rig.adapter.hooked
    why = f"swap_speed {value:g} is outside the unit's 9..80 mm/s"
    notes = [line for line in rig.gcode.lines if "swap_speed" in line]
    assert notes == [f"ace2k_u1: ace2k a: {why}: its moves run at {runs:g} mm/s"]
    assert rig.module.swap_cfg.swap_speed_for(fakes.FakeFeedApi()) == runs


def test_speeds_within_the_units_bounds_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = {"eject_probe_speed": 9.0, "swap_speed": 80.0, "probe_speed": 9.0}
    rig = bounded_rig(monkeypatch, tmp_path, values)
    assert not rig.module.problems and rig.adapter.hooked


def test_the_default_swap_speed_follows_an_older_images_top_speed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rig = bounded_rig(monkeypatch, tmp_path, {}, top=70.0)
    assert not rig.module.problems and rig.adapter.hooked
    cfg = rig.module.swap_cfg
    assert cfg.swap_speed == 80.0  # the config itself is left as it was read
    assert cfg.swap_speed_for(fakes.FakeFeedApi()) == 70.0
    expected = [
        "ace2k_u1: ace2k a: swap_speed defaults to the unit's top speed, 70 mm/s",
        "ace2k_u1: ace2k b: swap_speed defaults to the unit's top speed, 70 mm/s",
    ]
    assert [line for line in rig.gcode.lines if "swap_speed" in line] == expected
    rig.module._hook(0.0)  # a re-hook in the same connect does not say it again
    assert [line for line in rig.gcode.lines if "swap_speed" in line] == expected


def test_a_unit_that_reports_no_top_speed_runs_the_default_swap_speed_at_70(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "speed_max", None)
    import test_tools  # noqa: PLC0415 — test_tools imports this module

    rig = test_tools.tools_rig(tmp_path)[0]
    assert not rig.module.problems and rig.adapter.hooked
    assert rig.module.swap_cfg.swap_speed_for(fakes.FakeFeedApi()) == 70.0
    assert [line for line in rig.gcode.lines if "swap_speed" in line] == [
        "ace2k_u1: ace2k a: the unit reports no top speed: swap_speed is 70 mm/s",
        "ace2k_u1: ace2k b: the unit reports no top speed: swap_speed is 70 mm/s",
    ]


def test_the_default_swap_speed_is_80_on_an_image_with_that_top_speed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rig = bounded_rig(monkeypatch, tmp_path, {})
    assert not rig.module.problems and rig.adapter.hooked
    assert rig.module.swap_cfg.swap_speed_for(fakes.FakeFeedApi()) == 80.0


def test_the_default_swap_speed_is_per_unit_and_70_without_limits() -> None:
    cfg = ace2k_u1_config.SwapConfig(
        swap_speed=ace2k_u1_config.SWAP_SPEED,
        splitter_mm={},
        splitter_default=set(),
        colour_max_distance=25.0,
        colour_min_margin=8.0,
        probe_mm=100.0,
        probe_speed=50.0,
        probe_max_temp=60.0,
        swap_speed_set=False,
    )
    assert cfg.swap_speed_for(SimpleNamespace(speed_max=70.0)) == 70.0
    assert cfg.swap_speed_for(SimpleNamespace(speed_max=80.0)) == 80.0
    assert cfg.swap_speed_for(SimpleNamespace()) == 70.0  # an image with no limits
    cfg.swap_speed, cfg.swap_speed_set = 60.0, True
    assert cfg.swap_speed_for(SimpleNamespace(speed_max=70.0)) == 60.0  # set: as it is


def test_a_swap_speed_set_above_an_older_images_top_speed_is_not_hooked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rig = bounded_rig(monkeypatch, tmp_path, {"swap_speed": 80.0}, top=70.0)
    why = "swap_speed 80 is outside the unit's 9..70 mm/s"
    assert f"ace2k_u1: not hooked: ace2k a: {why}; ace2k b: {why}" in rig.gcode.lines


def test_without_the_units_bounds_a_swap_speed_above_70_is_not_hooked(tmp_path: Path) -> None:
    import test_tools  # noqa: PLC0415 — test_tools imports this module

    rig = test_tools.tools_rig(tmp_path, values={"swap_speed": 75.0})[0]
    why = "swap_speed 75 is above 70 mm/s"
    assert f"ace2k_u1: not hooked: ace2k a: {why}; ace2k b: {why}" in rig.gcode.lines


def test_a_probe_longer_than_the_units_longest_move_is_not_hooked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "move_max_mm", 200.0)
    import test_tools  # noqa: PLC0415 — test_tools imports this module

    rig = test_tools.tools_rig(tmp_path, values={"probe_mm": 250.0})[0]
    why = "probe_mm 250 is longer than the unit's longest move, 200 mm"
    assert f"ace2k_u1: not hooked: ace2k a: {why}; ace2k b: {why}" in rig.gcode.lines


def test_a_held_mm_too_large_for_the_slowest_armed_move_is_not_hooked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The slowest armed move is the unit's least speed, 9 mm/s: held_mm 19 clamps its window to
    the unit's longest and lets friction end it held."""
    monkeypatch.setattr(fakes.FakeFeedApi, "has_stop_when", True)
    monkeypatch.setattr(fakes.FakeFeedApi, "held_restart_mm", 19.253)
    rig = bounded_rig(monkeypatch, tmp_path, {})
    why = (
        "held_mm: at 9 mm/s its held window (2000 ms) lets friction end a move held below 9.6 mm/s,"
        " more than 75 % of the speed; keep held_mm at 13.3 mm or less"
    )
    assert f"ace2k_u1: not hooked: ace2k a: {why}; ace2k b: {why}" in rig.gcode.lines


def test_the_default_held_mm_hooks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fakes.FakeFeedApi, "has_stop_when", True)
    rig = bounded_rig(monkeypatch, tmp_path, {})
    assert not rig.module.problems and rig.adapter.hooked


@pytest.mark.parametrize("missing", ["M104", "M109"])
def test_a_temperature_command_missing_at_the_hook_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, missing: str
) -> None:
    """A temperature that the printer has no command for cannot be held: one log line names it;
    the other is wrapped."""
    import test_tools  # noqa: PLC0415 — test_tools imports this module

    rig = test_tools.tools_rig(tmp_path)[0]
    gcode = Gcode()
    kept = "M109" if missing == "M104" else "M104"
    gcode.register_command(kept, lambda gcmd: None)
    caplog.clear()
    rig.module.wraps.wrap_temperatures(gcode)
    line = (
        f"ace2k_u1: no {missing} to wrap: a tool's temperature for a lane not in its head is "
        "applied, not kept for its swap"
    )
    assert [r.getMessage() for r in caplog.records] == [line]
    assert gcode.commands[kept] is not None and missing not in gcode.commands
