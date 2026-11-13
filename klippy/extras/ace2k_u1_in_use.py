"""Tells each unit which lanes are in use and which lanes the user named by hand.

The unit moves a lane on its own only to find out which lane a tag belongs to. It does that only
for lanes the host says are not in use, so a lane that feeds a head must never be left out.
in_use_masks decides which lanes those are. InUseSender passes the masks to the units when they
change. The unit's host module repeats the last mask every second, so a mask is sent here on a
change only.

tell_by_hand says that the user set a lane's material: the antenna never changes it. The unit's
host module sends it again at each of the unit's announcements that lacks it, so a mark given
while the strand is still going in reaches the unit. tell_saved_by_hand says it again, at each
start of Klipper, for every lane whose ACEK_SPOOL record is on file and whose bay does not read
empty: the unit's host module forgets the lanes it wants set by hand when Klipper restarts.

A head that several lanes feed and whose lane is not known (after a restart) counts every lane
with filament as in use (lane_in_use), except a lane the unit is loading from its bay: its tip is
in its own tube, and the load's back-and-forth proof of its tag may run. The unit attributes no
tag to a lane in use, so a tag on those lanes waits until the head's lane is known again: a load,
a swap or an unload of that head, its probe, or ACEK_LANE_ACTIVE. ACEK_SPOOL on a lane names its
material by hand, and then its tag no longer matters. The unit's own read pushes a lane up to
150 mm toward the head; doing that to a lane that may sit in the head is what this rule prevents.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_printer_types import LaneView


class InUseAdapter(Protocol):
    """What in_use_masks needs of the adapter."""

    @property
    def hooked(self) -> list[Hooked]: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    def lane_status(self, h: Hooked, eventtime: float | None = None) -> LaneView: ...
    @property
    def ejector(self) -> _Ejector: ...
    def set_by_hand(self, h: Hooked) -> bool: ...


class _Ejector(Protocol):
    def known_loaded(self, h: Hooked) -> bool: ...


def read_lane(adapter: InUseAdapter, h: Hooked) -> LaneView | None:
    """The unit's status of lane h, or None when it cannot be read.

    A lane that cannot be read is logged once when that starts, with the error, and once when it
    reads again, not on every tick.
    """
    try:
        lane = adapter.lane_status(h)
    except Exception:
        if not h.bay_unreadable:
            h.bay_unreadable = True
            logging.exception(
                "ace2k_u1: lane %d bay cannot be read for the in-use mask; it counts as full",
                h.lane + 1,
            )
        return None
    if h.bay_unreadable:
        h.bay_unreadable = False
        logging.info("ace2k_u1: lane %d bay reads again", h.lane + 1)
    return lane


def may_hold(lane: LaneView | None) -> bool:
    """True unless the bay reads empty. A bay not read yet, or unreadable (None), may be full."""
    return lane is None or lane.get("insert") is not False


def lane_in_use(adapter: InUseAdapter, h: Hooked, printing: bool) -> bool:
    """True when lane h feeds its head now, or may.

    That is the lane that is in a head while a print runs or is paused, or a lane that is known
    loaded in its head. On a head that several lanes feed and whose lane is not known (after a
    restart), every lane with filament may be in it, so each is in use until the head's lane is
    known. Once it is known, the other lanes of the head are not in use.
    """
    slot = adapter.slot_of(h)
    if slot is None:
        return False
    if slot.shared and slot.current is None:
        # a lane the unit is loading from its bay is in its own tube, not in the head
        lane = read_lane(adapter, h)
        return may_hold(lane) and (lane is None or lane.get("mode") != "loading")
    if slot.shared and slot.current is not h:
        return False
    if printing and slot.current is h:
        return True
    return bool(adapter.ejector.known_loaded(h))


def in_use_masks(adapter: InUseAdapter, printing: bool) -> dict[object, int]:
    """Returns the in-use mask of each unit that has a hooked lane (bit n is lane n, 0 to 3).

    printing says that a print runs or is paused.
    """
    masks: dict[object, int] = {}
    for h in adapter.hooked:
        masks.setdefault(h.unit, 0)
        if lane_in_use(adapter, h, printing):
            masks[h.unit] |= 1 << h.lane
    return masks


class InUseSender:
    """Sends the in-use masks to the units, each time one changes."""

    def __init__(self) -> None:
        self.sent: dict[object, int] = {}

    def send(self, adapter: InUseAdapter, printing: bool) -> None:
        """Sends the masks that differ from the last ones that were taken.

        A unit without the call (an older ace2k), or a call that fails, is logged and tried again
        in the next tick. Nothing is raised.
        """
        for unit, mask in in_use_masks(adapter, printing).items():
            if self.sent.get(unit) == mask:
                continue
            try:
                unit.rfid.set_in_use(mask)  # type: ignore[attr-defined]
            except AttributeError:
                self.sent[unit] = mask  # an older ace2k: nothing to send, nothing to retry
            except Exception:
                logging.exception("ace2k_u1: in-use mask %d", mask)
            else:
                self.sent[unit] = mask


class OwnWrites(Protocol):
    """An object that counts the adapter's own writes of a head's setting."""

    own_writes: int


@contextmanager
def own_write(adapter: OwnWrites) -> Iterator[None]:
    """Marks the writes of a head's setting inside the block as the adapter's own.

    The tag write, the spool step of a swap and the print start's spool step all write the setting
    on their own. None of them is the user naming a material, so none is told to the unit as set
    by hand (LaneSpools._by_hand looks at the count).
    """
    adapter.own_writes += 1
    try:
        yield
    finally:
        adapter.own_writes -= 1


def tell_by_hand(unit: object, lane: int) -> None:
    """Tells unit that the material of lane (0 to 3) was set by hand. Never raises."""
    try:
        unit.rfid.set_by_hand(lane)  # type: ignore[attr-defined]
    except AttributeError:
        pass  # an older ace2k has no attribution
    except Exception:
        logging.exception("ace2k_u1: lane %d set by hand", lane + 1)


def tell_saved_by_hand(adapter: InUseAdapter) -> None:
    """Tells each unit that its hooked lanes with an ACEK_SPOOL record on file were set by hand.

    This is the resend at a start of Klipper, whose host module forgot the lanes it wants set by
    hand. That module sends each one again at each announcement of the unit that lacks it, and
    stops when the unit's announcement shows the mark gone (its strand left). Every record on file
    was set by ACEK_SPOOL, and is dropped when the lane's bay empties. A lane whose bay reads empty
    now (its strand left while Klipper was down) is not told; one whose bay is not read yet is.
    """
    for h in adapter.hooked:
        if adapter.set_by_hand(h) and may_hold(read_lane(adapter, h)):
            tell_by_hand(h.unit, h.lane)
