"""A lane's material before its load, its eject and a print.

The material is set by the lane's tag or its Edit spool record. The head's setting counts on a
head of one lane while it belongs to the spool in the bay, and on a shared head when it was written
for the lane. The U1 takes a load's temperature from the head's setting when its load starts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import ace2k_u1_material
import ace2k_u1_probe
import fakes
import pytest
from test_probe import reports_tick
from test_tags_wiring import TABLE
from test_tools import (
    EIGHT,
    print_start,
    run,
    set_plan,
    spools,
    swap_rig,
    tick,
    tool,
    tools_rig,
)
from test_units import Gcmd

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked, Slot
    from fakes import FakeAce2k, FakeMove

LONE_CH = 0  # a:3 feeds head 2 alone: channel 0 of the left feed
LONE_LANE = 2
SHARED_CH = 1  # a:1 and a:2 feed head 1: channel 1 of the left feed
SHARED_LANE = 1  # a:2
REFUSED = "ace2k_u1: lane a:3: no material set — set it with Edit spool: its load refused"


def lone(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k]:
    """a:3 alone on head 2, in its bay, the head empty. A push sets the head's sensor 120 mm on.
    A roll back that starts with the sensor set clears it at the next pause and runs on until
    stopped; with rig.sensor_clears False (or the sensor clear) it ends done at once."""
    rig, by_name = tools_rig(tmp_path)
    rig.module.printer.objects["filament_parameters"] = fakes.FakeFilamentParameters(TABLE)
    a = by_name["a"]
    a.link_proven = True
    a.lanes[LONE_LANE]["insert"] = True
    sensor = rig.left.runout_sensor[LONE_CH]
    sensor.detected = False
    rig.sensor_clears = True

    def clear_once(_now: float) -> None:
        sensor.detected = False
        rig.reactor.on_pause.remove(clear_once)

    def on_start(move: FakeMove) -> None:
        if move.mode == "feed":
            a.lanes[LONE_LANE]["encoder_mm"] = a.lanes[LONE_LANE]["encoder_mm"] + 120.0
            sensor.detected = True
        if move.mode == "rollback":
            if sensor.detected and rig.sensor_clears:
                rig.reactor.on_pause.append(clear_once)  # the tip leaves the sensor on the way
                return
            length = a.feed.started[-1][2]
            move.event = {
                "kind": "done",
                "seq": move.seq,
                "filament_mm": -length,
                "motor_mm": length,
            }

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stop_ends_it(lane: int) -> None:
        stop(lane)
        for _l, mode, _len, _s, move in a.feed.started:
            if mode == "rollback" and move.event is None and rig.stop_ends:
                move.event = {"kind": "stopped", "seq": move.seq}

    a.feed.stop = stop_ends_it
    rig.stop_ends = True  # False: a stop whose final event never comes
    reports_tick(rig, a)  # a sensors report per pause, as the unit sends them
    return rig, a


def push_max(rig: SimpleNamespace) -> float:
    """How far the last push may have gone (the move stopped at the sensor counts whole)."""
    return rig.adapter.hooked_lane(rig.units["a"], LONE_LANE).push_max_mm


def rollbacks(a: FakeAce2k) -> list[float]:
    return [length for lane, mode, length, _s, _m in a.feed.started if mode == "rollback"]


def stops_in_back_out(a: FakeAce2k) -> int:
    """Count the stops sent since the first roll back started."""
    log = a.feed.log
    first = next(i for i, entry in enumerate(log) if entry[:3] == ("start", LONE_LANE, "rollback"))
    return sum(1 for entry in log[first:] if entry[0] == "stop")


def head_type(rig: SimpleNamespace, head: int, kind: str) -> None:
    rig.task_config.status["filament_type"][head] = kind


def test_a_u1_load_of_a_lane_with_no_material_is_rolled_back_and_refused(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    with pytest.raises(RuntimeError, match="no material set"):
        rig.left._put_into_drive(LONE_CH)
    # back until its sensor clears (the push's farthest is only the cap), stopped there and its
    # final event waited for, then BACK_OUT_MM more
    assert rollbacks(a) == [push_max(rig), ace2k_u1_material.BACK_OUT_MM]
    assert push_max(rig) >= 120.0
    assert stops_in_back_out(a) == 1
    assert REFUSED in rig.gcode.lines
    assert rig.left.channel_error[LONE_CH] == "general"


def test_a_u1_load_whose_head_was_set_for_another_type_writes_it_and_asks_again(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PETG", "COLOR": "ffffff"})
    head_type(rig, 1, "PLA")
    sent: list[str] = []
    rig.gcode.run_script_from_command = sent.append
    with pytest.raises(RuntimeError, match="load it again"):
        rig.left._put_into_drive(LONE_CH)
    written = [ln for ln in sent if ln.startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=1 ")]
    assert len(written) == 1 and "FILAMENT_TYPE=PETG" in written[0]  # written on the head
    assert rollbacks(a) == [push_max(rig), ace2k_u1_material.BACK_OUT_MM]
    assert (
        "ace2k_u1: head 2: a:3 is PETG, the head was set for PLA: set now — load it again"
        in rig.gcode.lines
    )


def test_a_u1_load_whose_head_has_the_lanes_type_goes_on(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "pla", "COLOR": "ffffff"})
    head_type(rig, 1, "PLA")  # set by another lane, or before a restart: the type is what counts
    assert rig.module.spool_lanes.own_spool(rig.adapter.hooked_lane(rig.units["a"], 2))[1] == "PLA"
    assert rig.left._put_into_drive(LONE_CH) is None
    assert rollbacks(a) == []


def test_a_u1_load_of_a_lane_the_head_already_held_is_refused_and_not_pulled(
    tmp_path: Path,
) -> None:
    """The head's sensor saw the lane before the load: its filament may be in the head's gear,
    and nothing pulls a filament against the head. The load is refused, nothing rolled back."""
    rig, a = lone(tmp_path)
    rig.left.runout_sensor[LONE_CH].detected = True
    with pytest.raises(RuntimeError, match="no material set"):
        rig.left._put_into_drive(LONE_CH)
    assert rollbacks(a) == []


def test_a_u1_load_of_a_lane_the_preload_left_at_the_sensor_is_rolled_back(tmp_path: Path) -> None:
    """The preload's push left the tip at the head's sensor, short of the head's gear."""
    rig, a = lone(tmp_path)
    rig.left.runout_sensor[LONE_CH].detected = True
    h = rig.adapter.hooked_lane(rig.units["a"], LONE_LANE)
    h.parked_at_sensor = True
    with pytest.raises(RuntimeError, match="no material set"):
        rig.left._put_into_drive(LONE_CH)
    # it came 0 mm: the cap is BACK_OUT_MM, then BACK_OUT_MM past the sensor's clearing
    assert rollbacks(a) == [ace2k_u1_material.BACK_OUT_MM, ace2k_u1_material.BACK_OUT_MM]
    assert h.parked_at_sensor is False  # its roll back moved it


