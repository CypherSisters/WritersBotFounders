"""D127: "send this file to <app>", for any package with ``accepts`` (02, D48c), and Windows's Open dialog for Pin and
Link. The two store packages, Document search and Document convert, run for real on a real Core when their repo is here.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core import send_file
from puffpai_core.api.app import create_app
from puffpai_core.zone1.folders import Library
from test_pin_documents import PHONE, docx, para
from z1kit import make

STORE = REPO.parent / "puffpai-packages"
ACCEPTS = {"accepts": [{"types": [".docx", ".txt", "text/*"], "tool": "add_document"}]}


# -- which tool, in which shape -------------------------------------------------------------------

def test_the_tool_is_found_by_ending_or_kind():
    assert send_file.tool_for(ACCEPTS, "A.DOCX") == "add_document"
    assert send_file.tool_for(ACCEPTS, "page.html") == "add_document"              # text/*
    assert send_file.tool_for(ACCEPTS, "a.pdf") is None and send_file.tool_for(None, "a.txt") is None
    assert send_file.types_said(ACCEPTS) == ".docx, .txt"


def test_the_contract_shape():
    a = send_file.args_for("a.txt", "Leeks \u00e9".encode("utf-8"))
    assert a == {"file_name": "a.txt", "mime": "text/plain", "text": "Leeks \u00e9"}
    b = send_file.args_for("a.docx", b"PK\x03\x04")
    assert b["content_b64"] == base64.b64encode(b"PK\x03\x04").decode() and "text" not in b
    assert "content_b64" in send_file.args_for("odd.txt", b"\xff\xfe\xfa")          # not text after all


# -- hand -----------------------------------------------------------------------------------------

class Steward:
    def __init__(self, manifest):
        self.manifest = manifest

    def read_manifest(self, _id):
        return self.manifest


class Registry:
    def __init__(self, row):
        self.row = row

    def get(self, _id):
        return self.row

    def list(self):
        return [self.row]


class Core:
    def __init__(self, manifest):
        self.steward, self.registry = Steward(manifest), Registry({"id": "p", "name": "Thing", "status": "running", "port": 1})


def test_a_kind_it_doesnt_take_is_refused_in_plain_words():
    with pytest.raises(send_file.SendRefused, match="Thing doesn't take that kind of file. It takes .docx, .txt."):
        send_file.hand(Core(ACCEPTS), lambda _c: {"run": True}, "p", "a.pdf", b"x")


def test_no_drawbridge_or_its_no_sends_nothing(monkeypatch):
    sent = []
    monkeypatch.setattr("puffpai_core.zone2.mcp.call_tool", lambda *a, **k: sent.append(a) or {"ok": True, "text": "x"})
    with pytest.raises(send_file.SendRefused, match="Drawbridge"):
        send_file.hand(Core(ACCEPTS), None, "p", "a.txt", b"x")
    with pytest.raises(send_file.SendRefused, match="No thanks"):
        send_file.hand(Core(ACCEPTS), lambda _c: {"run": False, "line": "No thanks"}, "p", "a.txt", b"x")
    assert sent == []


def test_a_package_with_internet_hosts_gets_drawbridges_check_on_what_the_file_holds(monkeypatch):
    seen = []

    def review(call):
        seen.append(call)
        return {"run": False, "line": "Not sent."}
    local = Core(ACCEPTS)
    with pytest.raises(send_file.SendRefused):
        send_file.hand(local, review, "p", "a.txt", f"Call {PHONE}".encode())
    assert seen[-1]["labels"] == []                                                 # stays on this computer
    online = Core(dict(ACCEPTS, needs={"hosts": ["example.com"]}))
    with pytest.raises(send_file.SendRefused):
        send_file.hand(online, review, "p", "a.txt", f"Call {PHONE}".encode())
    assert "person.phone" in seen[-1]["labels"]
    with pytest.raises(send_file.SendRefused):
        send_file.hand(online, review, "p", "a.docx", b"not a zip")
    assert seen[-1]["labels"] == [send_file.UNREAD_LABEL]
    assert seen[-1]["args"] == {"file_name": "a.docx", "size": 9}                   # the review never gets the words


def test_the_review_passes_labels_to_drawbridge():
    from puffpai_core import wiring

    class DB:
        got = None

        def review_action(self, package, tool, args, *, labels=(), caller=""):
            DB.got = labels
            return {"verdict": "deny", "reason_code": "labelled", "line": "A key or password never leaves your computer."}

        def see_tool(self, *a, **k):
            pass
    core = Core(ACCEPTS)
    got = wiring.tool_review(core, DB())({"package": "p", "name": "add_document", "args": {}, "labels": ["secret.token"]})
    assert DB.got == ["secret.token"] and got["run"] is False


# -- a path is only a pinned or linked one --------------------------------------------------------

def test_only_a_pinned_or_linked_file_can_go_by_its_path(state, tmp_path):
    a, *_ = make(state)
    lib = Library(a, state)
    pin = tmp_path / "pin.docx"
    docx(pin, para("x"))
    mine = tmp_path / "Writing"
    mine.mkdir()
    inside = mine / "deep.xlsx"
    inside.write_bytes(b"x")
    other = tmp_path / "other.docx"
    docx(other, para("x"))
    lib.pin(pin)
    lib.link(mine)
    assert lib.handable(pin) and lib.handable(inside)
    assert lib.handable(other) is None and lib.handable(tmp_path / "gone.docx") is None
    assert lib.handable(mine) is None                                               # a folder is not a file
    (state / "vault").mkdir(exist_ok=True)
    held = state / "vault" / "x.txt"
    held.write_text("x", encoding="utf-8")
    assert lib.handable(held) is None
    names = [p.name for p in lib.known_documents()]
    assert names == ["pin.docx", "deep.xlsx"]


def test_the_routes_refuse_what_they_should(state, tmp_path):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    client = app.test_client()
    assert client.get("/v1/packages/sendable").status_code in (401, 403)
    assert client.get("/v1/packages/sendable", headers=owner).json == []
    url = "/v1/packages/nothing/send"
    assert client.post(url, json={"file_name": "a.txt", "content_b64": "eA=="}, headers=owner).status_code == 403
    assert client.post(url, json={}, headers=dev).status_code == 400
    assert client.post(url, json={"file_name": "a.txt", "content_b64": "not base64!"}, headers=dev).status_code == 400
    r = client.post(url, json={"file_name": "a.txt", "content_b64": "eA=="}, headers=dev)
    assert r.status_code == 409 and "not installed" in r.json["error"]
    r = client.post(url, json={"path": str(tmp_path / "secret.txt")}, headers=dev)
    assert r.status_code == 400 and "on your list" in r.json["error"]


# -- the picker -----------------------------------------------------------------------------------

def test_the_pick_route_says_type_it_from_another_device(state, monkeypatch):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    client = app.test_client()
    monkeypatch.setattr(send_file, "pick", lambda kind: f"C:\\picked\\{kind}")
    assert client.post("/v1/knowledge/pick", json={"kind": "file"}, headers=owner).status_code == 403
    assert client.post("/v1/knowledge/pick", json={"kind": "drive"}, headers=dev).status_code == 400
    r = client.post("/v1/knowledge/pick", json={"kind": "folder"}, headers=dev)
    if send_file.can_pick():
        assert r.json == {"path": "C:\\picked\\folder"}
    far = client.post("/v1/knowledge/pick", json={"kind": "file"}, headers=dev, environ_base={"REMOTE_ADDR": "192.168.1.9"})
    assert far.json["typed"] is True and far.json["path"] == ""


@pytest.mark.skipif(os.name != "nt", reason="The file picker is a Windows dialog (send_file.can_pick)")
def test_pick_answers_the_path_and_one_at_a_time(monkeypatch):
    got = {}

    def run(argv, **k):
        got["argv"], got["k"] = argv, k
        assert not send_file._picking.acquire(blocking=False)                       # noqa: SLF001 - one at a time
        return subprocess.CompletedProcess(argv, 0, "C:\\Users\\me\\Caf\u00e9\\My Novel.docx\r\n".encode(), b"")
    monkeypatch.setattr(send_file.subprocess, "run", run)
    assert send_file.pick("file") == "C:\\Users\\me\\Caf\u00e9\\My Novel.docx"
    assert got["argv"][0].lower().endswith("powershell.exe") and os.path.isabs(got["argv"][0])
    assert "-EncodedCommand" in got["argv"] and got["k"]["stdin"] == subprocess.DEVNULL
    monkeypatch.setattr(send_file.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 0, b"", b""))
    assert send_file.pick("folder") == ""                                           # she closed it
    monkeypatch.setattr(send_file.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 1, b"", b"x"))
    with pytest.raises(send_file.SendRefused, match="Type the path"):
        send_file.pick("file")
    monkeypatch.setattr(send_file.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 1, b"C:\\a.docx", b""))
    assert send_file.pick("file") == "C:\\a.docx"                                   # the real dialog can exit 1 with a path


@pytest.mark.skipif(not send_file.can_pick(), reason="Windows only")
@pytest.mark.parametrize("kind", ["file", "files", "folder"])
def test_the_real_script_is_good_powershell_up_to_the_dialog(kind):
    """Everything but showing it: this computer may be in use, so no dialog opens in a test."""
    script = send_file.pick_script(kind)
    assert "ShowDialog" in script
    dry = script[:script.index("if($d.ShowDialog")] + "[Console]::Out.Write($d.GetType().Name)"
    encoded = base64.b64encode(dry.encode("utf-16-le")).decode()
    got = subprocess.run([send_file._powershell(), "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded],  # noqa: SLF001
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert got.returncode == 0, got.stderr
    assert got.stdout == ("FolderBrowserDialog" if kind == "folder" else "OpenFileDialog")


@pytest.mark.skipif(not send_file.can_pick(), reason="Windows only")
def test_the_real_files_answer_is_one_path_a_line():
    """The script's last part, by real PowerShell, on what a dialog's ``FileNames`` holds. No dialog opens in a test."""
    script = send_file.pick_script("files")
    head, answer = script.index("if($d.ShowDialog"), script.index("[Console]::Out.Write(")
    dry = (script[:head] + "$d=[pscustomobject]@{FileNames=[string[]]@('C:\\a b\\caf\u00e9.txt','C:\\c.docx')};" + script[answer:-1])
    encoded = base64.b64encode(dry.encode("utf-16-le")).decode()
    got = subprocess.run([send_file._powershell(), "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded],  # noqa: SLF001
                         capture_output=True, stdin=subprocess.DEVNULL, timeout=120,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert got.returncode == 0, got.stderr
    assert got.stdout.decode("utf-8") == "C:\\a b\\caf\u00e9.txt\nC:\\c.docx"


# -- chat -----------------------------------------------------------------------------------------

def test_chat_offers_it_only_when_an_app_takes_files(state):
    from puffpai_core.zone2 import concierge as cg

    class Pins:
        take = False

        def names(self):
            return []

        def apps_take_files(self):
            return self.take

        def send(self, name, app):
            return {"ok": True, "said": f"{name} to {app}"}
    pins = Pins()
    tools = lambda: {t.name: t for t in cg.Concierge(None, state, keep=str, pinned=pins).core_tools(lambda: None)}  # noqa: E731
    assert cg.SEND_DOCUMENT not in tools()
    pins.take = True
    tool = tools()[cg.SEND_DOCUMENT]
    assert tool.fn({"name": "a.docx", "app": "document-search"}, {})["said"] == "a.docx to document-search"
    assert set(tool.schema["properties"]) == {"name", "app"}                         # a name, never a path


# -- the page -------------------------------------------------------------------------------------

def test_the_page_has_it_in_both_shells():
    web = REPO / "packages" / "desktop" / "web"
    for shell in ("index.html", "window.html"):
        html = (web / shell).read_text(encoding="utf-8")
        assert html.index("devzones.js") < html.index("sendfile.js") < html.index("pages/knowledge.js")
    assert "P.sendFileButton(takes, said)" in (web / "pages" / "apps.js").read_text(encoding="utf-8")
    page = (web / "pages" / "knowledge.js").read_text(encoding="utf-8")
    assert page.count("P.sendPathButton(") == 2 and "P.pickPath(kind, said)" in page


# -- the two real store packages, on a real Core ----------------------------------------------------

@pytest.mark.skipif(not (STORE / "document-search").is_dir(), reason="the store repo is not next to this one")
def test_real_files_to_the_two_store_packages_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    import socket
    from puffpai_core.cli import main
    from test_i10a_desktop_clicks import EDGE, WHERE, _call, _desktop, _point, _text_point
    if os.name != "nt" or EDGE is None:
        pytest.skip("Windows Job Objects and Microsoft Edge")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    for pid in ("document-search", "document-convert"):
        assert main(["add", str(STORE / pid)]) == 0
    mine = tmp_path / "Writing"
    mine.mkdir()
    docx(mine / "warranty.docx", para("The roof warranty runs for twenty years."))
    (mine / "notes.txt").write_text("Plain notes on turnips.", encoding="utf-8")
    novel_file = tmp_path / "My Novel.docx"
    docx(novel_file, para("It was the summer the river ran low."))
    with _desktop(state, tmp_path, monkeypatch, complete=True, env={"OLLAMA_HOST": f"127.0.0.1:{closed_port}"}) as b:
        got = _call(b, "GET", "/v1/packages/sendable")
        assert {t["id"] for t in got["data"]} == {"document-search", "document-convert"}, got
        assert _call(b, "POST", "/v1/folders/link", {"path": str(mine)})["status"] == 200
        assert _call(b, "POST", "/v1/knowledge/pinned", {"path": str(novel_file)})["status"] == 200
        # a linked file, by its path, to Document search: a real Word file, kept and found
        sent = _call(b, "POST", "/v1/packages/document-search/send", {"path": str(mine / "warranty.docx")})
        assert sent["status"] == 200, sent
        assert "warranty.docx" in sent["data"]["line"]
        # the picker's way, from bytes: a text file
        raw = base64.b64encode(b"Plain notes on turnips.").decode()
        sent = _call(b, "POST", "/v1/packages/document-search/send", {"file_name": "notes.txt", "content_b64": raw})
        assert sent["status"] == 200, sent
        tool = _call(b, "POST", "/v1/chat/tool", {"tool": "document-search.search_documents", "args": {"query": "roof warranty"}})
        assert "warranty.docx" in json.dumps(tool["data"]), tool
        # a PDF is not what Document search takes, and it says so
        sent = _call(b, "POST", "/v1/packages/document-search/send", {"file_name": "a.pdf", "content_b64": raw})
        assert sent["status"] == 409 and "doesn't take that kind" in sent["data"]["error"], sent
        # Document convert: a pinned Word file. It needs LibreOffice for the PDF, and says so without it
        sent = _call(b, "POST", "/v1/packages/document-convert/send", {"path": str(novel_file)})
        convert_line = sent["data"].get("line") or sent["data"].get("error")
        assert sent["status"] in (200, 409) and ("PDF" in convert_line or "LibreOffice" in convert_line), sent
        print("\nDocument convert said:", convert_line)
        # the button: Apps has it on both, and a real click opens the picker's input
        b.click(*_point(b, ".rail a.item[href='#apps']", 0))
        assert b.wait("document.querySelectorAll('#view-apps [data-act=send]').length === 2", timeout_s=30), b.js(WHERE)
        # Send to... on a pinned document's row, by real clicks
        b.click(*_point(b, ".rail a.item[href='#knowledge']", 0))
        assert b.wait("!!document.querySelector('[data-send-to=\"My Novel.docx\"]')", timeout_s=30), b.js(WHERE)
        b.click(*_text_point(b, "[data-document] button", "Send to..."))
        assert b.wait("!document.querySelector('[data-send-to=\"My Novel.docx\"]').hidden", timeout_s=10)
        b.click(*_text_point(b, "[data-send-to=\"My Novel.docx\"] button", "Document search"))
        assert b.wait("!!document.querySelector('[data-sent=document-search]')", timeout_s=60), b.js(WHERE)
        assert "My Novel.docx" in b.js("document.querySelector('[data-sent=document-search]').textContent")
    assert (mine / "warranty.docx").exists() and novel_file.exists()
    assert Path(novel_file).read_bytes()[:2] == b"PK"                                # never changed
