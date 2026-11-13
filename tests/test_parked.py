"""The parked move and the render check of the tip (ace2k_u1_parked).

A parked move turns the extruder of a docked head while another head prints. It is scheduled
after what the print has queued, on that extruder's stepper alone, with a motion queue and a
kinematics of its own. The tests stand in for the toolhead, the stepper, the stepper enable and
the motion library, and record what each is asked, in order.

The tip check renders the U1's CONTROL_RETRACT_ACTION for the parked head, without running it, and
keeps only the moves the dock can run. The three tables below are what the U1's macro gives for a
standard 0.4 mm nozzle: PLA (two pulls), a material with no table of its own (the 57 mm push) and
a soft filament.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from ace2k_u1_parked import OwnStepsError, ParkedMoveError, ParkedMover, TipStep, tip_moves

# --- the U1's tables, rendered (the macro's own indentation and blank lines kept) -----------

PLA_04 = """


                    M83
                    G1 E20 F400
                    G1 E10 F1500
                    G1 E-35 F3000
                    G1 E-5 F50
                    G1 E25 F1000
                    G1 E15 F1500
                    G1 E-35 F3300
                    G1 E-5 F25
                    G1 E-30 F1500

"""

PUSH_57 = """

                    M83
                    G1 E57 F400
                    G1 E3 F1500
                    G1 E-27 F2700
                    G1 E-5.5 F40
                    # G4 P200
                    G1 E-37.5 F1500
"""

SOFT_04 = """
                    M83
                    G1 E5 F600
                    G1 E-27 F2700
                    G1 E-5.5 F40
                    # G4 P200
                    G1 E-37.5 F1500

"""


# --- fakes -------------------------------------------------------------------------------------


class Log:
    """The calls of every fake, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def add(self, *call: object) -> None:
        self.calls.append(call)

    def names(self) -> list[object]:
        return [c[0] for c in self.calls]


class FakeLib:
    """The motion library: queues and kinematics are plain tokens."""

    def __init__(self, log: Log) -> None:
        self.log = log
        self.count = 0
        # the print time of a move that a kinematics has still to make steps for, by kinematics
        self.active: dict[object, float] = {}

    def trapq_alloc(self) -> str:
        self.count += 1
        return f"tq{self.count}"

    def cartesian_stepper_alloc(self, axis: bytes) -> str:
        self.count += 1
        return f"sk{self.count}:{axis.decode()}"

    def trapq_free(self, tq: object) -> None: ...

    def free(self, sk: object) -> None: ...

    def trapq_append(self, *args: object) -> None:
        self.log.add("trapq_append", *args)

    def trapq_finalize_moves(self, tq: object, a: float, b: float) -> None:
        self.log.add("trapq_finalize_moves", tq, a, b)

    def itersolve_check_active(self, sk: object, flush_time: float) -> float:
        at = self.active.get(sk, 0.0)
        return at if at < flush_time else 0.0


class FakeMain:
    def gc(self, obj: object, free: object) -> object:
        return obj


class FakeStepper:
    def __init__(self, name: str, log: Log) -> None:
        self.name = name
        self.log = log
        self.sk: object = f"own-sk-{name}"
        self.tq: object = f"own-tq-{name}"
        self.pos = 12.5
        self.fail = False

    def get_name(self) -> str:
        return self.name

    def get_commanded_position(self) -> float:
        return self.pos

    def get_stepper_kinematics(self) -> object:
        return self.sk

    def set_stepper_kinematics(self, sk: object) -> object:
        old, self.sk = self.sk, sk
        self.log.add("set_sk", self.name, sk)
        return old

    def set_trapq(self, tq: object) -> object:
        old, self.tq = self.tq, tq
        self.log.add("set_trapq", self.name, tq)
        return old

    def set_position(self, coord: tuple[float, float, float]) -> None:
        self.pos = coord[0]
        self.log.add("set_position", self.name, coord)

    def generate_steps(self, flush_time: float) -> None:
        self.log.add("generate_steps", self.name, flush_time)
        if self.fail:
            raise RuntimeError("stepcompress")


