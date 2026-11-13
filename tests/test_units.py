"""Several units: each head on its unit's lane (lanes: <unit>:<lane>), every per-head path on
that unit, events matched by unit and lane, ACEK_EJECT and ACEK_CLEAR taking UNIT=."""

from __future__ import annotations

import logging
import types
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1
import ace2k_u1_adapter
import ace2k_u1_common
import ace2k_u1_swap
import ace2k_u1_wraps
import fakes
import pytest
from test_check import Config, Gcode, Printer

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path
    from types import SimpleNamespace
    from typing import Any

    from ace2k_u1_common import Hooked
    from ace2k_u1_printer_types import Status
    from fakes import FakeAce2k, FakeUnits


class LanesConfig(Config):
    """Config whose lanes: is read from the values (comma-separated, as Klipper's getlist)."""

    def getlist(self, name: str, default: Sequence[str]) -> Sequence[str]:
        value = self.values.get(name)
        if value is None:
            return default
        return [x.strip() for x in value.split(",") if x.strip()]


NO_DEFAULT = object()  # Klipper's sentinel: a parameter with no default is required


class Gcmd:
    error = ValueError

    def __init__(self, params: dict[str, str], command: str = "ACEK_EJECT") -> None:
        self.params = params
        self.command = command
        self.lines = []  # respond_info, in call order

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)

    def get_command(self) -> str:
        return self.command

    def get_commandline(self) -> str:
        return " ".join([self.command] + [f"{k}={v}" for k, v in self.params.items()])

    def get_command_parameters(self) -> dict[str, str]:
        return self.params

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.params.get(name, default)

    def get_float(
        self,
        name: str,
        default: float | None = None,
        minval: float | None = None,
        maxval: float | None = None,
    ) -> float:
        value = self.params.get(name)
        value = default if value is None else float(value)
        if value is None:
            raise self.error(f"{name} missing")
        return value

    def get_int(
        self,
        name: str,
        default: object = NO_DEFAULT,
        minval: int | None = None,
        maxval: int | None = None,
    ) -> object:
        """Klipper's: a missing parameter is its default, unchecked (None too); without one,
        the G-code error."""
        value = self.params.get(name)
        if value is None:
            if default is NO_DEFAULT:
                raise self.error(f"{name} missing or out of range")
            return default
        value = int(value)
        if minval is not None and value < minval:
            raise self.error(f"{name} missing or out of range")
        if maxval is not None and value > maxval:
            raise self.error(f"{name} out of range")
        return value


def make_rig(
    lanes: str | None,
    units: FakeUnits,
    # unit names, or None for [ace2k]: Any, since both dict[str, …] and {None: …} are passed; the
    # body reads only .values() and keeps the mapping as rig.units
    by_name: Mapping[Any, FakeAce2k],
    config_dir: Path | None = None,
    sdcard_path: Path | None = None,
    print_stats: bool = False,
    task_config: bool = False,
    values: Mapping[str, object] | None = None,
) -> SimpleNamespace:
    """config_dir: printer.cfg's folder (Klipper's start arguments); sdcard_path: the G-code
    folder (virtual_sdcard); print_stats: a FakePrintStats registered, kept as rig.print_stats;
    task_config: a FakePrintTaskConfig registered, kept as rig.task_config. The U1's two print
    starts, its T0-T31 and RESUME are registered as stubs (Gcode.stub); rig.units_obj is the
    coordinator."""
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    gcode = Gcode()
    gcode.stub("SDCARD_PRINT_FILE")
    gcode.stub("SDCARD_PRINT_FILE_WITH_PARAMETERS")
    for n in range(ace2k_u1_wraps.TOOLS):
        gcode.stub(f"T{n}")
    gcode.stub("RESUME")
    gcode.stub("SM_PRINT_END_AUTO_UNLOAD_FILAMENT")  # the U1's end-of-print unload
    gcode.stub("SM_PRINT_PREEXTRUDE_FILAMENT")  # the U1's pre-extrusion after a file's T
    gcode.stub("MOVE_TO_XY_IDLE_POSITION_EXTRUDER")  # the U1's idle exit (registered per extruder)
    gcode.stub("M104")
    gcode.stub("M109")
    gcode.stub("SET_HEATER_TEMPERATURE")  # Klipper's, as Fluidd sets a heater
    gcode.stub("ACEK_FEED")  # ace2k's manual feed, wrapped with a + group
    objects = {
        "gcode": gcode,
        "ace2k_units": units,
        "extruder_list": fakes.extruder_list(),
        "webhooks": fakes.FakeWebhooks(),  # the U1's temperature control of its screen
    }
    for extruder in objects["extruder_list"]:  # each also under its own name, as Klipper's
        objects[extruder.name] = extruder
    for unit in by_name.values():
        objects[unit.section] = unit
    for name in (ace2k_u1_swap.SWAP_UNLOAD_MACRO, ace2k_u1_swap.SWAP_LOAD_MACRO):
        # the release's config defines both: Klipper's macro object and its G-code command
        objects[f"gcode_macro {name}"] = object()
        gcode.register_command(name, lambda gcmd: None, desc="G-Code macro")
    if sdcard_path is not None:
        objects["virtual_sdcard"] = types.SimpleNamespace(sdcard_dirname=str(sdcard_path))
    stats = fakes.FakePrintStats() if print_stats else None
    if stats is not None:
        objects["print_stats"] = stats
    ptc = fakes.FakePrintTaskConfig() if task_config else None
    if ptc is not None:
        objects["print_task_config"] = ptc
    printer = Printer(reactor, objects, [left, right])
    if config_dir is not None:
        printer.start_args = {"config_file": str(config_dir / "printer.cfg")}
    values = dict(values or {}, **({} if lanes is None else {"lanes": lanes}))
    module = ace2k_u1.ACE2kU1(LanesConfig(printer, values))
    module._on_connect()
    module._hook(reactor.now)
    rig = types.SimpleNamespace(
        printer=printer,
        reactor=reactor,
        left=left,
        right=right,
        gcode=gcode,
        module=module,
        adapter=module.adapter,
        units=by_name,
        told_tail=[],
        print_stats=stats,
        task_config=ptc,
        units_obj=units,
    )
    if rig.adapter is not None and rig.adapter.hooked:
        real = rig.adapter.grip_watch.on_tail

        def on_tail(h: Hooked) -> None:
            rig.told_tail.append(h.head)
            real(h)

        rig.adapter.grip_watch.on_tail = on_tail
    return rig


