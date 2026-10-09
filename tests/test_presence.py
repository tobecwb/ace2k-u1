"""The adapter's presence: the inlet reading the lane's insert, the hook-time state, the bay
watch with its settle window, and the encoder-fed wheels."""

import ace2k_u1
import fakes

SETTLE_S = 2.0 / fakes.FakeFeedApi.report_hz + 0.2  # two feed reports plus a watch period


def build(lanes=(1, 2, 3, 4), extruders=None):
    reactor = fakes.FakeReactor()
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    adapter = ace2k_u1.Adapter(
        reactor,
        fakes.fake_module(),
        ace,
        [left, right],
        lanes,
        feed_speed=30.0,
        head_budget_mm=2000.0,
        poll_s=0.2,
        extruders=ace2k_u1.extruder_names(
            fakes.extruder_list() if extruders is None else extruders
        ),
    )
    return adapter, reactor, left, right, ace


class Console:
    def __init__(self):
        self.lines = []

    def respond_info(self, msg):
        self.lines.append(msg)


def events(feed):
    return [c for c in feed.calls if c[0] == "port_event"]


def settle(adapter, reactor):
    """One watch sample, then another once the settle window has passed."""
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)


def test_the_inlet_reads_the_lane_insert():
    adapter, reactor, left, right, ace = build()
    assert adapter.hook_all() == []
    ace.lanes[0]["insert"] = True  # lane 1 -> e0 -> left channel 1
    assert left._port[1].get_filament_detected() is True
    assert left._port[0].get_filament_detected() is False  # lane 2, insert None
    assert left.module_exist == [True, True]


def test_a_problem_hooks_nothing():
    adapter, reactor, left, right, ace = build()
    del right.wheel_2
    problems = adapter.hook_all()
    assert problems
    assert adapter.hooked == []
    assert left.module_exist == [False, False]
    assert left._port[1].get_filament_detected() is False


def test_a_missing_head_sensor_hooks_nothing():
    adapter, reactor, left, right, ace = build()
    right.runout_sensor[1] = None  # e3's sensor absent
    problems = adapter.hook_all()
    assert problems == ["head sensor e3_filament missing"]
    assert adapter.hooked == []
    assert left.module_exist == [False, False] and right.module_exist == [False, False]


def test_a_missing_head_sensor_on_an_unhooked_lane_is_no_problem():
    adapter, reactor, left, right, ace = build(lanes=(1, 2, 3))
    right.runout_sensor[1] = None  # e3 = lane 4, not hooked
    assert adapter.hook_all() == []
    assert len(adapter.hooked) == 3


def test_hooking_rederives_each_channel_state_without_moving():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    reactor.run_async()
    assert ("do_feed", 1, "update_auto_mode", None, True) in left.calls
    assert ("do_feed", 0, "update_auto_mode", None, True) in left.calls
    assert ("do_feed", 0, "update_auto_mode", None, True) in right.calls
    assert not events(left) and not events(right)
    assert ace.feed.started == []


def test_a_filament_present_at_hook_is_present_without_an_event():
    adapter, reactor, left, right, ace = build()
    ace.lanes[0]["insert"] = True
    ace.lanes[1].update(insert=True, mode="loading")
    adapter.hook_all()
    by_lane = {h.lane: h for h in adapter.hooked}
    assert by_lane[0].present is True
    assert by_lane[1].present is False
    adapter.watch(reactor.now)
    assert not events(left)


def test_the_watch_sends_one_port_event_per_change():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[2]["insert"] = True  # lane 3 -> e2 -> right channel 0
    settle(adapter, reactor)
    adapter.watch(reactor.now)
    ace.lanes[2]["insert"] = False
    adapter.watch(reactor.now)
    adapter.watch(reactor.now)
    assert events(right) == [("port_event", True, 0), ("port_event", False, 0)]


def test_an_insert_waits_for_the_settle_and_the_units_load():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True  # the mode report still says idle
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S - 0.1
    adapter.watch(reactor.now)
    assert not events(left)
    assert not [h for h in adapter.hooked if h.lane == 0 and h.present]
    ace.lanes[0]["mode"] = "loading"  # the unit's own load, reported a second later
    reactor.now += 30.0
    adapter.watch(reactor.now)
    assert not events(left)
    ace.lanes[0]["mode"] = "idle"  # the window starts afresh when the load ends
    reactor.now += 0.2
    adapter.watch(reactor.now)
    assert not events(left)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)
    assert events(left) == [("port_event", True, 1)]


