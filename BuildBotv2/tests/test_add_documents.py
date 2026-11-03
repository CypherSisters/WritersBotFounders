"""V3-1 (PROPOSED, not yet tested by her): one Add documents button, one list on Knowledge, and Remove. Real routes, the real
gate on a real vault, real junctions and real links where this computer can make them. Nothing here reaches a network.

Most of this file tries to break two promises: an original is never deleted, and plain words of a document never stay in
the index after it stops being "no vault" (D146).
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from puffpai_core import bot_move, owner_files, send_file, wiring
from puffpai_core.wizard.terminal import moved_lines
from puffpai_core.zone1 import books, fetched, folders, pinned, uploads
from puffpai_core.zone1 import gate as zone1_gate
from puffpai_core.zone1.folders import (FICTION_LINE, FICTION_REASON, GONE_LISTED, FolderRefused, Library, LibraryBusy,
                                        SCAN_LOCK, canon)
from test_bot_move import _world
from test_d134_upload import folder, on, phone, send  # noqa: F401 - fixtures
from test_lan_pairing import local
from z1kit import canary_key, make

REPO = Path(__file__).resolve().parents[1]
LIST = "/v1/knowledge/documents"
NAMED = LIST + "/named"
REMOVE = LIST + "/remove"
PICK = "/v1/knowledge/pick"
PHONE = "415-555-0137"
OTHER_PHONE = "415-555-0199"
KEY = canary_key()
WORDS = "The river ran low that summer."
PRIVATE = f"The river ran low. Ring {PHONE} by the orchard. The key {KEY} opens it."
READY = "{} is ready. Your bot can find it now."
OFF = "{} is off your list. The file itself stays where it is."
GONE = "{} is deleted from your bot."


# -- the world --------------------------------------------------------------------------------------------------------
@pytest.fixture
def dev(core):
    """The owner's own terminal key, on this computer."""
    c = core.app.test_client()
    key = local(core.tokens.issue_device("a device"))

    def call(method, path, body=None):
        return c.open(path, method=method, json=body, headers=key)
    return call


@pytest.fixture
def w(state, tmp_path, monkeypatch):
    """The real gate on a real vault, with no Core around it."""
    a, *_ = make(state)
    zone1_gate.bind(a)
    monkeypatch.setattr(a.inbox, "seal_held", lambda *x, **k: None)
    yield SimpleNamespace(a=a, lib=Library(a, state), state=state, tmp=tmp_path)
    zone1_gate.bind(None)


@pytest.fixture
def junction():
    """A real junction (a real folder link off Windows), taken away before the tree goes."""
    made = []

    def make_one(target, where):
        Path(where).parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(target), str(where))
            else:
                os.symlink(target, where, target_is_directory=True)
        except OSError:
            pytest.skip("this account can't make links")
        made.append(where)
    yield make_one
    for where in made:
        (os.rmdir if os.name == "nt" else os.unlink)(where)


def file_link(target, where):
    """A real link to a file. Windows lets only some accounts make one."""
    Path(where).parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, where)
    except OSError:
        pytest.skip("this account can't make file links")


def original(tmp_path, name="My Novel.txt", words=WORDS) -> Path:
    p = tmp_path / "Writing" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(words, encoding="utf-8")
    return p


def uploaded(state, name="sent.txt", words=WORDS, data=None) -> Path:
    p = Path(data or Path(state) / "data") / uploads.FOLDER / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(words, encoding="utf-8")
    return p


def settings(state) -> dict:
    return json.loads((Path(state) / "settings" / "folders.json").read_text(encoding="utf-8"))


def write_settings(state, **values) -> None:
    path = Path(state) / "settings" / "folders.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        now = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        now = {}
    path.write_text(json.dumps({**(now if isinstance(now, dict) else {}), **values}), encoding="utf-8")


def rows_of(lib, path) -> tuple[int, int]:
    """Rows in the index for this file, under either way it could be spelled."""
    with closing(sqlite3.connect(lib.db_path)) as db:
        keys = (str(path), str(Path(path).resolve()))
        return (db.execute("SELECT count(*) FROM chunks WHERE path IN (?, ?)", keys).fetchone()[0],
                db.execute("SELECT count(*) FROM files WHERE path IN (?, ?)", keys).fetchone()[0])


def everything(lib) -> str:
    with closing(sqlite3.connect(lib.db_path)) as db:
        return "\n".join(r[0] for r in db.execute("SELECT text FROM chunks"))


def row_for(documents, path) -> dict:
    return next(r for r in documents if canon(r["path"]) == canon(path))


def on_list(documents, path) -> bool:
    return any(canon(r["path"]) == canon(path) for r in documents)


# -- add ---------------------------------------------------------------------------------------------------------------
def test_adding_a_file_reads_it_now_and_says_so(core, dev, tmp_path, state):
    p = original(tmp_path)
    r = dev("POST", LIST, {"path": str(p)})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert set(r.json) == {"name", "path", "ok", "said", "documents"}
    assert r.json["name"] == "My Novel.txt" and r.json["ok"] is True and r.json["said"] == READY.format("My Novel.txt")
    assert canon(r.json["path"]) == canon(p)
    assert r.json["documents"] == [{"name": "My Novel.txt", "path": r.json["path"], "there": True, "line": "Your bot can find this.", "ok": True, "waiting": False,
                                    "named": False, "copy": False, "fiction": False}]
    assert [canon(x) for x in settings(state)["added"]] == [canon(p)] and "pinned" not in settings(state)
    assert p.read_text(encoding="utf-8") == WORDS                  # only ever read
    g = dev("GET", LIST)
    assert g.status_code == 200 and g.json == {"documents": r.json["documents"]} and g.headers["Cache-Control"] == "no-store"


def test_an_added_file_is_searched_and_not_named_in_chats(w, tmp_path):
    p = w.lib.add(original(tmp_path))
    assert w.lib.scan()["indexed"] >= 1 and w.lib.search("river")[0]["path"] == str(p)
    core = SimpleNamespace(state=w.state, bus=None)
    assert wiring.PinnedDocuments(core).names() == []
    got = pinned.read(w.lib, "My Novel")                           # read_pinned and show_passage stay pinned-only
    assert got["ok"] is False and "No one pinned document" in got["error"]


def test_adding_what_is_on_the_list_is_no_error_and_one_row(core, dev, tmp_path):
    p = original(tmp_path)
    first = dev("POST", LIST, {"path": str(p)}).json
    second = dev("POST", LIST, {"path": str(p).upper() if os.name == "nt" else str(p)})
    assert second.status_code == 200 and len(second.json["documents"]) == 1 and second.json["path"] == first["path"]
    assert dev("POST", "/v1/knowledge/pinned", {"path": str(p)}).status_code == 200
    third = dev("POST", LIST, {"path": str(p)})
    assert third.status_code == 200 and len(third.json["documents"]) == 1 and third.json["documents"][0]["named"] is True


def test_the_one_check_refuses_what_a_pin_refuses_and_nothing_goes_on_the_list(core, dev, tmp_path, state):
    (tmp_path / "keys.pem").write_text("x", encoding="utf-8")
    (tmp_path / "old.doc").write_bytes(b"\xd0\xcf")
    vault = Path(state) / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "x.txt").write_text("v", encoding="utf-8")
    own = Path(state) / "notes.txt"
    own.write_text("o", encoding="utf-8")
    for path, why in ((tmp_path / "keys.pem", folders.PIN_KIND), (tmp_path / "old.doc", folders.PIN_KIND),
                      (tmp_path / "none.txt", folders.PIN_GONE), (tmp_path, folders.PIN_FOLDER),
                      (vault / "x.txt", folders.PIN_VAULT), (own, folders.PIN_OWN), ("relative.txt", folders.PIN_WHOLE_PATH)):
        r = dev("POST", LIST, {"path": str(path)})
        assert r.status_code == 400 and r.json == {"error": why}, path
    for bad in ({}, {"path": ""}, {"path": 5}, {"path": "a\x00b.txt"}, {"path": "x" * 1001}, []):
        assert dev("POST", LIST, bad).status_code == 400
    assert dev("GET", LIST).json == {"documents": []}


def test_a_file_it_cant_read_says_why_and_stays_on_the_list(core, dev, tmp_path):
    p = tmp_path / "locked.docx"
    p.write_bytes(b"not a zip at all")
    r = dev("POST", LIST, {"path": str(p)})
    assert r.status_code == 200 and r.json["ok"] is False
    why = "This Word file can't be read. It may have a password."
    assert r.json["said"] == f"locked.docx was added, but your bot can't read it. {why}"
    assert r.json["documents"][0]["ok"] is False and r.json["documents"][0]["line"] == why


@pytest.mark.parametrize("text, said", [
    (f"Ring {PHONE} soon.", "a.txt is ready. 1 detail looks private, so your vault keeps it."),
    (f"Ring {PHONE} or {OTHER_PHONE} soon.", "a.txt is ready. 2 details look private, so your vault keeps them."),
])
def test_held_details_are_said_in_the_right_number(w, tmp_path, text, said):
    p = w.lib.add(original(tmp_path, "a.txt", text))
    assert w.lib.read_now(p) == (True, said)
    assert PHONE not in everything(w.lib)


def test_no_vault_says_only_that_it_is_ready(w, tmp_path):
    p = w.lib.pin(original(tmp_path, "a.txt", f"Ring {PHONE} soon."))
    assert w.lib.set_fiction(p, True) is True
    assert w.lib.read_now(p) == (True, "a.txt is ready.")