def make_two_unit_rig(lanes: str = "a:1, a:2, b:1, b:2") -> SimpleNamespace:
    units, by_name = fakes.two_units()
    return make_rig(lanes, units, by_name)


def make_one_unit_rig(lanes: str | None = None) -> SimpleNamespace:
    unit = fakes.FakeAce2k()
    return make_rig(lanes, fakes.FakeUnits([unit]), {None: unit})


def head(rig: SimpleNamespace, index: int) -> Hooked:
    return next(h for h in rig.adapter.hooked if h.head == index)


# --- the map ----------------------------------------------------------------------------------


def test_each_head_hooks_its_units_lane() -> None:
    rig = make_two_unit_rig()
    hooked = {h.head: (h.unit.name, h.lane) for h in rig.adapter.hooked}
    assert hooked == {0: ("a", 0), 1: ("a", 1), 2: ("b", 0), 3: ("b", 1)}
    assert "ace2k_u1: hooked lanes [a:1, a:2, b:1, b:2]" in rig.gcode.lines


def test_the_adapter_status_names_each_head_by_unit_and_lane() -> None:
    rig = make_two_unit_rig()
    status = Gcode()
    rig.gcode.commands["ACEK_ADAPTER_STATUS"](status)
    lanes = [line.split(" ->")[0] for line in status.lines if line.startswith("lane ")]
    assert lanes == ["lane a:1", "lane a:2", "lane b:1", "lane b:2"]


def test_bare_lanes_on_one_unit_hook_as_before() -> None:
    rig = make_one_unit_rig()
    assert {h.head: h.lane for h in rig.adapter.hooked} == {0: 0, 1: 1, 2: 2, 3: 3}
    assert "ace2k_u1: hooked lanes [1, 2, 3, 4]" in rig.gcode.lines
    rig = make_one_unit_rig("2, 4")  # lane n feeds head n-1, as before
    assert {h.head: h.lane for h in rig.adapter.hooked} == {1: 1, 3: 3}


def test_a_named_single_unit_takes_its_lanes_bare_or_named() -> None:
    unit = fakes.FakeAce2k("a")
    rig = make_rig("1, 2, 3, 4", fakes.FakeUnits([unit]), {"a": unit})
    assert {h.head: (h.unit.name, h.lane) for h in rig.adapter.hooked}[3] == ("a", 3)
    rig = make_rig("A:2, a:1", fakes.FakeUnits([unit]), {"a": unit})  # any case
    assert {h.head: h.lane for h in rig.adapter.hooked} == {0: 1, 1: 0}


# --- config errors and hook-time problems -----------------------------------------------------


def config_of(lanes: str, names: Iterable[str | None] = ("a", "b")) -> LanesConfig:
    """A config declaring the units names ([ace2k] for None) and lanes:, for a config error."""
    units = fakes.FakeUnits(fakes.FakeAce2k(name) for name in names)
    objects = {"gcode": Gcode(), "ace2k_units": units}
    objects.update((unit.section, unit) for unit in units.units)
    return LanesConfig(Printer(fakes.FakeReactor(), objects, []), {"lanes": lanes})


@pytest.mark.parametrize(
    "lanes",
    ["5", "0", "a:5", "a:0", "a:x", "a:1, a:1", "1, 1", "a:1, b:1, a:2, b:2, a:3", "1, a:2"],
)
def test_a_bad_lanes_entry_stops_at_the_config(lanes: str) -> None:
    with pytest.raises(ValueError, match="ace2k_u1: lanes"):
        ace2k_u1.ACE2kU1(config_of(lanes, names=(None,) if ":" not in lanes else ("a", "b")))


@pytest.mark.parametrize("lanes", [":2", "a:1, :2", " :3, b:1"])
def test_an_entry_with_an_empty_unit_name_stops_at_the_config(lanes: str) -> None:
    with pytest.raises(ValueError) as err:
        ace2k_u1.ACE2kU1(config_of(lanes))
    entry = [e.strip() for e in lanes.split(",") if e.strip().startswith(":")][0]
    assert str(err.value) == f"ace2k_u1: lanes: {entry!r} is not <unit>:<lane>"
    with pytest.raises(ValueError, match="is not <unit>:<lane>"):  # one unit, named or not
        ace2k_u1.ACE2kU1(config_of(lanes, names=("a",)))


