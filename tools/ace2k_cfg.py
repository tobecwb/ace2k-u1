"""ace2k_cfg: read and edit the U1 installer's config files (docs/install.md).

install.sh runs it from the unpacked release, as python3 -I ace2k_cfg.py COMMAND ARGS.  Each
command reads its files and prints the result on stdout; install.sh writes it in place after its
backup.  An edit changes only the lines it names: every other line stays byte for byte.

  units CFG                  one line per unit section: name, serial, uid (tab-separated)
  objects CFG                one line per unit section: its Klipper object, spelt as written
  render TEMPLATE N=SER ...  the release's ace2k.cfg with these units, each named
  rename-a CFG               an unnamed [ace2k] becomes unit a
  add CFG NAME SER           a unit's [mcu ace2k_NAME], [ace2k NAME] and its three sensors
  drop CFG NAME              that unit's sections and its sensors out
  uid CFG NAME UID           uid: into [ace2k NAME] when it has none
  heads U1CFG NAME           the four heads' lanes: entries, written NAME:lane
  set-lanes U1CFG VALUE      lanes: set to VALUE in [ace2k_u1]
  drop-lanes U1CFG NAME      NAME's lanes out of lanes:; an empty head becomes -
  check-head ANSWER UNITS USED   one head's answer, normalised; exit 1 with the reason
  head-default TODAY UNITS USED  TODAY normalised when check-head takes it, else the next lane
                             no head takes (the units in order, lanes 1-4), or - when none is left
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

HEADS = 4
# The name an unnamed [ace2k] takes once a unit is added (install.sh: UNNAMED_RENAMED)
RENAMED = "a"
LANES = 4
# a unit's three sensors: each one's sensor: and the end of its section's name
SENSORS = ("ptc_left", "ptc_right", "chamber")
HEADER = re.compile(r"^\[([^\]]*)\]")
KEY = re.compile(r"^([A-Za-z0-9_]+)\s*[:=]\s*(.*?)\s*$")


class UsageError(Exception):
    """A bad command line or a file the tool cannot work with: main() exits 2 with it."""

    pass


def section_name(line: str) -> str | None:
    """The section a header line opens, as Klipper compares it (lower case, one space)."""
    m = HEADER.match(line)
    return " ".join(m.group(1).lower().split()) if m else None


def sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """(name, first line, end) per section; end is before the comments that open the next one."""
    heads = [(i, n) for i, line in enumerate(lines) if (n := section_name(line)) is not None]
    out = []
    for k, (i, name) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        while end > i + 1 and lines[end - 1].startswith("#"):
            end -= 1  # a comment right above the next header belongs to that header
        out.append((name, i, end))
    return out


def key(lines: list[str], start: int, end: int, name: str) -> tuple[int, str] | None:
    """(line, value) of an uncommented key in lines[start + 1:end]."""
    for i in range(start + 1, end):
        m = KEY.match(lines[i])
        if m and m.group(1).lower() == name:
            return i, re.sub(r"\s+[#;].*$", "", m.group(2))
    return None


def unit_of(name: str) -> str | None:
    """The unit a section name holds: '' for [ace2k], 'a' for [ace2k a], None otherwise."""
    if name == "ace2k":
        return ""
    return name[6:] if name.startswith("ace2k ") else None


def units(lines: list[str]) -> list[tuple[str, str, str]]:
    """(name, serial, uid) per [ace2k] or [ace2k NAME] section, in the file's order.

    The name is '' for [ace2k]; the serial is the serial: of the [mcu] its mcu: names (ace2k when
    it has none; '' when that section has no serial:); the uid is its uid: without spaces, colons
    or dashes, in lower case ('' when it has none).  install.sh's cfg_mcu_serials reads the same
    file another way, from the [mcu ace2k...] sections: a removal fetches no release, so it has no
    tool.
    """
    found = []
    secs = sections(lines)
    for name, s, e in secs:
        unit = unit_of(name)
        if unit is None:
            continue
        mcu = key(lines, s, e, "mcu")
        mcu_name = mcu[1].lower() if mcu else "ace2k"
        serial = ""
        for n2, s2, e2 in secs:
            if n2 == f"mcu {mcu_name}" and (k := key(lines, s2, e2, "serial")):
                serial = k[1]
        uid = key(lines, s, e, "uid")
        clean = re.sub(r"[\s:-]", "", uid[1]).lower() if uid else ""
        found.append((unit, serial, clean))
    return found


def objects(lines: list[str]) -> list[str]:
    """The unit sections' Klipper objects: Klipper keeps a section's own spelling ([ace2k A])."""
    out = []
    for name, s, _e in sections(lines):
        m = HEADER.match(lines[s])
        if m and unit_of(name) is not None:
            out.append(m.group(1))
    return out


def unit_block(name: str, serial: str) -> list[str]:
    """Unit NAME's [mcu ace2k_NAME] on SERIAL and its [ace2k NAME], each one then a blank line."""
    return [
        f"[mcu ace2k_{name}]",
        f"serial: {serial}",
        "baud: 250000",
        "restart_method: command",
        "",
        f"[ace2k {name}]",
        f"mcu: ace2k_{name}",
        "",
    ]


