"""The installer's config tool, tools/ace2k_cfg.py.

Most tests call the tool's main() in this process; the smoke tests at the end run it as install.sh
does, as python3 -I ace2k_cfg.py COMMAND ARGS.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "ace2k_cfg.py"
if str(TOOL.parent) not in sys.path:
    sys.path.insert(0, str(TOOL.parent))

import ace2k_cfg  # noqa: E402

SER_A = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_TESTUNITA-if00"
SER_B = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_TESTUNITB-if00"
UID_A = "0123456789abcdef01234567"

ONE = f"""\
# my notes
[mcu ace2k]
serial: {SER_A}
baud: 250000
restart_method: command

[ace2k]
feed_speed: 40
#uid: <the unit's UID>

[include ace2k_tags.cfg]

# the sensors
[temperature_sensor ace_a_chamber]
sensor_type: ace2k
sensor: chamber

#[temperature_sensor ace_mcu]
#sensor_type: temperature_mcu
#sensor_mcu: ace2k
"""


def call(*argv: str) -> tuple[int, bytes, str]:
    """The tool's main() on argv in this process: (exit status, stdout as bytes, stderr)."""
    out = io.BytesIO()
    text = io.TextIOWrapper(out, encoding="utf-8", newline="", write_through=True)
    err = io.StringIO()
    with contextlib.redirect_stdout(text), contextlib.redirect_stderr(err):
        rc = ace2k_cfg.main(list(argv))
    text.detach()  # flushes it; the BytesIO stays open
    return rc, out.getvalue(), err.getvalue()


def run(*argv: str, ok: bool = True) -> str:
    """stdout of a command that succeeds, or stderr of one that fails (ok=False)."""
    rc, out, err = call(*argv)
    assert (rc == 0) == ok, err
    return out.decode() if ok else err


def put(tmp: Path, name: str, text: str) -> str:
    f = tmp / name
    f.write_text(text)
    return str(f)


def test_units_of_one_unnamed_unit(tmp_path: Path) -> None:
    assert run("units", put(tmp_path, "a.cfg", ONE)) == f"\t{SER_A}\t\n"


def test_units_reads_klipper_spelling(tmp_path: Path) -> None:
    text = f"[MCU  Ace2k_A]\nserial: {SER_A}\n[ACE2K  A]\nmcu: ace2k_a\nuid: {UID_A.upper()}\n"
    assert run("units", put(tmp_path, "a.cfg", text)) == f"a\t{SER_A}\t{UID_A}\n"


def test_rename_keeps_every_other_line(tmp_path: Path) -> None:
    out = run("rename-a", put(tmp_path, "a.cfg", ONE))
    assert out == ONE.replace("[mcu ace2k]", "[mcu ace2k_a]").replace(
        "[ace2k]\n", "[ace2k a]\nmcu: ace2k_a\n"
    ).replace("sensor_type: ace2k\n", "sensor_type: ace2k\nunit: a\n").replace(
        "#sensor_mcu: ace2k\n", "#sensor_mcu: ace2k_a\n"
    )


def test_add_puts_the_unit_before_the_sensors_and_its_sensors_last(tmp_path: Path) -> None:
    named = run("rename-a", put(tmp_path, "a.cfg", ONE))
    out = run("add", put(tmp_path, "n.cfg", named), "b", SER_B)
    lines = out.splitlines()
    assert lines.index("[ace2k b]") < lines.index("# the sensors")
    assert lines[lines.index("[mcu ace2k_b]") + 1] == f"serial: {SER_B}"
    assert out.endswith(
        "[temperature_sensor ace_b_chamber]\nsensor_type: ace2k\nunit: b\nsensor: chamber\n"
    )
    assert "[temperature_sensor ace_b_ptc_left]" in out
    assert run("units", put(tmp_path, "o.cfg", out)) == f"a\t{SER_A}\t\nb\t{SER_B}\t\n"


def test_drop_takes_out_the_unit_and_its_sensors_only(tmp_path: Path) -> None:
    named = run("rename-a", put(tmp_path, "a.cfg", ONE))
    two = run("add", put(tmp_path, "n.cfg", named), "b", SER_B)
    assert run("drop", put(tmp_path, "t.cfg", two), "b") == named


