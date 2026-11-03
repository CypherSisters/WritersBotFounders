"""D146: the "No vault for this document" switch. One made-up document, the real gate on a real vault, the real Brain and
loop on a scripted model, and the real ``wiring`` tools."""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from puffpai_core import wiring
from puffpai_core.api.app import create_app
from puffpai_core.zone1 import archivist, folders, pinned, uploads
from puffpai_core.zone1 import gate as zone1_gate
from puffpai_core.zone1.folders import FICTION_LINE, Library
from puffpai_core.zone2.gate import production
from z1kit import canary_key, make
from zone2_doubles import FakeRuntime, make_brain

PHONE = "415-555-0137"
OTHER_PHONE = "415-555-0199"
KEY = canary_key()
OTHER_KEY = canary_key()
#: What to search for, and the private words a hit beside it must show.
BESIDE = {"lives": "14 Larkspur Lane", "ring": PHONE, "born": "03/14/1961", "write": "writer@example.com",
          "paid": "4111 1111 1111 1111", "said": "password is swordfish", "lithium": "prescribed", "doctor": "therapist",
          "opens": KEY}
DOC = (f"She lives at 14 Larkspur Lane. Ring {PHONE} by the orchard. Born 03/14/1961 in spring. Write writer@example.com "
       f"soon. Card 4111 1111 1111 1111 paid. The password is swordfish, she said. Lithium was prescribed. Her doctor, a "
       f"therapist. The key {KEY} opens it.")
OTHER = f"The orchard line is {OTHER_PHONE} today. The key {OTHER_KEY} is here."
MIDDLE = "in spring"


@pytest.fixture
def w(state, tmp_path, freshtable, monkeypatch):
    a, bus, *_ = make(state)
    where = str(Path(state).resolve())
    archivist._BY_STATE[where] = a                        # noqa: SLF001 - wiring reaches this vault through seams
    zone1_gate.bind(a)
    p, other = tmp_path / "novel.txt", tmp_path / "other.txt"
    p.write_text(DOC, encoding="utf-8")
    other.write_text(OTHER, encoding="utf-8")
    lib = Library(a, state)
    core = SimpleNamespace(state=state, bus=bus)
    rt = FakeRuntime()
    brain, *_ = make_brain(state, freshtable, rt, settings={"tool_budget": 12}, search=wiring.knowledge_search(core))
    brain._gate = production                              # noqa: SLF001 - the real gate
    brain.pinned = wiring.PinnedDocuments(core)
    sealed: list = []
    monkeypatch.setattr(a.inbox, "seal_held", lambda *x, **k: sealed.append(x))
    yield SimpleNamespace(a=a, lib=lib, p=lib.pin(p), other=lib.pin(other), rt=rt, brain=brain, sealed=sealed, state=state)
    archivist._BY_STATE.pop(where, None)                  # noqa: SLF001
    zone1_gate.bind(None)


def switch(w, on: bool) -> None:
    assert w.lib.set_fiction(w.p, on) is True
    w.lib.scan_file(w.p)


def results(w, *calls: tuple[str, dict]) -> list[str]:
    """What the runtime received as tool results, after ``model_messages``, for one turn with these calls."""
    w.rt.script += [{"tool_calls": [{"id": f"c{i}", "name": n, "arguments": args} for i, (n, args) in enumerate(calls)]},
                    {"text": "Done."}]
    w.brain.turn("Read it to me.", conversation=f"c{len(w.rt.calls)}")
    return [m["content"] for m in w.rt.calls[-1]["messages"] if m.get("role") == "tool"]


READ = ("read_pinned", {"name": "novel"})
SHOW = ("show_passage", {"name": "novel", "passage": MIDDLE})
FIND = ("search_knowledge", {"query": "orchard", "limit": 5})


def chunks(w) -> str:
    with closing(sqlite3.connect(w.lib.db_path)) as db:
        return "\n".join(r[0] for r in db.execute("SELECT text FROM chunks"))


@pytest.mark.parametrize("locked", [False, True])
def test_switch_off_is_todays_gate(w, locked):
    if locked:
        w.a.lock()
    w.lib.scan()
    read, show, find = results(w, READ)[0], results(w, SHOW)[0], results(w, FIND)[0]
    assert "part 1 of 1" in read and "show_passage" in show and "orchard" in find
    for text in (read, show, find):
        assert KEY not in text and "swordfish" not in text
        assert not locked or PHONE not in text
    assert PHONE not in chunks(w) and KEY not in chunks(w)
    assert bool(w.sealed) is locked                       # the recording below is a real one
    assert locked or PHONE in read


