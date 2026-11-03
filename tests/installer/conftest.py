"""The fake U1 fixture for install.sh's tests."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from fake_u1 import FakeU1  # noqa: E402


@pytest.fixture
def u1(tmp_path: Path) -> Iterator[FakeU1]:
    fake = FakeU1(tmp_path / "root")
    yield fake
    fake.close()
