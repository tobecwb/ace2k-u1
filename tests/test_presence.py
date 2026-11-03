"""The adapter's presence: the inlet reading the lane's insert, the hook-time state, the bay
watch with its settle window, and the encoder-fed wheels."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ace2k_u1
import ace2k_u1_adapter
import ace2k_u1_hooks
import fakes

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ace2k_u1_adapter import Adapter
    from fakes import FakeAce2k, FakeExtruder, FakeFeed, FakeReactor

SETTLE_S = 2.0 / fakes.FakeFeedApi.report_hz + 0.2  # two feed reports plus a watch period


def build(
    lanes: Iterable[int] = (1, 2, 3, 4),
    extruders: list[FakeExtruder] | None = None,
    head_budget_mm: float = 2000.0,
) -> tuple[Adapter, FakeReactor, FakeFeed, FakeFeed, FakeAce2k]:
    reactor = fakes.FakeReactor()
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    adapter = ace2k_u1_adapter.Adapter(
        reactor,
        fakes.fake_module(),
        [left, right],
        fakes.one_unit_heads(ace, lanes),
        ace2k_u1_adapter.AdapterSetup(
            feed_speed=30.0,
            head_budget_mm=head_budget_mm,
            poll_s=0.2,
            extruders=ace2k_u1.extruder_names(
                fakes.extruder_list() if extruders is None else extruders
            ),
            units=fakes.FakeUnits([ace]),
        ),
    )
    return adapter, reactor, left, right, ace


class Console:
    def __init__(self) -> None:
        self.lines = []

    def respond_info(self, msg: str, log: bool = True) -> None:
        self.lines.append(msg)


def events(feed: FakeFeed) -> list[tuple[object, ...]]:
    return [c for c in feed.calls if c[0] == "port_event"]


def settle(adapter: Adapter, reactor: FakeReactor) -> None:
    """One watch sample, then another once the settle window has passed."""
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S
    adapter.watch(reactor.now)


def test_the_inlet_reads_the_lane_insert() -> None:
    adapter, reactor, left, right, ace = build()
    assert adapter.hook_all() == []
    ace.lanes[0]["insert"] = True  # lane 1 -> e0 -> left channel 1
    assert left._port[1].get_filament_detected() is True
    assert left._port[0].get_filament_detected() is False  # lane 2, insert None
    assert left.module_exist == [True, True]


def test_a_problem_hooks_nothing() -> None:
    adapter, reactor, left, right, ace = build()
    del right.wheel_2
    problems = adapter.hook_all()
    assert problems
    assert adapter.hooked == []
    assert left.module_exist == [False, False]
    assert left._port[1].get_filament_detected() is False


def test_a_missing_head_sensor_hooks_nothing() -> None:
    adapter, reactor, left, right, ace = build()
    right.runout_sensor[1] = None  # e3's sensor absent
    problems = adapter.hook_all()
    assert problems == ["head sensor e3_filament missing"]
    assert adapter.hooked == []
    assert left.module_exist == [False, False] and right.module_exist == [False, False]


def test_a_missing_head_sensor_on_an_unhooked_lane_is_no_problem() -> None:
    adapter, reactor, left, right, ace = build(lanes=(1, 2, 3))
    right.runout_sensor[1] = None  # e3 = lane 4, not hooked
    assert adapter.hook_all() == []
    assert len(adapter.hooked) == 3


def test_hooking_rederives_each_channel_state_without_moving() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    reactor.run_async()
    assert ("do_feed", 1, "update_auto_mode", None, True) in left.calls
    assert ("do_feed", 0, "update_auto_mode", None, True) in left.calls
    assert ("do_feed", 0, "update_auto_mode", None, True) in right.calls
    assert not events(left) and not events(right)
    assert ace.feed.started == []


def test_a_filament_present_at_hook_is_present_without_an_event() -> None:
    adapter, reactor, left, right, ace = build()
    ace.lanes[0]["insert"] = True
    ace.lanes[1].update(insert=True, mode="loading")
    adapter.hook_all()
    by_lane = {h.lane: h for h in adapter.hooked}
    assert by_lane[0].present is True
    assert by_lane[1].present is False
    adapter.watch(reactor.now)
    assert not events(left)


def test_the_watch_sends_one_port_event_per_change() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[2]["insert"] = True  # lane 3 -> e2 -> right channel 0
    settle(adapter, reactor)
    adapter.watch(reactor.now)
    ace.lanes[2]["insert"] = False
    adapter.watch(reactor.now)
    adapter.watch(reactor.now)
    assert events(right) == [("port_event", True, 0), ("port_event", False, 0)]


def test_an_insert_waits_for_the_settle_and_the_units_load() -> None:
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


def test_a_lone_heads_insert_is_told_once_the_units_tag_search_begins() -> None:
    """The unit's load searches for the tag past the parking point. On a head that the lane alone
    feeds, the U1 is told then, so that its preload's push can cut the search (bench 2026-10-24)."""
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0].update(insert=True, mode="loading", tag=dict(fakes.NO_TAG, state="pending"))
    settle(adapter, reactor)
    assert not events(left)  # still on its way to the parking point
    ace.lanes[0]["tag"] = dict(fakes.NO_TAG, state="searching")
    settle(adapter, reactor)
    assert events(left) == [("port_event", True, 1)]