def sensor_block(name: str) -> list[str]:
    """Unit NAME's three temperature sensors, each section after a blank line."""
    out = []
    for sensor in SENSORS:
        out += [
            "",
            f"[temperature_sensor ace_{name}_{sensor}]",
            "sensor_type: ace2k",
            f"unit: {name}",
            f"sensor: {sensor}",
        ]
    return out


def first_sensor(lines: list[str]) -> int:
    """Where a unit's sections go: before the first sensor and the comments above it."""
    for name, s, _e in sections(lines):
        if name.startswith("temperature_sensor "):
            at = s
            while at > 0 and lines[at - 1].startswith("#"):
                at -= 1
            return at
    return len(lines)


# One line to put in a file: (index, line, insert).  An insert goes before lines[index]; else the
# line replaces lines[index].
Edit = tuple[int, str, bool]


def sensor_unit_insert(lines: list[str], s: int, e: int, name: str) -> Edit | None:
    """Where an ace2k sensor without unit: gets its unit: NAME line; None when it needs none."""
    st = key(lines, s, e, "sensor_type")
    if st and st[1].lower() == "ace2k" and key(lines, s, e, "unit") is None:
        return st[0] + 1, f"unit: {name}", True
    return None


def renamed(line: str, header: str) -> str:
    """A header line with its [...] replaced: what follows it, a comment, stays."""
    m = HEADER.match(line)
    return header + line[m.end() :] if m else header


def point_mcu(lines: list[str], s: int, e: int, name: str) -> Edit | None:
    """[ace2k]'s mcu: line once it is [ace2k NAME]: a missing one is inserted, an explicit
    mcu: ace2k is renamed in place; None when its mcu: names another mcu (the line stays)."""
    mcu = key(lines, s, e, "mcu")
    if mcu is None:
        return s + 1, f"mcu: ace2k_{name}", True
    line = lines[mcu[0]]
    new = re.sub(
        r"^(\s*mcu\s*[:=]\s*)ace2k(?=\s*([#;].*)?$)",
        lambda m: f"{m.group(1)}ace2k_{name}",
        line,
        flags=re.I,
    )
    return (mcu[0], new, False) if new != line else None  # an unchanged line keeps its end


def apply_edits(lines: list[str], edits: list[Edit | None]) -> list[str]:
    """A copy of lines with the edits made: the replacements, then the inserts from the last one
    up, so each index is the one it was found at.  None stands for no edit."""
    out = list(lines)
    for i, line, _insert in (x for x in edits if x and not x[2]):
        out[i] = line
    for i, line, _insert in sorted((x for x in edits if x and x[2]), reverse=True):
        out.insert(i, line)
    return out


