"""Every G-code macro the configuration declares is reachable by its own name, and none takes a
name the adapter registers itself.

Klipper's G-code parser splits a line into letters and values: a command name ends at its first
digit, and whatever follows is read as a parameter. A macro named `ACEK_EJECT2X` would answer to
`ACEK_EJECT2` with a parameter `X`, so it could never be called. The upstream parser splits on
`([A-Z_]+|[A-Z*])`; the Snapmaker U1's tree on `([A-Z_]+|[A-Z*/])`. Both are checked. The same
check guards ace2k's own configuration in its tree.
"""

import re
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[1] / "config"
SPLITTERS = {
    "upstream": re.compile("([A-Z_]+|[A-Z*])"),
    "snapmaker-u1": re.compile("([A-Z_]+|[A-Z*/])"),
}
SECTION = re.compile(r"^\[gcode_macro\s+([^\]\s]+)\s*\]", re.MULTILINE)
# the adapter's own commands: a macro of the same name would clash at Klipper's start
ADAPTER_COMMANDS = ("ACEK_ADAPTER_STATUS", "ACEK_EJECT")
# a split line with a command: what came before it, the letters, then the rest
SPLIT_PARTS = 3


def macros() -> list[tuple[str, str]]:
    """(file name, macro name) for every [gcode_macro NAME] section under config/."""
    found = []
    for path in sorted(CONFIG.glob("*.cfg")):
        found.extend(
            (path.name, name) for name in SECTION.findall(path.read_text(encoding="utf-8"))
        )
    return found


def klipper_command(line: str, splitter: re.Pattern[str]) -> str:
    """The command name Klipper's parser reads off a line, as klippy/gcode.py does."""
    parts = splitter.split(line.upper())
    if len(parts) >= SPLIT_PARTS:
        return parts[1] + parts[2].strip()
    return line.upper()


def test_no_macro_takes_an_adapter_command_name() -> None:
    # ACEK_EJECT (ACE_EJECT then) was a macro of this file before the adapter took it over
    assert [m for m in macros() if m[1].upper() in ADAPTER_COMMANDS] == []


@pytest.mark.parametrize("flavour", sorted(SPLITTERS))
def test_every_macro_name_is_its_own_command(flavour: str) -> None:
    for cfg, name in macros():
        cmd = klipper_command(name, SPLITTERS[flavour])
        assert cmd == name.upper(), (
            f"{cfg}: [gcode_macro {name}] is read by Klipper's {flavour} parser as command"
            f" {cmd!r} — a digit in a macro name starts a parameter; rename it without digits"
        )


def test_the_name_check_catches_a_digit() -> None:
    assert klipper_command("ACEK_EJECT2X", SPLITTERS["upstream"]) == "ACEK_EJECT2"


def section(name: str) -> str:
    """The text of one [gcode_macro NAME] section of config/ace2k-u1.cfg."""
    text = (CONFIG / "ace2k-u1.cfg").read_text(encoding="utf-8")
    start = text.index(f"[gcode_macro {name}]")
    end = text.find("\n[", start + 1)
    return text[start : end if end != -1 else None]


def test_the_swaps_load_ends_with_the_u1s_clean() -> None:
    # The load cleans, then the swap's U1 pre-extrusion cleans again: two cleans in a row.
    load = section("ACEK_SWAP_LOAD")
    assert load.rstrip().splitlines()[-1].strip() == "ROUGHLY_CLEAN_NOZZLE_WITH_DISCARD"
    assert "MOVE_TO_DISCARD_FILAMENT_POSITION" in load


def test_the_swaps_load_runs_at_one_temperature() -> None:
    load = section("ACEK_SWAP_LOAD")
    assert "G1 E40 F360" in load and "G1 E10 F360" in load
    # The swap passes PRINT_TEMP = LOAD_TEMP, so there is no fan step to cool the head.
    assert "M106" not in load
    # The only branch is the grip, which a background swap skips (GRIP=0).
    assert load.count("{% if") == 1 and "{% if params.GRIP|default(1)|int %}" in load


def test_the_swaps_load_skips_only_the_grip_with_grip_0() -> None:
    load = section("ACEK_SWAP_LOAD")
    lines = [ln.split(";")[0].strip() for ln in load.splitlines()]
    branch = lines.index("{% if params.GRIP|default(1)|int %}")
    assert lines[branch + 1 : branch + 3] == ["G1 E40 F360", "{% endif %}"]
    assert lines[branch + 3] == "G1 E10 F360"
