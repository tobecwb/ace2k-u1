"""Updating an installed ace2k: the operator's cfgs kept, one Klipper restart."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fake_u1 import (
    ACE2K_V,
    DEBUG_MSG,
    NEW_ONE,
    PORT_B,
    PORT_NAME,
    PRINTER_CFG,
    SAVED,
    SAVED_A,
    U1_V,
    UID_A,
    UID_B,
    UNIT_CFG,
    ace2k_state,
    installed_two,
    local_release,
    publish_installed,
    unit_a,
)

if TYPE_CHECKING:
    from fake_u1 import FakeU1

UNVERIFIED = {"exit": 2, "print": "Klipper's config has no [ace2k] on this port."}

UNNAMED_CFG = """\
# my ace2k config
[mcu ace2k]
serial: {port}
baud: 250000
restart_method: command

[ace2k]
feed_speed: 70

[include ace2k_tags.cfg]

[temperature_sensor ace_chamber]
sensor_type: ace2k
sensor: chamber

#[temperature_sensor ace_mcu]
#sensor_type: temperature_mcu
#sensor_mcu: ace2k
"""


def installed_one_unnamed(u1: FakeU1, lanes: str | None = None, saved: bool = False) -> None:
    """An unnamed [ace2k] on the first cable, as an install before several units wrote it,
    Klipper ready with it at the release's version."""
    printer = PRINTER_CFG if saved else PRINTER_CFG.replace(SAVED, "")
    publish_installed(u1, UNNAMED_CFG.format(port=u1.port), lanes, printer)
    u1.moonraker(ace2k_state(ACE2K_V, u1.port))
    u1.after_start(ace2k_state(ACE2K_V, u1.port))


def same_release_installed(u1: FakeU1) -> None:
    """The published release's files on the printer, as the installer itself writes them."""
    p = u1.source(
        "WORK=$(mktemp -d); fetch_release >/dev/null && start_changes update && "
        'sync_files update && write_files && state_write; rc=$?; rm -rf "$WORK"; exit $rc'
    )
    assert p.returncode == 0, p.out


@pytest.fixture
def installed(u1: FakeU1, tmp_path: Path) -> FakeU1:
    """ace2k 0.13.0 / ace2k-u1 0.4.0 installed by the installer itself, Klipper ready with it."""
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    p = u1.run("--from", local_release(tmp_path / "rel"), answers=NEW_ONE)
    assert p.returncode == 0, p.out
    (u1.fake / "klipper.log").unlink()
    (u1.fake / "flasher.argv").unlink()
    (u1.kcfg / "ace2k.cfg").write_text((u1.kcfg / "ace2k.cfg").read_text() + "feed_speed: 70\n")
    return u1


def test_the_same_release_again_does_nothing(installed: FakeU1, tmp_path: Path) -> None:
    p = installed.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 0 and "nothing to do" in p.out
    assert installed.klipper_log() == [] and installed.flasher_calls() == []


def test_a_page_only_root_reads_is_made_readable_even_with_nothing_to_do(
    installed: FakeU1, tmp_path: Path
) -> None:
    # a page left 0700 (copied under root's umask 0077 by an older install): same content, so no
    # change is planned, but nginx answers 403 until its modes are set
    u1 = installed
    for path in [u1.web, *u1.web.rglob("*")]:
        path.chmod(0o700 if path.is_dir() else 0o600)
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 0 and "nothing to do" in p.out
    assert u1.klipper_log() == [] and u1.flasher_calls() == []
    for path in [u1.web, *u1.web.rglob("*")]:
        want = 0o055 if path.is_dir() else 0o044
        assert stat.S_IMODE(path.stat().st_mode) & want == want, path


