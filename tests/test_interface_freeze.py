"""The interface the adapter keeps for its documents, its installer and its web page.

tests/fixtures/interface-freeze.json records what they depend on: the G-codes (name -> help text)
the adapter registers or wraps, the status keys and shapes, the pause reasons, EJECT_SAY, the
grip's refusal lines, and the console texts the installer and the docs quote. The data is rebuilt
here from live objects and compared with the fixture; each text is also looked up in the string
literals of klippy/extras/ace2k_u1*.py (comments and docstrings do not count; an f-string counts
both by its literal parts and whole, each of its fields written {}), so a literal moved between
modules passes and one changed fails. The fixture is edited by hand: a change to the interface is
a deliberate edit of both. (The history line format is pinned by
web/tests/fixtures/event-lines.json and is not repeated here.)
"""

from __future__ import annotations

import ast
import importlib
import json
import types
from pathlib import Path
from typing import Any
from unittest import mock

import fakes
import pytest
import test_check
import test_eject
import test_groups
import test_push
import test_standing
import test_tools

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "interface-freeze.json").read_text())
SOURCES = sorted(ROOT.glob("klippy/extras/ace2k_u1*.py"))
MODULES = [importlib.import_module(p.stem) for p in SOURCES]

# the commands the U1 owns that the adapter may wrap, registered as stand-ins by the rig
STUBBED = {
    "ACEK_CLEAR",
    "ACEK_FEED",
    "ACEK_LANE_ACTIVE",
    "M104",
    "M109",
    "RESUME",
    "SDCARD_PRINT_FILE",
    "SDCARD_PRINT_FILE_WITH_PARAMETERS",
    "SET_HEATER_TEMPERATURE",
    "SM_PRINT_END_AUTO_UNLOAD_FILAMENT",
    "SM_PRINT_PREEXTRUDE_FILAMENT",
    *(f"T{n}" for n in range(32)),
}


def frozen(name: str) -> object:
    """The module-level name, from whichever ace2k_u1* module defines it."""
    for module in MODULES:
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)


def template(node: ast.JoinedStr) -> str:
    """An f-string's text with each of its fields written {}."""
    return "".join(
        part.value if isinstance(part, ast.Constant) else "{}"
        for part in node.values
        if isinstance(part, (ast.Constant, ast.FormattedValue))
    )


def literals() -> set[str]:
    """Every string literal of the modules (the literal parts of f-strings included, and each
    f-string whole: template), docstrings left out."""
    found: set[str] = set()
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        found.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
        )
        found.update(template(node) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr))
    return found


def keys(value: dict[str, Any]) -> list[str]:
    return sorted(value)


def own_commands(gcode: test_check.Gcode) -> dict[str, str | None]:
    # ACEK_CLEAR is ace2k's own, registered by the fake before the adapter hooks
    return {n: gcode.gcode_help.get(n) for n in sorted(gcode.commands) if n != "ACEK_CLEAR"}


def wrapped_commands() -> tuple[list[str], dict[str, tuple[str | None, str | None]]]:
    """The U1 commands the adapter wraps in a shared-head rig, and (original help, help now)."""
    originals: dict[str, Any] = {}
    real_init = test_check.Gcode.__init__

    def init(self: test_check.Gcode) -> None:
        real_init(self)
        self.stub("ACEK_LANE_ACTIVE")

    def stub(self: test_check.Gcode, name: str) -> None:
        def handler(gcmd: object) -> None:
            pass

        originals[name] = handler
        self.register_command(name, handler, desc=f"help of {name}")

    with (
        mock.patch.object(test_check.Gcode, "__init__", init),
        mock.patch.object(test_check.Gcode, "stub", stub),
    ):
        rig = test_groups.shared_head_rig()[0]
    wrapped = sorted(
        n for n, h in rig.gcode.commands.items() if n in originals and h is not originals[n]
    )
    helps = {n: (f"help of {n}", rig.gcode.gcode_help.get(n)) for n in wrapped}
    return wrapped, helps


