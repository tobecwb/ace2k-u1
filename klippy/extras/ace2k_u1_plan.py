"""Plans a print from a G-code file.

It reads the tools of the file and the order of its T commands, without changing the file. It
matches each tool to a lane by material and by the closest colour as the eye sees it (CIE76 ΔE in
CIELAB). It also counts the lane swaps that a choice causes for each head. There is no Klipper
here. The adapter calls this module, and the host tests drive it.
"""

from __future__ import annotations

import collections
import math
import mmap
import os
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import NamedTuple, TypeVar, Union


class Tool(NamedTuple):
    """A tool of the config block of the slicer.

    It has #rrggbb, the material, the temperature and the temperature of the first layer. A value
    is None where it is unknown. The page copies its fields into the tools of the plan
    (PlanTool).
    """

    colour: str | None
    material: str | None
    temp: int | None
    first_temp: int | None


class LaneSpool(NamedTuple):
    """The spool of a lane, as the plan matches it.

    It has the key (the text of the lane), #rrggbb and the material. empty means that the bay of
    the lane reads empty, with no spool in it. Such a lane is never a candidate. Its colour and
    material are None, unless its spool ran out with its rest still loaded in its head: they then
    tell what that rest is (LaneSpools.lane_spool).
    """

    key: str
    colour: str | None
    material: str | None
    empty: bool = False


class ParsedPlan(NamedTuple):
    """The plan that was read from a file.

    order holds the T lines, with a T that is repeated on consecutive lines merged. raw holds every
    T line, in file order. The cool-down of the outgoing tool looks at the very next one. order_at
    holds, for each entry of order, the byte offset of the T line that starts it, and size the size
    of the file those offsets refer to. The page draws the print's timeline from them. read_file
    gives a list; a plan built by hand without offsets has an empty tuple, never one list shared
    by every such plan.
    """

    tools: list[Tool]
    order: list[int]
    raw: list[int]
    order_at: Sequence[int] = ()
    size: int = 0


# The plan of a file as the cache keeps it: (tools, order, raw, order_at, size).
Parsed = tuple[tuple[Tool, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...], int]
# A mapped file. An empty one is read as b"".
Data = Union[bytes, mmap.mmap]
Key = TypeVar("Key")

T_LINE = re.compile(rb"^T(\d+)[ \t]*(?:;[^\n]*)?\r?$", re.M)
CONFIG_START = b"; CONFIG_BLOCK_START"
CONFIG_END = b"; CONFIG_BLOCK_END"
EXEC_START = b"; EXECUTABLE_BLOCK_START"
EXEC_END = b"; EXECUTABLE_BLOCK_END"
CACHE_SIZE = 4
# Maps (path, mtime_ns, size) to (tools, order, raw).
_CACHE: collections.OrderedDict[tuple[str, int, int], Parsed] = collections.OrderedDict()
# The reader thread of the plan reads and writes the cache (ace2k_u1_tracker.read_plan).
_CACHE_LOCK = threading.Lock()
CONFIG_LINE = re.compile(r"^; (\w+) =\s?(.*)$")
# For the conversion from sRGB to CIELAB (D65). The linear segment of the sRGB curve ends at this
# channel value. The cube root of CIELAB starts above this ratio, and below it CIELAB is linear.
SRGB_LINEAR_MAX = 0.04045
LAB_LINEAR_MAX = 0.008856


class PlanError(Exception):
    """A file that the plan cannot read. The message is the reason, for the console and the page."""


COLOUR = re.compile(r"[0-9a-f]{6}([0-9a-f]{2})?")


def parse_colour(value: object, hash_ok: bool = True) -> tuple[str, str | None] | None:
    """Parses a colour written rrggbb or rrggbbaa, in any case.

    The colour can come from a slicer, from a tag or from a setting of the U1. Returns (rrggbb,
    aa) in lower case. aa is None when the colour is written without alpha. A leading # is dropped
    when hash_ok is set. Otherwise it makes the colour unreadable. Returns None when the value is
    neither form.
    """
    text = str(value or "").strip().lower()
    if hash_ok:
        text = text.lstrip("#")
    if not COLOUR.fullmatch(text):
        return None
    return text[:6], text[6:] or None


def normal_colour(value: object) -> str | None:
    """Returns #rrggbb for a colour written #rrggbb, rrggbb or rrggbbaa, or None.

    The colour can be written with or without #, in any case. It can come from a slicer, from a
    tag or from a setting of the U1.
    """
    parsed = parse_colour(value)
    return None if parsed is None else "#" + parsed[0]


