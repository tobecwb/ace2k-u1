"""lanes: groups (several lanes per head, joined by +) and the swap's keys."""

from __future__ import annotations

import types
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1
import ace2k_u1_common
import ace2k_u1_config
import ace2k_u1_probe
import ace2k_u1_push
import fakes
import pytest
import test_units
from test_check import Printer
from test_units import LanesConfig, make_rig

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked, Slot
    from ace2k_u1_config import LaneRef, SwapConfig
    from fakes import FakeAce2k, FakeUnits


def parse(
    lanes: str, units: Iterable[str] = ("a", "b"), values: Mapping[str, object] | None = None
) -> list[LaneRef]:
    config = LanesConfig(Printer(fakes.FakeReactor(), {}, []), dict(values or {}, lanes=lanes))
    config.unit_names = list(units)
    return ace2k_u1_config.parse_lanes(config)


@pytest.fixture(autouse=True)
def declared(monkeypatch: pytest.MonkeyPatch) -> None:
    # parse()'s configs name their units; a rig's config (make_rig) declares them as on the
    # printer, through the real rule
    real = ace2k_u1_config.ace2k_units.unit_names
    monkeypatch.setattr(
        ace2k_u1_config.ace2k_units,
        "unit_names",
        lambda config: getattr(config, "unit_names", None) or real(config),
    )


def test_a_group_feeds_one_head_with_each_lane() -> None:
    refs = parse("a:1+a:2, a:3, -, b:1+b:2")
    assert refs == [(0, "a", 0), (0, "a", 1), (1, "a", 2), (3, "b", 0), (3, "b", 1)]


def test_a_bare_group_on_one_unit_feeds_its_first_lanes_head() -> None:
    assert parse("1+2, 3", units=(None,)) == [(0, None, 0), (0, None, 1), (2, None, 2)]


@pytest.mark.parametrize(
    "lanes, why",
    [
        ("a:1+a:2, a:2", "feeds two heads"),
        ("a:1+, a:3", "is not <unit>:<lane>"),
    ],
)
def test_a_bad_group_stops_klipper(lanes: str, why: str) -> None:
    with pytest.raises(Exception, match=why):
        parse(lanes)


def test_a_splitter_distance_on_a_head_with_one_lane_stops_klipper() -> None:
    rig_values = {"head2_splitter_mm": "400"}
    with pytest.raises(Exception, match="head2_splitter_mm: head 2 has one lane"):
        ace2k_u1_config.read_swap_keys(
            LanesConfig(Printer(fakes.FakeReactor(), {}, []), rig_values),
            ace2k_u1_config.lane_counts([(0, "a", 0), (0, "a", 1), (1, "a", 2)]),
        )


def test_the_lanes_per_head_are_counted_once_for_both_readers() -> None:
    refs = [(0, "a", 0), (0, "a", 1), (1, "a", 2), (3, "b", 0), (3, "b", 1), (3, "b", 2)]
    assert ace2k_u1_config.lane_counts(refs) == {0: 2, 1: 1, 3: 3}


def test_the_swap_keys_take_their_defaults() -> None:
    keys = ace2k_u1_config.read_swap_keys(
        LanesConfig(Printer(fakes.FakeReactor(), {}, []), {}),
        ace2k_u1_config.lane_counts([(0, "a", 0), (0, "a", 1)]),
    )
    assert keys.swap_speed == 70.0
    assert keys.splitter_mm == {0: 1000.0}
    assert keys.splitter_default == {0}
    assert keys.colour_max_distance == 25.0 and keys.colour_min_margin == 8.0
    assert keys.probe_mm == 100.0 and keys.probe_speed == 50.0 and keys.probe_max_temp == 60.0
    assert keys.unload_temps == {"PLA": 220.0}


def test_a_group_with_a_dash_in_the_list_is_a_config_error_for_a_head_left_to_the_u1() -> None:
    with pytest.raises(Exception, match="is not <unit>:<lane>"):
        parse("a:1+-")


def keys(
    values: Mapping[str, object],
    refs: list[LaneRef] | tuple[LaneRef, ...] = ((0, "a", 0), (0, "a", 1)),
) -> SwapConfig:
    config = LanesConfig(Printer(fakes.FakeReactor(), {}, []), values)
    return ace2k_u1_config.read_swap_keys(config, ace2k_u1_config.lane_counts(refs))


@pytest.mark.parametrize(
    "values",
    [
        {"head1_splitter_mm": "0"},
        {"head1_splitter_mm": "2001"},
        {"swap_speed": "0"},
        {"probe_mm": "59"},
        {"probe_mm": "301"},
        {"probe_speed": "0"},
        {"probe_max_temp": "200"},
        {"probe_max_temp": "29"},
    ],
)
def test_a_key_outside_its_bounds_stops_klipper(values: dict[str, str]) -> None:
    with pytest.raises(Exception, match="must be"):
        keys(values)


def test_the_swap_speed_may_be_the_units_ceiling() -> None:
    assert keys({"swap_speed": "70"}).swap_speed == 70.0


def test_a_speed_above_70_is_read_and_left_to_the_hook() -> None:
    """The ceiling is the unit's, judged at the hook (test_check)."""
    got = keys({"swap_speed": "70.5", "probe_speed": "71"})
    assert (got.swap_speed, got.probe_speed) == (70.5, 71.0)


class ExtruderSection:
    """An [ace2k …] section with its laneN_extruder keys."""

    def __init__(self, name: str, values: dict[str, str]) -> None:
        self.name, self.values = name, values

    def get_name(self) -> str:
        return self.name

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)


class SectionsConfig:
    error = ValueError

    def __init__(self, sections: list[ExtruderSection]) -> None:
        self.sections = sections

    def get_prefix_sections(self, prefix: str) -> list[ExtruderSection]:
        return [s for s in self.sections if s.name.startswith(prefix)]


STANDARD = ace2k_u1.extruder_names(fakes.extruder_list())  # extruder, extruder1, ...


def lane_extruder_problem(
    config: SectionsConfig, refs: list[LaneRef], names: Mapping[int, str] = STANDARD
) -> str | None:
    counts = ace2k_u1_config.lane_counts(refs)
    keys = ace2k_u1_config.read_lane_extruders(config, refs, counts)
    return ace2k_u1_config.check_lane_extruders(keys, names)


def test_a_lane_extruder_key_against_its_group_head_stops_klipper() -> None:
    refs = [(0, "a", 0), (0, "a", 1), (1, "a", 2), (3, "b", 0)]
    config = SectionsConfig(
        [
            ExtruderSection("ace2k a", {"lane2_extruder": " extruder1 "}),
            ExtruderSection("ace2k b", {}),
            ExtruderSection("ace2k_tag x", {"lane2_extruder": "nonsense"}),
        ]
    )
    assert lane_extruder_problem(config, refs) == (
        "ace2k_u1: lanes: a:2 is in head 1's group but [ace2k a] lane2_extruder is extruder1"
    )


