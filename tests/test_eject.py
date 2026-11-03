"""ACEK_EJECT: the filament out of its bay whatever the head holds. The head's sensor clear: the
follow off and the unit's unload. Set: a rollback past the buffer's slack probes the filament,
stopped on a taut plunger (held), else judged by the lane's buffer plunger once settled — at rest,
free: the same unload, with a safety net for its early stuck; moved (or a lane error), held by the
head: the filament the probe pulled fed back and the U1's own unload of that head started. Refused
while in use by a print, busy, in error, with no filament, or already ejecting; everything runs from
a reactor timer that never raises and does nothing twice when the reactor runs it again."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn
from unittest.mock import MagicMock

import ace2k_u1
import ace2k_u1_common
import ace2k_u1_eject
import ace2k_u1_history as hist
import fakes
import pytest
from test_check import Config, Printer
from test_check import Gcode as CheckGcode
from test_presence import build

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ace2k_klipper_types import FeedEvent
    from ace2k_u1_adapter import Adapter
    from ace2k_u1_eject_job import EjectJob
    from fakes import FakeAce2k, FakeFeed, FakeMove, FakeReactor

LANE = 0  # lane 1: head 0, the left feed's channel 1
U1_UNLOAD = (
    "AUTO_FEEDING EXTRUDER=0 UNLOAD=1 STAGE=prepare\nAUTO_FEEDING EXTRUDER=0 UNLOAD=1 STAGE=doing"
)


def setup(
    head: bool = False, mode: str = "idle", insert: bool = True
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeAce2k]:
    adapter, reactor, left, right, ace = build()
    adapter.gcode = fakes.FakeGcode()
    adapter.hook_all()
    ace.lanes[LANE]["insert"] = insert
    ace.lanes[LANE]["mode"] = mode
    left.runout_sensor[1].detected = head
    adapter.eject_settle_s = 0.0  # the plunger judged at once; the settle's own tests set it
    ace.clock = reactor  # the unit's sensors reports arrive at 10 Hz of the test's time
    return adapter, reactor, left, ace


def probe_end(kind: str, filament_mm: float, motor_mm: float) -> FeedEvent:
    return {"kind": kind, "mode": "rolling_back", "motor_mm": motor_mm, "filament_mm": filament_mm}


def plunger(ace: FakeAce2k, at_rest: bool) -> None:
    ace.lanes[LANE].update(rest=at_rest, pushed=not at_rest)


def end_probe(
    ace: FakeAce2k, kind: str, filament_mm: float, motor_mm: float, at_rest: bool
) -> None:
    """The probe's final event, the lane's plunger set as the case it stands for: at rest for a
    filament that came back whole, moved for a held tip."""
    plunger(ace, at_rest)
    last_start(ace)[4].event = probe_end(kind, filament_mm, motor_mm)


def last_start(ace: FakeAce2k) -> tuple[int, str, float, float, FakeMove]:
    lane, mode, length, speed, move = ace.feed.started[-1]
    return lane, mode, length, speed, move


def lines(adapter: Adapter) -> list[str]:
    return [line for line in adapter.gcode.lines if "eject" in line]


def advance(reactor: FakeReactor, s: float = 0.1) -> None:
    reactor.now += s
    reactor.run_timers()


# --- the three paths ---------------------------------------------------------------------------


def test_head_empty_unloads_at_once() -> None:
    adapter, reactor, left, ace = setup(head=False)
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: head empty — unloading"]
    assert last_start(ace)[:3] == (LANE, "unload", 0.0)
    assert job.decided and job.failed is None and LANE not in adapter.ejector.eject_jobs
    assert all(mode != "rollback" for _, mode, *_ in ace.feed.started)


def test_head_empty_stops_the_follow_first_and_retries_a_busy_unload() -> None:
    adapter, reactor, left, ace = setup(head=False)
    left._set_channel_state(1, "load_finish", True)  # the follow armed on the loaded head
    h = adapter.hooked[[x.lane for x in adapter.hooked].index(LANE)]
    assert h.armed and h.follow_wanted
    ace.feed.refusals = ["busy"]  # the stopped follow winding down
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.25)
    assert ("stop", LANE) in ace.feed.log
    stop_at = ace.feed.log.index(("stop", LANE))
    assert ace.feed.log[stop_at + 1][:3] == ("start", LANE, "unload")
    assert not h.armed and not h.follow_wanted


def test_probe_free_unloads() -> None:
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    lane, mode, length, speed, move = last_start(ace)
    assert (lane, mode, length, speed) == (LANE, "rollback", 80.0, 10.0)
    assert not job.decided
    move.event = probe_end("done", -9.6, -10.3)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert last_start(ace)[:2] == (LANE, "unload")
    assert job.decided and adapter.gcode.scripts == []


def test_probe_held_feeds_back_and_starts_the_u1_unload() -> None:
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -3.0, -10.4, at_rest=False)
    advance(reactor)
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject: held by the head (plunger moved) — unloading the head on the U1"
    ]
    lane, mode, length, speed, move = last_start(ace)
    assert (lane, mode, length, speed) == (LANE, "feed", 3.0, 10.0)  # what left, not the motor
    assert ace.feed.cleared == [] and adapter.gcode.scripts == []
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.4, "filament_mm": 3.1}
    advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]
    assert job.decided and job.failed is None and LANE not in adapter.ejector.eject_jobs
    assert all(mode != "unload" for _, mode, *_ in ace.feed.started)  # the U1's end brings it


def test_probe_in_error_is_held_the_lane_cleared_first() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stuck", -1.2, -22.0, at_rest=False)
    advance(reactor)
    assert ace.feed.cleared == [LANE]
    assert "held by the head (the probe met resistance)" in lines(adapter)[0]
    lane, mode, length, speed, move = last_start(ace)
    assert (mode, length) == ("feed", 1.2)  # the filament that left; the motor slipped
    move.event = {"kind": "done", "mode": "feeding", "motor_mm": 22.0, "filament_mm": 2.0}
    advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]


def test_a_tiny_motor_travel_is_not_fed_back() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stuck", 0.0, -0.2, at_rest=False)
    starts = len(ace.feed.started)
    advance(reactor)
    assert len(ace.feed.started) == starts and adapter.gcode.scripts == [U1_UNLOAD]


def test_a_probe_stopped_ends_the_eject() -> None:
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stopped", -2.0, -2.0, at_rest=False)
    advance(reactor)
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject failed: the probe ended stopped; the follow stays off until"
        " e0's next load"
    ]
    assert job.failed and adapter.gcode.scripts == [] and LANE not in adapter.ejector.eject_jobs


def test_a_probe_with_no_end_is_stopped_and_fails() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 20.0)
    assert not lines(adapter)
    advance(reactor, 1.5)  # past twice its travel time (80 mm at 10 mm/s) plus the margin
    assert ("stop", LANE) in ace.feed.log
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject failed: the probe got no end; the follow stays off until e0's"
        " next load"
    ]


def test_a_busy_probe_is_retried_then_a_refusal_fails() -> None:
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.refusals = ["busy", "busy"]
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.25)
    advance(reactor, 0.25)
    assert last_start(ace)[1] == "rollback"
    adapter2, reactor2, left2, ace2 = setup(head=True)
    ace2.feed.refuse = "no_link"
    adapter2.ejector.start_eject(LANE)
    reactor2.run_timers()
    assert lines(adapter2) == [
        "ace2k_u1: lane 1 eject failed: the probe not started — refused: no_link"
        "; the follow stays off until e0's next load"
    ]


def test_the_probe_runs_at_eject_probe_speed_unclamped() -> None:
    """The unit's bounds are judged at the hook, not at each start."""
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.speed_min = 12.0  # after the hook: nothing at the start changes the speed
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert last_start(ace)[3] == adapter.eject_probe_speed == 10.0


def test_a_failed_u1_unload_is_the_eject_line() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.gcode.fail = RuntimeError("Unknown command:AUTO_FEEDING")
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -1.0, -10.0, at_rest=False)
    advance(reactor)
    last_start(ace)[4].event = {"kind": "done", "mode": "feeding", "motor_mm": 10.0}
    advance(reactor)
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject failed: the U1's unload of e0 — Unknown command:AUTO_FEEDING"
        "; the follow stays off until e0's next load"
    )


# --- the refusals ------------------------------------------------------------------------------


def refused(adapter: Adapter, match: str) -> None:
    with pytest.raises(ace2k_u1_eject.EjectRefused, match=match):
        adapter.ejector.start_eject(LANE)


def test_refused_in_use_by_the_print() -> None:
    for state in ("printing", "paused"):
        adapter, reactor, left, ace = setup(head=True)
        adapter.print_stats = fakes.FakePrintStats(state)
        refused(adapter, "lane 1 eject refused: in use by the print")
        adapter, reactor, left, ace = setup(head=False, mode="following")
        adapter.print_stats = fakes.FakePrintStats(state)
        refused(adapter, "in use by the print")
    # a lane the print does not use: its head clear, the lane idle
    adapter, reactor, left, ace = setup(head=False)
    adapter.print_stats = fakes.FakePrintStats("printing")
    adapter.ejector.start_eject(LANE)
    assert ace.feed.started == []  # nothing before the timer runs


def test_refused_busy_in_error_no_filament() -> None:
    adapter, reactor, left, ace = setup(mode="feeding")
    refused(adapter, r"the lane is busy \(feeding\)")
    adapter, reactor, left, ace = setup(mode="error")
    refused(adapter, "in error — ACEK_CLEAR LANE=1 first")
    adapter, reactor, left, ace = setup(insert=False)
    refused(adapter, "no filament in the bay")
    with pytest.raises(ace2k_u1_eject.EjectRefused, match="not hooked"):
        adapter.ejector.start_eject(7)


def test_a_second_eject_is_refused_while_the_first_runs() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    refused(adapter, "an eject is already running")
    reactor.run_timers()  # probing
    refused(adapter, "an eject is already running")
    end_probe(ace, "done", -9.0, -10.0, at_rest=True)
    advance(reactor)  # the unit's unload scheduled and started
    assert LANE not in adapter.ejector.eject_jobs


# --- the timer ---------------------------------------------------------------------------------


