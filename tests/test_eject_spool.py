"""ACEK_EJECT and the lane's spool record (bench 2026-10-31).

The user ejects to take the spool out. An ACEK_EJECT that ends well with the bay empty drops the
lane's Edit spool record and its forgotten-tag mark, as a spool taken out by hand does: on a lane
that feeds its head alone, and on a lane parked outside its shared head. So does an eject that
ends its unit unload in error, once its filament leaves the bay (2026-11-07, a:4), until the lane
really moves. A failed eject whose bay stays full keeps both, and so do an eject that never
reached the unit's unload, an unload failure judged under a U1 flow, and the adapter's own roll
backs, such as a swap's or a refill's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import fakes
import pytest
from test_tools import run, tools_rig

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from ace2k_u1_common import Hooked
    from fakes import FakeAce2k

SHARED = 1  # a:2, which shares head 1 with a:1
LONE = 2  # a:3, which feeds head 2 alone
LONE_CH = 0  # its channel on the left feed


def rig_with_spool(tmp_path: Path, lane: int) -> tuple[SimpleNamespace, FakeAce2k, Hooked]:
    """A spool set by hand on lane a:<lane + 1>, its bay full, the head's sensor clear."""
    rig, by_name = tools_rig(tmp_path)
    fakes.give_materials(rig.module)
    a = by_name["a"]
    a.lanes[lane].update(insert=True, mode="idle", rest=True)
    h = rig.adapter.hooked_lane(a, lane)
    if lane == SHARED:  # a:1 is in head 1, so a:2 is parked outside it
        rig.adapter.set_current(rig.adapter.slot_of_head(h.head), rig.adapter.hooked_lane(a, 0))
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": str(lane + 1), "TYPE": "PLA", "COLOR": "ff0000"})
    tick(rig)
    assert h.bay_full
    return rig, a, h


def tick(rig: SimpleNamespace, s: float = 1.0) -> None:
    rig.reactor.run_timers(rig.reactor.now + s)


def records(rig: SimpleNamespace) -> dict[str, object]:
    return rig.module.spool_store.records


def eject(rig: SimpleNamespace, lane: int) -> None:
    run(rig, "ACEK_EJECT", {"UNIT": "a", "LANE": str(lane + 1), "WAIT": "0"})
    tick(rig)


def unload_ends(
    rig: SimpleNamespace,
    a: FakeAce2k,
    lane: int,
    kind: str,
    filament_mm: float = -1918.0,
    motor_mm: float = 1950.0,
    bay_empties: bool = True,
    mode: str = "idle",
) -> None:
    """The unit's unload of the eject ends with kind, the lane then in mode. With bay_empties the
    bay reads empty first, while the unload runs; without, it still reads full."""
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == lane and m == "unload"]
    assert started, "the eject started no unload"
    if bay_empties:
        a.lanes[lane].update(insert=False, mode="unloading")
        tick(rig)
    a.lanes[lane]["mode"] = mode
    event = {"kind": kind, "mode": "unloading", "seq": started[-1].seq, "unit": "a"}
    event.update(motor_mm=motor_mm, filament_mm=filament_mm)
    rig.adapter.on_feed_event(lane, event)
    tick(rig)