def test_a_lane_extruder_key_that_agrees_or_is_on_a_lone_lane_is_left_to_ace2k() -> None:
    refs = [(0, "a", 0), (0, "a", 1), (1, "a", 2), (3, None, 0)]
    config = SectionsConfig(
        [
            # head 1's own extruder: agrees; a:3 alone on head 2: not this adapter's to check
            ExtruderSection(
                "ace2k a", {"lane1_extruder": "extruder", "lane3_extruder": "extruder3"}
            ),
            ExtruderSection("ace2k", {"lane1_extruder": "extruder"}),
        ]
    )
    assert lane_extruder_problem(config, refs) is None


def test_a_group_lane_whose_unit_has_no_section_has_no_lane_extruder_key() -> None:
    refs = [(0, "a", 0), (0, "a", 1)]
    assert ace2k_u1_config.read_lane_extruders(SectionsConfig([]), refs, {0: 2}) == []


def test_a_lane_extruder_key_is_found_in_a_section_named_in_capitals() -> None:
    # lanes: reads unit names without case; Klipper keeps a section's name as written
    refs = [(0, "a", 0), (0, "a", 1)]
    config = SectionsConfig([ExtruderSection("ace2k A", {"lane2_extruder": "extruder1"})])
    assert lane_extruder_problem(config, refs) == (
        "ace2k_u1: lanes: a:2 is in head 1's group but [ace2k A] lane2_extruder is extruder1"
    )


def test_a_lane_extruder_key_of_the_unnamed_unit_against_its_group_head_stops_klipper() -> None:
    refs = [(2, None, 0), (2, None, 1)]
    config = SectionsConfig([ExtruderSection("ace2k", {"lane1_extruder": "extruder"})])
    assert lane_extruder_problem(config, refs) == (
        "ace2k_u1: lanes: 1 is in head 3's group but [ace2k] lane1_extruder is extruder"
    )


def test_a_lane_extruder_key_is_checked_against_the_u1s_own_extruder_names() -> None:
    refs = [(0, "a", 0), (0, "a", 1)]
    names = {0: "left_tool", 1: "right_tool"}
    agrees = SectionsConfig([ExtruderSection("ace2k a", {"lane2_extruder": "left_tool"})])
    assert lane_extruder_problem(agrees, refs, names) is None
    usual = SectionsConfig([ExtruderSection("ace2k a", {"lane1_extruder": "extruder"})])
    assert lane_extruder_problem(usual, refs, names) == (
        "ace2k_u1: lanes: a:1 is in head 1's group but [ace2k a] lane1_extruder is extruder"
    )


def test_a_lane_extruder_key_against_its_group_head_stops_klipper_at_connect() -> None:
    # the U1's extruders known at klippy:connect: a disagreement is Klipper's config error there
    extruders = fakes.extruder_list(2)
    extruders[0].name, extruders[1].name = "left_tool", "right_tool"
    printer = Printer(fakes.FakeReactor(), {"extruder_list": extruders}, [])
    module = object.__new__(ace2k_u1.ACE2kU1)
    module.printer = printer
    module.config_error = ValueError
    config = SectionsConfig([ExtruderSection("ace2k a", {"lane1_extruder": "extruder"})])
    refs = [(0, "a", 0), (0, "a", 1)]
    module.lane_extruder_keys = ace2k_u1_config.read_lane_extruders(
        config, refs, ace2k_u1_config.lane_counts(refs)
    )
    module.extruders_checked = False
    with pytest.raises(ValueError, match="lane1_extruder is extruder"):
        module._on_connect()


def test_a_splitter_distance_on_a_head_with_no_lane_stops_klipper() -> None:
    with pytest.raises(Exception, match="head3_splitter_mm: head 3 has one lane or none"):
        keys({"head3_splitter_mm": "400"})


def shared_head_rig() -> tuple[SimpleNamespace, dict[str, FakeAce2k]]:
    units, by_name = fakes.two_units()
    units.share("extruder", ("a", 0), ("a", 1))
    return make_rig("a:1+a:2, a:3, -, b:1", units, by_name), by_name


def test_every_lane_of_a_group_is_hooked_on_one_slot() -> None:
    rig, by_name = shared_head_rig()
    a = rig.adapter
    assert sorted((h.head, h.unit.name, h.lane) for h in a.hooked) == [
        (0, "a", 0),
        (0, "a", 1),
        (1, "a", 2),
        (3, "b", 0),
    ]
    slot = a.slot_of_head(0)
    assert [h.lane for h in slot.lanes] == [0, 1] and slot.current is None
    assert a.slot_of_head(1).current.lane == 2
    assert "ace2k_u1: hooked lanes [a:1+a:2, a:3, -, b:1]" in rig.gcode.lines


def test_a_shared_head_on_the_default_splitter_distance_is_said_at_the_hook() -> None:
    rig, _ = shared_head_rig()
    assert (
        "ace2k_u1: head 1 rolls back the default 1000 mm: run ACEK_SPLITTER_MEASURE HEAD=1, then"
        " SAVE_CONFIG" in rig.gcode.lines
    )
    assert sum("rolls back the default" in line for line in rig.gcode.lines) == 1


def test_a_shared_head_with_its_own_splitter_distance_says_nothing_at_the_hook() -> None:
    units, by_name = fakes.two_units()
    units.share("extruder", ("a", 0), ("a", 1))
    rig = make_rig("a:1+a:2, a:3", units, by_name, values={"head1_splitter_mm": "450"})
    assert not [line for line in rig.gcode.lines if "rolls back the default" in line]


def test_the_current_lane_follows_the_chosen_active_lane_at_the_hook() -> None:
    units, by_name = fakes.two_units()
    units.share("extruder", ("a", 0), ("a", 1))
    units.active["extruder"] = ("a", 1)
    rig = make_rig("a:1+a:2, a:3", units, by_name)
    assert rig.adapter.slot_of_head(0).current.lane == 1


def test_a_slot_without_a_current_lane_refuses_a_push_into_an_empty_head() -> None:
    rig, _ = shared_head_rig()
    with pytest.raises(RuntimeError, match="push to head 1: no lane in the head"):
        rig.left._put_into_drive(1)  # head 0 is the left feed's channel 1
    assert (
        "ace2k_u1: head 1 is shared by lanes a:1, a:2: load refused with no lane known in it and"
        " its sensor empty — choose the lane with ACEK_LANE_ACTIVE, then load"
    ) in rig.gcode.lines


def test_the_status_lists_each_heads_lanes_and_its_current_one() -> None:
    rig, _ = shared_head_rig()
    status = rig.module.get_status()
    assert status["heads"][0] == [{"unit": "a", "lane": 1}, {"unit": "a", "lane": 2}]
    assert status["heads"][1] == [{"unit": "a", "lane": 3}]
    assert status["current"][0] is None and status["current"][1] == {"unit": "a", "lane": 3}


