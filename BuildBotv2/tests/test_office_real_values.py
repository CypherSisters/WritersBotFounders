"""D140, the owner's one exception to the gate: "When she turns the option on, it behaves as v1 does: the real text goes
into her file, not a {{vault:...}} mark."

The real Zone 1 gate on a real vault, the real Brain and tool loop, on a scripted model. The package is an in-process MCP
server, recording what it was sent or running the real ``packages/office`` code. No port is opened.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import MCPServer

from conftest import REPO
from puffpai_core import owner_files, wiring
from puffpai_core.anchor import sha
from puffpai_core.api.app import create_app
from puffpai_core.registry import Registry, row_from_manifest
from puffpai_core.zone1 import gate as zone1_gate
from puffpai_core.zone2 import settings as model_settings
from puffpai_core.zone2.brain import Brain
from puffpai_core.zone2.errors import BrainError
from puffpai_core.zone2.gate import NO_GATE, Gate, clean_value, production
from puffpai_core.zone2.mcp import Target
from puffpai_core.zone2.tools import ToolTable
from puffpai_core.zone2.tools import call as tool_call
from z1kit import canary_key, every_byte_under, make
from zone2_doubles import FakeRuntime, call

PHONE = "415-555-0137"
ASK = f"make a word document with my number {PHONE}"
FOLDER = REPO / "packages" / "office"
OFFICE = json.loads((FOLDER / "puffpai.json").read_text(encoding="utf-8"))
MAKE = "office__make_document"
EDIT = "office__edit_document"
ALLOW = {"run": True, "changing": True, "asked": False}


def office_server_module():
    spec = importlib.util.spec_from_file_location("office_server_real_values", FOLDER / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def recording(answer: str = "Done."):
    """A package like Office that writes nothing and keeps what it was sent. ``note`` is a tool it doesn't list as changing."""
    sent: list[dict] = []
    srv = MCPServer("office")

    @srv.tool()
    def make_document(kind: str, title: str, content: str) -> str:
        """Make a file."""
        sent.append({"kind": kind, "title": title, "content": content})
        return answer

    @srv.tool()
    def edit_document(path: str, changes: list[dict]) -> str:
        """Change a file."""
        sent.append({"path": path, "changes": changes})
        return answer

    @srv.tool()
    def note(text: str) -> str:
        """Keep a note."""
        sent.append({"text": text})
        return answer
    return srv, sent


def real_office(state):
    """The real package code behind the same three names, with the Core's own rule for where her files are."""
    mod = office_server_module()
    files = SimpleNamespace(made_folder=lambda: str(owner_files.made_folder(state)),
                            where=lambda path: owner_files.where(state, path))
    office = mod.Office(files)
    srv = MCPServer("office")

    @srv.tool()
    def make_document(kind: str, title: str, content: str) -> str:
        """Make a file."""
        return office.make_document(kind, title, content)

    @srv.tool()
    def edit_document(path: str, changes: list[dict]) -> str:
        """Change a file."""
        return office.edit_document(path, changes)
    return srv


def manifest_with(**needs) -> dict:
    m = copy.deepcopy(OFFICE)
    m["needs"].update(needs)
    return m


@pytest.fixture
def vault(state):
    a, *_ = make(state)
    zone1_gate.bind(a)
    return a


def brain_on(state, srv, *script, manifest=None, reaches=lambda _pid: False, settings=None, review=None):
    """The real Brain and the real gate, with the one package. By default every condition holds but the setting."""
    manifest = OFFICE if manifest is None else manifest
    reg = Registry(state / "registry.db")
    if reg.get("office") is None:
        reg.add(row_from_manifest(manifest, "made", status="running", port=18500))
    model_settings.save(state, **{"address": "http://127.0.0.1:9", "model": "fake:1b", "tool_budget": 30, **(settings or {})})
    rt = FakeRuntime(*script)
    brain = Brain(reg, state, runtime=rt, gate=production, manifest=lambda pid: manifest if pid == "office" else None,
                  targets=lambda pid: Target(package=pid, kind="inproc", server=srv) if pid == "office" else None,
                  browser=False, review=(lambda _call: dict(ALLOW)) if review is None else review, reaches=reaches)
    return brain, rt


