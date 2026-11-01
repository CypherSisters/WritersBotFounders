"""D125 and D126: a document pinned to the bot, read in parts, and linked folders that read Word and PDF files.

One reader for both. The gate runs as for any file of hers, on the real Archivist. The novel at the end is about
120,000 invented words, in a real .docx and a real .pdf, made by tests/novel_kit.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import zipfile

import pytest

from conftest import REPO
from novel_kit import novel, write_docx, write_pdf
from puffpai_core.api.app import create_app
from puffpai_core.zone1 import gate as zone1_gate
from puffpai_core.zone1 import pinned
from puffpai_core.zone1.folders import FolderRefused, Library, SCAN_LOCK, pinned_paths
from puffpai_core.zone2 import concierge as cg
from puffpai_core.zone2.gate import Gate, production
from z1kit import make
from zone2_doubles import FakeRuntime, make_brain

NODE = shutil.which("node")
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
PHONE = "415-555-0137"


def docx(path, body: str, *, extra: dict | None = None, head: str = ""):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"{head}<w:document {W} {MC}><w:body>{body}</w:body></w:document>")
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return path


def para(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


@pytest.fixture
def lib(state):
    a, *_ = make(state)
    return Library(a, state)


@pytest.fixture
def bound(lib):
    zone1_gate.bind(lib.archivist)
    yield lib
    zone1_gate.bind(None)


# -- one reader -----------------------------------------------------------------------------------

def test_a_word_file_gives_its_words_in_order_and_nothing_it_only_keeps(tmp_path):
    body = (para("Chapter 1") + '<w:p><w:r><w:t>One</w:t><w:tab/><w:t>two</w:t><w:br/><w:t>three</w:t></w:r></w:p>'
            + '<w:p><w:del><w:r><w:delText>struck out</w:delText></w:r></w:del>'
              '<w:moveFrom><w:r><w:t>moved away</w:t></w:r></w:moveFrom>'
              '<w:ins><w:r><w:t>kept in</w:t></w:r></w:ins></w:p>'
            + '<w:p><mc:AlternateContent><mc:Choice><w:r><w:t>in the box</w:t></w:r></mc:Choice>'
              '<mc:Fallback><w:r><w:t>the box again</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p>')
    note = f'<w:footnotes {W}><w:footnote><w:p><w:r><w:t>A footnote.</w:t></w:r></w:p></w:footnote></w:footnotes>'
    got = pinned.read_document(docx(tmp_path / "a.docx", body, extra={"word/footnotes.xml": note}))
    assert got.text == "Chapter 1\n\nOne\ttwo\nthree\n\nkept in\n\nin the box\n\nA footnote."
    assert got.also == ()


@pytest.mark.parametrize("make_it, line", [
    (lambda p: p.write_bytes(b"not a zip at all"), "may have a password"),
    (lambda p: zipfile.ZipFile(p, "w").writestr("other.txt", "x"), "can't be read"),
    (lambda p: docx(p, "<w:p/>"), "no words"),
    (lambda p: docx(p, para("x"), head='<!DOCTYPE w [<!ENTITY a "b">]>'), "can't be read"),
])
def test_a_word_file_that_cant_give_its_words_says_why(tmp_path, make_it, line):
    p = tmp_path / "bad.docx"
    make_it(p)
    with pytest.raises(pinned.Unreadable, match=line):
        pinned.read_document(p)


def test_a_pdf_gives_its_words_and_says_why_when_it_cant(tmp_path):
    from pypdf import PdfReader, PdfWriter
    good = tmp_path / "good.pdf"
    write_pdf("The orchard came in well.\n\nChapter 2", good)
    assert "orchard came in well" in pinned.read_document(good).text
    blank = tmp_path / "scan.pdf"
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.write(blank)
    with pytest.raises(pinned.Unreadable, match="may be a scan"):
        pinned.read_document(blank)
    locked = tmp_path / "locked.pdf"
    w = PdfWriter(clone_from=PdfReader(good))
    w.encrypt("secret-word")
    w.write(locked)
    with pytest.raises(pinned.Unreadable, match="has a password"):
        pinned.read_document(locked)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 and then nothing")
    with pytest.raises(pinned.Unreadable):
        pinned.read_document(bad)


# -- linked folders (D126) ------------------------------------------------------------------------

def linked_folder(lib, tmp_path):
    mine = tmp_path / "Writing"
    mine.mkdir()
    docx(mine / "letter.docx", para(f"Dear aunt, the leeks are in. Call me at {PHONE} when you can."))
    write_pdf("The orchard came in well this year.", mine / "harvest.pdf")
    (mine / "notes.txt").write_text("Plain notes on turnips.", encoding="utf-8")
    (mine / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0 an old Word file")
    (mine / "~$letter.docx").write_bytes(b"Word's own lock file")
    (mine / "desktop.ini").write_text("x", encoding="utf-8")
    lib.link(mine)
    return mine


def test_a_linked_folder_reads_word_and_pdf_through_the_gate(lib, tmp_path):
    mine = linked_folder(lib, tmp_path)
    report = lib.scan()
    assert report["indexed"] == 3 and report["not_indexed"] == [], report
    assert [h["path"] for h in lib.search("leeks", 5)] == [str((mine / "letter.docx").resolve())]
    assert lib.search("orchard", 5) and lib.search("turnips", 5)
    snippet = lib.indexed_text(mine / "letter.docx")
    assert PHONE not in snippet and "{{vault:" in snippet           # held, as for any file of hers
    assert not lib.search("lock", 5)                                # Word's lock file is not a document


def test_the_folders_row_says_what_was_read_and_what_wasnt(lib, tmp_path):
    mine = linked_folder(lib, tmp_path)
    assert lib.folder_report(mine)["waiting"] == 3                  # linked, and not read yet
    assert "waiting to be read" in lib.folder_report(mine)["line"]
    lib.scan()
    got = lib.folder_report(mine)
    assert got["read"] == 3 and got["waiting"] == 0 and got["not_read"] == 1
    assert got["line"] == "3 files read. 1 file BuildBot can't read yet: old.doc."
    # a file it tried and could not read says why
    (mine / "scan.docx").write_bytes(b"not a zip at all")
    lib.scan()
    got = lib.folder_report(mine)
    assert got["not_read"] == 2 and "scan.docx" in got["line"] and "old.doc" in got["line"]
    only = tmp_path / "Only"
    only.mkdir()
    (only / "scan.docx").write_bytes(b"not a zip at all")
    lib.link(only)
    lib.scan()
    assert "may have a password" in lib.folder_report(only)["line"]


def test_a_file_that_cant_be_read_is_not_opened_again_at_every_scan(lib, tmp_path, monkeypatch):
    mine = tmp_path / "Scans"
    mine.mkdir()
    (mine / "bad.pdf").write_bytes(b"%PDF-1.4 and then nothing")
    lib.link(mine)
    lib.scan()
    opened = []
    real = pinned.read_document
    monkeypatch.setattr(pinned, "read_document", lambda p: (opened.append(p), real(p))[1])
    assert len(lib.scan()["not_indexed"]) == 1 and opened == []
    time.sleep(0.05)
    (mine / "bad.pdf").write_bytes(b"%PDF-1.4 and then something else")
    lib.scan()
    assert len(opened) == 1                                         # she changed it: read again


def test_the_books_store_is_not_read_as_her_documents(lib, state):
    """A fetched PDF is public, and its text is beside it: reading it as hers would fill her vault."""
    from puffpai_core.zone1.fetched import root
    store = root(state)
    store.mkdir(parents=True, exist_ok=True)
    write_pdf("A public book, with 14 Larkspur Lane in it.", store / "book.pdf")
    assert all(p.name != "book.pdf" for p in lib._files())          # noqa: SLF001


def test_linking_reads_the_folder_now_and_a_search_never_waits_for_it(state, tmp_path):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    mine = tmp_path / "Recipes"
    mine.mkdir()
    docx(mine / "soup.docx", para("Leek soup."))
    client = app.test_client()
    assert client.post("/v1/folders/link", json={"path": str(mine)}, headers=dev).status_code == 200
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        row = client.get("/v1/folders", headers=dev).json["linked"][0]
        if row.get("read") == 1:
            break
        time.sleep(0.1)
    assert row["read"] == 1 and row["line"] == "1 file read.", row
    from puffpai_core import wiring
    with SCAN_LOCK:                                                  # a long scan is running
        assert wiring.knowledge_search(core)("leek", 5)              # and the search answers from what is read


# -- pinning (D125) -------------------------------------------------------------------------------

def test_pin_list_and_unpin_and_the_file_never_changes(lib, tmp_path):
    p = tmp_path / "My Novel.docx"
    docx(p, para("It was the summer the river ran low."))
    before = p.read_bytes(), p.stat().st_mtime
    got = lib.pin(p)
    assert lib.pinned() == [got] and pinned_paths(lib.state) == [got]
    with pytest.raises(FolderRefused, match="pinned already"):
        lib.pin(p)
    lib.scan_file(got)
    row = lib.pinned_view()[0]
    assert row["name"] == "My Novel.docx" and row["there"] is True and row["line"] == "Ready."
    assert lib.search("river", 5)
    assert lib.unpin(p) is True and lib.pinned() == [] and not lib.search("river", 5)
    assert lib.unpin(p) is False
    assert (p.read_bytes(), p.stat().st_mtime) == before


def test_unpinning_a_file_a_linked_folder_also_reads_leaves_it_searchable(lib, tmp_path):
    mine = tmp_path / "Writing"
    mine.mkdir()
    (mine / "a.txt").write_text("Turnips and more turnips.", encoding="utf-8")
    lib.link(mine)
    lib.pin(mine / "a.txt")
    lib.scan()
    assert lib.unpin(mine / "a.txt") and lib.search("turnips", 5)


def test_a_missing_drive_never_loses_a_pin(lib, tmp_path):
    p = tmp_path / "away.txt"
    p.write_text("Words on a drive that goes away.", encoding="utf-8")
    got = lib.pin(p)
    lib.scan()
    p.unlink()
    lib.scan()
    assert lib.pinned() == [got]
    row = lib.pinned_view()[0]
    assert row["there"] is False and "stays pinned" in row["line"]
    answer = pinned.read(lib, "away.txt")
    assert answer["ok"] is False and "stays pinned" in answer["error"]
    p.write_text("Words on a drive that goes away.", encoding="utf-8")
    assert "Words on a drive" in pinned.read(lib, "away.txt")


def test_a_row_says_what_the_file_gave(lib, tmp_path):
    p = tmp_path / "held.txt"
    p.write_text(f"Call me at {PHONE} about the orchard.", encoding="utf-8")
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip at all")
    for f in (p, bad):
        lib.scan_file(lib.pin(f))
    lines = {r["name"]: r["line"] for r in lib.pinned_view()}
    assert "1 detail looks private" in lines["held.txt"] and "while your vault is open" in lines["held.txt"]
    assert "may have a password" in lines["bad.docx"]


def test_the_routes(state, tmp_path):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    client = app.test_client()
    p = tmp_path / "My Novel.docx"
    docx(p, para("It was the summer the river ran low."))
    assert client.get("/v1/knowledge/pinned", headers=owner).json["pinned"] == []
    assert client.get("/v1/knowledge/pinned").status_code in (401, 403)
    assert client.post("/v1/knowledge/pinned", json={"path": str(p)}, headers=owner).status_code == 403
    r = client.post("/v1/knowledge/pinned", json={"path": f'"{p}"'}, headers=dev)    # Copy as path adds the quotes
    assert r.status_code == 200 and r.json["pinned"][0]["name"] == "My Novel.docx", r.json
    assert r.headers["Cache-Control"] == "no-store"
    assert client.post("/v1/knowledge/pinned", json={"path": str(p)}, headers=dev).status_code == 400
    for bad in ("", 7, "x" * 1001):
        assert client.post("/v1/knowledge/pinned", json={"path": bad}, headers=dev).status_code == 400
    assert client.post("/v1/knowledge/pinned", json={"path": str(tmp_path / "no.docx")}, headers=dev).status_code == 400
    assert client.post("/v1/knowledge/pinned/remove", json={"path": str(tmp_path / "no.docx")}, headers=dev).status_code == 404
    assert client.post("/v1/knowledge/pinned/remove", json={"path": str(p)}, headers=owner).status_code == 403
    r = client.post("/v1/knowledge/pinned/remove", json={"path": str(p)}, headers=dev)
    assert r.status_code == 200 and r.json["pinned"] == []


# -- by name, in parts ----------------------------------------------------------------------------

def test_by_name(tmp_path):
    a, b, c = tmp_path / "x" / "My Novel.docx", tmp_path / "y" / "My Novel.docx", tmp_path / "Notes.md"
    assert pinned.named([c], "") == c and pinned.named([a, c], "") is None
    assert pinned.named([c], "notes") == c and pinned.named([c], "NOTES.MD") == c and pinned.named([c], "note") == c
    assert pinned.named([c], "other") is None
    assert list(pinned.names([a, b])) == ["x\\My Novel.docx", "y\\My Novel.docx"]
    assert pinned.named([a, b], "My Novel") is None and pinned.named([a, b], "y\\My Novel.docx") == b


def test_parts_cut_no_word_and_lose_nothing():
    text = " ".join(f"word{i}" for i in range(4000)) + "\n\n" + "x" * (pinned.PART * 2 + 5)
    starts = pinned.part_starts(text)
    ends = starts[1:] + [len(text)]
    assert all(e - s <= pinned.PART for s, e in zip(starts, ends))
    assert "".join(text[s:e] for s, e in zip(starts, ends)) == text
    for s in starts[1:]:
        if text[s].startswith("w"):
            assert text[s - 1] in " \n"                              # no word cut, while there is a place to cut
    assert pinned.part_starts("short") == [0]


def test_read_goes_part_by_part_and_find_names_the_parts(lib, tmp_path):
    p = tmp_path / "long.txt"
    p.write_text("\n\n".join(f"Chapter {i}. " + "The river ran low. " * 400 for i in range(1, 6)), encoding="utf-8")
    lib.pin(p)
    first = pinned.read(lib, "long")
    assert first.startswith("long.txt, part 1 of ") and "Part 2 is next." in first and "Chapter 1." in first
    of = int(first.split(" of ")[1].split(".")[0])
    last = pinned.read(lib, "long", of)
    assert f"part {of} of {of}" in last and "That is the end." in last
    assert "has parts 1 to" in pinned.read(lib, "long", of + 1)["error"]
    assert "Pinned: long.txt" in pinned.read(lib, "nothing")["error"]
    got = pinned.read(lib, "long", find="chapter 3")
    assert len(got["parts"]) == 1 and "Chapter 3." in pinned.read(lib, "long", got["parts"][0])
    assert pinned.read(lib, "long", find="zebra")["parts"] == []
    # she changes the file: the next read has the new words, through the gate
    time.sleep(0.05)
    p.write_text(f"Now a short file. Call {PHONE}.", encoding="utf-8")
    assert "Now a short file" in pinned.read(lib, "long") and PHONE not in pinned.read(lib, "long")


def test_a_local_brain_with_the_vault_open_gets_real_values_and_anyone_else_handles(state, tmp_path, monkeypatch):
    p = tmp_path / "held.txt"
    p.write_text(f"Call me at {PHONE}. The password is swordfish, and the orchard is fine.", encoding="utf-8")
    a, *_ = make(state)
    lib = Library(a, state)
    lib.pin(p)
    mine = pinned.read(lib, "held", real=True)
    assert PHONE in mine and "swordfish" not in mine and "{{vault:" in mine           # keys and passwords never
    other = pinned.read(lib, "held")
    assert PHONE not in other and "swordfish" not in other and "orchard" in other
    a.lock()
    sealed = []
    monkeypatch.setattr(a.inbox, "seal_held", lambda *x, **k: sealed.append(x))
    locked = pinned.read(Library(a, state), "held", real=True)
    assert PHONE not in locked and "swordfish" not in locked and "orchard" in locked
    assert sealed == []                                                                # nothing new is sealed


def test_a_path_that_turns_bad_after_the_pin_is_not_read(lib, tmp_path, monkeypatch):
    p = tmp_path / "x.txt"
    p.write_text("Words.", encoding="utf-8")
    lib.pin(p)
    from puffpai_core.zone1 import folders
    monkeypatch.setattr(folders, "file_problem", lambda *_a, **_k: "That file is part of your vault.")
    answer = pinned.read(lib, "x")
    assert answer["ok"] is False and "part of your vault" in answer["error"]
    assert all(f != p.resolve() for f in lib._files())               # noqa: SLF001


# -- the brain ------------------------------------------------------------------------------------

class Pins:
    def __init__(self, lib):
        self.lib, self.calls = lib, []

    def names(self):
        return list(pinned.names(self.lib.pinned()))

    def read(self, name, part, find, real):
        self.calls.append((name, part, find, real))
        return pinned.read(self.lib, name, part, find, real=real)


def brain_for(state, lib, freshtable, rt):
    brain, *_ = make_brain(state, freshtable, rt, settings={"tool_budget": 12})
    brain.pinned = Pins(lib)
    return brain


def tools_of(rt, n=0):
    return [t["function"]["name"] for t in rt.calls[n]["tools"]]


def test_a_bot_with_nothing_pinned_has_no_tool_and_no_line(state, lib, freshtable):
    rt = FakeRuntime({"text": "hi"})
    brain_for(state, lib, freshtable, rt).turn("hello")
    assert cg.READ_PINNED not in tools_of(rt)
    assert "pinned" not in rt.calls[0]["messages"][0]["content"]


def test_every_new_chat_knows_it_by_name_and_never_gets_its_words_in_the_prompt(state, lib, freshtable, tmp_path):
    p = tmp_path / "My Novel.docx"
    docx(p, para("It was the summer the river ran low."))
    lib.scan_file(lib.pin(p))
    rt = FakeRuntime({"text": "one"}, {"text": "two"})
    brain = brain_for(state, lib, freshtable, rt)
    brain.turn("hello", conversation="one")
    brain.turn("hello again", conversation="two")
    for n in (0, 1):
        prompt = rt.calls[n]["messages"][0]["content"]
        assert "- My Novel.docx" in prompt and "river" not in prompt and cg.READ_PINNED in tools_of(rt, n)


def test_the_brain_reads_it_in_a_turn_through_the_gate(state, bound, freshtable, tmp_path):
    p = tmp_path / "My Novel.docx"
    docx(p, para(f"It was the summer the river ran low. Call {PHONE}. The password is swordfish."))
    bound.scan_file(bound.pin(p))
    rt = FakeRuntime({"tool_calls": [{"id": "c1", "name": "read_pinned", "arguments": {"name": "My Novel"}}]},
                     {"text": "The river."})
    brain, *_ = make_brain(state, freshtable, rt, settings={"tool_budget": 12})
    brain._gate = production                                          # noqa: SLF001 - the real gate
    brain.pinned = Pins(bound)
    got = brain.turn("what happens first in my novel?")
    assert got["used"] == [cg.READ_PINNED] and brain.pinned.calls[0][3] is True    # this computer's brain
    result = next(m for m in rt.calls[1]["messages"] if m.get("role") == "tool")["content"]
    assert "part 1 of 1" in result and "river ran low" in result and "swordfish" not in result


def test_a_document_not_found_points_at_the_knowledge_page(state, freshtable):
    table_tool = {t.name: t for t in cg.Concierge(None, state, keep=str, search=lambda *_a: []).core_tools(lambda: None)}
    said = table_tool["search_knowledge"].fn({"query": "x"}, {})["said"]
    assert "Knowledge page" in said and "Your documents" in said and "no other place" in said
    assert "Knowledge page" in table_tool["search_knowledge"].description


# -- the page -------------------------------------------------------------------------------------

@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_page_logic():
    web = REPO / "packages" / "desktop" / "web"
    for f in ("devzones.js", "pages/knowledge.js", "window.js"):
        assert subprocess.run([NODE, "--check", str(web / f)], capture_output=True, timeout=60).returncode == 0
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "d125.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr


def test_the_knowledge_page_is_the_one_place(state):
    web = REPO / "packages" / "desktop" / "web"
    page = (web / "pages" / "knowledge.js").read_text(encoding="utf-8")
    for words in ("Your documents", "Pin this file", "Unpin", "Link this folder", "Unlink", "/v1/knowledge/pinned",
                  "/v1/knowledge/pinned/remove", "/v1/folders/link", "/v1/folders/unlink", "Pick a file", "Pick a folder"):
        assert words in page, words
    assert '"/link": ["knowledge"]' in (web / "window.js").read_text(encoding="utf-8")
    for shell in ("index.html", "window.html"):
        html = (web / shell).read_text(encoding="utf-8")
        assert html.index("devzones.js") < html.index("pages/knowledge.js")


def test_the_knowledge_page_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    """Real Edge, real Core, every press a real click: link a folder with a Word file, see what it read and what it
    couldn't, pin a file, unpin it, unlink the folder."""
    import socket
    from test_i10a_desktop_clicks import EDGE, WHERE, _desktop, _point, _text_point, _type
    if os.name != "nt" or EDGE is None:
        pytest.skip("Windows Job Objects and Microsoft Edge")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()
    mine = tmp_path / "Writing"
    mine.mkdir()
    docx(mine / "letter.docx", para("Dear aunt, the leeks are in."))
    (mine / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0 an old Word file")
    novel_file = tmp_path / "My Novel.docx"
    docx(novel_file, para("It was the summer the river ran low."))

    def tile_has(text):
        return f"!!document.querySelector('#view-knowledge [data-tile=folders]') && document.querySelector('#view-knowledge [data-tile=folders]').textContent.indexOf({json.dumps(text)}) >= 0"

    with _desktop(state, tmp_path, monkeypatch, complete=True, env={"OLLAMA_HOST": f"127.0.0.1:{closed_port}"}) as b:
        b.click(*_point(b, ".rail a.item[href='#knowledge']", 0))
        assert b.wait(tile_has("Your documents"), timeout_s=30), b.js(WHERE)
        assert b.js(tile_has("0 PINNED, 0 LINKED"))
        b.js("document.querySelectorAll('[data-tile=folders] details').forEach(d => d.open = true)")
        _type(b, "#view-knowledge input[aria-label='A folder to link']", str(mine))
        b.click(*_text_point(b, "#view-knowledge [data-tile=folders] button", "Link this folder"))
        assert b.wait(tile_has("0 PINNED, 1 LINKED"), timeout_s=30), b.js(WHERE)
        assert b.wait(tile_has("BuildBot can't read yet: old.doc"), timeout_s=30)
        b.js("document.querySelectorAll('[data-tile=folders] details').forEach(d => d.open = true)")
        _type(b, "#view-knowledge input[aria-label='A file to pin']", f'"{novel_file}"')
        b.click(*_text_point(b, "#view-knowledge [data-tile=folders] button", "Pin this file"))
        assert b.wait(tile_has("1 PINNED, 1 LINKED"), timeout_s=30), b.js(WHERE)
        assert b.js(tile_has("My Novel.docx")) and b.js(tile_has("Ready."))
        b.js("document.querySelectorAll('[data-tile=folders] details').forEach(d => d.open = true)")
        # a bad path says why, in the tile
        _type(b, "#view-knowledge input[aria-label='A file to pin']", str(tmp_path / "nope.docx"))
        b.click(*_text_point(b, "#view-knowledge [data-tile=folders] button", "Pin this file"))
        assert b.wait(tile_has("That file isn't there."), timeout_s=30), b.js(WHERE)
        b.click(*_text_point(b, "#view-knowledge [data-tile=folders] button", "Unpin"))
        assert b.wait(tile_has("0 PINNED, 1 LINKED"), timeout_s=30), b.js(WHERE)
        b.click(*_text_point(b, "#view-knowledge [data-tile=folders] button", "Unlink"))
        assert b.wait(tile_has("0 PINNED, 0 LINKED"), timeout_s=30), b.js(WHERE)
        # the window draws the same page in its side pane, with the same login
        b.js("location.assign('/window/')")
        assert b.wait("!!document.querySelector('[data-rail=knowledge]')", timeout_s=30), b.js(WHERE)
        b.click(*_point(b, "[data-rail=knowledge]", 0))
        tile = "[data-tile=folders]"
        assert b.wait(f"!!document.querySelector('{tile}')", timeout_s=30), b.js(WHERE)
        opened = f"document.querySelectorAll('{tile} details').forEach(d => d.open = true)"
        b.js(opened)
        _type(b, "input[aria-label='A file to pin']", str(novel_file))
        b.click(*_text_point(b, f"{tile} button", "Pin this file"))
        here = f"document.querySelector('{tile}').textContent.indexOf('1 PINNED, 0 LINKED') >= 0"
        assert b.wait(here, timeout_s=30), b.js(WHERE)
        b.js(opened)
        _type(b, "input[aria-label='A folder to link']", str(mine))
        b.click(*_text_point(b, f"{tile} button", "Link this folder"))
        assert b.wait(here.replace("0 LINKED", "1 LINKED"), timeout_s=30), b.js(WHERE)
    assert novel_file.exists() and (mine / "letter.docx").exists()


# -- a novel, whole -------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["docx", "pdf"])
def test_a_novel_reads_whole_and_almost_none_of_it_is_held(state, tmp_path, kind):
    """The real risk: a novel full of names, read back through the real gate, part by part. Every full name stays. A
    few values that look private (a street address, a birthday, an illness said about someone) are held, and a local
    brain with the vault open gets them back; only password-shaped words stay handles."""
    text = novel(120_000)
    p = tmp_path / f"novel.{kind}"
    (write_docx if kind == "docx" else write_pdf)(text, p)
    a, *_ = make(state)
    zone1_gate.bind(a)
    try:
        lib = Library(a, state)
        started = time.monotonic()
        got = lib.scan_file(lib.pin(p))
        scan_s = time.monotonic() - started
        assert got["status"] == "indexed"
        gate = Gate(prepare=production().prepare, vault_open=a.is_open)
        first = pinned.read(lib, f"novel.{kind}", real=True)
        of = int(first.split(" of ")[1].split(".")[0])
        assert of > 120
        read = []
        handles = 0
        for n in range(1, of + 1):
            part = pinned.read(lib, f"novel.{kind}", n, real=True)
            assert isinstance(part, str) and len(part) < 4200
            model = gate.clean(part, for_model=True, real_values=True)
            handles += model.count("{{vault:")
            read.append(model.split("\n\n", 1)[1])
        words = " ".join(read).split()
        assert len(words) > 110_000
        names = ["Margaret Hale", "Thomas Ashby", "Eleanor Vance", "Samuel Oakes", "Ruth Calloway", "Daniel Pryce",
                 "Clara Whitlock", "Henry Marsh", "Ida Bellamy", "Walter Finch", "Josephine Reyes", "Arthur Lund"]
        joined = " ".join(words)
        assert all(joined.count(n) > 500 for n in names)
        assert handles <= 40                                         # only the password lines, of about 18,000 sentences
        held = lib.indexed_text(p).count("{{vault:")
        assert 0 < held < 400
        print(f"\n{kind}: {p.stat().st_size} bytes, {of} parts, scan {scan_s:.1f}s, held {held}, handles to a local "
              f"brain {handles}, {len(words)} words read back")
    finally:
        zone1_gate.bind(None)


# -- D140: show a fix and the passage it means ------------------------------------------------------------------------

def _shown_doc(state, tmp_path):
    from puffpai_core.zone1 import pinned
    a, *_ = make(state)
    lib = Library(a, state)
    f = tmp_path / "My Novel.txt"
    words = "Opening words go here. " * 400 + 'She said, "the river ran low that summer," and  walked\non. ' + "Closing words go here. " * 400
    f.write_text(words, encoding="utf-8")
    before = f.read_bytes()
    lib.pin(f)
    return pinned, lib, f, before


def test_show_finds_the_exact_words_and_changes_nothing(state, tmp_path):
    pinned, lib, f, before = _shown_doc(state, tmp_path)
    got = pinned.show(lib, "My Novel", "the river ran low", "the river ran dry", "Stronger.")
    assert got["ok"] and got["show_passage"] and got["document"] == "My Novel.txt"
    assert got["passage"] == "the river ran low" and got["suggestion"] == "the river ran dry" and got["why"] == "Stronger."
    assert got["before"].endswith('She said, "') and got["after"].startswith(" that summer")
    text = lib.indexed_text(f)
    assert 1 <= got["part"] <= got["of"] and "the river ran low" in pinned.read(lib, "My Novel", got["part"])
    assert len(got["before"]) <= pinned.AROUND and len(got["after"]) <= pinned.AROUND and text
    assert f.read_bytes() == before                                # read only


def test_show_forgives_spacing_case_and_quote_marks_but_never_guesses(state, tmp_path):
    pinned, lib, _f, _b = _shown_doc(state, tmp_path)
    loose = pinned.show(lib, "My Novel", "\u201cTHE river ran low that summer,\u201d and walked on.")
    assert loose["ok"] and loose["passage"].startswith('"the river') and "\n" in loose["passage"]
    for wrong in ("the river ran high", "", "x" * (pinned.PASSAGE_MAX + 1)):
        got = pinned.show(lib, "My Novel", wrong)
        assert got["ok"] is False and got["error"]
    assert pinned.show(lib, "Nothing here", "the river")["ok"] is False


def test_show_starts_at_the_part_she_read_and_falls_back(state, tmp_path):
    pinned, lib, _f, _b = _shown_doc(state, tmp_path)
    of = pinned.show(lib, "My Novel", "Closing words")["of"]
    late = pinned.show(lib, "My Novel", "Opening words", part=of)   # nothing after there: found from the start
    assert late["ok"] and late["part"] == 1
    assert pinned.show(lib, "My Novel", "Closing words", part=of)["part"] == of


def test_the_loop_shows_the_card_and_the_model_gets_the_result():
    from puffpai_core.zone2 import loop
    from puffpai_core.zone2.tools import ToolTable, make
    result = {"ok": True, "show_passage": True, "document": "N.txt", "part": 2, "of": 3, "before": "a ", "passage": "b", "after": " c",
              "suggestion": "d", "why": "e", "said": "She sees it."}
    table = ToolTable()
    table.add(make("show_passage", "x", {"type": "object", "properties": {}}, lambda _a, _c: result))
    import itertools
    replies = itertools.chain([{"content": "", "tool_calls": [{"name": "show_passage", "arguments": {}}]}], itertools.repeat({"content": "Done.", "tool_calls": []}))
    events = []
    got = loop.run([{"role": "user", "content": "go"}], table=table, ask_model=lambda *a, **k: next(replies), to_model=str,
                   emit=events.append, ctx={})
    card = [e for e in events if e["type"] == "passage"]
    assert card and card[0]["passage"] == "b" and card[0]["suggestion"] == "d" and "said" not in card[0]
    assert got["changed"] == []
