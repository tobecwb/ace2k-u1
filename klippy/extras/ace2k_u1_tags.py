"""Turns the tag of a spool into the filament setting of a head of the U1.

It translates a tag record of ace2k, and it holds the policy of each lane: when to write the
setting and when to clear it. TagWriter queues the writes and runs each one as a G-code from a
timer. TagReader watches the tag of a gripped or waiting spool from another timer: the unit
reads a tag only while its lane moves, so it asks for no read. Both use the reactor of Klipper.
ace2k_u1.py creates them and runs the decisions.
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Collection, Container, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Union, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import GCode, Reactor
    from .ace2k_u1_common import Hooked, LaneKey, Slot
    from .ace2k_u1_printer_types import (
        FilamentParameters,
        LaneView,
        PrintStats,
        PrintTaskConfig,
        Status,
        TagRecord,
        TagView,
        ToolHead,
    )

try:
    from . import ace2k_u1_plan as plan_module
except ImportError:  # the host tests put klippy/extras on the path and import by bare name
    import ace2k_u1_plan as plan_module  # type: ignore[import-not-found, no-redef]
try:
    from . import ace2k_u1_hooks as hooks_module
    from . import ace2k_u1_in_use as in_use_module
except ImportError:  # same as above, for the host tests
    import ace2k_u1_hooks as hooks_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_in_use as in_use_module  # type: ignore[import-not-found, no-redef]
try:
    from .ace2k_u1_common import setting_written
    from .ace2k_u1_printer import U1Printer
except ImportError:  # same as above, for the host tests
    from ace2k_u1_common import setting_written  # type: ignore[import-not-found, no-redef]
    from ace2k_u1_printer import U1Printer  # type: ignore[import-not-found, no-redef]

SAFE = re.compile(r"[^A-Za-z0-9_+-]")  # vendor: no spaces
SAFE_SUBTYPE = re.compile(r"[^A-Za-z0-9 _+-]")  # subtype: spaces kept, never a quote
BOUNDARY = " +-_"  # what may follow a type inside a material (PLA+, PETG-HF, PLA Matte)
_NO_UNDO = object()
READ_POLL_S = 0.25  # how often the tag of a watched lane is checked
# A watched lane whose tag is not "read" after READ_DEADLINE_S gets the console line. The unit
# settles the tag state shortly after a load ends, so a read on the grip's move comes by then.
READ_DEADLINE_S = 5.0

# The tags work on the filament setting of each head of the U1 (print_task_config).
TAG_COMMAND = "SET_PRINT_FILAMENT_CONFIG"
TAG_PARAMS = (
    "CONFIG_EXTRUDER",
    "VENDOR",
    "FILAMENT_TYPE",
    "FILAMENT_SUBTYPE",
    "FILAMENT_COLOR_RGBA",
    "FORCE",
)
# The setting of the head in the status of print_task_config. It has one list entry per head.
TAG_STATUS = {
    key: hooks_module.PRINT_TASK_KEYS[key] for key in ("vendor", "type", "subtype", "rgba")
}
TAG_CLEAR = "VENDOR=NONE FILAMENT_TYPE=NONE FILAMENT_SUBTYPE=NONE FILAMENT_COLOR_RGBA=FFFFFFFF"
TAG_HEADS = 4  # the range of CONFIG_EXTRUDER on the U1, 0..3
TAG_TRIPLE = ("VENDOR", "FILAMENT_TYPE", "FILAMENT_SUBTYPE")  # the U1 takes these together
TAG_QUOTED = ("FILAMENT_SUBTYPE",)  # sent in single quotes on a write, as subtypes carry spaces
HEX8 = re.compile(r"[0-9A-Fa-f]{8}")
# What breaks a G-code value. The U1 cuts a line at ';'. Its parameter parser stops at '#', '*' or
# ';', with quotes or without. A value without quotes also ends at a blank.
UNSAFE = re.compile(r"[\s'\"#*;]")
UNSAFE_QUOTED = re.compile(r"['\"#*;]")
# A key of filament_parameters is <vendor>_<type>_<subtype>_<key>. A type is in upper case (PLA,
# PETG-CF, PA6-GF). That tells it from the other entries of the table (hard_filaments_max_flow_k).
TYPE_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9+-]*")
KEY_PARTS = 4  # <vendor>_<type>_<subtype>_<key>


class TagsAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as the writes and reads of the tags use it."""

    print_stats: PrintStats | None  # set at setup_tags
    own_writes: int  # the adapter's own writes of a head's setting going on now

    @property
    def reactor(self) -> Reactor: ...
    @property
    def hooked(self) -> list[Hooked]: ...
    @property
    def by_key(self) -> dict[LaneKey, Hooked]: ...
    @property
    def gcode(self) -> GCode | None: ...
    def say(self, msg: str) -> None: ...
    def print_state(self) -> str | None: ...
    def head_of_extruder(self, name: str) -> int | None: ...
    def in_print(self) -> bool: ...
    def lane_text(self, h: Hooked) -> str: ...
    def key_text(self, key: LaneKey) -> str: ...
    def head_order(self, key: LaneKey) -> float: ...
    def parked(self, h: Hooked) -> bool: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    def lane_status(self, h: Hooked, eventtime: float | None = None) -> LaneView: ...


