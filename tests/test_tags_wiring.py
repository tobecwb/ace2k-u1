"""The tags in the adapter: the U1's known types read at hook time, the policy fed from the bay
watch, each decision run as SET_PRINT_FILAMENT_CONFIG from a reactor timer, the refusals, the
status lines and the apply_tags switch. Synthetic records only."""

from pathlib import Path

import ace2k_u1
import ace2k_u1_tags
import fakes
from test_check import Config, Gcode, Printer

TABLE = (
    "generic_PLA_generic_print_temp",
    "Snapmaker_PLA_Matte_print_temp",
    "generic_PETG_generic_print_temp",
)
BAMBU = dict(
    format="bambu",
    uid="00000001",
    brand="Bambu Lab",
    material="PLA",
    name="PLA Basic",
    color_rgba="FF0000FF",
)
CMD = "SET_PRINT_FILAMENT_CONFIG"
BAMBU_WRITE = (
    f"{CMD} CONFIG_EXTRUDER=0 VENDOR=BambuLab FILAMENT_TYPE=PLA FILAMENT_SUBTYPE='Basic'"
    " FILAMENT_COLOR_RGBA=FF0000FF FORCE=1"
)
CLEAR0 = (
    f"{CMD} CONFIG_EXTRUDER=0 VENDOR=NONE FILAMENT_TYPE=NONE FILAMENT_SUBTYPE=NONE"
    " FILAMENT_COLOR_RGBA=FFFFFFFF FORCE=1"
)


def build(table=TABLE, ptc=True, enabled=True, state="standby"):
    reactor = fakes.FakeReactor()
    left = fakes.FakeFeed(reactor, filament_ch=(1, 0))
    right = fakes.FakeFeed(reactor, filament_ch=(2, 3))
    ace = fakes.FakeAce2k()
    adapter = ace2k_u1.Adapter(
        reactor,
        fakes.fake_module(),
        ace,
        [left, right],
        (1, 2, 3, 4),
        feed_speed=30.0,
        head_budget_mm=2000.0,
        poll_s=0.2,
        extruders=ace2k_u1.extruder_names(fakes.extruder_list()),
    )
    task = fakes.FakePrintTaskConfig() if ptc else None
    gcode = fakes.FakeGcode(task)
    adapter.gcode = gcode
    assert adapter.hook_all() == []
    stats = fakes.FakePrintStats(state)
    toolhead = fakes.FakeToolhead("extruder3")  # head 3 active; lanes 1-3 feed heads 0-2
    adapter.setup_tags(task, fakes.FakeFilamentParameters(table), stats, enabled, toolhead)
    adapter.toolhead_fake = toolhead
    return adapter, reactor, ace, task, gcode, stats


def tag(ace, lane, record, state="read"):
    ace.lanes[lane]["insert"] = True
    ace.lanes[lane]["tag"] = dict(state=state, uid=record.get("uid"), source="tag", record=record)


def eject(ace, lane):
    ace.lanes[lane]["insert"] = False
    ace.lanes[lane]["tag"] = dict(fakes.NO_TAG)


def tick(adapter, reactor):
    """One bay-watch tick, then whatever it scheduled."""
    adapter.watch(reactor.now)
    reactor.run_timers()
    reactor.now += 0.2


def test_the_known_types_are_the_tables_types_read_at_hook_time():
    adapter, *_ = build()
    assert adapter.tags is not None and adapter.tags_off is None
    assert adapter.tags.known_types == {"PLA", "PETG"}


def test_no_known_type_turns_the_tags_off_with_a_line_and_keeps_the_hooks():
    adapter, reactor, ace, task, gcode, _ = build(table=())
    assert adapter.tags is None
    assert gcode.lines == ["ace2k_u1: tags not applied: no filament types in [filament_parameters]"]
    assert len(adapter.hooked) == 4
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    assert gcode.scripts == []


