"""No document, configuration, web page or module string of this tree still sends an ACE_ command.

Every G-code is ACEK_* (up to v0.12.0-rc.1 they were ACE_*): the ACE_* names stay free for other
ACE modules. An old name may appear only in the migration form, beside its own ACEK_ twin
(`ACE_EJECT` → `ACEK_EJECT`, "is now", "(now ...)"); a line that merely holds an ACEK_ name is no
exemption. CHANGELOG.md records what was true at each release and is not checked.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]
# retired macro names of the burn-in, named only to say they are to be deleted
RETIRED = {"ACE_TO_HEAD", "ACE_FROM_HEAD"}
PY_DIRS = ("klippy/extras",)
PY_EXEMPT = set()
C_DIRS = ()
MUST_SCAN = "model.js"  # the page's G-code strings


def sources() -> Iterator[Path]:
    yield ROOT / "README.md"
    yield from sorted((ROOT / "docs").glob("*.md"))
    yield from sorted((ROOT / "config").glob("*.cfg"))
    web = ROOT / "web"
    for pattern in ("*.js", "*.html", "*.json", "js/**/*.js", "i18n/*.json"):
        yield from sorted(web.glob(pattern))


# --- begin shared part: identical in the ace2k and ace2k-u1 copies of this file ---
OLD = re.compile(r"(?<![A-Za-z0-9_])ACE_[A-Z][A-Z_]*")
# the one form an old name may take: beside its ACEK_ twin, as "`ACE_X` → `ACEK_X`",
# "`ACE_X` is now `ACEK_X`" or "`ACE_X` (now `ACEK_X`)"
MIGRATION = re.compile(r"`?\bACE_([A-Z_]+)`?\s*(?:→|->|is now|\(now)\s*`?ACEK_\1\b")


def old_in_text(text: str) -> set[str]:
    """The old names in text once its migration examples are taken out."""
    return set(OLD.findall(MIGRATION.sub("", text))) - RETIRED


def old_names(path: Path, root: Path = ROOT) -> list[str]:
    found = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        names = old_in_text(line)
        if names:
            found.append(f"{path.relative_to(root)}:{n}: {', '.join(sorted(names))}")
    return found


def old_in_python(source: str, label: str | Path) -> list[str]:
    """The old names in a module's string constants (docstrings and f-string parts included),
    the constants bound to a name in PY_EXEMPT left out."""
    tree = ast.parse(source)
    exempt = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in PY_EXEMPT for t in node.targets
        ):
            exempt.add(id(node.value))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in exempt:
                continue
            names = set().union(*(old_in_text(line) for line in node.value.splitlines() or [""]))
            if names:
                found.append(f"{label}:{node.lineno}: {', '.join(sorted(names))}")
    return found


def python_sources() -> Iterator[Path]:
    for d in PY_DIRS:
        yield from sorted((ROOT / d).rglob("*.py"))


def c_sources() -> Iterator[Path]:
    for d in C_DIRS:
        for pattern in ("*.c", "*.h"):
            yield from sorted((ROOT / d).rglob(pattern))


def test_no_document_or_page_sends_an_old_command() -> None:
    files = list(sources())
    assert any(p.name == MUST_SCAN for p in files)
    found = [hit for path in files for hit in old_names(path)]
    assert found == [], "rename to ACEK_: " + "; ".join(found)


def test_no_module_string_names_an_old_command() -> None:
    files = list(python_sources())
    assert files
    found = [
        hit
        for path in files
        for hit in old_in_python(path.read_text(encoding="utf-8"), path.relative_to(ROOT))
    ]
    assert found == [], "rename to ACEK_: " + "; ".join(found)


def test_no_c_source_names_an_old_command() -> None:
    # comments and string literals alike, read as plain text
    files = list(c_sources())
    assert files or not C_DIRS
    found = [hit for path in files for hit in old_names(path)]
    assert found == [], "rename to ACEK_: " + "; ".join(found)


def test_the_check_catches_an_old_command(tmp_path: Path) -> None:
    doc = tmp_path / "x.md"
    doc.write_text(
        "run `ACEK_STOP`, then `ACE_CLEAR`\n"
        "`ACE_FAN` → `ACEK_FAN`\n"
        "`ACE_DRY` is now `ACEK_DRY`, `ACE_STOP` (now `ACEK_STOP`)\n"
        "`ACE_FEED` → `ACEK_DRY`\n",
        encoding="utf-8",
    )
    assert old_names(doc, tmp_path) == ["x.md:1: ACE_CLEAR", "x.md:4: ACE_FEED"]
    src = tmp_path / "x.c"
    src.write_text('// the host checks ACE_SNAG_SET\nputs("`ACE_FAN` → `ACEK_FAN`");\n')
    assert old_names(src, tmp_path) == ["x.c:1: ACE_SNAG_SET"]


def test_the_check_catches_an_old_command_in_a_module() -> None:
    source = 'READ_SCRIPT = "ACE_RFID_READ LANE=%d MOVE=0 WAIT=0"\n'
    assert old_in_python(source, "m.py") == ["m.py:1: ACE_RFID_READ"]
    assert old_in_python('def f():\n    """`ACE_FAN` → `ACEK_FAN`"""\n', "m.py") == []


# --- end shared part ---
