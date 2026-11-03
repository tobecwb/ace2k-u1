"""The Unload of the U1's screen, cold when the lane in the head is free.

The screen unloads a head in two stages of the U1's unload (FEED_AUTO ... UNLOAD=1): prepare
(home, pick the head, heat) and then doing (form the tip and pull it out), which it sends once it
reads unload_heat_finish. The batch of the screen first preheats the head (AUTO_FEEDING_PREHEAT).

A lane that the U1 has not loaded (its channel is not at load_finish) may only be preloaded: its
tip waits at the head's sensor, short of the gear. At the prepare stage such a lane is ejected as
ACEK_EJECT does it: with the head's sensor set, a short roll back judges the filament by the lane's
buffer plunger. Free (or the head empty), the unit takes the lane back to its bay, cold, and the
channel is moved to unload_heat_finish with no home, no pick and no heating. The doing stage that
follows ends the unload (unload_doing, then unload_finish), and the screen reads it as done while
the unit still runs its unload. Held by the head, the filament that the probe pulled is fed back,
and the U1's own unload goes on as always, with its heating. A loaded lane (load_finish) gets the
U1's own unload, unchanged.

Everything that ACEK_EJECT refuses (a print, a busy lane, an error, no filament in the bay, an
eject running) also gets the U1's own unload, and so does a head that a swap is on.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NoReturn, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from .ace2k_klipper_types import GCode, Reactor
    from .ace2k_u1_common import Hooked
    from .ace2k_u1_eject import Ejector
    from .ace2k_u1_heads import HeadWatch
    from .ace2k_u1_printer_types import FeedModule, FilamentFeed

try:
    from .ace2k_u1_common import PUSH_POLL_S, heater_off
    from .ace2k_u1_eject import EJECT_RETRY_S, EjectRefused
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        PUSH_POLL_S,
        heater_off,
    )
    from ace2k_u1_eject import (  # type: ignore[import-not-found, no-redef]
        EJECT_RETRY_S,
        EjectRefused,
    )

UNLOAD_CODE = 70  # the exception code of the U1's unload
# The longest wait for the eject's decision. Its probe and feed back each have a deadline of about
# 30 s, and a busy start is retried for 5 s, so this is a backstop that only a fault reaches.
DECIDE_WAIT_S = 120.0
# The longest wait for the start of the unit's unload once the eject decided "cold". Its timer
# gives up on a busy unit after EJECT_RETRY_S, so this is a backstop that only a fault reaches.
START_WAIT_S = EJECT_RETRY_S + 2.0
COLD_LINE = "ace2k_u1: lane {n}: the screen's unload is cold — the unit takes it back to its bay"


class ScreenAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the screen's unload uses it."""

    @property
    def reactor(self) -> Reactor: ...
    @property
    def module(self) -> FeedModule: ...
    @property
    def gcode(self) -> GCode | None: ...
    @property
    def swapping(self) -> set[int]: ...
    @property
    def ejector(self) -> Ejector: ...
    @property
    def head_watch(self) -> HeadWatch: ...
    def feed_error(self, name: str) -> str: ...
    def lane_text(self, h: Hooked) -> str: ...
    def say(self, msg: str) -> None: ...
    def channel_lock(
        self, ff: FilamentFeed, ch: int, wait_s: float | None
    ) -> AbstractContextManager[bool]: ...


