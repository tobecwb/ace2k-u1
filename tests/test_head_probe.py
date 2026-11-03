"""ACEK_HEAD_PROBE: a print start's probe of a shared head, or a swap's push into it, alone,
outside a print, for the bench."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import ace2k_u1_probe
import fakes
import pytest
from ace2k_u1_swap import SwapState
from test_probe import end, free_lane, modes, probe_rig, reports_tick
from test_tools import run

if TYPE_CHECKING:
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked, Slot
    from fakes import FakeAce2k, FakeMove


def test_a_set_sensor_is_probed_and_the_lane_found_named_the_heads(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)  # head 1 = a:1+a:2, its sensor set, cold
    slot = rig.adapter.slot_of_head(0)
    free = free_lane(0)

    def on_start(move: FakeMove) -> None:
        free(move)
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True  # the gear holds a:2
        elif move.lane == 1 and move.mode == "feed":
            a.lanes[1]["pushed"] = False
            end(move, 20.0)

    a.feed.on_start = on_start
    reports_tick(rig, a)
    armed = []
    rig.adapter.follow.arm = lambda h, *args, **kwargs: armed.append(h)
    gcmd = run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})
    assert modes(a)[:3] == [(0, "rollback"), (0, "feed"), (1, "rollback")]
    assert all(speed == 50.0 for _l, _m, _len, speed, _mv in a.feed.started)  # probe_speed
    assert slot.current is slot.lanes[1]
    assert armed == [slot.lanes[1]]  # the probe stopped it: armed again
    assert "ace2k_u1: head 1: probe: a:2 is in the head" in rig.gcode.lines
    assert gcmd.lines == ["ace2k_u1: head 1: a:2 named the head's, following"]


def test_on_host_interface_17_the_probes_roll_backs_count_the_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each roll back of the probe asks ace2k's budget with reverse (the tail the unit runs once
    the filament leaves the motor); each push does not."""
    monkeypatch.setattr(fakes.FakeFeedApi, "API_VERSION", 17)
    rig, a = probe_rig(tmp_path)
    free = free_lane(0)

    def on_start(move: FakeMove) -> None:
        free(move)
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True  # the gear holds a:2
        elif move.lane == 1 and move.mode == "feed":
            a.lanes[1]["pushed"] = False
            end(move, 20.0)

    a.feed.on_start = on_start
    reports_tick(rig, a)
    rig.adapter.follow.arm = lambda h, *args, **kwargs: None
    run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})
    moves = [
        (lane, mode, (lane, length, speed)) for lane, mode, length, speed, _m in a.feed.started
    ]
    assert {mode for _l, mode, _w in moves} == {"rollback", "feed"}
    assert a.feed.waits == [w for _l, _m, w in moves]
    assert a.feed.reverse_waits == [w for _l, mode, w in moves if mode == "rollback"]


def _probe_with_push_back_ending(tmp_path: Path, kind: str) -> tuple[SimpleNamespace, FakeAce2k]:
    """a:1 rolls back freely and is pushed back; a:2's plunger goes taut after 65.4 mm and its
    push back ends kind."""
    rig, a = probe_rig(tmp_path)
    free = free_lane(0)
    opened = {}

    def on_start(move: FakeMove) -> None:
        free(move)
        if move.lane == 1 and move.mode == "rollback":
            a.lanes[1]["pushed"] = True
            opened[1] = move
        elif move.lane == 1 and move.mode == "feed":
            a.lanes[1]["pushed"] = False
            end(move, 56.8, kind=kind)

    a.feed.on_start = on_start
    stop = a.feed.stop

    def stopped(lane: int) -> None:
        stop(lane)
        move = opened.pop(lane, None)
        if move is not None:
            end(move, -65.4, kind="stopped")

    a.feed.stop = stopped
    reports_tick(rig, a)
    rig.adapter.follow.arm = lambda h, *args, **kwargs: None
    return rig, a


def test_a_push_back_ending_blocked_is_the_tip_re_seated(tmp_path: Path) -> None:
    rig, a = _probe_with_push_back_ending(tmp_path, "blocked")
    slot = rig.adapter.slot_of_head(0)
    gcmd = run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})
    assert slot.current is slot.lanes[1]
    assert gcmd.lines == ["ace2k_u1: head 1: a:2 named the head's, following"]