def test_the_status_names_the_lane_each_heads_setting_was_written_for() -> None:
    rig, _ = shared_head_rig()
    assert rig.module.get_status()["setting_for"] == [None] * 4
    slot = rig.adapter.slot_of_head(0)
    slot.setting_for = slot.lanes[1]
    assert rig.module.get_status()["setting_for"][0] == {"unit": "a", "lane": 2}


def test_a_slot_without_a_current_lane_moves_nothing_and_arms_nothing() -> None:
    # head 0 shared by a:1 and a:2, both with filament in their bays, none chosen: no hook of
    # the U1's channel reaches its motor, tells it an inlet, pushes or arms a lane
    rig, by_name = shared_head_rig()
    a, left = by_name["a"], rig.left
    for lane in (0, 1):
        a.lanes[lane]["insert"] = True
    left.calls.clear()
    left.motor.calls.clear()
    left._hang_neutral(1)
    left._port_event_handler(False, 1)
    assert left._port[1].get_filament_detected() is False
    left._do_feed(1, "preload")
    assert left.channel_state[1] == "preload_finish" and left.channel_active is None
    left.channel_active = 1
    left._set_channel_state(1, "load_extruding")
    left.motor.run_one_cycle(fakes.fake_module().FEED_MOTOR_DIR_B, 0.85, 0.1)  # extrude retry
    left._set_channel_state(1, "load_finish")
    assert left.motor.calls == []
    assert [c for c in left.calls if c[0] in ("hang_neutral", "port_event", "do_feed")] == []
    assert a.feed.started == [] and a.feed.attempts == 0
    assert not any(h.armed for h in rig.adapter.slot_of_head(0).lanes)


def chosen_rig(lane: int) -> tuple[SimpleNamespace, dict[str, FakeAce2k]]:
    """head 0 fed by a:1+a:2, the coordinator's choice for its extruder a's lane (0-based) at
    the hook."""
    units, by_name = fakes.two_units()
    units.share("extruder", ("a", 0), ("a", 1))
    units.active["extruder"] = ("a", lane)
    return make_rig("a:1+a:2, a:3", units, by_name), by_name


def test_the_u1s_wheel_of_a_group_head_is_one_object_whatever_lane_is_current() -> None:
    rig, _ = chosen_rig(1)
    a, left = rig.adapter, rig.left
    slot = a.slot_of_head(0)
    first, second = slot.lanes
    wheel = left.wheel[1]
    assert wheel is slot.wheel and left.wheel_2[1] is wheel
    for lane in (first, second, None):
        a.set_current(slot, lane)
        assert left.wheel[1] is wheel and left.wheel_2[1] is wheel


def test_the_u1s_wheel_counts_go_on_from_their_last_value_after_a_swap() -> None:
    # each lane's encoder holds its own absolute travel: a change of lane re-bases the U1's wheel
    # on the new lane's encoder, so its counts continue instead of jumping to the new lane's
    rig, by_name = chosen_rig(1)
    a, unit = rig.adapter, by_name["a"]
    slot = a.slot_of_head(0)
    first, second = slot.lanes
    wheel = rig.left.wheel[1]
    per_mm = wheel.counts_per_mm
    unit.lanes[0]["encoder_mm"] = 5000.0
    unit.lanes[1]["encoder_mm"] = 0.0
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    base = wheel.get_counts()
    unit.lanes[1]["encoder_mm"] = 100.0  # a:2 (current) travels 100 mm
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    after_a2 = wheel.get_counts()
    assert after_a2 - base == int(100.0 * per_mm + 1e-6)
    a.set_current(slot, first)
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert wheel.get_counts() == after_a2  # no jump on the swap
    unit.lanes[0]["encoder_mm"] = 5050.0  # a:1 (now current) travels 50 mm
    unit.lanes[1]["encoder_mm"] = 900.0  # a:2, parked, is not the U1's wheel any more
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert abs(wheel.get_counts() - (after_a2 + 50.0 * per_mm)) <= 1


def test_a_group_head_with_a_current_lane_pushes_that_lane_on_a_load() -> None:
    rig, by_name = chosen_rig(1)
    fakes.give_materials(rig.module)  # a load and an eject need a material set
    a = by_name["a"]
    a.lanes[1]["insert"] = True
    rig.reactor.on_pause = [lambda now: setattr(rig.left.runout_sensor[1], "detected", True)]
    assert rig.left._put_into_drive(1) is None  # empty head: a:2 pushed to its sensor
    assert a.feed.started[0][:2] == (1, "feed") and a.feed.stopped == [1]
    assert rig.left.channel_error[1] == "ok"
    assert not [line for line in rig.gcode.lines if "shared" in line]


class LaneActiveGcode(test_units.Gcode):
    """The console with ace2k's ACEK_LANE_ACTIVE: it records the choice in the coordinator, as
    ace2k does (set by the test: units)."""

    units = None

    def __init__(self) -> None:
        super().__init__()
        self.register_command("ACEK_LANE_ACTIVE", self._lane_active)

    def _lane_active(self, gcmd: test_units.Gcmd) -> None:
        unit = gcmd.params.get("UNIT")
        lane = int(gcmd.params["LANE"]) - 1
        extruder = next(e for e, keys in self.units.shared.items() if (unit, lane) in keys)
        self.units.active[extruder] = (unit, lane)


def lane_active_rig(
    monkeypatch: pytest.MonkeyPatch, lanes: str
) -> tuple[SimpleNamespace, FakeUnits]:
    units, by_name = fakes.two_units()
    units.share("extruder", ("a", 0), ("a", 1))
    units.share("extruder1", ("a", 2), ("b", 0))
    monkeypatch.setattr(LaneActiveGcode, "units", units)
    monkeypatch.setattr(test_units, "Gcode", LaneActiveGcode)
    return make_rig(lanes, units, by_name), units


def test_acek_lane_active_after_the_hook_moves_a_group_heads_current_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, units = lane_active_rig(monkeypatch, "a:1+a:2, a:3")
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is None
    rig.gcode.commands["ACEK_LANE_ACTIVE"](test_units.Gcmd({"UNIT": "a", "LANE": "2"}))
    assert slot.current.lane == 1 and rig.left.wheel[1] is slot.wheel
    rig.gcode.commands["ACEK_LANE_ACTIVE"](test_units.Gcmd({"UNIT": "a", "LANE": "1"}))
    assert slot.current.lane == 0


def test_acek_lane_active_on_a_lane_outside_the_group_leaves_its_current_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, units = lane_active_rig(monkeypatch, "a:1+a:2, a:3")
    slot = rig.adapter.slot_of_head(0)
    rig.gcode.commands["ACEK_LANE_ACTIVE"](test_units.Gcmd({"UNIT": "a", "LANE": "2"}))
    units.active["extruder"] = ("b", 3)  # the coordinator's choice: a lane not in head 1's group
    rig.gcode.commands["ACEK_LANE_ACTIVE"](test_units.Gcmd({"UNIT": "a", "LANE": "3"}))
    assert slot.current.lane == 1
    assert rig.adapter.slot_of_head(1).current.lane == 2  # one-lane head: its lane, always