def test_a_read_that_breaks_says_so_and_keeps_it_listed(w, tmp_path, monkeypatch):
    p = w.lib.add(original(tmp_path, "a.txt"))
    monkeypatch.setattr(w.lib, "scan_file", lambda _p: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    assert w.lib.read_now(p) == (False, f"a.txt was added, but your bot can't read it. {folders.CANT_READ_LINE}")
    assert on_list(w.lib.documents_view(), p)


# -- the upload --------------------------------------------------------------------------------------------------------
def test_an_upload_says_what_happened_and_is_on_the_list_with_no_pin(core, phone, state):
    r = send(phone, "doc.txt")
    assert r.status_code == 200 and r.json["ok"] is True and r.json["said"] == READY.format("doc.txt")
    row = row_for(r.json["documents"], r.json["path"])
    assert row["copy"] is True and row["named"] is False and row["ok"] is True
    assert {"name", "path", "size", "replaced", "pinned", "line"} <= set(r.json)
    assert not (Path(state) / "settings" / "folders.json").exists() or "added" not in settings(state)


def test_a_replaced_copy_says_so_and_a_pin_on_it_stays(core, phone, state):
    first = send(phone, "doc.txt", b"Old words.").json
    assert phone.open("POST", "/v1/knowledge/pinned", body={"path": first["path"]}).status_code == 200
    r = send(phone, "doc.txt", b"Newer words.")
    assert r.json["said"] == "doc.txt replaces your earlier copy and " + READY.format("doc.txt")[len("doc.txt "):]
    assert r.json["pinned"] is True and r.json["replaced"] is True
    row = row_for(r.json["documents"], r.json["path"])
    assert row["named"] is True and row["copy"] is True


def test_an_upload_it_cant_read_is_saved_and_says_why(core, phone):
    r = send(phone, "locked.docx", b"not a zip at all")
    assert r.status_code == 200 and r.json["ok"] is False
    assert r.json["said"] == ("locked.docx was added, but your bot can't read it. "
                              "This Word file can't be read. It may have a password.")


# -- the copies --------------------------------------------------------------------------------------------------------
def test_the_folder_is_the_list_and_only_real_copies_count(w, tmp_path):
    good = [uploaded(w.state, n) for n in ("a.txt", "b.md", "C.DOCX", "d.pdf")]
    for name in ("half.part", "~$a.docx", "keys.pem", "x.exe", "noending"):
        uploaded(w.state, name)
    uploaded(w.state, "deep/inside.txt")
    (good[0].parent / "folder.txt").mkdir()
    assert sorted(canon(p) for p in w.lib.copies()) == sorted(canon(p) for p in good)
    assert not (Path(w.state) / "settings" / "folders.json").exists()     # a row comes from the disk alone


def test_a_copy_is_never_written_to_added(w):
    p = uploaded(w.state)
    w.lib.add(p)
    assert not (Path(w.state) / "settings" / "folders.json").exists() or "added" not in settings(w.state)
    assert len(w.lib.documents_view()) == 1


def test_words_lock_file_in_uploads_cant_be_added_through_the_side(w):
    with pytest.raises(FolderRefused):
        w.lib.add(uploaded(w.state, "~$a.docx"))
    assert w.lib.documents_view() == []


def test_unpin_on_a_copy_leaves_a_row_so_it_can_be_removed(core, dev, state):
    """The old Unpin trap: the row went and the file stayed, searchable, with no button."""
    p = uploaded(state)
    assert dev("POST", "/v1/knowledge/pinned", {"path": str(p)}).status_code == 200
    assert dev("POST", "/v1/knowledge/pinned/remove", {"path": str(p)}).status_code == 200
    row = row_for(dev("GET", LIST).json["documents"], p)
    assert row["copy"] is True and row["named"] is False
    assert dev("POST", REMOVE, {"path": str(p)}).json["said"] == GONE.format("sent.txt") and not p.exists()


def test_two_of_one_name_get_their_folder_and_none_is_lost(w, tmp_path):
    a = w.lib.add(original(tmp_path / "one", "same.txt"))         # both folders are called Writing
    b = w.lib.add(original(tmp_path / "two", "same.txt"))
    c = tmp_path / "Other" / "same.txt"
    c.parent.mkdir()
    c.write_text(WORDS, encoding="utf-8")
    w.lib.add(c)
    docs = w.lib.documents_view()
    assert len(docs) == 3 and on_list(docs, a) and on_list(docs, b) and len({r["name"] for r in docs}) == 3
    assert row_for(docs, c)["name"] == "Other\\same.txt"


# -- one list only -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("how", ["named", "old route"])
def test_pinning_takes_a_path_out_of_added(core, dev, tmp_path, state, how):
    p = original(tmp_path)
    dev("POST", LIST, {"path": str(p)})
    if how == "named":
        r = dev("POST", NAMED, {"path": str(p), "on": True})
        assert r.status_code == 200 and set(r.json) == {"documents"} and r.headers["Cache-Control"] == "no-store"
    else:
        assert dev("POST", "/v1/knowledge/pinned", {"path": str(p)}).status_code == 200
    assert settings(state)["added"] == [] and len(settings(state)["pinned"]) == 1
    assert dev("GET", LIST).json["documents"][0]["named"] is True


def test_named_off_goes_back_to_added_and_a_copy_does_not(core, dev, tmp_path, state):
    p, c = original(tmp_path), uploaded(state)
    for x in (p, c):
        dev("POST", "/v1/knowledge/pinned", {"path": str(x)})
        assert dev("POST", NAMED, {"path": str(x), "on": False}).status_code == 200
    assert settings(state)["pinned"] == [] and [canon(x) for x in settings(state)["added"]] == [canon(p)]
    docs = dev("GET", LIST).json["documents"]
    assert len(docs) == 2 and all(r["named"] is False for r in docs)
    assert dev("POST", NAMED, {"path": str(p), "on": False}).status_code == 200    # off again is no error


def test_named_on_a_copy_and_back(core, dev, state):
    c = uploaded(state)
    assert dev("POST", NAMED, {"path": str(c), "on": True}).json["documents"][0]["named"] is True
    assert dev("POST", NAMED, {"path": str(c), "on": True}).status_code == 200      # on again is no error
    assert len(settings(state)["pinned"]) == 1


def test_named_says_when_it_isnt_on_the_list_and_never_pins_a_stranger(core, dev, tmp_path, state):
    p = original(tmp_path)
    for on_ in (True, False):
        r = dev("POST", NAMED, {"path": str(p), "on": on_})
        assert r.status_code == 404 and r.json == {"error": "That file is not on your list."}
    for bad in ({"path": str(p), "on": "yes"}, {"path": str(p), "on": 1}, {"path": str(p)}, {"on": True}, []):
        assert dev("POST", NAMED, bad).status_code == 400
    assert dev("GET", LIST).json == {"documents": []}


def test_named_on_goes_through_the_one_check_again(w, tmp_path):
    p = original(tmp_path, "a.txt")
    write_settings(w.state, added=[str(tmp_path / "keys.pem")])
    (tmp_path / "keys.pem").write_text("k", encoding="utf-8")
    with pytest.raises(FolderRefused):
        w.lib.set_named(tmp_path / "keys.pem", True)
    assert w.lib.pinned() == [] and p.exists()


# -- remove: what she is told ------------------------------------------------------------------------------------------
def test_removing_an_original_leaves_the_file(core, dev, tmp_path, state):
    p = original(tmp_path)
    dev("POST", LIST, {"path": str(p)})
    r = dev("POST", REMOVE, {"path": str(p)})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store" and set(r.json) == {"said", "documents"}
    assert r.json == {"said": OFF.format("My Novel.txt"), "documents": []}
    assert p.read_text(encoding="utf-8") == WORDS and settings(state)["added"] == []
    assert dev("POST", REMOVE, {"path": str(p)}).status_code == 404


def test_removing_a_pinned_original_leaves_the_file_too(core, dev, tmp_path, state):
    p = original(tmp_path)
    dev("POST", "/v1/knowledge/pinned", {"path": str(p)})
    assert dev("POST", REMOVE, {"path": str(p)}).json["said"] == OFF.format("My Novel.txt")
    assert p.exists() and settings(state)["pinned"] == []


def test_removing_a_copy_deletes_it_and_its_words(w):
    p = uploaded(w.state)
    w.lib.scan()
    assert rows_of(w.lib, p) != (0, 0)
    assert w.lib.remove_document(p) == GONE.format("sent.txt")
    assert not p.exists() and rows_of(w.lib, p) == (0, 0) and w.lib.search("river") == []
    w.lib.scan()
    assert w.lib.search("river") == []                             # the walk can't bring it back


def test_what_isnt_on_the_list_is_a_404_and_nothing_moves(core, dev, tmp_path):
    p = original(tmp_path)
    for path in (p, tmp_path, tmp_path / "none.txt", "relative.txt"):
        r = dev("POST", REMOVE, {"path": str(path)})
        assert r.status_code == 404 and r.json == {"error": "That file is not on your list."}
    for bad in ({}, {"path": ""}, {"path": 5}, {"path": "a\x00b.txt"}, []):
        assert dev("POST", REMOVE, bad).status_code == 400
    assert p.exists()


def test_a_linked_folder_still_reads_it_and_she_is_told(w, tmp_path):
    p = original(tmp_path)
    w.lib.link(p.parent)
    w.lib.add(p)
    w.lib.scan()
    said = w.lib.remove_document(p)
    assert said == OFF.format("My Novel.txt") + f" Your bot still reads it, because it is inside a folder you added, {p.parent.resolve()}. Move it out of there to stop that."
    assert p.exists() and rows_of(w.lib, p) != (0, 0)              # the walk's rows stay


def test_the_data_folder_walk_still_reads_it_and_she_is_told(w):
    p = Path(w.state) / "data" / "notes" / "a.txt"
    p.parent.mkdir(parents=True)
    p.write_text(WORDS, encoding="utf-8")
    w.lib.add(p)
    said = w.lib.remove_document(p)
    assert said == OFF.format("a.txt") + f" Your bot still reads it, because it is inside your data folder, {w.lib.data_folder}. Move it out of there to stop that."
    assert p.exists()


def test_a_copy_is_never_said_to_be_still_read(w):
    assert "still reads" not in w.lib.remove_document(uploaded(w.state))


def test_what_the_vault_kept_is_said_and_stays(w, tmp_path):
    p = w.lib.add(original(tmp_path, "a.txt", f"Ring {PHONE} soon."))
    w.lib.read_now(p)
    assert w.lib._row(str(p.resolve()))[2] == 1                    # noqa: SLF001
    assert w.lib.remove_document(p) == OFF.format("a.txt") + " What your vault kept from it stays in your vault."
    c = uploaded(w.state, "c.txt", f"Ring {OTHER_PHONE} soon.")
    w.lib.read_now(c)
    assert w.lib.remove_document(c) == GONE.format("c.txt") + " What your vault kept from it stays in your vault."


def test_a_pinned_copy_already_gone_comes_off_with_no_error(w):
    p = uploaded(w.state)
    w.lib.pin(p)
    p.unlink()
    row = w.lib.documents_view()[0]
    assert row["there"] is False and row["copy"] is True and row["ok"] is False
    assert w.lib.remove_document(p) == GONE.format("sent.txt") and w.lib.documents_view() == []


# -- remove: trying to break it ----------------------------------------------------------------------------------------
def test_a_junction_planted_as_uploads_makes_nothing_a_copy(w, tmp_path, junction):
    """The trap in "inside canon(uploads)": a junction's own target is inside it by that test alone."""
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "novel.txt"
    victim.write_text(WORDS, encoding="utf-8")
    (Path(w.state) / "data").mkdir(parents=True)
    junction(outside, Path(w.state) / "data" / uploads.FOLDER)
    assert w.lib.copies() == []
    for path in (victim, Path(w.state) / "data" / uploads.FOLDER / "novel.txt"):
        assert w.lib.remove_document(path) is None
    assert victim.read_text(encoding="utf-8") == WORDS


def test_a_junction_from_uploads_to_elsewhere_in_the_data_folder_too(w, junction):
    data = Path(w.state) / "data"
    other = data / "My own"
    other.mkdir(parents=True)
    victim = other / "novel.txt"
    victim.write_text(WORDS, encoding="utf-8")
    junction(other, data / uploads.FOLDER)
    assert w.lib.copies() == [] and w.lib.remove_document(victim) is None and victim.exists()


def test_the_junction_with_her_pin_on_it_leaves_the_file(w, tmp_path, junction):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "novel.txt"
    victim.write_text(WORDS, encoding="utf-8")
    (Path(w.state) / "data").mkdir(parents=True)
    junction(outside, Path(w.state) / "data" / uploads.FOLDER)
    w.lib.pin(victim)
    assert w.lib.remove_document(victim).startswith(OFF.format("novel.txt")) and victim.exists()    # the walk goes through it


def test_a_junction_in_uploads_is_no_copy(w, tmp_path, junction):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text(WORDS, encoding="utf-8")
    junction(outside, Path(w.state) / "data" / uploads.FOLDER / "looks.txt")
    assert w.lib.copies() == []
    assert w.lib.remove_document(Path(w.state) / "data" / uploads.FOLDER / "looks.txt") is None
    assert (outside / "x.txt").exists() and (Path(w.state) / "data" / uploads.FOLDER / "looks.txt").exists()


def test_a_file_link_in_uploads_is_no_copy_and_its_target_stays(w, tmp_path):
    victim = original(tmp_path)
    link = Path(w.state) / "data" / uploads.FOLDER / "looks.txt"
    file_link(victim, link)
    assert w.lib.copies() == []
    assert w.lib.remove_document(link) is None and w.lib.remove_document(victim) is None
    assert victim.exists() and os.path.lexists(link)
    assert w.lib.handable(link) is None


def test_a_link_in_uploads_listed_by_hand_takes_off_the_list_and_deletes_nothing(w, tmp_path):
    victim = original(tmp_path)
    link = Path(w.state) / "data" / uploads.FOLDER / "looks.txt"
    file_link(victim, link)
    write_settings(w.state, added=[str(link)], pinned=[str(link)])
    assert w.lib.remove_document(link) == OFF.format("looks.txt")
    assert victim.exists() and os.path.lexists(link) and settings(w.state)["added"] == [] == settings(w.state)["pinned"]


@pytest.mark.parametrize("guard", ["by its link bit", "by its file mode"])
def test_a_link_is_no_copy_on_an_account_that_cant_make_one(w, monkeypatch, guard):
    """Stands in for the real file-link tests where Windows refuses this account a link: each guard alone says no."""
    from puffpai_core.zone1 import knowledge_folder
    import stat as stat_
    p = uploaded(w.state, "looks.txt")
    if guard == "by its link bit":
        monkeypatch.setattr(knowledge_folder, "is_link", lambda x: Path(x).name == "looks.txt")
    else:
        real = os.lstat
        monkeypatch.setattr(folders.os, "lstat", lambda x, **k: SimpleNamespace(st_mode=stat_.S_IFLNK) if Path(x).name == "looks.txt" else real(x, **k))
    assert w.lib.copies() == []
    monkeypatch.undo()
    assert p.exists()


def test_a_link_between_two_copies_deletes_the_real_one_and_no_more(w, tmp_path):
    real = uploaded(w.state, "real.txt")
    link = real.parent / "second.txt"
    file_link(real, link)
    assert [canon(p) for p in w.lib.copies()] == [canon(real)]
    assert w.lib.remove_document(link) == GONE.format("real.txt")
    assert not real.exists() and os.path.lexists(link)             # the name that was checked is the name that went


@pytest.mark.skipif(os.name != "nt", reason="Windows spellings of one path")
def test_a_copy_written_two_ways_is_one_copy(w):
    for spelled in (lambda p: str(p).upper(), lambda p: str(p).replace("\\", "/"), lambda p: str(p.parent / ".." / uploads.FOLDER / p.name)):
        p = uploaded(w.state)
        assert w.lib.remove_document(spelled(p)) == GONE.format("sent.txt"), spelled(p)
        assert not p.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows spellings of one path")
def test_an_original_written_two_ways_is_never_deleted(w, tmp_path):
    p = original(tmp_path)
    for spelled in (str(p).upper(), str(p).replace("\\", "/"), str(p.parent / ".." / p.parent.name / p.name)):
        w.lib.add(p)
        assert w.lib.remove_document(spelled) == OFF.format("My Novel.txt"), spelled
        assert p.exists() and w.lib.added() == []


@pytest.mark.skipif(os.name != "nt", reason="Windows device and stream spellings")
def test_device_and_stream_spellings_delete_nothing(w, tmp_path):
    p = uploaded(w.state)
    o = w.lib.add(original(tmp_path))
    for raw in (f"\\\\?\\{o}", f"{o}::$DATA", f"{o}:stream", f"\\\\.\\{o}"):
        assert w.lib.remove_document(raw) in (None, OFF.format("My Novel.txt")), raw   # at most the list, never the file
        w.lib.add(o)
    for raw in (f"\\\\?\\{p}", f"{p}::$DATA", f"{p}:stream", f"\\\\.\\{p}"):
        assert w.lib.remove_document(raw) is None, raw
    assert o.exists() and p.exists()


def test_a_data_folder_that_moved_makes_the_old_copy_an_original(w, tmp_path):
    p = uploaded(w.state)
    w.lib.pin(p)
    new = tmp_path / "new data"
    new.mkdir()
    write_settings(w.state, data=str(new))
    assert canon(p) not in [canon(c) for c in w.lib.copies()]
    row = row_for(w.lib.documents_view(), p)
    assert row["copy"] is False
    assert w.lib.remove_document(p).startswith(OFF.format("sent.txt")) and p.exists()


def test_a_data_folder_that_moved_finds_only_its_own_copies(w, tmp_path):
    old = uploaded(w.state, "old.txt")
    new = tmp_path / "new data"
    new.mkdir()
    write_settings(w.state, data=str(new))
    here = Path(uploads.save(w.lib, "here.txt", io.BytesIO(b"Words."), None)["path"])
    assert [canon(c) for c in w.lib.copies()] == [canon(here)]
    assert w.lib.remove_document(old) is None and old.exists()
    assert w.lib.remove_document(here) == GONE.format("here.txt") and not here.exists()


@pytest.mark.parametrize("where", ["vault", "state", "settings-unreadable"])
def test_a_data_setting_or_a_vault_it_cant_read_makes_nothing_a_copy(w, where):
    state = Path(w.state)
    if where == "vault":
        place = state / "vault"
        victim = uploaded(state, data=place)
        write_settings(state, data=str(place))
    elif where == "state":
        victim = uploaded(state, data=state)
        write_settings(state, data=str(state))
    else:
        victim = uploaded(state)
        (state / "settings").mkdir(exist_ok=True)
        (state / "settings" / "vault.json").write_text("{ not json", encoding="utf-8")
    assert w.lib.copies() == [] and w.lib.remove_document(victim) is None and victim.exists(), where


def test_a_pin_on_a_copy_the_check_refuses_now_takes_the_pin_and_leaves_the_file(w):
    victim = uploaded(w.state)
    w.lib.pin(victim)
    (Path(w.state) / "settings" / "vault.json").write_text("{ not json", encoding="utf-8")
    assert w.lib.remove_document(victim).startswith(OFF.format("sent.txt")) and victim.exists()


def test_a_file_in_uploads_a_pin_also_names_is_one_row_and_goes(w):
    p = uploaded(w.state)
    w.lib.pin(p)
    assert len(w.lib.documents_view()) == 1 and w.lib.documents_view()[0]["copy"] is True
    assert w.lib.remove_document(p) == GONE.format("sent.txt")
    assert not p.exists() and w.lib.pinned() == [] and w.lib.documents_view() == []


def test_added_by_hand_at_an_uploads_file_is_that_copy(w):
    p = uploaded(w.state)
    write_settings(w.state, added=[str(p)])
    assert len(w.lib.documents_view()) == 1
    assert w.lib.remove_document(p) == GONE.format("sent.txt")
    assert not p.exists() and settings(w.state)["added"] == []


def test_added_by_hand_at_an_original_is_never_deleted(w, tmp_path):
    p = original(tmp_path)
    write_settings(w.state, added=[str(p), str(p).upper() if os.name == "nt" else str(p)])
    assert len(w.lib.documents_view()) == 1
    assert w.lib.remove_document(p) == OFF.format("My Novel.txt") and p.exists() and settings(w.state)["added"] == []


def test_added_by_hand_one_folder_below_uploads_is_an_original(w):
    p = uploaded(w.state, "deep/inside.txt")
    write_settings(w.state, added=[str(p)])
    assert w.lib.documents_view()[0]["copy"] is False
    assert w.lib.remove_document(p).startswith(OFF.format("inside.txt")) and p.exists()


@pytest.mark.parametrize("rel", ["vault/x.txt", "settings/x.txt", "x.txt"])
def test_added_by_hand_at_a_place_the_check_refuses_isnt_read_or_sent_or_deleted(w, rel):
    p = Path(w.state) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"Ring {PHONE} soon.", encoding="utf-8")
    write_settings(w.state, added=[str(p)])
    assert canon(p) not in [canon(x) for x in w.lib._files()]      # noqa: SLF001
    assert w.lib.handable(p) is None
    row = w.lib.documents_view()[0]
    assert row["ok"] is False and row["line"] in (folders.PIN_VAULT, folders.PIN_OWN)
    assert w.lib.remove_document(p) == OFF.format("x.txt") and p.exists()