def test_an_eject_of_a_lane_that_feeds_its_head_alone_drops_the_record(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    h.tag_forgotten = True
    eject(rig, LONE)
    unload_ends(rig, a, LONE, "unloaded")
    assert "a:3" not in records(rig)
    assert h.tag_forgotten is False
    # the same spool put back is not "set by hand" again
    a.lanes[LONE]["insert"] = True
    tick(rig)
    assert "a:3" not in records(rig)


def test_an_eject_of_a_lane_parked_outside_its_shared_head_drops_the_record(
    tmp_path: Path,
) -> None:
    rig, a, _h = rig_with_spool(tmp_path, SHARED)
    eject(rig, SHARED)
    unload_ends(rig, a, SHARED, "unloaded")
    assert "a:2" not in records(rig)


def test_a_drop_whose_write_fails_is_tried_again(tmp_path: Path) -> None:
    rig, a, _h = rig_with_spool(tmp_path, LONE)
    store = rig.module.spool_store
    real = store.drop
    calls: list[str] = []

    def drop(key: str, text: str) -> bool:
        calls.append(key)
        return False if len(calls) == 1 else real(key, text)

    store.drop = drop
    eject(rig, LONE)
    unload_ends(rig, a, LONE, "unloaded")
    tick(rig)
    assert calls[:2] == ["a:3", "a:3"]
    assert "a:3" not in records(rig)


def test_a_failed_eject_whose_bay_stays_full_keeps_the_record(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    h.tag_forgotten = True
    eject(rig, LONE)
    unload_ends(rig, a, LONE, "stuck", -100.0, 130.0, bay_empties=False, mode="error")  # held
    assert any("lane a:3 eject failed: stuck" in ln for ln in rig.gcode.lines)
    tick(rig, 10.0)
    assert "a:3" in records(rig)
    assert h.tag_forgotten is True
    assert h.eject_by_user is False


def test_a_failed_eject_whose_filament_left_the_bay_drops_the_record(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    h.tag_forgotten = True
    eject(rig, LONE)
    unload_ends(rig, a, LONE, "stuck", filament_mm=-100.0, motor_mm=130.0)  # the bay emptied
    assert any("lane a:3 eject failed: stuck" in ln for ln in rig.gcode.lines)
    assert "a:3" not in records(rig)
    assert h.tag_forgotten is False


def unload_incomplete_with_the_tip_at_the_bay(rig: SimpleNamespace, a: FakeAce2k) -> None:
    """2026-11-07 (a:4): the unload ran its whole budget and ended unload_incomplete 1161 mm back,
    the tip out of the unit's drive gear but still on the bay's sensor. A failure."""
    unload_ends(rig, a, LONE, "unload_incomplete", -1161.4, 2100.0, bay_empties=False, mode="error")
    assert any("lane a:3 eject failed: unload_incomplete" in ln for ln in rig.gcode.lines)


@pytest.mark.parametrize("later_s", [2.0, 300.0])
def test_a_failed_eject_whose_tip_is_pulled_out_later_drops_the_record(
    tmp_path: Path, later_s: float
) -> None:
    """The user pulls the tip out by hand, 2 s or 5 min after the failure, the lane still in
    error: a new spool with no tag is not given the old one's material."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    unload_incomplete_with_the_tip_at_the_bay(rig, a)
    tick(rig, later_s)
    assert "a:3" in records(rig) and h.eject_drop is True
    a.lanes[LONE]["insert"] = False  # pulled out by hand
    tick(rig)
    assert "a:3" not in records(rig)
    a.lanes[LONE].update(insert=True, mode="idle")  # a new spool with no tag
    tick(rig)
    assert "a:3" not in records(rig)


def test_a_failed_eject_then_a_move_of_the_lane_keeps_the_record(tmp_path: Path) -> None:
    """The drop of a failed eject lasts until the lane's next move: a bay that move empties is
    the move's, not the eject's."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    unload_incomplete_with_the_tip_at_the_bay(rig, a)
    rig.adapter.started(h, h.api.start_move(h.lane, "rollback", 100.0, 30.0))
    assert h.eject_drop is False
    a.lanes[LONE].update(insert=False, mode="rolling_back")
    tick(rig)
    assert "a:3" in records(rig)


def test_a_failed_eject_the_adapter_started_keeps_the_record(tmp_path: Path) -> None:
    """The eject at a print's end, not asked: its failure and an emptied bay keep the record."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    rig.adapter.ejector.start_eject(h.lane, h.unit)
    tick(rig)
    unload_incomplete_with_the_tip_at_the_bay(rig, a)
    assert h.eject_drop is False
    a.lanes[LONE]["insert"] = False
    tick(rig)
    assert "a:3" in records(rig)


def test_a_roll_back_that_is_not_an_asked_eject_keeps_the_record(tmp_path: Path) -> None:
    """A swap's unload, a refill or the probe rolls the lane back by itself: no ACEK_EJECT."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    rig.adapter.ejector.start_eject(h.lane, h.unit)  # the eject at a print's end, not asked
    tick(rig)
    unload_ends(rig, a, LONE, "unloaded")
    assert "a:3" in records(rig)
    move = h.api.start_move(h.lane, "rollback", 500.0, 30.0)  # a roll back of the adapter's own
    rig.adapter.started(h, move)
    a.lanes[LONE].update(insert=True)
    tick(rig)
    a.lanes[LONE].update(insert=False, mode="rolling_back")
    tick(rig)
    assert "a:3" in records(rig)


def test_a_move_started_after_the_eject_ends_cancels_the_drop(tmp_path: Path) -> None:
    """The eject ended before the bay read empty, and a new move of the lane emptied it."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "unload"][-1]
    event = {"kind": "unloaded", "mode": "unloading", "seq": started.seq, "unit": "a"}
    event.update(motor_mm=1950.0, filament_mm=-1918.0)
    rig.adapter.on_feed_event(LONE, event)
    assert h.eject_drop is True
    rig.adapter.started(h, h.api.start_move(h.lane, "rollback", 100.0, 30.0))
    a.lanes[LONE].update(insert=False, mode="rolling_back")
    tick(rig)
    assert "a:3" in records(rig)


def test_an_eject_through_the_head_drops_the_record_at_the_units_unload(tmp_path: Path) -> None:
    """The head's gear holds the filament: the U1 unloads the head, then the unit the bay."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    rig.left.runout_sensor[LONE_CH].detected = True
    rig.left._set_channel_state(LONE_CH, "load_finish")
    scripts: list[str] = []
    rig.gcode.run_script = scripts.append
    eject(rig, LONE)
    probe = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "rollback"][-1]
    a.lanes[LONE].update(rest=False, pushed=True)  # the plunger moved: held by the head's gear
    probe.event = {"kind": "done", "mode": "rolling_back", "motor_mm": -80.0, "filament_mm": -79.0}
    tick(rig, 10.0)
    back = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "feed"][-1]
    a.lanes[LONE].update(rest=True, pushed=False)
    back.event = {"kind": "done", "mode": "feeding", "motor_mm": 80.0, "filament_mm": 79.0}
    tick(rig, 10.0)
    assert any("UNLOAD=1" in line for line in scripts), "the U1's unload of the head did not run"
    assert h.eject_by_user is True
    rig.left.runout_sensor[LONE_CH].detected = False
    rig.left._set_channel_state(LONE_CH, "unload_finish")  # the U1's unload ended
    tick(rig)
    unload_ends(rig, a, LONE, "unloaded")
    assert "a:3" not in records(rig)


def test_an_eject_that_leaves_its_tail_at_the_bay_drops_the_record_once_it_is_pulled_out(
    tmp_path: Path,
) -> None:
    """The tail past the unit's drive gear, under the bay's sensor: the user pulls it out by hand,
    which is what the eject was for, even right after the eject."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    h.tag_forgotten = True
    eject(rig, LONE)
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "unload"][-1]
    event = {"kind": "unload_incomplete", "mode": "unloading", "seq": started.seq, "unit": "a"}
    event.update(motor_mm=300.0, filament_mm=-290.0)
    rig.adapter.on_feed_event(LONE, event)
    tick(rig)
    assert any("take it out by hand" in line for line in rig.gcode.lines)
    assert "a:3" in records(rig)  # the tail is still in the bay
    a.lanes[LONE].update(insert=False, mode="idle")  # pulled out at once, inside BAY_OUT_S
    tick(rig, 0.2)
    assert "a:3" not in records(rig)
    assert h.tag_forgotten is False


def test_a_cancelled_eject_keeps_the_record_and_a_later_automatic_unload_too(
    tmp_path: Path,
) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    a.feed.refuse = "busy"  # the unit's unload of the eject is retried, still to start
    eject(rig, LONE)
    assert h.key in rig.adapter.ejector.eject_timers
    rig.adapter.ejector.cancel_eject(h)  # a new pull cancels it (the follow's arm)
    assert h.eject_by_user is False
    a.feed.refuse = None
    rig.adapter.ejector.schedule_eject(h)  # a later automatic unload: the U1's own, say
    tick(rig)
    unload_ends(rig, a, LONE, "unloaded")
    assert "a:3" in records(rig)


def test_a_filament_put_in_or_a_new_record_cancels_a_pending_drop(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    h.eject_drop = True
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PLA", "COLOR": "00ff00"})
    assert h.eject_drop is False
    a.lanes[LONE].update(insert=False, mode="rolling_back")  # emptied by a move: record kept
    rig.adapter.moved_out(h)
    tick(rig)
    assert "a:3" in records(rig) and not h.bay_full
    h.eject_drop = True
    a.lanes[LONE].update(insert=True, mode="idle")  # a filament put into the empty bay first
    tick(rig)
    assert h.eject_drop is False
    assert "a:3" in records(rig)


def stuck_at_the_bay(rig: SimpleNamespace, a: FakeAce2k, lane: int) -> None:
    """Bench 2026-10-31 (a:3): the unload pulled the tail out of the drive gear, which could not
    pull it the last few cm off the bay's sensor: stuck, the bay still set."""
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == lane and m == "unload"]
    a.lanes[lane].update(mode="unloading")
    tick(rig)
    a.lanes[lane]["mode"] = "idle"
    event = {"kind": "stuck", "mode": "unloading", "seq": started[-1].seq, "unit": "a"}
    event.update(motor_mm=1813.7, filament_mm=-1876.0)
    rig.adapter.on_feed_event(lane, event)
    tick(rig)


