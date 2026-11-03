"""Lane i23c: at the add, the folders a run made go with its files, so the app starts as it did the first time.

In the owner's test7 a database app worked in its try and failed after her Yes: its files were dropped but the
empty ``mariadb_data/mysql`` folder stayed, so MariaDB skipped its first-run setup and died. Anything that reads
"the folder is there" as "already set up" can do this. Shapes here: a MariaDB bind mount, a postgres-like data
folder and an app's output folder.

What the run made is written straight to the try folder, as a container's bind mount does. No network, no Docker.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from test_d61_allowlist import WIN, _checked, world  # noqa: F401 - shared
from test_i2a_part_c import _adopted_manifest, _plan_yes, _try_that_served, real_world  # noqa: F401 - shared
from puffpai_core.packages import fsutil
from puffpai_core.zone3.drawbridge import tree_sha

SHAPES = {
    "mariadb": ("mariadb_data", ["mariadb_data/mysql/db.frm", "mariadb_data/mysql/db.MYD", "mariadb_data/shop/x.ibd",
                                 "mariadb_data/performance_schema/db.opt", "mariadb_data/sys/db.opt"]),
    "postgres": ("data", ["data/pg/PG_VERSION", "data/pg/base/1/16384", "data/pg/pg_wal/000001"]),
    "uploads": ("uploads", ["uploads/cache/thumb-1.bin", "uploads/cache/a/b/thumb-2.bin"]),
}


def _repo(tmp_path: Path) -> Path:
    """A small download: a shipped empty folder, and a shipped folder with a file in it."""
    repo = tmp_path / "repo"
    (repo / "shipped_empty" / "inner").mkdir(parents=True)
    (repo / "static").mkdir()
    (repo / "static" / "site.css").write_text("p { margin: 0 }\n", encoding="utf-8")
    (repo / "README.md").write_text("# Hello Page\n\nA tiny page of notes.\n", encoding="utf-8")
    (repo / "index.html").write_text("<!doctype html><title>Hello</title><p>Hello</p>\n", encoding="utf-8")
    return repo


def _made(folder: Path, rels) -> None:
    for rel in rels:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(b"made by the run\n")


@pytest.fixture
def tried(world, tmp_path):  # noqa: F811 - the shared fixture
    _b, link, try_id, rec = _checked(world, _repo(tmp_path))
    return world, link, try_id, rec


def _drop(w, rec) -> None:
    w.host.trials._drop_run_files(rec)


# -- the three shapes, through the whole add ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_folders_a_run_made_are_not_in_what_is_added(tried, shape):
    w, link, try_id, rec = tried
    top, rels = SHAPES[shape]
    _made(rec.folder, rels)
    got = link.try_add(try_id, "local", _adopted_manifest(rec.pkg_id))
    assert got.get("ok"), got
    staged = w.ports.pm[got["consent_id"]]["staged"]
    assert not (staged / top).exists()
    assert (staged / "index.html").is_file() and (staged / "static" / "site.css").is_file()
    assert (staged / "shipped_empty" / "inner").is_dir()
    assert json.loads((staged / ".buildbot" / "puffpai.json").read_text(encoding="utf-8"))["id"] == rec.pkg_id


def test_the_real_package_manager_installs_it_without_the_run_folders(real_world, tmp_path):  # noqa: F811
    w = real_world
    w.ports.fixture_repo = _repo(tmp_path)
    b, link, try_id, rec = _try_that_served(w)
    _made(rec.folder, SHAPES["mariadb"][1])
    _plan_yes(w, b)
    got = link.try_add(try_id, "local", _adopted_manifest(rec.pkg_id))
    assert got.get("ok") and got.get("covered"), got
    state = w.core.packages.wait(got["consent_id"], 300)
    assert state["status"] == "installed", state
    final = w.state / "packages" / rec.pkg_id
    assert not (final / "mariadb_data").exists()
    assert (final / "shipped_empty" / "inner").is_dir() and (final / "static" / "site.css").is_file()


# -- what stays --------------------------------------------------------------------------------------------------

def test_a_shipped_empty_folder_stays(tried):
    w, _link, _try_id, rec = tried
    _drop(w, rec)
    assert (rec.folder / "shipped_empty" / "inner").is_dir()


def test_a_shipped_folder_that_gained_run_files_keeps_its_shipped_files(tried):
    w, _link, _try_id, rec = tried
    _made(rec.folder, ["static/run.log", "static/cache/a.bin", "shipped_empty/inner/b.bin"])
    _drop(w, rec)
    assert (rec.folder / "static" / "site.css").is_file()
    assert not (rec.folder / "static" / "run.log").exists() and not (rec.folder / "static" / "cache").exists()
    assert (rec.folder / "shipped_empty" / "inner").is_dir()
    assert not (rec.folder / "shipped_empty" / "inner" / "b.bin").exists()


def test_a_run_made_folder_holding_a_file_toolsmith_wrote_stays(tried):
    w, link, try_id, rec = tried
    assert link.try_write(try_id, "conf/deep/app.ini", "[app]\n")["ok"]
    _made(rec.folder, ["conf/run.log", "conf/deep/tmp/a.bin", "conf/other/b.bin"])
    _drop(w, rec)
    assert (rec.folder / "conf" / "deep" / "app.ini").is_file()
    for gone in ("conf/run.log", "conf/deep/tmp", "conf/other"):
        assert not (rec.folder / gone).exists(), gone


def test_the_folder_of_a_file_toolsmith_wrote_stays_even_if_the_file_went(tried):
    w, link, try_id, rec = tried
    assert link.try_write(try_id, "conf/app.ini", "[app]\n")["ok"]
    (rec.folder / "conf" / "app.ini").unlink()                # the run took it away
    _drop(w, rec)
    assert (rec.folder / "conf").is_dir()


def test_a_folder_toolsmith_made_stays_but_what_the_run_put_in_it_goes(tried):
    w, _link_api, _try_id, rec = tried
    assert w.host.trials.fs(rec, {"op": "mkdir", "path": "made/by/toolsmith"})["ok"]
    _made(rec.folder, ["made/by/toolsmith/run/a.bin"])
    _drop(w, rec)
    assert (rec.folder / "made" / "by" / "toolsmith").is_dir()
    assert not (rec.folder / "made" / "by" / "toolsmith" / "run").exists()


def test_the_core_manifest_folder_stays(tried):
    w, link, try_id, rec = tried
    got = link.try_add(try_id, "local", _adopted_manifest(rec.pkg_id))
    assert got.get("ok"), got
    assert (w.ports.pm[got["consent_id"]]["staged"] / ".buildbot" / "puffpai.json").is_file()


def test_folders_setup_makes_again_are_left_as_they_were(tried):
    w, _link_api, _try_id, rec = tried
    _made(rec.folder, ["node_modules/x/index.js", "static/__pycache__/a.pyc"])
    _drop(w, rec)
    assert (rec.folder / "node_modules" / "x" / "index.js").is_file()
    assert (rec.folder / "static" / "__pycache__" / "a.pyc").is_file()


# -- rec.dirs stays honest ---------------------------------------------------------------------------------------

def test_a_moved_shipped_folder_is_kept_at_its_new_place(tried):
    w, _link_api, _try_id, rec = tried
    assert w.host.trials.fs(rec, {"op": "move", "from": "shipped_empty", "to": "new/place"})["ok"]
    _drop(w, rec)
    assert (rec.folder / "new" / "place" / "inner").is_dir()
    assert not (rec.folder / "shipped_empty").exists()


def test_a_deleted_shipped_folder_the_run_made_again_goes(tried):
    w, _link_api, _try_id, rec = tried
    assert w.host.trials.fs(rec, {"op": "delete", "path": "shipped_empty"})["ok"]
    (rec.folder / "shipped_empty" / "inner").mkdir(parents=True)
    _drop(w, rec)
    assert not (rec.folder / "shipped_empty").exists()


# -- links and locks ---------------------------------------------------------------------------------------------

def _link(link: Path, target: Path) -> None:
    if WIN:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def _unlink(link: Path) -> None:
    if WIN:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], check=True, capture_output=True)
    else:
        link.unlink()


def test_a_link_in_run_data_is_not_followed_and_its_target_is_untouched(tried, tmp_path):
    w, _link_api, _try_id, rec = tried
    outside = tmp_path / "outside"
    (outside / "empty").mkdir(parents=True)
    (outside / "keep.txt").write_text("hers\n", encoding="utf-8")
    _made(rec.folder, ["dbdata/mysql/a.frm", "dbdata/other/b.frm"])
    inner, top = rec.folder / "dbdata" / "mysql" / "linked", rec.folder / "linked_top"
    _link(inner, outside)
    _link(top, outside)
    try:
        _drop(w, rec)
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "hers\n"
        assert (outside / "empty").is_dir()
        assert os.path.lexists(inner) and os.path.lexists(top)    # a link is never taken for a folder to remove
        assert not (rec.folder / "dbdata" / "mysql" / "a.frm").exists()
        assert not (rec.folder / "dbdata" / "other").exists()
    finally:
        _unlink(inner)
        _unlink(top)


@pytest.mark.skipif(not WIN, reason="a file held open blocks its removal only on Windows")
def test_a_folder_that_stays_locked_does_not_fail_the_drop(tried):
    w, _link_api, _try_id, rec = tried
    _made(rec.folder, ["data/pg/held.bin", "data/free/a.bin"])
    with open(rec.folder / "data" / "pg" / "held.bin", "rb"):
        _drop(w, rec)
        assert (rec.folder / "data" / "pg" / "held.bin").is_file()
    assert not (rec.folder / "data" / "free").exists()


# -- the hashes the Yes is bound to ------------------------------------------------------------------------------

def test_removing_a_run_folder_does_not_change_the_hash_of_the_tree(tried):
    _w, _link_api, _try_id, rec = tried
    before = tree_sha(rec.folder), fsutil.tree_hash(rec.folder)
    (rec.folder / "mariadb_data" / "mysql").mkdir(parents=True)
    assert (tree_sha(rec.folder), fsutil.tree_hash(rec.folder)) == before
    (rec.folder / "mariadb_data" / "mysql").rmdir()
    (rec.folder / "mariadb_data").rmdir()
    assert (tree_sha(rec.folder), fsutil.tree_hash(rec.folder)) == before