class FakeEnable:
    def __init__(self, log: Log, enabled: bool) -> None:
        self.log = log
        self.enabled = enabled

    def is_motor_enabled(self) -> bool:
        return self.enabled

    def motor_enable(self, print_time: float) -> None:
        self.log.add("motor_enable", print_time)
        self.enabled = True


class FakeStepperEnable:
    def __init__(self, log: Log) -> None:
        self.lines: dict[str, FakeEnable] = {}
        self.log = log

    def lookup_enable(self, name: str) -> FakeEnable:
        return self.lines.setdefault(name, FakeEnable(self.log, True))


class FakeExtruder:
    def __init__(self, name: str, log: Log) -> None:
        self.name = name
        self.heater = SimpleNamespace(can_extrude=True)
        self.extruder_stepper: SimpleNamespace | None = SimpleNamespace(
            stepper=FakeStepper(name, log)
        )

    def get_name(self) -> str:
        return self.name

    def get_heater(self) -> SimpleNamespace:
        return self.heater

    @property
    def stepper(self) -> FakeStepper:
        assert self.extruder_stepper is not None
        return self.extruder_stepper.stepper  # type: ignore[no-any-return]


class FakeMcu:
    def __init__(self) -> None:
        self.offset = 100.0  # print time = reactor time + offset

    def estimated_print_time(self, eventtime: float) -> float:
        return eventtime + self.offset


class FakeToolhead:
    def __init__(self, log: Log, active: FakeExtruder) -> None:
        self.log = log
        self.active = active
        self.print_time = 0.0
        self.step_gen_time = 0.0
        self.mcu = FakeMcu()

    def get_extruder(self) -> FakeExtruder:
        return self.active

    def note_mcu_movequeue_activity(self, mq_time: float) -> None:
        self.log.add("note_activity", mq_time)


class FakeReactor:
    def __init__(self) -> None:
        self.now = 10.0

    def monotonic(self) -> float:
        return self.now


class FakePrinter:
    def __init__(self, objects: dict[str, object]) -> None:
        self.objects = objects
        self.reactor = FakeReactor()
        self.shutdowns: list[str] = []

    def lookup_object(self, name: str, default: object = None) -> object:
        return self.objects.get(name, default)

    def get_reactor(self) -> FakeReactor:
        return self.reactor

    def invoke_shutdown(self, msg: str) -> None:
        self.shutdowns.append(msg)


def calc(dist: float, speed: float, accel: float) -> tuple[float, float, float, float]:
    """A stand-in for the trapezoid: 0.1 s of accel each way, the rest at speed."""
    axis_r = -1.0 if dist < 0 else 1.0
    return axis_r, 0.1, abs(dist) / speed - 0.1, speed


def rig(enabled: bool = True) -> SimpleNamespace:
    log = Log()
    e0, e1, e2 = (FakeExtruder(n, log) for n in ("extruder", "extruder1", "extruder2"))
    toolhead = FakeToolhead(log, e0)
    enable = FakeStepperEnable(log)
    enable.lines["extruder1"] = FakeEnable(log, enabled)
    printer = FakePrinter({"toolhead": toolhead, "stepper_enable": enable})
    lib = FakeLib(log)
    mover = ParkedMover(printer, ffi=(FakeMain(), lib), calc_move_time=calc)  # type: ignore[arg-type]
    return SimpleNamespace(
        log=log, e0=e0, e1=e1, e2=e2, toolhead=toolhead, printer=printer, lib=lib, mover=mover
    )


def last_append(r: SimpleNamespace) -> tuple[object, ...]:
    return next(c for c in reversed(r.log.calls) if c[0] == "trapq_append")


# --- the schedule ------------------------------------------------------------------------------


def test_the_start_is_the_mcu_clock_plus_the_delay_when_nothing_is_queued() -> None:
    r = rig()
    start, end = r.mover.move(r.e1, 10.0, 5.0)
    # MCU now is 110.0; + 0.25 delay + 0.05 gap
    assert start == pytest.approx(110.30)
    assert end == pytest.approx(start + 2.1)  # 0.1 up, 1.9 at speed, 0.1 down
    assert last_append(r)[2] == pytest.approx(start)


