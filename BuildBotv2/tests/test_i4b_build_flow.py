"""Lane I4b: Toolsmith's build flow, from the second live fresh-install proof (2026-10-23).

1 an offer she ticks when the brain is unsure; 2 fewer, relevant candidates; 3 a pasted GitHub address is
tried alone; 4 (tests in test_i3b_bridge_cards.py) hold cards in one batch per job; 5 the port the app
really uses; 6 the chat result says why, one line per candidate.

Nothing here reaches the internet: GitHub's answers are recorded, and every server a test starts it stops
by its own handle (its own process number).
"""

import json
import socket
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from conftest import wait_for
from test_i3a_proof_fixes import ABILITIES, FRESHTABLE, BrainDouble, FitLink, Notices, ChatConsent, freshtable_row
from toolsmith_fakes import FIXTURES, FakePorts, World, make_venv, script
from wizard_kit import FakeToolsmith, Kit
from puffpai_core import wiring
from puffpai_core.registry import row_from_manifest
from puffpai_core.zone3.toolsmith import agent, find, runbook, tries
from puffpai_core.zone3.toolsmith_host import settings as settings_mod
from puffpai_core.zone3.toolsmith_host import knowledge as kmod

RAW = "https://raw.githubusercontent.com/"
SEARCH = "https://api.github.com/search/repositories?*"
RUNS = "## Quick start\n```bash\nnpm install\nnpm start\n```\n"


class SampleOnly(BrainDouble):
    """A 7B brain that is unsure: it picks FreshTable's tool for the sample ask, nothing for the "does" text."""

    def __call__(self, package, ask):
        self.seen.append((package, ask))
        if package in ("", "freshtable") and "milk" in ask.lower():
            return {"ok": True, "asked": True, "package": "freshtable", "called": ["freshtable.add_shopping"],
                    "reason": ""}
        return {"ok": False, "asked": True, "package": package, "called": ["ask_toolsmith"],
                "reason": "the brain called ask_toolsmith instead"}


SHOP = {"name": "Shopping List Manager", "does": "keep a shopping list", "sample_ask": "Put milk on my shopping list"}


# -- 1. an offer she ticks: the brain is unsure, so she decides ---------------------------------------
def test_one_ask_picking_a_package_is_an_offer_not_a_reuse(tmp_path):
    f = find.Finder(FitLink(SampleOnly()), tmp_path / "c")
    not_used, picked, offer = [], [], {}
    assert f.brain_reuse({**SHOP, "pasted": []}, [freshtable_row()], not_used, picked, offer) is None
    assert offer["package"] == "freshtable" and offer["tool"] == "freshtable.add_shopping"
    assert "is on the plan as an offer you can tick" in not_used[0]["why"]
    plan = f.plan_job({**SHOP, "pasted": []}, [], [freshtable_row()], {})
    assert plan["reuse"] is None and plan["offer"]["package"] == "freshtable"


def test_both_asks_agreeing_still_reuse_at_once(tmp_path):
    plan = find.Finder(FitLink(BrainDouble()), tmp_path / "c").plan_job(
        {**SHOP, "pasted": []}, [], [freshtable_row()], {})
    assert plan["reuse"] == "freshtable" and "offer" not in plan


@pytest.fixture
def offer_world(tmp_path, state):
    w = World(tmp_path, state)
    w.registry.add(row_from_manifest(FRESHTABLE, "local:x", status="running", port=18500))
    w.registry.update("freshtable", abilities=ABILITIES)
    w.registry.set_health("freshtable", True, "Its page answers (200).")
    w.ports.brain_picks = SampleOnly()
    yield w
    w.host.stop(timeout=10)


def _offer_plan(w):
    """A planned build with an offer and one candidate, its card raised by the Core (agent_plan)."""
    st = w.host.settings()
    b = w.host.store.create("meals", [SHOP], st)
    cand = {"name": "mern-shopping-list", "full_name": "example/mern-shopping-list", "runtime": "node",
            "source": "github"}
    offer = {"package": "freshtable", "name": "FreshTable", "tool": "freshtable.add_shopping",
             "ask": SHOP["sample_ask"], "picked": [{"ask": SHOP["sample_ask"], "tool": "freshtable.add_shopping"}]}
    w.host.store.change(b["id"], lambda x: x.update(planning=True))
    w.host.agent_plan(b["id"], {"jobs": [{**SHOP, "plan": {"candidates": [cand], "offer": offer}}]})
    return w.host.view(b["id"])


def test_the_card_shows_the_offer_unticked(offer_world):
    b = _offer_plan(offer_world)
    lines = b["card"]["lines"]
    assert ("Shopping List Manager: FreshTable may cover this: it would put an item on the shopping list. Tick to "
            "use it instead of searching.") in lines
    assert "add_shopping" not in " ".join(lines)
    assert any(ln.startswith("Shopping List Manager: try mern-shopping-list first") for ln in lines)
    assert b["card"]["offers"][0]["package"] == "freshtable"
    assert b["envelope"]["jobs"][0]["offer"] == "freshtable"
    assert b["jobs"][0]["plan"]["reuse"] is None and "use" not in b       # nothing ticked by default


def test_no_tick_searches_as_planned(offer_world):
    w = offer_world
    b = _offer_plan(w)
    cid = b["consent"]["id"]
    w.ports.consent.say_yes(cid)
    got = w.host.start(b["id"], cid)
    p = got["jobs"][0]["plan"]
    assert p["reuse"] is None and [c["ref"] for c in p["candidates"]] == ["github:example/mern-shopping-list"]
    assert got["yes"]["use"] == {}


