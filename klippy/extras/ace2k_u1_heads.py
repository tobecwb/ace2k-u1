"""The heads that several lanes feed.

This module has two parts. The hand watch notices a filament that was put into such a head by
hand, which makes its lane unknown. The second part handles the U1's own unload or load for a
head with no lane known in it. A probe finds the lane first, or the flow is refused. The adapter
keeps the lanes and the heads, and HeadWatch runs these steps for it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import GCode
    from .ace2k_u1_common import Hooked, LaneKey
    from .ace2k_u1_eject import Ejector
    from .ace2k_u1_printer_types import Coordinator, FeedModule, FilamentFeed, LaneView
    from .ace2k_u1_probe import HeadProbe

try:
    from . import ace2k_u1_probe
    from .ace2k_u1_common import CHANNEL_WAIT_S, PAUSE_WAIT_S, Slot, error_line, heater_off
    from .ace2k_u1_grip import HEAD_CLEAR_S
    from .ace2k_u1_hooks import LOAD_CODE
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_probe  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        CHANNEL_WAIT_S,
        PAUSE_WAIT_S,
        Slot,
        error_line,
        heater_off,
    )
    from ace2k_u1_grip import HEAD_CLEAR_S  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_hooks import LOAD_CODE  # type: ignore[import-not-found, no-redef]

UNLOAD_CODE = 70  # the exception code of a failed unload
# The U1 flows that need the lane of their head to be known (see HeadWatch.flow_unknown). Each
# entry holds four things. The first is its exception code. The second is its fail state. The
# third is the advice that its refusal ends with (after "Find the head's lane"). The fourth is
# what the refusal for a hot head adds. The screen unloads several heads in a batch and preheats
# the next head while it unloads one, so a head can be hot in a batch.
UNKNOWN_FLOWS = {
    "unload": (
        UNLOAD_CODE,
        "FEED_STA_UNLOAD_FAIL",
        ", or eject the lane from the /ace2k page",
        ", alone (the screen preheats the next head of a batch)",
    ),
    "load": (LOAD_CODE, "FEED_STA_LOAD_FAIL", "", ""),
}
# A filament put in by hand is this case: the sensor of a shared head is set, after it read clear
# for HEAD_CLEAR_S, and neither the lane of the head nor a lane that this adapter runs (hold) has
# pushed toward it since. The lane of that filament is not known.
# These are the moves toward a head, which may set its sensor.
HAND_PUSH_MODES = ("feeding", "loading")
HAND_CAUSE = "a filament came into the head with no lane of it moving (by hand?)"
HAND_TAIL = "a print, an unload or an eject probes it first"
# What a forgotten head says about its lane, before the tail of the line.
LANE_UNKNOWN = "its lane is not known now"


class HeadsAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as far as the head watch uses it."""

    @property
    def module(self) -> FeedModule: ...
    @property
    def gcode(self) -> GCode | None: ...
    @property
    def probe(self) -> HeadProbe | None: ...
    @property
    def units(self) -> Coordinator: ...
    @property
    def extruders(self) -> dict[int, str] | None: ...  # head to the name of its extruder
    @property
    def ejector(self) -> Ejector: ...
    @property
    def swap_slot(self) -> Callable[[], Slot | None]: ...
    def say(self, msg: str) -> None: ...
    def feed_error(self, name: str) -> str: ...
    def u1_pause_reason(self, line: str) -> None: ...
    def in_print(self) -> bool: ...
    def print_state(self) -> str | None: ...
    def head_set(self, h: Hooked) -> bool: ...
    def head_holds(self, h: Hooked) -> bool: ...
    def set_current(self, slot: Slot, h: Hooked | None) -> None: ...
    def refusals(self) -> tuple[type[Exception], ...]: ...
    def channel_lock(
        self, ff: FilamentFeed, ch: int, wait_s: float | None
    ) -> AbstractContextManager[bool]: ...