@pytest.mark.parametrize("field", ["print_time", "step_gen_time"])
def test_the_start_waits_behind_what_the_print_has_queued(field: str) -> None:
    r = rig()
    setattr(r.toolhead, field, 140.0)
    start, _ = r.mover.move(r.e1, 10.0, 5.0)
    assert start == pytest.approx(140.05)


def test_a_second_move_starts_at_the_first_ones_end() -> None:
    r = rig()
    _, end1 = r.mover.move(r.e1, 10.0, 5.0)
    start2, _ = r.mover.move(r.e1, -10.0, 5.0)
    assert start2 == pytest.approx(end1)  # back to back: no gap


def test_a_move_after_one_that_ended_long_ago_gets_the_gap() -> None:
    r = rig()
    r.mover.move(r.e1, 10.0, 5.0)
    r.toolhead.print_time = 140.0  # the print has queued past the first move's end
    start2, _ = r.mover.move(r.e1, -10.0, 5.0)
    assert start2 == pytest.approx(140.05)


def test_the_last_end_of_one_head_does_not_hold_another() -> None:
    r = rig()
    r.mover.move(r.e1, 50.0, 5.0)
    start, _ = r.mover.move(r.e2, 10.0, 5.0)
    assert start == pytest.approx(110.30)


def test_the_trapezoid_is_the_one_of_the_move() -> None:
    r = rig()
    start, end = r.mover.move(r.e1, -20.0, 10.0, accel=400.0)
    call = last_append(r)
    _, tq, t0, accel_t, cruise_t, decel_t, x, y, z, ax, ay, az, v0, cruise_v, accel = call[:15]
    assert (t0, accel_t, cruise_t, decel_t) == pytest.approx((start, 0.1, 1.9, 0.1))
    assert (x, y, z, ax, ay, az, v0) == (0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0)
    assert (cruise_v, accel) == (10.0, 400.0)
    assert end == pytest.approx(start + 2.1)


# --- the borrow --------------------------------------------------------------------------------


def borrow_calls(r: SimpleNamespace) -> list[tuple[object, ...]]:
    keep = {"set_sk", "set_trapq", "set_position", "generate_steps", "trapq_finalize_moves"}
    return [c for c in r.log.calls if c[0] in keep]


def test_the_stepper_is_borrowed_and_given_back_in_order() -> None:
    r = rig()
    _, end = r.mover.move(r.e1, 10.0, 5.0)
    calls = borrow_calls(r)
    sk, tq = calls[0][2], calls[1][2]
    assert calls == [
        ("set_sk", "extruder1", sk),
        ("set_trapq", "extruder1", tq),
        ("set_position", "extruder1", (0.0, 0.0, 0.0)),
        ("generate_steps", "extruder1", end),
        ("trapq_finalize_moves", tq, end + 99999.9, end + 99999.9),
        ("set_trapq", "extruder1", "own-tq-extruder1"),
        ("set_sk", "extruder1", "own-sk-extruder1"),
        ("set_position", "extruder1", (12.5, 0.0, 0.0)),
    ]
    assert last_append(r)[1] == tq
    assert r.log.calls[-1] == ("note_activity", end)
    assert r.e1.stepper.sk == "own-sk-extruder1"
    assert r.e1.stepper.tq == "own-tq-extruder1"


def test_the_stepper_is_given_back_when_the_steps_fail() -> None:
    r = rig()
    r.e1.stepper.fail = True
    with pytest.raises(RuntimeError):
        r.mover.move(r.e1, 10.0, 5.0)
    assert r.e1.stepper.sk == "own-sk-extruder1"
    assert r.e1.stepper.tq == "own-tq-extruder1"
    assert r.e1.stepper.pos == 12.5
    assert "trapq_finalize_moves" in r.log.names()
    assert "note_activity" not in r.log.names()
    assert r.mover.remaining_s(r.e1) == 0.0
    # steps that fail to be made are an internal error of Klipper: it shuts down
    assert r.printer.shutdowns == ["ace2k_u1: the parked move of extruder1: stepcompress"]


def test_a_refused_move_is_no_shutdown() -> None:
    r = rig()
    with pytest.raises(ParkedMoveError):
        r.mover.move(r.e1, 0.0, 5.0)
    assert r.printer.shutdowns == []