def test_a_new_firmware_is_flashed_with_one_stop_and_one_start(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    cfgs = {n: (u1.kcfg / n).read_bytes() for n in ("ace2k.cfg", "ace2k-u1.cfg")}
    rel = local_release(
        tmp_path / "new",
        ace2k_v="0.14.0",
        u1_v="0.5.0",
        cfg_extra="#new_setting: 1\n#new_setting2: 2\n",
    )
    u1.flasher({"exit": 0})
    u1.after_start(unit_a("0.14.0", u1.port))
    p = u1.run("--update", "--from", rel, answers=["y"])
    assert p.returncode == 0, p.out
    assert u1.klipper_log() == ["stop", "start"]
    argv = u1.flasher_calls()[0]
    assert argv[argv.index("--port") + 1] == u1.port and "--config-follows" in argv
    assert {n: (u1.kcfg / n).read_bytes() for n in cfgs} == cfgs
    assert (u1.kcfg / "ace2k_tags.cfg").read_text() == "# tags cfg 0.14.0\n"
    assert u1.state()["ace2k"] == "v0.14.0" and u1.state()["ace2k_u1"] == "v0.5.0"


def test_only_the_adapter_changed_one_plain_restart(installed: FakeU1, tmp_path: Path) -> None:
    u1 = installed
    rel = local_release(tmp_path / "new", u1_v="0.5.0")
    (u1.fake / "requests.log").unlink(missing_ok=True)
    p = u1.run("--update", "--from", rel, answers=["y"])
    assert p.returncode == 0, p.out
    assert u1.flasher_calls() == [] and u1.klipper_log() == ["restart"]
    # the start checks and the wait after the restart: one objects query each
    assert u1.requests("/printer/objects/query") == 2


def test_a_new_install_under_roots_umask_leaves_everything_readable(
    u1: FakeU1, tmp_path: Path
) -> None:
    # the .cfg files the installer edits and the directories it creates, besides the modules
    u1.flasher(UNVERIFIED)
    u1.after_start(unit_a(ACE2K_V, u1.port))
    old = os.umask(0o077)
    try:
        p = u1.run("--from", local_release(tmp_path / "rel"), answers=NEW_ONE)
    finally:
        os.umask(old)
    assert p.returncode == 0, p.out
    for path in [u1.kcfg, u1.state_dir, u1.state_dir / "flash", *u1.kcfg.glob("*.cfg")]:
        want = 0o055 if path.is_dir() else 0o044
        assert stat.S_IMODE(path.stat().st_mode) & want == want, path


def test_an_update_under_roots_umask_leaves_its_files_readable(
    installed: FakeU1, tmp_path: Path
) -> None:
    # the printer's root has umask 0077: Klipper runs as lava and stops on "Permission denied"
    # at a module, or at a directory, that only root reads
    u1 = installed
    rel = local_release(tmp_path / "new", u1_v="0.5.0")
    old = os.umask(0o077)
    try:
        p = u1.run("--update", "--from", rel, answers=["y"])
    finally:
        os.umask(old)
    assert p.returncode == 0, p.out
    for path in [
        u1.extras / "ace2k_u1.py",
        u1.extras / "ace2k_u1_history.py",
        u1.kcfg / "ace2k_tags.cfg",
    ]:
        assert stat.S_IMODE(path.stat().st_mode) & 0o044 == 0o044, path
    assert (u1.extras / "ace2k_u1.py").read_text() == "# adapter 0.5.0\n"


def test_a_reload_in_the_same_klipper_process_counts(installed: FakeU1, tmp_path: Path) -> None:
    # FIRMWARE_RESTART keeps the process: the wait sees it not ready, then ready again
    u1 = installed
    u1.after_start(
        {
            "klippy_state": "startup",
            "pid": u1.pid(),
            "on_info": [{"klippy_state": "ready"}],
        }
    )
    p = u1.run("--update", "--from", local_release(tmp_path / "new", u1_v="0.5.0"), answers=["y"])
    assert p.returncode == 0, p.out
    assert "Done" in p.out


def test_a_refusal_with_the_unit_unchanged_stops_and_starts_klipper_again(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    u1.flasher({"exit": 3, "print": "A print is running or paused"})
    p = u1.run(
        "--update", "--from", local_release(tmp_path / "new", ace2k_v="0.14.0"), answers=["y"]
    )
    assert p.returncode == 1 and "changed nothing on the unit" in p.out
    assert "run the installer again" in p.out and u1.klipper_log() == ["stop", "start"]
    assert "Starting Klipper again" in p.out
    assert (u1.extras / "ace2k.py").read_text() == "# ace2k host 0.14.0\n"
    assert u1.state()["ace2k"] == f"v{ACE2K_V}"  # recorded only by a run that finished
    # the old files are in the run's backup
    bk = u1.backups()[-1]
    assert (bk / str(u1.extras / "ace2k.py").lstrip("/")).read_text() == f"# ace2k host {ACE2K_V}\n"


def test_a_failure_with_klipper_gone_keeps_the_new_files(installed: FakeU1, tmp_path: Path) -> None:
    u1 = installed
    u1.flasher({"exit": 1, "print": "The update failed.", "moonraker": {"klippy_state": "error"}})
    p = u1.run(
        "--update", "--from", local_release(tmp_path / "new", ace2k_v="0.14.0"), answers=["y"]
    )
    assert p.returncode == 1 and "did not finish" in p.out and "run the installer again" in p.out
    assert (u1.extras / "ace2k.py").read_text() == "# ace2k host 0.14.0\n"


@pytest.fixture
def broken(installed: FakeU1) -> FakeU1:
    """After a U1 firmware upgrade: the extras gone, Klipper in error, printer_data kept."""
    u1 = installed
    for f in u1.extras.glob("ace2k*.py"):
        f.unlink()
    u1.moonraker(
        {
            "klippy_state": "error",
            "status": {"print_stats": {"state": "standby"}, "configfile": {"settings": {}}},
        }
    )
    return u1


# Klipper in error reports no unit: each unit of ace2k.cfg is flashed by its cable, with Klipper
# stopped (the flasher reaches ace2k there itself: 2, its own proof done, Klipper's check to come).
@pytest.mark.parametrize("status", [0, 2])
def test_repair_flashes_the_unit_with_one_stop_and_one_start(
    broken: FakeU1, tmp_path: Path, status: int
) -> None:
    u1 = broken
    u1.flasher({"exit": status})
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 0, p.out
    assert "a: unknown → 0.13.0 (flash)" in p.out
    assert u1.klipper_log() == ["stop", "start"] and len(u1.flasher_calls()) == 1
    argv = u1.flasher_calls()[0]
    assert argv[argv.index("--port") + 1] == u1.port  # read from the printer's ace2k.cfg


def test_repair_where_nothing_answers_stops_and_starts_klipper_again(
    broken: FakeU1, tmp_path: Path
) -> None:
    u1 = broken
    u1.flasher({"exit": 4, "print": "Nothing on the port answered"})
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 1 and f"Nothing answered on {u1.port}" in p.out
    assert u1.klipper_log() == ["stop", "start"] and "Starting Klipper again" in p.out


def test_repair_that_never_gets_ready_stops_after_one_more_restart(
    broken: FakeU1, tmp_path: Path
) -> None:
    u1 = broken
    u1.flasher({"exit": 2})
    u1.after_start({"klippy_state": "error"})  # the one more start gets no further
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 1 and "One more restart" in p.out
    assert "Klipper did not report ace2k 0.13.0 on every unit" in p.out
    assert "klippy.log" in p.out


def test_the_adapter_version_is_shown_without_its_v(installed: FakeU1, tmp_path: Path) -> None:
    p = installed.run(
        "--update", "--from", local_release(tmp_path / "new", ace2k_v="0.14.0"), answers=["n"]
    )
    assert f"ace2k is installed: ace2k-u1 {U1_V}," in p.out
    assert f"ace2k-u1 {U1_V} → {U1_V}" in p.out


def test_update_reports_what_changes(installed: FakeU1, tmp_path: Path) -> None:
    p = installed.run(
        "--update", "--from", local_release(tmp_path / "new", ace2k_v="0.14.0"), answers=["n"]
    )
    assert p.returncode == 1 and f"a: {ACE2K_V} → 0.14.0 (flash)" in p.out and U1_V in p.out


def test_nothing_to_do_still_creates_the_debug_flag_it_was_allowed_to(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    (u1.root / "oem" / ".debug").unlink()
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 0 and "nothing to do" in p.out
    assert (u1.root / "oem" / ".debug").exists() and DEBUG_MSG in p.out
    assert u1.klipper_log() == []


def test_the_start_checks_query_klipper_once(installed: FakeU1, tmp_path: Path) -> None:
    u1 = installed
    (u1.fake / "requests.log").unlink(missing_ok=True)
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 0 and "nothing to do" in p.out
    assert u1.requests("/printer/objects/query") == 1 and u1.requests("/server/info") == 1


def test_the_klipper_from_before_the_restart_does_not_count(
    installed: FakeU1, tmp_path: Path
) -> None:
    # the restart did nothing: the old process still reports the target version, so the check
    # must wait for a new Klipper process and give up
    u1 = installed
    (u1.fake / "klipper.noop").write_text("")
    p = u1.run("--update", "--from", local_release(tmp_path / "new", u1_v="0.5.0"), answers=["y"])
    assert p.returncode == 1 and "Done" not in p.out


def test_only_the_new_klippers_console_counts_for_the_hooked_lanes(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    u1.after_start(unit_a(ACE2K_V, u1.port, console=()))
    p = u1.run("--update", "--from", local_release(tmp_path / "new", u1_v="0.5.0"), answers=["y"])
    assert p.returncode == 0, p.out
    assert "does not show 'ace2k_u1: hooked lanes'" in p.out


def test_a_klipper_without_a_pid_yet_after_its_start_means_one_start(
    broken: FakeU1, tmp_path: Path
) -> None:
    # Klipper not attached to Moonraker at the start's mark (no PID); the new process shows its
    # PID one look later: no second start
    u1 = broken
    u1.moonraker(
        {
            "klippy_state": "disconnected",
            "status": {"print_stats": {"state": "standby"}, "configfile": {"settings": {}}},
        }
    )
    u1.flasher({"exit": 2})
    late = unit_a(ACE2K_V, u1.port)
    u1.after_start({"klippy_state": "disconnected", "on_info": [{}, late]})
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 0, p.out
    assert u1.restarts() == 1 and u1.klipper_log() == ["stop", "start"]


def test_a_failed_update_shows_as_unfinished_and_the_rerun_finishes_it(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    rel = local_release(tmp_path / "new", ace2k_v="0.14.0")
    u1.flasher({"exit": 3, "print": "A print is running or paused"})
    assert u1.run("--update", "--from", rel, answers=["y"]).returncode == 1
    assert u1.state()["in_progress"] == "update"
    u1.flasher({"exit": 0})
    u1.after_start(unit_a("0.14.0", u1.port))
    p = u1.run("--from", rel, answers=["y", "y"])
    assert p.returncode == 0, p.out
    assert "A previous run did not finish." in p.out and "Update (u)" not in p.out
    assert "a: 0.13.0 → 0.14.0 (flash)" in p.out  # the update path: unit a, by its cable
    assert u1.state()["ace2k"] == "v0.14.0" and "in_progress" not in u1.state()


def test_update_with_a_missing_cfg_writes_only_that_one(installed: FakeU1, tmp_path: Path) -> None:
    u1 = installed
    mine = (u1.kcfg / "ace2k.cfg").read_bytes()
    (u1.kcfg / "ace2k-u1.cfg").unlink()
    p = u1.run("--update", "--from", local_release(tmp_path / "new", u1_v="0.5.0"), answers=["y"])
    assert p.returncode == 0, p.out
    assert (u1.kcfg / "ace2k.cfg").read_bytes() == mine
    assert (u1.kcfg / "ace2k-u1.cfg").read_text().startswith("[ace2k_u1]")
    assert "your ace2k.cfg is kept; ace2k-u1.cfg is written (none was there)" in p.out
    assert u1.flasher_calls() == [] and u1.klipper_log() == ["restart"]  # the update path


def test_an_ace2k_cfg_with_the_old_sensor_names_is_kept_without_a_second_sensor(
    installed: FakeU1, tmp_path: Path
) -> None:
    # the sensors were named without the unit before 0.13.0: the update renames none and adds
    # none, so Klipper never sees ace_chamber and ace_a_chamber side by side
    u1 = installed
    cfg = u1.kcfg / "ace2k.cfg"
    cfg.write_text(cfg.read_text().replace("ace_a_chamber", "ace_chamber"))
    mine = cfg.read_bytes()
    p = u1.run("--update", "--from", local_release(tmp_path / "new", u1_v="0.5.0"), answers=["y"])
    assert p.returncode == 0, p.out
    assert cfg.read_bytes() == mine
    assert "[temperature_sensor ace_chamber]" in mine.decode()
    assert "ace_a_chamber" not in mine.decode()


def test_the_flasher_is_not_kept_waiting_for_a_klipper_pid(broken: FakeU1, tmp_path: Path) -> None:
    u1 = broken
    u1.moonraker(
        {
            "klippy_state": "disconnected",
            "status": {"print_stats": {"state": "standby"}, "configfile": {"settings": {}}},
        }
    )
    u1.flasher({"exit": 2})
    for f in ("requests.log", "flasher.infos"):
        (u1.fake / f).unlink(missing_ok=True)
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 0, p.out
    assert (u1.fake / "flasher.infos").read_text().split() == ["1"]  # the stop's one look


def test_an_update_with_matching_files_says_only_the_unit_is_flashed(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    u1.moonraker(unit_a("0.12.0", u1.port))
    u1.flasher({"exit": 0})
    p = u1.run("--update", "--from", local_release(tmp_path / "same"), answers=["y"])
    assert p.returncode == 0, p.out
    assert "The files already match." in p.out and f"a: 0.12.0 → {ACE2K_V} (flash)" in p.out
    assert "these files change" not in p.out


SWAP_CFG = (
    "[ace2k_u1]\n#lanes: 1, 2, 3, 4\n\n"
    "# DANGER: the swap macros move the head.\n# Change them only if you are sure.\n\n"
    "[gcode_macro ACEK_SWAP_UNLOAD]\n# the old filament out\ngcode:\n  M400\n\n"
    "[gcode_macro ACEK_SWAP_LOAD]\n# the new filament in\ngcode:\n  M83\n\n\n"
    "[ace2k_u1_history]\n#interval: 30\n"
)


def test_update_adds_the_sections_the_operators_cfg_lacks_and_keeps_the_rest(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    mine = (u1.kcfg / "ace2k-u1.cfg").read_text().replace("#lanes: 1, 2, 3, 4", "lanes: 1+2, 3, 4")
    (u1.kcfg / "ace2k-u1.cfg").write_text(mine)
    rel = local_release(tmp_path / "new", u1_v="0.5.0", u1_cfg=SWAP_CFG)
    p = u1.run("--update", "--from", rel, answers=["y"])
    assert p.returncode == 0, p.out
    now = (u1.kcfg / "ace2k-u1.cfg").read_text()
    assert now.startswith(mine)  # the operator's text, untouched
    assert now[len(mine) :] == (
        "\n# DANGER: the swap macros move the head.\n# Change them only if you are sure.\n\n"
        "[gcode_macro ACEK_SWAP_UNLOAD]\n# the old filament out\ngcode:\n  M400\n"
        "\n[gcode_macro ACEK_SWAP_LOAD]\n# the new filament in\ngcode:\n  M83\n"
    )
    assert (
        "your ace2k-u1.cfg is kept and gains [gcode_macro ACEK_SWAP_UNLOAD]"
        " [gcode_macro ACEK_SWAP_LOAD] at its end" in p.out
    )
    # run again: nothing lacks, nothing to do
    p = u1.run(
        "--update",
        "--from",
        local_release(tmp_path / "again", u1_v="0.5.0", u1_cfg=SWAP_CFG),
        answers=["y"],
    )
    assert p.returncode == 0 and "nothing to do" in p.out
    assert (u1.kcfg / "ace2k-u1.cfg").read_text() == now


def test_a_section_the_operator_commented_out_is_not_added_back(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    mine = (u1.kcfg / "ace2k-u1.cfg").read_text() + "\n#[gcode_macro  ACEK_SWAP_LOAD]\n#gcode:\n"
    (u1.kcfg / "ace2k-u1.cfg").write_text(mine)
    rel = local_release(tmp_path / "new", u1_v="0.5.0", u1_cfg=SWAP_CFG)
    p = u1.run("--update", "--from", rel, answers=["y"])
    assert p.returncode == 0, p.out
    now = (u1.kcfg / "ace2k-u1.cfg").read_text()
    assert now.startswith(mine) and now.count("[gcode_macro ACEK_SWAP_UNLOAD]") == 1
    assert "\n[gcode_macro ACEK_SWAP_LOAD]" not in now


def test_an_empty_operators_cfg_gains_every_section_of_the_release(
    installed: FakeU1, tmp_path: Path
) -> None:
    u1 = installed
    (u1.kcfg / "ace2k-u1.cfg").write_text("")
    rel = local_release(tmp_path / "new", u1_v="0.5.0", u1_cfg=SWAP_CFG)
    p = u1.run("--update", "--from", rel, answers=["y"])
    assert p.returncode == 0, p.out
    now = (u1.kcfg / "ace2k-u1.cfg").read_text()
    for header in ("[ace2k_u1]", "[gcode_macro ACEK_SWAP_UNLOAD]", "[gcode_macro ACEK_SWAP_LOAD]"):
        assert f"\n{header}\n" in now, header
    assert "[ace2k_u1_history]" in now


# --- several units ---


def _flashed_ports(u1: FakeU1) -> list[str]:
    return [c[c.index("--port") + 1] for c in u1.flasher_calls() if "--identify" not in c]


def test_update_flashes_only_the_outdated_unit(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b)
    units = [("a", u1.port, UID_A), ("b", b, UID_B)]
    u1.moonraker(ace2k_state(ACE2K_V, "", units=units, versions={"b": "0.12.0"}))
    u1.flasher({"exit": 2})
    u1.after_start(ace2k_state(ACE2K_V, "", units=units))
    r = u1.run("--update", answers=["y"])
    assert r.returncode == 0, r.out
    assert f"a: {ACE2K_V} (already)" in r.out and f"b: 0.12.0 → {ACE2K_V} (flash)" in r.out
    assert _flashed_ports(u1) == [b]
    assert all("--config-follows" in c for c in u1.flasher_calls())
    assert u1.klipper_log() == ["stop", "start"] and u1.restarts() == 1


def test_an_outdated_unnamed_unit_is_flashed_by_its_cable(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    u1.moonraker(ace2k_state("0.12.0", u1.port))
    u1.flasher({"exit": 2})
    r = u1.run("--update", answers=["y"])
    assert r.returncode == 0, r.out
    assert f"ace2k: 0.12.0 → {ACE2K_V} (flash)" in r.out and "Flashing unit ace2k" in r.out
    assert _flashed_ports(u1) == [u1.port] and u1.klipper_log() == ["stop", "start"]
    assert (u1.kcfg / "ace2k.cfg").read_text() == UNNAMED_CFG.format(port=u1.port)


def test_a_failed_flash_of_b_leaves_only_b_for_the_next_run(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b)
    units = [("a", u1.port, UID_A), ("b", b, UID_B)]
    u1.moonraker(ace2k_state("0.12.0", "", units=units))
    u1.flasher({"exit": 2}, {"exit": 1, "print": "The update failed."})
    u1.after_start(ace2k_state(ACE2K_V, "", units=units, versions={"b": "0.12.0"}))
    r = u1.run("--update", answers=["y"])
    assert (
        r.returncode == 1 and f"Before this stop, unit a is flashed with ace2k {ACE2K_V}" in r.out
    )
    assert u1.klipper_log() == ["stop", "start"]
    (u1.fake / "flasher.argv").unlink()
    u1.flasher({"exit": 2})
    u1.after_start(ace2k_state(ACE2K_V, "", units=units))
    r = u1.run("--update", answers=["y"])
    assert r.returncode == 0, r.out
    assert f"a: {ACE2K_V} (already)" in r.out and _flashed_ports(u1) == [b]


def test_a_unit_gone_stops_before_any_change(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b)
    u1.remove_port(b)
    before = u1.digest()
    r = u1.run("--update", answers=["n"])
    assert r.returncode != 0 and "Unit b" in r.out and "is not plugged in" in r.out
    assert "Take unit b out of the config? [y/N]" in r.out and "nothing changed" in r.out
    assert u1.digest() == before and u1.klipper_log() == []


def test_a_unit_gone_taken_out_on_yes(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b, lanes="a:1+a:2, a:3, a:4, b:1", saved_b=True)
    u1.remove_port(b)
    u1.moonraker(ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A)]))
    u1.after_start(ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A)]))
    r = u1.run("--update", answers=["y", "y"])
    assert r.returncode == 0, r.out
    assert "take unit b out of the config" in r.out
    cfg = (u1.kcfg / "ace2k.cfg").read_text()
    assert "[ace2k b]" not in cfg and "ace2k_b" not in cfg and "ace_b_" not in cfg
    assert cfg.startswith(
        "# my ace2k config\n" + UNIT_CFG.format(n="a", serial=u1.port, uid=f"uid: {UID_A}\n")
    )
    assert "[temperature_sensor ace_a_chamber]" in cfg
    assert "lanes: a:1+a:2, a:3, a:4, -\n" in (u1.kcfg / "ace2k-u1.cfg").read_text()
    printer = (u1.cfg / "printer.cfg").read_text()
    assert printer == PRINTER_CFG.replace(SAVED, SAVED_A)
    assert u1.flasher_calls() == [] and u1.klipper_log() == ["restart"]


def test_yes_refuses_a_unit_gone(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b)
    u1.remove_port(b)
    before = u1.digest()
    r = u1.run("--update", "--yes")
    assert r.returncode != 0 and "nothing changed" in r.out and "Unit b" in r.out
    assert u1.digest() == before


def test_every_unit_gone_stops(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    Path(u1.port).unlink()
    before = u1.digest()
    r = u1.run("--update", answers=["y"])
    assert r.returncode != 0 and "nothing changed" in r.out and "Take" not in r.out
    assert "No cable of a unit" in r.out and "check the units' cables" in r.out
    assert "run this with --remove" in r.out
    assert u1.digest() == before


def test_an_update_with_no_unit_in_ace2k_cfg_points_to_remove(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    (u1.kcfg / "ace2k.cfg").write_text("# no unit\n")
    before = u1.digest()
    r = u1.run("--update", answers=["y"])
    assert r.returncode != 0 and "run this with --remove, then install again" in r.out
    assert u1.digest() == before


def test_port_is_ignored_by_an_update_with_one_warning(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    same_release_installed(u1)
    r = u1.run("--update", "--port", "/dev/serial/by-id/elsewhere")
    assert r.returncode == 0 and "nothing to do" in r.out, r.out
    warning = "WARNING: --port is ignored by --update; the units come from ace2k.cfg"
    assert r.out.count(warning) == 1 and r.out.count("--port") == 1


def test_nothing_to_do_with_a_uid_written_keeps_five_backups(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b, uids=False)
    same_release_installed(u1)
    for i in range(6):
        (u1.state_dir / "backup" / f"20200101-00000{i}-1").mkdir(parents=True)
        (u1.state_dir / "backup" / f"20200101-00000{i}-1" / "f").write_text("x")
    r = u1.run("--update")
    assert r.returncode == 0 and "uid: written for a, b" in r.out, r.out
    assert len(u1.backups()) == 5


def test_a_new_unit_on_an_unnamed_install(u1: FakeU1) -> None:
    installed_one_unnamed(u1, lanes="1, 2, 3, 4", saved=True)
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    u1.flasher({"exit": 2})
    u1.after_start(ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A), ("b", b, UID_B)]))
    r = u1.run("--update", answers=["y", "", "", "b:1", "b:2", "y"])
    assert r.returncode == 0, r.out
    assert "Add the unit whose LEDs blink now as b? [y/N]" in r.out
    assert "Head 3 (T2): which lanes feed it? [a:3]" in r.out
    cfg = (u1.kcfg / "ace2k.cfg").read_text()
    assert "[mcu ace2k_a]\n" in cfg and "[mcu ace2k]" not in cfg
    assert "[ace2k a]\nmcu: ace2k_a\n" in cfg and "[ace2k b]\nmcu: ace2k_b\n" in cfg
    assert f"[mcu ace2k_b]\nserial: {b}\n" in cfg and "feed_speed: 70" in cfg
    assert "sensor_type: ace2k\nunit: a\nsensor: chamber" in cfg
    assert "#sensor_mcu: ace2k_a" in cfg and "[temperature_sensor ace_b_chamber]" in cfg
    assert "lanes: a:1, a:2, b:1, b:2\n" in (u1.kcfg / "ace2k-u1.cfg").read_text()
    printer = (u1.cfg / "printer.cfg").read_text()
    assert "#*# [ace2k a]\n#*# lane1_encoder_scale = 1.2424\n" in printer
    assert "#*# [ace2k]" not in printer
    assert _flashed_ports(u1) == [b]
    assert u1.klipper_log() == ["stop", "start"]


def test_a_new_unit_declined_is_left_out(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    same_release_installed(u1)
    u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    cfg = (u1.kcfg / "ace2k.cfg").read_bytes()
    r = u1.run("--update", answers=["n"])
    assert r.returncode == 0, r.out
    assert "left out" in r.out and (u1.kcfg / "ace2k.cfg").read_bytes() == cfg


def test_a_new_unit_with_yes_is_left_out(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    r = u1.run("--update", "--yes")
    assert r.returncode == 0, r.out
    assert "left out" in r.out and "[ace2k b]" not in (u1.kcfg / "ace2k.cfg").read_text()
    assert "--identify" not in str(u1.flasher_calls())


def test_a_new_unit_that_cannot_blink_is_added_by_its_cable(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 5})
    u1.flasher({"exit": 2})
    u1.after_start(ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A), ("b", b, UID_B)]))
    r = u1.dialog(
        "--update",
        steps=[
            ("unplug the USB cable", lambda: u1.remove_port(b), ""),
            ("Plug it back in", lambda: u1.add_port(PORT_B), ""),
            ("Head 1", None, ""),
            ("Head 2", None, ""),
            ("Head 3", None, "b:1"),
            ("Head 4", None, "-"),
            ("Update?", None, "y"),
        ],
    )
    assert r.returncode == 0, r.out
    assert "[ace2k b]" in (u1.kcfg / "ace2k.cfg").read_text()
    assert _flashed_ports(u1) == [b]


def test_two_cables_unplugged_for_a_new_unit_leave_it_out(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    same_release_installed(u1)
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 5})
    before = u1.digest()
    r = u1.dialog(
        "--update",
        steps=[
            ("unplug the USB cable", lambda: (u1.remove_port(b), u1.remove_port(u1.port)), ""),
            (f"Plug {u1.port} back in", lambda: u1.add_port(PORT_NAME), ""),
            (f"Plug {b} back in", lambda: u1.add_port(PORT_B), ""),
        ],
    )
    assert r.returncode == 0, r.out
    assert "not the cable of the new unit alone: it is left out" in r.out
    assert "Plug it back in" not in r.out and "nothing to do" in r.out
    assert f"The unit on {b} was not added: run this with --update again" in r.out
    assert u1.digest() == before
    assert all("--identify" in c for c in u1.flasher_calls())


