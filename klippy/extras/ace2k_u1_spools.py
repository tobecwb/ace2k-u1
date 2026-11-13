"""Keeps the spool of each lane, and works out what the spool of a lane is.

The records of the operator (ACEK_SPOOL) are kept in a file beside printer.cfg. The spool of a
lane comes from its tag, its record and the setting of its head on the U1, in the order that
LaneSpools.lane_spool gives.
There is no G-code here. ace2k_u1.py reads and writes the records.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, Union, cast

if TYPE_CHECKING:
    from .ace2k_klipper_types import Printer, Reactor
    from .ace2k_u1_common import Hooked, Slot
    from .ace2k_u1_eject import Ejector
    from .ace2k_u1_plan import LaneSpool
    from .ace2k_u1_printer import Spool, U1Printer
    from .ace2k_u1_printer_types import Coordinator, LaneView, SpoolRecord, TagRecord
    from .ace2k_u1_tags import Entry, TagPolicy

try:
    from . import ace2k_u1_plan
except ImportError:  # the host tests put klippy/extras on the path
    import ace2k_u1_plan  # type: ignore[import-not-found, no-redef]
try:
    from .ace2k_u1_common import note_setting_seen, setting_type, setting_written
except ImportError:  # same as above, for the host tests
    from ace2k_u1_common import (  # type: ignore[import-not-found, no-redef]
        note_setting_seen,
        setting_type,
        setting_written,
    )
try:
    from . import ace2k_u1_in_use as in_use_module
    from . import ace2k_u1_tags as tags_module
except ImportError:  # same as above, for the host tests
    import ace2k_u1_in_use as in_use_module  # type: ignore[import-not-found, no-redef]
    import ace2k_u1_tags as tags_module  # type: ignore[import-not-found, no-redef]

# The statuses of the units, as Adapter.read_units gives them: {id(unit): its lanes}.
UnitLanes = Mapping[int, "list[LaneView]"]
# The filament setting of a head as (#rrggbb, type), None when unset; and that of every head.
HeadSetting = Union["tuple[str | None, str]", None]
HeadSettings = Sequence[HeadSetting]


class TagsOn(Protocol):
    """The tag writer of the adapter, as the spools read its policy.

    The policy is None when the tags are off.
    """

    @property
    def tags(self) -> TagPolicy | None: ...


class SpoolsAdapter(Protocol):
    """The adapter (ace2k_u1_adapter.Adapter), as the spools see it."""

    @property
    def tag_writer(self) -> TagsOn: ...
    def lane_text(self, h: Hooked) -> str: ...
    def in_print(self) -> bool: ...

    own_writes: int

    def lane_status(self, h: Hooked, eventtime: float | None = None) -> LaneView: ...
    def read_units(self, eventtime: float) -> dict[int, list[LaneView]]: ...
    def slot_of(self, h: Hooked) -> Slot | None: ...
    @property
    def hooked(self) -> list[Hooked]: ...
    @property
    def ejector(self) -> Ejector: ...


class SpoolsHost(Protocol):
    """The ACE2kU1 (ace2k_u1.ACE2kU1), as the spools see it.

    It gives the adapter (None until it is built), the U1Printer, the printer and the reactor. It
    also gives the coordinator of ace2k (None without it).
    """

    @property
    def adapter(self) -> SpoolsAdapter | None: ...
    @property
    def u1(self) -> U1Printer: ...
    @property
    def printer(self) -> Printer: ...
    @property
    def reactor(self) -> Reactor: ...
    def units(self) -> Coordinator | None: ...


def _write_json(path: str, data: object) -> None:
    """Writes data as JSON to path.

    It writes a temporary file first and renames it over path, so the file is never half written.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _spool_record_ok(record: object) -> bool:
    """Tells whether an entry of the spool file is one that the plan can read.

    It must be a table with a string type and a #rrggbb colour. The colour must be written in
    lower case, with nothing else.
    """
    return (
        isinstance(record, dict)
        and isinstance(record.get("type"), str)
        and isinstance(record.get("color"), str)
        and ace2k_u1_plan.normal_colour(record["color"]) == record["color"]
    )