def test_removing_a_hard_link_takes_one_name_and_her_file_stays(w, tmp_path):
    victim = original(tmp_path)
    second = Path(w.state) / "data" / uploads.FOLDER / "second.txt"
    second.parent.mkdir(parents=True)
    os.link(victim, second)
    assert w.lib.remove_document(second) == GONE.format("second.txt")
    assert not second.exists() and victim.read_text(encoding="utf-8") == WORDS


def test_a_copy_held_open_stays_with_everything_as_it_was(w, monkeypatch):
    p = uploaded(w.state)
    w.lib.pin(p)
    w.lib.scan()

    def held(_path):
        raise PermissionError("in use")
    monkeypatch.setattr(folders.os, "unlink", held)
    with pytest.raises(LibraryBusy, match="close it and try again"):
        w.lib.remove_document(p)
    monkeypatch.undo()
    assert p.exists() and len(w.lib.pinned()) == 1 and rows_of(w.lib, p) != (0, 0)      # read again


@pytest.mark.skipif(os.name != "nt", reason="Windows holds an open file")
def test_a_copy_really_open_on_windows_says_so_by_the_route(core, dev, state):
    p = uploaded(state)
    with open(p, "rb"):
        r = dev("POST", REMOVE, {"path": str(p)})
    assert r.status_code == 409 and "close it and try again" in r.json["error"] and p.exists()
    assert on_list(dev("GET", LIST).json["documents"], p)