def test_an_extruder_with_steps_of_its_own_left_is_refused() -> None:
    r = rig()
    r.lib.active["own-sk-extruder1"] = 109.0  # a move of its own, not yet made into steps
    with pytest.raises(OwnStepsError, match="steps of its own"):
        r.mover.move(r.e1, 10.0, 5.0)
    assert borrow_calls(r) == [] and "motor_enable" not in r.log.names()
    assert r.mover.remaining_s(r.e1) == 0.0
    r.lib.active.clear()
    r.mover.move(r.e1, 10.0, 5.0)  # once they are made, the move goes


def test_own_steps_left_tells_a_move_would_wait_and_queues_nothing() -> None:
    r = rig()
    r.lib.active["own-sk-extruder1"] = 109.0
    assert r.mover.own_steps_left(r.e1) is True
    assert borrow_calls(r) == [] and "motor_enable" not in r.log.names()
    assert r.mover.remaining_s(r.e1) == 0.0
    r.lib.active.clear()
    assert r.mover.own_steps_left(r.e1) is False
    r.e1.extruder_stepper = None  # the move refuses it for another reason
    assert r.mover.own_steps_left(r.e1) is False


def test_each_extruder_has_its_own_queue_and_kinematics() -> None:
    r = rig()
    r.mover.move(r.e1, 10.0, 5.0)
    r.mover.move(r.e1, 10.0, 5.0)
    r.mover.move(r.e2, 10.0, 5.0)
    borrowed = {
        (c[1], c[2])
        for c in r.log.calls
        if c[0] in ("set_sk", "set_trapq") and not str(c[2]).startswith("own-")
    }
    by_name: dict[object, set[object]] = {}
    for name, obj in borrowed:
        by_name.setdefault(name, set()).add(obj)
    assert len(by_name["extruder1"]) == 2  # one queue and one kinematics, kept between moves
    assert len(by_name["extruder2"]) == 2
    assert not by_name["extruder1"] & by_name["extruder2"]
    assert r.lib.count == 4
    assert all(str(o).endswith(":x") for o in by_name["extruder1"] if str(o).startswith("sk"))


def test_a_disabled_motor_is_enabled_just_before_the_move() -> None:
    r = rig(enabled=False)
    start, _ = r.mover.move(r.e1, 10.0, 5.0)
    assert ("motor_enable", pytest.approx(start - 0.1)) in r.log.calls
    r.log.calls.clear()
    r.mover.move(r.e1, 10.0, 5.0)
    assert "motor_enable" not in r.log.names()


def test_the_motion_library_and_the_profile_are_klippers_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = rig()
    monkeypatch.setitem(
        sys.modules, "chelper", SimpleNamespace(get_ffi=lambda: (FakeMain(), r.lib))
    )
    monkeypatch.setitem(sys.modules, "force_move", SimpleNamespace(calc_move_time=calc))
    mover = ParkedMover(r.printer)
    start, end = mover.move(r.e1, 10.0, 5.0)
    assert end == pytest.approx(start + 2.1)
    assert last_append(r)[1] == "tq1"


# --- the refusals ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("distance", "speed"),
    [(0.0, 5.0), (200.1, 5.0), (-200.1, 5.0), (10.0, 0.0), (10.0, -1.0), (10.0, 60.1)],
)
def test_a_move_out_of_its_limits_is_refused(distance: float, speed: float) -> None:
    r = rig()
    with pytest.raises(ParkedMoveError):
        r.mover.move(r.e1, distance, speed)
    assert r.log.calls == []


def test_the_limits_themselves_are_taken() -> None:
    r = rig()
    r.mover.move(r.e1, 200.0, 60.0)
    r.mover.move(r.e1, -200.0, 60.0)


def test_the_active_extruder_is_refused() -> None:
    r = rig()
    with pytest.raises(ParkedMoveError, match="active"):
        r.mover.move(r.e0, 10.0, 5.0)
    assert r.log.calls == []


def test_a_cold_head_is_refused() -> None:
    r = rig()
    r.e1.heater.can_extrude = False
    with pytest.raises(ParkedMoveError, match="min_extrude_temp"):
        r.mover.move(r.e1, 10.0, 5.0)
    assert r.log.calls == []