class SpoolStore:
    """The ACEK_SPOOL records, by lane key, kept in a file.

    path is the file. None keeps the records in memory only (the host tests). on_change is called
    before the records change in place.
    """

    def __init__(self, path: str | None, on_change: Callable[[], object]) -> None:
        self.path = path
        self.on_change = on_change
        # Lanes whose record could not be dropped from the saved file (logged once).
        self.drop_failed: set[str] = set()
        self.records = self.read()

    def read(self) -> dict[str, SpoolRecord]:
        """Reads the records from the file.

        Returns no records when there is no file, or when it cannot be read or is not a table. An
        entry of another shape is dropped and logged.
        """
        if self.path is None or not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            logging.exception("ace2k_u1: %s unreadable: no spool records", self.path)
            return {}
        if not isinstance(data, dict):
            logging.warning("ace2k_u1: %s is not a table: no spool records", self.path)
            return {}
        # An entry edited by hand into another shape is dropped. The plan reads only well-formed
        # ones.
        good = {k: v for k, v in data.items() if _spool_record_ok(v)}
        bad = sorted(str(k) for k in data if k not in good)
        if bad:
            logging.warning(
                "ace2k_u1: %s: spool records dropped (bad shape): %s",
                self.path,
                ", ".join(bad),
            )
        return good

    def write(self) -> None:
        """Writes the records to their file (_write_json)."""
        if self.path is None:
            return
        _write_json(self.path, self.records)

    @staticmethod
    def key(unit: object, lane: int) -> str:
        """Returns the key of a lane in the spool file.

        It is "<unit>:<lane>", or "-:<lane>" for the unnamed unit.
        """
        return f"{getattr(unit, 'name', None) or '-'}:{lane + 1}"

    def set(self, key: str, record: SpoolRecord) -> None:
        """Saves the record of a lane.

        If the file cannot be written, the record stays as it was and OSError is raised.
        """
        before = self.records.get(key)
        self.on_change()
        self.records[key] = record
        try:
            self.write()
        except OSError:
            if before is None:
                del self.records[key]
            else:
                self.records[key] = before
            raise

    def drop(self, key: str, name: str) -> bool:
        """Drops the record of a lane whose bay emptied.

        GripWatch.bay_watch calls it. The ACEK_SPOOL record is dropped and the file is saved.
        If the write fails, the record is kept and False is returned, so the next tick tries
        again. The failure is logged once, until a write of that record succeeds. name is the
        text of the lane, for the log.
        """
        record = self.records.pop(key, None)
        if record is None:
            return True
        try:
            self.write()
        except OSError:
            self.records[key] = record
            if key not in self.drop_failed:
                self.drop_failed.add(key)
                logging.exception("ace2k_u1: lane %s spool record not dropped", name)
            return False
        self.drop_failed.discard(key)
        self.on_change()
        logging.info("ace2k_u1: lane %s's spool record dropped: its bay is empty", name)
        return True


