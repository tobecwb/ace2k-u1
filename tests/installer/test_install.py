"""A new install: every unit named and flashed with Klipper stopped once, then started once."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fake_u1 import (
    ACE2K_V,
    CFG_TOOL,
    DEBUG_MSG,
    NEW_ONE,
    PORT_B,
    UID_A,
    UID_B,
    ace2k_state,
    local_release,
    unit_a,
)

if TYPE_CHECKING:
    from fake_u1 import FakeU1, Json

UNVERIFIED = {"exit": 2, "print": "Klipper's config has no [ace2k] on this port."}


def _rel(tmp_path: Path) -> Path:
    return local_release(tmp_path / "rel")


def test_from_the_original_firmware_one_stop_and_one_start(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert u1.klipper_log() == ["stop", "start"] and u1.restarts() == 1
    [argv] = u1.flasher_calls()
    assert argv[0] == str(u1.state_dir / "flash" / f"ace2k-{ACE2K_V}.bin")
    assert argv[argv.index("--port") + 1] == u1.port
    assert argv[argv.index("--calibration-dir") + 1] == str(u1.state_dir / "calibration")
    assert "--yes" not in argv
    assert u1.state()["ace2k"] == f"v{ACE2K_V}"
    assert "Done" in p.out and "hooked lanes" in p.out


SPLITTER_REMINDER = "Measure each shared head's path to its splitter and set headN_splitter_mm"
SHARED = 'shared_head "$KCFG/ace2k-u1.cfg" && echo shared'


@pytest.mark.parametrize(
    "lanes, said",
    [
        ("lanes: a:1+a:2, a:3", True),
        ("lanes: 1, 2, 3, 4", False),
        ("lanes: 1, 2  # or a:1+a:2", False),
        ("lanes: a:1+a:2  # two on one head", True),
        ("lanes = a:1+a:2, a:3", True),  # Klipper takes = as well as :
        ("lanes=1, 2", False),
        ("lanes: a:1,\n  a:2+a:3", True),  # its value continued on indented lines
        ("lanes: 1,\n  2  # or a:1+a:2\nfeed_speed: 70", False),
        ("lanes: 1, 2\nfeed_speed: 70\n  # a:1+a:2", False),  # past the value's end
        ("lanes: 1, 2  ; or a:1+a:2", False),  # Klipper's other inline comment
        ("lanes: 1,\n  2  ; or a:1+a:2", False),
        ("lanes: a:1+a:2  ; two on one head", True),
        ("lanes: 1,\n\n  2+3", True),  # Klipper reads on past a blank line
        ("lanes: 1,\n   \n  2+3", True),  # and past a line of spaces
        ("lanes: 1,\n# a note\n  2+3", True),  # and past a comment line
    ],
)
def test_a_shared_head_is_read_from_the_lanes_value(u1: FakeU1, lanes: str, said: bool) -> None:
    # the closing summary's test, on the file an update keeps as the operator wrote it
    (u1.kcfg / "ace2k-u1.cfg").write_text(f"[ace2k_u1]\n{lanes}\n")
    p = u1.source(SHARED)
    assert ("shared" in p.stdout) is said, p.out


def test_a_commented_lanes_line_or_no_cfg_is_not_shared(u1: FakeU1) -> None:
    assert "shared" not in u1.source(SHARED).stdout  # no file
    (u1.kcfg / "ace2k-u1.cfg").write_text("[ace2k_u1]\n#lanes: a:1+a:2\n")
    assert "shared" not in u1.source(SHARED).stdout


def test_the_closing_summary_reminds_of_the_splitter_when_a_head_is_shared(
    u1: FakeU1, tmp_path: Path
) -> None:
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=["a:1+a:2", "", "", "", "y"])
    assert p.returncode == 0, p.out
    assert "lanes: a:1+a:2, a:3, a:4, -" in (u1.kcfg / "ace2k-u1.cfg").read_text()
    assert SPLITTER_REMINDER in p.out and "(see docs/configuration.md)." in p.out


def test_one_lane_per_head_keeps_the_summary_silent(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert SPLITTER_REMINDER not in p.out


def test_yes_runs_without_a_terminal(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), "--yes", tty=False)
    assert p.returncode == 0, p.out
    assert "--yes" in u1.flasher_calls()[0]


def test_no_terminal_and_no_yes_changes_nothing(u1: FakeU1, tmp_path: Path) -> None:
    before = u1.digest()
    p = u1.run("--from", _rel(tmp_path), tty=False)
    assert p.returncode == 1 and u1.digest() == before and u1.flasher_calls() == []


def test_port_takes_one_unit_on_that_cable_named_a(u1: FakeU1, tmp_path: Path) -> None:
    b = u1.add_port(PORT_B)
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, b))
    p = u1.run("--from", _rel(tmp_path), "--port", b, answers=NEW_ONE)
    assert p.returncode == 0, p.out
    [argv] = u1.flasher_calls()  # no --identify: one unit needs no blink
    assert argv[argv.index("--port") + 1] == b
    cfg = (u1.kcfg / "ace2k.cfg").read_text()
    assert f"[mcu ace2k_a]\nserial: {b}\n" in cfg and u1.port not in cfg


def test_no_adapter_stops(u1: FakeU1, tmp_path: Path) -> None:
    Path(u1.port).unlink()
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1 and "docs/cable.md" in p.out


def test_a_missing_debug_flag_is_created_and_explained(u1: FakeU1, tmp_path: Path) -> None:
    (u1.root / "oem" / ".debug").unlink()
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=["y", *NEW_ONE])
    assert p.returncode == 0, p.out
    assert (u1.root / "oem" / ".debug").exists() and DEBUG_MSG in p.out


def test_a_refusal_with_the_unit_unchanged_stops_and_starts_klipper_again(
    u1: FakeU1, tmp_path: Path
) -> None:
    # the flasher's status 3 alone decides: its text is not read
    u1.flasher({"exit": 3, "print": "No unit answered DISCOVER on X"})
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1 and "changed nothing on the unit" in p.out
    [bk] = u1.backups()
    assert str(bk) in p.out and "run the installer again" in p.out
    assert u1.klipper_log() == ["stop", "start"] and u1.state() == {"in_progress": "install"}


def test_a_failure_after_the_write_stops_and_the_next_run_finishes(
    u1: FakeU1, tmp_path: Path
) -> None:
    u1.flasher(
        {
            "exit": 1,
            "print": "The update failed: chunk 3/9: no answer.\n"
            "The factory firmware may still hold this update and refuse a new one: power-cycle "
            "the unit, then run the same command again.",
        }
    )
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1 and "did not finish" in p.out and "run the installer again" in p.out
    # the installer's own line defers to the flasher's: it does not promise that a new update is
    # accepted at once, which the factory firmware refuses until it is power-cycled
    own = next(line for line in p.out.splitlines() if "The flash did not finish" in line)
    assert "accepts a new update" not in own
    assert "If it says to power-cycle the unit, do that first." in own
    assert (
        (u1.extras / "ace2k.py").exists()
        and u1.state() == {"in_progress": "install"}
        and u1.klipper_log() == ["stop", "start"]  # Klipper usable again, on its old config
    )
    # the next run says the install did not finish, continues it, and the flasher finds the unit
    # in recovery
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=["y", *NEW_ONE])
    assert p.returncode == 0, p.out
    assert "A previous run did not finish." in p.out and "Continue it?" in p.out
    assert "Update (u)" not in p.out
    argv = u1.flasher_calls()[-1]
    assert argv[argv.index("--port") + 1] == u1.port
    assert u1.restarts() == 2 and u1.state()["ace2k"] == f"v{ACE2K_V}"


def test_a_failure_whose_text_says_nothing_changed_is_still_a_failure(
    u1: FakeU1, tmp_path: Path
) -> None:
    # status 1: the unit may have changed, whatever the words
    u1.flasher({"exit": 1, "print": "Something failed; nothing changed."})
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1 and "did not finish" in p.out


def test_the_nothing_answered_text_with_status_1_is_not_taken_for_ace2k(
    u1: FakeU1, tmp_path: Path
) -> None:
    u1.flasher({**_nothing_answered(u1), "exit": 1})
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1 and "may already run ace2k" not in p.out
    assert len(u1.flasher_calls()) == 1


def _nothing_answered(u1: FakeU1) -> Json:
    return {
        "exit": 4,
        "print": f"Nothing on {u1.port} answered: not the factory firmware, not "
        "the bootloader's recovery, and Klipper does not report ace2k on "
        "it.  Check the port, the cable and the unit's power; nothing changed.",
    }


def test_more_than_four_unit_cables_stop_before_any_blink(u1: FakeU1, tmp_path: Path) -> None:
    for n in range(4):
        u1.add_port(f"usb-1a86_USB_Single_Serial_EXTRA{n}-if00")
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1
    assert "5 unit cables under /dev/serial/by-id" in p.out
    assert "ace2k drives at most four" in p.out and "nothing changed" in p.out
    assert u1.flasher_calls() == [] and list(u1.kcfg.glob("ace2k*.cfg")) == []


def test_nothing_answering_on_the_cable_stops(u1: FakeU1, tmp_path: Path) -> None:
    # the flasher reaches ace2k on a free port itself: its 4 means nothing answered at all
    u1.flasher(_nothing_answered(u1))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1
    assert f"Nothing answered on {u1.port}: the unit may be off or unplugged" in p.out
    assert list(u1.kcfg.glob("ace2k*.cfg")) == [] and u1.klipper_log() == ["stop", "start"]
    assert len(u1.flasher_calls()) == 1


def test_a_new_install_tells_the_flasher_the_config_follows(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    assert u1.run("--from", _rel(tmp_path), answers=NEW_ONE).returncode == 0
    [argv] = u1.flasher_calls()
    assert "--config-follows" in argv


@pytest.mark.parametrize("knows", ['["--yes"]', '["--yes", "--config-follows"]'])
def test_an_older_flasher_is_refused_before_any_change(
    u1: FakeU1, tmp_path: Path, knows: str
) -> None:
    # one older than several units cannot reach ace2k over the cable: on the free port it ends
    # with its 4, and an option it does not know with argparse's 2, read as "flashed"
    f = u1.fake / "ace2k_flash.pyz"
    stub = f.read_text()
    older = stub.replace('KNOWS = ["--yes", "--config-follows", "--identify"]', f"KNOWS = {knows}")
    assert older != stub
    f.write_text(older)
    before = u1.digest()
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1 and "--identify" in p.out and "nothing changed" in p.out
    assert u1.digest() == before and u1.flasher_calls() == []


def test_the_extra_restart_gets_the_whole_wait(u1: FakeU1, tmp_path: Path) -> None:
    # the reload's error seen on the only try of the wait: the new start still gets its own
    u1.flasher(UNVERIFIED)
    u1.after_start({"klippy_state": "error"}, unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE, ACE2K_WAIT_TRIES="1")
    assert p.returncode == 0, p.out
    assert "One more restart" in p.out and u1.restarts() == 2


def test_the_run_s_own_empty_backup_is_kept(u1: FakeU1, tmp_path: Path) -> None:
    own = u1.state_dir / "backup" / "29990101-000000-1"
    own.mkdir(parents=True)
    u1.source(f'BK="{own}"; prune_backups')
    assert own.exists()


def test_the_run_s_own_backup_survives_the_keep_five_pass(u1: FakeU1, tmp_path: Path) -> None:
    for n in range(6):
        (u1.state_dir / "backup" / f"2030010{n}-000000-1" / "f").mkdir(parents=True)
    own = u1.state_dir / "backup" / "19700101-000000-1"
    (own / "f").mkdir(parents=True)
    u1.source(f'BK="{own}"; prune_backups')
    assert own.exists()


def test_empty_backups_are_not_kept(u1: FakeU1, tmp_path: Path) -> None:
    old = u1.state_dir / "backup" / "20200101-000000-1"
    old.mkdir(parents=True)
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    assert u1.run("--from", _rel(tmp_path), answers=NEW_ONE).returncode == 0
    assert not old.exists()


def test_klipper_stopped_by_its_own_reload_gets_one_more_restart(
    u1: FakeU1, tmp_path: Path
) -> None:
    # The unit still holds the config of an earlier Klipper: Klipper resets it and reloads
    # inside its own process, which on the U1 ends in an error; a start through init connects.
    u1.flasher(UNVERIFIED)
    u1.after_start({"klippy_state": "error"}, unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert "One more restart" in p.out
    assert u1.klipper_log() == ["stop", "start", "restart"]


def test_an_error_before_the_restart_does_not_add_one(u1: FakeU1, tmp_path: Path) -> None:
    u1.moonraker({**json.loads((u1.fake / "moonraker.json").read_text()), "klippy_state": "error"})
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert "One more restart" not in p.out and u1.restarts() == 1


def test_klipper_never_reporting_the_version_stops(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    u1.after_start({"klippy_state": "error"})
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1 and f"did not report ace2k {ACE2K_V}" in p.out
    assert "run the installer again" in p.out and u1.state() == {"in_progress": "install"}


def test_an_existing_file_is_backed_up_before_it_is_overwritten(u1: FakeU1, tmp_path: Path) -> None:
    (u1.kcfg / "ace2k_tags.cfg").write_text("# an older copy\n")  # not a sign of an install
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    assert u1.run("--from", _rel(tmp_path), answers=NEW_ONE).returncode == 0
    [bk] = u1.backups()
    saved = bk / str(u1.kcfg / "ace2k_tags.cfg").lstrip("/")
    assert saved.read_text() == "# an older copy\n"


def test_the_end_runs_the_health_and_status_checks(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert u1.gcodes() == ["ACEK_HEALTH"]  # the version and the link: from Klipper's objects
    assert "ACEK_HEALTH: OK" in p.out
    assert f"Klipper reports ace2k {ACE2K_V} with its link proven on every unit (a)." in p.out
    assert f"Done: Klipper reports ace2k {ACE2K_V} on every unit (a)" in p.out
    assert "ACEK_ADAPTER_STATUS" in p.out


def test_health_is_asked_again_while_it_has_not_run_yet(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    state = unit_a(ACE2K_V, u1.port)
    state["reply_queue"] = {"ACEK_HEALTH": [["// health: not run yet"]] * 2}
    u1.after_start(state)
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert u1.gcodes().count("ACEK_HEALTH") == 3 and "ACEK_HEALTH: OK" in p.out


def test_health_that_never_runs_is_reported(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    state = unit_a(ACE2K_V, u1.port)
    state["replies"]["ACEK_HEALTH"] = ["// health: not run yet"]
    u1.after_start(state)
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert u1.gcodes().count("ACEK_HEALTH") == 4  # ACE2K_CHECK_TRIES
    assert "WARNING: ACEK_HEALTH: not run yet (expected: OK" in p.out


def test_an_http_error_from_the_gcode_endpoint_is_reported_with_its_text(
    u1: FakeU1, tmp_path: Path
) -> None:
    u1.flasher(UNVERIFIED)
    state = unit_a(ACE2K_V, u1.port)
    state["gcode_errors"] = {"ACEK_HEALTH": [400, "Unknown command: ACEK_HEALTH"]}
    u1.after_start(state)
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert "ACEK_HEALTH did not run: Moonraker answered HTTP 400: Unknown command" in p.out
    assert "gave no answer" not in p.out


def test_a_failing_health_is_reported_not_hidden(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port, health="FAILING mains", proven="no"))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert "WARNING: ACEK_HEALTH: FAILING mains" in p.out
    assert f"WARNING: Not at ace2k {ACE2K_V} with its link proven: a (run ACEK_STATUS" in p.out
    # FAILING is final; a link not proven is asked again until the window ends
    assert u1.gcodes() == ["ACEK_HEALTH"]
    assert u1.requests("/printer/objects/query") == 2 + 4  # start checks, the wait, the window


def test_link_proven_no_is_asked_again(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    state = unit_a(ACE2K_V, u1.port, proven="no")
    state["on_query"] = [{}, {"status": unit_a(ACE2K_V, u1.port)["status"]}]
    u1.after_start(state)
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert "with its link proven on every unit (a)." in p.out and "WARNING" not in p.out
    assert u1.requests("/printer/objects/query") == 4  # start checks, the wait, two looks


def test_health_needs_every_unit(u1: FakeU1, tmp_path: Path) -> None:
    # one OK line of two units is not settled: asked again until both say OK
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    u1.flasher(UNVERIFIED, UNVERIFIED)
    state = ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A), ("b", b, UID_B)])
    state["reply_queue"] = {"ACEK_HEALTH": [["// ace2k a: health: OK"]]}
    u1.after_start(state)
    p = u1.run("--from", _rel(tmp_path), answers=["a", "", "", "", "", "y"])
    assert p.returncode == 0, p.out
    assert u1.gcodes() == ["ACEK_HEALTH", "ACEK_HEALTH"] and "ACEK_HEALTH: OK" in p.out


def test_health_of_one_unit_missing_is_reported(u1: FakeU1, tmp_path: Path) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    u1.flasher(UNVERIFIED, UNVERIFIED)
    state = ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A), ("b", b, UID_B)])
    state["replies"]["ACEK_HEALTH"] = ["// ace2k a: health: OK"]
    u1.after_start(state)
    p = u1.run("--from", _rel(tmp_path), answers=["a", "", "", "", "", "y"])
    assert p.returncode == 0, p.out
    assert "WARNING: ACEK_HEALTH: 1 of 2 units report OK" in p.out


@pytest.mark.parametrize(
    "step",
    [
        {"exit": 3, "print": "Not confirmed"},
        {"exit": 1, "print": "The update failed: chunk 3/9: no answer."},
    ],
)
def test_a_new_install_whose_flash_stops_leaves_no_cfg(
    u1: FakeU1, tmp_path: Path, step: Json
) -> None:
    # paxx includes every .cfg in extended/klipper: an ace2k.cfg with no ace2k unit behind it
    # would stop Klipper at its next start
    u1.flasher(step)
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1, p.out
    assert list(u1.kcfg.glob("ace2k*.cfg")) == []
    assert (u1.extras / "ace2k.py").exists() and u1.state() == {"in_progress": "install"}


def test_a_new_klipper_ready_without_ace2k_is_a_final_answer(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    bare = unit_a(ACE2K_V, u1.port)
    bare["status"] = {"print_stats": {"state": "standby"}, "configfile": {"settings": {}}}
    u1.after_start(bare)
    (u1.fake / "requests.log").unlink(missing_ok=True)
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 1 and f"did not report ace2k {ACE2K_V}" in p.out
    assert u1.requests("/printer/objects/query") == 2  # the start checks, then one look


def test_klipper_keeps_being_asked_while_it_starts(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher(UNVERIFIED)
    early = unit_a(ACE2K_V, u1.port)
    early["klippy_state"] = "startup"
    early["on_info"] = [{}, {"klippy_state": "ready"}]
    u1.after_start(early)
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0 and "Done" in p.out, p.out


def test_a_broken_nginx_leaves_the_page_pending(u1: FakeU1, tmp_path: Path) -> None:
    (u1.fake / "nginx.rc").write_text("1")
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert "The web page was not installed" in p.out and "http://<printer>/ace2k/" not in p.out
    assert not u1.nginx_conf.exists() and u1.state()["in_progress"] == "update"


def test_new_install_with_two_units(u1: FakeU1, tmp_path: Path) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    u1.flasher({"exit": 2, "print": "proved"}, {"exit": 2, "print": "proved"})
    u1.after_start(ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A), ("b", b, UID_B)]))
    # the unit on the first cable is a (b is the last, named with no question); four heads; Install?
    p = u1.run("--from", _rel(tmp_path), answers=["a", "", "", "b:1", "b:2", "y"])
    assert p.returncode == 0, p.out
    calls = [c for c in u1.flasher_calls() if "--identify" not in c]
    assert [c[c.index("--port") + 1] for c in calls] == [u1.port, b]
    assert all("--config-follows" in c for c in calls)
    assert u1.klipper_log() == ["stop", "start"] and u1.restarts() == 1
    cfg = (u1.kcfg / "ace2k.cfg").read_text()
    assert f"[ace2k a]\nmcu: ace2k_a\nuid: {UID_A}\n" in cfg
    assert f"[ace2k b]\nmcu: ace2k_b\nuid: {UID_B}\n" in cfg
    assert "[temperature_sensor ace_b_chamber]\nsensor_type: ace2k\nunit: b\n" in cfg
    assert "lanes: a:1, a:2, b:1, b:2" in (u1.kcfg / "ace2k-u1.cfg").read_text()
    assert f"Done: Klipper reports ace2k {ACE2K_V} on every unit (a, b)" in p.out
    assert "uid: written for a, b (" in p.out


def test_the_plan_lists_each_unit_and_the_lanes_before_install(u1: FakeU1, tmp_path: Path) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    p = u1.run("--from", _rel(tmp_path), answers=["b", "", "", "", "", "n"])
    assert p.returncode == 1 and "Not confirmed; nothing changed." in p.out
    plan = p.stdout[p.stdout.index("Plan:") :]
    assert f"unit a: {b}" in plan and f"unit b: {u1.port}" in plan
    assert plan.index(f"unit a: {b}") < plan.index(f"unit b: {u1.port}")
    assert "lanes: a:1, a:2, a:3, a:4" in plan and "ace2k-u1.cfg" in plan
    assert u1.flasher_calls() == [["--identify", "--port", u1.port]]


def test_new_install_with_one_unit_names_it_a(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher({"exit": 2})
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert not any("--identify" in c for c in u1.flasher_calls())
    assert "which name?" not in p.out and "Head 1 (T0): which lanes feed it? [a:1]" in p.stderr
    cfg = (u1.kcfg / "ace2k.cfg").read_text()
    assert "[mcu ace2k_a]" in cfg and f"[ace2k a]\nmcu: ace2k_a\nuid: {UID_A}\n" in cfg
    assert "[temperature_sensor ace_a_chamber]\nsensor_type: ace2k\nunit: a\n" in cfg
    assert "#[temperature_sensor ace_mcu]" in cfg
    assert "lanes: a:1, a:2, a:3, a:4" in (u1.kcfg / "ace2k-u1.cfg").read_text()
    assert "uid: written for a (Klipper checks it" in p.out


def test_a_uid_klipper_does_not_report_is_not_written(u1: FakeU1, tmp_path: Path) -> None:
    u1.flasher({"exit": 2})
    u1.after_start(ace2k_state(ACE2K_V, "", units=[("a", u1.port, "")]))
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    assert "uid:" not in (u1.kcfg / "ace2k.cfg").read_text()
    assert "Klipper does not report the uid of unit a" in p.out


def test_a_cable_gone_before_its_flash_stops_the_run(u1: FakeU1, tmp_path: Path) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    u1.flasher({"exit": 2})
    heads = [("which lanes feed it?", None, "")] * 4
    p = u1.dialog(
        "--from",
        _rel(tmp_path),
        steps=[
            ("which name?", None, "a"),
            *heads,
            ("Install?", lambda: u1.remove_port(b), "y"),  # unplugged after the plan
        ],
    )
    assert p.returncode != 0
    assert "unit a is flashed" in p.out and "were copied first to" in p.out
    assert f"The cable of unit b ({b}) is no longer plugged in" in p.out
    [flash] = [c for c in u1.flasher_calls() if "--identify" not in c]  # b's never started
    assert flash[flash.index("--port") + 1] == u1.port
    assert not (u1.kcfg / "ace2k.cfg").exists()  # nothing half-configured
    assert u1.klipper_log() == ["stop", "start"]  # Klipper usable again
    assert "Starting Klipper again" in p.out


def test_nothing_answering_on_the_second_cable_names_the_first_flashed(
    u1: FakeU1, tmp_path: Path
) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    u1.flasher({"exit": 2}, {"exit": 4, "print": "Nothing answered"})
    p = u1.run("--from", _rel(tmp_path), answers=["a", "", "", "", "", "y"])
    assert p.returncode != 0
    assert "unit a is flashed" in p.out and f"Nothing answered on {b}" in p.out
    assert u1.klipper_log() == ["stop", "start"]


def test_a_failure_writing_the_config_after_the_flashes_starts_klipper(
    u1: FakeU1, tmp_path: Path
) -> None:
    broken = tmp_path / "cfgtool.py"
    broken.write_text(
        "import runpy, sys\n"
        "if sys.argv[1] == 'render':\n    sys.exit(1)\n"
        f"sys.argv[0] = {str(CFG_TOOL)!r}\n"
        f"runpy.run_path({str(CFG_TOOL)!r}, run_name='__main__')\n"
    )
    u1.flasher({"exit": 2})
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE, ACE2K_CFGTOOL=str(broken))
    assert p.returncode == 1 and "cannot write ace2k.cfg's units" in p.out
    assert "Starting Klipper again" in p.out and "were copied first to" in p.out
    assert u1.klipper_log() == ["stop", "start"]


def test_a_signal_during_the_flash_leaves_klipper_stopped_and_says_so(
    u1: FakeU1, tmp_path: Path
) -> None:
    # a flash may have been cut: Klipper is not started on a unit in that state
    (u1.fake / "kill.flasher").write_text("SIGTERM")
    u1.flasher({"exit": 2})
    p = u1.run("--from", _rel(tmp_path), answers=NEW_ONE)
    assert p.returncode != 0 and "Interrupted" in p.out
    assert "Klipper is stopped: start it with /etc/init.d/S60klipper start" in p.out
    assert u1.klipper_log() == ["stop"]


@pytest.mark.parametrize("rc", [0, 2])
def test_the_flasher_s_0_and_2_are_success(u1: FakeU1, tmp_path: Path, rc: int) -> None:
    u1.flasher({"exit": rc})
    u1.after_start(unit_a(ACE2K_V, u1.port))
    assert u1.run("--from", _rel(tmp_path), answers=NEW_ONE).returncode == 0