def tag_identity(tag: TagView | None) -> tuple[object, object]:
    """Returns the tag of a lane as (state, uid).

    That pair tells a fresh read from the record that was held before.
    """
    tag = tag or {}
    return (tag.get("state"), tag.get("uid"))


@dataclass(frozen=True)
class Setting:
    """The filament setting of a head.

    A type of None means that the U1 does not know the material, so only the colour is written.
    reason says why. An rgba of None means that no colour is written.
    """

    vendor: str
    type: str | None
    subtype: str
    rgba: str | None
    reason: str | None = None


# An entry of the catalogue in the filament table of the U1: (vendor, type, subtype).
Entry = tuple[str, str, str]
# The setting of the head, as the policy compares it. It is a Setting, or vendor, type, subtype and
# rgba as the status of the U1 gives them (TagWriter.head_setting). None means it is not known.
Current = Union[Setting, Mapping[str, str], None]
# The action of a lane in one tick: ("write" | "skip" | "clear", the head, the Setting).
Action = tuple[str, int, Setting]


def _clean(text: str | None) -> str:
    return SAFE.sub("", text or "")


def _clean_subtype(text: str | None) -> str:
    return " ".join(SAFE_SUBTYPE.sub("", text or "").split())


def _match_type(material: str, known_types: Collection[str]) -> str | None:
    """Returns the U1 type that a material names, or None.

    An exact match wins. Otherwise the result is the longest known type that is followed by a
    boundary character. For example PLA+ gives PLA and PETG-HF gives PETG. PCTG and PAHT match
    nothing.
    """
    if material in known_types:
        return material
    return next(
        (
            t
            for t in sorted(known_types, key=len, reverse=True)
            if len(material) > len(t) and material.startswith(t) and material[len(t)] in BOUNDARY
        ),
        None,
    )


def _tail(text: str, ftype: str) -> str:
    """Returns what follows the type in text, with boundary characters stripped.

    For example PETG-HF gives HF and PLA+ gives "".
    """
    return text[len(ftype) :].strip(BOUNDARY)


def _rgba(value: object) -> str | None:
    """Returns RRGGBBAA for the colour of a tag, in upper case, or None.

    A colour written as RRGGBB gets FF as alpha. This uses the reader of the plan, but a # is not
    part of the colour of a tag.
    """
    parsed = plan_module.parse_colour(value, hash_ok=False)
    return None if parsed is None else (parsed[0] + (parsed[1] or "ff")).upper()


def translate(record: TagRecord, known_types: Collection[str]) -> Setting:
    """Translates a tag record (brand, material, name, color_rgba) into a Setting of the U1.

    The subtype is made of the words of the name that come after the type. For example PLA Full
    Spectrum gives Full Spectrum, and PETG-HF Fast gives HF Fast. With no name, the subtype is
    what follows the type in the material. For example PETG-HF gives HF, and PLA+ gives nothing.
    A name, when there is one, always wins.
    """
    vendor = _clean(record.get("brand")) or "Generic"
    material = (record.get("material") or "").strip().upper()
    ftype = _match_type(material, known_types)
    if ftype:
        reason = None
    elif not material:
        reason = "the tag gave no material"
        if record.get("format") == "uid":
            reason += " (read by its UID alone)"
    else:
        reason = f"material {record.get('material')!r} unknown to the U1"
    subtype = ""
    if ftype:
        words = (record.get("name") or "").split()
        if words:
            first = words[0].upper()
            if _match_type(first, {ftype}):  # this is the type word, so keep only its tail
                words[0] = _tail(words[0], ftype)
            elif first in known_types and ftype.startswith(first):  # a shorter form of the type
                words = words[1:]
            subtype = _clean_subtype(" ".join(words))
        else:
            subtype = _clean_subtype(_tail(material, ftype))
    return Setting(vendor, ftype, subtype, _rgba(record.get("color_rgba")), reason)


def catalogued(setting: Setting, names: Iterable[Entry]) -> Setting:
    """Returns setting as an entry that the catalogue of the U1 lists.

    names holds the (vendor, type, subtype) entries of the catalogue. They are compared without
    case, and the result is written in the spelling of the catalogue.
    - If the entry is listed, the result has the vendor and subtype of the entry.
    - Otherwise the vendor is Generic. The subtype is the one of a generic entry of the type,
      if the catalogue lists one, or else none.
    The U1 finds a temperature for any name, because it falls back to its generic entries. But
    its screen shows a vendor or subtype that it does not list as it is. A setting with no type is
    left alone.
    """
    if setting.type is None:
        return setting
    # Sorted, so that of two entries that differ only in case, the same one wins at every start.
    folded: dict[Entry, tuple[str, str]] = {}
    for v, t, s in sorted(names, reverse=True):
        folded[(v.casefold(), t.casefold(), s.casefold())] = (v, s)
    ftype, sub = setting.type.casefold(), setting.subtype.casefold() or "generic"
    entry = folded.get((setting.vendor.casefold(), ftype, sub))
    if entry is not None:
        vendor, subtype = entry
        subtype = "" if not setting.subtype else subtype
        return Setting(vendor, setting.type, subtype, setting.rgba, setting.reason)
    generic = folded.get(("generic", ftype, sub))
    subtype = generic[1] if generic is not None and setting.subtype else ""
    return Setting("Generic", setting.type, subtype, setting.rgba, setting.reason)