def test_an_extruder_without_a_stepper_is_refused() -> None:
    r = rig()
    r.e1.extruder_stepper = None
    with pytest.raises(ParkedMoveError, match="stepper"):
        r.mover.move(r.e1, 10.0, 5.0)


# --- the record for the feed-forward -----------------------------------------------------------


def test_the_move_is_recorded_with_its_start_duration_and_distance() -> None:
    r = rig()
    notes: list[tuple[str, float, float, float]] = []

    def note(name: str, start: float, duration: float, distance: float) -> bool:
        notes.append((name, start, duration, distance))
        return True

    start, end = r.mover.move(r.e1, -27.0, 45.0, note=note)
    assert notes == [("extruder1", start, pytest.approx(end - start), -27.0)]


@pytest.mark.parametrize("fails", ["false", "raises"])
def test_a_record_that_fails_is_logged_and_the_move_stands(
    fails: str, caplog: pytest.LogCaptureFixture
) -> None:
    r = rig()

    def note(name: str, start: float, duration: float, distance: float) -> bool:
        if fails == "raises":
            raise ValueError("no unit")
        return False

    start, end = r.mover.move(r.e1, 10.0, 5.0, note=note)
    assert "parked move" in caplog.text
    assert r.mover.move(r.e1, 10.0, 5.0)[0] == pytest.approx(end)


# --- remaining_s and queued ----------------------------------------------------------------


def test_a_dwell_pushes_the_next_move_back_by_its_length() -> None:
    r = rig()
    _, end1 = r.mover.move(r.e1, 10.0, 5.0)
    assert r.mover.dwell(r.e1, 0.2) == pytest.approx(end1 + 0.2)
    start2, _ = r.mover.move(r.e1, -10.0, 5.0)
    assert start2 == pytest.approx(end1 + 0.2)  # the pause is its length, not more


def test_a_dwell_with_no_move_starts_at_the_mcu_now() -> None:
    r = rig()
    assert r.mover.dwell(r.e1, 1.0) == pytest.approx(111.0)


@pytest.mark.parametrize("seconds", [-0.1, 10.1, float("nan")])
def test_a_dwell_out_of_its_limits_is_refused(seconds: float) -> None:
    r = rig()
    with pytest.raises(ParkedMoveError):
        r.mover.dwell(r.e1, seconds)


def test_remaining_s_counts_down_on_the_mcu_clock() -> None:
    r = rig()
    assert r.mover.remaining_s(r.e1) == 0.0
    _, end = r.mover.move(r.e1, 10.0, 5.0)
    assert r.mover.remaining_s(r.e1) == pytest.approx(end - 110.0)
    r.printer.reactor.now = end - 100.0 + 1.0
    assert r.mover.remaining_s(r.e1) == 0.0


def test_queued_tells_whether_any_parked_move_has_not_ended() -> None:
    r = rig()
    del r.printer.objects["toolhead"]
    assert r.mover.queued() is False  # no move ever: nothing is looked up
    r.printer.objects["toolhead"] = r.toolhead
    _, end = r.mover.move(r.e1, 10.0, 5.0)
    assert r.mover.queued() is True
    r.printer.reactor.now = end - 100.0 + 0.01
    assert r.mover.queued() is False
    del r.printer.objects["toolhead"]
    assert r.mover.queued() is False  # the ended move is forgotten


# --- the tip -----------------------------------------------------------------------------------


class FakeTemplate:
    def __init__(self, text: str | Exception) -> None:
        self.text = text
        self.context: dict[str, object] = {}

    def create_template_context(self) -> dict[str, object]:
        return {"printer": "status"}

    def render(self, context: dict[str, object]) -> str:
        self.context = context
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


def tip_rig(text: str | Exception | None) -> tuple[FakePrinter, FakeTemplate]:
    template = FakeTemplate(text if text is not None else "")
    objects: dict[str, object] = {}
    if text is not None:
        macro = SimpleNamespace(variables={"kept": 1}, template=template)
        objects["gcode_macro CONTROL_RETRACT_ACTION"] = macro
    return FakePrinter(objects), template