def test_a_push_back_ending_stuck_still_fails_the_probe(tmp_path: Path) -> None:
    rig, a = _probe_with_push_back_ending(tmp_path, "stuck")
    with pytest.raises(ValueError, match="push back ended stuck"):
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})


def test_a_probe_that_finds_the_heads_own_lane_says_so(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])

    def on_start(move: FakeMove) -> None:
        if move.lane == 0 and move.mode == "rollback":
            a.lanes[0]["pushed"] = True
        elif move.lane == 0 and move.mode == "feed":
            a.lanes[0]["pushed"] = False
            end(move, 20.0)
        else:
            end(move, -100.0 if move.mode == "rollback" else 100.0)

    a.feed.on_start = on_start
    reports_tick(rig, a)
    rig.adapter.follow.arm = lambda h, *args, **kwargs: None
    gcmd = run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})
    assert gcmd.lines == ["ace2k_u1: head 1: a:1 was the head's already, following"]


def test_an_empty_sensor_pushes_the_lane_named_as_a_swap_does(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.head_sensor.detected = False

    def on_start(move: FakeMove) -> None:
        rig.head_sensor.detected = True  # the push reaches the head's sensor

    a.feed.on_start = on_start
    armed = []
    rig.adapter.follow.arm = lambda h, *args, **kwargs: armed.append(h)
    gcmd = run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1", "UNIT": "a", "LANE": "2"})
    assert ("stop", 0) in a.feed.log  # the head's other lane stopped before the push
    _lane, mode, _length, speed, _m = a.feed.started[0]
    assert (_lane, mode, speed) == (1, "feed", 70.0)  # swap_speed
    assert slot.current is slot.lanes[1]
    assert armed == [slot.lanes[1]]
    assert gcmd.lines == ["ace2k_u1: head 1: a:2 pushed to its sensor, following"]


def refused(rig: SimpleNamespace, a: FakeAce2k, params: dict[str, str], reason: str) -> None:
    with pytest.raises(ValueError, match=f"head 1 probe refused: {reason}"):
        run(rig, "ACEK_HEAD_PROBE", params)
    assert modes(a) == []


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_refused_during_a_print(tmp_path: Path, state: str) -> None:
    rig, a = probe_rig(tmp_path)
    rig.print_stats.state = state
    refused(rig, a, {"HEAD": "1"}, "a print is printing or paused")


def test_refused_with_a_swap_pending(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.module.swapper.swap_pending = SwapState(
        slot, slot.current, slot.lanes[-1], None, step="free"
    )
    refused(rig, a, {"HEAD": "1"}, "a swap is pending")


def test_refused_on_a_head_one_lane_feeds(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)  # head 2 = a:3 alone
    with pytest.raises(ValueError, match="head 2 probe refused: its head is fed by one lane alone"):
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "2"})
    assert modes(a) == []


@pytest.mark.parametrize("temp, target", [(60.0, 0.0), (30.0, 220.0)])
def test_refused_on_a_hot_or_heating_head(tmp_path: Path, temp: float, target: float) -> None:
    rig, a = probe_rig(tmp_path)
    ext = rig.module.printer.objects["extruder"]
    ext.heater.temp, ext.heater.target = temp, target
    refused(rig, a, {"HEAD": "1"}, "it is heating or at 60 °C or more: let it cool")
    assert not any(line.startswith("M104") for line in rig.gcode.lines)  # never touched


