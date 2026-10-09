"""Every G-code macro the configuration declares is reachable by its own name, and none takes a
name the adapter registers itself.

Klipper's G-code parser splits a line into letters and values: a command name ends at its first
digit, and whatever follows is read as a parameter. A macro named `ACE_EJECT2X` would answer to
`ACE_EJECT2` with a parameter `X`, so it could never be called. The upstream parser splits on
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
ADAPTER_COMMANDS = ("ACE_ADAPTER_STATUS", "ACE_EJECT")


def macros():
    """(file name, macro name) for every [gcode_macro NAME] section under config/."""
    found = []
    for path in sorted(CONFIG.glob("*.cfg")):
        for name in SECTION.findall(path.read_text(encoding="utf-8")):
            found.append((path.name, name))
    return found


def klipper_command(line, splitter):
    """The command name Klipper's parser reads off a line, as klippy/gcode.py does."""
    parts = splitter.split(line.upper())
    if len(parts) >= 3:
        return parts[1] + parts[2].strip()
    return line.upper()


def test_no_macro_takes_an_adapter_command_name():
    # ACE_EJECT was a macro of this file before the adapter took it over
    assert [m for m in macros() if m[1].upper() in ADAPTER_COMMANDS] == []


@pytest.mark.parametrize("flavour", sorted(SPLITTERS))
def test_every_macro_name_is_its_own_command(flavour):
    for cfg, name in macros():
        cmd = klipper_command(name, SPLITTERS[flavour])
        assert cmd == name.upper(), (
            f"{cfg}: [gcode_macro {name}] is read by Klipper's {flavour} parser as command"
            f" {cmd!r} — a digit in a macro name starts a parameter; rename it without digits"
        )


def test_the_name_check_catches_a_digit():
    assert klipper_command("ACE_EJECT2X", SPLITTERS["upstream"]) == "ACE_EJECT2"
