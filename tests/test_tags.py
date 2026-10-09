"""The spool's tag as the U1's filament setting: the translation of a tag record into the head's
vendor / type / subtype / colour, and the per-lane policy of when to write, clear or wait. The
records are synthetic, shaped like ace2k's tag decoder output."""

from ace2k_u1_tags import Setting, TagPolicy, translate

KNOWN = {"PLA", "PLA-CF", "PETG", "ABS", "ASA", "TPU", "PA", "PC"}

BAMBU = {
    "uid": "00000001",
    "brand": "Bambu Lab",
    "material": "PLA",
    "name": "PLA Basic",
    "color_rgba": "FF8000FF",
}
SNAPMAKER = {
    "uid": "00000002",
    "brand": "Snapmaker",
    "material": "PLA",
    "name": "Matte",
    "color_rgba": "112233FF",
}
ANYCUBIC = {
    "uid": "00000003",
    "brand": "Anycubic",
    "material": "PETG",
    "name": None,
    "color_rgba": "445566",
}


# --- translation ---------------------------------------------------------------------------


def test_a_bambu_record_becomes_vendor_type_subtype_and_colour():
    record = {"brand": "Bambu Lab", "material": "PLA", "name": "PLA Matte"}
    record["color_rgba"] = "FF8000FF"
    assert translate(record, KNOWN) == Setting(
        vendor="BambuLab", type="PLA", subtype="Matte", rgba="FF8000FF"
    )


def test_the_three_formats_translate():
    assert translate(BAMBU, KNOWN) == Setting("BambuLab", "PLA", "Basic", "FF8000FF")
    assert translate(SNAPMAKER, KNOWN) == Setting("Snapmaker", "PLA", "Matte", "112233FF")
    assert translate(ANYCUBIC, KNOWN) == Setting("Anycubic", "PETG", "", "445566FF")


def test_the_longest_type_matches_first_and_case_does_not_matter():
    assert translate({"material": "PLA-CF"}, KNOWN).type == "PLA-CF"
    assert translate({"material": "pla-cf"}, KNOWN).type == "PLA-CF"
    assert translate({"material": "petg"}, KNOWN).type == "PETG"
    assert translate({"material": "PLA-CF", "name": "PLA-CF Pro"}, KNOWN).subtype == "Pro"


def test_a_type_followed_by_a_boundary_matches_and_lends_its_subtype():
    assert translate({"material": "PLA+"}, KNOWN) == Setting("Generic", "PLA", "", None)
    assert translate({"material": "PETG-HF"}, KNOWN) == Setting("Generic", "PETG", "HF", None)
    assert translate({"material": "petg_hf"}, KNOWN).subtype == "HF"
    # a name always wins over the material's tail
    assert translate({"material": "PETG-HF", "name": "PETG HF Fast"}, KNOWN).subtype == "HF Fast"
    assert translate({"material": "PLA+", "name": "PLA+ Silk"}, KNOWN).subtype == "Silk"


def test_a_type_without_a_boundary_does_not_match():
    for material in ("PCTG", "PAHT", "PLAX"):
        setting = translate({"material": material, "color_rgba": "FF0000FF"}, KNOWN)
        assert setting.type is None and setting.rgba == "FF0000FF"


def test_the_subtype_keeps_its_spaces():
    record = {"brand": "Snap maker", "material": "PLA", "name": "PLA Full Spectrum"}
    assert translate(record, KNOWN) == Setting("Snapmaker", "PLA", "Full Spectrum", None)


def test_the_longest_known_type_wins_over_a_shorter_prefix():
    record = {"material": "PLA-CF Matte", "name": "PLA-CF Matte"}
    assert translate(record, KNOWN) == Setting("Generic", "PLA-CF", "Matte", None)
    without = KNOWN - {"PLA-CF"}
    assert translate(record, without) == Setting("Generic", "PLA", "CF Matte", None)
    assert translate({"material": "PLA-CF"}, without) == Setting("Generic", "PLA", "CF", None)


