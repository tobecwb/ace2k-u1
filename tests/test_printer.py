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


# --- the tangle check of a head ----------------------------------------------------------------


class TangleCheck:
    """filament_entangle_detect of the U1: skip_entangle_check takes a new start (the head's
    position and wheel counts, which raise when fail is set) and sets its flag last, as the U1's
    does. skip_length and skip_length_remained are its start-of-print grace."""

    def __init__(self, flag: bool = False) -> None:
        self.skip_check_flag = flag
        self.calls: list[bool] = []
        self.fail: set[bool] = set()
        self.skip_length = 20.0
        self.skip_length_remained = -1.0  # the grace was used up

    def skip_entangle_check(self, skip: bool = False) -> None:
        self.calls.append(skip)
        if skip in self.fail:
            raise RuntimeError("no wheel")
        self.skip_check_flag = skip


def checked(check: TangleCheck) -> ace2k_u1_printer.U1Printer:
    return facade(**{"filament_entangle_detect e1_filament": check})


def test_the_tangle_check_of_the_head_is_off_in_the_block_and_on_after_it() -> None:
    check = TangleCheck()
    other = TangleCheck()
    u1 = facade(
        **{
            "filament_entangle_detect e1_filament": check,
            "filament_entangle_detect e0_filament": other,
        }
    )
    with u1.tangle_check_off(1):
        assert check.skip_check_flag is True
    assert check.calls == [True, False] and check.skip_check_flag is False
    assert other.calls == []
    assert "skip_entangle_check" not in vars(check)  # the U1's own method is back


def test_the_tangle_check_is_on_again_when_the_block_raises() -> None:
    check = TangleCheck()
    with pytest.raises(ValueError, match="refused"), checked(check).tangle_check_off(1):
        raise ValueError("refused")
    assert check.calls == [True, False] and check.skip_check_flag is False
    assert "skip_entangle_check" not in vars(check)


def test_the_u1_turning_the_check_on_inside_the_block_waits_for_its_end() -> None:
    # the U1's load or unload of the same head, run while the block's parked moves are queued
    check = TangleCheck()
    with checked(check).tangle_check_off(1):
        check.skip_entangle_check(True)  # as filament_feed calls it
        check.skip_entangle_check(False)
        assert check.skip_check_flag is True  # the parked moves still run
    assert check.calls == [True, True, False] and check.skip_check_flag is False


def test_a_block_inside_the_u1s_own_skip_turns_the_check_on_once_both_have_ended() -> None:
    check = TangleCheck(flag=True)  # the U1's own load or unload turned it off before the block
    with checked(check).tangle_check_off(1):
        check.skip_entangle_check(False)  # the U1's ends inside the block
        assert check.skip_check_flag is True
    assert check.calls == [False] and check.skip_check_flag is False


def test_a_u1_skip_that_outlasts_the_block_is_left_to_the_u1() -> None:
    check = TangleCheck()
    with checked(check).tangle_check_off(1):
        check.skip_entangle_check(True)  # the U1's load starts inside the block
    assert check.calls == [True, True] and check.skip_check_flag is True
    check.skip_entangle_check(False)  # and turns it on itself, with its own method
    assert check.calls == [True, True, False] and check.skip_check_flag is False


def test_a_tangle_check_that_was_off_is_left_off() -> None:
    check = TangleCheck(flag=True)  # the U1's own load or unload of the head turned it off
    with checked(check).tangle_check_off(1):
        pass
    assert check.calls == [] and check.skip_check_flag is True
    assert "skip_entangle_check" not in vars(check)


def test_nested_blocks_turn_the_check_on_once_the_outer_one_ends() -> None:
    check = TangleCheck()
    u1 = checked(check)
    with u1.tangle_check_off(1):
        with u1.tangle_check_off(1):
            pass
        assert check.skip_check_flag is True
    assert check.calls == [True, False] and check.skip_check_flag is False


def test_a_tangle_check_that_does_not_turn_off_raises_and_the_block_does_not_run() -> None:
    check = TangleCheck()
    check.fail = {True}
    ran = []
    with (
        pytest.raises(ace2k_u1_printer.TangleOffError, match="head 2.*turned off"),
        checked(check).tangle_check_off(1),
    ):
        ran.append(True)
    assert ran == [] and check.calls == [True] and check.skip_check_flag is False
    assert "skip_entangle_check" not in vars(check)


def test_a_tangle_check_that_does_not_turn_on_restarts_with_the_u1s_grace(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # its start is stale: the grace (the U1's own at the start of a print) takes a new one
    check = TangleCheck()
    check.fail = {False}
    with caplog.at_level("ERROR"), checked(check).tangle_check_off(1):
        pass
    assert check.skip_check_flag is False
    assert check.skip_length_remained == check.skip_length
    assert any("grace" in r.getMessage() for r in caplog.records)


def test_a_tangle_check_with_no_grace_that_does_not_turn_on_is_on_all_the_same(
    caplog: pytest.LogCaptureFixture,
) -> None:
    check = TangleCheck()
    check.fail = {False}
    del check.skip_length
    with caplog.at_level("ERROR"), checked(check).tangle_check_off(1):
        pass
    assert check.skip_check_flag is False  # never left off: a later hold reads it as the U1's
    assert any("may be stale" in r.getMessage() for r in caplog.records)


def test_a_method_set_on_the_check_itself_is_given_back() -> None:
    check = TangleCheck()
    calls: list[bool] = []

    def own(skip: bool = False) -> None:
        calls.append(skip)
        check.skip_check_flag = skip

    check.skip_entangle_check = own  # type: ignore[method-assign]
    with checked(check).tangle_check_off(1):
        pass
    assert check.skip_entangle_check is own
    assert calls == [True, False]


def test_with_no_tangle_check_the_block_runs(caplog: pytest.LogCaptureFixture) -> None:
    ran = []
    with caplog.at_level("DEBUG"), facade().tangle_check_off(2):
        ran.append(True)
    assert ran == [True]
    assert [r.getMessage() for r in caplog.records] == [
        "ace2k_u1: no filament_entangle_detect e2_filament: the parked moves of head 3 run with "
        "no check"
    ]