def test_a_load_ending_in_error_raises_no_event_and_says_so_once():
    adapter, reactor, left, right, ace = build()
    console = Console()
    adapter.gcode = console
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    adapter.watch(reactor.now)
    ace.lanes[0]["mode"] = "error"
    for _ in range(5):
        reactor.now += SETTLE_S
        adapter.watch(reactor.now)
    assert not events(left)
    assert console.lines == [
        "ace2k_u1: lane 1: the unit's load ended in error — ACE_CLEAR LANE=1, then re-insert"
    ]


def test_clearing_the_error_with_the_filament_in_place_raises_the_event():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[3].update(insert=True, mode="error")  # lane 4 -> e3 -> right channel 1
    settle(adapter, reactor)
    assert not events(right)
    ace.lanes[3]["mode"] = "idle"  # ACE_CLEAR LANE=4: the window starts afresh
    reactor.now += 0.2
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S - 0.4
    adapter.watch(reactor.now)
    assert not events(right)
    reactor.now += 0.4
    adapter.watch(reactor.now)
    assert events(right) == [("port_event", True, 1)]


def test_a_fall_inside_the_settle_window_raises_nothing():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0]["insert"] = True
    adapter.watch(reactor.now)
    reactor.now += 1.0
    ace.lanes[0]["insert"] = False
    adapter.watch(reactor.now)
    ace.lanes[0]["insert"] = True  # back in: the window starts over
    reactor.now += 0.2
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S - 0.4
    adapter.watch(reactor.now)
    assert not events(left)
    reactor.now += 0.4
    adapter.watch(reactor.now)
    assert events(left) == [("port_event", True, 1)]


def test_a_fall_during_the_units_load_does_nothing():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0].update(insert=True, mode="loading")
    settle(adapter, reactor)
    ace.lanes[0]["insert"] = False
    reactor.now += 0.2
    adapter.watch(reactor.now)
    assert not events(left)
    assert ace.feed.stopped == []


def test_a_falling_insert_stops_an_assisting_lane():
    for mode in ("assisting", "assisting_back"):
        adapter, reactor, left, right, ace = build()
        adapter.hook_all()
        ace.lanes[0]["insert"] = True
        settle(adapter, reactor)
        ace.lanes[0].update(mode=mode)
        adapter.watch(reactor.now)
        ace.lanes[0].update(insert=False)
        adapter.watch(reactor.now)
        assert ace.feed.stopped == [0], mode
        assert events(left) == [("port_event", True, 1), ("port_event", False, 1)], mode


def test_a_falling_insert_leaves_a_following_lane_to_its_tail():
    # the unit always enters the tail when the insert falls in the follow: the follow feeds on,
    # still armed and wanted; the U1 is told of the empty bay only when the tail has ended
    for tail in (False, True):  # the tail notice not yet in, or in
        adapter, reactor, left, right, ace = build()
        adapter.hook_all()
        h = next(h for h in adapter.hooked if h.lane == 0)
        ace.lanes[0]["insert"] = True
        settle(adapter, reactor)  # the U1 told of the filament (its head empty)
        left.runout_sensor[1].detected = True  # its preload: the filament at the head's sensor
        left._set_channel_state(1, "load_finish", True)
        ace.lanes[0].update(mode="following")
        adapter.watch(reactor.now)
        ace.lanes[0].update(insert=False, tail=tail)
        adapter.watch(reactor.now)
        assert ace.feed.stopped == [], tail
        assert h.armed and h.follow_wanted, tail
        assert events(left) == [("port_event", True, 1)], tail
        adapter.on_feed_event(0, {"kind": "tail_out", "mode": "following", "seq": h.assist_seq})
        ace.lanes[0].update(mode="idle", tail=False)  # the tail's end
        adapter.watch(reactor.now)
        adapter.watch(reactor.now)
        assert events(left) == [("port_event", True, 1), ("port_event", False, 1)], tail