def test_two_heads_on_one_unit_lane_is_a_config_error() -> None:
    with pytest.raises(ValueError, match="b:1 feeds two heads"):
        ace2k_u1.ACE2kU1(config_of("a:1, b:1, B:1"))


def test_bare_lanes_with_several_units_stop_at_the_config() -> None:
    for lanes in ("1, 2, 3, 4", "2"):
        with pytest.raises(ValueError) as err:
            ace2k_u1.ACE2kU1(config_of(lanes))
        assert str(err.value) == (
            "ace2k_u1: lanes: needs <unit>:<lane> entries: this printer has several units (a, b)"
        )
    with pytest.raises(ValueError, match="needs <unit>:<lane>"):  # the default lanes too
        ace2k_u1.ACE2kU1(config_of(None))


def test_a_unit_the_config_does_not_declare_stops_at_the_config() -> None:
    with pytest.raises(ValueError) as err:
        ace2k_u1.ACE2kU1(config_of("a:1, a:2, c:1, b:2"))
    assert str(err.value) == "ace2k_u1: lanes: no unit c; units: a, b"
    with pytest.raises(ValueError) as err:  # a named entry on the unnamed [ace2k]
        ace2k_u1.ACE2kU1(config_of("a:1", names=(None,)))
    assert str(err.value) == "ace2k_u1: lanes: no unit a; units: [ace2k]"


def test_a_single_declared_unit_takes_bare_and_named_lanes() -> None:
    assert ace2k_u1.ACE2kU1(config_of("1, 2, 3, 4", names=("a",))).lane_refs[0] == (0, None, 0)
    assert ace2k_u1.ACE2kU1(config_of("a:3", names=("a",))).lane_refs == [(0, "a", 2)]


def test_an_old_interface_on_one_unit_is_a_problem_naming_it() -> None:
    units, by_name = fakes.two_units()
    by_name["b"].feed.API_VERSION = 8
    rig = make_rig("a:1, a:2, b:1, b:2", units, by_name)
    assert rig.gcode.lines[0] == "ace2k_u1: not hooked: ace2k b host interface older than 12"
    assert by_name["a"].feed.extruders == [] and by_name["b"].feed.extruders == []


def test_api_min_is_twelve() -> None:
    assert ace2k_u1_adapter.API_MIN == 12


# --- events -----------------------------------------------------------------------------------


def test_a_feed_event_reaches_only_its_units_head() -> None:
    rig = make_two_unit_rig()
    rig.adapter.on_feed_event(0, {"kind": "tail", "unit": "b", "seq": 0})
    assert rig.told_tail == [2]  # head 3 only
    rig.adapter.on_feed_event(0, {"kind": "tail", "unit": "a", "seq": 0})
    assert rig.told_tail == [2, 0]  # head 1 only
    assert any("lane b:1 ran out at the bay" in line for line in rig.gcode.lines)


def test_a_final_event_ends_only_its_units_follow() -> None:
    rig = make_two_unit_rig()
    rig.left._set_channel_state(1, "load_extruding")  # head 0: unit a lane 1
    rig.right._set_channel_state(0, "load_extruding")  # head 2: unit b lane 1
    a_move = rig.units["a"].feed.started[-1][4]
    assert head(rig, 0).armed and head(rig, 2).armed
    b_move = rig.units["b"].feed.started[-1][4]
    event = {"kind": "stopped", "mode": "following", "stop_by": "host"}
    rig.adapter.on_feed_event(0, dict(event, seq=a_move.seq, unit="a"))
    assert not head(rig, 0).armed and head(rig, 2).armed
    rig.adapter.on_feed_event(0, dict(event, seq=b_move.seq, unit="b"))
    assert not head(rig, 2).armed


# --- the commands -----------------------------------------------------------------------------


def test_eject_with_unit_ejects_that_units_lane() -> None:
    rig = make_two_unit_rig()
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    rig.units["b"].lanes[0].update(insert=True, mode="idle")
    rig.units["a"].lanes[0].update(insert=True, mode="idle")
    rig.gcode.commands["ACEK_EJECT"](Gcmd({"UNIT": "b", "LANE": "1", "WAIT": "0"}))
    rig.reactor.run_timers()
    assert [s[:2] for s in rig.units["b"].feed.started] == [(0, "unload")]
    assert rig.units["a"].feed.started == []
    assert "ace2k_u1: lane b:1 eject: head empty — unloading" in rig.gcode.lines


def test_eject_without_unit_on_two_units_is_refused() -> None:
    rig = make_two_unit_rig()
    with pytest.raises(Exception, match="needs UNIT="):
        rig.module.cmd_ACEK_EJECT(Gcmd({"LANE": "1"}))
    with pytest.raises(Exception, match="no unit c"):
        rig.module.cmd_ACEK_EJECT(Gcmd({"UNIT": "c", "LANE": "1"}))
    assert rig.units["a"].feed.started == [] and rig.units["b"].feed.started == []