def tip(text: str | Exception | None, head: int = 1, soft: int = 0) -> list[TipStep] | str:
    printer, _ = tip_rig(text)
    return tip_moves(printer, head, soft)  # type: ignore[arg-type]


def test_the_macro_is_rendered_for_the_parked_head_as_a_command_would() -> None:
    printer, template = tip_rig(PLA_04)
    tip_moves(printer, 2, 1)  # type: ignore[arg-type]
    assert template.context == {
        "kept": 1,
        "printer": "status",
        "params": {"EXTRUDER": "2", "SOFT": "1"},
        "rawparams": "EXTRUDER=2 SOFT=1",
    }


def test_soft_given_as_a_flag_is_rendered_as_a_number() -> None:
    printer, template = tip_rig(SOFT_04)
    tip_moves(printer, 0, True)  # type: ignore[arg-type]
    assert template.context["params"] == {"EXTRUDER": "0", "SOFT": "1"}


def test_the_pla_table_gives_its_nine_moves() -> None:
    assert tip(PLA_04) == [
        TipStep("move", 20.0, 400.0),
        TipStep("move", 10.0, 1500.0),
        TipStep("move", -35.0, 3000.0),
        TipStep("move", -5.0, 50.0),
        TipStep("move", 25.0, 1000.0),
        TipStep("move", 15.0, 1500.0),
        TipStep("move", -35.0, 3300.0),
        TipStep("move", -5.0, 25.0),
        TipStep("move", -30.0, 1500.0),
    ]


def test_the_57_mm_table_drops_its_comment() -> None:
    assert tip(PUSH_57) == [
        TipStep("move", 57.0, 400.0),
        TipStep("move", 3.0, 1500.0),
        TipStep("move", -27.0, 2700.0),
        TipStep("move", -5.5, 40.0),
        TipStep("move", -37.5, 1500.0),
    ]


def test_the_soft_table() -> None:
    assert tip(SOFT_04) == [
        TipStep("move", 5.0, 600.0),
        TipStep("move", -27.0, 2700.0),
        TipStep("move", -5.5, 40.0),
        TipStep("move", -37.5, 1500.0),
    ]


def test_a_dwell_and_a_trailing_comment_are_taken() -> None:
    text = "M83\nG1 E5 F600 ; push\nG4 P200\ng1 f300 e-2\n"
    assert tip(text) == [
        TipStep("move", 5.0, 600.0),
        TipStep("dwell", 200.0, 0.0),
        TipStep("move", -2.0, 300.0),
    ]


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("M83\nG1 E5 F600\nG1 X10\n", "G1 X10"),
        ("M83\nM104 S200\nG1 E5 F600\n", "M104 S200"),
        ("M83\nG1 E5\n", "G1 E5"),
        ("M83\nG1 E5 F600 X1\n", "G1 E5 F600 X1"),
        ("M83\nG1 Ex F600\n", "G1 Ex F600"),
        ("M83\nG1 E5 F0\n", "G1 E5 F0"),
        ("M83\nG1 E0 F600\n", "G1 E0 F600"),
        ("M83\nG1 E5 E6\n", "G1 E5 E6"),
        ("M83\nG4 S1\n", "G4 S1"),
        ("M83\nG4 P-5\n", "G4 P-5"),
        ("G1 E5 F600\n", "G1 E5 F600"),
        ("M82\nG1 E5 F600\n", "M82"),
    ],
)
def test_a_line_the_dock_cannot_run_is_refused_by_name(text: str, line: str) -> None:
    result = tip(text)
    assert isinstance(result, str)
    assert line in result


@pytest.mark.parametrize("text", ["", "\n   \n", "M83\n# G4 P200\n", "M83\nG4 P200\n"])
def test_a_table_with_no_move_is_refused(text: str) -> None:
    result = tip(text)
    assert isinstance(result, str)
    assert "no move" in result


def test_a_missing_macro_is_refused() -> None:
    result = tip(None)
    assert isinstance(result, str)
    assert "CONTROL_RETRACT_ACTION" in result


def test_a_render_that_fails_is_refused() -> None:
    result = tip(RuntimeError("bad template"))
    assert isinstance(result, str)
    assert "bad template" in result
