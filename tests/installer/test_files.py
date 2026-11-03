"""The file layer: the release compared with the printer, every change backed up first."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from fake_u1 import NGINX_CONF, local_release

if TYPE_CHECKING:
    from fake_u1 import FakeU1

# one unit, a, on the port, and the lanes: a new install asked for: what write_cfgs writes from
LOAD = (
    'WORK=$(mktemp -d); FROM="{rel}"; fetch_release >/dev/null; PORT="{port}"; '
    'printf "a\\t%s\\n" "$PORT" >"$WORK/names"; LANES="a:1, a:2, a:3, a:4"; '
)


def _load(u1: FakeU1, tmp_path: Path) -> str:
    return LOAD.format(rel=local_release(tmp_path / "rel"), port=u1.port)


def test_an_overwritten_file_is_backed_up_first(u1: FakeU1, tmp_path: Path) -> None:
    (u1.extras / "ace2k_u1.py").write_text("# an older adapter\n")
    p = u1.source(
        _load(u1, tmp_path) + "start_changes install; sync_files new; write_files; write_cfgs"
    )
    assert p.returncode == 0, p.out
    assert (u1.extras / "ace2k_u1.py").read_text() == "# adapter 0.4.0\n"
    [bk] = u1.backups()
    saved = bk / str(u1.extras / "ace2k_u1.py").lstrip("/")
    assert saved.read_text() == "# an older adapter\n"


def test_backups_of_the_same_second_are_ordered_by_their_time_not_their_pid(u1: FakeU1) -> None:
    """The pid wrapped between two runs of the same second: 9929 is the newer one."""
    d = u1.state_dir / "backup"
    made = (("20261018-052847-20000", 1), ("20261018-052848-10171", 2), ("20261018-052848-9929", 3))
    for name, t in made:
        (d / name).mkdir(parents=True)
        os.utime(d / name, (t, t))
    assert [bk.name for bk in u1.backups()] == [name for name, _t in made]


def test_a_new_install_writes_everything(u1: FakeU1, tmp_path: Path) -> None:
    p = u1.source(
        _load(u1, tmp_path) + "start_changes install; sync_files new; write_files; write_cfgs"
    )
    assert p.returncode == 0, p.out
    assert (u1.extras / "ace2k.py").exists() and (u1.extras / "ace2k_u1.py").exists()
    assert (u1.extras / "ace2k_tags" / "__init__.py").exists()
    assert f"serial: {u1.port}" in (u1.kcfg / "ace2k.cfg").read_text()
    assert (u1.kcfg / "ace2k-u1.cfg").exists() and (u1.kcfg / "ace2k_tags.cfg").exists()
    assert (u1.web / "index.html").exists() and not (u1.web / "nginx").exists()
    # the snippet lands unchanged, the page's headers with it (tests/test_nginx_conf.py)
    assert u1.nginx_conf.read_text() == NGINX_CONF.read_text()
    assert 'add_header Cache-Control "no-cache" always;' in u1.nginx_conf.read_text()
    # nginx -t before the snippet is written and after, then the reload
    assert (u1.fake / "nginx.log").read_text().splitlines() == ["-t", "-t", "-s reload"]
    assert (u1.state_dir / "flash" / "ace2k_flash.pyz").exists()
    assert (u1.state_dir / "flash" / "ace2k-0.13.0.bin").exists()


def test_the_page_and_the_modules_are_readable_under_roots_umask(
    u1: FakeU1, tmp_path: Path
) -> None:
    # the printer's root has umask 0077: a copy made under it is root's alone; nginx answers 403,
    # and Klipper, which runs as lava, stops at its start on "Permission denied"
    p = u1.source(
        "umask 077; "
        + _load(u1, tmp_path)
        + "start_changes install; sync_files new; write_files; write_cfgs"
    )
    assert p.returncode == 0, p.out
    assert stat.S_IMODE(u1.web.stat().st_mode) & 0o055 == 0o055
    assert stat.S_IMODE((u1.web / "index.html").stat().st_mode) & 0o044 == 0o044
    for module in (u1.extras / "ace2k.py", u1.extras / "ace2k_u1.py"):
        assert stat.S_IMODE(module.stat().st_mode) & 0o044 == 0o044, module


def test_an_older_installs_flasher_is_replaced_and_nothing_else_goes(
    u1: FakeU1, tmp_path: Path
) -> None:
    flash = u1.state_dir / "flash"
    flash.mkdir(parents=True)
    (flash / "ace2k_flash.py").write_text("# an older install's flasher\n")
    (flash / "ace2k_flash.py.bak").write_text("the operator's copy of it")
    (flash / "ace2k-0.12.0.bin").write_text("an older image")
    (flash / "notes.txt").write_text("the operator's")
    own = u1.root / "root" / "ace2k-flash" / "ace2k_flash.py"  # a flash done by hand
    own.parent.mkdir(parents=True)
    own.write_text("# the operator's copy\n")
    p = u1.source(_load(u1, tmp_path) + "start_changes install; sync_files new; write_files")
    assert p.returncode == 0, p.out
    assert sorted(f.name for f in flash.iterdir()) == [
        "ace2k-0.12.0.bin",
        "ace2k-0.13.0.bin",
        "ace2k_flash.py.bak",
        "ace2k_flash.pyz",
        "notes.txt",
    ]
    assert own.read_text() == "# the operator's copy\n"


def test_a_new_install_writes_no_cfg_before_write_cfgs(u1: FakeU1, tmp_path: Path) -> None:
    p = u1.source(_load(u1, tmp_path) + "start_changes install; sync_files new; write_files")
    assert p.returncode == 0, p.out
    assert list(u1.kcfg.glob("ace2k*.cfg")) == [] and (u1.extras / "ace2k.py").exists()


def test_an_update_keeps_the_operators_cfgs_and_drops_retired_modules(
    u1: FakeU1, tmp_path: Path
) -> None:
    (u1.kcfg / "ace2k.cfg").write_text("mine\n")
    mine = "[ace2k_u1]\nlanes: 3\n\n[ace2k_u1_history]\ninterval: 60\n"  # every release section
    (u1.kcfg / "ace2k-u1.cfg").write_text(mine)
    (u1.extras / "ace2k_retired.py").write_text("# gone in the new release\n")
    (u1.extras / "ace2k_u1_tags.py").write_text("# the adapter's, not ace2k's\n")
    p = u1.source(_load(u1, tmp_path) + "start_changes update; sync_files update; write_files")
    assert p.returncode == 0, p.out
    assert (u1.kcfg / "ace2k.cfg").read_text() == "mine\n"
    assert (u1.kcfg / "ace2k-u1.cfg").read_text() == mine
    assert (u1.kcfg / "ace2k_tags.cfg").exists()
    assert not (u1.extras / "ace2k_retired.py").exists()
    assert not (u1.extras / "ace2k_u1_tags.py").exists()  # the adapter release dropped it too


def test_the_comparison_writes_nothing(u1: FakeU1, tmp_path: Path) -> None:
    load = _load(u1, tmp_path)
    u1.source(load + "start_changes update; sync_files update; write_files")
    before = u1.digest()
    p = u1.source(load + 'sync_files update; echo "changed=$CHANGED"')
    assert "changed=0" in p.out and u1.digest() == before
    (u1.extras / "ace2k.py").write_text("# edited\n")
    (u1.extras / "ace2k_retired.py").write_text("# gone\n")
    p = u1.source(load + 'sync_files update; echo "changed=$CHANGED"')
    assert "changed=1" in p.out and (u1.extras / "ace2k.py").read_text() == "# edited\n"
    assert (u1.extras / "ace2k_retired.py").exists()


def test_the_apply_step_makes_exactly_the_planned_changes(u1: FakeU1, tmp_path: Path) -> None:
    (u1.extras / "ace2k_retired.py").write_text("# gone\n")
    load = _load(u1, tmp_path)
    # the plan lists the retired module; dropping its line from the list keeps the file
    p = u1.source(
        load + 'sync_files update; cat "$WORK/changes"; '
        'grep -v ace2k_retired "$WORK/changes" > "$WORK/c" && mv "$WORK/c" "$WORK/changes"; '
        "start_changes update; write_files"
    )
    assert p.returncode == 0, p.out
    assert "ace2k_retired.py" in p.out
    assert (u1.extras / "ace2k_retired.py").exists() and (u1.extras / "ace2k.py").exists()


def test_nginx_already_broken_leaves_the_snippet_alone_and_goes_on(
    u1: FakeU1, tmp_path: Path
) -> None:
    u1.nginx_conf.write_text("# the old snippet\n")
    (u1.fake / "nginx.rc").write_text("1")
    p = u1.source(
        _load(u1, tmp_path) + "start_changes update; sync_files update; write_files; echo went-on"
    )
    assert p.returncode == 0 and "went-on" in p.out, p.out
    assert "already broken" in p.out
    assert u1.nginx_conf.read_text() == "# the old snippet\n"
    assert (u1.web / "index.html").exists()
    assert (u1.fake / "nginx.log").read_text().splitlines() == ["-t"]


def test_nginx_refusing_the_new_snippet_puts_the_previous_one_back(
    u1: FakeU1, tmp_path: Path
) -> None:
    u1.nginx_conf.write_text("# the old snippet\n")
    (u1.fake / "nginx.seq").write_text("0 1")
    p = u1.source(_load(u1, tmp_path) + "start_changes update; sync_files update; write_files")
    assert p.returncode == 1 and "nginx -t refuses" in p.out and "run the installer again" in p.out
    assert u1.nginx_conf.read_text() == "# the old snippet\n"


def test_nginx_refusing_a_first_snippet_removes_it(u1: FakeU1, tmp_path: Path) -> None:
    (u1.fake / "nginx.seq").write_text("0 1")
    p = u1.source(_load(u1, tmp_path) + "start_changes install; sync_files new; write_files")
    assert p.returncode == 1 and "nginx -t refuses" in p.out
    assert not u1.nginx_conf.exists()


def test_calibration_files_are_copied_not_moved(u1: FakeU1) -> None:
    for sub in ("", "ace2k-flash/", "ace-calibration/"):
        d = u1.root / "root" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / f"ace-calibration-X-{len(sub)}.txt").write_text(sub)
    u1.source("calibration_copy_in")
    copied = sorted(p.name for p in (u1.state_dir / "calibration").iterdir())
    assert copied == [
        "ace-calibration-X-0.txt",
        "ace-calibration-X-12.txt",
        "ace-calibration-X-16.txt",
    ]
    assert (u1.root / "root" / "ace-calibration-X-0.txt").exists()


def test_the_state_file_marks_a_run_in_progress_and_a_finished_run_clears_it(u1: FakeU1) -> None:
    u1.source("start_changes install")
    assert u1.state() == {"in_progress": "install"}
    u1.source("ACE2K_TAG=v0.13.0 U1_TAG=v0.4.0; state_write")
    assert u1.state()["ace2k"] == "v0.13.0" and u1.state()["ace2k_u1"] == "v0.4.0"
    assert "in_progress" not in u1.state()
    u1.source("start_changes update")  # an update keeps the versions it started from
    assert u1.state()["in_progress"] == "update" and u1.state()["ace2k"] == "v0.13.0"