def test_wait_zero_runs_from_the_timer_and_a_re_entered_timer_does_nothing() -> None:
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.ejector.start_eject(LANE)
    assert ace.feed.started == [] and job.timer in reactor.timers  # nothing moved yet
    reentered = []

    class LockWaitingGcode(fakes.FakeGcode):
        def run_script(self, script: str) -> None:
            # run_script waits for the G-code lock: the reactor runs the same timer again
            reentered.append(job.timer[0](reactor.now))
            super().run_script(script)

    adapter.gcode = LockWaitingGcode()
    reactor.run_timers()
    end_probe(ace, "done", -2.0, -10.0, at_rest=False)
    advance(reactor)
    last_start(ace)[4].event = {"kind": "done", "mode": "feeding", "motor_mm": 10.0}
    starts = len(ace.feed.started)
    advance(reactor)
    assert reentered == [reactor.NEVER]
    assert adapter.gcode.scripts == [U1_UNLOAD] and len(ace.feed.started) == starts
    assert job.timer is None and reactor.timers.count(job.timer) == 0


def test_the_timer_never_raises() -> None:
    adapter, reactor, left, ace = setup(head=True)

    def broken(eventtime: float) -> NoReturn:
        raise RuntimeError("status gone")

    adapter.ejector.start_eject(LANE)
    ace.get_status = broken
    reactor.run_timers()
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject failed: status gone; the follow stays off until e0's next load"
    ]
    assert LANE not in adapter.ejector.eject_jobs


def test_every_step_but_done_has_its_method() -> None:
    # the timer runs the job's step from the table: a job at u1 starts the U1's unload, and a
    # job done runs nothing again
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.ejector.start_eject(LANE)
    steps = {"begin", "probe", "probe_wait", "settle", "back", "back_wait", "back_settle"}
    assert set(adapter.ejector.steps) == steps | {"u1", "net"}
    job.step = "u1"
    assert adapter.ejector._eject_step(job, reactor.now) == reactor.NEVER
    assert adapter.gcode.scripts == [U1_UNLOAD] and job.decided and job.step == "done"
    assert adapter.ejector._eject_step(job, reactor.now) == reactor.NEVER
    assert adapter.gcode.scripts == [U1_UNLOAD] and ace.feed.started == []


# --- the command and the configuration -------------------------------------------------------


class Gcmd:
    error = ValueError

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
    ) -> int:
        value = self.params.get(name)
        value = default if value is None else int(value)
        if value is None or (minval is not None and value < minval):
            raise self.error(f"{name} missing or out of range")
        if maxval is not None and value > maxval:
            raise self.error(f"{name} out of range")
        return value


def hooked_u1(
    values: Mapping[str, object] | None = None,
) -> tuple[ace2k_u1.ACE2kU1, FakeReactor, FakeFeed, FakeAce2k, CheckGcode]:
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    gcode = CheckGcode()
    objects = {"gcode": gcode, "ace2k": ace, "extruder_list": fakes.extruder_list()}
    u1 = ace2k_u1.ACE2kU1(Config(Printer(reactor, objects, [left, right]), values))
    return u1, reactor, left, ace, gcode


def test_the_command_waits_for_the_decision_only() -> None:
    u1, reactor, left, ace, gcode = hooked_u1()
    u1._hook(reactor.now)
    fakes.give_materials(u1)  # a load and an eject need a material set
    ace.lanes[LANE].update(insert=True, mode="idle")
    left.runout_sensor[1].detected = True
    ace.feed.refusals = []

    def decide(now: float) -> None:
        reactor.run_timers()
        for _, mode, _, _, move in ace.feed.started:
            if mode == "rollback" and move.event is None:
                move.event = probe_end("done", -9.0, -10.0)

    reactor.on_pause = [decide]
    gcode.commands["ACEK_EJECT"](Gcmd({"LANE": "1"}))
    assert any("filament free (plunger at rest)" in line for line in gcode.lines)
    assert u1.adapter.ejector.eject_jobs == {}


def test_the_command_returns_at_once_with_wait_zero_and_refuses_as_a_gcode_error() -> None:
    u1, reactor, left, ace, gcode = hooked_u1()
    with pytest.raises(ValueError, match="not hooked"):
        gcode.commands["ACEK_EJECT"](Gcmd({"LANE": "1"}))
    u1._hook(reactor.now)
    fakes.give_materials(u1)  # a load and an eject need a material set
    ace.lanes[LANE].update(insert=True, mode="idle")
    left.runout_sensor[1].detected = True
    gcode.commands["ACEK_EJECT"](Gcmd({"LANE": "1", "WAIT": "0"}))
    assert ace.feed.started == [] and LANE in u1.adapter.ejector.eject_jobs
    with pytest.raises(ValueError, match="already running"):
        gcode.commands["ACEK_EJECT"](Gcmd({"LANE": "1", "WAIT": "0"}))
    with pytest.raises(ValueError):
        gcode.commands["ACEK_EJECT"](Gcmd({"LANE": "5"}))


class StrictConfig(Config):
    """getfloat with Klipper's bounds."""

    def getfloat(
        self,
        name: str,
        default: float | None,
        above: float | None = None,
        minval: float | None = None,
        maxval: float | None = None,
    ) -> object:
        value = self.values.get(name, default)
        if value is None:
            return None
        if (minval is not None and value < minval) or (maxval is not None and value > maxval):
            raise self.error(f"{name} {value} out of range")
        if above is not None and value <= above:
            raise self.error(f"{name} {value} out of range")
        return value

    def getint(
        self, name: str, default: int | None, minval: int | None = None, maxval: int | None = None
    ) -> object:
        value = self.values.get(name, default)
        if (minval is not None and value < minval) or (maxval is not None and value > maxval):
            raise self.error(f"{name} {value} out of range")
        return value


def strict_u1(values: Mapping[str, object]) -> ace2k_u1.ACE2kU1:
    reactor = fakes.FakeReactor()
    gcode = CheckGcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [])
    return ace2k_u1.ACE2kU1(StrictConfig(printer, values))


def test_the_eject_settings_and_their_bounds() -> None:
    u1 = strict_u1({})
    assert (u1.eject_probe_mm, u1.eject_probe_speed, u1.eject_settle_ms) == (80.0, 10.0, 500)
    assert not hasattr(u1, "eject_free_mm")
    assert strict_u1({"eject_probe_mm": 150.0}).eject_probe_mm == 150.0
    u1 = strict_u1({"eject_probe_mm": 30.0, "eject_probe_speed": 5.0, "eject_settle_ms": 2000})
    assert (u1.eject_probe_mm, u1.eject_probe_speed, u1.eject_settle_ms) == (30.0, 5.0, 2000)
    assert strict_u1({"eject_settle_ms": 200}).eject_settle_ms == 200
    for bad in (
        {"eject_probe_mm": 150.5},
        {"eject_probe_speed": 4.0},
        {"eject_probe_speed": 31.0},
        {"eject_settle_ms": 199},
        {"eject_settle_ms": 2001},
    ):
        with pytest.raises(ValueError):
            strict_u1(bad)


def test_the_settings_reach_the_adapter() -> None:
    u1, reactor, left, ace, gcode = hooked_u1(
        {"eject_probe_mm": 20.0, "eject_probe_speed": 15.0, "eject_settle_ms": 800}
    )
    u1._hook(reactor.now)
    a = u1.adapter
    assert (a.eject_probe_mm, a.eject_probe_speed, a.eject_settle_s) == (20.0, 15.0, 0.8)


# --- fix round 1 ---------------------------------------------------------------------------------


def held_until_feed_back(adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k) -> FakeMove:
    """An eject probed held: the feed back started, its move returned."""
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -2.0, -10.0, at_rest=False)
    advance(reactor)
    assert last_start(ace)[1] == "feed"
    return last_start(ace)[4]


@pytest.mark.parametrize("kind", ["stopped", "stopped_link", "stopped_shutdown", "runout"])
def test_a_feed_back_ended_by_a_stop_fails_and_never_unloads_the_head(kind: str) -> None:
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    move.event = {"kind": kind, "mode": "feeding", "motor_mm": 3.0, "filament_mm": 1.0}
    advance(reactor)
    assert adapter.gcode.scripts == []
    assert lines(adapter)[-1].startswith(
        f"ace2k_u1: lane 1 eject failed: the feed back ended {kind}"
    )


def test_a_feed_back_in_a_lane_error_is_cleared_and_goes_on() -> None:
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    move.event = {"kind": "tangled", "mode": "feeding", "motor_mm": 3.0, "filament_mm": 1.0}
    advance(reactor)
    assert ace.feed.cleared == [LANE] and adapter.gcode.scripts == [U1_UNLOAD]


def test_a_print_started_before_the_u1_unload_fails_it() -> None:
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    adapter.print_stats = fakes.FakePrintStats("printing")
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert adapter.gcode.scripts == []
    assert lines(adapter)[-1].startswith("ace2k_u1: lane 1 eject failed: a print started")


def test_a_print_started_before_the_probe_or_the_feed_back_fails_it() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    adapter.print_stats = fakes.FakePrintStats("printing")
    reactor.run_timers()
    assert ace.feed.started == []  # no probe; the follow was off before: it stays off
    assert lines(adapter)[-1].startswith("ace2k_u1: lane 1 eject failed: a print started")
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -2.0, -10.0, at_rest=False)
    adapter.print_stats = fakes.FakePrintStats("paused")
    advance(reactor)
    assert all(mode != "feed" for _, mode, *_ in ace.feed.started)
    assert "a print started" in lines(adapter)[-1]


def test_a_head_cleared_before_the_u1_unload_takes_the_units_unload() -> None:
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    left.runout_sensor[1].detected = False
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert adapter.gcode.scripts == []
    assert lines(adapter)[-1] == "ace2k_u1: lane 1 eject: head now empty — unloading"
    assert last_start(ace)[1] == "unload"