def test_a_copy_gone_between_the_list_and_the_delete_is_no_error(w, monkeypatch):
    p = uploaded(w.state)
    real = os.unlink

    def twice(path):
        real(path)
        real(path)
    monkeypatch.setattr(folders.os, "unlink", twice)
    assert w.lib.remove_document(p) == GONE.format("sent.txt")


def test_the_file_is_asked_about_once_more_right_before_the_delete(w, monkeypatch):
    """Turned into something else after the list was read: refused, and nothing is deleted."""
    p = uploaded(w.state)
    real = w.lib._is_copy                                          # noqa: SLF001
    calls = []

    def changed(path, place):
        calls.append(path)
        return real(path, place) and len(calls) == 1
    monkeypatch.setattr(w.lib, "_is_copy", changed)
    with pytest.raises(LibraryBusy):
        w.lib.remove_document(p)
    assert p.exists() and len(calls) == 2


# -- an uploads folder of her own, in a data folder she chose -------------------------------------------------------------
@pytest.fixture
def chosen(w, tmp_path):
    data = tmp_path / "Novels"
    data.mkdir()
    write_settings(w.state, data=str(data))
    return data


def test_her_own_uploads_folder_gets_no_rows_and_nothing_in_it_can_be_deleted(w, chosen):
    hers = uploaded(w.state, "draft.txt", data=chosen)
    assert w.lib.copies() == [] and w.lib.documents_view() == []
    assert w.lib.remove_document(hers) is None and hers.read_text(encoding="utf-8") == WORDS
    assert w.lib.set_named(hers, True) is False and w.lib.handable(hers) is None


def test_her_own_file_stays_an_ordinary_file_the_walk_reads(w, chosen):
    hers = uploaded(w.state, "draft.txt", data=chosen)
    w.lib.scan()
    assert [h["path"] for h in w.lib.search("river")] == [str(hers.resolve())]


def test_a_file_saved_there_is_recorded_listed_and_deletable_and_its_record_goes(w, chosen):
    hers = uploaded(w.state, "draft.txt", data=chosen)
    got = uploads.save(w.lib, "sent.txt", io.BytesIO(b"Sent words."), None)
    saved = Path(got["path"])
    assert settings(w.state)["copies"] == [canon(saved)]               # the whole real path
    assert [canon(c) for c in w.lib.copies()] == [canon(saved)]
    row = w.lib.documents_view()[0]
    assert row["copy"] is True and len(w.lib.documents_view()) == 1
    assert w.lib.remove_document(saved) == GONE.format("sent.txt")
    assert not saved.exists() and settings(w.state)["copies"] == [] and hers.exists()
    saved.write_text("Her own now, by that name.", encoding="utf-8")   # the record went: this one is hers
    assert w.lib.copies() == [] and w.lib.remove_document(saved) is None and saved.exists()


def test_a_second_save_records_once_and_replaces_its_own(w, chosen):
    uploads.save(w.lib, "sent.txt", io.BytesIO(b"First."), None)
    got = uploads.save(w.lib, "sent.txt", io.BytesIO(b"Second."), None)
    assert got["replaced"] is True and len(settings(w.state)["copies"]) == 1
    assert Path(got["path"]).read_bytes() == b"Second."


def test_an_upload_can_never_replace_her_own_file_there(core, phone, tmp_path):
    """It would destroy her file, and then record it as a copy Remove could delete."""
    data = tmp_path / "Novels"
    hers = uploaded(core.state, "draft.txt", data=data)
    write_settings(core.state, data=str(data))
    r = send(phone, "draft.txt", b"Other words.")
    assert r.status_code == 409 and r.json["code"] == "hers" and r.json["error"] == uploads.HERS
    assert hers.read_text(encoding="utf-8") == WORDS and list(hers.parent.iterdir()) == [hers]     # no half file
    assert "copies" not in settings(core.state)
    assert phone.open("POST", REMOVE, body={"path": str(hers)}).status_code == 404 and hers.exists()


def test_in_the_default_folder_an_earlier_copy_is_still_replaced(core, phone, state):
    earlier = uploaded(state, "draft.txt")
    r = send(phone, "draft.txt", b"Newer.")
    assert r.status_code == 200 and r.json["replaced"] is True and earlier.read_bytes() == b"Newer."


def test_a_default_data_folder_that_is_really_a_link_counts_as_one_she_chose(w, tmp_path, junction):
    mine = tmp_path / "Mine"
    hers = uploaded(w.state, "draft.txt", data=mine)
    junction(mine, Path(w.state) / "data")
    assert w.lib.copies() == []
    assert w.lib.remove_document(Path(w.state) / "data" / uploads.FOLDER / "draft.txt") is None and hers.exists()


def test_a_data_setting_naming_the_default_folder_is_the_default(w):
    p = uploaded(w.state)
    write_settings(w.state, data=str(Path(w.state) / "data"))
    assert [canon(c) for c in w.lib.copies()] == [canon(p)]


def test_a_moved_data_folder_cant_match_a_record_by_name(w, tmp_path):
    first = tmp_path / "First"
    first.mkdir()
    write_settings(w.state, data=str(first))
    old = Path(uploads.save(w.lib, "same.txt", io.BytesIO(b"Sent."), None)["path"])
    second = tmp_path / "Second"
    hers = uploaded(w.state, "same.txt", data=second)
    write_settings(w.state, data=str(second))
    assert w.lib.copies() == []
    assert w.lib.remove_document(hers) is None and hers.exists()
    assert w.lib.remove_document(old) is None and old.exists()         # and the old one is out of its uploads now


@pytest.mark.parametrize("rel", ["../elsewhere.txt", "beside.txt", "uploads/deep/below.txt"])
def test_a_hand_written_record_outside_the_uploads_folder_is_ignored(w, chosen, rel):
    victim = chosen / rel
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text(WORDS, encoding="utf-8")
    uploaded(w.state, "real.txt", data=chosen)
    write_settings(w.state, copies=[str(victim)])
    assert w.lib.copies() == [] and w.lib.remove_document(victim) is None and victim.exists()


def test_a_hand_written_record_outside_lists_nothing_even_for_an_earlier_data_folder(w, chosen):
    old = uploaded(w.state, "old.txt")                                 # the default folder's, not the current one
    write_settings(w.state, copies=[canon(old)])
    assert w.lib.copies() == [] and w.lib.remove_document(old) is None and old.exists()


def test_a_hand_written_record_inside_the_uploads_folder_names_that_file_as_a_copy(w, chosen):
    """The limit, on purpose: whoever can write settings can say a file right in that folder is a copy. It reaches no
    further: never a link, never below that folder, never outside it."""
    hers = uploaded(w.state, "draft.txt", data=chosen)
    write_settings(w.state, copies=[str(hers)])
    assert [canon(c) for c in w.lib.copies()] == [canon(hers)]
    assert w.lib.remove_document(hers) == GONE.format("draft.txt") and not hers.exists()


def test_a_junction_as_her_uploads_keeps_a_record_from_deleting_through_it(w, chosen, tmp_path, junction):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "novel.txt"
    victim.write_text(WORDS, encoding="utf-8")
    junction(outside, chosen / uploads.FOLDER)
    write_settings(w.state, copies=[canon(victim)])
    assert w.lib.copies() == [] and w.lib.remove_document(victim) is None and victim.exists()


@pytest.mark.parametrize("bad", [5, "x.txt", {"a": 1}, [5, None, ""], True])
def test_a_bad_copies_shape_makes_nothing_a_copy(w, chosen, bad):
    hers = uploaded(w.state, "draft.txt", data=chosen)
    write_settings(w.state, copies=bad)
    assert w.lib.copies() == [] and w.lib.remove_document(hers) is None and hers.exists()
    got = uploads.save(w.lib, "sent.txt", io.BytesIO(b"Sent."), None)  # and a good write mends it
    assert settings(w.state)["copies"] == [canon(got["path"])]


def test_her_file_pinned_there_comes_off_the_list_and_stays(w, chosen):
    hers = uploaded(w.state, "draft.txt", data=chosen)
    w.lib.pin(hers)
    assert w.lib.documents_view()[0]["copy"] is False
    said = w.lib.remove_document(hers)
    assert said == OFF.format("draft.txt") + f" Your bot still reads it, because it is inside your data folder, {w.lib.data_folder}. Move it out of there to stop that."
    assert hers.exists() and w.lib.pinned() == []


def test_her_file_added_there_is_an_original(w, chosen):
    hers = uploaded(w.state, "draft.txt", data=chosen)
    w.lib.add(hers)
    assert [canon(x) for x in w.lib.added()] == [canon(hers)] and w.lib.documents_view()[0]["copy"] is False
    assert w.lib.remove_document(hers).startswith(OFF.format("draft.txt")) and hers.exists()


def test_her_file_named_off_there_goes_to_added_and_a_recorded_copy_does_not(w, chosen):
    hers = uploaded(w.state, "draft.txt", data=chosen)
    w.lib.pin(hers)
    assert w.lib.set_named(hers, False) is True and [canon(x) for x in w.lib.added()] == [canon(hers)]
    saved = uploads.save(w.lib, "sent.txt", io.BytesIO(b"Sent."), None)["path"]
    w.lib.pin(saved)
    assert w.lib.set_named(saved, False) is True and [canon(x) for x in w.lib.added()] == [canon(hers)]


