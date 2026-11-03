"""ace2k_u1_printer: the U1Printer reads of the U1's state that fail or find the object absent."""

from __future__ import annotations

import types

import ace2k_u1_printer
import pytest

LOAD_TEMP = 215
E_POS = -12  # an int: read as a float


class Printer:
    def __init__(self, **objects: object) -> None:
        self.objects = objects

    def lookup_object(self, name: str, default: object = None) -> object:
        if name in self.objects:
            return self.objects[name]
        if default is None and name == "toolhead":
            raise KeyError(name)
        return default


class Broken:
    def get_status(self, _eventtime: float) -> dict[str, object]:
        raise RuntimeError("unreadable")


def facade(**objects: object) -> ace2k_u1_printer.U1Printer:
    printer = Printer(**objects)
    reactor = types.SimpleNamespace(monotonic=lambda: 1.0)
    return ace2k_u1_printer.U1Printer(lambda: printer, lambda: reactor)


def test_an_unreadable_layer_is_none() -> None:
    assert facade(print_stats=Broken()).current_layer() is None
    assert facade().current_layer() is None


def test_unreadable_homed_axes_read_as_all_homed() -> None:
    assert facade(toolhead=Broken()).homed_axes() == "xyz"
    assert facade().homed_axes() == "xyz"


def test_a_missing_or_unreadable_head_setting_is_none() -> None:
    assert facade().head_setting(0) is None
    assert facade(print_task_config=Broken()).head_setting(0) is None


def test_the_head_setting_is_the_stored_type_and_colour() -> None:
    ptc = types.SimpleNamespace(
        get_status=lambda _t: {"filament_type": [" PLA "], "filament_color_rgba": ["FF0000FF"]}
    )
    assert facade(print_task_config=ptc).head_setting(0) == ("PLA", "FF0000FF")


def test_the_filament_record_is_the_task_configs() -> None:
    ptc = types.SimpleNamespace(get_status=lambda _t: {"filament_exist": [True, False]})
    assert facade(print_task_config=ptc).filament_exist() == [True, False]


def test_a_filament_table_that_cannot_be_asked_answers_none() -> None:
    ptc = types.SimpleNamespace(get_status=lambda _t: {})
    fparams = types.SimpleNamespace(get_is_soft=lambda *_a: True)
    extruder = types.SimpleNamespace(nozzle_diameter=0.4)
    u1 = facade(print_task_config=ptc, filament_parameters=fparams)
    assert u1.u1_filament(extruder, 0, "get_is_soft") is None
    assert u1.soft(extruder, 0) == 0
    assert u1.u1_filament(None, 0, "get_is_soft") is None


def test_an_extruder_that_does_not_say_its_nozzle_cannot_be_asked_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    status = {"filament_vendor": ["v"], "filament_type": ["PLA"], "filament_sub_type": ["s"]}
    ptc = types.SimpleNamespace(get_status=lambda _t: status)
    fparams = types.SimpleNamespace(get_is_soft=lambda *_a: True)
    u1 = facade(print_task_config=ptc, filament_parameters=fparams)
    assert u1.u1_filament(types.SimpleNamespace(nozzle_diameter=0.4), 0, "get_is_soft") is True
    assert u1.u1_filament(types.SimpleNamespace(), 0, "get_is_soft") is None
    assert "filament_parameters get_is_soft" in caplog.text


def test_the_filament_tables_are_the_status_and_the_dicts_the_object_holds() -> None:
    class Fparams:
        def __init__(self) -> None:
            self._config_a = {"v_PLA_s_k": 1}
            self.other = "not a table"

        def get_status(self, _t: float) -> dict[str, int]:
            return {"v_PETG_s_k": 2}

    assert ace2k_u1_printer.U1Printer.filament_tables(Fparams()) == [
        {"v_PETG_s_k": 2},
        {"v_PLA_s_k": 1},
    ]


def test_filament_tables_without_a_readable_status_are_the_held_dicts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fparams = types.SimpleNamespace(get_status=Broken().get_status, _config_a={"k": 1})
    assert ace2k_u1_printer.U1Printer.filament_tables(fparams) == [{"k": 1}]
    assert "filament_parameters status" in caplog.text


def test_no_filament_table_or_no_spool_type_has_no_load_temperature() -> None:
    extruder = types.SimpleNamespace()
    assert facade().load_temp(("v", "PLA", "s", "c"), extruder, 0.4) is None
    u1 = facade(filament_parameters=types.SimpleNamespace())
    assert u1.load_temp(None, extruder, 0.4) is None
    assert u1.load_temp(("v", None, "s", "c"), extruder, 0.4) is None


def test_the_load_temperature_is_asked_with_the_default_nozzle_when_the_extruder_has_none() -> None:
    asked = []
    fparams = types.SimpleNamespace(get_load_temp=lambda *a: asked.append(a) or LOAD_TEMP)
    value = facade(filament_parameters=fparams).load_temp(
        ("v", "PLA", "s", "c"), types.SimpleNamespace(), 0.4
    )
    assert value == LOAD_TEMP and asked == [("v", "PLA", "s", 0.4, "standard")]


def test_no_extruder_name_is_no_extruder() -> None:
    assert facade().extruder(None) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, "1.0", None])
def test_a_printing_e_pos_that_is_no_finite_number_is_unread(value: object) -> None:
    """NaN (and infinity) is no position: None, as an unreadable one."""
    assert ace2k_u1_printer.U1Printer.e_pos(types.SimpleNamespace(printing_e_pos=value)) is None


def test_a_finite_printing_e_pos_is_read_as_a_float() -> None:
    extruder = types.SimpleNamespace(printing_e_pos=E_POS)
    assert ace2k_u1_printer.U1Printer.e_pos(extruder) == float(E_POS)