def _written(setting: Setting) -> dict[str, str]:
    """Returns the fields that a write of setting puts on the head.

    Vendor, type and subtype are written only with a type. The colour is written only when the
    setting has one.
    """
    fields: dict[str, str] = {}
    if setting.type is not None:
        fields.update(vendor=setting.vendor, type=setting.type, subtype=setting.subtype)
    if setting.rgba is not None:
        fields["rgba"] = setting.rgba
    return fields


def _field(current: Current, name: str) -> str:
    value = current.get(name) if isinstance(current, dict) else getattr(current, name, None)
    return "" if value is None else str(value).upper()


def _unchanged(current: Current, setting: Setting) -> bool:
    """Tells whether the head still shows what was written.

    The comparison ignores case and looks at the written fields only.
    """
    if current is None:
        return False
    return all(_field(current, k) == str(v).upper() for k, v in _written(setting).items())


def _shows(current: Current, setting: Setting, names: Iterable[str]) -> bool:
    return all(_field(current, k) == str(getattr(setting, k)).upper() for k in names)


def _union(old: Setting | None, new: Setting, current: Current) -> Setting:
    """Returns what the tag of the lane has put on the head after new is written over old.

    The result has the fields of new. It also keeps the vendor, type and subtype of old, or the
    colour of old, where new carries none and the head still shows the values of old. A field
    that was changed by hand since then is no longer the field of the tag.
    """
    if old is None:
        return new
    keep_triple = (
        new.type is None
        and old.type is not None
        and _shows(current, old, ("vendor", "type", "subtype"))
    )
    keep_rgba = new.rgba is None and old.rgba is not None and _shows(current, old, ("rgba",))
    if not (keep_triple or keep_rgba):
        return new
    src = old if keep_triple else new
    return Setting(src.vendor, src.type, src.subtype, old.rgba if keep_rgba else new.rgba)


@dataclass(frozen=True)
class LaneTick:
    """What one poll tells TagPolicy.on_tick about a lane.

    - insert: the bay of the lane reads a filament.
    - tag_state and record: the state and the record of its tag.
    - printing: the U1 is printing or paused. A clear waits then.
    - current: the setting of the head now, as a Setting or a mapping with vendor, type, subtype
      and rgba, or None.
    - hold: a write for this head waits. The caller decides this: it is the active extruder while
      printing and not paused.
    - parked: the lane is not in its head, which several lanes feed. There is no action then.
    """

    insert: bool
    tag_state: str | None
    record: TagRecord | None
    printing: bool
    current: Current
    hold: bool = False
    parked: bool = False


class _Lane:
    def __init__(self) -> None:
        self.inserted = False
        self.applied_uid: object = None  # the uid that was last acted on since the insert rose
        self.applied = False
        # What the tag of this lane has put on the head since the last clear. It is the union of
        # the fields of its writes (a Setting), or None for nothing.
        self.last_written: Setting | None = None
        self.clear_pending = False
        # The value of last_written before the latest write or clear, for note_failed. _NO_UNDO
        # means there is none.
        self.undo: object = _NO_UNDO


