"""The swap stops of the print that runs, measured (status print_stops, ace2k_u1_swap_times).

The rigs are the ones of test_tools (swap_rig) and test_background (bg_rig); the fake clock
moves by hand, so each stop is known.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import ace2k_u1_swap_times as st
import fakes
import pytest
import test_states
from test_background import bg_rig, finished
from test_swap_time_samples import STEP_S, run_state, step_clock
from test_tools import ends, freed_then, probe_finds, resume, swap_rig, tool
from test_units import Gcmd

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from ace2k_u1_swap import SwapState


def stops(rig: SimpleNamespace) -> dict[str, object]:
    return rig.module.get_status()["print_stops"]  # type: ignore[no-any-return]


class Stats(fakes.FakePrintStats):
    """print_stats with a print_duration that follows the fake clock."""

    def __init__(self, rig: SimpleNamespace, state: str = "printing") -> None:
        super().__init__(state)
        self.rig, self.at = rig, rig.reactor.now - 100.0  # 100 s printed so far

    def get_status(self, eventtime: float | None = None) -> dict[str, object]:
        status = dict(super().get_status(eventtime))
        status["print_duration"] = max(0.0, self.rig.reactor.now - self.at)
        return status


def inline_ready(rig: SimpleNamespace, a: fakes.FakeAce2k) -> None:
    a.feed.on_start = ends("done")
    a.feed.on_push = lambda: setattr(rig.head_sensor, "detected", True)


def test_no_stops_before_any_swap(tmp_path: Path) -> None:
    rig, _ = swap_rig(tmp_path)
    assert stops(rig) == {"s": 0.0, "n": 0, "since": None}


def test_an_inline_swap_counts_its_stop_from_its_t(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    ran = step_clock(rig)
    inline_ready(rig, a)
    bg = rig.module.background
    real = bg.before_tool

    def waits(gcmd: object, n: int, owed: object = None) -> object:
        rig.reactor.now += 3.0  # the T waits 3 s before its swap starts (a parked move)
        return real(gcmd, n, owed)

    bg.before_tool = waits
    began = rig.reactor.now
    assert tool(rig, 2) == [{}]
    out = stops(rig)
    assert out == {"s": round(rig.reactor.now - began, 1), "n": 1, "since": None}
    assert out["s"] >= 3.0 + STEP_S * len(ran)  # type: ignore[operator]
    assert rig.module.swap_times.stops.stop is None  # the T ended


def test_the_stop_runs_to_the_end_of_the_whole_t(tmp_path: Path) -> None:
    # The U1's own T (its head pick) after the swap is part of the stop.
    rig, a = swap_rig(tmp_path)
    step_clock(rig)
    inline_ready(rig, a)
    rig.gcode.commands_orig["T2"] = lambda gcmd: setattr(rig.reactor, "now", rig.reactor.now + 12.0)
    began = rig.reactor.now
    rig.gcode.commands["T2"](Gcmd({}, "T2"))
    out = stops(rig)
    assert out["n"] == 1
    assert out["s"] == round(rig.reactor.now - began, 1)


def test_a_t_with_no_swap_is_no_stop(tmp_path: Path) -> None:
    rig, _ = swap_rig(tmp_path)
    assert tool(rig, 0) == [{}]  # a:1 is in head 1 already
    assert stops(rig)["n"] == 0


def test_a_swap_before_the_print_clock_started_is_not_counted(tmp_path: Path) -> None:
    # Klipper's print_duration stays 0 until the first extrusion: a swap before it is not in it.
    rig, a = swap_rig(tmp_path)
    rig.print_stats = Stats(rig)
    rig.print_stats.at = 1e9  # print_duration reads 0 (clamped below), as before the extrusion
    rig.printer.objects["print_stats"] = rig.print_stats
    step_clock(rig)
    inline_ready(rig, a)
    seen: list[object] = []
    real = rig.module.swapper.swap_step

    def step(name: str, state: SwapState) -> None:
        seen.append(stops(rig)["since"])
        real(name, state)

    rig.module.swapper.swap_step = step
    assert tool(rig, 2) == [{}]
    assert stops(rig) == {"s": 0.0, "n": 0, "since": None}
    assert seen and set(seen) == {None}


def test_a_pause_that_came_into_the_stop_drops_it(tmp_path: Path) -> None:
    # A pause taken and resumed during the T: print_duration leaves the paused time out.
    rig, a = swap_rig(tmp_path)
    step_clock(rig)
    inline_ready(rig, a)
    real = rig.module.swapper.swap_step

    def step(name: str, state: SwapState) -> None:
        if name == "load":
            rig.module.swap_times.stops.note_pause()  # print_stats:paused, then a resume
        real(name, state)

    rig.module.swapper.swap_step = step
    assert tool(rig, 2) == [{}]
    assert stops(rig)["n"] == 0
    # The next T's swap counts again.
    rig.module.swapper.swap_step = real
    inline_ready(rig, a)
    assert tool(rig, 0) == [{}]
    assert stops(rig)["n"] == 1


def test_the_print_stats_events_clear_the_stops_and_drop_a_paused_one() -> None:
    u1, printer, _, _ = test_states.hooked_u1()
    handlers = dict(printer.handlers)
    stops = u1.swap_times.stops
    stops.s, stops.n = 40.0, 2
    stops.stop = st.Stop(stops.gen, 5.0)
    stops.since = 12.0
    handlers["print_stats:paused"]()
    assert not stops.stop.counts
    assert stops.since is None  # cleared as soon as the stop will not count
    handlers["print_stats:new_task_start"]()
    assert (stops.s, stops.n, stops.stop) == (0.0, 0, None)


def test_since_is_the_print_duration_at_the_stops_start_while_it_runs(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    rig.print_stats = Stats(rig)
    rig.printer.objects["print_stats"] = rig.print_stats
    inline_ready(rig, a)
    seen: list[object] = []
    real = rig.module.swapper.swap_step

    def step(name: str, state: SwapState) -> None:
        rig.reactor.now += STEP_S
        seen.append(stops(rig)["since"])
        real(name, state)

    rig.module.swapper.swap_step = step
    assert tool(rig, 2) == [{}]
    assert seen and set(seen) == {100.0}  # the T came at 100 s printed
    assert stops(rig)["since"] is None
    assert stops(rig)["n"] == 1


def test_swaps_the_averages_leave_out_still_count(tmp_path: Path) -> None:
    # A probed swap and a T typed at the console while printing stop the print all the same.
    rig, a = swap_rig(tmp_path)
    step_clock(rig)
    probe_finds(rig, 0)
    run_state(rig, a, probed=True)
    assert stops(rig)["n"] == 1
    assert rig.module.get_status()["swap_times"]["inline"]["n"] == 0


def test_a_swap_in_a_paused_print_or_a_failed_one_is_not_counted(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    step_clock(rig)
    a.feed.on_start = freed_then(rig, "stuck", filament_mm=-250.0, motor_mm=-250.0)
    assert tool(rig, 2) == []  # it failed and paused the print
    assert stops(rig)["n"] == 0
    inline_ready(rig, a)
    probe_finds(rig, 0)
    resume(rig)  # RESUME finishes it while the print is paused: print_duration leaves it out
    assert rig.module.swapper.swap_pending is None
    assert stops(rig)["n"] == 0


def test_an_arrival_counts_its_wait_for_the_dock(tmp_path: Path) -> None:
    rig, _ = bg_rig(tmp_path)
    finished(rig)
    bg = rig.bg
    real = bg.stop_and_wait

    def wait(*args: object, **kw: object) -> object:
        rig.reactor.now += 7.0  # the dock reaches a safe point 7 s after the T
        return real(*args, **kw)

    bg.stop_and_wait = wait
    real_resume = rig.module.swapper.swap_step

    def step(name: str, state: SwapState) -> None:
        rig.reactor.now += 2.0
        real_resume(name, state)

    rig.module.swapper.swap_step = step
    began = rig.reactor.now
    assert tool(rig, 2) == [{}]
    out = stops(rig)
    assert out["n"] == 1
    assert out["s"] == round(rig.reactor.now - began, 1)
    assert out["s"] >= 7.0  # type: ignore[operator]


def start_print(rig: SimpleNamespace, refuse: bool = False) -> None:
    rig.print_stats.state = "standby"

    def start(gcmd: object) -> None:
        if refuse:
            raise ValueError("no such file")

    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = start
    rig.gcode.commands["SDCARD_PRINT_FILE"](Gcmd({"FILENAME": "eight.gcode"}, "SDCARD_PRINT_FILE"))


def test_the_print_start_command_clears_them_and_a_failed_one_puts_them_back(
    tmp_path: Path,
) -> None:
    rig, a = swap_rig(tmp_path)
    step_clock(rig)
    inline_ready(rig, a)
    assert tool(rig, 2) == [{}]
    before = dict(stops(rig))
    assert before["n"] == 1
    with pytest.raises(ValueError, match="no such file"):
        start_print(rig, refuse=True)
    assert stops(rig) == before
    start_print(rig)
    assert stops(rig) == {"s": 0.0, "n": 0, "since": None}


# --- PrintStops on its own: a hand-moved clock ---------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


class Duration:
    """A printer whose print_stats reads print_duration (None: no such object)."""

    def __init__(self, value: float | None) -> None:
        self.value = value

    def lookup_object(self, name: str, default: object = None) -> object:
        if self.value is None:
            return default
        value = self.value
        return types.SimpleNamespace(get_status=lambda eventtime: {"print_duration": value})


def unit(duration: float | None = 500.0) -> tuple[st.PrintStops, Clock, Duration]:
    return st.PrintStops(), Clock(), Duration(duration)


def on() -> bool:
    return True


def test_a_u1_t_inside_a_swap_with_no_t_around_it_is_the_swaps_stop() -> None:
    # A swap with no T of the file around it (typed at the console, or a pending swap): the U1's
    # own T A0 that its return sends must not open, end or clear its stop.
    stops, clock, printer = unit()
    with stops.swap(clock, on, printer) as run:  # type: ignore[arg-type]
        clock.now += 30.0
        assert stops.since == 500.0
        with stops.of_t(clock, on, u1_t=True):  # type: ignore[arg-type]
            clock.now += 5.0
        assert stops.since == 500.0 and stops.stop is not None
        with stops.of_t(clock, on, u1_t=False):  # type: ignore[arg-type]
            clock.now += 5.0  # any T while a swap runs: the same stop
        assert stops.stop is not None
        run.ok = True
    assert (stops.n, stops.s, stops.since) == (1, 40.0, None)


def test_a_failed_swap_in_a_t_clears_since_at_once_and_adds_nothing() -> None:
    stops, clock, printer = unit()
    with stops.of_t(clock, on, u1_t=False):  # type: ignore[arg-type]
        with stops.swap(clock, on, printer):  # type: ignore[arg-type]
            clock.now += 20.0
            assert stops.since == 500.0
        assert stops.since is None  # failed: known at once, not at the T's end
        clock.now += 10.0
    assert (stops.n, stops.s) == (0, 0.0)


def test_two_swaps_in_one_t_are_one_stops_time_and_two_swaps() -> None:
    stops, clock, printer = unit()
    with stops.of_t(clock, on, u1_t=False):  # type: ignore[arg-type]
        clock.now += 2.0
        for _ in range(2):
            with stops.swap(clock, on, printer) as run:  # type: ignore[arg-type]
                clock.now += 10.0
                run.ok = True
        assert stops.since == 498.0  # from the T's start
        clock.now += 3.0
    assert (stops.n, stops.s, stops.since) == (2, 25.0, None)


def test_a_stop_ends_uncounted_when_the_print_does_not_print_at_its_end() -> None:
    stops, clock, printer = unit()
    printing = [True]
    with stops.of_t(clock, lambda: printing[0], u1_t=False):  # type: ignore[arg-type]
        with stops.swap(clock, lambda: printing[0], printer) as run:  # type: ignore[arg-type]
            run.ok = True
        printing[0] = False
    assert stops.n == 0


def test_a_swap_while_paused_or_before_the_clock_or_unread_duration() -> None:
    stops, clock, printer = unit()
    with stops.swap(clock, lambda: False, printer) as run:  # type: ignore[arg-type]
        assert stops.since is None
        run.ok = True
    assert stops.n == 0
    stops, clock, printer = unit(0.0)  # before the first extrusion
    with stops.swap(clock, on, printer) as run:  # type: ignore[arg-type]
        assert stops.since is None
        run.ok = True
    assert stops.n == 0
    stops, clock, printer = unit(None)  # no print_stats: counted, no since
    with stops.swap(clock, on, printer) as run:  # type: ignore[arg-type]
        clock.now += 7.0
        assert stops.since is None
        run.ok = True
    assert (stops.n, stops.s) == (1, 7.0)


def test_reset_drops_the_open_stop_and_an_old_t_adds_nothing_to_the_new_print() -> None:
    stops, clock, printer = unit()
    with stops.of_t(clock, on, u1_t=False):  # type: ignore[arg-type]
        with stops.swap(clock, on, printer) as run:  # type: ignore[arg-type]
            run.ok = True
            stops.reset()  # a new print's start, while the old T is still open
            assert (stops.stop, stops.running, stops.since) == (None, False, None)
        clock.now += 10.0
    assert (stops.n, stops.s, stops.since) == (0, 0.0, None)


def test_the_status_object_is_built_again_only_after_a_change(tmp_path: Path) -> None:
    rig, a = swap_rig(tmp_path)
    first = stops(rig)
    assert stops(rig) is first
    step_clock(rig)
    inline_ready(rig, a)
    tool(rig, 2)
    assert stops(rig) is not first
