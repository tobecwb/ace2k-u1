"""The adapter's refusals of ACEK_PLAN_SET that the web page reads.

web/tests/fixtures/adapter-refusals.json holds each refusal line exactly as the adapter raises it,
and whether the page plans the file again on it. The page's tests feed the same lines to its
reading of a refusal, so a change of the text on either side fails a test.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from test_tools import EIGHT, FIXTURES, run, spools, tools_rig

if TYPE_CHECKING:
    from types import SimpleNamespace

WEB_FIXTURES = Path(__file__).resolve().parents[1] / "web" / "tests" / "fixtures"
REFUSALS = WEB_FIXTURES / "adapter-refusals.json"


def planned(tmp_path: Path) -> tuple[SimpleNamespace, dict[str, str]]:
    """Returns a rig with eight.gcode planned, and a full choice of its suggested lanes."""
    rig, _ = tools_rig(tmp_path)
    spools(rig, EIGHT)
    run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    tools = rig.module.get_status()["plan"]["tools"]
    return rig, {f"T{n}": t["lane"] for n, t in enumerate(tools)}


def refusal(case: str, file: str, tmp_path: Path) -> str:
    """Returns the line that ACEK_PLAN_SET raises for one case of the fixture.

    file is the fixture's file of the case. A case that this test does not know fails.
    """
    rig, full = planned(tmp_path)
    if case == "changed":
        os.utime(tmp_path / "gcodes" / file, (1, 1))
        params = dict(full, FILE=file)
    elif case == "no_lane":
        params = {"FILE": file, "T0": full["T0"]}
    elif case == "no_plan":
        shutil.copy(FIXTURES / "8_colours_no_tower.gcode", tmp_path / "gcodes" / file)
        params = dict(full, FILE=file)
    else:
        pytest.fail(f"no way to produce the refusal case {case!r}")
    with pytest.raises(ValueError) as err:
        run(rig, "ACEK_PLAN_SET", params)
    return str(err.value)


CASES = json.loads(REFUSALS.read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_each_refusal_line_is_the_adapters_own(tmp_path: Path, case: dict[str, object]) -> None:
    assert refusal(str(case["case"]), str(case["file"]), tmp_path) == case["line"]


def test_an_unknown_case_fails(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="'new'"):
        refusal("new", "eight.gcode", tmp_path)


def test_the_fixture_has_the_refusal_the_page_plans_again_on() -> None:
    assert [c["case"] for c in CASES if c["replan"]] == ["changed"]