class TagPolicy:
    """Decides, for each lane, when to write the setting of the head from the tag.

    It also decides when to clear the setting and when to wait.

    heads maps a hooked lane to its head on the U1 (the CONFIG_EXTRUDER index). known_types are
    the filament types of the table of the U1. on_tick is called once for each lane in each poll.
    It returns at most one action, which is already counted as done.

    - ("write", head, Setting): a tag was read with a uid that was not acted on since the insert
      rose. With hold set, the head is the active extruder of a running print. The write then
      waits and nothing is recorded. A later tick without hold writes what the lane holds then,
      if it is still inserted. A re-read of the same uid within one insert does not write again,
      so a manual change stays until the spool is reseated. The exception is a lane whose tag
      left "read" in between (a forget puts it back to pending). Then the next read writes
      again, with the same uid too.
    - ("skip", head, Setting): the same as a write, but nothing can be written (no type and no
      colour). The reason of the Setting is the console line.
    - ("clear", head, Setting): the insert has fallen, nothing is printing, and the head still
      shows the last write of the lane, which is the Setting. The comparison ignores case and
      uses the fields that the write carried. The caller resets only those fields.

    If the G-code of the caller for a write or clear fails, the caller calls note_failed(lane)
    before the next on_tick of the lane. It undoes the setting of the head that was recorded.
    The attempt itself stays recorded, so it is not retried.

    A lane that is parked outside a head that several lanes feed (it is not the current lane of
    the head) never acts. The setting of the head speaks for the lane that is in it. A parked
    lane keeps no write of its own on the head, so there is none to clear later. It also has none
    acted on. So on the tick when it becomes the lane of the head again, its tag is written.
    """

    def __init__(
        self,
        heads: Mapping[LaneKey, int],
        known_types: Iterable[str],
        names: Iterable[Entry] | None = None,
    ) -> None:
        self.heads = dict(heads)
        self.known_types = set(known_types)
        # For catalogued(). None means that the setting is as the tag says.
        self.names = None if names is None else set(names)
        self.lanes = {lane: _Lane() for lane in self.heads}

    def on_tick(self, lane: LaneKey, tick: LaneTick) -> Action | None:
        """Returns the action of the lane for this poll, or None. tick is a LaneTick."""
        st = self.lanes.get(lane)
        if st is None:
            return None
        head = self.heads[lane]
        st.undo = _NO_UNDO  # note_failed applies to the action of this tick only
        if tick.parked:
            self._reset_parked(st, tick.insert)
            return None
        self._track_insert(st, tick.insert)
        if tick.insert:
            return self._decide_write(lane, tick)
        return self._decide_clear(st, head, tick.current, tick.printing)

    def _reset_parked(self, st: _Lane, insert: bool) -> None:
        """Resets a parked lane. It keeps no write of its own and none acted on."""
        st.inserted = bool(insert)
        st.applied, st.applied_uid = False, None
        st.last_written, st.clear_pending = None, False

    def _track_insert(self, st: _Lane, insert: bool) -> None:
        """Follows the edge of the insert.

        A new spool arms the write again and drops a pending clear. A spool that is taken out and
        had written something owes a clear.
        """
        if insert and not st.inserted:  # a new spool: any uid writes, and a pending clear is moot
            st.applied, st.applied_uid, st.clear_pending = False, None, False
        elif not insert and st.inserted and st.last_written is not None:
            st.clear_pending = True
        st.inserted = bool(insert)

    def _decide_write(self, lane: LaneKey, tick: LaneTick) -> Action | None:
        """Returns the write (or skip) that a read tag of an inserted lane calls for, or None."""
        st, head = self.lanes[lane], self.heads[lane]
        tag_state, record = tick.tag_state, tick.record
        # A tag that was forgotten (pending) or is in the middle of a read: the next read writes
        # again. A missing or unknown tag state arms nothing.
        if tag_state in ("pending", "searching", "reading", "no_tag"):
            st.applied, st.applied_uid = False, None
        if tag_state != "read" or not record:
            return None
        uid = record.get("uid")
        if st.applied and uid == st.applied_uid:
            return None
        setting = translate(record, self.known_types)
        if self.names is not None:
            setting = catalogued(setting, self.names)
        if tick.hold and _written(setting):
            return None
        st.applied, st.applied_uid = True, uid
        if not _written(setting):
            return ("skip", head, setting)
        st.undo, st.last_written = st.last_written, _union(st.last_written, setting, tick.current)
        return ("write", head, setting)

    def _decide_clear(
        self, st: _Lane, head: int, current: Current, printing: bool
    ) -> Action | None:
        """Returns the clear that the lane of a removed spool owes, or None.

        The clear is owed once nothing is printing.
        """
        if not st.clear_pending or printing:
            return None
        st.clear_pending = False
        # A pending clear always follows a write, so there is one to clear.
        written = cast("Setting", st.last_written)
        st.last_written = None
        if not _unchanged(current, written):
            return None  # it was changed by hand since then, so it is left alone
        st.undo = written
        return ("clear", head, written)

    def note_failed(self, lane: LaneKey) -> None:
        """Records that the G-code of the last action of the lane failed.

        It restores the recorded setting of the head. The head keeps what it had.
        """
        st = self.lanes.get(lane)
        if st is not None and st.undo is not _NO_UNDO:
            st.last_written, st.undo = cast("Setting | None", st.undo), _NO_UNDO


