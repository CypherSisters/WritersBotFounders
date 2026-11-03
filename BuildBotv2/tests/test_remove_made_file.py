"""Remove on Knowledge's "Made by your bot" tile: ``owner_files.remove_file`` and ``POST /v1/knowledge/files/remove``.

A route that deletes a file by a path the client sent: only a regular file inside My files, by its real path.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from puffpai_core import owner_files
from puffpai_core.api.app import create_app
from puffpai_core.zone1 import folders
from puffpai_core.zone1.folders import Library, SCAN_LOCK
from z1kit import make

URL = "/v1/knowledge/files/remove"
WORD = "turnipsoup"


def mine(state) -> Path:
    folder = owner_files.my_files(state)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def put(path: Path, text: str = "Just words.") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tree(place: Path) -> dict:
    """Every name under *place*, without following a link, and the bytes of each plain file."""
    out = {}
    for here, dirs, names in os.walk(place, followlinks=False):
        for n in dirs + names:
            p = Path(here) / n
            out[str(p)] = p.read_bytes() if p.is_file() and not p.is_symlink() else None
    return out


def link_file(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this system won't make a link: {exc}")


def link_folder(target: Path, link: Path) -> None:
    """A junction on Windows, which needs no special right; a symlink elsewhere."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.fixture
def lib(state):
    a, *_ = make(state)
    return Library(a, state)


# -- what it deletes ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("where_", ["top.txt", "Made by Puff/deep/sub.txt"])
def test_it_deletes_one_and_it_leaves_search_and_nothing_else_changes(state, lib, where_):
    target = put(mine(state) / where_, f"Words about {WORD}.")
    other = put(mine(state) / "other.txt", "Words about something else entirely.")
    put(mine(state) / "Made by Puff" / "stays.txt")
    assert lib.scan_file(target)["status"] == "indexed" and lib.search(WORD, 5)
    assert lib.scan_file(other)["status"] == "indexed"
    before = tree(state.parent)
    assert owner_files.remove_file(state, str(target)) == ""
    assert not target.exists() and not lib.search(WORD, 5)
    assert lib._row(folders._key(target)) is None                     # noqa: SLF001 - no row to say it is still read
    assert lib.search("entirely", 5)                                  # another file's words stay
    after = tree(state.parent)
    gone = {k for k in before if k not in after}
    assert gone == {str(target)}
    for k, v in before.items():
        if k in after and v is not None and "knowledge.db" not in k and "vault" not in k:
            assert after[k] == v, k


def test_a_bare_name_means_one_in_my_files(state, lib):
    target = put(mine(state) / "named.txt")
    assert owner_files.remove_file(state, "named.txt") == "" and not target.exists()


# -- what it refuses ---------------------------------------------------------------------------------------------------

def test_it_refuses_everything_outside_my_files(state, lib, tmp_path):
    folder = mine(state)
    put(folder / "keep.txt")
    linked = tmp_path / "Linked"
    in_linked = put(linked / "a.txt")
    lib.link(linked)
    places = [put(state / "data" / "in_data.txt"), in_linked, put(state / "own.txt"), put(state / "vault" / "vault.txt"),
              put(state / "settings" / "a.json"), put(tmp_path / "elsewhere.txt")]
    before = tree(state.parent)
    for p in places:
        assert owner_files.remove_file(state, str(p)) == owner_files.REMOVE_OUTSIDE, p
    assert all(p.exists() for p in places) and tree(state.parent) == before


def test_it_refuses_dots_going_up_even_if_they_land_inside(state, lib):
    folder = mine(state)
    above = put(state / "data" / "above.txt")
    inside = put(folder / "keep.txt")
    (folder / "sub").mkdir()
    for raw in (str(folder / "..") + os.sep + "above.txt", "../above.txt", "..\\above.txt",
                str(folder / "sub") + os.sep + ".." + os.sep + "keep.txt", "sub/../keep.txt"):
        assert owner_files.remove_file(state, raw) == owner_files.REMOVE_OUTSIDE, raw
    assert above.exists() and inside.exists()


