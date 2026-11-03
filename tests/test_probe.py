"""The probe of a shared head whose sensor is set: on a cold head, each lane in turn rolls back
until its plunger reads taut, the head's sensor goes empty, or the whole probe length goes by."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1_probe
import fakes
import pytest
from fakes import NOT_PASSED
from test_tools import run, tools_rig

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked, Slot
    from ace2k_u1_push import PushResult
    from fakes import FakeAce2k, FakeMove


def probe_rig(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:
    rig, by_name = tools_rig(tmp_path)  # head 1 = a:1+a:2
    rig.head_sensor = rig.left.runout_sensor[1]
    rig.head_sensor.detected = True
    a = by_name["a"]
    a.link_proven = True
    a.feed.on_start = lambda move: None  # moves stay open until a test ends them
    return rig, a


def rollbacks(a: FakeAce2k) -> list[tuple[int, float]]:
    return [(lane, length) for lane, mode, length, _s, _m in a.feed.started if mode == "rollback"]


def modes(a: FakeAce2k) -> list[tuple[int, str]]:
    return [(lane, mode) for lane, mode, _l, _s, _m in a.feed.started]


def end(move: FakeMove, mm: float, kind: str = "done") -> None:
    move.event = {"kind": kind, "seq": move.seq, "filament_mm": mm}


def free_lane(lane: int, length: float = 100.0) -> Callable[[FakeMove], None]:
    """An on_start in which lane rolls back the whole length freely and is fed back."""

    def on_start(move: FakeMove) -> None:
        if move.lane == lane:
            end(move, -length if move.mode == "rollback" else length)

    return on_start


def reports_tick(rig: SimpleNamespace, a: FakeAce2k) -> None:
    """Each pause brings the unit's next sensors report."""
    rig.reactor.on_pause.append(lambda now: setattr(a, "reports", a.reports + 1))


def test_a_taut_plunger_names_the_lane(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    free = free_lane(0)

    def on_start(move: FakeMove) -> None:
        free(move)
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True  # the gear holds a:2: its plunger goes taut

    a.feed.on_start = on_start
    reports_tick(rig, a)
    found = rig.module.probe.which_lane(slot)
    assert found is slot.lanes[1]
    assert rollbacks(a) == [(0, 100.0), (1, 100.0)]  # a:1 first: free, pushed back
    assert modes(a) == [(0, "rollback"), (0, "feed"), (1, "rollback")]  # a:2 not pushed back
    assert all(speed == 50.0 for _l, _m, _len, speed, _mv in a.feed.started)
    assert a.feed.log[-1] == ("stop", 1)  # stopped on the taut reading
    assert "ace2k_u1: head 1: probe: a:2 is in the head" in rig.gcode.lines


def test_a_lane_just_put_in_its_bay_is_not_rolled_back(tmp_path: Path) -> None:
    """The probe's 100 mm roll back took lanes just put in their
    bays, loaded to their grip only, out of their units. Such a lane is in no head: skipped."""
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    slot.lanes[0].at_grip = True

    def on_start(move: FakeMove) -> None:
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True

    a.feed.on_start = on_start
    reports_tick(rig, a)
    assert rig.module.probe.which_lane(slot) is slot.lanes[1]
    assert [lane for lane, *_ in a.feed.started] == [1]  # a:1 never moved
    assert "ace2k_u1: head 1: probe skips a:1: just put in its bay, at its grip" in rig.gcode.lines


def test_the_gripped_load_marks_the_lane_and_any_later_end_clears_it(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[0]
    h.grip_live = h.grip_down = True
    rig.adapter.on_feed_event(0, {"kind": "loaded", "seq": 0, "mode": "loading", "unit": "a"})
    assert h.at_grip
    # the probe's own stop of a lane that did not move leaves the mark
    rig.adapter.on_feed_event(0, {"kind": "stopped", "seq": 6, "mode": "following", "unit": "a"})
    assert h.at_grip
    rig.adapter.on_feed_event(
        0, {"kind": "done", "seq": 7, "mode": "feeding", "unit": "a", "filament_mm": 120.0}
    )
    assert not h.at_grip


def test_the_grip_mark_goes_with_a_late_end_or_a_motor_only_travel(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[0]
    for late in (True, False):
        h.at_grip = True
        if late:
            h.old_seqs = [9]  # an earlier motion's end, come late
        event = {"kind": "stopped", "seq": 9, "mode": "feeding", "unit": "a"}
        event.update({"filament_mm": 0.0, "motor_mm": 800.0})  # the encoder read nothing
        rig.adapter.on_feed_event(0, event)
        assert not h.at_grip


def test_the_grip_mark_goes_with_an_assist_whose_bursts_moved_the_filament(tmp_path: Path) -> None:
    """An assist's link loss ends a mode of two bursts: its final event carries both, 33.3 mm,
    though the burst the loss cut had moved 0.6 mm only."""
    rig, a = probe_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[0]
    h.at_grip = True
    event = {"kind": "stopped_link", "seq": 8, "mode": "assisting", "unit": "a"}
    event.update({"filament_mm": 33.3, "motor_mm": 8.7})
    rig.adapter.on_feed_event(0, event)
    assert not h.at_grip


def test_a_gripped_load_that_met_the_old_end_is_not_marked(tmp_path: Path) -> None:
    """blocked: the grip met the old filament's end at the gear, which may still reach a head."""
    rig, a = probe_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[0]
    h.grip_live = h.grip_down = True
    rig.adapter.on_feed_event(0, {"kind": "blocked", "seq": 0, "mode": "loading", "unit": "a"})
    assert not h.at_grip


def test_the_lane_found_taut_is_fed_back_what_it_rolled_back(tmp_path: Path) -> None:
    """The taut lane stopped under tension; fed back its roll back's distance (its final
    event's, after the stop) at probe_speed, its plunger is back at rest before the unload."""
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    free = free_lane(0)
    opened = {}

    def on_start(move: FakeMove) -> None:
        free(move)
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True
            opened[1] = move
        elif move.lane == 1 and move.mode == "feed":
            end(move, 37.0)

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(lane: int) -> None:
        stop(lane)
        move = opened.pop(lane, None)
        if move is not None:  # the unit's final event of the stopped roll back
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -37.0}

    a.feed.stop = stopped
    reports_tick(rig, a)
    assert rig.module.probe.which_lane(slot) is slot.lanes[1]
    assert modes(a) == [(0, "rollback"), (0, "feed"), (1, "rollback"), (1, "feed")]
    lane, mode, length, speed, _m = a.feed.started[-1]
    assert (lane, mode, length, speed) == (1, "feed", 37.0, 50.0)


def test_a_single_taut_report_is_not_enough(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    moves = []

    def tick(now: float) -> None:
        a.reports += 1
        a.lanes[0]["pushed"] = a.reports == 5  # a:1's plunger reads taut on one report only
        if moves and moves[0].event is None and a.reports > 40:
            end(moves[0], -100.0)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "rollback":
            moves.append(move)  # ended by tick, after the one taut report
        else:
            end(move, -100.0 if move.mode == "rollback" else 100.0)

    rig.reactor.on_pause.append(tick)
    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="no lane of the head holds"):
        rig.module.probe.which_lane(slot)
    assert modes(a) == [(0, "rollback"), (0, "feed"), (1, "rollback"), (1, "feed")]


def test_the_sensor_going_empty_names_the_lane_and_it_goes_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "rollback":
            rig.head_sensor.detected = False  # a:1 was at the sensor, not in the gear
        if move.lane == 0 and move.mode == "feed":
            rig.head_sensor.detected = True
        if move.lane == 1:  # a:2 is free: every lane is tried
            end(move, -100.0 if move.mode == "rollback" else 100.0)

    a.feed.on_start = on_start
    assert rig.module.probe.which_lane(slot) is slot.lanes[0]
    assert modes(a) == [(0, "rollback"), (0, "feed"), (1, "rollback"), (1, "feed")]
    assert a.feed.started[1][2] == 100.0  # the push back's budget: probe_mm
    log = a.feed.log
    assert log[log.index(("start", 0, "feed", 100.0)) + 1] == ("stop", 0)  # at the sensor set
    assert "ace2k_u1: head 1: probe: a:1 is in the head" in rig.gcode.lines


def test_two_lanes_found_fail_naming_them(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            a.lanes[move.lane]["pushed"] = True  # each plunger goes taut: both read held
        for lane in (0, 1):
            if lane != move.lane:
                a.lanes[lane]["pushed"] = False

    a.feed.on_start = on_start
    reports_tick(rig, a)
    with pytest.raises(
        ace2k_u1_probe.ProbeError, match="head 1: more than one lane holds its filament: a:1, a:2"
    ):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert modes(a) == [(0, "rollback"), (1, "rollback")]  # both tried, neither pushed back
    assert not any("is in the head" in line for line in rig.gcode.lines)


def test_a_push_back_that_does_not_reach_the_sensor_fails(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            rig.head_sensor.detected = False
        else:
            end(move, 100.0)  # the whole budget, the sensor still empty

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="sensor"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))


def test_a_free_lane_goes_back_what_it_rolled_back_and_the_next_is_tried(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0:
            end(move, -100.0 if move.mode == "rollback" else 100.0)
        else:  # a:2 sat at the sensor: empty on its roll back, set again on its push back
            rig.head_sensor.detected = move.mode == "feed"

    a.feed.on_start = on_start
    found = rig.module.probe.which_lane(slot)
    assert found is slot.lanes[1]
    started = [(lane, mode, length) for lane, mode, length, _s, _m in a.feed.started]
    assert started[:3] == [(0, "rollback", 100.0), (0, "feed", 100.0), (1, "rollback", 100.0)]


def test_a_free_roll_back_that_ran_on_with_its_tail_goes_back_its_whole_distance(
    tmp_path: Path,
) -> None:
    """a:1's roll back went on with a tail move and its final event carries 130 mm, more than
    probe_mm: it is fed back those 130 mm, so its tip is where it was."""
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0:
            end(move, -130.0 if move.mode == "rollback" else 130.0)
        else:  # a:2 sat at the sensor: empty on its roll back, set again on its push back
            rig.head_sensor.detected = move.mode == "feed"

    a.feed.on_start = on_start
    assert rig.module.probe.which_lane(slot) is slot.lanes[1]
    started = [(lane, mode, length) for lane, mode, length, _s, _m in a.feed.started]
    assert started[:2] == [(0, "rollback", 100.0), (0, "feed", 130.0)]


def test_no_lane_holding_the_filament_fails(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        end(move, -100.0 if move.mode == "rollback" else 100.0)

    a.feed.on_start = on_start
    with pytest.raises(Exception, match="head 1: no lane of the head holds its filament"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert modes(a) == [(0, "rollback"), (0, "feed"), (1, "rollback"), (1, "feed")]


def test_a_link_not_proven_fails_before_the_next_lane_moves(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        end(move, -100.0 if move.mode == "rollback" else 100.0)
        if move.mode == "feed":
            a.link_proven = False  # lost after a:1 went back

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="link"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert modes(a) == [(0, "rollback"), (0, "feed")]  # a:2 never moved


def test_a_link_lost_during_a_roll_back_stops_the_lane_and_fails(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        rig.reactor.on_pause.append(lambda now: setattr(a, "link_proven", False))

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="link"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert modes(a) == [(0, "rollback")]
    assert a.feed.log[-1] == ("stop", 0)


def test_a_roll_back_with_an_unexpected_end_fails(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    a.feed.on_start = lambda move: end(move, -30.0, kind="stuck")
    with pytest.raises(ace2k_u1_probe.ProbeError, match="ended stuck"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert modes(a) == [(0, "rollback")]


def test_the_follow_is_stopped_first_and_the_hold_given_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    lanes = {h.lane: h for h in slot.lanes}
    lanes[1].hold = True  # another step's hold outlives the probe
    held = []

    def on_start(move: FakeMove) -> None:
        held.append((move.lane, move.mode, lanes[move.lane].hold))
        end(move, -100.0 if move.mode == "rollback" else 100.0)

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError):
        rig.module.probe.which_lane(slot)
    assert all(hold for _l, _m, hold in held)
    assert lanes[0].hold is False and lanes[1].hold is True
    for lane in (0, 1):
        log = a.feed.log
        first_start = next(i for i, e in enumerate(log) if e[0] == "start" and e[1] == lane)
        assert ("stop", lane) in log[:first_start]


def test_a_cold_head_is_probed_without_a_wait(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    before = rig.reactor.now
    rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))
    assert rig.reactor.now == before
    assert not any(line.startswith("M104") for line in rig.gcode.lines)


def test_a_heating_head_below_the_limit_is_turned_off_first(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.heater.temp, ext.heater.target = 40.0, 220.0  # heating, not yet at the limit
    sent = []
    a.feed.on_start = lambda move: sent.append(list(rig.gcode.lines))
    before = rig.reactor.now
    rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))
    assert "M104 S0 T0 A0" in rig.gcode.lines
    assert rig.reactor.now == before  # already below the limit: no wait
    a.feed.on_start = lambda move: (
        sent.append(list(rig.gcode.lines)),
        end(move, -100.0 if move.mode == "rollback" else 100.0),
    )
    with pytest.raises(ace2k_u1_probe.ProbeError):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert sent and all("M104 S0 T0 A0" in lines for lines in sent)  # off before any move


def test_a_hot_head_is_turned_off_and_waited_for(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.heater.temp, ext.heater.cooling = 90.0, 1.0
    last = [rig.reactor.now]

    def cool(now: float) -> None:
        ext.heater.temp -= ext.heater.cooling * (now - last[0])
        last[0] = now

    rig.reactor.on_pause.append(cool)
    rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))
    assert "M104 S0 T0 A0" in rig.gcode.lines
    assert ext.heater.temp < 60.0
    assert "ace2k_u1: head 1: probe waits for the head below 60 °C, its fan on" in rig.gcode.lines


def test_a_hot_head_waits_with_its_fan_on_and_gets_its_speed_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.heater.temp, ext.heater.cooling = 90.0, 1.0
    ext.binding_fan.speed = 0.3
    seen = []
    last = [rig.reactor.now]

    def cool(now: float) -> None:
        seen.append(ext.binding_fan.speed)
        ext.heater.temp -= ext.heater.cooling * (now - last[0])
        last[0] = now

    rig.reactor.on_pause.append(cool)
    rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))
    assert seen and all(speed == 1.0 for speed in seen)
    assert ext.binding_fan.set == [1.0, 0.3] and ext.binding_fan.speed == 0.3
    assert "ace2k_u1: head 1: probe waits for the head below 60 °C, its fan on" in rig.gcode.lines


def test_the_fan_speed_given_back_is_the_one_asked_whatever_its_max_power(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.binding_fan = fakes.FakeFan(max_power=0.8)
    ext.binding_fan.speed = 0.5
    ext.heater.temp, ext.heater.cooling = 90.0, 1.0
    last = [rig.reactor.now]

    def cool(now: float) -> None:
        ext.heater.temp -= ext.heater.cooling * (now - last[0])
        last[0] = now

    rig.reactor.on_pause.append(cool)
    rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))
    assert ext.binding_fan.set == [1.0, pytest.approx(0.5)]


def test_a_fan_that_cannot_be_given_back_does_not_hide_the_probes_error(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.heater.temp = 90.0  # it never cools
    real = ext.binding_fan.set_speed_from_command

    def first_only(value: float) -> None:
        real(value)
        ext.binding_fan.fail = True

    ext.binding_fan.set_speed_from_command = first_only
    with pytest.raises(ace2k_u1_probe.ProbeError, match="did not cool"):
        rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))


def test_a_cold_head_leaves_its_fan_alone(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))
    assert ext.binding_fan.set == []


