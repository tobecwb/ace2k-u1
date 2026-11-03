"""The U1's runout while an eject moves the lane.

On the bench (2026-10-24) the eject's roll back of a:2, loaded in shared head 1, cleared the
head's sensor; the U1's own runout then moved the channel from load_finish to preload_finish,
and the adapter's channel hook stopped the lane, which the eject counted as a failure. While an
eject moves the lane (its probe to its feed back, or the unit's unload) and the head's sensor
reads clear, that runout is the eject's own doing: the lane is not stopped. A flow of the U1
that starts on the head, or a sensor that still sees filament, still stops it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import fakes
import pytest
from test_tools import run, tools_rig

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from fakes import FakeAce2k

SHARED_CH = 1  # a:1 and a:2 feed head 1: channel 1 of the left feed
LANE = 1  # a:2
LONE_CH = 0  # a:3 feeds head 2 alone: channel 0 of the left feed
LONE_LANE = 2


def probing(
    tmp_path: Path, ch: int = SHARED_CH, lane: int = LANE, text: str = "2"
) -> tuple[SimpleNamespace, FakeAce2k]:
    """The lane loaded in its head by the U1 (load_finish), its eject's probe rolling back."""
    rig, by_name = tools_rig(tmp_path)
    fakes.give_materials(rig.module)
    a = by_name["a"]
    a.lanes[lane].update(insert=True, mode="idle", rest=True)
    h = rig.adapter.hooked_lane(a, lane)
    rig.adapter.set_current(rig.adapter.slot_of_head(h.head), h)
    rig.left.runout_sensor[ch].detected = True
    rig.left._set_channel_state(ch, "load_finish")
    run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": text, "WAIT": "0"})
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    # the probe's roll back (a head one lane feeds armed its follow at load_finish first)
    assert [m for _l, m, *_ in a.feed.started][-1] == "rollback"
    a.feed.stopped.clear()
    return rig, a


def end_probe_free(rig: SimpleNamespace, a: FakeAce2k) -> None:
    probe = [mv for _l, m, _len, _s, mv in a.feed.started if m == "rollback"][-1]
    probe.event = {"kind": "done", "mode": "rolling_back", "motor_mm": -10.0, "filament_mm": -9.0}
    rig.reactor.run_timers(rig.reactor.now + 10.0)


@pytest.mark.parametrize("state", ["preload_finish", "wait_insert"])
def test_the_u1s_runout_during_the_probe_does_not_stop_the_lane(tmp_path: Path, state: str) -> None:
    rig, a = probing(tmp_path)
    rig.left.runout_sensor[SHARED_CH].detected = False  # the probe took the tip out of the sensor
    rig.left._set_channel_state(SHARED_CH, state)  # the U1's runout
    assert a.feed.stopped == []
    end_probe_free(rig, a)
    assert [m for _l, m, *_ in a.feed.started][-1] == "unload"  # the eject went on to the bay


def test_on_a_head_one_lane_feeds_the_u1s_runout_does_not_stop_the_probe_either(
    tmp_path: Path,
) -> None:
    rig, a = probing(tmp_path, LONE_CH, LONE_LANE, "3")
    rig.left.runout_sensor[LONE_CH].detected = False
    rig.left._set_channel_state(LONE_CH, "preload_finish")
    assert a.feed.stopped == []


def test_the_u1s_runout_during_the_units_unload_does_not_stop_it(tmp_path: Path) -> None:
    rig, a = probing(tmp_path)
    end_probe_free(rig, a)
    assert [m for _l, m, *_ in a.feed.started][-1] == "unload"
    a.lanes[LANE]["mode"] = "unloading"
    rig.left._set_channel_state(SHARED_CH, "load_finish")
    a.feed.stopped.clear()
    rig.left.runout_sensor[SHARED_CH].detected = False
    rig.left._set_channel_state(SHARED_CH, "wait_insert")
    assert a.feed.stopped == []


def test_an_unload_entry_whose_lane_no_longer_unloads_lets_the_runout_stop_it(
    tmp_path: Path,
) -> None:
    """The unload's final event lost: its entry stays, but the lane's mode is not unloading."""
    rig, a = probing(tmp_path)
    end_probe_free(rig, a)
    a.lanes[LANE]["mode"] = "following"
    rig.left._set_channel_state(SHARED_CH, "load_finish")
    a.feed.stopped.clear()
    rig.left.runout_sensor[SHARED_CH].detected = False
    rig.left._set_channel_state(SHARED_CH, "preload_finish")
    assert a.feed.stopped == [LANE]


def test_a_u1_flow_starting_during_the_probe_still_stops_the_lane(tmp_path: Path) -> None:
    rig, a = probing(tmp_path)
    rig.left._set_channel_state(SHARED_CH, "load_prepare")
    assert a.feed.stopped == [LANE]


def test_a_change_with_the_heads_sensor_still_seeing_filament_stops_the_lane(
    tmp_path: Path,
) -> None:
    rig, a = probing(tmp_path)
    rig.left._set_channel_state(SHARED_CH, "preload_finish")  # not the probe's doing
    assert a.feed.stopped == [LANE]


def test_a_head_leaving_load_finish_with_no_eject_still_stops_the_lane(tmp_path: Path) -> None:
    rig, by_name = tools_rig(tmp_path)
    a = by_name["a"]
    h = rig.adapter.hooked_lane(a, LANE)
    rig.adapter.set_current(rig.adapter.slot_of_head(0), h)
    rig.left._set_channel_state(SHARED_CH, "load_finish")
    a.feed.stopped.clear()
    rig.left.runout_sensor[SHARED_CH].detected = False
    rig.left._set_channel_state(SHARED_CH, "preload_finish")
    assert a.feed.stopped == [LANE]


