"""D136 part 1: Office files, ported from v1's ``office_docs.py``. A built-in ``tool`` package (D17) over the Core's one
rule for her own files (02 ``needs.owner_files``, ``owner_files.py``). The three libraries live in the package's own
venv, so these tests skip where they are not installed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from conftest import REPO
from puffpai_core import owner_files, wiring
from puffpai_core.api.app import create_app
from puffpai_core.zone1.folders import Library
from z1kit import make

FOLDER = REPO / "packages" / "office"
MANIFEST = json.loads((FOLDER / "puffpai.json").read_text(encoding="utf-8"))


def _server():
    spec = importlib.util.spec_from_file_location("office_server_d136", FOLDER / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


server = _server()


class Files:
    """What ``GET /v1/owner-files`` answers, straight from the Core's rule."""

    def __init__(self, state):
        self.state = state

    def made_folder(self):
        return str(owner_files.made_folder(self.state))

    def where(self, path):
        return owner_files.where(self.state, path)


@pytest.fixture
def libs():
    return (pytest.importorskip("docx"), pytest.importorskip("openpyxl"), pytest.importorskip("pptx"))


@pytest.fixture
def linked(state, tmp_path):
    a, *_ = make(state)
    folder = tmp_path / "Linked"
    folder.mkdir()
    Library(a, state).link(folder)
    return folder


@pytest.fixture
def office(state):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "bot.json").write_text('{"name": "GardenBot"}', encoding="utf-8")
    return server.Office(Files(state))


def _path_in(line):
    return line.split(" in your files: ", 1)[1].rsplit(". Open it", 1)[0]


# -- making, v1's tests ---------------------------------------------------------------------------

def test_a_word_file_goes_in_made_by_and_never_overwrites(state, office, libs):
    docx = libs[0]
    line = office.make_document("word", "Plant plan", "# Spring\n\nWater daily.\n\n- tomatoes\n- basil")
    path = _path_in(line)
    assert os.path.dirname(path) == str(state / "data" / "My files" / "Made by GardenBot")
    assert "Knowledge page" in line
    text = [p.text for p in docx.Document(path).paragraphs]
    assert text[:3] == ["Plant plan", "Spring", "Water daily."] and "basil" in text
    again = _path_in(office.make_document("word", "Plant plan", "x"))
    assert again != path and again.endswith("Plant plan (2).docx")


def test_excel_and_powerpoint(office, libs):
    _docx, openpyxl, pptx = libs
    ws = openpyxl.load_workbook(_path_in(office.make_document("excel", "Costs", "item,price\nsoil,12\nseeds,3.5"))).active
    assert [[c.value for c in r] for r in ws.iter_rows()] == [["item", "price"], ["soil", 12], ["seeds", 3.5]]
    deck = pptx.Presentation(_path_in(office.make_document("powerpoint", "Talk", "# One\n- a\n- b\n---\nTwo\nc")))
    assert [s.shapes.title.text for s in deck.slides] == ["Talk", "One", "Two"]
    with pytest.raises(server.Refused, match="word, excel or powerpoint"):
        office.make_document("pdf", "x", "y")


def test_a_title_excel_would_refuse_as_a_sheet_name_still_makes_a_file(office, libs):
    path = _path_in(office.make_document("excel", "Costs [2026]: what's left", "a,b"))
    assert libs[1].load_workbook(path).active.max_row == 1


def test_a_held_detail_is_said_not_hidden(office, libs):
    line = office.make_document("word", "Letter", "Dear Ann, I live at {{vault:abc123}}.")
    assert "1 private detail from your vault" in line
    assert "private detail" not in office.make_document("word", "Plain", "Hello.")


# -- changing ------------------------------------------------------------------------------------