def test_an_unknown_material_keeps_the_colour_and_says_why():
    setting = translate({"brand": "Bambu Lab", "material": "WOOD", "color_rgba": "A0522DFF"}, KNOWN)
    assert setting.type is None
    assert setting.rgba == "A0522DFF"
    assert setting.subtype == ""
    assert "WOOD" in setting.reason


def test_defaults_and_character_cleaning():
    assert translate({"material": "PLA"}, KNOWN) == Setting("Generic", "PLA", "", None)
    assert translate({"material": "PLA", "color_rgba": "ff8000"}, KNOWN).rgba == "FF8000FF"
    dirty = {"brand": "Ac'me; Co=1", "material": "PLA", "name": "PLA Silk+ (dual)"}
    setting = translate(dirty, KNOWN)
    assert setting.vendor == "AcmeCo1"
    assert setting.subtype == "Silk+ dual"
    quoted = translate({"material": "PLA", "name": 'PLA  It\'s "odd"  '}, KNOWN)
    assert quoted.subtype == "Its odd"
    assert translate({"material": "PLA", "color_rgba": "nothex"}, KNOWN).rgba is None


# --- policy --------------------------------------------------------------------------------


def policy():
    return TagPolicy(heads={1: 0, 2: 1}, known_types=KNOWN)


def tick(
    p, lane=1, insert=True, state="read", record=BAMBU, printing=False, current=None, hold=False
):
    return p.on_tick(lane, insert, state, record, printing, current, hold)


def written(p):
    """Insert a Bambu spool on lane 1 and return the Setting it wrote."""
    action = tick(p)
    assert action[0] == "write"
    return action[2]


def test_a_new_read_writes_the_lanes_head_once():
    p = policy()
    assert tick(p) == ("write", 0, translate(BAMBU, KNOWN))
    assert tick(p) is None
    assert tick(p, lane=2, record=SNAPMAKER) == ("write", 1, translate(SNAPMAKER, KNOWN))


def test_a_different_uid_on_the_same_insert_writes_again():
    p = policy()
    written(p)
    assert tick(p, record=SNAPMAKER) == ("write", 0, translate(SNAPMAKER, KNOWN))


def test_the_same_uid_after_a_refill_writes_again():
    p = policy()
    setting = written(p)
    assert tick(p, insert=False, state="no_tag", record=None, printing=True) is None
    assert tick(p, insert=True) == ("write", 0, setting)


def test_no_record_or_no_tag_or_an_unhooked_lane_does_nothing():
    p = policy()
    assert tick(p, state="no_tag") is None
    assert tick(p, record=None) is None
    assert tick(p, state="searching") is None
    assert tick(p, lane=3) is None
    assert tick(p, insert=False) is None


def test_an_ejection_clears_what_the_tag_wrote_when_idle():
    p = policy()
    setting = written(p)
    assert tick(p, insert=False, state="no_tag", record=None, current=setting)[:2] == ("clear", 0)
    assert tick(p, insert=False, state="no_tag", record=None, current=None) is None


def test_a_setting_changed_by_hand_is_left_alone():
    p = policy()
    setting = written(p)
    by_hand = Setting("Generic", "PETG", "", "FFFFFFFF")
    assert tick(p, insert=False, state="no_tag", record=None, current=by_hand) is None
    # and the pending clear is gone: putting the written value back does not clear later
    assert tick(p, insert=False, state="no_tag", record=None, current=setting) is None


def test_the_clear_waits_for_the_print_and_happens_after():
    p = policy()
    setting = written(p)
    empty = {"insert": False, "state": "no_tag", "record": None, "current": setting}
    assert tick(p, printing=True, **empty) is None
    assert tick(p, printing=True, **empty) is None
    assert tick(p, printing=False, **empty)[:2] == ("clear", 0)


