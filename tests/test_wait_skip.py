"""The wait after a swap (S6).

The U1's pre-extrusion ends with 5 s of fan, and the slicer's M109 right after the change checks
once a second for the nozzle to settle; the head waited parked 1–2 s and dripped. The first M109
for the swapped tool after its swap runs without the wait: the pre-extrusion already waited."""

from pathlib import Path
from types import SimpleNamespace

from test_tools import failing, print_start, swap_rig, tool
from test_units import Gcmd


def temps(rig: SimpleNamespace) -> dict[str, list[dict[str, str]]]:
    calls: dict[str, list[dict[str, str]]] = {"M104": [], "M109": []}
    for name, seen in calls.items():
        rig.gcode.commands_orig[name] = lambda gcmd, seen=seen: seen.append(gcmd.params)
    return calls


def m109(rig: SimpleNamespace, **params: str) -> None:
    rig.gcode.commands["M109"](Gcmd(dict(params), "M109"))


def swapped(tmp_path: Path) -> SimpleNamespace:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    tool(rig, 2)
    return rig


def test_the_first_wait_after_the_swap_is_skipped_once(tmp_path: Path) -> None:
    rig = swapped(tmp_path)
    calls = temps(rig)
    m109(rig, S="220", T="2")
    assert calls == {"M104": [{"S": "220", "T": "2"}], "M109": []}
    m109(rig, S="220", T="2")
    assert calls["M109"] == [{"S": "220", "T": "2"}]


def test_another_tools_wait_is_kept(tmp_path: Path) -> None:
    rig = swapped(tmp_path)
    calls = temps(rig)
    other = next(n for n, h in rig.module.tracker.plan.choice.items() if h.head != 0)
    m109(rig, S="220", T=str(other))
    assert calls["M104"] == [] and len(calls["M109"]) == 1


def test_a_t_between_clears_the_mark(tmp_path: Path) -> None:
    rig = swapped(tmp_path)
    other = next(n for n, h in rig.module.tracker.plan.choice.items() if h.head != 0)
    tool(rig, other)
    calls = temps(rig)
    m109(rig, S="220", T="2")
    assert calls["M104"] == [] and len(calls["M109"]) == 1


def test_a_failed_swap_leaves_no_mark(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)
    failing(rig, "ACEK_SWAP_LOAD")
    tool(rig, 2)
    assert rig.module.tracker.wait_skip_tool is None
    assert rig.module.tracker.wait_skip_temp is None


def test_the_prints_end_clears_the_mark(tmp_path: Path) -> None:
    rig = swapped(tmp_path)
    assert rig.module.tracker.wait_skip_tool == 2
    rig.print_stats.state = "complete"
    rig.module.watch_print(rig.reactor.now)
    assert rig.module.tracker.wait_skip_tool is None
    assert rig.module.tracker.wait_skip_temp is None


def test_the_swaps_own_wait_is_never_skipped(tmp_path: Path) -> None:
    rig = swapped(tmp_path)
    calls = temps(rig)
    m109(rig, S="220", T="0", A="0")
    assert calls["M104"] == [] and len(calls["M109"]) == 1
    assert rig.module.tracker.wait_skip_tool == 2


def test_a_print_start_clears_the_mark(tmp_path: Path) -> None:
    rig = swapped(tmp_path)
    rig.module.tracker.wait_skip_tool = 2
    rig.module.tracker.wait_skip_temp = 220.0
    rig.print_stats.state = "standby"  # a running print refuses a start before the clears
    print_start(rig)
    assert rig.module.tracker.wait_skip_tool is None
    assert rig.module.tracker.wait_skip_temp is None


def test_a_resumes_own_heat_is_never_skipped(tmp_path: Path) -> None:
    """The U1's RESUME heats with M109 ... T<n> A0, so it never meets the skip."""
    rig = swapped(tmp_path)
    calls = temps(rig)
    m109(rig, S="220", T="0", A="0")
    assert calls["M109"] == [{"S": "220", "T": "0", "A": "0"}] and calls["M104"] == []
    assert rig.module.tracker.wait_skip_tool == 2


def test_the_wait_is_skipped_near_the_swaps_temperature(tmp_path: Path) -> None:
    """The swap left the head at its PRINT_TEMP; an M109 within 1.5 °C of it does not wait."""
    rig = swapped(tmp_path)
    assert rig.module.tracker.wait_skip_temp == 220
    calls = temps(rig)
    m109(rig, S="221.5", T="2")
    assert calls == {"M104": [{"S": "221.5", "T": "2"}], "M109": []}
    assert rig.module.tracker.wait_skip_temp is None


def test_another_temperature_waits_and_spends_the_mark(tmp_path: Path) -> None:
    """A swap at the load temperature (no printing one known) is not the file's: it waits."""
    rig = swapped(tmp_path)
    calls = temps(rig)
    m109(rig, S="190", T="2")
    assert calls == {"M104": [], "M109": [{"S": "190", "T": "2"}]}
    assert rig.module.tracker.wait_skip_tool is None
    assert rig.module.tracker.wait_skip_temp is None
    m109(rig, S="220", T="2")  # the mark is spent: this one waits too
    assert len(calls["M109"]) == 2 and calls["M104"] == []
