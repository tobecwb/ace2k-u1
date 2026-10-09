"""ACE_EJECT: the filament out of its bay whatever the head holds. The head's sensor clear: the
follow off and the unit's unload. Set: a short rollback probes the filament, judged by the lane's
buffer plunger once settled — at rest, free: the same unload; moved (or a lane error), held by the
head: the probe's motor travel fed back and the U1's own unload of that head started. Refused
while in use by a print, busy, in error, with no filament, or already ejecting; everything runs from
a reactor timer that never raises and does nothing twice when the reactor runs it again."""

import ace2k_u1
import fakes
import pytest
from test_check import Config, Printer
from test_check import Gcode as CheckGcode
from test_presence import build

LANE = 0  # lane 1: head 0, the left feed's channel 1
U1_UNLOAD = (
    "AUTO_FEEDING EXTRUDER=0 UNLOAD=1 STAGE=prepare\nAUTO_FEEDING EXTRUDER=0 UNLOAD=1 STAGE=doing"
)


def setup(head=False, mode="idle", insert=True):
    adapter, reactor, left, right, ace = build()
    adapter.gcode = fakes.FakeGcode()
    adapter.hook_all()
    ace.lanes[LANE]["insert"] = insert
    ace.lanes[LANE]["mode"] = mode
    left.runout_sensor[1].detected = head
    adapter.eject_settle_s = 0.0  # the plunger judged at once; the settle's own tests set it
    return adapter, reactor, left, ace


def probe_end(kind, filament_mm, motor_mm):
    return {"kind": kind, "mode": "rolling_back", "motor_mm": motor_mm, "filament_mm": filament_mm}


def plunger(ace, at_rest):
    ace.lanes[LANE].update(rest=at_rest, pushed=not at_rest)


def end_probe(ace, kind, filament_mm, motor_mm, at_rest):
    """The probe's final event, the lane's plunger set as the case it stands for: at rest for a
    filament that came back whole, moved for a held tip."""
    plunger(ace, at_rest)
    last_start(ace)[4].event = probe_end(kind, filament_mm, motor_mm)


def last_start(ace):
    lane, mode, length, speed, move = ace.feed.started[-1]
    return lane, mode, length, speed, move


def lines(adapter):
    return [line for line in adapter.gcode.lines if "eject" in line]


def advance(reactor, s=0.1):
    reactor.now += s
    reactor.run_timers()


# --- the three paths ---------------------------------------------------------------------------


def test_head_empty_unloads_at_once():
    adapter, reactor, left, ace = setup(head=False)
    job = adapter.start_eject(LANE)
    reactor.run_timers()
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: head empty — unloading"]
    assert last_start(ace)[:3] == (LANE, "unload", 0.0)
    assert job.decided and job.failed is None and LANE not in adapter.eject_jobs
    assert all(mode != "rollback" for _, mode, *_ in ace.feed.started)


def test_head_empty_stops_the_follow_first_and_retries_a_busy_unload():
    adapter, reactor, left, ace = setup(head=False)
    left._set_channel_state(1, "load_finish", True)  # the follow armed on the loaded head
    h = adapter.hooked[[x.lane for x in adapter.hooked].index(LANE)]
    assert h.armed and h.follow_wanted
    ace.feed.refusals = ["busy"]  # the stopped follow winding down
    adapter.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.25)
    assert ("stop", LANE) in ace.feed.log
    stop_at = ace.feed.log.index(("stop", LANE))
    assert ace.feed.log[stop_at + 1][:3] == ("start", LANE, "unload")
    assert not h.armed and not h.follow_wanted


def test_probe_free_unloads():
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.start_eject(LANE)
    reactor.run_timers()
    lane, mode, length, speed, move = last_start(ace)
    assert (lane, mode, length, speed) == (LANE, "rollback", 10.0, 10.0)
    assert not job.decided
    move.event = probe_end("done", -9.6, -10.3)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert last_start(ace)[:2] == (LANE, "unload")
    assert job.decided and adapter.gcode.scripts == []


def test_probe_held_feeds_back_and_starts_the_u1_unload():
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -3.0, -10.4, at_rest=False)
    advance(reactor)
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject: held by the head (plunger moved) — unloading the head on the U1"
    ]
    lane, mode, length, speed, move = last_start(ace)
    assert (lane, mode, length, speed) == (LANE, "feed", 10.4, 10.0)
    assert ace.feed.cleared == [] and adapter.gcode.scripts == []
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.4, "filament_mm": 3.1}
    advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]
    assert job.decided and job.failed is None and LANE not in adapter.eject_jobs
    assert all(mode != "unload" for _, mode, *_ in ace.feed.started)  # the U1's end brings it


