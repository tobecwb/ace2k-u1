"""The in-use masks and the by-hand marks that the adapter sends to each unit's tag reader."""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import fakes
import pytest
from ace2k_u1_in_use import in_use_masks
from test_tools import run, tools_rig

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace


class Rfid:
    """A unit's tag reader as the adapter calls it: the masks and by-hand lanes it was told."""

    def __init__(self) -> None:
        self.masks: list[int] = []
        self.by_hand: list[int] = []
        self.reads: list[int] = []

    def set_in_use(self, mask: int) -> None:
        self.masks.append(mask)

    def set_by_hand(self, lane: int) -> None:
        self.by_hand.append(lane)

    def start_read(self, lane: int) -> None:
        self.reads.append(lane)


def rig_with_rfid(tmp_path: Path) -> tuple[SimpleNamespace, dict[str, Rfid]]:
    """The rig, every bay read empty: a shared head whose lane is not known then has none in use."""
    rig, units = tools_rig(tmp_path)
    rfids = {name: Rfid() for name in units}
    for name, unit in units.items():
        unit.rfid = rfids[name]
        for lane in unit.lanes:
            lane["insert"] = False
    return rig, rfids


def printing(rig: SimpleNamespace, state: str = "printing") -> None:
    rig.print_stats.state = state


def test_lone_lanes_count_only_when_known_loaded(tmp_path: Path) -> None:
    rig, _ = rig_with_rfid(tmp_path)
    ad = rig.adapter
    lone = ad.hooked_lane(rig.units["a"], 2)
    ad.ejector.known_loaded = lambda h: h is lone
    masks = in_use_masks(ad, printing=False)
    assert masks[rig.units["a"]] == 0b0100


def test_the_current_lane_of_a_head_is_in_use_while_a_print_runs_or_pauses(tmp_path: Path) -> None:
    rig, _ = rig_with_rfid(tmp_path)
    ad = rig.adapter
    ad.ejector.known_loaded = lambda h: False
    slot = ad.slot_of_head(0)
    ad.set_current(slot, slot.lanes[1])
    assert in_use_masks(ad, printing=False)[rig.units["a"]] == 0
    # a:2 is the head's lane; a:3 and a:4 are the only lanes of their heads
    assert in_use_masks(ad, printing=True)[rig.units["a"]] == 0b1110
    # its shared head has no lane known, and no bay with filament
    assert in_use_masks(ad, printing=True)[rig.units["b"]] == 0
    bays(rig, "b", [False, True, False, False])  # b:2 has filament: it may be in the head
    assert in_use_masks(ad, printing=True)[rig.units["b"]] == 0b0010


def bays(rig: SimpleNamespace, unit: str, insert: list[bool | None]) -> None:
    """The insert readings of the unit's four bays."""
    for lane, value in enumerate(insert):
        rig.units[unit].lanes[lane]["insert"] = value


def test_a_shared_head_with_an_unknown_lane_has_every_lane_with_filament_in_use(
    tmp_path: Path,
) -> None:
    """After a restart the head's lane is not known: any lane of it with filament may be in it."""
    rig, _ = rig_with_rfid(tmp_path)
    ad = rig.adapter
    slot = ad.slot_of_head(0)
    slot.current = None
    ad.ejector.known_loaded = lambda h: False
    bays(rig, "a", [True, False, False, False])
    for printing_now in (False, True):
        mask = in_use_masks(ad, printing=printing_now)[rig.units["a"]]
        assert mask & 0b0011 == 0b0001  # a:1 has filament, a:2's bay is empty
    bays(rig, "a", [True, None, False, False])  # a bay not read yet counts as full
    assert in_use_masks(ad, printing=False)[rig.units["a"]] & 0b0011 == 0b0011
    # the head's lane known: its sibling is not in use, the lane in it is while printing
    ad.set_current(slot, slot.lanes[0])
    assert in_use_masks(ad, printing=False)[rig.units["a"]] & 0b0011 == 0
    assert in_use_masks(ad, printing=True)[rig.units["a"]] & 0b0011 == 0b0001


