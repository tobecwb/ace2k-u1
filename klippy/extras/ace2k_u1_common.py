"""The records and the arithmetic that the adapter and the head probe share.

The adapter is ``ace2k_u1.py`` and the head probe is ``ace2k_u1_probe.py``. This module holds the
hooked lanes and the heads they feed, the wait for a move, the distance that a final event reports
and the taut watch of the plunger of a lane. It imports no other module of the adapter at run
time (imports for the type checker aside), so each of them can import it.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple, Union, cast

if TYPE_CHECKING:
    from typing import TypeGuard  # Python 3.10: for the type checker only

    from .ace2k_klipper_types import GCode, Reactor
    from .ace2k_u1_hooks import EncoderWheel
    from .ace2k_u1_printer_types import (
        ArmedStart,
        Coordinator,
        FeedMove,
        FilamentFeed,
        LaneView,
        Unit,
        UnitFeed,
    )
    from .ace2k_u1_probe import HeadProbe

# A lane's key in the adapter's per-lane tables (see unit_key).
LaneKey = Union[int, tuple[str, int]]
# Head index to that head's extruder position now (mm, as the U1 reads it). None when the position
# is not known.
ExtruderPosition = Callable[[int], Union[float, None]]

# The wait for a move is at least twice its travel time plus this margin (see unit_move_wait_s).
MOVE_MARGIN_S = 5.0
# The versions of the host interface of ace2k (its feed's API_VERSION) that this adapter reads.
# docs/protocol.md of ace2k lists what each version added ("Host interface").
API_MIN = 12  # the least that the adapter hooks
SPEED_API = 13  # feed.set_speed: the speed of a running move can change
MOVE_WAIT_API = 17  # feed.move_wait_s: ace2k's own time budget for a move (see unit_move_wait_s)
HOLD_WHY_API = 21  # a hold carries its reason, which ace2k's ACEK_STATUS shows
# A move longer than the longest move of the unit is split into several moves. If one of them ends
# "done" but carried the filament less than this share of its length, the filament slipped, and no
# further move follows.
CHUNK_CARRIED = 0.9
# A push that gains less than this over the ease before it is a hard stop, and the probe stops
# pushing. A budget with less than this left runs no further move.
INSIST_MIN_MM = 5.0
# A final event whose filament went at least this far forward took a waiting filament from the bay
# into the gear (see Hooked.seated). The limit sits between the two measured cases: a load blocked
# against an old end at the gear stops about 10 mm in, and a load that takes the filament pulls
# about 300 mm.
SEATED_MM = 20.0
# Timings and lane modes that the adapter and the eject share.
# How long (s) a stopped follower of a shared head may take to end. This is the same wait as in
# ace2k.
PAUSE_WAIT_S = 5.0
# The longest wait (s) for the channel lock of the U1. Another flow of this module's channel may
# hold the lock. A preload, or the probe before a U1 flow, gives up after this wait. The wait only
# guards against a lock that is never released. It is longer than any normal flow of the U1, a
# flow that heats its head included.
CHANNEL_WAIT_S = 300.0
PUSH_POLL_S = 0.05
# The period (s) at which a refused "busy" request is retried (eject, follow, retry of a feed). It
# does not depend on the rate of the feed report.
BUSY_RETRY_POLL_S = 0.25
# How long (s) the feed of the retry, and the re-arm of the follow, are retried while the unit is
# busy.
NUDGE_BUSY_S = 5.0
# A lane held by the follow, or by a one-way assist that a manual ACEK_ASSIST may have left on it.
ARMED_MODES = ("following", "assisting", "assisting_back")
# The lane modes in which a stop ends the motion, and so brings a final event.
IDLE_MODES = (None, "idle", "error")
# Two sets for the rule of Slot.loaded (what is in a head since its last load). They are not
# inverse, and they differ on "error" on purpose. MOVE_MODES are the modes of a final event whose
# move may have changed what is in the head: the follow keeps the filament where it is, and an
# event in "error" clears nothing. STILL_MODES are the modes of a lane, read now, that let the
# tool change trust Slot.loaded with no probe: a lane in "error" is not trusted, because where its
# failed move left the filament is not known.
MOVE_MODES = frozenset(
    {"feeding", "rolling_back", "unloading", "loading", "assisting", "assisting_back"}
)
STILL_MODES = (None, "idle", "following")
# The tag states of a search or a read on command that has not settled yet.
TAG_UNSETTLED = ("searching", "reading")
# The taut watch: this many different reports in a row that read the plunger as pushed mean that
# the plunger is taut (see TautWatch).
TAUT_READINGS = 2
# The stops on the plunger that the unit can make itself, by the names that the probe and the eject
# use (see unit_stops). "taut" is the unit's "held": the plunger is taut and the filament stands
# still. "rest" is the unit's "at_rest": the plunger is at rest. A move armed for one of them ends
# with that kind as its final event.
UNIT_STOPS = {"taut": "held", "rest": "at_rest"}
# The held window of a move armed for "held" (see held_window_ms). Against a held tip the motor
# turns on for the whole window while the filament stands still, so the window is set from the
# move's speed: about HELD_PULL_MM of motor at any speed. A shorter window would let friction end
# the move held, so the window is never so short that the friction floor (held_restart_mm over the
# window) passes HELD_FLOOR_SHARE of the move's speed. A thick tip dragged through the tube still
# comes at more than that share of the motor's speed, and so it runs on.
HELD_PULL_MM = 6.0
HELD_FLOOR_SHARE = 0.75
# One encoder count at the widest scale ace2k accepts, in mm (its protocol.md). ace2k rounds
# held_mm up to whole counts of this size for the window's restart (held_restart_mm).
WIDEST_COUNT_MM = 1.481


def heater_off(gcode: GCode | None, head: int) -> None:
    """Turn off the heater target of a head from a running G-code (FEED_AUTO of the U1). It
    never raises: a failure is logged."""
    try:
        if gcode is not None:
            gcode.run_script_from_command(f"M104 S0 T{head} A0")
    except Exception:
        logging.exception("ace2k_u1: head %d heater off", head + 1)


def finite(value: object) -> TypeGuard[float]:
    """True when value is a real number: not a bool, not NaN and not infinite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def forward_mm(event: Mapping[str, object] | None) -> float:
    """Return how far the filament of a final event went toward the printer (mm).

    The encoder's travel is signed, positive toward the printer. A travel back, or none, is 0.
    """
    value = (event or {}).get("filament_mm")
    return max(0.0, value) if finite(value) else 0.0