def test_probe_in_error_is_held_the_lane_cleared_first():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stuck", -1.2, -22.0, at_rest=False)
    advance(reactor)
    assert ace.feed.cleared == [LANE]
    assert "held by the head (the probe met resistance)" in lines(adapter)[0]
    lane, mode, length, speed, move = last_start(ace)
    assert (mode, length) == ("feed", 22.0)
    move.event = {"kind": "done", "mode": "feeding", "motor_mm": 22.0, "filament_mm": 2.0}
    advance(reactor)
    assert adapter.gcode.scripts == [U1_UNLOAD]


def test_a_tiny_motor_travel_is_not_fed_back():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stuck", 0.0, -0.2, at_rest=False)
    starts = len(ace.feed.started)
    advance(reactor)
    assert len(ace.feed.started) == starts and adapter.gcode.scripts == [U1_UNLOAD]


def test_a_probe_stopped_ends_the_eject():
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stopped", -2.0, -2.0, at_rest=False)
    advance(reactor)
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject failed: the probe ended stopped; the follow stays off until"
        " e0's next load"
    ]
    assert job.failed and adapter.gcode.scripts == [] and LANE not in adapter.eject_jobs


def test_a_probe_with_no_end_is_stopped_and_fails():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 6.0)
    assert not lines(adapter)
    advance(reactor, 1.5)  # past twice its travel time plus the margin
    assert ("stop", LANE) in ace.feed.log
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject failed: the probe got no end; the follow stays off until e0's"
        " next load"
    ]


def test_a_busy_probe_is_retried_then_a_refusal_fails():
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.refusals = ["busy", "busy"]
    adapter.start_eject(LANE)
    reactor.run_timers()
    advance(reactor, 0.25)
    advance(reactor, 0.25)
    assert last_start(ace)[1] == "rollback"
    adapter2, reactor2, left2, ace2 = setup(head=True)
    ace2.feed.refuse = "no_link"
    adapter2.start_eject(LANE)
    reactor2.run_timers()
    assert lines(adapter2) == [
        "ace2k_u1: lane 1 eject failed: the probe not started — refused: no_link"
        "; the follow stays off until e0's next load"
    ]


def test_the_probe_speed_keeps_within_the_units_bounds():
    adapter, reactor, left, ace = setup(head=True)
    ace.feed.speed_min = 12.0
    adapter.start_eject(LANE)
    reactor.run_timers()
    assert last_start(ace)[3] == 12.0


def test_a_failed_u1_unload_is_the_eject_line():
    adapter, reactor, left, ace = setup(head=True)
    adapter.gcode.fail = RuntimeError("Unknown command:AUTO_FEEDING")
    adapter.start_eject(LANE)
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


def refused(adapter, match):
    with pytest.raises(ace2k_u1.EjectRefused, match=match):
        adapter.start_eject(LANE)


def test_refused_in_use_by_the_print():
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
    adapter.start_eject(LANE)
    assert ace.feed.started == []  # nothing before the timer runs


def test_refused_busy_in_error_no_filament():
    adapter, reactor, left, ace = setup(mode="feeding")
    refused(adapter, r"the lane is busy \(feeding\)")
    adapter, reactor, left, ace = setup(mode="error")
    refused(adapter, "in error — ACE_CLEAR LANE=1 first")
    adapter, reactor, left, ace = setup(insert=False)
    refused(adapter, "no filament in the bay")
    with pytest.raises(ace2k_u1.EjectRefused, match="not hooked"):
        adapter.start_eject(7)


def test_a_second_eject_is_refused_while_the_first_runs():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    refused(adapter, "an eject is already running")
    reactor.run_timers()  # probing
    refused(adapter, "an eject is already running")
    end_probe(ace, "done", -9.0, -10.0, at_rest=True)
    advance(reactor)  # the unit's unload scheduled and started
    assert LANE not in adapter.eject_jobs


# --- the timer ---------------------------------------------------------------------------------


def test_wait_zero_runs_from_the_timer_and_a_re_entered_timer_does_nothing():
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.start_eject(LANE)
    assert ace.feed.started == [] and job.timer in reactor.timers  # nothing moved yet
    reentered = []

    class LockWaitingGcode(fakes.FakeGcode):
        def run_script(self, script):
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