def test_it_refuses_a_folder_a_blank_and_a_bare_name_that_is_elsewhere(state, lib, tmp_path, monkeypatch):
    folder = mine(state)
    (folder / "sub").mkdir()
    put(folder / "sub" / "kept.txt")
    assert owner_files.remove_file(state, str(folder / "sub")) == owner_files.NOT_FOUND
    assert owner_files.remove_file(state, str(folder)) == owner_files.NOT_FOUND
    assert (folder / "sub" / "kept.txt").exists()
    for blank in ("", "   ", None, '""'):
        assert owner_files.remove_file(state, blank) == owner_files.NO_FILE
    assert owner_files.remove_file(state, "x" * 1001) == owner_files.NO_FILE
    assert owner_files.remove_file(state, "a\x00b") == owner_files.NO_FILE
    here = tmp_path / "cwd"
    here.mkdir()
    stray = put(here / "stray.txt")
    monkeypatch.chdir(here)
    assert owner_files.remove_file(state, "stray.txt") == owner_files.NOT_FOUND and stray.exists()


@pytest.mark.parametrize("name", [".hidden.txt", "~$lock.docx", ".hidden_folder/inside.txt"])
def test_it_refuses_the_names_recent_leaves_out(state, lib, name):
    p = put(mine(state) / name)
    assert name not in [r["name"] for r in owner_files.recent(state)] and not owner_files.recent(state)
    assert owner_files.remove_file(state, str(p)) == owner_files.REMOVE_NOT_LISTED and p.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_it_refuses_a_device_path_a_stream_and_a_share_here(state, lib):
    p = put(mine(state) / "keep.txt")
    drive_rest = str(p.resolve())
    share = "\\\\localhost\\" + drive_rest[0] + "$" + drive_rest[2:]
    for raw in ("\\\\?\\" + drive_rest, "\\\\.\\" + drive_rest, drive_rest + "::$DATA", drive_rest + ":extra", share):
        assert owner_files.remove_file(state, raw) == owner_files.REMOVE_OUTSIDE, raw
    assert p.read_text(encoding="utf-8") == "Just words."


def test_a_link_out_is_refused_and_its_target_stays(state, lib, tmp_path):
    outside = put(tmp_path / "outside.txt")
    link = mine(state) / "link.txt"
    link_file(outside, link)
    assert owner_files.remove_file(state, str(link)) == owner_files.REMOVE_OUTSIDE
    assert os.path.lexists(link) and outside.exists()


def test_a_link_to_a_file_inside_is_refused_not_followed(state, lib):
    target = put(mine(state) / "real.txt")
    link = mine(state) / "link.txt"
    link_file(target, link)
    assert owner_files.remove_file(state, str(link)) == owner_files.REMOVE_NOT_LISTED
    assert os.path.lexists(link) and target.exists()


def test_the_file_itself_is_asked_if_it_is_a_link(state, lib, monkeypatch):
    """The two tests above skip where the OS won't make a file link: this one runs everywhere."""
    from puffpai_core.zone1 import knowledge_folder
    p = put(mine(state) / "looks_like_a_link.txt")
    monkeypatch.setattr(knowledge_folder, "is_link", lambda way: Path(way) == p)
    assert owner_files.remove_file(state, str(p)) == owner_files.REMOVE_NOT_LISTED and p.exists()