def error_line(err: BaseException) -> str:
    """Return the exception as a line: its message, or its class name when it has no message."""
    return str(err) or type(err).__name__


def unit_prefix(units: Coordinator, unit: Unit) -> str:
    """Return the text that opens a problem line about a unit.

    This is "<section>: " when there are several units, and nothing otherwise.
    """
    return f"{units.label(unit)}: " if units.several() else ""


def move_wait_s(length_mm: float, speed_mm_s: float, margin_s: float = MOVE_MARGIN_S) -> float:
    """Return how long (s) to wait for a move of length_mm at speed_mm_s.

    The wait is twice the travel time plus margin_s.
    """
    return length_mm / speed_mm_s * 2.0 + margin_s


def unit_move_wait_s(
    h: Hooked,
    length_mm: float,
    speed_mm_s: float,
    margin_s: float = MOVE_MARGIN_S,
    *,
    reverse: bool = False,
) -> float:
    """Return how long (s) to wait for a move of length_mm at speed_mm_s on the lane of h.

    The wait starts from move_wait_s: twice the travel time plus margin_s. A unit whose host
    interface has MOVE_WAIT_API also gives the time budget of ace2k for the move (feed.move_wait_s,
    with the landing at the floor speed of the unit). The waited G-codes of ace2k use the same
    budget. The wait is the longer of the two, so it is never shorter than before. The firmware of
    the unit ends a move that passes its own deadline with a final event. So this wait is only a
    backstop for an event that never comes (a lost link). It is not the limit of the move.

    Set reverse for a roll back. The filament may leave the motor, and the unit then runs a tail
    after the move. The budget of ace2k counts that tail, as its own ACEK_ROLLBACK does. A forward
    move leaves reverse unset.

    An older unit, or a move for which ace2k gives no estimate, waits move_wait_s alone. That
    formula is the same with or without reverse.
    """
    plain = move_wait_s(length_mm, speed_mm_s, margin_s)
    api = h.api
    if api_at_least(api, MOVE_WAIT_API):
        try:
            return max(plain, api.move_wait_s(h.lane, length_mm, speed_mm_s, reverse=reverse))
        except (ValueError, RuntimeError):
            pass  # no estimate (ace2k refuses the arguments, or has no feed): use the plain formula
    return plain


_MARKS = itertools.count(1)


def setting_written(h: Hooked) -> None:
    """Note that the head's setting was just written for the spool in h's bay (setting_mark)."""
    h.setting_mark = next(_MARKS)


def own_spool_came(h: Hooked) -> None:
    """Note that a tag read or an Edit spool record just came for h (own_mark)."""
    h.own_mark = next(_MARKS)


def watch_tag_seen(h: Hooked, lane: Mapping[str, Any]) -> None:
    """Note a new tag read of h in its status (own_spool_came).

    Only a read of another tag (its uid) or with another record counts: the same tag read again
    is as old as the first read, so it does not outrank a setting written since. The first read
    after Klipper starts, with the bay full since, only records it: it is the spool that was
    there, as old as the head's setting. After the bay was seen empty (spool_left), any read is
    the new spool's and counts.
    """
    tag = lane.get("tag") or {}
    if tag.get("state") != "read":
        return
    record = tag.get("record") or {}
    seen = (tag.get("uid"), record.get("material"), record.get("color_rgba"))
    before, h.tag_seen = h.tag_seen, seen
    if before is not None and seen != before:
        own_spool_came(h)


def spool_left(h: Hooked) -> None:
    """Note that the spool that h's head setting belonged to has left: its bay reads empty and
    the lane is out of its head, or a new spool went into the bay.

    The next look with the bay full only records the setting (note_setting_seen): a setting left
    from before, or set before the new spool went in, is not that spool's. Only a write or a
    change after it went in is. A tag read from then on is the new spool's (watch_tag_seen).
    """
    h.setting_mark = None
    h.setting_seen = None
    h.tag_seen = ()


