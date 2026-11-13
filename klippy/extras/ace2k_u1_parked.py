"""Moves the extruder of a parked head while another head prints, and reads the tip of that head.

On the U1 each head has its own MCU and its own extruder stepper. A head in its dock can turn its
extruder while the active head prints, if the move is put on that stepper alone. ParkedMover does
that. It lends the stepper a motion queue and a kinematics of its own, adds one move, makes the
steps at once and gives the stepper back. This is how Klipper's FORCE_MOVE moves a stepper, but
without its flush and its dwell, which would stop the print. A parked move starts only after what
the print has already queued, so it waits behind the print's buffer. Once queued, it cannot be
taken back.

tip_moves reads the U1's tip for a head: the extruder moves of CONTROL_RETRACT_ACTION for that
head's nozzle and filament. It renders the macro as a command would, but does not run it. Only the
moves that a parked move can make are kept. Anything else refuses the whole tip, so the swap at
the T forms it instead.

The background swap (ace2k_u1_background) uses both.
"""

from __future__ import annotations

import importlib
import logging
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer
    from .ace2k_u1_printer_types import (
        CObject,
        FfiLib,
        FfiMain,
        GCodeMacro,
        MotionStepper,
        MotionToolHead,
        MovingExtruder,
        StepperEnable,
    )

# The limits of one parked move. A longer move or a faster one is refused. The U1's own tip moves
# reach 55 mm/s (F3300); its extruders allow 100 mm/s for a move with no XY.
MAX_DISTANCE_MM = 200.0
MAX_SPEED_MM_S = 60.0
# The longest pause between two parked moves (a G4 of the tip), in seconds.
MAX_DWELL_S = 10.0
# The acceleration of a parked move, in mm/s².
DEFAULT_ACCEL = 300.0
# A parked move starts at least this long after the MCU's now, so its steps reach the MCU in time.
SCHEDULE_DELAY_S = 0.25
# The gap added after the print's queue, its steps or the MCU's now, when one of them is the latest
# time a move must wait for. A move right after the last parked move of its extruder has no gap.
SCHEDULE_GAP_S = 0.05
# A motor that is off is turned on this long before its move starts.
ENABLE_LEAD_S = 0.1
# Emptying the private queue: a time far after the move, so every part of it goes.
FINALIZE_AFTER_S = 99999.9
# The line number that Klipper gives a move that no G-code line made.
NO_LINE = 0xFFFFFFFF

# The U1's tip macro, as a printer object.
TIP_MACRO = "gcode_macro CONTROL_RETRACT_ACTION"

# The motion profile of a move, as Klipper's force_move.calc_move_time computes it:
# (distance, speed, accel) -> (direction, accel time, cruise time, cruise speed).
CalcMoveTime = Callable[[float, float, float], tuple[float, float, float, float]]
# The record of a move for the feed-forward: (extruder name, start, duration, distance).
# It returns False, or raises, when it could not record the move.
Note = Callable[[str, float, float, float], object]


class ParkedMoveError(Exception):
    """A parked move that is refused: the head is active or cold, or the move is out of limits."""


class OwnStepsError(ParkedMoveError):
    """A parked move refused while the extruder's own moves still have steps to be made."""


class TipStep(NamedTuple):
    """One step of the tip.

    kind is "move" or "dwell". For a move, value is the E distance in mm and feed the speed in
    mm/min (the G1's E and F). For a dwell, value is the time in ms and feed is 0.
    """

    kind: str
    value: float
    feed: float


class _Borrowed(NamedTuple):
    """The private motion queue and kinematics of one extruder, made on its first parked move."""

    trapq: CObject
    sk: CObject


def _klipper_ffi() -> tuple[FfiMain, FfiLib]:
    """Returns the motion library of Klipper (chelper). It is only there on the printer."""
    chelper = importlib.import_module("chelper")
    return cast("tuple[FfiMain, FfiLib]", chelper.get_ffi())