def test_an_absent_print_task_config_turns_the_tags_off_with_a_line():
    adapter, reactor, ace, task, gcode, _ = build(ptc=False)
    assert adapter.tags is None and len(adapter.hooked) == 4
    assert gcode.lines == ["ace2k_u1: tags not applied: [print_task_config] absent"]


def test_a_changed_u1_command_turns_the_tags_off_with_the_reason():
    class Changed(fakes.FakePrintTaskConfig):
        def cmd_SET_PRINT_FILAMENT_CONFIG(self, gcmd):  # noqa: N802 — the U1's own name
            gcmd.get_int("CONFIG_EXTRUDER")
            gcmd.get("FILAMENT_TYPE", None)

    adapter, reactor, ace, task, gcode, _ = build()
    gcode.lines.clear()
    assert adapter.setup_tags(Changed(), fakes.FakeFilamentParameters(TABLE), None) is not None
    assert adapter.tags is None
    assert "does not read VENDOR" in gcode.lines[0]
    assert "does not read FILAMENT_COLOR_RGBA" in gcode.lines[0]


def test_a_read_writes_the_head_once_from_a_timer():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    adapter.watch(reactor.now)
    assert gcode.scripts == []  # not from the watch itself
    reactor.run_timers()
    assert gcode.scripts == [BAMBU_WRITE]
    assert task.status["filament_vendor"][0] == "BambuLab"
    assert task.status["filament_sub_type"][0] == "Basic"
    for _ in range(3):
        tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]
    assert adapter.applied[0] == "BambuLab PLA Basic FF0000FF"


def test_a_forget_then_the_same_tag_read_again_writes_the_head_again():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]
    tag(ace, 0, {}, state="pending")  # ACE_RFID_FORGET: back to pending, the filament still in
    ace.lanes[0]["tag"]["record"] = None
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]
    tag(ace, 0, BAMBU)  # the unit reads the same tag on the lane's next move
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE, BAMBU_WRITE]


def test_the_write_goes_to_the_lanes_head():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 2, dict(BAMBU, uid="00000003"))
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE.replace("CONFIG_EXTRUDER=0", "CONFIG_EXTRUDER=2")]


def test_an_empty_subtype_is_written_quoted_empty():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, dict(BAMBU, name="PLA"))
    tick(adapter, reactor)
    assert "FILAMENT_SUBTYPE='' " in gcode.scripts[0]
    assert task.status["filament_sub_type"][0] == ""


def test_a_subtype_with_a_space_is_written_quoted():
    setting = ace2k_u1_tags.Setting("Snapmaker", "PLA", "Full Spectrum", "FFFFFFFF")
    assert ace2k_u1.tag_gcode(1, setting) == (
        f"{CMD} CONFIG_EXTRUDER=1 VENDOR=Snapmaker FILAMENT_TYPE=PLA"
        " FILAMENT_SUBTYPE='Full Spectrum' FILAMENT_COLOR_RGBA=FFFFFFFF FORCE=1"
    )
    task = fakes.FakePrintTaskConfig()
    fakes.FakeGcode(task).run_script(ace2k_u1.tag_gcode(1, setting))
    assert task.status["filament_sub_type"][1] == "Full Spectrum"


def test_a_colour_only_write_sends_the_colour_alone_and_says_why():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, dict(BAMBU, material="PEEK", name="PEEK", color_rgba="00FF00FF"))
    tick(adapter, reactor)
    assert gcode.scripts == [f"{CMD} CONFIG_EXTRUDER=0 FILAMENT_COLOR_RGBA=00FF00FF FORCE=1"]
    assert any("colour only" in line and "PEEK" in line for line in gcode.lines)
    assert task.status["filament_type"][0] == "NONE"


def test_nothing_writable_is_a_line_and_no_gcode():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, dict(BAMBU, material="PEEK", name="PEEK", color_rgba=None))
    tick(adapter, reactor)
    assert gcode.scripts == []
    assert any("tag not applied" in line and "PEEK" in line for line in gcode.lines)