# the U1's real channel states (its filament_feed.py's FEED_STA_*): at rest, or inside a flow
U1_STATES = [
    "none",
    "inited",
    "wait_insert",
    "preload_prepare",
    "preload_feeding",
    "preload_finish",
    "preload_fail",
    "load_prepare",
    "load_homing",
    "load_picking",
    "load_heating",
    "load_feeding",
    "load_extruding",
    "load_flushing",
    "load_finish",
    "load_fail",
    "unload_prepare",
    "unload_homing",
    "unload_picking",
    "unload_heating",
    "unload_heat_finish",
    "unload_doing",
    "unload_finish",
    "unload_fail",
    "manual_sta_prepare",
    "manual_sta_homing",
    "manual_sta_picking",
    "manual_sta_prepare_finish",
    "manual_sta_prepare_fail",
    "manual_sta_heating",
    "manual_sta_extruding",
    "manual_sta_extrude_finish",
    "manual_sta_extrude_fail",
    "manual_sta_flushing",
    "manual_sta_flush_finish",
    "manual_sta_flush_fail",
    "manual_sta_finish",
    "manual_sta_fail",
    "test",
]
AT_REST = [
    "none",
    "inited",
    "wait_insert",
    "preload_finish",
    "preload_fail",
    "load_finish",
    "load_fail",
    "unload_finish",
    "unload_fail",
    "manual_sta_finish",
    "manual_sta_fail",
    "manual_sta_prepare_fail",
    "manual_sta_extrude_fail",
    "manual_sta_flush_fail",
]


def test_the_fake_carries_every_real_state() -> None:
    real = sorted(v for k, v in fakes.CONSTANTS.items() if k.startswith("FEED_STA_"))
    assert real == sorted(U1_STATES)


@pytest.mark.parametrize("state", U1_STATES + ["some_future_state"])
def test_the_u1_flow_rule_over_every_state(state: str) -> None:
    adapter, reactor, left, ace = setup(head=True)
    left.channel_state[1] = state
    if state in AT_REST:
        adapter.ejector.start_eject(LANE)
    else:
        refused(adapter, "the U1 is loading/unloading this head")


def test_an_unreadable_channel_state_is_refused_never_raised() -> None:
    adapter, reactor, left, ace = setup(head=True)

    class Broken(list):
        def __getitem__(self, i: int) -> NoReturn:
            raise RuntimeError("gone")

    left.channel_state = Broken(left.channel_state)
    refused(adapter, "the U1 is loading/unloading this head")


def test_refused_while_a_channel_flow_holds_it_and_while_the_units_unload_runs() -> None:
    adapter, reactor, left, ace = setup(head=False)
    left.channel_active = 1
    refused(adapter, "the U1 is loading/unloading this head")
    adapter, reactor, left, ace = setup(head=False)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()  # the unit's unload started: in flight until its final event
    assert LANE in adapter.ejector.ejects
    refused(adapter, "an eject is already running")


def test_a_failure_with_the_head_loaded_says_the_follow_stays_off() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stopped", -1.0, -1.0, at_rest=False)
    advance(reactor)
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject failed: the probe ended stopped; the follow stays off until"
        " e0's next load"
    )


def test_a_probe_with_no_filament_travel_fails() -> None:
    for value in (None, "x"):
        adapter, reactor, left, ace = setup(head=True)
        adapter.ejector.start_eject(LANE)
        reactor.run_timers()
        event = probe_end("done", value, -10.0)
        last_start(ace)[4].event = event
        advance(reactor)
        assert all(mode != "feed" for _, mode, *_ in ace.feed.started)
        assert "eject failed: the probe reported no filament travel" in lines(adapter)[-1]


def test_wait_one_giving_up_says_so_once() -> None:
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.ejector.start_eject(LANE)
    adapter.ejector.wait_eject(job)  # no timer runs: no decision
    said = [line for line in adapter.gcode.lines if "no decision" in line]
    assert said == [
        "ace2k_u1: lane 1 eject: no decision after 60 s — ACEK_EJECT returns, the eject runs on"
    ]


# --- fix round 2 ---------------------------------------------------------------------------------


def armed_follow(adapter: Adapter, ace: FakeAce2k) -> bool:
    h = next(x for x in adapter.hooked if x.lane == LANE)
    return h.armed and h.follow_wanted and last_start(ace)[1] == "assist_both"


def test_a_print_started_before_the_probe_restores_the_follow() -> None:
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)  # the follow armed on the loaded head
    adapter.ejector.start_eject(LANE)
    adapter.print_stats = fakes.FakePrintStats("printing")
    reactor.run_timers()
    assert ("stop", LANE) in ace.feed.log  # the eject's begin stopped it
    assert armed_follow(adapter, ace)
    assert all(mode != "rollback" for _, mode, *_ in ace.feed.started)
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject failed: a print started; its follow armed again"
    )


def test_a_print_started_before_the_u1_unload_restores_the_follow() -> None:
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)  # the follow armed on the loaded head
    move = held_until_feed_back(adapter, reactor, ace)
    adapter.print_stats = fakes.FakePrintStats("printing")
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert adapter.gcode.scripts == [] and armed_follow(adapter, ace)


def test_a_u1_flow_at_a_recheck_restores_the_follow_too() -> None:
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)
    move = held_until_feed_back(adapter, reactor, ace)
    left.channel_state[1] = "unload_prepare"  # the U1's own unload began: a loaded state
    move.event = {"kind": "done", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert "the U1 is loading/unloading this head" in lines(adapter)[-1]
    assert armed_follow(adapter, ace)


def test_no_follow_restored_on_a_lane_in_error() -> None:
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)
    move = held_until_feed_back(adapter, reactor, ace)
    adapter.print_stats = fakes.FakePrintStats("printing")
    ace.lanes[LANE]["error"] = "stuck"
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert last_start(ace)[1] == "feed"
    assert lines(adapter)[-1].endswith("the follow stays off until e0's next load")


def test_a_nan_filament_travel_fails() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", float("nan"), -10.0, at_rest=False)
    advance(reactor)
    assert "the probe reported no filament travel" in lines(adapter)[-1]
    assert all(mode != "feed" for _, mode, *_ in ace.feed.started)


# --- fix round 3 ---------------------------------------------------------------------------------


def test_a_follow_off_before_the_eject_stays_off_after_a_recheck_failure() -> None:
    for at in ("probe", "u1"):
        adapter, reactor, left, ace = setup(head=True)
        left.channel_state[1] = "load_finish"  # loaded, but its follow not armed (off by hand)
        if at == "probe":
            adapter.ejector.start_eject(LANE)
            adapter.print_stats = fakes.FakePrintStats("printing")
            reactor.run_timers()
        else:
            move = held_until_feed_back(adapter, reactor, ace)
            adapter.print_stats = fakes.FakePrintStats("printing")
            move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0}
            advance(reactor)
        assert all(mode != "assist_both" for _, mode, *_ in ace.feed.started), at
        assert lines(adapter)[-1].endswith("the follow stays off until e0's next load"), at


def test_a_manual_feed_channel_state_is_not_armed() -> None:
    for state in ("manual_sta_finish", "manual_sta_prepare_finish"):
        adapter, reactor, left, ace = setup(head=True)
        left._set_channel_state(1, "load_finish", True)
        move = held_until_feed_back(adapter, reactor, ace)
        left.channel_state[1] = state  # set directly: the hook's own reaction left out
        adapter.print_stats = fakes.FakePrintStats("printing")
        move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0}
        advance(reactor)
        assert last_start(ace)[1] == "feed", state
        assert lines(adapter)[-1].endswith("the follow stays off until e0's next load"), state


def test_the_restore_holds_off_the_standing_re_arm_while_it_waits() -> None:
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)
    adapter.ejector.start_eject(LANE)
    h = next(x for x in adapter.hooked if x.lane == LANE)
    adapter.print_stats = fakes.FakePrintStats("printing")
    ace.feed.refusals = ["busy", "busy"]  # the stopped follow winding down
    seen = []
    reactor.on_pause = [lambda now: seen.append((h.hold, h.rearm_tried))]
    reactor.run_timers()
    assert seen and all(hold for hold, _ in seen)  # no nested standing re-arm meanwhile
    assert h.armed and not h.hold
    assert [line for line in adapter.gcode.lines if "not armed" in line] == []


# --- the probe judged by the buffer plunger ---------------------------------------


def test_the_bench_case_a_full_travel_with_the_plunger_pushed_is_held() -> None:
    # measured: a tip held in the head's gear, the probe read 9.9 mm at the unit — the buffer
    # absorbed it — and the plunger ended pushed
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.2, at_rest=False)
    advance(reactor)
    assert "held by the head (plunger moved)" in lines(adapter)[-1]
    assert last_start(ace)[1:3] == ("feed", 9.9)


def test_a_short_travel_with_the_plunger_at_rest_is_free() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -2.0, -10.0, at_rest=True)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert last_start(ace)[1] == "unload"


def test_rest_and_pushed_both_set_is_not_at_rest() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=False)
    ace.lanes[LANE].update(rest=True, pushed=True)
    advance(reactor)
    assert "plunger moved" in lines(adapter)[-1]


def test_the_plunger_is_read_after_the_settle_pause_from_the_reactor() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 0.5
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=False)  # the report not yet in
    advance(reactor, 0.1)
    assert lines(adapter) == [] and not job.decided
    timer = job.timer
    assert timer in reactor.timers and timer[1] > reactor.now  # waiting on the reactor
    assert timer[0](reactor.now) == job.settle_at  # an early run judges nothing, waits on
    assert lines(adapter) == []
    plunger(ace, True)  # the report arrives: at rest
    advance(reactor, 0.3)
    assert lines(adapter) == []  # still within the settle
    advance(reactor, 0.2)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert [m for _, m, *_ in ace.feed.started].count("unload") == 1


def test_a_re_entered_timer_during_the_settle_does_nothing() -> None:
    # the reactor runs the eject's timer again while its call judging the settled plunger is
    # still in progress: the re-entered call leaves at once, the filament judged and unloaded once
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 0.5
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=True)
    advance(reactor, 0.1)
    reentered = []
    read = adapter.ejector._plunger_at_rest

    def reading_while_reentered(h: EjectJob) -> bool:
        if not reentered:
            reentered.append(job.timer[0](reactor.now))
        return read(h)

    adapter.ejector._plunger_at_rest = reading_while_reentered
    advance(reactor, 0.5)
    assert reentered == [reactor.NEVER]
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert [m for _, m, *_ in ace.feed.started].count("unload") == 1