def read_file(path: str | os.PathLike[str]) -> ParsedPlan:
    """Reads a slicer file and returns ParsedPlan(tools, order, raw, order_at, size).

    It reads the executable and config blocks of OrcaSlicer. The file is mapped, not read line by
    line. The config block is found from the end of the file and parsed alone. One regular
    expression finds the T lines in the executable block.

    The last CACHE_SIZE plans are kept by (path, mtime, size), so a file that did not change is
    not read again.
    """
    st = os.stat(path)
    key = (os.fspath(path), st.st_mtime_ns, st.st_size)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is None:
            cached = _parse(path)
            _CACHE[key] = cached
            while len(_CACHE) > CACHE_SIZE:
                _CACHE.popitem(last=False)
        else:
            _CACHE.move_to_end(key)
    tools, order, raw, order_at, size = cached
    return ParsedPlan(
        tools=list(tools), order=list(order), raw=list(raw), order_at=list(order_at), size=size
    )


def _marker(
    data: Data, text: bytes, start: int = 0, end: int | None = None, last: bool = False
) -> int:
    """Returns where the line text starts in data[start:end], or -1.

    With last set, it finds the last match instead of the first. A match counts only as a whole
    line.
    """
    end = len(data) if end is None else end
    while True:
        at = data.rfind(text, start, end) if last else data.find(text, start, end)
        if at < 0:
            return -1
        after = at + len(text)
        whole_start = at == 0 or data[at - 1 : at] == b"\n"
        whole_end = after == len(data) or data[after : after + 1] in (b"\n", b"\r")
        if whole_start and whole_end:
            return at
        if last:
            end = at
        else:
            start = after


def _blocks(data: Data) -> tuple[str | None, tuple[int, int] | None]:
    """Returns the text of the config block and the (start, end) of the executable block.

    The file is mapped. Either result is None when that block is absent.
    """
    config = None
    top = _marker(data, CONFIG_START, last=True)
    if top >= 0:
        bottom = _marker(data, CONFIG_END, top)
        config = data[top : len(data) if bottom < 0 else bottom].decode("utf-8", "replace")
    executable = None
    start = _marker(data, EXEC_START)
    if start >= 0:
        end = _marker(data, EXEC_END, start)
        executable = (start, len(data) if end < 0 else end)
    return config, executable


def _parse(path: str | os.PathLike[str]) -> Parsed:
    """Returns (tools, order, raw, order_at, size) of a file; all but size are tuples.

    They are kept in the cache and never change.
    """
    data: Data
    with open(path, "rb") as f:
        try:
            data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # an empty file cannot be mapped
            data = b""
        try:
            size = len(data)
            config_text, executable = _blocks(data)
            order, raw, order_at = _t_lines(data, executable)
        finally:
            if isinstance(data, mmap.mmap):
                data.close()
    tools = _tools(_config_keys(config_text))
    return tools, tuple(order), tuple(raw), tuple(order_at), size


def _t_lines(
    data: Data, executable: tuple[int, int] | None
) -> tuple[list[int], list[int], list[int]]:
    """Returns (order, raw, order_at) for the T lines in the executable block (start, end) of data.

    raw holds every T line in file order. order is the same, with a T that is repeated on
    consecutive lines merged. order_at holds the byte offset of the T line that starts each entry
    of order. All are empty without the block.
    """
    order: list[int] = []
    raw: list[int] = []
    order_at: list[int] = []
    if executable is None:
        return order, raw, order_at
    for m in T_LINE.finditer(data, *executable):
        n = int(m.group(1))
        raw.append(n)
        if not order or order[-1] != n:
            order.append(n)
            order_at.append(m.start())
    return order, raw, order_at


def _config_keys(config_text: str | None) -> dict[str, str]:
    """Returns the "; key = value" lines of the slicer config block as {key: value}.

    Raises a PlanError when the block is missing, or when it has no filament_colour.
    """
    config: dict[str, str] = {}
    for line in (config_text or "").splitlines():
        m = CONFIG_LINE.match(line)
        if m:
            config[m.group(1)] = m.group(2)
    if not config:
        raise PlanError("no slicer config block: slice the file with OrcaSlicer")
    if "filament_colour" not in config:
        raise PlanError("the slicer config block has no filament_colour")
    return config


def _tools(config: dict[str, str]) -> tuple[Tool, ...]:
    """Returns the tools from the keys of the slicer config block.

    Each filament_colour entry gives one tool, with its colour, material, temperature and
    temperature of the first layer. A value is None where a list is short.
    """
    colours = [normal_colour(c) for c in config["filament_colour"].split(";")]
    materials = [m.strip() for m in config.get("filament_type", "").split(";")]
    temps = [_int(t) for t in config.get("nozzle_temperature", "").split(",")]
    firsts = [_int(t) for t in config.get("nozzle_temperature_initial_layer", "").split(",")]
    tools: list[Tool] = []
    for i, colour in enumerate(colours):
        tools.append(
            Tool(
                colour=colour,
                material=materials[i] if i < len(materials) else None,
                temp=temps[i] if i < len(temps) else None,
                first_temp=firsts[i] if i < len(firsts) else None,
            )
        )
    return tuple(tools)