def test_her_pinned_file_gone_there_says_off_the_list_not_deleted(w, chosen):
    hers = uploaded(w.state, "draft.txt", data=chosen)
    w.lib.pin(hers)
    hers.unlink()
    assert w.lib.documents_view()[0]["copy"] is False
    assert w.lib.remove_document(hers) == OFF.format("draft.txt")


# -- a data folder setup made is BuildBot's own, not one she chose ---------------------------------------------------------
@pytest.fixture
def setup_folder(state, tmp_path, monkeypatch):
    """Setup's default for this bot, on a fake home: %USERPROFILE%\\PuffPAI Data\\<bot>."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    data = home / "PuffPAI Data" / Path(state).name
    write_settings(state, data=str(data))
    return data


def test_setup_and_the_library_share_one_rule_for_that_folder(state, setup_folder):
    from puffpai_core.wizard import engine
    from puffpai_core.wizard.fields import default_data_folder
    assert canon(default_data_folder(state)) == canon(setup_folder)
    made = Path(engine.__file__).read_text(encoding="utf-8")           # one rule: neither of them writes the path itself
    assert "PuffPAI Data" not in made and "default_data_folder(self.e.state, self.e.platform, self.e.env)" in made
    assert "PuffPAI Data" not in Path(folders.__file__).read_text(encoding="utf-8")


def test_an_old_copy_on_a_bot_setup_made_has_its_row_and_can_be_removed(core, dev, state, setup_folder):
    old = uploaded(state, "sent.txt", "Older words about the river.", data=setup_folder)
    row = row_for(dev("GET", LIST).json["documents"], old)
    assert row["copy"] is True and row["named"] is False
    r = dev("POST", REMOVE, {"path": str(old)})
    assert r.status_code == 200 and r.json["said"] == GONE.format("sent.txt") and not old.exists()


def test_an_old_copy_on_a_bot_setup_made_can_be_sent_again(core, phone, state, setup_folder):
    old = uploaded(state, "sent.txt", "Older words.", data=setup_folder)
    kept = uploaded(state, "kept.txt", "Older pinned words.", data=setup_folder)
    write_settings(state, pinned=[str(kept)])
    r = send(phone, "sent.txt", b"Newer words.")
    assert r.status_code == 200 and r.json["replaced"] is True and old.read_bytes() == b"Newer words."
    assert row_for(r.json["documents"], old)["copy"] is True
    r = send(phone, "kept.txt", b"Newer pinned words.")
    assert r.status_code == 200 and r.json["pinned"] is True and kept.read_bytes() == b"Newer pinned words."
    assert row_for(r.json["documents"], kept)["named"] is True and row_for(r.json["documents"], kept)["copy"] is True


@pytest.mark.parametrize("name", ["another bot", "Novels"])
def test_a_folder_beside_setups_is_still_one_she_chose(core, phone, dev, state, setup_folder, name):
    data = setup_folder.parent / name                                  # one bot's setup folder is no pass for another's
    hers = uploaded(state, "draft.txt", data=data)
    write_settings(state, data=str(data))
    assert dev("GET", LIST).json == {"documents": []}
    assert dev("POST", REMOVE, {"path": str(hers)}).status_code == 404 and hers.exists()
    assert send(phone, "draft.txt", b"Other words.").status_code == 409 and hers.read_text(encoding="utf-8") == WORDS


def test_setups_folder_that_is_really_a_link_counts_as_one_she_chose(state, w, setup_folder, tmp_path, junction):
    mine = tmp_path / "Mine"
    hers = uploaded(state, "draft.txt", data=mine)
    junction(mine, setup_folder)
    assert w.lib.copies() == [] and w.lib.remove_document(setup_folder / uploads.FOLDER / "draft.txt") is None and hers.exists()


# -- an upload through a link saves nothing -----------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["default", "chosen"])
def test_an_upload_never_overwrites_through_a_junction_planted_as_uploads(core, phone, dev, state, tmp_path, junction, where):
    data = Path(state) / "data" if where == "default" else tmp_path / "Novels"
    own = data / "My own"
    own.mkdir(parents=True)
    hers = own / "novel.txt"
    hers.write_text("hers", encoding="utf-8")
    if where == "chosen":
        write_settings(state, data=str(data))
    junction(own, data / uploads.FOLDER)
    for name, body in (("novel.txt", b"overwritten"), ("new.txt", b"new")):      # a new name too: never "was added" with no row
        r = send(phone, name, body)
        assert r.status_code == 400 and r.json == {"error": uploads.LINKED_PLACE, "code": "place"}, name
    assert hers.read_text(encoding="utf-8") == "hers" and [p.name for p in own.iterdir()] == ["novel.txt"]
    assert dev("GET", LIST).json == {"documents": []}
    assert where == "default" or "copies" not in settings(state)       # default: nothing has written that file at all


def test_an_upload_never_replaces_what_isnt_right_in_uploads(core, phone, state, tmp_path, monkeypatch):
    """Where a name in uploads leads elsewhere: on an account that can't make a file link, ``canon`` says so for it."""
    earlier = uploaded(state, "novel.txt", "hers")
    elsewhere = tmp_path / "elsewhere.txt"
    real = uploads.canon
    monkeypatch.setattr(uploads, "canon", lambda x: str(elsewhere) if Path(x).name == "novel.txt" else real(x))
    r = send(phone, "novel.txt", b"overwritten")
    assert r.status_code == 409 and r.json["code"] == "hers" and earlier.read_text(encoding="utf-8") == "hers"


def test_an_upload_over_a_real_file_link_in_uploads_is_refused(core, phone, state, tmp_path):
    victim = original(tmp_path, "novel.txt", "hers")
    file_link(victim, Path(state) / "data" / uploads.FOLDER / "novel.txt")
    assert send(phone, "novel.txt", b"overwritten").status_code == 409
    assert victim.read_text(encoding="utf-8") == "hers"


def test_a_junction_to_outside_says_the_same(core, phone, state, tmp_path, junction):
    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(state) / "data").mkdir(parents=True)
    junction(outside, Path(state) / "data" / uploads.FOLDER)
    r = send(phone, "new.txt", b"new")
    assert r.status_code == 400 and r.json["error"] == uploads.LINKED_PLACE and list(outside.iterdir()) == []


# -- a name that can't be stored ----------------------------------------------------------------------------------------
LONE = chr(0xd800)


def lone_settings(state, key, path=None):
    """As a hand edit or an old build could leave it: a real ``\\ud800`` in the JSON. A good file beside it stays listed."""
    text = json.dumps(path or "C:/x/a" + LONE + ".txt")
    where = Path(state) / "settings" / "folders.json"
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text('{"%s": %s}' % (key, text if key == "data" else "[" + text + "]"), encoding="utf-8")


@pytest.mark.parametrize("key", ["added", "pinned", "fiction", "copies", "linked", "data"])
def test_a_name_that_cant_be_stored_in_settings_stops_nothing(core, dev, state, tmp_path, key):
    lone_settings(state, key)
    assert dev("GET", LIST).json == {"documents": []}
    assert dev("GET", "/v1/knowledge/pinned").status_code == 200 and dev("GET", "/v1/knowledge/pinned").json["pinned"] == []
    assert dev("GET", "/v1/folders").status_code == 200
    assert dev("POST", REMOVE, {"path": "C:/x/a" + LONE + ".txt"}).status_code == 404
    assert dev("POST", NAMED, {"path": "C:/x/a" + LONE + ".txt", "on": True}).status_code == 404
    lib = Library(None, state)
    assert lib.handable("C:/x/a" + LONE + ".txt") is None and lib.known_documents() == []
    lib.scan()
    p = original(tmp_path)
    r = dev("POST", LIST, {"path": str(p)})                            # and a good add still works, and mends the list
    assert r.status_code == 200 and [d["name"] for d in r.json["documents"]] == [p.name]


@pytest.mark.parametrize("url", [LIST, "/v1/knowledge/pinned"])
def test_add_and_pin_refuse_such_a_path_before_anything_is_saved(core, dev, state, url):
    r = dev("POST", url, {"path": "C:/x/a" + LONE + ".txt"})
    assert r.status_code == 400 and r.json == {"error": folders.PIN_ODD_PATH}
    assert not (Path(state) / "settings" / "folders.json").exists()
    with pytest.raises(FolderRefused, match="written in a way"):
        Library(None, state).add("C:/x/a" + LONE + ".txt")
    with pytest.raises(FolderRefused, match="written in a way"):
        Library(None, state).pin("C:/x/a" + LONE + ".txt")


@pytest.fixture
def lone_file(tmp_path):
    """A real file with such a name, where this computer's disk allows one. Gone again after: other tools can't delete it."""
    made = []

    def make(folder, name="a" + LONE + ".txt"):
        Path(folder).mkdir(parents=True, exist_ok=True)
        p = Path(folder) / name
        try:
            p.write_text(WORDS, encoding="utf-8")
        except (OSError, UnicodeError):
            pytest.skip("this disk can't hold that name")
        made.append(p)
        return p
    yield make
    for p in made:
        p.unlink(missing_ok=True)


def test_a_real_file_with_such_a_name_is_refused_and_the_list_goes_on(core, dev, state, tmp_path, lone_file):
    p = lone_file(tmp_path / "Writing")
    keep = original(tmp_path)
    assert dev("POST", LIST, {"path": str(keep)}).status_code == 200
    for url in (LIST, "/v1/knowledge/pinned"):
        r = dev("POST", url, {"path": str(p)})
        assert r.status_code == 400 and r.json == {"error": folders.PIN_ODD_PATH}
    assert [d["name"] for d in dev("GET", LIST).json["documents"]] == [keep.name]
    assert dev("POST", REMOVE, {"path": str(p)}).status_code == 404 and p.exists()
    assert dev("GET", LIST).status_code == 200


@pytest.mark.parametrize("place", ["data", "uploads", "linked"])
def test_a_real_file_with_such_a_name_in_a_walked_folder_breaks_no_scan_and_no_page(core, dev, state, tmp_path, lone_file, place):
    data = Path(state) / "data"
    if place == "linked":
        mine = tmp_path / "Linked"
        p = lone_file(mine)
        assert dev("POST", "/v1/folders/link", {"path": str(mine)}).status_code == 200
    else:
        p = lone_file(data / uploads.FOLDER if place == "uploads" else data)
    uploaded(state, "good.txt")
    lib = Library(None, state)
    assert all(canon(x) != canon(p) for x in lib._files())             # noqa: SLF001
    assert [d["name"] for d in dev("GET", LIST).json["documents"]] == ["good.txt"]
    assert dev("GET", "/v1/folders").status_code == 200 and p.exists()