def make_it(content: str = f"Call me at {PHONE}.", title: str = "Letter") -> dict:
    return call(MAKE, kind="word", title=title, content=content)


def run_make(state, *, content=f"Call me at {PHONE}.", tool=None, **kw) -> list[dict]:
    srv, sent = recording()
    brain, _rt = brain_on(state, srv, tool or make_it(content), {"text": "Done."}, **kw)
    brain.turn(ASK)
    assert sent, "the package was never called"
    return sent


def assert_locked(args: dict) -> None:
    text = json.dumps(args)
    assert PHONE not in text and "{{vault:" in text


# -- when every condition holds -------------------------------------------------------------------

def test_every_condition_holds_and_the_real_text_goes(state, vault):
    owner_files.set_edits(state, True)
    (got,) = run_make(state)
    assert got == {"kind": "word", "title": "Letter", "content": f"Call me at {PHONE}."}


def test_edit_document_gets_the_real_text_and_its_path_stays_as_reviewed(state, vault):
    owner_files.set_edits(state, True)
    srv, sent = recording()
    args = {"path": f"Notes {PHONE}.docx", "changes": [{"append": f"Call {PHONE}."}, {"replace": PHONE, "with": "x"}]}
    brain, _rt = brain_on(state, srv, call(EDIT, **args), {"text": "Done."})
    brain.turn(ASK)
    (got,) = sent
    assert got["changes"] == args["changes"]
    assert PHONE not in got["path"] and "{{vault:" in got["path"]    # the reviewed path is the path it acts on


# -- each condition failing on its own ------------------------------------------------------------

def test_setting_off_is_todays_locked_form(state, vault):
    assert_locked(run_make(state)[0])


def test_setting_unreadable_is_off(state, vault):
    owner_files.set_edits(state, True)
    (state / "settings" / "document_edits.json").write_text("not json", encoding="utf-8")
    assert_locked(run_make(state)[0])


def test_a_tool_it_does_not_list_as_changing_stays_locked(state, vault):
    owner_files.set_edits(state, True)
    assert_locked(run_make(state, tool=call("office__note", text=f"Call {PHONE}."))[0])


def test_a_package_without_owner_files_write_stays_locked(state, vault):
    owner_files.set_edits(state, True)
    assert_locked(run_make(state, manifest=manifest_with(owner_files="read"))[0])


@pytest.mark.parametrize("needs", [{"hosts": ["example.com"]}, {"addresses": "lan"}, {"addresses": "internet"}])
def test_a_package_that_declares_a_way_out_stays_locked(state, vault, needs):
    owner_files.set_edits(state, True)
    assert_locked(run_make(state, manifest=manifest_with(**needs))[0])


@pytest.mark.parametrize("reaches", [lambda _pid: True, lambda _pid: "maybe", lambda _pid: 1 / 0, None],
                         ids=["allowed", "not_a_clear_no", "breaks", "not_wired"])
def test_a_package_with_an_address_or_no_clear_no_stays_locked(state, vault, reaches):
    owner_files.set_edits(state, True)
    assert_locked(run_make(state, reaches=reaches)[0])