def test_acek_lane_active_is_not_wrapped_without_a_group(monkeypatch: pytest.MonkeyPatch) -> None:
    rig, units = lane_active_rig(monkeypatch, "a:1, a:2, a:3")
    assert rig.gcode.commands["ACEK_LANE_ACTIVE"] == rig.gcode._lane_active


def test_a_parked_lane_with_a_spool_inserted_is_neither_held_nor_armed() -> None:
    # head 1 holds a:1's filament; a spool goes into a:2's bay, parked outside the head: its bay
    # is read, nothing of the head's sensor is applied to it; it has no grip: the unit's own load
    # searches for its tag and comes back to the parking point
    rig, by_name = chosen_rig(0)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    a2 = slot.lanes[1]
    rig.left.runout_sensor[1].detected = True
    rig.left.config["auto_mode"][1] = False  # an insert there would be armed at once
    a.lanes[1]["insert"] = True
    a.lanes[1]["encoder_mm"] = 12.0
    rig.reactor.run_timers(rig.reactor.now + 10.0)
    assert a2.present is True
    assert not (a2.waits_behind or a2.rise_held or a2.follow_wanted or a2.armed)
    assert a2.wheel_source == "encoder"
    assert [g for g in a.feed.grips if g[0] == 1] == []
    assert [x for x in a.feed.holds if x[0] == 1] == []
    assert [s for s in a.feed.started if s[0] == 1] == []
    # ace2k's follow on it (none should run) is not adopted by the watch either
    move = fakes.FakeMove(1, "assist_both", 99)
    a.feed.default_moves[1] = move
    a.lanes[1]["mode"] = "following"
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert not a2.armed


def test_set_current_starts_the_old_and_the_new_lane_clean() -> None:
    rig, by_name = chosen_rig(0)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    a1, a2 = slot.lanes
    for h in (a1, a2):
        h.waits_behind = h.rise_held = True
        h.grip_wanted = h.grip_live = True
    rig.adapter.set_current(slot, a2)
    for h in (a1, a2):
        assert not (h.waits_behind or h.rise_held or h.grip_wanted or h.grip_live)
    assert sorted(g for g in a.feed.grips if g[1] == 0) == [(0, 0), (1, 0)]
    assert sorted(x for x in a.feed.holds if x[1] is False) == [(0, False), (1, False)]