def test_its_own_files_change_each_way(office, libs):
    docx, openpyxl, pptx = libs
    w = _path_in(office.make_document("word", "Notes", "Hello Bob."))
    assert "Changed Notes.docx" in office.edit_document(w, [{"replace": "Bob", "with": "Ann"}, {"append": "Bye."}])
    text = [p.text for p in docx.Document(w).paragraphs]
    assert "Hello Ann." in text and text[-1] == "Bye."
    x = _path_in(office.make_document("excel", "Sheet", "a,b"))
    office.edit_document(x, [{"rows": [["c", 2]]}])
    assert [c.value for c in list(openpyxl.load_workbook(x).active.iter_rows())[-1]] == ["c", 2]
    p = _path_in(office.make_document("powerpoint", "Deck", "intro"))
    office.edit_document(p, [{"append": "# Extra\n- one"}])
    assert len(pptx.Presentation(p).slides) == 3
    assert office.edit_document(w, [{"replace": "nothing like this", "with": "x"}]) == "Nothing in it matched; it is unchanged."


def test_a_bare_name_finds_what_the_bot_made(office, libs):
    office.make_document("word", "Packing", "Socks.")
    office.edit_document("Packing.docx", [{"append": "Hat."}])
    made = owner_files.where(office.files.state, "Packing.docx")
    assert made["place"] == "made" and [p.text for p in libs[0].Document(made["path"]).paragraphs][-1] == "Hat."


def test_a_linked_file_keeps_its_original_each_time(office, linked, libs):
    openpyxl = libs[1]
    budget = linked / "budget.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["rent", 900])
    wb.save(str(budget))
    line = office.edit_document(str(budget), [{"rows": [["food", 300]]}])
    assert "budget (before BuildBot).xlsx" in line
    before = linked / "budget (before BuildBot).xlsx"
    assert [[c.value for c in r] for r in openpyxl.load_workbook(str(before)).active.iter_rows()] == [["rent", 900]]
    assert openpyxl.load_workbook(str(budget)).active.max_row == 2
    office.edit_document(str(budget), [{"rows": [["x", 1]]}])
    assert (linked / "budget (before BuildBot) (2).xlsx").exists()


def test_her_own_file_keeps_no_copy(state, office, libs):
    docx = libs[0]
    mine = state / "data" / "letter.docx"
    mine.parent.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    doc.add_paragraph("Dear Bob")
    doc.save(str(mine))
    office.edit_document(str(mine), [{"append": "Hi"}])
    assert not list(mine.parent.glob("*before BuildBot*"))


def test_what_it_wont_touch(state, office, tmp_path, libs):
    outside = tmp_path / "elsewhere.docx"
    outside.write_bytes(b"x")
    with pytest.raises(server.Refused, match="data folder"):
        office.edit_document(str(outside), [{"append": "x"}])
    with pytest.raises(server.Refused, match="could not find"):
        office.edit_document(str(tmp_path / "nope.docx"), [{"append": "x"}])
    notes = state / "data" / "notes.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("x", encoding="utf-8")
    with pytest.raises(server.Refused, match="Word, Excel and PowerPoint"):
        office.edit_document(str(notes), [{"append": "x"}])
    junk = state / "data" / "junk.docx"
    junk.write_bytes(b"not a word file")
    with pytest.raises(server.Refused, match="could not open"):
        office.edit_document(str(junk), [{"append": "x"}])
    assert junk.read_bytes() == b"not a word file"


# -- the Core's rule ------------------------------------------------------------------------------

def test_the_one_reading_of_a_path(state, linked, tmp_path):
    made = owner_files.made_folder(state)
    (made / "a.docx").write_bytes(b"x")
    assert owner_files.where(state, str(made / "a.docx"))["place"] == "made"
    (state / "data" / "b.docx").write_bytes(b"x")
    assert owner_files.where(state, str(state / "data" / "b.docx"))["place"] == "mine"
    (linked / "c.docx").write_bytes(b"x")
    assert owner_files.where(state, str(linked / "c.docx"))["place"] == "linked"
    (tmp_path / "d.docx").write_bytes(b"x")
    assert owner_files.where(state, str(tmp_path / "d.docx"))["line"] == owner_files.OUTSIDE
    (state / "vault").mkdir(exist_ok=True)
    (state / "vault" / "e.docx").write_bytes(b"x")
    assert owner_files.where(state, str(state / "vault" / "e.docx"))["line"] == owner_files.OUTSIDE
    assert owner_files.where(state, "")["line"] == owner_files.NO_FILE
    assert owner_files.where(state, "bad\x00.docx")["line"] == owner_files.NO_FILE
    assert owner_files.where(state, "nope.docx")["line"] == owner_files.NOT_FOUND


