"""A fake Snapmaker U1 below a temporary directory, for install.sh.

The root holds the U1's paths (paxx's BUILD_VERSION, /oem/.debug, Klipper's extras, printer_data,
nginx's fluidd.d, /dev/serial/by-id), stub programs that log every call (S60klipper, nginx, the
flasher), and one local HTTP server that plays Moonraker and GitHub from JSON files under
<root>/fake.  install.sh reaches all of it through its ACE2K_* variables.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import Any

    # a release's files, {name: bytes or str}
    Files = dict[str, bytes | str]
    # a JSON object, as Moonraker's fake keeps its state and steps
    Json = dict[str, Any]

    class Ran(subprocess.CompletedProcess[str]):
        """A finished run of install.sh, as FakeU1.run and FakeU1.source return it."""

        out: str  # stdout + stderr, set by FakeU1


TREE = Path(__file__).resolve().parents[2]
INSTALL_SH = TREE / "install.sh"
# the page's nginx snippet as the release ships it; install.sh copies it unchanged
NGINX_CONF = TREE / "web" / "nginx" / "ace2k.conf"
CFG_TOOL = TREE / "tools" / "ace2k_cfg.py"
# ace2k's tree, as tests/conftest.py finds it: its host bundle is what install.sh unpacks
ACE2K_TREE = Path(
    os.environ.get("ACE2K_EXTRAS") or TREE.parent / "ace2k" / "klippy" / "extras"
).parents[1]
ACE2K_V = "0.13.0"
U1_V = "0.4.0"
PORT_NAME = "usb-1a86_USB_Single_Serial_TESTUNIT-if00"
PORT_B = "usb-1a86_USB_Single_Serial_TESTUNITB-if00"
# invented unit UIDs, as Klipper reports them
UID_A = "0123456789abcdef01234567"
UID_B = "89abcdef0123456789abcdef"
# a new install with one unit: Enter at the four lanes: questions, then yes to "Install?"
NEW_ONE = ["", "", "", "", "y"]
PAXX = "2.0.0-paxx12-24-0de1a77\n"
DEBUG_MSG = (
    "Created /oem/.debug. No reboot is needed: the files written now survive the next boot, "
    "because the U1 wipes its overlay at boot only when this flag is missing."
)

RELEASE_ACE2K_CFG = """\
# ace2k — a printer.cfg snippet
[mcu ace2k]
serial: /dev/serial/by-id/<the unit's USB adapter>
baud: 250000
restart_method: command

[ace2k]
#feed_speed: 30
{extra}
[include ace2k_tags.cfg]

[temperature_sensor ace_a_chamber]
sensor_type: ace2k
sensor: chamber