def test_a_declined_preload_waits_for_the_u1s_lock_at_most_its_bound(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig, by_name = shared_head_rig()
    a, left = by_name["a"], rig.left
    for lane in (0, 1):
        a.lanes[lane]["insert"] = True
    left.channel_active = 0  # the other channel holds the U1's lock and never lets it go
    state = left.channel_state[1]
    start = rig.adapter.reactor.monotonic()
    left._do_feed(1, "preload")
    waited = rig.adapter.reactor.monotonic() - start
    assert ace2k_u1_push.CHANNEL_WAIT_S <= waited < ace2k_u1_push.CHANNEL_WAIT_S + 1.0
    assert left.channel_active == 0 and left.channel_state[1] == state  # left as it is
    assert "the U1's lock still held" in caplog.text


def parked_eject_rig(printing: bool) -> tuple[SimpleNamespace, FakeAce2k, Hooked]:
    """Head 1 holds a:1's filament; a:2, parked outside it, has a spool in its bay."""
    rig, by_name = chosen_rig(0)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    a2 = slot.lanes[1]
    rig.left.runout_sensor[1].detected = True  # the head's sensor: a:1's filament
    a.lanes[1].update(insert=True, mode="idle")
    rig.adapter.print_stats = type("Stats", (), {"state": "printing" if printing else "standby"})
    return rig, a, a2


@pytest.mark.parametrize("printing", [True, False])
def test_a_parked_lanes_eject_is_a_plain_unload_out_of_its_bay(printing: bool) -> None:
    # the head's sensor reads a:1, not a:2: a:2's eject is allowed during a print, is no probe
    # and no U1 unload of the head
    rig, a, a2 = parked_eject_rig(printing)
    sent = []
    rig.gcode.run_script = sent.append
    rig.adapter.ejector.start_eject(1, "a")
    rig.reactor.run_timers()
    assert [s[:2] for s in a.feed.started] == [(1, "unload")]
    assert sent == []
    assert "ace2k_u1: lane a:2 eject: parked outside its head — unloading" in rig.gcode.lines
    move = a.feed.started[0][4]
    rig.adapter.on_feed_event(1, {"kind": "done", "seq": move.seq, "unit": "a"})
    assert rig.adapter.slot_of_head(0).current is rig.adapter.slot_of_head(0).lanes[0]


def test_an_eject_with_no_lane_known_in_the_head_probes_and_hands_it_the_head_for_the_u1() -> None:
    # Seen on the unit: after a restart head 1 named no lane; a:4, in the head, was ejected as
    # parked (the unit's plain unload against the gear: stuck). With no lane known the head's
    # sensor may be the lane's: the probe, and a held one is handed the head before the U1 unloads
    rig, a, a2 = parked_eject_rig(False)
    slot = rig.adapter.slot_of_head(0)
    slot.current = None
    sent = []
    rig.gcode.run_script = sent.append
    rig.adapter.material_refusal = lambda _h: None  # its material set
    job = rig.adapter.ejector.start_eject(1, "a")
    rig.reactor.run_timers()
    assert "ace2k_u1: lane a:2 eject: parked outside its head — unloading" not in rig.gcode.lines
    assert [s[1] for s in a.feed.started][:1] == ["rollback"]  # the probe, not the unload
    rig.adapter.ejector._eject_u1(job)  # held: the U1's unload of the head
    assert slot.current is a2 and rig.units_obj.handed == [("a", 1)]
    assert len(sent) == 1 and "UNLOAD=1" in sent[0]


def push_rig() -> tuple[SimpleNamespace, FakeAce2k, Hooked]:
    """Head 1 empty, a:2 its current lane."""
    rig, by_name = chosen_rig(1)
    a = by_name["a"]
    slot = rig.adapter.slot_of_head(0)
    a2 = slot.lanes[1]
    a.lanes[1].update(insert=True, mode="idle")
    return rig, a, a2


def test_a_push_refused_before_any_move_says_why() -> None:
    rig, a, a2 = push_rig()
    a.feed.refuse = "no_filament"
    assert rig.adapter.push_runner.push_to_head(a2) == "no_filament"


def test_a_push_whose_load_wait_times_out_starts_nothing() -> None:
    rig, a, a2 = push_rig()
    a.lanes[1]["mode"] = "loading"  # the unit's own load, never ending
    assert rig.adapter.push_runner.push_to_head(a2) == "timeout"
    assert a.feed.started == []


def test_a_lane_extruder_key_against_its_group_head_found_at_the_hook_stops_klipper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the U1's extruders not known at klippy:connect: the hook checks, shuts Klipper down and
    # hooks nothing
    rig, _ = shared_head_rig()
    module = rig.module
    module.lane_extruder_keys = [(0, "a:1", "ace2k a", "lane1_extruder", "extruder1")]
    module.extruders_checked = False
    shutdowns = []
    monkeypatch.setattr(
        Printer, "invoke_shutdown", lambda self, msg: shutdowns.append(msg), raising=False
    )
    module._hook(rig.reactor.now)
    msg = "ace2k_u1: lanes: a:1 is in head 1's group but [ace2k a] lane1_extruder is extruder1"
    assert shutdowns == [msg]
    assert module.problems == [msg]


def test_unload_temps_take_one_temperature_per_material_whatever_its_case() -> None:
    assert keys({"unload_temps": "PETG:240, abs:250"}).unload_temps == {
        "PETG": 240.0,
        "ABS": 250.0,
    }
    assert keys({"unload_temps": "pla:150,PA6-CF:350,"}).unload_temps == {
        "PLA": 150.0,
        "PA6-CF": 350.0,
    }


@pytest.mark.parametrize(
    "value", ["PLA", "PLA:", ":220", "PLA:hot", "PLA:149", "PLA:351", "PLA:220, PETG"]
)
def test_an_unparsable_unload_temps_entry_stops_klipper(value: str) -> None:
    with pytest.raises(Exception, match="unload_temps: .* is not <material>:<°C> \\(150-350\\)"):
        keys({"unload_temps": value})


@pytest.mark.parametrize("value", ["PLA:200, pla:220", "PETG:240, ABS:250, petg:240"])
def test_a_material_twice_in_unload_temps_stops_klipper(value: str) -> None:
    with pytest.raises(Exception, match="unload_temps: .* is listed twice"):
        keys({"unload_temps": value})


def _boot_follows(by_name: dict[str, FakeAce2k], lanes: Iterable[int]) -> None:
    """ace2k's follow_default has armed these lanes (0-based, unit a) before the hook's groups."""
    a = by_name["a"]
    for lane in lanes:
        a.feed.default_moves[lane] = fakes.FakeMove(lane, "assist_both", 10 + lane)
        a.lanes[lane]["mode"] = "following"


def test_the_watch_stops_the_follow_of_a_shared_head_with_no_lane_known() -> None:
    rig, by_name = shared_head_rig()
    a = by_name["a"]
    _boot_follows(by_name, (0, 1))
    rig.gcode.lines.clear()
    rig.reactor.run_timers(rig.reactor.now + 3.0)
    assert sorted(set(a.feed.stopped) & {0, 1}) == [0, 1]
    slot = rig.adapter.slot_of_head(0)
    assert not any(h.armed or h.follow_wanted for h in slot.lanes)
    lines = [m for m in rig.gcode.lines if "no lane is known" in m]
    assert lines == [
        "ace2k_u1: head 1: no lane is known in it: its lanes' follow is stopped until one is"
        " chosen (a print start probes it, or ACEK_LANE_ACTIVE)"
    ]
    stops = len(a.feed.stopped)
    rig.reactor.run_timers(rig.reactor.now + 3.0)  # still reporting it: stopped once, said once
    assert len(a.feed.stopped) == stops
    assert len([m for m in rig.gcode.lines if "no lane is known" in m]) == 1


def test_the_watch_leaves_a_head_with_a_known_lane_and_a_held_lane_alone() -> None:
    rig, by_name = chosen_rig(1)
    _boot_follows(by_name, (0, 1))
    rig.gcode.lines.clear()
    rig.reactor.run_timers(rig.reactor.now + 3.0)
    assert not any(h.unchosen_stopped for h in rig.adapter.slot_of_head(0).lanes)
    assert not [m for m in rig.gcode.lines if "no lane is known" in m]
    rig2, by_name2 = shared_head_rig()
    _boot_follows(by_name2, (0,))
    rig2.adapter.slot_of_head(0).lanes[0].hold = True
    rig2.reactor.run_timers(rig2.reactor.now + 3.0)
    assert not rig2.adapter.slot_of_head(0).lanes[0].unchosen_stopped


def test_the_watch_spares_the_lane_the_coordinator_has_just_chosen() -> None:
    rig, by_name = shared_head_rig()
    _boot_follows(by_name, (1,))
    rig.units_obj.active["extruder"] = ("a", 1)  # chosen, slot.current not yet re-synced
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is None
    rig.reactor.run_timers(rig.reactor.now + 3.0)
    assert not slot.lanes[1].unchosen_stopped


# --- a filament put in a shared head by hand ----------------------------------------------------


def hand_rig() -> tuple[SimpleNamespace, FakeAce2k]:
    """head 0 fed by a:1+a:2, a:1 in it (the coordinator's choice), both bays full, the head's
    sensor clear; each tick of hand_tick a fresh watch pass."""
    rig, by_name = chosen_rig(0)
    a = by_name["a"]
    for lane in (0, 1):
        a.lanes[lane]["insert"] = True
    rig.sensor = rig.left.runout_sensor[1]  # head 1 (index 0): the left feed's channel 1
    rig.sensor.detected = False
    rig.t = 100.0
    return rig, a


def hand_tick(rig: SimpleNamespace, seconds: float = 0.2) -> None:
    rig.t += seconds
    rig.adapter.watch(rig.t)


def said_hand(rig: SimpleNamespace) -> list[str]:
    return [ln for ln in rig.gcode.lines if "came into the head with no lane of it moving" in ln]


def test_a_filament_put_in_a_shared_head_by_hand_forgets_its_lane_and_the_choice() -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is slot.lanes[0]
    for _ in range(8):  # clear for 1.6 s, nothing moving
        hand_tick(rig)
    rig.sensor.detected = True  # a:2's filament pushed up the tube by hand
    hand_tick(rig)
    assert slot.current is None
    assert rig.units_obj.released == [("extruder", ace2k_u1_common.PAUSE_WAIT_S)]
    assert said_hand(rig) == [
        "ace2k_u1: head 1: a filament came into the head with no lane of it moving (by hand?):"
        " its lane is not known now — a print, an unload or an eject probes it first"
    ]
    hand_tick(rig)  # said once: the head has no lane now, nothing more is watched
    assert len(said_hand(rig)) == 1


def test_a_push_of_the_heads_lane_that_sets_the_sensor_is_its_own() -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    for _ in range(6):
        hand_tick(rig)
    a.lanes[0]["mode"] = "feeding"  # the U1's load pushes a:1
    hand_tick(rig)
    a.lanes[0]["mode"] = "idle"
    rig.sensor.detected = True
    hand_tick(rig)
    assert slot.current is slot.lanes[0] and said_hand(rig) == []


def test_a_short_push_seen_only_by_its_final_event_is_its_own() -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    for _ in range(6):
        hand_tick(rig)
    # a 10 mm nudge started and ended between two ticks: only its final event tells
    a.lanes[0]["last_event"] = {"kind": "done", "mode": "feeding", "seq": 7}
    rig.sensor.detected = True
    hand_tick(rig)
    assert slot.current is slot.lanes[0] and said_hand(rig) == []


def test_a_move_of_another_lane_this_adapter_runs_is_its_own() -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    for _ in range(6):
        hand_tick(rig)
    slot.lanes[1].hold = True  # a probe or a swap runs a:2
    a.lanes[1]["mode"] = "feeding"
    hand_tick(rig)
    rig.sensor.detected = True
    hand_tick(rig)
    assert slot.current is slot.lanes[0] and said_hand(rig) == []


def test_a_move_of_another_lane_typed_at_the_console_is_not_the_heads_lane() -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    for _ in range(6):
        hand_tick(rig)
    a.lanes[1]["mode"] = "feeding"  # ACEK_FEED LANE=2: not this adapter's, not the head's lane
    hand_tick(rig)
    rig.sensor.detected = True
    hand_tick(rig)
    assert slot.current is None and len(said_hand(rig)) == 1


def test_a_clear_shorter_than_its_debounce_is_a_glitch() -> None:
    rig, a = hand_rig()
    rig.sensor.detected = True
    hand_tick(rig)
    rig.sensor.detected = False
    for _ in range(3):  # 0.6 s
        hand_tick(rig)
    rig.sensor.detected = True
    hand_tick(rig)
    assert rig.adapter.slot_of_head(0).current is not None and said_hand(rig) == []


def test_in_a_print_the_hand_watch_decides_nothing() -> None:
    rig, a = hand_rig()
    rig.adapter.print_stats = types.SimpleNamespace(state="paused")
    for _ in range(8):
        hand_tick(rig)
    rig.sensor.detected = True
    hand_tick(rig)
    assert rig.adapter.slot_of_head(0).current is not None and said_hand(rig) == []


def test_without_a_release_on_the_coordinator_the_lane_is_still_forgotten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(type(fakes.two_units()[0]), "release")  # an older ace2k (interface 12)
    rig, a = hand_rig()
    for _ in range(8):
        hand_tick(rig)
    rig.sensor.detected = True
    hand_tick(rig)
    assert rig.adapter.slot_of_head(0).current is None and len(said_hand(rig)) == 1


def test_the_unload_that_empties_the_head_does_not_explain_a_later_filament() -> None:
    """Review: the roll back running when the clear began, and its end, are no push."""
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    a.lanes[0]["mode"] = "rolling_back"  # a:1 unloaded: the head empties as it goes
    for _ in range(3):
        hand_tick(rig)
    a.lanes[0]["mode"] = "idle"
    a.lanes[0]["last_event"] = {"kind": "done", "mode": "rolling_back", "seq": 9}
    for _ in range(6):
        hand_tick(rig)
    rig.sensor.detected = True  # a:2 pushed in by hand
    hand_tick(rig)
    assert slot.current is None and len(said_hand(rig)) == 1


def test_a_standing_follow_of_the_heads_lane_does_not_explain_a_filament() -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    a.lanes[0]["mode"] = "following"
    for _ in range(8):
        hand_tick(rig)
    rig.sensor.detected = True
    hand_tick(rig)
    assert slot.current is None and len(said_hand(rig)) == 1


def test_a_lane_status_not_read_while_clear_decides_nothing() -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    for _ in range(6):
        hand_tick(rig)
    views = rig.adapter.read_units
    rig.adapter.read_units = lambda eventtime: {}  # no unit read this tick
    hand_tick(rig)
    rig.adapter.read_units = views
    rig.sensor.detected = True
    hand_tick(rig)
    assert slot.current is slot.lanes[0] and said_hand(rig) == []


def test_a_head_sensor_that_cannot_be_read_is_no_rise(monkeypatch: pytest.MonkeyPatch) -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)
    for _ in range(8):
        hand_tick(rig)

    def unreadable(h: Hooked) -> NoReturn:
        raise RuntimeError("no sensor")

    monkeypatch.setattr(rig.adapter, "head_set", unreadable)
    hand_tick(rig)
    assert slot.current is slot.lanes[0] and said_hand(rig) == []