def test_ace_clear_with_unit_releases_that_units_head() -> None:
    rig = make_two_unit_rig()
    for index in (0, 2):
        rig.adapter.grip_watch._set_waits_behind(head(rig, index), "a test")
    rig.gcode.commands["ACEK_CLEAR"](Gcmd({"UNIT": "b", "LANE": "1"}, "ACEK_CLEAR"))
    assert not head(rig, 2).waits_behind
    assert head(rig, 0).waits_behind


# --- each head's calls on its own unit --------------------------------------------------------


def test_each_heads_calls_go_to_its_own_units_feed() -> None:
    rig = make_two_unit_rig()
    a, b = rig.units["a"].feed, rig.units["b"].feed
    assert a.extruders == [(0, "extruder"), (1, "extruder1")]
    assert b.extruders == [(0, "extruder2"), (1, "extruder3")]
    assert a.lane_follow == {0: (70.0, False), 1: (70.0, False)}
    assert b.lane_follow == {0: (70.0, False), 1: (70.0, False)}
    rig.right._set_channel_state(1, "load_extruding")  # head 3: unit b lane 2
    assert [s[:2] for s in b.started] == [(1, "assist_both")] and a.started == []
    rig.right._set_channel_state(1, "load_fail")
    assert b.stopped == [1] and a.stopped == []
    rig.left._set_channel_state(0, "load_extruding")  # head 1: unit a lane 2
    assert [s[:2] for s in a.started] == [(1, "assist_both")]


def test_the_watch_reads_each_unit_and_sets_each_heads_grip_on_its_unit() -> None:
    rig = make_two_unit_rig()
    rig.right.runout_sensor[0].detected = True  # head 2 holds a piece unit b lane 1 does not own
    rig.adapter.watch(rig.reactor.now)
    assert rig.units["b"].feed.grips == [(0, 40.0)]
    assert rig.units["a"].feed.grips == []


def test_the_gripped_spools_wait_asks_no_units_reader() -> None:
    rig = make_two_unit_rig()
    h = head(rig, 3)
    rig.adapter.tag_reader.queue(h)
    rig.reactor.run_timers()
    assert rig.units["b"].rfid.reads == [] and rig.units["a"].rfid.reads == []
    assert h.key in rig.adapter.tag_reader.read_waits


def test_ace_clear_without_unit_on_two_units_releases_nothing_and_logs_no_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = make_two_unit_rig()
    rig.adapter.grip_watch._set_waits_behind(head(rig, 0), "a test")
    with caplog.at_level("INFO"):
        rig.gcode.commands["ACEK_CLEAR"](Gcmd({"LANE": "1"}, "ACEK_CLEAR"))
    assert head(rig, 0).waits_behind
    assert not [r for r in caplog.records if r.exc_info]
    assert any("names no unit" in r.getMessage() for r in caplog.records)


def test_an_unread_unit_skips_only_its_own_heads_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = make_two_unit_rig()

    def broken(eventtime: float) -> NoReturn:
        raise RuntimeError("link down")

    rig.units["a"].get_status = broken
    rig.right.runout_sensor[0].detected = True  # head 2 (unit b) holds a piece it does not own
    with caplog.at_level("ERROR"):
        rig.adapter.watch(rig.reactor.now)
        rig.adapter.watch(rig.reactor.now + 0.2)
    assert rig.units["b"].feed.grips == [(0, 40.0)]  # b's heads ran their tick
    said = [r for r in caplog.records if "ace2k a status unread" in r.getMessage()]
    assert len(said) == 1


def test_the_adapter_status_prints_the_other_units_heads_when_one_unit_is_unreadable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = make_two_unit_rig()

    def broken(eventtime: float) -> NoReturn:
        raise RuntimeError("link down")

    rig.units["a"].get_status = broken
    status = Gcode()
    with caplog.at_level("ERROR"):
        rig.gcode.commands["ACEK_ADAPTER_STATUS"](status)
    lanes = [line for line in status.lines if line.startswith("lane ")]
    assert lanes[:2] == [
        "lane a:1 -> e0: ace2k a's status could not be read",
        "lane a:2 -> e1: ace2k a's status could not be read",
    ]
    assert [line.split(" ->")[0] for line in lanes[2:]] == ["lane b:1", "lane b:2"]
    assert all(" bay=" in line for line in lanes[2:])
    assert any("link down" in str(r.exc_info) for r in caplog.records if r.exc_info)


def test_the_bay_watch_and_the_adapter_status_log_an_unread_unit_once_per_episode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = make_two_unit_rig()
    read = rig.units["a"].get_status

    def broken(eventtime: float) -> NoReturn:
        raise RuntimeError("link down")

    def unread() -> list[logging.LogRecord]:
        return [r for r in caplog.records if "ace2k a status unread" in r.getMessage()]

    rig.units["a"].get_status = broken
    with caplog.at_level("ERROR"):
        rig.gcode.commands["ACEK_ADAPTER_STATUS"](Gcode())
        rig.gcode.commands["ACEK_ADAPTER_STATUS"](Gcode())
        rig.adapter.watch(rig.reactor.now)
        rig.adapter.watch(rig.reactor.now + 0.2)
        assert len(unread()) == 1  # one episode: logged once, by whichever read first
        rig.units["a"].get_status = read
        rig.gcode.commands["ACEK_ADAPTER_STATUS"](Gcode())  # read again: the episode ends
        rig.units["a"].get_status = broken
        rig.adapter.watch(rig.reactor.now + 0.4)
        rig.gcode.commands["ACEK_ADAPTER_STATUS"](Gcode())
    assert len(unread()) == 2  # a new episode, logged once more


