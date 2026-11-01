"""Lane I3a: the punch list from the live fresh-install proof (2026-10-22), one or more tests per item.

1 chat reaches Toolsmith; 2 ``puffpai check`` item 12 (tests in test_check.py); 3 the model address;
4 the plan reuses what is installed; 5 an honest Done; 6 the plan list once; 7 the terminal clear (tests in
test_wizard_pb.py); 8 built-ins by id; 9 a page's root; 10 no needless "Install Python"; 11 progress lines.
"""

import http.server
import json
import shutil
import sys
import threading
import types
from pathlib import Path

import pytest

from conftest import REPO, record_install
from toolsmith_fakes import Script, World, tool
from wizard_kit import Kit, TerminalRun
from zone2_doubles import FakeRuntime, call, make_brain
from puffpai_core import wiring
from puffpai_core.registry import row_from_manifest
from puffpai_core.wizard import lines as L
from puffpai_core.zone2 import loop
from puffpai_core.zone2 import settings as model_settings
from puffpai_core.zone2.settings import _ask_version as REAL_ASK_VERSION    # before the autouse stub
from puffpai_core.zone3.toolsmith import find, programs, tries

PACKAGES = REPO / "packages"
FRESHTABLE = json.loads((PACKAGES / "freshtable" / "puffpai.json").read_text(encoding="utf-8"))
ABILITIES = [{"tool": "freshtable.add_shopping", "description": "Put an item on the shopping list.", "changes": True,
              "needs_yes": True},
             {"tool": "freshtable.list_pantry", "description": "List what is in the pantry.", "changes": False,
              "needs_yes": False},
             {"tool": "freshtable.add_meal", "description": "Plan a meal for a day of the week.", "changes": True,
              "needs_yes": True}]


def freshtable_row(status="running", health=True):
    row = {"id": "freshtable", "name": "FreshTable", "kind": "app", "status": status,
           "description": FRESHTABLE["description"], "abilities": ABILITIES,
           "examples": FRESHTABLE["abilities"]["examples"],
           "health": {"ok": health, "checked_at": "2026-10-22T00:00:00Z", "reason": "Its page answers (200)."}}
    return row


# -- 1. chat reaches Toolsmith ------------------------------------------------------------------------
def test_chat_job_reads_a_github_address_and_plain_words():
    got = wiring.chat_job("please add github.com/techwithtim/Flask-Web-App-Tutorial.")
    assert got["name"] == "Flask-Web-App-Tutorial"
    assert got["pasted"] == ["https://github.com/techwithtim/Flask-Web-App-Tutorial"]
    assert find.github_of(got["pasted"][0]) == "techwithtim/Flask-Web-App-Tutorial"
    got = wiring.chat_job("Add an app for tracking my plants")
    assert got["name"] == "Tracking my plants" and "pasted" not in got


def test_the_prompt_sends_add_requests_to_ask_toolsmith():
    assert "ask_toolsmith" in loop.TOOL_NOTE and "add github.com/owner/repo" in loop.TOOL_NOTE
    assert "Never say something was added" in loop.TOOL_NOTE


def test_ask_toolsmith_gets_the_turn_and_says_its_result_in_the_same_conversation(state, freshtable):
    rt = FakeRuntime(call("ask_toolsmith", job="add github.com/koala73/worldmonitor"), {"text": "It is building."})
    brain, _, _ = make_brain(state, freshtable, rt)
    seen = {}

    def toolsmith(job, ctx):
        seen.update(job=job, keys=set(ctx))
        ctx["say_later"]("Toolsmith finished. worldmonitor: works.")
        return {"ok": True, "said": "Toolsmith is building it now."}
    brain.toolsmith = toolsmith
    specs = None
    got = brain.turn("add github.com/koala73/worldmonitor")
    specs = {t["function"]["name"]: t["function"]["description"] for t in rt.calls[0]["tools"]}
    assert "github.com/koala73/worldmonitor" in seen["job"]
    assert {"notify", "say_later", "conversation"} <= seen["keys"]
    assert "Toolsmith is building it now." in rt.seen(1) and got["text"] == "It is building."
    said = [m["content"] for m in brain.store.load("main") if m["role"] == "assistant"]
    assert "Toolsmith finished. worldmonitor: works." in said
    assert "GitHub address" in specs["ask_toolsmith"] and "ask_toolsmith" in specs["checklist_add"]