# On a Klipper older than the one ace2k's image is built from, comment this section out.
[temperature_sensor ace_mcu]
sensor_type: temperature_mcu
sensor_mcu: ace2k
"""
RELEASE_U1_CFG = "[ace2k_u1]\n#lanes: 1, 2, 3, 4\n\n[ace2k_u1_history]\n#interval: 30\n"
PRINTER_CFG = """\
[include fluidd.cfg]
[include extended/klipper/*.cfg]
#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.
#*#
#*# [bed_mesh default]
#*# version = 1
#*# points =
#*# \t12, 12
#*#
#*# [ace2k]
#*# lane1_encoder_scale = 1.2424
"""

# A test interrupts install.sh at a chosen step: a stub that finds fake/kill.<stub> sends the
# signal named there to its parent, the installer's shell, once.
KILL_HOOK = """
import os, signal
def kill_hook(fake, name):
    f = fake / ("kill." + name)
    if f.exists():
        sig = getattr(signal, f.read_text().strip())
        f.unlink()
        os.kill(os.getppid(), sig)
"""

STUB_KLIPPER = (
    """#!/usr/bin/env python3
import json, pathlib, sys
fake = pathlib.Path(__file__).resolve().parents[2] / "fake"
action = sys.argv[1]
with open(fake / "klipper.log", "a") as log:
    log.write(action + "\\n")
"""
    + KILL_HOOK
    + """
kill_hook(fake, "klipper")
if (fake / "klipper.noop").exists():  # an init script that returns without restarting anything
    sys.exit(0)
state = json.loads((fake / "moonraker.json").read_text())
if action == "stop":
    state["klippy_state"] = "disconnected"
else:
    after = fake / "after_start.json"
    steps = json.loads(after.read_text()) if after.exists() else [{"klippy_state": "ready"}]
    new = dict(steps[0])
    # a new Klipper process: a new pid, and its console lines after the old ones
    state["pid"] = state.get("pid", 100) + 1
    state["console"] = state.get("console", []) + new.pop("console", [])
    state.update(new)
    if len(steps) > 1:
        after.write_text(json.dumps(steps[1:]))
(fake / "moonraker.json").write_text(json.dumps(state))
"""
)
STUB_NGINX = (
    """#!/usr/bin/env python3
import pathlib, sys
fake = pathlib.Path(__file__).resolve().parents[1]
with open(fake / "nginx.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
"""
    + KILL_HOOK
    + """
if sys.argv[1:] == ["-t"]:
    kill_hook(fake, "nginx")
seq = fake / "nginx.seq"  # one status per nginx -t, in order
codes = seq.read_text().split() if seq.exists() else []
if sys.argv[1:] == ["-t"] and codes:  # once the sequence is used up, nginx.rc answers
    seq.write_text(" ".join(codes[1:]))
    sys.exit(int(codes[0]))
rc = fake / "nginx.rc"
sys.exit(int(rc.read_text()) if rc.exists() and sys.argv[1:] == ["-t"] else 0)
"""
)
STUB_FLASHER = (
    """import json, os, pathlib, sys, time
KNOWS = ["--yes", "--config-follows", "--identify"]  # the options --help lists
if sys.argv[1:] == ["--help"]:
    print("usage: ace2k_flash.pyz [-h] [" + "] [".join(KNOWS) + "] image")
    sys.exit(0)
fake = pathlib.Path(os.environ["ACE2K_FAKE_DIR"])
with open(fake / "flasher.argv", "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
with open(fake / "flasher.times", "a") as log:  # when each run started
    log.write(str(time.time()) + "\\n")
# how many times the installer had looked at /printer/info when it started the flasher
requests = fake / "requests.log"
infos = requests.read_text().split().count("/printer/info") if requests.exists() else 0
with open(fake / "flasher.infos", "a") as log:
    log.write(str(infos) + "\\n")"""
    + KILL_HOOK
    + """
# --identify takes its steps from identify.json, so naming and flashing are scripted apart
script = fake / ("identify.json" if "--identify" in sys.argv else "flasher.json")
steps = json.loads(script.read_text())
step = steps[0]
if len(steps) > 1:
    script.write_text(json.dumps(steps[1:]))
print(step.get("print", ""), flush=True)
for action in step.get("klipper", []):
    with open(fake / "klipper.log", "a") as log:
        log.write("flasher-" + action + "\\n")
if "moonraker" in step:
    state = json.loads((fake / "moonraker.json").read_text())
    new = dict(step["moonraker"])
    if "start" in step.get("klipper", []):
        state["pid"] = state.get("pid", 100) + 1
    state["console"] = state.get("console", []) + new.pop("console", [])
    state.update(new)
    (fake / "moonraker.json").write_text(json.dumps(state))
kill_hook(fake, "flasher")  # the installer interrupted while the flasher ends
sys.exit(step["exit"])
"""
)


def _bytes(value: bytes | str) -> bytes:
    return value if isinstance(value, bytes) else value.encode()


def pyz(source: str) -> bytes:
    """source as a one-file Python program, the way ace2k builds its flasher."""
    buf = io.BytesIO()
    buf.write(b"#!/usr/bin/env python3\n")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("__main__.py", (2000, 1, 1, 0, 0, 0)), source)
    return buf.getvalue()


def tarball(entries: Files) -> bytes:
    """The same entries always give the same bytes: the gzip header carries no time, so a release
    built twice in one test keeps the checksums of its SHA256SUMS."""
    buf = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as tar,
    ):
        for name, text in entries.items():
            data = _bytes(text)
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def host_bundle(ace2k_v: str = ACE2K_V, cfg_extra: str = "#new_setting: 1\n") -> Files:
    """The files of ace2k's host bundle, {name: bytes or str}; test_flasher.py ties the names to
    the real bundle's."""
    return {
        "klippy/extras/ace2k.py": f"# ace2k host {ace2k_v}\n",
        "klippy/extras/ace2k_feed.py": f"# feed {ace2k_v}\n",
        "klippy/extras/ace2k_tags/__init__.py": f"# tags {ace2k_v}\n",
        "config/ace2k.cfg": RELEASE_ACE2K_CFG.format(extra=cfg_extra),
        "config/ace2k_tags.cfg": f"# tags cfg {ace2k_v}\n",
        "firmware/tools/ace2k_flash.pyz": pyz(STUB_FLASHER),
        "LICENSE": "GPL\n",
    }


def release_files(
    ace2k_v: str = ACE2K_V,
    u1_v: str = U1_V,
    requires: str | None = None,
    cfg_extra: str = "#new_setting: 1\n",
    u1_cfg: str | None = None,
    host_without: Iterable[str] = (),
) -> tuple[Files, Files]:
    """(ace2k's release files, ace2k-u1's release files), each {name: bytes or str}.
    host_without names files left out of ace2k's host bundle."""
    host = host_bundle(ace2k_v, cfg_extra)
    for name in host_without:
        del host[name]
    ace2k: Files = {
        f"ace2k-{ace2k_v}.bin": b"\x00image " + ace2k_v.encode(),
        f"ace2k-host-{ace2k_v}.tar.gz": tarball(host),
    }
    u1: Files = {
        f"ace2k-u1-{u1_v}.tar.gz": tarball(
            {
                "klippy/extras/ace2k_u1.py": f"# adapter {u1_v}\n",
                "klippy/extras/ace2k_u1_history.py": f"# history {u1_v}\n",
                "config/ace2k-u1.cfg": u1_cfg if u1_cfg is not None else RELEASE_U1_CFG,
                "tools/ace2k_cfg.py": CFG_TOOL.read_text(),
                "web/index.html": f"<html>{u1_v}</html>\n",
                "web/nginx/ace2k.conf": NGINX_CONF.read_text(),
                "LICENSE": "GPL\n",
            }
        ),
        f"ace2k-u1-{u1_v}.requires": requires if requires is not None else f"ace2k v{ace2k_v}\n",
        "install.sh": INSTALL_SH.read_text(),
    }
    return ace2k, u1


def sums(files: Files) -> str:
    return "".join(
        f"{hashlib.sha256(_bytes(v)).hexdigest()}  {k}\n" for k, v in sorted(files.items())
    )


def write_files(dest: Path, files: Files) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        (dest / name).write_bytes(_bytes(value))
    return dest


def local_release(dest: Path, **kw: Any) -> Path:  # noqa: ANN401 — forwarded unchanged to release_files
    """One directory as --from takes it: both releases' files and one merged SHA256SUMS."""
    ace2k, u1 = release_files(**kw)
    write_files(dest, {**ace2k, **u1})
    (dest / "SHA256SUMS").write_text(sums(ace2k) + sums(u1))
    return dest


def unit_a(version: str, port: str, **kw: Any) -> Json:  # noqa: ANN401 — forwarded to ace2k_state
    """ace2k_state() of a Klipper running one named unit, a, on <port>: a new install's config."""
    return ace2k_state(version, port, units=[("a", port, UID_A)], **kw)


def ace2k_state(
    version: str,
    port: str,
    console: Iterable[str] = ("ace2k_u1: hooked lanes [1, 2, 3, 4]",),
    health: str = "OK",
    proven: str = "yes",
    units: Iterable[tuple[str, str, str]] | None = None,
    versions: dict[str, str] | None = None,
) -> Json:
    """Moonraker's view of a Klipper that is ready with ace2k <version> on <port>; `replies` are
    the console lines a G-code script adds.  With `units`, a list of (name, port, uid), Klipper
    runs those named units instead of one unnamed unit on <port>; `versions` gives one of them
    another version, {name: version}."""
    status: Json = {"print_stats": {"state": "standby"}}
    if units is None:
        status["ace2k"] = {"version": version, "link_proven": proven == "yes", "uid": ""}
        settings: Json = {"mcu ace2k": {"serial": port}, "ace2k": {}}
        health_lines = f"// health: {health}\n"
    else:
        settings, health_lines = {}, ""
        for name, unit_port, uid in units:
            status[f"ace2k {name}"] = {
                "version": (versions or {}).get(name, version),
                "link_proven": proven == "yes",
                "uid": uid,
            }
            settings[f"mcu ace2k_{name}"] = {"serial": unit_port}
            settings[f"ace2k {name}"] = {"mcu": f"ace2k_{name}"}
            health_lines += f"// ace2k {name}: health: {health}\n"
    status["configfile"] = {"settings": settings}
    return {
        "klippy_state": "ready",
        "console": list(console),
        "replies": {
            "ACEK_HEALTH": [f"{health_lines}// failed since boot or last clear: none"],
            "ACEK_STATUS": [f"// ace2k {version}, link proven: {proven}\n// lane 1: insert=0"],
        },
        "status": status,
    }


# An install the installer made earlier, written straight onto the fake U1.
UNIT_CFG = """\
[mcu ace2k_{n}]
serial: {serial}
baud: 250000
restart_method: command

[ace2k {n}]
mcu: ace2k_{n}
{uid}"""
SENSOR_CFG = """
[temperature_sensor ace_{n}_chamber]
sensor_type: ace2k
unit: {n}
sensor: chamber
"""
SAVED = (
    "#*#\n#*# [ace2k]\n#*# lane1_encoder_scale = 1.2424\n"  # printer.cfg's, for an unnamed [ace2k]
)
SAVED_A = "#*#\n#*# [ace2k a]\n#*# lane1_encoder_scale = 1.2424\n"
SAVED_B = "#*#\n#*# [ace2k b]\n#*# lane1_encoder_scale = 1.3\n"


def publish_installed(u1: FakeU1, cfg: str, lanes: str | None, printer: str) -> None:
    """ace2k 0.13.0 / ace2k-u1 0.4.0 on GitHub; the operator's .cfg files and the state file."""
    ace2k, adapter = release_files()
    u1.publish("ace2k", f"v{ACE2K_V}", ace2k)
    u1.publish("ace2k-u1", f"v{U1_V}", adapter)
    (u1.kcfg / "ace2k.cfg").write_text(cfg)
    lanes_line = f"lanes: {lanes}" if lanes else "#lanes: 1, 2, 3, 4"
    (u1.kcfg / "ace2k-u1.cfg").write_text(
        f"[ace2k_u1]\n{lanes_line}\n\n[ace2k_u1_history]\n#interval: 30\n"
    )
    (u1.cfg / "printer.cfg").write_text(printer)
    u1.state_dir.mkdir(parents=True, exist_ok=True)
    (u1.state_dir / "state").write_text(f"ace2k=v{ACE2K_V}\nace2k_u1=v{U1_V}\n")


def installed_two(
    u1: FakeU1, b: str, lanes: str = "a:1, a:2, b:1, b:2", saved_b: bool = False, uids: bool = True
) -> None:
    """Units a and b in ace2k.cfg (b on cable b), Klipper ready with both at the release's
    version; with saved_b, printer.cfg's SAVE_CONFIG block holds [ace2k b] after [ace2k a]."""
    cfg = "# my ace2k config\n"
    for n, serial, uid in (("a", u1.port, UID_A), ("b", b, UID_B)):
        cfg += UNIT_CFG.format(n=n, serial=serial, uid=f"uid: {uid}\n" if uids else "") + "\n"
    cfg += "[include ace2k_tags.cfg]\n" + SENSOR_CFG.format(n="a") + SENSOR_CFG.format(n="b")
    printer = PRINTER_CFG.replace(SAVED, SAVED_A + (SAVED_B if saved_b else ""))
    publish_installed(u1, cfg, lanes, printer)
    state = ace2k_state(ACE2K_V, "", units=[("a", u1.port, UID_A), ("b", b, UID_B)])
    u1.moonraker(state)
    u1.after_start(state)


class _Handler(BaseHTTPRequestHandler):
    fake: Path

    def log_message(self, *args: object) -> None:
        pass

    def _send(self, code: int, body: object) -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _state(self) -> Json:
        return json.loads((self.fake / "moonraker.json").read_text())

    def _advance(self, st: Json, key: str) -> None:
        """The next of st[key]'s steps merged into the state, after one request it counts."""
        steps = st.pop(key, [])
        if steps:
            st.update(steps[0])
            if steps[1:]:
                st[key] = steps[1:]
            (self.fake / "moonraker.json").write_text(json.dumps(st))

    def do_GET(self) -> None:  # noqa: PLR0911 — one answer per route the fake serves
        path = urlparse(self.path).path
        with open(self.fake / "requests.log", "a") as log:
            log.write(path + "\n")
        if path == "/server/info":
            return self._send(200, {"result": {"klippy_state": self._state()["klippy_state"]}})
        if path == "/printer/objects/query":
            st = self._state()
            self._send(200, {"result": {"status": st["status"]}})
            self._advance(st, "on_query")  # objects that change while they are watched
            return None
        if path == "/printer/info":
            st = self._state()
            if st["klippy_state"] == "disconnected":
                self._send(503, {"error": {"message": "Klippy Disconnected"}})
            else:
                self._send(
                    200,
                    {"result": {"state": st["klippy_state"], "process_id": st.get("pid", 100)}},
                )
            self._advance(st, "on_info")  # a Klipper that changes while it is watched
            return None
        if path == "/server/gcode_store":
            console = self._state().get("console", [])
            store = [{"time": float(i + 1), "message": m} for i, m in enumerate(console)]
            return self._send(200, {"result": {"gcode_store": store}})
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "repos" and parts[3] == "releases":
            code = self.fake / "github" / parts[2] / "releases.status"
            if code.exists():
                return self._send(int(code.read_text()), {"message": "API rate limit exceeded"})
            f = self.fake / "github" / parts[2] / "releases.json"
            return self._send(200, json.loads(f.read_text())) if f.exists() else self._send(404, {})
        if len(parts) == 6 and parts[2:4] == ["releases", "download"]:
            f = self.fake / "github" / parts[1] / parts[4] / parts[5]
            return self._send(200, f.read_bytes()) if f.exists() else self._send(404, b"Not Found")
        self._send(404, {"error": {"message": "not found"}})
        return None

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path == "/printer/gcode/script":
            script = parse_qs(url.query).get("script", [""])[0]
            with open(self.fake / "gcode.log", "a") as log:
                log.write(script + "\n")
            state = self._state()
            if script in state.get("gcode_errors", {}):
                code, message = state["gcode_errors"][script]
                return self._send(code, {"error": {"message": message}})
            # the first answers from "reply_queue", one per call; then "replies"
            queue = state.get("reply_queue", {}).get(script) or []
            reply = queue.pop(0) if queue else state.get("replies", {}).get(script, [])
            state["console"] = state.get("console", []) + reply
            (self.fake / "moonraker.json").write_text(json.dumps(state))
            return self._send(200, {"result": "ok"})
        self._send(404, {})
        return None


class FakeU1:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.fake = root / "fake"
        lava = root / "home" / "lava"
        self.extras = lava / "klipper" / "klippy" / "extras"
        self.cfg = lava / "printer_data" / "config"
        self.kcfg = self.cfg / "extended" / "klipper"
        self.web = lava / "ace2k-web"
        self.nginx_conf = root / "etc" / "nginx" / "fluidd.d" / "ace2k.conf"
        self.state_dir = lava / "printer_data" / "ace2k"
        self.logs = lava / "printer_data" / "logs" / "ace2k"
        self.port = str(root / "dev" / "serial" / "by-id" / PORT_NAME)
        for d in (
            self.extras,
            self.kcfg,
            self.nginx_conf.parent,
            root / "oem",
            root / "tmp",
            root / "root",
            root / "dev" / "serial" / "by-id",
            self.fake / "bin",
            root / "etc" / "init.d",
        ):
            d.mkdir(parents=True, exist_ok=True)
        (root / "etc" / "BUILD_VERSION").write_text(PAXX)
        (root / "oem" / ".debug").write_text("")
        (self.cfg / "printer.cfg").write_text(
            PRINTER_CFG.replace("#*#\n#*# [ace2k]\n#*# lane1_encoder_scale = 1.2424\n", "")
        )
        Path(self.port).write_text("")
        self._exe(root / "etc" / "init.d" / "S60klipper", STUB_KLIPPER)
        self._exe(self.fake / "bin" / "nginx", STUB_NGINX)
        (self.fake / "ace2k_flash.pyz").write_text(STUB_FLASHER)
        self.moonraker(
            {
                "klippy_state": "ready",
                "status": {"print_stats": {"state": "standby"}, "configfile": {"settings": {}}},
            }
        )
        handler = type("H", (_Handler,), {"fake": self.fake})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    @staticmethod
    def _exe(path: Path, text: str) -> None:
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def close(self) -> None:
        self.server.shutdown()

    # -- the scenario --
    def moonraker(self, state: Json) -> None:
        (self.fake / "moonraker.json").write_text(json.dumps(state))

    def after_start(self, *states: Json) -> None:
        (self.fake / "after_start.json").write_text(json.dumps(list(states)))

    def flasher(self, *steps: Json) -> None:
        (self.fake / "flasher.json").write_text(json.dumps(list(steps)))

    def identify(self, *steps: Json) -> None:
        """The stub flasher's answers to --identify, one step per run."""
        (self.fake / "identify.json").write_text(json.dumps(list(steps)))

    def add_port(self, name: str) -> str:
        """A cable plugged in: its /dev/serial/by-id path."""
        path = self.root / "dev" / "serial" / "by-id" / name
        path.write_text("")
        return str(path)

    def remove_port(self, path: str) -> None:
        """A cable unplugged."""
        Path(path).unlink()

    def publish(
        self,
        repo: str,
        tag: str,
        files: Files,
        prerelease: bool = False,
        draft: bool = False,
        with_sums: bool = True,
    ) -> None:
        write_files(self.fake / "github" / repo / tag, files)
        if with_sums:
            (self.fake / "github" / repo / tag / "SHA256SUMS").write_text(sums(files))
        index = self.fake / "github" / repo / "releases.json"
        releases = json.loads(index.read_text()) if index.exists() else []
        releases.insert(0, {"tag_name": tag, "prerelease": prerelease, "draft": draft})
        index.write_text(json.dumps(releases))

    # -- running --
    def env(self, tty: bool = True, **extra: str) -> dict[str, str]:
        e = dict(
            os.environ,
            ACE2K_ROOT=str(self.root),
            ACE2K_MOONRAKER=self.url,
            ACE2K_GITHUB_API=self.url,
            ACE2K_GITHUB_DL=self.url,
            ACE2K_TTY=str(self.fake / "tty") if tty else str(self.fake / "no-tty"),
            ACE2K_NGINX=str(self.fake / "bin" / "nginx"),
            ACE2K_FLASHER=str(self.fake / "ace2k_flash.pyz"),
            ACE2K_FAKE_DIR=str(self.fake),
            ACE2K_CFGTOOL=str(CFG_TOOL),
            ACE2K_POLL_S="0",
            ACE2K_WAIT_TRIES="3",
            ACE2K_CHECK_TRIES="4",
            ACE2K_BLINK_S="0",
            PYTHONDONTWRITEBYTECODE="1",
        )
        e.update(extra)
        return e

    def run(
        self,
        *args: object,
        answers: Iterable[str] = (),
        tty: bool = True,
        shell: str = "sh",
        **extra: str,
    ) -> Ran:
        (self.fake / "tty").write_text("".join(a + "\n" for a in answers))
        p: Ran = subprocess.run(  # type: ignore[assignment]  # .out is set below
            [shell, str(INSTALL_SH), *map(str, args)],
            env=self.env(tty=tty, **extra),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        p.out = p.stdout + p.stderr
        return p

    def source(
        self, script: str, tty: bool = True, answers: Iterable[str] = (), **extra: str
    ) -> Ran:
        """Run shell code with install.sh's functions loaded and its paths set."""
        (self.fake / "tty").write_text("".join(a + "\n" for a in answers))
        p: Ran = subprocess.run(  # type: ignore[assignment]  # .out is set below
            ["sh", "-c", self._prelude(script)],
            env=self.env(tty=tty, **extra),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        p.out = p.stdout + p.stderr
        return p

    @staticmethod
    def _prelude(script: str) -> str:
        return f'ACE2K_SOURCE_ONLY=1 . "{INSTALL_SH}"; setup_paths; parse_args; open_tty; {script}'

    def dialog(
        self,
        *args: object,
        steps: Iterable[tuple[str, Callable[[], None] | None, str]],
        **extra: str,
    ) -> Ran:
        """Run install.sh and answer its prompts as they come: for each (text, action, answer),
        wait until what it printed so far (stderr, where the prompts go, then stdout) contains
        text, run action, then type answer."""
        return self._dialog(["sh", str(INSTALL_SH), *map(str, args)], steps, extra)

    def dialog_source(
        self,
        script: str,
        steps: Iterable[tuple[str, Callable[[], None] | None, str]],
        **extra: str,
    ) -> Ran:
        """dialog() for shell code with install.sh's functions loaded, as source() runs it."""
        return self._dialog(["sh", "-c", self._prelude(script)], steps, extra)

    def _dialog(
        self,
        argv: list[str],
        steps: Iterable[tuple[str, Callable[[], None] | None, str]],
        extra: dict[str, str],
    ) -> Ran:
        fifo = self.fake / "tty.fifo"
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(fifo)
        env = self.env(**extra)
        env["ACE2K_TTY"] = str(fifo)
        p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        seen, out = bytearray(), bytearray()

        def drain(stream: io.BufferedReader, into: bytearray) -> None:
            while chunk := stream.read1(4096):
                into += chunk

        threads = [
            threading.Thread(target=drain, args=(p.stderr, seen), daemon=True),
            threading.Thread(target=drain, args=(p.stdout, out), daemon=True),
        ]
        for t in threads:
            t.start()
        with open(fifo, "w") as tty:  # blocks until install.sh opens it
            for text, action, answer in steps:
                deadline = time.monotonic() + 30
                while text.encode() not in seen + out and time.monotonic() < deadline:
                    time.sleep(0.01)
                if action:
                    action()
                tty.write(answer + "\n")
                tty.flush()
        p.wait(timeout=120)
        for t in threads:
            t.join()
        ran: Ran = subprocess.CompletedProcess(  # type: ignore[assignment]  # .out is set below
            argv, p.returncode, out.decode(), seen.decode()
        )
        ran.out = ran.stdout + ran.stderr
        return ran

    # -- what happened --
    def gcodes(self) -> list[str]:
        f = self.fake / "gcode.log"
        return f.read_text().splitlines() if f.exists() else []

    def requests(self, path: str) -> int:
        """How many GET requests reached path (Moonraker's or GitHub's)."""
        f = self.fake / "requests.log"
        return f.read_text().split().count(path) if f.exists() else 0

    def klipper_log(self) -> list[str]:
        f = self.fake / "klipper.log"
        return f.read_text().split() if f.exists() else []

    def restarts(self) -> int:
        return sum(a in ("start", "restart", "flasher-start") for a in self.klipper_log())

    def flasher_calls(self) -> list[list[str]]:
        f = self.fake / "flasher.argv"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def backups(self) -> list[Path]:
        """The backup folders, the newest last: by their modification time, not their name.
        install.sh names each <date>-<time>-<pid>, and a pid wraps, so two in the same second
        may sort either way by name. (install.sh's own prune_backups sorts them by name.)"""
        d = self.state_dir / "backup"
        return sorted(d.iterdir(), key=lambda p: p.stat().st_mtime_ns) if d.exists() else []

    def pid(self) -> int:
        return json.loads((self.fake / "moonraker.json").read_text()).get("pid", 100)

    def state(self) -> dict[str, str]:
        f = self.state_dir / "state"
        return dict(line.split("=", 1) for line in f.read_text().splitlines()) if f.exists() else {}

    def digest(self) -> dict[str, str]:
        """Every file the printer sees, by path (the state directory and the fake's own aside)."""
        skip = (self.fake, self.root / "tmp", self.state_dir)
        out = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and not any(p.is_relative_to(s) for s in skip):
                out[str(p.relative_to(self.root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out

    def digest_readable(self) -> dict[str, str]:
        """digest() that names an unreadable file instead of reading it."""
        skip = (self.fake, self.root / "tmp", self.state_dir)
        out = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and not any(p.is_relative_to(s) for s in skip):
                k = str(p.relative_to(self.root))
                out[k] = (
                    hashlib.sha256(p.read_bytes()).hexdigest() if os.access(p, os.R_OK) else "-"
                )
        return out


def shell_available(name: str) -> bool:
    return shutil.which(name) is not None