def test_a_file_gone_during_the_delete_says_its_words_may_stay(state, lib, monkeypatch):
    p = put(mine(state) / "raced.txt")
    monkeypatch.setattr(Library, "forget", lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    assert "is deleted, but a search may still find" in owner_files.remove_file(state, str(p)) and not p.exists()


def test_a_way_through_a_junction_is_refused_out_of_or_inside_my_files(state, lib, tmp_path):
    outside = put(tmp_path / "away" / "a.txt")
    inner = put(mine(state) / "real_folder" / "b.txt")
    link_folder(outside.parent, mine(state) / "out_way")
    link_folder(inner.parent, mine(state) / "in_way")
    assert owner_files.remove_file(state, str(mine(state) / "out_way" / "a.txt")) == owner_files.REMOVE_OUTSIDE
    assert owner_files.remove_file(state, str(mine(state) / "in_way" / "b.txt")) == owner_files.REMOVE_NOT_LISTED
    assert outside.exists() and inner.exists()


def test_a_vault_named_inside_my_files_is_refused(state, lib):
    """``where``'s vault rule still runs: a vault place that settings put in My files is never a file to delete."""
    place = mine(state) / "safe"
    p = put(place / "sealed.bin")
    put(state / "settings" / "vault.json", json.dumps({"folder": str(place)}))
    assert owner_files.remove_file(state, str(p)) == owner_files.REMOVE_OUTSIDE and p.exists()


# -- gone, held open, busy ---------------------------------------------------------------------------------------------

def test_a_file_already_gone_says_so(state, lib):
    p = put(mine(state) / "once.txt")
    assert owner_files.remove_file(state, str(p)) == ""
    assert owner_files.remove_file(state, str(p)) == owner_files.NOT_FOUND


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to delete a file held open")
def test_a_file_held_open_says_so_and_stays_in_search(state, lib):
    p = put(mine(state) / "held.txt", f"Words about {WORD}.")
    lib.scan_file(p)
    with open(p, "rb"):
        said = owner_files.remove_file(state, str(p))
    assert "could not be deleted" in said and "open" in said
    assert p.exists() and lib.search(WORD, 5)


def test_a_scan_running_refuses_and_changes_nothing(state, lib, monkeypatch):
    p = put(mine(state) / "busy.txt")
    monkeypatch.setattr(folders, "SCAN_WAIT", 0.01)
    with SCAN_LOCK:
        assert owner_files.remove_file(state, str(p)) == folders.BUSY_LINE
    assert p.exists()


# -- the route ---------------------------------------------------------------------------------------------------------

def test_the_route_says_what_happened_and_it_leaves_search(state, tmp_path):
    a, *_ = make(state)
    lib = Library(a, state)
    target = put(mine(state) / "Made by Puff" / "Plan.txt", f"Words about {WORD}.")
    lib.scan_file(target)
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    client = app.test_client()
    for body in ({}, {"path": ""}, {"path": "   "}, {"path": 7}, {"path": "x" * 1001}):
        r = client.post(URL, json=body, headers=dev)
        assert r.status_code == 400 and r.json == {"error": "Say which file."}, body
    outside = put(tmp_path / "outside.txt")
    r = client.post(URL, json={"path": str(outside)}, headers=dev)
    assert r.status_code == 400 and r.json == {"error": owner_files.REMOVE_OUTSIDE} and outside.exists()
    r = client.post(URL, json={"path": f'"{target}"'}, headers=dev)     # Copy as path adds the quotes
    assert r.status_code == 200 and r.json == {"removed": True, "said": "Plan.txt is deleted from your bot."}
    assert not target.exists() and not lib.search(WORD, 5)
    r = client.post(URL, json={"path": str(target)}, headers=dev)
    assert r.status_code == 400 and r.json == {"error": owner_files.NOT_FOUND}


def test_only_a_paired_device_may(state):
    target = put(mine(state) / "stays.txt")
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    for token in (core.tokens.issue_owner_key(), core.tokens.issue_package_token("some-app"), core.tokens.issue_toolsmith("b1")):
        r = client.post(URL, json={"path": str(target)}, headers={"Authorization": "Bearer " + token})
        assert r.status_code == 403
    assert client.post(URL, json={"path": str(target)}).status_code in (401, 403)
    assert target.exists()