class ChatConsent:
    """The consent store's shape chat uses (get, wait, expire) over the fake ports' cards."""

    def __init__(self, ports, answer):
        self.ports, self.answer, self.status, self.expired = ports, answer, {}, []

    def get(self, cid):
        card = self.ports.consent.cards.get(cid) or {}
        return {"status": self.status.get(cid, "pending"), "card": (card.get("covers") or {}).get("card"),
                "card_sha": "sha-of-the-card", "floor": False}

    def wait(self, cid, timeout_s=None):
        if self.answer == "yes":
            self.ports.consent.say_yes(cid)
            self.status[cid] = "granted"
        elif self.answer == "no":
            self.status[cid] = "declined"
        return self.get(cid)

    def expire(self, cid, line):
        self.status[cid] = "expired"
        self.expired.append(line)


class Notices:
    def __init__(self):
        self.posted = []

    def post(self, package, body):
        self.posted.append((package, body))


class BrainDouble:
    """Stands in for the local brain's tool pick (``Brain.picks``): for an ask about shopping, the pantry or
    dinners it calls FreshTable's add_shopping, else ask_toolsmith. *package* "" asks whose tool it calls.
    ``asked=False`` plays a brain that can't be asked."""

    def __init__(self, asked=True):
        self.asked, self.seen = asked, []

    def __call__(self, package, ask):
        self.seen.append((package, ask))
        if not self.asked:
            return {"ok": False, "asked": False, "called": [], "reason": "the model server is not running"}
        hit = package in ("", "freshtable") and any(w in ask.lower() for w in ("shopping", "pantry", "dinner"))
        return {"ok": hit, "asked": True, "package": "freshtable" if hit else package,
                "called": ["freshtable.add_shopping"] if hit else ["ask_toolsmith"],
                "reason": "" if hit else "the brain called ask_toolsmith instead"}


@pytest.fixture
def chat_world(tmp_path, state):
    w = World(tmp_path, state)
    w.registry.add(row_from_manifest(FRESHTABLE, "local:x", status="running", port=18500))
    w.registry.update("freshtable", abilities=ABILITIES)
    w.registry.set_health("freshtable", True, "Its page answers (200).")
    w.ports.brain_picks = BrainDouble()               # the Core's brain_picks port (zone2.brain.picks)
    yield w
    w.host.stop(timeout=10)


@pytest.mark.parametrize("answer", ["yes", "no", "none"])
def test_chat_plan_shows_one_yes_card_builds_after_yes_and_reports(chat_world, answer):
    w = chat_world
    core = types.SimpleNamespace(consent=ChatConsent(w.ports, answer), notices=Notices())
    ask = wiring.chat_toolsmith(core, w.app, wait_s=1, every_s=0.01)
    cards, said, done = [], [], threading.Event()
    ctx = {"notify": cards.append, "say_later": lambda text: (said.append(text), done.set())}
    got = ask("add an app for my shopping list", ctx)
    assert [x[0] for x in w.ports.brain_picks.seen[:2]] == ["", ""]    # two asks: whose tool does the brain pick?
    assert len(cards) == 1 and cards[0]["consent_id"]                 # the plan's ONE Yes card, in chat
    lines = cards[0]["card"]["lines"]
    # What the tool does, in its own words; never its name (lane I8a).
    assert lines == ["My shopping list: FreshTable, already on your bot: it would put an item on the shopping list."]
    build = w.host.store.all()[-1]
    assert build["jobs"][0]["plan"]["reuse"] == "freshtable" and [r for r in w.runner.runs if r[0] == "plan"]
    if answer != "yes":
        assert got["ok"] is False and not [r for r in w.runner.runs if r[0] == "build"]
        assert got["error"] == (wiring.CHAT_SAID_NO if answer == "no" else wiring.CHAT_EXPIRED)
        return
    assert got["ok"] is True and "already on your bot" in got["said"]
    assert ("build", build["id"]) in w.runner.runs                   # started after her Yes (/start's check)
    assert done.wait(10)
    assert said[0].startswith("Toolsmith finished. My shopping list: works.") and "it can put an item" in said[0]
    assert core.notices.posted[0][0] == "toolsmith"


def test_chat_with_nothing_found_asks_no_yes(chat_world):
    w = chat_world
    core = types.SimpleNamespace(consent=ChatConsent(w.ports, "yes"), notices=Notices())
    cards = []
    got = wiring.chat_toolsmith(core, w.app, wait_s=1)("add an app for a world news map", {"notify": cards.append})
    assert got["ok"] is False and got["error"].startswith(wiring.CHAT_NOTHING) and cards == []
    assert w.ports.consent.cards == {}                                  # no card at all for an empty plan
    build = w.host.view(w.host.store.all()[-1]["id"])
    assert build["consent"]["id"] is None and build["jobs"][0]["status"] == "nothing_found"