def test_update_with_klipper_down_reads_the_file(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b)
    u1.moonraker(
        {
            "klippy_state": "error",
            "status": {"print_stats": {"state": "standby"}, "configfile": {"settings": {}}},
        }
    )
    u1.flasher({"exit": 2}, {"exit": 2})
    u1.after_start(ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A), ("b", b, UID_B)]))
    r = u1.run("--update", answers=["y"])
    assert r.returncode == 0, r.out
    assert _flashed_ports(u1) == [u1.port, b]
    assert u1.klipper_log() == ["stop", "start"] and "Done" in r.out


def test_update_writes_a_missing_uid_with_nothing_else_to_do(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b, uids=False)
    same_release_installed(u1)
    r = u1.run("--update")
    assert r.returncode == 0, r.out
    assert "nothing to do" in r.out and "uid: written for a, b" in r.out
    cfg = (u1.kcfg / "ace2k.cfg").read_text()
    assert f"mcu: ace2k_a\nuid: {UID_A}\n" in cfg and f"mcu: ace2k_b\nuid: {UID_B}\n" in cfg
    assert u1.restarts() == 0 and u1.flasher_calls() == []
    assert "in_progress" not in u1.state()


def test_an_unnamed_unit_with_nothing_to_do_gains_its_uid(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    same_release_installed(u1)
    state = ace2k_state(ACE2K_V, u1.port)
    state["status"]["ace2k"]["uid"] = UID_A
    u1.moonraker(state)
    r = u1.run("--update")
    assert r.returncode == 0, r.out
    assert "nothing to do" in r.out and "uid: written for ace2k" in r.out
    cfg = (u1.kcfg / "ace2k.cfg").read_text()
    assert cfg == UNNAMED_CFG.format(port=u1.port).replace("[ace2k]\n", f"[ace2k]\nuid: {UID_A}\n")
    assert u1.restarts() == 0 and u1.flasher_calls() == []


def test_the_plan_names_the_units_that_gain_a_uid(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b)
    cfg = u1.kcfg / "ace2k.cfg"
    cfg.write_text(cfg.read_text().replace(f"uid: {UID_B}\n", ""))
    u1.moonraker(ace2k_state("0.12.0", "", units=[("a", u1.port, UID_A), ("b", b, UID_B)]))
    r = u1.run("--update", answers=["n"])
    assert "write uid: into b\n" in r.out


def test_the_units_are_asked_for_again_once_the_release_brings_its_cfg_tool(u1: FakeU1) -> None:
    # on the printer the cfg tool comes with the release, after the start checks: their query
    # asked only for an unnamed [ace2k], so the named units are asked for once more
    b = u1.add_port(PORT_B)
    installed_two(u1, b)
    same_release_installed(u1)
    (u1.fake / "requests.log").unlink(missing_ok=True)
    r = u1.run("--update", ACE2K_CFGTOOL="")
    assert r.returncode == 0 and "nothing to do" in r.out, r.out
    assert u1.flasher_calls() == [] and u1.requests("/printer/objects/query") == 2


def test_a_flasher_from_before_several_units_is_refused_before_any_change(
    u1: FakeU1, tmp_path: Path
) -> None:
    # it takes --config-follows but cannot reach ace2k over the cable (no --identify)
    installed_one_unnamed(u1)
    u1.moonraker(ace2k_state("0.12.0", u1.port))
    rel = local_release(tmp_path / "rel")
    stub = u1.fake / "ace2k_flash.pyz"
    stub.write_text(stub.read_text().replace(', "--identify"]', "]"))
    before = u1.digest()
    r = u1.run("--update", "--from", rel, answers=["y"])
    assert r.returncode == 1 and "--identify" in r.out and "nothing changed" in r.out, r.out
    assert u1.digest() == before and u1.flasher_calls() == []


@pytest.mark.parametrize(
    ("rc", "said"),
    [
        (4, "Nothing answered on {c}: that unit is left out."),
        (1, "The flasher could not reach the unit on {c} (status 1, see above): it is left out."),
    ],
)
def test_a_new_unit_whose_blink_fails_is_left_out(u1: FakeU1, rc: int, said: str) -> None:
    installed_one_unnamed(u1)
    same_release_installed(u1)
    b = u1.add_port(PORT_B)
    u1.identify({"exit": rc})
    r = u1.run("--update")
    assert r.returncode == 0 and said.format(c=b) in r.out, r.out
    assert "[ace2k b]" not in (u1.kcfg / "ace2k.cfg").read_text()


def test_a_new_unit_c_on_a_named_install_of_a_and_b(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    installed_two(u1, b)
    before = (u1.kcfg / "ace2k.cfg").read_text()
    c = u1.add_port("usb-1a86_USB_Single_Serial_TESTUNITC-if00")
    u1.identify({"exit": 0})
    u1.flasher({"exit": 2})
    uid_c = "fedcba9876543210fedcba98"
    units = [("a", u1.port, UID_A), ("b", b, UID_B), ("c", c, uid_c)]
    u1.after_start(ace2k_state(ACE2K_V, "", units=units))
    r = u1.run("--update", answers=["y", "", "", "", "c:1", "y"])
    assert r.returncode == 0, r.out
    assert "Add the unit whose LEDs blink now as c? [y/N]" in r.out
    assert "Head 4 (T3): which lanes feed it? [b:2]" in r.out
    cfg = (u1.kcfg / "ace2k.cfg").read_text()
    head = before.split("[include ace2k_tags.cfg]")[0]
    assert cfg.startswith(head), cfg  # a and b as they were
    assert f"[mcu ace2k_c]\nserial: {c}\n" in cfg and "[ace2k c]\nmcu: ace2k_c\n" in cfg
    assert f"uid: {uid_c}\n" in cfg and "[temperature_sensor ace_c_chamber]" in cfg
    assert "lanes: a:1, a:2, b:1, c:1\n" in (u1.kcfg / "ace2k-u1.cfg").read_text()
    assert _flashed_ports(u1) == [c] and u1.klipper_log() == ["stop", "start"]


def test_a_new_unit_left_out_is_named_again_after_a_finished_update(u1: FakeU1) -> None:
    installed_one_unnamed(u1)
    u1.moonraker(ace2k_state("0.12.0", u1.port))  # the unit needs a flash: a full update runs
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 5})
    u1.flasher({"exit": 2})
    r = u1.dialog(
        "--update",
        steps=[
            ("unplug the USB cable", lambda: (u1.remove_port(b), u1.remove_port(u1.port)), ""),
            (f"Plug {u1.port} back in", lambda: u1.add_port(PORT_NAME), ""),
            (f"Plug {b} back in", lambda: u1.add_port(PORT_B), ""),
            ("Update?", None, "y"),
        ],
    )
    assert r.returncode == 0, r.out
    assert "Done" in r.out and "[ace2k b]" not in (u1.kcfg / "ace2k.cfg").read_text()
    said = f"The unit on {b} was not added: run this with --update again"
    assert said in r.stdout and r.stdout.index(said) > r.stdout.index("Done")
    assert _flashed_ports(u1) == [u1.port]


def test_a_new_unit_beside_one_named_unit_keeps_its_bare_lanes_on_that_unit(u1: FakeU1) -> None:
    # bare lanes: feed the one unit there is, whatever its name; the new unit takes none of them
    cfg = UNIT_CFG.format(n="left", serial=u1.port, uid="") + "\n[include ace2k_tags.cfg]\n"
    publish_installed(u1, cfg, "1, 2, 3, 4", PRINTER_CFG.replace(SAVED, ""))
    u1.moonraker(ace2k_state(ACE2K_V, "", units=[("left", u1.port, UID_A)]))
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    u1.flasher({"exit": 2})
    units = [("left", u1.port, UID_A), ("a", b, UID_B)]
    u1.after_start(ace2k_state(ACE2K_V, "", units=units))
    r = u1.run("--update", answers=["y", "", "", "", "", "y"])
    assert r.returncode == 0, r.out
    assert "Add the unit whose LEDs blink now as a? [y/N]" in r.out
    assert "Head 1 (T0): which lanes feed it? [left:1]" in r.out
    assert "lanes: left:1, left:2, left:3, left:4\n" in (u1.kcfg / "ace2k-u1.cfg").read_text()