def test_a_refill_during_the_print_drops_the_pending_clear():
    p = policy()
    setting = written(p)
    empty = {"state": "no_tag", "record": None, "current": setting}
    assert tick(p, insert=False, printing=True, **empty) is None
    assert tick(p, insert=True, state="searching", record=None, printing=True) is None
    assert tick(p, insert=False, state="no_tag", record=None, current=setting)[:2] == ("clear", 0)
    p2 = policy()
    setting = written(p2)
    tick(p2, insert=False, state="no_tag", record=None, printing=True, current=setting)
    tick(p2, insert=True, state="no_tag", record=None, printing=True)
    assert tick(p2, insert=True, state="no_tag", record=None, current=setting) is None


def test_a_colour_only_write_clears_on_its_colour_alone():
    p = policy()
    wood = {"uid": "00000004", "brand": "X", "material": "WOOD", "color_rgba": "A0522DFF"}
    action = tick(p, record=wood)
    assert action[0] == "write" and action[2].type is None
    # the head kept whatever type it had; only the colour is the tag's
    current = Setting("Generic", "PLA", "", "A0522DFF")
    assert tick(p, insert=False, state="no_tag", record=None, current=current)[:2] == ("clear", 0)


def test_the_current_value_may_be_a_mapping_and_case_does_not_matter():
    p = policy()
    written(p)
    current = {"vendor": "bambulab", "type": "pla", "subtype": "basic", "rgba": "ff8000ff"}
    assert tick(p, insert=False, state="no_tag", record=None, current=current)[:2] == ("clear", 0)


def test_a_record_with_nothing_to_write_is_skipped_once_with_its_reason():
    p = policy()
    bare = {"uid": "00000005", "material": "WOOD"}
    action = tick(p, record=bare)
    assert action[0] == "skip" and action[1] == 0 and "WOOD" in action[2].reason
    assert tick(p, record=bare) is None
    assert tick(p, insert=False, state="no_tag", record=None) is None


def test_a_failed_write_is_not_retried_and_leaves_nothing_to_clear():
    p = policy()
    setting = written(p)
    p.note_failed(1)
    assert tick(p) is None  # the attempt is recorded
    assert tick(p, insert=False, state="no_tag", record=None, current=setting) is None


def test_a_failed_write_keeps_the_previous_write_for_the_clear():
    p = policy()
    first = written(p)
    assert tick(p, record=SNAPMAKER)[0] == "write"
    p.note_failed(1)
    assert tick(p, insert=False, state="no_tag", record=None, current=first)[:2] == ("clear", 0)


def test_note_failed_after_a_later_tick_changes_nothing():
    p = policy()
    setting = written(p)
    tick(p)
    p.note_failed(1)
    assert tick(p, insert=False, state="no_tag", record=None, current=setting)[:2] == ("clear", 0)


def test_a_failed_clear_is_not_retried():
    p = policy()
    setting = written(p)
    empty = {"insert": False, "state": "no_tag", "record": None, "current": setting}
    assert tick(p, **empty)[:2] == ("clear", 0)
    p.note_failed(1)
    assert tick(p, **empty) is None


def test_a_forget_then_a_re_read_of_the_same_uid_writes_again():
    p = policy()
    setting = translate(BAMBU, KNOWN)
    assert tick(p) == ("write", 0, setting)
    assert tick(p) is None  # repeated reads without a forget: once
    assert tick(p) is None
    assert tick(p, state="pending", record=None) is None  # forgotten, the filament still in
    assert tick(p) == ("write", 0, setting)  # the same uid read again
    assert tick(p) is None


def test_a_status_without_the_tag_does_not_re_arm_a_rewrite():
    p = policy()
    assert tick(p)[0] == "write"
    assert tick(p, state=None, record=None) is None
    assert tick(p, state="unknown", record=None) is None
    assert tick(p) is None  # the same uid: nothing written
    for state in ("pending", "searching", "reading", "no_tag"):
        assert tick(p, state=state, record=None) is None
        assert tick(p)[0] == "write", state


def test_a_re_read_after_a_forget_still_waits_under_hold():
    p = policy()
    assert tick(p)[0] == "write"
    assert tick(p, state="pending", record=None) is None
    assert tick(p, printing=True, hold=True) is None
    assert tick(p, printing=True, hold=True) is None
    assert tick(p, printing=True)[0] == "write"
    assert tick(p, printing=True) is None