def test_nothing_found_says_the_real_reason(chat_world):
    """The Bridge closed (no search) and her own pasted project with no run steps: said, not "paste one"."""
    from puffpai_core.zone3.toolsmith.errors import Refused
    w = chat_world

    def closed(url, **kw):
        raise Refused("The Bridge is closed for api.github.com, so nothing was sent.")
    w.ports.http_get = closed
    core = types.SimpleNamespace(consent=ChatConsent(w.ports, "yes"), notices=Notices())
    got = wiring.chat_toolsmith(core, w.app, wait_s=1)("add an app for a world news map", {})
    assert "GitHub could not be reached: the Bridge is closed" in got["error"] and "Paste a GitHub address" in got["error"]
    assert got["error"].count("Bridge is closed") == 1                          # one line, not one per host
    got = wiring.chat_toolsmith(core, w.app, wait_s=1)("add github.com/example/news-map", {})
    assert "news-map" in got["error"] and "Paste a GitHub address" not in got["error"]


# -- 3. the model address ---------------------------------------------------------------------------
def test_ollama_is_looked_for_on_this_computer_only_in_order():
    assert model_settings.ollama_candidates({"OLLAMA_HOST": "0.0.0.0:11500"}) == [
        "http://127.0.0.1:11500", "http://127.0.0.1:11434", "http://127.0.0.1:11435"]
    assert model_settings.ollama_candidates({"OLLAMA_HOST": "http://otherbox:11435"}) == [
        "http://127.0.0.1:11435", "http://127.0.0.1:11434"]                      # never another computer
    assert model_settings.ollama_candidates({}) == ["http://127.0.0.1:11434", "http://127.0.0.1:11435"]
    asked = []
    found = model_settings.find_local_ollama({}, ask=lambda url: asked.append(url) or url.endswith(":11435"))
    assert found == "http://127.0.0.1:11435" and asked == model_settings.ollama_candidates({})


class _Version(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"version": "0.12.0"}).encode() if self.path == "/api/version" else b"{}"
        self.send_response(200 if self.path == "/api/version" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_the_real_version_ask_finds_a_server_by_its_answer():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Version)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        port = srv.server_address[1]
        found = model_settings.find_local_ollama({"OLLAMA_HOST": f"127.0.0.1:{port}"}, ask=REAL_ASK_VERSION)
        assert found == f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_scan_keeps_the_address_and_the_core_reads_it(state, monkeypatch):
    monkeypatch.setattr(model_settings, "_ask_version", lambda url, timeout_s=1.5: url.endswith(":11435"))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    models = wiring.WizardModels(state)
    assert models.find_runtime() == "http://127.0.0.1:11435"
    assert model_settings.load(state).address == "http://127.0.0.1:11435"         # the Core reads it from here
    kit = Kit(state)
    kit.models.find_runtime = models.find_runtime
    screen = kit.engine().submit("scan", {})
    assert L.OLLAMA_FOUND.format(address="http://127.0.0.1:11435") in screen["lines"]
    census = json.loads((state / "settings" / "hardware.json").read_text(encoding="utf-8"))
    assert census["programs"]["ollama_address"] == "http://127.0.0.1:11435"


def test_the_scan_prefers_the_ollama_that_has_her_model():
    answers = lambda url: True                                                   # noqa: E731
    has = lambda url, model: url.endswith(":11435") and model == "qwen2.5:7b-instruct"   # noqa: E731
    env = {}
    assert model_settings.find_local_ollama(env, ask=answers, model="qwen2.5:7b-instruct", has=has) \
        == "http://127.0.0.1:11435"
    assert model_settings.find_local_ollama(env, ask=answers, model="", has=has) == "http://127.0.0.1:11434"
    assert model_settings.find_local_ollama(env, ask=answers, model="other:1b", has=has) == "http://127.0.0.1:11434"


def test_the_scan_says_when_no_ollama_answers(state):
    kit = Kit(state)
    kit.models.find_runtime = lambda: ""
    assert L.OLLAMA_NOT_FOUND in kit.engine().submit("scan", {})["lines"]


# -- 4. the plan reuses what is installed, when the brain picks its tools ----------------------------------
PROOF_JOBS = [("Shopping list manager", "Tracks the shopping list", ""),
              ("Pantry inventory tracker", "Monitors available pantry supplies", ""),
              ("Meal planner", "Plans dinners for the week", "")]