def test_uid_goes_after_mcu_and_never_replaces(tmp_path: Path) -> None:
    named = run("rename-a", put(tmp_path, "a.cfg", ONE))
    out = run("uid", put(tmp_path, "n.cfg", named), "a", UID_A)
    assert f"[ace2k a]\nmcu: ace2k_a\nuid: {UID_A}\n" in out
    again = run("uid", put(tmp_path, "o.cfg", out), "a", "f" * 24)
    assert again == out


def test_render_names_every_unit(tmp_path: Path) -> None:
    tpl = put(
        tmp_path,
        "tpl.cfg",
        ONE.replace(SER_A, "/dev/serial/by-id/<the unit's USB adapter>").replace(
            "#[temperature_sensor ace_mcu]\n#sensor_type: temperature_mcu\n#sensor_mcu: ace2k\n",
            "[temperature_sensor ace_mcu]\nsensor_type: temperature_mcu\nsensor_mcu: ace2k\n",
        ),
    )
    out = run("render", tpl, f"a={SER_A}", f"b={SER_B}")
    assert run("units", put(tmp_path, "o.cfg", out)) == f"a\t{SER_A}\t\nb\t{SER_B}\t\n"
    assert (
        "#[temperature_sensor ace_mcu]\n#sensor_type: temperature_mcu\n#sensor_mcu: ace2k_a\n"
        in out
    )


U1 = "[ace2k_u1]\n#lanes: 1, 2, 3, 4       # comment\nfeed_speed: 70\n"


@pytest.mark.parametrize(
    ("lanes", "heads"),
    [
        (None, ["a:1", "a:2", "a:3", "a:4"]),
        ("lanes: 1+2, 3, 4", ["a:1+a:2", "-", "a:3", "a:4"]),
        ("lanes: 2, 4", ["-", "a:2", "-", "a:4"]),
        ("lanes: a:1+a:2, a:3+a:4, -", ["a:1+a:2", "a:3+a:4", "-", "-"]),
    ],
)
def test_heads_in_the_named_form(tmp_path: Path, lanes: str | None, heads: list[str]) -> None:
    text = U1 if lanes is None else U1.replace("feed_speed", lanes + "\nfeed_speed")
    assert run("heads", put(tmp_path, "u.cfg", text), "a").split() == heads


def test_set_lanes_replaces_the_value_and_keeps_the_comment(tmp_path: Path) -> None:
    text = U1.replace("feed_speed", "lanes: 1, 2,\n  3, 4\nfeed_speed")
    out = run("set-lanes", put(tmp_path, "u.cfg", text), "a:1, a:2, b:1, -")
    kept = "#lanes: 1, 2, 3, 4       # comment\nfeed_speed: 70\n"
    assert out == "[ace2k_u1]\nlanes: a:1, a:2, b:1, -\n" + kept


@pytest.mark.parametrize("gap", ["   ", "", "# a note"])
def test_lanes_read_on_past_a_blank_or_comment_line_as_klipper_does(
    tmp_path: Path, gap: str
) -> None:
    # Klipper keeps reading a value past such a line while an indented line follows it
    text = U1.replace("feed_speed", f"lanes: a:1, a:2,\n{gap}\n  a:3+a:4\n\nfeed_speed")
    path = put(tmp_path, "u.cfg", text)
    assert run("heads", path, "a").split() == ["a:1", "a:2", "a:3+a:4", "-"]
    out = run("set-lanes", path, "a:1, a:2, a:3, a:4")
    assert "a:3+a:4" not in out  # the whole old value goes, none of it is left behind
    kept = "#lanes: 1, 2, 3, 4       # comment\n\nfeed_speed: 70\n"
    assert out == "[ace2k_u1]\nlanes: a:1, a:2, a:3, a:4\n" + kept  # the new value after the header


def test_drop_lanes_leaves_the_head_to_the_u1(tmp_path: Path) -> None:
    text = U1.replace("feed_speed", "lanes: a:1+a:2, a:3, a:4, b:1\nfeed_speed")
    out = run("drop-lanes", put(tmp_path, "u.cfg", text), "b")
    assert "lanes: a:1+a:2, a:3, a:4, -\n" in out