def test_a_record_that_cant_be_written_says_not_saved_and_leaves_no_file(core, phone, tmp_path, monkeypatch):
    data = tmp_path / "Novels"
    data.mkdir()
    write_settings(core.state, data=str(data))
    monkeypatch.setattr(Library, "record_copy", lambda *_a: (_ for _ in ()).throw(OSError("full")))
    r = send(phone, "sent.txt", b"Sent.")
    assert r.status_code == 507 and r.json["error"] == uploads.NOT_SAVED
    assert list((data / uploads.FOLDER).iterdir()) == []


def test_the_still_reads_line_names_her_books_folder(w, tmp_path):
    write_settings(w.state, knowledge=str(tmp_path / "Books"))
    p = fetched.root(w.state) / "notes.txt"
    p.parent.mkdir(parents=True)
    p.write_text(WORDS, encoding="utf-8")
    w.lib.add(p)
    assert w.lib.remove_document(p) == OFF.format("notes.txt") + " Your bot still reads it, because it is inside your books and downloads folder. Move it out of there to stop that."


def test_a_moved_bots_page_says_to_add_its_documents_again():
    page = (REPO / "packages" / "desktop" / "web" / "pages" / "settings.js").read_text(encoding="utf-8")
    assert 'bits("Add these documents again", d.documents)' in page


def test_the_words_on_the_button_never_say_pin():
    for line in (folders.PIN_KIND, folders.PIN_FOLDER, folders.PIN_VAULT, folders.PIN_OWN, folders.PIN_VAULT_UNREADABLE, folders.PIN_CANT_CHECK):
        assert "pin" not in line.lower().replace("puffpai", ""), line
    web = (REPO / "packages" / "desktop" / "web" / "logic.js").read_text(encoding="utf-8")
    assert folders.PIN_KIND in web


# -- D146: "no vault" stays pinned-only, and plain words never stay -----------------------------------------------------
def plain_doc(w, *, copy=False):
    p = uploaded(w.state, "novel.txt", PRIVATE) if copy else original(w.tmp, "novel.txt", PRIVATE)
    w.lib.pin(p)
    p = w.lib.pinned()[0]
    assert w.lib.set_fiction(p, True) is True
    w.lib.scan_file(p)
    assert PHONE in everything(w.lib) and KEY in everything(w.lib)     # the plain words really are there first
    return p


def gated(w, p):
    text = w.lib.indexed_text(p)
    return bool(text) and PHONE not in text and KEY not in text and "river" in text


def test_named_off_an_original_gates_it_at_once(w):
    p = plain_doc(w)
    assert w.lib.set_named(p, False) is True
    assert not w.lib.is_fiction(p) and settings(w.state).get("fiction") == []
    assert gated(w, p) and PHONE not in everything(w.lib) and KEY not in everything(w.lib)
    assert row_for(w.lib.documents_view(), p)["fiction"] is False
    assert [canon(x) for x in w.lib.added()] == [canon(p)]


def test_named_off_a_copy_gates_it_at_once(w):
    p = plain_doc(w, copy=True)
    assert w.lib.set_named(p, False) is True
    assert gated(w, p) and PHONE not in everything(w.lib) and w.lib.added() == [] and p.exists()


def test_named_off_with_the_file_gone_leaves_no_plain_words(w):
    p = plain_doc(w)
    p.unlink()
    assert w.lib.set_named(p, False) is True
    assert PHONE not in everything(w.lib) and KEY not in everything(w.lib) and rows_of(w.lib, p) == (0, 0)
    row = row_for(w.lib.documents_view(), p)
    assert row["there"] is False and row["line"] == GONE_LISTED


def test_named_off_where_the_check_now_refuses_leaves_no_plain_words(w):
    p = plain_doc(w)
    (Path(w.state) / "settings" / "vault.json").write_text("{ not json", encoding="utf-8")
    assert w.lib.set_named(p, False) is True
    assert PHONE not in everything(w.lib) and KEY not in everything(w.lib)


def test_named_off_a_document_with_no_plain_words_isnt_read_twice(w, tmp_path, monkeypatch):
    p = w.lib.pin(original(tmp_path, "a.txt", f"Ring {PHONE} soon."))
    w.lib.scan_file(p)
    monkeypatch.setattr(folders, "prepare", lambda *_a, **_k: pytest.fail("held again, for nothing"))
    assert w.lib.set_named(p, False) is True and PHONE not in everything(w.lib)


def test_remove_an_original_takes_its_plain_words_and_the_mark(w):
    p = plain_doc(w)
    assert w.lib.remove_document(p) == OFF.format("novel.txt")
    assert p.exists() and PHONE not in everything(w.lib) and KEY not in everything(w.lib) and rows_of(w.lib, p) == (0, 0)
    assert settings(w.state)["fiction"] == [] and w.lib.search("river") == []


def test_remove_a_copy_takes_its_plain_words_and_the_mark(w):
    p = plain_doc(w, copy=True)
    assert w.lib.remove_document(p) == GONE.format("novel.txt")
    assert not p.exists() and PHONE not in everything(w.lib) and settings(w.state)["fiction"] == []


def test_a_copy_sent_again_after_remove_is_gated_again(w):
    p = plain_doc(w, copy=True)
    w.lib.remove_document(p)
    got = uploads.save(w.lib, "novel.txt", io.BytesIO(PRIVATE.encode()), None)
    assert w.lib.read_now(Path(got["path"]))[0] is True
    assert PHONE not in everything(w.lib) and not w.lib.is_fiction(got["path"])


def test_remove_where_a_folder_still_reads_it_gates_it_on_the_next_walk(w, tmp_path):
    mine = tmp_path / "Linked"
    mine.mkdir()
    (mine / "novel.txt").write_text(PRIVATE, encoding="utf-8")
    w.lib.link(mine)
    p = w.lib.pin(mine / "novel.txt")
    w.lib.set_fiction(p, True)
    w.lib.scan_file(p)
    assert PHONE in everything(w.lib)
    assert "still reads it" in w.lib.remove_document(p)
    assert PHONE not in everything(w.lib)                              # at once, not at the next walk
    w.lib.scan()
    assert gated(w, p)


def test_added_stays_gated_even_with_a_mark_written_by_hand(w, tmp_path):
    """D146 is pinned-only: no route marks an added file, and a stale mark gives it no pass."""
    p = w.lib.add(original(tmp_path, "a.txt", PRIVATE))
    write_settings(w.state, fiction=[str(p)])
    assert w.lib.is_fiction(p) is False
    w.lib.scan_file(p)
    assert gated(w, p) and w.lib.documents_view()[0]["fiction"] is False
    assert w.lib.read_now(p)[1].startswith("a.txt is ready. ") and "private" in w.lib.read_now(p)[1]


def test_a_linked_file_with_a_mark_written_by_hand_is_gated_too(w, tmp_path):
    mine = tmp_path / "Linked"
    mine.mkdir()
    (mine / "a.txt").write_text(PRIVATE, encoding="utf-8")
    w.lib.link(mine)
    write_settings(w.state, fiction=[str(mine / "a.txt")])
    w.lib.scan()
    assert PHONE not in everything(w.lib)


def test_the_no_vault_switch_refuses_a_document_that_isnt_pinned(core, dev, tmp_path, state):
    p, c = original(tmp_path), uploaded(state)
    dev("POST", LIST, {"path": str(p)})
    for x in (p, c):
        r = dev("POST", "/v1/knowledge/pinned/fiction", {"path": str(x), "on": True})
        assert r.status_code == 404
    assert settings(state).get("fiction", []) == []
    assert all(r["fiction"] is False for r in dev("GET", LIST).json["documents"])


def test_plain_words_left_behind_by_a_crash_are_read_again_on_the_next_scan(w, tmp_path):
    """Her mark went, and the process stopped before the rows did. The file has not changed, so nothing else would."""
    p = w.lib.add(original(tmp_path, "a.txt", PRIVATE))
    st = p.stat()
    w.lib._index(str(p.resolve()), st, PRIVATE, held=0, top="P0", reason=FICTION_REASON)   # noqa: SLF001
    assert PHONE in everything(w.lib)
    w.lib.scan()
    assert PHONE not in everything(w.lib) and gated(w, p)


