"""Put this tree's klippy/extras on the path so the adapter imports by bare name, as Klipper's
extras do in the host tests of ace2k, and install the fake U1 side-feeder module in its place."""

import sys
from pathlib import Path

EXTRAS = Path(__file__).resolve().parents[1] / "klippy" / "extras"
TESTS = Path(__file__).resolve().parent
for path in (EXTRAS, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import fakes  # noqa: E402 — after the path setup

sys.modules.setdefault("filament_feed", fakes.fake_module())