@pytest.mark.parametrize(
    ("answer", "used", "reason"),
    [
        ("c:1", "", "no unit c"),
        ("a:5", "", "lane 5 is not 1-4"),
        ("a:1", "a:1", "a:1 already feeds another head"),
        ("a:1+a:1", "", "a:1 twice"),
        ("1", "", "write the unit: a:1"),
        ("", "", "empty"),
    ],
)
def test_check_head_refuses(answer: str, used: str, reason: str) -> None:
    assert reason in run("check-head", answer, "a,b", used, ok=False)


def test_check_head_normalises() -> None:
    assert run("check-head", " A:1 + a:2 ", "a,b", "") == "a:1+a:2\n"
    assert run("check-head", "-", "a", "") == "-\n"


def test_head_default_without_today_offers_the_next_free_lane() -> None:
    assert run("head-default", "", "a,b", "a:1,a:2,a:3,a:4") == "b:1\n"
    assert run("head-default", "", "a", "a:1,a:2,a:3,a:4") == "-\n"


def test_suggest_is_no_command() -> None:
    assert call("suggest", "a", "")[0] == 2


def test_rename_keeps_a_comment_after_a_header(tmp_path: Path) -> None:
    text = ONE.replace("[mcu ace2k]\n", "[mcu ace2k]   # the unit\n").replace(
        "[ace2k]\n", "[ace2k] ; the driver\n"
    )
    out = run("rename-a", put(tmp_path, "a.cfg", text))
    assert "[mcu ace2k_a]   # the unit\n" in out
    assert "[ace2k a] ; the driver\nmcu: ace2k_a\n" in out


def test_rename_renames_sensor_mcu_in_any_case(tmp_path: Path) -> None:
    text = ONE.replace("#sensor_mcu: ace2k\n", "#Sensor_MCU: ACE2K\n")
    out = run("rename-a", put(tmp_path, "a.cfg", text))
    assert "#Sensor_MCU: ace2k_a\n" in out


def run_bytes(*argv: str) -> tuple[int, bytes]:
    """(status, stdout as bytes) of a command."""
    rc, out, _err = call(*argv)
    return rc, out


@pytest.mark.parametrize("ending", ["\r\n", "\n"])
@pytest.mark.parametrize("final", [True, False])
def test_an_edit_keeps_the_line_endings_and_the_final_newline(
    tmp_path: Path, ending: str, final: bool
) -> None:
    text = ONE.replace("\n", ending)
    if not final:
        text = text[: -len(ending)]
    f = tmp_path / "a.cfg"
    f.write_bytes(text.encode())
    rc, out = run_bytes("uid", str(f), "", UID_A)
    assert rc == 0
    want = text.replace(f"[ace2k]{ending}", f"[ace2k]{ending}uid: {UID_A}{ending}")
    assert out == want.encode()


@pytest.mark.parametrize("lanes", ["lanes: 0, 2", "lanes: 9", "lanes: x", "lanes: \u00b2"])
def test_heads_refuses_a_bare_lane_out_of_range(tmp_path: Path, lanes: str) -> None:
    f = put(tmp_path, "u.cfg", U1.replace("feed_speed", lanes + "\nfeed_speed"))
    rc, _out, err = call("heads", f, "a")
    assert rc == 2 and err.startswith("ace2k_cfg: lanes: "), err


def test_add_without_sensors_leaves_a_blank_line_before_the_unit(tmp_path: Path) -> None:
    text = f"[mcu ace2k_a]\nserial: {SER_A}\n\n[ace2k a]\nmcu: ace2k_a\n"
    out = run("add", put(tmp_path, "a.cfg", text), "b", SER_B)
    assert out.startswith(text + "\n[mcu ace2k_b]\n"), out


def test_a_file_with_mixed_line_endings_parses(tmp_path: Path) -> None:
    f = tmp_path / "a.cfg"
    f.write_bytes(f"[mcu ace2k_a]\r\nserial: {SER_A}\n[ace2k a]\r\nmcu: ace2k_a\n".encode())
    assert run("units", str(f)) == f"a\t{SER_A}\t\n"


TEMPLATE = Path(__file__).resolve().parents[3] / "ace2k" / "config" / "ace2k.cfg"