def test_a_real_address_yes_locks_it_and_taking_it_back_opens_it_again(state, vault, drawbridge):
    from puffpai_core.zone3 import addresses
    app = create_app(state, port=18419)
    core = app.extensions["puffpai"]
    zone1_gate.bind(vault)
    owner_files.set_edits(state, True)
    reaches = core.packages._has_address
    srv, sent = recording()
    brain, _rt = brain_on(state, srv, make_it(), {"text": "Done."}, make_it(), {"text": "Done."}, make_it(),
                          {"text": "Done."}, reaches=reaches)
    brain.turn(ASK)
    assert PHONE in sent[-1]["content"]
    rec = core.consent.ask("host", "pkg:office", addresses.covers_for("office", "Office files",
                                                                      addresses.parse("192.168.1.20:8123"), closed=True))
    assert reaches("office") is False                                # only asked for: nothing can go there yet
    from puffpai_core.consent import card_sha
    core.consent.answer(rec["consent_id"], "yes", "tap", by="owner", card_sha=card_sha(core.consent.get(rec["consent_id"])["card"]))
    assert reaches("office") is True
    brain.turn(ASK)
    assert_locked(sent[-1])
    core.consent.revoke(rec["consent_id"])
    brain.turn(ASK)
    assert PHONE in sent[-1]["content"]


def test_a_brain_not_on_this_computer_stays_locked(state, vault):
    owner_files.set_edits(state, True)
    assert_locked(run_make(state, settings={"address": "http://192.168.1.50:11434", "lan_hosts": ["192.168.1.50"]})[0])


def test_a_locked_vault_stays_locked(state, vault):
    owner_files.set_edits(state, True)
    vault.lock()
    assert_locked(run_make(state)[0])


@pytest.mark.parametrize("reader", [lambda _pid: None, lambda _pid: 1 / 0, None], ids=["none", "breaks", "no_reader"])
def test_no_manifest_at_the_call_stays_locked(state, vault, reader):
    """Read fresh at the call, so a file changed since her Yes (the reader says None) stops it at once."""
    owner_files.set_edits(state, True)
    srv, sent = recording()
    brain, _rt = brain_on(state, srv)
    fn = next(t for t in brain.concierge(production()).package_tools("office") if t.name == "office.make_document").fn
    args = {"kind": "word", "title": "T", "content": f"Call {PHONE}."}
    fn(args, {"real": True})
    assert PHONE in sent[-1]["content"]                                 # the same tool, with the manifest readable
    brain.manifest = reader
    target = Target(package="office", kind="inproc", server=srv)
    assert_locked(brain.concierge(production())._package_args(target, "make_document", args, {"real": True}))


def test_a_tool_server_she_added_stays_locked_even_under_a_matching_id(state, vault):
    """A tool server may be off this computer: it never gets real values, whatever a package of the same id says."""
    owner_files.set_edits(state, True)
    srv, _sent = recording()
    brain, _rt = brain_on(state, srv)
    concierge = brain.concierge(production())
    target = Target(package="office", kind="inproc", server=srv, outside=True)
    assert_locked(concierge._package_args(target, "make_document", {"content": f"Call {PHONE}."}, {"real": True}))


@pytest.mark.parametrize("ctx", [{}, {"real": False}, {"real": 1}, {"real": "yes"}, None])
def test_only_a_plain_true_is_a_local_brain(state, vault, ctx):
    owner_files.set_edits(state, True)
    srv, _sent = recording()
    brain, _rt = brain_on(state, srv)
    target = Target(package="office", kind="inproc", server=srv)
    assert_locked(brain.concierge(production())._package_args(target, "make_document", {"content": f"Call {PHONE}."}, ctx))


@pytest.mark.parametrize("bad", [None, "x", {}, {"needs": {"owner_files": "write"}},
                                 {"needs": {"owner_files": "write"}, "abilities": {"changes": "make_document"}},
                                 {"needs": "write", "abilities": {"changes": ["make_document"]}}])
def test_a_manifest_of_any_other_shape_is_a_no(state, bad):
    owner_files.set_edits(state, True)
    assert owner_files.takes_real_text(state, bad, "make_document") is False


def test_the_call_to_tools_from_a_page_stays_locked(state, vault):
    """``POST /v1/chat/tool`` is no chat turn with a local brain: it has no ``real``."""
    owner_files.set_edits(state, True)
    srv, sent = recording()
    brain, _rt = brain_on(state, srv)
    brain.call_tool("office.make_document", {"kind": "word", "title": "T", "content": f"Call {PHONE}."})
    assert_locked(sent[0])