def test_the_not_hooked_eject_refusal_names_the_units_lane() -> None:
    units, by_name = fakes.two_units()
    by_name["b"].feed.API_VERSION = 8
    rig = make_rig("a:1, a:2, b:1, b:2", units, by_name)
    with pytest.raises(ValueError, match="lane b:1 eject refused: the adapter is not hooked"):
        rig.module.cmd_ACEK_EJECT(Gcmd({"UNIT": "b", "LANE": "1"}))


def test_ejects_on_the_same_lane_of_two_units_keep_apart() -> None:
    rig = make_two_unit_rig()
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    for name in ("a", "b"):
        rig.units[name].lanes[0].update(insert=True, mode="idle")
        rig.gcode.commands["ACEK_EJECT"](Gcmd({"UNIT": name, "LANE": "1", "WAIT": "0"}))
    assert set(rig.adapter.ejector.eject_jobs) == {("a", 0), ("b", 0)}
    rig.reactor.run_timers()
    assert set(rig.adapter.ejector.ejects) == {("a", 0), ("b", 0)}
    b_move = rig.adapter.ejector.ejects[("b", 0)]
    rig.adapter.on_feed_event(0, {"kind": "stuck", "seq": b_move.seq, "unit": "b"})
    assert set(rig.adapter.ejector.ejects) == {("a", 0)}
    assert any(
        "lane b:1 eject failed: stuck — ACEK_CLEAR LANE=1 UNIT=b" in x for x in rig.gcode.lines
    )
    assert not any("lane a:1 eject failed" in line for line in rig.gcode.lines)


# --- the web page's map: which head each unit's lane feeds ---------------------------------------


def one_lane_heads(status: Status) -> Status:
    """A status whose heads have one lane each, as {heads: [its {unit, lane} or None], hooked}:
    each head's list holds that one lane, its current lane (hooked; none while not hooked)."""
    heads = [None if lanes is None else lanes[0] for lanes in status["heads"]]
    assert all(lanes is None or len(lanes) == 1 for lanes in status["heads"])
    assert status["current"] == (heads if status["hooked"] else [None] * 4)
    return {"heads": heads, "hooked": status["hooked"]}


def test_status_maps_each_head_to_its_unit_and_lane() -> None:
    rig = make_two_unit_rig("b:1, b:2, a:1, a:2")
    assert one_lane_heads(rig.module.get_status(0.0)) == {
        "heads": [
            {"unit": "b", "lane": 1},
            {"unit": "b", "lane": 2},
            {"unit": "a", "lane": 1},
            {"unit": "a", "lane": 2},
        ],
        "hooked": True,
    }


def test_status_on_one_unit_with_bare_lanes() -> None:
    assert one_lane_heads(make_one_unit_rig().module.get_status(0.0)) == {
        "heads": [{"unit": None, "lane": n} for n in (1, 2, 3, 4)],
        "hooked": True,
    }
    # lanes: 2, 4 — heads 1 and 3; the others fed by no lane
    assert one_lane_heads(make_one_unit_rig("2, 4").module.get_status(0.0)) == {
        "heads": [None, {"unit": None, "lane": 2}, None, {"unit": None, "lane": 4}],
        "hooked": True,
    }


def test_status_publishes_the_configured_map_while_the_adapter_is_not_hooked() -> None:
    # not hooked (an old interface on b): the map lanes: configures, so the page keeps its in-use
    # locks during a print; hooked False, so it offers no Edit spool
    configured = [
        {"unit": "a", "lane": 1},
        {"unit": "a", "lane": 2},
        {"unit": "b", "lane": 1},
        {"unit": "b", "lane": 2},
    ]
    units, by_name = fakes.two_units()
    by_name["b"].feed.API_VERSION = 8
    rig = make_rig("a:1, a:2, b:1, b:2", units, by_name)
    assert rig.gcode.lines[0].startswith("ace2k_u1: not hooked:")
    assert one_lane_heads(rig.module.get_status(0.0)) == {"heads": configured, "hooked": False}
    # before the hook has run (within a second of klippy:ready): the same
    module = ace2k_u1.ACE2kU1(config_of("a:1, a:2, b:1, b:2"))
    assert one_lane_heads(module.get_status(0.0)) == {"heads": configured, "hooked": False}
    # one unit, bare lanes, not hooked yet
    module = ace2k_u1.ACE2kU1(config_of("2, 4", names=(None,)))
    assert one_lane_heads(module.get_status(0.0)) == {
        "heads": [None, {"unit": None, "lane": 2}, None, {"unit": None, "lane": 4}],
        "hooked": False,
    }