def test_drop_keeps_the_comment_blocks_between_sections(tmp_path: Path) -> None:
    two = run("render", str(TEMPLATE), f"a={SER_A}", f"b={SER_B}")
    out = run("drop", put(tmp_path, "t.cfg", two), "a")
    assert run("units", put(tmp_path, "o.cfg", out)) == f"b\t{SER_B}\t\n"
    assert "\n[ace2k a]" not in out and "\nunit: a\n" not in out
    for kept in ("#[temperature_sensor ace_mcu]\n", "# Several units:", "#[mcu ace2k_a]\n"):
        assert out.count(kept) == two.count(kept) == 1, kept
    assert "\n\n\n" not in out


def test_drop_takes_a_key_after_a_blank_line_inside_the_section(tmp_path: Path) -> None:
    text = f"[mcu ace2k_b]\nserial: {SER_B}\n\nbaud: 250000\n\n# kept\n[ace2k a]\nmcu: ace2k_a\n"
    assert run("drop", put(tmp_path, "a.cfg", text), "b") == "# kept\n[ace2k a]\nmcu: ace2k_a\n"


@pytest.mark.parametrize(
    ("line", "want"),
    [
        ("mcu: ace2k", "mcu: ace2k_a"),
        ("MCU = Ace2K   # the unit", "MCU = ace2k_a   # the unit"),
        ("mcu:ace2k", "mcu:ace2k_a"),
        ("mcu: ace2k_other", "mcu: ace2k_other"),
    ],
)
def test_rename_points_an_explicit_mcu_at_the_renamed_mcu(
    tmp_path: Path, line: str, want: str
) -> None:
    out = run("rename-a", put(tmp_path, "a.cfg", ONE.replace("[ace2k]\n", f"[ace2k]\n{line}\n")))
    assert f"[ace2k a]\n{want}\nfeed_speed: 40\n" in out, out


@pytest.mark.parametrize("lane", ["²", "١", "1٠"])
def test_check_head_takes_ascii_lane_digits_only(lane: str) -> None:
    rc, _out, err = call("check-head", f"a:{lane}", "a", "")
    assert rc == 1 and err == f"lane {lane} is not 1-4\n", err


@pytest.mark.parametrize(
    ("cmd", "extra"), [("uid", ("a", UID_A)), ("drop", ("b",)), ("add", ("c", SER_B))]
)
def test_an_edit_of_a_mixed_file_keeps_each_lines_own_ending(
    tmp_path: Path, cmd: str, extra: tuple[str, ...]
) -> None:
    named = run("rename-a", put(tmp_path, "a.cfg", ONE))
    two = run("add", put(tmp_path, "n.cfg", named), "b", SER_B)
    lf = two.splitlines()
    mixed = "".join(ln + ("\r\n" if i % 3 == 0 else "\n") for i, ln in enumerate(lf))
    f = tmp_path / "m.cfg"
    f.write_bytes(mixed.encode())
    rc, out = run_bytes(cmd, str(f), *extra)
    assert rc == 0
    want = run(cmd, put(tmp_path, "lf.cfg", two), *extra).splitlines()
    got = out.decode().split("\n")
    assert got.pop() == ""
    assert [ln.removesuffix("\r") for ln in got] == want
    before = mixed.split("\n")[:-1]
    for ln in got:  # an unchanged line keeps its own ending; a new one takes the commoner (LF)
        t = ln.removesuffix("\r")
        if lf.count(t) == want.count(t) == 1:
            assert ln in before, ln
        elif t not in lf:
            assert not ln.endswith("\r"), ln


def test_objects_keep_the_sections_own_spelling(tmp_path: Path) -> None:
    text = f"[mcu ace2k_a]\nserial: {SER_A}\n[ace2k  A] # x\nmcu: ace2k_a\n[ace2k_u1]\n[ace2k]\n"
    assert run("objects", put(tmp_path, "a.cfg", text)) == "ace2k  A\nace2k\n"


@pytest.mark.parametrize(
    ("today", "used", "offer"),
    [(" A:2 ", "", "a:2"), ("c:1", "", "a:1"), ("a:1", "a:1", "a:2"), ("", "a:1", "a:2")],
)
def test_head_default_offers_todays_entry_while_it_is_taken(
    today: str, used: str, offer: str
) -> None:
    assert run("head-default", today, "a,b", used) == offer + "\n"