def test_a_release_refused_forgets_nothing_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    rig, a = hand_rig()
    slot = rig.adapter.slot_of_head(0)

    def refused(extruder: str, wait_s: float) -> NoReturn:
        raise RuntimeError("ace2k: extruder not released: ACEK_LANE_ACTIVE is arming lane a:2")

    monkeypatch.setattr(rig.units_obj, "release", refused)
    for _ in range(8):
        hand_tick(rig)
    rig.sensor.detected = True
    hand_tick(rig)
    assert slot.current is slot.lanes[0]
    assert not any("its lane is not known now" in ln for ln in rig.gcode.lines)
    assert [ln for ln in said_hand(rig) if "could not be released" in ln] == [
        "ace2k_u1: head 1: a filament came into the head with no lane of it moving (by hand?), but"
        " its active lane could not be released — ace2k: extruder not released: ACEK_LANE_ACTIVE"
        " is arming lane a:2; check the head, then ACEK_LANE_ACTIVE"
    ]


# --- the U1's unload of a shared head with no lane known ----------------------------------------


def unload_rig(
    monkeypatch: pytest.MonkeyPatch, cold: bool = True, found: int | str = 1
) -> tuple[SimpleNamespace, Slot]:
    """head 0 fed by a:1+a:2, no lane known, its sensor set, both bays full; the probe stubbed:
    the head cold or not, and the lane (index) it finds — or a ProbeError text."""
    rig, by_name = shared_head_rig()
    a = by_name["a"]
    for lane in (0, 1):
        a.lanes[lane]["insert"] = True
    rig.left.runout_sensor[1].detected = True
    # loaded by the U1: its unload is the U1's own (a preloaded lane's is cold, see
    # test_screen_unload)
    rig.left.channel_state[1] = "load_finish"
    probe = rig.module.probe
    slot = rig.adapter.slot_of_head(0)
    rig.probed = []
    # the screen's preheat has set a target already (no wait): the head's temperature decides
    temp = 30.0 if cold else 180.0
    monkeypatch.setattr(rig.adapter, "head_heater", lambda head: (temp, 130.0))

    def which_lane(s: Slot) -> Hooked:
        rig.probed.append(s)
        if isinstance(found, str):
            raise ace2k_u1_probe.ProbeError(found)
        return s.lanes[found]

    monkeypatch.setattr(probe, "which_lane", which_lane)
    rig.left.calls.clear()
    return rig, slot


def unloads_passed(rig: SimpleNamespace) -> list[tuple[object, ...]]:
    return [c for c in rig.left.calls if c[0] == "do_feed" and c[2] == "unload"]


