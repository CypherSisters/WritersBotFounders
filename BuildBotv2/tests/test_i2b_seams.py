"""Integration lane I2b: the last seams (safe mode, the brain's core tools, Zone 3's cards, check item 6,
the adopted origin, a core tool's own question)."""

import json
import sys
import threading
import time

import pytest

from conftest import wait_for, yes
from puffpai_core import wiring
from puffpai_core.packages.manager import AddError


@pytest.fixture
def core(core):
    from puffpai_core.api.app import wire_core
    wire_core(core)
    return core


@pytest.fixture
def started(core, drawbridge):
    from puffpai_core.api.app import start_core, stop_core
    terminal = start_core(core)
    yield core
    stop_core(core, terminal)


def _titles(core):
    return [n["title"] for n in core.notices.list()]


# -- 1. Caretaker's one safe-mode call --------------------------------------------------------------
def test_safe_mode_runs_its_steps_once_raises_one_card_and_adds_nothing(core, hello_copy):
    ct = core.caretaker
    ran, cards = [], []
    ct.safe_steps[:] = [lambda: ran.append(1) or "the Bridge is closed", lambda: 1 / 0, lambda: None]
    ct.on_card.append(cards.append)
    got = ct.safe_mode("The outside log was changed.")
    assert got["new"] and ct.in_safe_mode and ct.safe_reasons == ["The outside log was changed."]
    assert got["done"] == ["the Bridge is closed", "nothing new is added"]          # a broken step is skipped
    assert len(cards) == 1 and cards[0]["kind"] == "safe_mode" and cards[0]["package"] is None
    line = cards[0]["line"]
    assert "safe mode" in line and "the Bridge is closed and nothing new is added" in line
    assert "PIN and recovery code still work" in line and "\u2014" not in line
    again = ct.safe_mode("The outside log was changed.")
    assert not again["new"] and again["done"] == [] and ran == [1] and len(cards) == 1
    with pytest.raises(AddError) as e:
        core.packages.add(str(hello_copy))
    assert e.value.status == 503 and "safe mode" in e.value.problems[0]


def test_a_passing_selftest_keeps_runtime_safe_mode(core):
    core.caretaker.safe_mode("x happened")
    assert core.caretaker.selftest()["mode"] == "safe" and core.caretaker.in_safe_mode


def test_tick_hooks_run_after_each_tick_even_when_one_breaks(core):
    seen = []
    core.caretaker.on_tick[:] = [lambda: 1 / 0, lambda: seen.append("ran")]
    core.caretaker.tick()
    core.caretaker.tick()
    assert seen == ["ran", "ran"]


def test_the_wired_safe_mode_closes_the_bridge_locks_the_vault_pauses_toolsmith(started):
    from puffpai_core.zone1 import archivist
    from z1kit import PIN
    core = started
    a = archivist.for_state(core.state, core.bus)
    a.setup(PIN)
    a.unlock("pin", PIN)
    core.zone3.bridge.open(by="owner")
    host = core.app.extensions["puffpai_toolsmith"]
    got = core.caretaker.safe_mode("A test reason.")
    assert got["done"] == ["the Bridge is closed", "the vault is locked", "Toolsmith is paused", "nothing new is added"]
    assert core.zone3.bridge.posture()["state"] == "closed" and not a.is_open() and host._stopping
    assert _titles(core).count("BuildBot is in safe mode") == 1


def test_hookguards_safe_mode_goes_through_caretaker_with_one_plain_card(started):
    core = started
    host = core.app.extensions["puffpai_toolsmith"]
    host.safe_mode("core_python_hook_changed")                           # what L4's HookGuard calls
    assert core.caretaker.in_safe_mode
    notes = [n for n in core.notices.list() if n["title"] == "BuildBot is in safe mode"]
    assert len(notes) == 1 and "Python start-up files" in notes[0]["text"]
    assert "core_python_hook_changed" not in notes[0]["text"]
    assert host.trials.rescan_all in core.caretaker.on_tick               # the tick-hook list, not a wrapped tick