#: Jobs whose names share no word with FreshTable (verifier, 10-23): only the brain can say they fit.
SYNONYMS = [("Grocery List Manager", "Maintains shopping and pantry lists", "Can I add an item to my shopping list?"),
            ("Grocery list", "keep a shopping list", ""),
            ("Kitchen inventory", "track what is in my pantry", ""),
            ("Weekly menu", "plan dinners for the week", "")]
#: Real words a word match alone took for FreshTable (run, plan, table, egg, shop).
NOT_FRESHTABLE = ["plan my workouts and log my runs", "Periodic table", "Egg timer", "Coffee shop finder",
                  "Running log", "World news map"]


class FitLink:
    """Toolsmith's link with only the brain's tool pick (``link.fits``)."""

    def __init__(self, brain):
        self.brain = brain

    def fits(self, package, ask):
        return self.brain(package, ask)


def test_the_job_ask_is_its_sample_ask_else_what_it_does_else_its_name():
    assert find.job_ask({"name": "Grocery list", "does": "keep a shopping list",
                         "sample_ask": "What is on my list?"}) == "What is on my list?"
    assert find.job_ask({"name": "Grocery list", "does": "keep a shopping list",
                         "sample_ask": "What does Grocery list show right now?"}) == "keep a shopping list"
    assert find.job_ask({"name": "Grocery list", "does": ""}) == "Grocery list"


@pytest.mark.parametrize("name, does, ask", PROOF_JOBS + SYNONYMS)
def test_reused_when_the_brain_picks_its_tools(tmp_path, name, does, ask):
    brain = BrainDouble()
    f = find.Finder(FitLink(brain), tmp_path)
    rows = [freshtable_row(), {"id": "example-page", "name": "My notes", "kind": "page", "status": "running"}]
    job = {"name": name, "does": does, "sample_ask": ask}
    assert f.brain_reuse(job, rows, []) == "freshtable"
    # The sample ask and the "does" text (or one of them twice), each over everything installed.
    assert brain.seen == [("", ask), ("", does)] if ask else brain.seen == [("", does), ("", does)]


def test_the_two_asks_must_agree(tmp_path):
    class Split(BrainDouble):
        def __call__(self, package, ask):
            self.seen.append((package, ask))
            pkg = "freshtable" if "shopping" in ask else "notes-app"
            return {"ok": True, "asked": True, "package": pkg, "called": [f"{pkg}.add_item"], "reason": ""}
    rows = [freshtable_row(), {**freshtable_row(), "id": "notes-app", "name": "Notes"}]
    not_used, picked = [], []
    job = {"name": "Egg timer", "does": "remind me when the eggs are done",
           "sample_ask": "put eggs on the shopping list"}
    assert find.Finder(FitLink(Split()), tmp_path).brain_reuse(job, rows, not_used, picked) is None
    # Her words are plain (lane I8a); the tool names stay in the build log's detail.
    assert not_used[0]["why"] == "Nothing on your bot does this yet."
    assert "the two asks picked different things" in not_used[0]["detail"]
    assert "freshtable.add_item" in not_used[0]["detail"] and "notes-app.add_item" in not_used[0]["detail"]
    assert [p["tool"] for p in picked] == ["freshtable.add_item", "notes-app.add_item"]


def test_agreement_keeps_the_tool_picked_on_each_ask(tmp_path):
    picked = []
    job = {"name": "Grocery list", "does": "keep a shopping list", "sample_ask": "Can I add milk to my shopping list?"}
    assert find.Finder(FitLink(BrainDouble()), tmp_path).brain_reuse(job, [freshtable_row()], [], picked) \
        == "freshtable"
    assert picked == [{"ask": "Can I add milk to my shopping list?", "tool": "freshtable.add_shopping"},
                      {"ask": "keep a shopping list", "tool": "freshtable.add_shopping"}]
    plan = find.Finder(FitLink(BrainDouble()), tmp_path).plan_job({**job, "pasted": []}, [], [freshtable_row()], {})
    assert plan["reuse"] == "freshtable" and [p["tool"] for p in plan["picked"]] == ["freshtable.add_shopping"] * 2


def test_one_text_is_asked_twice():
    assert find.job_asks({"name": "Grocery list", "does": "keep a shopping list"}) == ["keep a shopping list"] * 2
    assert find.job_asks({"name": "Grocery list", "sample_ask": "What is on my list?"}) == ["What is on my list?"] * 2
    assert find.job_asks({"name": "Grocery list", "does": "x", "sample_ask": "What does Grocery list show right now?"}) \
        == ["x", "x"]
    assert find.job_asks({"name": "Grocery list"}) == ["Grocery list", "Grocery list"]
    assert find.job_asks({"name": "G", "does": "same", "sample_ask": "same"}) == ["same", "same"]