def test_a_stale_plunger_reading_counts_as_held() -> None:
    # no fresh sensors report can be told (the link not proven): the reading may be from before
    # the probe — held, the safe side
    adapter, reactor, left, ace = setup(head=True)
    ace.link_proven = False
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=True)
    advance(reactor)
    assert "held by the head (plunger moved)" in lines(adapter)[-1]


def test_a_print_started_during_the_settle_ends_the_eject() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 0.5
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=True)
    advance(reactor, 0.1)
    adapter.print_stats = fakes.FakePrintStats("printing")
    advance(reactor, 0.5)
    assert lines(adapter)[-1].startswith("ace2k_u1: lane 1 eject failed: a print started")
    assert all(m != "unload" for _, m, *_ in ace.feed.started)


def test_the_plunger_after_the_feed_back_is_read_after_its_settle() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 0.5
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=False)
    advance(reactor, 0.1)  # the probe's end seen: the settle starts
    advance(reactor, 0.5)  # held, the feed back started
    assert last_start(ace)[1] == "feed"
    move = last_start(ace)[4]
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0}
    advance(reactor, 0.1)  # the feed back's end seen; its report not in yet
    assert job.timer[0](reactor.now) == job.settle_at
    assert adapter.gcode.scripts == []
    plunger(ace, True)  # the report arrives within the settle: at rest
    advance(reactor, 0.5)  # the settle, counted from the feed back's end
    assert not any("not at rest" in line for line in adapter.gcode.lines)
    assert adapter.gcode.scripts == [U1_UNLOAD]


def test_a_plunger_not_at_rest_after_the_feed_back_is_said_and_the_u1_unload_goes_on() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=False)
    advance(reactor)
    move = last_start(ace)[4]
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 1.0}
    advance(reactor)  # the plunger still pushed
    assert (
        "ace2k_u1: lane 1 eject: plunger not at rest after the feed back — going on, the U1's"
        " unload settles it"
    ) in adapter.gcode.lines
    assert adapter.gcode.scripts == [U1_UNLOAD]
    adapter2, reactor2, left2, ace2 = setup(head=True)
    adapter2.ejector.start_eject(LANE)
    reactor2.run_timers()
    end_probe(ace2, "done", -9.9, -10.0, at_rest=False)
    advance(reactor2)
    plunger(ace2, True)  # the feed back put it back at rest: nothing to say
    last_start(ace2)[4].event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0}
    advance(reactor2)
    assert not any("not at rest" in line for line in adapter2.gcode.lines)
    assert adapter2.gcode.scripts == [U1_UNLOAD]


def test_every_normal_path_eject_line_classifies_as_info() -> None:
    """Only a real failure may show red in the web page's event list."""
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stuck", -1.2, -22.0, at_rest=False)
    advance(reactor)
    seen = lines(adapter)  # the held line, as the adapter says it
    seen += [
        template.format(n=1, why="plunger moved", s=ace2k_u1_eject.EJECT_WAIT_MAX_S, mm=39.5)
        for template in ace2k_u1_eject.EJECT_SAY.values()
    ]
    assert any("no decision after" in s for s in seen)
    assert any("held by the head" in s for s in seen)
    for line in seen:
        assert hist.classify(line)[0] == "info", line


# --- the probe takes up the buffer's slack first, and the unload's safety net ------------------


def unload_end(ace: FakeAce2k, kind: str, filament_mm: float, motor_mm: float) -> FeedEvent:
    """The final event of the unit's unload the eject started, as ace2k sends it."""
    lane, mode, length, speed, move = last_start(ace)
    assert mode == "unload"
    return {
        "kind": kind,
        "mode": "unloading",
        "seq": move.seq,
        "motor_mm": motor_mm,
        "filament_mm": filament_mm,
    }


def test_the_probe_bound_covers_the_buffers_slack() -> None:
    # a follow leaves up to ≈ 50 mm of slack between the unit and the head (bench: 9.9 + 39.5 mm
    # came out before the gear held): the probe's default bound is well past it
    adapter, reactor, left, ace = setup(head=True)
    assert adapter.eject_probe_mm == ace2k_u1_eject.EJECT_PROBE_MM == 80.0
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert last_start(ace)[1:3] == ("rollback", 80.0)


def test_a_plunger_gone_taut_during_the_probe_stops_it_and_is_held() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    move = last_start(ace)[4]
    advance(reactor, 0.5)  # the slack taken up, the plunger still at rest
    assert ("stop", LANE) not in ace.feed.log and lines(adapter) == []
    plunger(ace, False)  # the slack gone: the head's gear holds, the plunger goes taut
    advance(reactor, 0.1)
    assert ("stop", LANE) not in ace.feed.log  # one taut report is not yet believed
    advance(reactor, 0.1)
    assert ace.feed.log[-1] == ("stop", LANE)
    advance(reactor, 0.1)
    assert ace.feed.log.count(("stop", LANE)) == 1  # one stop, while the end is awaited
    move.event = probe_end("stopped", -41.0, -42.5)
    advance(reactor)
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject: held by the head (plunger taut) — unloading the head on the U1"
    ]
    assert last_start(ace)[1:3] == ("feed", 41.0)
    last_start(ace)[4].event = {"kind": "blocked", "mode": "feeding", "motor_mm": 42.5}
    plunger(ace, True)
    advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]


def test_an_unproven_link_keeps_the_short_probe_and_counts_as_held() -> None:
    # no taut stop can be trusted: the probe must not grind 80 mm against a held tip
    adapter, reactor, left, ace = setup(head=True)
    ace.link_proven = False
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert (
        last_start(ace)[1:3]
        == ("rollback", ace2k_u1_eject.EJECT_PROBE_SHORT_MM)
        == ("rollback", 10.0)
    )
    plunger(ace, False)
    advance(reactor, 0.5)
    assert ("stop", LANE) not in ace.feed.log  # the settle's reading decides (held, here)
    end_probe(ace, "done", -9.9, -10.0, at_rest=True)
    advance(reactor)
    assert "held by the head (plunger moved)" in lines(adapter)[-1]


def test_a_stale_taut_reading_at_the_start_does_not_stop_a_free_probe() -> None:
    # the report from before the rollback still says pushed (the follow's last pull); the next
    # reports say rest: the filament is free and the probe runs to its end
    adapter, reactor, left, ace = setup(head=True)
    plunger(ace, False)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.1)
    advance(reactor, 0.1)
    plunger(ace, True)
    for _ in range(10):
        advance(reactor, 0.1)
    assert ("stop", LANE) not in ace.feed.log
    end_probe(ace, "done", -80.0, -80.5, at_rest=True)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]


def test_a_single_fresh_taut_reading_does_not_stop_the_probe() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.5)
    plunger(ace, False)
    advance(reactor, 0.1)
    plunger(ace, True)  # a flicker: back at rest on the next report
    advance(reactor, 0.1)
    plunger(ace, False)
    advance(reactor, 0.1)
    assert ("stop", LANE) not in ace.feed.log


def test_the_taut_watch_polls_at_the_report_rate_and_logs_a_status_failure_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert job.timer[1] == pytest.approx(reactor.now + 0.1)  # 10 Hz, the unit's switch reports
    real = ace.get_status

    def broken(eventtime: float) -> NoReturn:
        raise RuntimeError("status gone")

    ace.get_status = broken
    with caplog.at_level("ERROR"):
        for _ in range(5):
            advance(reactor, 0.1)
    ace.get_status = real
    unread = [r for r in caplog.records if "plunger unread" in r.getMessage()]
    assert len(unread) == 1
    assert ("stop", LANE) not in ace.feed.log


def taut_stopped(adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k) -> None:
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.3)
    plunger(ace, False)
    advance(reactor, 0.1)
    advance(reactor, 0.1)
    assert ("stop", LANE) in ace.feed.log


@pytest.mark.parametrize("travel", [None, float("nan")])
def test_a_taut_stop_with_no_filament_figure_is_held_fed_back_by_the_motor(
    travel: float | None,
) -> None:
    # stopped at the taut reading, before the motor slipped: its travel is what left
    adapter, reactor, left, ace = setup(head=True)
    taut_stopped(adapter, reactor, ace)
    last_start(ace)[4].event = probe_end("stopped", travel, -42.5)
    advance(reactor)
    assert "held by the head (plunger taut)" in lines(adapter)[-1]
    assert last_start(ace)[1:3] == ("feed", 42.5)


def test_a_taut_stop_with_no_travel_at_all_goes_to_the_u1_unload() -> None:
    adapter, reactor, left, ace = setup(head=True)
    taut_stopped(adapter, reactor, ace)
    last_start(ace)[4].event = probe_end("stopped", None, None)
    advance(reactor)
    assert "held by the head (plunger taut)" in lines(adapter)[-1]
    assert all(m != "feed" for _, m, *_ in ace.feed.started)
    assert adapter.gcode.scripts == [U1_UNLOAD]


def test_a_probe_ended_by_a_lane_error_after_the_taut_stop_is_held_too() -> None:
    adapter, reactor, left, ace = setup(head=True)
    taut_stopped(adapter, reactor, ace)
    last_start(ace)[4].event = probe_end("stuck", -40.0, -60.0)  # the unit's own end came first
    advance(reactor)
    assert ace.feed.cleared == [LANE]
    assert "held by the head (plunger taut)" in lines(adapter)[0]


def test_the_bench_case_slack_then_the_unload_meets_the_gear_takes_the_u1_unload() -> None:
    # Seen on the U1: a following lane with slack, the tip in the head's gear. The probe (then
    # 10 mm) only took up slack: done at -9.9 mm, the plunger at rest — "free"; the unit's unload
    # met the gear and ended stuck at -39.5 mm. The eject clears the lane itself, feeds the
    # unload's travel back and goes the held way: the U1's unload of the head
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)  # the follow armed on the loaded head
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, 9.1, at_rest=True)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert job.decided
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -39.5, 69.1))
    assert not any("eject failed" in line for line in lines(adapter))
    assert ace.feed.cleared == []  # nothing from the event handler: the timer does it
    advance(reactor)
    assert ace.feed.cleared == [LANE]
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject: the unload met the head's gear 39.5 mm in — the lane cleared,"
        " held by the head after all — unloading the head on the U1"
    )
    # what left the unit: the probe's 9.9 mm and the unload's 39.5 mm, not the slipping motor
    assert last_start(ace)[0] == LANE and last_start(ace)[1] == "feed"
    assert last_start(ace)[2] == pytest.approx(49.4)
    last_start(ace)[4].event = {"kind": "blocked", "mode": "feeding", "motor_mm": 49.4}
    plunger(ace, True)
    advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]
    assert LANE not in adapter.ejector.eject_jobs


