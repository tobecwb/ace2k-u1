"""install.sh's units: read, matched to the cables, named by blink, lanes: asked."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fake_u1 import PORT_B, PORT_NAME, ace2k_state

if TYPE_CHECKING:
    from fake_u1 import FakeU1, Ran

CFG_TWO = """\
[mcu ace2k_a]
serial: {a}
[ace2k a]
mcu: ace2k_a
[mcu ace2k_b]
serial: {b}
[ace2k b]
mcu: ace2k_b
"""
NAME_NEW = 'read_cables; NEW=$CABLES; name_new; cat "$WORK/names"'
MATCH = 'read_units; read_cables; match_units; printf "gone=%s|new=%s|" "$GONE" "$NEW"'


def with_cfg(u1: FakeU1, text: str) -> None:
    (u1.kcfg / "ace2k.cfg").write_text(text)


def in_work(u1: FakeU1, script: str) -> str:
    """script with a work directory, as main sets one (parse_args clears WORK)."""
    return f'WORK="{u1.root / "tmp" / "w"}"; mkdir -p "$WORK"; {script}'


def source(u1: FakeU1, script: str, answers: list[str] | None = None) -> Ran:
    return u1.source(in_work(u1, script), answers=answers or [])


def test_read_units_of_two_named_units(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    with_cfg(u1, CFG_TWO.format(a=u1.port, b=b))
    r = source(u1, 'read_units; cat "$WORK/units"')
    assert r.stdout.splitlines() == [f"a\t{u1.port}\t\t\t", f"b\t{b}\t\t\t"], r.out


def test_read_units_joins_what_klipper_reports(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    cfg = CFG_TWO.format(a=u1.port, b=b).replace("mcu: ace2k_b\n", "mcu: ace2k_b\nuid: 01\n")
    with_cfg(u1, cfg)
    u1.moonraker(ace2k_state("0.13.0", u1.port, units=[("a", u1.port, "0123")]))
    r = source(u1, 'read_status; read_units; cat "$WORK/units"')
    assert r.stdout.splitlines() == [f"a\t{u1.port}\t\t0.13.0\tyes", f"b\t{b}\t01\t\t"], r.out


def test_read_status_reads_every_unit(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    u1.moonraker(ace2k_state("0.13.0", "", units=[("b", b, "02"), ("a", u1.port, "01")]))
    r = source(
        u1,
        'read_status; echo "run=$RUNNING has=$HAS_ACE2K"; cat "$WORK/kunits"',
    )
    assert r.stdout.splitlines() == [
        "run=0.13.0 has=yes",
        "a\t0.13.0\tyes\t01",
        "b\t0.13.0\tyes\t02",
    ], r.out


def test_read_status_asks_for_each_unit_as_klipper_spells_its_object(u1: FakeU1) -> None:
    # Klipper keeps a section's own spelling for its object: [ace2k A] is "ace2k A"
    cfg = CFG_TWO.format(a=u1.port, b="x").replace("[ace2k a]", "[ace2k A]")
    with_cfg(u1, cfg.replace("[ace2k b]", "[ace2k  B]"))
    log = u1.fake / "query.log"
    r = source(u1, f'mr() {{ printf "%s\\n" "$1" >"{log}"; }}; read_status')
    assert r.returncode == 0, r.out
    assert log.read_text().strip().endswith("&ace2k&ace2k%20A&ace2k%20%20B")


def test_read_status_joins_a_unit_spelt_in_capitals(u1: FakeU1) -> None:
    state = ace2k_state("0.13.0", "", units=[("A", u1.port, "01")])
    state["status"]["ace2k  B"] = dict(state["status"]["ace2k A"], uid="02")
    u1.moonraker(state)
    r = source(u1, 'read_status; cat "$WORK/kunits"')
    assert r.stdout.splitlines() == ["a\t0.13.0\tyes\t01", "b\t0.13.0\tyes\t02"], r.out


def test_read_status_asks_moonraker_for_each_unit_of_the_cfg(u1: FakeU1) -> None:
    with_cfg(u1, CFG_TWO.format(a=u1.port, b="x"))
    log = u1.fake / "query.log"
    r = source(u1, f'mr() {{ printf "%s\\n" "$1" >"{log}"; }}; read_status')
    assert r.returncode == 0, r.out
    assert log.read_text().strip().endswith("&ace2k&ace2k%20a&ace2k%20b")


def test_an_unnamed_unit_is_read_with_an_empty_name(u1: FakeU1) -> None:
    with_cfg(u1, f"[mcu ace2k]\nserial: {u1.port}\n[ace2k]\nuid: 0a\n")
    u1.moonraker(ace2k_state("0.12.0", u1.port))
    r = source(
        u1,
        'read_status; read_units; read_cables; match_units; cat "$WORK/units"; '
        'echo "gone=$GONE new=$NEW"',
    )
    assert r.stdout.splitlines() == [f"\t{u1.port}\t0a\t0.12.0\tyes", "gone= new="], r.out


def test_a_serial_by_another_path_is_the_same_cable(u1: FakeU1) -> None:
    link = u1.root / "dev" / "ttyACM0"
    link.symlink_to(u1.port)
    b = u1.add_port(PORT_B)
    # unit b's [mcu] is gone: its serial is empty, so it matches no cable, and b's cable is new
    with_cfg(u1, CFG_TWO.format(a=link, b=b).replace("[mcu ace2k_b]", "[x]"))
    r = source(u1, MATCH)
    assert r.stdout == f"gone=b\n|new={b}\n|", r.out


def test_a_unit_whose_cable_is_out_is_gone(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    with_cfg(u1, CFG_TWO.format(a=u1.port, b=b))
    u1.remove_port(b)
    r = source(u1, MATCH)
    assert r.stdout == "gone=b\n|new=|", r.out


def test_two_new_units_are_named_by_their_blink(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0}, {"exit": 0})
    r = source(u1, NAME_NEW, answers=["b"])
    assert "The unit whose LEDs blink now: which name? (a, b, c, d) [a]" in r.out
    assert r.stdout.splitlines()[-2:] == [f"a\t{b}", f"b\t{u1.port}"], r.out  # sorted by name
    assert u1.flasher_calls() == [["--identify", "--port", u1.port]]  # the last needs no blink


def test_the_next_unit_blinks_only_once_the_last_blink_is_over(u1: FakeU1) -> None:
    u1.add_port(PORT_B)
    u1.add_port("usb-1a86_USB_Single_Serial_TESTUNITC-if00")
    u1.identify({"exit": 0}, {"exit": 0})
    r = u1.source(in_work(u1, NAME_NEW), answers=["a", "b"], ACE2K_BLINK_S="1")
    assert r.returncode == 0, r.out
    start = [float(t) for t in (u1.fake / "flasher.times").read_text().split()]
    assert len(start) == 2 and start[1] - start[0] >= 1.0, start
    assert "Waiting for the LEDs of the last unit to stop." in r.out


def test_a_name_taken_is_asked_again(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    r = source(u1, "TAKEN=a; " + NAME_NEW, answers=["a", "e", "B"])
    assert r.out.count("which name?") == 3
    assert "a is taken" in r.out and "e is not one of: b, c, d" in r.out
    assert r.stdout.splitlines()[-2:] == [f"b\t{u1.port}", f"c\t{b}"], r.out


def test_one_new_unit_on_a_new_install_is_a_with_no_blink(u1: FakeU1) -> None:
    r = source(u1, "TAKEN=; " + NAME_NEW)
    assert r.stdout.splitlines()[-1] == f"a\t{u1.port}", r.out
    assert u1.flasher_calls() == [] and "which name?" not in r.out


def test_yes_refuses_two_new_units(u1: FakeU1) -> None:
    u1.add_port(PORT_B)
    r = source(u1, "YES=1; " + NAME_NEW)
    assert r.returncode != 0
    assert "run this from a terminal to name the units" in r.out
    assert "nothing changed" in r.out and u1.flasher_calls() == []


def test_a_unit_that_answers_nothing_stops_the_naming(u1: FakeU1) -> None:
    u1.add_port(PORT_B)
    u1.identify({"exit": 4})
    r = source(u1, NAME_NEW)
    assert r.returncode != 0
    assert f"Nothing answered on {u1.port}" in r.out and "nothing changed" in r.out


def test_a_flasher_failure_on_the_blink_is_not_called_nothing_answered(u1: FakeU1) -> None:
    u1.add_port(PORT_B)
    u1.identify({"exit": 1, "print": "The port is busy."})
    r = source(u1, NAME_NEW)
    assert r.returncode != 0 and "Nothing answered" not in r.out
    assert f"The flasher could not reach the unit on {u1.port} (status 1, see above)" in r.out
    assert "nothing changed" in r.out


def test_a_unit_that_cannot_blink_is_named_by_its_cable(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 5}, {"exit": 5})
    r = u1.dialog_source(
        in_work(u1, NAME_NEW),
        steps=[
            ("Unplug the USB cable of the unit that should be a", lambda: u1.remove_port(b), ""),
            ("Plug it back in", lambda: u1.add_port(PORT_B), ""),
        ],
    )
    assert r.returncode == 0, r.out
    assert r.stdout.splitlines()[-2:] == [f"a\t{b}", f"b\t{u1.port}"], r.out


def test_the_unplug_path_asks_again_until_one_cable_is_out(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 5})

    def both_out() -> None:
        u1.remove_port(u1.port)
        u1.remove_port(b)

    plug = "Plug it back in, then press Enter. "
    r = u1.dialog_source(
        in_work(u1, NAME_NEW),
        steps=[
            ("should be a, then press Enter.", None, ""),  # nothing unplugged
            ("No cable of a unit without a name was unplugged.", both_out, ""),
            ("2 cables were unplugged", lambda: u1.add_port(PORT_NAME), ""),
            (plug, None, ""),  # not back yet: asked again
            (plug + plug, lambda: u1.add_port(PORT_B), ""),
        ],
    )
    assert r.returncode == 0, r.out
    assert r.out.count("Unplug the USB cable") == 3
    assert r.stdout.splitlines()[-2:] == [f"a\t{b}", f"b\t{u1.port}"], r.out


def test_lanes_asked_head_by_head(u1: FakeU1) -> None:
    r = source(
        u1,
        'UNITS_NAMES=a,b; ask_lanes; echo "lanes=$LANES"',
        answers=["", "", "b:1+b:2", "c:1", "-"],
    )
    assert "joined by +, as a:1+a:2" in r.out
    assert "Head 1 (T0): which lanes feed it? [a:1]" in r.out
    assert "Head 4 (T3): which lanes feed it? [a:3]" in r.out
    assert "no unit c" in r.out  # head 4 asked again
    assert r.out.count("Head 4 (T3)") == 2
    assert r.stdout.strip().endswith("lanes=a:1, a:2, b:1+b:2, -"), r.out


def test_lanes_suggest_what_the_heads_have_today(u1: FakeU1) -> None:
    r = source(
        u1,
        'UNITS_NAMES=a,b; HEADS_NOW=$(printf "a:2\\na:1\\n-\\nb:1"); ask_lanes; '
        'echo "lanes=$LANES"',
        answers=["", "", "", ""],
    )
    assert "Head 1 (T0): which lanes feed it? [a:2]" in r.out
    assert r.stdout.strip().endswith("lanes=a:2, a:1, -, b:1"), r.out


def test_lanes_with_yes_take_the_suggestions(u1: FakeU1) -> None:
    r = source(u1, 'YES=1; UNITS_NAMES=a; ask_lanes; echo "lanes=$LANES"')
    assert r.stdout.strip().endswith("lanes=a:1, a:2, a:3, a:4"), r.out
    assert "which lanes feed it?" not in r.out


def test_match_units_resolves_the_paths_in_one_call(u1: FakeU1) -> None:
    # a Python start is slow on the U1: the serials and the cables in one, not per pair
    b = u1.add_port(PORT_B)
    with_cfg(u1, CFG_TWO.format(a=u1.port, b=b))
    log = u1.fake / "python.log"
    wrapper = u1.fake / "python"
    wrapper.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >>"{log}"\nexec python3 "$@"\n')
    wrapper.chmod(0o755)
    r = u1.source(in_work(u1, MATCH), ACE2K_PYTHON=str(wrapper))
    assert r.stdout == "gone=|new=|", r.out
    assert log.read_text().count("realpath") == 1


def test_match_units_tells_a_serial_from_a_cable_with_one_resolve(u1: FakeU1) -> None:
    # the one start reads the serials, then the cables: the split must keep each list whole
    b = u1.add_port(PORT_B)
    with_cfg(u1, CFG_TWO.format(a=u1.port, b="/dev/serial/by-id/usb-1a86_USB_Single_Serial_GONE"))
    r = source(u1, MATCH)
    assert r.stdout == f"gone=b\n|new={b}\n|", r.out


def python_log(u1: FakeU1) -> tuple[str, str]:
    """A python3 that logs its arguments: (its path, its log's path)."""
    log = u1.fake / "python.log"
    wrapper = u1.fake / "python"
    wrapper.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >>"{log}"\nexec python3 "$@"\n')
    wrapper.chmod(0o755)
    return str(wrapper), str(log)


def test_read_status_reads_the_unit_names_once_while_the_cfg_is_the_same(u1: FakeU1) -> None:
    # Klipper is polled up to 60 times: a Python start per poll for the names is slow on the U1
    with_cfg(u1, CFG_TWO.format(a=u1.port, b="x"))
    wrapper, log = python_log(u1)
    edit = f'printf "[ace2k c]\\n" >>"{u1.kcfg / "ace2k.cfg"}"'
    r = u1.source(
        in_work(u1, f"read_status; read_status; read_status; {edit}; read_status; read_status"),
        ACE2K_PYTHON=wrapper,
    )
    assert r.returncode == 0, r.out
    assert Path(log).read_text().count(" objects ") == 2


def test_lanes_start_one_cfg_tool_per_head_with_yes(u1: FakeU1) -> None:
    wrapper, log = python_log(u1)
    script = 'YES=1; UNITS_NAMES=a,b; HEADS_NOW=$(printf "a:1\\nc:2\\n-\\nb:1"); ask_lanes'
    r = u1.source(in_work(u1, script + '; echo "lanes=$LANES"'), ACE2K_PYTHON=wrapper)
    assert r.stdout.strip().endswith("lanes=a:1, a:2, -, b:1"), r.out
    assert Path(log).read_text().count("ace2k_cfg.py") == 4


def test_klipper_info_reads_the_state_and_the_pid_in_one_python_start(u1: FakeU1) -> None:
    # Klipper is polled up to 60 times while it starts: one start per look, not two
    u1.moonraker(ace2k_state("0.13.0", u1.port))
    wrapper, log = python_log(u1)
    r = u1.source(
        in_work(u1, 'klipper_info; echo "state=$KSTATE_NOW pid=$KPID"'), ACE2K_PYTHON=wrapper
    )
    assert "state=ready pid=100" in r.stdout, r.out
    assert Path(log).read_text().count("process_id") == 1


def test_klipper_info_of_a_klipper_that_is_down_is_empty(u1: FakeU1) -> None:
    st = ace2k_state("0.13.0", u1.port)
    st["klippy_state"] = "disconnected"
    u1.moonraker(st)
    r = source(u1, 'klipper_info && echo "state=$KSTATE_NOW|pid=$KPID|"')
    assert "state=|pid=|" in r.stdout, r.out


def test_write_uids_starts_one_cfg_tool_per_unit(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    with_cfg(u1, CFG_TWO.format(a=u1.port, b=b))
    u1.moonraker(ace2k_state("0.13.0", "", units=[("a", u1.port, "0a"), ("b", b, "0b")]))
    wrapper, log = python_log(u1)
    r = u1.source(
        in_work(u1, "MODE=new; read_status; read_units; write_uids"), ACE2K_PYTHON=wrapper
    )
    assert "uid: written for a, b " in r.stdout, r.out
    text = (u1.kcfg / "ace2k.cfg").read_text()
    assert "mcu: ace2k_a\nuid: 0a\n" in text and "mcu: ace2k_b\nuid: 0b\n" in text, text
    # one start per unit: the release's own cfg tool may take a single NAME UID pair only
    assert Path(log).read_text().count("ace2k_cfg.py uid") == 2


def test_lanes_check_a_typed_answer_once_more(u1: FakeU1) -> None:
    wrapper, log = python_log(u1)
    r = u1.source(
        in_work(u1, 'UNITS_NAMES=a; ask_lanes; echo "lanes=$LANES"'),
        answers=["a:4", "", "", ""],
        ACE2K_PYTHON=wrapper,
    )
    assert r.stdout.strip().endswith("lanes=a:4, a:1, a:2, a:3"), r.out
    assert Path(log).read_text().count("ace2k_cfg.py") == 5


def test_the_end_of_the_input_is_an_answer_that_fails(u1: FakeU1) -> None:
    r = source(u1, 'ask "Q?" && echo "got=$ANSWER"; ask "Q?" || echo "eof=$ANSWER|"', [""])
    assert "got=\n" in r.stdout and "eof=|" in r.stdout, r.out


def test_confirm_takes_the_end_of_the_input_as_no(u1: FakeU1) -> None:
    r = source(u1, 'confirm "Go?" || echo no')
    assert r.returncode == 0 and r.stdout.strip() == "no", r.out


def test_lanes_stop_when_the_input_ends(u1: FakeU1) -> None:
    r = source(u1, "UNITS_NAMES=a; ask_lanes; echo after", ["c:1"])
    assert r.returncode != 0 and "after" not in r.stdout
    assert "no unit c" in r.out and "nothing changed" in r.out


def test_a_name_stops_when_the_input_ends(u1: FakeU1) -> None:
    u1.add_port(PORT_B)
    u1.identify({"exit": 0})
    r = source(u1, NAME_NEW + "; echo after", ["e"])
    assert r.returncode != 0 and "after" not in r.stdout
    assert "e is not one of" in r.out and "nothing changed" in r.out


def test_the_unplug_path_stops_when_the_input_ends(u1: FakeU1) -> None:
    u1.add_port(PORT_B)
    u1.identify({"exit": 5})
    r = source(u1, NAME_NEW + "; echo after", [""])  # Enter with nothing unplugged, then the end
    assert r.returncode != 0 and "after" not in r.stdout
    assert "No cable of a unit without a name was unplugged." in r.out
    assert "nothing changed" in r.out


def test_the_plug_back_question_stops_when_the_input_ends(u1: FakeU1) -> None:
    b = u1.add_port(PORT_B)
    u1.identify({"exit": 5})
    r = u1.dialog_source(
        in_work(u1, NAME_NEW + "; echo after"),
        steps=[("should be a, then press Enter.", lambda: u1.remove_port(b), "")],
    )
    assert r.returncode != 0 and "after" not in r.stdout
    assert "Plug it back in" in r.out and "nothing changed" in r.out


def test_a_refused_lanes_entry_of_today_falls_back_to_the_next_free_lane(u1: FakeU1) -> None:
    # HEADS_NOW names a unit that is not there any more: its entry is not offered
    r = source(
        u1,
        'UNITS_NAMES=a; HEADS_NOW=$(printf "a:1\\nc:2\\n-\\n-"); ask_lanes; echo "lanes=$LANES"',
        ["", "", "", ""],
    )
    assert "Head 2 (T1): which lanes feed it? [a:2]" in r.out
    assert r.out.count("Head 2 (T1)") == 1
    assert r.stdout.strip().endswith("lanes=a:1, a:2, -, -"), r.out


def test_klipper_that_still_answers_after_its_stop_stops_the_run(u1: FakeU1) -> None:
    (u1.fake / "klipper.noop").write_text("")  # the init script returns, Klipper keeps running
    r = source(u1, "stop_klipper_once; echo after")
    assert r.returncode != 0 and "after" not in r.stdout
    assert "Klipper still answers after its stop" in r.out and "no unit was written" in r.out
    assert "Klipper was not stopped, so it is not started again" in r.out
    assert u1.klipper_log() == ["stop"]  # no start: the run never stopped it