def test_an_ejection_clears_the_head_once_idle():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    eject(ace, 0)
    tick(adapter, reactor)
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE, CLEAR0]
    assert task.status["filament_vendor"][0] == "NONE"
    assert adapter.applied[0] == "none"


def test_the_clear_waits_while_printing_or_paused():
    adapter, reactor, ace, task, gcode, stats = build()
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    stats.state = "printing"
    eject(ace, 0)
    tick(adapter, reactor)
    stats.state = "paused"
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]
    stats.state = "complete"
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE, CLEAR0]


def test_a_setting_changed_by_hand_is_not_cleared():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    task.status["filament_sub_type"][0] = "Matte"  # by hand on the screen
    eject(ace, 0)
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]


def test_the_head_setting_is_compared_trimmed_and_case_insensitively():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    task.status["filament_vendor"][0] = " bambulab "
    task.status["filament_color_rgba"][0] = "ff0000ff"
    eject(ace, 0)
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE, CLEAR0]


def test_an_unreadable_head_setting_skips_the_tick_and_keeps_the_clear():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    task.fail = RuntimeError("status")
    eject(ace, 0)
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]
    task.fail = None
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE, CLEAR0]


def test_a_refused_gcode_is_logged_never_raised_and_leaves_nothing_to_clear(caplog):
    adapter, reactor, ace, task, gcode, _ = build()
    gcode.fail = RuntimeError("official filament")
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)  # does not raise
    refused = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(refused) == 1 and "official filament" in refused[0]
    assert gcode.lines == []  # the U1 shows its own G-code error: not repeated on the console
    assert adapter.applied[0].startswith("not applied")
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]  # not retried
    gcode.fail = None
    eject(ace, 0)
    tick(adapter, reactor)
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]  # nothing the tag wrote: nothing cleared


def test_status_shows_the_tag_and_what_was_applied():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    lines = []
    adapter.status_lines(lines.append)
    assert lines[0].endswith(" tag=read Bambu Lab PLA Basic FF0000FF applied=none")
    tick(adapter, reactor)
    lines.clear()
    adapter.status_lines(lines.append)
    assert "applied=BambuLab PLA Basic FF0000FF" in lines[0]
    assert " tag=unknown applied=none" in lines[1]


def test_apply_tags_false_sends_nothing():
    adapter, reactor, ace, task, gcode, _ = build(enabled=False)
    assert adapter.tags is None and adapter.tags_off == "apply_tags: False"
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    eject(ace, 0)
    tick(adapter, reactor)
    assert gcode.scripts == []


def test_apply_tags_is_read_from_the_config():
    class NoTags(Config):
        def getboolean(self, name, default):
            return False if name == "apply_tags" else default

    reactor = fakes.FakeReactor()
    objects = {
        "gcode": Gcode(),
        "ace2k": fakes.FakeAce2k(),
        "print_task_config": fakes.FakePrintTaskConfig(),
        "filament_parameters": fakes.FakeFilamentParameters(TABLE),
        "print_stats": fakes.FakePrintStats(),
    }
    on = ace2k_u1.ACE2kU1(Config(Printer(reactor, objects, [fakes.FakeFeed(reactor)])))
    on._hook(reactor.now)
    assert on.adapter.tags is not None
    off = ace2k_u1.ACE2kU1(NoTags(Printer(reactor, objects, [fakes.FakeFeed(reactor)])))
    off._hook(reactor.now)
    assert off.adapter.tags is None and off.adapter.tags_off == "apply_tags: False"


def test_the_cfg_carries_apply_tags():
    cfg = Path(__file__).resolve().parents[1] / "config" / "ace2k-u1.cfg"
    lines = cfg.read_text(encoding="utf-8").splitlines()
    assert any(
        line.split("#")[1].strip() == "apply_tags: True"
        for line in lines
        if line.startswith("#apply_tags")
    )