# -- 3. Zone 3's own cards ------------------------------------------------------------------------
@pytest.mark.parametrize("kind,title,safe", [
    ("bridge_damaged", "Your Bridge settings were changed", True),
    ("ledger_damaged", "The record of what left was changed", True),
    ("audit_damaged", "The audit log was changed", True),
    ("vault_mismatch", "The Bridge does not match your vault", False),
    ("bypass", "An app went around the Bridge", False),
    ("watch_blind", "BuildBot can't watch an app", False),
    ("watch_not_running", "The Bridge watch is not running", False),
])
def test_zone3_cards_reach_the_notices_and_the_terminal(started, capsys, kind, title, safe):
    core = started
    core.zone3.cards.append({"kind": kind, "line": f"A {kind} line."})
    assert title in _titles(core)
    assert f"{title}: A {kind} line." in capsys.readouterr().out
    assert core.caretaker.in_safe_mode is safe


def test_a_real_bridge_tamper_card_means_safe_mode(started):
    core = started
    core.zone3.bridge._policy_damaged()                                  # what the policy file check calls
    assert core.caretaker.in_safe_mode and "Your Bridge settings were changed" in _titles(core)
    assert core.zone3.bridge.posture()["state"] == "closed"


def test_cards_raised_before_the_core_started_are_routed_at_start(core, drawbridge):
    from puffpai_core.api.app import start_core, stop_core
    core.zone3.cards.append({"kind": "watch_blind", "package": "x", "line": "Blind at start."})
    terminal = start_core(core)
    try:
        assert "BuildBot can't watch an app" in _titles(core)
    finally:
        stop_core(core, terminal)


def test_puffpai_notices_lists_them(started, capsys):
    from puffpai_core.cli import main
    started.zone3.cards.append({"kind": "proxy_down", "line": "No proxy here."})
    capsys.readouterr()
    assert main(["notices"]) == 0
    out = capsys.readouterr().out
    assert "The Bridge proxy is not running (from core)" in out and "No proxy here." in out


# -- 2. the brain's core tools --------------------------------------------------------------------
def test_search_knowledge_reads_the_folders_index_with_held_values_as_handles(core, state, monkeypatch):
    from puffpai_core.zone1 import archivist
    from z1kit import make
    a, *_ = make(state)
    monkeypatch.setitem(archivist._BY_STATE, str(state.resolve()), a)
    data = state / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "soup.txt").write_text("Tomato soup for Sam, call 415-555-0137 when it is ready.", encoding="utf-8")
    search = wiring.knowledge_search(core)
    hits = search("tomato soup", 5)
    assert len(hits) == 1 and hits[0]["file"] == "soup.txt"
    assert "415-555-0137" not in json.dumps(hits) and "{{vault:" in hits[0]["snippet"]
    assert search("nothing-like-this", 5) == []