def test_a_second_ask_that_picks_nothing_means_no_reuse(tmp_path):
    not_used = []
    job = {"name": "Egg timer", "does": "remind me when the eggs are done", "sample_ask": "add eggs to the pantry"}
    assert find.Finder(FitLink(BrainDouble()), tmp_path).brain_reuse(job, [freshtable_row()], not_used) is None
    assert not_used[0]["why"] == "Nothing on your bot does this yet."
    assert "none of them does this" in not_used[0]["detail"] and "remind me when the eggs are done" in not_used[0]["detail"]


@pytest.mark.parametrize("name", NOT_FRESHTABLE)
def test_not_reused_when_the_brain_does_not_pick_it(tmp_path, name):
    f = find.Finder(FitLink(BrainDouble()), tmp_path)
    not_used = []
    assert f.brain_reuse({"name": name, "does": name, "sample_ask": ""}, [freshtable_row()], not_used) is None
    assert not_used[0]["why"] == "Nothing on your bot does this yet." and "none of them does this" in not_used[0]["detail"]


def test_a_brain_that_cant_be_asked_means_no_reuse_and_says_why(tmp_path):
    f = find.Finder(FitLink(BrainDouble(asked=False)), tmp_path)
    not_used = []
    assert f.brain_reuse({"name": "Shopping list", "does": "keep a shopping list"}, [freshtable_row()],
                         not_used) is None
    assert "could not be asked" in not_used[0]["why"] and "model server is not running" in not_used[0]["detail"]
    assert find.picks(object(), "", "x")["asked"] is False        # no fits on the link: not asked


def test_only_an_app_or_tool_that_runs_is_reused(tmp_path):
    for row in (freshtable_row(status="removed"), {**freshtable_row(), "kind": "page"}):
        brain = BrainDouble()
        assert find.Finder(FitLink(brain), tmp_path).brain_reuse(
            {"name": "Grocery list", "does": "keep a shopping list"}, [row], []) is None
        assert brain.seen == []                                     # nothing reusable: the brain is not asked


def test_her_pasted_project_is_tried_not_swapped():
    job = {"name": "Shopping list", "does": "", "pasted": ["https://github.com/example/shop"]}
    assert find.reuse_for(job, [freshtable_row()], {}) is None


def test_brain_picks_is_the_concierge_pick_as_a_dry_run(state, freshtable):
    rt = FakeRuntime(call("freshtable__add_to_list", item="milk"))
    brain, items, _ = make_brain(state, freshtable, rt)
    got = brain.picks("freshtable", "put milk on the shopping list")
    assert got["ok"] is True and got["called"] == ["freshtable.add_to_list"] and items == []   # nothing ran
    assert "freshtable__add_to_list" in [t["function"]["name"] for t in rt.calls[0]["tools"]]
    brain, _, _ = make_brain(state / "b", freshtable, FakeRuntime(call("ask_toolsmith", job="a run log")))
    assert brain.picks("freshtable", "log my runs") == {"ok": False, "package": "freshtable",
                                                         "called": ["ask_toolsmith"],
                                                         "reason": "the brain called ask_toolsmith instead"}
    brain, _, _ = make_brain(state / "c", freshtable, FakeRuntime({"text": "I can't."}))
    assert brain.picks("freshtable", "periodic table")["reason"] == "the brain called none of its tools"
    # No package named: whose tool does it call?
    brain, _, _ = make_brain(state / "d", freshtable, FakeRuntime(call("freshtable__add_to_list", item="milk")))
    got = brain.picks("", "Can I add an item to my shopping list?")
    assert got["ok"] is True and got["package"] == "freshtable"
    brain, _, _ = make_brain(state / "e", freshtable, FakeRuntime(call("list_installed")))
    got = brain.picks("", "egg timer")
    assert got["ok"] is False and got["reason"] == "the brain called list_installed instead"


def _reused_build(w, job):
    st = w.host.settings()
    b = w.host.store.create("meals", [job], st)
    w.host.store.set_plan(b["id"], {"jobs": [{**job, "plan": {"reuse": "freshtable", "reuse_name": "FreshTable"}}]},
                          st, "")
    w.host.store.change(b["id"], lambda x: x.update(yes={"given": True}))
    return b