def shapes(tmp_path: Path) -> dict[str, Any]:
    """The status shapes the web page reads, from a planned rig with a spool, a setting_for and
    a blocked push."""
    rig, _ = test_tools.tools_rig(tmp_path)
    test_tools.spools(rig, test_tools.EIGHT)
    test_tools.run(rig, "ACEK_PLAN", {"FILE": "eight.gcode"})
    unplanned = rig.module.get_status()["plan"]
    full = {f"T{n}": t["lane"] for n, t in enumerate(rig.module.tracker.plan.tools)}
    test_tools.run(rig, "ACEK_PLAN_SET", dict(full, FILE="eight.gcode"))
    a1 = rig.adapter.hooked[0]
    rig.adapter.slot_of(a1).setting_for = a1
    a3 = next(h for h in rig.adapter.hooked if rig.module.spool_store.key(h.unit, h.lane) == "a:3")
    a3.push_end, a3.push_end_seq = "blocked", 3
    status = rig.module.get_status()
    plan = status["plan"]
    return {
        "spool_record": keys(status["spools"]["a:1"]),
        "plan": keys(plan),
        "plan_not_set": keys(unplanned),
        "plan_tool": keys(plan["tools"][0]),
        "plan_lane": keys(plan["lanes"][0]),
        "setting_for_entry": keys(next(e for e in status["setting_for"] if e)),
        "lanes_entry_push_end": keys(status["lanes"]["a:3"]),
        "push_end": status["lanes"]["a:3"]["push_end"],
    }


def grip_refusals() -> dict[str, list[str]]:
    """The console line of a grip the unit refuses, set and cleared."""
    adapter, _, _, ace, _, h = test_standing.setup()
    ace.feed.grip_fail = RuntimeError("the link is down")
    said = {}
    for what, holds in (("set", True), ("cleared", False)):
        h.grip_refused = None
        h.grip_wanted = h.grip_live = what == "cleared"
        adapter.gcode.lines.clear()
        adapter.grip_watch.sync_grip(h, holds=holds)
        said[what] = list(adapter.gcode.lines)
    return said


def pause_reasons(tmp_path: Path) -> dict[str, Any]:
    """The reasons the adapter gives a paused print, driven as the pause tests drive them."""
    adapter, _, left, _, ace = test_push.build()
    adapter.hook_all()
    adapter.print_stats = fakes.FakePrintStats("printing")
    ace.feed.refuse = "no_filament"
    with pytest.raises(RuntimeError):
        left._put_into_drive(1)
    push = adapter.pause_reason

    rig, a = test_tools.swap_rig(tmp_path)

    def on_start(move: types.SimpleNamespace) -> None:
        if move.mode == "rollback":
            a.lanes[0]["pushed"] = True
        else:
            move.event = {"kind": "done", "seq": move.seq}

    a.feed.on_start = on_start
    test_tools.tool(rig, 2)
    return {"load_failure": push, "swap_failure": rig.module.get_status()["pause_reason"]}


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    u1, reactor, _, _, gcode = test_eject.hooked_u1()
    unhooked = u1.get_status()
    u1._hook(reactor.now)
    status = u1.get_status()
    wrapped, helps = wrapped_commands()
    assert all(original == now for original, now in helps.values())  # _wrap keeps the help
    return {
        "gcodes": own_commands(gcode),
        "wrapped_u1_commands": wrapped,
        "wrapped_u1_helps": {n: now for n, (_, now) in helps.items()},
        "status_keys": {
            "not_hooked": keys(unhooked),
            "hooked": keys(status),
            "heads_entry": keys(status["heads"][0][0]),
            "current_entry": keys(status["current"][0]),
            "lanes_entry": keys(next(iter(status["lanes"].values()))),
        },
        "status_shapes": shapes(tmp_path_factory.mktemp("shapes")),
        "pause_reason": {"not_hooked": unhooked["pause_reason"], "hooked": status["pause_reason"]},
        "pause_reasons": pause_reasons(tmp_path_factory.mktemp("pause")),
        "eject_say": dict(frozen("EJECT_SAY")),
        "grip_refusals": grip_refusals(),
    }


@pytest.mark.parametrize(
    "part",
    [
        "gcodes",
        "wrapped_u1_commands",
        "wrapped_u1_helps",
        "status_keys",
        "status_shapes",
        "pause_reason",
        "pause_reasons",
        "eject_say",
        "grip_refusals",
    ],
)
def test_the_interface_is_the_fixtures(live: dict[str, Any], part: str) -> None:
    assert live[part] == FIXTURE[part]


def test_the_tool_commands_wrapped_are_all_the_tools() -> None:
    tools = [f"T{n}" for n in range(int(str(frozen("TOOLS"))))]
    assert set(tools) <= set(FIXTURE["wrapped_u1_commands"])
    assert tools


def test_the_texts_the_installer_and_the_docs_quote_are_in_the_source() -> None:
    texts = FIXTURE["source_texts"]
    assert texts
    strings = literals()
    missing = [t for t in texts if not any(t in s for s in strings)]
    assert missing == []


def test_the_pause_reason_prefix_the_page_strips_is_in_the_source() -> None:
    # web/js/views/topbar.js strips /^ace2k_u1: / from the reason it shows
    prefix = FIXTURE["pause_reason_prefix"]
    assert prefix == "ace2k_u1: "
    assert all(r.startswith(prefix) for r in FIXTURE["pause_reasons"].values())
