"""The tangle and runout pauses: the U1's own pauses for a head, run from the pause timer.

The adapter keeps the objects of the U1, the pause reason and the alert. PauseRunner queues the
pauses and runs them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import GCode, Reactor
    from .ace2k_u1_common import Hooked
    from .ace2k_u1_printer_types import ExceptionManager, PauseResume, PrintStats, PrintTaskConfig

# The tangle pause of the U1 (its filament_entangle_detect), for the head of the lane.
TANGLE_ID = 523
TANGLE_CODE = 38
TANGLE_MESSAGE = "detect filament tangled!"
TANGLE_LEVEL = 2
TANGLE_SCRIPT = "\nPAUSE\nM400\n"
# The printer event that it sends with the head.
TANGLE_EVENT = "filament_entangle_detect:tangled"
# The runout pause of the U1. It comes from the RunoutHelper of its filament_switch_sensor, which
# sits behind the filament_motion_sensor e<n>_filament of each head. The module is
# MODULE_ID_TOOLHEAD and the code is CODE_TOOLHEAD_FILAMENT_RUNOUT.
RUNOUT_ID = 523
RUNOUT_CODE = 0
RUNOUT_LEVEL = 2
# The name of the sensor of the head, which its runout message carries.
RUNOUT_SENSOR = "e%d_filament"
# The default pause_delay of the helper, used when the sensor does not say.
RUNOUT_PAUSE_DELAY_S = 0.5
# The pause prefix of the helper, its (empty) runout_gcode and M400.
RUNOUT_SCRIPT = "PAUSE IS_RUNOUT=1\n\nM400"
REPLENISH_SCRIPT = "\nM400\nINNER_AUTO_REPLENISH_FILAMENT EXTRUDER=%d\n"


class PauseAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the pauses use it.

    That is the objects of the U1 that the pauses go through (set at setup_pause), its console and
    the runout of the shared head.
    """

    print_stats: PrintStats | None
    pause_resume: PauseResume | None
    exception_manager: ExceptionManager | None
    send_event: Callable[..., object] | None  # send_event of the printer
    runout_source: str
    task_config: PrintTaskConfig | None

    @property
    def reactor(self) -> Reactor: ...
    @property
    def gcode(self) -> GCode | None: ...
    @property
    def on_shared_runout(self) -> Callable[[Hooked], object] | None: ...
    def lane_text(self, h: Hooked) -> str: ...
    def print_state(self) -> str | None: ...
    def say(self, msg: str) -> None: ...


@dataclass(frozen=True)
class PauseObjects:
    """The objects of the U1 that the pauses go through (see PauseRunner.setup_pause).

    These are print_stats (the state of the print), pause_resume, the exception manager, send_event
    of the printer, the runout source, and print_task_config (its auto replenish).
    """

    print_stats: PrintStats | None
    pause_resume: PauseResume | None
    exception_manager: ExceptionManager | None
    send_event: Callable[..., object] | None
    runout_source: str = "head"
    task_config: PrintTaskConfig | None = None