def test_the_timer_never_raises():
    adapter, reactor, left, ace = setup(head=True)

    def broken(eventtime):
        raise RuntimeError("status gone")

    adapter.start_eject(LANE)
    ace.get_status = broken
    reactor.run_timers()
    assert lines(adapter) == [
        "ace2k_u1: lane 1 eject failed: status gone; the follow stays off until e0's next load"
    ]
    assert LANE not in adapter.eject_jobs


# --- the command and the configuration -------------------------------------------------------


class Gcmd:
    error = ValueError

    def __init__(self, params):
        self.params = params

    def get_int(self, name, default=None, minval=None, maxval=None):
        value = self.params.get(name)
        value = default if value is None else int(value)
        if value is None or (minval is not None and value < minval):
            raise self.error(f"{name} missing or out of range")
        if maxval is not None and value > maxval:
            raise self.error(f"{name} out of range")
        return value


def hooked_u1(values=None):
    reactor = fakes.FakeReactor()
    reactor.NOW = 0.0
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    gcode = CheckGcode()
    objects = {"gcode": gcode, "ace2k": ace, "extruder_list": fakes.extruder_list()}
    u1 = ace2k_u1.ACE2kU1(Config(Printer(reactor, objects, [left, right]), values))
    return u1, reactor, left, ace, gcode


def test_the_command_waits_for_the_decision_only():
    u1, reactor, left, ace, gcode = hooked_u1()
    u1._hook(reactor.now)
    ace.lanes[LANE].update(insert=True, mode="idle")
    left.runout_sensor[1].detected = True
    ace.feed.refusals = []

    def decide(now):
        reactor.run_timers()
        for _, mode, _, _, move in ace.feed.started:
            if mode == "rollback" and move.event is None:
                move.event = probe_end("done", -9.0, -10.0)

    reactor.on_pause = [decide]
    gcode.commands["ACE_EJECT"](Gcmd({"LANE": "1"}))
    assert any("filament free (plunger at rest)" in line for line in gcode.lines)
    assert u1.adapter.eject_jobs == {}


def test_the_command_returns_at_once_with_wait_zero_and_refuses_as_a_gcode_error():
    u1, reactor, left, ace, gcode = hooked_u1()
    with pytest.raises(ValueError, match="not hooked"):
        gcode.commands["ACE_EJECT"](Gcmd({"LANE": "1"}))
    u1._hook(reactor.now)
    ace.lanes[LANE].update(insert=True, mode="idle")
    left.runout_sensor[1].detected = True
    gcode.commands["ACE_EJECT"](Gcmd({"LANE": "1", "WAIT": "0"}))
    assert ace.feed.started == [] and LANE in u1.adapter.eject_jobs
    with pytest.raises(ValueError, match="already running"):
        gcode.commands["ACE_EJECT"](Gcmd({"LANE": "1", "WAIT": "0"}))
    with pytest.raises(ValueError):
        gcode.commands["ACE_EJECT"](Gcmd({"LANE": "5"}))


class StrictConfig(Config):
    """getfloat with Klipper's bounds."""

    def getfloat(self, name, default, above=None, minval=None, maxval=None):
        value = self.values.get(name, default)
        if value is None:
            return None
        if (minval is not None and value < minval) or (maxval is not None and value > maxval):
            raise self.error(f"{name} {value} out of range")
        if above is not None and value <= above:
            raise self.error(f"{name} {value} out of range")
        return value

    def getint(self, name, default, minval=None, maxval=None):
        value = self.values.get(name, default)
        if (minval is not None and value < minval) or (maxval is not None and value > maxval):
            raise self.error(f"{name} {value} out of range")
        return value


def strict_u1(values):
    reactor = fakes.FakeReactor()
    gcode = CheckGcode()
    printer = Printer(reactor, {"gcode": gcode, "ace2k": fakes.FakeAce2k()}, [])
    return ace2k_u1.ACE2kU1(StrictConfig(printer, values))