@dataclass(frozen=True)
class FlowSite:
    """Where a U1 flow runs on a head with no lane known.

    It holds the filament_feed object (ff), the channel (ch), the head (slot) and the flow
    ("unload" or "load", see UNKNOWN_FLOWS).
    """

    ff: FilamentFeed
    ch: int
    slot: Slot
    flow: str


def _event_key(lane: LaneView | None) -> tuple[object, object]:
    """Return the last final event of a lane status, as the hand watch compares it.

    The result is the seq and the kind of the event. Both are None with no status, or with no event
    yet.
    """
    event = (lane or {}).get("last_event") or {}
    return (event.get("seq"), event.get("kind"))


class HeadWatch:
    """The hand watch and the flows on an unknown head, for the heads of one adapter."""

    def __init__(self, adapter: HeadsAdapter) -> None:
        self.adapter = adapter

    def watch_hand(self, slot: Slot, views: dict[LaneKey, LaneView], eventtime: float) -> None:
        """Notice a filament that was put into a head of several lanes by hand.

        This applies to a head with a lane known in it. Its sensor must be set after it read clear
        for HEAD_CLEAR_S. Since the clear began, no push toward the head (HAND_PUSH_MODES) must have
        been seen, neither of the lane of the head nor of a lane that this adapter runs (hold: a
        probe, a swap). Then the filament was put in by hand, or by a push of another lane typed
        at the console. Which lane it is is not known, so the lane of the head is forgotten, as
        after a restart (see forget_head).

        A roll back or an unload moves away from the head, and a follow is no push. Neither
        explains a filament that sets the sensor.

        This works outside a print only. In a print, the sensor of the head reads its own piece,
        and a pause is the job of the U1. It runs before the lanes' own watch, so that
        tick acts on the head as it is now.

        A sensor that cannot be read decides nothing. Neither does a lane whose status was not
        read while the head was clear.
        """
        if slot.current is None:
            slot.clear_at = None
            return
        holds = self.adapter.head_holds(slot.current)
        if slot.current.head_unread:
            slot.clear_at = None  # the read failed, so it counts as set, which is no rise
            return
        if not holds:
            self._watch_clear(slot, views, eventtime)
            return
        if slot.clear_at is None:
            return
        self._note_pushes(slot, views)  # the tick of the rise: a short push may end within it
        clear_for = eventtime - slot.clear_at
        slot.clear_at = None
        if self._put_in_by_hand(slot, clear_for):
            self.forget_head(slot, HAND_CAUSE, HAND_TAIL)

    def _watch_clear(self, slot: Slot, views: dict[LaneKey, LaneView], eventtime: float) -> None:
        """Handle a tick of the hand watch while the head sensor of slot is clear.

        It notes the start of the clear once, with the last events of the lanes then. It also notes
        the pushes since then (see _note_pushes).
        """
        slot.loaded = None  # the head emptied: what comes into it next is not known
        if slot.clear_at is None:
            slot.clear_at = eventtime
            slot.explained = slot.unsure = False
            slot.events = {h.key: _event_key(views.get(h.key)) for h in slot.lanes}
        self._note_pushes(slot, views)

    def _put_in_by_hand(self, slot: Slot, clear_for: float) -> bool:
        """True when the rise of the head sensor of slot, after it was clear for clear_for s, is a
        filament put in by hand.

        That is the case when no push explains it, every lane was read, the head was clear long
        enough and no print runs.
        """
        return not (
            slot.explained or slot.unsure or clear_for < HEAD_CLEAR_S or self.adapter.in_print()
        )

    def _note_pushes(self, slot: Slot, views: dict[LaneKey, LaneView]) -> None:
        """Note a push toward the head that explains what sets the sensor next.

        The push is one of the lane of the head, or of a lane that this adapter runs. It counts
        when it began since the clear: it runs now, or it ended since. A push has ended when its
        final event is of a push mode and is newer than the start of the clear. A lane whose
        status is not read leaves the head unsure.
        """
        for h in slot.lanes:
            lane = views.get(h.key)
            if lane is None:
                slot.unsure = True
                continue
            if not (h is slot.current or h.hold):
                continue
            pushing = lane.get("mode") in HAND_PUSH_MODES
            event = lane.get("last_event") or {}
            ended = _event_key(lane) != slot.events.get(h.key)
            if pushing or (ended and event.get("mode") in HAND_PUSH_MODES):
                slot.explained = True

    def flow_unknown(
        self, ff: FilamentFeed, ch: int, slot: Slot, flow: str, stage: str | None = None
    ) -> None:
        """Handle the U1 unload or load (flow, see UNKNOWN_FLOWS) on a head with no lane known.

        The flow comes from the screen. It applies to a head of several lanes, before the heating.
        An unload is handled at its prepare stage (or as a whole unload, with stage None). A load
        is handled at its start.

        If the sensor of the head is set, the preheat of the screen is turned off (the flow heats
        again itself), and the head is below probe_max_temp, then:
        - The probe finds the lane (HeadProbe.which_lane) under the channel lock of the U1.
        - The head is handed to that lane (hand_head).
        - The flow goes on with the follow of the lane. An unload ends in its eject, as
          ACEK_EJECT does.

        Otherwise the flow is refused (see _refuse_flow). The tip forming of the U1, or the
        extrusion of its load, would pull against a lane that no one drives, and an unload would
        leave the filament past the splitter. The reasons to refuse are:
        - The head is hot. A probe would wait minutes for it to cool, and the printer would be
          blocked. The batch of the screen preheats the next head while it works on one, so a
          head later in a batch comes hot.
        - The sensor or the heater cannot be read.
        - A probe or a hand-over fails.
        - Any other error under the probe. It is raised as it came.
        - A swap is pending on this head, or an eject runs on its lanes.

        These cases go on untouched:
        - A head whose sensor is empty holds nothing. A load is then refused by its own push
          (put_into_drive).
        - The cancel of an unload stops what runs.
        - The later stage of an unload (doing) is for the U1 to judge. With no prepare before it,
          the U1 ignores it.

        While a print is printing, nothing is decided, because a print starts with every head it
        uses known. In a paused print the adapter leaves the heat alone, and a hot head is refused
        with the way out of RESUME. The probe may turn its own heater off (HeadProbe.wait_cold, on
        a head below the limit with a target). The batch of the screen puts that back, because it
        restores the temperature of every head at its end in a paused print.
        """
        if flow == "unload" and stage not in (None, self.adapter.module.FEED_UNLOAD_STAGE_PREPARE):
            return
        state = self.adapter.print_state()
        if state == "printing":
            logging.info("ace2k_u1: head %d %s in a print with no lane known", slot.head + 1, flow)
            return
        paused = state == "paused"
        site = FlowSite(ff, ch, slot, flow)
        refusal: tuple[str, bool] | None  # (reason, heat_off), or None when the probe goes on
        try:
            full = self.adapter.head_set(slot.lanes[0])
        except Exception as err:
            refusal = (f"its sensor cannot be read — {err}", not paused)
        else:
            if not full:
                return
            refusal = self._flow_refusal(slot, flow, paused)
        if refusal is not None:
            self._refuse_flow(site, *refusal)  # always raises
        self._probe_flow(site)

    def _probe_flow(self, site: FlowSite) -> None:
        """Run the probe of flow_unknown under the channel lock of the U1.

        The head is handed to the lane that was found, or the flow is refused (see _refuse_flow).
        An internal error fails the channel and goes on.
        """
        slot, flow = site.slot, site.flow
        probe = cast("HeadProbe", self.adapter.probe)  # it is there, or the flow was refused
        self.adapter.say(
            f"ace2k_u1: head {slot.head + 1}: no lane known in it: probing before the {flow}"
        )
        # The U1's own lock. No flow of the other channel of the module runs beside the
        # probe. If the lock is still held after CHANNEL_WAIT_S, the probe and its flow are given
        # up.
        with self.adapter.channel_lock(site.ff, site.ch, CHANNEL_WAIT_S) as held:
            if not held:
                self._fail_flow(
                    site,
                    f"ace2k_u1: head {slot.head + 1}: the U1 kept its channel busy for "
                    f"{CHANNEL_WAIT_S:g} s; the {flow} given up",
                )
            try:
                self.hand_head(slot, probe.which_lane(slot))
            except ace2k_u1_probe.ProbeError as err:
                self._refuse_flow(site, str(err))
            except self.adapter.refusals() as err:
                self._refuse_flow(site, error_line(err))
            except Exception:
                # An internal error. FEED_AUTO of the U1 catches every exception, so the screen is
                # given the failure of its flow, and the error goes on as it came.
                logging.exception("ace2k_u1: head %d probe before the %s", slot.head + 1, flow)
                code, fail_state, _advice, _batch = UNKNOWN_FLOWS[flow]
                self.fail_channel(
                    site.ff, site.ch, self.adapter.feed_error("FEED_ERR"), code, fail_state
                )
                raise

    def _flow_refusal(self, slot: Slot, flow: str, paused: bool) -> tuple[str, bool] | None:
        """Return why the flow on an unknown head is refused before any probe, or None.

        The reason comes as (reason, heat_off). None means that the probe may go on. The sensor of
        the head reads set. The preheat of the screen is turned off here, except in a paused
        print, before the temperature is read.
        """
        if not paused:
            self._preheat_off(slot)  # before the temperature read. The flow heats again
        return self._busy_flow_refusal(slot) or self._hot_flow_refusal(slot, flow, paused)

    def _busy_flow_refusal(self, slot: Slot) -> tuple[str, bool] | None:
        """The first half of _flow_refusal: no probe, a swap pending, or an eject running."""
        if self.adapter.probe is None:
            return "no probe for its head", False
        if self.adapter.swap_slot() is slot:
            return "a swap is pending on it", False
        if any(self.adapter.ejector.eject_running(x) for x in slot.lanes):
            # An eject's own U1 unload hands the head over first, so it never comes
            # here.
            return "an eject is running on one of its lanes", False
        return None

    def _hot_flow_refusal(self, slot: Slot, flow: str, paused: bool) -> tuple[str, bool] | None:
        """The second half of _flow_refusal: the temperature is unread, or the head is hot."""
        probe = cast("HeadProbe", self.adapter.probe)  # it is there (_busy_flow_refusal came first)
        try:
            hot = probe.too_hot(slot)
        except ace2k_u1_probe.ProbeError as err:
            return str(err), False
        if hot and paused:
            return "the print is paused with the head hot: RESUME probes it", False
        if hot:
            batch = UNKNOWN_FLOWS[flow][3]
            return (
                f"the head is hot: let it cool below {probe.cfg.probe_max_temp:g} °C and {flow} "
                f"it again{batch}",
                False,
            )
        return None

    def hand_head(self, slot: Slot, found: Hooked | None) -> None:
        """Hand the head to the lane that a probe found, if that is not its lane already.

        The coordinator hands the head over, then the lane of the head is set. A refusal of the
        core is raised as it came, like any other error.
        """
        if found is not None and found is not slot.current:
            self.adapter.units.hand_over(found.unit, found.lane, PAUSE_WAIT_S)
            self.adapter.set_current(slot, found)

    def _preheat_off(self, slot: Slot) -> None:
        """Turn off the heater target of the head (the preheat of the screen). It never raises."""
        heater_off(self.adapter.gcode, slot.head)  # in FEED_AUTO of the U1: its G-code runs

    def fail_channel(
        self, ff: FilamentFeed, ch: int, error: str, code: int, fail_state: str | None = None
    ) -> None:
        """Fail the channel of the U1 the way its own flows fail it.

        The coded error of its caller then shows on the screen. This sets the error and the
        exception code of the channel. With fail_state (the name of a module constant), it also
        does what the prepare of the unload and the load of the U1 do. It stores the state that the
        channel failed in, and then sets that state. (The failure of the preload keeps its own
        order, the one of the preload of the U1.)
        """
        ff.channel_error[ch] = error
        ff.exception_code[ch] = code
        if fail_state is not None:
            ff.channel_error_state[ch] = ff.channel_state[ch]
            try:
                ff._set_channel_state(ch, getattr(self.adapter.module, fail_state))
            except Exception:
                logging.exception("ace2k_u1: channel %d %s", ch, fail_state)

    def _refuse_flow(self, site: FlowSite, why: str, heat_off: bool = False) -> NoReturn:
        """Refuse the unload or load of the U1 (flow) the way its own flow fails. It always raises.

        The channel fails as in fail_channel (general error, the code and the fail state of the
        flow, see UNKNOWN_FLOWS). So FEED_AUTO of the U1 raises its coded failure to the screen,
        and the reason goes to the console. heat_off says that the preheat is turned off here,
        which a refusal before flow_unknown did not do.
        """
        advice = UNKNOWN_FLOWS[site.flow][2]
        line = (
            f"ace2k_u1: head {site.slot.head + 1}: {site.flow} refused — no lane known in it: "
            f"{why}. Find the head's lane (ACEK_HEAD_PROBE, the head cold){advice}"
        )
        self._fail_flow(site, line, heat_off)

    def _fail_flow(self, site: FlowSite, line: str, heat_off: bool = False) -> NoReturn:
        """Fail the U1 flow with a console line. It always raises.

        The line is said, and in a paused print it becomes the pause reason. The channel fails as
        the U1's own flow fails it (see fail_channel: general error, the code and the fail
        state of the flow). With heat_off, the preheat is turned off.
        """
        code, fail_state, _advice, _batch = UNKNOWN_FLOWS[site.flow]
        self.adapter.say(line)
        self.adapter.u1_pause_reason(line)  # in a paused print, as the push refusal of the load
        self.fail_channel(site.ff, site.ch, self.adapter.feed_error("FEED_ERR"), code, fail_state)
        if heat_off:
            self._preheat_off(site.slot)
        raise RuntimeError(line)

    def forget_head(self, slot: Slot, cause: str, tail: str, state: str = LANE_UNKNOWN) -> None:
        """Forget the lane of the head, for ``cause``. It never raises.

        A filament put in by hand is one cause (HAND_CAUSE), a load refused for its material on a
        shared head another (MaterialGate.back_out). The line said once the lane is forgotten is
        "ace2k_u1: head N: <cause>: <state> — <tail>".

        First the choice of the coordinator for the extruder of the head is undone (Units.release,
        host interface 13). Then the lane of the head is forgotten (set_current None). The next
        print, unload or eject on the head probes it, or refuses, as after a restart.

        If the release is refused (a lane of the group is being armed, or a follower would not
        end), nothing is forgotten, because the two would disagree. A line says so. An older ace2k
        with no release keeps its choice, and stop_unchosen stops a follow of that lane, which is
        not in the head. The release waits only while a follower of the head runs, which is what
        must stop.
        """
        head = slot.head + 1
        name = self.adapter.extruders.get(slot.head) if self.adapter.extruders else None
        release = getattr(self.adapter.units, "release", None)
        active = getattr(self.adapter.units, "active", None) or {}
        if name is not None and release is not None and active.get(name) is not None:
            try:
                release(name, PAUSE_WAIT_S)
            except Exception as err:
                logging.exception("ace2k_u1: head %d choice not released", head)
                self.adapter.say(
                    f"ace2k_u1: head {head}: {cause}, but its active lane could not be released"
                    f" — {err}; check the head, then ACEK_LANE_ACTIVE"
                )
                return
        self.adapter.say(f"ace2k_u1: head {head}: {cause}: {state} — {tail}")
        self.adapter.set_current(slot, None)