class TagWriter:
    """Writes the tags to the heads.

    At setup it checks the command and the status of the U1. In each tick it takes the decision
    for each lane, and it queues the G-code for the tag timer. The Adapter creates one.
    """

    def __init__(self, adapter: TagsAdapter) -> None:
        self.adapter = adapter
        # The tags are off until setup_tags. tags_off says why.
        self.tags: TagPolicy | None = None
        self.tags_off: str | None = "not set up"
        self.ptc: PrintTaskConfig | None = None  # the print_task_config of the U1
        # The toolhead. The writes for its active extruder wait while printing.
        self.toolhead: ToolHead | None = None
        self.tag_timer: object = None
        # Maps a lane key to the action that the tag timer still has to run.
        self.tag_pending: dict[LaneKey, Action] = {}
        # The lane keys whose G-code is being run. The timer may be entered again meanwhile.
        self.tag_running: set[LaneKey] = set()
        # Maps a lane key to what the tag last did to the head, for ACEK_ADAPTER_STATUS.
        self.applied: dict[LaneKey, str | None] = {}

    @staticmethod
    def table_entries(fparams: FilamentParameters) -> set[Entry]:
        """Returns the (vendor, type, subtype) of every key in the tables of filament_parameters.

        The tables are its status and the dicts that hold them. The keys are written as
        <vendor>_<type>_<subtype>_<key name>.
        """
        entries: set[Entry] = set()
        for table in U1Printer.filament_tables(fparams):
            for key in table if isinstance(table, dict) else ():
                parts = str(key).split("_")
                if len(parts) >= KEY_PARTS and TYPE_TOKEN.fullmatch(parts[1]):
                    entries.add((parts[0], parts[1], parts[2]))
        return entries

    @staticmethod
    def known_types(
        fparams: FilamentParameters | None, entries: set[Entry] | None = None
    ) -> set[str]:
        """Returns the types of the filament table of the U1.

        They are the distinct <type> parts of its keys. They are taken from entries when
        table_entries already read them.
        """
        return {
            ftype
            for _, ftype, _ in (
                TagWriter.table_entries(cast("FilamentParameters", fparams))
                if entries is None
                else entries
            )
        }

    @staticmethod
    def check_tags(ptc: PrintTaskConfig) -> list[str]:
        """Checks the print_task_config object (ptc) of the U1.

        Returns [] when it takes SET_PRINT_FILAMENT_CONFIG with every parameter that the tags
        write, and reports the four fields for each head that they compare. Otherwise it returns
        one reason for each difference.
        """
        method = getattr(ptc, "cmd_" + TAG_COMMAND, None)
        if not callable(method):
            return [f"print_task_config has no {TAG_COMMAND}"]
        try:
            source = inspect.getsource(method)
        except (OSError, TypeError):
            return [f"{TAG_COMMAND}'s source unreadable"]
        problems = [
            f"{TAG_COMMAND} does not read {name}"
            for name in TAG_PARAMS
            if f"'{name}'" not in source and f'"{name}"' not in source
        ]
        try:
            status = ptc.get_status(0)
        except Exception as err:
            return problems + [f"print_task_config status: {err}"]
        problems += [
            f"print_task_config status has no list {key}"
            for key in TAG_STATUS.values()
            if not isinstance(status.get(key), list)
        ]
        return problems

    @staticmethod
    def tag_params(head: int, setting: Setting) -> dict[str, str]:
        """Returns the parameters for the write of a Setting.

        Vendor, type and subtype are included only with a type. The colour is included only when
        the setting has one.
        """
        params = {"CONFIG_EXTRUDER": str(head)}
        if setting.type is not None:
            params.update(
                VENDOR=setting.vendor, FILAMENT_TYPE=setting.type, FILAMENT_SUBTYPE=setting.subtype
            )
        if setting.rgba is not None:
            params["FILAMENT_COLOR_RGBA"] = setting.rgba
        params["FORCE"] = "1"
        return params

    @staticmethod
    def clear_params(head: int, written: Setting | None = None) -> dict[str, str]:
        """Returns the parameters that reset what a write put on the head.

        Vendor, type and subtype are reset only if the write carried a type. The colour is reset
        only if the write carried one. With written set to None, everything is reset.
        """
        reset = dict(w.split("=", 1) for w in TAG_CLEAR.split())
        if written is not None:
            if written.type is None:
                for key in TAG_TRIPLE:
                    del reset[key]
            if written.rgba is None:
                del reset["FILAMENT_COLOR_RGBA"]
        params = {"CONFIG_EXTRUDER": str(head)}
        params.update(reset)
        params["FORCE"] = "1"
        return params

    @staticmethod
    def check_params(params: Mapping[str, str], quoted: Container[str] = ()) -> list[str]:
        """Checks the parameters before they are sent.

        It finds what SET_PRINT_FILAMENT_CONFIG of the U1 would refuse, and what would not
        survive the G-code parser. Returns one reason for each problem, or [] when the parameters
        can be sent.
        """
        problems: list[str] = []
        head = params.get("CONFIG_EXTRUDER", "")
        if not (head.isdigit() and 0 <= int(head) < TAG_HEADS):
            problems.append(f"CONFIG_EXTRUDER {head!r} not within 0..{TAG_HEADS - 1}")
        given = [k for k in TAG_TRIPLE if k in params]
        if given and len(given) != len(TAG_TRIPLE):
            problems.append("VENDOR, FILAMENT_TYPE and FILAMENT_SUBTYPE not together")
        rgba = params.get("FILAMENT_COLOR_RGBA")
        if rgba is not None and not HEX8.fullmatch(rgba):
            problems.append(f"FILAMENT_COLOR_RGBA {rgba!r} not 8 hex digits")
        for key, value in params.items():
            bad = (UNSAFE_QUOTED if key in quoted else UNSAFE).search(value)
            if bad:
                problems.append(f"{key} {value!r} carries {bad.group()!r}")
        if params.get("FORCE") != "1":
            problems.append("FORCE not 1")
        return problems

    @staticmethod
    def format_params(params: Mapping[str, str], quoted: Container[str] = ()) -> str:
        words = [TAG_COMMAND]
        for key, value in params.items():
            words.append(f"{key}='{value}'" if key in quoted else f"{key}={value}")
        return " ".join(words)

    @staticmethod
    def head_setting(status: Mapping[str, object], head: int) -> dict[str, str] | None:
        """Returns the current setting of the head from the status of print_task_config.

        The result has the form that the policy compares: vendor, type, subtype and rgba, trimmed.
        Returns None when the status does not carry it.
        """
        current: dict[str, str] = {}
        for field, key in TAG_STATUS.items():
            values = status.get(key)
            if not isinstance(values, list) or not 0 <= head < len(values):
                return None
            value = values[head]
            current[field] = "" if value is None else str(value).strip()
        return current

    @staticmethod
    def describe(setting: Setting) -> str:
        """Returns what a write puts on the head, as text for ACEK_ADAPTER_STATUS."""
        if setting.type is None:
            return f"{setting.rgba} only — {setting.reason}"
        subtype = setting.subtype or "''"
        text = f"{setting.vendor} {setting.type} {subtype}"
        return text + (f" {setting.rgba}" if setting.rgba is not None else "")

    @staticmethod
    def tag_summary(tag: TagView | None) -> str:
        """Returns the tag of a lane as text for ACEK_ADAPTER_STATUS.

        It has the state and the brand, name and colour of the record.
        """
        tag = tag or {}
        record = tag.get("record") or {}
        words = [tag.get("state") or "unknown"]
        words += [str(record[key]) for key in ("brand", "name", "color_rgba") if record.get(key)]
        if record and not record.get("name") and record.get("material"):
            words.insert(2 if record.get("brand") else 1, str(record["material"]))
        return " ".join(words)

    def setup_tags(
        self,
        ptc: PrintTaskConfig | None,
        fparams: FilamentParameters | None,
        print_stats: PrintStats | None,
        enabled: bool = True,
        toolhead: ToolHead | None = None,
    ) -> str | None:
        """Turns the tags on, after the hooks.

        It checks the command and the status of the U1, and it reads its filament types. Returns
        why the tags are off, or None. Never raises.
        """
        self.tags = None
        if not enabled:
            self.tags_off = "apply_tags: False"
            return self.tags_off
        try:
            problems = ["[print_task_config] absent"] if ptc is None else self.check_tags(ptc)
            names = self.table_entries(fparams) if fparams is not None and not problems else set()
            types_ = self.known_types(fparams, names)
            if not problems and not types_:
                problems = ["no filament types in [filament_parameters]"]
        except Exception as err:
            logging.exception("ace2k_u1: tags setup")
            problems = [str(err)]
        if problems:
            self.tags_off = "; ".join(problems)
            self.adapter.say(f"ace2k_u1: tags not applied: {self.tags_off}")
            return self.tags_off
        self.ptc, self.toolhead = ptc, toolhead
        self.adapter.print_stats = print_stats
        self.tags = TagPolicy({h.key: h.head for h in self.adapter.hooked}, types_, names)
        self.tags_off = None
        if self.tag_timer is None:
            self.tag_timer = self.adapter.reactor.register_timer(
                self._run_tags, self.adapter.reactor.NEVER
            )
        return None

    def _active_head(self) -> int | None:
        """Returns the head index of the active extruder, or None when it cannot be told."""
        try:
            name = cast("ToolHead", self.toolhead).get_extruder().get_name()
        except Exception:
            return None
        return self.adapter.head_of_extruder(name)

    def _held(self) -> set[int]:
        """Returns the heads whose writes wait.

        That is the head of the active extruder while printing (not paused). It is every head
        when the active one cannot be told.
        """
        if self.adapter.print_state() != "printing":
            return set()
        active = self._active_head()
        return {h.head for h in self.adapter.hooked} if active is None else {active}

    def watch_tags(self, views: Mapping[LaneKey, LaneView], eventtime: float) -> None:
        """Takes the decisions of the tags for this tick.

        views holds the dict of each hooked lane by its key, as the bay watch read it. The G-code
        is queued for the tag timer. A tick whose head setting cannot be read is skipped, because
        the policy would read None as a value. A lane that fails is logged, and the others go on.
        What is queued is always scheduled.
        """
        try:
            ptc_status = cast("PrintTaskConfig", self.ptc).get_status(eventtime)
            printing = self.adapter.in_print()
            held = self._held()
            for h in sorted(self.adapter.hooked, key=lambda h: h.head):
                if h.key not in views:
                    continue  # the status of its unit was not read in this tick
                try:
                    self._watch_tag(h, views[h.key], ptc_status, printing, h.head in held)
                except Exception:
                    logging.exception("ace2k_u1: lane %s tag", self.adapter.lane_text(h))
        except Exception:
            logging.exception("ace2k_u1: tags")
        finally:
            if self.tag_pending:
                self.adapter.reactor.update_timer(self.tag_timer, self.adapter.reactor.NOW)

    def _watch_tag(
        self, h: Hooked, lane: LaneView, ptc_status: Status, printing: bool, hold: bool
    ) -> None:
        if h.key in self.tag_pending:  # its last action has not run yet
            return
        current = self.head_setting(ptc_status, h.head)
        if current is None:
            return
        tag = lane.get("tag") or {}
        if h.tag_forgotten:  # forgotten by hand, so nothing is written, whatever the unit reads
            tag = {}
        # A lane that is parked outside a head that several lanes feed. Its tag is read and shown,
        # but never written to the head, whose setting speaks for the lane that is in it.
        parked = self.adapter.parked(h)
        tick = LaneTick(
            lane.get("insert") is True,
            tag.get("state"),
            tag.get("record"),
            printing,
            current,
            hold,
            parked,
        )
        action = cast("TagPolicy", self.tags).on_tick(h.key, tick)
        if action is None:
            return
        if action[0] == "skip":
            self.applied[h.key] = action[2].reason
            self.adapter.say(
                f"ace2k_u1: lane {self.adapter.lane_text(h)} tag not applied: {action[2].reason}"
            )
            return
        self.tag_pending[h.key] = action

    def _run_tags(self, _eventtime: float) -> float:
        """Runs the tag timer. Each queued write or clear runs as one G-code.

        A lane stays queued until its G-code has run. run_script may wait for the G-code lock
        while the bay watch goes on. So the policy hears of a refusal before the next decision
        for that lane.

        While one call waits for the lock, the reactor may run this timer again. A lane that is
        being run is left to the call that runs it, so each action runs once.

        A refusal is a console line. Nothing is raised.
        """
        try:
            while True:
                ready = [key for key in self.tag_pending if key not in self.tag_running]
                if not ready:
                    break
                key = min(ready, key=self.adapter.head_order)  # head order, as in every loop
                self.tag_running.add(key)
                try:
                    self._run_tag(key, self.tag_pending[key])
                except Exception:  # never out of the timer
                    logging.exception("ace2k_u1: lane %s tag", self.adapter.key_text(key))
                finally:
                    self.tag_running.discard(key)
                    self.tag_pending.pop(key, None)
        except Exception:  # never out of the timer
            logging.exception("ace2k_u1: tags")
        return self.adapter.reactor.NEVER

    def _run_tag(self, key: LaneKey, action: Action) -> None:
        """Runs one write or clear for the head of the lane key.

        It is checked first. A refusal that can be foreseen is a console line, and nothing is
        sent. A refusal by the U1 itself shows as the U1 shows any G-code error, and it is logged
        here. In both cases the policy is told that the head kept what it had.
        """
        n = self.adapter.key_text(key)
        setting: Setting | None
        quoted: tuple[str, ...]
        if action[0] == "write":
            head, setting = action[1], action[2]
            params, quoted, done = (
                self.tag_params(head, setting),
                TAG_QUOTED,
                self.describe(setting),
            )
        else:
            head, setting = action[1], None
            params, quoted, done = self.clear_params(head, action[2]), (), "none"
        script = self.format_params(params, quoted)
        problems = self.check_params(params, quoted)
        tags = cast("TagPolicy", self.tags)
        if problems:
            tags.note_failed(key)
            reason = "; ".join(problems)
            self.applied[key] = f"not applied — {reason}"
            self.adapter.say(f"ace2k_u1: lane {n} -> e{head}: not sent — {reason}")
            return
        try:
            with in_use_module.own_write(self.adapter):
                cast("GCode", self.adapter.gcode).run_script(script)
        except Exception as err:
            tags.note_failed(key)
            self.applied[key] = f"not applied — {err}"
            logging.warning("ace2k_u1: lane %s -> e%d: %s refused — %s", n, head, script, err)
            return
        self.applied[key] = done
        h = self.adapter.by_key.get(key)
        slot = None if h is None else self.adapter.slot_of(h)
        if slot is not None and slot.current is h:
            slot.setting_for = h  # the setting of the head belongs to this lane now
        # A colour-only write leaves the head's type as it was: it is not the new spool's setting.
        if h is not None and slot is not None and setting is not None and setting.type is not None:
            owner = slot.current is h or not slot.shared
            if owner:
                setting_written(h)  # and to the spool in its bay
        if setting is not None and setting.reason:
            self.adapter.say(f"ace2k_u1: lane {n} -> e{head}: colour only — {setting.reason}")