def crash_before_the_index_clears(monkeypatch):
    """The process stops right after her list is saved, before the index is cleared."""
    def stops(*_a, **_k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(Library, "_leave_index", stops)


@pytest.mark.parametrize("step", ["remove_document", "unpin", "set_named"])
def test_a_crash_between_the_list_and_the_index_leaves_no_plain_words_after_the_next_scan(w, monkeypatch, step):
    p = plain_doc(w)
    crash_before_the_index_clears(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        getattr(w.lib, step)(p, False) if step == "set_named" else getattr(w.lib, step)(p)
    monkeypatch.undo()
    assert PHONE in everything(w.lib)                                  # the crash really did leave them
    zone1_gate.bind(w.a)
    lib = Library(w.a, w.state)                                        # a fresh start
    lib.scan()
    assert PHONE not in everything(lib) and KEY not in everything(lib)
    if step == "set_named":                                            # still on her list: gated again
        assert gated(w, p)
    else:
        assert lib.search("orchard") == [] and rows_of(lib, p) == (0, 0) and p.exists()


def test_remove_during_a_read_that_holds_no_lock_leaves_no_plain_words(w, monkeypatch):
    """A chat turn's read and the old /pinned/fiction route scan with no lock. The remove lands during that read."""
    p = plain_doc(w)
    w.lib.forget(p)
    real = Library._index
    said = []

    def during(self, key, st, store, **k):
        if not said:
            said.append(self.remove_document(p))
        return real(self, key, st, store, **k)
    monkeypatch.setattr(Library, "_index", during)
    w.lib.scan_file(p)
    monkeypatch.setattr(Library, "_index", real)
    assert said == [OFF.format("novel.txt")] and PHONE in everything(w.lib)     # the bad order really happened
    w.lib.scan()
    assert PHONE not in everything(w.lib) and w.lib.search("orchard") == [] and w.lib.documents_view() == []


def test_the_old_unpin_during_a_scan_leaves_no_plain_words_after_the_next(w, monkeypatch):
    p = plain_doc(w)
    w.lib.forget(p)
    real = Library._index
    done = []

    def during(self, key, st, store, **k):
        if not done:
            done.append(self.unpin(p))
        return real(self, key, st, store, **k)
    monkeypatch.setattr(Library, "_index", during)
    with SCAN_LOCK:
        w.lib.scan()
    monkeypatch.setattr(Library, "_index", real)
    w.lib.scan()
    assert done == [True] and PHONE not in everything(w.lib) and KEY not in everything(w.lib)


def test_the_sweep_leaves_a_document_that_is_still_no_vault(w):
    p = plain_doc(w)
    got = w.lib.scan()
    assert got["indexed"] == 0 and got["unchanged"] == 1               # not taken out and read again
    assert PHONE in everything(w.lib) and w.lib.is_fiction(p)


def test_the_sweep_leaves_a_no_vault_document_that_is_away_right_now(w):
    p = plain_doc(w)
    away = p.with_name("away.txt")
    os.replace(p, away)
    w.lib.scan()
    assert PHONE in everything(w.lib)                                  # its pin and its mark stand, and it comes back as it was
    os.replace(away, p)
    assert w.lib.scan()["unchanged"] == 1


def test_still_no_vault_is_unchanged_and_isnt_read_again(w):
    p = plain_doc(w)
    assert w.lib.scan_file(p)["status"] == "unchanged" and PHONE in everything(w.lib)


# -- a scan on its thread ----------------------------------------------------------------------------------------------
def test_a_scan_holding_the_lock_refuses_with_nothing_changed(core, dev, tmp_path, monkeypatch, state):
    p = original(tmp_path)
    c = uploaded(state)
    dev("POST", LIST, {"path": str(p)})
    monkeypatch.setattr(folders, "SCAN_WAIT", 0.01)
    with SCAN_LOCK:
        for path, url, body in ((p, REMOVE, {}), (c, REMOVE, {}), (p, NAMED, {"on": True})):
            r = dev("POST", url, {"path": str(path), **body})
            assert r.status_code == 409 and r.json == {"error": folders.BUSY_LINE}
    assert p.exists() and c.exists() and len(settings(state)["added"]) == 1


def test_remove_waits_for_a_scan_on_that_very_document(w, monkeypatch):
    """A scan that had already read a "no vault" document must not put its words back after the remove."""
    p = plain_doc(w)
    w.lib.forget(p)
    reading, go_on = threading.Event(), threading.Event()
    real = pinned.read_document

    def slow(path):
        got = real(path)
        reading.set()
        assert go_on.wait(20)
        return got
    monkeypatch.setattr(pinned, "read_document", slow)

    def scan():
        with SCAN_LOCK:
            w.lib.scan_file(p)
    thread = threading.Thread(target=scan)
    thread.start()
    assert reading.wait(20)
    said: list = []
    remover = threading.Thread(target=lambda: said.append(w.lib.remove_document(p)))
    remover.start()
    remover.join(0.3)
    assert remover.is_alive() and said == []                           # it waits
    go_on.set()
    thread.join(20)
    remover.join(20)
    assert said == [OFF.format("novel.txt")]
    assert PHONE not in everything(w.lib) and KEY not in everything(w.lib) and rows_of(w.lib, p) == (0, 0)


def test_removed_while_a_route_reads_it_leaves_no_words(w, tmp_path, monkeypatch):
    """A route doesn't wait out a long scan, so it can read as she removes. Its words go again."""
    p = w.lib.add(original(tmp_path))
    real = w.lib.scan_file

    def removed_meanwhile(path):
        got = real(path)
        data = w.lib._settings()                                       # noqa: SLF001 - the remove, between the read and the write
        data["added"] = []
        w.lib._save(data)                                              # noqa: SLF001
        return got
    monkeypatch.setattr(w.lib, "scan_file", removed_meanwhile)
    assert w.lib.read_now(p)[0] is False
    assert rows_of(w.lib, p) == (0, 0) and w.lib.search("river") == []


# -- each read checks again --------------------------------------------------------------------------------------------
def test_an_added_path_that_leads_somewhere_else_now_isnt_read_or_sent(w, tmp_path):
    p = w.lib.add(original(tmp_path, "a.txt"))
    keys = tmp_path / "keys.pem"
    keys.write_text("hers", encoding="utf-8")
    p.unlink()
    file_link(keys, p)
    assert canon(p) not in [canon(x) for x in w.lib._files()]          # noqa: SLF001
    assert w.lib.handable(p) is None and w.lib.handable(keys) is None
    row = w.lib.documents_view()[0]
    assert row["ok"] is False and row["line"] == folders.PIN_KIND


def test_an_added_file_gone_keeps_its_row_and_comes_back(w, tmp_path):
    p = w.lib.add(original(tmp_path, "a.txt"))
    w.lib.scan()
    moved = tmp_path / "away.txt"
    os.replace(p, moved)
    assert w.lib.handable(p) is None
    row = w.lib.documents_view()[0]
    assert row["there"] is False and row["line"] == GONE_LISTED and row["ok"] is False
    os.replace(moved, p)
    assert w.lib.documents_view()[0]["ok"] is True


def test_a_junction_on_the_way_to_an_added_file_is_checked_again(w, tmp_path, junction):
    vault = Path(w.state) / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "a.txt").write_text(f"Ring {PHONE} soon.", encoding="utf-8")
    junction(vault, tmp_path / "friendly")
    write_settings(w.state, added=[str(tmp_path / "friendly" / "a.txt")])
    assert canon(vault / "a.txt") not in [canon(x) for x in w.lib._files()]    # noqa: SLF001
    assert w.lib.handable(tmp_path / "friendly" / "a.txt") is None
    assert w.lib.handable(vault / "a.txt") is None
    assert (vault / "a.txt").exists()


# -- handable and known_documents --------------------------------------------------------------------------------------
def test_handable_is_exact_membership(w, tmp_path):
    p = w.lib.add(original(tmp_path, "a.txt"))
    c = uploaded(w.state, "c.txt")
    pin = w.lib.pin(original(tmp_path, "pin.txt"))
    near = original(tmp_path, "a.txt.txt")
    for yes in (p, c, pin):
        assert w.lib.handable(yes) is not None, yes
    other = uploaded(w.state, "deep/below.txt")
    for no in (near, tmp_path / "Writing", c.parent, other, Path(w.state) / "data" / "uploads" / "none.txt", "relative.txt"):
        assert w.lib.handable(no) is None, no
    w.lib.remove_document(p)
    assert w.lib.handable(p) is None


def test_handable_follows_links_to_a_listed_file(w, tmp_path, junction):
    p = w.lib.add(original(tmp_path, "a.txt"))
    junction(p.parent, tmp_path / "another way")
    assert w.lib.handable(tmp_path / "another way" / "a.txt") is not None


def test_a_listed_file_is_never_handable_from_a_vault(w):
    vault = Path(w.state) / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "a.txt").write_text("v", encoding="utf-8")
    write_settings(w.state, added=[str(vault / "a.txt")], pinned=[str(vault / "a.txt")])
    assert w.lib.handable(vault / "a.txt") is None


def test_a_vault_it_cant_read_hands_nothing(w, tmp_path):
    p = w.lib.add(original(tmp_path, "a.txt"))
    c = uploaded(w.state)
    (Path(w.state) / "settings" / "vault.json").write_text("{ not json", encoding="utf-8")
    assert w.lib.handable(p) is None and w.lib.handable(c) is None


def test_chat_can_name_added_documents_and_copies_each_once(w, tmp_path):
    p = w.lib.add(original(tmp_path, "a.txt"))
    c = uploaded(w.state, "c.txt")
    w.lib.pin(c)
    known = w.lib.known_documents()
    assert sorted(canon(x) for x in known) == sorted([canon(p), canon(c)])
    assert pinned.named(known, "c") is not None