# -- read at the call, not when the turn began ------------------------------------------------------

def test_the_setting_turned_off_while_a_yes_waits_stays_locked(state, vault):
    owner_files.set_edits(state, True)

    def review(_call):
        owner_files.set_edits(state, False)
        return dict(ALLOW)
    assert_locked(run_make(state, review=review)[0])


def test_the_vault_locked_while_a_yes_waits_stays_locked(state, vault):
    owner_files.set_edits(state, True)

    def review(_call):
        vault.lock()
        return dict(ALLOW)
    assert_locked(run_make(state, review=review)[0])


def test_an_address_allowed_while_a_yes_waits_stays_locked(state, vault):
    owner_files.set_edits(state, True)
    allowed = []

    def review(_call):
        allowed.append(True)
        return dict(ALLOW)
    assert_locked(run_make(state, review=review, reaches=lambda _pid: bool(allowed))[0])


# -- keys, tokens and passwords never go -------------------------------------------------------------

def test_a_key_and_a_password_stay_out_with_the_real_text_beside_them(state, vault):
    owner_files.set_edits(state, True)
    key = canary_key()
    (got,) = run_make(state, content=f"Call {PHONE}. My key is {key}. The password is swordfish.")
    assert PHONE in got["content"]
    assert key not in got["content"] and "swordfish" not in got["content"] and "{{vault:" in got["content"]


def test_a_key_in_the_title_or_the_changes_stays_out_too(state, vault):
    owner_files.set_edits(state, True)
    key = canary_key()
    (got,) = run_make(state, tool=make_it(title=f"Letter {key}"))
    assert key not in got["title"]
    srv, sent = recording()
    brain, _rt = brain_on(state, srv, call(EDIT, path="a.docx", changes=[{"append": f"key {key}", "with": key}]), {"text": "Done."})
    brain.turn(ASK)
    assert key not in json.dumps(sent)


def test_the_gate_method_checks_for_itself_with_a_prepare_that_would_let_all_through():
    """Each layer alone: the vault check and the floor key check, whatever ``prepare`` answers (Zone 1 checks both too)."""
    through = lambda text, _open: (text, [])  # noqa: E731
    assert Gate(prepare=through, vault_open=lambda: False).with_real_values({"content": PHONE}) is None
    assert Gate(prepare=through, vault_open=lambda: 1 / 0).with_real_values({"content": PHONE}) is None
    key = canary_key()
    got = Gate(prepare=through, vault_open=lambda: True).with_real_values({"content": f"Call {PHONE}. {key}", "api_key": "Zx81vQ2mLp90"})
    assert PHONE in got["content"] and key not in got["content"] and "Zx81vQ2mLp90" not in got["api_key"]


def test_a_gate_that_breaks_sends_nothing(state, vault):
    owner_files.set_edits(state, True)
    srv, sent = recording()
    brain, _rt = brain_on(state, srv)
    concierge = brain.concierge(production())

    def broken(_value):
        raise BrainError(NO_GATE, "no_gate")
    concierge.real_args = broken
    tool = next(t for t in concierge.package_tools("office") if t.name == "office.make_document")
    table = ToolTable([tool])
    got = tool_call(table, "office.make_document", {"kind": "word", "title": "T", "content": f"Call {PHONE}."}, {"real": True})
    assert got["ok"] is False and PHONE not in json.dumps(got) and sent == []


# -- a mark the model wrote itself ------------------------------------------------------------------

def test_a_mark_the_model_wrote_stays_a_mark(state, vault):
    """The gate finds values and never opens a mark, so a mark from an earlier chat turn goes in as a mark."""
    owner_files.set_edits(state, True)
    mark = production().keep(PHONE)
    assert mark.startswith("{{vault:") and PHONE not in mark
    (got,) = run_make(state, content=f"Call me at {mark}.")
    assert got["content"] == f"Call me at {mark}."