def test_the_question_for_each_place(state, linked, tmp_path):
    owner_files.set_edits(state, True)
    ask = lambda path, tool="edit_document", m=MANIFEST: owner_files.question(state, m, tool, {"path": path})  # noqa: E731
    made = owner_files.made_folder(state)
    (made / "a.docx").write_bytes(b"x")
    assert ask(str(made / "a.docx")) is None                     # its own file: at once
    (state / "data" / "letter.docx").write_bytes(b"x")
    assert ask(str(state / "data" / "letter.docx"))["title"] == "May I change letter.docx?"
    (linked / "budget.xlsx").write_bytes(b"x")
    q = ask(str(linked / "budget.xlsx"))
    assert q["title"] == ("This file is in a folder you linked. BuildBot normally never changes those. Change it anyway?")
    assert "A copy of the original is kept" in q["what"]
    (tmp_path / "d.docx").write_bytes(b"x")
    assert ask(str(tmp_path / "d.docx")) == {"line": owner_files.OUTSIDE}
    # no such field, another tool, no path: this rule says nothing, so no other package changes at all
    assert ask(str(tmp_path / "d.docx"), m={"abilities": {"changes": ["edit_document"]}}) is None
    assert ask(str(tmp_path / "d.docx"), tool="other") is None
    assert owner_files.question(state, MANIFEST, "edit_document", {}) is None


class DB:
    def __init__(self, verdict="allow"):
        self.verdict = verdict

    def review_action(self, package, tool, args, *, labels=(), caller=""):
        return {"verdict": self.verdict, "reason_code": "in_scope", "review_id": "rv_1", "floor": False, "line": "No."}

    def see_tools(self, *a, **k):
        pass


def _review(state, manifest=MANIFEST, verdict="allow", answer=None):
    app = create_app(state)
    core = app.extensions["puffpai"]
    core.steward.read_manifest = lambda _id: manifest
    cards = []

    def notify(card):
        cards.append(card)
        if answer:
            core.consent.answer(card["consent_id"], answer, "tap", by="owner", card_sha=card["card_sha"])
    return wiring.tool_review(core, DB(verdict), wait_s=0.5), notify, cards


def test_a_yes_runs_her_file_once_and_a_no_never(state, linked):
    owner_files.set_edits(state, True)
    (state / "data").mkdir(exist_ok=True)
    (state / "data" / "letter.docx").write_bytes(b"x")
    path = str(state / "data" / "letter.docx")
    call = {"package": "office", "name": "edit_document", "args": {"path": path, "changes": [{"append": "x"}]},
            "summary": [f"path: {path}"]}
    review, notify, cards = _review(state, answer="yes")
    got = review(dict(call, notify=notify))
    assert got["run"] is True and got["asked"] is True
    assert cards[0]["card"]["title"] == "May I change letter.docx?" and cards[0]["card"]["tool"] == "edit_document"
    review, notify, _cards = _review(state, answer="no")
    got = review(dict(call, notify=notify))
    assert got["run"] is False and "said No" in got["line"]
    review, notify, _cards = _review(state)                       # no answer in time
    assert review(dict(call, notify=notify))["run"] is False


def test_the_review_runs_its_own_file_and_refuses_outside_with_no_card(state, tmp_path):
    owner_files.set_edits(state, True)
    made = owner_files.made_folder(state)
    (made / "a.docx").write_bytes(b"x")
    review, notify, cards = _review(state)
    got = review({"package": "office", "name": "edit_document", "args": {"path": str(made / "a.docx")}, "notify": notify})
    assert got["run"] is True and not cards
    (tmp_path / "d.docx").write_bytes(b"x")
    got = review({"package": "office", "name": "edit_document", "args": {"path": str(tmp_path / "d.docx")}, "notify": notify})
    assert got["run"] is False and got["line"] == owner_files.OUTSIDE and not cards
    assert review({"package": "office", "name": "make_document", "args": {"kind": "word"}, "notify": notify})["run"] is True


def test_drawbridges_no_still_wins(state):
    owner_files.set_edits(state, True)
    made = owner_files.made_folder(state)
    (made / "a.docx").write_bytes(b"x")
    review, notify, cards = _review(state, verdict="deny")
    assert review({"package": "office", "name": "edit_document", "args": {"path": str(made / "a.docx")},
                   "notify": notify})["run"] is False and not cards