class ScreenUnload:
    """The cold path of the screen's unload (see the module)."""

    def __init__(self, adapter: ScreenAdapter) -> None:
        self.adapter = adapter

    def prepare(self, h: Hooked) -> bool:
        """Run the prepare stage for lane h, cold when it is free.

        Return True when the lane was taken back cold (the channel reads unload_heat_finish).
        False: the U1's own prepare runs. A cold unload whose unit unload did not start, or an
        eject that never decided, fails the unload with its line (it raises, as the U1's own
        prepare raises its failures).
        """
        ff, ch, m = h.ff, h.ch, self.adapter.module
        n = self.adapter.lane_text(h)
        h.cold_unload = False  # an earlier cold prepare whose doing never came
        if ff.channel_state[ch] == m.FEED_STA_LOAD_FINISH or h.head in self.adapter.swapping:
            return False
        try:
            job = self.adapter.ejector.start_eject(h.lane, h.unit, for_screen=True)
        except EjectRefused as err:
            logging.info("ace2k_u1: lane %s: the screen's unload heats (%s)", n, err)
            return False
        heater_off(self.adapter.gcode, h.head)  # the screen's preheat: the cold path never heats
        # The eject runs from its own timer. It decides in time: every move it starts has a
        # deadline, and a busy start is retried for a bounded time. DECIDE_WAIT_S is a backstop.
        self.adapter.ejector.wait_eject(job, DECIDE_WAIT_S, say=False)
        if not job.decided:
            self.adapter.ejector.abandon(job, f"no decision after {DECIDE_WAIT_S:g} s")
            self._fail(
                h, f"ace2k_u1: lane {n}: the screen's unload failed: its eject did not decide"
            )
        if job.outcome != "cold":
            return False  # held (fed back), or the eject failed with its own line: the U1 heats
        # The unit's unload is started from a timer, which retries a busy refusal for a while.
        reactor = self.adapter.reactor
        give_up = reactor.monotonic() + START_WAIT_S
        while self.adapter.ejector.unload_starting(h) and reactor.monotonic() < give_up:
            reactor.pause(reactor.monotonic() + PUSH_POLL_S)
        if self.adapter.ejector.unload_starting(h):
            self.adapter.ejector.drop_eject(h)  # still trying at the backstop: no start comes now
        if not self.adapter.ejector.unload_started(h):
            self._fail(
                h,
                f"ace2k_u1: lane {n}: the screen's unload failed: the unit's unload did not start",
            )
        self.adapter.say(COLD_LINE.format(n=n))
        with self.adapter.channel_lock(ff, ch, None):
            ff.manual_feeding[ch] = False
            self._ok(ff, ch)
            h.cold_unload = True
            ff._set_channel_state(ch, m.FEED_STA_UNLOAD_PREPARE, True)
            ff._set_channel_state(ch, m.FEED_STA_UNLOAD_HEAT_FINISH)
        return True

    def doing(self, h: Hooked) -> bool:
        """Run the doing stage after a cold prepare: unload_doing, then unload_finish.

        The heater is turned off, as the U1's own doing ends. Return False when the prepare of
        lane h was not cold. The U1's own doing then runs, and it fails a channel that is not at
        unload_heat_finish, as it always does.
        """
        ff, ch, m = h.ff, h.ch, self.adapter.module
        if not h.cold_unload:
            return False
        if ff.channel_state[ch] != m.FEED_STA_UNLOAD_HEAT_FINISH:
            h.cold_unload = False
            return False
        with self.adapter.channel_lock(ff, ch, None):
            self._ok(ff, ch)
            ff._set_channel_state(ch, m.FEED_STA_UNLOAD_DOING)
            heater_off(self.adapter.gcode, h.head)
            ff._set_channel_state(ch, m.FEED_STA_UNLOAD_FINISH)
        return True

    def _ok(self, ff: FilamentFeed, ch: int) -> None:
        """Set the error fields of the channel as a U1 unload that goes well sets them."""
        m = self.adapter.module
        ff.exception_code[ch] = UNLOAD_CODE
        ff.channel_error[ch] = m.FEED_OK
        ff.channel_error_state[ch] = m.FEED_STA_NONE

    def _fail(self, h: Hooked, line: str) -> NoReturn:
        """Fail the unload as the U1's own prepare fails it, with line. It always raises."""
        self.adapter.say(line)
        self.adapter.head_watch.fail_channel(
            h.ff, h.ch, self.adapter.feed_error("FEED_ERR"), UNLOAD_CODE, "FEED_STA_UNLOAD_FAIL"
        )
        raise RuntimeError(line)  # FEED_AUTO of the U1 gives the screen the failure