def test_a_tag_material_with_no_colour_other_than_the_heads_refuses_the_load(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    a.lanes[LONE_LANE]["tag"] = {"state": "read", "record": {"material": "PETG"}}
    head_type(rig, 1, "PLA")
    with pytest.raises(RuntimeError, match="a:3's load refused"):
        rig.left._put_into_drive(LONE_CH)
    assert rollbacks(a)


def test_a_lane_whose_status_cannot_be_read_is_not_refused(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(rig.units["a"], LONE_LANE)
    rig.module.spool_lanes.lane_read = lambda h, units=None: {}  # the read failed
    assert rig.module.material.defined(h) is None
    assert rig.module.material.refusal(h) is None
    assert rig.left._put_into_drive(LONE_CH) is None


def test_a_tag_with_a_material_and_no_colour_counts_as_set(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    a.lanes[LONE_LANE]["tag"] = {"state": "read", "record": {"material": "PLA"}}
    h = rig.adapter.hooked_lane(rig.units["a"], LONE_LANE)
    assert rig.module.material.defined(h) is True


def test_a_long_back_out_runs_in_the_units_longest_moves(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    rig.sensor_clears = False  # the head's sensor never reads clear: back as far as it came
    rig.left.runout_sensor[LONE_CH].detected = True
    h = rig.adapter.hooked_lane(rig.units["a"], LONE_LANE)
    h.push_max_mm = 4500.0
    rig.module.material.back_out(h)
    assert rollbacks(a) == [2000.0, 2000.0, 500.0]  # the fake's longest move is 2000 mm


def test_a_back_out_refused_busy_is_retried_and_counts_as_the_adapters_move(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    a.feed.refusals = ["busy"]  # the push's stop still winding down
    h = rig.adapter.hooked_lane(rig.units["a"], LONE_LANE)
    rig.module.material.back_out(h)
    assert rollbacks(a) == [ace2k_u1_material.BACK_OUT_MM]  # the sensor is already clear
    assert h.motion_seq == a.feed.started[-1][4].seq


def test_prepare_after_a_push_with_the_status_unreadable_refuses_and_rolls_back(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    gate = rig.module.material
    answers = iter([False, None])
    gate.defined = lambda h, units=None: next(answers)
    with pytest.raises(ValueError, match="its status not read after the push"):
        prepare(rig)
    assert rollbacks(a)


def test_a_back_out_move_past_its_time_is_stopped(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    a.feed.on_start = lambda move: None  # the roll back never ends
    rig.stop_ends = False
    h = rig.adapter.hooked_lane(rig.units["a"], LONE_LANE)
    rig.module.material.back_out(h)
    assert LONE_LANE in a.feed.stopped
    assert any(
        ln.startswith("ace2k_u1: lane a:3 roll back out of e1:") and "timed out" in ln
        for ln in rig.gcode.lines
    )


def prepare(rig: SimpleNamespace) -> Gcmd:
    return run(rig, "ACEK_HEAD_PREPARE", {"UNIT": "a", "LANE": "3"})


def test_prepare_does_nothing_for_a_lane_whose_material_is_set(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    spools(rig, {"a:3": "d32c34"})
    gcmd = prepare(rig)
    assert a.feed.started == []
    assert gcmd.lines == ["ace2k_u1: lane a:3: its material is set"]


def test_prepare_pushes_and_takes_the_tag_read_on_the_way(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    real = a.feed.on_start

    def read_on_the_way(move: FakeMove) -> None:
        real(move)
        if move.mode == "feed":
            a.lanes[LONE_LANE]["tag"] = {
                "state": "read",
                "record": {"material": "PLA", "color_rgba": "FFFFFFFF"},
            }

    a.feed.on_start = read_on_the_way
    gcmd = prepare(rig)
    assert [m for _l, m, _len, _s, _mv in a.feed.started] == ["feed"]
    assert gcmd.lines == ["ace2k_u1: lane a:3: its tag read on the way to e1"]
    assert rig.adapter.hooked_lane(rig.units["a"], LONE_LANE).parked_at_sensor is True


def test_prepare_with_no_material_after_the_push_rolls_back_and_refuses(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    with pytest.raises(ValueError, match="no material set"):
        prepare(rig)
    assert rollbacks(a) == [push_max(rig), ace2k_u1_material.BACK_OUT_MM]


def test_a_stop_whose_final_event_does_not_come_moves_nothing_more(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    rig.stop_ends = False
    with pytest.raises(ValueError, match="no material set"):
        prepare(rig)
    assert rollbacks(a) == [push_max(rig)]  # no BACK_OUT_MM after a stop not confirmed
    assert any("its roll back was not seen to stop" in ln for ln in rig.gcode.lines)


def test_a_lone_head_whose_sensor_never_clears_rolls_back_as_far_as_it_came(
    tmp_path: Path,
) -> None:
    """Where the sensor fired is not known: the push's own farthest is the safe floor."""
    rig, a = lone(tmp_path)
    rig.sensor_clears = False
    with pytest.raises(ValueError, match="no material set"):
        prepare(rig)
    assert rollbacks(a) == [push_max(rig)]
    assert stops_in_back_out(a) == 0


def test_a_lone_head_whose_sensor_cannot_be_read_rolls_back_as_far_as_it_came(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    rig.left.runout_sensor[LONE_CH].detected = True
    h = rig.adapter.hooked_lane(rig.units["a"], LONE_LANE)
    h.push_max_mm = 300.0
    probe = rig.adapter.insister
    probe._sensor = lambda lane: (_ for _ in ()).throw(ace2k_u1_probe.ProbeError("unread"))
    rig.module.material.back_out(h)
    assert rollbacks(a) == [300.0]
    assert h.follow_wanted is False


def shared_push(tmp_path: Path) -> tuple[SimpleNamespace, FakeAce2k, float]:
    """a:2 (no material set) is the lane of head 1, which a:1 shares; the head is empty. Its
    push sets the head's sensor; a roll back clears it and ends done with all it was asked.
    Returns the rig, the unit and head 1's splitter_mm."""
    rig, by_name = tools_rig(tmp_path)
    rig.module.printer.objects["filament_parameters"] = fakes.FakeFilamentParameters(TABLE)
    a = by_name["a"]
    a.link_proven = True
    a.lanes[SHARED_LANE]["insert"] = True
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[SHARED_LANE])
    sensor = rig.left.runout_sensor[SHARED_CH]
    sensor.detected = False

    def push_in(slot: Slot, h: Hooked, speed: float | None) -> str | None:
        sensor.detected = True
        return None

    rig.module.probe.push_in = push_in

    def on_start(move: FakeMove) -> None:
        if move.mode == "rollback":
            sensor.detected = False
            length = a.feed.started[-1][2]
            move.event = {
                "kind": "done",
                "seq": move.seq,
                "filament_mm": -length,
                "motor_mm": length,
            }

    a.feed.on_start = on_start
    reports_tick(rig, a)
    return rig, a, rig.module.swap_cfg.splitter_mm[0]


def test_prepare_refused_on_a_shared_head_rolls_back_behind_the_splitter_only(
    tmp_path: Path,
) -> None:
    rig, a, splitter = shared_push(tmp_path)
    with pytest.raises(ValueError, match="no material set"):
        run(rig, "ACEK_HEAD_PREPARE", {"UNIT": "a", "LANE": "2"})
    # back until the sensor clears (here the first roll back clears it), then splitter_mm more
    assert rollbacks(a) == [splitter, splitter]
    swap_speed = rig.module.swap_cfg.swap_speed
    assert [s for _l, m, _len, s, _mv in a.feed.started if m == "rollback"] == [swap_speed] * 2
    h = rig.adapter.hooked_lane(a, SHARED_LANE)
    assert rig.adapter.slot_of_head(0).current is None  # forgotten, as after a restart
    assert h.follow_wanted is False and h.parked_at_sensor is False
    assert (
        f"ace2k_u1: head 1: {ace2k_u1_material.FORGET_CAUSE}: its lane is not known now — "
        f"{ace2k_u1_material.FORGET_TAIL}"
    ) in rig.gcode.lines


def test_a_swap_after_a_refused_load_rolls_the_lane_back_no_further(tmp_path: Path) -> None:
    """a:2 refused on head 1 and rolled back behind the splitter: the print's T0 (a:1) finds
    the head empty with no lane known, hands it to a:1 and pushes it in, with no roll back."""
    rig, a = swap_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    a1, a2 = slot.lanes
    rig.adapter.set_current(slot, a2)  # a:2 at the head's sensor, its load refused
    rig.head_sensor.detected = True
    rig.module.material.back_out(a2)
    assert slot.current is None and not rig.head_sensor.detected
    refused = len(rollbacks(a))
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 0)
    assert len(rollbacks(a)) == refused  # 0 mm more for a:2, and no probe moved a lane
    assert slot.current is a1
    assert "ace2k_u1: head 1: a:1 in (0 s)" in rig.gcode.lines


def test_a_u1_load_refused_on_a_shared_head_rolls_back_behind_the_splitter_only(
    tmp_path: Path,
) -> None:
    rig, a, splitter = shared_push(tmp_path)
    with pytest.raises(RuntimeError, match="no material set"):
        rig.left._put_into_drive(SHARED_CH)
    assert rollbacks(a) == [splitter, splitter]
    assert rig.adapter.slot_of_head(0).current is None


def test_prepare_is_refused_while_the_u1_runs_a_flow_on_the_head(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    rig.adapter.ejector.u1_flow = lambda h: True
    with pytest.raises(ValueError, match="the U1 is loading/unloading head 2"):
        prepare(rig)
    assert a.feed.started == []


def test_prepare_is_refused_in_a_print_and_on_a_head_holding_filament(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    rig.left.runout_sensor[LONE_CH].detected = True
    with pytest.raises(ValueError, match="already holds filament"):
        prepare(rig)
    rig.left.runout_sensor[LONE_CH].detected = False
    rig.print_stats.state = "printing"
    with pytest.raises(ValueError):
        prepare(rig)
    assert a.feed.started == []


def loaded(rig: SimpleNamespace, ch: int) -> None:
    """The U1 holds the head of the left feed's channel ch loaded (its load finished)."""
    rig.left.channel_state[ch] = "load_finish"  # set as is: no hook of the adapter runs


def test_an_eject_of_a_lane_with_no_material_loaded_in_its_head_is_refused(tmp_path: Path) -> None:
    # Loaded through the head's gear, its eject would need the U1's unload, which heats the head,
    # and a lane with no material set has no temperature.
    rig, a = lone(tmp_path)
    rig.left.runout_sensor[LONE_CH].detected = True
    loaded(rig, LONE_CH)
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "3", "WAIT": "0"})
    assert str(err.value) == (
        "ace2k_u1: lane a:3 eject refused: no material set — set it with Edit spool"
    )
    assert a.feed.started == []


def test_an_eject_of_a_lane_with_no_material_out_of_its_head_rolls_it_back(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)  # the head's sensor is clear: the filament is in the tube
    run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "3", "WAIT": "0"})
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert [mode for _l, mode, *_ in a.feed.started] == ["unload"]


def test_an_eject_of_a_tip_at_the_sensor_with_no_material_unloads_it_cold(tmp_path: Path) -> None:
    # A tip at the head's sensor, not loaded: the probe finds it free, and the unit rolls it back.
    rig, a = lone(tmp_path)
    rig.sensor_clears = False  # the eject's own roll backs end at once
    rig.left.runout_sensor[LONE_CH].detected = True
    run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "3", "WAIT": "0"})
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert [mode for _l, mode, *_ in a.feed.started] == ["rollback", "unload"]


def test_an_eject_held_by_the_head_with_no_material_stops_before_heating(tmp_path: Path) -> None:
    # A tip at the sensor, not known loaded, that the probe finds held by the head's gear: the
    # cold feed back relaxes it, then the eject ends before the U1's unload.
    rig, a = lone(tmp_path)
    rig.sensor_clears = False  # the eject's own roll backs end at once
    rig.left.runout_sensor[LONE_CH].detected = True
    a.lanes[LONE_LANE].update(rest=False, pushed=True)  # the plunger moved: held by the gear
    run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "3", "WAIT": "0"})
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert [mode for _l, mode, *_ in a.feed.started] == ["rollback", "feed"]  # probe, feed back
    a.feed.started[-1][4].event = {"kind": "blocked", "mode": "feeding", "motor_mm": 50.0}
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert [mode for _l, mode, *_ in a.feed.started] == ["rollback", "feed"]
    # the failure said, and no U1 unload ran (it would fail with another reason)
    failed = "ace2k_u1: lane a:3 eject failed: no material set — set it with Edit spool"
    assert any(line.startswith(failed) for line in rig.gcode.lines)


def shared(tmp_path: Path, current: int) -> tuple[SimpleNamespace, FakeAce2k]:
    """a:2 in its bay with no material set; lane `current` of a:1/a:2 is known in head 1, whose
    sensor sees filament and which the U1 holds loaded."""
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    a.link_proven = True
    a.lanes[SHARED_LANE]["insert"] = True
    h = rig.adapter.hooked_lane(a, current)
    rig.adapter.set_current(rig.adapter.slot_of_head(h.head), h)
    rig.left.runout_sensor[SHARED_CH].detected = True
    loaded(rig, SHARED_CH)
    return rig, a


def test_an_eject_of_a_lane_parked_outside_its_shared_head_goes_on(tmp_path: Path) -> None:
    rig, a = shared(tmp_path, current=0)  # a:1 is in head 1, a:2 waits in the tube
    run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "2", "WAIT": "0"})
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert [mode for _l, mode, *_ in a.feed.started] == ["unload"]


def test_an_eject_of_the_lane_loaded_in_its_shared_head_with_no_material_is_refused(
    tmp_path: Path,
) -> None:
    rig, a = shared(tmp_path, current=SHARED_LANE)
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "2", "WAIT": "0"})
    assert str(err.value) == (
        "ace2k_u1: lane a:2 eject refused: no material set — set it with Edit spool"
    )
    assert a.feed.started == []


