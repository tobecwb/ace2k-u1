"""Reads the tag of a spool with motion when the unit's own load left it unread.

The unit's own load pulls a spool put in to its parking point. It searches past it for the tag
only when every other lane of the unit is idle. With a lane following, the load stops at the
parking point and the tag stays pending. TagSearch then starts the unit's read with motion once
for that spool, as ``ACEK_RFID_READ LANE=n MOVE=1`` does: ace2k takes a follow at rest as quiet,
searches up to its ``rfid_search_mm`` or an obstacle, and comes back. On a shared head the
obstacle may be the other lane's filament past the splitter, and the search stops there. The
Adapter creates one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from .ace2k_klipper_types import GCode, Reactor
    from .ace2k_u1_common import Hooked, LaneKey
    from .ace2k_u1_eject import Ejector
    from .ace2k_u1_printer_types import LaneView

try:
    from .ace2k_u1_common import TAG_UNSETTLED, error_line
    from .ace2k_u1_push import search_reach_mm
    from .ace2k_u1_tags import TagWriter
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        TAG_UNSETTLED,
        error_line,
    )
    from ace2k_u1_push import search_reach_mm  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_tags import TagWriter  # type: ignore[import-not-found, no-redef]

# How long after the end of a load its tag state is looked at. The unit settles the state at its
# next look after the load's end, so the load's event can come before it.
SEARCH_SETTLE_S = 2.0
# A refusal of the unit that may pass (another lane moves, or this one is busy) is tried once
# more after this.
SEARCH_RETRY_S = 5.0
RETRY_REASONS = ("other_lane_moving", "busy")
# A look that finds a reason that may pass (a print, a move, a read on command) looks again every
# SEARCH_RECHECK_S, up to SEARCH_RECHECK_MAX_S after the first look. Then the read is given up.
SEARCH_RECHECK_S = 3.0
SEARCH_RECHECK_MAX_S = 60.0
SEARCH_POLL_S = 0.5  # how often a started read is looked at
# The wait of a read when ace2k cannot tell its own: the search's reach out and back, plus the
# pauses of the read sessions, at the lowest lane speed, plus a margin (as ace2k_rfid computes it).
# The reach is the load's search reach (ace2k_u1_push.search_reach_mm).
SESSION_ALLOWANCE_MM = 60.0
LANE_SPEED_FLOOR_MM_S = 9.0
SEARCH_MARGIN_S = 15.0
# The tag states that the read with motion is for: not read yet, or a search that never settled.
UNREAD_STATES = ("pending", "searching")
# The outcome of a settled search, in plain words, for the states that are not a read.
OUTCOMES = {"no_tag": "no tag", "pending": "tag not read", "unknown": "the bay emptied"}


class SearchAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as the read with motion uses it."""

    @property
    def reactor(self) -> Reactor: ...
    @property
    def gcode(self) -> GCode | None: ...
    @property
    def ejector(self) -> Ejector: ...
    @property
    def swapping(self) -> set[int]: ...
    @property
    def swap_pending(self) -> Callable[[], bool]: ...
    def say(self, msg: str) -> None: ...
    def lane_text(self, h: Hooked) -> str: ...
    def lane_args(self, h: Hooked) -> str: ...
    def lane_or_none(self, h: Hooked) -> LaneView | None: ...
    def in_print(self) -> bool: ...
    def motion(self, h: Hooked, lane: LaneView | None = None) -> str | None: ...
    def set_by_hand(self, h: Hooked) -> bool: ...
    @property
    def hooked(self) -> list[Hooked]: ...


@dataclass
class _Search:
    """One lane's read with motion: when to look next, and where it stands.

    ``first`` is the time of the first look. ``started`` is False until the read is accepted.
    ``retried`` is True once a refusal that may pass was tried again. ``ended`` is True once the
    load of the read ended.
    """

    h: Hooked
    due: float
    first: float | None = None
    started: bool = False
    retried: bool = False
    ended: bool = False
    deadline: float = 0.0
    wait: float = 0.0


def search_wait_s(h: Hooked) -> float:
    """Returns how long a read with motion of h may take, in seconds.

    It is ace2k's own wait for ``ACEK_RFID_READ MOVE=1`` when its tag reader tells it (the reach
    of the search and the load speed). Otherwise it is the same formula with the default reach and
    the lowest lane speed.
    """
    rfid = getattr(h.unit, "rfid", None)
    try:
        wait = rfid._move_timeout(rfid._search_mm())  # type: ignore[union-attr]
        if isinstance(wait, (int, float)) and wait > 0:
            return float(wait)
    except Exception:
        logging.debug("ace2k_u1: ace2k's wait of a read with motion unknown", exc_info=True)
    travel = 2 * search_reach_mm(h.api) + SESSION_ALLOWANCE_MM
    return travel / LANE_SPEED_FLOOR_MM_S + SEARCH_MARGIN_S