# -- routes ---------------------------------------------------------------------------------------

def test_the_routes(state, tmp_path, monkeypatch):
    app = create_app(state)
    core = app.extensions["puffpai"]
    core.steward.read_manifest = lambda pid: MANIFEST if pid == "office" else {"needs": {}}
    package = {"Authorization": "Bearer " + core.tokens.issue_package_token("office")}
    other = {"Authorization": "Bearer " + core.tokens.issue_package_token("calendar")}
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    client = app.test_client()
    assert client.get("/v1/owner-files").status_code in (401, 403)
    assert client.get("/v1/owner-files", headers=other).status_code == 403
    made = client.get("/v1/owner-files", headers=package).json["made"]
    assert made.endswith(os.path.join("My files", "Made by Puff"))
    (tmp_path / "d.docx").write_bytes(b"x")
    assert client.get("/v1/owner-files", query_string={"path": str(tmp_path / "d.docx")}, headers=package).json["line"] == owner_files.EDITS_OFF

    f = os.path.join(made, "Plan.docx")
    with open(f, "wb") as out:
        out.write(b"x")
    with open(os.path.join(made, "~$Plan.docx"), "wb") as out:
        out.write(b"x")
    assert client.get("/v1/knowledge/files").status_code in (401, 403)
    rows = client.get("/v1/knowledge/files", headers=dev).json["files"]
    assert [r["name"] for r in rows] == ["Plan.docx"] and rows[0]["folder"] == "Made by Puff"

    opened = []
    monkeypatch.setattr(owner_files, "_start", opened.append)
    assert client.post("/v1/knowledge/files/open", json={"path": f}, headers=owner).status_code == 403
    assert client.post("/v1/knowledge/files/open", json={"path": f}, headers=dev).json == {"opened": True}
    assert opened == [os.path.realpath(f)]
    r = client.post("/v1/knowledge/files/open", json={"path": str(tmp_path / "d.docx")}, headers=dev)
    assert r.status_code == 400 and r.json["error"] == owner_files.OPEN_OUTSIDE
    runs = os.path.join(made, "x.exe")
    with open(runs, "wb") as out:
        out.write(b"x")
    assert client.post("/v1/knowledge/files/open", json={"path": runs}, headers=dev).json["error"] == owner_files.OPEN_RUNS
    assert client.post("/v1/knowledge/files/open", json={}, headers=dev).status_code == 400
    assert len(opened) == 1


def test_the_real_card_through_the_package_manager(state, drawbridge):
    from puffpai_core.consent import card_view
    core = create_app(state).extensions["puffpai"]
    staged = core.packages.add("office")                          # the built-in, by its id, with Drawbridge's check
    text = " ".join(card_view(staged["card"], staged["floor"])["lines"])
    assert "only after your Yes" in text and "Your own files it may use: write" not in text
    assert "pypi.org" in text and staged["floor"] is False
    core.consent.answer(staged["consent_id"], "no", by="owner")


# -- the real server, over HTTP -------------------------------------------------------------------