def test_a_held_write_waits_and_writes_once_released():
    p = policy()
    assert tick(p, printing=True, hold=True) is None
    assert tick(p, printing=True, hold=True) is None
    assert tick(p, printing=True) == ("write", 0, translate(BAMBU, KNOWN))
    assert tick(p, printing=True) is None  # the same uid: nothing more


def test_a_held_write_is_dropped_if_the_spool_leaves_and_follows_a_new_uid():
    p = policy()
    assert tick(p, hold=True) is None
    assert tick(p, insert=False, state=None, record=None) is None  # left before the release
    assert tick(p, insert=False, state=None, record=None) is None
    other = dict(BAMBU, uid="00000009", color_rgba="00FF00FF")
    assert tick(p, record=other, hold=True) is None
    assert tick(p, record=other) == ("write", 0, translate(other, KNOWN))


def test_hold_only_concerns_writes():
    p = policy()
    nothing = dict(BAMBU, material="PEEK", name="PEEK", color_rgba=None)
    assert tick(p, record=nothing, hold=True)[0] == "skip"


def test_the_clear_carries_what_the_lane_wrote():
    p = policy()
    setting = written(p)
    action = tick(p, insert=False, state="no_tag", record=None, current=setting)
    assert action == ("clear", 0, setting)


def test_a_shorter_type_leading_the_name_is_not_kept_in_the_subtype():
    known = {"PLA", "PLA-CF", "PETG"}
    record = dict(BAMBU, material="PLA-CF", name="PLA Basic")
    setting = translate(record, known)
    assert (setting.type, setting.subtype) == ("PLA-CF", "Basic")
    assert translate(dict(record, name="PLA"), known).subtype == ""


def test_the_clear_resets_the_union_of_the_writes_since_the_last():
    # spool A written; ejected mid-print (the clear waits); spool B, colour only, before the end
    p = policy()
    a = written(p)
    assert tick(p, insert=False, state=None, record=None, printing=True, current=a) is None
    b_rec = dict(BAMBU, uid="0000000B", material="PEEK", name="PEEK", color_rgba="00FF00FF")
    action = tick(p, record=b_rec, printing=True, current=a)
    assert action[0] == "write" and action[2].type is None  # B sends its colour only
    on_head = Setting(a.vendor, a.type, a.subtype, "00FF00FF")
    action = tick(p, insert=False, state=None, record=None, current=on_head)
    assert action == ("clear", 0, on_head)  # A's triple and B's colour


def test_a_union_drops_what_was_changed_by_hand_since():
    p = policy()
    a = written(p)
    by_hand = Setting("Generic", "PETG", "", a.rgba)
    assert tick(p, insert=False, state=None, record=None, printing=True, current=by_hand) is None
    b_rec = dict(BAMBU, uid="0000000B", material="PEEK", name="PEEK", color_rgba="00FF00FF")
    tick(p, record=b_rec, printing=True, current=by_hand)
    now = Setting("Generic", "PETG", "", "00FF00FF")
    action = tick(p, insert=False, state=None, record=None, current=now)
    assert action == ("clear", 0, translate(b_rec, KNOWN))  # B's colour alone: PETG stays


def test_a_full_write_without_colour_keeps_the_earlier_colour():
    p = policy()
    a = written(p)
    second = dict(BAMBU, uid="00000002", color_rgba=None)
    assert tick(p, record=second, current=a)[0] == "write"
    action = tick(p, insert=False, state=None, record=None, current=a)
    assert action == ("clear", 0, a)


def test_a_failed_write_restores_the_earlier_union():
    p = policy()
    a = written(p)
    second = dict(BAMBU, uid="00000002", material="PEEK", name="PEEK", color_rgba="00FF00FF")
    assert tick(p, record=second, current=a)[0] == "write"
    p.note_failed(1)
    action = tick(p, insert=False, state=None, record=None, current=a)
    assert action == ("clear", 0, a)