def test_her_tick_reuses_it_and_the_build_time_checks_still_decide(offer_world):
    w = offer_world
    b = _offer_plan(w)
    cid = b["consent"]["id"]
    assert w.host.use_offers(b["id"], cid, [0]) == {b["jobs"][0]["id"]: "freshtable"}
    w.ports.consent.say_yes(cid)
    got = w.host.start(b["id"], cid)
    p = got["jobs"][0]["plan"]
    assert p["reuse"] == "freshtable" and p["reuse_by"] == "tick" and p["candidates"] == []
    assert got["yes"]["use"] == {b["jobs"][0]["id"]: "freshtable"}
    agent.Builder(w.link(b["id"])).run()
    j = w.host.view(b["id"])["jobs"][0]
    assert j["status"] == "working", j["run"]
    assert "you ticked it for this job" in j["run"]["line"] and "it can put an item on the shopping list" in j["run"]["line"]
    assert "add_shopping" not in j["run"]["line"]
    # The re-ask with the package named still decides: a brain that does not pick it means not working.
    b2 = _offer_plan(w)
    w.host.use_offers(b2["id"], b2["consent"]["id"], [0])
    w.ports.consent.say_yes(b2["consent"]["id"])
    # Lane I18e: a job that works is never run again by the same build, so the brain says no before the start.
    w.ports.brain_picks = lambda package, ask: {"ok": False, "asked": True, "called": [], "reason": "no pick"}
    w.host.start(b2["id"], b2["consent"]["id"])
    assert w.host.view(b2["id"])["jobs"][0]["status"] == "not_yet"


def test_a_tick_is_only_hers_and_only_before_her_yes(offer_world):
    w = offer_world
    b = _offer_plan(w)
    cid = b["consent"]["id"]
    with pytest.raises(ValueError):
        w.host.use_offers(b["id"], cid, [3])                        # no offer on job 3
    with pytest.raises(PermissionError):
        w.host.use_offers(b["id"], "c_other", [0])                  # another card
    # Toolsmith's own token can't reach the owner's route.
    tok = w.core.tokens.issue_toolsmith(b["id"])
    r = w.app.test_client().put(f"/v1/toolsmith/builds/{b['id']}/use", json={"consent_id": cid, "use": [0]},
                                headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403) and "use" not in w.host.store.read(b["id"])
    w.ports.consent.say_yes(cid)
    w.host.start(b["id"], cid)
    with pytest.raises(PermissionError):
        w.host.use_offers(b["id"], cid, [0])                        # after her Yes: fixed


def test_an_offer_must_name_an_installed_app(offer_world):
    w = offer_world
    st = w.host.settings()
    b = w.host.store.create("meals", [SHOP], st)
    w.host.store.change(b["id"], lambda x: x.update(planning=True))
    w.host.agent_plan(b["id"], {"jobs": [{**SHOP, "plan": {"candidates": [], "offer": {"package": "not-here"}}}]})
    b = w.host.view(b["id"])
    assert "offer" not in b["jobs"][0]["plan"] and b["consent"]["id"] is None


class OfferToolsmith(FakeToolsmith):
    def __init__(self):
        super().__init__()
        self.yes_with = []

    def offers(self, build):
        return [{"job": 0, "job_name": "Shopping List Manager", "package": "freshtable", "name": "FreshTable",
                 "tool": "freshtable.add_shopping", "line": "..."}]

    def yes(self, build, use=None):
        self.yes_with.append(use)
        return {"ok": True}


@pytest.mark.parametrize("pick, use", [("none", None), ("0", [0]), ("", None)])
def test_the_wizard_yes_step_asks_for_ticks_unticked_by_default(state, pick, use):
    kit = Kit(state)
    kit.toolsmith = OfferToolsmith()
    e = kit.engine()
    e.set_build("b1")
    screen = e.submit("yes", {}, renderer="terminal")
    assert screen["field"]["id"] == "use" and screen["field"]["default"] == "none"
    assert [o["value"] for o in screen["field"]["options"]] == ["none", "0"]
    done = e.submit("yes", {"use": pick, "yes": "yes"}, renderer="terminal")
    assert done["status"] == "done" and kit.toolsmith.yes_with == [use]
    assert any("Toolsmith uses FreshTable" in ln for ln in done["lines"]) == bool(use)


def test_no_offers_no_tick_question(state):
    kit = Kit(state)
    e = kit.engine()
    e.set_build("b1")
    assert e.submit("yes", {}, renderer="terminal")["field"]["id"] == "yes"


# -- 2. fewer, relevant candidates (recorded GitHub answers, the proof's own kinds of results) ----------
AWESOME = """# Awesome-Selfhosted

## Blogging Platforms

- [Mataroa](https://github.com/mataroablog/mataroa) - Naked blogging platform for minimalists. `MIT` `Python`
- [WriteFreely](https://github.com/writefreely/writefreely) - Writing software for starting a minimalist, federated blog. `AGPL-3.0` `Go`

## Bookmarks

- [Servas](https://github.com/beromir/Servas) - A self-hosted bookmark management tool. Organize lists of links. `GPL-3.0` `PHP`

## Recipe Management

- [RecipeSage](https://github.com/julianpoy/RecipeSage) - A collaborative recipe keeper, meal planner and shopping list manager. `AGPL-3.0` `Nodejs`
- [TRIP](https://github.com/itskovacs/trip) - Minimalist POI Map Tracker and Trip Planner. `MIT` `Python`
"""
SEARCH_ITEMS = {"items": [
    {"full_name": "example/mern_shopping_list", "name": "mern_shopping_list", "description": "Shopping list app",
     "stargazers_count": 900, "language": "JavaScript", "topics": [], "default_branch": "main"},
    {"full_name": "healthchecks/healthchecks", "name": "healthchecks",
     "description": "A cron monitoring tool, with a list of checks", "stargazers_count": 8000, "language": "Python",
     "topics": [], "default_branch": "main"}]}