def test_the_eject_settings_and_their_bounds():
    u1 = strict_u1({})
    assert (u1.eject_probe_mm, u1.eject_probe_speed, u1.eject_settle_ms) == (10.0, 10.0, 500)
    assert not hasattr(u1, "eject_free_mm")
    u1 = strict_u1({"eject_probe_mm": 30.0, "eject_probe_speed": 5.0, "eject_settle_ms": 2000})
    assert (u1.eject_probe_mm, u1.eject_probe_speed, u1.eject_settle_ms) == (30.0, 5.0, 2000)
    assert strict_u1({"eject_settle_ms": 200}).eject_settle_ms == 200
    for bad in (
        {"eject_probe_mm": 4.9},
        {"eject_probe_mm": 30.5},
        {"eject_probe_speed": 4.0},
        {"eject_probe_speed": 31.0},
        {"eject_settle_ms": 199},
        {"eject_settle_ms": 2001},
    ):
        with pytest.raises(ValueError):
            strict_u1(bad)


def test_the_settings_reach_the_adapter():
    u1, reactor, left, ace, gcode = hooked_u1(
        {"eject_probe_mm": 20.0, "eject_probe_speed": 15.0, "eject_settle_ms": 800}
    )
    u1._hook(reactor.now)
    a = u1.adapter
    assert (a.eject_probe_mm, a.eject_probe_speed, a.eject_settle_s) == (20.0, 15.0, 0.8)


# --- fix round 1 ---------------------------------------------------------------------------------


def held_until_feed_back(adapter, reactor, ace):
    """An eject probed held: the feed back started, its move returned."""
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -2.0, -10.0, at_rest=False)
    advance(reactor)
    assert last_start(ace)[1] == "feed"
    return last_start(ace)[4]


@pytest.mark.parametrize("kind", ["stopped", "stopped_link", "stopped_shutdown", "runout"])
def test_a_feed_back_ended_by_a_stop_fails_and_never_unloads_the_head(kind):
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    move.event = {"kind": kind, "mode": "feeding", "motor_mm": 3.0, "filament_mm": 1.0}
    advance(reactor)
    assert adapter.gcode.scripts == []
    assert lines(adapter)[-1].startswith(
        f"ace2k_u1: lane 1 eject failed: the feed back ended {kind}"
    )


def test_a_feed_back_in_a_lane_error_is_cleared_and_goes_on():
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    move.event = {"kind": "tangled", "mode": "feeding", "motor_mm": 3.0, "filament_mm": 1.0}
    advance(reactor)
    assert ace.feed.cleared == [LANE] and adapter.gcode.scripts == [U1_UNLOAD]


def test_a_print_started_before_the_u1_unload_fails_it():
    adapter, reactor, left, ace = setup(head=True)
    move = held_until_feed_back(adapter, reactor, ace)
    adapter.print_stats = fakes.FakePrintStats("printing")
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert adapter.gcode.scripts == []
    assert lines(adapter)[-1].startswith("ace2k_u1: lane 1 eject failed: a print started")


def test_a_print_started_before_the_probe_or_the_feed_back_fails_it():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    adapter.print_stats = fakes.FakePrintStats("printing")
    reactor.run_timers()
    assert ace.feed.started == []  # no probe; the follow was off before: it stays off
    assert lines(adapter)[-1].startswith("ace2k_u1: lane 1 eject failed: a print started")
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -2.0, -10.0, at_rest=False)
    adapter.print_stats = fakes.FakePrintStats("paused")
    advance(reactor)
    assert all(mode != "feed" for _, mode, *_ in ace.feed.started)
    assert "a print started" in lines(adapter)[-1]


def test_a_head_cleared_before_the_u1_unload_takes_the_units_unload():
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


def test_the_fake_carries_every_real_state():
    real = sorted(v for k, v in fakes.CONSTANTS.items() if k.startswith("FEED_STA_"))
    assert real == sorted(U1_STATES)


@pytest.mark.parametrize("state", U1_STATES + ["some_future_state"])
def test_the_u1_flow_rule_over_every_state(state):
    adapter, reactor, left, ace = setup(head=True)
    left.channel_state[1] = state
    if state in AT_REST:
        adapter.start_eject(LANE)
    else:
        refused(adapter, "the U1 is loading/unloading this head")


def test_an_unreadable_channel_state_is_refused_never_raised():
    adapter, reactor, left, ace = setup(head=True)

    class Broken(list):
        def __getitem__(self, i):
            raise RuntimeError("gone")

    left.channel_state = Broken(left.channel_state)
    refused(adapter, "the U1 is loading/unloading this head")


def test_refused_while_a_channel_flow_holds_it_and_while_the_units_unload_runs():
    adapter, reactor, left, ace = setup(head=False)
    left.channel_active = 1
    refused(adapter, "the U1 is loading/unloading this head")
    adapter, reactor, left, ace = setup(head=False)
    adapter.start_eject(LANE)
    reactor.run_timers()  # the unit's unload started: in flight until its final event
    assert LANE in adapter.ejects
    refused(adapter, "an eject is already running")