def test_a_lane_whose_gcode_waits_for_the_lock_gets_no_new_decision():
    # run_script may wait for the G-code lock while the bay watch goes on: the lane stays
    # queued, so a refusal reaches the policy before that lane's next decision
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    adapter.watch(reactor.now)
    seen = []

    def run_script(script):
        seen.append(script)
        eject(ace, 0)
        adapter.watch(reactor.now)  # a watch tick while the G-code waits
        raise RuntimeError("refused")

    gcode.run_script = run_script
    reactor.run_timers()
    assert seen == [BAMBU_WRITE]
    assert adapter.tag_pending == {}
    tick(adapter, reactor)
    assert seen == [BAMBU_WRITE]  # nothing the tag wrote: nothing to clear


def run(adapter, reactor, lane, action):
    """Queue one action for the tag timer, as the watch does, and run it."""
    adapter.tag_pending[lane] = action
    reactor.update_timer(adapter.tag_timer, reactor.NOW)
    reactor.run_timers()


def test_a_head_out_of_range_is_not_sent():
    adapter, reactor, ace, task, gcode, _ = build()
    run(adapter, reactor, 0, ("write", 4, ace2k_u1_tags.Setting("Generic", "PLA", "", None)))
    assert gcode.scripts == []
    assert len(gcode.lines) == 1 and "CONFIG_EXTRUDER '4' not within 0..3" in gcode.lines[0]


def test_a_colour_not_eight_hex_digits_is_not_sent():
    adapter, reactor, ace, task, gcode, _ = build()
    run(adapter, reactor, 0, ("write", 0, ace2k_u1_tags.Setting("Generic", None, "", "FF00")))
    assert gcode.scripts == []
    assert len(gcode.lines) == 1 and "not 8 hex digits" in gcode.lines[0]


def test_a_quote_in_any_value_is_not_sent():
    adapter, reactor, ace, task, gcode, _ = build()
    run(adapter, reactor, 0, ("write", 0, ace2k_u1_tags.Setting("Generic", "PLA", "it's", None)))
    run(adapter, reactor, 1, ("write", 1, ace2k_u1_tags.Setting('A"B', "PLA", "", None)))
    assert gcode.scripts == []
    assert len(gcode.lines) == 2
    assert "FILAMENT_SUBTYPE" in gcode.lines[0] and "VENDOR" in gcode.lines[1]


def test_a_check_failure_is_recorded_like_a_failed_attempt():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, dict(BAMBU, color_rgba="FF0000FF"))
    adapter.watch(reactor.now)
    action = adapter.tag_pending[0]
    adapter.tag_pending[0] = ("write", 7, action[2])  # a head the U1 would refuse
    reactor.run_timers()
    assert gcode.scripts == [] and "not sent" in gcode.lines[0]
    assert adapter.applied[0].startswith("not applied")
    eject(ace, 0)
    tick(adapter, reactor)
    tick(adapter, reactor)
    assert gcode.scripts == []  # nothing the tag wrote: nothing to clear


def test_the_checks_the_u1_makes():
    check = ace2k_u1.check_params
    ok = ace2k_u1.tag_params(0, ace2k_u1_tags.Setting("Snapmaker", "PLA", "Full Spectrum", None))
    assert check(ok, ace2k_u1.TAG_QUOTED) == []
    assert check(ace2k_u1.clear_params(3)) == []
    colour_only = {"CONFIG_EXTRUDER": "0", "FILAMENT_COLOR_RGBA": "00FF00FF", "FORCE": "1"}
    assert check(colour_only) == []
    partial = dict(colour_only, VENDOR="Generic", FILAMENT_TYPE="PLA")
    assert check(partial) == ["VENDOR, FILAMENT_TYPE and FILAMENT_SUBTYPE not together"]
    assert check(dict(colour_only, FORCE="0")) == ["FORCE not 1"]
    assert check(dict(colour_only, CONFIG_EXTRUDER="-1"))[0].startswith("CONFIG_EXTRUDER")
    # a space is fine only in a quoted value
    assert check(ok) and "FILAMENT_SUBTYPE" in check(ok)[0]