def test_hang_neutral_is_a_no_op_on_hooked_channels_only():
    adapter, reactor, left, right, ace = build(lanes=(1,))
    adapter.hook_all()
    left._hang_neutral(1)  # lane 1, hooked
    left._hang_neutral(0)  # lane 2, not hooked
    assert ("hang_neutral", 1) not in left.calls
    assert ("hang_neutral", 0) in left.calls


def test_the_wheel_counts_absolute_travel():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    wheel = left.wheel[1]
    assert wheel is left.wheel_2[1] and wheel.ppr == 6
    ace.lanes[0]["encoder_mm"] = 31.4159
    adapter.watch(reactor.now)
    assert wheel.get_counts() == 12
    ace.lanes[0]["encoder_mm"] = 0.0  # rollback: still counts up
    reactor.now += 0.2
    adapter.watch(reactor.now)
    assert wheel.get_counts() == 24
    assert wheel.get_last_report_time() == reactor.now
    assert abs(wheel.get_rpm() - 300.0) < 1e-6  # one revolution in 0.2 s


def test_the_wheel_is_seeded_at_hook_from_the_current_encoder():
    adapter, reactor, left, right, ace = build()
    ace.lanes[0]["encoder_mm"] = 500.0
    adapter.hook_all()
    adapter.watch(reactor.now)
    assert left.wheel[1].get_counts() == 0


def test_small_steps_add_up_to_whole_counts():
    # at ppr 6 one count is about 2.6 mm: steps of 0.3 mm each round to 0 on their own
    wheel = ace2k_u1.EncoderWheel(6, 31.4159)
    wheel.sample(0.0, 0.0)
    seen = []
    for i in range(1, 101):
        wheel.sample(0.3 * i, 0.2 * i)
        seen.append(wheel.get_counts())
    assert seen == sorted(seen)  # never decreases
    assert seen[9] == int(3.0 * 12 / 31.4159)  # 10 steps, 3 mm -> 1
    assert seen[-1] == int(30.0 * 12 / 31.4159)  # 100 steps, 30 mm -> 11
    assert abs(wheel.get_rpm() - 0.3 / 31.4159 * 60.0 / 0.2) < 1e-9


def test_small_steps_count_through_the_watch():
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    wheel = left.wheel[1]
    for _ in range(10):
        reactor.now += 0.2
        ace.lanes[0]["encoder_mm"] += 0.3
        adapter.watch(reactor.now)
    assert wheel.get_counts() == 1


def test_hooking_maps_each_lane_to_its_heads_extruder():
    adapter, reactor, left, right, ace = build()
    assert adapter.hook_all() == []
    assert sorted(ace.feed.extruders) == [
        (0, "extruder"),
        (1, "extruder1"),
        (2, "extruder2"),
        (3, "extruder3"),
    ]


def test_only_the_hooked_lanes_are_mapped():
    adapter, reactor, left, right, ace = build(lanes=(2, 4))
    assert adapter.hook_all() == []
    assert sorted(ace.feed.extruders) == [(1, "extruder1"), (3, "extruder3")]


def test_a_failed_hook_maps_nothing():
    adapter, reactor, left, right, ace = build()
    del right.wheel_2
    assert adapter.hook_all()
    assert ace.feed.extruders == []


def test_the_extruder_names_are_the_printers_own():
    # the names are read off the U1's extruder objects by their index, whatever they are called
    named = fakes.extruder_list()
    named[2].name = "toolhead_c"
    adapter, reactor, left, right, ace = build(extruders=list(reversed(named)))
    adapter.hook_all()
    assert (2, "toolhead_c") in ace.feed.extruders
    assert (0, "extruder") in ace.feed.extruders


def test_a_head_without_an_extruder_is_hooked_unmapped_and_said():
    adapter, reactor, left, right, ace = build(extruders=fakes.extruder_list(3))
    console = Console()
    adapter.gcode = console
    assert adapter.hook_all() == []
    assert len(adapter.hooked) == 4
    assert sorted(ace.feed.extruders) == [(0, "extruder"), (1, "extruder1"), (2, "extruder2")]
    assert console.lines == ["ace2k_u1: lane 4: no extruder 3 on the printer; no feed-forward"]