def test_search_knowledge_and_checklist_add_in_a_turn(core, state, freshtable, monkeypatch):
    from puffpai_core.checklist import Checklist
    from zone2_doubles import FakeRuntime, GateDouble, call, make_brain
    steps = Checklist(state / "checklist.json")
    rt = FakeRuntime(call("search_knowledge", query="soup"),
                     call("checklist_add", title="Plug in the speaker", why="Music needs it", kind="plug_in"),
                     {"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(),
                             search=lambda q, n: [{"file": "soup.txt", "path": "x", "snippet": f"[{q}]"}],
                             checklist=steps)
    brain.turn("help")
    assert "soup.txt" in rt.seen(1)
    [step] = steps.list()
    assert step["package"] == "core:concierge" and step["raised_by"] == "concierge (chat)"
    assert step["title"] == "Plug in the speaker" and not step.get("admin") and "Your turn" in rt.seen(2)


def test_brain_steps_get_package_trust_never_core(state):
    from puffpai_core.checklist import Checklist
    from puffpai_core.zone2.concierge import Concierge
    steps = Checklist(state / "checklist.json")
    c = Concierge(registry=None, state=state, keep=str, checklist=steps)
    seen = []
    steps.add = lambda step, package, blocks=None, trusted=False, trust=None, raised_by=None: (
        seen.append((package, trusted, trust)) or {"id": "s", "title": step["title"]})
    tool = next(t for t in c.core_tools(lambda: None) if t.name == "checklist_add")
    tool.fn({"title": "t", "why": "w"}, {})
    assert seen == [("core:concierge", False, "package")]


def test_the_running_cores_brain_gets_the_checklist_and_the_index(core):
    with core.app.app_context():
        from puffpai_core.api.groups.chat import brain
        b = brain()
    assert b.checklist is core.app.extensions["puffpai.checklist"] and callable(b.search)
    assert b.consent is core.app.extensions["puffpai.core_question"]


# -- 6. a core tool's own question ------------------------------------------------------------------
def _stopped_row(core):
    from puffpai_core.registry import row_from_manifest
    core.registry.add(row_from_manifest({"schema": "puffpai-package/1", "id": "notes-app", "kind": "app",
                                         "name": "Notes App", "version": "1.0.0", "description": "Notes.",
                                         "consent": "c"}, "made", status="stopped"))
    return core.registry.get("notes-app")


@pytest.mark.parametrize("answer,want", [("yes", True), ("no", False)])
def test_read_app_on_a_stopped_app_asks_with_a_normal_yes_card(core, answer, want):
    from puffpai_core.consent import card_sha
    from puffpai_core.zone2.apps import start_question
    row = _stopped_row(core)
    ask = wiring.core_question(core, wait_s=30)
    got = {}
    t = threading.Thread(target=lambda: got.update(v=ask(start_question(row))))
    t.start()
    pending = wait_for(lambda: core.consent.list(status="pending"), 10)
    rec = pending[0]
    assert rec["scope"] == {"kind": "tool_call", "target": "notes-app.start"}
    assert rec["card"]["title"] == "May I start Notes App?"
    core.consent.answer(rec["consent_id"], answer, "tap", None, by="test", card_sha=card_sha(rec["card"]))
    t.join(10)
    assert got["v"] is want


def test_a_core_question_with_no_answer_or_no_card_says_so_plainly(core):
    from puffpai_core.zone2.apps import start_question
    from puffpai_core.zone2.tools import ToolTable, call, make
    row = _stopped_row(core)
    assert wiring.core_question(core, wait_s=0.2)(start_question(row)) is None          # expired
    assert wiring.core_question(core)({"kind": "tool_call", "package": "notes-app"}) is None   # not a start
    q = start_question(row)
    tool = make("read_app", "Read an app.", {"type": "object", "properties": {"id": {"type": "string"}},
                                             "required": ["id"]}, lambda a, c: "read", question=lambda a, c: q)
    table = ToolTable([tool])
    no_card = call(table, "read_app", {"id": "notes-app"}, {"ask": None})
    assert not no_card["ok"] and "Notes App is stopped" in no_card["error"] and "puffpai start notes-app" in no_card["error"]
    unanswered = call(table, "read_app", {"id": "notes-app"}, {"ask": lambda _q: None})
    assert "Notes App is stopped" in unanswered["error"]
    said_no = call(table, "read_app", {"id": "notes-app"}, {"ask": lambda _q: False})
    assert "You said No, so Notes App was not started" in said_no["error"]


# -- 5. the adopted origin ----------------------------------------------------------------------
def _adopted(hello_copy, url):
    """An adopted app's manifest is the one the Core wrote (.buildbot/puffpai.json); the top-level one stays
    the project's own."""
    data = json.loads((hello_copy / "puffpai.json").read_text(encoding="utf-8"))
    data["origin"] = f"adopted:{url}"
    (hello_copy / ".buildbot").mkdir(exist_ok=True)
    (hello_copy / ".buildbot" / "puffpai.json").write_text(json.dumps(data), encoding="utf-8")


def test_an_adopted_app_keeps_its_adopted_origin_in_the_registry(core, hello_copy):
    url = "https://github.com/example/hello-tool"
    _adopted(hello_copy, url)
    staged = core.packages.add(str(hello_copy), adopted=url)
    assert staged["card"]["origin"] == f"adopted:{url}"
    assert yes(core, staged)["status"] == "installed"
    assert core.registry.get("hello-tool")["origin"] == f"adopted:{url}"


def test_an_adopted_origin_must_match_the_manifest_and_be_a_web_address(core, hello_copy):
    with pytest.raises(AddError):
        core.packages.add(str(hello_copy), adopted="https://github.com/example/other")
    with pytest.raises(AddError):
        core.packages.add(str(hello_copy), adopted="file:///C:/x")
    staged = core.packages.add(str(hello_copy))
    assert staged["card"]["origin"].startswith("local:")                  # a plain add is never adopted


def test_toolsmiths_port_hands_the_adopted_url_on(hello_copy):
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    url = "https://github.com/example/hello-tool"
    _adopted(hello_copy, url)
    seen = []

    class PM:
        def add(self, source, link=False, adopted=None):
            seen.append(adopted)
            return {"consent_id": "c1"}
    p = Ports.__new__(Ports)
    p.packages = PM()
    assert p.packages_add(hello_copy, adopted=url)["consent_id"] == "c1" and seen == [url]
    # the address comes from the Core's own record, never from a puffpai.json in the folder
    seen.clear()
    assert p.packages_add(hello_copy)["consent_id"] == "c1" and seen == [None]


def test_the_open_route_says_new_tab_for_an_adopted_app(core, tmp_path):
    from conftest import REPO
    import shutil
    src = tmp_path / "example-page"
    shutil.copytree(REPO / "packages" / "example-page", src)
    data = json.loads((src / "puffpai.json").read_text(encoding="utf-8"))
    url = "https://github.com/example/example-page"
    data["origin"] = f"adopted:{url}"
    (src / ".buildbot").mkdir(exist_ok=True)
    (src / ".buildbot" / "puffpai.json").write_text(json.dumps(data), encoding="utf-8")
    assert yes(core, core.packages.add(str(src), adopted=url))["status"] == "installed"
    page = data["ui"]["pages"][0]["id"]
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    r = core.app.test_client().post(f"/v1/packages/example-page/open/{page}", headers=owner)
    assert r.status_code == 200 and r.json["open"] == "new_tab"


# -- 4. puffpai check item 6 --------------------------------------------------------------------
SERVER = r'''
import http.server, os, threading, urllib.request
def ask(host):
    try:
        urllib.request.urlopen("http://" + host + "/", timeout=5).read()
    except Exception:
        pass
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a):
        pass
for h in HOSTS:
    threading.Thread(target=ask, args=(h,), daemon=True).start()
http.server.HTTPServer(("127.0.0.1", int(os.environ["PUFFPAI_PORT"])), H).serve_forever()
'''


def _caller_package(tmp_path, asks, declared):
    pkg = tmp_path / "src" / "asker"
    pkg.mkdir(parents=True)
    (pkg / "server.py").write_text(f"HOSTS = {asks!r}\n" + SERVER, encoding="utf-8")
    (pkg / "README.md").write_text("# Asker\n\n## PuffPAI\n\nAsks the internet for things.\n", encoding="utf-8")
    (pkg / "puffpai.json").write_text(json.dumps({
        "schema": "puffpai-package/1", "id": "asker", "kind": "app", "name": "Asker", "version": "1.0.0",
        "description": "Asks hosts for pages.", "author": "Cypher Sisters", "license": "MIT",
        "run": {"windows": {"type": "command", "start": ["{python}", "server.py"]},
                "default": {"type": "command", "start": ["{python}", "server.py"]}},
        "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 2},
        "ui": {"pages": [{"id": "main", "title": "Asker", "path": "/", "show": "new_tab"}]},
        "needs": {"hosts": declared}, "consent": "Asker asks the hosts it lists."}), encoding="utf-8")
    return pkg


def _item6(report):
    return next((s, w) for lv, i, s, w in report.items if (lv, i) == (1, 6))


def _waits_for_asks(monkeypatch):
    """Give the package's requests time to reach the proxy before the check stops it."""
    from puffpai_core.packages import check as check_mod
    real = check_mod._wait_healthy

    def slower(*a, **k):
        got = real(*a, **k)
        time.sleep(3)
        return got
    monkeypatch.setattr(check_mod, "_wait_healthy", slower)


def test_check_item_6_reports_undeclared_hosts_from_the_bridge_log(drawbridge, no_brain, tmp_path, monkeypatch):
    from puffpai_core.packages.check import FAIL, check
    _waits_for_asks(monkeypatch)
    pkg = _caller_package(tmp_path, ["declared.example.com", "sneaky.example.net"], ["declared.example.com"])
    status, words = _item6(check(pkg, start_timeout_s=30))
    assert status == FAIL and "sneaky.example.net" in words and "declared.example.com" not in words


def test_check_item_6_passes_when_every_host_is_declared(drawbridge, no_brain, tmp_path, monkeypatch):
    from puffpai_core.packages.check import PASS, check
    from puffpai_core.zone3 import service
    before = service._CURRENT
    _waits_for_asks(monkeypatch)
    pkg = _caller_package(tmp_path, ["declared.example.com"], ["declared.example.com"])
    status, words = _item6(check(pkg, start_timeout_s=30))
    assert status == PASS and "declared.example.com" in words
    assert service._CURRENT is before                                     # the Core's own Zone 3 stays current