def test_an_added_document_and_a_copy_can_go_to_an_app_by_path_and_a_neighbour_cant(core, phone, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(send_file, "hand", lambda _c, _r, pid, name, data: sent.append((pid, name, data)) or "It has it.")
    p = original(tmp_path, "a.txt")
    assert phone.open("POST", LIST, body={"path": str(p)}).status_code == 200
    copy = send(phone, "doc.txt").json["path"]
    assert phone.open("POST", "/v1/packages/some-app/send", body={"path": str(p)}).status_code == 200
    assert phone.open("POST", "/v1/packages/some-app/send", body={"path": copy}).status_code == 200
    assert [x[1] for x in sent] == ["a.txt", "doc.txt"]
    near = original(tmp_path, "b.txt")
    r = phone.open("POST", "/v1/packages/some-app/send", body={"path": str(near)})
    assert r.status_code == 400 and r.json == {"error": "Only a document on your list or a file in a folder you linked can be sent this way."}


# -- a bad shape in folders.json ---------------------------------------------------------------------------------------
BAD = [{"added": 5}, {"added": "x.txt"}, {"added": {"a": 1}}, {"added": [5, None, {"p": 1}, [], "", "  ", "a\x00b.txt"]},
       {"added": True}, {"pinned": 5}, {"pinned": [5, None]}, {"fiction": 5}, {"fiction": [5]}, {"linked": 5}, {"linked": [5, None]},
       {"data": 5}, {"data": ["x"]}, {"data": "a\x00b"}, {"copies": 5}, {"copies": "x.txt"}, {"copies": {"a": 1}}, {"copies": [5, None, ""]}]


@pytest.mark.parametrize("shape", BAD, ids=lambda s: json.dumps(s)[:40])
def test_a_bad_shape_stops_no_page_and_no_turn(core, dev, state, tmp_path, shape):
    write_settings(state, **shape)
    assert dev("GET", LIST).status_code == 200
    assert dev("GET", "/v1/knowledge/pinned").status_code == 200
    assert dev("GET", "/v1/folders").status_code == 200
    assert dev("POST", REMOVE, {"path": str(tmp_path / "a.txt")}).status_code == 404
    assert dev("POST", NAMED, {"path": str(tmp_path / "a.txt"), "on": True}).status_code == 404
    lib = Library(None, state)
    lib._files()                                                       # noqa: SLF001
    lib.known_documents()
    assert lib.handable(tmp_path / "a.txt") is None
    assert folders.pinned_paths(state) == [] or shape.get("data")
    p = original(tmp_path)
    assert dev("POST", LIST, {"path": str(p)}).status_code == 200      # and a good write mends that list


@pytest.mark.parametrize("text", ["[]", "5", '"x"', "null", "{ not json", "", "[" * 100000], ids=["list", "number", "string", "null", "junk", "empty", "deep"])
def test_a_folders_file_of_another_shape_stops_nothing(core, dev, state, tmp_path, text):
    path = Path(state) / "settings" / "folders.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert dev("GET", LIST).json == {"documents": []}
    assert dev("POST", REMOVE, {"path": str(tmp_path / "a.txt")}).status_code == 404
    assert folders.pinned_paths(state) == [] == folders.added_paths(state) == folders.fiction_paths(state)
    assert wiring.PinnedDocuments(SimpleNamespace(state=state, bus=None)).names() == []


def test_a_link_that_leads_back_to_itself_stops_no_page(core, dev, state, tmp_path):
    loop = tmp_path / "loop.txt"
    try:
        os.symlink(loop, loop)
    except OSError:
        pytest.skip("this account can't make file links")
    write_settings(state, added=[str(loop)])
    r = dev("GET", LIST)
    assert r.status_code == 200 and r.json["documents"][0]["ok"] is False
    assert dev("POST", REMOVE, {"path": str(loop)}).status_code == 200
    assert os.path.lexists(loop)


# -- the picker --------------------------------------------------------------------------------------------------------
def at_the_bots_computer(core, body):
    key = core.tokens.issue_device("a device")
    return core.app.test_client().post(PICK, json=body, headers=local(key), environ_base={"REMOTE_ADDR": "127.0.0.1"})


def test_many_files_come_back_as_paths(core, on, monkeypatch):  # noqa: F811
    monkeypatch.setattr(send_file, "can_pick", lambda: True)
    monkeypatch.setattr(send_file, "pick", lambda kind: "C:\\a b\\one.txt\nC:\\two.docx\r\nC:\\old.doc\n" if kind == "files" else pytest.fail(kind))
    r = at_the_bots_computer(core, {"kind": "files"})
    assert r.status_code == 200 and r.json == {"paths": ["C:\\a b\\one.txt", "C:\\two.docx", "C:\\old.doc"]}


def test_a_closed_dialog_is_no_paths(core, on, monkeypatch):  # noqa: F811
    monkeypatch.setattr(send_file, "can_pick", lambda: True)
    monkeypatch.setattr(send_file, "pick", lambda kind: "")
    assert at_the_bots_computer(core, {"kind": "files"}).json == {"paths": []}


def test_files_from_a_device_or_with_no_dialog_go_by_upload_as_file_does(core, phone, on, monkeypatch):  # noqa: F811
    monkeypatch.setattr(send_file, "can_pick", lambda: True)
    monkeypatch.setattr(send_file, "pick", lambda kind: pytest.fail("a dialog opened on the bot's computer"))
    one, many = (phone.open("POST", PICK, body={"kind": k}).json for k in ("file", "files"))
    assert many == one and many["upload"] is True and many["typed"] is True
    monkeypatch.setattr(send_file, "can_pick", lambda: False)
    assert at_the_bots_computer(core, {"kind": "files"}).json == one


def test_one_dialog_at_a_time_and_the_words_for_files_say_no_typed_path(core, on, monkeypatch):  # noqa: F811
    monkeypatch.setattr(send_file, "can_pick", lambda: True)
    def busy(_kind):
        raise send_file.SendRefused("A window to pick from is open already. Look for it on this computer.")
    monkeypatch.setattr(send_file, "pick", busy)
    r = at_the_bots_computer(core, {"kind": "files"})
    assert r.status_code == 409 and "open already" in r.json["error"]
    assert "Type the path" not in send_file._no_window("files") and "Type the path" in send_file._no_window("file")   # noqa: SLF001


def test_the_old_kinds_are_as_they_were(core, on, monkeypatch):  # noqa: F811
    monkeypatch.setattr(send_file, "can_pick", lambda: True)
    monkeypatch.setattr(send_file, "pick", lambda kind: "C:/" + kind)
    assert at_the_bots_computer(core, {"kind": "file"}).json == {"path": "C:/file"}
    assert at_the_bots_computer(core, {"kind": "folder"}).json == {"path": "C:/folder"}
    assert at_the_bots_computer(core, {"kind": "nope"}).status_code == 400
    assert "Multiselect=$false" in send_file.pick_script("file") and "All files" not in send_file.pick_script("file")
    assert "$d.FileName)" in send_file.pick_script("file") and "$d.SelectedPath)" in send_file.pick_script("folder")


def test_the_files_script_takes_many_and_offers_all_files():
    script = send_file.pick_script("files")
    assert "Multiselect=$true" in script and "All files (*.*)|*.*" in script and "FileNames -join [char]10" in script


# -- the other lines ---------------------------------------------------------------------------------------------------
def test_a_word_or_pdf_at_the_top_of_the_store_gets_a_row_and_stays_where_it_is(core, dev, state, tmp_path, junction):
    store = fetched.root(state)
    store.mkdir(parents=True)
    for name in ("novel.docx", "Scan.PDF"):
        (store / name).write_bytes(b"x")
    (store / "notes.txt").write_text("x", encoding="utf-8")
    (store / "~$novel.docx").write_bytes(b"x")
    (store / "folder.pdf").mkdir()
    r = dev("GET", "/v1/knowledge/sources")
    assert r.status_code == 200
    assert sorted(x["name"] for x in r.json["waiting"]) == ["Scan.PDF", "novel.docx"]
    assert {x["line"] for x in r.json["waiting"]} == {"Word and PDF files aren't read here. Use Add documents at the top of this page."}
    assert books.dropped(state) == []                                  # look never takes these in
    assert books.look(state, None, None) is False
    books.learn_now(state, None, None)
    assert (store / "novel.docx").exists() and (store / "Scan.PDF").exists() and sorted(x["name"] for x in dev("POST", "/v1/knowledge/books/look", {}).json["waiting"]) == ["Scan.PDF", "novel.docx"]


def test_a_dropped_zim_keeps_its_own_row_beside_them(state):
    store = fetched.root(state)
    store.mkdir(parents=True)
    (store / "a.zim").write_bytes(b"x")
    (store / "a.docx").write_bytes(b"x")
    got = {x["name"]: x["line"] for x in books.waiting(state)}
    assert got == {"a.zim": books.WAITING, "a.docx": books.DOCUMENT_HERE} and [p.name for p in books.dropped(state)] == ["a.zim"]


def test_a_gone_store_has_no_rows(state):
    assert books.waiting(state) == []


def test_the_brain_is_told_the_new_way():
    from puffpai_core.zone2 import concierge as cg
    assert cg.ADD_DOCUMENTS == ("The owner adds her documents on the Knowledge page, with the Add documents button, or adds a whole "
                                "folder there. There is no other place for them.")


def test_a_moved_bot_keeps_added_names_only(state, tmp_path):
    make(state)
    write_settings(state, added=[str(original(tmp_path / "secret place", "Added Novel.docx"))])
    reg, pk = _world(tmp_path)
    out = tmp_path / "editor-bot"
    bot_move.export(state, reg, pk, out)
    raw = (out / "puffpai-bot.json").read_text(encoding="utf-8")
    assert json.loads(raw)["knowledge"]["documents"] == ["Added Novel.docx"] and "secret place" not in raw
    said = bot_move.lines_for(bot_move.read(out))
    assert said["documents"] == ["Added Novel.docx"]
    assert "Add these documents again: Added Novel.docx." in moved_lines(said)
    assert not (Path(state) / "settings" / bot_move.LIST).exists()     # nothing comes back on its own


@pytest.mark.parametrize("know", [{}, {"documents": 5}, {"documents": "abc"}, {"documents": None}, None])
def test_an_older_or_odd_bundle_names_no_documents(know):
    said = bot_move.lines_for({"knowledge": know, "apps": []})
    assert said["documents"] == [] and not any(line.startswith("Add these documents") for line in moved_lines(said))


def test_an_added_file_gets_what_a_pinned_file_gets_from_owner_files_and_no_more(w, tmp_path):
    p = original(tmp_path, "a.txt")
    write_settings(w.state, pinned=[str(p)])
    as_pinned = owner_files.where(w.state, p)
    write_settings(w.state, pinned=[], added=[str(p)])
    assert owner_files.where(w.state, p) == as_pinned == {"line": owner_files.OUTSIDE}


# -- who may -----------------------------------------------------------------------------------------------------------
def test_writes_are_a_paired_devices_and_the_read_is_the_owners(core, tmp_path, state):
    p = original(tmp_path)
    client = core.app.test_client()
    dev_ = local(core.tokens.issue_device("a device"))
    owner, package = local(core.tokens.issue_owner_key()), local(core.tokens.issue_package_token("some-app"))
    toolsmith = local(core.tokens.issue_toolsmith("b1"))
    assert client.post(LIST, json={"path": str(p)}, headers=dev_).status_code == 200
    c = uploaded(state)
    for url, body in ((LIST, {"path": str(p)}), (NAMED, {"path": str(p), "on": True}), (REMOVE, {"path": str(c)})):
        for who in (owner, package, toolsmith):
            assert client.post(url, json=body, headers=who).status_code == 403, url
        assert client.post(url, json=body).status_code in (401, 403), url
    assert c.exists() and client.get(LIST, headers=owner).status_code == 200
    assert client.get(LIST, headers=package).status_code == 403 and client.get(LIST).status_code in (401, 403)


def test_a_paired_device_needs_both_halves_and_the_right_origin_to_delete(core, phone):
    got = send(phone, "doc.txt").json
    saved = Path(got["path"])
    assert phone.open("POST", REMOVE, body={"path": got["path"]}, second=False).status_code == 401
    assert phone.open("POST", REMOVE, body={"path": got["path"]}, cookie=False).status_code == 401
    r = phone.open("POST", REMOVE, body={"path": got["path"]}, origin=False, headers={"Origin": "http://evil.example", "X-PuffPAI": "1"})
    assert r.status_code == 403 and saved.exists()
    assert phone.open("POST", REMOVE, body={"path": got["path"]}).json["said"] == GONE.format("doc.txt") and not saved.exists()


def test_the_routes_are_in_the_contract(core):
    import yaml
    spec = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))["paths"]
    assert spec[LIST]["get"]["x-access"] == "owner" and spec[LIST]["post"]["x-access"] == "owner_device"
    assert spec[NAMED]["post"]["x-access"] == "owner_device" and spec[REMOVE]["post"]["x-access"] == "owner_device"
    assert "files" in spec[PICK]["post"]["description"]
    view = core.app.view_functions
    assert getattr(view["knowledge.knowledge_documents"], "puffpai_access") == "owner"
    for name in ("knowledge_add_document", "knowledge_document_named", "knowledge_remove_document"):
        assert getattr(view[f"knowledge.{name}"], "puffpai_access") == "owner_device"