def test_the_u1s_unload_with_no_lane_known_probes_hands_the_head_over_and_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == [slot]
    assert rig.units_obj.handed == [("a", 1)]
    assert slot.current is slot.lanes[1]
    assert unloads_passed(rig) == [("do_feed", 1, "unload", "prepare", None)]
    assert "ace2k_u1: head 1: no lane known in it: probing before the unload" in rig.gcode.lines


def refused(rig: SimpleNamespace, why: str) -> str:
    return (
        f"ace2k_u1: head 1: unload refused — no lane known in it: {why}. Find the head's lane"
        " (ACEK_HEAD_PROBE, the head cold), or eject the lane from the /ace2k page"
    )


def test_the_u1s_unload_of_a_hot_head_with_no_lane_known_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch, cold=False)
    with pytest.raises(RuntimeError, match="unload refused"):
        rig.left._do_feed(1, "unload", "prepare")
    why = (
        "the head is hot: let it cool below 60 °C and unload it again, alone (the screen preheats"
        " the next head of a batch)"
    )
    assert refused(rig, why) in rig.gcode.lines
    assert rig.left.channel_state[1] == "unload_fail"
    assert rig.probed == [] and unloads_passed(rig) == []
    assert rig.left.channel_error[1] == "general" and rig.left.exception_code[1] == 70
    assert "M104 S0 T0 A0" in rig.gcode.lines  # the screen's preheat off
    assert slot.current is None


def test_the_u1s_unload_whose_probe_fails_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    rig, slot = unload_rig(monkeypatch, found="head 1: no lane holds its filament")
    with pytest.raises(RuntimeError, match="unload refused"):
        rig.left._do_feed(1, "unload", "prepare")
    assert refused(rig, "head 1: no lane holds its filament") in rig.gcode.lines
    assert unloads_passed(rig) == [] and slot.current is None


def test_the_u1s_unload_while_a_swap_is_pending_on_its_head_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.adapter.swap_slot = lambda: slot
    with pytest.raises(RuntimeError, match="a swap is pending on it"):
        rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == [] and unloads_passed(rig) == []


def test_a_swap_pending_on_another_head_does_not_refuse_the_unload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.adapter.swap_slot = lambda: rig.adapter.slot_of_head(3)
    rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == [slot] and len(unloads_passed(rig)) == 1


def test_the_u1s_unload_of_an_empty_head_or_its_cancel_goes_on_unprobed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.left._do_feed(1, "unload", "cancel")
    rig.left.runout_sensor[1].detected = False
    rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == []
    assert [c[3] for c in unloads_passed(rig)] == ["cancel", "prepare"]


def test_the_u1s_unload_while_printing_with_no_lane_known_goes_on_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.adapter.print_stats = types.SimpleNamespace(state="printing")
    rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == [] and len(unloads_passed(rig)) == 1


def test_the_u1s_unload_in_a_paused_print_is_probed_as_outside_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.adapter.print_stats = types.SimpleNamespace(state="paused")
    rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == [slot] and slot.current is slot.lanes[1]


def test_a_later_stage_of_the_u1s_unload_is_never_probed_and_left_to_the_u1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.left._do_feed(1, "unload", "doing")  # with no prepare before it the U1 ignores it
    assert rig.probed == [] and len(unloads_passed(rig)) == 1


def test_the_unload_turns_the_preheat_off_before_it_reads_the_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    order = []
    monkeypatch.setattr(
        rig.adapter, "head_heater", lambda head: (order.append("read"), (30.0, 0.0))[1]
    )
    lines = rig.gcode.lines
    real = rig.gcode.run_script_from_command
    rig.gcode.run_script_from_command = lambda script: (order.append(script), real(script))
    rig.left._do_feed(1, "unload", "prepare")
    assert order[:2] == ["M104 S0 T0 A0", "read"]
    assert "M104 S0 T0 A0" in lines


def test_a_refused_unload_in_a_paused_print_leaves_the_heat_on_and_points_to_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch, cold=False)
    rig.adapter.print_stats = types.SimpleNamespace(state="paused")
    with pytest.raises(RuntimeError, match="the print is paused with the head hot: RESUME"):
        rig.left._do_feed(1, "unload", "prepare")
    assert not any(ln.startswith("M104") for ln in rig.gcode.lines)  # the print's heat


def test_the_unload_of_an_empty_head_leaves_its_preheat_on(monkeypatch: pytest.MonkeyPatch) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.left.runout_sensor[1].detected = False
    rig.left._do_feed(1, "unload", "prepare")
    assert not any(ln.startswith("M104") for ln in rig.gcode.lines)


def test_the_probe_before_the_unload_holds_the_u1s_channel_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    probe = rig.module.probe
    seen = []
    which = probe.which_lane
    monkeypatch.setattr(
        probe, "which_lane", lambda s: (seen.append(rig.left.channel_active), which(s))[1]
    )
    rig.left._do_feed(1, "unload", "prepare")
    assert seen == [1] and rig.left.channel_active is None


@pytest.mark.parametrize(
    "flow, stage, fail_state", [("unload", "prepare", "unload_fail"), ("load", None, "load_fail")]
)
def test_the_probe_before_a_u1_flow_gives_up_when_the_u1_keeps_its_channel_busy(
    monkeypatch: pytest.MonkeyPatch, flow: str, stage: str | None, fail_state: str
) -> None:
    """The U1's lock held past CHANNEL_WAIT_S: nothing probed, one console line, the U1's
    flow failed as a refused one is (its screen shows the failure)."""
    rig, slot = unload_rig(monkeypatch)
    rig.left.channel_active = 0  # the other channel holds the U1's lock and never lets it go
    rig.gcode.lines.clear()
    start = rig.adapter.reactor.monotonic()
    with pytest.raises(RuntimeError):
        rig.left._do_feed(1, flow, stage)
    assert 300.0 <= rig.adapter.reactor.monotonic() - start < 301.0
    assert [ln for ln in rig.gcode.lines if "ace2k_u1: head 1" in ln] == [
        f"ace2k_u1: head 1: no lane known in it: probing before the {flow}",
        f"ace2k_u1: head 1: the U1 kept its channel busy for 300 s; the {flow} given up",
    ]
    assert rig.probed == [] and slot.current is None and rig.left.channel_active == 0
    assert rig.left.channel_state[1] == fail_state and rig.left.channel_error[1] == "general"