def test_a_failure_with_the_head_loaded_says_the_follow_stays_off():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stopped", -1.0, -1.0, at_rest=False)
    advance(reactor)
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject failed: the probe ended stopped; the follow stays off until"
        " e0's next load"
    )


def test_a_probe_with_no_filament_travel_fails():
    for value in (None, "x"):
        adapter, reactor, left, ace = setup(head=True)
        adapter.start_eject(LANE)
        reactor.run_timers()
        event = probe_end("done", value, -10.0)
        last_start(ace)[4].event = event
        advance(reactor)
        assert all(mode != "feed" for _, mode, *_ in ace.feed.started)
        assert "eject failed: the probe reported no filament travel" in lines(adapter)[-1]


def test_wait_one_giving_up_says_so_once():
    adapter, reactor, left, ace = setup(head=True)
    job = adapter.start_eject(LANE)
    adapter.wait_eject(job)  # no timer runs: no decision
    said = [line for line in adapter.gcode.lines if "no decision" in line]
    assert said == [
        "ace2k_u1: lane 1 eject: no decision after 60 s — ACE_EJECT returns, the eject runs on"
    ]


# --- fix round 2 ---------------------------------------------------------------------------------


def armed_follow(adapter, ace):
    h = next(x for x in adapter.hooked if x.lane == LANE)
    return h.armed and h.follow_wanted and last_start(ace)[1] == "assist_both"


def test_a_print_started_before_the_probe_restores_the_follow():
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)  # the follow armed on the loaded head
    adapter.start_eject(LANE)
    adapter.print_stats = fakes.FakePrintStats("printing")
    reactor.run_timers()
    assert ("stop", LANE) in ace.feed.log  # the eject's begin stopped it
    assert armed_follow(adapter, ace)
    assert all(mode != "rollback" for _, mode, *_ in ace.feed.started)
    assert lines(adapter)[-1] == (
        "ace2k_u1: lane 1 eject failed: a print started; its follow armed again"
    )


def test_a_print_started_before_the_u1_unload_restores_the_follow():
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)  # the follow armed on the loaded head
    move = held_until_feed_back(adapter, reactor, ace)
    adapter.print_stats = fakes.FakePrintStats("printing")
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert adapter.gcode.scripts == [] and armed_follow(adapter, ace)


def test_a_u1_flow_at_a_recheck_restores_the_follow_too():
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)
    move = held_until_feed_back(adapter, reactor, ace)
    left.channel_state[1] = "unload_prepare"  # the U1's own unload began: a loaded state
    move.event = {"kind": "done", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert "the U1 is loading/unloading this head" in lines(adapter)[-1]
    assert armed_follow(adapter, ace)


def test_no_follow_restored_on_a_lane_in_error():
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)
    move = held_until_feed_back(adapter, reactor, ace)
    adapter.print_stats = fakes.FakePrintStats("printing")
    ace.lanes[LANE]["error"] = "stuck"
    move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0, "filament_mm": 2.0}
    advance(reactor)
    assert last_start(ace)[1] == "feed"
    assert lines(adapter)[-1].endswith("the follow stays off until e0's next load")


def test_a_nan_filament_travel_fails():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", float("nan"), -10.0, at_rest=False)
    advance(reactor)
    assert "the probe reported no filament travel" in lines(adapter)[-1]
    assert all(mode != "feed" for _, mode, *_ in ace.feed.started)


# --- fix round 3 ---------------------------------------------------------------------------------


def test_a_follow_off_before_the_eject_stays_off_after_a_recheck_failure():
    for at in ("probe", "u1"):
        adapter, reactor, left, ace = setup(head=True)
        left.channel_state[1] = "load_finish"  # loaded, but its follow not armed (off by hand)
        if at == "probe":
            adapter.start_eject(LANE)
            adapter.print_stats = fakes.FakePrintStats("printing")
            reactor.run_timers()
        else:
            move = held_until_feed_back(adapter, reactor, ace)
            adapter.print_stats = fakes.FakePrintStats("printing")
            move.event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0}
            advance(reactor)
        assert all(mode != "assist_both" for _, mode, *_ in ace.feed.started), at
        assert lines(adapter)[-1].endswith("the follow stays off until e0's next load"), at


def test_a_manual_feed_channel_state_is_not_armed():
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