def test_the_safety_net_runs_once_a_second_stuck_fails() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 80.4, at_rest=True)
    advance(reactor)
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -20.0, 45.0))
    advance(reactor)
    assert last_start(ace)[1:3] == ("feed", 100.0)
    last_start(ace)[4].event = {"kind": "blocked", "mode": "feeding", "motor_mm": 100.0}
    plunger(ace, True)
    advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]
    # the U1's unload ends: the unit's unload as after any, the net not armed for it
    left._set_channel_state(1, "unload_finish", True)
    advance(reactor)
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -10.0, 40.0))
    advance(reactor)
    assert lines(adapter)[-1] == "ace2k_u1: lane 1 eject failed: stuck — ACEK_CLEAR LANE=1"
    assert adapter.gcode.scripts == [U1_UNLOAD] and ace.feed.cleared == [LANE]
    assert LANE not in adapter.ejector.eject_jobs


def test_free_filament_ejects_directly() -> None:
    # lane 4's case on the U1: the tip at the head's sensor, not in its gear
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    adapter.on_feed_event(LANE, unload_end(ace, "unloaded", -1918.3, 1950.0))
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert adapter.gcode.scripts == [] and LANE not in adapter.ejector.ejects


def test_a_stuck_far_into_the_unload_stays_a_failure() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -1914.5, 1960.0))
    advance(reactor)
    assert lines(adapter)[-1] == "ace2k_u1: lane 1 eject failed: stuck — ACEK_CLEAR LANE=1"
    assert adapter.gcode.scripts == [] and ace.feed.cleared == []
    assert all(mode != "feed" for _, mode, *_ in ace.feed.started)


def test_the_net_needs_the_head_still_set_and_a_free_verdict() -> None:
    # the head's sensor clear by the stuck: the filament is not in the head — a real failure
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)
    left.runout_sensor[1].detected = False
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -30.0, 60.0))
    advance(reactor)
    assert lines(adapter)[-1] == "ace2k_u1: lane 1 eject failed: stuck — ACEK_CLEAR LANE=1"
    # the head's sensor unreadable: no guess, a real failure
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)

    def unreadable(eventtime: float) -> NoReturn:
        raise RuntimeError("sensor gone")

    left.runout_sensor[1].get_status = unreadable
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -30.0, 60.0))
    advance(reactor)
    assert lines(adapter)[-1] == "ace2k_u1: lane 1 eject failed: stuck — ACEK_CLEAR LANE=1"
    assert adapter.gcode.scripts == [] and ace.feed.cleared == []
    # the head empty from the start: no probe, no net
    adapter, reactor, left, ace = setup(head=False)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    left.runout_sensor[1].detected = True
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -30.0, 60.0))
    advance(reactor)
    assert lines(adapter)[-1] == "ace2k_u1: lane 1 eject failed: stuck — ACEK_CLEAR LANE=1"
    assert adapter.gcode.scripts == []


def test_a_new_eject_is_refused_while_the_net_runs() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -39.5, 69.1))
    refused(adapter, "an eject is already running")


def test_the_new_eject_lines_classify_as_info() -> None:
    for line in (
        ace2k_u1_eject.EJECT_SAY["net"].format(n=3, mm=39.5),
        ace2k_u1_eject.EJECT_SAY["held"].format(n=3, why="plunger taut"),
    ):
        assert hist.classify(line)[0] == "info", line


@pytest.mark.parametrize("kind", ["stuck", "motor_stalled", "tangled"])
def test_the_net_engages_on_the_kinds_a_held_gear_ends(kind: str) -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)
    adapter.on_feed_event(LANE, unload_end(ace, kind, -30.0, 60.0))
    advance(reactor)
    assert "held by the head after all" in lines(adapter)[-1]
    assert ace.feed.cleared == [LANE]
    assert last_start(ace)[1:3] == ("feed", 110.0)  # 80 + 30 mm left the unit


@pytest.mark.parametrize("kind", ["unload_incomplete", "timeout", "runout"])
def test_the_net_leaves_other_kinds_a_real_failure(kind: str) -> None:
    # unload_incomplete: a standstill with the plunger at rest — nothing pulls on the buffer, so
    # not the head's gear; with the head's sensor still on this lane's filament, not its end at
    # the bay either (that is the short filament's, test_states)
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)
    adapter.on_feed_event(LANE, unload_end(ace, kind, -30.0, 60.0))
    advance(reactor)
    assert lines(adapter)[-1] == f"ace2k_u1: lane 1 eject failed: {kind} — ACEK_CLEAR LANE=1"
    assert adapter.gcode.scripts == [] and ace.feed.cleared == []


# --- review round 2 ------------------------------------------------------------------------------


def test_the_same_report_read_twice_does_not_stop_the_probe() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.5)
    ace.clock = None
    ace.reports = 5000  # one report, newer than the probe's start, and no other arriving
    plunger(ace, False)
    for _ in range(5):
        advance(reactor, 0.1)
    assert ("stop", LANE) not in ace.feed.log
    ace.reports = 5001  # a second, distinct report, still taut
    advance(reactor, 0.1)
    assert ("stop", LANE) in ace.feed.log


def test_an_ace2k_without_a_report_count_keeps_the_short_probe() -> None:
    adapter, reactor, left, ace = setup(head=True)
    ace.has_reports = False
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert last_start(ace)[1:3] == ("rollback", 10.0)


def free_then_unload(adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k) -> None:
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)
    assert last_start(ace)[1] == "unload"


def test_the_net_does_not_engage_during_a_print() -> None:
    adapter, reactor, left, ace = setup(head=True)
    free_then_unload(adapter, reactor, ace)
    adapter.print_stats = fakes.FakePrintStats("printing")
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -30.0, 60.0))
    advance(reactor)
    assert lines(adapter)[-1] == "ace2k_u1: lane 1 eject failed: stuck — ACEK_CLEAR LANE=1"
    assert ace.feed.cleared == [] and adapter.gcode.scripts == []
    assert LANE not in adapter.ejector.eject_jobs


def test_the_net_does_not_engage_during_a_u1_flow() -> None:
    adapter, reactor, left, ace = setup(head=True)
    free_then_unload(adapter, reactor, ace)
    left.channel_state[1] = "unload_prepare"
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -30.0, 60.0))
    advance(reactor)
    assert lines(adapter)[-1] == "ace2k_u1: lane 1 eject failed: stuck — ACEK_CLEAR LANE=1"
    assert ace.feed.cleared == [] and adapter.gcode.scripts == []


def test_a_link_lost_during_the_probe_stops_it_at_once_held() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert last_start(ace)[2] == 80.0
    advance(reactor, 0.3)
    ace.link_proven = False
    advance(reactor, 0.1)
    assert ace.feed.log[-1] == ("stop", LANE)
    last_start(ace)[4].event = probe_end("stopped", -5.0, -5.1)
    advance(reactor)
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject: held by the head (link lost) — unloading the head on the U1"
    )
    assert last_start(ace)[1:3] == ("feed", 5.0)


def test_the_taut_freshness_is_two_reports_whatever_the_settle() -> None:
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 2.0  # eject_settle_ms at its maximum
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.3)
    plunger(ace, False)
    advance(reactor, 0.1)
    advance(reactor, 0.1)
    assert ("stop", LANE) in ace.feed.log  # 0.5 s in, not after 2 s


def test_a_taut_stop_that_did_not_reach_the_unit_is_retried() -> None:
    adapter, reactor, left, ace = setup(head=True)
    real = ace.feed.stop
    fails = [RuntimeError("link busy")]

    def flaky(lane: int) -> None:
        if fails:
            raise fails.pop()
        real(lane)

    ace.feed.stop = flaky
    taut_ready(adapter, reactor, ace)
    advance(reactor, 0.1)  # the stop raised
    assert ("stop", LANE) not in ace.feed.log
    advance(reactor, 0.1)  # tried again
    assert ace.feed.log.count(("stop", LANE)) == 1
    advance(reactor, 0.3)
    assert ace.feed.log.count(("stop", LANE)) == 1
    last_start(ace)[4].event = probe_end("stopped", -40.0, -41.0)
    advance(reactor)
    assert "held by the head (plunger taut)" in lines(adapter)[-1]


def test_a_taut_stop_that_never_reaches_the_unit_is_bounded_and_held_on_its_end() -> None:
    adapter, reactor, left, ace = setup(head=True)
    tries = []

    def broken(lane: int) -> NoReturn:
        tries.append(lane)
        raise RuntimeError("link busy")

    ace.feed.stop = broken
    taut_ready(adapter, reactor, ace)
    for _ in range(10):
        advance(reactor, 0.1)
    assert len(tries) == ace2k_u1_eject.EJECT_STOP_TRIES == 3
    last_start(ace)[4].event = probe_end("stuck", -40.0, -70.0)  # the unit's own end
    advance(reactor)
    assert "held by the head (plunger taut)" in lines(adapter)[-1]
    assert ace.feed.cleared == [LANE]


def taut_ready(adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k) -> None:
    """A probe whose plunger read taut once: the next poll stops it."""
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.3)
    plunger(ace, False)
    advance(reactor, 0.1)


def test_an_eject_probe_below_the_new_minimum_is_raised_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        u1 = strict_u1({"eject_probe_mm": 10.0})
    assert u1.eject_probe_mm == 20.0
    warned = [r.getMessage() for r in caplog.records if "eject_probe_mm" in r.getMessage()]
    assert warned == ["ace2k_u1: eject_probe_mm 10 is below its minimum 20 — 20 used"]
    assert strict_u1({"eject_probe_mm": 5.0}).eject_probe_mm == 20.0
    with pytest.raises(ValueError):
        strict_u1({"eject_probe_mm": 4.9})  # outside even the old range


