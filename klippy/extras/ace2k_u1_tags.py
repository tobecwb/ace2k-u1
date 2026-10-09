"""The spool's tag as the U1's filament setting: the
translation of an ace2k tag record and the per-lane policy of when to write and clear.
No Klipper here; ace2k_u1.py runs the decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

SAFE = re.compile(r"[^A-Za-z0-9_+-]")  # vendor: no spaces
SAFE_SUBTYPE = re.compile(r"[^A-Za-z0-9 _+-]")  # subtype: spaces kept, never a quote
BOUNDARY = " +-_"  # what may follow a type inside a material (PLA+, PETG-HF, PLA Matte)
HEX8 = re.compile(r"[0-9A-F]{8}")
_NO_UNDO = object()


@dataclass(frozen=True)
class Setting:
    """A head's filament setting. type None: the material is unknown to the U1, only the colour
    is written (reason says why); rgba None: no colour is written."""

    vendor: str
    type: str | None
    subtype: str
    rgba: str | None
    reason: str | None = None


def _clean(text):
    return SAFE.sub("", text or "")


def _clean_subtype(text):
    return " ".join(SAFE_SUBTYPE.sub("", text or "").split())


def _match_type(material, known_types):
    """The U1 type a material names: an exact match, else the longest known type followed by a
    boundary character (PLA+ -> PLA, PETG-HF -> PETG; PCTG and PAHT match nothing)."""
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


def _tail(text, ftype):
    """What follows the type in text, boundary characters stripped (PETG-HF -> HF, PLA+ -> "")."""
    return text[len(ftype) :].strip(BOUNDARY)


def translate(record, known_types):
    """A tag record (brand / material / name / color_rgba) as the U1's Setting.

    The subtype is the name's words after the type (PLA Full Spectrum -> Full Spectrum, PETG-HF
    Fast -> HF Fast); with no name, what follows the type in the material (PETG-HF -> HF, PLA+ ->
    nothing). A name, when present, always wins."""
    vendor = _clean(record.get("brand")) or "Generic"
    material = (record.get("material") or "").strip().upper()
    ftype = _match_type(material, known_types)
    reason = None if ftype else f"material {record.get('material')!r} unknown to the U1"
    subtype = ""
    if ftype:
        words = (record.get("name") or "").split()
        if words:
            first = words[0].upper()
            if _match_type(first, {ftype}):  # the type word: keep only its tail
                words[0] = _tail(words[0], ftype)
            elif first in known_types and ftype.startswith(first):  # a shorter form: PLA, PLA-CF
                words = words[1:]
            subtype = _clean_subtype(" ".join(words))
        else:
            subtype = _clean_subtype(_tail(material, ftype))
    rgba = (record.get("color_rgba") or "").strip().upper()
    if len(rgba) == 6:
        rgba += "FF"
    return Setting(vendor, ftype, subtype, rgba if HEX8.fullmatch(rgba) else None, reason)


def _written(setting):
    """The fields a write of setting puts on the head: vendor/type/subtype only with a type,
    the colour only with one."""
    fields = {}
    if setting.type is not None:
        fields.update(vendor=setting.vendor, type=setting.type, subtype=setting.subtype)
    if setting.rgba is not None:
        fields["rgba"] = setting.rgba
    return fields


def _field(current, name):
    value = current.get(name) if isinstance(current, dict) else getattr(current, name, None)
    return "" if value is None else str(value).upper()


def _unchanged(current, setting):
    """The head still shows what was written (case-insensitive, written fields only)."""
    if current is None:
        return False
    return all(_field(current, k) == str(v).upper() for k, v in _written(setting).items())


def _shows(current, setting, names):
    return all(_field(current, k) == str(getattr(setting, k)).upper() for k in names)


def _union(old, new, current):
    """What the lane's tag has put on the head after new is written over old: new's fields,
    plus old's vendor/type/subtype or colour where new carries none and the head still shows
    old's (a field changed by hand since is no longer the tag's)."""
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


class _Lane:
    def __init__(self):
        self.inserted = False
        self.applied_uid = None  # the uid last acted on since the insert rose
        self.applied = False
        # what this lane's tag has put on the head since the last clear: the union of its
        # writes' fields (a Setting; None: nothing)
        self.last_written = None
        self.clear_pending = False
        self.undo = _NO_UNDO  # last_written before the latest write/clear, for note_failed


class TagPolicy:
    """Per lane, when to write the head's setting from the tag, when to clear it, when to wait.

    heads maps a hooked lane to its U1 head (the CONFIG_EXTRUDER index); known_types are the
    U1 table's filament types. on_tick is called once per lane per poll and returns at most one
    action, already counted as done:

    - ("write", head, Setting): a tag read with a uid not yet acted on since the insert rose.
      With hold (the head is the active extruder of a running print) the write waits, nothing
      recorded: a later tick without hold writes what the lane holds then, if still inserted.
      A re-read of the same uid within one insert does not rewrite (a manual change stays
      until the spool is reseated), unless the lane's tag left "read" in between (a forget
      puts it back to pending): the next read writes again, the same uid included.
    - ("skip", head, Setting): the same, with nothing writable (no type, no colour); the
      Setting's reason is the console line.
    - ("clear", head, Setting): the insert has fallen, nothing is printing, and the head still
      shows the lane's last write, the Setting (compared case-insensitively on the fields that
      write carried); the caller resets only those fields.

    If the caller's G-code for a write or clear fails, note_failed(lane), before the lane's next
    on_tick, undoes the head's recorded setting; the attempt itself stays recorded, so it is not
    retried."""

    def __init__(self, heads, known_types):
        self.heads = dict(heads)
        self.known_types = set(known_types)
        self.lanes = {lane: _Lane() for lane in self.heads}

    def on_tick(self, lane, insert, tag_state, record, printing, current, hold=False):
        """printing: the U1 is printing or paused (a clear waits); current: the head's setting
        now, a Setting or a mapping with vendor/type/subtype/rgba, or None; hold: a write for
        this head waits (the caller's: the active extruder while printing, not paused)."""
        st = self.lanes.get(lane)
        if st is None:
            return None
        head = self.heads[lane]
        st.undo = _NO_UNDO  # note_failed applies to this tick's action only
        if insert and not st.inserted:  # a new spool: any uid writes, a pending clear is moot
            st.applied, st.applied_uid, st.clear_pending = False, None, False
        elif not insert and st.inserted and st.last_written is not None:
            st.clear_pending = True
        st.inserted = bool(insert)

        if insert:
            # forgotten (pending) or in a read's way: the next read writes again; a missing or
            # unknown tag state re-arms nothing
            if tag_state in ("pending", "searching", "reading", "no_tag"):
                st.applied, st.applied_uid = False, None
            if tag_state != "read" or not record:
                return None
            uid = record.get("uid")
            if st.applied and uid == st.applied_uid:
                return None
            setting = translate(record, self.known_types)
            if hold and _written(setting):
                return None
            st.applied, st.applied_uid = True, uid
            if not _written(setting):
                return ("skip", head, setting)
            st.undo, st.last_written = st.last_written, _union(st.last_written, setting, current)
            return ("write", head, setting)

        if not st.clear_pending or printing:
            return None
        st.clear_pending = False
        written, st.last_written = st.last_written, None
        if not _unchanged(current, written):
            return None  # changed by hand since: left alone
        st.undo = written
        return ("clear", head, written)

    def note_failed(self, lane):
        """The G-code of the lane's last action failed: the head keeps what it had."""
        st = self.lanes.get(lane)
        if st is not None and st.undo is not _NO_UNDO:
            st.last_written, st.undo = st.undo, _NO_UNDO