def unload_ends(rig: SimpleNamespace, a: FakeAce2k, lane: int, kind: str) -> None:
    """The unit's unload of the eject ends with kind, the bay read empty first."""
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == lane and m == "unload"]
    assert started, "the eject started no unload"
    a.lanes[lane].update(insert=False, mode="unloading")
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    a.lanes[lane]["mode"] = "idle"
    event = {"kind": kind, "mode": "unloading", "seq": started[-1].seq, "unit": "a"}
    event.update(motor_mm=1950.0, filament_mm=-1918.0)
    rig.adapter.on_feed_event(lane, event)
    rig.reactor.run_timers(rig.reactor.now + 1.0)


def test_an_eject_that_empties_the_bay_of_the_lane_in_a_shared_head_forgets_that_lane(
    tmp_path: Path,
) -> None:
    """Bench 2026-10-31: after a:1's eject through T0 the head still named a:1."""
    rig, a = probing(tmp_path)
    end_probe_free(rig, a)
    slot = rig.adapter.slot_of_head(0)
    assert slot.current is not None and slot.current.lane == LANE
    unload_ends(rig, a, LANE, "unloaded")
    assert slot.current is None
    assert any("head 1: its lane was ejected" in m for m in rig.gcode.lines)


def test_a_failed_eject_unload_keeps_the_lane_of_the_shared_head(tmp_path: Path) -> None:
    rig, a = probing(tmp_path)
    end_probe_free(rig, a)
    slot = rig.adapter.slot_of_head(0)
    unload_ends(rig, a, LANE, "stall")
    assert slot.current is not None and slot.current.lane == LANE


def test_an_eject_of_the_lane_parked_outside_its_shared_head_keeps_the_heads_lane(
    tmp_path: Path,
) -> None:
    rig, by_name = tools_rig(tmp_path)
    fakes.give_materials(rig.module)
    a = by_name["a"]
    a.lanes[LANE].update(insert=True, mode="idle", rest=True)
    slot = rig.adapter.slot_of_head(0)
    other = rig.adapter.hooked_lane(a, 0)
    rig.adapter.set_current(slot, other)  # a:1 in the head, a:2 parked
    run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": "2", "WAIT": "0"})
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    unload_ends(rig, a, LANE, "unloaded")
    assert slot.current is other


def test_an_eject_of_a_lane_that_feeds_its_head_alone_keeps_it_as_the_heads_lane(
    tmp_path: Path,
) -> None:
    """A head of one lane always names that lane: its card then shows the lane's bay empty."""
    rig, a = probing(tmp_path, LONE_CH, LONE_LANE, "3")
    end_probe_free(rig, a)
    slot = rig.adapter.slot_of_head(1)
    lane = slot.current
    unload_ends(rig, a, LONE_LANE, "unloaded")
    assert slot.current is lane and lane is not None


def test_an_eject_unloaded_again_forgets_the_lane_of_the_shared_head_at_its_last_end(
    tmp_path: Path,
) -> None:
    """The bench case: the first unload spent its whole move (unloading again), the next emptied the
    bay. The head keeps its lane through the first end and forgets it at the last."""
    rig, a = probing(tmp_path)
    end_probe_free(rig, a)
    slot = rig.adapter.slot_of_head(0)
    first = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LANE and m == "unload"][-1]
    unload_ends(rig, a, LANE, "unload_incomplete")  # 1950 mm: a whole move carried
    again = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LANE and m == "unload"][-1]
    assert again is not first, "the eject did not unload again"
    assert slot.current is not None and slot.current.lane == LANE
    unload_ends(rig, a, LANE, "unloaded")
    assert slot.current is None


def test_an_eject_through_the_safety_net_forgets_the_lane_of_the_shared_head(
    tmp_path: Path,
) -> None:
    """The unload met the head's gear: the net fed back, the U1 unloaded the head, then the unit's
    unload emptied the bay."""
    rig, a = probing(tmp_path)
    end_probe_free(rig, a)
    slot = rig.adapter.slot_of_head(0)
    first = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LANE and m == "unload"][-1]
    event = {"kind": "stuck", "mode": "unloading", "seq": first.seq, "unit": "a"}
    event.update(motor_mm=45.0, filament_mm=-20.0)
    rig.adapter.on_feed_event(LANE, event)
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    back = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LANE and m == "feed"][-1]
    back.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 30.0}
    a.lanes[LANE].update(rest=False, pushed=True)
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    assert any("held by the head after all" in m for m in rig.gcode.lines)
    rig.left._set_channel_state(SHARED_CH, "unload_finish")
    rig.reactor.run_timers(rig.reactor.now + 1.0)
    last = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LANE and m == "unload"][-1]
    assert last is not first, "the U1's unload brought no unit unload"
    assert slot.current is not None and slot.current.lane == LANE
    unload_ends(rig, a, LANE, "unloaded")
    assert slot.current is None


def test_an_eject_that_a_print_started_does_not_forget_the_lane_of_the_head(
    tmp_path: Path,
) -> None:
    """The U1's end unload of a print ejects while the print still reads printing."""
    rig, a = probing(tmp_path)
    end_probe_free(rig, a)
    slot = rig.adapter.slot_of_head(0)
    lane = slot.current
    rig.print_stats.state = "printing"
    unload_ends(rig, a, LANE, "unloaded")
    assert slot.current is lane and lane is not None
