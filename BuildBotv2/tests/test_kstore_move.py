"""D103: her books and downloads in a folder or drive of her own (zone1.fetched.folder_for, zone1.knowledge_store).

Small files in tmp folders. A second drive is faked by making the two places look like different volumes, or by
a drive letter that is not there. The folder check itself is in test_kstore_folder_check.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from puffpai_core.zone1 import fetched, knowledge_folder as kf, knowledge_store as ks
from puffpai_core.zone1.folders import Library


class FakeArchivist:
    """Nothing held: these files are public text, or plain words with no private details."""

    def __init__(self, state):
        self.state = state


@pytest.fixture(autouse=True)
def fake_computer(tmp_path, monkeypatch):
    """Home, AppData and the other bots are fakes here: her real bots are never read, wherever TEMP is."""
    from puffpai_core.zone1 import knowledge_folder as kf
    users = tmp_path / "c" / "Users"
    home = users / "me"
    (home / "AppData" / "Local").mkdir(parents=True)
    (home / "AppData" / "Roaming").mkdir()
    for name, value in {"USERPROFILE": home, "HOME": home, "LOCALAPPDATA": home / "AppData" / "Local",
                        "APPDATA": home / "AppData" / "Roaming", "SystemDrive": tmp_path / "c",
                        "PUBLIC": users / "Public"}.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setattr(kf, "_LITERAL_USERS", (str(users),))


def _set(state: Path, **kw) -> None:
    p = state / "settings" / "folders.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data.update(kw)
    p.write_text(json.dumps(data), encoding="utf-8")


def _meta(title="Moby Dick"):
    return {"source": "gutenberg", "title": title, "url": "https://www.gutenberg.org/1", "license": "Public domain",
            "fetched_at": "2026-10-30T00:00:00Z"}


@pytest.fixture
def bot(tmp_path):
    state = tmp_path / "bots" / "bot1"
    (state / "settings").mkdir(parents=True)
    (state / "index").mkdir()
    (state / "data").mkdir()
    return state


def _lib(state):
    return Library(FakeArchivist(state), state)


def _seed(state, n=3):
    paths = [fetched.save(state, "gutenberg", f"book-{i}", f"The whale number {i} swims in plain words.\n" * 20, _meta())
             for i in range(n)]
    lib = _lib(state)
    assert lib.scan()["indexed"] == n
    return paths


def _wait(state):
    ks._runs[ks.canon(state)].thread.join(30)
    return ks.move_view(state)


# -- where it goes -------------------------------------------------------------------------------------------

def test_default_is_the_data_folder_as_before(bot):
    assert fetched.root(bot) == bot / "data" / "knowledge"
    assert fetched.folder_for(bot, "gutenberg") == bot / "data" / "knowledge" / "gutenberg"


def test_every_save_goes_through_the_one_function_to_her_folder(bot, tmp_path):
    books = tmp_path / "other" / "Books"
    _set(bot, knowledge=str(books))
    path = fetched.save(bot, "gutenberg", "moby", "Call me a plain reader.", _meta())
    assert path == books / "knowledge" / "gutenberg" / "moby.txt"
    assert (books / "knowledge" / fetched.MARKER).exists()
    assert not (bot / "data" / "knowledge").exists()
    assert fetched.written_by_fetch(bot, path, path.read_bytes())
    assert [r["file"] for r in fetched.sources(bot)] == ["moby.txt"]
    assert fetched.meta_for(bot, path)["title"] == "Moby Dick"


def test_folder_for_takes_only_a_plain_name(bot):
    for bad in ("..", "a/b", "A", "", "c:\\x", "gutenberg\\..\\.."):
        with pytest.raises(ValueError):
            fetched.folder_for(bot, bad)


# -- drive gone, drive full --------------------------------------------------------------------------------------

def _gone_letter():
    for c in "QXYZJK":
        if not os.path.exists(f"{c}:\\"):
            return c
    pytest.skip("no free drive letter")


@pytest.mark.skipif(os.name != "nt", reason="drive letters")
def test_drive_gone_one_plain_line_and_the_rest_keeps_working(bot, tmp_path):
    _seed(bot)
    other = tmp_path / "linked"
    other.mkdir()
    (other / "note.txt").write_text("The lighthouse keeper writes plainly.", encoding="utf-8")
    lib = _lib(bot)
    lib.link(other)
    letter = _gone_letter()
    _set(bot, knowledge=f"{letter}:\\PuffPAI Books")
    line = fetched.away_line(bot)
    assert f"{letter}:" in line and "not there" in line and "Plug it in" in line and "puffpai knowledge move" in line
    with pytest.raises(fetched.StoreAway) as got:
        fetched.save(bot, "gutenberg", "more", "More words.", _meta())
    assert str(got.value) == line
    assert isinstance(got.value, OSError)                 # a download's own disk handling ends only that download
    lib.scan()                                            # never raises
    assert lib.search("lighthouse")                       # her other knowledge still works
    assert lib.search("whale")                            # and what is indexed still answers
    assert not (bot / "data" / "knowledge" / "gutenberg" / "more.txt").exists()   # never saved somewhere else instead


def test_drive_full_checks_before_it_starts(bot, tmp_path, monkeypatch):
    books = tmp_path / "Books"
    _set(bot, knowledge=str(books))
    monkeypatch.setattr(fetched, "free_bytes", lambda p: 5_000_000)
    with pytest.raises(fetched.StoreAway) as got:
        fetched.folder_for(bot, "kn-big", need=2_000_000_000)
    assert "2.0 GB" in str(got.value) and "5 MB free" in str(got.value) and "puffpai knowledge move" in str(got.value)
    assert not (books / "knowledge" / "kn-big").exists()


def test_a_big_download_on_the_bots_own_drive_leaves_more_room(bot, monkeypatch):
    monkeypatch.setattr(fetched, "free_bytes", lambda p: 3_000_000_000)
    assert fetched.away_line(bot, need=1000) == ""        # a book is fine
    assert "free" in fetched.away_line(bot, need=1_000_000_000)   # a big download would leave too little


def test_fat32_says_so_for_a_big_download(bot, tmp_path, monkeypatch):
    _set(bot, knowledge=str(tmp_path / "Books"))
    monkeypatch.setattr(fetched, "filesystem", lambda p: "FAT32")
    monkeypatch.setattr(fetched, "free_bytes", lambda p: 900_000_000_000)
    assert "FAT32" in fetched.away_line(bot, need=50_000_000_000)
    assert fetched.away_line(bot, need=1_000_000) == ""


def test_notice_once_per_problem(bot, monkeypatch):
    from puffpai_core.wiring import tell_knowledge_away
    posted = []
    core = SimpleNamespace(state=bot, notices=SimpleNamespace(post=lambda who, what: posted.append(what)))
    monkeypatch.setattr(fetched, "away_line", lambda state, need=0: "E: is not there.")
    tell_knowledge_away(core)
    tell_knowledge_away(core)
    assert [p["text"] for p in posted] == ["E: is not there."]
    monkeypatch.setattr(fetched, "away_line", lambda state, need=0: "")
    tell_knowledge_away(core)
    monkeypatch.setattr(fetched, "away_line", lambda state, need=0: "E: is not there.")
    tell_knowledge_away(core)
    assert len(posted) == 2


def test_search_tool_carries_the_line(bot, monkeypatch):
    from puffpai_core.wiring import knowledge_search
    from puffpai_core import seams
    monkeypatch.setattr(seams, "archivist", lambda s, b: FakeArchivist(s))
    _seed(bot)
    letter = "Q"
    monkeypatch.setattr(fetched, "away_line", lambda state, need=0: f"{letter}: is not there.")
    search = knowledge_search(SimpleNamespace(state=bot, bus=None, notices=None))
    assert search("whale", 5)
    assert search.away() == "Q: is not there."


# -- the move ----------------------------------------------------------------------------------------------------

def test_move_to_another_folder_keeps_everything_findable(bot, tmp_path):
    paths = _seed(bot)
    books = tmp_path / "other" / "Books"
    ks.start(bot, str(books), wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "done" and "Moved 4 files" in m["line"]     # three books and meta.json
    new = books / "knowledge" / "gutenberg"
    assert sorted(p.name for p in new.glob("*.txt")) == sorted(p.name for p in paths)
    assert not (bot / "data" / "knowledge").exists()
    assert fetched.chosen(bot) == str(books)
    # Archivist still finds it, with no new reading: same rows, new paths
    lib = _lib(bot)
    hits = lib.search("whale")
    assert hits and all(Path(h["path"]).parent == new.resolve() for h in hits)
    report = lib.scan()
    assert report["indexed"] == 0 and report["unchanged"] == 3
    for p in new.glob("*.txt"):
        assert fetched.written_by_fetch(bot, p, p.read_bytes())       # still public text, not her data
    assert len(fetched.sources(bot)) == 3
    assert fetched.meta_for(bot, new / paths[0].name)["title"] == "Moby Dick"
    assert not (bot / "index" / ks.LOG).exists()
    # and the next book goes there
    assert fetched.save(bot, "gutenberg", "next", "The next plain book.", _meta()).parent == new


def test_move_back_to_the_data_folder(bot, tmp_path):
    _seed(bot)
    books = tmp_path / "Books"
    ks.start(bot, str(books), wait=True)
    ks.start(bot, "", wait=True)
    assert ks.move_view(bot)["phase"] == "done"
    assert fetched.chosen(bot) == ""
    assert len(list((bot / "data" / "knowledge" / "gutenberg").glob("*.txt"))) == 3
    assert not (books / "knowledge").exists()
    assert _lib(bot).search("whale")


def test_choosing_the_data_folder_itself_is_the_default(bot):
    with pytest.raises(ks.MoveRefused) as got:
        ks.start(bot, str(bot / "data"))
    assert got.value.code == "same"


def _different_drives(monkeypatch):
    monkeypatch.setattr(ks, "_same_drive", lambda a, b: False)


def test_a_stop_loses_nothing_and_resume_finishes(bot, tmp_path, monkeypatch):
    paths = _seed(bot)
    _different_drives(monkeypatch)
    before = {p.name: p.read_bytes() for p in paths}
    books = tmp_path / "Books"
    calls = {"n": 0}
    real = ks._Run.one_file

    def stop_after_one(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(5, "Input/output error")
        return real(self, *a, **kw)

    monkeypatch.setattr(ks._Run, "one_file", stop_after_one)
    ks.start(bot, str(books), wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "stopped" and m["can_resume"] and m["can_cancel"] and "Nothing is lost" in m["line"]
    # the old place is whole and in use; nothing new is written; search still answers
    assert fetched.root(bot) == bot / "data" / "knowledge"
    assert {p.name: p.read_bytes() for p in paths} == before
    assert fetched.moving(bot) and "being moved" in fetched.away_line(bot)
    with pytest.raises(fetched.StoreAway):
        fetched.save(bot, "gutenberg", "during", "Words.", _meta())
    lib = _lib(bot)
    assert lib.scan()["indexed"] == 0 and lib.search("whale")
    monkeypatch.setattr(ks._Run, "one_file", real)
    ks.resume(bot, wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "done" and fetched.chosen(bot) == str(books)
    assert m["percent"] == 100 and m["done_files"] == m["total_files"] == 4
    new = books / "knowledge" / "gutenberg"
    assert {p.name: p.read_bytes() for p in new.glob("*.txt")} == before
    assert not (bot / "data" / "knowledge").exists()


def test_a_half_copied_file_is_picked_up_where_it_stopped(bot, tmp_path, monkeypatch):
    paths = _seed(bot, 1)
    _different_drives(monkeypatch)
    want = paths[0].read_bytes()
    books = tmp_path / "Books"
    # a hard stop: the record says copying, a part file is there
    ks._write(bot, ks.check_new(bot, str(books)))
    new = books / "knowledge" / "gutenberg"
    new.mkdir(parents=True)
    (new / (paths[0].name + ks.PART)).write_bytes(want[:50])
    m = ks.move_view(bot)
    assert m["phase"] == "stopped" and m["can_resume"]            # nobody is running it
    ks.resume(bot, wait=True)
    assert ks.move_view(bot)["phase"] == "done"
    assert (new / paths[0].name).read_bytes() == want
    assert not list(new.glob("*" + ks.PART))


def test_a_bad_part_file_is_not_trusted(bot, tmp_path, monkeypatch):
    paths = _seed(bot, 1)
    _different_drives(monkeypatch)
    want = paths[0].read_bytes()
    books = tmp_path / "Books"
    ks._write(bot, ks.check_new(bot, str(books)))
    new = books / "knowledge" / "gutenberg"
    new.mkdir(parents=True)
    (new / (paths[0].name + ks.PART)).write_bytes(b"X" * 50)      # not what the original holds
    ks.resume(bot, wait=True)
    assert ks.move_view(bot)["phase"] == "done"
    assert (new / paths[0].name).read_bytes() == want


def test_a_stop_while_switching_is_finished_by_resume(bot, tmp_path, monkeypatch):
    _seed(bot)
    _different_drives(monkeypatch)
    books = tmp_path / "Books"
    real = ks._rekey_index

    def hard(*a):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ks, "_rekey_index", hard)
    ks.start(bot, str(books), wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "stopped" and m["switched"] and not m["can_cancel"]
    assert fetched.chosen(bot) == str(books) and not fetched.moving(bot)   # the new place is in use already
    with pytest.raises(ks.MoveRefused) as got:
        ks.cancel(bot)
    assert got.value.code == "switched"
    monkeypatch.setattr(ks, "_rekey_index", real)
    ks.resume(bot, wait=True)
    assert ks.move_view(bot)["phase"] == "done"
    hits = _lib(bot).search("whale")
    assert hits and all(Path(h["path"]).parent.parent == (books / "knowledge").resolve() for h in hits)


def test_a_stop_between_switching_and_writing_it_down_cannot_be_cancelled(bot, tmp_path, monkeypatch):
    _seed(bot)
    _different_drives(monkeypatch)
    books = tmp_path / "Books"
    j = ks.check_new(bot, str(books))
    ks._write(bot, dict(j, phase="switching"))
    ks._set_place(bot, str(books))                                 # the hard stop came right here
    with pytest.raises(ks.MoveRefused) as got:
        ks.cancel(bot)
    assert got.value.code == "switched"


def test_cancel_takes_back_only_its_own_copies(bot, tmp_path, monkeypatch):
    paths = _seed(bot)
    _different_drives(monkeypatch)
    before = {p.name: p.read_bytes() for p in paths}
    books = tmp_path / "Books"
    (books / "knowledge").mkdir(parents=True)
    (books / "keepme.txt").write_text("Her own file, beside the store.", encoding="utf-8")
    (books / "knowledge" / "hers.txt").write_text("Hers too.", encoding="utf-8")
    real = ks._Run.one_file
    calls = {"n": 0}

    def stop(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(5, "Input/output error")
        return real(self, *a, **kw)

    monkeypatch.setattr(ks._Run, "one_file", stop)
    ks.start(bot, str(books), wait=True)
    assert ks.move_view(bot)["phase"] == "stopped"
    ks.cancel(bot)
    assert fetched.chosen(bot) == "" and not fetched.moving(bot)
    assert {p.name: p.read_bytes() for p in paths} == before
    assert (books / "keepme.txt").exists() and (books / "knowledge" / "hers.txt").exists()
    assert not (books / "knowledge" / "gutenberg").exists()
    assert fetched.save(bot, "gutenberg", "works", "It works again.", _meta()).exists()


def test_cancel_a_rename_puts_it_back(bot, tmp_path, monkeypatch):
    paths = _seed(bot)
    monkeypatch.setattr(ks, "_same_drive", lambda a, b: True)
    before = {p.name: p.read_bytes() for p in paths}
    real = ks._Run.one_file
    calls = {"n": 0}

    def stop(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(5, "Input/output error")
        return real(self, *a, **kw)

    monkeypatch.setattr(ks._Run, "one_file", stop)
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    assert sum(p.exists() for p in paths) < 3                      # renamed, on the same drive
    ks.cancel(bot)
    assert {p.name: p.read_bytes() for p in paths} == before


def test_same_drive_rename_needs_no_copy(bot, tmp_path, monkeypatch):
    _seed(bot)
    monkeypatch.setattr(ks._Run, "_copy", lambda *a: (_ for _ in ()).throw(AssertionError("copied")))
    monkeypatch.setattr(fetched, "free_bytes", lambda p: 1)        # no room at all: a rename needs none
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    assert ks.move_view(bot)["phase"] == "done"
    assert len(list((tmp_path / "Books" / "knowledge" / "gutenberg").glob("*.txt"))) == 3


def test_not_enough_room_refused_before_anything_moves(bot, tmp_path, monkeypatch):
    _seed(bot)
    _different_drives(monkeypatch)
    monkeypatch.setattr(fetched, "free_bytes", lambda p: 1000)
    with pytest.raises(ks.MoveRefused) as got:
        ks.start(bot, str(tmp_path / "Books"))
    assert got.value.code == "no_room" and "free" in str(got.value)
    assert not (bot / "index" / fetched.MOVE).exists() and fetched.chosen(bot) == ""


def test_fat32_refuses_a_big_file_with_a_plain_line(bot, tmp_path, monkeypatch):
    _seed(bot, 1)
    _different_drives(monkeypatch)
    monkeypatch.setattr(fetched, "filesystem", lambda p: "FAT32")
    monkeypatch.setattr(fetched, "FAT32_MAX", 10)
    with pytest.raises(ks.MoveRefused) as got:
        ks.start(bot, str(tmp_path / "Books"))
    assert got.value.code == "too_big_for_drive" and "NTFS or exFAT" in str(got.value)


def test_a_different_file_at_the_new_place_stops_the_move_and_overwrites_nothing(bot, tmp_path, monkeypatch):
    paths = _seed(bot, 1)
    _different_drives(monkeypatch)
    books = tmp_path / "Books"
    target = books / "knowledge" / "gutenberg" / paths[0].name
    target.parent.mkdir(parents=True)
    target.write_text("Her own different file.", encoding="utf-8")
    ks.start(bot, str(books), wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "stopped" and "different file" in m["line"]
    assert target.read_text(encoding="utf-8") == "Her own different file."
    assert paths[0].exists()


def test_a_file_changed_after_it_was_copied_keeps_the_new_bytes(bot, tmp_path, monkeypatch):
    paths = _seed(bot, 2)
    _different_drives(monkeypatch)
    real = ks._Run.cleaning
    (tmp_path / "Books").mkdir()

    def change_then_clean(self, j):
        paths[0].write_text("She changed this one.", encoding="utf-8")
        return real(self, j)

    monkeypatch.setattr(ks._Run, "cleaning", change_then_clean)
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "done" and m["left"] == 1 and "stay at" in m["line"]
    assert paths[0].read_text(encoding="utf-8") == "She changed this one."      # its old copy is kept


@pytest.mark.skipif(os.name != "nt", reason="junctions")
def test_a_junction_in_the_old_place_is_never_followed(bot, tmp_path):
    import subprocess
    _seed(bot, 1)
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "keys.txt").write_text("Never copy this.", encoding="utf-8")
    link = bot / "data" / "knowledge" / "gutenberg" / "jump"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(secret)], check=True, capture_output=True)
    try:
        ks.start(bot, str(tmp_path / "Books"), wait=True)
        m = ks.move_view(bot)
        assert m["phase"] == "done" and "links" in m["line"]
        assert not list((tmp_path / "Books").rglob("keys.txt"))
        assert (secret / "keys.txt").exists()
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


@pytest.mark.skipif(os.name != "nt", reason="drive letters")
def test_a_drive_that_changed_letter_is_taken_as_it_is(bot, tmp_path):
    """The USB came back as another letter: her files are at the new place already, so nothing is copied."""
    paths = _seed(bot, 1)
    books = tmp_path / "Books"
    ks.start(bot, str(books), wait=True)
    letter = _gone_letter()
    old_root = f"{letter}:\\PuffPAI Books\\knowledge"
    # the index and record as they would be with the drive under the old letter
    new_key = str((books / "knowledge").resolve())
    with closing(sqlite3.connect(bot / "index" / "knowledge.db")) as db, db:
        for table in ("files", "chunks"):
            db.execute(f"UPDATE {table} SET path = ? || substr(path, ?)", (old_root, len(new_key) + 1))
    ledger = json.loads((bot / "index" / fetched.LEDGER).read_text(encoding="utf-8"))["files"]
    low = os.path.normcase(new_key)
    (bot / "index" / fetched.LEDGER).write_text(json.dumps({"files": {os.path.normcase(old_root) + k[len(low):]: v
                                                                      for k, v in ledger.items()}}), encoding="utf-8")
    _set(bot, knowledge=f"{letter}:\\PuffPAI Books")
    assert "not there" in fetched.away_line(bot)
    ks.start(bot, str(books), wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "done" and "are at" in m["line"]
    assert fetched.chosen(bot) == str(books) and fetched.away_line(bot) == ""
    hits = _lib(bot).search("whale")
    assert hits and Path(hits[0]["path"]).parent.parent == (books / "knowledge").resolve()
    moved = books / "knowledge" / "gutenberg" / paths[0].name
    assert fetched.written_by_fetch(bot, moved, moved.read_bytes())


@pytest.mark.skipif(os.name != "nt", reason="drive letters")
def test_old_drive_gone_for_good_needs_her_say_so(bot, tmp_path):
    letter = _gone_letter()
    _set(bot, knowledge=f"{letter}:\\PuffPAI Books")
    with pytest.raises(ks.MoveRefused) as got:
        ks.start(bot, str(tmp_path / "Books"))
    assert got.value.code == "source_away" and f"{letter}:" in str(got.value)
    ks.start(bot, str(tmp_path / "Books"), leave_old=True, wait=True)
    assert ks.move_view(bot)["phase"] == "done" and fetched.chosen(bot) == str(tmp_path / "Books")
    assert fetched.save(bot, "gutenberg", "new", "A new plain book.", _meta()).exists()


def test_the_vault_is_never_touched_and_never_a_target(bot, tmp_path):
    vault = bot / "vault"
    vault.mkdir()
    (vault / "keys.bin").write_bytes(b"sealed")
    _seed(bot, 1)
    with pytest.raises(ks.MoveRefused):
        ks.start(bot, str(vault))
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    assert (vault / "keys.bin").read_bytes() == b"sealed"


def test_two_moves_never_run_at_once(bot, tmp_path, monkeypatch):
    _seed(bot, 1)
    _different_drives(monkeypatch)
    gate = threading.Event()
    real = ks._Run.one_file

    def slow(self, *a, **kw):
        gate.wait(10)
        return real(self, *a, **kw)

    monkeypatch.setattr(ks._Run, "one_file", slow)
    ks.start(bot, str(tmp_path / "Books"))
    try:
        assert ks.move_view(bot)["running"]
        with pytest.raises(ks.MoveRefused):
            ks.start(bot, str(tmp_path / "Other"))
        with pytest.raises(ks.MoveRefused):
            ks.resume(bot)
    finally:
        gate.set()
        _wait(bot)
    assert ks.move_view(bot)["phase"] == "done"


def test_a_new_move_waits_for_an_unfinished_one(bot, tmp_path, monkeypatch):
    _seed(bot, 1)
    _different_drives(monkeypatch)
    monkeypatch.setattr(ks._Run, "one_file", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "bad")))
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    with pytest.raises(ks.MoveRefused) as got:
        ks.start(bot, str(tmp_path / "Other"))
    assert got.value.code == "unfinished"


def test_a_folder_nested_in_the_store_is_refused(bot, tmp_path):
    books = tmp_path / "Books"
    _set(bot, knowledge=str(books))
    fetched.folder_for(bot, "gutenberg")
    with pytest.raises(ks.MoveRefused):
        ks.start(bot, str(books / "knowledge"))


# -- the big downloads (D99) follow the one function ------------------------------------------------------------

@pytest.fixture
def downloads(tmp_path, monkeypatch):
    import test_sources_knowledge as sk
    state = tmp_path / "bots" / "bot1"
    (state / "settings").mkdir(parents=True)
    store = sk.Builds(state)
    store.write({"id": sk.BID, "goal": sk.GOAL, "planning": True, "jobs": [], "jobs_in": []})
    monkeypatch.setattr(sk.dl, "picked_entries", lambda *a: (5, [1, 2], True))
    monkeypatch.setattr(sk.dl, "read_articles", lambda path, picked: [("Tomato", "Tomato plants grow in a garden. " * 20),
                                                                      ("Rose", "Rose plants grow in a garden. " * 20)][:len(picked)])
    ports = sk.Ports(["opds.library.kiwix.org", "catalog.data.gov", "huggingface.co"])
    host = SimpleNamespace(store=store, ports=ports, state=state, registry=sk.Registry(), _stopping=False)
    world = SimpleNamespace(host=host, ports=ports, store=store, fetcher=sk.kmod.Fetcher(host), state=state)
    kp = sk.plan(world)
    only = [o["value"] for o in kp["offers"] if o["source"] == "kiwix"]
    return SimpleNamespace(sk=sk, world=world, kp=kp, only=only, state=state, pid=sk.dl.package_id("kiwix", "gardenology"))


def test_a_big_download_goes_to_her_place_whole_and_is_found_there(downloads, tmp_path):
    d = downloads
    books = tmp_path / "Books"
    _set(d.state, knowledge=str(books))
    run = d.sk.steps(d.world, d.kp, d.only)
    assert run["downloads"][d.only[0]]["done"] is True
    folder = books / "knowledge" / d.pid
    assert (folder / "gardenology_2026-09.zim").read_bytes() == d.sk.ZIM
    assert not (d.state / "data" / "knowledge" / d.pid).exists()
    assert [r for r in fetched.sources(d.state) if r["source"] == d.pid]
    names = [p.name for p in _lib(d.state)._files()]
    assert "puffpai.json" not in names and fetched.MARKER not in names and any(n.endswith(".txt") for n in names)


def test_a_big_download_checks_room_on_her_drive_before_a_byte_comes(downloads, tmp_path, monkeypatch):
    d = downloads
    _set(d.state, knowledge=str(tmp_path / "Books"))
    monkeypatch.setattr(fetched, "free_bytes", lambda p: 1_000_000)
    run = d.sk.steps(d.world, d.kp, d.only)
    line = run["downloads"][d.only[0]]["line"]
    assert "was not downloaded" in line and "free" in line and "puffpai knowledge move" in line
    assert not list((tmp_path / "Books").rglob("*.zim"))
    assert not list((tmp_path / "Books").rglob("*.part"))


@pytest.mark.skipif(os.name != "nt", reason="drive letters")
def test_a_big_download_on_a_drive_that_is_gone_says_so_and_saves_nowhere_else(downloads):
    d = downloads
    letter = _gone_letter()
    _set(d.state, knowledge=f"{letter}:\\PuffPAI Books")
    run = d.sk.steps(d.world, d.kp, d.only)
    line = run["downloads"][d.only[0]]["line"]
    assert "was not downloaded" in line and f"{letter}:" in line and "not there" in line
    assert not (d.state / "data" / "knowledge" / d.pid).exists()


def test_a_drive_pulled_out_during_a_download_keeps_the_part_and_says_which_drive(downloads, tmp_path, monkeypatch):
    d = downloads
    _set(d.state, knowledge=str(tmp_path / "Books"))
    folder = fetched.package_folder(d.state, d.pid)
    part = folder / ".gardenology.part"
    part.write_bytes(b"half a library")

    def pulled(*_a, **_k):
        monkeypatch.setattr(fetched, "free_bytes", lambda p: None)
        raise FileNotFoundError(2, "gone")

    monkeypatch.setattr(d.sk.dl, "fetch_range", pulled)
    run = d.sk.steps(d.world, d.kp, d.only)
    line = run["downloads"][d.only[0]]["line"]
    assert "was not downloaded" in line and "not there" in line and "FileNotFoundError" not in line
    assert part.read_bytes() == b"half a library"


def test_a_download_stopped_halfway_keeps_its_part_through_a_move(bot, tmp_path):
    folder = fetched.package_folder(bot, "kn-kiwix-garden")
    (folder / ".garden.part").write_bytes(b"half a library")
    (folder / "puffpai.json").write_text('{"id": "kn-kiwix-garden"}', encoding="utf-8")
    (fetched.save_file(bot, "kn-kiwix-garden", "a.png", b"picture"))
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    assert ks.move_view(bot)["phase"] == "done"
    new = fetched.package_folder(bot, "kn-kiwix-garden")
    assert new == tmp_path / "Books" / "knowledge" / "kn-kiwix-garden"
    assert (new / ".garden.part").read_bytes() == b"half a library"
    assert (new / "a.png").read_bytes() == b"picture"
    assert fetched.save_file(bot, "kn-kiwix-garden", "b.png", b"more").parent == new


# -- the Library reads the new place --------------------------------------------------------------------------

def test_the_library_indexes_a_chosen_place_and_skips_its_marker_and_meta(bot, tmp_path):
    books = tmp_path / "Books"
    (books).mkdir()
    (books / "not-mine-to-read.txt").write_text("Her other file beside the store: never walked.", encoding="utf-8")
    _set(bot, knowledge=str(books))
    fetched.save(bot, "gutenberg", "moby", "Plain words about a whale.", _meta())
    lib = _lib(bot)
    assert [Path(p).name for p in lib._files()] == ["moby.txt"]
    assert lib.scan()["indexed"] == 1
    assert lib.search("whale") and not lib.search("beside")


def test_a_link_into_the_store_is_refused(bot, tmp_path):
    from puffpai_core.zone1.folders import FolderRefused
    books = tmp_path / "Books"
    _set(bot, knowledge=str(books))
    fetched.folder_for(bot, "gutenberg")
    with pytest.raises(FolderRefused):
        _lib(bot).link(books / "knowledge")
    with pytest.raises(FolderRefused):
        _lib(bot).link(books / "knowledge" / "gutenberg")


# -- the routes -------------------------------------------------------------------------------------------------

@pytest.fixture
def api(bot, monkeypatch):
    from puffpai_core.api.app import create_app
    monkeypatch.setenv("PUFFPAI_STATE", str(bot))
    app = create_app(bot)
    core = app.extensions["puffpai"]
    return app.test_client(), {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}


def test_routes_need_a_key(api):
    c, _dev = api
    assert c.get("/v1/knowledge/place").status_code == 401
    assert c.post("/v1/knowledge/place", json={"path": "C:\\x"}).status_code == 401
    assert c.post("/v1/knowledge/place/resume", json={}).status_code == 401
    assert c.post("/v1/knowledge/place/cancel", json={}).status_code == 401


def test_the_desktop_routes_move_and_say_how_it_goes(api, bot, tmp_path):
    c, dev = api
    _seed(bot, 2)
    got = c.get("/v1/knowledge/place", headers=dev).get_json()
    assert got["default"] is True and got["there"] is True and "free on" in got["free"] and got["move"] is None
    books = tmp_path / "Books"
    r = c.post("/v1/knowledge/place", json={"path": str(books)}, headers=dev)
    assert r.status_code == 202
    _wait(bot)
    got = c.get("/v1/knowledge/place", headers=dev).get_json()
    assert got["default"] is False and got["store"] == str(books / "knowledge")
    assert got["move"]["phase"] == "done" and got["move"]["percent"] == 100
    r = c.post("/v1/knowledge/place", json={"path": str(bot / "vault")}, headers=dev)
    assert r.status_code == 400 and r.get_json()["error"]
    assert c.post("/v1/knowledge/place", json={"path": 7}, headers=dev).status_code == 400
    assert c.post("/v1/knowledge/place/resume", json={}, headers=dev).status_code == 409
    assert c.post("/v1/knowledge/place/cancel", json={}, headers=dev).status_code == 409


def test_a_download_running_holds_the_move(api, bot, tmp_path, monkeypatch):
    c, dev = api
    from puffpai_core.api.groups import toolsmith
    fake = SimpleNamespace(store=SimpleNamespace(all=lambda: [{"knowledge_run": {"state": "fetching"}}]))
    monkeypatch.setattr(toolsmith, "service", lambda: fake)
    r = c.post("/v1/knowledge/place", json={"path": str(tmp_path / "Books")}, headers=dev)
    assert r.status_code == 409 and r.get_json()["code"] == "downloading"
    assert fetched.chosen(bot) == ""


# -- the terminal ---------------------------------------------------------------------------------------------

def test_terminal_with_no_core_runs_the_move_to_the_end(bot, tmp_path, monkeypatch, capsys):
    from puffpai_core import cli
    _seed(bot, 2)
    monkeypatch.setenv("PUFFPAI_STATE", str(bot))
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    monkeypatch.setattr(cli, "_offline", lambda fn, args: fn(args, None))
    books = tmp_path / "Books"
    assert cli.main(["knowledge", "where"]) == 0
    assert "free on" in capsys.readouterr().out
    assert cli.main(["knowledge", "move", str(books)]) == 0
    out = capsys.readouterr().out
    assert "Moved 3 files" in out and str(books) in out
    assert fetched.chosen(bot) == str(books)
    assert cli.main(["knowledge", "move", str(bot / "vault")]) == 1
    assert "Not done:" in capsys.readouterr().out
    assert cli.main(["knowledge", "move"]) == 2
    assert cli.main(["knowledge", "move", "default"]) == 0
    assert fetched.chosen(bot) == ""


# -- the wizard's question ------------------------------------------------------------------------------------

def _vault_hook(state, tmp_path):
    from puffpai_core.wizard.engine import VaultStep
    engine = SimpleNamespace(state=state, platform="windows" if os.name == "nt" else "linux",
                             env={"USERPROFILE": str(tmp_path / "home"), "HOME": str(tmp_path / "home")},
                             settings=state / "settings", services=SimpleNamespace(vault=None),
                             write_setting=lambda name, data: (state / "settings" / name).write_text(json.dumps(data), encoding="utf-8"))
    return VaultStep(engine)


def test_the_real_step_asks_it_after_the_data_folder_and_enter_keeps_it_there(tmp_path):
    from wizard_kit import CODE, Kit
    state = tmp_path / "state"
    eng = Kit(state, env={"USERPROFILE": str(tmp_path / "c" / "Users" / "me")}).engine()
    eng.submit("vault", {"where": "here", "pin": "482913", "pin_again": "482913"})
    eng.submit("vault", {"recovery_confirm": CODE})
    data = tmp_path / "Data"
    screen = eng.submit("vault", {"data_folder": str(data)})
    assert screen["field"]["id"] == "knowledge_folder" and screen["field"]["default"] == str(data)
    assert "free" in screen["field"]["help"] and "Press Enter" in screen["field"]["help"]
    screen = eng.submit("vault", {"knowledge_folder": "", "linked": ""})
    assert any(line.startswith("Your books and downloads:") for line in screen["lines"])
    assert fetched.chosen(state) == "" and fetched.root(state) == data / "knowledge"


def test_the_real_step_refuses_a_bad_folder_and_asks_again(tmp_path):
    from wizard_kit import CODE, Kit
    state = tmp_path / "state"
    eng = Kit(state, env={"USERPROFILE": str(tmp_path / "c" / "Users" / "me")}).engine()
    eng.submit("vault", {"where": "here", "pin": "482913", "pin_again": "482913"})
    eng.submit("vault", {"recovery_confirm": CODE})
    screen = eng.submit("vault", {"data_folder": str(tmp_path / "Data"), "knowledge_folder": str(state / "vault")})
    assert screen["field"]["id"] == "knowledge_folder" and screen["error"]
    books = tmp_path / "Books"
    screen = eng.submit("vault", {"knowledge_folder": str(books), "linked": ""})
    assert fetched.chosen(state) == str(books)
    assert any(str(books) in line and "free" in line for line in screen["lines"])


def test_wizard_says_the_room_and_enter_keeps_the_default(bot, tmp_path):
    hook = _vault_hook(bot, tmp_path)
    field = {"id": "knowledge_folder", "help": "It may suit another drive."}
    known = {"data_folder": str(tmp_path / "data")}
    assert hook.default(field, known) == str(tmp_path / "data")
    words = hook.help(field, known)
    assert "It may suit another drive." in words and "free" in words and "Press Enter" in words
    got = hook.finish({}, dict(known, knowledge_folder=known["data_folder"]), None)
    assert "knowledge" not in json.loads((bot / "settings" / "folders.json").read_text(encoding="utf-8"))
    assert any(line.startswith("Your books and downloads:") and "free" in line for line in got["lines"])


def test_wizard_takes_another_drive_and_refuses_the_vault(bot, tmp_path):
    from puffpai_core.wizard.engine import Ask
    hook = _vault_hook(bot, tmp_path)
    field = {"id": "knowledge_folder"}
    known = {"data_folder": str(tmp_path / "data")}
    with pytest.raises(Ask):
        hook.check(field, str(bot / "vault"), known)
    books = tmp_path / "Books"
    hook.check(field, str(books), known)
    hook.finish({}, dict(known, knowledge_folder=str(books)), None)
    assert fetched.chosen(bot) == str(books)
    assert fetched.save(bot, "gutenberg", "moby", "Plain words.", _meta()).parent == books / "knowledge" / "gutenberg"


def test_wizard_never_leaves_kept_files_behind(bot, tmp_path):
    hook = _vault_hook(bot, tmp_path)
    _seed(bot, 1)
    known = {"data_folder": str(bot / "data"), "knowledge_folder": str(tmp_path / "Books")}
    got = hook.finish({}, known, None)
    assert fetched.chosen(bot) == ""
    assert any("puffpai knowledge move" in line for line in got["lines"])


# -- the fix round: each of these reproduces what a verifier refuted with real junctions ---------------------------

windows = pytest.mark.skipif(os.name != "nt", reason="junctions")


@pytest.fixture
def junction():
    """``junction(link, target)``: a real junction, gone again before the tree is deleted."""
    made = []

    def make(link, target):
        Path(link).parent.mkdir(parents=True, exist_ok=True)
        got = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        assert got.returncode == 0, got.stdout + got.stderr
        made.append(link)
        return Path(link)

    yield make
    for link in reversed(made):
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


@pytest.fixture
def vault(bot):
    """This bot's vault, with something in it to lose."""
    place = bot / "vault"
    (place / "sub").mkdir(parents=True)
    (place / "keys.bin").write_bytes(b"sealed")
    (place / "sub" / "more.bin").write_bytes(b"sealed too")
    return place