def test_a_stuck_with_the_tail_at_the_bay_is_taken_out_by_hand_and_drops_the_record(
    tmp_path: Path,
) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    stuck_at_the_bay(rig, a, LONE)
    said = [ln for ln in rig.gcode.lines if "lane a:3" in ln]
    assert not any("eject failed" in ln for ln in said)
    assert any("at its bay: take it out by hand" in ln for ln in said)
    assert a.feed.cleared[-1] == LONE  # the lane's error cleared, as ACEK_CLEAR
    assert "a:3" in records(rig)  # the bay still reads the tail
    a.lanes[LONE]["insert"] = False  # taken out by hand
    tick(rig)
    assert "a:3" not in records(rig)


def test_a_stuck_short_of_the_lanes_known_length_stays_a_failure(tmp_path: Path) -> None:
    """The lane's last push to its head went 1900 mm: a stuck 600 mm back is far from the bay."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    h.push_mm, h.push_known = 1900.0, True
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "unload"]
    event = {"kind": "stuck", "mode": "unloading", "seq": started[-1].seq, "unit": "a"}
    event.update(motor_mm=580.0, filament_mm=-600.0)
    rig.adapter.on_feed_event(LONE, event)
    tick(rig)
    assert any("lane a:3 eject failed: stuck" in ln for ln in rig.gcode.lines)
    assert "a:3" in records(rig)


def test_a_stuck_within_the_known_length_less_its_slack_is_the_tail_at_the_bay(
    tmp_path: Path,
) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    h.push_mm, h.push_known = 1950.0, True
    stuck_at_the_bay(rig, a, LONE)  # 1876 mm back: within the slack of 1950
    assert any("at its bay: take it out by hand" in ln for ln in rig.gcode.lines)


def test_a_stuck_after_an_unload_ran_again_counts_the_whole_ejects_travel(
    tmp_path: Path,
) -> None:
    """A head 2 500 mm away: the first unload carried 1 000 mm and ran again; the second ends
    stuck 1 600 mm back. The eject came back 2 600 mm in all: the tail at the bay."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    h.push_mm, h.push_known = 2500.0, True
    rig.adapter.ejector.eject_spent[h.key] = 1000.0
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "unload"]
    event = {"kind": "stuck", "mode": "unloading", "seq": started[-1].seq, "unit": "a"}
    event.update(motor_mm=1580.0, filament_mm=-1600.0)
    rig.adapter.on_feed_event(LONE, event)
    tick(rig)
    assert not any("eject failed" in ln for ln in rig.gcode.lines)
    assert any("ejected (2600 mm back)" in ln for ln in rig.gcode.lines)  # the whole eject's