@dataclass
class _ReadWait:
    """A lane whose tag is watched.

    It holds the lane, the deadline, the tag of the lane before the watch (state, uid), and
    whether the state of the tag has left "read" since the watch began.
    """

    h: Hooked
    deadline: float
    before: tuple[object, object]
    left: bool = False


class TagReader:
    """Watches the tag of a spool at the end of a gripped load, and tells about a waiting one.

    The unit gives a tag only to a lane that moves, so no read is asked here. After a gripped
    load, the grip's own move may have read the tag: the lane is watched for READ_DEADLINE_S. A
    spool that goes in under the wait has not moved, so the console says at once that its tag is
    read when it moves. It holds the lanes still to be watched, and the watches that run (lane key
    to its _ReadWait). The Adapter creates one.
    """

    def __init__(self, adapter: TagsAdapter) -> None:
        self.adapter = adapter
        self.read_timer: object = None
        self.read_pending: list[Hooked] = []
        self.read_waits: dict[LaneKey, _ReadWait] = {}
        self.read_running = False

    def queue(self, h: Hooked) -> None:
        if self.read_timer is None:
            self.read_timer = self.adapter.reactor.register_timer(
                self._run_reads, self.adapter.reactor.NEVER
            )
        if h not in self.read_pending:
            self.read_pending.append(h)
        self.adapter.reactor.update_timer(self.read_timer, self.adapter.reactor.NOW)

    def tell_waiting(self, h: Hooked) -> None:
        """Say that a spool put in under the wait has its tag read when it moves.

        No load runs on a waiting lane, so nothing can read its tag yet: nothing is watched. A lane
        whose tag already reads "read" (a read, or a kept record restored) says nothing, unless it
        is still the record the lane held when the bay emptied (the old spool's).
        """
        tag = self.adapter.lane_status(h).get("tag") or {}
        before = h.tag_before
        if tag.get("state") == "read" and (before[0] != "read" or tag_identity(tag) != before):
            return
        self.adapter.say(
            f"ace2k_u1: lane {self.adapter.lane_text(h)}: the new spool's tag is read when it "
            f"moves — e{h.head} keeps its filament setting until then"
        )

    def _run_reads(self, _eventtime: float) -> float:
        """Runs the watch timer.

        It starts the watch of each queued lane. Then it looks at the tag of every watched lane.
        A tag that is read is done, and the tags apply it. A watch past its deadline gives one
        console line.

        It runs again every READ_POLL_S while a watch runs. The reactor may run it again while
        the G-code takes the lock. The call that is running does the work, and the call that was
        entered again leaves at once.
        """
        if self.read_running:
            return self.adapter.reactor.NEVER
        self.read_running = True
        try:
            now = self.adapter.reactor.monotonic()
            while self.read_pending:
                h = self.read_pending.pop(0)
                self.read_waits[h.key] = _ReadWait(h, now + READ_DEADLINE_S, h.tag_before)
            for key, wait in list(self.read_waits.items()):
                self._watch_read_guarded(key, wait, now)
        finally:
            self.read_running = False
        if self.read_waits:
            return self.adapter.reactor.monotonic() + READ_POLL_S
        return self.adapter.reactor.NEVER

    def _watch_read_guarded(self, key: LaneKey, wait: _ReadWait, now: float) -> None:
        """Runs _watch_read for one watched lane. key is the key of its lane.

        An error drops the watch and is logged. It never leaves the timer.
        """
        try:
            self._watch_read(wait, now)
        except Exception:  # never out of the timer
            self.read_waits.pop(key, None)
            logging.exception("ace2k_u1: lane %s tag read", self.adapter.key_text(key))

    def _watch_read(self, wait: _ReadWait, now: float) -> None:
        """Looks at the tag of a watched lane.

        The watch is done when the tag is read, and it is fresh: the grip's own move may have
        read it. A tag is fresh when it is new since the filament went in (it is not the record
        that the lane held then). It is also fresh when it is read again after the state left
        "read" since the watch began, which is the same spool read again. The tags apply it on
        their next tick, with the rule of the active extruder while printing.

        If the tag is still not read at the deadline, the console says that it is read when it
        moves, and the head keeps its setting until then. A session that runs at the deadline
        (state "reading") keeps the watch until it ends.
        """
        h, deadline, before = wait.h, wait.deadline, wait.before
        tag = self.adapter.lane_status(h).get("tag") or {}
        if tag.get("state") != "read":
            wait.left = True  # searching, reading or pending: a later "read" is this spool's
        fresh = before[0] != "read" or tag_identity(tag) != before or wait.left
        if tag.get("state") == "read" and tag.get("record") and fresh:
            del self.read_waits[h.key]
            return
        if now >= deadline and tag.get("state") != "reading":
            del self.read_waits[h.key]
            self._not_read(h)

    def _not_read(self, h: Hooked) -> None:
        n = self.adapter.lane_text(h)
        self.adapter.say(
            f"ace2k_u1: lane {n}: the gripped spool's tag is read when it moves — e{h.head} keeps "
            "its filament setting until then"
        )