def setting_type(kind: object) -> str:
    """The type of a head's setting in upper case, or "" when it is unset (empty or NONE)."""
    upper = str(kind or "").strip().upper()
    return "" if upper == "NONE" else upper


def _real(setting: tuple[str, object] | None) -> str:
    """The type of a setting in upper case, or "" when it is unset (empty or NONE)."""
    return "" if setting is None else setting_type(setting[0])


def note_setting_seen(h: Hooked, setting: tuple[str, object]) -> None:
    """Note the setting of the head that h feeds alone, as one tick reads it on the U1.

    Nothing is noted while the bay is not known full. The first look with the bay full (after
    Klipper starts, or after the spool left: spool_left) only records it: Klipper starts with the
    setting owned (Hooked.setting_mark), and a new spool does not own the one it found. After
    that, a set setting whose type or colour changed was written for the spool in the bay, on the
    U1's screen or by a command (setting_written). A command that writes the colour alone is
    taken in where it runs, so it is not seen as a change (LaneSpools.setting_set), and a write of
    the same value is caught there too.
    """
    if not h.bay_full:
        return
    before, h.setting_seen = h.setting_seen, setting
    if before is None or setting == before:
        return
    if _real(setting):
        setting_written(h)


def tag_state(lane: Mapping[str, Any]) -> str | None:
    """Return the state of a lane's tag in its status ("read", "searching", ...), or None."""
    return cast("str | None", (lane.get("tag") or {}).get("state"))


def tag_read_live(lane: Mapping[str, Any]) -> bool:
    """True when the unit has read the lane's tag since it started.

    A read that ace2k restored after a restart (source "restored") shows as read too, but the unit
    has not seen the tag yet: a push still reads it on the way, at its slow speed.
    """
    tag = lane.get("tag") or {}
    return tag.get("state") == "read" and tag.get("source") != "restored"


class FreshWait(NamedTuple):
    """The bounds of a wait for a fresh report of the unit (see wait_fresh_lane), in seconds."""

    min_s: float  # the report comes at least this long after the wait starts
    max_s: float  # the longest wait
    poll_s: float  # how often the status is read


def wait_fresh_lane(
    reactor: Reactor,
    read: Callable[[], tuple[LaneView, int | None] | None],
    start: int | None,
    wait: FreshWait,
) -> LaneView | None:
    """Wait for a report of the unit newer than start and wait.min_s from now; return its lane.

    read() gives the lane and the unit's report count (None when the status carries no count), or
    None to give up. A start of None takes the first read once wait.min_s has passed. It polls
    every wait.poll_s and returns None when no such report comes within wait.max_s. What read
    raises goes to the caller.
    """
    min_s, max_s, poll_s = wait
    t0 = reactor.monotonic()
    while True:
        reactor.pause(reactor.monotonic() + poll_s)
        now = reactor.monotonic()
        got = read()
        if got is None:
            return None
        lane, reports = got
        fresh = start is None or (reports is not None and reports > start)
        if fresh and now - t0 >= min_s:
            return lane
        if now - t0 >= max_s:
            return None


def moved_mm(result: Mapping[str, object] | None) -> float:
    """Return the filament travel (mm) of a final event.

    If the event has no filament travel, the travel of its motor is used. The result is 0 when
    neither is a finite number.
    """
    for key in ("filament_mm", "motor_mm"):
        value = (result or {}).get(key)
        if finite(value):
            return abs(value)
    return 0.0


def carried(step: float, moved: float) -> bool:
    """True when a move of step mm that reported moved mm carried the filament.

    That means the distance is at least CHUNK_CARRIED of the step. No distance counts as not
    carried: a push goes on only after a move that is known to have carried the filament. A roll
    back may safely go too far, so it takes a missing distance as the whole step (see back in the
    probe).
    """
    return moved > 0.0 and moved >= step * CHUNK_CARRIED


def unit_stops(h: Hooked) -> bool:
    """True when the unit of h can end a move itself on the plunger of the lane.

    That is an ace2k with has_stop_when (host interface 20) and an image with the stop. The move is
    then started with stop_when, and the host reads no status while it runs. Otherwise the host
    reads the plunger in the status reports and stops the move itself (see TautWatch).
    """
    return getattr(h.api, "has_stop_when", False) is True


def held_window_ms(h: Hooked, speed_mm_s: float) -> int | None:
    """Return the held window in ms for a move of h at ``speed_mm_s`` armed for "held".

    It is the longer of two: HELD_PULL_MM of motor at that speed, and the least window whose
    friction floor stays at HELD_FLOOR_SHARE of the speed. It is then kept within the unit's
    bounds. With the defaults (held_mm 3 mm) that is 600 ms at 10 mm/s, 200 ms at 30 and 120 ms at
    50. None when the unit publishes no window limits, or for a speed that is not above 0: the
    unit then uses its own held_ms. The bound can make the floor pass the share: the connect
    refuses such a held_mm (held_floor_problem).
    """
    return window_ms_of(h.api, speed_mm_s)


