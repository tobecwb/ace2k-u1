"""The adapter's log lines for a read that fails and a command it cannot wrap (route step 8, D8)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import ace2k_u1_hooks
import ace2k_u1_unload_watch
import test_tools
from test_check import Gcode

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_a_temperature_check_that_cannot_wrap_a_command_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = test_tools.tools_rig(tmp_path)[0]
    gcode = Gcode()
    gcode.register_command("M104", lambda gcmd: None)
    caplog.clear()
    rig.module.wraps.wrap_temp_checks(gcode)
    lines = [r.getMessage() for r in caplog.records]
    assert "ace2k_u1: no M109 to wrap: its temperature is not checked" in lines
    assert "ace2k_u1: no SDCARD_PRINT_FILE to wrap: its temperatures are not checked" in lines
    assert not any("no M104" in line for line in lines)


def test_the_unload_log_reads_a_reading_that_is_not_a_number_as_unknown() -> None:
    assert ace2k_u1_unload_watch._text(float("nan"), "{:.1f}") == "unknown"
    assert ace2k_u1_unload_watch._text(float("inf"), "{:.1f}") == "unknown"
    assert ace2k_u1_unload_watch._text(2.0, "{:.1f}") == "2.0"


def test_a_lane_whose_mode_cannot_be_read_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = test_tools.tools_rig(tmp_path)[0]
    adapter = rig.module.adapter
    h = adapter.hooked[0]

    def lane_status(*_args: object, **_kw: object) -> dict[str, object]:
        raise RuntimeError("status unread")

    adapter.lane_status = lane_status
    slot = adapter.slot_of_head(h.head)
    assert slot is not None
    with caplog.at_level(logging.ERROR):
        assert ace2k_u1_hooks._mode(adapter, h) is None
        assert rig.module.wraps._lanes_still(slot) is False  # an unread lane is moving
    assert caplog.text.count(f"ace2k_u1: lane {adapter.lane_text(h)} status") == 2