def rename_sections(lines: list[str], name: str) -> list[Edit | None]:
    """The edits to the unnamed unit's sections and its sensors' sections (see rename)."""
    edits: list[Edit | None] = []
    for sec, s, e in sections(lines):
        if sec == "mcu ace2k":
            edits.append((s, renamed(lines[s], f"[mcu ace2k_{name}]"), False))
        elif sec == "ace2k":
            edits.append((s, renamed(lines[s], f"[ace2k {name}]"), False))
            edits.append(point_mcu(lines, s, e, name))
        elif sec.startswith("temperature_sensor "):
            edits.append(sensor_unit_insert(lines, s, e, name))
    return edits


def rename(lines: list[str], name: str) -> list[str]:
    """The unnamed unit as unit NAME: [mcu ace2k] and [ace2k] renamed, [ace2k NAME] pointed at
    its mcu, its sensors given unit: NAME, and sensor_mcu: ace2k (commented out or not) renamed.
    Every other line stays as it was."""
    edits = rename_sections(lines, name)
    for i, line in enumerate(lines):
        new = re.sub(
            r"^(#?\s*sensor_mcu\s*[:=]\s*)ace2k\s*$",
            lambda m: f"{m.group(1)}ace2k_{name}",
            line,
            flags=re.IGNORECASE,
        )
        if new != line:  # an unchanged line keeps its end
            edits.append((i, new, False))
    return apply_edits(lines, edits)


def add(lines: list[str], name: str, serial: str) -> list[str]:
    """Unit NAME on SERIAL added: its two sections before the first sensor, its sensors last."""
    at = first_sensor(lines)
    before = lines[:at]
    if before and before[-1] != "":
        before.append("")  # a blank line between the section above and the new unit
    out = strip_trailing_blanks(before + unit_block(name, serial) + lines[at:])
    return out + sensor_block(name)


def owned_by(lines: list[str], sec: str, s: int, e: int, name: str) -> bool:
    """True when the section is unit NAME's: its two sections, or a sensor naming it."""
    if not sec.startswith("temperature_sensor "):
        return sec in (f"mcu ace2k_{name}", f"ace2k {name}")
    u = key(lines, s, e, "unit")
    m = key(lines, s, e, "sensor_mcu")
    return bool((u and u[1].lower() == name) or (m and m[1].lower() == f"ace2k_{name}"))


def strip_trailing_blanks(lines: list[str]) -> list[str]:
    """A copy of lines without the blank lines at its end."""
    out = list(lines)
    while out and out[-1] == "":
        out.pop()
    return out


def owned_end(lines: list[str], s: int, e: int) -> int:
    """The end of a section's own lines: through its last key and the comments right after it.

    A comment block after a blank line is free-standing: it stays when the section goes.
    """
    last = s
    for i in range(s + 1, e):
        if lines[i].strip() and not lines[i].startswith(("#", ";")):
            last = i
    while last + 1 < e and lines[last + 1].startswith(("#", ";")):
        last += 1
    return last + 1


def drop(lines: list[str], name: str) -> list[str]:
    """Unit NAME's sections and its sensors taken out, each with the blank line that separated
    it; free-standing comment blocks stay.  A file without the unit comes back unchanged.
    """
    gone: set[int] = set()
    for sec, s, e in sections(lines):
        if owned_by(lines, sec, s, e, name):
            end = owned_end(lines, s, e)
            gone.update(range(s, end))
            if s > 0 and lines[s - 1] == "":
                gone.add(s - 1)  # the blank line that separated it
            elif end < len(lines) and lines[end] == "":
                gone.add(end)
    return strip_trailing_blanks([line for i, line in enumerate(lines) if i not in gone])


def set_uid(lines: list[str], name: str, uid: str) -> list[str]:
    """uid: UID added after the mcu: of unit NAME's section ('' for [ace2k]), or after its header
    when it has no mcu:.  A section that has a uid: already, or no such section, leaves the file
    unchanged.
    """
    want = "ace2k" if name == "" else f"ace2k {name}"
    for sec, s, e in sections(lines):
        if sec == want and key(lines, s, e, "uid") is None:
            mcu = key(lines, s, e, "mcu")
            at = mcu[0] + 1 if mcu else s + 1
            return lines[:at] + [f"uid: {uid}"] + lines[at:]
    return lines