def _klipper_calc_move_time() -> CalcMoveTime:
    """Returns calc_move_time of Klipper's force_move, a module of the same package."""
    name = f"{__package__}.force_move" if __package__ else "force_move"
    return cast("CalcMoveTime", importlib.import_module(name).calc_move_time)


class ParkedMover:
    """Moves the extruder of a parked head without stopping the print.

    Each extruder gets its own motion queue and kinematics, made on its first move and kept. Two
    extruders never share them, because the kinematics keeps the time of its last steps. The
    motion library and calc_move_time are found when first needed, so a test can give its own.
    """

    def __init__(
        self,
        printer: Printer,
        ffi: tuple[FfiMain, FfiLib] | None = None,
        calc_move_time: CalcMoveTime | None = None,
    ) -> None:
        self._printer = printer
        self._ffi = ffi
        self._calc = calc_move_time
        self._borrowed: dict[str, _Borrowed] = {}
        # the print time at which the last parked move of each extruder ends
        self._last_end: dict[str, float] = {}

    # --- the move ----------------------------------------------------------------------------

    def move(
        self,
        ext: MovingExtruder,
        distance_mm: float,
        speed_mm_s: float,
        accel: float = DEFAULT_ACCEL,
        note: Note | None = None,
    ) -> tuple[float, float]:
        """Queues one move of the extruder ext and returns its (start, end) in print time.

        Nothing waits on the toolhead. The steps are made now and the MCU runs them from start.
        The move is refused (ParkedMoveError) for the active extruder, a head below
        min_extrude_temp, a distance of 0 or over MAX_DISTANCE_MM, and a speed of 0 or over
        MAX_SPEED_MM_S. It is refused too while the extruder's own moves still have steps to be
        made before start: steps made after the parked move's would be out of order. When note is
        given, the move is recorded through it for the feed-forward; a record that fails is only
        logged. Steps that fail to be made are an internal error of Klipper: it shuts down.
        """
        stepper = self._check(ext, distance_mm, speed_mm_s)
        name = ext.get_name()
        toolhead = self._toolhead()
        start = self._start(toolhead, name)
        self._own_steps_done(stepper, name, start)
        self._enable(stepper, start)
        axis_r, accel_t, cruise_t, cruise_v = self._calc_move_time()(distance_mm, speed_mm_s, accel)
        end = start + accel_t + cruise_t + accel_t
        self._run(stepper, self._pair(name), (start, accel_t, cruise_t, axis_r, cruise_v, accel))
        toolhead.note_mcu_movequeue_activity(end)
        self._last_end[name] = end
        if note is not None:
            self._note(note, name, start, end - start, distance_mm)
        return start, end

    def _check(self, ext: MovingExtruder, distance_mm: float, speed_mm_s: float) -> MotionStepper:
        """Returns the stepper of ext, or raises ParkedMoveError when the move is refused."""
        name = ext.get_name()
        if not (math.isfinite(distance_mm) and 0.0 < abs(distance_mm) <= MAX_DISTANCE_MM):
            raise ParkedMoveError(
                f"{name}: a parked move of {distance_mm} mm (0 < |distance| <= {MAX_DISTANCE_MM})"
            )
        if not (math.isfinite(speed_mm_s) and 0.0 < speed_mm_s <= MAX_SPEED_MM_S):
            raise ParkedMoveError(
                f"{name}: a parked move at {speed_mm_s} mm/s (0 < speed <= {MAX_SPEED_MM_S})"
            )
        if self._toolhead().get_extruder().get_name() == name:
            raise ParkedMoveError(f"{name} is the active extruder")
        if not ext.get_heater().can_extrude:
            raise ParkedMoveError(f"{name} is below min_extrude_temp")
        if ext.extruder_stepper is None:
            raise ParkedMoveError(f"{name} has no extruder stepper")
        return ext.extruder_stepper.stepper

    def _start(self, toolhead: MotionToolHead, name: str) -> float:
        """Returns the print time at which the next parked move of the extruder name starts.

        It is after what the print has queued, after the steps already made, a little after the
        MCU's now, and after the last parked move (or pause) of that extruder. When that last end
        is the latest, the move starts at it, so queued moves follow each other with no stop.
        Otherwise it starts SCHEDULE_GAP_S after the latest.
        """
        now = toolhead.mcu.estimated_print_time(self._printer.get_reactor().monotonic())
        latest = max(toolhead.print_time, toolhead.step_gen_time, now + SCHEDULE_DELAY_S)
        last = self._last_end.get(name, 0.0)
        return last if last >= latest else latest + SCHEDULE_GAP_S

    def _own_steps_done(self, stepper: MotionStepper, name: str, start: float) -> None:
        """Raises ParkedMoveError while the stepper's own kinematics has steps to make before start.

        That is the case for the moves of the extruder that the toolhead has queued but not yet
        made into steps. FORCE_MOVE flushes them first; a parked move must not wait for that, so
        it is refused, and the caller tries again later.
        """
        if self._own_active(stepper, start):
            raise OwnStepsError(f"{name} has steps of its own still to make")

    def _own_active(self, stepper: MotionStepper, start: float) -> bool:
        own = stepper.get_stepper_kinematics()
        return own is not None and bool(self._lib().itersolve_check_active(own, start))

    def own_steps_left(self, ext: MovingExtruder) -> bool:
        """Tells whether a parked move of ext queued now would be refused with OwnStepsError.

        Nothing is queued. An extruder with no stepper gives False: its move is refused for that.
        """
        if ext.extruder_stepper is None:
            return False
        start = self._start(self._toolhead(), ext.get_name())
        return self._own_active(ext.extruder_stepper.stepper, start)

    def _enable(self, stepper: MotionStepper, start: float) -> None:
        """Turns on the motor of the stepper just before start, when it is off."""
        enables: StepperEnable = self._printer.lookup_object("stepper_enable")
        line = enables.lookup_enable(stepper.get_name())
        if not line.is_motor_enabled():
            line.motor_enable(start - ENABLE_LEAD_S)

    def _run(
        self,
        stepper: MotionStepper,
        pair: _Borrowed,
        trapezoid: tuple[float, float, float, float, float, float],
    ) -> None:
        """Lends the stepper the pair, adds the move, makes its steps and gives the stepper back.

        The stepper gets back its own queue, its own kinematics and its commanded position, even
        when making the steps fails. So the extruder's own position is the same as before the
        move. The private queue is emptied in every case.

        What a reader of the extruder's past position sees (the U1's tangle check reads
        find_past_position at the MCU's now, filament_entangle_detect.py:162-165). In the U1's
        tree (klippy/): find_past_position is
        stepper.mcu_to_commanded_position(stepper.get_past_mcu_position(t))
        (kinematics/extruder.py:309-311), that is the MCU step count at t times the step
        distance, minus _mcu_position_offset (stepper.py:166-172). The step count at t comes
        from the step history, which counts a step only once its clock is past t
        (chelper/stepcompress.c:591-616). Giving the kinematics back keeps the MCU step count,
        which the move's steps have already advanced (stepper.py:181-191), and set_position then
        puts the commanded position back while keeping that count, so the offset moves by the
        move's distance (stepper.py:148-153, 163-165). So, as soon as the move is queued, the
        past position at the MCU's now reads the position before the move minus its distance;
        it then walks back as the move's steps run, and reads the position before the move
        again once the move has ended.
        """
        start, accel_t, cruise_t, axis_r, cruise_v, accel = trapezoid
        end = start + accel_t + cruise_t + accel_t
        lib = self._lib()
        prev_pos = stepper.get_commanded_position()
        prev_sk = stepper.set_stepper_kinematics(pair.sk)
        try:
            prev_trapq = stepper.set_trapq(pair.trapq)
            try:
                stepper.set_position((0.0, 0.0, 0.0))
                lib.trapq_append(
                    pair.trapq,
                    start,
                    accel_t,
                    cruise_t,
                    accel_t,
                    0.0,
                    0.0,
                    0.0,
                    axis_r,
                    0.0,
                    0.0,
                    0.0,
                    cruise_v,
                    accel,
                    NO_LINE,
                )
                self._generate(stepper, end)
            finally:
                far = end + FINALIZE_AFTER_S
                lib.trapq_finalize_moves(pair.trapq, far, far)
                stepper.set_trapq(prev_trapq)
        finally:
            stepper.set_stepper_kinematics(prev_sk)
            stepper.set_position((prev_pos, 0.0, 0.0))

    def _generate(self, stepper: MotionStepper, end: float) -> None:
        """Makes the steps of the move. A failure there is fatal: Klipper shuts down, as it does
        for its own moves, and the error is raised again."""
        try:
            stepper.generate_steps(end)
        except Exception as err:
            shutdown = getattr(self._printer, "invoke_shutdown", None)
            if callable(shutdown):
                shutdown(f"ace2k_u1: the parked move of {stepper.get_name()}: {err}")
            raise

    @staticmethod
    def _note(note: Note, name: str, start: float, duration: float, distance: float) -> None:
        """Records the move for the feed-forward. A record that fails is logged, never raised."""
        try:
            if note(name, start, duration, distance) is False:
                logging.warning("ace2k_u1: the parked move of %s was not recorded", name)
        except Exception:
            logging.exception("ace2k_u1: recording the parked move of %s", name)

    def dwell(self, ext: MovingExtruder, seconds: float) -> float:
        """Leaves a pause of seconds before the next parked move of ext, and returns its end.

        The pause starts at the end of the last parked move of ext, or at the MCU's now when it
        has none left. It is refused (ParkedMoveError) when it is negative, not finite, or longer
        than MAX_DWELL_S.
        """
        if not (math.isfinite(seconds) and 0.0 <= seconds <= MAX_DWELL_S):
            raise ParkedMoveError(f"a parked pause of {seconds} s (0 <= pause <= {MAX_DWELL_S})")
        name = ext.get_name()
        end = max(self._last_end.get(name, 0.0), self._mcu_now()) + seconds
        self._last_end[name] = end
        return end

    # --- the waits ---------------------------------------------------------------------------

    def remaining_s(self, ext: MovingExtruder) -> float:
        """Returns the seconds until the last parked move of ext ends on the MCU clock.

        It is 0 when that move has ended, or when ext has made none.
        """
        end = self._last_end.get(ext.get_name())
        if end is None:
            return 0.0
        return max(0.0, end - self._mcu_now())

    def queued(self) -> bool:
        """Tells whether a parked move (or pause) of any extruder has not ended on the MCU clock.

        It reads nothing when no extruder ever had one. An ended move is forgotten here.
        """
        if not self._last_end:
            return False
        now = self._mcu_now()
        for name, end in list(self._last_end.items()):
            if end <= now:
                del self._last_end[name]
        return bool(self._last_end)

    # --- the parts found when first needed ---------------------------------------------------

    def _toolhead(self) -> MotionToolHead:
        toolhead: MotionToolHead = self._printer.lookup_object("toolhead")
        return toolhead

    def _mcu_now(self, eventtime: float | None = None) -> float:
        """Returns the print time of the reactor's now (or of eventtime) on the main MCU."""
        if eventtime is None:
            eventtime = self._printer.get_reactor().monotonic()
        return self._toolhead().mcu.estimated_print_time(eventtime)

    def _ffi_pair(self) -> tuple[FfiMain, FfiLib]:
        if self._ffi is None:
            self._ffi = _klipper_ffi()
        return self._ffi

    def _lib(self) -> FfiLib:
        return self._ffi_pair()[1]

    def _calc_move_time(self) -> CalcMoveTime:
        if self._calc is None:
            self._calc = _klipper_calc_move_time()
        return self._calc

    def _pair(self, name: str) -> _Borrowed:
        """Returns the private queue and kinematics of the extruder name, made on first use.

        The kinematics is a cartesian one on its x axis, as FORCE_MOVE uses. Both are freed with
        the mover.
        """
        pair = self._borrowed.get(name)
        if pair is None:
            ffi_main, ffi_lib = self._ffi_pair()
            trapq = ffi_main.gc(ffi_lib.trapq_alloc(), ffi_lib.trapq_free)
            sk = ffi_main.gc(ffi_lib.cartesian_stepper_alloc(b"x"), ffi_lib.free)
            pair = self._borrowed[name] = _Borrowed(trapq, sk)
        return pair