def test_no_early_insert_with_the_cut_off() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    adapter.tag_push_speed_mm_s = 0.0  # the search runs and comes back, as before
    ace.lanes[0].update(insert=True, mode="loading", tag=dict(fakes.NO_TAG, state="searching"))
    settle(adapter, reactor)
    assert not events(left)


def test_a_load_ending_in_error_raises_no_event_and_says_so_once() -> None:
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
        "ace2k_u1: lane 1: the unit's load ended in error — ACEK_CLEAR LANE=1, then re-insert"
    ]


def test_clearing_the_error_with_the_filament_in_place_raises_the_event() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[3].update(insert=True, mode="error")  # lane 4 -> e3 -> right channel 1
    settle(adapter, reactor)
    assert not events(right)
    ace.lanes[3]["mode"] = "idle"  # ACEK_CLEAR LANE=4: the window starts afresh
    reactor.now += 0.2
    adapter.watch(reactor.now)
    reactor.now += SETTLE_S - 0.4
    adapter.watch(reactor.now)
    assert not events(right)
    reactor.now += 0.4
    adapter.watch(reactor.now)
    assert events(right) == [("port_event", True, 1)]


def test_a_fall_inside_the_settle_window_raises_nothing() -> None:
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


def test_a_fall_during_the_units_load_does_nothing() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    ace.lanes[0].update(insert=True, mode="loading")
    settle(adapter, reactor)
    ace.lanes[0]["insert"] = False
    reactor.now += 0.2
    adapter.watch(reactor.now)
    assert not events(left)
    assert ace.feed.stopped == []


def test_a_falling_insert_stops_an_assisting_lane() -> None:
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


def test_a_falling_insert_whose_stop_fails_still_tells_the_u1() -> None:
    """The watch tick's stop is the guarded one: a stop that raises is a console line, and
    the tick goes on — the lane disarmed, the U1 told of the empty bay."""
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    adapter.gcode = Console()
    h = next(h for h in adapter.hooked if h.lane == 0)
    ace.lanes[0]["insert"] = True
    settle(adapter, reactor)
    ace.lanes[0].update(mode="assisting")
    adapter.watch(reactor.now)

    def stop(_lane_index: int) -> None:
        raise RuntimeError("the link is down")

    ace.feed.stop = stop
    ace.lanes[0].update(insert=False)
    adapter.watch(reactor.now)
    assert events(left) == [("port_event", True, 1), ("port_event", False, 1)]
    assert not h.armed and not h.present
    assert adapter.gcode.lines == [
        "ace2k_u1: lane 1 not stopped on its filament leaving the bay — the link is down"
    ]


def test_a_falling_insert_leaves_a_following_lane_to_its_tail() -> None:
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