@pytest.mark.parametrize("push_mm, at_bay", [(2500.0, False), (2100.0, True)])
def test_an_unload_run_again_that_sticks_at_once_is_judged_on_the_whole_eject(
    tmp_path: Path, push_mm: float, at_bay: bool
) -> None:
    """The unload ran again after 2 000 mm and stuck 20 mm into its own travel. Short of the
    lane's length to its head (2 500 mm) the filament did not move: a failure. Past it (2 100 mm
    less the slack) the tail was already at the bay."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    h.push_mm, h.push_known = push_mm, True
    rig.adapter.ejector.eject_spent[h.key] = 2000.0
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "unload"]
    event = {"kind": "stuck", "mode": "unloading", "seq": started[-1].seq, "unit": "a"}
    event.update(motor_mm=20.0, filament_mm=-20.0)
    rig.adapter.on_feed_event(LONE, event)
    tick(rig)
    failed = any("lane a:3 eject failed: stuck" in ln for ln in rig.gcode.lines)
    assert failed is not at_bay
    assert any("ejected (2020 mm back)" in ln for ln in rig.gcode.lines) is at_bay


def test_an_eject_whose_unload_never_started_keeps_the_record(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    a.feed.refuse = "no_filament"  # the unit refuses the unload: the eject never started
    eject(rig, LONE)
    assert any("lane a:3 eject not started" in ln for ln in rig.gcode.lines)
    tick(rig, 10.0)
    assert h.eject_drop is False and "a:3" in records(rig)
    # A bay that a move emptied keeps the record, as with no eject. (One emptied by hand with no
    # move goes by the rule of a spool taken out by hand, with or without the eject.)
    rig.adapter.moved_out(h)
    a.lanes[LONE].update(insert=False, mode="rolling_back")
    tick(rig)
    assert "a:3" in records(rig)


def held_by_the_head(rig: SimpleNamespace, a: FakeAce2k) -> None:
    """The eject's probe finds the filament held by the head's gear and feeds it back: the U1's
    unload of the head comes next, before any unit unload."""
    rig.left.runout_sensor[LONE_CH].detected = True
    rig.left._set_channel_state(LONE_CH, "load_finish")
    eject(rig, LONE)
    probe = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "rollback"][-1]
    a.lanes[LONE].update(rest=False, pushed=True)
    probe.event = {"kind": "done", "mode": "rolling_back", "motor_mm": -80.0, "filament_mm": -79.0}
    tick(rig, 10.0)
    back = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "feed"][-1]
    a.lanes[LONE].update(rest=True, pushed=False)
    back.event = {"kind": "done", "mode": "feeding", "motor_mm": 80.0, "filament_mm": 79.0}
    tick(rig, 10.0)


def test_a_u1_unload_that_fails_before_the_units_unload_keeps_the_record(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)

    def fails(_script: str) -> None:
        raise RuntimeError("the head is too cold")

    rig.gcode.run_script = fails
    held_by_the_head(rig, a)
    assert any("eject failed: the U1's unload of e" in ln for ln in rig.gcode.lines)
    assert h.eject_drop is False and h.eject_by_user is False
    a.lanes[LONE].update(insert=False, mode="error")
    tick(rig)
    assert "a:3" in records(rig)


def test_a_u1_failure_during_the_units_unload_leaves_its_end_deciding(tmp_path: Path) -> None:
    """An unrelated U1 failure on the head while the eject's unit unload runs arms nothing, and
    the unload's own end still drops the record."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    assert rig.adapter.ejector.unload_started(h)
    rig.left._set_channel_state(LONE_CH, "preload_fail")
    assert h.eject_drop is False and h.eject_by_user is True
    unload_ends(rig, a, LONE, "unloaded")
    assert "a:3" not in records(rig)