def test_a_lane_loaded_in_a_shared_head_with_no_lane_known_is_refused(tmp_path: Path) -> None:
    # After a restart no lane is known in head 1: a:2 loaded there is still refused up front.
    rig, a = shared(tmp_path, current=SHARED_LANE)
    rig.adapter.set_current(rig.adapter.slot_of_head(0), None)
    with pytest.raises(ValueError, match="eject refused: no material set"):
        run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "2", "WAIT": "0"})


def test_the_status_says_which_lanes_an_eject_refuses_for_their_material(tmp_path: Path) -> None:
    rig, _a = shared(tmp_path, current=SHARED_LANE)
    spools(rig, {"a:1": EIGHT["a:1"]})
    lanes = rig.module.get_status()["lanes"]
    assert lanes["a:2"]["eject_no_material"] is True  # loaded in head 1, no material set
    assert lanes["a:1"]["eject_no_material"] is False  # its material set
    assert lanes["a:3"]["eject_no_material"] is False  # no material, but its head is empty
    rig.left.channel_state[SHARED_CH] = "preload_finish"  # at the sensor, not loaded
    assert rig.module.get_status()["lanes"]["a:2"]["eject_no_material"] is False
    loaded(rig, SHARED_CH)
    rig.left.runout_sensor[SHARED_CH].detected = False  # the head ran out
    assert rig.module.get_status()["lanes"]["a:2"]["eject_no_material"] is False


