"""Rolls the old lane back for a lane swap, and rolls back the rest of a refill.

The length is rolled back in moves of at most the longest move of the unit. Each move is waited
for, within a limit. The module also decides what the final event of a move means. The swap
(ace2k_u1_swap) decides the length and runs the roll back.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import FeedEvent, Reactor
    from .ace2k_u1_adapter import Adapter
    from .ace2k_u1_common import Hooked
    from .ace2k_u1_config import SwapConfig
    from .ace2k_u1_swap import SwapState

try:
    from .ace2k_u1_common import (
        PUSH_POLL_S,
        STOPPED_KINDS,
        StoppedError,
        move_max_mm,
        unit_move_wait_s,
    )
    from .ace2k_u1_hooks import NUDGE_MARGIN_S
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        PUSH_POLL_S,
        STOPPED_KINDS,
        StoppedError,
        move_max_mm,
        unit_move_wait_s,
    )
    from ace2k_u1_hooks import NUDGE_MARGIN_S  # type: ignore[import-not-found, no-redef]

# The values of ace2k for a tip that snags (its snag_* keys). They are used for a feed that does not
# have them in its feed.snag. The roll back of a swap with a free tip waits for each catch that the
# unit may forgive (_rollback_wait_s).
SNAG_FWD_MM = 20.0
SNAG_BACK_MM = 5.0
SNAG_REST_MS = 500.0
SNAG_FREE_MM = 50.0
# The floor speed of ace2k's lanes and its landing, the last part of a move that runs at that
# floor. They are used for a feed that does not give its own (feed.speed_min, feed.landing_mm).
UNIT_FLOOR_MM_S = 9.0
UNIT_LANDING_MM = 10.0


def _touch_s(api: object, back: float, speed: float) -> float:
    """Returns how long the unit takes for a touch of back mm asked at speed.

    The touch is a move of the unit, so its landing runs at the floor speed of the unit: the last
    landing_mm of it, or the whole of a touch that is no longer. The default touch, 5 mm, runs
    whole at the floor."""
    floor = float(getattr(api, "speed_min", None) or UNIT_FLOOR_MM_S)
    landing = float(getattr(api, "landing_mm", None) or UNIT_LANDING_MM)
    if back > landing:
        return (back - landing) / speed + landing / floor
    return back / floor


def _rollback_wait_s(h: Hooked, length: float, speed: float, free: bool) -> float:
    """Returns how long to wait for the roll back of a swap on the lane of h.

    The roll back is length mm at speed. The wait is the wait of the move itself
    (unit_move_wait_s): twice its travel time plus NUDGE_MARGIN_S, or the budget of ace2k for the
    move when that is longer. It is only a backstop, because the unit ends a move after its own
    deadline. The budget of ace2k counts the tail that the unit runs once the filament leaves
    the motor, because the roll back of a swap or of the rest of a refill can take it out.

    With a free tip, the wait also counts each catch that the unit may forgive on the way, one for
    each snag_free_mm of the length. Each catch adds the time to move snag_fwd_mm at the roll back
    speed, the time of the touch of snag_back_mm (_touch_s: at the floor speed of the unit, for
    the landing of the touch), plus snag_rest_ms of rest. These are the values that the unit
    uses (feed.snag of ace2k).
    """
    wait = unit_move_wait_s(h, length, speed, NUDGE_MARGIN_S, reverse=True)
    if not free:
        return wait
    snag: Mapping[str, float] = getattr(h.api, "snag", None) or {}
    fwd = snag.get("snag_fwd_mm", SNAG_FWD_MM)
    back = snag.get("snag_back_mm", SNAG_BACK_MM)
    rest_s = snag.get("snag_rest_ms", SNAG_REST_MS) / 1000.0
    catches = math.floor(length / snag.get("snag_free_mm", SNAG_FREE_MM))
    return wait + catches * (fwd / speed + _touch_s(h.api, back, speed) + rest_s)


class RollbackHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the roll back reads it.

    It gives the adapter (built at the hook, before any swap), the reactor and the swap config.
    """

    @property
    def adapter(self) -> Adapter | None: ...
    @property
    def reactor(self) -> Reactor: ...
    @property
    def swap_cfg(self) -> SwapConfig: ...


