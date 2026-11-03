"""The temperature check: a temperature that does not fit the material of the lane in the head.

It only warns, on the console (and so on the page's events): at a print's start for each tool of
the file, and outside a print for M104, M109 and the temperature control of the screen.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import ace2k_u1_config
import ace2k_u1_temps
import fakes
import pytest
from test_tools import FIXTURES, print_start, run, set_plan, temp, tools_rig
from test_units import Gcmd, LanesConfig

if TYPE_CHECKING:
    from types import SimpleNamespace

ONE_PER_HEAD = "a:1, a:2, a:3, a:4"


def warnings(rig: SimpleNamespace) -> list[str]:
    return [ln for ln in rig.gcode.lines if " °C is " in ln]


def material(rig: SimpleNamespace, lane: str, kind: str) -> None:
    unit, n = lane.split(":")
    run(rig, "ACEK_SPOOL", {"UNIT": unit, "LANE": n, "TYPE": kind, "COLOR": "ffffff"})


def screen(rig: SimpleNamespace, **params: object) -> list[dict[str, object]]:
    """The temperature control of the screen (Moonraker's printer.control.extruder_temp)."""
    request = fakes.FakeWebRequest(params)
    rig.printer.lookup_object("webhooks").call(ace2k_u1_temps.TEMP_ENDPOINT, request)
    return request.sent


# --- the table --------------------------------------------------------------------------------


def config(value: str | None) -> LanesConfig:
    values = {} if value is None else {"temp_ranges": value}
    return LanesConfig(None, values)


def test_the_default_table_has_the_common_materials() -> None:
    assert ace2k_u1_config.read_temp_ranges(config(None)) == {
        "PLA": (190.0, 230.0),
        "PETG": (220.0, 260.0),
        "ABS": (230.0, 270.0),
        "ASA": (230.0, 270.0),
        "TPU": (210.0, 240.0),
    }


def test_a_table_is_read_as_one_word_in_upper_case() -> None:
    ranges = ace2k_u1_config.read_temp_ranges(config("pla silk:200-225, PA-CF:260-290"))
    assert ranges == {"PLASILK": (200.0, 225.0), "PA-CF": (260.0, 290.0)}
    assert ace2k_u1_config.read_temp_ranges(config("")) == {}  # no table: nothing warns


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("PLA", "'PLA' is not <material>:<low>-<high>"),
        ("PLA:230-190", "'PLA:230-190' is not <material>:<low>-<high>"),
        ("PLA:190", "'PLA:190' is not <material>:<low>-<high>"),
        (":190-230", "':190-230' is not <material>:<low>-<high>"),
        ("PLA:100-230", "'PLA:100-230' is not <material>:<low>-<high>"),
        ("PLA:190-400", "'PLA:190-400' is not <material>:<low>-<high>"),
        ("PLA:190-230, pla:200-220", "PLA is listed twice"),
    ],
)
def test_a_wrong_table_stops_at_the_config(value: str, why: str) -> None:
    with pytest.raises(ValueError) as err:
        ace2k_u1_config.read_temp_ranges(config(value))
    assert str(err.value).startswith(f"ace2k_u1: temp_ranges: {why}")


# --- M104 and M109 ----------------------------------------------------------------------------


def one_per_head(tmp_path: Path, **values: str) -> SimpleNamespace:
    rig, _ = tools_rig(tmp_path, lanes=ONE_PER_HEAD, values=values)
    for n in range(1, 5):
        material(rig, f"a:{n}", "PLA")
    return rig


@pytest.mark.parametrize("command", ["M104", "M109"])
def test_a_temperature_above_the_range_warns_and_still_heats(tmp_path: Path, command: str) -> None:
    rig = one_per_head(tmp_path)
    assert temp(rig, command, {"S": "260", "T": "1"}) == [{"S": "260", "T": "1"}]
    assert warnings(rig) == ["ace2k_u1: head 2: 260 °C is above PLA's 190–230 °C (lane a:2)"]