def test_the_raised_probe_is_said_on_the_console_at_the_hook() -> None:
    u1, reactor, left, ace, gcode = hooked_u1({"eject_probe_mm": 10.0})
    u1._hook(reactor.now)
    assert "ace2k_u1: eject_probe_mm 10 is below its minimum 20 — 20 used" in gcode.lines
    assert u1.adapter.eject_probe_mm == 20.0


# --- the feed back's own refusals and timeout (characterisation) ---------------------------------


def probe_held(
    adapter: Adapter,
    reactor: FakeReactor,
    ace: FakeAce2k,
    refusals: Iterable[str] = (),
    refuse: str | None = None,
) -> None:
    """The probe ended held; the next feed back start will answer as asked."""
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    plunger(ace, False)
    last_start(ace)[4].event = probe_end("done", -2.0, -10.0)
    ace.feed.refusals = list(refusals)
    ace.feed.refuse = refuse


def probe_held_then(
    adapter: Adapter,
    reactor: FakeReactor,
    ace: FakeAce2k,
    refusals: Iterable[str] = (),
    refuse: str | None = None,
) -> None:
    """The probe ended held; the next feed back start answers as asked, then runs the tick."""
    probe_held(adapter, reactor, ace, refusals, refuse)
    advance(reactor)


def test_a_busy_feed_back_is_retried_then_started() -> None:
    adapter, reactor, left, ace = setup(head=True)
    probe_held_then(adapter, reactor, ace, refusals=["busy"])
    assert last_start(ace)[1] == "rollback"  # the feed back not started yet: retried
    assert not any("failed" in line for line in lines(adapter))
    advance(reactor, ace2k_u1_common.BUSY_RETRY_POLL_S)
    assert last_start(ace)[:3] == (LANE, "feed", 2.0)


def test_a_refused_feed_back_fails_the_eject() -> None:
    adapter, reactor, left, ace = setup(head=True)
    probe_held_then(adapter, reactor, ace, refuse="no_link")
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject failed: the feed back not started — refused: no_link"
        "; the follow stays off until e0's next load"
    )
    assert adapter.gcode.scripts == [] and LANE not in adapter.ejector.eject_jobs


def test_a_print_started_before_the_feed_back_ends_the_eject_at_its_step() -> None:
    adapter, reactor, left, ace = setup(head=True)
    # the step the held probe leaves behind runs with a print started since
    probe_held(adapter, reactor, ace)
    adapter.print_stats = fakes.FakePrintStats("printing")
    advance(reactor)
    assert len(ace.feed.started) == 1  # the probe only: no feed back
    assert lines(adapter)[-1].startswith("ace2k_u1: lane 1 eject failed: a print started")


def test_a_feed_back_with_no_end_is_stopped_and_fails() -> None:
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    advance(reactor, 1.0)  # within twice its travel time plus the margin: still running
    assert not [x for x in lines(adapter) if "failed" in x]
    assert ("stop", LANE) not in ace.feed.log
    advance(reactor, ace2k_u1_eject.EJECT_MARGIN_S)
    assert ("stop", LANE) in ace.feed.log
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject failed: the feed back got no end; the follow stays off until"
        " e0's next load"
    )
    assert adapter.gcode.scripts == [] and move.event is None


def test_a_unit_with_host_interface_17_bounds_the_ejects_probe_by_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe's wait is ace2k's budget of the move (feed.move_wait_s)."""
    monkeypatch.setattr(fakes.FakeFeedApi, "API_VERSION", 17)
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert last_start(ace)[1] == "rollback"
    assert ace.feed.waits == [(LANE, adapter.eject_probe_mm, adapter.eject_probe_speed)]


def test_on_host_interface_17_the_ejects_probe_counts_the_tail_and_its_feed_back_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe is a roll back: its wait asks ace2k's budget with reverse (the tail the unit
    runs once the filament leaves the motor). The feed back after it is a forward move."""
    monkeypatch.setattr(fakes.FakeFeedApi, "API_VERSION", 17)
    adapter, reactor, left, ace = setup(head=True)
    held_until_feed_back(adapter, reactor, ace)
    probe = (LANE, adapter.eject_probe_mm, adapter.eject_probe_speed)
    assert ace.feed.waits[0] == probe and len(ace.feed.waits) == 2
    assert ace.feed.reverse_waits == [probe]


# --- final events of several moves (the unit reports the whole mode) -----------------------------


def test_a_held_probe_that_ran_on_with_its_tail_feeds_back_the_whole_roll_back() -> None:
    """The probe's roll back passed its deadline with the plunger at rest and went on with a tail
    move. Its final event carries the whole roll back, 125 mm, and that is what is fed back."""
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -125.0, 140.0, at_rest=False)
    advance(reactor)
    assert "held by the head (plunger moved)" in lines(adapter)[0]
    assert last_start(ace)[1:3] == ("feed", 125.0)


def test_the_net_feeds_back_the_whole_unload_after_a_touch() -> None:
    """The unload's tip caught and came back after a touch, then the motor stalled 45 mm in. The
    final event carries the whole unload, so the net feeds back the probe's 80 mm and all 45."""
    adapter, reactor, left, ace = setup(head=True)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -80.0, 81.0, at_rest=True)
    advance(reactor)
    adapter.on_feed_event(LANE, unload_end(ace, "motor_stalled", -45.0, 70.0))
    advance(reactor)
    assert "held by the head after all" in lines(adapter)[-1]
    assert last_start(ace)[1:3] == ("feed", 125.0)


def test_an_unload_whose_tail_move_ran_its_length_says_the_whole_travel_back() -> None:
    """The unload went on with a tail move that ran its length, then ended unload_incomplete. The
    final event carries the whole unload, 453 mm: that is the travel the line gives back."""
    adapter, reactor, left, ace = setup(head=False)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    adapter.on_feed_event(LANE, unload_end(ace, "unload_incomplete", -453.0, 470.0))
    advance(reactor)
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 ejected (453 mm back): its tail is past the unit's drive gear, at its"
        " bay: take it out by hand"
    )
    assert ace.feed.cleared == [LANE]


def test_an_unload_that_carried_a_whole_move_with_its_tail_counts_all_of_it() -> None:
    """The unload carried 1950 mm, its tail move included, and ended unload_incomplete: a whole
    move. All of it is spent from head_budget_mm, so the next unload asks for the 1050 mm left."""
    adapter, reactor, left, ace = setup(head=False)
    adapter.head_budget_mm = 3000.0
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    adapter.on_feed_event(LANE, unload_end(ace, "unload_incomplete", -1950.0, 1990.0))
    advance(reactor)
    assert ace.feed.cleared == [LANE]
    assert last_start(ace)[1] == "unload" and last_start(ace)[2] == pytest.approx(1050.0)


# --- the unit's own stop on its plunger (ace2k host interface 20) --------------------------------
# An image with has_stop_when ends the probe itself: "held" when the plunger is taut and the
# filament stands still. The host then reads no status while the probe runs. An image without it
# keeps the host's taut watch. Each path runs on both images.

HELD_ONLY = frozenset({"held"})


def held_end(filament_mm: float | None, motor_mm: float | None) -> FeedEvent:
    """The final event of a probe that the unit ended itself on a held tip."""
    return {
        "kind": "held",
        "mode": "rolling_back",
        "motor_mm": motor_mm,
        "filament_mm": filament_mm,
    }


def hold_tip(adapter: Adapter, reactor: FakeReactor, ace: FakeAce2k, stop: bool) -> int:
    """Run a probe whose tip the head holds, up to its final event. Return the status reads made
    while it ran.

    The plunger goes taut 0.5 s in. With the unit's stop, the unit ends the probe "held" 0.6 s
    later, and the host never stops it. Without it, the host's taut watch stops it, and the unit
    answers that stop."""
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    reads = ace.status_reads
    advance(reactor, 0.5)
    plunger(ace, False)
    for _ in range(6):
        advance(reactor, 0.1)
    move = last_start(ace)[4]
    if stop:
        assert ("stop", LANE) not in ace.feed.log  # the unit decides, not the host
        move.event = held_end(-41.0, -46.5)
    else:
        assert ace.feed.log.count(("stop", LANE)) == 1
        move.event = probe_end("stopped", -41.0, -42.5)
    reads = ace.status_reads - reads
    advance(reactor)
    return reads


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_a_tip_held_by_the_head_is_held_on_both_images(stop: bool) -> None:
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = stop
    reads = hold_tip(adapter, reactor, ace, stop)
    assert ace.feed.started[0][:4] == (LANE, "rollback", 80.0, 10.0)
    assert ace.feed.stop_whens[0] == (LANE, "rollback", HELD_ONLY if stop else fakes.NOT_PASSED)
    # about 6 mm of motor at 10 mm/s: the eject's own window, not the unit's 550 ms
    assert ace.feed.held_mss[0] == (LANE, "rollback", 600 if stop else fakes.NOT_PASSED)
    assert (reads == 0) is stop  # the host polls the plunger only without the unit's stop
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject: held by the head (plunger taut) — unloading the head on the U1"
    ]
    lane, mode, length, speed, move = last_start(ace)
    assert (lane, mode, length, speed) == (LANE, "feed", 41.0, 10.0)  # what left, not the motor
    assert ace.feed.stop_whens[-1] == (LANE, "feed", fakes.NOT_PASSED)  # the feed back: no stop
    assert ace.feed.cleared == []
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 41.5, "filament_mm": 41.0}
    plunger(ace, True)
    advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]


@pytest.mark.parametrize("stop", [True, False], ids=["unit_stop", "host_watch"])
def test_a_free_filament_is_free_on_both_images(stop: bool) -> None:
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = stop
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    reads = ace.status_reads
    for _ in range(5):
        advance(reactor, 0.1)
    assert (ace.status_reads == reads) is stop
    assert ace.feed.stop_whens[0] == (LANE, "rollback", HELD_ONLY if stop else fakes.NOT_PASSED)
    end_probe(ace, "done", -80.0, -80.5, at_rest=True)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert last_start(ace)[:2] == (LANE, "unload")
    assert ace.feed.stop_whens[-1] == (LANE, "unload", fakes.NOT_PASSED)