def test_a_lane_loading_from_its_bay_is_not_in_use_on_a_head_with_no_lane_known(
    tmp_path: Path,
) -> None:
    """Its tip is in its own tube: the load may prove its tag by the back-and-forth."""
    rig, _ = rig_with_rfid(tmp_path)
    ad = rig.adapter
    ad.slot_of_head(0).current = None
    ad.ejector.known_loaded = lambda h: False
    bays(rig, "a", [True, True, False, False])
    rig.units["a"].lanes[1]["mode"] = "loading"
    assert in_use_masks(ad, printing=False)[rig.units["a"]] & 0b0011 == 0b0001
    rig.units["a"].lanes[1]["mode"] = "idle"
    assert in_use_masks(ad, printing=False)[rig.units["a"]] & 0b0011 == 0b0011


def test_a_bay_that_cannot_be_read_counts_as_full(tmp_path: Path) -> None:
    rig, _ = rig_with_rfid(tmp_path)
    ad = rig.adapter
    ad.slot_of_head(0).current = None
    ad.ejector.known_loaded = lambda h: False

    def unreadable(eventtime: float) -> dict[str, object]:
        raise OSError("status")

    rig.units["a"].get_status = unreadable
    assert in_use_masks(ad, printing=False)[rig.units["a"]] & 0b0011 == 0b0011


def test_a_bay_that_cannot_be_read_is_logged_once_per_lane_and_once_when_it_reads_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig, _ = rig_with_rfid(tmp_path)
    ad = rig.adapter
    ad.slot_of_head(0).current = None
    ad.ejector.known_loaded = lambda h: False
    readable = rig.units["a"].get_status

    def unreadable(eventtime: float) -> dict[str, object]:
        raise OSError("status")

    rig.units["a"].get_status = unreadable
    with caplog.at_level("INFO"):
        for _ in range(5):
            in_use_masks(ad, printing=False)
    failed = [r for r in caplog.records if "cannot be read" in r.getMessage()]
    assert sorted(r.getMessage() for r in failed) == [
        "ace2k_u1: lane 1 bay cannot be read for the in-use mask; it counts as full",
        "ace2k_u1: lane 2 bay cannot be read for the in-use mask; it counts as full",
    ]
    assert all(r.exc_info for r in failed)
    caplog.clear()
    rig.units["a"].get_status = readable
    with caplog.at_level("INFO"):
        for _ in range(3):
            in_use_masks(ad, printing=False)
    assert sorted(r.getMessage() for r in caplog.records) == [
        "ace2k_u1: lane 1 bay reads again",
        "ace2k_u1: lane 2 bay reads again",
    ]


def test_the_masks_go_out_on_a_change_only(tmp_path: Path) -> None:
    rig, rfids = rig_with_rfid(tmp_path)
    ad = rig.adapter
    ad.ejector.known_loaded = lambda h: False
    ad.in_use.send(ad, False)
    ad.in_use.send(ad, False)
    assert rfids["a"].masks == [0]
    assert rfids["b"].masks == [0]
    ad.ejector.known_loaded = lambda h: h.lane == 2
    ad.in_use.send(ad, False)
    assert rfids["a"].masks == [0, 0b0100]
    assert rfids["b"].masks == [0]  # a lane of its shared head, none known in it: unchanged


def test_a_failed_send_is_tried_again_and_an_old_image_is_let_be(tmp_path: Path) -> None:
    rig, rfids = rig_with_rfid(tmp_path)
    ad = rig.adapter
    ad.ejector.known_loaded = lambda h: False
    calls = []

    def fails(mask: int) -> None:
        calls.append(mask)
        raise OSError("link down")

    rfids["a"].set_in_use = fails
    ad.in_use.send(ad, False)
    ad.in_use.send(ad, False)
    assert calls == [0, 0]
    rig.units["b"].rfid = types.SimpleNamespace()  # an older ace2k has no such call
    ad.in_use.send(ad, False)  # nothing raises


def test_the_watch_tick_sends_the_masks(tmp_path: Path) -> None:
    rig, rfids = rig_with_rfid(tmp_path)
    rig.adapter.watch(0.0)
    assert rfids["a"].masks and rfids["b"].masks