def test_a_printing_temperature_below_the_range_warns(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    material(rig, "a:3", "PETG")
    temp(rig, "M104", {"S": "200", "T": "2"})
    assert warnings(rig) == ["ace2k_u1: head 3: 200 °C is below PETG's 220–260 °C (lane a:3)"]


@pytest.mark.parametrize(
    "params",
    [
        {"S": "210", "T": "0"},  # within the range
        {"S": "0", "T": "0"},  # the heater off
        {"S": "140", "T": "0"},  # a standby or a warm-up, not a printing temperature
        {"S": "260", "T": "0", "A": "0"},  # a call of the U1 or of the adapter itself
    ],
)
def test_no_warning_otherwise(tmp_path: Path, params: dict[str, str]) -> None:
    rig = one_per_head(tmp_path)
    assert temp(rig, "M104", params) == [params]
    assert warnings(rig) == []


def test_a_material_not_in_the_table_or_not_known_does_not_warn(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    material(rig, "a:1", "PA")
    temp(rig, "M104", {"S": "300", "T": "0"})
    (tmp_path / "bare").mkdir()
    rig2, _ = tools_rig(tmp_path / "bare", lanes=ONE_PER_HEAD)  # no spool record at all
    temp(rig2, "M104", {"S": "300", "T": "0"})
    assert warnings(rig) == warnings(rig2) == []


def test_the_tool_goes_through_the_u1_table(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    material(rig, "a:4", "PETG")
    rig.task_config.map_table[0] = 3  # T0 prints on head 4
    temp(rig, "M104", {"S": "250", "T": "0"})
    assert warnings(rig) == []  # PETG at 250: no warning; PLA of head 1 is not looked at
    temp(rig, "M104", {"S": "270", "T": "0"})
    assert warnings(rig) == ["ace2k_u1: head 4: 270 °C is above PETG's 220–260 °C (lane a:4)"]


def test_no_warning_in_a_print(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    for state in ("printing", "paused"):
        rig.print_stats.state = state
        temp(rig, "M104", {"S": "260", "T": "0"})
    assert warnings(rig) == []


def test_the_table_of_the_config_is_used(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path, temp_ranges="PLA:190-270")
    temp(rig, "M104", {"S": "260", "T": "0"})
    assert warnings(rig) == []


def test_a_shared_head_warns_for_its_current_lane_only(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)  # head 1 is a:1+a:2
    material(rig, "a:1", "PLA")
    material(rig, "a:2", "PETG")
    temp(rig, "M104", {"S": "250", "T": "0"})
    assert warnings(rig) == []  # no lane known in the head yet
    rig.adapter.set_current(rig.adapter.slot_of_head(0), rig.adapter.find(1, rig.units["a"]))
    temp(rig, "M104", {"S": "270", "T": "0"})
    assert warnings(rig) == ["ace2k_u1: head 1: 270 °C is above PETG's 220–260 °C (lane a:2)"]


# --- the screen -------------------------------------------------------------------------------


def test_the_screen_temperature_warns_and_still_heats(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    # The screen names the head itself (A=0). The U1's own handler sets the heater.
    assert screen(rig, S=260, T=1, A=0) == [{"heated": (260, 1, 0)}]
    assert warnings(rig) == ["ace2k_u1: head 2: 260 °C is above PLA's 190–230 °C (lane a:2)"]


def test_the_screen_temperature_within_the_range_or_in_a_print_says_nothing(
    tmp_path: Path,
) -> None:
    rig = one_per_head(tmp_path)
    screen(rig, S=215, T=0, A=0)
    rig.print_stats.state = "printing"
    screen(rig, S=260, T=0, A=0)
    assert warnings(rig) == []


def test_a_screen_warning_that_fails_still_heats(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)

    def broken(*_args: object, **_kw: object) -> str:
        raise RuntimeError("unreadable")

    rig.module.spool_lanes.material = broken
    assert screen(rig, S=260, T=1, A=0) == [{"heated": (260, 1, 0)}]


# --- the print start --------------------------------------------------------------------------


def started(rig: SimpleNamespace, *args: str, **kw: str) -> list[object]:
    """A print start, then the reactor timer that checks its temperatures."""
    calls = print_start(rig, *args, **kw)
    assert warnings(rig) == []  # the file is read after the start command returned
    rig.reactor.run_timers()
    return calls


def hot_file(tmp_path: Path, temps: str, firsts: str | None = None) -> None:
    """The eight-colour file with other temperatures (a comma-separated list per tool)."""
    path = tmp_path / "gcodes" / "hot.gcode"
    text = (FIXTURES / "8_colours_no_tower.gcode").read_text()
    text = text.replace(
        "; nozzle_temperature = 220,220,220,220,220,220,220,220",
        f"; nozzle_temperature = {temps}",
    ).replace(
        "; nozzle_temperature_initial_layer = 220,220,220,220,220,220,220,220",
        f"; nozzle_temperature_initial_layer = {firsts or temps}",
    )
    path.write_text(text)


def test_a_start_warns_for_each_tool_whose_temperature_misses_its_lane(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    material(rig, "a:2", "PETG")
    hot_file(tmp_path, "220,250,220,265,220,220,220,220", "220,240,220,265,220,220,220,220")
    assert len(started(rig, name="hot.gcode")) == 1  # the start goes on
    assert warnings(rig) == [
        "ace2k_u1: head 4: 265 °C is above PLA's 190–230 °C (lane a:4, T3)",
    ]  # T1 at 250/240 fits PETG on a:2; the other tools are PLA at 220


def test_a_start_says_one_line_per_tool_and_temperature(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    hot_file(tmp_path, "220,220,220,220,250,220,220,220", "220,220,220,220,240,220,220,220")
    started(rig, "SDCARD_PRINT_FILE_WITH_PARAMETERS", name="hot.gcode")
    assert warnings(rig) == [  # T4 goes to head 1 in the default table of this command
        "ace2k_u1: head 1: 250 °C is above PLA's 190–230 °C (lane a:1, T4)",
        "ace2k_u1: head 1: 240 °C is above PLA's 190–230 °C (lane a:1, T4)",
    ]


def test_a_planned_start_reads_the_lane_that_the_plan_chose(tmp_path: Path) -> None:
    rig, _ = tools_rig(tmp_path)  # head 1 is a:1+a:2, and no lane is known in it
    hot_file(tmp_path, "220,220,250,220,220,220,220,220")
    set_plan(rig, "hot.gcode")  # T2 (white) is a:2
    started(rig, name="hot.gcode")
    assert warnings(rig) == ["ace2k_u1: head 1: 250 °C is above PLA's 190–230 °C (lane a:2, T2)"]


def test_a_start_of_a_file_from_another_slicer_says_nothing(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    (tmp_path / "gcodes" / "plain.gcode").write_text("G28\nM104 S260\n")
    assert len(started(rig, name="plain.gcode")) == 1
    assert warnings(rig) == []


def test_a_start_without_a_gcode_folder_is_not_checked_and_goes_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = one_per_head(tmp_path)
    hot_file(tmp_path, "265,220,220,220,220,220,220,220")
    rig.printer.objects["virtual_sdcard"].sdcard_dirname = None
    with caplog.at_level("INFO"):
        assert len(started(rig, name="hot.gcode")) == 1  # the start goes on, with no error
    assert warnings(rig) == []
    assert "temperatures of the start not checked" in caplog.text


def test_a_start_with_a_print_on_is_not_checked(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    hot_file(tmp_path, "265,220,220,220,220,220,220,220")
    rig.print_stats.state = "printing"
    started(rig, name="hot.gcode")
    assert warnings(rig) == []


def test_a_refused_start_says_nothing(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    hot_file(tmp_path, "265,220,220,220,220,220,220,220")

    def refuse(_gcmd: object) -> None:
        raise ValueError("SD busy")

    rig.gcode.commands_orig["SDCARD_PRINT_FILE"] = refuse
    with pytest.raises(ValueError, match="SD busy"):
        rig.gcode.commands["SDCARD_PRINT_FILE"](
            Gcmd({"FILENAME": "hot.gcode"}, "SDCARD_PRINT_FILE")
        )
    assert warnings(rig) == []


def test_a_t_of_the_file_without_a_tool_does_not_hide_the_others(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    hot_file(tmp_path, "265,220")  # two tools in the config block, eight T numbers in the file
    started(rig, name="hot.gcode")
    assert warnings(rig) == ["ace2k_u1: head 1: 265 °C is above PLA's 190–230 °C (lane a:1, T0)"]


# --- the U1's own calls, Fluidd, the subtypes --------------------------------------------------


def test_no_warning_while_a_flow_of_the_u1_runs(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    rig.left.channel_active = 0  # the U1's unload heats for the head's setting: M109 S250
    temp(rig, "M109", {"S": "250"})
    temp(rig, "M104", {"S": "260", "T": "1"})
    assert warnings(rig) == []


@pytest.mark.parametrize("params", [{"S": "260", "T": "-1"}, {"S": "260", "T": "40"}])
def test_a_call_that_the_u1_refuses_does_not_warn(tmp_path: Path, params: dict[str, str]) -> None:
    rig = one_per_head(tmp_path)
    temp(rig, "M104", params)
    assert warnings(rig) == []


def test_the_active_head_is_the_one_of_the_active_extruder(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    rig.printer.objects["toolhead"] = fakes.FakeToolhead("extruder2")
    temp(rig, "M104", {"S": "260"})
    assert warnings(rig) == ["ace2k_u1: head 3: 260 °C is above PLA's 190–230 °C (lane a:3)"]


def test_fluidd_set_heater_temperature_warns_and_still_heats(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path)
    params = {"HEATER": "extruder1", "TARGET": "260"}
    assert temp(rig, "SET_HEATER_TEMPERATURE", params) == [params]
    temp(rig, "SET_HEATER_TEMPERATURE", {"HEATER": "heater_bed", "TARGET": "260"})
    assert warnings(rig) == ["ace2k_u1: head 2: 260 °C is above PLA's 190–230 °C (lane a:2)"]


def test_a_subtype_takes_the_range_of_its_base_material(tmp_path: Path) -> None:
    rig = one_per_head(tmp_path, temp_ranges="PLA:190-230, PLA SILK:200-240")
    material(rig, "a:1", "PLA Matte")
    material(rig, "a:2", "PLA Silk")
    temp(rig, "M104", {"S": "235", "T": "0"})
    temp(rig, "M104", {"S": "235", "T": "1"})
    assert warnings(rig) == ["ace2k_u1: head 1: 235 °C is above PLA's 190–230 °C (lane a:1)"]