class Rollback:
    """Rolls lanes back for a swap or a refill.

    host is the ACE2kU1. Its adapter is built at the hook. Its reactor and its swap config are
    read when used.
    """

    def __init__(self, host: RollbackHost) -> None:
        self.host = host

    def _adapter(self) -> Adapter:
        """Returns the adapter of the host. It is built at the hook, before any swap runs."""
        return cast("Adapter", self.host.adapter)

    def roll_back(self, state: SwapState, length_mm: float, refill: bool) -> None:
        """Rolls back the old lane of state at swap_speed.

        The length is length_mm less what the free check already rolled back. A filament whose tip
        is at the head ends just behind the splitter. A filament from further back rolls further,
        at worst out of its bay, and that counts as done.

        refill is set for the rest of a refill. It rolls back the whole way out of its bay,
        whether it is done or ran out.
        """
        old = cast("Hooked", state.old)  # the unload step refused an old lane that is not known
        rest_mm = length_mm - state.freed_mm
        # The roll back runs in moves of at most the longest move of the unit. The rest of a refill
        # is longer than one move. It ends at once when it runs out of its bay.
        cap_mm = move_max_mm(old)
        while rest_mm >= 1.0:
            # The tail of the rest of a refill can stop at the sensor of the bay, past the drive
            # gear. The move then ends as unload_incomplete. The tail is out of reach of the unit,
            # so the operator takes it out.
            kind = self.run(old, min(rest_mm, cap_mm), tail_out=refill)
            if kind == "unload_incomplete":
                state.tail_at_bay_s = self.host.reactor.monotonic()  # refill_ready waits for this
            if kind in ("runout", "unload_incomplete"):
                break
            rest_mm -= min(rest_mm, cap_mm)

    def run(self, h: Hooked, length: float, tail_out: bool = False) -> str | None:
        """Rolls the lane back length mm at swap_speed and waits for its final event.

        The wait has a limit. Returns the kind of the event, which is done or runout. With
        tail_out set (the rest of a refill), it can also return unload_incomplete. That means the
        tail is past the drive gear at the sensor of the bay, and the error of the lane is
        cleared. Any other kind is raised as an error.

        The follow of the lane is stopped first and kept off.

        On a unit with has_free_rollback (ace2k host interface 14), the roll back starts with a
        free tip. The free check found the tip out of the extruder, or no unload ran with the
        head empty. So every catch on the way back is forgiven, as long as the unit sees the
        filament come back snag_free past the last catch. Not only the first catch is forgiven,
        because a thick tip may catch twice. Whatever the sensor said, the unit still ends a tip
        that is held from the start as stuck, because it never comes back snag_free. The wait
        counts each catch that the unit may forgive (_rollback_wait_s).
        """
        adapter = self._adapter()
        reactor = self.host.reactor
        speed = self.host.swap_cfg.swap_speed_for(h.api)
        free = bool(getattr(h.api, "has_free_rollback", False))
        h.follow_wanted = False  # the lane leaves the head, so its follow is not armed again
        held = h.hold  # it is given back as it was, because the hold of another step outlives this
        h.hold = True  # the follow is not armed again while the roll back runs
        try:
            adapter.stop(h, "the swap")
            move = adapter.retry_busy_start(
                h, lambda: h.api.start_move(h.lane, "rollback", length, speed, free_tip=free)
            )
            deadline = reactor.monotonic() + _rollback_wait_s(h, length, speed, free)
            while move.result() is None:
                if reactor.monotonic() >= deadline:
                    adapter.stop(h, "the swap's roll back timeout")
                    raise RuntimeError(f"{adapter.lane_text(h)} roll back timed out")
                reactor.pause(reactor.monotonic() + PUSH_POLL_S)
        finally:
            h.hold = held
        # The wait above has seen the final event arrive.
        kind: str | None = cast("FeedEvent", move.result()).get("kind")
        if kind == "runout":
            # The filament left its bay, so it is certainly behind the splitter. The swap goes on.
            adapter.say(
                f"ace2k_u1: head {h.head + 1}: {adapter.lane_text(h)} rolled out of its bay during "
                "the roll back"
            )
        elif kind == "unload_incomplete" and tail_out:
            # The unit leaves the lane in error. The error is cleared, and the operator takes out
            # the tail.
            try:
                h.api.clear(h.lane)
            except Exception:
                logging.exception("ace2k_u1: %s cleared after its tail", adapter.lane_text(h))
            adapter.say(
                f"ace2k_u1: head {h.head + 1}: {adapter.lane_text(h)}'s tail is past the unit's "
                "drive gear, at its bay: take it out by hand"
            )
        elif kind in STOPPED_KINDS:
            raise StoppedError(f"{adapter.lane_text(h)} ended {kind}")
        elif kind != "done":
            raise RuntimeError(f"{adapter.lane_text(h)} ended {kind}")
        return kind