def render(lines: list[str], pairs: list[tuple[str, str]]) -> list[str]:
    """The release's template with the units of pairs ((name, serial), the first one first): the
    template's unit takes the first pair's serial: and name, the others are added, and
    [temperature_sensor ace_mcu] is commented out.  pairs has at least one pair.
    """
    first, serial = pairs[0]
    out = []
    sec = None
    for line in lines:
        sec = section_name(line) or sec
        if sec == "mcu ace2k" and KEY.match(line) and line.lower().startswith("serial"):
            out.append(f"serial: {serial}")
        else:
            out.append(line)
    out = rename(out, first)
    out = comment_mcu_sensor(out)
    for name, ser in pairs[1:]:
        out = add(out, name, ser)
    return out


def comment_mcu_sensor(lines: list[str]) -> list[str]:
    """Comment out [temperature_sensor ace_mcu]: the U1's Klipper is older than the image's."""
    out = list(lines)
    for sec, s, e in sections(out):
        if sec == "temperature_sensor ace_mcu":
            for i in range(s, e):
                if out[i] and not out[i].startswith("#"):
                    out[i] = "#" + out[i]
    return out


def lanes_span(lines: list[str]) -> tuple[int, int, int] | None:
    """(header, first, end) of the uncommented lanes: in [ace2k_u1], or (header, -1, -1).

    The value ends where Klipper's parser ends it: a blank line or a comment line does not end it
    while an indented line follows, and end is past the last indented line.
    """
    for sec, s, e in sections(lines):
        if sec != "ace2k_u1":
            continue
        for i in range(s + 1, e):
            m = KEY.match(lines[i])
            if m and m.group(1).lower() == "lanes":
                return s, i, value_end(lines, i + 1, e)
        return s, -1, -1
    return None


def value_end(lines: list[str], j: int, e: int) -> int:
    """The index past the last indented line of the value whose next line is j, before e."""
    end = j
    for k in range(j, e):
        text = lines[k].strip()
        if text and lines[k][:1] in (" ", "\t"):
            end = k + 1
        elif text and not text.startswith(("#", ";")):
            break
    return end


def lanes_value(lines: list[str]) -> str | None:
    """[ace2k_u1]'s lanes: value with its continuation lines joined and its # and ; comments
    stripped; None when there is no [ace2k_u1] or no uncommented lanes: in it.
    """
    span = lanes_span(lines)
    if span is None or span[1] < 0:
        return None
    text = " ".join(re.sub(r"[#;].*$", "", ln).strip() for ln in lines[span[1] : span[2]])
    m = KEY.match(text.strip())
    return m.group(2) if m else ""


def bare_heads(entries: list[str], name: str) -> list[str]:
    """The four heads of a bare lanes: (1+2, 3, 4: lane n feeds the head of its first lane), each
    written NAME:lane.  Raises UsageError for an entry that is not lanes 1-4 joined by +."""
    top = min(LANES, HEADS)
    out = ["-"] * HEADS
    for entry in entries:
        members = entry.split("+")
        if not all(m.isascii() and m.isdigit() and 1 <= int(m) <= top for m in members):
            raise UsageError(f"lanes: {entry} is not a lane 1-{top}")
        lanes = [int(m) for m in members]
        out[lanes[0] - 1] = "+".join(f"{name}:{n}" for n in lanes)
    return out


def heads(lines: list[str], name: str) -> list[str]:
    """The four heads' entries of [ace2k_u1]'s lanes: (1, 2, 3, 4 when it has none), in the named
    form; a bare lane is NAME's.  Raises UsageError for a bare lane outside 1-4."""
    value = lanes_value(lines)
    entries = [x.strip().lower() for x in (value or "1, 2, 3, 4").split(",") if x.strip()]
    if any(":" in x for x in entries):
        return (entries + ["-"] * HEADS)[:HEADS]
    return bare_heads(entries, name)