class LaneSpools:
    """Works out what the spool of each hooked lane is.

    host is the ACE2kU1. Its adapter is built later, and its U1Printer and printer are read when
    used. store is the SpoolStore.
    """

    def __init__(self, host: SpoolsHost, store: SpoolStore) -> None:
        self.host = host
        self.store = store
        self.unread_logged: set[int] = set()  # material(once=True): the lanes already logged

    def _adapter(self) -> SpoolsAdapter:
        """Returns the adapter of the host. It is built before any lane is read."""
        return cast("SpoolsAdapter", self.host.adapter)

    def bay_empty(self, h: Hooked) -> bool:
        """Handles on_bay_empty of the adapter: the bay of lane h reads empty.

        The ACEK_SPOOL record of the lane is dropped (SpoolStore.drop). Its False means that the
        next tick tries again.
        """
        return self.store.drop(SpoolStore.key(h.unit, h.lane), self._adapter().lane_text(h))

    def lane_read(self, h: Hooked, units: UnitLanes | None = None) -> LaneView:
        """Returns the status of the lane, or {} (logged) when it cannot be read.

        units is the same as in own_record.
        """
        try:
            if units is None:
                return self._adapter().lane_status(h)
            lanes = units.get(id(h.unit))
            return {} if lanes is None else lanes[h.lane]
        except Exception:
            logging.exception("ace2k_u1: lane %s status unreadable", self._adapter().lane_text(h))
            return {}

    def own_record(
        self, h: Hooked, units: UnitLanes | None = None, lane: LaneView | None = None
    ) -> tuple[str | None, TagRecord, str | None]:
        """Returns the spool of the lane. The tag comes first, then the ACEK_SPOOL record.

        This is the spool that the lane tells by itself, not the setting of its head. The result
        is one of three.
        - ("tag", its tag record, #rrggbb), when its tag carries a colour and a material.
        - ("spool", its ACEK_SPOOL record, None), otherwise.
        - (None, its tag record, None), when there is neither. The tag record is {} without a
          tag.

        units holds the statuses of the units that are already read (Adapter.read_units). A unit
        that is left out cannot be read. With None, the status of the lane is read here. lane is
        the status of the lane if it is already read (lane_read).
        """
        if lane is None:
            lane = self.lane_read(h, units)
        try:
            record = {} if h.tag_forgotten else (lane.get("tag") or {}).get("record") or {}
        except Exception:
            logging.exception("ace2k_u1: lane %s status unreadable", self._adapter().lane_text(h))
            record = {}
        colour = ace2k_u1_plan.normal_colour(record.get("color_rgba"))
        if colour and record.get("material"):
            return "tag", record, colour
        spool = self.store.records.get(self.store.key(h.unit, h.lane))
        if spool:
            return "spool", spool, None
        return None, record, None

    def own_spool(
        self,
        h: Hooked,
        lane: LaneView | None = None,
        table: tuple[set[str], set[Entry]] | None = None,
    ) -> Spool | None:
        """Returns (vendor, type, subtype, rrggbbaa) of the spool of the lane.

        The spool comes from its tag, or else from its ACEK_SPOOL record (see own_record).
        Returns None with neither. The type is None when the filament table of the U1 does not
        know it.

        The vendor and the subtype of a tag are the ones that the table of the U1 lists. A name
        that it does not list becomes Generic (ace2k_u1_tags.catalogued), whether the tag policy
        is on or not. Those of a record are the operator's own. lane is the lane's status when
        it is already read (lane_read); {} (unread) leaves the record. table is catalogue() when
        the caller already read it.
        """
        kind, record, colour = self.own_record(h, lane=lane)
        if kind is None:
            return None
        types_, names = self.catalogue() if table is None else table
        if kind == "tag":
            setting = tags_module.translate(record, types_)
            if names:
                setting = tags_module.catalogued(setting, names)
            rgba = cast("str", colour)[1:].upper() + "FF"  # a tag record comes with its colour
            return setting.vendor, setting.type, setting.subtype, rgba
        vendor = record.get("vendor") or "Generic"
        material = str(record["type"]).strip().upper()
        known = material if material in types_ else None
        return vendor, known, "", record["color"][1:].upper() + "FF"

    def catalogue(self) -> tuple[set[str], set[Entry]]:
        """Returns the types and the entries of the filament table of the U1.

        The entries are (vendor, type, subtype). They come from the tag policy. Otherwise they
        are read once from [filament_parameters] (when the tags are off). Both are empty when
        neither can be read.
        """
        tags = self._adapter().tag_writer.tags
        known: set[str] | None = getattr(tags, "known_types", None)
        if known:
            return known, getattr(tags, "names", None) or set()
        fparams = self.host.printer.lookup_object("filament_parameters", None)
        if fparams is None:
            return set(), set()
        entries = tags_module.TagWriter.table_entries(fparams)
        return tags_module.TagWriter.known_types(fparams, entries), entries

    def head_setting(self, head: int, settings: HeadSettings | None = None) -> HeadSetting:
        """Returns (#rrggbb, type) of the filament setting of a head on the U1.

        Returns None when the setting is unset or cannot be read. settings holds the settings of
        every head already read (head_settings); with None the head's setting is read here.
        """
        if settings is not None:
            return settings[head] if head < len(settings) else None
        setting = self.host.u1.head_setting(head)
        return None if setting is None else self._setting(*setting)

    def head_settings(self) -> HeadSettings:
        """Returns head_setting of every head, from one read of the U1 ([] when unread)."""
        return [self._setting(*s) for s in self.host.u1.head_settings() or []]

    @staticmethod
    def _setting(kind: str, rgba: object) -> HeadSetting:
        if not setting_type(kind):
            return None
        return ace2k_u1_plan.normal_colour(rgba), kind

    def watch_settings(self) -> None:
        """Note, once per watch tick, the setting of each head that one lane feeds.

        The heads' settings are read once (note_setting_seen). A read that fails changes nothing.
        """
        adapter = self._adapter()
        settings = self.host.u1.head_settings()
        if settings is None:
            return
        for h in adapter.hooked:
            slot = adapter.slot_of(h)
            if slot is not None and not slot.shared and h.head < len(settings):
                note_setting_seen(h, settings[h.head])

    def setting_set(self, head: int, kind: str) -> None:
        """Note a SET_PRINT_FILAMENT_CONFIG that just ran on head, with its type kind (Wraps).

        On a head that one lane feeds, with its bay full, or with its spool run out and its rest
        still loaded in the head (Ejector.known_loaded), a set type makes the setting that spool's
        (setting_written), even when the value is the same as before: the page's Edit spool and
        Define write it for the spool in the bay, or for that rest. A write of the colour alone,
        or of no type (empty or NONE), is not; the setting it leaves is taken in as seen, so the
        next tick does not count it as a change (note_setting_seen).
        """
        adapter = self._adapter()
        real = bool(setting_type(kind))
        for h in adapter.hooked:
            slot = adapter.slot_of(h)
            if slot is None or slot.shared or slot.head != head:
                continue
            if not h.bay_full and not (real and adapter.ejector.known_loaded(h)):
                continue
            if real:
                setting_written(h)
                self._by_hand(h)
            elif h.setting_seen is not None:
                h.setting_seen = self.host.u1.head_setting(head) or h.setting_seen

    def _by_hand(self, h: Hooked) -> None:
        """Tell the unit that the user named the material of lane h, when this write is theirs.

        A write of the adapter's own is not (in_use.own_write): the tag write, the spool step of a
        swap, the page's Edit spool step and the check at a load. A write while a print runs is not
        either: the print's start and the slicer write the setting, and the lane that prints is in
        use anyway. A head that several lanes feed gets no mark here (setting_set skips it): its
        lanes are marked through ACEK_SPOOL only.
        """
        adapter = self._adapter()
        if adapter.own_writes or adapter.in_print():
            return
        in_use_module.tell_by_hand(h.unit, h.lane)

    def lone_setting(self, h: Hooked, settings: HeadSettings | None = None) -> HeadSetting:
        """Returns head_setting of the head of lane h while it speaks for the spool in the bay.

        That is on a head that the lane feeds alone, with the setting set (not NONE or empty),
        written or changed for the spool in the bay (or there when Klipper started with the bay
        full), and no newer tag read or Edit spool record came for the lane (Hooked.setting_mark,
        own_mark; see note_setting_seen and setting_set).
        It then comes before the lane's tag and record, as on the page. Returns None otherwise:
        a shared head, a setting that belongs to an older spool, or one that cannot be read.
        settings is the same as in head_setting.
        """
        slot = self._adapter().slot_of(h)
        if slot is None or slot.shared:
            return None
        if h.setting_mark is None or h.own_mark > h.setting_mark:
            return None
        return self.head_setting(h.head, settings)

    def material(self, h: Hooked, head_setting: bool = False, *, once: bool = False) -> str:
        """Returns the material of lane h in upper case, or "" when it is not known.

        The material comes from the spool as lane_spool tells it. On a shared head, the setting
        of the head counts only when it was written for this lane. With head_setting set, the
        setting of the head is used otherwise, whatever lane it was written for. That is the case
        for the old lane at an unload, whose filament the head still holds. A spool that cannot
        be read (the status of the unit) is logged and taken as none. With once set, for a caller
        that asks every few seconds, it is logged once per lane, until the lane reads again.
        """
        try:
            material = self.lane_spool(h).material
        except Exception as err:
            if not once or id(h) not in self.unread_logged:
                name = self._adapter().lane_text(h)
                logging.warning("ace2k_u1: %s's spool unread for its material: %s", name, err)
            if once:
                self.unread_logged.add(id(h))
            material = None
        else:
            if once:
                self.unread_logged.discard(id(h))
        if not material and head_setting:
            setting = self.head_setting(h.head)
            material = setting[1] if setting is not None else None
        return str(material or "").strip().upper()

    def lane_spools(
        self, hooked: Iterable[Hooked], units: UnitLanes | None = None
    ) -> list[LaneSpool]:
        """Returns lane_spool of each lane in hooked, in order.

        The status of each unit is read once, or units (Adapter.read_units) is used. The heads'
        settings are read once.
        """
        if units is None:
            units = self._adapter().read_units(self.host.reactor.monotonic())
        settings = self.head_settings()
        return [self.lane_spool(h, units, settings) for h in hooked]

    def lane_spool(
        self,
        h: Hooked,
        units: UnitLanes | None = None,
        settings: HeadSettings | None = None,
        lane: LaneView | None = None,
        loaded: bool | None = None,
    ) -> LaneSpool:
        """Returns LaneSpool(key, colour, material, empty) for a hooked lane.

        A lane whose bay reads empty (insert False) has no spool to offer: empty is set. Its colour
        and material are None, whatever its records or the setting of its head say, unless it is
        still loaded in its head (Ejector.known_loaded): its spool ran out and its rest is in the
        head, so they tell what that rest is, in the order below. The setting of its head is kept
        for it until it leaves the head (GripWatch._setting_owner).

        The order depends on the head, as on the page.
        - On a head with one lane: the setting of the head on the U1 while it speaks for the
          spool in the bay (lone_setting), then the tag, then the ACEK_SPOOL record (own_record).
        - On a shared head: the tag, then the record, then the setting of the head, but only
          when the spool of the lane was the last one written to it (setting_for).

        units is the same as in own_record, settings as in head_setting, and lane is the status
        of the lane when it is already read (lane_read). loaded is Ejector.known_loaded of the lane
        when the caller already has it; with None it is asked here, for an empty bay only.
        """
        key = cast("Coordinator", self.host.units()).lane_text(h.unit, h.lane)
        if lane is None:
            lane = self.lane_read(h, units)
        empty = lane.get("insert") is False
        if loaded is None and empty:
            loaded = self._adapter().ejector.known_loaded(h)
        if empty and not loaded:
            return ace2k_u1_plan.LaneSpool(key, None, None, True)
        setting = self.lone_setting(h, settings)
        if setting is not None:
            return ace2k_u1_plan.LaneSpool(key, setting[0], setting[1], empty)
        kind, record, colour = self.own_record(h, lane=lane)
        if kind == "tag":
            return ace2k_u1_plan.LaneSpool(key, colour, record["material"], empty)
        if kind == "spool":
            return ace2k_u1_plan.LaneSpool(key, record.get("color"), record.get("type"), empty)
        slot = self._adapter().slot_of(h)
        if slot is not None and slot.shared and slot.setting_for is h:
            setting = self.head_setting(h.head, settings)
            if setting is not None:
                return ace2k_u1_plan.LaneSpool(key, setting[0], setting[1], empty)
        return ace2k_u1_plan.LaneSpool(key, None, record.get("material"), empty)