def test_reuse_is_working_only_after_the_brain_picks_and_health_passes(chat_world):
    from puffpai_core.zone3.toolsmith import agent
    w = chat_world
    job = {"name": "Shopping list manager", "does": "Tracks the shopping list", "sample_ask": ""}
    b = _reused_build(w, job)
    assert w.host.view(b["id"])["card"]["lines"] == ["Shopping list manager: FreshTable, already on your bot."]
    assert w.host.view(b["id"])["jobs"][0]["plan"]["picked"] == []        # no brain pick on record: no "would use"
    assert w.host.view(b["id"])["envelope"]["install_hosts"] == []      # it downloads nothing
    agent.Builder(w.link(b["id"])).run()
    j = w.host.view(b["id"])["jobs"][0]
    assert j["status"] == "working" and "it can put an item on the shopping list" in j["run"]["line"]
    assert "add_shopping" not in j["run"]["line"]
    assert "freshtable.add_shopping" in j["run"]["result"]["proof"]["ask"]["called"]   # the name: the build log
    # The brain does not pick it for this job: not working, whatever its health.
    b = _reused_build(w, {"name": "Running log", "does": "log my runs", "sample_ask": ""})
    agent.Builder(w.link(b["id"])).run()
    j = w.host.view(b["id"])["jobs"][0]
    assert j["status"] == "not_yet" and "did not pick it for this job" in j["run"]["line"]
    assert "freshtable" not in json.dumps(w.host.store.proven().get("running log", []))
    # It picks it, but health fails: not working.
    w.registry.set_health("freshtable", False, "Its page did not answer.")
    b = _reused_build(w, job)
    agent.Builder(w.link(b["id"])).run()
    assert w.host.view(b["id"])["jobs"][0]["status"] == "not_yet"


def test_the_fits_route_checks_its_body(chat_world):
    w = chat_world
    b = w.host.store.create("meals", [{"name": "Meals"}], w.host.settings())
    link = w.link(b["id"])
    assert link.fits("freshtable", "put milk on the shopping list")["ok"] is True
    with pytest.raises(Exception):
        link._call("POST", "/fits", {"package": "freshtable", "ask": ["not", "a", "string"]})
    with pytest.raises(Exception):
        link.fits("no-such-package", "x")


def test_the_registry_toolsmith_sees_carries_abilities(chat_world):
    w = chat_world
    st = w.host.settings()
    b = w.host.store.create("meals", [{"name": "Meals"}], st)
    row = next(r for r in w.link(b["id"]).registry()["rows"] if r["id"] == "freshtable")
    assert row["abilities"][0]["tool"] == "freshtable.add_shopping" and row["description"]


# -- 5. an honest Done ---------------------------------------------------------------------------------
def _done_with(state, results):
    kit = Kit(state)
    kit.toolsmith.results = lambda build: results
    eng = kit.engine()
    eng.set_build("b1")
    from puffpai_core.wizard.engine import DoneStep
    return DoneStep(eng)._said()


def test_done_says_what_really_happened_job_by_job(state):
    said = _done_with(state, [{"name": "Shopping list", "state": "not_yet", "reason": "No app worked yet."},
                              {"name": "Meal planner", "state": "nothing_found", "reason": "Nothing found yet."}])
    assert said[0] == L.DONE_NONE and "Your bot is set up" not in " ".join(said)
    assert said[1] == "Shopping list: not working yet: No app worked yet."
    assert said[2] == "Meal planner: nothing found: Toolsmith found no app for it."
    assert said[-1] == L.DONE_NEXT
    said = _done_with(state / "b", [{"name": "Shopping list", "state": "working", "reason": ""},
                                    {"name": "Meal planner", "state": "not_yet", "reason": ""}])
    assert said[0] == L.DONE_SOME
    said = _done_with(state / "c", [{"name": "Shopping list", "state": "working", "reason": "FreshTable works."}])
    assert said == [L.DONE_ALL, "Shopping list: works."]


def test_done_never_says_set_up_when_results_cant_be_read(state):
    kit = Kit(state)

    def broken(build):
        raise RuntimeError("no host")
    kit.toolsmith.results = broken
    eng = kit.engine()
    eng.set_build("b1")
    from puffpai_core.wizard.engine import DoneStep
    said = DoneStep(eng)._said()
    assert said == [L.DONE_UNREAD] and "set up" not in said[0]


# -- 6. the plan list once ------------------------------------------------------------------------------
def test_the_terminal_prints_the_plan_list_once(state):
    kit = Kit(state)
    run = TerminalRun(kit)
    kit.engine().set_build("b1")
    run.engine.set_build("b1")
    run.step("plan", {})
    run.step("yes", {"yes": "no"})
    item = "World news map: World Monitor (koala73/worldmonitor), needs Docker Desktop"
    assert run.all_text().count(item) == 1