def _vault_whole(place):
    return (place / "keys.bin").read_bytes() == b"sealed" and (place / "sub" / "more.bin").read_bytes() == b"sealed too" \
        and sorted(p.name for p in place.rglob("*")) == ["keys.bin", "more.bin", "sub"]


def _stop_at(monkeypatch, n):
    real, calls = ks._Run.one_file, {"n": 0}

    def stop(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == n:
            raise OSError(5, "Input/output error")
        return real(self, *a, **kw)

    monkeypatch.setattr(ks._Run, "one_file", stop)


# 1: a source that is a link

@windows
def test_a_default_store_that_is_a_junction_to_the_vault_is_never_emptied(bot, vault, tmp_path, junction):
    junction(bot / "data" / "knowledge", vault)
    with pytest.raises(ks.MoveRefused) as got:
        ks.start(bot, str(tmp_path / "New"), wait=True)
    assert "link" in str(got.value)
    assert _vault_whole(vault)
    assert not (tmp_path / "New" / "knowledge").exists()


@windows
def test_a_chosen_store_that_is_a_junction_to_the_vault_is_never_emptied(bot, vault, tmp_path, junction):
    books = tmp_path / "Books"
    junction(books / "knowledge", vault)
    _set(bot, knowledge=str(books))
    with pytest.raises(ks.MoveRefused):
        ks.start(bot, str(tmp_path / "New"), wait=True)
    with pytest.raises(ks.MoveRefused):
        ks.start(bot, "", wait=True)
    assert _vault_whole(vault)
    assert not (bot / "data" / "knowledge").exists()


@windows
def test_a_store_reached_through_a_junction_higher_up_is_refused(bot, vault, tmp_path, junction):
    (vault / "knowledge").mkdir()
    (vault / "knowledge" / "a.txt").write_text("Inside the vault.", encoding="utf-8")
    link = junction(tmp_path / "up", vault)
    _set(bot, data=str(link))
    with pytest.raises(ks.MoveRefused):
        ks.start(bot, str(tmp_path / "New"), wait=True)
    assert (vault / "knowledge" / "a.txt").exists()


def test_a_store_that_overlaps_a_vault_place_is_refused_with_no_link_at_all(bot, tmp_path):
    place = bot / "data" / "knowledge"
    place.mkdir(parents=True)
    (place / "a.txt").write_text("In a vault place.", encoding="utf-8")
    (bot / "settings" / "vault.json").write_text(json.dumps({"folder": str(place)}), encoding="utf-8")
    with pytest.raises(ks.MoveRefused) as got:
        ks.start(bot, str(tmp_path / "New"), wait=True)
    assert str(got.value) == kf.SOURCE_VAULT and (place / "a.txt").exists()


@windows
@pytest.mark.parametrize("phase", ["copying", "stopped", "switching", "cleaning"])
def test_resume_refuses_a_source_that_became_a_link(bot, vault, tmp_path, junction, monkeypatch, phase):
    _seed(bot, 1)
    _different_drives(monkeypatch)
    j = ks.check_new(bot, str(tmp_path / "New"))
    ks._write(bot, dict(j, phase=phase, was="copying"))
    ks._note_done(bot, {"rel": "keys.bin", "size": 6, "mtime_ns": (vault / "keys.bin").stat().st_mtime_ns})
    (tmp_path / "New" / "knowledge").mkdir(parents=True)
    (tmp_path / "New" / "knowledge" / "keys.bin").write_bytes(b"sealed")
    (bot / "data" / "knowledge").rename(tmp_path / "aside")
    junction(bot / "data" / "knowledge", vault)
    with pytest.raises(ks.MoveRefused) as got:
        ks.resume(bot, wait=True)
    assert "link" in str(got.value)
    assert _vault_whole(vault)


@windows
def test_a_link_that_comes_after_the_start_stops_the_run_before_it_touches_anything(bot, vault, tmp_path, junction, monkeypatch):
    _seed(bot, 1)
    _different_drives(monkeypatch)
    ks._write(bot, ks.check_new(bot, str(tmp_path / "New")))
    (bot / "data" / "knowledge").rename(tmp_path / "aside")
    junction(bot / "data" / "knowledge", vault)
    run = ks._Run(bot)
    got = run.run()
    assert got["phase"] == "stopped" and "link" in got["line"]
    assert _vault_whole(vault)


@windows
def test_a_default_place_target_that_is_a_link_is_refused(bot, tmp_path, junction):
    books = tmp_path / "Books"
    _set(bot, knowledge=str(books))
    fetched.save(bot, "gutenberg", "moby", "Plain words.", _meta())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    junction(bot / "data" / "knowledge", elsewhere)
    with pytest.raises(ks.MoveRefused) as got:
        ks.start(bot, "", wait=True)
    assert "link" in str(got.value)
    assert list(elsewhere.iterdir()) == []
    assert (books / "knowledge" / "gutenberg" / "moby.txt").exists()


# 2: a link below the store, on write

@windows
@pytest.mark.parametrize("chosen", [True, False])
def test_a_save_never_writes_through_a_source_folder_that_is_a_junction_to_the_vault(bot, vault, tmp_path, junction, chosen):
    store = (tmp_path / "Books" / "knowledge") if chosen else (bot / "data" / "knowledge")
    store.mkdir(parents=True)
    if chosen:
        _set(bot, knowledge=str(store.parent))
    junction(store / "wikipedia", vault)
    with pytest.raises(fetched.StoreAway) as got:
        fetched.save(bot, "wikipedia", "moby", "Plain words.", _meta())
    assert "link" in str(got.value)
    assert _vault_whole(vault)


@windows
def test_a_save_never_writes_through_a_default_store_that_is_a_junction(bot, vault, junction):
    junction(bot / "data" / "knowledge", vault)
    with pytest.raises(fetched.StoreAway):
        fetched.save(bot, "gutenberg", "moby", "Plain words.", _meta())
    assert _vault_whole(vault)


@windows
def test_a_move_never_copies_through_a_junction_below_the_new_place(bot, tmp_path, junction, monkeypatch):
    paths = _seed(bot, 1)
    _different_drives(monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    junction(tmp_path / "Books" / "knowledge" / "gutenberg", elsewhere)
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "stopped" and "link" in m["line"]
    assert list(elsewhere.iterdir()) == []
    assert paths[0].exists()


@windows
def test_a_same_drive_move_never_renames_through_a_junction_below_the_new_place(bot, tmp_path, junction):
    paths = _seed(bot, 1)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    junction(tmp_path / "Books" / "knowledge" / "gutenberg", elsewhere)
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    assert ks.move_view(bot)["phase"] == "stopped"
    assert list(elsewhere.iterdir()) == [] and paths[0].exists()


# 3: deletes that follow a link

def _target_with_things(tmp_path):
    target = tmp_path / "target"
    (target / "empty").mkdir(parents=True)
    (target / "deep" / "empty").mkdir(parents=True)
    (target / ("hers.txt" + ks.PART)).write_text("Hers.", encoding="utf-8")
    (target / fetched.MARKER).write_text('{"bot": "someone"}', encoding="utf-8")
    return target


def _target_whole(target):
    return (target / "empty").is_dir() and (target / "deep" / "empty").is_dir() and (target / ("hers.txt" + ks.PART)).exists() \
        and (target / fetched.MARKER).exists()


@windows
def test_tree_never_enters_a_junction_and_walk_never_lists_through_one(tmp_path, junction):
    top = tmp_path / "top"
    (top / "real").mkdir(parents=True)
    (top / "real" / "a.txt").write_text("A.", encoding="utf-8")
    target = _target_with_things(tmp_path)
    junction(top / "real" / "jump", target)
    seen = [here for here, _d, _f, _l in ks._tree(top)]
    assert seen == [top, top / "real"]
    files, links = ks._walk(top)
    assert [rel for _p, rel, _s in files] == ["real/a.txt"] and links == 1
    assert ks._walk(top / "real" / "jump") == ([], 1)
    assert list(ks._tree(top / "real" / "jump")) == []


@windows
def test_a_folder_that_cant_be_read_stops_walk_but_not_tree(tmp_path, monkeypatch):
    top = tmp_path / "top"
    top.mkdir()
    real = os.scandir

    def denied(path):
        raise PermissionError(5, "denied")

    with monkeypatch.context() as m:
        m.setattr(ks.os, "scandir", denied)
        assert list(ks._tree(top)) == []
        with pytest.raises(OSError):
            ks._walk(top)
    assert os.scandir is real


@windows
def test_drop_empty_never_touches_a_junctions_target(tmp_path, junction):
    top = tmp_path / "top"
    (top / "real" / "empty").mkdir(parents=True)
    target = _target_with_things(tmp_path)
    link = junction(top / "real" / "jump", target)
    ks._drop_empty(top)
    assert _target_whole(target)
    assert link.exists() and not (top / "real" / "empty").exists()


@windows
def test_drop_empty_on_a_link_does_nothing_at_all(tmp_path, junction):
    target = _target_with_things(tmp_path)
    link = junction(tmp_path / "jump", target)
    ks._drop_empty(link)
    assert _target_whole(target) and link.exists()


@windows
def test_cancel_never_deletes_through_a_junction_in_the_new_place(bot, tmp_path, junction, monkeypatch):
    paths = _seed(bot)
    _different_drives(monkeypatch)
    _stop_at(monkeypatch, 3)
    books = tmp_path / "Books"
    ks.start(bot, str(books), wait=True)
    assert ks.move_view(bot)["phase"] == "stopped"
    target = _target_with_things(tmp_path)
    junction(books / "knowledge" / "jump", target)
    ks.cancel(bot)
    assert _target_whole(target)
    assert all(p.exists() for p in paths)


@windows
def test_clearing_the_old_place_never_deletes_through_a_junction_there(bot, tmp_path, junction):
    _seed(bot, 1)
    target = _target_with_things(tmp_path)
    junction(bot / "data" / "knowledge" / "jump", target)
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    assert ks.move_view(bot)["phase"] == "done"
    assert _target_whole(target)


@windows
def test_cancel_refuses_when_either_place_is_a_link_now(bot, vault, tmp_path, junction, monkeypatch):
    _seed(bot)
    _different_drives(monkeypatch)
    _stop_at(monkeypatch, 3)
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    (bot / "data" / "knowledge").rename(tmp_path / "aside")
    junction(bot / "data" / "knowledge", vault)
    with pytest.raises(ks.MoveRefused) as got:
        ks.cancel(bot)
    assert got.value.code == "stuck"
    assert _vault_whole(vault)


@windows
def test_clearing_keeps_an_original_when_a_link_came_to_be_on_its_way(bot, tmp_path, junction, monkeypatch):
    paths = _seed(bot, 1)
    _different_drives(monkeypatch)
    real = ks._Run.cleaning
    books = tmp_path / "Books"

    def swap_then_clean(self, j):
        (books / "knowledge" / "gutenberg").rename(tmp_path / "aside")
        junction(books / "knowledge" / "gutenberg", tmp_path / "aside")
        return real(self, j)

    monkeypatch.setattr(ks._Run, "cleaning", swap_then_clean)
    ks.start(bot, str(books), wait=True)
    assert ks.move_view(bot)["left"] >= 1
    assert paths[0].exists()


# 4: the mover deleting what it did not make

def test_a_same_bytes_file_already_at_the_new_place_is_logged_not_made_and_cancel_keeps_it(bot, tmp_path, monkeypatch):
    paths = _seed(bot, 2)
    _different_drives(monkeypatch)
    books = tmp_path / "Books"
    new = books / "knowledge" / "gutenberg"
    new.mkdir(parents=True)
    hers = {p.name: p.read_bytes() for p in paths}
    for n, data in hers.items():
        (new / n).write_bytes(data)                                # hers, with the same bytes, before any move
    _stop_at(monkeypatch, 3)
    ks.start(bot, str(books), wait=True)
    assert ks.move_view(bot)["phase"] == "stopped"
    rows = ks._done_rows(bot)
    ks.cancel(bot)
    for n, data in hers.items():
        assert (new / n).read_bytes() == data, n
        assert (bot / "data" / "knowledge" / "gutenberg" / n).read_bytes() == data, n
    assert all(r.get("made") is False for r in rows.values() if Path(r["rel"]).name in hers)


def test_a_changed_source_never_deletes_her_same_bytes_file_on_resume(bot, tmp_path, monkeypatch):
    paths = _seed(bot, 2)
    _different_drives(monkeypatch)
    books = tmp_path / "Books"
    new = books / "knowledge" / "gutenberg"
    new.mkdir(parents=True)
    for p in paths:
        (new / p.name).write_bytes(p.read_bytes())
    _stop_at(monkeypatch, 3)
    ks.start(bot, str(books), wait=True)
    done = next(p for p in paths if any(Path(r["rel"]).name == p.name for r in ks._done_rows(bot).values()))
    before = done.read_bytes()
    done.write_text("She changed the original.", encoding="utf-8")
    monkeypatch.undo()
    _different_drives(monkeypatch)
    ks.resume(bot, wait=True)
    m = ks.move_view(bot)
    assert m["phase"] == "stopped" and "different file" in m["line"]
    assert (new / done.name).read_bytes() == before


def test_cancel_takes_back_a_copy_it_made_that_nobody_touched(bot, tmp_path, monkeypatch):
    paths = _seed(bot)
    _different_drives(monkeypatch)
    _stop_at(monkeypatch, 3)
    ks.start(bot, str(tmp_path / "Books"), wait=True)
    assert any(r.get("made") for r in ks._done_rows(bot).values())
    ks.cancel(bot)
    assert ks.move_view(bot)["line"] == "The move is cancelled. Everything is where it was."
    assert not (tmp_path / "Books" / "knowledge").exists()
    assert all(p.exists() for p in paths)


def test_cancel_leaves_a_copy_she_edited_and_says_so(bot, tmp_path, monkeypatch):
    paths = _seed(bot)
    _different_drives(monkeypatch)
    _stop_at(monkeypatch, 3)
    books = tmp_path / "Books"
    ks.start(bot, str(books), wait=True)
    made = [r for r in ks._done_rows(bot).values() if r["rel"].endswith(".txt")][0]
    copy = books / "knowledge" / made["rel"]
    copy.write_text("She wrote in the new copy.", encoding="utf-8")
    ks.cancel(bot)
    assert copy.read_text(encoding="utf-8") == "She wrote in the new copy."
    m = ks.move_view(bot)
    assert "1 copy" in m["line"] and "stays" in m["line"] and str(books) in m["line"]
    assert all(p.exists() for p in paths)


def test_cancel_leaves_a_copy_edited_to_the_same_size(bot, tmp_path, monkeypatch):
    _seed(bot)
    _different_drives(monkeypatch)
    _stop_at(monkeypatch, 3)
    books = tmp_path / "Books"
    ks.start(bot, str(books), wait=True)
    made = [r for r in ks._done_rows(bot).values() if r["rel"].endswith(".txt")][0]
    copy = books / "knowledge" / made["rel"]
    later = copy.stat().st_mtime_ns + 5_000_000_000
    copy.write_bytes(b"x" * copy.stat().st_size)
    os.utime(copy, ns=(later, later))
    ks.cancel(bot)
    assert copy.read_bytes() == b"x" * made["size"]


def test_the_part_suffix_is_our_own_and_a_plain_moving_file_of_hers_stays(bot, tmp_path, monkeypatch):
    paths = _seed(bot)
    _different_drives(monkeypatch)
    _stop_at(monkeypatch, 3)
    books = tmp_path / "Books"
    hers = books / "knowledge" / "gutenberg" / "my.moving"
    hers.parent.mkdir(parents=True)
    hers.write_text("Her own file, named like that.", encoding="utf-8")
    ks.start(bot, str(books), wait=True)
    ks.cancel(bot)
    assert hers.exists()
    assert all(p.exists() for p in paths)
    assert ks.PART == ".puffpai-moving"


def test_cancel_removes_a_part_file_only_when_its_original_is_still_there(bot, tmp_path, monkeypatch):
    paths = _seed(bot)
    _different_drives(monkeypatch)
    _stop_at(monkeypatch, 3)
    books = tmp_path / "Books"
    new = books / "knowledge" / "gutenberg"
    new.mkdir(parents=True)
    ks.start(bot, str(books), wait=True)
    ours = new / (paths[0].name + ks.PART)
    ours.write_bytes(b"half a copy")
    hers = new / ("no-original.txt" + ks.PART)
    hers.write_bytes(b"no original anywhere")
    ks.cancel(bot)
    assert not ours.exists()
    assert hers.read_bytes() == b"no original anywhere"


# 5: the Library under the store

def _secret(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "keys.txt").write_text("Never index this.", encoding="utf-8")
    return secret


@windows
@pytest.mark.parametrize("chosen", [True, False])
def test_the_library_never_walks_a_store_that_is_a_link(bot, tmp_path, junction, chosen):
    store = (tmp_path / "Books" / "knowledge") if chosen else (bot / "data" / "knowledge")
    if chosen:
        _set(bot, knowledge=str(store.parent))
    junction(store, _secret(tmp_path))
    assert not [p for p in _lib(bot)._files() if p.name == "keys.txt"]


@windows
@pytest.mark.parametrize("chosen", [True, False])
def test_the_library_never_enters_a_link_below_the_store(bot, tmp_path, junction, chosen):
    store = (tmp_path / "Books" / "knowledge") if chosen else (bot / "data" / "knowledge")
    if chosen:
        _set(bot, knowledge=str(store.parent))
    (store / "gutenberg").mkdir(parents=True)
    (store / "gutenberg" / "moby.txt").write_text("Plain words.", encoding="utf-8")
    junction(store / "gutenberg" / "jump", _secret(tmp_path))
    assert [p.name for p in _lib(bot)._files()] == ["moby.txt"]


@windows
def test_the_library_never_walks_a_chosen_store_reached_through_a_link(bot, tmp_path, junction):
    real = tmp_path / "real"
    (real / "knowledge").mkdir(parents=True)
    (real / "knowledge" / "keys.txt").write_text("Never index this.", encoding="utf-8")
    link = junction(tmp_path / "up", real)
    _set(bot, knowledge=str(link))
    assert not [p for p in _lib(bot)._files() if p.name == "keys.txt"]


def test_the_library_skips_a_link_file_in_the_store(bot, tmp_path):
    store = bot / "data" / "knowledge" / "gutenberg"
    store.mkdir(parents=True)
    (tmp_path / "keys.txt").write_text("Never index this.", encoding="utf-8")
    try:
        os.symlink(tmp_path / "keys.txt", store / "keys.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not permitted here")
    assert _lib(bot)._files() == []


@windows
def test_the_library_leaves_other_roots_as_they_were(bot, tmp_path, junction):
    """A junction in the data folder outside the store, or in a linked folder, is walked as before."""
    junction(bot / "data" / "jump", _secret(tmp_path))
    linked = tmp_path / "linked"
    linked.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "note.txt").write_text("Linked words.", encoding="utf-8")
    junction(linked / "jump", other)
    _set(bot, linked=[str(linked)])
    assert sorted(p.name for p in _lib(bot)._files()) == ["keys.txt", "note.txt"]


# 7: junk settings and a move

def test_a_move_refuses_junk_settings_and_leaves_the_file_as_it_was(bot, tmp_path):
    path = bot / "settings" / "folders.json"
    path.write_text("not json{", encoding="utf-8")
    for folder in ("", str(tmp_path / "New")):
        with pytest.raises(ks.MoveRefused):
            ks.start(bot, folder, wait=True)
    assert path.read_text(encoding="utf-8") == "not json{"
    assert not (bot / "index" / fetched.MOVE).exists()


# 8: Toolsmith, through a real move

def test_toolsmith_never_reaches_a_store_left_behind(bot, tmp_path):
    from puffpai_core.zone3 import toolsmith_zone as tz
    _seed(bot, 1)
    old = tmp_path / "Old"
    ks.start(bot, str(old), wait=True)
    ks.start(bot, str(tmp_path / "New"), leave_old=True, wait=True)
    assert ks.move_view(bot)["phase"] == "done" and (old / "knowledge" / "gutenberg").is_dir()
    zone = tz.zone_for("", state=str(bot))
    assert zone.classify(str(old / "knowledge" / "gutenberg" / "book-0.txt")) == (tz.DENY, tz.DATA)
    assert zone.classify(str(tmp_path / "New" / "knowledge" / "x")) == (tz.DENY, tz.DATA)


def test_toolsmith_never_reaches_either_place_of_a_stopped_move(bot, tmp_path, monkeypatch):
    from puffpai_core.zone3 import toolsmith_zone as tz
    _seed(bot)
    _different_drives(monkeypatch)
    _stop_at(monkeypatch, 3)
    books = tmp_path / "Books"
    ks.start(bot, str(books), wait=True)
    assert ks.move_view(bot)["phase"] == "stopped"
    zone = tz.zone_for("", state=str(bot))
    assert zone.classify(str(books / "knowledge" / "gutenberg" / "book-0.txt")) == (tz.DENY, tz.DATA)