def test_status_while_hooked_maps_only_the_heads_actually_hooked() -> None:
    # the right filament_feed (heads 2 and 3) is missing: b:1 and b:2 are not hooked, so the
    # page must not lock or edit them as feeding a head
    units, by_name = fakes.two_units()
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    gcode = Gcode()
    objects = {"gcode": gcode, "ace2k_units": units, "extruder_list": fakes.extruder_list()}
    for unit in by_name.values():
        objects[unit.section] = unit
    printer = Printer(reactor, objects, [left])
    module = ace2k_u1.ACE2kU1(LanesConfig(printer, {"lanes": "a:1, a:2, b:1, b:2"}))
    module._on_connect()
    module._hook(reactor.now)
    assert {h.head for h in module.adapter.hooked} == {0, 1}
    assert one_lane_heads(module.get_status(0.0)) == {
        "heads": [{"unit": "a", "lane": 1}, {"unit": "a", "lane": 2}, None, None],
        "hooked": True,
    }


# --- "-": a head left to the U1 ------------------------------------------------------------------


def test_a_dash_leaves_its_head_the_u1s_own_between_two_units() -> None:
    rig = make_two_unit_rig("a:1, a:2, -, b:1")
    hooked = {h.head: (h.unit.name, h.lane) for h in rig.adapter.hooked}
    assert hooked == {0: ("a", 0), 1: ("a", 1), 3: ("b", 0)}
    assert "ace2k_u1: hooked lanes [a:1, a:2, b:1]" in rig.gcode.lines
    # the web page: head 3 fed by no lane; ACEK_ADAPTER_STATUS lists only the hooked lanes
    assert one_lane_heads(rig.module.get_status(0.0)) == {
        "heads": [
            {"unit": "a", "lane": 1},
            {"unit": "a", "lane": 2},
            None,
            {"unit": "b", "lane": 1},
        ],
        "hooked": True,
    }
    status = Gcode()
    rig.gcode.commands["ACEK_ADAPTER_STATUS"](status)
    lanes = [line.split(" ->")[0] for line in status.lines if line.startswith("lane ")]
    assert lanes == ["lane a:1", "lane a:2", "lane b:1"]


def test_a_dash_with_one_named_unit() -> None:
    unit = fakes.FakeAce2k("a")
    rig = make_rig("-, a:2, -, a:1", fakes.FakeUnits([unit]), {"a": unit})
    assert {h.head: h.lane for h in rig.adapter.hooked} == {1: 1, 3: 0}


@pytest.mark.parametrize("lanes", ["2, -, 4", "-, 1", "1, 2, 3, -"])
def test_a_dash_among_bare_lanes_stops_at_the_config(lanes: str) -> None:
    # a bare lane n always feeds head n: a dash there would mean nothing
    with pytest.raises(ValueError) as err:
        ace2k_u1.ACE2kU1(config_of(lanes, names=(None,)))
    assert str(err.value) == (
        "ace2k_u1: lanes: '-' needs <unit>:<lane> entries; with bare lanes leave the lane out"
    )


def test_a_dash_does_not_decide_the_form() -> None:
    with pytest.raises(ValueError, match="every entry <unit>:<lane>, or every one bare"):
        ace2k_u1.ACE2kU1(config_of("a:1, 2, -"))
    with pytest.raises(ValueError, match="more than 4 heads"):
        ace2k_u1.ACE2kU1(config_of("a:1, -, -, -, a:2"))


def test_only_dashes_hook_nothing_and_start() -> None:
    module = ace2k_u1.ACE2kU1(config_of("-, -"))  # several units declared: no error
    assert module.lane_refs == []
    assert ace2k_u1.ACE2kU1(config_of("-", names=(None,))).lane_refs == []  # one unit too
    assert one_lane_heads(module.get_status(0.0)) == {
        "heads": [None, None, None, None],
        "hooked": False,
    }


def test_a_dash_does_not_hide_a_duplicate() -> None:
    with pytest.raises(ValueError, match="a:1 feeds two heads"):
        ace2k_u1.ACE2kU1(config_of("a:1, -, a:1"))


def test_the_tag_timer_runs_its_queued_actions_in_head_order() -> None:
    # head 0 is b:1 and head 1 is a:1: the order of the unit names would run head 1 first
    rig = make_two_unit_rig("b:1, a:1")
    adapter = rig.adapter
    ran = []
    adapter.tag_writer._run_tag = lambda key, action: ran.append((key, action[1]))
    adapter.tag_writer.tag_pending[head(rig, 1).key] = ("clear", 1, None)
    adapter.tag_writer.tag_pending[head(rig, 0).key] = ("clear", 0, None)
    adapter.tag_writer._run_tags(rig.reactor.now)
    assert ran == [(("b", 0), 0), (("a", 0), 1)]
    assert adapter.tag_writer.tag_pending == {}


def test_a_lanes_key_is_one_rule_for_a_unit_and_for_its_name() -> None:
    rig = make_two_unit_rig()
    b = rig.units["b"]
    assert ace2k_u1_common.unit_key(b, 2) == ace2k_u1_common.unit_key("b", 2) == ("b", 2)
    assert ace2k_u1_common.unit_key(None, 2) == ace2k_u1_common.unit_key(fakes.FakeAce2k(), 2) == 2
    # the event's name and the unit find the same head
    assert rig.adapter.hooked_lane("b", 0) is rig.adapter.find(0, b) is head(rig, 2)


def counting_reads(unit: FakeAce2k) -> list[float]:
    """unit's get_status, counted: [calls]."""
    calls = []
    real = unit.get_status

    def get_status(eventtime: float) -> Status:
        calls.append(eventtime)
        return real(eventtime)

    unit.get_status = get_status
    return calls