# -- 8. built-ins by id ----------------------------------------------------------------------------------
def test_a_builtin_is_found_by_its_id():
    from puffpai_core.packages.manager import builtin_source
    assert Path(builtin_source("freshtable")) == (PACKAGES / "freshtable").resolve()
    assert builtin_source("example-page") and builtin_source("no-such-package") is None
    assert builtin_source("../packages/freshtable") is None and builtin_source("FreshTable") is None


def test_puffpai_add_a_builtin_by_id_through_the_same_yes(capsys, monkeypatch, drawbridge, state):
    from puffpai_core.cli import main
    from puffpai_core.registry import Registry
    answers = []
    monkeypatch.setattr("builtins.input", lambda prompt: answers.append(prompt) or "y")
    assert main(["add", "example-page"]) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert answers and "Say Yes" in answers[0]                     # the one Yes card was asked
    assert Registry().get("example-page") is not None and "not a folder" not in out


# -- 9. a page's root -----------------------------------------------------------------------------------
def test_a_page_root_serves_its_index_html(state):
    from puffpai_core.api import create_app
    app = create_app(state)
    core = app.extensions["puffpai"]
    folder = state / "packages" / "example-page"
    shutil.copytree(PACKAGES / "example-page", folder)
    manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    record_install(core, folder, manifest, "local:" + str(folder), status="running")
    client = app.test_client()
    pages = {"puffpai.listener": "pages"}
    r = client.get("/", environ_base=pages, base_url="http://example-page.localhost:8422")
    assert r.status_code == 200 and b"<html" in r.data.lower()
    r = client.get("/nope.txt", environ_base=pages, base_url="http://example-page.localhost:8422")
    assert r.status_code == 404


# -- 10. no needless "Install Python" -------------------------------------------------------------------
def test_python_is_never_missing_while_the_core_runs_on_3_11():
    assert sys.version_info[:2] >= programs.PYTHON_MIN and programs.core_python()
    assert programs.missing(["python"]) == []


def test_a_python_app_plan_has_no_install_python_step(tmp_path, state, monkeypatch):
    monkeypatch.setattr(programs.shutil, "which", lambda name: None)       # no python, no node on the PATH
    w = World(tmp_path, state)
    st = w.host.settings()
    job = {"name": "Flask web app", "does": "notes"}
    b = w.host.store.create("flask", [job], st)
    cand = {"full_name": "techwithtim/Flask-Web-App-Tutorial", "runtime": "python", "programs": ["python", "node"],
            "readme_steps": ["pip install -r requirements.txt", "python main.py"]}
    got = w.host.store.set_plan(b["id"], {"jobs": [{**job, "plan": {"candidates": [cand]}}]}, st, "")
    assert [s["id"] for s in got["setup"]] == ["install-node"]                # node is still asked for


def test_install_program_python_raises_no_step():
    asked = []
    fake = types.SimpleNamespace(cand={"name": "Flask app"}, ask=lambda step: asked.append(step) or {"ok": False})
    got = tries.Try.t_install_program(fake, {"name": "python", "why": "It is a Flask app."})
    assert got["ok"] and "python -m venv" not in got["said"] and '"venv"' in got["said"] and asked == []
    tries.Try.t_install_program(fake, {"name": "ruby"})
    assert asked and asked[0]["id"] == "install-ruby"


# -- 11. progress lines ----------------------------------------------------------------------------------
def test_toolsmith_says_what_it_does_now_while_it_plans(tmp_path, state):
    w = World(tmp_path, state)
    b = w.host.store.create("news", [{"name": "World news map"}], w.host.settings())
    w.link(b["id"]).now("Job 1 of 1, World news map: searching GitHub for \"world news\" (search 1 of at most 6)")
    assert w.host.view(b["id"])["now"].startswith("Job 1 of 1, World news map: searching GitHub")


def test_the_now_route_takes_only_a_string(tmp_path, state):
    w = World(tmp_path, state)
    b = w.host.store.create("news", [{"name": "World news map"}], w.host.settings())
    link = w.link(b["id"])
    for bad in (["a", "list"], {"a": "dict"}, 7, None):
        with pytest.raises(Exception):
            link._call("PUT", "/now", {"line": bad})
    assert not w.host.view(b["id"]).get("now")


def test_a_job_keeps_one_now_line_until_it_ends(tmp_path, state):
    w = World(tmp_path, state)
    b = w.build_with_yes([{"full_name": "example/news-map", "name": "news-map"}])
    jid = b["jobs"][0]["id"]
    w.host.store.job_update(b["id"], jid, {"now": "news-map (app 1 of 1): step 3 of at most 20: running npm"})
    assert w.host.view(b["id"])["jobs"][0]["run"]["now"].endswith("running npm")
    w.host.store.job_update(b["id"], jid, {"state": "working", "line": "It works."})
    assert w.host.view(b["id"])["jobs"][0]["run"]["now"] == ""