def _rpc(port, method, params=None, token="t0ken"):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=body, headers={"Authorization": f"Bearer {token}",
                                                                                     "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=20) as r:
        return json.loads(r.read())


def test_the_mcp_server_answers_and_refuses_without_its_token(state, office, libs):
    srv = server.make_server(0, office.files, "t0ken")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as no:
            _rpc(port, "tools/list", token="wrong")
        assert no.value.code == 401
        tools = {t["name"]: t for t in _rpc(port, "tools/list")["result"]["tools"]}
        assert set(tools) == set(MANIFEST["abilities"]["changes"]) == set(MANIFEST["abilities"]["tools"])
        got = _rpc(port, "tools/call", {"name": "make_document", "arguments": {"kind": "word", "title": "T", "content": "Hi"}})
        assert "Made T.docx" in got["result"]["content"][0]["text"]
        got = _rpc(port, "tools/call", {"name": "edit_document", "arguments": {"path": "nope.docx", "changes": [{"append": "x"}]}})
        assert got["result"]["isError"] and "could not find" in got["result"]["content"][0]["text"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_manifest_and_the_check():
    assert MANIFEST["needs"]["owner_files"] == "write" and MANIFEST["id"] == FOLDER.name
    assert "startfile" not in (FOLDER / "server.py").read_text(encoding="utf-8")    # no Windows-only call in the package


# -- for real: `puffpai add`, pip through the Bridge, the page. Needs the internet, so only when asked. ---------------

@pytest.mark.skipif(not os.environ.get("PUFFPAI_OFFICE_REAL"), reason="installs from PyPI: only when asked")
def test_real_install_make_change_and_the_page(state, tmp_path, monkeypatch, drawbridge):
    import time
    from test_i10a_desktop_clicks import EDGE, WHERE, _call, _desktop, _point
    if EDGE is None:
        pytest.skip("Microsoft Edge")
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        assert _call(b, "POST", "/v1/bridge/open", {})["status"] == 200      # closed, nothing leaves, even with her Yes
        staged = _call(b, "POST", "/v1/packages", {"source": "office"})
        assert staged["status"] in (200, 201, 202), staged
        got = _call(b, "POST", f"/v1/consent/{staged['data']['consent_id']}/answer",
                    {"answer": "yes", "factor": "tap", "card_sha": staged["data"].get("card_sha")})
        assert got["status"] == 200, got
        deadline = time.time() + 280
        while time.time() < deadline:
            row = _call(b, "GET", "/v1/registry/office")
            if row["status"] == 200 and row["data"].get("status") == "running":
                break
            time.sleep(3)
        assert row["status"] == 200 and row["data"].get("status") == "running", (state / "logs" / "office.log").read_text(encoding="utf-8")[-1500:]
        got = _call(b, "POST", "/v1/chat/tool", {"tool": "office.make_document",
                                                 "args": {"kind": "word", "title": "Packing", "content": "- socks"}})
        assert got["status"] == 200 and "Made Packing.docx" in json.dumps(got["data"]), got
        got = _call(b, "POST", "/v1/chat/tool", {"tool": "office.edit_document",
                                                 "args": {"path": "Packing.docx", "changes": [{"append": "- hat"}]}})
        assert got["status"] != 200 or "turned off" in json.dumps(got["data"]), got      # off until she turns it on (D140)
        assert _call(b, "PUT", "/v1/settings/edits", {"on": True})["data"] == {"on": True}
        got = _call(b, "POST", "/v1/chat/tool", {"tool": "office.edit_document",
                                                 "args": {"path": "Packing.docx", "changes": [{"append": "- hat"}]}})
        assert got["status"] == 200 and "Changed Packing.docx" in json.dumps(got["data"]), got
        b.click(*_point(b, ".rail a.item[href='#knowledge']", 0))
        assert b.wait("!!document.querySelector('[data-documents=files] [data-file]')", timeout_s=30), b.js(WHERE)
        assert b.js("document.querySelector('[data-documents=files] button').textContent") == "Open"


# -- D140: off until she turns it on, and a fix she makes herself -----------------------------------------------------

def test_changing_is_off_for_every_file_until_she_turns_it_on(state, linked, tmp_path):
    made = owner_files.made_folder(state)
    (made / "a.docx").write_bytes(b"x")
    (linked / "c.docx").write_bytes(b"x")
    assert owner_files.edits_on(state) is False
    review, notify, cards = _review(state)
    for path in (made / "a.docx", linked / "c.docx", tmp_path / "nope.docx"):
        got = review({"package": "office", "name": "edit_document", "args": {"path": str(path)}, "notify": notify})
        assert got["run"] is False and got["line"] == owner_files.EDITS_OFF and not cards
    # a new file may still be made
    assert review({"package": "office", "name": "make_document", "args": {"kind": "word"}, "notify": notify})["run"] is True
    owner_files.set_edits(state, True)
    assert review({"package": "office", "name": "edit_document", "args": {"path": str(made / "a.docx")}, "notify": notify})["run"] is True
    owner_files.set_edits(state, False)
    assert owner_files.edits_on(state) is False
    (state / "settings" / "document_edits.json").write_text("not json", encoding="utf-8")
    assert owner_files.edits_on(state) is False                  # unreadable is off


def test_the_brain_isnt_offered_a_tool_that_changes_a_file_while_off(state):
    path_schema = {"properties": {"path": {"type": "string"}, "changes": {"type": "array"}}}
    assert owner_files.hidden(state, MANIFEST, "edit_document", path_schema) is True
    assert owner_files.hidden(state, MANIFEST, "make_document", {"properties": {"kind": {}, "title": {}, "content": {}}}) is False
    assert owner_files.hidden(state, {"abilities": {"changes": ["edit_document"]}}, "edit_document", path_schema) is False
    owner_files.set_edits(state, True)
    assert owner_files.hidden(state, MANIFEST, "edit_document", path_schema) is False


def test_the_setting_route(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    package = {"Authorization": "Bearer " + core.tokens.issue_package_token("office")}
    client = app.test_client()
    assert client.get("/v1/settings/edits", headers=dev).json == {"on": False}
    assert client.put("/v1/settings/edits", json={"on": True}, headers=owner).status_code == 403
    assert client.put("/v1/settings/edits", json={"on": True}, headers=package).status_code == 403
    assert client.put("/v1/settings/edits", json={"on": "yes"}, headers=dev).status_code == 400
    assert client.put("/v1/settings/edits", json={"on": True}, headers=dev).json == {"on": True}
    assert owner_files.edits_on(state) is True


def test_the_rule_sees_a_file_however_a_tool_names_it(state, tmp_path):
    (tmp_path / "d.docx").write_bytes(b"x")
    m = {"needs": {"owner_files": "write"}, "abilities": {"changes": ["t"]}}
    for args in ({"path": "d.docx"}, {"file": "d.docx"}, {"args": {"path": "d.docx"}}, {"a": [{"file_path": "d.docx"}]}):
        assert owner_files.question(state, m, "t", args) == {"line": owner_files.EDITS_OFF}, args
    assert owner_files.question(state, m, "t", {"kind": "word", "title": "x"}) is None
    assert owner_files.hidden(state, m, "t", {"allOf": [{"properties": {"path": {"type": "string"}}}]}) is True
    # any other shape of manifest is a no, never an error
    for bad in ("x", [], {"needs": "x"}, {"needs": [], "abilities": "y"}, {"needs": {"owner_files": "write"}, "abilities": {"changes": "t"}}):
        assert owner_files.hidden(state, bad, "t", {"properties": {"path": {}}}) is False, bad


def test_a_file_cant_be_handed_to_a_tool_that_changes_files_while_off(state):
    from puffpai_core import send_file

    class Row:
        def get(self, _id):
            return {"id": "w", "name": "Writer", "status": "running", "port": 1}

    class Steward:
        def read_manifest(self, _id):
            return {"needs": {"owner_files": "write"}, "abilities": {"changes": ["edit_document"]},
                    "accepts": [{"types": [".txt"], "tool": "edit_document"}]}

    class Core:
        pass
    core = Core()
    core.state, core.registry, core.steward = state, Row(), Steward()
    with pytest.raises(send_file.SendRefused, match="turned off"):
        send_file.hand(core, lambda _c: {"run": True}, "w", "novel.txt", b"her words")


def test_a_broken_setting_file_is_off_and_never_breaks_a_turn(state):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "document_edits.json").write_text("[" * 200000, encoding="utf-8")
    assert owner_files.edits_on(state) is False
    assert owner_files.set_edits(state, False) is False            # and she can clear it from Settings


def test_a_read_package_or_the_owner_still_hears_where_a_file_is(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    made = owner_files.made_folder(state)
    (made / "a.docx").write_bytes(b"x")
    core.steward.read_manifest = lambda pid: {"needs": {"owner_files": "read" if pid == "reader" else "write"}}
    client = app.test_client()
    q = {"path": str(made / "a.docx")}
    reader = {"Authorization": "Bearer " + core.tokens.issue_package_token("reader")}
    writer = {"Authorization": "Bearer " + core.tokens.issue_package_token("writer")}
    assert client.get("/v1/owner-files", query_string=q, headers=reader).json["place"] == "made"
    assert client.get("/v1/owner-files", query_string=q, headers=writer).json == {"line": owner_files.EDITS_OFF}