def test_the_bay_watch_reads_each_unit_once_per_tick_also_when_it_stops_an_adopted_follow() -> None:
    # ace2k's follow on a lane waiting behind a piece, with a motion of the adapter's own still
    # open: the adopt's stop judges the lane on the tick's view, not on a fresh status read
    rig = make_two_unit_rig()
    a, b = rig.units["a"], rig.units["b"]
    h = head(rig, 0)
    a.lanes[0].update(mode="following", insert=True)
    a.feed.default_moves[0] = fakes.FakeMove(0, "assist_both", 7)
    h.waits_behind = True
    h.motion_seq, h.motion_open = 5, True
    reads = {"a": counting_reads(a), "b": counting_reads(b)}
    rig.adapter.watch(rig.reactor.now)
    assert a.feed.stopped == [0]  # ace2k's follow stopped
    assert h.expect_stopped is True  # judged running on the tick's view: its own motion answers
    assert {name: len(calls) for name, calls in reads.items()} == {"a": 1, "b": 1}


# --- one active lane per shared extruder (bench finding 1) ------------------------------------

NOT_ACTIVE = "ace2k_u1: lane a:4 shares e3 and is not its active lane: no follow"


def shared_rig() -> tuple[SimpleNamespace, FakeUnits, FakeAce2k, Hooked]:
    """The bench: heads 1-4 on unit a's lanes, a:4 and b:1 on extruder3, nothing chosen."""
    rig = make_two_unit_rig("a:1, a:2, a:3, a:4")
    units = rig.module.adapter.units
    units.share("extruder3", ("a", 3), ("b", 0))
    rig.units["a"].lanes[3]["insert"] = True
    return rig, units, rig.units["a"], head(rig, 3)


def said(caplog: pytest.LogCaptureFixture, line: str) -> int:
    return [r.getMessage() for r in caplog.records].count(line)


def test_the_u1s_load_on_a_head_whose_lane_shares_its_extruder_unchosen_arms_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig, units, a, h = shared_rig()
    with caplog.at_level(logging.INFO):
        rig.right._set_channel_state(1, "load_extruding")  # the U1 loads head 4
        rig.right._set_channel_state(1, "load_finish")
    assert ("a", 3) in units.asked
    assert [s for s in a.feed.started if s[0] == 3] == [] and a.feed.attempts == 0
    assert h.follow_wanted and not h.armed  # kept: ACEK_LANE_ACTIVE may choose it later
    assert said(caplog, NOT_ACTIVE) == 1  # once per episode
    assert not [line for line in rig.gcode.lines if "lane a:4" in line]  # nothing into the U1


def test_acek_lane_active_choosing_the_lane_is_adopted_not_fought() -> None:
    rig, units, a, h = shared_rig()
    rig.right._set_channel_state(1, "load_extruding")
    units.active["extruder3"] = ("a", 3)  # ACEK_LANE_ACTIVE UNIT=a LANE=4: ace2k arms it
    a.feed.default_moves[3] = fakes.FakeMove(3, "assist_both", 9)
    a.lanes[3].update(mode="following")
    rig.adapter.watch(rig.reactor.now)
    assert h.armed and h.assist_seq == 9
    assert a.feed.started == [] and a.feed.stopped == []


def test_acek_lane_active_choosing_the_other_units_lane_is_not_re_armed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig, units, a, h = shared_rig()
    rig.right._set_channel_state(1, "load_extruding")
    units.active["extruder3"] = ("a", 3)
    a.feed.default_moves[3] = fakes.FakeMove(3, "assist_both", 9)
    a.lanes[3].update(mode="following")
    rig.adapter.watch(rig.reactor.now)
    # ACEK_LANE_ACTIVE UNIT=b LANE=1: ace2k stops a:4 as ACEK_STOP would, then chooses b:1
    units.active["extruder3"] = ("b", 0)
    stopped = {"kind": "stopped", "mode": "following", "stop_by": "command", "unit": "a"}
    rig.adapter.on_feed_event(3, dict(stopped, seq=9))
    a.feed.default_moves[3].event = dict(stopped, seq=9)  # ace2k's move ended with it
    a.lanes[3].update(mode="idle")
    assert not h.armed
    with caplog.at_level(logging.INFO):
        rig.reactor.now += 5.0
        rig.adapter.watch(rig.reactor.now)  # the standing re-arm
        rig.right._set_channel_state(1, "load_extruding")  # the U1 loads head 4 again
        rig.right._set_channel_state(1, "load_finish")
    assert a.feed.started == [] and not h.armed