def test_refused_with_a_lane_named_and_the_sensor_set(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    refused(
        rig,
        a,
        {"HEAD": "1", "UNIT": "a", "LANE": "1"},
        "its sensor sees filament: probe it without LANE",
    )


def test_refused_with_the_sensor_empty_and_no_lane(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    rig.head_sensor.detected = False
    refused(rig, a, {"HEAD": "1"}, "its sensor sees no filament: name the LANE to push")


def test_refused_with_a_lane_of_another_head(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    rig.head_sensor.detected = False
    refused(rig, a, {"HEAD": "1", "UNIT": "a", "LANE": "3"}, "LANE is not one of its lanes")


def test_refused_with_the_sensor_unread(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)

    def unread(h: Hooked) -> NoReturn:
        raise RuntimeError("no switch")

    rig.adapter.head_set = unread
    refused(rig, a, {"HEAD": "1"}, "head 1's sensor cannot be read")


def test_refused_with_the_heads_temperature_unread(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)

    def unread(slot: Slot) -> NoReturn:
        raise ace2k_u1_probe.ProbeError("head 1: its temperature cannot be read")

    rig.module.probe.is_cold = unread
    refused(rig, a, {"HEAD": "1"}, "head 1: its temperature cannot be read")


def test_refused_with_no_shared_head_hooked(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    rig.module.probe = None
    refused(rig, a, {"HEAD": "1"}, "no head fed by several lanes is hooked")


def test_a_probe_that_fails_is_the_commands_error(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    # a:1 and a:2 both roll back freely: no lane holds the head
    a.feed.on_start = lambda move: end(move, -100.0 if move.mode == "rollback" else 100.0)
    with pytest.raises(ValueError, match="no lane of the head holds its filament"):
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})


def test_a_push_that_fails_gives_the_head_back_to_its_lane(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    slot.lanes[0].armed = True  # a:1 followed
    rig.head_sensor.detected = False
    armed = []
    rig.adapter.follow.arm = lambda h, *args, **kwargs: armed.append(h)
    rig.module.probe.push_in = lambda slot, h, speed, budget=None: "timeout"
    with pytest.raises(ValueError, match="a:2 did not reach the head \\(timeout\\)"):
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1", "UNIT": "a", "LANE": "2"})
    assert slot.current is slot.lanes[0]
    assert armed == [slot.lanes[0]]  # following again, as before
    assert "ace2k_u1: head 1: the push failed, the head is back to a:1" in rig.gcode.lines


def test_a_push_that_fails_with_no_lane_before_leaves_the_lane_named(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, None)
    rig.head_sensor.detected = False
    armed = []
    rig.adapter.follow.arm = lambda h, *args, **kwargs: armed.append(h)
    rig.module.probe.push_in = lambda slot, h, speed, budget=None: "timeout"
    with pytest.raises(ValueError, match="did not reach the head"):
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1", "UNIT": "a", "LANE": "2"})
    assert slot.current is slot.lanes[1]
    assert armed == []
    assert "ace2k_u1: head 1: the push failed; a:2 stays the head's lane" in rig.gcode.lines


def test_a_probe_that_fails_arms_the_heads_lane_again(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    slot.lanes[0].armed = True
    armed = []
    rig.adapter.follow.arm = lambda h, *args, **kwargs: armed.append(h)
    # a:1 and a:2 both roll back freely: no lane holds the head
    a.feed.on_start = lambda move: end(move, -100.0 if move.mode == "rollback" else 100.0)
    with pytest.raises(ValueError, match="no lane of the head holds its filament"):
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})
    assert armed == [slot.lanes[0]]


def test_a_push_refused_for_an_empty_bay_moves_nothing(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    rig.head_sensor.detected = False
    a.lanes[1]["insert"] = False
    refused(rig, a, {"HEAD": "1", "UNIT": "a", "LANE": "2"}, "its bay is empty")


def test_lane_zero_is_out_of_range(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    with pytest.raises(ValueError, match="LANE missing or out of range"):
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1", "LANE": "0"})
    assert modes(a) == []


def test_refused_with_an_eject_running_on_a_lane_of_the_head(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.ejector.eject_jobs[slot.lanes[1].key] = object()
    refused(rig, a, {"HEAD": "1"}, "an eject is running on one of its lanes")


@pytest.mark.parametrize("mode", ["feeding", "rolling_back", "unloading", "assisting"])
def test_refused_while_a_lane_of_the_head_moves(tmp_path: Path, mode: str) -> None:
    rig, a = probe_rig(tmp_path)
    a.lanes[1]["mode"] = mode
    refused(rig, a, {"HEAD": "1"}, "one of its lanes is moving: wait for it to end")


def test_a_following_lane_is_at_rest_for_the_probe(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    a.lanes[0]["mode"] = "following"
    assert rig.module.bench.moving_refusal(rig.adapter.slot_of_head(0)) is None


def test_the_status_says_whether_the_shared_head_is_cold_for_the_probe(tmp_path: Path) -> None:
    rig, _a = probe_rig(tmp_path)
    status = rig.module.get_status()
    assert status["probe_cold"][:2] == [True, None]  # head 1 is shared, head 2 is a:3 alone
    assert status["probe_max_temp"] == 60.0
    ext = rig.module.printer.objects["extruder"]
    ext.heater.temp = 60.0
    assert rig.module.get_status()["probe_cold"][0] is False
    ext.heater.temp, ext.heater.target = 25.0, 200.0
    assert rig.module.get_status()["probe_cold"][0] is False


# --- the refusals of the probe's and the push's own calls (characterisation) --------------------


def test_a_start_probe_refused_is_the_commands_error_and_the_lane_armed_again(
    tmp_path: Path,
) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    slot.lanes[0].armed = True
    armed = []
    rig.adapter.follow.arm = lambda h, *args, **kwargs: armed.append(h)

    def refuse(slot: Slot) -> NoReturn:
        raise RuntimeError("a follower did not end")

    rig.module.probe.which_lane = refuse
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})
    assert str(err.value) == "ace2k_u1: head 1: a follower did not end"
    assert armed == [slot.lanes[0]] and slot.current is slot.lanes[0]


def test_a_hand_over_refused_after_the_probe_is_the_commands_error(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot = rig.adapter.slot_of_head(0)
    rig.module.probe.which_lane = lambda slot: slot.lanes[1]

    def refuse(slot: Slot, h: Hooked) -> NoReturn:
        raise RuntimeError("the core refused")

    rig.adapter.head_watch.hand_head = refuse
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1"})
    assert str(err.value) == "ace2k_u1: head 1: the core refused"
    assert slot.current is None


def push_failing_with(rig: SimpleNamespace, exc: Exception) -> tuple[Slot, list[Hooked]]:
    slot = rig.adapter.slot_of_head(0)
    rig.adapter.set_current(slot, slot.lanes[0])
    slot.lanes[0].armed = True  # a:1 followed
    rig.head_sensor.detected = False
    armed = []
    rig.adapter.follow.arm = lambda h, *args, **kwargs: armed.append(h)

    def push_in(
        slot: Slot, h: Hooked, speed: float | None, budget: float | None = None
    ) -> NoReturn:
        raise exc

    rig.module.probe.push_in = push_in
    return slot, armed


def test_a_push_that_raises_a_probe_error_gives_the_head_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot, armed = push_failing_with(rig, ace2k_u1_probe.ProbeError("head 1: the lane in the way"))
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1", "UNIT": "a", "LANE": "2"})
    assert str(err.value) == "ace2k_u1: head 1: the lane in the way"
    assert slot.current is slot.lanes[0] and armed == [slot.lanes[0]]
    assert "ace2k_u1: head 1: the push failed, the head is back to a:1" in rig.gcode.lines


def test_a_push_that_is_refused_gives_the_head_back(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot, armed = push_failing_with(rig, RuntimeError("busy"))
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1", "UNIT": "a", "LANE": "2"})
    assert str(err.value) == "ace2k_u1: head 1: busy"
    assert slot.current is slot.lanes[0] and armed == [slot.lanes[0]]
    assert "ace2k_u1: head 1: the push failed, the head is back to a:1" in rig.gcode.lines


def test_a_hand_back_refused_after_a_failed_push_is_said_not_raised(tmp_path: Path) -> None:
    rig, a = probe_rig(tmp_path)
    slot, armed = push_failing_with(rig, RuntimeError("busy"))
    hand_head = rig.adapter.head_watch.hand_head

    def refuse_the_way_back(slot: Slot, h: Hooked) -> None:
        if h is slot.lanes[0]:
            raise RuntimeError("the core refused")
        hand_head(slot, h)

    rig.adapter.head_watch.hand_head = refuse_the_way_back
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_HEAD_PROBE", {"HEAD": "1", "UNIT": "a", "LANE": "2"})
    assert str(err.value) == "ace2k_u1: head 1: busy"
    assert "ace2k_u1: head 1: not given back to a:1: the core refused" in rig.gcode.lines
    assert slot.current is slot.lanes[1] and armed == []