def test_a_write_to_the_active_extruder_waits_while_printing_then_applies_on_the_pause():
    adapter, reactor, ace, task, gcode, stats = build()
    adapter.toolhead_fake.name = "extruder"  # head 0 active
    stats.state = "printing"
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    tick(adapter, reactor)
    assert gcode.scripts == []
    stats.state = "paused"
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]  # the same uid: nothing more


def test_a_held_write_is_dropped_if_the_spool_left():
    adapter, reactor, ace, task, gcode, stats = build()
    adapter.toolhead_fake.name = "extruder"
    stats.state = "printing"
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    eject(ace, 0)
    tick(adapter, reactor)
    stats.state = "complete"
    tick(adapter, reactor)
    tick(adapter, reactor)
    assert gcode.scripts == []


def test_a_write_to_another_head_while_printing_is_at_once():
    adapter, reactor, ace, task, gcode, stats = build()  # head 3 active
    stats.state = "printing"
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]


def test_an_unknown_active_extruder_holds_every_head_while_printing():
    adapter, reactor, ace, task, gcode, stats = build()
    adapter.toolhead_fake.name = "no_such_extruder"
    stats.state = "printing"
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    assert gcode.scripts == []
    stats.state = "standby"
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE]


def test_a_failing_lane_does_not_stall_the_others():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tag(ace, 1, dict(BAMBU, uid="00000002"))
    on_tick = adapter.tags.on_tick

    def flaky(lane, *args):
        if lane == 1:
            raise RuntimeError("lane 2 broke")
        return on_tick(lane, *args)

    adapter.tags.on_tick = flaky
    tick(adapter, reactor)  # lane 2 (index 1) raises after lane 1 was queued
    assert gcode.scripts == [BAMBU_WRITE]


def test_a_colour_only_write_is_cleared_on_its_colour_alone():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, dict(BAMBU, material="PCTG", name="PCTG", color_rgba="00FF00FF"))
    tick(adapter, reactor)
    # set by hand on the screen afterwards: vendor, type and subtype; the colour kept
    task.status["filament_vendor"][0] = "Generic"
    task.status["filament_type"][0] = "PETG"
    task.status["filament_sub_type"][0] = ""
    eject(ace, 0)
    tick(adapter, reactor)
    assert gcode.scripts[-1] == f"{CMD} CONFIG_EXTRUDER=0 FILAMENT_COLOR_RGBA=FFFFFFFF FORCE=1"
    assert task.status["filament_type"][0] == "PETG"
    assert task.status["filament_vendor"][0] == "Generic"
    assert task.status["filament_color_rgba"][0] == "FFFFFFFF"


def test_a_write_without_a_colour_is_cleared_without_one():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, dict(BAMBU, color_rgba=None))
    tick(adapter, reactor)
    task.status["filament_color_rgba"][0] = "123456FF"  # by hand
    eject(ace, 0)
    tick(adapter, reactor)
    assert gcode.scripts[-1] == (
        f"{CMD} CONFIG_EXTRUDER=0 VENDOR=NONE FILAMENT_TYPE=NONE FILAMENT_SUBTYPE=NONE FORCE=1"
    )
    assert task.status["filament_color_rgba"][0] == "123456FF"


def test_the_comment_and_checksum_characters_are_refused_quoted_too():
    for ch in ";#*":
        params = ace2k_u1.tag_params(
            0, ace2k_u1_tags.Setting("Generic", "PLA", f"Matte{ch}x", None)
        )
        problems = ace2k_u1.check_params(params, ace2k_u1.TAG_QUOTED)
        assert len(problems) == 1 and "FILAMENT_SUBTYPE" in problems[0], ch


def test_a_failing_first_lane_does_not_stall_the_next():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tag(ace, 1, dict(BAMBU, uid="00000002"))
    on_tick = adapter.tags.on_tick

    def flaky(lane, *args):
        if lane == 0:
            raise RuntimeError("lane 1 broke")
        return on_tick(lane, *args)

    adapter.tags.on_tick = flaky
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE.replace("CONFIG_EXTRUDER=0", "CONFIG_EXTRUDER=1")]