class PauseRunner:
    """The pauses of one adapter.

    It holds the queue that the pause timer drains, and it runs the tangle and runout pauses of
    the U1.
    """

    def __init__(self, adapter: PauseAdapter) -> None:
        self.adapter = adapter
        self.pause_timer: object = None
        # The (path, Hooked, kind) entries that the pause timer still has to run.
        self.pause_pending: list[tuple[str, Hooked, str]] = []
        # The pause timer is running. The G-code of a pause may yield.
        self.pause_running = False

    def setup_pause(self, objects: PauseObjects) -> None:
        """Hand the objects of the U1 that the pauses go through (PauseObjects) to the adapter."""
        self.adapter.print_stats = objects.print_stats
        self.adapter.pause_resume = objects.pause_resume
        self.adapter.exception_manager = objects.exception_manager
        self.adapter.send_event = objects.send_event
        self.adapter.runout_source = objects.runout_source
        self.adapter.task_config = objects.task_config
        if self.pause_timer is None:
            self.pause_timer = self.adapter.reactor.register_timer(
                self._run_pauses, self.adapter.reactor.NEVER
            )

    def queue_pause(self, path: str, h: Hooked, kind: str) -> None:
        self.pause_pending.append((path, h, kind))
        self.adapter.reactor.update_timer(self.pause_timer, self.adapter.reactor.NOW)

    def _run_pauses(self, _eventtime: float) -> float:
        """Run each queued pause in turn. This is the pause timer.

        A print that is no longer printing (an earlier pause paused it, or it ended) gets a console
        line instead. Nothing is raised. While the G-code of a pause waits, the reactor may run
        this timer again. The call that is running drains the queue, so the re-entered call leaves
        at once.
        """
        if self.pause_running:
            return self.adapter.reactor.NEVER
        self.pause_running = True
        try:
            self._drain_pauses()
        finally:
            self.pause_running = False
        return self.adapter.reactor.NEVER

    def _drain_pauses(self) -> None:
        while self.pause_pending:
            path, h, kind = self.pause_pending.pop(0)
            who = f"lane {self.adapter.lane_text(h)}"
            try:
                if path == "shared runout":
                    # it judges the state of the print itself
                    cast("Callable[[Hooked], object]", self.adapter.on_shared_runout)(h)
                elif self.adapter.print_state() != "printing":
                    self.adapter.say(
                        f"ace2k_u1: {who} {kind}: the print is {self.adapter.print_state()}, "
                        "not paused again"
                    )
                elif path == "tangle":
                    self._tangle_pause(h)
                else:
                    self.runout_pause(h)
            except Exception as err:
                logging.exception("ace2k_u1: %s pause", who)
                self.adapter.say(f"ace2k_u1: {who} {kind}: the pause failed — {err}")

    def _tangle_pause(self, h: Hooked) -> None:
        """Run the tangle pause of the U1 for the head, as its own tangle detection raises it."""
        if self.adapter.send_event is not None:
            self.adapter.send_event(
                "print_stats:update_exception_info",
                TANGLE_ID,
                h.head,
                TANGLE_CODE,
                TANGLE_MESSAGE,
                TANGLE_LEVEL,
            )
        if self.adapter.pause_resume is not None:
            self.adapter.pause_resume.send_pause_command()
        if self.adapter.send_event is not None:
            self.adapter.send_event(TANGLE_EVENT, h.head)
        if self.adapter.exception_manager is not None:
            self.adapter.exception_manager.raise_exception_async(
                id=TANGLE_ID,
                index=h.head,
                code=TANGLE_CODE,
                message=TANGLE_MESSAGE,
                oneshot=1,
                level=TANGLE_LEVEL,
            )
        cast("GCode", self.adapter.gcode).run_script(TANGLE_SCRIPT)

    def declines(self, h: Hooked) -> str | None:
        """Return why the runout pause of the U1 would not pause the print for the head of h.

        That is the head's sensor disabled, its pause on runout off, or the end of the print
        running. The text follows "lane n " on the console. None means it would pause.
        """
        cfg = self.adapter.task_config
        helper = getattr(h.ff.runout_sensor[h.ch], "runout_helper", None)
        if not getattr(helper, "sensor_enabled", True):
            return f"ran out; e{h.head}'s sensor is disabled: not paused"
        if not getattr(helper, "runout_pause", True):
            return f"ran out; e{h.head}'s pause on runout is off"
        if cfg is not None and getattr(cfg, "is_exec_print_end_action", False):
            return "ran out during the print's end; not paused"
        return None

    def runout_pause(self, h: Hooked) -> None:
        """Run the runout pause of the U1 for the head, as the runout of its head sensor raises it.

        It does these steps in order:
        - With the sensor disabled it does nothing more than a console line.
        - It backs up the filament info of the head.
        - With pause on runout off it prints a line and stops.
        - Then it sends the exception, the pause command, waits the pause delay, runs PAUSE
          IS_RUNOUT=1 and the auto replenish.
        - It raises the runout exception when no replenish took over the print.
        """
        n = self.adapter.lane_text(h)
        cfg = self.adapter.task_config
        helper = getattr(h.ff.runout_sensor[h.ch], "runout_helper", None)
        why = self.declines(h)
        sensor_off = not getattr(helper, "sensor_enabled", True)
        if why is not None and sensor_off:
            self.adapter.say(f"ace2k_u1: lane {n} {why}")
            return
        if cfg is not None and callable(getattr(cfg, "backup_filament_info", None)):
            cfg.backup_filament_info(h.head)
        if why is not None:
            self.adapter.say(f"ace2k_u1: lane {n} {why}")
            return
        name = getattr(helper, "name", None) or RUNOUT_SENSOR % h.head
        delay = getattr(helper, "pause_delay", RUNOUT_PAUSE_DELAY_S)
        message = f"{name} runout"
        if self.adapter.send_event is not None:
            self.adapter.send_event(
                "print_stats:update_exception_info",
                RUNOUT_ID,
                h.head,
                RUNOUT_CODE,
                message,
                RUNOUT_LEVEL,
            )
        if self.adapter.pause_resume is not None:
            self.adapter.pause_resume.send_pause_command()
        self.adapter.reactor.pause(self.adapter.reactor.monotonic() + delay)
        # As the U1 does: a failing PAUSE script is logged, then the replenish and the exception
        # follow.
        try:
            cast("GCode", self.adapter.gcode).run_script(RUNOUT_SCRIPT)
        except Exception:
            logging.exception("ace2k_u1: lane %s runout pause", self.adapter.lane_text(h))
        try:
            cast("GCode", self.adapter.gcode).run_script(REPLENISH_SCRIPT % h.head)
        except Exception:
            logging.exception("ace2k_u1: lane %s auto replenish", self.adapter.lane_text(h))
        replenished = cfg is not None and getattr(cfg, "perform_auto_replenish", False)
        if not replenished and self.adapter.exception_manager is not None:
            self.adapter.exception_manager.raise_exception_async(
                id=RUNOUT_ID,
                index=h.head,
                code=RUNOUT_CODE,
                message=message,
                oneshot=0,
                level=RUNOUT_LEVEL,
            )