@pytest.mark.parametrize("flow, stage", [("unload", "prepare"), ("load", None)])
def test_the_probe_before_a_u1_flow_waits_for_a_channel_released_in_time(
    monkeypatch: pytest.MonkeyPatch, flow: str, stage: str | None
) -> None:
    """The U1's lock freed before CHANNEL_WAIT_S: the probe runs under it, the head goes to
    the lane found and the U1's flow goes on, no give-up line."""
    rig, slot = unload_rig(monkeypatch)
    rig.left.channel_active = 0  # the other channel's flow holds the lock for 10 s
    reactor = rig.adapter.reactor
    start = reactor.monotonic()
    held = []
    which = rig.module.probe.which_lane

    def which_lane(s: Slot) -> Hooked:
        held.append(rig.left.channel_active)
        return which(s)

    monkeypatch.setattr(rig.module.probe, "which_lane", which_lane)
    reactor.on_pause = [
        lambda now: setattr(rig.left, "channel_active", None) if now - start >= 10.0 else None
    ]
    rig.left._do_feed(1, flow, stage)
    assert 10.0 <= reactor.monotonic() - start < ace2k_u1_push.CHANNEL_WAIT_S
    assert held == [1] and rig.probed and slot.current is slot.lanes[1]
    assert not any("kept its channel busy" in ln for ln in rig.gcode.lines)
    assert [c[2] for c in rig.left.calls if c[0] == "do_feed"] == [flow]
    assert rig.left.channel_active is None


def test_an_internal_error_under_the_probe_fails_the_unload_and_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)

    def broken(s: Slot) -> NoReturn:
        raise KeyError("lanes")

    monkeypatch.setattr(rig.module.probe, "which_lane", broken)
    with pytest.raises(KeyError):
        rig.left._do_feed(1, "unload", "prepare")
    assert rig.left.channel_error[1] == "general" and rig.left.exception_code[1] == 70
    assert rig.left.channel_state[1] == "unload_fail" and rig.left.channel_active is None


@pytest.mark.parametrize("what", ["sensor", "heater"])
def test_the_u1s_unload_with_a_sensor_or_heater_unreadable_is_refused(
    monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    rig, slot = unload_rig(monkeypatch)

    def unreadable(*args: object) -> NoReturn:
        raise RuntimeError("no reading")

    if what == "sensor":
        monkeypatch.setattr(rig.adapter, "head_set", unreadable)
        match = "its sensor cannot be read"
    else:
        monkeypatch.setattr(rig.adapter, "head_heater", lambda head: None)
        match = "its temperature cannot be read"
    with pytest.raises(RuntimeError, match=match):
        rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == [] and unloads_passed(rig) == []


def test_the_u1s_unload_while_an_eject_runs_on_its_lanes_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    monkeypatch.setattr(rig.adapter.ejector, "eject_running", lambda h: h is slot.lanes[1])
    with pytest.raises(RuntimeError, match="an eject is running on one of its lanes"):
        rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == []


def test_the_u1s_unload_whose_hand_over_is_refused_is_refused_with_no_lane_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.units_obj.hand_over_fail = RuntimeError("lane a:1 stopped but did not end within 5 s")
    with pytest.raises(RuntimeError, match="did not end within 5 s"):
        rig.left._do_feed(1, "unload", "prepare")
    assert slot.current is None and unloads_passed(rig) == []


def test_the_u1s_unload_with_a_lane_known_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.adapter.set_current(slot, slot.lanes[0])
    rig.left._do_feed(1, "unload", "prepare")
    assert rig.probed == [] and len(unloads_passed(rig)) == 1


# --- the U1's load of a shared head with no lane known ------------------------------------------


def loads_passed(rig: SimpleNamespace) -> list[tuple[object, ...]]:
    return [c for c in rig.left.calls if c[0] == "do_feed" and c[2] == "load"]


def test_the_u1s_load_with_no_lane_known_probes_hands_the_head_over_and_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.left._do_feed(1, "load")
    assert rig.probed == [slot] and rig.units_obj.handed == [("a", 1)]
    assert slot.current is slot.lanes[1]
    assert loads_passed(rig) == [("do_feed", 1, "load", None, None)]
    assert "ace2k_u1: head 1: no lane known in it: probing before the load" in rig.gcode.lines


def test_the_u1s_load_of_a_hot_head_with_no_lane_known_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch, cold=False)
    with pytest.raises(RuntimeError, match="load refused"):
        rig.left._do_feed(1, "load")
    line = (
        "ace2k_u1: head 1: load refused — no lane known in it: the head is hot: let it cool below"
        " 60 °C and load it again. Find the head's lane (ACEK_HEAD_PROBE, the head cold)"
    )
    assert line in rig.gcode.lines
    assert rig.left.channel_error[1] == "general" and rig.left.exception_code[1] == 30
    assert rig.left.channel_state[1] == "load_fail"
    assert rig.probed == [] and loads_passed(rig) == [] and slot.current is None


def test_the_u1s_load_of_an_empty_head_with_no_lane_known_goes_to_its_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch)
    rig.left.runout_sensor[1].detected = False
    rig.left._do_feed(1, "load")  # put_into_drive then refuses it (the fake runs no flow)
    assert rig.probed == [] and len(loads_passed(rig)) == 1


def test_the_u1s_load_whose_probe_fails_is_refused_as_a_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch, found="head 1: no lane holds its filament")
    with pytest.raises(RuntimeError, match="load refused — no lane known in it: head 1: no lane"):
        rig.left._do_feed(1, "load")
    assert rig.left.exception_code[1] == 30 and loads_passed(rig) == []


def test_a_refused_load_in_a_paused_print_is_the_pauses_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig, slot = unload_rig(monkeypatch, cold=False)
    rig.adapter.print_stats = types.SimpleNamespace(state="paused")
    with pytest.raises(RuntimeError, match="the print is paused with the head hot"):
        rig.left._do_feed(1, "load")
    reason = rig.module.get_status()["pause_reason"]
    assert reason is not None and reason.startswith("ace2k_u1: head 1: load refused")


def test_a_failing_grip_sync_of_a_parked_lane_is_logged_and_the_watch_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig, by_name = chosen_rig(0)
    adapter = rig.adapter
    a2 = adapter.slot_of_head(0).lanes[1]
    assert adapter.parked(a2)

    def failing(h: object, *args: object, **kwargs: object) -> None:
        if h is a2:
            raise RuntimeError("boom")

    adapter.grip_watch.sync_grip = failing
    with caplog.at_level("ERROR"):
        assert adapter.watch(rig.reactor.now) == rig.reactor.now + adapter.poll_s
    mine = [r for r in caplog.records if r.getMessage() == "ace2k_u1: lane a:2 grip"]
    assert len(mine) == 1 and mine[0].exc_info


def test_a_failing_hand_watch_is_logged_per_group_head_and_the_watch_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig, _ = chosen_rig(0)
    adapter = rig.adapter
    adapter.head_watch.watch_hand = lambda slot, views, eventtime: 1 / 0
    with caplog.at_level("ERROR"):
        assert adapter.watch(rig.reactor.now) == rig.reactor.now + adapter.poll_s
    mine = [r for r in caplog.records if r.getMessage() == "ace2k_u1: head 1 hand watch"]
    assert len(mine) == 1 and mine[0].exc_info