def window_ms_of(api: object, speed_mm_s: float) -> int | None:
    """held_window_ms for a unit's feed object ``api`` (ace2k's feed)."""
    restart_mm = getattr(api, "held_restart_mm", None)
    low = getattr(api, "held_ms_min", None)
    high = getattr(api, "held_ms_max", None)
    if not finite(restart_mm) or not finite(low) or not finite(high) or not speed_mm_s > 0:
        return None
    pull_ms = HELD_PULL_MM * 1000.0 / speed_mm_s
    floor_ms = restart_mm * 1000.0 / (HELD_FLOOR_SHARE * speed_mm_s)
    # the small tolerance keeps a float a hair over a whole number from rounding up a millisecond
    window = math.ceil(max(pull_ms, floor_ms) - 1e-6)
    return int(min(high, max(low, window)))


def held_floor_problem(api: object, speed_mm_s: float) -> str | None:
    """Return why the unit's held_mm is too large for a move at ``speed_mm_s``, or None.

    ``api`` is the unit's feed object, and ``speed_mm_s`` the slowest speed of an armed move. A
    large held_mm needs a long window to keep the friction floor at HELD_FLOOR_SHARE of the speed.
    When that window passes the unit's longest, it is cut there, and friction could end a slow
    move held (past 95 %, ace2k refuses the move). The line names the largest held_mm that keeps
    the share: whole counts of WIDEST_COUNT_MM, about 13.3 mm at the unit's least speed of 9 mm/s.
    None for a unit without the stop on the plunger.
    """
    if getattr(api, "has_stop_when", False) is not True:
        return None
    window = window_ms_of(api, speed_mm_s)
    restart_mm = getattr(api, "held_restart_mm", None)
    if window is None or not finite(restart_mm):
        return None
    floor = restart_mm * 1000.0 / window
    if floor <= HELD_FLOOR_SHARE * speed_mm_s + 1e-9:
        return None
    longest_mm = HELD_FLOOR_SHARE * speed_mm_s * window / 1000.0
    limit = math.floor(longest_mm / WIDEST_COUNT_MM + 1e-9) * WIDEST_COUNT_MM
    return (
        f"held_mm: at {speed_mm_s:g} mm/s its held window ({window} ms) lets friction end a move"
        f" held below {floor:.1f} mm/s, more than {HELD_FLOOR_SHARE * 100:g} % of the speed;"
        f" keep held_mm at {limit:.1f} mm or less"
    )


def start_armed(
    h: Hooked, mode: str, length_mm: float, speed_mm_s: float, stop_when: frozenset[str]
) -> FeedMove:
    """Start a move of h, with the stops on the plunger that the unit makes itself.

    ``stop_when`` holds names of UNIT_STOPS values ("held", "at_rest"). The front tip is never
    free here (free_tip False), as for every move that uses it. Empty, the start is sent as on a
    unit without the stop. The caller arms a stop only on a unit that has it (see unit_stops).
    With "held", the move carries its own held window for its speed (see held_window_ms).
    Raises what start_move raises.
    """
    if not stop_when:
        return h.api.start_move(h.lane, mode, length_mm, speed_mm_s, free_tip=False)
    armed: ArmedStart = cast("ArmedStart", h.api.start_move)
    window = held_window_ms(h, speed_mm_s) if "held" in stop_when else None
    if window is None:
        return armed(h.lane, mode, length_mm, speed_mm_s, stop_when=stop_when)
    return armed(h.lane, mode, length_mm, speed_mm_s, stop_when=stop_when, held_ms=window)


class TautWatch:
    """Decide when the plunger of a lane has stayed pushed (taut) long enough to stop a move.

    The host uses it only with a unit that cannot stop on its plunger itself (see unit_stops).

    Only a status report that is newer than the start of the move and later than ``fresh_at_s``
    counts. A report that is read twice counts once. ``readings`` different reports in a row that
    read pushed mean taut. A report that does not read pushed starts the count again.
    """

    def __init__(
        self, fresh_at_s: float, start_reports: int | None, readings: int = TAUT_READINGS
    ) -> None:
        self.fresh_at_s = fresh_at_s
        self.start_reports = start_reports
        self.readings = readings
        self.last_reports: int | None = None
        self.count = 0

    def fresh(self, now_s: float, reports: int) -> bool:
        """True when ``reports`` is a new report to judge. A stale report resets the count."""
        if self.start_reports is None:
            self.start_reports = reports
        if now_s < self.fresh_at_s or reports <= self.start_reports:
            self.count = 0
            return False
        if reports == self.last_reports:
            return False
        self.last_reports = reports
        return True

    def taut(self, pushed: bool) -> bool:
        """Count one fresh report. True once ``readings`` reports in a row have read pushed."""
        self.count = self.count + 1 if pushed else 0
        return self.count >= self.readings