def test_a_spool_replaced_mid_print_is_cleared_of_both_after_it():
    adapter, reactor, ace, task, gcode, stats = build()  # head 3 active: head 0 is not held
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    stats.state = "printing"
    eject(ace, 0)
    tick(adapter, reactor)
    tag(ace, 0, dict(BAMBU, uid="0000000B", material="PEEK", name="PEEK", color_rgba="00FF00FF"))
    tick(adapter, reactor)
    assert gcode.scripts[-1] == f"{CMD} CONFIG_EXTRUDER=0 FILAMENT_COLOR_RGBA=00FF00FF FORCE=1"
    stats.state = "complete"
    eject(ace, 0)
    tick(adapter, reactor)
    assert gcode.scripts[-1] == CLEAR0
    assert [task.status[k][0] for k in ace2k_u1.TAG_STATUS.values()] == [
        "NONE",
        "NONE",
        "NONE",
        "FFFFFFFF",
    ]


def reentering(adapter, reactor, gcode, during=None):
    """run_script waiting for the G-code lock: the reactor runs the tag timer again meanwhile
    (as it runs any due timer while a caller waits), once per script, after during()."""
    sent = gcode.run_script

    def run_script(script):
        sent(script)
        if during is not None:
            during()
        adapter._run_tags(reactor.now)

    gcode.run_script = run_script


def test_a_reentered_tag_timer_sends_the_clear_once():
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)
    eject(ace, 0)
    reentering(adapter, reactor, gcode)
    tick(adapter, reactor)  # does not raise
    tick(adapter, reactor)
    assert gcode.scripts == [BAMBU_WRITE, CLEAR0]
    assert adapter.tag_pending == {} and adapter.tag_running == set()


def test_a_lane_queued_while_another_waits_for_the_lock_runs_once():
    # the batch unload: lane 1's clear waits for the lock; meanwhile the watch queues lane 2's
    # clear and the reactor runs the tag timer again, which takes lane 2 and leaves lane 1
    adapter, reactor, ace, task, gcode, _ = build()
    tag(ace, 0, BAMBU)
    tag(ace, 1, dict(BAMBU, uid="00000002"))
    tick(adapter, reactor)
    assert len(gcode.scripts) == 2
    eject(ace, 0)
    adapter.watch(reactor.now)
    assert list(adapter.tag_pending) == [0]
    calls = []

    def during():
        if not calls:
            calls.append(1)
            eject(ace, 1)
            adapter.watch(reactor.now)  # a watch tick while lane 1's G-code waits

    reentering(adapter, reactor, gcode, during)
    reactor.run_timers()
    clear1 = CLEAR0.replace("CONFIG_EXTRUDER=0", "CONFIG_EXTRUDER=1")
    assert gcode.scripts[2:] == [CLEAR0, clear1]
    assert adapter.tag_pending == {} and adapter.tag_running == set()
    tick(adapter, reactor)
    assert gcode.scripts[2:] == [CLEAR0, clear1]


def test_the_tag_timer_never_raises_when_the_bookkeeping_does(caplog):
    adapter, reactor, ace, task, gcode, _ = build()
    gcode.fail = RuntimeError("refused")

    def broken(lane):
        raise RuntimeError("note_failed broke")

    adapter.tags.note_failed = broken
    tag(ace, 0, BAMBU)
    tick(adapter, reactor)  # does not raise
    assert gcode.scripts == [BAMBU_WRITE]
    assert adapter.tag_pending == {} and adapter.tag_running == set()

    class Broken(dict):
        def pop(self, *args):
            raise KeyError("pop broke")

    adapter.tag_pending = Broken({1: ("clear", 1, None)})
    when = adapter._run_tags(reactor.now)  # does not raise
    assert when == reactor.NEVER
    assert any(r.getMessage() == "ace2k_u1: tags" and r.exc_info for r in caplog.records)