def set_lanes(lines: list[str], value: str) -> list[str]:
    """lanes: VALUE right after [ace2k_u1]'s header, in place of its lanes: and continuation
    lines; a file without [ace2k_u1] gains one at its end.
    """
    span = lanes_span(lines)
    if span is None:
        return [*lines, "", "[ace2k_u1]", f"lanes: {value}"]
    s, first, end = span
    out = lines[:first] + lines[end:] if first >= 0 else list(lines)
    return out[: s + 1] + [f"lanes: {value}"] + out[s + 1 :]


def drop_lanes(lines: list[str], name: str) -> list[str]:
    """Unit NAME's lanes out of every head's entry; a head left with no lane becomes - (the
    U1's).  Raises UsageError as heads does.
    """
    kept = []
    for entry in heads(lines, name):
        members = [m for m in entry.split("+") if not m.startswith(f"{name}:")]
        kept.append("+".join(members) if members and entry != "-" else "-")
    return set_lanes(lines, ", ".join(kept))


def check_member(member: str, names: list[str], used: list[str], seen: list[str]) -> str:
    """One lane of a head's answer, as unit:lane with no leading zero.  Raises ValueError with
    the reason when it names no unit, a unit not in names, a lane outside 1-LANES, a lane in used
    (another head's) or in seen (this head's).
    """
    unit, colon, lane = member.rpartition(":")
    if not colon or not unit:
        raise ValueError(f"write the unit: {names[0]}:{lane or 1}")
    if unit not in names:
        raise ValueError(f"no unit {unit}; units: {', '.join(names)}")
    if not (lane.isascii() and lane.isdigit()) or not 1 <= int(lane) <= LANES:
        raise ValueError(f"lane {lane} is not 1-{LANES}")
    ref = f"{unit}:{int(lane)}"
    if ref in used:
        raise ValueError(f"{ref} already feeds another head")
    if ref in seen:
        raise ValueError(f"{ref} twice")
    return ref


def check_head(answer: str, names: list[str], used: list[str]) -> str:
    """One head's answer normalised: - (the U1's), or its lanes joined by + (see check_member).
    Case and spaces do not count.  Raises ValueError with the reason when it is empty or a lane is
    refused.
    """
    text = "".join(answer.lower().split())
    if not text:
        raise ValueError("empty: give a lane, as a:1, or - for the U1")
    if text == "-":
        return "-"
    seen: list[str] = []
    for member in text.split("+"):
        seen.append(check_member(member, names, used, seen))
    return "+".join(seen)


def suggest(names: list[str], used: list[str]) -> str:
    """The first lane of the units in order, lanes 1-LANES, that used does not hold; - when none
    is left.
    """
    for unit in names:
        for n in range(1, LANES + 1):
            if f"{unit}:{n}" not in used:
                return f"{unit}:{n}"
    return "-"


def head_default(today: str, names: list[str], used: list[str]) -> str:
    """A head's offer: today's entry while it is still taken (no unit gone, no lane taken)."""
    if today:
        try:
            return check_head(today, names, used)
        except ValueError:
            pass
    return suggest(names, used)


class Line(str):
    """A line read from a file, with the end it had there ('' for a last line without one)."""

    nl = "\n"

    def __new__(cls, text: str, nl: str) -> Line:
        """The line's text, without its end, and that end."""
        line = super().__new__(cls, text)
        line.nl = nl
        return line


@dataclass(frozen=True)
class Style:
    """How most lines of a file end (newline), and whether its last line has an end (final).

    write() gives each Line read its own end back, so an edit leaves every other byte of the file
    as it was; a new or changed line (a plain str) takes the file's commoner end.
    """

    newline: str = "\n"
    final: bool = True


def read(path: str) -> tuple[list[str], Style]:
    """The file's lines, each a Line with its own end, and the file's Style.

    Raises OSError when the file cannot be read, UnicodeDecodeError when it is not UTF-8.
    """
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    crlf = text.count("\r\n")
    style = Style(
        newline="\r\n" if crlf > text.count("\n") - crlf else "\n",
        final=text == "" or text.endswith("\n"),
    )
    parts = text.split("\n")
    last = parts.pop()  # '' after a final end
    lines: list[str] = [Line(p[:-1], "\r\n") if p.endswith("\r") else Line(p, "\n") for p in parts]
    if last:
        lines.append(Line(last, ""))
    return lines, style