def test_a_planned_start_with_a_lane_whose_material_went_away_is_refused(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    del rig.module.spool_store.records["a:1"]  # T0's lane: its record removed after the plan
    with pytest.raises(ValueError, match="T0: lane a:1: no material set"):
        start = Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE")
        rig.gcode.commands["SDCARD_PRINT_FILE"](start)


def test_the_status_says_which_lanes_have_their_material_set(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    spools(rig, {"a:1": EIGHT["a:1"]})
    lanes = rig.module.get_status()["lanes"]
    assert lanes["a:1"]["material_set"] is True
    assert lanes["a:2"]["material_set"] is False


def test_a_lone_heads_real_setting_sets_its_lanes_material(tmp_path: Path) -> None:
    # The head's setting written by the page's Edit spool, the tag still pending after a restart
    # and no record: the lane's material is the setting, as the page shows it.
    rig, a = lone(tmp_path)
    head_type(rig, 1, "PLA")
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    assert rig.module.material.defined(h) is True
    assert rig.module.material.refusal(h) is None
    assert rig.module.spool_lanes.material(h) == "PLA"
    lanes = rig.module.get_status()["lanes"]
    assert lanes["a:3"]["material_set"] is True
    rig.left.runout_sensor[LONE_CH].detected = True
    loaded(rig, LONE_CH)
    assert rig.module.get_status()["lanes"]["a:3"]["eject_no_material"] is False
    assert rig.module.material.eject_refusal(h) is None
    run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "3", "WAIT": "0"})  # not refused up front