def test_an_unload_failure_under_a_u1_flow_does_not_arm_the_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    monkeypatch.setattr(rig.adapter.ejector, "u1_flow", lambda _h: True)
    unload_incomplete_failure = {"kind": "unload_incomplete", "unit": "a", "mode": "unloading"}
    started = [mv for ln, m, _len, _s, mv in a.feed.started if ln == LONE and m == "unload"][-1]
    unload_incomplete_failure.update(seq=started.seq, motor_mm=2100.0, filament_mm=-1161.4)
    a.lanes[LONE]["mode"] = "error"
    rig.adapter.on_feed_event(LONE, unload_incomplete_failure)
    tick(rig)
    assert h.eject_drop is False
    a.lanes[LONE]["insert"] = False
    tick(rig)
    assert "a:3" in records(rig)


def test_a_u1_load_after_a_failed_eject_ends_the_drop(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    unload_incomplete_with_the_tip_at_the_bay(rig, a)
    assert h.eject_drop is True
    rig.left._set_channel_state(LONE_CH, "load_prepare")  # the user reloads the head on the U1
    assert h.eject_drop is False
    a.lanes[LONE]["insert"] = False
    tick(rig)
    assert "a:3" in records(rig)


def test_a_mode_out_of_idle_in_the_lanes_status_ends_the_drop(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    unload_incomplete_with_the_tip_at_the_bay(rig, a)
    tick(rig, 10.0)  # the report after the end is believed
    assert h.eject_drop is True
    a.lanes[LONE]["mode"] = "feeding"  # a move of the unit
    tick(rig)
    assert h.eject_drop is False


@pytest.mark.parametrize("change_mm", [40.0, -40.0, -5000.0])
def test_the_encoder_alone_never_ends_the_drop(tmp_path: Path, change_mm: float) -> None:
    """A push or a pull by hand, or a reset of the count: the drop stays."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    unload_incomplete_with_the_tip_at_the_bay(rig, a)
    tick(rig, 10.0)
    a.lanes[LONE]["encoder_mm"] = (a.lanes[LONE].get("encoder_mm") or 0.0) + change_mm
    tick(rig)
    assert h.eject_drop is True
    a.lanes[LONE]["insert"] = False
    tick(rig)
    assert "a:3" not in records(rig)


def test_a_load_finish_set_again_does_not_end_the_drop(tmp_path: Path) -> None:
    """The U1 asserting load_finish over load_finish is no load; a new load from it is."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    rig.left._set_channel_state(LONE_CH, "load_finish")
    h.eject_drop = True
    rig.left._set_channel_state(LONE_CH, "load_finish")
    assert h.eject_drop is True
    rig.left._set_channel_state(LONE_CH, "load_prepare")
    assert h.eject_drop is False


def test_the_steps_of_one_load_are_one_start(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    rig.left._set_channel_state(LONE_CH, "load_prepare")
    h.eject_drop = True  # (not reachable this way on the unit: only the guard is tested)
    rig.left._set_channel_state(LONE_CH, "load_heating")
    assert h.eject_drop is True


def test_a_follow_arm_that_starts_nothing_keeps_the_drop(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    unload_incomplete_with_the_tip_at_the_bay(rig, a)
    h.waits_behind = True  # the head runs on the piece ahead: no follow is sent
    rig.adapter.follow.arm(h)
    assert h.eject_drop is True
    h.waits_behind = False
    a.feed.refuse = "busy"  # the unit refuses the follow's start
    rig.adapter.follow.arm(h)
    assert not h.armed and h.eject_drop is True


def test_an_unload_end_of_a_kind_not_listed_arms_the_drop(tmp_path: Path) -> None:
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    unload_ends(rig, a, LONE, "slip_detected", -800.0, 900.0, bay_empties=False, mode="error")
    assert h.eject_drop is True
    a.lanes[LONE]["insert"] = False
    tick(rig)
    assert "a:3" not in records(rig)


def test_a_u1_failure_keeps_the_running_unloads_lengths(tmp_path: Path) -> None:
    """The unload's own end is judged with the eject's real lengths after a U1 failure."""
    rig, a, h = rig_with_spool(tmp_path, LONE)
    eject(rig, LONE)
    ejector = rig.adapter.ejector
    ejector.eject_spent[h.key] = 1000.0
    asked = ejector.eject_asked[h.key]
    rig.left._set_channel_state(LONE_CH, "preload_fail")
    assert ejector.eject_spent[h.key] == 1000.0 and ejector.eject_asked[h.key] == asked
    assert h.eject_by_user is True