class TagSearch:
    """Starts the read with motion of a spool whose tag the unit's own load did not read.

    ``done`` holds the lanes whose spool had its chance: one read with motion per spool. A lane
    leaves it when its bay is seen empty, a spool goes in, or its tag is forgotten or changes.
    ``searches`` maps a lane key to its _Search. ``seen`` holds the insert, tag state and uid of
    each lane at the last tick.
    """

    def __init__(self, adapter: SearchAdapter) -> None:
        self.adapter = adapter
        self.timer: object = None
        self.done: set[LaneKey] = set()
        self.searches: dict[LaneKey, _Search] = {}
        self.seen: dict[LaneKey, tuple[object, object, object]] = {}
        self.running = False

    def watch_lane(self, h: Hooked, lane: LaneView) -> None:
        """Takes the view of the lane in a tick of the bay watch.

        A new spool gets its own read: the mark of the last one is dropped when the bay reads
        empty, on an insert edge, and when the tag is forgotten (back to pending) or its uid
        changes. While a read of this module runs, its own forget changes nothing.
        """
        tag = lane.get("tag") or {}
        now = (lane.get("insert"), tag.get("state"), tag.get("uid"))
        last = self.seen.get(h.key)
        self.seen[h.key] = now
        if now[0] is False:
            self.done.discard(h.key)
        if last is None or h.key in self.searches:
            return
        forgotten = last[1] in ("read", "no_tag") and now[1] == "pending"
        new_uid = now[2] is not None and now[2] != last[2]
        if (now[0] is True and last[0] is not True) or forgotten or new_uid:
            self.done.discard(h.key)

    def in_flight(self, h: Hooked) -> bool:
        """True while a read with motion that this module started on h has not ended."""
        search = self.searches.get(h.key)
        return search is not None and search.started and not search.ended

    def on_load_end(self, h: Hooked, kind: str | None, at_park: bool, search_end: bool) -> None:
        """Handle the final event of the unit's own load of h (seq 0, mode loading).

        ``at_park`` is True when that load stops at the parking point by itself (no grip and no
        park of a refill). ``search_end`` is True when ace2k says the load was a read with motion.
        A load that ended "loaded" at the park, and was not such a read, is looked at
        SEARCH_SETTLE_S later. The end of the load of a started read gives its outcome then.
        """
        now = self.adapter.reactor.monotonic()
        search = self.searches.get(h.key)
        if search is not None and search.started:
            search.ended, search.due = True, now + SEARCH_SETTLE_S
        elif (
            search is None
            and kind == "loaded"
            and at_park
            and not search_end
            and h.key not in self.done
        ):
            self.searches[h.key] = _Search(h, now + SEARCH_SETTLE_S)
        else:
            return
        self._wake(now)

    def _wake(self, when: float) -> None:
        if self.timer is None:
            self.timer = self.adapter.reactor.register_timer(self._run, self.adapter.reactor.NEVER)
        self.adapter.reactor.update_timer(self.timer, when)

    def _run(self, _eventtime: float) -> float:
        """Runs the timer: starts the due reads and looks at the started ones.

        run_script may wait for the G-code lock, and the reactor may run this timer again
        meanwhile. The call that runs does the work, and the call that was entered again leaves.
        """
        if self.running:
            return self.adapter.reactor.NEVER
        self.running = True
        try:
            for key, search in list(self.searches.items()):
                if search.due > self.adapter.reactor.monotonic():
                    continue
                try:
                    self._step(search)
                except Exception:  # never out of the timer
                    self.searches.pop(key, None)
                    logging.exception(
                        "ace2k_u1: lane %s tag search", self.adapter.lane_text(search.h)
                    )
        finally:
            self.running = False
        if not self.searches:
            return self.adapter.reactor.NEVER
        return min(s.due for s in self.searches.values())

    def _step(self, search: _Search) -> None:
        if search.started:
            self._watch(search)
        else:
            self._try_start(search)

    def _moot(self, h: Hooked, lane: LaneView) -> str | None:
        """Returns why no read with motion is needed any more, or None.

        The bay emptied, the spool was set by hand (its tag is never taken), or the tag is read,
        has no tag, or was never reported.
        """
        if lane.get("insert") is not True:
            return "its bay emptied"
        if self.adapter.set_by_hand(h):
            return "its spool was set by hand"
        state = (lane.get("tag") or {}).get("state")
        if state not in UNREAD_STATES and state not in TAG_UNSETTLED:
            return f"its tag is {state or 'unknown'}"
        return None

    def _wait_reason(self, h: Hooked, lane: LaneView) -> str | None:
        """Returns why the read with motion waits now, or None.

        These may pass: a print on, a session reading the tag, and a real move of the lane or of
        its head (a swap, an eject, a push). The tag reader's watch of a gripped spool reads
        nothing, so it does not hold the search back. The lane's own follow is no move here: the
        unit takes a follow at rest as quiet.
        """
        if self.adapter.in_print():
            return "a print is on"
        if (lane.get("tag") or {}).get("state") == "reading":
            return "a read of its tag runs"
        if (
            self.adapter.motion(h, lane) not in (None, "following")
            or h.head in self.adapter.swapping
            or self.adapter.swap_pending()
            or self.adapter.ejector.eject_running(h)
        ):
            return "a move is pending"
        if self._other_lane_moves(h):
            return "another lane of its unit moves"  # the unit would refuse the read
        return None

    def _other_lane_moves(self, h: Hooked) -> bool:
        """True when another lane of h's unit moves, as the unit counts it for the read.

        Every lane of the unit counts, whether it feeds a head of the U1 or not, from one status
        read of the unit. A lane moves when it is not idle or in error. A lane in its follow with
        its motor at rest (duty 0) does not count: the read yields when that motor starts. A
        status that cannot be read counts as a move, so the read waits.
        """
        try:
            lanes = h.unit.get_status(self.adapter.reactor.monotonic())["lanes"]
        except Exception:
            logging.exception("ace2k_u1: lane %s status", self.adapter.lane_text(h))
            return True
        for i, lane in enumerate(lanes):
            mode = lane.get("mode")
            if i == h.lane or mode in (None, "idle", "error"):
                continue
            if mode != "following" or (lane.get("duty") or 0) > 0:
                return True
        return False

    def _try_start(self, search: _Search) -> None:
        """Starts the read with motion of a due lane.

        A moot read is dropped, with the reason in the log. A read that must wait looks again
        later, and is given up with a console line after SEARCH_RECHECK_MAX_S. A refusal that may
        pass is tried once more SEARCH_RETRY_S later. Any other refusal, and the second one, is a
        console line.
        """
        h = search.h
        n = self.adapter.lane_text(h)
        now = self.adapter.reactor.monotonic()
        if search.first is None:
            search.first = now
        lane = self.adapter.lane_or_none(h)
        moot = None if lane is None else self._moot(h, lane)
        if moot is not None:
            del self.searches[h.key]
            logging.info("ace2k_u1: lane %s tag read with motion not needed: %s", n, moot)
            return
        why = "its status cannot be read" if lane is None else self._wait_reason(h, lane)
        if why is not None:
            if now - search.first < SEARCH_RECHECK_MAX_S:
                search.due = now + SEARCH_RECHECK_S
                return
            self._give_up(h, why)
            return
        self._send(search, n)

    def _send(self, search: _Search, n: str) -> None:
        h = search.h
        self.done.add(h.key)
        if not search.retried:
            self.adapter.say(
                f"ace2k_u1: lane {n}: the tag was not read at the parking point — reading it"
                " with motion"
            )
        try:
            cast("GCode", self.adapter.gcode).run_script(
                f"ACEK_RFID_READ {self.adapter.lane_args(h)} MOVE=1 WAIT=0"
            )
        except Exception as err:
            line = error_line(err)
            if any(r in line for r in RETRY_REASONS) and not search.retried:
                search.retried = True
                search.due = self.adapter.reactor.monotonic() + SEARCH_RETRY_S
                return
            self._give_up(h, line)
            return
        now = self.adapter.reactor.monotonic()
        search.started, search.due = True, now + SEARCH_POLL_S
        search.wait = search_wait_s(h)
        search.deadline = now + search.wait

    def _give_up(self, h: Hooked, why: str) -> None:
        del self.searches[h.key]
        self.adapter.say(
            f"ace2k_u1: lane {self.adapter.lane_text(h)}: tag read with motion given up — {why}"
        )

    def _watch(self, search: _Search) -> None:
        """Looks at a started read. It gives its one outcome line, or looks again later.

        A tag read is the outcome at once. Otherwise the outcome is the settled tag state at the
        look after the read's load ended, or no outcome at the deadline.
        """
        h = search.h
        lane = self.adapter.lane_or_none(h) or {}
        tag = lane.get("tag") or {}
        state = tag.get("state")
        now = self.adapter.reactor.monotonic()
        if state == "read" and tag.get("record"):
            outcome = f"found — {TagWriter.tag_summary(tag).split(' ', 1)[-1]}"
        elif lane.get("insert") is False:
            outcome = "the bay emptied"
        elif now >= search.deadline:
            outcome = f"no outcome within {search.wait:.0f} s"
        elif search.ended and state not in TAG_UNSETTLED:
            outcome = OUTCOMES.get(str(state), f"tag {state or 'unknown'}")
        else:
            search.due = min(now + SEARCH_POLL_S, search.deadline)
            return
        del self.searches[h.key]
        n = self.adapter.lane_text(h)
        self.adapter.say(f"ace2k_u1: lane {n} tag read with motion: {outcome}")