def _shop_ports(tmp_path):
    web = {SEARCH: (200, json.dumps(SEARCH_ITEMS)),
           RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md": (200, AWESOME)}
    for full in ("example/mern_shopping_list", "healthchecks/healthchecks"):
        web[RAW + f"{full}/main/README.md"] = (200, f"# {full}\nA project.\n{RUNS}")
    for full in ("mataroablog/mataroa", "writefreely/writefreely", "beromir/Servas", "julianpoy/RecipeSage",
                 "itskovacs/trip"):
        web[RAW + f"{full}/HEAD/README.md"] = (200, f"# {full}\nRun it yourself.\n{RUNS}")
    return FakePorts(tmp_path, web=web)


def test_a_blog_engine_is_never_a_shopping_list_candidate(tmp_path):
    f = find.Finder(_shop_ports(tmp_path), tmp_path / "c")
    job = {"n": 0, **SHOP, "pasted": []}
    plan = f.plan_job(job, [["shopping", "list"], ["grocery", "list"]], [], {})
    names = [c["name"] for c in plan["candidates"]]
    assert names == ["mern_shopping_list", "RecipeSage"], names
    why = {n["name"]: n["why"] for n in plan["not_used"]}
    assert why["healthchecks"] == 'its name, description, topics and README never mention "shopping" or "grocery"', why
    # "list" no longer matches inside "minimalist": the blog engines never even came up; a list row must name
    # "shopping" or "grocery" too (lane I18b), so a bookmark manager's "lists of links" never comes up either
    assert "Mataroa" not in why and "WriteFreely" not in why and "TRIP" not in why and "Servas" not in why


def test_nothing_on_subject_says_nothing_found_and_why(tmp_path):
    ports = _shop_ports(tmp_path)
    ports.web[SEARCH] = (200, json.dumps({"items": [SEARCH_ITEMS["items"][1]]}))
    ports.web[RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md"] = (200, "# none\n")
    f = find.Finder(ports, tmp_path / "c")
    job = {"n": 0, **SHOP, "pasted": []}
    plan = f.plan_job(job, [["shopping", "list"]], [], {})
    assert plan["candidates"] == []
    assert plan["notes"] == ['1 project came up in the search, but it is not about "shopping"']
    line = find.nothing_line({**job, "plan": plan})
    assert line.startswith('Nothing found yet: 1 project came up in the search, but it is not about "shopping"')


def test_subject_words_leave_out_generic_ones():
    assert find.subject_of({"name": "Dinner Planner", "does": "plan dinners for the week"}) == {find.stem("dinner")}
    assert find.subject_of({"name": "Planner", "does": ""}) == {"plan"}                  # only generic: kept
    assert not find.names_a_word({"name": "x", "description": "for minimalists"}, ["list"])
    assert find.names_a_word({"name": "x", "description": "keeps lists"}, ["list"])


# -- 3. a pasted GitHub address: that project only -----------------------------------------------------
FLASK = "https://github.com/techwithtim/Flask-Web-App-Tutorial"
FLASK_README = "# Flask Web App\n## Run\n```bash\npip install -r requirements.txt\npython main.py\n```\n"


def test_a_pasted_address_is_tried_alone_and_nothing_is_searched(tmp_path):
    ports = _shop_ports(tmp_path)
    ports.web[RAW + "techwithtim/Flask-Web-App-Tutorial/HEAD/README.md"] = (200, FLASK_README)
    seen = []
    real = ports.http_send

    def spy(method, url, **kw):
        seen.append(url)
        return real(method, url, **kw)
    ports.http_send = spy
    f = find.Finder(ports, tmp_path / "c")
    job = {"n": 0, "name": "Flask-Web-App-Tutorial", "does": "add github.com/techwithtim/Flask-Web-App-Tutorial",
           "sample_ask": "", "pasted": [FLASK]}
    plan = f.plan_job(job, [["shopping", "list"]], [], {})
    assert [c["ref"] for c in plan["candidates"]] == ["github:techwithtim/Flask-Web-App-Tutorial"]
    assert not any("api.github.com/search" in u or "awesome" in u for u in seen), seen


def test_chat_paste_plan_says_only_and_no_more_fallbacks(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.web[RAW + "techwithtim/Flask-Web-App-Tutorial/HEAD/README.md"] = (200, FLASK_README)
    w.ports.brain_picks = BrainDouble()
    try:
        core = types.SimpleNamespace(consent=ChatConsent(w.ports, "no"), notices=Notices())
        cards = []
        wiring.chat_toolsmith(core, w.app, wait_s=1)(f"add {FLASK[8:]}", {"notify": cards.append})
        lines = cards[0]["card"]["lines"]
        assert lines == ["Flask-Web-App-Tutorial: try Flask-Web-App-Tutorial only, the project you gave (Tries as "
                         "a program on this computer, as you. It could read your files and change PuffPAI). If it "
                         "does not work, Toolsmith says why and stops."]
        assert kmod.NO_TOPICS_LINE not in lines        # D84: a pasted plan, even with the topic ask unanswered
        assert "more if it does not work" not in json.dumps(cards)
        assert w.host.store.all()[-1]["envelope"]["jobs"][0]["candidates"] == [
            "github:techwithtim/Flask-Web-App-Tutorial"]
    finally:
        w.host.stop(timeout=10)


# -- 5. the port the app really uses ----------------------------------------------------------------
@pytest.mark.parametrize("log, want", [
    (" * Running on http://127.0.0.1:5000\nPress CTRL+C to quit", [("127.0.0.1", 5000)]),
    ("Server listening on port 3000", [("", 3000)]),
    ("  VITE v5  ready\n  ➜  Local:   http://localhost:5173/\n", [("127.0.0.1", 5173)]),
    ("INFO: Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)", [("0.0.0.0", 8000)]),
    (" * Running on all addresses (0.0.0.0)\n * Running on http://127.0.0.1:5000\n * Running on "
     "http://192.168.1.20:5000", [("127.0.0.1", 5000), ("192.168.1.20", 5000)]),
    ("Listening on [::]:8080", [("::", 8080)]),
    ("Downloaded 3 packages in 2s", []),
])
def test_server_logs_say_where_they_listen(log, want):
    assert runbook.log_listens(log) == want


def test_port_ways_names_its_own_settings():
    assert runbook.port_ways("Run `python app.py --port 8000` or `-p 9000`.", 18431) == \
        "PORT=18431 (it already gets it), --port 18431, -p 18431"


class PortLink:
    """A Core link with a recorded listen report and pages (no server)."""

    def __init__(self, listens, pages):
        self.listens, self.pages, self.said = listens, pages, []

    def try_open(self, job_id, ref):
        return {"try_id": "t1", "port": 18431}

    def try_log(self, try_id):
        return {"log": " * Running on http://127.0.0.1:5000", "alive": 1, "listens": self.listens}

    def read_page(self, url):
        return self.pages.get(url, {"ok": False, "said": "Connection refused."})


class Said(tries.Hooks):
    def __init__(self):
        self.lines = []

    def say(self, line):
        self.lines.append(line)


def _try(state, link):
    st = settings_mod.read(state)
    st["timing"]["page_wait_s"] = 1
    st["timing"]["poll_s"] = 0.01
    hooks = Said()
    t = tries.Try({"id": "j1", "name": "Flask app"}, {"ref": "github:x/flask", "name": "Flask app",
                                                      "url": "https://github.com/x/flask"},
                  link=link, settings=st, hooks=hooks)
    t.folder, t.readme = Path(state), "python main.py"
    return t, hooks


PAGE = {"ok": True, "title": "Home", "text": "Login Sign up for an account today please"}


def test_toolsmith_reads_the_page_on_the_port_its_own_server_uses(state):
    link = PortLink({"port": 18431, "own": [["127.0.0.1", 5000]], "app_port": 5000, "exposed": [], "taken": [],
                     "said": "Flask app listens on http://127.0.0.1:5000, not on its port 18431."},
                    {"http://127.0.0.1:5000/": PAGE})
    t, hooks = _try(state, link)
    got = t.t_wait_for_page({"url": "http://127.0.0.1:18431/"})
    assert got["ok"] and "Its own server listens on http://127.0.0.1:5000/, not on your port 18431" in got["said"]
    assert "PORT=18431 (it already gets it), --port 18431" in got["said"]
    assert any("listens on port 5000" in ln for ln in hooks.lines)
    got = t.t_done({"url": "http://127.0.0.1:5000/"})
    assert got["ok"] and t.result["port"] == 5000
    assert any("fixed port 5000" in n for n in t.notes)


def test_a_page_another_program_serves_never_counts(state):
    link = PortLink({"port": 18431, "own": [], "app_port": None, "exposed": [], "taken": [5000],
                     "said": "Its log says it uses port 5000, but another program on this computer already holds "
                             "that port, so Flask app could not use it. Start it on its port 18431 instead."},
                    {"http://127.0.0.1:5000/": PAGE})
    t, hooks = _try(state, link)
    got = t.t_wait_for_page({"url": "http://127.0.0.1:18431/"})
    assert not got["ok"] and "another program on this computer already holds that port" in got["said"]
    got = t.t_done({"url": "http://127.0.0.1:5000/"})
    assert not got["ok"] and "No program of this try listens on port 5000" in got["said"]
    link.listens = dict(link.listens, own=None)                     # the listen table can't be read
    assert "can't see which program listens" in t.t_done({"url": "http://127.0.0.1:5000/"})["said"]


def test_a_server_on_all_networks_is_stopped_and_said(tmp_path, state, monkeypatch):
    """The listen table says one of the try's own servers listens on 0.0.0.0: the Core stops that server."""
    from puffpai_core.steward import ports as sp
    w, b, link, try_id, rec = _real_try(tmp_path, state)
    try:
        got = script(link, try_id, "idle.py", "import time\nprint('Listening on 0.0.0.0:18999')\ntime.sleep(60)\n",
                     start=True)
        assert got["ok"], got
        pid = int(got["pid"])
        assert wait_for(lambda: rec.broker.alive(), timeout_s=10)
        monkeypatch.setattr(sp, "job_listeners", lambda pids: [("0.0.0.0", 18999)] if pid in pids else [])
        rep = link.try_log(try_id)["listens"]
        assert rep["exposed"] == [["0.0.0.0", 18999]] and "so the Core stopped that server" in rep["said"]
        assert wait_for(lambda: not rec.broker.alive(), timeout_s=10)
    finally:
        w.host.stop(timeout=10)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _real_try(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.fixture_repo = FIXTURES / "toolsmith" / "hello-page"
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = w.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    assert link.try_download(opened["try_id"])["ok"] and link.try_check(opened["try_id"])["ok"]
    make_venv(link, opened["try_id"])
    return w, b, link, opened["try_id"], w.host.trials.get(b["id"], opened["try_id"])


SERVER = """import http.server, sys
port = int(sys.argv[1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<title>Tiny</title><p>Hello from the tiny app on its own fixed port</p>"
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
srv = http.server.HTTPServer(("127.0.0.1", port), H)
print(f" * Running on http://127.0.0.1:{port}", flush=True)
srv.serve_forever()
"""


@pytest.mark.skipif(sys.platform != "win32", reason="the try's job object and listen table are read on Windows")
def test_a_real_server_on_its_own_fixed_port_is_found(tmp_path, state):
    w, b, link, try_id, rec = _real_try(tmp_path, state)
    try:
        port = _free_port()
        assert port not in (8420, 8421, 8422, rec.port)
        got = script(link, try_id, "tiny.py", SERVER, start=True, args=[str(port)])
        assert got["ok"], got
        rep = wait_for(lambda: (link.try_log(try_id)["listens"] or {}).get("app_port") and
                       link.try_log(try_id)["listens"], timeout_s=20)
        assert rep["app_port"] == port and ["127.0.0.1", port] in rep["own"] and rep["taken"] == []
        st = settings_mod.read(state)
        t = tries.Try({"id": b["jobs"][0]["id"], "name": "Hello"}, dict(b["jobs"][0]["plan"]["candidates"][0]),
                      link=link, settings=st, hooks=Said())
        t.try_id, t.port, t.folder = try_id, rec.port, rec.folder       # the same open try
        got = t.t_wait_for_page({"url": f"http://127.0.0.1:{rec.port}/"})
        assert got["ok"] and f"listens on http://127.0.0.1:{port}/" in got["said"], got
        assert t.t_done({"url": f"http://127.0.0.1:{port}/"})["ok"]
    finally:
        w.host.stop(timeout=10)                       # the Core stops the try's server by its job object


@pytest.mark.skipif(sys.platform != "win32", reason="the try's job object and listen table are read on Windows")
def test_a_port_another_program_holds_is_said_plainly(tmp_path, state):
    port = _free_port()
    held = tmp_path / "holder.py"
    held.write_text(SERVER, encoding="utf-8")
    # A stray program of this TEST holds the port; the test stops it by its own handle (its own pid).
    other = subprocess.Popen([sys.executable, "-I", str(held), str(port)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, cwd=str(tmp_path))
    w = None
    try:
        from puffpai_core.steward import ports as sp
        assert wait_for(lambda: sp.port_in_use(port), timeout_s=15)
        w, b, link, try_id, rec = _real_try(tmp_path, state)
        code = f"import time\nprint(' * Running on http://127.0.0.1:{port}', flush=True)\ntime.sleep(60)\n"
        assert script(link, try_id, "says.py", code, start=True)["ok"]
        rep = wait_for(lambda: link.try_log(try_id)["listens"].get("taken") and link.try_log(try_id)["listens"],
                       timeout_s=20)
        assert rep["taken"] == [port] and rep["app_port"] is None
        assert f"another program on this computer already holds that port" in rep["said"]
    finally:
        if w is not None:
            w.host.stop(timeout=10)
        other.terminate()
        try:
            other.wait(timeout=10)
        except subprocess.TimeoutExpired:
            other.kill()
            other.wait(timeout=10)


# -- 6. the chat result says why, one line per candidate --------------------------------------------
def test_reasons_never_say_a_name_twice():
    assert agent.why_of("superset", "superset: You said No to trying it, so Toolsmith moves on.") == \
        "You said No to trying it, so Toolsmith moves on."
    assert agent.tried_line([{"name": "Flask-Web-App-Tutorial", "why": "its server listens on port 5000."},
                             {"name": "superset", "why": "superset: you said No to trying it"}]) == \
        "Flask-Web-App-Tutorial: its server listens on port 5000. superset: you said No to trying it."


def test_the_chat_result_names_each_candidate_tried_with_its_reason():
    jobs = [{"name": "Flask-Web-App-Tutorial", "status": "not_yet",
             "run": {"line": "No app worked yet.", "tried": [
                 {"name": "Flask-Web-App-Tutorial", "ref": "github:techwithtim/Flask-Web-App-Tutorial",
                  "why": "Flask-Web-App-Tutorial: it did not reach a working page within 12 minutes"}]}},
            {"name": "Pantry", "status": "working", "run": {"line": "FreshTable is already on your bot."}}]
    assert wiring.chat_result_lines(jobs) == [
        "Flask-Web-App-Tutorial: not working yet. Toolsmith tried 1 app:",
        "Flask-Web-App-Tutorial: it did not reach a working page within 12 minutes.",
        "Pantry: works. FreshTable is already on your bot."]


# -- 4 (the wizard's side): ONE hold card per job, she ticks which ones -----------------------------
HOLD_CARD = {"consent_id": "c_h", "title": "Let Toolsmith try these 2 apps for Shopping? Drawbridge couldn't check "
             "some of their files", "lines": ["1. RecipeSage ...", "2. Grocy ..."], "floor": False,
             "choices": [{"ref": "github:a/recipesage", "name": "RecipeSage"}, {"ref": "github:b/grocy", "name": "Grocy"}],
             "yes_means": "Your Yes lets Toolsmith try the ones you tick, one at a time. Nothing from them runs "
                          "before that."}


@pytest.mark.parametrize("typed, want", [("2", (True, ["github:b/grocy"])), ("1 2", (True, ["github:a/recipesage",
                                          "github:b/grocy"])), ("all", (True, ["github:a/recipesage", "github:b/grocy"])),
                                          ("none", (False, [])), ("", (False, [])), ("yes", (True, []))])
def test_the_build_step_asks_which_set_aside_apps_to_try(state, typed, want):
    kit = Kit(state)
    eng = kit.engine()
    eng.set_build("b1")
    answers = []
    kit.toolsmith.progress = lambda build: {"jobs": [], "steps": [], "cards": [dict(HOLD_CARD)]}
    kit.toolsmith.answer_card = lambda build, cid, yes, picks=None: answers.append((cid, yes, picks)) or ""
    kit.toolsmith.answer_step = lambda *a: ""
    screen = eng.submit("build", {})
    field = screen["field"]
    assert field["type"] == "text" and field["label"] == "Which ones may Toolsmith try?"
    assert "Your Yes lets Toolsmith try the ones you tick" in field["help"]
    assert "got this app working" not in json.dumps(screen)
    screen = eng.submit("build", {"card": typed})
    assert answers == [("c_h", want[0], want[1])]
    said = "You said Yes but ticked none, so Toolsmith tries none of them."
    assert (said in screen["lines"]) == (typed == "yes")


def test_a_hold_card_for_one_app_says_what_a_yes_does_now(state):
    kit = Kit(state)
    eng = kit.engine()
    eng.set_build("b1")
    one = dict(HOLD_CARD, choices=HOLD_CARD["choices"][:1],
               yes_means="Your Yes lets Toolsmith try it. Nothing from it runs before that.")
    kit.toolsmith.progress = lambda build: {"jobs": [], "steps": [], "cards": [one]}
    answers = []
    kit.toolsmith.answer_card = lambda build, cid, yes, picks=None: answers.append((cid, yes, picks)) or ""
    kit.toolsmith.answer_step = lambda *a: ""
    field = eng.submit("build", {})["field"]
    assert field["type"] == "confirm" and field["help"] == ("Yes tries RecipeSage. Your Yes lets Toolsmith try it. "
                                                            "Nothing from it runs before that.")
    eng.submit("build", {"card": "yes"})                  # its Yes field names that one app: the Yes ticks it
    assert answers == [("c_h", True, ["github:a/recipesage"])]


def test_an_unreadable_pick_is_asked_again():
    from puffpai_core.wizard.engine import pick_answer
    assert pick_answer("3", HOLD_CARD["choices"]) == (None, [])
    assert pick_answer("two", HOLD_CARD["choices"]) == (None, [])


@pytest.mark.parametrize("log, want", [
    ("Address already in use\nPort 5000 is in use by another program. Either identify and stop that program", [5000]),
    ("Error: listen EADDRINUSE: address already in use 127.0.0.1:3000", [3000]),
    ("OSError: [Errno 10048] Only one usage of each socket address (protocol/network address/port) is normally "
     "permitted: ('127.0.0.1', 8000)", [8000]),
    (" * Running on http://127.0.0.1:5000", []),
])
def test_a_log_that_could_not_take_its_port_is_read(log, want):
    assert runbook.log_in_use(log) == want


def test_generic_verbs_are_not_a_subject():
    got = find.subject_of({"name": "Shopping List Manager", "does": "Maintains a list of items to buy"})
    assert got == {find.stem("shopping"), "buy"}


def test_the_plan_keeps_why_nothing_matched(tmp_path):
    """agent.plan adds its own notes to the finder's (the live run lost "none is about ...")."""
    ports = _shop_ports(tmp_path)
    ports.web[SEARCH] = (200, json.dumps({"items": [SEARCH_ITEMS["items"][1]]}))
    ports.web[RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md"] = (200, "# none\n")
    sent = {}

    class Link:
        def __init__(self):
            self.http_get = ports.http_get
            self.screen_queries = ports.screen_queries     # the Core's search screen (lane I15)

        def build(self):
            return {"build": {"goal": "a shopping list", "jobs_in": [SHOP]}}

        def brain_json(self, system, user):
            return None

        def registry(self):
            return {"rows": [], "proven": {}}

        def put_plan(self, plan):
            sent.update(plan)
    agent.plan(Link(), tmp_path / "c")
    notes = sent["jobs"][0]["plan"]["notes"]
    assert notes and "but it is not about" in notes[0], notes


# -- the subject check keeps WorldMonitor (v1 lost it to filters, 03 3.2; the owner's known-good adoption) ----
WM = Path(__file__).parent / "data" / "worldmonitor"            # a hand-made fixture in GitHub's shape (SOURCE.md there)
WM_README = (WM / "README.fixture.md").read_text(encoding="utf-8")


def _wm_ports(tmp_path):
    """Fixture GitHub: each search answers with its own fixture result; every README the finder reads is
    WorldMonitor's fixture one for it, and a plain run-step README for the others (so the README check passes
    and only the subject check decides)."""
    searches = {"world+news+map": "search_world_news_map.json", "world+news": "search_world_news.json",
                "news+map": "search_news_map.json"}

    def search(url):
        q = url.split("?q=", 1)[1].split("+in:", 1)[0]
        name = searches.get(q)
        return (200, (WM / name).read_text(encoding="utf-8")) if name else (200, '{"items": []}')

    def readme(url):
        if "/koala73/worldmonitor/" in url:
            return 200, WM_README
        return 200, "# A project\n" + RUNS
    return FakePorts(tmp_path, web={SEARCH: search, RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md":
                                    (200, "# none\n"), RAW + "*": readme})


@pytest.mark.parametrize("job, phrases", [
    ({"name": "World news map", "does": "a map of world news"}, [["world", "news", "map"], ["world", "news"]]),
    ({"name": "World events map", "does": "show me what's happening in the world on a map"},
     [["world", "news", "map"], ["world", "news"]]),
    ({"name": "World news map", "does": "a map of world news"}, [["world", "news"]]),     # no brain: the job's name
])
def test_worldmonitor_stays_a_candidate_and_comes_first(tmp_path, job, phrases):
    f = find.Finder(_wm_ports(tmp_path), tmp_path / "c")
    plan = f.plan_job({"n": 0, "sample_ask": "", "pasted": [], **job}, phrases, [], {})
    refs = [c["ref"] for c in plan["candidates"]]
    assert refs and refs[0] == "github:koala73/worldmonitor", (refs, plan["not_used"])
    assert not any(n["ref"] == "github:koala73/worldmonitor" for n in plan["not_used"])


def test_the_world_news_map_plan_drops_stuffed_descriptions(tmp_path):
    """The whole plan for "a map of world news": the four results with descriptions over 1000 characters are
    left out before their README is read, and the apps behind WorldMonitor follow in score order."""
    f = find.Finder(_wm_ports(tmp_path), tmp_path / "c")
    job = {"n": 0, "sample_ask": "", "pasted": [], "name": "World news map", "does": "a map of world news"}
    plan = f.plan_job(job, [["world", "news", "map"], ["world", "news"]], [], {})
    # Lane I15: scored before the brain sees them (no brain here to rank): PolyWorld (a world map dashboard) and
    # osint-worldview (news and maps) match "world news map" better than the voice assistant J.A.R.V.I.S, which
    # only has more stars.
    assert [c["ref"] for c in plan["candidates"]] == [
        "github:koala73/worldmonitor", "github:AmazingAng/PolyWorld", "github:samuelclay/NewsBlur",
        "github:marc-shade/world-intel-mcp", "github:amanimran786/osint-worldview"]
    stuffed = [n["ref"] for n in plan["not_used"] if n["why"] == "a description stuffed with words"]
    assert stuffed == ["github:cirosantilli/china-dictatorship", "github:cirosantilli/china-dictatroship-7",
                       "github:mRFWq7LwNPZjaVv5v6eo/cihna-dictattorshrip-8", "github:bermufine/dcmp"]


def test_github_itself_misses_worldmonitor_for_a_phrase_with_map(tmp_path):
    """Why the phrases matter (modeled on GitHub's results): GitHub needs every word, and its description and topics never say map."""
    names = [i["full_name"] for i in json.loads((WM / "search_world_news_map.json").read_text(encoding="utf-8"))["items"]]
    assert "koala73/worldmonitor" not in names
    names = [i["full_name"] for i in json.loads((WM / "search_world_news.json").read_text(encoding="utf-8"))["items"]]
    assert names[0] == "koala73/worldmonitor"


def test_a_pasted_worldmonitor_is_tried_alone_with_no_subject_check(tmp_path):
    ports = _wm_ports(tmp_path)
    f = find.Finder(ports, tmp_path / "c")
    job = {"n": 0, "name": "Garden helper", "does": "tell me when to water the tomatoes", "sample_ask": "",
           "pasted": ["https://github.com/koala73/worldmonitor"]}
    plan = f.plan_job(job, [["garden", "watering"]], [], {})        # a job its words never match
    assert [c["ref"] for c in plan["candidates"]] == ["github:koala73/worldmonitor"]
    assert plan["candidates"][0]["source"] == "pasted" and plan["not_used"] == []


def test_the_brains_phrases_carry_synonyms(tmp_path):
    """"meal planner recipes" keeps a recipe app for "Dinner Planner"; without the phrases it is dropped."""
    item = {"full_name": "example/helium", "name": "Helium", "description": "A recipe manager you host yourself",
            "stargazers_count": 300, "language": "Python", "topics": ["recipes"], "default_branch": "main"}
    web = {SEARCH: (200, json.dumps({"items": [item]})),
           RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md": (200, "# none\n"),
           RAW + "example/helium/main/README.md": (200, "# Helium\nKeep your recipes.\n" + RUNS)}
    job = {"n": 0, "name": "Dinner Planner", "does": "plan dinners for the week", "sample_ask": "", "pasted": []}
    plan = find.Finder(FakePorts(tmp_path, web=web), tmp_path / "a").plan_job(job, [["meal", "planner"], ["recipes"]],
                                                                             [], {})
    assert [c["name"] for c in plan["candidates"]] == ["Helium"]
    plan = find.Finder(FakePorts(tmp_path, web=web), tmp_path / "b").plan_job(job, [["dinner"]], [], {})
    assert plan["candidates"] == []
    assert plan["not_used"][-1] == {"name": "Helium", "ref": "github:example/helium",
                                    "why": 'its name, description, topics and README never mention "dinner"'}


def test_every_dropped_reason_is_true(tmp_path):
    """03 3.2 rule 5: for each project the subject check left out, none of the words its line names appears
    (as a root) in the name, description, topics or README that were read; and it names exactly the words
    the check looked for (the job's name, what it does, and the brain's phrases, generic words left out)."""
    import re
    ports = _shop_ports(tmp_path)
    f = find.Finder(ports, tmp_path / "c")
    job = {"n": 0, **SHOP, "pasted": []}
    phrases = [["shopping", "list"], ["grocery", "list"]]
    plan = f.plan_job(job, phrases, [], {})
    dropped = [n for n in plan["not_used"] if n["why"].startswith("its name, description, topics and README")]
    assert {n["name"] for n in dropped} == {"healthchecks"}, plan["not_used"]
    rows = {i["full_name"]: i for i in SEARCH_ITEMS["items"]}
    rows["beromir/Servas"] = {"full_name": "beromir/Servas", "name": "Servas",
                              "description": "A self-hosted bookmark management tool. Organize lists of links."}
    for n in dropped:
        named = {find.stem(w) for w in re.findall(r'"([^"]+)"', n["why"])}
        assert named == find.subject_of(job, phrases)
        full = n["ref"].split(":", 1)[1]
        readme = ports.web[next(k for k in ports.web if full in k)][1]
        assert not named & find._word_stems(find._item_text(rows[full]) + " " + readme), n


# -- the verifier's round on d2d79530 ------------------------------------------------------------------
from test_i3b_bridge_cards import held_core  # noqa: E402,F401  (the real Core with Drawbridge holds)


def test_a_tick_after_her_yes_is_refused_and_start_reads_only_the_fixed_copy(offer_world):
    w = offer_world
    b = _offer_plan(w)
    cid = b["consent"]["id"]
    w.host.use_offers(b["id"], cid, [0])                      # while the card waits: kept
    w.ports.consent.say_yes(cid)                               # her Yes: the Core fixes the ticks now
    assert w.host.store.read(b["id"])["use_fixed"]["jobs"] == {b["jobs"][0]["id"]: "freshtable"}
    with pytest.raises(PermissionError):
        w.host.use_offers(b["id"], cid, [])                     # granted, not started: refused all the same
    got = w.host.start(b["id"], cid)
    assert got["jobs"][0]["plan"]["reuse"] == "freshtable"
    # A tick written into the build after the grant (not through set_use) never counts.
    b2 = _offer_plan(w)
    w.ports.consent.say_yes(b2["consent"]["id"])
    w.host.store.change(b2["id"], lambda x: x.update(use={"consent_id": b2["consent"]["id"],
                                                          "jobs": {b2["jobs"][0]["id"]: "freshtable"}}))
    got = w.host.start(b2["id"], b2["consent"]["id"])
    assert got["jobs"][0]["plan"]["reuse"] is None and got["yes"]["use"] == {}


CANDS2 = [{"name": "news-board", "full_name": "example/news-board", "runtime": "node", "source": "github"},
          {"name": "hello-page", "full_name": "example/hello-page", "runtime": "node", "source": "github"}]


def _held_build(held_core):
    from toolsmith_fakes import Link, flask_transport
    app, core, host, ports = held_core
    st = host.settings()
    job = {"name": "Daily headlines", "does": "Shows news headlines", "sample_ask": "What are the headlines?"}
    b = host.store.create("daily news", [job], st)
    host.store.set_plan(b["id"], {"jobs": [{**job, "plan": {"candidates": CANDS2}}]}, st, "")

    def yes(x):
        x["yes"] = {"given": True, "consent_id": "c_test", "sha": x["plan_sha"]}
    b = host.store.change(b["id"], yes)
    return b, Link(flask_transport(app, core.tokens.issue_toolsmith(b["id"])), b["id"])


def _held_try(link, jid, ref):
    o = link.try_open(jid, ref)
    assert link.try_download(o["try_id"])["ok"]
    got = link.try_check(o["try_id"])
    link.try_end(o["try_id"])
    return got


def test_one_hold_card_per_job_and_only_after_every_fully_checked_one_ended(held_core, state):
    app, core, host, ports = held_core
    ports.held = {"example/news-board"}
    b, link = _held_build(held_core)
    jid = b["jobs"][0]["id"]
    assert _held_try(link, jid, "github:example/news-board").get("held")
    early = link.holds_ask(jid)                                 # hello-page (fully checked) not tried yet
    assert early["consent_id"] == "" and early.get("not_yet")
    assert _held_try(link, jid, "github:example/hello-page")["ok"]
    card = link.holds_ask(jid)
    assert card["consent_id"] and link.holds_ask(jid)["consent_id"] == card["consent_id"]   # the same one
    core.consent.answer(card["consent_id"], "no", by="owner")
    assert link.holds_state(jid)["try"] == []
    again = _held_try(link, jid, "github:example/news-board")  # downloaded again: refused, never a new card
    assert not again.get("ok") and not again.get("held") and "one card" in again["said"]
    assert link.holds_ask(jid)["consent_id"] == ""
    cards = [r for r in core.consent.list() if isinstance((r.get("card") or {}).get("try_holds"), list)]
    assert [r["status"] for r in cards] == ["declined"]


def test_an_unticked_or_refused_held_app_stays_skipped(held_core, state):
    app, core, host, ports = held_core
    b, link = _held_build(held_core)                            # both held
    jid = b["jobs"][0]["id"]
    for ref in ("github:example/news-board", "github:example/hello-page"):
        assert _held_try(link, jid, ref).get("held")
    card = link.holds_ask(jid)
    host.trials.tick_holds(b["id"], card["consent_id"], ["github:example/news-board"])
    rec = core.consent.get(card["consent_id"])
    core.consent.answer(card["consent_id"], "yes", card_sha=rec["card_sha"], by="owner")
    assert link.holds_state(jid)["try"] == ["github:example/news-board"]
    assert not _held_try(link, jid, "github:example/hello-page").get("ok")       # not ticked
    real = ports.fetch_repo

    def changed(url, dest):
        real(url, dest)
        (Path(dest) / "extra.js").write_text("console.log(1)\n", encoding="utf-8")
        return ""
    ports.fetch_repo = changed
    got = _held_try(link, jid, "github:example/news-board")
    assert not got.get("ok") and got["said"] == "It changed after Drawbridge's check, so it was not used."
    ports.fetch_repo = real
    assert not _held_try(link, jid, "github:example/news-board").get("ok")       # refused once: stays skipped


def test_a_failed_answer_keeps_no_ticks(held_core, state, monkeypatch):
    from puffpai_core.consent import ConsentError
    from puffpai_core.wiring import WizardToolsmith
    app, core, host, ports = held_core
    b, link = _held_build(held_core)
    jid = b["jobs"][0]["id"]
    for ref in ("github:example/news-board", "github:example/hello-page"):
        _held_try(link, jid, ref)
    card = link.holds_ask(jid)

    def broken(*a, **k):
        raise ConsentError(409, "That card changed after you saw it. Look at it again.", "card_changed")
    monkeypatch.setattr(core.consent, "answer", broken)
    said = WizardToolsmith(app).answer_card(b["id"], card["consent_id"], True, ["github:example/hello-page"])
    assert "changed" in said
    assert not (host.store.read(b["id"]).get("hold_ticks") or {}).get(card["consent_id"])


def test_a_one_app_card_says_who_a_yes_tries(held_core, state):
    from puffpai_core.consent import card_view
    app, core, host, ports = held_core
    ports.held = {"example/news-board"}
    b, link = _held_build(held_core)
    jid = b["jobs"][0]["id"]
    _held_try(link, jid, "github:example/news-board")
    _held_try(link, jid, "github:example/hello-page")
    card = core.consent.get(link.holds_ask(jid)["consent_id"])
    assert "Yes tries news-board." in " ".join(card_view(card["card"], False)["lines"])


def test_a_name_said_again_in_a_reason_is_dropped():
    assert agent.why_of("news-board", "news-board changed after Drawbridge's check, so it was not used.") == \
        "it changed after Drawbridge's check, so it was not used."
    assert agent.tried_line([{"name": "news-board", "why": "news-board: news-board changed after it"}]) == \
        "news-board: it changed after it."


@pytest.mark.skipif(sys.platform != "win32", reason="the try's job object and listen table are read on Windows")
def test_a_stray_page_on_the_tries_own_port_never_counts(tmp_path, state):
    w, b, link, try_id, rec = _real_try(tmp_path, state)
    held = tmp_path / "stray.py"
    held.write_text(SERVER, encoding="utf-8")
    # A stray program of this TEST takes the try's own port; the test stops it by its own handle.
    other = subprocess.Popen([sys.executable, "-I", str(held), str(rec.port)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, cwd=str(tmp_path))
    try:
        from puffpai_core.steward import ports as sp
        assert wait_for(lambda: sp.port_in_use(rec.port), timeout_s=15)
        assert script(link, try_id, "idle.py", "import time\nprint(1, flush=True)\ntime.sleep(60)\n", start=True)["ok"]
        t = tries.Try({"id": b["jobs"][0]["id"], "name": "Hello"}, dict(b["jobs"][0]["plan"]["candidates"][0]),
                      link=link, settings=settings_mod.read(state), hooks=Said())
        t.try_id, t.port, t.folder = try_id, rec.port, rec.folder
        got = t.t_done({"url": f"http://127.0.0.1:{rec.port}/"})
        assert not got["ok"] and "No program of this try listens on port" in got["said"] and t.result is None
    finally:
        w.host.stop(timeout=10)
        other.terminate()
        try:
            other.wait(timeout=10)
        except subprocess.TimeoutExpired:
            other.kill()
            other.wait(timeout=10)


def test_an_expired_hold_card_skips_them_all_for_the_build(held_core, state):
    app, core, host, ports = held_core
    b, link = _held_build(held_core)
    jid = b["jobs"][0]["id"]
    for ref in ("github:example/news-board", "github:example/hello-page"):
        _held_try(link, jid, ref)
    card = link.holds_ask(jid)
    host.trials.tick_holds(b["id"], card["consent_id"], ["github:example/news-board"])
    core.consent.expire(card["consent_id"], "no answer in time")
    st = link.holds_state(jid)
    assert st["try"] == [] and {x["why"] for x in st["skipped"]} == {"its card got no answer in time"}
    assert not _held_try(link, jid, "github:example/news-board").get("ok")
    assert link.holds_ask(jid)["consent_id"] == ""


def test_a_stopped_wide_server_stays_on_the_report(tmp_path, state, monkeypatch):
    from puffpai_core.steward import ports as sp
    w, b, link, try_id, rec = _real_try(tmp_path, state)
    try:
        got = script(link, try_id, "idle.py", "import time\ntime.sleep(60)\n", start=True)
        pid = int(got["pid"])
        monkeypatch.setattr(sp, "job_listeners", lambda pids: [("0.0.0.0", 18999)] if pid in pids else [])
        assert link.try_log(try_id)["listens"]["exposed"] == [["0.0.0.0", 18999]]
        assert wait_for(lambda: not rec.broker.alive(), timeout_s=10)
        again = link.try_log(try_id)["listens"]                   # read again after the stop: still said
        assert again["exposed"] == [["0.0.0.0", 18999]] and "stopped that server" in again["said"]
    finally:
        w.host.stop(timeout=10)