def test_with_the_units_stop_a_taut_plunger_alone_does_not_stop_the_probe() -> None:
    """A thick tip dragged back through the tube pulls the plunger taut while the filament still
    comes. The unit runs on (no false held), and so does the host: it does not read the plunger."""
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = True
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    plunger(ace, False)
    for _ in range(20):
        advance(reactor, 0.1)
    assert ("stop", LANE) not in ace.feed.log
    end_probe(ace, "done", -80.0, -80.5, at_rest=True)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]


def test_with_the_units_stop_an_arming_refused_busy_is_retried() -> None:
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = True
    ace.feed.refusals = ["busy"]  # the unit refuses the arming while the stopped follow ends
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.25)
    assert ace.feed.stop_whens == [(LANE, "rollback", HELD_ONLY)] * 2
    assert last_start(ace)[1:3] == ("rollback", 80.0)


def test_with_the_units_stop_an_unproven_link_keeps_the_short_probe() -> None:
    """The gate on the link at the start stays as it is: without a proven link the probe is the
    short one, judged after its end. The unit's stop is armed on it too."""
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = True
    ace.link_proven = False
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert last_start(ace)[1:3] == ("rollback", 10.0)
    assert ace.feed.stop_whens[-1] == (LANE, "rollback", HELD_ONLY)
    end_probe(ace, "done", -9.9, -10.0, at_rest=True)
    advance(reactor)
    assert "held by the head (plunger moved)" in lines(adapter)[-1]


def test_a_held_end_with_no_filament_figure_feeds_back_the_motor_less_the_window() -> None:
    """The motor ran the whole held window (600 ms at 10 mm/s, 6 mm) with the filament still, so
    only the rest of its travel left the head."""
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = True
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    last_start(ace)[4].event = held_end(None, -46.5)
    advance(reactor)
    assert "held by the head (plunger taut)" in lines(adapter)[-1]
    assert last_start(ace)[1:3] == ("feed", 40.5)


def test_a_held_end_inside_its_window_feeds_back_nothing() -> None:
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = True
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    starts = len(ace.feed.started)
    last_start(ace)[4].event = held_end(None, -5.0)
    advance(reactor)
    assert len(ace.feed.started) == starts and adapter.gcode.scripts == [U1_UNLOAD]


@pytest.mark.parametrize("event", [True, False], ids=["stopped_link", "no_event"])
def test_with_the_units_stop_a_lost_link_fails_the_eject_with_no_read_and_no_stop(
    event: bool,
) -> None:
    """The host reads no status during an armed probe, so it does not see the link go. The unit
    stops the motor on its own link check: the probe ends stopped_link, or its event is lost
    with the link. Either way the eject fails, with no heated unload on a guess."""
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = True
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    reads = ace.status_reads
    ace.link_proven = False
    for _ in range(30):
        advance(reactor, 0.1)
    assert ace.status_reads == reads  # 3 s of the probe with no read of the plunger or the link
    assert ("stop", LANE) not in ace.feed.log
    if event:
        last_start(ace)[4].event = probe_end("stopped_link", -3.0, -3.1)
        advance(reactor)
        why = "the probe ended stopped_link"
    else:
        for _ in range(600):  # the probe's deadline: its move's wait, then the margin
            advance(reactor, 0.1)
            if lines(adapter):
                break
        why = "the probe got no end"
    assert lines(adapter)[-1].startswith(f"ace2k_u1: lane 1 eject failed: {why}")
    assert adapter.gcode.scripts == []


def test_a_truthy_has_stop_when_that_is_not_true_arms_nothing() -> None:
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_stop_when = MagicMock()  # an attribute of a mock: truthy, but not True
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    assert ace.feed.stop_whens[0] == (LANE, "rollback", fakes.NOT_PASSED)
    assert ace.feed.held_mss[0] == (LANE, "rollback", fakes.NOT_PASSED)


# --- the watch of the lane while the U1 unloads the head ---------------------------------------
# The tach counts pulses either way, and the speed of the state report is the setpoint, never
# signed. An ace2k with the motor report (host interface 25) gives the travel each way; with an
# older one the watch tells the direction from the follow's corrections that the unit counts each
# way: a taut catch-up or a forward dose goes toward the head, a full take-up or a reverse dose
# goes back.

PULSES_PER_MM = 12.35


def watch_lane(ace: FakeAce2k, **fields: object) -> None:
    ace.lanes[LANE].update(fields)


def watched_unload(steps: list[dict[str, object]], dt: float = 1.0) -> type[fakes.FakeGcode]:
    """A U1 unload that lasts dt seconds per step: each step's lane fields are set (its
    motor_reports is the unit's count instead), then dt of the reactor runs (the watch's timer
    with it)."""

    class UnloadingGcode(fakes.FakeGcode):
        ace: FakeAce2k
        reactor: FakeReactor

        def run_script(self, script: str) -> None:
            super().run_script(script)
            for step in steps:
                fields = dict(step)
                if "motor_reports" in fields:
                    self.ace.motor_reports = fields.pop("motor_reports")  # type: ignore[assignment]
                watch_lane(self.ace, **fields)
                self.reactor.now += dt
                self.reactor.run_timers()

    return UnloadingGcode


def pulses(mm: float) -> int:
    return int(round(mm * PULSES_PER_MM))


def run_watched(
    steps: list[dict[str, object]],
    caplog: pytest.LogCaptureFixture,
    unit_report: bool = False,
    watches: list[bool] | None = None,
    reports: int | None = None,
    dt: float = 1.0,
) -> list[str]:
    adapter, reactor, left, ace = setup(head=True)
    if unit_report:  # an ace2k with the motor report (host interface 25)
        ace.feed.has_motor_travel = True  # type: ignore[attr-defined]
        calls = [] if watches is None else watches
        ace.feed.motor_watch = lambda on: calls.append(on) or True  # type: ignore[attr-defined]
        watch_lane(ace, motor_fwd_mm=500.0, motor_back_mm=300.0)
        ace.motor_reports = reports  # the count of the reports, on an ace2k that has it
    watch_lane(
        ace,
        mode="following",
        speed=25.0,
        duty=30,
        fg=1000,
        encoder_mm=50.0,
        ff_taut_fixes=4,
        ff_full_fixes=2,
        ff_doses=0,
        ff_back_doses=0,
        ff_rate=0.0,
    )
    gcode_cls = watched_unload(steps, dt)
    gcode = gcode_cls()
    gcode.ace, gcode.reactor = ace, reactor
    move = held_until_feed_back(adapter, reactor, ace)
    adapter.gcode = gcode
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.4, "filament_mm": 2.0}
    with caplog.at_level("INFO"):
        advance(reactor)
    assert gcode.scripts == [U1_UNLOAD]
    return [r.getMessage() for r in caplog.records if "U1 unload" in r.getMessage()]


def test_the_u1_unload_logs_the_lane_each_second_with_its_direction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    steps: list[dict[str, object]] = [
        # a take-up: the head pushed the filament back, the lane followed it
        dict(fg=1000 + pulses(15), encoder_mm=35.0, ff_full_fixes=3, rest=False, pushed=False),
        # the take-up goes on: no new correction, the direction carries
        dict(fg=1000 + pulses(15) + pulses(5), encoder_mm=30.0),
        # a taut catch-up while the filament still went back: pushed against the head
        dict(
            fg=1000 + pulses(15) + pulses(5) + pulses(10),
            encoder_mm=27.0,
            ff_taut_fixes=5,
            rest=False,
            pushed=True,
        ),
        # still
        dict(rest=True, pushed=False),
    ]
    logged = run_watched(steps, caplog)
    assert logged == [
        "ace2k_u1: lane 1 eject, U1 unload 1 s: following, motor -15.0 mm (back, counted),"
        " filament -15.0"
        " mm, buffer mid, starts fwd 0 back 1, setpoint 25.0 mm/s, duty 30 %, ff rate +0.0 mm/s",
        "ace2k_u1: lane 1 eject, U1 unload 2 s: following, motor -5.0 mm (back, counted),"
        " filament -5.0"
        " mm, buffer mid, starts fwd 0 back 0, setpoint 25.0 mm/s, duty 30 %, ff rate +0.0 mm/s",
        "ace2k_u1: lane 1 eject, U1 unload 3 s: following, motor +10.0 mm (fwd, counted),"
        " filament -3.0"
        " mm, buffer taut, starts fwd 1 back 0, setpoint 25.0 mm/s, duty 30 %, ff rate +0.0 mm/s",
        "ace2k_u1: lane 1 eject, U1 unload 4 s: following, motor 0.0 mm (still, counted),"
        " filament +0.0"
        " mm, buffer rest, starts fwd 0 back 0, setpoint 25.0 mm/s, duty 30 %, ff rate +0.0 mm/s",
        "ace2k_u1: lane 1 eject, U1 unload over after 4 s: motor forward 10.0 mm in 1 s, back"
        " 20.0 mm in 2 s, unsure 0.0 mm in 0 s; filament -23.0 mm; forward while the filament"
        " did not go forward: 10.0 mm in 1 s; direction from the unit in 0 s, from the follow's"
        " corrections in 4 s",
    ]


def test_corrections_both_ways_in_one_second_are_unsure_and_a_reset_counter_restarts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    steps: list[dict[str, object]] = [
        # a forward dose and a take-up in the same second
        dict(fg=1000 + pulses(8), encoder_mm=49.0, ff_doses=1, ff_full_fixes=3),
        # the follow armed again: the unit's counters start from 0; its tach was reset too
        dict(fg=pulses(4), encoder_mm=53.0, ff_taut_fixes=1, ff_full_fixes=0, ff_doses=0),
    ]
    logged = run_watched(steps, caplog)
    assert "motor ±8.0 mm (unsure, counted)" in logged[0] and "starts fwd 1 back 1" in logged[0]
    assert "motor +4.0 mm (fwd, counted)" in logged[1] and "starts fwd 1 back 0" in logged[1]
    assert logged[-1] == (
        "ace2k_u1: lane 1 eject, U1 unload over after 2 s: motor forward 4.0 mm in 1 s, back"
        " 0.0 mm in 0 s, unsure 8.0 mm in 1 s; filament +3.0 mm; forward while the filament did"
        " not go forward: 0.0 mm in 0 s; direction from the unit in 0 s, from the follow's"
        " corrections in 2 s"
    )