def test_acek_spool_says_by_hand(tmp_path: Path) -> None:
    rig, rfids = rig_with_rfid(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ff0000"})
    assert rfids["a"].by_hand == [1]
    assert rfids["b"].by_hand == []


def test_a_restart_does_not_tell_a_saved_record_whose_bay_reads_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unit's host forgets a lane whose bay reads empty: the adapter does not tell it again."""
    rig, _units = tools_rig(tmp_path)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ff0000"})
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PETG", "COLOR": "00ff00"})
    real = fakes.two_units

    def emptied() -> tuple[fakes.FakeUnits, dict[str, fakes.FakeAce2k]]:
        units2, by_name = real()
        by_name["a"].lanes[1]["insert"] = False  # a:2 was taken out while Klipper was down
        return units2, by_name

    monkeypatch.setattr(fakes, "two_units", emptied)
    _rig2, units2 = tools_rig(tmp_path)
    assert units2["a"].rfid.by_hand == [2]


def test_a_restart_tells_the_saved_spool_records_by_hand_again(tmp_path: Path) -> None:
    """Klipper restarted: the unit's host module forgot the by-hand lanes, the adapter has not."""
    rig, units = tools_rig(tmp_path)
    assert units["a"].rfid.by_hand == []
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "2", "TYPE": "PLA", "COLOR": "ff0000"})
    run(rig, "ACEK_SPOOL", {"UNIT": "b", "LANE": "4", "TYPE": "PETG", "COLOR": "00ff00"})
    rig2, units2 = tools_rig(tmp_path)  # the hook of the new start, the records on file
    assert units2["a"].rfid.by_hand == [1]
    assert units2["b"].rfid.by_hand == [3]


def wrapped_setting(rig: SimpleNamespace, head: int, kind: str = "PLA") -> None:
    """A write of the setting that goes through the wrap, as a screen write does."""
    if "SET_PRINT_FILAMENT_CONFIG" not in rig.gcode.commands:
        rig.gcode.register_command("SET_PRINT_FILAMENT_CONFIG", lambda g: None)
        rig.module.wraps.wrap_head_setting(rig.gcode)
    params = {"CONFIG_EXTRUDER": str(head), "VENDOR": "Generic", "FILAMENT_TYPE": kind}
    from test_tools import Gcmd  # noqa: PLC0415

    rig.gcode.commands["SET_PRINT_FILAMENT_CONFIG"](Gcmd(params, "SET_PRINT_FILAMENT_CONFIG"))


def full_bay(rig: SimpleNamespace, unit: str, lane: int) -> None:
    rig.adapter.hooked_lane(rig.units[unit], lane).bay_full = True


def test_a_screen_write_says_by_hand_for_the_lane_of_the_head(tmp_path: Path) -> None:
    rig, rfids = rig_with_rfid(tmp_path)
    full_bay(rig, "a", 2)
    wrapped_setting(rig, 1)  # head 2 is fed by a:3 alone
    assert rfids["a"].by_hand == [2]


def test_a_write_while_a_print_runs_is_not_by_hand(tmp_path: Path) -> None:
    rig, rfids = rig_with_rfid(tmp_path)
    full_bay(rig, "a", 2)
    printing(rig)
    wrapped_setting(rig, 1)
    assert rfids["a"].by_hand == []


def test_the_adapters_own_tag_write_is_not_by_hand(tmp_path: Path) -> None:
    rig, rfids = rig_with_rfid(tmp_path)
    full_bay(rig, "a", 2)
    h = rig.adapter.hooked_lane(rig.units["a"], 2)
    if "SET_PRINT_FILAMENT_CONFIG" not in rig.gcode.commands:
        rig.gcode.register_command("SET_PRINT_FILAMENT_CONFIG", lambda g: None)
        rig.module.wraps.wrap_head_setting(rig.gcode)
    seen = []

    def run_script(script: str) -> None:  # the U1 runs the command, the wrap sees it
        from test_tools import Gcmd  # noqa: PLC0415

        seen.append(rig.adapter.own_writes)
        words = dict(w.split("=", 1) for w in script.split()[1:])
        rig.gcode.commands["SET_PRINT_FILAMENT_CONFIG"](Gcmd(words, "SET_PRINT_FILAMENT_CONFIG"))

    rig.gcode.run_script = run_script
    rig.adapter.tag_writer.tags = types.SimpleNamespace(note_failed=lambda key: None)
    setting = types.SimpleNamespace(
        vendor="Generic", type="PLA", subtype="", rgba="FF0000FF", reason=None
    )
    rig.adapter.tag_writer._run_tag(h.key, ("write", h.head, setting))
    assert seen == [1]
    assert rfids["a"].by_hand == []
    assert rig.adapter.own_writes == 0
    wrapped_setting(rig, 1)  # the user's own write afterwards counts
    assert rfids["a"].by_hand == [2]