# --- the tip -----------------------------------------------------------------------------------


def tip_moves(printer: Printer, head: int, soft: int) -> list[TipStep] | str:
    """Returns the tip of the head, as the steps that a parked move can make.

    The U1's CONTROL_RETRACT_ACTION is rendered with EXTRUDER=head and SOFT=soft, the way the U1
    renders a macro for a command (its variables, the template context, params and rawparams).
    It is not run. Each line must be a comment, empty, M83, a G1 with only E and F, or a G4 with
    only P. M83 must come before the first G1, so the E values are relative. When the macro is
    missing, does not render, has another line or has no move, the result is a text that says
    why, with the first line refused.
    """
    text = _render_tip(printer, head, soft)
    if not isinstance(text, list):
        return text
    steps: list[TipStep] = []
    relative = False
    for line in text:
        if line.upper() == "M83":
            relative = True
            continue
        step = _tip_step(line)
        if step is None:
            return f"the tip has a line the dock cannot run: {line}"
        if step.kind == "move" and not relative:
            return f"the tip moves before its M83: {line}"
        steps.append(step)
    if not any(s.kind == "move" for s in steps):
        return "the tip has no move"
    return steps


def _render_tip(printer: Printer, head: int, soft: int) -> list[str] | str:
    """Returns the rendered tip as its lines without comments or blanks, or a text on failure."""
    macro: GCodeMacro | None = printer.lookup_object(TIP_MACRO, None)
    if macro is None:
        return "the U1 has no CONTROL_RETRACT_ACTION macro"
    # what a command's parameters give: the values as text, so SOFT is a number, not True
    params = {"EXTRUDER": str(int(head)), "SOFT": str(int(soft))}
    context: dict[str, object] = dict(macro.variables)
    context.update(macro.template.create_template_context())
    context["params"] = params
    context["rawparams"] = " ".join(f"{k}={v}" for k, v in params.items())
    try:
        rendered = macro.template.render(context)
    except Exception as e:
        logging.exception("ace2k_u1: rendering CONTROL_RETRACT_ACTION for head %d", head)
        return f"CONTROL_RETRACT_ACTION did not render: {e}"
    lines = []
    for raw in rendered.splitlines():
        line = raw.split(";", 1)[0].split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _tip_step(line: str) -> TipStep | None:
    """Returns the step of one line of the tip (G1 E F, or G4 P), or None for any other line."""
    command, *words = line.upper().split()
    values: dict[str, float] = {}
    for word in words:
        letter, number = word[:1], word[1:]
        try:
            value = float(number)
        except ValueError:
            return None
        if letter in values or not math.isfinite(value):
            return None
        values[letter] = value
    if command == "G1" and set(values) == {"E", "F"}:
        if values["E"] == 0.0 or values["F"] <= 0.0:
            return None
        return TipStep("move", values["E"], values["F"])
    if command == "G4" and set(values) == {"P"} and values["P"] >= 0.0:
        return TipStep("dwell", values["P"], 0.0)
    return None