def test_an_unknown_reading_is_said_and_the_watch_ends_with_the_unload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    steps: list[dict[str, object]] = [
        dict(fg=None, encoder_mm=None, mode=None, rest=False, pushed=False)
    ]
    logged = run_watched(steps, caplog)
    assert logged[0] == (
        "ace2k_u1: lane 1 eject, U1 unload 1 s: unknown, motor unknown, filament unknown,"
        " buffer mid, starts fwd 0 back 0, setpoint 25.0 mm/s, duty 30 %, ff rate +0.0 mm/s"
    )
    assert logged[-1].startswith("ace2k_u1: lane 1 eject, U1 unload over after 1 s:")


def test_the_watch_stops_when_the_u1_unload_fails(caplog: pytest.LogCaptureFixture) -> None:
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    adapter.gcode.fail = RuntimeError("Unknown command:AUTO_FEEDING")
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.4, "filament_mm": 2.0}
    timers = len(reactor.timers)
    with caplog.at_level("INFO"):
        advance(reactor)
    assert len(reactor.timers) < timers  # the eject's timer and the watch's are both gone
    over = [r.getMessage() for r in caplog.records if "U1 unload over" in r.getMessage()]
    assert over == [
        "ace2k_u1: lane 1 eject, U1 unload over after 0 s: motor forward 0.0 mm in 0 s, back"
        " 0.0 mm in 0 s, unsure 0.0 mm in 0 s; filament unknown; forward while the filament did"
        " not go forward: 0.0 mm in 0 s; direction from the unit in 0 s, from the follow's"
        " corrections in 0 s"
    ]


def test_an_unreadable_status_is_logged_once_and_never_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)

    class Broken(fakes.FakeGcode):
        def run_script(self, script: str) -> None:
            super().run_script(script)
            ace.get_status = MagicMock(side_effect=RuntimeError("gone"))  # type: ignore[method-assign]
            for _ in range(3):
                reactor.now += 1.0
                reactor.run_timers()

    adapter.gcode = Broken()
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.4, "filament_mm": 2.0}
    with caplog.at_level("INFO"):
        advance(reactor)
    unread = [r for r in caplog.records if "status unread" in r.getMessage()]
    assert len(unread) == 1
    assert any("U1 unload over after 3 s" in r.getMessage() for r in caplog.records)


def test_with_the_units_motor_report_the_travel_each_way_is_read_and_the_report_turned_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    watches: list[bool] = []
    steps: list[dict[str, object]] = [
        # 15 mm back and 4 mm forward in one second: both read, no inference
        dict(motor_fwd_mm=504.0, motor_back_mm=315.0, encoder_mm=39.0, ff_full_fixes=3),
        # forward while the filament went back: against
        dict(motor_fwd_mm=514.0, motor_back_mm=315.0, encoder_mm=36.0),
        # this second's report is missing: the corrections decide, and the line says so
        dict(motor_fwd_mm=None, fg=1000 + pulses(6), encoder_mm=30.0, ff_full_fixes=4),
    ]
    logged = run_watched(steps, caplog, unit_report=True, watches=watches)
    assert watches == [True, False]  # on for the unload, off at its end
    assert logged[0].startswith(
        "ace2k_u1: lane 1 eject, U1 unload 1 s: following, motor fwd 4.0 mm back 15.0 mm (unit),"
        " filament -11.0 mm,"
    )
    assert "motor fwd 10.0 mm back 0.0 mm (unit), filament -3.0 mm" in logged[1]
    assert "motor -6.0 mm (back, counted), filament -6.0 mm" in logged[2]
    assert logged[-1] == (
        "ace2k_u1: lane 1 eject, U1 unload over after 3 s: motor forward 14.0 mm in 2 s, back"
        " 21.0 mm in 2 s, unsure 0.0 mm in 0 s; filament -20.0 mm; forward while the filament"
        " did not go forward: 14.0 mm in 2 s; direction from the unit in 2 s, from the follow's"
        " corrections in 1 s"
    )


def test_a_motor_watch_that_raises_falls_back_to_the_corrections(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.has_motor_travel = True  # type: ignore[attr-defined]
    ace.feed.motor_watch = MagicMock(side_effect=RuntimeError("gone"))  # type: ignore[attr-defined]
    move = held_until_feed_back(adapter, reactor, ace)
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.4, "filament_mm": 2.0}
    with caplog.at_level("INFO"):
        advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]
    assert ace.feed.motor_watch.call_count == 1  # never turned off: it was never on
    assert any("motor report" in r.getMessage() for r in caplog.records)


def test_the_seconds_follow_the_unit_reports_when_their_phase_drifts_from_the_watch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The reports come at 1.0 s, 2.25 s and 3.0 s of the unload: a watch on a 1 s timer of its own
    # would read one, none, then two. Each line is one report.
    q: dict[str, object] = {}  # a quarter second with no new report
    steps: list[dict[str, object]] = [
        q,
        q,
        q,
        dict(motor_reports=8, motor_fwd_mm=504.0, motor_back_mm=315.0, encoder_mm=39.0),
        q,
        q,
        q,
        q,
        dict(motor_reports=9, motor_fwd_mm=514.0, motor_back_mm=315.0, encoder_mm=36.0),
        q,
        q,
        dict(motor_reports=10, motor_fwd_mm=514.0, motor_back_mm=327.0, encoder_mm=24.0),
    ]
    logged = run_watched(steps, caplog, unit_report=True, reports=7, dt=0.25)
    assert len(logged) == 4  # three reports, then the summary: no second without a report
    assert "U1 unload 1 s: following, motor fwd 4.0 mm back 15.0 mm (unit)" in logged[0]
    assert "U1 unload 2 s: following, motor fwd 10.0 mm back 0.0 mm (unit)" in logged[1]
    assert "U1 unload 3 s: following, motor fwd 0.0 mm back 12.0 mm (unit)" in logged[2]
    assert logged[-1] == (
        "ace2k_u1: lane 1 eject, U1 unload over after 3 s: motor forward 14.0 mm in 2 s, back"
        " 27.0 mm in 2 s, unsure 0.0 mm in 0 s; filament -26.0 mm; forward while the filament"
        " did not go forward: 14.0 mm in 2 s; direction from the unit in 3 s, from the follow's"
        " corrections in 0 s"
    )


def test_two_reports_read_together_count_two_seconds_and_a_late_report_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    q: dict[str, object] = {}
    steps: list[dict[str, object]] = [
        # two reports since the last reading (the reactor was held): one line, two seconds
        dict(motor_reports=9, motor_fwd_mm=500.0, motor_back_mm=320.0, encoder_mm=30.0),
        # then none for 2 s and more: the corrections give the motor of that time
        q,
        q,
        dict(fg=1000 + pulses(6), ff_full_fixes=3),
    ]
    logged = run_watched(steps, caplog, unit_report=True, reports=7, dt=1.0)
    assert "motor fwd 0.0 mm back 20.0 mm (unit, 2 reports)" in logged[0]
    # the poll after 2 s without a report, at 3 s: the corrections give its 2 s
    assert "U1 unload 3 s: following, motor 0.0 mm (still, counted)" in logged[1]
    assert "U1 unload 4 s: following, motor -6.0 mm (back, counted)" in logged[2]
    assert logged[-1] == (
        "ace2k_u1: lane 1 eject, U1 unload over after 4 s: motor forward 0.0 mm in 0 s, back"
        " 26.0 mm in 3 s, unsure 0.0 mm in 0 s; filament -20.0 mm; forward while the filament"
        " did not go forward: 0.0 mm in 0 s; direction from the unit in 2 s, from the follow's"
        " corrections in 3 s"
    )


# --- a lane with no material set never reaches the U1's heated unload ---------------------------

NO_MATERIAL = "no material set — set it with Edit spool"


def no_material(adapter: Adapter) -> None:
    """The lane's material is not set: its tag not read, no Edit spool record."""
    adapter.material_refusal = lambda _h: NO_MATERIAL


def test_a_held_probe_of_a_lane_with_no_material_stops_before_the_u1_unload() -> None:
    # The probe finds the tip held by the head's gear: the cold feed back relaxes the filament,
    # then the eject stops before the U1's unload, and nothing heats.
    adapter, reactor, left, ace = setup(head=True)
    no_material(adapter)
    job = adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -3.0, -10.4, at_rest=False)
    advance(reactor)
    assert last_start(ace)[1:3] == ("feed", 3.0)  # the feed back
    last_start(ace)[4].event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.4}
    advance(reactor)
    assert lines(adapter)[-1] == (
        f"ace2k_u1: lane 1 eject failed: {NO_MATERIAL}; the follow stays off until e0's next load"
    )
    assert adapter.gcode.scripts == []
    assert job.decided and job.failed == NO_MATERIAL and LANE not in adapter.ejector.eject_jobs


def test_the_safety_net_of_a_lane_with_no_material_stops_before_the_u1_unload() -> None:
    adapter, reactor, left, ace = setup(head=True)
    no_material(adapter)
    adapter.ejector.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, 9.1, at_rest=True)  # free: the unit's unload
    advance(reactor)
    adapter.on_feed_event(LANE, unload_end(ace, "stuck", -39.5, 69.1))  # the net takes over
    advance(reactor)
    assert last_start(ace)[1:3] == ("feed", pytest.approx(49.4))  # the feed back
    last_start(ace)[4].event = {"kind": "blocked", "mode": "feeding", "motor_mm": 49.4}
    plunger(ace, True)
    advance(reactor)
    assert lines(adapter)[-1].startswith(f"ace2k_u1: lane 1 eject failed: {NO_MATERIAL}")
    assert adapter.gcode.scripts == []
    assert LANE not in adapter.ejector.eject_jobs


def test_an_unreadable_channel_is_not_known_loaded_and_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, reactor, left, ace = setup(head=True)
    h = adapter.hooked[[x.lane for x in adapter.hooked].index(LANE)]
    left.channel_state = None  # reading it raises TypeError
    assert not adapter.ejector.known_loaded(h)
    assert not adapter.ejector.known_loaded(h)
    assert sum("channel state" in r.getMessage() for r in caplog.records) == 1