def lone_a3(tmp_path: Path) -> tuple[SimpleNamespace, dict[str, Rfid], object]:
    """A rig whose head 3 (a:3 alone) has its bay full and a spool record for the lane."""
    rig, rfids = rig_with_rfid(tmp_path)
    full_bay(rig, "a", 2)
    run(rig, "ACEK_SPOOL", {"UNIT": "a", "LANE": "3", "TYPE": "PLA", "COLOR": "ff0000"})
    rfids["a"].by_hand.clear()  # the record itself was by hand
    rig.gcode.register_command("SET_PRINT_FILAMENT_CONFIG", lambda g: None)
    rig.module.wraps.wrap_head_setting(rig.gcode)

    def script(line: str) -> None:  # the U1 runs the line, the wrap sees it
        from test_tools import Gcmd  # noqa: PLC0415

        words = dict(w.split("=", 1) for w in line.split()[1:])
        rig.gcode.commands["SET_PRINT_FILAMENT_CONFIG"](Gcmd(words, "SET_PRINT_FILAMENT_CONFIG"))

    rig.module.u1.script = script
    rig.adapter.tag_writer.tags = types.SimpleNamespace(known_types={"PLA", "PETG"})
    return rig, rfids, rig.adapter.hooked_lane(rig.units["a"], 2)


def test_the_swap_spool_step_on_a_lone_head_is_not_by_hand(tmp_path: Path) -> None:
    rig, rfids, h = lone_a3(tmp_path)
    assert rig.module.swapper.set_head_spool(h, new_lane=False) is None
    assert rfids["a"].by_hand == []
    assert rig.adapter.own_writes == 0
    wrapped_setting(rig, 1)  # the user's write afterwards counts (a:3 feeds head 2)
    assert rfids["a"].by_hand == [2]


def test_the_load_time_material_check_is_not_by_hand(tmp_path: Path) -> None:
    rig, rfids, h = lone_a3(tmp_path)
    rig.module.u1.head_setting = lambda head: ("FFFFFF", "PETG")  # the head was set for PETG
    line = rig.module.material._head_differs(h)  # the check at a load: it sets the head now
    assert line is not None
    assert "set now" in line
    assert rfids["a"].by_hand == []
    assert rig.adapter.own_writes == 0


def test_an_own_write_that_fails_still_ends_the_mark(tmp_path: Path) -> None:
    rig, _, h = lone_a3(tmp_path)

    def refuses(line: str) -> None:
        raise RuntimeError("refused")

    rig.module.u1.script = refuses
    assert "refused" in rig.module.swapper.set_head_spool(h, new_lane=False)
    assert rig.adapter.own_writes == 0


def test_a_paused_print_counts_for_the_current_lane(tmp_path: Path) -> None:
    rig, _ = rig_with_rfid(tmp_path)
    ad = rig.adapter
    ad.ejector.known_loaded = lambda h: False
    printing(rig, "paused")
    assert ad.in_print()
    assert in_use_masks(ad, printing=ad.in_print())[rig.units["a"]] == 0b1100


def test_a_lone_lane_that_is_not_known_loaded_is_in_use_while_printing(tmp_path: Path) -> None:
    rig, _ = rig_with_rfid(tmp_path)
    ad = rig.adapter
    ad.ejector.known_loaded = lambda h: False
    assert in_use_masks(ad, printing=False)[rig.units["a"]] == 0
    assert in_use_masks(ad, printing=True)[rig.units["a"]] == 0b1100
