"""The print tracker: the plan's published status is the dict the page has always read."""

from __future__ import annotations

import types

import ace2k_u1_tracker as tracker


def sample_plan() -> tracker.Plan:
    return tracker.Plan(
        file="x.gcode",
        path="/gcodes/x.gcode",
        mtime=12.5,
        tools=[
            {"colour": "#000000", "material": "PLA", "temp": 220, "first_temp": 225, "lane": "a:1"},
            {"colour": None, "material": None, "temp": None, "first_temp": None, "lane": None},
        ],
        order=[0, 1, 0],
        raw=[0, 0, 1, 0],
        order_at=[10, 20, 30],
        size=99,
        heads={0: 2},
        lanes=[{"key": "a:1", "head": 0, "colour": "#000000", "material": "PLA", "empty": False}],
    )


def host() -> types.SimpleNamespace:
    units = types.SimpleNamespace(lane_text=lambda unit, lane: f"{unit}:{lane + 1}")
    return types.SimpleNamespace(units=lambda: units)


def test_an_unset_plan_is_published_without_its_choice_or_its_private_state() -> None:
    t = tracker.PrintTracker(host())
    assert t.plan_status() is None
    t.plan = sample_plan()
    t.plan.kept[0] = 230.0
    t.plan.kept_said.add(0)
    t.plan.cool_said.add(0)
    t.plan.off_held[0] = 0
    status = t.plan_status()
    assert status == {
        "file": "x.gcode",
        "mtime": 12.5,
        "tools": t.plan.tools,
        "order": [0, 1, 0],
        "raw": [0, 0, 1, 0],
        "order_at": [10, 20, 30],
        "size": 99,
        "heads": {0: 2},
        "set": False,
        "choice": None,
        "lanes": t.plan.lanes,
    }
    assert status["tools"] is not t.plan.tools and status["order"] is not t.plan.order


def test_a_set_plan_is_published_with_its_choice_as_lane_texts_and_its_end_unload() -> None:
    t = tracker.PrintTracker(host())
    t.plan = sample_plan()
    t.plan.choice = {0: types.SimpleNamespace(unit="a", lane=0)}
    t.plan.set = True
    t.plan.end_unload = False
    assert t.plan_status() == {
        "file": "x.gcode",
        "mtime": 12.5,
        "tools": t.plan.tools,
        "order": [0, 1, 0],
        "raw": [0, 0, 1, 0],
        "order_at": [10, 20, 30],
        "size": 99,
        "heads": {0: 2},
        "set": True,
        "choice": {0: "a:1"},
        "lanes": t.plan.lanes,
        "end_unload": False,
    }