def test_a_try_says_its_step_of_at_most_budget(tmp_path, state):
    w = World(tmp_path, state)
    b = w.build_with_yes([{"full_name": "example/hello-page", "name": "Hello Page"}])
    link = w.link(b["id"])
    s = link.build()["settings"]
    nows = []

    class Hooks(tries.Hooks):
        label = "Hello Page (app 1 of 2)"

        def now(self, line):
            nows.append(f"{self.label}: {line}")
    job = b["jobs"][0]
    t = tries.Try(job, job["plan"]["candidates"][0], link=link, settings=s, hooks=Hooks())
    w.ports.local_replies = Script([tool("give_up", reason="no page")])
    tries.drive(t, tries.Models(link))
    budget = s["budgets"]["adopt_steps"]
    assert f"Hello Page (app 1 of 2): step 1 of at most {budget}: asking the model what to do next" in nows
    assert f"Hello Page (app 1 of 2): step 1 of at most {budget}: giving up" in nows
    w.host.stop(timeout=10)


def test_the_build_step_shows_each_new_now_line(state):
    kit = Kit(state)
    eng = kit.engine()
    eng.set_build("b1")
    feed = [{"jobs": [{"id": "j", "name": "News", "status": "building: X", "lines": [], "now": "X: step 1"}],
             "steps": [], "cards": []},
            {"jobs": [{"id": "j", "name": "News", "status": "building: X", "lines": [], "now": "X: step 1"}],
             "steps": [], "cards": []},
            {"jobs": [{"id": "j", "name": "News", "status": "building: X", "lines": [], "now": "X: step 2"}],
             "steps": [], "cards": []}]
    kit.toolsmith.progress = lambda build: feed.pop(0)
    kit.toolsmith.answer_step = lambda *a: ""
    got = [eng.submit("build", {})["lines"] for _ in range(3)]
    assert "News: X: step 1" in got[0] and got[1] == [] and got[2] == ["News: X: step 2"]


def test_a_slow_goal_says_so_in_both_renderers(state):
    import time as _time
    from puffpai_core.wizard.engine import Engine
    kit = Kit(state)
    slow = kit.toolsmith.interpret

    def interpret(goal, use_brain):
        _time.sleep(0.3)
        return slow(goal, use_brain)
    kit.toolsmith.interpret = interpret
    eng = Engine(state, kit.services(), platform="windows", env=kit.env, census_fn=lambda: kit.census,
                 brain_wait_s=0, sleep=lambda s: None, quick_s=0.01)
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "model.json").write_text('{"name": "qwen2.5:7b-instruct"}', encoding="utf-8")
    first = eng.submit("goal", {"goal": "a world news map"})
    assert first["status"] == "working" and first["lines"] == [L.GOAL_WORKING] and first["poll_s"]
    for _ in range(100):
        screen = eng.submit("goal", {})
        if screen["status"] != "working":
            break
        _time.sleep(0.05)
    assert screen["status"] == "done" and "Job 1: World news map" in " ".join(screen["lines"])


def test_the_plan_step_shows_what_toolsmith_does_now(state):
    kit = Kit(state)
    eng = kit.engine()
    eng.set_build("b1")
    states = [{"ready": False, "now": ""}, {"ready": False, "now": "Job 1 of 1: reading the README of a/b"},
              {"ready": True}]
    kit.toolsmith.plan_start = lambda build: states.pop(0)
    one = eng.submit("plan", {})
    two = eng.submit("plan", {})
    three = eng.submit("plan", {})
    assert one["status"] == "working" and one["lines"] == [L.PLAN_WORKING]
    assert two["lines"] == ["Job 1 of 1: reading the README of a/b"]
    assert three["status"] != "working"


def test_ports_ask_the_running_cores_brain_first(state):
    """The Core's own Brain (the one chat uses, with the packages' tokens), not a bare one on the folder."""
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    p = Ports(state)
    p.picker = lambda package, ask: {"ok": True, "called": [f"{package}.add_shopping"], "reason": ""}
    assert p.brain_picks("freshtable", "milk") == {"ok": True, "called": ["freshtable.add_shopping"], "reason": "",
                                                   "asked": True}

    def down(package, ask):
        raise RuntimeError("the model server is not running")
    p.picker = down
    got = p.brain_picks("freshtable", "milk")
    assert got["asked"] is False and "not running" in got["reason"]