def test_a_mark_of_a_key_stays_a_mark(state, vault):
    owner_files.set_edits(state, True)
    key = canary_key()
    mark = production().keep(key)
    assert mark.startswith("{{vault:") and key not in mark
    (got,) = run_make(state, content=f"The key: {mark}.")
    assert key not in got["content"] and "{{vault:" in got["content"]


# -- the card, the store, the result ------------------------------------------------------------------

class DB:
    def review_action(self, package, tool, args, *, labels=(), caller=""):
        return {"verdict": "ask", "reason_code": "needs_yes", "review_id": "rv_1", "floor": False, "line": ""}

    def see_tools(self, *_a, **_k):
        pass


def test_the_yes_card_and_its_sha_stay_locked_and_the_call_gets_the_real_text(state, vault):
    core = create_app(state, port=18419).extensions["puffpai"]
    zone1_gate.bind(vault)
    core.steward.read_manifest = lambda _id: OFFICE
    core.registry.add(row_from_manifest(OFFICE, "made", status="running", port=18500))
    owner_files.set_edits(state, True)
    review = wiring.tool_review(core, DB(), wait_s=10)
    srv, sent = recording()
    events = []

    def wired(the_call):
        def answering(card):
            events.append(card)
            core.consent.answer(card["consent_id"], "yes", "tap", by="owner", card_sha=card["card_sha"])
        return review(dict(the_call, notify=answering))
    brain, _rt = brain_on(state, srv, make_it(), {"text": "Done."}, review=wired)
    brain.registry = core.registry
    brain.turn(ASK)
    assert sent[0]["content"] == f"Call me at {PHONE}."
    (card,) = [e["card"] for e in events]
    assert PHONE not in json.dumps(card) and any("{{vault:" in line for line in card["arguments"])
    args = {"kind": "word", "title": "Letter", "content": f"Call me at {PHONE}."}
    assert card["args_sha"] == sha(clean_value(args, production().keep)) != sha(args)


def test_what_drawbridge_reviews_is_locked(state, vault):
    owner_files.set_edits(state, True)
    seen = []

    def review(the_call):
        seen.append(the_call)
        return dict(ALLOW)
    (got,) = run_make(state, review=review)
    assert PHONE in got["content"]
    assert_locked(seen[0]["args"])
    assert PHONE not in json.dumps(seen[0]["summary"])


def test_the_store_stays_locked_with_the_real_text_in_the_call_and_its_answer(state, vault):
    owner_files.set_edits(state, True)
    srv, sent = recording(answer=f"Made it, with {PHONE} in it.")
    brain, rt = brain_on(state, srv, make_it(), {"text": f"Done: {PHONE} is in your letter."})
    got = brain.turn(ASK)
    assert PHONE in sent[0]["content"] and PHONE in got["text"]        # the reply goes back as the brain said it
    tool_says = next(m for m in rt.calls[1]["messages"] if m.get("role") == "tool")["content"]
    assert PHONE in tool_says                                           # the turn's own gate, as for read_pinned
    kept = every_byte_under(state / "chat")
    assert PHONE.encode() not in kept and b"{{vault:" in kept


def test_a_key_in_the_tool_answer_still_stops_at_the_gate(state, vault):
    owner_files.set_edits(state, True)
    key = canary_key()
    srv, _sent = recording(answer=f"My key is {key}")
    brain, rt = brain_on(state, srv, make_it(), {"text": "Done."})
    brain.turn(ASK)
    assert key not in rt.seen(1)


# -- a real Word file ---------------------------------------------------------------------------------

def _words(path) -> str:
    docx = pytest.importorskip("docx")
    return "\n".join(p.text for p in docx.Document(path).paragraphs)