def write(lines: list[str], style: Style) -> None:
    """The lines on stdout, as UTF-8, each with its own end or the style's (see Style)."""
    ends = [getattr(line, "nl", "") or style.newline for line in lines]
    if lines and not style.final:
        ends[-1] = ""
    text = "".join(line + end for line, end in zip(lines, ends))
    sys.stdout.buffer.write(text.encode("utf-8"))


def edit(path: str, change: Callable[[list[str]], list[str]]) -> None:
    """The file at path, changed by change, on stdout in the file's own style."""
    lines, style = read(path)
    write(change(lines), style)


def lines_of(path: str) -> list[str]:
    """The file's lines (see read), for a command that only reads it."""
    return read(path)[0]


def split(text: str) -> list[str]:
    """A comma-separated list as its items, without empty ones."""
    return [x for x in text.split(",") if x]


def cmd_units(a: list[str]) -> None:
    """units: one tab-separated line per unit on stdout."""
    for u in units(lines_of(a[0])):
        print("\t".join(u))


def cmd_render(a: list[str]) -> None:
    """render: the template rendered with each NAME=SERIAL.  Raises UsageError for an argument
    that is not NAME=SERIAL with both halves.
    """
    pairs = []
    for item in a[1:]:
        name, eq, ser = item.partition("=")
        if not eq or not name or not ser:
            raise UsageError(f"render wants NAME=SERIAL, not {item}")
        pairs.append((name, ser))
    edit(a[0], lambda lines: render(lines, pairs))


def cmd_check_head(a: list[str]) -> int:
    """check-head: the normalised answer on stdout and 0, or the reason on stderr and 1."""
    try:
        print(check_head(a[0], split(a[1]), split(a[2])))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


# command -> (fewest arguments, most arguments or None for no limit, function); the function's
# result is the exit status, None for 0
COMMANDS: dict[str, tuple[int, int | None, Callable[[list[str]], int | None]]] = {
    "units": (1, 1, cmd_units),
    "objects": (1, 1, lambda a: print("\n".join(objects(lines_of(a[0]))))),
    "render": (2, None, cmd_render),
    "rename-a": (1, 1, lambda a: edit(a[0], lambda lines: rename(lines, RENAMED))),
    "add": (3, 3, lambda a: edit(a[0], lambda lines: add(lines, a[1], a[2]))),
    "drop": (2, 2, lambda a: edit(a[0], lambda lines: drop(lines, a[1]))),
    "uid": (3, 3, lambda a: edit(a[0], lambda lines: set_uid(lines, a[1], a[2]))),
    "heads": (2, 2, lambda a: print("\n".join(heads(lines_of(a[0]), a[1])))),
    "set-lanes": (2, 2, lambda a: edit(a[0], lambda lines: set_lanes(lines, a[1]))),
    "drop-lanes": (2, 2, lambda a: edit(a[0], lambda lines: drop_lanes(lines, a[1]))),
    "check-head": (3, 3, cmd_check_head),
    "head-default": (3, 3, lambda a: print(head_default(a[0], split(a[1]), split(a[2])))),
}


def run(argv: list[str]) -> int:
    """Run one command line (COMMAND ARGS); return its exit status.  Raises UsageError for an
    unknown command or a wrong number of arguments, and lets a command's UsageError through.
    """
    if not argv or argv[0] not in COMMANDS:
        raise UsageError(f"bad command line: {' '.join(argv)}")
    fewest, most, fn = COMMANDS[argv[0]]
    rest = argv[1:]
    if len(rest) < fewest or (most is not None and len(rest) > most):
        raise UsageError(f"bad command line: {' '.join(argv)}")
    return fn(rest) or 0


def main(argv: list[str]) -> int:
    """Run one command line; return the exit status: 0 done, 1 an answer refused (check-head),
    2 a bad command line or file (the reason on stderr).  A file that cannot be read raises
    OSError (Python then exits 1 with a traceback).
    """
    try:
        return run(argv)
    except UsageError as e:
        print(f"ace2k_cfg: {e} (see the docstring)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