def test_the_restore_holds_off_the_standing_re_arm_while_it_waits():
    adapter, reactor, left, ace = setup(head=True)
    left._set_channel_state(1, "load_finish", True)
    adapter.start_eject(LANE)
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


def test_the_bench_case_a_full_travel_with_the_plunger_pushed_is_held():
    # measured: a tip held in the head's gear, the probe read 9.9 mm at the unit — the buffer
    # absorbed it — and the plunger ended pushed
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.2, at_rest=False)
    advance(reactor)
    assert "held by the head (plunger moved)" in lines(adapter)[-1]
    assert last_start(ace)[1:3] == ("feed", 10.2)


def test_a_short_travel_with_the_plunger_at_rest_is_free():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -2.0, -10.0, at_rest=True)
    advance(reactor)
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert last_start(ace)[1] == "unload"


def test_rest_and_pushed_both_set_is_not_at_rest():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=False)
    ace.lanes[LANE].update(rest=True, pushed=True)
    advance(reactor)
    assert "plunger moved" in lines(adapter)[-1]


def test_the_plunger_is_read_after_the_settle_pause_from_the_reactor():
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 0.5
    job = adapter.start_eject(LANE)
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


def test_a_re_entered_timer_during_the_settle_does_nothing():
    # the reactor runs the eject's timer again while its call judging the settled plunger is
    # still in progress: the re-entered call leaves at once, the filament judged and unloaded once
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 0.5
    job = adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=True)
    advance(reactor, 0.1)
    reentered = []
    read = adapter._plunger_at_rest

    def reading_while_reentered(h):
        if not reentered:
            reentered.append(job.timer[0](reactor.now))
        return read(h)

    adapter._plunger_at_rest = reading_while_reentered
    advance(reactor, 0.5)
    assert reentered == [reactor.NEVER]
    assert lines(adapter) == ["ace2k_u1: lane 1 eject: filament free (plunger at rest) — unloading"]
    assert [m for _, m, *_ in ace.feed.started].count("unload") == 1


def test_a_stale_plunger_reading_counts_as_held():
    # no fresh sensors report can be told (the link not proven): the reading may be from before
    # the probe — held, the safe side
    adapter, reactor, left, ace = setup(head=True)
    ace.link_proven = False
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=True)
    advance(reactor)
    assert "held by the head (plunger moved)" in lines(adapter)[-1]


def test_a_print_started_during_the_settle_ends_the_eject():
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 0.5
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "done", -9.9, -10.0, at_rest=True)
    advance(reactor, 0.1)
    adapter.print_stats = fakes.FakePrintStats("printing")
    advance(reactor, 0.5)
    assert lines(adapter)[-1].startswith("ace2k_u1: lane 1 eject failed: a print started")
    assert all(m != "unload" for _, m, *_ in ace.feed.started)


def test_the_plunger_after_the_feed_back_is_read_after_its_settle():
    adapter, reactor, left, ace = setup(head=True)
    adapter.eject_settle_s = 0.5
    job = adapter.start_eject(LANE)
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


def test_a_plunger_not_at_rest_after_the_feed_back_is_said_and_the_u1_unload_goes_on():
    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
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
    adapter2.start_eject(LANE)
    reactor2.run_timers()
    end_probe(ace2, "done", -9.9, -10.0, at_rest=False)
    advance(reactor2)
    plunger(ace2, True)  # the feed back put it back at rest: nothing to say
    last_start(ace2)[4].event = {"kind": "blocked", "mode": "feeding", "motor_mm": 10.0}
    advance(reactor2)
    assert not any("not at rest" in line for line in adapter2.gcode.lines)
    assert adapter2.gcode.scripts == [U1_UNLOAD]


def test_every_normal_path_eject_line_classifies_as_info():
    """Only a real failure may show red in the web page's event list."""
    import ace2k_u1_history as hist

    adapter, reactor, left, ace = setup(head=True)
    adapter.start_eject(LANE)
    reactor.run_timers()
    end_probe(ace, "stuck", -1.2, -22.0, at_rest=False)
    advance(reactor)
    seen = lines(adapter)  # the held line, as the adapter says it
    seen += [
        template.format(n=1, why="plunger moved", s=ace2k_u1.EJECT_WAIT_MAX_S)
        for template in ace2k_u1.EJECT_SAY.values()
    ]
    assert any("no decision after" in s for s in seen)
    assert any("held by the head" in s for s in seen)
    for line in seen:
        assert hist.classify(line)[0] == "info", line