def written(rig: SimpleNamespace, head: int, kind: str) -> None:
    """The head's setting written on the U1 (the page's Edit spool, the screen), seen by a tick
    before and after."""
    tick(rig)
    head_type(rig, head, kind)
    tick(rig)


def test_the_newer_of_a_lone_heads_setting_and_the_lanes_own_spool_speaks(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")  # there since Klipper started, the bay full
    assert rig.module.spool_lanes.material(h) == "PLA"
    # a record set from the console, not written on the head: newer, so it speaks
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PETG", "COLOR": "ffffff"})
    assert rig.module.spool_lanes.material(h) == "PETG"
    written(rig, 1, "ABS")  # the head written since, for the spool in the bay
    assert rig.module.spool_lanes.material(h) == "ABS"
    # the first read since Klipper started, the bay full since: the spool that was there, as old
    # as the setting, so the operator's setting still speaks
    petg = {"material": "PETG", "color_rgba": "FF0000FF"}
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0001", source="tag", record=petg)
    tick(rig)
    assert rig.module.spool_lanes.material(h) == "ABS"
    # a read of another tag, not written on the head: newer, so it speaks
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0002", source="tag", record=petg)
    tick(rig)
    assert rig.module.spool_lanes.lane_spool(h) == ("a:3", "#ff0000", "PETG", False)


def edit_spool_on_head(rig: SimpleNamespace, head: int, kind: str) -> None:
    """The page's Edit spool or Define on a head: the U1's SET_PRINT_FILAMENT_CONFIG, wrapped."""
    sent: list[object] = []
    if "SET_PRINT_FILAMENT_CONFIG" not in rig.gcode.commands:
        rig.gcode.register_command("SET_PRINT_FILAMENT_CONFIG", sent.append)  # the U1's own
        rig.module.wraps.wrap_head_setting(rig.gcode)
        rig.sent_settings = sent
    params = {"CONFIG_EXTRUDER": str(head), "VENDOR": "Generic", "FILAMENT_TYPE": kind}
    rig.gcode.commands["SET_PRINT_FILAMENT_CONFIG"](Gcmd(params, "SET_PRINT_FILAMENT_CONFIG"))
    head_type(rig, head, kind)
    assert rig.sent_settings  # the U1's own command ran


def test_a_lone_heads_setting_left_from_before_does_not_count_for_a_new_spool(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")
    tick(rig)
    assert rig.module.material.defined(h) is True
    a.lanes[LONE_LANE]["insert"] = False  # the spool taken out
    tick(rig)
    assert rig.module.get_status()["lanes"]["a:3"]["material_set"] is False
    a.lanes[LONE_LANE]["insert"] = True  # a new spool, no tag read yet
    for _ in range(3):
        tick(rig)
    assert rig.module.material.defined(h) is False  # the old spool's setting does not count
    rig.left.runout_sensor[LONE_CH].detected = True
    with pytest.raises(ValueError, match="already holds filament"):
        prepare(rig)
    rig.left.runout_sensor[LONE_CH].detected = False
    edit_spool_on_head(rig, 1, "PLA")  # Define from the page: the same value, written for it
    assert rig.module.material.defined(h) is True
    assert rig.module.spool_lanes.material(h) == "PLA"


def test_a_setting_set_with_the_bay_empty_does_not_count_for_the_spool_put_in_next(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    a.lanes[LONE_LANE]["insert"] = False
    tick(rig)
    written(rig, 1, "PLA")  # on the screen, with no spool in the bay
    edit_spool_on_head(rig, 1, "PLA")  # or by a command, with no spool in the bay
    a.lanes[LONE_LANE]["insert"] = True
    for _ in range(3):
        tick(rig)
    assert rig.module.material.defined(h) is False
    written(rig, 1, "PETG")  # changed on the screen with the spool in
    assert rig.module.spool_lanes.material(h) == "PETG"


def test_a_colour_only_command_does_not_make_the_setting_the_spools(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    tick(rig)
    mark = h.setting_mark
    handler = rig.module.wraps._head_setting_handler(lambda _g: None)
    handler(Gcmd({"CONFIG_EXTRUDER": "1", "FILAMENT_COLOR_RGBA": "123456FF"}, "X"))
    assert h.setting_mark == mark


def test_a_tag_read_again_does_not_outrank_a_setting_written_since(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    tick(rig)
    petg = {"material": "PETG", "color_rgba": "FF0000FF"}
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0001", source="tag", record=petg)
    tick(rig)
    written(rig, 1, "PLA")  # the operator's setting, after the read
    assert rig.module.spool_lanes.material(h) == "PLA"
    a.lanes[LONE_LANE]["tag"] = dict(state="searching", uid=None)
    tick(rig)
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0001", source="tag", record=petg)
    tick(rig)  # the same tag read again
    assert rig.module.spool_lanes.material(h) == "PLA"
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0003", source="tag", record=petg)
    tick(rig)  # another tag
    assert rig.module.spool_lanes.material(h) == "PETG"


def test_a_colour_change_on_the_screen_makes_the_setting_the_spools(tmp_path: Path) -> None:
    # A new spool set on the screen with the same type and another colour.
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")
    tick(rig)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PETG", "COLOR": "ffffff"})
    assert rig.module.spool_lanes.material(h) == "PETG"
    rig.task_config.status["filament_color_rgba"][1] = "123456FF"
    tick(rig)
    assert rig.module.spool_lanes.lane_spool(h) == ("a:3", "#123456", "PLA", False)


def test_a_colour_only_command_is_taken_in_and_not_seen_as_a_change(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")
    tick(rig)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PETG", "COLOR": "ffffff"})

    def colour_only(gcmd: Gcmd) -> None:
        rig.task_config.status["filament_color_rgba"][1] = gcmd.params["FILAMENT_COLOR_RGBA"]

    rig.gcode.register_command("SET_PRINT_FILAMENT_CONFIG", colour_only)
    rig.module.wraps.wrap_head_setting(rig.gcode)
    params = {"CONFIG_EXTRUDER": "1", "FILAMENT_COLOR_RGBA": "123456FF"}
    rig.gcode.commands["SET_PRINT_FILAMENT_CONFIG"](Gcmd(params, "SET_PRINT_FILAMENT_CONFIG"))
    tick(rig)
    assert rig.module.spool_lanes.material(h) == "PETG"  # the record still speaks


@pytest.mark.parametrize("kind", ["NONE", ""])
def test_a_command_that_clears_the_type_never_takes_the_setting(tmp_path: Path, kind: str) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    tick(rig)
    mark = h.setting_mark
    rig.module.spool_lanes.setting_set(1, kind)
    assert h.setting_mark == mark


def test_a_colour_only_spool_write_does_not_make_the_setting_the_spools(tmp_path: Path) -> None:
    # set_head_spool for a type the U1 does not know writes the colour alone.
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "WOOD9", "COLOR": "ffffff"})
    mark = h.setting_mark
    assert rig.module.swapper.set_head_spool(h, new_lane=False) is None
    assert h.setting_mark == mark


def test_a_run_out_rest_tells_its_material_by_the_heads_setting_once_the_record_went(
    tmp_path: Path,
) -> None:
    # The spool ran out: its rest is in the head, the bay reads empty, so the record goes. The
    # head's setting, written for that spool, still tells what the rest is.
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PLA", "COLOR": "ffffff"})
    tick(rig)
    head_type(rig, 1, "PLA")  # the print's start writes the head from the record
    tick(rig)
    rig.left.runout_sensor[LONE_CH].detected = True
    loaded(rig, LONE_CH)
    a.lanes[LONE_LANE]["insert"] = False
    tick(rig, 2.0)
    tick(rig)
    assert "a:3" not in rig.module.spool_store.records
    assert rig.module.material.defined(h) is True
    lanes = rig.module.get_status()["lanes"]
    assert lanes["a:3"]["material_set"] is True and lanes["a:3"]["eject_no_material"] is False


@pytest.mark.parametrize("read", [False, True])
def test_a_tag_forget_or_read_drops_a_lone_heads_setting(tmp_path: Path, read: bool) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")  # written from the tag before
    pla = {"material": "PLA", "color_rgba": "FF0000FF"}
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0001", source="tag", record=pla)
    tick(rig)
    run(rig, "ACEK_TAG_FORGET", dict({"UNIT": "a", "LANE": "3"}, **({"READ": "1"} if read else {})))
    assert h.setting_mark is None
    if not read:
        assert rig.module.material.defined(h) is False  # the forgotten tag and its setting


def test_an_empty_bay_has_no_material_set(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PLA", "COLOR": "ffffff"})
    head_type(rig, 1, "PLA")
    a.lanes[LONE_LANE]["insert"] = False  # no tick: the record is still there
    assert rig.module.get_status()["lanes"]["a:3"]["material_set"] is False


def test_a_shared_heads_setting_written_for_the_lane_sets_its_material(tmp_path: Path) -> None:
    rig, a = shared(tmp_path, current=SHARED_LANE)
    h = rig.adapter.hooked_lane(a, SHARED_LANE)
    head_type(rig, 0, "PLA")
    rig.adapter.slot_of(h).setting_for = h
    assert rig.module.material.defined(h) is True
    assert rig.module.get_status()["lanes"]["a:2"]["material_set"] is True
    assert rig.module.material.eject_refusal(h) is None


def test_the_status_reads_the_heads_settings_once_and_logs_a_failure_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _a = lone(tmp_path)
    u1 = rig.module.u1
    calls = {"one": 0, "all": 0}
    one, every = u1.head_setting, u1.head_settings

    def count_one(head: int) -> object:
        calls["one"] += 1
        return one(head)

    def count_all() -> object:
        calls["all"] += 1
        return every()

    u1.head_setting, u1.head_settings = count_one, count_all
    rig.module.get_status()
    assert calls == {"one": 0, "all": 1}
    u1.head_setting, u1.head_settings = one, every
    status = rig.task_config.get_status

    def fails(_now: float) -> NoReturn:
        raise RuntimeError("unreadable")

    rig.task_config.get_status = fails
    for _ in range(3):
        assert u1.head_settings() is None
    assert caplog.text.count("the heads' settings unreadable") == 1
    rig.task_config.get_status = status
    assert u1.head_settings() is not None
    rig.task_config.get_status = fails
    u1.head_settings()
    assert caplog.text.count("the heads' settings unreadable") == 2


@pytest.mark.parametrize("setting", ["NONE", ""])
def test_a_lone_heads_unset_setting_sets_no_material(tmp_path: Path, setting: str) -> None:
    rig, a = lone(tmp_path)
    head_type(rig, 1, setting)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    assert rig.module.material.defined(h) is False
    assert rig.module.get_status()["lanes"]["a:3"]["material_set"] is False


def test_a_shared_heads_setting_for_no_lane_sets_no_material(tmp_path: Path) -> None:
    rig, a = shared(tmp_path, current=SHARED_LANE)
    head_type(rig, 0, "PLA")  # written for no lane (setting_for None)
    h = rig.adapter.hooked_lane(a, SHARED_LANE)
    assert rig.adapter.slot_of(h).setting_for is None
    assert rig.module.material.defined(h) is False
    assert rig.module.get_status()["lanes"]["a:2"]["material_set"] is False
    with pytest.raises(ValueError, match="eject refused: no material set"):
        run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "2", "WAIT": "0"})


def test_a_planned_start_takes_a_lone_heads_setting_as_its_lanes_material(
    tmp_path: Path,
) -> None:
    rig, by_name = tools_rig(tmp_path)
    set_plan(rig)
    by_name["a"].lanes[2]["insert"] = True
    del rig.module.spool_store.records["a:3"]  # T1's lane a:3 feeds head 2 alone
    start = Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE")
    with pytest.raises(ValueError, match="lane a:3: no material set"):
        rig.gcode.commands["SDCARD_PRINT_FILE"](start)
    written(rig, 1, "PLA")
    assert len(print_start(rig)) == 1  # the U1's own start ran


def test_the_plan_set_checks_the_material_from_the_spools_it_read(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)
    set_plan(rig)
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.get_status()["plan"]["tools"])}

    def unused(*_args: object) -> NoReturn:
        raise AssertionError("each lane's status and spool are read once")

    rig.module.material.defined = unused
    del rig.module.spool_store.records["a:1"]
    with pytest.raises(ValueError, match="a:1 has no material set"):
        run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))