def test_rename_keeps_the_ending_of_an_mcu_line_it_does_not_change(tmp_path: Path) -> None:
    f = tmp_path / "a.cfg"
    f.write_bytes(b"[ace2k]\nmcu: other\r\nfoo: 1\n")
    rc, out = run_bytes("rename-a", str(f))
    assert rc == 0 and out == b"[ace2k a]\nmcu: other\r\nfoo: 1\n", out


@pytest.mark.parametrize(
    "argv", [(), ("nothing",), ("units",), ("render", "tpl.cfg"), ("uid", "a.cfg", "a")]
)
def test_a_bad_command_line_exits_2(argv: tuple[str, ...]) -> None:
    rc, out, err = call(*argv)
    assert rc == 2 and out == b"", out
    assert err == f"ace2k_cfg: bad command line: {' '.join(argv)} (see the docstring)\n"


def test_render_refuses_a_unit_without_its_serial(tmp_path: Path) -> None:
    rc, _out, err = call("render", put(tmp_path, "t.cfg", ONE), "a")
    assert rc == 2 and "render wants NAME=SERIAL, not a" in err, err


# The smoke tests: the tool as install.sh runs it, in its own Python.


def tool(*argv: str) -> subprocess.CompletedProcess[bytes]:
    """The tool run as install.sh runs it: python3 -I ace2k_cfg.py ARGS."""
    return subprocess.run(
        [sys.executable, "-I", str(TOOL), *argv], capture_output=True, check=False
    )


def test_smoke_units(tmp_path: Path) -> None:
    p = tool("units", put(tmp_path, "a.cfg", ONE))
    assert p.returncode == 0 and p.stdout == f"\t{SER_A}\t\n".encode(), p.stderr


def test_smoke_an_edit_keeps_crlf(tmp_path: Path) -> None:
    f = tmp_path / "a.cfg"
    f.write_bytes(ONE.replace("\n", "\r\n").encode())
    p = tool("uid", str(f), "", UID_A)
    want = ONE.replace("[ace2k]\n", f"[ace2k]\nuid: {UID_A}\n").replace("\n", "\r\n")
    assert p.returncode == 0 and p.stdout == want.encode(), p.stderr


def test_smoke_a_bad_lanes_value_exits_2_without_a_traceback(tmp_path: Path) -> None:
    p = tool("heads", put(tmp_path, "u.cfg", U1.replace("feed_speed", "lanes: 9\nfeed_speed")), "a")
    assert p.returncode == 2 and b"Traceback" not in p.stderr, p.stderr
    assert p.stderr == b"ace2k_cfg: lanes: 9 is not a lane 1-4 (see the docstring)\n"


def test_smoke_a_refused_head_exits_1(tmp_path: Path) -> None:
    p = tool("check-head", "c:1", "a,b", "")
    assert p.returncode == 1 and p.stderr == b"no unit c; units: a, b\n", p.stderr


def test_two_reads_keep_each_files_own_style(tmp_path: Path) -> None:
    first = tmp_path / "crlf.cfg"
    first.write_bytes(b"x\r\ny")
    second = tmp_path / "lf.cfg"
    second.write_bytes(b"a\n")
    lines, style = ace2k_cfg.read(str(first))
    ace2k_cfg.read(str(second))
    out = io.BytesIO()
    text = io.TextIOWrapper(out, encoding="utf-8")
    with contextlib.redirect_stdout(text):
        ace2k_cfg.write([*lines, "new"], style)
    assert out.getvalue() == b"x\r\ny\r\nnew"
    text.detach()


def test_render_names_the_template_unit_after_the_first_pair(tmp_path: Path) -> None:
    out = run("render", put(tmp_path, "tpl.cfg", ONE), f"b={SER_B}")
    assert run("units", put(tmp_path, "o.cfg", out)) == f"b\t{SER_B}\t\n"
    assert "[ace2k b]\nmcu: ace2k_b\n" in out and "unit: b\n" in out
    assert "#sensor_mcu: ace2k_b\n" in out


def test_rename_inserts_in_order_and_replaces_in_place(tmp_path: Path) -> None:
    lines = ace2k_cfg.read(put(tmp_path, "a.cfg", ONE))[0]
    out = ace2k_cfg.rename(lines, "c")
    assert lines == ace2k_cfg.read(put(tmp_path, "b.cfg", ONE))[0]  # the input is not changed
    assert len(out) == len(lines) + 2  # mcu: and unit: inserted