def test_a_head_that_does_not_cool_gets_its_fan_speed_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.heater.temp = 90.0  # it never cools
    with pytest.raises(ace2k_u1_probe.ProbeError, match="did not cool"):
        rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))
    assert ext.binding_fan.set == [1.0, 0.0]


def test_a_head_with_no_fan_still_waits(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.binding_fan = None
    ext.heater.temp, ext.heater.cooling = 90.0, 1.0
    last = [rig.reactor.now]

    def cool(now: float) -> None:
        ext.heater.temp -= ext.heater.cooling * (now - last[0])
        last[0] = now

    rig.reactor.on_pause.append(cool)
    rig.module.probe.wait_cold(rig.adapter.slot_of_head(0))
    assert ext.heater.temp < 60.0
    assert "ace2k_u1: head 1: probe waits for the head below 60 °C" in rig.gcode.lines


def test_a_head_that_does_not_cool_fails(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.heater.temp = 90.0  # it never cools
    start = rig.reactor.now
    with pytest.raises(ace2k_u1_probe.ProbeError, match="head 1 did not cool below 60 °C"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert rig.reactor.now - start >= ace2k_u1_probe.PROBE_COOL_WAIT_S
    assert a.feed.started == []  # nothing moved


def test_a_head_whose_temperature_cannot_be_read_is_not_probed(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    del rig.module.printer.objects["extruder"]
    with pytest.raises(ace2k_u1_probe.ProbeError, match="temperature"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert a.feed.started == []


def test_the_probe_keys_are_used(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    cfg = rig.module.swap_cfg
    cfg.probe_mm, cfg.probe_speed = 60.0, 5.0
    a.feed.on_start = lambda move: end(move, -60.0 if move.mode == "rollback" else 60.0)
    with pytest.raises(ace2k_u1_probe.ProbeError):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert {(length, speed) for _l, _m, length, speed, _mv in a.feed.started} == {(60.0, 5.0)}


def test_no_probe_without_a_shared_head(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path, lanes="a:1, a:2, a:3, a:4")
    assert rig.module.probe is None


def test_a_lane_already_tried_is_not_re_armed_while_the_next_one_moves(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    first = slot.lanes[0]
    rig.adapter.set_current(slot, first)  # a:1 is the head's lane: the standing follow watches it
    rig.units_obj.active["extruder"] = ("a", 0)
    first.follow_wanted = True
    a.lanes[0].update(insert=True, mode="idle")
    rig.left.channel_state[first.ch] = "load_finish"  # the U1 holds the head loaded

    def on_start(move: FakeMove) -> None:
        if move.lane == 0:
            end(move, -100.0 if move.mode == "rollback" else 100.0)
        elif move.mode == "rollback":
            rig.adapter.watch(rig.reactor.now)  # a bay-watch tick while a:2 moves
            rig.head_sensor.detected = False
        else:
            rig.head_sensor.detected = True

    a.feed.on_start = on_start
    rig.module.probe.which_lane(slot)
    assert [mode for lane, mode, _l, _s, _m in a.feed.started if lane == 0] == ["rollback", "feed"]


def test_a_head_whose_sensor_sees_no_filament_is_not_probed(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    rig.head_sensor.detected = False
    with pytest.raises(ace2k_u1_probe.ProbeError, match="head 1: its sensor sees no filament"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert a.feed.started == []


def test_an_unreadable_sensor_during_the_push_back_fails_and_stops_the_lane(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)

    def broken(eventtime: float) -> NoReturn:
        raise OSError("sensor gone")

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            rig.head_sensor.detected = False
        else:
            rig.head_sensor.get_status = broken  # unreadable: never taken for "set"

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="sensor cannot be read"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert modes(a) == [(0, "rollback"), (0, "feed")]
    assert a.feed.log[-1] == ("stop", 0)


def test_a_lane_that_rolls_out_of_its_bay_is_not_the_one_and_stays_out(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0:
            end(move, -40.0, kind="runout")
        else:
            rig.head_sensor.detected = move.mode == "feed"

    a.feed.on_start = on_start
    assert rig.module.probe.which_lane(slot) is slot.lanes[1]
    assert modes(a) == [(0, "rollback"), (1, "rollback"), (1, "feed")]  # a:1 not pushed back


def test_a_move_that_never_ends_times_out_stopped(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)  # moves stay open, the sensor stays set, no fresh report
    start = rig.reactor.now
    with pytest.raises(ace2k_u1_probe.ProbeError, match="timed out"):
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert modes(a) == [(0, "rollback")]
    assert a.feed.log[-1] == ("stop", 0)
    assert rig.reactor.now - start >= 100.0 / 50.0 * 2.0


def test_the_free_check_of_a_held_lane_fails_and_feeds_nothing_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "rollback":
            a.lanes[0]["pushed"] = True  # the gear holds a:1: its plunger goes taut

    a.feed.on_start = on_start
    reports_tick(rig, a)
    with pytest.raises(ace2k_u1_probe.ProbeError, match="a:1 is still held by head 1"):
        rig.module.probe.free_check(slot.lanes[0])
    assert modes(a) == [(0, "rollback")]
    assert a.feed.log[-1] == ("stop", 0)


def snagged_lane(rig: SimpleNamespace, a: FakeAce2k, mm: float, empties: bool = True) -> None:
    """a:1's roll back goes taut (its tip caught); stopped, the head's sensor empties when empties
    (the tip springs past it: above the gear) and the final event carries mm."""
    opened = {}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "rollback":
            a.lanes[0]["pushed"] = True
            opened[move.lane] = move

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(lane: int) -> None:
        stop(lane)
        move = opened.pop(lane, None)
        if move is not None:
            end(move, -mm, kind="stopped")
            if empties:
                rig.reactor.on_pause.append(lambda now: setattr(rig.head_sensor, "detected", False))

    a.feed.stop = stopped
    reports_tick(rig, a)


def test_a_taut_free_check_whose_head_sensor_then_empties_is_a_snag_and_free(
    tmp_path: Path,
) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    snagged_lane(rig, a, 28.4)
    assert rig.module.probe.free_check(slot.lanes[0]) == 28.4
    assert modes(a) == [(0, "rollback")]  # nothing fed back: the swap's roll back goes on
    assert (
        "ace2k_u1: head 1: a:1's plunger went taut after 28.4 mm with the head's sensor then"
        " empty: its tip caught above the gear, not held"
    ) in rig.gcode.lines


def test_a_taut_free_check_whose_head_sensor_stays_set_is_held(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    snagged_lane(rig, a, 28.4, empties=False)
    start = rig.reactor.now
    with pytest.raises(ace2k_u1_probe.HeldError, match="a:1 is still held by head 1"):
        rig.module.probe.free_check(slot.lanes[0])
    assert rig.reactor.now - start >= ace2k_u1_probe.SNAG_WAIT_S


def test_a_head_sensor_that_bounces_empty_once_after_a_taut_stop_is_held(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    snagged_lane(rig, a, 28.4, empties=False)
    stop = a.feed.stop

    def stopped(lane: int) -> None:
        stop(lane)
        if not a.feed.started:
            return  # the stop before the move
        reads = iter([False, True])  # one empty read, then set again: a bounce

        def bounce(now: float) -> None:
            rig.head_sensor.detected = next(reads, True)

        rig.reactor.on_pause.append(bounce)

    a.feed.stop = stopped
    with pytest.raises(ace2k_u1_probe.HeldError, match="a:1 is still held by head 1"):
        rig.module.probe.free_check(slot.lanes[0])


def test_the_free_check_ends_when_the_heads_sensor_goes_empty(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    opened = {}

    def on_start(move: FakeMove) -> None:
        rig.head_sensor.detected = False  # the tip leaves the sensor: nothing holds it
        opened[move.lane] = move  # the move stays open: its final event comes with the stop

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(lane: int) -> None:
        stop(lane)
        move = opened.pop(lane, None)
        if move is not None:
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -12.0}

    a.feed.stop = stopped
    assert rig.module.probe.free_check(slot.lanes[0]) == 12.0
    assert modes(a) == [(0, "rollback")]
    _lane, _mode, length, speed, _m = a.feed.started[0]
    assert (length, speed) == (100.0, 50.0)


@pytest.mark.parametrize("kind", ["done", "runout"])
def test_the_free_check_of_a_free_lane_returns_its_distance(tmp_path: Path, kind: str) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a.feed.on_start = lambda move: end(move, -100.0, kind=kind)
    assert rig.module.probe.free_check(slot.lanes[0]) == 100.0


def test_the_free_check_of_a_roll_back_that_ran_on_with_its_tail_returns_the_whole_distance(
    tmp_path: Path,
) -> None:
    """The roll back passed its deadline with the plunger at rest and went on with a tail move.
    Its final event carries the whole roll back, 130 mm: that is what the swap subtracts."""
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a.feed.on_start = lambda move: end(move, -130.0)
    assert rig.module.probe.free_check(slot.lanes[0]) == 130.0


def test_the_free_check_with_another_end_fails(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a.feed.on_start = lambda move: end(move, -3.0, kind="stuck")
    with pytest.raises(ace2k_u1_probe.ProbeError, match="free check ended stuck"):
        rig.module.probe.free_check(slot.lanes[0])


def test_the_free_check_stops_the_follow_first_and_gives_the_hold_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[0]
    h.hold = False
    seen = []
    a.feed.on_start = lambda move: seen.append(h.hold) or end(move, -100.0)
    rig.module.probe.free_check(h)
    assert seen == [True]
    assert h.hold is False
    log = a.feed.log
    first_start = next(i for i, e in enumerate(log) if e[0] == "start")
    assert ("stop", 0) in log[:first_start]


def test_the_free_check_never_touches_the_heater(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[0]
    sent, asked = [], []
    rig.module.probe.run_script = sent.append
    rig.module.probe.a.head_heater = lambda head: asked.append(head) or (220.0, 220.0)
    a.feed.on_start = lambda move: end(move, -100.0)
    rig.module.probe.free_check(h)
    assert sent == [] and asked == []


# --- the push that probes: a head whose sensor is empty ---


@pytest.fixture
def no_insist(monkeypatch: pytest.MonkeyPatch) -> None:
    """The search's tests: a blocked push is a real obstacle, not pushed again first."""
    monkeypatch.setattr(ace2k_u1_probe, "INSIST_TRIES", 0)


def search_rig(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k, Slot]:
    rig, a = probe_rig(tmp_path)
    rig.head_sensor.detected = False
    return rig, a, rig.adapter.slot_of_head(0)


def four_lane_rig(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k, Slot]:
    """Head 4 = b:1+b:2+b:3+b:4, its sensor empty, every move open until a test ends it."""
    rig, by_name = tools_rig(tmp_path)
    rig.head_sensor = rig.right.runout_sensor[1]
    rig.head_sensor.detected = False
    b = by_name["b"]
    b.link_proven = True
    b.feed.on_start = lambda move: None
    return rig, b, rig.adapter.slot_of_head(3)


def moves(api: FakeAce2k) -> list[tuple[int, str, float, float]]:
    return [(lane, mode, length, speed) for lane, mode, length, speed, _m in api.feed.started]


def test_a_push_that_reaches_the_sensor_moves_nothing_else(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        rig.head_sensor.detected = True

    a.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert moves(a) == [(0, "feed", 2000.0, 70.0)]


def test_a_budget_over_the_units_longest_move_is_pushed_in_moves(tmp_path: Path) -> None:
    """A refill's 2500 mm from its bay is over the unit's 2000 mm move: the
    first move ends done short of the sensor, the rest goes on, not counted as a try."""
    rig, a, slot = search_rig(tmp_path)

    feeds = []

    def on_start(move: FakeMove) -> None:
        feeds.append(move)
        if len(feeds) == 1:  # the whole first move, short of the sensor
            move.event = {"kind": "done", "seq": move.seq, "filament_mm": 2000.0}
        else:
            rig.head_sensor.detected = True

    a.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0, budget=2500.0) is None
    assert moves(a)[:2] == [(0, "feed", 2000.0, 70.0), (0, "feed", 500.0, 70.0)]
    assert not any("pushed again" in ln for ln in rig.gcode.lines)


def test_a_slipping_move_of_a_long_budget_is_not_pushed_again(tmp_path: Path) -> None:
    """A move that ends done having carried the filament only a little
    slipped: no further move."""
    rig, a, slot = search_rig(tmp_path)
    a.feed.on_start = lambda move: setattr(
        move, "event", {"kind": "done", "seq": move.seq, "filament_mm": 300.0}
    )
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0, budget=2500.0) is not None
    assert [m[1] for m in a.feed.started].count("feed") == 1


def test_a_budget_of_one_move_ends_done_short_without_another(tmp_path: Path) -> None:
    """A swap's push (head_budget_mm, one move) that ends done short of the
    sensor is the error at once, never a second push."""
    rig, a, slot = search_rig(tmp_path)
    a.feed.on_start = lambda move: setattr(
        move, "event", {"kind": "done", "seq": move.seq, "filament_mm": 1986.0}
    )
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) == "distance"
    assert [m[1] for m in a.feed.started].count("feed") == 1


def test_a_roll_back_over_the_units_longest_move_runs_in_moves(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    a.feed.on_start = lambda move: setattr(move, "event", {"kind": "done", "seq": move.seq})
    rig.module.probe.back(slot.lanes[0], 2300.0, speed=70.0)
    assert rollbacks(a) == [(0, 2000.0), (0, 300.0)]


def test_a_blocked_push_finds_the_lane_in_the_way(tmp_path: Path, no_insist: None) -> None:
    rig, a, slot = search_rig(tmp_path)
    pushes = {"a1": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            pushes["a1"] += 1
            if pushes["a1"] == 1:  # a:2's filament past the splitter blocks it
                move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 480.0}
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            rig.head_sensor.detected = True  # a:2 was the one past the splitter
        elif move.mode == "rollback":
            if move.lane == 1:
                rig.head_sensor.detected = False
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert moves(a) == [
        (0, "feed", 2000.0, 70.0),  # blocked after 480 mm
        (0, "rollback", 480.0, 70.0),  # what it pushed, at swap_speed
        (1, "feed", 2000.0, 50.0),  # a:2 sets the head's sensor: it was in the way
        (1, "rollback", 1000.0, 70.0),  # headN_splitter_mm at swap_speed
        (0, "feed", 2000.0, 70.0),  # pushed again
    ]
    assert pushes["a1"] == 2
    log = a.feed.log
    assert log[log.index(("start", 1, "feed", 2000.0)) + 1] == ("stop", 1)  # at the sensor set
    assert "ace2k_u1: head 1: a:2 was past the splitter: rolled back" in rig.gcode.lines


def test_a_budget_over_one_move_searches_each_lane_with_one_move(
    tmp_path: Path, no_insist: None
) -> None:
    """head_budget_mm 3 000 is pushed in moves; the search pushes
    each other lane one move at most, never over the unit's longest."""
    rig, a, slot = search_rig(tmp_path)
    rig.adapter.head_budget_mm = 3000.0
    pushes = {"a1": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            pushes["a1"] += 1
            if pushes["a1"] == 1:
                move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 480.0}
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            rig.head_sensor.detected = True
        elif move.mode == "rollback":
            if move.lane == 1:
                rig.head_sensor.detected = False
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert (1, "feed", 2000.0, 50.0) in moves(a)
    assert max(length for _l, _m, length, _s in moves(a)) == 2000.0


def test_a_search_push_that_ends_done_was_not_in_the_way(tmp_path: Path, no_insist: None) -> None:
    """A lane whose tip is at its bay, two splitters away, ends its
    one move done short of the sensor: not in the way, it rolls back and the search goes on."""
    rig, a, slot = search_rig(tmp_path)
    pushes = {"a1": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            pushes["a1"] += 1
            if pushes["a1"] == 1:
                move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 480.0}
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            move.event = {"kind": "done", "seq": move.seq, "filament_mm": 1995.0}
        elif move.mode == "rollback":
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="blocked in the tube, not by another lane"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert (1, "rollback", 1995.0, 70.0) in moves(a)  # what it pushed, out of the tube


def test_a_later_move_that_fails_otherwise_rolls_back_what_the_push_took(tmp_path: Path) -> None:
    """A push in moves whose second move is stuck leaves nothing in the tube:
    what both moves took rolls back."""
    rig, a, slot = search_rig(tmp_path)
    rig.adapter.head_budget_mm = 3000.0
    feeds = {"n": 0}

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            feeds["n"] += 1
            kind, mm = ("done", 2000.0) if feeds["n"] == 1 else ("stuck", 300.0)
            move.event = {"kind": kind, "seq": move.seq, "filament_mm": mm}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    error = rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert error == rig.adapter.feed_error("FEED_ERR_WHEEL_SPEED")
    assert rollbacks(a) == [(0, 2000.0), (0, 300.0)]  # 2 300 mm, in moves


def test_a_search_push_done_after_a_retry_rolls_back_once(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    tries = {"a1": 0, "a2": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            tries["a1"] += 1
            move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 480.0}
        elif move.lane == 1 and move.mode == "feed":
            tries["a2"] += 1
            if tries["a2"] == 1:
                move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 400.0}
            else:
                move.event = {"kind": "done", "seq": move.seq, "filament_mm": 1600.0}  # the rest
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    rig.module.probe.ease_to_rest = lambda x: (True, 0.0)
    with pytest.raises(ace2k_u1_probe.ProbeError):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert [r for r in rollbacks(a) if r[0] == 1] == [(1, 2000.0)]  # once: 400 + 1 600


def test_a_search_push_done_that_slipped_is_still_a_probe_error(
    tmp_path: Path, no_insist: None
) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 480.0}
        elif move.lane == 1 and move.mode == "feed":
            move.event = {"kind": "done", "seq": move.seq, "filament_mm": 150.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="a:2's push toward the head ended done"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)


def test_a_later_move_with_no_final_event_rolls_back_as_far_as_it_may_have_gone(
    tmp_path: Path,
) -> None:
    """The second move of a push in moves times out (no final event): what it
    ran is not known, so the roll back covers both moves whole."""
    rig, a, slot = search_rig(tmp_path)
    rig.adapter.head_budget_mm = 3000.0
    feeds = {"n": 0}

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            feeds["n"] += 1
            if feeds["n"] == 1:
                move.event = {"kind": "done", "seq": move.seq, "filament_mm": 2000.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    error = rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert error == rig.adapter.feed_error("FEED_ERR_TIMEOUT")
    assert rollbacks(a) == [(0, 2000.0), (0, 1000.0)]  # 2 000 + the whole second move


def test_a_later_move_that_raises_is_rolled_back_and_raised(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    rig.adapter.head_budget_mm = 3000.0
    feeds = {"n": 0}

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            feeds["n"] += 1
            if feeds["n"] == 1:
                move.event = {"kind": "done", "seq": move.seq, "filament_mm": 2000.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    real = rig.adapter.push_runner._poll_push

    def poll(h: Hooked, move: FakeMove, speed: float, budget: float, fast: float) -> PushResult:
        if feeds["n"] == 2:
            raise OSError("the head's sensor could not be read")
        return real(h, move, speed, budget, fast)

    rig.adapter.push_runner._poll_push = poll
    with pytest.raises(OSError):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert rollbacks(a) == [(0, 2000.0), (0, 1000.0)]


def test_a_later_move_stuck_with_no_distance_leaves_the_tip_unknown(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    rig.adapter.head_budget_mm = 3000.0
    feeds = {"n": 0}

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            feeds["n"] += 1
            if feeds["n"] == 1:
                move.event = {"kind": "done", "seq": move.seq, "filament_mm": 2000.0}
            else:
                move.event = {"kind": "stuck", "seq": move.seq}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    h = slot.lanes[0]
    assert (h.push_mm, h.push_known, h.push_max_mm) == (2000.0, False, 3000.0)
    assert rollbacks(a) == [(0, 2000.0), (0, 1000.0)]


def test_a_short_done_after_a_retry_is_not_taken_as_out_of_the_way(tmp_path: Path) -> None:
    """a:2 blocks at 1 900 mm, then its retry of the rest ends done short:
    under a whole move in all it is no lane far back, and stays the error it was."""
    rig, a, slot = search_rig(tmp_path)
    tries = {"a2": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 480.0}
        elif move.lane == 1 and move.mode == "feed":
            tries["a2"] += 1
            if tries["a2"] == 1:
                move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 1000.0}
            else:
                move.event = {"kind": "done", "seq": move.seq, "filament_mm": 500.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    rig.module.probe.ease_to_rest = lambda x: (True, 0.0)
    with pytest.raises(ace2k_u1_probe.ProbeError, match="a:2's push toward the head ended done"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)


def parked_reach_rig(
    tmp_path: Path, h_again: bool
) -> tuple[SimpleNamespace, FakeAce2k, Slot, dict[str, int]]:
    """a:1's push blocked (its own tip); a:2, parked, reaches the sensor after 1010 mm of 1000's
    splitter_mm; a:1 pushed again sets the sensor when h_again, else blocks again."""
    rig, a, slot = search_rig(tmp_path)
    pushes = {"a1": 0}
    opened = {}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            pushes["a1"] += 1
            if pushes["a1"] == 1 or not h_again:
                move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 1480.0}
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            rig.head_sensor.detected = True
            opened[1] = move
        elif move.mode == "rollback":
            if move.lane == 1:
                rig.head_sensor.detected = False
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(lane: int) -> None:
        stop(lane)
        move = opened.pop(lane, None)
        if move is not None:
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": 1010.0}

    a.feed.stop = stopped
    return rig, a, slot, pushes


def test_a_parked_lane_that_reaches_the_head_at_its_distance_is_not_in_the_way(
    tmp_path: Path, no_insist: None
) -> None:
    """a:1, parked, reached the sensor at 999 of 990 mm and was blamed; the
    block was the pushed lane's cut tip. Now the path is clear and the pushed lane goes again."""
    rig, a, slot, pushes = parked_reach_rig(tmp_path, h_again=True)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert moves(a) == [
        (0, "feed", 2000.0, 70.0),  # blocked after 1480 mm
        (0, "rollback", 1480.0, 70.0),
        (1, "feed", 2000.0, 50.0),  # reaches the sensor at 1010 mm: from behind the splitter
        (1, "rollback", 1010.0, 70.0),  # what it pushed: its tip where it was
        (0, "feed", 2000.0, 70.0),  # pushed again
    ]
    lines = rig.gcode.lines
    assert (
        "ace2k_u1: head 1: a:2 reached the head from behind the splitter (1010 mm): not in the"
        " way, rolled back"
    ) in lines
    assert not any("was past the splitter" in ln for ln in lines)
    assert (
        "ace2k_u1: head 1: no lane in the way, the path to the head is clear: a:1 pushed again"
    ) in lines


def test_a_lane_blocked_again_with_the_path_clear_names_its_tip(
    tmp_path: Path, no_insist: None
) -> None:
    rig, a, slot, _pushes = parked_reach_rig(tmp_path, h_again=False)
    with pytest.raises(
        ace2k_u1_probe.ProbeError,
        match="head 1: the push of a:1 is blocked again with the path to the head clear: check"
        " its tip",
    ):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert rollbacks(a)[-1] == (0, 1480.0)  # never left in the shared tube


def test_a_blocked_push_with_no_distance_rolls_back_the_heads_splitter_mm(
    tmp_path: Path, no_insist: None
) -> None:
    """A blocked event with no finite distance: the pushed lane's tip is at or before
    the splitter junction, so it rolls back the head's splitter_mm, at swap_speed."""
    rig, a, slot = search_rig(tmp_path)
    pushes = {"a1": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            pushes["a1"] += 1
            if pushes["a1"] == 1:
                move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": float("nan")}
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            rig.head_sensor.detected = True
        elif move.mode == "rollback":
            if move.lane == 1:
                rig.head_sensor.detected = False
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert moves(a)[:2] == [
        (0, "feed", 2000.0, 70.0),  # blocked, no distance on its event
        (0, "rollback", 1000.0, 70.0),  # head 1's splitter_mm, at swap_speed
    ]


def test_a_searched_lane_blocked_with_no_distance_rolls_back_the_heads_splitter_mm(
    tmp_path: Path, no_insist: None
) -> None:
    """The search's other lane ending blocked with no distance rolls back the head's
    splitter_mm, as the pushed lane does."""
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            distance = 300.0 if move.lane == 0 else float("nan")
            move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": distance}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="blocked in the tube"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert rollbacks(a) == [(0, 300.0), (1, 1000.0)]


def test_a_blocked_push_with_no_lane_in_the_way_is_the_tube(
    tmp_path: Path, no_insist: None
) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 300.0}
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    with pytest.raises(
        ace2k_u1_probe.ProbeError,
        match="head 1: the push of a:1 is blocked in the tube, not by another lane: check the"
        " tube after the splitter",
    ):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert modes(a) == [(0, "feed"), (0, "rollback"), (1, "feed"), (1, "rollback")]
    assert rollbacks(a) == [(0, 300.0), (1, 300.0)]  # each what it pushed; nothing more moves


def test_a_blocked_other_lane_backs_off_and_the_next_is_tried(
    tmp_path: Path, no_insist: None
) -> None:
    rig, b, slot = four_lane_rig(tmp_path)
    pushes = {0: 0}

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            if move.lane == 2:
                rig.head_sensor.detected = False
            end(move, -1.0)
        elif move.lane == 0:
            pushes[0] += 1
            if pushes[0] == 1:
                end(move, 500.0, kind="blocked")
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1:
            end(move, 120.0, kind="blocked")  # b:2 meets b:3's filament: not it
        elif move.lane == 2:
            rig.head_sensor.detected = True  # b:3 was past the splitter

    b.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert modes(b) == [
        (0, "feed"),
        (0, "rollback"),
        (1, "feed"),
        (1, "rollback"),
        (2, "feed"),
        (2, "rollback"),
        (0, "feed"),
    ]
    assert rollbacks(b) == [(0, 500.0), (1, 120.0), (2, 1000.0)]  # b:4 never moves


def test_a_second_blocked_push_fails_one_search_only(tmp_path: Path, no_insist: None) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            end(move, 480.0, kind="blocked")  # blocked again after a:2 rolled back
        elif move.lane == 1 and move.mode == "feed":
            rig.head_sensor.detected = True
        else:
            if move.lane == 1:
                rig.head_sensor.detected = False
            end(move, -1.0)

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="head 1: the push of a:1 is blocked again"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert modes(a) == [
        (0, "feed"),
        (0, "rollback"),
        (1, "feed"),
        (1, "rollback"),
        (0, "feed"),
        (0, "rollback"),  # blocked again: rolled back, never left in the shared tube
    ]


def test_a_lane_in_the_way_that_does_not_clear_the_sensor_fails(
    tmp_path: Path, no_insist: None
) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            if move.lane == 0:
                end(move, 480.0, kind="blocked")
            else:
                rig.head_sensor.detected = True
        else:
            end(move, -1.0)  # a:2's roll back leaves the sensor set

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="still sees filament"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert modes(a) == [(0, "feed"), (0, "rollback"), (1, "feed"), (1, "rollback")]


def test_a_snag_notice_does_not_end_the_push(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:  # the tip catches at the splitter's outlet and goes on
        rig.adapter.on_feed_event(0, {"kind": "snag", "seq": move.seq, "mode": "feeding"})
        rig.head_sensor.detected = True

    a.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert modes(a) == [(0, "feed")]


def test_another_push_error_is_the_u1_error_and_nothing_else_moves(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    a.feed.on_start = lambda move: end(move, 30.0, kind="stuck")
    error = rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert error == rig.adapter.feed_error("FEED_ERR_WHEEL_SPEED")
    assert modes(a) == [(0, "feed")]


def test_a_probe_move_the_unit_refuses_is_a_probe_error_with_its_reason(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    a.feed.refuse = "error"  # not busy: ace2k's FeedRefused
    with pytest.raises(ace2k_u1_probe.ProbeError) as err:
        rig.module.probe.which_lane(rig.adapter.slot_of_head(0))
    assert str(err.value) == "head 1: a:1's roll back was refused — refused: error"
    assert err.value.reason == "error"


def test_a_lane_with_an_empty_bay_is_not_in_the_way(tmp_path: Path, no_insist: None) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            end(move, 480.0, kind="blocked")
        else:
            end(move, -480.0)
            a.feed.refusals.append("no_filament")  # a:2's start is refused: its bay is empty

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="blocked in the tube"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert modes(a) == [(0, "feed"), (0, "rollback")]


def test_no_lane_of_the_head_is_re_armed_while_the_search_runs(
    tmp_path: Path, no_insist: None
) -> None:
    rig, a, slot = search_rig(tmp_path)
    second = slot.lanes[1]
    rig.adapter.set_current(slot, second)  # a:2 is the head's lane: the standing follow watches it
    rig.units_obj.active["extruder"] = ("a", 1)
    second.follow_wanted = True  # the standing follow would arm a:2 when idle and unheld
    a.lanes[1].update(insert=True, mode="idle")
    rig.left.channel_state[second.ch] = "load_finish"
    pushes = {"a1": 0}

    def on_start(move: FakeMove) -> None:
        rig.adapter.watch(rig.reactor.now)  # a bay-watch tick as each move starts
        if move.lane == 0 and move.mode == "feed":
            pushes["a1"] += 1
            if pushes["a1"] == 1:
                end(move, 480.0, kind="blocked")
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            rig.head_sensor.detected = True
        else:
            if move.lane == 1:
                rig.head_sensor.detected = False
            end(move, -1.0)

    a.feed.on_start = on_start
    rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert [mode for _l, mode in modes(a)] == ["feed", "rollback", "feed", "rollback", "feed"]
    assert all(h.hold is False for h in slot.lanes)  # each lane's hold given back


def first_blocked(move: FakeMove) -> None:
    """a:1's push ends blocked after 480 mm; its roll back runs the whole length."""
    if move.mode == "feed":
        end(move, 480.0, kind="blocked")
    else:
        end(move, -480.0)


def test_another_lane_whose_push_ends_otherwise_fails_without_a_roll_back(
    tmp_path: Path, no_insist: None
) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0:
            first_blocked(move)
        else:
            end(move, 200.0, kind="stuck")  # a:2's push ends neither blocked nor at the sensor

    a.feed.on_start = on_start
    with pytest.raises(
        ace2k_u1_probe.ProbeError, match="head 1: a:2's push toward the head ended stuck"
    ):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert modes(a) == [(0, "feed"), (0, "rollback"), (1, "feed")]  # a:2 not rolled back


def test_another_lane_refused_for_another_reason_fails(tmp_path: Path, no_insist: None) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            end(move, 480.0, kind="blocked")
        else:
            end(move, -480.0)
            a.feed.refusals.append("error")  # a:2's start is refused, its bay not empty

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="head 1: a:2's push was refused"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert modes(a) == [(0, "feed"), (0, "rollback")]


def test_a_polling_failure_in_the_search_stops_the_lane_and_surfaces_as_it_is(
    tmp_path: Path,
) -> None:
    rig, a, slot = search_rig(tmp_path)

    def on_start(move: FakeMove) -> None:
        if move.lane == 0:
            first_blocked(move)
        else:  # a:2's push is running when the poll breaks

            def broken(now: float) -> NoReturn:
                raise RuntimeError("reactor gone")

            rig.reactor.on_pause.append(broken)

    a.feed.on_start = on_start
    with pytest.raises(RuntimeError, match="reactor gone"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert a.feed.log[-1] == ("stop", 1)


def test_a_lane_whose_bay_reads_empty_is_skipped_and_never_rolled_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a.lanes[0]["insert"] = False  # a:1's bay empty: no roll back that could end it in error

    def on_start(move: FakeMove) -> None:
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True

    a.feed.on_start = on_start
    reports_tick(rig, a)
    assert rig.module.probe.which_lane(slot) is slot.lanes[1]
    assert modes(a) == [(1, "rollback")]


def ease_rig(tmp_path: Path, rest: bool) -> tuple[SimpleNamespace, FakeAce2k, Slot]:
    """head 1 empty; a:1's push reaches the sensor and leaves its plunger at rest or pressed."""
    rig, a, slot = search_rig(tmp_path)
    opened = {}

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed" and not rig.head_sensor.detected:  # the push to the sensor
            rig.head_sensor.detected = True
            a.lanes[0]["rest"] = rest
        else:
            opened[move.lane] = move  # the ease: open until its stop

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(n: int) -> None:
        stop(n)
        move = opened.pop(n, None)
        if move is not None:
            move.event = {"kind": "stopped", "seq": move.seq, "filament_mm": -12.0}

    a.feed.stop = stopped
    return rig, a, slot


def test_a_push_that_leaves_the_plunger_pressed_is_eased_slowly_to_rest(tmp_path: Path) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)

    def tick(now: float) -> None:
        a.reports += 1
        if any(m[1] == "rollback" for m in a.feed.started):
            a.lanes[0]["rest"] = True  # the slow roll back lets the plunger back to rest

    rig.reactor.on_pause.append(tick)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert moves(a)[1] == (0, "rollback", 40.0, 10.0)  # EASE_MM at EASE_SPEED
    assert a.feed.log[-1] == ("stop", 0)  # stopped at rest
    assert "ace2k_u1: head 1: a:1 eased 12.0 mm" in rig.gcode.lines


def test_a_push_with_the_plunger_at_rest_is_not_eased(tmp_path: Path) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=True)
    reports_tick(rig, a)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert [m[1] for m in a.feed.started] == ["feed"]


def test_an_ease_that_empties_the_sensor_pushes_back_to_it(tmp_path: Path) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)

    def tick(now: float) -> None:
        a.reports += 1
        modes_now = [m[1] for m in a.feed.started]
        if modes_now[-1] == "rollback":
            rig.head_sensor.detected = False  # the tip left the sensor before the plunger rested
        elif len(modes_now) > 2:
            rig.head_sensor.detected = True

    rig.reactor.on_pause.append(tick)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    # pushed back what it eased (12 mm) plus EASE_MARGIN_MM
    assert moves(a)[1:] == [(0, "rollback", 40.0, 10.0), (0, "feed", 22.0, 10.0)]


def test_no_ease_without_a_proven_link(tmp_path: Path) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)
    a.link_proven = False
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert [m[1] for m in a.feed.started] == ["feed"]


def test_a_taut_plunger_after_the_push_is_not_eased(tmp_path: Path) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)
    a.lanes[0]["pushed"] = True  # taut, not pressed: a roll back would pull against the gear
    reports_tick(rig, a)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert [m[1] for m in a.feed.started] == ["feed"]


def test_a_push_into_a_set_sensor_eases_nothing(tmp_path: Path) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)
    rig.head_sensor.detected = True  # the head holds a filament: push_to_head moves nothing
    reports_tick(rig, a)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert a.feed.started == []


def test_an_ease_that_fails_leaves_the_push_standing(tmp_path: Path) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)
    reports_tick(rig, a)

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed" and not rig.head_sensor.detected:
            rig.head_sensor.detected = True
            a.lanes[0]["rest"] = False
        else:
            move.event = {"kind": "slip", "seq": move.seq, "filament_mm": -3.0}

    a.feed.on_start = on_start
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert "ace2k_u1: head 1: a:1 not eased: its roll back ended slip" in rig.gcode.lines


def test_an_ease_off_the_sensor_that_cannot_reach_it_again_fails_the_push(tmp_path: Path) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)
    reports_tick(rig, a)

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed" and len(a.feed.started) == 1:  # the push: stopped at the sensor
            rig.head_sensor.detected = True
            a.lanes[0]["rest"] = False
        elif move.mode == "rollback":
            rig.head_sensor.detected = False  # the ease takes the tip off the sensor
        else:
            move.event = {"kind": "done", "seq": move.seq, "filament_mm": 22.0}  # never set

    a.feed.on_start = on_start
    with pytest.raises(ace2k_u1_probe.ProbeError, match="did not reach it again"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)


def insist_rig(
    tmp_path: Path, blocks: Sequence[float]
) -> tuple[SimpleNamespace, FakeAce2k, Slot, list[FakeMove]]:
    """head 1 empty; a:1's pushes end blocked after each length of blocks in turn, then the one
    after reaches the sensor; every roll back done."""
    rig, a, slot = search_rig(tmp_path)
    pushes = []

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            pushes.append(move)
            if len(pushes) <= len(blocks):
                a.lanes[0]["rest"] = True
                move.event = {
                    "kind": "blocked",
                    "seq": move.seq,
                    "filament_mm": blocks[len(pushes) - 1],
                }
            else:
                rig.head_sensor.detected = True
        elif move.mode == "feed":  # the search: the other lane meets nothing past the junction
            move.event = {"kind": "blocked", "seq": move.seq, "filament_mm": 10.0}
        elif move.mode == "rollback":
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    rig.module.probe._ease_after_set = lambda slot, h: None
    # the ease back to the plunger's rest before each push again (ease_to_rest has its own
    # tests): rig.eased its mm, None when it does not reach rest
    rig.eased = 0.0
    rig.module.probe.ease_to_rest = lambda h: (
        (True, rig.eased) if rig.eased is not None else (False, 0.0)
    )
    reports_tick(rig, a)  # each pause a fresh sensors report
    return rig, a, slot, pushes


def test_a_push_stopped_blocked_is_pushed_again_and_reaches_the_sensor(tmp_path: Path) -> None:
    rig, a, slot, pushes = insist_rig(tmp_path, [612.0])  # a tip that caught twice in one move
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert moves(a) == [(0, "feed", 2000.0, 70.0), (0, "feed", 1388.0, 70.0)]  # no search
    assert (
        "ace2k_u1: head 1: the push of a:1 stopped blocked after 612 mm: eased 0 mm to its"
        " plunger's rest, pushed again (1/3)"
    ) in rig.gcode.lines


def test_a_free_tip_push_that_ends_blocked_still_reaches_the_push_again(tmp_path: Path) -> None:
    """The free tip forgives every catch while the filament follows the motor, but a push with a
    free tip still ends blocked when the filament stops following (measured on the unit: a feed
    with FREE=1 into a held tube ended blocked). Such an end reaches _insist's push again: on a
    shared head, the U1's load is push_lane, then push_in, then _push_insisting."""
    rig, a, slot, pushes = insist_rig(tmp_path, [612.0])
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert [free for lane, mode, free in a.feed.free_tips if mode == "feed"] == [True, True]
    assert len(pushes) == 2
    assert (
        "ace2k_u1: head 1: the push of a:1 stopped blocked after 612 mm: eased 0 mm to its"
        " plunger's rest, pushed again (1/3)"
    ) in rig.gcode.lines


def test_a_searched_lanes_free_tip_push_that_ends_blocked_is_pushed_again(tmp_path: Path) -> None:
    """The search for the lane in the way pushes each other lane with a free tip too, and its
    blocked end goes through _insist the same way."""
    rig, a, slot = search_rig(tmp_path)
    tries = {"a2": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            if not rig.head_sensor.detected and tries["a2"] == 0:
                end(move, 480.0, kind="blocked")
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            tries["a2"] += 1
            if tries["a2"] == 1:
                end(move, 300.0, kind="blocked")
            else:
                rig.head_sensor.detected = True
        elif move.mode == "rollback":
            if move.lane == 1:
                rig.head_sensor.detected = False
            end(move, -1.0)

    a.feed.on_start = on_start
    reports_tick(rig, a)
    rig.module.probe._ease_after_set = lambda slot, h: None
    rig.module.probe.ease_to_rest = lambda h: (True, 0.0)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert tries["a2"] == 2  # a:2 pushed again after its blocked end
    feeds = [(lane, free) for lane, mode, free in a.feed.free_tips if mode == "feed"]
    assert [lane for lane, _free in feeds].count(1) == 2
    assert all(free is True for _lane, free in feeds)
    assert (
        "ace2k_u1: head 1: a:2 stopped blocked after 300 mm: eased 0 mm to its plunger's rest,"
        " pushed again (1/3)"
    ) in rig.gcode.lines


def test_a_push_blocked_through_every_try_rolls_back_all_it_pushed_and_searches(
    tmp_path: Path,
) -> None:
    rig, a, slot, pushes = insist_rig(tmp_path, [300.0, 40.0, 30.0, 20.0, 9.0])
    with pytest.raises(ace2k_u1_probe.ProbeError, match="blocked in the tube"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert len(pushes) == 4  # the push and three tries
    assert rollbacks(a)[0] == (0, 390.0)  # 300 + 40 + 30 + 20, at swap_speed


def test_a_push_again_that_barely_moves_is_a_hard_stop(tmp_path: Path) -> None:
    rig, a, slot, pushes = insist_rig(tmp_path, [300.0, 2.0, 50.0])
    with pytest.raises(ace2k_u1_probe.ProbeError, match="blocked in the tube"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert len(pushes) == 2  # the second gained 2 mm: no third
    assert rollbacks(a)[0] == (0, 302.0)


def test_no_push_again_while_the_plunger_is_not_back_at_rest(tmp_path: Path) -> None:
    rig, a, slot, pushes = insist_rig(tmp_path, [300.0])
    on_start = a.feed.on_start

    def held(move: FakeMove) -> None:
        on_start(move)
        a.lanes[0]["rest"] = False  # something holds the filament: the plunger stays pulled

    a.feed.on_start = held
    rig.eased = None  # the ease back does not reach rest
    with pytest.raises(ace2k_u1_probe.ProbeError, match="blocked in the tube"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert len(pushes) == 1
    assert rollbacks(a)[0] == (0, 300.0)


def test_no_push_again_without_a_proven_link(tmp_path: Path) -> None:
    rig, a, slot, pushes = insist_rig(tmp_path, [300.0])
    a.link_proven = False
    rig.module.probe.ease_to_rest = type(rig.module.probe).ease_to_rest.__get__(rig.module.probe)
    with pytest.raises(ace2k_u1_probe.ProbeError):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert len(pushes) == 1


def test_a_push_again_with_no_distance_rolls_back_the_heads_splitter_mm(tmp_path: Path) -> None:
    rig, a, slot, pushes = insist_rig(tmp_path, [300.0, 0.0])  # the try's end gives no distance
    with pytest.raises(ace2k_u1_probe.ProbeError, match="blocked in the tube"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert len(pushes) == 2
    assert rollbacks(a)[0] == (0, 1000.0)  # its tip unknown: the head's splitter_mm


def test_a_push_again_takes_only_what_is_left_of_the_budget(tmp_path: Path) -> None:
    rig, a, slot, pushes = insist_rig(tmp_path, [612.0])
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert [m[2] for m in a.feed.started] == [2000.0, 1388.0]


def test_a_searched_lane_whose_tip_catches_twice_is_pushed_again_and_found(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    tries = {"a2": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            if not rig.head_sensor.detected and tries["a2"] == 0:
                end(move, 480.0, kind="blocked")  # a:2's filament past the splitter
            else:
                rig.head_sensor.detected = True
        elif move.lane == 1 and move.mode == "feed":
            tries["a2"] += 1
            if tries["a2"] == 1:
                end(move, 300.0, kind="blocked")  # a:2's own tip caught twice
            else:
                rig.head_sensor.detected = True
        elif move.mode == "rollback":
            if move.lane == 1:
                rig.head_sensor.detected = False
            end(move, -1.0)

    a.feed.on_start = on_start
    reports_tick(rig, a)
    rig.module.probe._ease_after_set = lambda slot, h: None
    rig.module.probe.ease_to_rest = lambda h: (True, 0.0)
    monkey = rig.module.probe
    assert monkey.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert tries["a2"] == 2
    assert "ace2k_u1: head 1: a:2 was past the splitter: rolled back" in rig.gcode.lines


def test_a_push_again_that_fails_otherwise_rolls_back_all_it_pushed(tmp_path: Path) -> None:
    rig, a, slot, pushes = insist_rig(tmp_path, [300.0])
    on_start = a.feed.on_start

    def second_runs_out(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed" and len(pushes) == 1:
            pushes.append(move)
            move.event = {"kind": "runout", "seq": move.seq, "filament_mm": 40.0}
            return
        on_start(move)

    a.feed.on_start = second_runs_out
    error = rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert error is not None
    assert rollbacks(a) == [(0, 340.0)]  # the push's 300 and the try's 40: out of the tube


def test_a_searched_lane_whose_try_fails_is_rolled_back_before_the_error(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    tries = {"a2": 0}

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            end(move, 480.0, kind="blocked")
        elif move.lane == 1 and move.mode == "feed":
            tries["a2"] += 1
            if tries["a2"] == 1:
                end(move, 300.0, kind="blocked")
            else:
                end(move, 20.0, kind="slip")  # the try ends otherwise
        elif move.mode == "rollback":
            end(move, -1.0)

    a.feed.on_start = on_start
    reports_tick(rig, a)
    rig.module.probe.ease_to_rest = lambda h: (True, 0.0)
    with pytest.raises(ace2k_u1_probe.ProbeError, match="a:2's push toward the head ended slip"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert (1, 300.0) in rollbacks(a)  # a:2 not left in the shared tube


def test_an_eased_push_again_takes_back_what_it_eased(tmp_path: Path) -> None:
    """After a block the lane goes back to its plunger's rest (here 12 mm),
    then is pushed again with that much more of the budget."""
    rig, a, slot, pushes = insist_rig(tmp_path, [612.0])
    rig.eased = 12.0
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert [m[2] for m in a.feed.started] == [2000.0, 1400.0]
    assert (
        "ace2k_u1: head 1: the push of a:1 stopped blocked after 612 mm: eased 12 mm to its"
        " plunger's rest, pushed again (1/3)"
    ) in rig.gcode.lines


def test_the_ease_to_rest_rolls_back_slowly_until_a_fresh_report_reads_rest(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)  # the head's sensor empty all along: no "empty" end
    h = slot.lanes[0]
    a.lanes[0]["rest"] = False
    opened = {}

    def on_start(move: FakeMove) -> None:
        opened[move.lane] = move

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(lane: int) -> None:
        stop(lane)
        move = opened.pop(lane, None)
        if move is not None:
            end(move, -8.0, kind="stopped")

    a.feed.stop = stopped
    ticks = {"n": 0}

    def tick(now: float) -> None:
        a.reports += 1
        ticks["n"] += 1
        if ticks["n"] == 6:
            a.lanes[0]["rest"] = True  # the plunger back at rest

    rig.reactor.on_pause.append(tick)
    assert rig.module.probe.ease_to_rest(h) == (True, 8.0)
    lane, mode, length, speed, _m = a.feed.started[0]
    assert (lane, mode, length, speed) == (0, "rollback", ace2k_u1_probe.EASE_MM, 10.0)


def test_the_ease_to_rest_that_never_reads_rest_is_none(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    a.lanes[0]["rest"] = False  # held: the plunger never comes back
    a.feed.on_start = lambda move: end(move, -40.0)  # the whole EASE_MM, no rest read
    reports_tick(rig, a)
    rested, back = rig.module.probe.ease_to_rest(slot.lanes[0])
    assert rested is False and back == 40.0  # what it went back counts: the tip is there
    assert "not eased to its plunger's rest" in rig.gcode.lines[-1]


def test_a_plunger_already_at_rest_is_not_eased(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    a.lanes[0]["rest"] = True
    assert rig.module.probe.ease_to_rest(slot.lanes[0]) == (True, 0.0)
    assert a.feed.started == []


def test_a_push_again_that_only_retakes_its_ease_is_a_hard_stop(tmp_path: Path) -> None:
    """Eased 12 mm, pushed again 12 mm into the same block: no gain."""
    rig, a, slot, pushes = insist_rig(tmp_path, [300.0, 12.0, 50.0])
    rig.eased = 12.0
    with pytest.raises(ace2k_u1_probe.ProbeError, match="blocked in the tube"):
        rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0)
    assert len(pushes) == 2  # the second regained only its ease: no third


# --- the unit's own stop on its plunger (ace2k host interface 20) --------------------------------
# On an image with has_stop_when, the probe, the free check and the roll to rest arm the unit's
# stop: "held" for a taut plunger with the filament standing still, "at_rest" for a plunger at
# rest. The host then reads no status while the move runs. The head's sensor is the U1's, which
# the unit cannot see, so the host still watches it. An image without the stop keeps the host's
# watch of the plunger. Each path runs on both images.

HELD_ONLY = frozenset({"held"})
TO_REST = frozenset({"at_rest", "held"})
UNIT_POLLS = 8  # the reactor pauses before the unit ends a move itself, so the host polls a while


class UnitEnds:
    """The unit's end of each move of a test, and the status reads made while each ran.

    ``ends`` maps a (lane, mode) to the (kind, filament mm) that the move ends with. A move whose
    kind is "held" or "at_rest" stays open. With the unit's stop (``stop``), it ends by itself
    UNIT_POLLS pauses after its start. Without, it ends only when the host stops it, and the unit
    answers that stop with "stopped". A move with another kind ends at once. A host stop of an open
    move is also answered "stopped" (the head sensor's stop, on either image). ``reads`` gets the
    status reads made while each open move ran.
    """

    def __init__(self, rig: SimpleNamespace, a: FakeAce2k, stop: bool) -> None:
        self.a, self.stop = a, stop
        self.ends: dict[tuple[int, str], tuple[str, float]] = {}
        self.open: dict[int, list] = {}
        self.reads: list[int] = []
        a.feed.has_stop_when = stop
        a.feed.on_start = self.on_start
        self.host_stop = a.feed.stop
        a.feed.stop = self.stopped
        rig.reactor.on_pause.append(self.tick)

    def polls(self, lane: int) -> int:
        """The pauses since the open move of lane started (-1: none open)."""
        entry = self.open.get(lane)
        return -1 if entry is None else entry[1]

    def on_start(self, move: FakeMove) -> None:
        kind, mm = self.ends.get((move.lane, move.mode), ("", 0.0))
        if kind in ("held", "at_rest"):
            self.open[move.lane] = [move, 0, self.a.status_reads, kind, mm]
        elif kind:
            end(move, mm, kind=kind)

    def tick(self, now: float) -> None:
        self.a.reports += 1
        for lane, entry in list(self.open.items()):
            entry[1] += 1
            if self.stop and entry[1] >= UNIT_POLLS:
                self.finish(lane, entry[3])

    def finish(self, lane: int, kind: str) -> None:
        move, _polls, reads, _kind, mm = self.open.pop(lane)
        self.reads.append(self.a.status_reads - reads)
        end(move, mm, kind=kind)

    def stopped(self, lane: int) -> None:
        self.host_stop(lane)
        if lane in self.open:
            self.finish(lane, "stopped")


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_the_lane_held_by_the_head_is_found_on_both_images(tmp_path: Path, stop: bool) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    unit = UnitEnds(rig, a, stop)
    unit.ends = {
        (0, "rollback"): ("done", -100.0),
        (0, "feed"): ("done", 100.0),
        (1, "rollback"): ("held", -37.0),
        (1, "feed"): ("blocked", 37.0),
    }
    a.lanes[1]["pushed"] = True  # the gear holds a:2: its plunger goes taut
    assert rig.module.probe.which_lane(slot) is slot.lanes[1]
    assert moves(a) == [
        (0, "rollback", 100.0, 50.0),
        (0, "feed", 100.0, 50.0),
        (1, "rollback", 100.0, 50.0),
        (1, "feed", 37.0, 50.0),  # fed back what it rolled back
    ]
    armed = HELD_ONLY if stop else NOT_PASSED
    assert [s for _l, _m, s in a.feed.stop_whens] == [armed, NOT_PASSED, armed, NOT_PASSED]
    window = 120 if stop else NOT_PASSED  # about 6 mm of motor at 50 mm/s
    assert [w for _l, _m, w in a.feed.held_mss] == [window, NOT_PASSED, window, NOT_PASSED]
    assert (unit.reads == [0]) is stop  # without the stop the host polled the plunger
    # every lane is stopped once as the probe takes the head; the host's taut stop comes on top
    assert a.feed.log.count(("stop", 1)) == (1 if stop else 2)
    assert "ace2k_u1: head 1: probe: a:2 is in the head" in rig.gcode.lines


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_a_thick_tip_dragged_through_the_tube_is_not_taken_for_held(
    tmp_path: Path, stop: bool
) -> None:
    """a:1's tip, formed thick by a swap, drags in the tube. Its plunger reads taut while the
    filament still comes. The unit runs on, so a:1 is free. Without the stop, the host's watch
    takes it for held, as it always did, and the probe finds two lanes."""
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    unit = UnitEnds(rig, a, stop)
    unit.ends = {
        (0, "feed"): ("done", 100.0),
        (1, "rollback"): ("held", -37.0),
        (1, "feed"): ("blocked", 37.0),
    }
    drag: dict[str, FakeMove | int] = {}

    def on_start(move: FakeMove) -> None:
        a.lanes[0]["pushed"] = move.lane == 0 and move.mode == "rollback"
        if a.lanes[0]["pushed"]:
            drag.update(move=move, at=a.reports)  # ends below, after its whole length
        else:
            unit.on_start(move)

    def drags(now: float) -> None:
        move = drag.get("move")
        if move is not None and move.event is None and a.reports - drag["at"] >= 30:
            end(move, -100.0)  # the whole length, the filament coming all along

    def stopped(lane: int) -> None:
        unit.stopped(lane)
        move = drag.get("move")
        if lane == 0 and move is not None and move.event is None:
            end(move, -20.0, kind="stopped")

    a.feed.on_start = on_start
    a.feed.stop = stopped
    rig.reactor.on_pause.append(drags)
    a.lanes[1]["pushed"] = True
    if stop:
        assert rig.module.probe.which_lane(slot) is slot.lanes[1]
        assert moves(a)[:2] == [(0, "rollback", 100.0, 50.0), (0, "feed", 100.0, 50.0)]
        assert a.feed.log.count(("stop", 0)) == 1  # only the probe's stop as it takes the head
    else:
        with pytest.raises(ace2k_u1_probe.ProbeError, match="more than one lane holds"):
            rig.module.probe.which_lane(slot)


def test_with_the_units_stop_the_head_sensor_still_stops_the_probe_from_the_host(
    tmp_path: Path,
) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    unit = UnitEnds(rig, a, stop=True)
    unit.ends = {
        (0, "rollback"): ("held", -3.0),  # open: only the host's stop on the sensor ends it
        (1, "rollback"): ("done", -100.0),
        (1, "feed"): ("done", 100.0),
    }

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "feed":
            rig.head_sensor.detected = True  # pushed back to the sensor
        else:
            unit.on_start(move)

    def leaves(now: float) -> None:
        if unit.polls(0) >= 2:
            rig.head_sensor.detected = False  # a:1 sat at the sensor: its tip leaves it

    a.feed.on_start = on_start
    rig.reactor.on_pause.append(leaves)
    assert rig.module.probe.which_lane(slot) is slot.lanes[0]
    assert a.feed.stop_whens[0] == (0, "rollback", HELD_ONLY)
    log = a.feed.log
    assert log[log.index(("start", 0, "rollback", 100.0)) + 1] == ("stop", 0)  # the host's
    assert "ace2k_u1: head 1: probe: a:1 is in the head" in rig.gcode.lines


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_a_free_check_held_by_the_head_is_said_held_on_both_images(
    tmp_path: Path, stop: bool
) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    unit = UnitEnds(rig, a, stop)
    unit.ends = {(0, "rollback"): ("held", -31.5), (0, "feed"): ("done", 31.5)}
    a.lanes[0]["pushed"] = True
    gcmd = run(rig, "ACEK_FREE_CHECK", {"UNIT": "a", "LANE": "1", "LENGTH": "100", "SPEED": "30"})
    assert moves(a) == [(0, "rollback", 100.0, 30.0), (0, "feed", 31.5, 50.0)]
    assert a.feed.stop_whens[0] == (0, "rollback", HELD_ONLY if stop else NOT_PASSED)
    assert a.feed.held_mss[0] == (0, "rollback", 200 if stop else NOT_PASSED)  # 6 mm at 30
    assert (unit.reads == [0]) is stop
    assert a.feed.log.count(("stop", 0)) == (1 if stop else 2)
    assert gcmd.lines == [
        "ace2k_u1: a:1 free check, 100 mm at 30 mm/s: held by head 1, taut after 31.5 mm; fed back"
    ]


def test_with_the_units_stop_a_free_check_held_with_the_sensor_then_empty_is_a_snag(
    tmp_path: Path,
) -> None:
    rig, a = probe_rig(tmp_path)
    h = rig.adapter.slot_of_head(0).lanes[0]
    unit = UnitEnds(rig, a, stop=True)
    unit.ends = {(0, "rollback"): ("held", -12.0)}

    after = []

    def springs(now: float) -> None:
        # the tip springs past the sensor once the unit has ended the move held, so the host's
        # sensor stop does not come first
        if a.feed.started and a.feed.started[-1][4].event is not None:
            after.append(now)
            rig.head_sensor.detected = len(after) < 2

    rig.reactor.on_pause.append(springs)
    assert rig.module.probe.free_check(h) == 12.0
    assert a.feed.stop_whens == [(0, "rollback", HELD_ONLY)]
    assert unit.reads == [0]
    assert (
        "ace2k_u1: head 1: a:1's plunger went taut after 12.0 mm with the head's sensor then"
        " empty: its tip caught above the gear, not held"
    ) in rig.gcode.lines


def ease_ends(rig: SimpleNamespace, a: FakeAce2k, stop: bool, kind: str, mm: float) -> UnitEnds:
    """ease_rig's push to the sensor, then the ease's roll back as UnitEnds runs it."""
    push = a.feed.on_start
    unit = UnitEnds(rig, a, stop)
    unit.ends = {(0, "rollback"): (kind, mm)}

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed" and not rig.head_sensor.detected:
            push(move)  # the push reaches the sensor and leaves the plunger pressed
        elif move.mode == "rollback":
            unit.on_start(move)
        else:
            rig.head_sensor.detected = True  # a push back to the sensor

    a.feed.on_start = on_start
    return unit


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_the_ease_after_a_push_stops_at_rest_on_both_images(tmp_path: Path, stop: bool) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)
    unit = ease_ends(rig, a, stop, "at_rest", -12.0)

    def rests(now: float) -> None:
        if unit.polls(0) >= 3:
            a.lanes[0]["rest"] = True  # the slow roll back lets the plunger back to rest

    rig.reactor.on_pause.append(rests)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert moves(a)[1] == (0, "rollback", 40.0, 10.0)  # EASE_MM at EASE_SPEED
    assert a.feed.stop_whens[1] == (0, "rollback", TO_REST if stop else NOT_PASSED)
    assert a.feed.held_mss[1] == (0, "rollback", 600 if stop else NOT_PASSED)
    assert (unit.reads == [0]) is stop
    assert a.feed.log[-1] == (("start", 0, "rollback", 40.0) if stop else ("stop", 0))
    assert "ace2k_u1: head 1: a:1 eased 12.0 mm" in rig.gcode.lines


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_an_ease_that_meets_a_held_tip_is_not_eased_on_both_images(
    tmp_path: Path, stop: bool
) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)
    unit = ease_ends(rig, a, stop, "held", -4.0)
    start = a.feed.on_start

    def taut(move: FakeMove) -> None:
        start(move)
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True  # the roll back meets the gear: the plunger goes taut

    a.feed.on_start = taut
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    assert a.feed.stop_whens[1] == (0, "rollback", TO_REST if stop else NOT_PASSED)
    assert (unit.reads == [0]) is stop
    assert "ace2k_u1: head 1: a:1 not eased: its plunger went taut" in rig.gcode.lines


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_an_ease_off_the_sensor_is_stopped_by_the_host_on_both_images(
    tmp_path: Path, stop: bool
) -> None:
    rig, a, slot = ease_rig(tmp_path, rest=False)
    unit = ease_ends(rig, a, stop, "at_rest", -12.0)

    def leaves(now: float) -> None:
        if unit.polls(0) >= 2:
            rig.head_sensor.detected = False  # the tip left the sensor before the plunger rested

    rig.reactor.on_pause.append(leaves)
    assert rig.module.probe.push_in(slot, slot.lanes[0], speed=70.0) is None
    # pushed back what it eased (12 mm) plus EASE_MARGIN_MM
    assert moves(a)[1:] == [(0, "rollback", 40.0, 10.0), (0, "feed", 22.0, 10.0)]
    assert a.feed.stop_whens[1] == (0, "rollback", TO_REST if stop else NOT_PASSED)
    assert "ace2k_u1: head 1: a:1 eased 12.0 mm, off the head's sensor" in rig.gcode.lines


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_the_ease_before_a_push_again_stops_at_rest_on_both_images(
    tmp_path: Path, stop: bool
) -> None:
    rig, a, slot = search_rig(tmp_path)
    unit = UnitEnds(rig, a, stop)
    unit.ends = {(0, "rollback"): ("at_rest", -9.0)}
    a.lanes[0]["rest"] = False

    def rests(now: float) -> None:
        if unit.polls(0) >= 3:
            a.lanes[0]["rest"] = True

    rig.reactor.on_pause.append(rests)
    assert rig.module.probe.ease_to_rest(slot.lanes[0]) == (True, 9.0)
    assert a.feed.stop_whens == [(0, "rollback", TO_REST if stop else NOT_PASSED)]
    assert (unit.reads == [0]) is stop


def test_with_the_units_stop_a_short_ease_keeps_its_own_distance(tmp_path: Path) -> None:
    """The unit's at_rest can come one or two ticks in, with its distance on the event. The time
    the ease took (the arming, the start, the polls) is not used in its place."""
    rig, a, slot = search_rig(tmp_path)
    unit = UnitEnds(rig, a, stop=True)
    unit.ends = {(0, "rollback"): ("at_rest", -0.3)}
    a.lanes[0]["rest"] = False
    assert rig.module.probe.ease_to_rest(slot.lanes[0]) == (True, 0.3)


def test_on_a_host_watch_a_short_ease_still_counts_its_time(tmp_path: Path) -> None:
    rig, a, slot = search_rig(tmp_path)
    unit = UnitEnds(rig, a, stop=False)
    unit.ends = {(0, "rollback"): ("at_rest", -0.3)}
    a.lanes[0]["rest"] = False

    def rests(now: float) -> None:
        if unit.polls(0) >= 3:
            a.lanes[0]["rest"] = True

    rig.reactor.on_pause.append(rests)
    rested, eased = rig.module.probe.ease_to_rest(slot.lanes[0])
    assert rested and eased > 0.3  # the host's stop carries "stopped" at 0.3 mm: its time counts
