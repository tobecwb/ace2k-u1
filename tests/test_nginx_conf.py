"""The page's nginx snippet (web/nginx/ace2k.conf), as install.sh copies it unchanged.

Its page location sends two headers. Cache-Control "no-cache" makes the browser revalidate every
file, so an update never mixes new and old modules. The Content-Security-Policy allows scripts
from the page's own origin only, so the page must carry no inline script. Its websocket may go
to the page's own origin only: 'self' covers ws: and wss: on the page's host and port. nginx
drops the server block's add_header lines inside a location that has its own, so the headers
stay inside the page's location and never touch the printer's other pages.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
CONF = WEB / "nginx" / "ace2k.conf"
IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.S)


def _import_map_hash() -> str:
    """The CSP source for the page's one inline script, the import map: the sha256 of the exact
    text between its tags."""
    # Bytes, not text: the browser hashes the file's own bytes, line endings included.
    text = IMPORT_MAP.search((WEB / "index.html").read_bytes().decode())
    assert text, "index.html has no import map"
    digest = hashlib.sha256(text.group(1).encode()).digest()
    return "'sha256-" + base64.b64encode(digest).decode() + "'"


CSP = (
    "default-src 'self'; script-src 'self' " + _import_map_hash() + "; connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:"
)


def _blocks(text: str) -> list[tuple[str, str]]:
    """[(head, body)] for the blocks at the top of text, braces matched by depth, so a nested
    block stays inside its parent's body."""
    blocks, depth, head_at, body_at = [], 0, 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                body_at = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            assert depth >= 0, "unbalanced braces"
            if depth == 0:
                head = text[head_at : body_at - 1].rsplit(";", 1)[-1].strip()
                blocks.append((head, text[body_at:i]))
                head_at = i + 1
    assert depth == 0, "unbalanced braces"
    return blocks


def _strip_nested(body: str) -> str:
    """body without its nested blocks: the directives that apply to the block itself."""
    out, depth = [], 0
    for ch in body:
        depth += ch == "{"
        if depth == 0:
            out.append(ch)
        elif ch == "}" and depth == 1:
            out.append(";")  # the nested block ends like a directive: its head is not the next's
        depth -= ch == "}"
    return "".join(out)


def _locations(text: str) -> dict[str, str]:
    """{location argument: its body} for the top-level location blocks of a snippet."""
    text = re.sub(r"#.*", "", text)
    return {
        head.removeprefix("location").strip(): body
        for head, body in _blocks(text)
        if head.startswith("location")
    }


def _headers(body: str) -> list[str]:
    """The block's own add_header lines (a nested block's are not the block's)."""
    # one directive per ";", a ";" inside quotes (the policy's) kept in its directive
    directives = re.findall(r'(?:[^;"]|"[^"]*")+;', _strip_nested(body))
    return [" ".join(d.split()) for d in directives if d.split()[0] == "add_header"]


def test_the_page_location_revalidates_and_sends_the_policy() -> None:
    assert _headers(_locations(CONF.read_text())["/ace2k/"]) == [
        'add_header Cache-Control "no-cache" always;',
        f'add_header Content-Security-Policy "{CSP}" always;',
    ]


def test_no_header_outside_the_page_location() -> None:
    text = re.sub(r"#.*", "", CONF.read_text())
    assert text.count("add_header") == 2
    assert _headers(_locations(CONF.read_text())["= /ace2k"]) == []


def test_the_page_has_no_inline_script_for_the_policy_to_block() -> None:
    html = (WEB / "index.html").read_text()
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
    inline = [attrs for attrs, body in scripts if "src=" not in attrs or body.strip()]
    # the one inline script is the import map, which the policy allows by its hash
    assert scripts and inline == [' type="importmap"']
    assert all(not body.strip() for attrs, body in scripts if "src=" in attrs)
    assert not re.search(r"\son[a-z]+\s*=|javascript:", html)


def test_a_nested_block_does_not_cut_the_location_short() -> None:
    # read up to the first "}", this location's own header after the nested block was lost,
    # and the nested block's header counted as the location's
    text = """
    location = /x { return 301 /x/; }
    location /x/ {
        location ~ \\.js$ { add_header X-Nested "1"; }
        add_header Cache-Control "no-cache" always;
    }
    """
    found = _locations(text)
    assert list(found) == ["= /x", "/x/"]
    assert _headers(found["/x/"]) == ['add_header Cache-Control "no-cache" always;']
    assert _headers(found["= /x"]) == []