def test_hang_neutral_is_a_no_op_on_hooked_channels_only() -> None:
    adapter, reactor, left, right, ace = build(lanes=(1,))
    adapter.hook_all()
    left._hang_neutral(1)  # lane 1, hooked
    left._hang_neutral(0)  # lane 2, not hooked
    assert ("hang_neutral", 1) not in left.calls
    assert ("hang_neutral", 0) in left.calls


def test_the_wheel_counts_absolute_travel() -> None:
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


def test_the_wheel_is_seeded_at_hook_from_the_current_encoder() -> None:
    adapter, reactor, left, right, ace = build()
    ace.lanes[0]["encoder_mm"] = 500.0
    adapter.hook_all()
    adapter.watch(reactor.now)
    assert left.wheel[1].get_counts() == 0


def test_small_steps_add_up_to_whole_counts() -> None:
    # at ppr 6 one count is about 2.6 mm: steps of 0.3 mm each round to 0 on their own
    wheel = ace2k_u1_hooks.EncoderWheel(6, 31.4159)
    wheel.sample(0.0, 0.0)
    seen = []
    for i in range(1, 101):
        wheel.sample(0.3 * i, 0.2 * i)
        seen.append(wheel.get_counts())
    assert seen == sorted(seen)  # never decreases
    assert seen[9] == int(3.0 * 12 / 31.4159)  # 10 steps, 3 mm -> 1
    assert seen[-1] == int(30.0 * 12 / 31.4159)  # 100 steps, 30 mm -> 11
    assert abs(wheel.get_rpm() - 0.3 / 31.4159 * 60.0 / 0.2) < 1e-9


def test_small_steps_count_through_the_watch() -> None:
    adapter, reactor, left, right, ace = build()
    adapter.hook_all()
    wheel = left.wheel[1]
    for _ in range(10):
        reactor.now += 0.2
        ace.lanes[0]["encoder_mm"] += 0.3
        adapter.watch(reactor.now)
    assert wheel.get_counts() == 1


def test_hooking_maps_each_lane_to_its_heads_extruder() -> None:
    adapter, reactor, left, right, ace = build()
    assert adapter.hook_all() == []
    assert sorted(ace.feed.extruders) == [
        (0, "extruder"),
        (1, "extruder1"),
        (2, "extruder2"),
        (3, "extruder3"),
    ]


def test_only_the_hooked_lanes_are_mapped() -> None:
    adapter, reactor, left, right, ace = build(lanes=(2, 4))
    assert adapter.hook_all() == []
    assert sorted(ace.feed.extruders) == [(1, "extruder1"), (3, "extruder3")]


def test_a_failed_hook_maps_nothing() -> None:
    adapter, reactor, left, right, ace = build()
    del right.wheel_2
    assert adapter.hook_all()
    assert ace.feed.extruders == []


def test_the_extruder_names_are_the_printers_own() -> None:
    # the names are read off the U1's extruder objects by their index, whatever they are called
    named = fakes.extruder_list()
    named[2].name = "toolhead_c"
    adapter, reactor, left, right, ace = build(extruders=list(reversed(named)))
    adapter.hook_all()
    assert (2, "toolhead_c") in ace.feed.extruders
    assert (0, "extruder") in ace.feed.extruders


def test_the_head_of_an_extruder_is_read_from_the_map() -> None:
    named = fakes.extruder_list()
    named[2].name = "toolhead_c"
    adapter = build(extruders=named)[0]
    assert adapter.head_of_extruder("toolhead_c") == 2
    assert adapter.head_of_extruder("extruder") == 0
    assert adapter.head_of_extruder("extruder9") is None
    adapter.extruders = None
    assert adapter.head_of_extruder("extruder") is None


def test_a_head_without_an_extruder_is_hooked_unmapped_and_said() -> None:
    adapter, reactor, left, right, ace = build(extruders=fakes.extruder_list(3))
    console = Console()
    adapter.gcode = console
    assert adapter.hook_all() == []
    assert len(adapter.hooked) == 4
    assert sorted(ace.feed.extruders) == [(0, "extruder"), (1, "extruder1"), (2, "extruder2")]
    assert console.lines == ["ace2k_u1: lane 4: no extruder 3 on the printer; no feed-forward"]