def test_a_run_out_lane_still_loaded_keeps_its_heads_setting_until_it_leaves_the_head(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")
    tick(rig)
    rig.left.runout_sensor[LONE_CH].detected = True
    loaded(rig, LONE_CH)
    a.lanes[LONE_LANE]["insert"] = False  # the spool ran out, its rest in the head
    tick(rig)
    # one rule for the plan, the page and the gate: the rest is PLA
    assert rig.module.spool_lanes.lane_spool(h) == ("a:3", "#ffffff", "PLA", True)
    assert rig.module.material.defined(h) is True
    assert rig.module.get_status()["lanes"]["a:3"]["material_set"] is True
    assert rig.module.material.eject_refusal(h) is None  # the rest can be ejected
    rig.left.channel_state[LONE_CH] = "unload_finish"  # the head unloaded
    rig.left.runout_sensor[LONE_CH].detected = False
    tick(rig)
    assert h.setting_mark is not None  # one tick out of the head is no proof
    for _ in range(12):  # out of it for SETTING_LEAVE_S
        tick(rig)
    assert h.setting_mark is None
    assert rig.module.material.defined(h) is False


def test_a_new_spool_put_in_while_the_rest_is_loaded_does_not_take_the_setting(
    tmp_path: Path,
) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")
    tick(rig)
    rig.left.runout_sensor[LONE_CH].detected = True
    loaded(rig, LONE_CH)
    a.lanes[LONE_LANE]["insert"] = False
    tick(rig)
    assert h.setting_mark is not None  # kept for the rest in the head
    a.lanes[LONE_LANE]["insert"] = True  # a new spool behind it
    tick(rig)
    assert h.setting_mark is None


def test_a_change_in_the_first_tick_after_the_insertion_counts(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    a.lanes[LONE_LANE]["insert"] = False
    tick(rig)
    a.lanes[LONE_LANE]["insert"] = True
    tick(rig)  # the insertion seen, the setting looked at with the bay full
    head_type(rig, 1, "PETG")  # set on the screen for it
    tick(rig)
    assert rig.module.spool_lanes.material(h) == "PETG"


def test_a_head_sensor_blip_never_ends_a_run_out_rests_setting(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")
    tick(rig)
    sensor = rig.left.runout_sensor[LONE_CH]
    sensor.detected = True
    loaded(rig, LONE_CH)
    a.lanes[LONE_LANE]["insert"] = False  # run out, its rest in the head
    for blip in (False, True) * 8:  # the sensor blips clear on every other tick
        sensor.detected = blip
        tick(rig, dt=0.5)
    assert h.setting_mark is not None
    assert rig.module.spool_lanes.material(h) == "PLA"


def test_a_typed_write_on_a_run_out_rest_is_its_setting(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PLA", "COLOR": "ffffff"})
    rig.left.runout_sensor[LONE_CH].detected = True
    loaded(rig, LONE_CH)
    a.lanes[LONE_LANE]["insert"] = False
    tick(rig)
    assert rig.module.spool_lanes.material(h) == "PLA"  # the record
    edit_spool_on_head(rig, 1, "PETG")  # Define for the rest in the head
    assert rig.module.spool_lanes.material(h) == "PETG"


def test_a_restored_read_then_another_tag_outranks_the_setting(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    head_type(rig, 1, "PLA")
    petg = {"material": "PETG", "color_rgba": "FF0000FF"}
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0001", source="restored", record=petg)
    tick(rig)  # the kept read at the start: the spool that was there
    assert rig.module.spool_lanes.material(h) == "PLA"
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0001", source="tag", record=petg)
    tick(rig)  # the same spool read live
    assert rig.module.spool_lanes.material(h) == "PLA"
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0002", source="tag", record=petg)
    tick(rig)  # another spool
    assert rig.module.spool_lanes.material(h) == "PETG"


def test_a_first_read_after_the_bay_was_seen_empty_outranks_the_setting(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    a.lanes[LONE_LANE]["insert"] = False
    tick(rig)
    a.lanes[LONE_LANE]["insert"] = True
    tick(rig)
    written(rig, 1, "PLA")  # set for the new spool
    petg = {"material": "PETG", "color_rgba": "FF0000FF"}
    a.lanes[LONE_LANE]["tag"] = dict(state="read", uid="0001", source="tag", record=petg)
    tick(rig)
    assert rig.module.spool_lanes.material(h) == "PETG"


def test_the_ticks_decisions_read_the_bays_of_this_tick(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    h = rig.adapter.hooked_lane(a, LONE_LANE)
    a.lanes[LONE_LANE]["insert"] = False
    tick(rig)
    seen = []
    on_tick = rig.adapter.on_tick
    rig.adapter.on_tick = lambda now: (seen.append(h.bay_full), on_tick(now))
    a.lanes[LONE_LANE]["insert"] = True
    tick(rig)
    assert seen and seen[0] is True  # the bay watch ran before the host's tick


def test_one_status_reads_each_lanes_load_once(tmp_path: Path) -> None:
    rig, a = lone(tmp_path)
    a.lanes[LONE_LANE]["insert"] = False  # an empty bay asks for its load
    ejector = rig.adapter.ejector
    calls: list[object] = []
    real = ejector.known_loaded

    def counted(h: Hooked) -> bool:
        calls.append(h.key)
        return real(h)

    ejector.known_loaded = counted
    rig.module.get_status()
    assert sorted(calls) == sorted(h.key for h in rig.adapter.hooked)