def unit_key(unit: object, lane: int) -> LaneKey:
    """Return the key of a lane in the per-lane tables of this adapter.

    On the unnamed [ace2k] the key is the bare lane index, as the tables always had it. On a named
    unit the key is (name of the unit, lane index). The two forms never mix, because ace2k refuses
    an [ace2k] section beside an [ace2k <name>] section. ``unit`` is the unit, or its name as an
    event carries it (None means the unnamed unit).
    """
    name = unit if unit is None or isinstance(unit, str) else getattr(unit, "name", None)
    return lane if name is None else (name, lane)


@dataclass(eq=False)
class Hooked:
    """One hooked channel: a feed object, its channel and the head index.

    It also holds the unit and the lane (0-based) that feed the head. ``key`` is the lane in the
    per-lane tables of this adapter (see unit_key).
    """

    # The adapter never remembers where a filament is. Shared heads are probed instead.

    ff: FilamentFeed
    ch: int
    unit: Unit
    lane: int
    head: int = field(init=False)
    # The host interface of the unit. Every call for this head goes to it.
    api: UnitFeed = field(init=False)
    key: LaneKey = field(init=False)
    # What the U1 was last told about its inlet.
    present: bool = field(init=False, default=False)
    wheel: EncoderWheel | None = field(init=False, default=None)
    # Since when insert has been True without a break, until the U1 is told.
    rise_at: float | None = field(init=False, default=None)
    # The console was told that the load of this lane ended in error.
    error_said: bool = field(init=False, default=False)
    # This adapter armed the follow of the lane since then, or adopted the follow of ace2k.
    armed: bool = field(init=False, default=False)
    # An arm by this adapter is in flight. Another trigger is dropped.
    arming: bool = field(init=False, default=False)
    # The follow of ace2k stopped and this adapter did not adopt it. The lane waits behind.
    unadopted_seq: int | None = field(init=False, default=None)
    # This adapter is telling the U1 about the inlet (see _tell_port).
    telling: bool = field(init=False, default=False)
    # A reading of the U1's own inlet was dropped. This is logged once.
    inlet_said: bool = field(init=False, default=False)
    # When the follow was armed.
    armed_at: float = field(init=False, default=0.0)
    # The seq of the follow move that this adapter started.
    assist_seq: int | None = field(init=False, default=None)
    # The follow as the standing state. It is set when this adapter arms the follow. It is cleared
    # on a failure, at the end of the unload, when the filament leaves the bay outside the tail,
    # at the end of the tail (tail_out, or its end past the sensor of the head) and on a stop by
    # hand.
    follow_wanted: bool = field(init=False, default=False)
    # The standing re-arm was tried in this idle episode.
    rearm_tried: bool = field(init=False, default=False)
    # This adapter stopped the lane while it ran. The next final event answers that stop, so a
    # "stopped" then comes from this adapter and is not a stop by hand.
    expect_stopped: bool = field(init=False, default=False)
    # The seq of the motion that this stop ended. It is always known when expect_stopped is set.
    expect_seq: int | None = field(init=False, default=None)
    # The emptying of the bay is held back from the U1 while the lane is in the tail.
    port_held: bool = field(init=False, default=False)
    # The end of the tail at the sensor of the head stopped the lane.
    tail_stopped: bool = field(init=False, default=False)
    # A final event came while the emptying of the bay was held back.
    tail_ended: bool = field(init=False, default=False)
    # A tail_out came. A filament in the bay is then held like rise_held below, even when the
    # sensor of the head has already cleared. In a print its insert is then dropped and not raised.
    after_tail: bool = field(init=False, default=False)
    # The filament pulled forward since the bay last read empty, in mm (see GripWatch.watch_out).
    # It counts the encoder of the lane up to the tail_out, then the extruder of the head.
    out_mm: float = field(init=False, default=0.0)
    # The last reading that out_mm counted from, and its source ("encoder" or "extruder").
    out_last: float | None = field(init=False, default=None)
    out_source: str = field(init=False, default="encoder")
    # The line of the end of the tail (and its pause) waits for out_mm to reach OUT_NOTICE_MM.
    out_due: bool = field(init=False, default=False)
    # A print was on at that tail_out. Its end drops the waiting line.
    out_print: bool = field(init=False, default=False)
    # The extruder of the head could not be read for the count. This is logged once.
    out_unread: bool = field(init=False, default=False)
    # A settled insert was not told to the U1 because the sensor of the head saw a filament. (A
    # preload against a loaded head fails.) Outside a print it is told once the head clears. In a
    # print it is dropped.
    rise_held: bool = field(init=False, default=False)
    # The kind of the final event that ended the last push short of the head.
    push_end: str | None = field(init=False, default=None)
    # The seq of that final event. FeedEvents._on_final keeps push_end.
    push_end_seq: int | None = field(init=False, default=None)
    # How far that push went, from its final events. 0 means that none came.
    push_mm: float = field(init=False, default=0.0)
    # True when this adapter's own push (the preload, ACEK_HEAD_PREPARE) left the tip at the
    # head's sensor, short of the head's gear, and nothing moved the lane since (Adapter.started
    # clears it). Otherwise a filament that the head's sensor sees may be in the gear.
    parked_at_sensor: bool = field(init=False, default=False)
    # The prepare of the screen's unload took this lane back cold (ace2k_u1_screen_unload). Its
    # doing ends the unload with no tip forming, and its unload_finish brings no eject.
    cold_unload: bool = field(init=False, default=False)
    # False when the last move ended blocked with no distance.
    push_known: bool = field(init=False, default=True)
    # The moves that the push ran. There is more than one when the budget is over the longest
    # move of the unit.
    push_moves: int = field(init=False, default=0)
    # The farthest the push may have gone. This is push_mm, or more when a move is unknown or was
    # stopped at the head's sensor (that move counts whole).
    push_max_mm: float = field(init=False, default=0.0)
    # A push that cut the unit's tag search runs at tag_push_speed until the lane's encoder reads
    # this (mm), or its tag is read (see PushRunner._cut_search). None: no such stretch.
    push_slow_until: float | None = field(init=False, default=None)
    # The tag of the lane was forgotten by hand (ACEK_TAG_FORGET). This lasts in RAM until the bay
    # empties. Reads of the tag write nothing to the head, and the lane's own spool record
    # speaks for it.
    tag_forgotten: bool = field(init=False, default=False)
    # Whose word is the lane's material on a head that it feeds alone (LaneSpools.lone_setting).
    # setting_mark: when the head's setting on the U1 was last written for the spool in the bay
    # (a tag write, the head set from the lane's spool). None: the setting belongs to no spool
    # there, since the bay was seen empty or the tag was forgotten or read again. Klipper starts
    # with it owned (0): a bay full at the start keeps the setting it had. own_mark: when a tag
    # read or an Edit spool record last came for the lane. The newer of the two speaks for the
    # lane, so a read or a record that was not written to the head is not hidden by an older
    # setting. tag_seen: the lane's last tag read (uid, material, colour); () once the spool left
    # (any read is then new); None before the first read since Klipper started.
    setting_mark: int | None = field(init=False, default=0)
    # The bay emptied: whether the lane has left its head is watched (GripWatch._setting_owner),
    # and since when it reads out of it (None: not yet, or read loaded again).
    leave_watch: bool = field(init=False, default=False)
    leave_since: float | None = field(init=False, default=None)
    own_mark: int = field(init=False, default=-1)
    tag_seen: tuple[object, ...] | None = field(init=False, default=None)
    # The head's setting (type, rgba) on the U1 at the last look, for a head the lane feeds
    # alone (note_setting_seen); None before the first look with the bay full (since Klipper
    # started, or since the spool left).
    setting_seen: tuple[str, object] | None = field(init=False, default=None)
    # When the current motion of the lane ended by its final event. Until a report shows the lane
    # idle, this adapter starts a motion, or a report period has surely passed (a report since
    # then shows a newer motion), the reported mode is not believed.
    ended_at: float | None = field(init=False, default=None)
    # The seq of the last motion that this adapter started on the lane.
    motion_seq: int | None = field(init=False, default=None)
    # The bay and its spool record (see bay_watch). bay_full: the bay last read full. drop_due:
    # the record is due to be dropped, because the bay was emptied by hand and the write has not
    # yet succeeded. out_at: when a move of the lane that takes the filament out of the bay last
    # ran (a roll back, an unload, or a move that ran out of the bay). A bay emptied by such a move
    # is not a spool taken out.
    bay_full: bool = field(init=False, default=False)
    drop_due: bool = field(init=False, default=False)
    # When the bay last went from full to empty (the tail of a refill).
    bay_emptied_at: float = field(init=False, default=0.0)
    out_at: float | None = field(init=False, default=None)
    # eject_by_user: an eject that the user asked for (ACEK_EJECT) runs on the lane, so the user
    # takes the spool out.
    # eject_drop: such an eject ended well (unloaded, or its tail left at the bay). The spool
    # record goes once the bay reads empty, unless a filament goes in or the lane moves first.
    eject_by_user: bool = field(init=False, default=False)
    eject_drop: bool = field(init=False, default=False)
    # The final event of the motion is still to come.
    motion_open: bool = field(init=False, default=False)
    # Earlier motions of this adapter that were replaced before their end came.
    old_seqs: list[int] = field(init=False, default_factory=list)
    # The follow stopped and its head has no lane known.
    unchosen_stopped: bool = field(init=False, default=False)
    # This adapter is running the lane itself (the extrude retry).
    hold: bool = field(init=False, default=False)
    # The end of its spool in a shared head was taken (see GripWatch.shared_end), until the pause
    # timer has run it. The tail notice and the bay watch take it once.
    end_taken: bool = field(init=False, default=False)
    # The pause for the runout was declined too, because the print went on. The end stays taken
    # and the lane stays held until its bay reads empty ("waiting", then "emptied") and then full
    # again, or until the print ends. None otherwise.
    end_declined: str | None = field(init=False, default=None)
    # The grip. grip_wanted is set by this adapter and not cleared since. grip_live means the
    # grip is believed to be still on the unit. A start on the lane clears it there, and the
    # gripped load consumes it. Any final event does too.
    grip_wanted: bool = field(init=False, default=False)
    grip_live: bool = field(init=False, default=False)
    # The bay was seen empty while the grip was live (at the set, or by the bay watch). The
    # automatic load of the unit starts only on an insert edge. So a loaded of seq 0 with the grip
    # live, with the insert down since and up at the event, is the gripped load. A read with
    # motion (ACEK_RFID_READ MOVE=1) starts on a filament that was present all along, with no edge.
    grip_down: bool = field(init=False, default=False)
    # The gripped load of the unit (to its grip only) ended loaded, and no final event since has
    # moved the filament. The tip is at the bay and in no head, so the probe skips this lane. The
    # roll back of the probe would otherwise take such lanes out of their units.
    at_grip: bool = field(init=False, default=False)
    # The tag of the lane (state, uid) as its bay emptied. A "read" that equals it is a stale
    # record and not the tag of the spool put in since.
    tag_before: tuple[object, object] = field(init=False, default=(None, None))
    # The want that the interface of the unit last refused. This is said once.
    grip_refused: bool | None = field(init=False, default=None)
    # The live grip is the park (set_lane_park: the load to the parking point, with no tag
    # search). The bay of a lone head whose refill is pending has it. It is not a grip of grip_mm.
    grip_park: bool = field(init=False, default=False)
    # The live grip is the wait (set_lane_wait: no automatic load on an insert edge, not used up).
    # A lone head's lane has it while the head holds a piece the lane does not own.
    grip_wait: bool = field(init=False, default=False)
    # A filament was in the bay while the wait was live (see GripWatch.wait_filled), noted once
    # per filament: its tag is asked for then. It outlives the wait, which the head clearing ends
    # before the U1's load. Only a removal from the bay and a cleared head state clear it.
    waited: bool = field(init=False, default=False)
    # The filament that waited has left the bay into the gear: a final event of the lane took it
    # SEATED_MM forward or more (the seat's load, the cut's stop after its pull, a feed by hand).
    # It is cleared with waited.
    seated: bool = field(init=False, default=False)
    # The seq of the lane's last seat (see PushRunner._seat). Its late final event still seats.
    seat_seq: int | None = field(init=False, default=None)
    # The bay read empty at its last known reading. The edge to empty takes tag_before.
    bay_empty: bool = field(init=False, default=False)
    # The unit refused the park of this lane. The lane is gripped for the rest of the session.
    park_refused: bool = field(init=False, default=False)
    # The unit refused the wait of this lane. The lane is gripped for the rest of the session.
    wait_refused: bool = field(init=False, default=False)
    # The sensor of the head could not be read. This is logged once.
    head_unread: bool = field(init=False, default=False)
    # The channel state of the head could not be read for the eject's check. This is logged once.
    channel_unread: bool = field(init=False, default=False)
    # What the wheel stand-in reads: the encoder of the lane, or the extruder of the head while the
    # head holds a piece that is not the filament of this lane. The tangle check of the U1
    # compares the wheel with the extruder only while the bay reads a filament, and the waiting
    # lane has one.
    wheel_source: str = field(init=False, default="encoder")
    # The filament of the lane waits behind a piece in the head that is not its own. A U1 load that
    # finds the head set uses that piece. So the lane gets no follow and no end of its holds, the
    # grip stays wanted, and the wheel follows the extruder. Every decision reads this field.
    # It is set only at a transition: a tail_out with the sensor of the head set, or a rise held
    # because the head is set while the channel of the U1 does not hold it. A tick never sets it,
    # and neither does the hook. By default the lane owns what is in its head. This default has a
    # known limit. After a restart with a filament in the bay, and the head set by a piece that is
    # not its own, the lane is taken to own that piece (the channel of the U1 reads preload_finish
    # either way). A wrong guess ends in the error of the follow and the pause for the lane error,
    # which is the safe side. The field is cleared when the sensor of the head clears, by a push
    # that starts with the head clear, or by ACEK_CLEAR LANE=n.
    waits_behind: bool = field(init=False, default=False)
    # The "no follow" of this lane was logged in this episode.
    behind_said: bool = field(init=False, default=False)
    # The lane shares its extruder with other lanes (on any unit) and is not the active lane. Its
    # "no follow" was logged in this episode. The episode ends when the lane is free to follow.
    not_active_said: bool = field(init=False, default=False)
    # Since when the sensor of the head has read clear (the debounce).
    clear_since: float | None = field(init=False, default=None)
    # The console was told that the sensor cannot be read while waiting.
    unread_said: bool = field(init=False, default=False)
    # The position of the extruder of the head could not be read. This is logged once.
    position_unread: bool = field(init=False, default=False)

    @property
    def seat_due(self) -> bool:
        """The filament waited and is still at the bay, so a push starts with the seat."""
        return self.waited and not self.seated

    def clear_wait(self) -> None:
        """Forget the lane's wait and its seat: the filament left the bay, or the head was reset."""
        self.waited, self.seated, self.seat_seq = False, False, None

    def __post_init__(self) -> None:
        self.head = self.ff.filament_ch[self.ch]
        self.api = self.unit.feed
        self.key = unit_key(self.unit, self.lane)


