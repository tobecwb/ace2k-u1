"""A printing temperature for a tool whose lane is not in its head reaches a docked head (S1–S3).

The slicer pre-heats the next tool 30 s before its change. On a shared head that is docked, the
U1 would heat it; the adapter now lets that call through, at most at the tip temperature of the
filament in that head, and still keeps the slicer's value for the swap. While the head prints
another tool, the call stays kept, so that tool's temperature does not move.
"""

import logging
from pathlib import Path
from types import SimpleNamespace

import fakes
import pytest
from test_tools import planned, print_start, run, swap_rig, temp, tool
from test_units import Gcmd

KEPT = "ace2k_u1: T2's temperature kept for its swap: head 1 prints a:1"


def docked(rig: SimpleNamespace, name: str = "extruder1") -> None:
    """Sets the toolhead's active extruder: another one than "extruder" docks head 1."""
    rig.printer.objects["toolhead"] = fakes.FakeToolhead(name)


def test_a_docked_head_takes_the_pre_heat_and_keeps_it_for_the_swap(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    docked(rig)
    assert temp(rig, "M104", {"S": "220", "T": "2"}) == [{"S": "220", "T": "2"}]
    assert rig.module.tracker.plan.kept[2] == 220.0
    assert KEPT not in rig.gcode.lines
    assert temp(rig, "M109", {"S": "220", "T": "2"}) == [{"S": "220", "T": "2"}]


def test_the_printing_head_keeps_another_tools_pre_heat(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    docked(rig, "extruder")  # head 1 prints a:1
    assert temp(rig, "M104", {"S": "240", "T": "2"}) == []
    assert rig.module.tracker.plan.kept[2] == 240.0
    assert rig.gcode.lines.count(KEPT) == 1


def test_a_standby_or_an_off_for_a_docked_head_keeps_todays_handling(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    docked(rig)
    assert temp(rig, "M104", {"S": "70", "T": "2"}) == []
    assert 2 not in rig.module.tracker.plan.kept
    assert temp(rig, "M104", {"S": "0", "T": "2"}) == []
    assert rig.module.tracker.plan.off_held == {0: 2}
    assert KEPT not in rig.gcode.lines


def test_an_unreadable_active_extruder_keeps_the_call(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    rig.printer.objects.pop("toolhead", None)  # no toolhead to read the active extruder from
    assert temp(rig, "M104", {"S": "220", "T": "2"}) == []
    assert rig.module.tracker.plan.kept[2] == 220.0


def test_an_active_extruder_no_head_owns_keeps_the_call(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    docked(rig, "extruder9")
    assert temp(rig, "M104", {"S": "220", "T": "2"}) == []
    assert rig.module.tracker.plan.kept[2] == 220.0


def tips(rig: SimpleNamespace, **temps: float) -> None:
    """Sets unload_temps: the tip temperature of each material."""
    rig.module.swap_cfg.unload_temps.clear()
    rig.module.swap_cfg.unload_temps.update(temps)


def test_a_pre_heat_at_the_tip_temperature_is_applied_as_it_is(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    docked(rig)
    tips(rig, PLA=220.0)  # PLA in head 1, PLA next
    assert temp(rig, "M104", {"S": "220", "T": "2"}) == [{"S": "220", "T": "2"}]


def test_a_docked_pre_heat_stops_at_the_tip_temperature_of_the_head(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    docked(rig)
    tips(rig, PLA=220.0)  # PLA in head 1 (tip 220 °C), PETG next at 245 °C
    assert temp(rig, "M104", {"S": "245", "T": "2"}) == [{"S": "220", "T": "2"}]
    assert rig.module.tracker.plan.kept[2] == 245.0  # the swap still prints at the slicer's value
    assert KEPT not in rig.gcode.lines
    # An M109 keeps its wait, at the capped value.
    assert temp(rig, "M109", {"S": "245", "T": "2"}) == [{"S": "220", "T": "2"}]


def test_a_docked_pre_heat_below_the_tip_temperature_is_applied(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "1", "TYPE": "PETG", "COLOR": "000000"})
    docked(rig)
    tips(rig, PLA=220.0, PETG=240.0)  # PETG in head 1 (tip 240 °C), PLA next at 220 °C
    assert temp(rig, "M104", {"S": "220", "T": "2"}) == [{"S": "220", "T": "2"}]
    assert temp(rig, "M104", {"S": "245", "T": "2"}) == [{"S": "240", "T": "2"}]  # the PETG's tip


def test_a_docked_head_with_no_lane_known_keeps_the_call(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    docked(rig)
    rig.module.adapter.slot_of_head(0).current = None
    assert temp(rig, "M104", {"S": "220", "T": "2"}) == []
    assert rig.module.tracker.plan.kept[2] == 220.0


def test_a_docked_head_whose_tip_temperature_is_unread_keeps_the_call(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    docked(rig)

    def unread(h: object) -> float:
        raise RuntimeError("no table")

    rig.module.swapper.tip_temp = unread
    assert temp(rig, "M104", {"S": "220", "T": "2"}) == []
    assert rig.module.tracker.plan.kept[2] == 220.0
    assert KEPT in rig.gcode.lines


# --- the cool-down of a head whose next T swaps onto it -----------------------------------------
# T0 = a:1 and T2 = a:2 share head 1; T1 = a:3 is head 2. The print is at T0, on head 1. The
# cool-down reaches the U1 as sent; the head gets the kept pre-heat once the next T docks it.

OWED = "ace2k_u1: head 1 docked: T0's cool-down of 70 °C raised to T2's kept pre-heat"


def bench_order(rig: SimpleNamespace, order: list[int]) -> None:
    """Sets the plan's T lines to order, the print at its first entry."""
    rig.module.tracker.plan.order = list(order)
    rig.module.tracker.plan.raw = list(order)
    rig.module.tracker.t_index = rig.module.tracker.t_raw = 0


def scripts(rig: SimpleNamespace) -> list[str]:
    """Records the scripts the module runs from a command."""
    sent: list[str] = []
    rig.module.u1.script = lambda script, timer=False: sent.append(script)
    return sent


def kept_now(rig: SimpleNamespace, n: int, value: float) -> None:
    """Keeps value for T<n> in the current tool segment."""
    rig.module.tracker.plan.kept[n] = value
    rig.module.tracker.plan.kept_at[n] = rig.module.tracker.t_raw


def test_the_bench_sequence_heats_head_1_once_it_docks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = planned(tmp_path)
    bench_order(rig, [0, 1, 2])
    tips(rig, PLA=220.0)
    docked(rig, "extruder")  # head 1 prints T0
    sent = scripts(rig)
    caplog.set_level(logging.INFO)
    assert temp(rig, "M104", {"S": "220", "T": "2"}) == []  # kept: head 1 prints T0
    assert rig.module.tracker.plan.kept[2] == 220.0
    lines = list(rig.gcode.lines)
    assert temp(rig, "M104", {"S": "70", "T": "0"}) == [{"S": "70", "T": "0"}]  # as sent
    assert sent == []  # nothing while head 1 prints
    assert tool(rig, 1) == [{}]  # head 2 takes over: head 1 docks
    assert sent == ["M104 S220 T0 A0"]
    assert rig.gcode.lines == lines  # no console line
    said = [r.getMessage() for r in caplog.records if "docked" in r.getMessage()]
    assert said == [OWED + ": M104 S220 T0 A0"]
    assert rig.module.tracker.owed_heat == {}
    docked(rig)  # head 2 prints T1, head 1 docked
    assert temp(rig, "M109", {"S": "220", "T": "2"}) == [{"S": "220", "T": "2"}]
    assert tool(rig, 2) == [{}]
    assert rig.swaps == [(0, 1, 2)]
    assert sent == ["M104 S220 T0 A0"]  # nothing owed is sent twice


def test_the_owed_pre_heat_stops_at_the_tip_temperature(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    bench_order(rig, [0, 1, 2])
    tips(rig, PLA=220.0)  # PLA in head 1 (tip 220 °C), PETG kept at 245 °C
    kept_now(rig, 2, 245.0)
    sent = scripts(rig)
    assert temp(rig, "M109", {"S": "70", "T": "0"}) == [{"S": "70", "T": "0"}]
    tool(rig, 1)
    assert sent == ["M104 S220 T0 A0"]  # an M104 even for an M109: nothing waits
    assert rig.module.tracker.plan.kept[2] == 245.0


def test_a_pre_heat_kept_in_an_earlier_segment_owes_nothing(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    bench_order(rig, [0, 1, 2])
    tips(rig, PLA=220.0)
    rig.module.tracker.plan.kept[2] = 220.0
    rig.module.tracker.plan.kept_at[2] = -1  # before the T0 that runs now (a leftover)
    sent = scripts(rig)
    temp(rig, "M104", {"S": "70", "T": "0"})
    tool(rig, 1)
    assert sent == []


@pytest.mark.parametrize(
    ("order", "kept", "s"),
    [
        ([0, 1, 2], None, "70"),  # no kept value
        ([0, 1, 0, 2], 220.0, "70"),  # head 1's next T is T0 again: no swap
        ([0, 1, 2], 220.0, "0"),  # an off
        ([0, 1, 2], 220.0, "210"),  # a printing temperature of the printing tool
    ],
)
def test_otherwise_nothing_is_owed(
    tmp_path: Path, order: list[int], kept: float | None, s: str
) -> None:
    rig, _ = planned(tmp_path)
    bench_order(rig, order)
    tips(rig, PLA=220.0)
    if kept is not None:
        kept_now(rig, 2, kept)
    sent = scripts(rig)
    assert temp(rig, "M104", {"S": s, "T": "0"}) == [{"S": s, "T": "0"}]
    tool(rig, 1)
    assert sent == []


def test_an_unread_tip_temperature_owes_nothing(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    bench_order(rig, [0, 1, 2])
    kept_now(rig, 2, 220.0)

    def unread(h: object) -> float:
        raise RuntimeError("no table")

    rig.module.swapper.tip_temp = unread
    sent = scripts(rig)
    temp(rig, "M104", {"S": "70", "T": "0"})
    tool(rig, 1)
    assert sent == []


def test_no_t_to_another_head_sends_nothing(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    bench_order(rig, [0, 1, 2])
    tips(rig, PLA=220.0)
    kept_now(rig, 2, 220.0)
    sent = scripts(rig)
    temp(rig, "M104", {"S": "70", "T": "0"})
    assert rig.module.tracker.owed_heat[0][0] == 220.0
    tool(rig, 0, {"A": "0"})  # a T of the U1 itself: the segment goes on
    assert rig.module.tracker.owed_heat
    tool(rig, 0)  # the head's own T: it stays the active one
    assert sent == [] and rig.module.tracker.owed_heat == {}


OWED_TWO = {0: (220.0, "head 1"), 1: (200.0, "head 2")}


def direct_swap(rig: SimpleNamespace) -> bool:
    """Runs the real swap of head 1 to a:2 for T2, outside the T wrapper (a refill would)."""
    slot = rig.module.adapter.slot_of_head(0)
    return rig.module.swapper.swap(Gcmd({}, "T2"), slot, slot.lanes[1], 2)


def test_a_swap_onto_the_head_drops_what_it_owes(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    rig.module.tracker.owed_heat = dict(OWED_TWO)
    assert direct_swap(rig)
    assert rig.module.tracker.owed_heat == {1: OWED_TWO[1]}


def test_a_failed_swap_drops_everything_owed(tmp_path: Path) -> None:
    rig, _ = swap_rig(tmp_path)
    real = rig.gcode.run_script_from_command

    def run_script(script: str) -> None:
        real(script)
        if script.startswith("ACEK_SWAP_UNLOAD"):
            raise RuntimeError("heater fault")

    rig.gcode.run_script_from_command = run_script
    rig.module.tracker.owed_heat = dict(OWED_TWO)
    assert not direct_swap(rig)
    assert rig.module.tracker.owed_heat == {}


def test_a_print_start_drops_everything_owed(tmp_path: Path) -> None:
    rig, _ = planned(tmp_path)
    rig.print_stats.state = "complete"
    rig.module.tracker.owed_heat = dict(OWED_TWO)
    print_start(rig)
    assert rig.module.tracker.owed_heat == {}


def test_a_failed_send_is_logged_and_the_next_head_still_heats(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = planned(tmp_path)
    sent: list[str] = []

    def script(line: str, timer: bool = False) -> None:
        if "T0" in line:
            raise RuntimeError("refused")
        sent.append(line)

    rig.module.u1.script = script
    rig.module.wraps._heat_docked([(0, 220.0, "a"), (1, 200.0, "b")])
    assert sent == ["M104 S200 T1 A0"]
    assert "head 1's owed pre-heat" in caplog.text