@pytest.mark.parametrize("locked", [False, True])
def test_switch_on_every_word_reaches_the_runtime_and_nothing_is_sealed(w, locked, monkeypatch):
    def no_gate(*_a, **_k):
        raise AssertionError("the document's words went through the gate")
    w.lib.unpin(w.other)
    if locked:
        w.a.lock()
    monkeypatch.setattr(folders, "prepare", no_gate)
    monkeypatch.setattr(pinned, "prepare", no_gate)
    assert w.lib.set_fiction(w.p, True) is True and w.lib.is_fiction(w.p)
    assert w.lib.scan_file(w.p) == {"status": "indexed", "reason": folders.FICTION_REASON, "held": 0}
    for text in (results(w, READ)[0], results(w, SHOW)[0]):
        assert all(word in text for word in BESIDE.values()), text
    for query, word in BESIDE.items():
        assert word in results(w, ("search_knowledge", {"query": query}))[0], query
    assert "swordfish" in w.lib.search("swordfish")[0]["snippet"]
    assert w.sealed == []
    row = next(r for r in w.lib.pinned_view() if r["name"] == "novel.txt")
    assert row["fiction"] is True and row["line"] == FICTION_LINE


def test_call_tool_gets_the_plain_words_too(w):
    switch(w, True)
    for tool, args in (READ, SHOW):
        got = str(w.brain.call_tool(tool, args)["result"])
        assert KEY in got and PHONE in got


def test_another_document_in_the_same_turn_is_still_gated(w):
    switch(w, True)
    w.lib.scan_file(w.other)
    w.a.lock()
    plain, gated = results(w, READ, ("read_pinned", {"name": "other"}))
    assert KEY in plain and OTHER_PHONE not in gated and OTHER_KEY not in gated
    both = results(w, FIND)[0]                            # one result with a hit from each
    assert PHONE in both and "other.txt" in both and OTHER_PHONE not in both and OTHER_KEY not in both


def test_off_again_is_the_locked_form_straight_away(w):
    switch(w, True)
    assert PHONE in chunks(w) and KEY in chunks(w)
    w.a.lock()
    w.lib.set_fiction(w.p, False)
    assert w.lib.indexed_text(w.p) == ""
    for text in (results(w, READ)[0], results(w, SHOW)[0]):
        assert PHONE not in text and KEY not in text
    assert type(pinned.read(w.lib, "novel")) is str
    assert not any(word in chunks(w) for word in (PHONE, KEY, "swordfish"))


def test_unpin_drops_it_and_pin_again_is_off(w):
    switch(w, True)
    assert w.lib.unpin(w.p) is True
    assert not w.lib.is_fiction(w.p) and w.lib.indexed_text(w.p) == ""
    w.lib.scan_file(w.lib.pin(w.p))
    assert not w.lib.is_fiction(w.p) and PHONE not in chunks(w)
    assert next(r for r in w.lib.pinned_view() if r["name"] == "novel.txt")["fiction"] is False


def test_unpin_where_a_linked_folder_still_reads_it_gates_it_again(w, tmp_path):
    mine = tmp_path / "Writing"
    mine.mkdir()
    (mine / "linked.txt").write_text(DOC, encoding="utf-8")
    w.lib.link(mine)
    w.p = w.lib.pin(mine / "linked.txt")
    switch(w, True)
    assert PHONE in w.lib.indexed_text(w.p)
    assert w.lib.unpin(w.p) is True
    w.lib.scan()
    text = w.lib.indexed_text(w.p)
    assert text and PHONE not in text and KEY not in text


def test_an_upload_under_the_same_name_keeps_it(w):
    first = uploads.save(w.lib, "kept.txt", io.BytesIO(b"First words."), None)
    w.p = w.lib.pin(first["path"])
    switch(w, True)
    uploads.save(w.lib, "kept.txt", io.BytesIO(f"New words. Ring {PHONE}.".encode()), None)
    assert w.lib.is_fiction(w.p)
    w.lib.scan_file(w.p)
    assert PHONE in w.lib.indexed_text(w.p) and isinstance(pinned.read(w.lib, "kept"), zone1_gate.OwnWords)


def test_the_route(state, tmp_path):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    client = app.test_client()
    p = tmp_path / "My Novel.txt"
    p.write_text("It was the summer the river ran low.", encoding="utf-8")
    assert client.post("/v1/knowledge/pinned", json={"path": str(p)}, headers=dev).status_code == 200
    url = "/v1/knowledge/pinned/fiction"
    for bad in ({"path": str(p), "on": "yes"}, {"path": str(p), "on": 1}, {"path": str(p)}, {"on": True}):
        assert client.post(url, json=bad, headers=dev).status_code == 400
    r = client.post(url, json={"path": str(tmp_path / "no.txt"), "on": True}, headers=dev)
    assert r.status_code == 404 and r.json["error"] == "That file is not pinned."
    for who in (core.tokens.issue_package_token("freshtable"), core.tokens.issue_owner_key()):
        assert client.post(url, json={"path": str(p), "on": True},
                           headers={"Authorization": "Bearer " + who}).status_code == 403
    assert client.post(url, json={"path": str(p), "on": True}).status_code in (401, 403)
    assert client.get("/v1/knowledge/pinned", headers=dev).json["pinned"][0]["fiction"] is False
    row = client.post(url, json={"path": str(p), "on": True}, headers=dev).json["pinned"][0]
    assert row["fiction"] is True and row["line"] == FICTION_LINE
    row = client.post(url, json={"path": str(p), "on": False}, headers=dev).json["pinned"][0]
    assert row["fiction"] is False and row["line"] == "Your bot can find this."