def _int(text: str) -> int | None:
    """Returns a temperature of the config block as a whole number, or None when it is unknown.

    A value is unknown when it is not a number, or when it is NaN (ValueError) or infinite
    (OverflowError).
    """
    try:
        return int(float(text))
    except (ValueError, OverflowError):
        return None


def _lab(colour: str) -> tuple[float, float, float]:
    """Converts #rrggbb to CIELAB (D65)."""
    rgb = [int(colour[i : i + 2], 16) / 255.0 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= SRGB_LINEAR_MAX else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883

    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > LAB_LINEAR_MAX else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = f(x), f(y), f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def distance(a: str, b: str) -> float:
    """Returns how far apart two colours look, as CIE76 ΔE.

    About 2.3 is the smallest difference that can be seen.
    """
    return math.dist(_lab(a), _lab(b))


# The names that the page gives a spool's colour, each with a colour of its own. A spool's colour
# takes the name whose colour is closest to it (distance). The names are plain and rough on
# purpose: they help to tell the spools apart in a list, not to describe them.
COLOUR_NAMES = (
    ("white", "#ffffff"),
    ("black", "#000000"),
    ("gray", "#808080"),
    ("silver", "#c0c0c0"),
    ("red", "#d32020"),
    ("dark red", "#8b0000"),
    ("orange", "#ff8c00"),
    ("yellow", "#ffe000"),
    ("gold", "#d4af37"),
    ("beige", "#e8d8b0"),
    ("brown", "#7a4a2a"),
    ("pink", "#ffb6c1"),
    ("magenta", "#ff00ff"),
    ("purple", "#800080"),
    ("violet", "#8a2be2"),
    ("blue", "#1e50e0"),
    ("navy", "#000080"),
    ("light blue", "#87ceeb"),
    ("cyan", "#00ffff"),
    ("teal", "#008080"),
    ("green", "#20a020"),
    ("dark green", "#006400"),
    ("lime", "#80ff00"),
    ("olive", "#808000"),
)


def colour_name(colour: object) -> str | None:
    """Returns the name of COLOUR_NAMES closest to a colour, or None when it has none."""
    known = normal_colour(colour)
    if known is None:
        return None
    lab = _lab(known)
    return min(_NAMES_LAB, key=lambda pair: math.dist(lab, pair[1]))[0]


# The names of COLOUR_NAMES with their colours in CIELAB, worked out once.
_NAMES_LAB = tuple((name, _lab(colour)) for name, colour in COLOUR_NAMES)


def _material(text: object) -> str:
    return str(text or "").strip().lower()


def same_material(a: object, b: object) -> bool:
    """Tells whether two materials are the same.

    They are compared without case and without spaces at their ends. Both must be known.
    """
    return bool(_material(a)) and _material(a) == _material(b)


def other_material(a: object, b: object) -> bool:
    """Tells whether two materials are both known and not the same.

    A material that is unknown is not another material.
    """
    return bool(_material(a)) and bool(_material(b)) and _material(a) != _material(b)


def suggest(
    tools: Sequence[Tool], lanes: Sequence[LaneSpool], max_distance: float, min_margin: float
) -> list[tuple[str | None, str]]:
    """Suggests a lane for each tool.

    For each tool the result is (the key of the lane, "closest"), or (None, why). These are the
    reasons.
    - no_material: no lane has its material (same_material). A lane whose material is not known,
      or whose bay is empty, is no candidate.
    - no_colour: a candidate lane has no colour set.
    - too_far: the closest lane is farther than max_distance.
    - two_close: the second lane is within min_margin of the first.
    """
    out: list[tuple[str | None, str]] = []
    for t in tools:
        same = [ln for ln in lanes if not ln.empty and same_material(t.material, ln.material)]
        if not same:
            out.append((None, "no_material"))
            continue
        # Each candidate with its colour. A candidate without a colour leaves no choice.
        coloured = [(ln, ln.colour) for ln in same if ln.colour is not None]
        colour = t.colour
        if len(coloured) < len(same) or colour is None:
            out.append((None, "no_colour"))
            continue
        ranked = sorted(coloured, key=lambda pair: distance(colour, pair[1]))
        best = distance(colour, ranked[0][1])
        if best > max_distance:
            out.append((None, "too_far"))
        elif len(ranked) > 1 and distance(colour, ranked[1][1]) - best < min_margin:
            out.append((None, "two_close"))
        else:
            out.append((ranked[0][0].key, "closest"))
    return out


def swaps(
    order: Sequence[int], choice: Mapping[int, Key], head_of: Callable[[Key], int | None]
) -> dict[int, int]:
    """Counts the lane swaps of each head for a choice {tool: lane key}. Returns {head: swaps}.

    A swap happens at a T whose lane is not the last lane of its head.
    """
    last: dict[int, Key] = {}
    count: dict[int, int] = {}
    for n in order:
        key = choice.get(n)
        head = None if key is None else head_of(key)
        if key is None or head is None:
            continue
        count.setdefault(head, 0)
        if head in last and last[head] != key:
            count[head] += 1
        last[head] = key
    return count
