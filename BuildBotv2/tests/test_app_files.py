"""A folder holding ``.app-files`` belongs to an app (WB2, 02): Your files, the linked-folder report and the knowledge index
leave it out, and what the index held from it goes at the next pass. Her own folders still show."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing

import pytest

from puffpai_core import owner_files
from puffpai_core.zone1.folders import APP_FILES, Library
from z1kit import make


@pytest.fixture
def state(tmp_path):
    root = tmp_path / "bot"
    (root / "settings").mkdir(parents=True)
    (root / "settings" / "bot.json").write_text('{"name": "Dewey"}', encoding="utf-8")
    return root


def put(path, text="words", age=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age:
        then = time.time() - age
        os.utime(path, (then, then))
    return path


def a_book(state, *, marked=True, text="The tinkerbird sang over the harbor."):
    """A realistic app folder, as Manuscript leaves one."""
    shelf = owner_files.my_files(state) / "Manuscripts"
    book = shelf / "Storm Harbor"
    put(book / "sections" / "01 - One.md", text)
    put(book / "bible.json", '{"note": "%s"}' % text)
    put(book / "world.md", text)
    put(book / "cache" / ("a" * 64 + ".json"), '{"extraction": {"summary": "%s"}}' % text)
    put(shelf / "Removed" / "Old 20261101-000000" / "bible.json", '{"note": "%s"}' % text)
    if marked:
        put(shelf / APP_FILES, "")
    return shelf


def test_a_marked_folder_is_not_listed_and_her_folders_are(state):
    mine = owner_files.my_files(state)
    a_book(state)
    put(mine / "Made by Dewey" / "Synopsis.docx")
    put(mine / "Converted" / "My Novel.docx")
    put(mine / "Taxes" / "2026.xlsx")
    put(mine / "letter.txt")
    rows = owner_files.recent(state)
    assert sorted((r["folder"], r["name"]) for r in rows) == [
        ("", "letter.txt"), ("Converted", "My Novel.docx"), ("Made by Dewey", "Synopsis.docx"), ("Taxes", "2026.xlsx")]


def test_the_same_book_unmarked_is_what_she_saw_before(state):
    a_book(state, marked=False)
    assert len(owner_files.recent(state)) == 5


def test_thirty_newest_of_her_real_documents(state):
    mine = owner_files.my_files(state)
    shelf = a_book(state)
    for i in range(50):
        put(shelf / "Storm Harbor" / "sections" / f"{i:02} - Part.md")          # the newest of all, and none of them shows
    for i in range(35):
        put(mine / "Letters" / f"letter {i:02}.txt", age=100 * (i + 1))
    rows = owner_files.recent(state)
    assert [r["name"] for r in rows] == [f"letter {i:02}.txt" for i in range(30)]


def test_a_mark_counts_at_any_depth_and_nowhere_above(state):
    mine = owner_files.my_files(state)
    put(mine / "Projects" / "App" / APP_FILES, "")
    put(mine / "Projects" / "App" / "inner" / "working.json")
    put(mine / "Projects" / "plan.txt")
    assert [r["name"] for r in owner_files.recent(state)] == ["plan.txt"]


def test_no_path_moves_a_file_in_there_still_opens_by_its_path(state):
    a_book(state)
    got = owner_files.where(state, str(owner_files.my_files(state) / "Manuscripts" / "Storm Harbor" / "world.md"))
    assert got["place"] == "mine"


def test_the_index_does_not_read_a_marked_folder(state):
    a, *_ = make(state)
    a_book(state, text="The tinkerbird sang. Call 415-555-0134 about the password hunter2hunter2hunter2.")
    put(owner_files.my_files(state) / "Taxes" / "note.txt", "The accountant likes walnuts.")
    lib = Library(a, state)
    report = lib.scan()
    assert report["indexed"] == 1 and report["held"] == 0                      # only her own note went through the gate
    assert lib.search("tinkerbird", 5) == []
    assert [os.path.basename(h["path"]) for h in lib.search("walnuts", 5)] == ["note.txt"]


def test_rows_from_before_the_mark_go_at_the_next_pass(state):
    a, *_ = make(state)
    shelf = a_book(state, marked=False)
    lib = Library(a, state)
    assert lib.scan()["indexed"] == 5                                          # 0.4.0: the book's bookkeeping was all indexed
    assert len(lib.search("tinkerbird", 20)) == 5
    put(shelf / APP_FILES, "")
    assert lib.scan()["indexed"] == 0
    assert lib.search("tinkerbird", 20) == []
    with closing(sqlite3.connect(lib.db_path)) as db:
        assert db.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    assert (shelf / "Storm Harbor" / "bible.json").is_file()                   # only the index's rows, never her files


def test_a_document_she_pinned_there_stays_read(state):
    a, *_ = make(state)
    shelf = a_book(state, marked=False)
    lib = Library(a, state)
    lib.scan()
    pin = shelf / "Storm Harbor" / "sections" / "01 - One.md"
    lib.pin(pin)
    put(shelf / APP_FILES, "")
    assert lib.scan()["unchanged"] == 1
    assert [os.path.basename(h["path"]) for h in lib.search("tinkerbird", 20)] == [pin.name]
    assert lib.unpin(pin) and lib.search("tinkerbird", 20) == []


def test_a_linked_folder_report_and_what_chat_can_name_leave_it_out(state, tmp_path):
    a, *_ = make(state)
    linked = tmp_path / "Linked"
    put(linked / "mine.txt", "My own words.")
    put(linked / "app" / APP_FILES, "")
    put(linked / "app" / "working.json", "{}")
    lib = Library(a, state)
    lib.link(linked)
    assert [f["name"] for f in lib.folder_report(linked)["files"]] == ["mine.txt"]
    assert [p.name for p in lib.known_documents()] == ["mine.txt"]
    assert lib.scan()["indexed"] == 1