def test_the_cores_not_active_refusal_is_a_quiet_no_follow(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # the coordinator's answer and the start disagree (the choice moved in between): the core's
    # refusal is taken as the same no follow, once, never a retry or an error into the U1
    rig, units, a, h = shared_rig()
    units.active["extruder3"] = ("a", 3)
    a.feed.refuse = "not_active"
    with caplog.at_level(logging.INFO):
        rig.right._set_channel_state(1, "load_extruding")
        rig.right._set_channel_state(1, "load_finish")
    assert a.feed.attempts == 2 and a.feed.started == []
    assert h.follow_wanted and not h.armed
    assert said(caplog, NOT_ACTIVE) == 1
    assert not [line for line in rig.gcode.lines if "not armed" in line]


def test_a_lane_whose_extruder_is_its_own_is_armed_as_before() -> None:
    rig, units, a, h = shared_rig()
    rig.left._set_channel_state(1, "load_extruding")  # head 1: a:1, its own extruder
    assert [s[:2] for s in a.feed.started] == [(0, "assist_both")]


# --- a U1 load on a head whose extruder several lanes share ---------------------------------

SHARED_EMPTY = (
    "ace2k_u1: e3 is shared by lanes a:4, b:1: load refused with its sensor empty — put the"
    " filament in the head by hand, then load"
)


def test_a_load_on_a_shared_head_with_its_sensor_empty_is_refused_pushing_nothing() -> None:
    rig, units, a, h = shared_rig()
    units.active["extruder3"] = ("a", 3)  # chosen or not: the head's filament is not ours to pick
    with pytest.raises(RuntimeError, match="general"):
        rig.right._put_into_drive(1)  # the U1 loads head 4
    assert rig.right.channel_error[1] == "general" and rig.right.exception_code[1] == 30
    assert a.feed.started == [] and a.feed.attempts == 0
    assert rig.gcode.lines.count(SHARED_EMPTY) == 1
    assert not h.armed


SHARED_PRELOAD = (
    "ace2k_u1: lane a:4: e3 is shared by lanes a:4, b:1: the preload pushes nothing into it with"
    " its sensor empty"
)


def test_a_preload_on_a_shared_head_with_its_sensor_empty_pushes_nothing_and_fails_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # the bench: a filament inserted in bay a:4 — the U1's automatic preload — while e3, which
    # b:1 shares, is empty. ace2k parks the filament by itself; the adapter pushes nothing and
    # the U1's preload ends as a normal one, no error on its screen
    rig, units, a, h = shared_rig()
    rig.right.module_exist[1] = True
    rig.right._port[1].detected = True
    before = len(rig.gcode.lines)
    with caplog.at_level(logging.INFO):
        rig.right._do_feed(1, "preload")
    states = [c[2] for c in rig.right.calls if c[0] == "state" and c[1] == 1]
    assert states[-1] == "preload_finish" and "preload_fail" not in states
    assert rig.right.channel_error[1] == "ok" and rig.right.channel_error_state[1] == "none"
    assert rig.right.exception_manager.raised == []
    assert rig.right.channel_active is None
    assert a.feed.started == [] and a.feed.attempts == 0 and a.feed.stopped == []
    assert not h.armed  # nothing at the head to follow
    assert rig.gcode.lines[before:] == []  # nothing on the console
    assert said(caplog, SHARED_PRELOAD) == 1


def test_a_preload_on_a_shared_head_then_an_explicit_load_is_refused_as_before() -> None:
    rig, units, a, h = shared_rig()
    rig.right.module_exist[1] = True
    rig.right._port[1].detected = True
    rig.right._do_feed(1, "preload")
    before = len(rig.gcode.lines)
    with pytest.raises(RuntimeError, match="general"):
        rig.right._put_into_drive(1)  # the U1's load of head 4, from its screen or a G-code
    assert rig.right.channel_error[1] == "general" and rig.right.exception_code[1] == 30
    assert a.feed.started == [] and a.feed.attempts == 0
    assert rig.gcode.lines[before:] == [SHARED_EMPTY]


def test_a_preload_on_a_head_not_shared_pushes_as_before() -> None:
    rig, units, a, h = shared_rig()
    rig.units["a"].lanes[0]["insert"] = True
    rig.left.module_exist[1] = True
    rig.left._port[1].detected = True
    rig.reactor.on_pause = [lambda now: setattr(rig.left.runout_sensor[1], "detected", True)]
    rig.left._do_feed(1, "preload")  # head 1: a:1, its own extruder
    assert a.feed.started[0][:2] == (0, "feed")
    assert rig.left.channel_state[1] == "preload_finish" and rig.left.channel_error[1] == "ok"
    assert not [line for line in rig.gcode.lines if "shared" in line]


def test_a_load_on_a_shared_head_holding_filament_goes_on_and_follows_only_when_active() -> None:
    rig, units, a, h = shared_rig()
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    rig.right.runout_sensor[1].detected = True  # filament put in the head by hand
    assert rig.right._put_into_drive(1) is None  # the load goes on, nothing pushed
    rig.right._set_channel_state(1, "load_extruding")
    assert a.feed.started == [] and not h.armed and h.follow_wanted  # not the active lane
    assert SHARED_EMPTY not in rig.gcode.lines
    units.active["extruder3"] = ("a", 3)
    rig.right._set_channel_state(1, "load_finish")
    assert [s[:2] for s in a.feed.started] == [(3, "assist_both")] and h.armed


def test_a_load_on_a_head_not_shared_pushes_as_before() -> None:
    rig, units, a, h = shared_rig()
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    head1 = head(rig, 0)  # a:1, its own extruder
    rig.reactor.on_pause = [lambda now: setattr(rig.left.runout_sensor[1], "detected", True)]
    assert rig.left._put_into_drive(1) is None  # empty head: pushed to its sensor
    assert a.feed.started[0][:2] == (0, "feed") and a.feed.stopped == [0]
    assert rig.left.channel_error[1] == "ok"
    assert not [line for line in rig.gcode.lines if "shared" in line]
    assert head1.lane == 0
