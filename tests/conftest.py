"""Put this tree's klippy/extras on the path so the adapter imports by bare name, as Klipper's
extras do in the host tests of ace2k, and install the fake U1 side-feeder module in its place.

ace2k's own klippy/extras goes on the path too: the adapter takes the rule that says which units a
config declares from ace2k's coordinator (ace2k_units), as it does on the printer, where both
install into klippy/extras. It is read from ACE2K_EXTRAS when set, else from an ace2k checkout
beside this one (../ace2k)."""

import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ROOT / "klippy" / "extras"
TESTS = ROOT / "tests"
ACE2K_EXTRAS = Path(os.environ.get("ACE2K_EXTRAS") or ROOT.parent / "ace2k" / "klippy" / "extras")
if not (ACE2K_EXTRAS / "ace2k_units.py").is_file():
    pytest.exit(
        f"ace2k's klippy/extras not found at {ACE2K_EXTRAS}: check ace2k out beside this tree,"
        " or set ACE2K_EXTRAS",
        returncode=2,
    )
# ace2k's extras last: a module of this tree, or a fake, wins over a name both carry
for path in (ACE2K_EXTRAS, EXTRAS, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# ace2k's helper module imports klippy's msgproto for the message parser's error class; the
# printer has it, the host tests stand in for it as ace2k's own do
if "msgproto" not in sys.modules:
    msgproto = types.ModuleType("msgproto")

    class error(Exception):  # noqa: N801, N818 — klippy's own name for it
        """msgproto.error as klippy declares it."""

    msgproto.error = error
    sys.modules["msgproto"] = msgproto

import fakes  # noqa: E402 — after the path setup

sys.modules.setdefault("filament_feed", fakes.fake_module())