def _word_files(state):
    return sorted(owner_files.made_path(state).glob("*.docx"))


def test_a_real_word_file_holds_the_real_text(state, vault):
    pytest.importorskip("docx")
    owner_files.set_edits(state, True)
    key = canary_key()
    brain, rt = brain_on(state, real_office(state), make_it(f"Call me at {PHONE}. My key is {key}."), {"text": "Done."})
    brain.turn(ASK)
    (path,) = _word_files(state)
    text = _words(path)
    assert PHONE in text and key not in text
    said = next(m for m in rt.calls[1]["messages"] if m.get("role") == "tool")["content"]
    assert "Made Letter.docx" in said
    assert text.count("{{vault:") == 1                                  # the key's mark, and only that


def test_a_real_word_file_changed_holds_the_real_text(state, vault):
    pytest.importorskip("docx")
    owner_files.set_edits(state, True)
    brain, _rt = brain_on(state, real_office(state), make_it("Hello."), {"text": "Done."},
                          call(EDIT, path="Letter.docx", changes=[{"append": f"Call me at {PHONE}."}]), {"text": "Done."})
    brain.turn(ASK)
    brain.turn(ASK)
    (path,) = _word_files(state)
    assert PHONE in _words(path)


def test_with_the_setting_off_a_real_word_file_holds_the_mark_as_today(state, vault):
    pytest.importorskip("docx")
    brain, rt = brain_on(state, real_office(state), make_it(), {"text": "Done."})
    brain.turn(ASK)
    (path,) = _word_files(state)
    text = _words(path)
    assert PHONE not in text and "{{vault:" in text
    said = next(m for m in rt.calls[1]["messages"] if m.get("role") == "tool")["content"]
    assert "1 private detail from your vault" in said


# -- the setting's own route ----------------------------------------------------------------------------

def _audit_lines(state) -> list[dict]:
    path = state / "logs" / "audit.jsonl"
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return [r for r in rows if str(r.get("action", "")).startswith("document_edits")]


def test_each_change_of_the_setting_writes_one_audit_line(state):
    app = create_app(state, port=18419)
    dev = {"Authorization": "Bearer " + app.extensions["puffpai"].tokens.issue_device("Test laptop")}
    put = lambda on: app.test_client().put("/v1/settings/edits", json={"on": on}, headers=dev)  # noqa: E731
    assert put(False).json == {"on": False} and _audit_lines(state) == []     # no change, no line
    assert put(True).json == {"on": True} and put(True).json == {"on": True}
    assert put(False).json == {"on": False}
    assert [(r["actor"], r["action"]) for r in _audit_lines(state)] == [("owner", "document_edits_on"),
                                                                        ("owner", "document_edits_off")]


def test_no_audit_line_means_it_does_not_turn_on_and_off_still_works(state, monkeypatch):
    from puffpai_core.zone3.service import for_app
    app = create_app(state, port=18419)
    dev = {"Authorization": "Bearer " + app.extensions["puffpai"].tokens.issue_device("Test laptop")}
    put = lambda on: app.test_client().put("/v1/settings/edits", json={"on": on}, headers=dev)  # noqa: E731
    owner_files.set_edits(state, False)

    def boom(_record):
        raise OSError("disk full")
    monkeypatch.setattr(for_app(app).audit, "append", boom)
    got = put(True)
    assert got.status_code == 503 and "stays off" in got.json["error"] and owner_files.edits_on(state) is False
    owner_files.set_edits(state, True)
    assert put(False).json == {"on": False} and owner_files.edits_on(state) is False


# -- the wiring: the running Core's Brain asks the real reading ---------------------------------------------

def test_the_running_core_brain_asks_the_package_managers_reading(state):
    from puffpai_core.api.groups import chat
    app = create_app(state, port=18419)
    core = app.extensions["puffpai"]
    with app.app_context():
        got = chat.brain()
    assert got.reaches == core.packages._has_address and got.reaches("office") is False
