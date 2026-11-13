"""Reads the keys of ace2k_u1 from printer.cfg.

It reads the lanes: list, the extruder keys of the + groups and the keys of the lane swap. These
are pure functions of a Klipper config object. Each one reads its keys once, at the start. A
value outside its bounds stops Klipper there (config.error).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ace2k_klipper_types import Config as Section
    from .ace2k_u1_printer_types import Config

# The coordinator module of ace2k. It decides which units a config declares, so this module
# does not copy that rule.
try:
    from . import ace2k_units
except ImportError:
    try:  # the host tests put the klippy/extras of ace2k on the path
        import ace2k_units  # type: ignore[import-not-found, no-redef]
    except ImportError:  # ace2k is not installed, so there is nothing to hook; the hook says so
        ace2k_units = None  # type: ignore[assignment]  # the module, or None without ace2k

try:
    from .ace2k_u1_common import UNIT_TOP_FALLBACK_MM_S, unit_speed
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        UNIT_TOP_FALLBACK_MM_S,
        unit_speed,
    )

LANES = 4
# Default of headN_splitter_mm, in mm: 810 from the inlet to the gear, 80 to a splitter at the
# rear, 70 inside the splitter and 30 as a margin (990), rounded up to 1000.
SPLITTER_DEFAULT_MM = 1000.0
SPLITTER_MAX_MM = 2000.0  # the ceiling of headN_splitter_mm
SWAP_SPEED = 80.0  # mm/s, for the roll back of the old lane and the push of the new lane
# The ceiling of the SPEED of the bench G-codes (ACEK_FREE_CHECK and the probes): the top of the
# unit's range. The move still runs within the unit's own speeds (Adapter.within_speed).
BENCH_SPEED_MAX_MM_S = 80.0
# The longest move of a probe or a free check. This is a limit of the adapter, below the longest
# move of the unit.
SWAP_LENGTH_MAX_MM = 300.0
# The largest distance (CIE76 dE) that the colour of a spool may be from the colour in the file.
COLOUR_MAX_DISTANCE = 25.0
COLOUR_MIN_MARGIN = 8.0  # how much closer (dE) the best spool must be than the second best
UNLOAD_TEMPS_DEFAULT = "PLA:220"  # °C per material; the unload of a swap forms the tip at it
UNLOAD_TEMP_MIN, UNLOAD_TEMP_MAX = 150.0, 350.0
# The temperature range of each material (°C). A temperature outside it is warned about, never
# refused (ace2k_u1_temps). The bounds of an entry are those of unload_temps.
TEMP_RANGES_DEFAULT = "PLA:190-230, PETG:220-260, ABS:230-270, ASA:230-270, TPU:210-240"
# The purge of a background swap: the new filament pushed out of the nozzle in the dock (mm). The
# default is the load macro's own purge.
BACKGROUND_PURGE_MM, BACKGROUND_PURGE_MAX_MM = 10.0, 100.0
GROUP_LANES = 2  # a head with this many lanes or more is a + group

# The lane of a head as parse_lanes gives it: (head index, unit name or None, lane index).
LaneRef = tuple[int, "str | None", int]
# A laneN_extruder key of a lane in a + group: (head, text of the lane, section, key, value).
LaneExtruder = tuple[int, str, str, str, str]


@dataclass
class SwapConfig:
    """The keys of the lane swap, as read_swap_keys gives them."""

    swap_speed: float
    splitter_mm: dict[int, float]
    splitter_default: set[int]
    colour_max_distance: float
    colour_min_margin: float
    probe_mm: float
    probe_speed: float
    probe_max_temp: float
    unload_temps: dict[str, float] = field(default_factory=dict)
    # The heads that swap their lane while parked (background_heads, 0-based).
    background_heads: frozenset[int] = frozenset()
    # The new filament purged out of the nozzle in the dock after the grip (mm). 0: none, and the
    # load macro purges when the head is picked.
    background_purge_mm: float = BACKGROUND_PURGE_MM
    # True: a swap's arrival wipes the nozzle once more on the pads, after the U1's pre-extrusion
    # drops the purge in the bin and before the head goes to its idle position (arrival_wipe).
    arrival_wipe: bool = True
    # False when swap_speed is left out (SWAP_SPEED); the hook then checks it against no unit.
    swap_speed_set: bool = True

    def swap_speed_for(self, api: object) -> float:
        """Returns the swap speed of a move of the unit whose feed is api (mm/s).

        It is swap_speed (SWAP_SPEED when left out) within the unit's speeds (unit_speed): a
        unit whose top speed is lower, an older image's 70 or the fallback of an image that
        gives none, runs its moves at that.
        """
        return unit_speed(api, self.swap_speed)


def check_forms(config: Config, entries: list[str], declared: list[str | None]) -> None:
    """Checks the lanes: list as a whole.

    The forms must not mix. A "-" needs <unit>:<lane> places. There can be no more entries than
    heads. Several units need the named form. A wrong list stops Klipper.
    """
    # "-" is a place without a lane. That head stays with the U1. It decides no form.
    real = [entry for entry in entries if entry != "-"]
    for entry in real:
        _check_entry(config, entry)
    named = [":" in member for entry in real for member in entry.split("+")]
    _check_one_form(config, entries, real, named)
    if len(entries) > LANES:
        raise config.error(f"ace2k_u1: lanes: more than {LANES} heads")
    _check_several_units(config, declared, named)


def _check_several_units(config: Config, declared: list[str | None], named: list[bool]) -> None:
    """Checks that every member names its unit when several units are declared.

    named tells, for each member, whether it has a unit.
    """
    if len(declared) > 1 and not all(named):
        listed = ", ".join(name or "[ace2k]" for name in declared)
        raise config.error(
            "ace2k_u1: lanes: needs <unit>:<lane> entries: this printer has several units "
            f"({listed})"
        )


def _check_entry(config: Config, entry: str) -> None:
    """Checks that an entry other than "-" has no empty member and no "-" in a + group."""
    if any(m.strip() in ("", "-") for m in entry.split("+")):
        raise config.error(f"ace2k_u1: lanes: {entry!r} is not <unit>:<lane>")


def _check_one_form(config: Config, entries: list[str], real: list[str], named: list[bool]) -> None:
    """Checks that all entries have one form, and that a "-" is only among <unit>:<lane> entries.

    real are the entries other than "-". named tells, for each member, whether it has a unit.
    """
    if any(named) and not all(named):
        raise config.error("ace2k_u1: lanes: every entry <unit>:<lane>, or every one bare")
    if real and len(real) < len(entries) and not all(named):
        # A bare lane n always feeds head n, so an empty place there would mean nothing.
        raise config.error(
            "ace2k_u1: lanes: '-' needs <unit>:<lane> entries; with bare lanes leave the lane out"
        )


def parse_member(config: Config, member: str, declared: list[str | None]) -> tuple[str | None, int]:
    """Parses one <unit>:<lane> (or bare lane) of an entry.

    Returns the unit name (or None) and the lane (1-4). A member with another form stops
    Klipper. So does a lane outside 1..4, or a unit that the config does not declare.
    """
    name, colon, lane = member.rpartition(":")
    try:
        if colon and not name.strip():
            raise ValueError  # ":2" is a named form without its unit
        n = int(lane)
    except ValueError:
        raise config.error(f"ace2k_u1: lanes: {member!r} is not <unit>:<lane>") from None
    if not 1 <= n <= LANES:
        raise config.error("ace2k_u1: lanes must be within 1..4")
    unit = name.strip().lower() or None
    if unit is not None and declared and unit not in declared:
        listed = ", ".join(n or "[ace2k]" for n in declared)
        raise config.error(f"ace2k_u1: lanes: no unit {unit}; units: {listed}")
    return unit, n


def parse_lanes(config: Config) -> list[LaneRef]:
    """Parses lanes: into (head index, unit name or None, lane index) per head.

    An entry <unit>:<lane> feeds the head of its place in the list. For example a:1, a:2, b:1,
    b:2 feed heads 0-3. A bare lane n is valid with a single unit. It feeds head n-1 as it
    always has, so lanes: 2, 4 feed heads 1 and 3.

    An entry "-" is a head left to the U1. It counts as a place, so lanes: a:1, -, b:1 leaves
    head 2 to the U1. It decides no form. Only the <unit>:<lane> form has places. A bare lane n
    feeds head n-1 wherever it is listed, so a "-" among bare lanes is an error. A list of only
    "-" hooks nothing.

    These cases stop Klipper at the start.
    - The two forms are mixed.
    - An entry ":n" has no unit.
    - A lane is outside 1..4.
    - A unit:lane feeds two heads.
    - There are more entries than the U1 has heads.
    - A bare lane is used while the config declares several units.
    - A unit is not declared by the config.
    """
    declared = [] if ace2k_units is None else ace2k_units.unit_names(config)
    entries = [str(x).strip() for x in config.getlist("lanes", ["1", "2", "3", "4"])]
    check_forms(config, entries, declared)
    refs: list[LaneRef] = []
    for place, entry in enumerate(entries):
        if entry != "-":
            _add_entry(config, refs, place, entry, declared)
    return refs


def _add_entry(
    config: Config,
    refs: list[LaneRef],
    place: int,
    entry: str,
    declared: list[str | None],
) -> None:
    """Adds one entry of lanes: (at place in the list) to refs.

    It adds one (head, unit name, lane index) per member. A unit:lane that is already in refs
    stops Klipper.
    """
    head = None
    for member in (m.strip() for m in entry.split("+")):
        name, n = parse_member(config, member, declared)
        if (name, n - 1) in [(r[1], r[2]) for r in refs]:
            raise config.error(f"ace2k_u1: lanes: {member.lower()} feeds two heads")
        if head is None:
            # A group feeds the head of its first lane.
            head = place if ":" in member else n - 1
        refs.append((head, name, n - 1))


def lane_counts(refs: list[LaneRef]) -> dict[int, int]:
    """Counts the lanes of each head.

    Returns {head: number of lanes that lanes: gives it}. A head with none is left out.
    """
    counts: dict[int, int] = {}
    for head, _name, _lane in refs:
        counts[head] = counts.get(head, 0) + 1
    return counts


def read_lane_extruders(
    config: Config, refs: list[LaneRef], counts: dict[int, int]
) -> list[LaneExtruder]:
    """Reads the laneN_extruder keys that [ace2k <u>] sets for the lanes of a + group.

    counts is the result of lane_counts. Returns a list of (head, text of the lane, section,
    key, value). check_lane_extruders checks them against the extruder of the head. A lane alone
    on its head is left to ace2k to map.

    The lanes: key reads the name of a unit without case, but Klipper keeps the name of a
    section as written. So the section of the unit is found without case and named as written.
    """
    sections = {s.get_name().lower(): s for s in config.get_prefix_sections("ace2k")}
    found: list[LaneExtruder] = []
    for head, name, lane in refs:
        if counts[head] < GROUP_LANES:
            continue
        section, key, value = _lane_extruder(sections, name, lane)
        if value:
            text = f"{lane + 1}" if name is None else f"{name}:{lane + 1}"
            found.append((head, text, section, key, value))
    return found


def _lane_extruder(
    sections: Mapping[str, Section], name: str | None, lane: int
) -> tuple[str, str, str]:
    """Reads the laneN_extruder key of one lane.

    Returns the section of the unit as written, the key, and its value stripped. The value is ""
    when it is unset or the section is missing. sections maps the lower-case names to the ace2k
    sections.
    """
    section = "ace2k" if name is None else f"ace2k {name}"
    where = sections.get(section.lower())
    key = f"lane{lane + 1}_extruder"
    if where is None:
        return section, key, ""
    return where.get_name(), key, (where.get(key, None) or "").strip()


def check_lane_extruders(keys: list[LaneExtruder], extruders: Mapping[int, str]) -> str | None:
    """Checks the laneN_extruder keys (from read_lane_extruders) against the extruders of the heads.

    A key that names another extruder than the one of its group's head stops Klipper at the
    start. That key wins on the side of ace2k. The shared extruder of the ace2k core and the head
    of the adapter would then disagree, and the hand over of the head would stop the wrong
    followers.

    extruders maps the head to the name of its extruder on the U1 (extruder_names). Returns the
    text of the problem, or None. A head whose extruder is not known is not judged.
    """
    for head, text, section, key, value in keys:
        own = extruders.get(head)
        if own is not None and value != own:
            return (
                f"ace2k_u1: lanes: {text} is in head {head + 1}'s group but [{section}] {key} "
                f"is {value}"
            )
    return None


def read_swap_keys(config: Config, counts: dict[int, int]) -> SwapConfig:
    """Reads the keys of the lane swap.

    They are swap_speed, headN_splitter_mm for each head with several lanes, the colour limits,
    the keys of the probe, background_heads, background_purge_mm and arrival_wipe. counts is the
    result of lane_counts. For the splitter distance it records the value and which heads use the
    default. A splitter distance on a head with one lane or none stops Klipper at the start.
    """
    splitter: dict[int, float] = {}
    default: set[int] = set()
    for head in range(LANES):
        key = f"head{head + 1}_splitter_mm"
        value = config.getfloat(key, None, above=0.0, maxval=SPLITTER_MAX_MM)
        if counts.get(head, 0) < GROUP_LANES:
            if value is not None:
                raise config.error(f"ace2k_u1: {key}: head {head + 1} has one lane or none")
            continue
        if value is None:
            value = SPLITTER_DEFAULT_MM
            default.add(head)
        splitter[head] = value
    swap_speed = config.getfloat("swap_speed", None, above=0.0)
    return SwapConfig(
        # The ceiling of the speeds comes from the unit (its speed_max). A value that is set is
        # checked at the hook (bound_problems); the default follows each unit (swap_speed_for).
        swap_speed=SWAP_SPEED if swap_speed is None else swap_speed,
        swap_speed_set=swap_speed is not None,
        splitter_mm=splitter,
        splitter_default=default,
        colour_max_distance=config.getfloat("colour_max_distance", COLOUR_MAX_DISTANCE, above=0.0),
        colour_min_margin=config.getfloat("colour_min_margin", COLOUR_MIN_MARGIN, minval=0.0),
        # The probe of a shared head whose sensor is set. These keys are how far each lane rolls
        # back, the speed of every probe move, and the hottest the head may be. The roll back is
        # longer than the slack of the buffer (about 50 mm), which a held lane takes up with its
        # plunger at rest.
        probe_mm=config.getfloat("probe_mm", 100.0, minval=60.0, maxval=SWAP_LENGTH_MAX_MM),
        probe_speed=config.getfloat("probe_speed", 50.0, above=0.0),
        probe_max_temp=config.getfloat("probe_max_temp", 60.0, minval=30.0, maxval=150.0),
        unload_temps=read_unload_temps(config),
        background_heads=read_background_heads(config, counts),
        # After the grip in the dock, the new filament is pushed this far more: the purge of the
        # load, done in the dock. It falls through the dock's opening.
        background_purge_mm=config.getfloat(
            "background_purge_mm", BACKGROUND_PURGE_MM, minval=0.0, maxval=BACKGROUND_PURGE_MAX_MM
        ),
        # One more wipe on the pads at the end of each swap's arrival, before the idle position.
        arrival_wipe=config.getboolean("arrival_wipe", True),
    )


def read_background_heads(config: Config, counts: dict[int, int]) -> frozenset[int]:
    """Reads background_heads, a comma-separated list of head numbers 1 to 4.

    Returns the heads 0-based. Empty by default. A head out of 1-4, a head listed twice and a
    head that one lane feeds (or none) stop Klipper at the start.
    """
    raw = config.get("background_heads", "")
    heads: set[int] = set()
    for item in filter(None, (x.strip() for x in str(raw).split(","))):
        if not item.isdigit() or not 1 <= int(item) <= LANES:
            raise config.error(f"ace2k_u1: background_heads: {item!r} is not a head 1-{LANES}")
        head = int(item) - 1
        if head in heads:
            raise config.error(f"ace2k_u1: background_heads: head {head + 1} is listed twice")
        if counts.get(head, 0) < GROUP_LANES:
            raise config.error(
                f"ace2k_u1: background_heads: head {head + 1} is fed by one lane or none"
            )
        heads.add(head)
    return frozenset(heads)


def bound_problems(
    feed: object, where: str, speeds: Mapping[str, float], lengths: Mapping[str, float]
) -> list[str]:
    """Checks the speeds and lengths of the config against the bounds of a unit.

    speeds maps a key to mm/s and lengths maps a key to mm. They are the values that the moves
    of the unit take. The check runs once, at the hook (_speed_out, _length_out). A value
    outside the bounds would make the unit refuse every such move. where is the text that opens
    each line (unit_prefix). Returns the lines about the problems.
    """
    lines = [_speed_out(feed, key, value) for key, value in speeds.items()]
    lines += [_length_out(feed, key, value) for key, value in lengths.items()]
    return [where + line for line in lines if line is not None]


def _speed_out(feed: object, key: str, value: float) -> str | None:
    """Returns why a speed is outside feed.speed_min .. feed.speed_max, or None.

    An image without these limits uses UNIT_TOP_FALLBACK_MM_S as the upper limit.
    """
    low = getattr(feed, "speed_min", None)
    high = getattr(feed, "speed_max", None)
    if not (isinstance(low, (int, float)) and isinstance(high, (int, float))):
        if value > UNIT_TOP_FALLBACK_MM_S:
            return f"{key} {value:g} is above {UNIT_TOP_FALLBACK_MM_S:g} mm/s"
        return None
    if low <= value <= high:
        return None
    return f"{key} {value:g} is outside the unit's {low:g}..{high:g} mm/s"


def _length_out(feed: object, key: str, value: float) -> str | None:
    """Returns why a length is over feed.move_max_mm, or None. Every hooked unit has it."""
    longest = getattr(feed, "move_max_mm", None)
    if isinstance(longest, (int, float)) and value > longest:
        return f"{key} {value:g} is longer than the unit's longest move, {longest:g} mm"
    return None


def read_unload_temps(config: Config) -> dict[str, float]:
    """Reads unload_temps, a comma-separated list of <material>:<°C>.

    Returns {MATERIAL: °C}, with the material in upper case. An entry that is not a material
    with a temperature within its bounds stops Klipper at the start. So does a material that is
    listed twice, whatever its case.
    """
    raw = config.get("unload_temps", UNLOAD_TEMPS_DEFAULT)
    temps: dict[str, float] = {}
    for item in filter(None, (x.strip() for x in str(raw).split(","))):
        material, temp = _unload_temp_item(config, item)
        if material in temps:
            raise config.error(f"ace2k_u1: unload_temps: {material} is listed twice")
        temps[material] = temp
    return temps


def _unload_temp_item(config: Config, item: str) -> tuple[str, float]:
    """Parses one <material>:<°C> of unload_temps into (MATERIAL, °C).

    Anything else stops Klipper.
    """
    name, sep, value = item.partition(":")
    try:
        temp = float(value)
    except ValueError:
        temp = None
    if (
        not sep
        or not name.strip()
        or temp is None
        or not UNLOAD_TEMP_MIN <= temp <= UNLOAD_TEMP_MAX
    ):
        raise config.error(
            f"ace2k_u1: unload_temps: '{item}' is not <material>:<°C> "
            f"({UNLOAD_TEMP_MIN:g}-{UNLOAD_TEMP_MAX:g})"
        )
    # One word, as a material is matched (material_param): PLA SILK becomes PLASILK.
    return material_param(name), temp


def read_temp_ranges(config: Config) -> dict[str, tuple[float, float]]:
    """Reads temp_ranges, a comma-separated list of <material>:<low>-<high> in °C.

    Returns {MATERIAL: (low, high)}, with the material as material_param gives it. An empty
    value is an empty table. An entry that is not a material with a range within 150-350 °C (low
    below high) stops Klipper at the start. So does a material that is listed twice.
    """
    raw = config.get("temp_ranges", TEMP_RANGES_DEFAULT)
    ranges: dict[str, tuple[float, float]] = {}
    for item in filter(None, (x.strip() for x in str(raw).split(","))):
        material, span = _temp_range_item(config, item)
        if material in ranges:
            raise config.error(f"ace2k_u1: temp_ranges: {material} is listed twice")
        ranges[material] = span
    return ranges


def _temp_range_item(config: Config, item: str) -> tuple[str, tuple[float, float]]:
    """Parses one <material>:<low>-<high> of temp_ranges. Anything else stops Klipper."""
    name, sep, value = item.partition(":")
    low_text, dash, high_text = value.partition("-")
    low: float | None
    high: float | None
    try:
        low, high = float(low_text), float(high_text)
    except ValueError:
        low = high = None
    if (
        not sep
        or not dash
        or not name.strip()
        or low is None
        or high is None
        or not UNLOAD_TEMP_MIN <= low < high <= UNLOAD_TEMP_MAX
    ):
        raise config.error(
            f"ace2k_u1: temp_ranges: '{item}' is not <material>:<low>-<high> "
            f"({UNLOAD_TEMP_MIN:g}-{UNLOAD_TEMP_MAX:g} °C)"
        )
    return material_param(name), (low, high)


def material_param(material: object) -> str:
    """Returns material as one word in upper case.

    Only letters, digits, "-", "+" and "_" are kept. This is the form of the MATERIAL= value of
    a macro, where a space would split the line. It is also the form in which unload_temps is
    matched.
    """
    return re.sub(r"[^A-Z0-9+_-]", "", str(material or "").upper())