class Slot:
    """One head: its lanes (Hooked, in ``lanes:`` order) and the lane that is in it now.

    Every hook of the U1's channel acts on ``current``. A head with one lane has it from the hook
    on. With several lanes, the current lane is the head's active lane. It is set only through
    Adapter.set_current.
    """

    def __init__(self, head: int, lanes: list[Hooked]) -> None:
        self.head = head
        self.lanes = lanes
        self.current: Hooked | None = lanes[0] if len(lanes) == 1 else None
        # The lane for which the filament setting of the head of the U1 speaks. That is the lane
        # whose spool was last written to it (by the tags, by the spool step of the swap, or by
        # Edit spool on the lane in the head). None when this is not known. A lane that is put in
        # the head in another way is not its lane.
        self.setting_for: Hooked | None = None
        # The lane that the last load of the U1 or the last swap put in the head, while nothing
        # may have changed it since: no move of a lane of the head, no other flow of the U1 on its
        # channel, no other lane named in it. A print's start does not probe the head then. None
        # when this is not known. It lives in RAM, so a restart forgets it.
        self.loaded: Hooked | None = None
        # The wheel of the U1 for this channel. It is one object for the life of the head, fed by
        # the current lane (None: the first lane). A head of one lane shares that
        # lane. With several lanes, a change of lane re-bases the wheel on the source of the new
        # lane, so its counts never jump. wheel_key is the lane and source that it read last.
        self.wheel: EncoderWheel | None = None
        self.wheel_key: tuple[int, str] | None = None
        # The hand watch of a head of several lanes (see HeadWatch.watch_hand). clear_at is since
        # when its sensor has read clear (None when it is set). explained says whether a move
        # explains a filament that sets the sensor since then. events holds the last final event
        # of each lane at the start of the clear.
        self.clear_at: float | None = None
        self.explained = False
        # The status of a lane was not read while the sensor was clear, so the rise decides
        # nothing.
        self.unsure = False
        self.events: dict[LaneKey, tuple[object, object]] = {}

    @property
    def shared(self) -> bool:
        """More than one lane feeds this head."""
        return len(self.lanes) > 1


def parked_slot(slot: Slot | None, h: Hooked) -> TypeGuard[Slot]:
    """True when h is one of the several lanes of slot and is not the current one.

    A slot with no known current lane counts too.
    """
    return slot is not None and slot.shared and slot.current is not h


def move_max_mm(h: Hooked) -> float:
    """Return the longest move (mm) of the unit of h.

    Every unit in use has one, because the hook refuses a unit without it (see
    ACE2kU1._unit_problem).
    """
    return cast(float, h.api.move_max_mm)


class Route:
    """The hooked channels of a feed object, as the hooks of the U1 read them.

    ``get(channel)`` is the current lane of the slot. None means that no lane of this adapter acts
    on the channel now.
    """

    def __init__(self, slots: dict[int, Slot]) -> None:
        self.slots = slots  # channel to Slot

    def get(self, channel: int) -> Hooked | None:
        slot = self.slots.get(channel)
        return None if slot is None else slot.current

    def __contains__(self, channel: object) -> bool:
        # Hooked, whatever lane is current. The motor of the U1 stays off.
        return channel in self.slots


@dataclass
class HostCallbacks:
    """What the Adapter takes from the printer side at construction.

    ACE2kU1 builds it. The defaults need no Klipper, and the tests run on them.
    """

    # Without a gcode object, console notices go only to the log.
    gcode: GCode | None = None
    # The lookup_object of the printer.
    lookup: Callable[[str, object], object] | None = None
    # The G-code error class (printer.command_error of Klipper). The core refuses hand_over with
    # RuntimeError. Anything else that it raises is an internal error.
    command_error: type[Exception] = RuntimeError
    # Called for a lane whose bay reads empty.
    on_bay_empty: Callable[[Hooked], object] | None = None
    # Called once per watch tick, after every lane's bay watch.
    on_lanes_watched: Callable[[], object] | None = None
    # Called when the spool of a shared head ran out.
    on_shared_runout: Callable[[Hooked], object] | None = None
    # True when a swap (or a refill) is pending.
    swap_pending: Callable[[], bool] = lambda: False
    # The head that the swap is on, or None.
    swap_slot: Callable[[], Slot | None] = lambda: None
    # True when a refill of lane h is pending.
    refill_pending: Callable[[Hooked], bool] = lambda _h: False
    # Called first by each watch tick.
    on_tick: Callable[[float], object] | None = None
    # Called once the U1's load pushed a lane to its head's sensor, with the lane and whether the
    # head already held it. It returns why the load is refused (the lane rolled back, unless
    # held), or None.
    load_material: Callable[[Hooked, bool], str | None] = lambda _h, _held: None
    # Why the lane's material does not let the U1 heat its head for an unload, or None (no
    # material set). An eject checks it before the U1's unload of the head.
    material_refusal: Callable[[Hooked], str | None] = lambda _h: None
    # The probe of the shared head. It needs the adapter, so it is set at the hook.
    probe: HeadProbe | None = None


def api_at_least(feed: object, level: int) -> bool:
    """Return whether ace2k's feed object has the host interface version level or a later one.

    An object with no API_VERSION reads 0.
    """
    return bool(getattr(feed, "API_VERSION", 0) >= level)
