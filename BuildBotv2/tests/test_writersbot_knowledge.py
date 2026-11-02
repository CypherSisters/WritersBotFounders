"""WritersBot: the books a bot came with come back with one Yes, and Find books says what it is doing.

The list travels by topic (``knowledge.topics``), Recommended shows it, and it asks one plan card that reads only Wikipedia
and Gutenberg. Find books names the places it asks while a plan is made, what each found and which did not answer, why it
can't look, and a plan of books never reads as an app build. The page's states run in real Edge on a real Core, with the
answers recorded. The real run against the real places is not a test here: it needs the internet.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from puffpai_core import bot_move, jsonfile
from puffpai_core.api.app import create_app
from puffpai_core.registry import Registry
from puffpai_core.zone3.toolsmith import agent
from puffpai_core.zone3.toolsmith_host import knowledge as kmod
from puffpai_core.zone3.toolsmith_host.store import Builds
from test_bot_move import Packages, _bot, make
from test_sources_knowledge import Ports as CatalogPorts

SIX = ["World building", "Character archetypes", "Plot and subplots", "Science fiction feasibility", "Manuscript editing", "Word processing"]
BID = "20261101-120000-abcdef"
WEB = Path(__file__).resolve().parents[1] / "packages" / "desktop" / "web"
NODE = shutil.which("node")


def _read_build(state, bid=BID, topics=SIX, count=3, **more):
    """A build on the bot's disk that read *count* items on *topics*."""
    folder = state / "builds" / bid
    folder.mkdir(parents=True, exist_ok=True)
    jsonfile.write(folder / "build.json", {"id": bid, "goal": "a writer's bot", "created_at": "2026-10-31T10:00:00", "jobs": [], "yes": {"given": True},
                                           "knowledge_plan": {"topics": topics, "fetch": [{"source": "wikipedia", "name": "Wikipedia"}]},
                                           "knowledge_run": {"state": "done", "counts": {"wikipedia": count}, "planned": {"wikipedia": count}, "line": f"Read {count} Wikipedia articles."}, **more})


# -- the topics travel -----------------------------------------------------------------------------------------------

def test_topics_survive_export_read_and_a_second_export(state, tmp_path):
    make(state)
    _bot(state)
    _read_build(state)
    _read_build(state, "20261101-120100-abcdef", ["nothing here"], count=0)
    out = tmp_path / "bundle"
    bot_move.export(state, Registry(state / "registry.db"), Packages(), out)
    record = bot_move.read(out)
    assert record["knowledge"]["topics"] == SIX                            # the one that read nothing stays behind
    assert bot_move.lines_for(record)["topics"] == SIX
    there = tmp_path / "there" / "state"
    make(there)
    bot_move.apply(there, record)
    assert bot_move.reading_topics(there) == SIX
    # a bot that came with them and never fetched them: exported again, they are not lost
    again = tmp_path / "again"
    bot_move.export(there, Registry(there / "registry.db"), Packages(), again)
    assert bot_move.read(again)["knowledge"]["topics"] == SIX


def test_a_bundle_with_no_topics_still_works(state, tmp_path):
    make(state)
    _bot(state)
    out = tmp_path / "bundle"
    bot_move.export(state, Registry(state / "registry.db"), Packages(), out)
    main = out / bot_move.MAIN
    rec = json.loads(main.read_text(encoding="utf-8"))
    assert rec["knowledge"]["topics"] == []
    rec["knowledge"].pop("topics")                                         # as the older bundle has it
    body = json.dumps(rec).encode("utf-8")
    main.write_bytes(body)
    import hashlib
    sums = [ln for ln in (out / bot_move.SUMS).read_text(encoding="utf-8").splitlines() if not ln.endswith("  " + bot_move.MAIN)]
    (out / bot_move.SUMS).write_text("\n".join(sums + [f"{hashlib.sha256(body).hexdigest()}  {bot_move.MAIN}"]) + "\n", encoding="utf-8", newline="\n")
    record = bot_move.read(out)
    assert bot_move.lines_for(record)["topics"] == []
    there = tmp_path / "there" / "state"
    make(there)
    bot_move.apply(there, record)
    assert bot_move.reading_topics(there) == []


# -- plan_books with several topics --------------------------------------------------------------------------------------

def test_the_list_reads_only_wikipedia_and_gutenberg_with_a_plans_own_limits():
    kp = kmod.plan_of(SIX, only=kmod.READING_SOURCES)
    assert [f["source"] for f in kp["fetch"]] == ["wikipedia", "gutenberg"]
    assert kp["hosts"] == ["en.wikipedia.org", "www.gutenberg.org"] and not kp["offered"] and "offers" not in kp and "look" not in kp
    normal = {f["source"]: f for f in kmod.plan_of(SIX)["fetch"]}
    for f in kp["fetch"]:                                                  # the same limits as a normal plan
        assert {k: f[k] for k in ("per_topic", "max_items", "max_kb")} == {k: normal[f["source"]][k] for k in ("per_topic", "max_items", "max_kb")}
    offer = kmod.reading_offer(SIX)
    assert offer["sources"] == [{"name": "Wikipedia", "kind": "articles", "about_mb": 2.0, "up_to": 40},
                                {"name": "Project Gutenberg", "kind": "books", "about_mb": 5.0, "up_to": 10}]
    assert offer["about_mb"] == 7.0


def _world(tmp_path, state, **ports):
    from toolsmith_fakes import FakePorts, World
    p = FakePorts(tmp_path)
    p.search_places = lambda: ["opds.library.kiwix.org"]
    for k, v in ports.items():
        setattr(p, k, v)
    return World(tmp_path, state, ports=p)


def test_plan_books_with_several_topics_makes_one_card_asks_no_model_and_downloads_nothing(state, tmp_path, monkeypatch):
    asked = []
    w = _world(tmp_path, state, model_needs=lambda *a: asked.append(a) or [], model_offer=lambda _r: {"role": "x"})

    class Link:
        plan = None

        def build(self):
            return w.host.agent_build(b["id"])

        def now(self, _line):
            pass

        def knowledge_find(self, topics):
            raise AssertionError("the list asks no catalog")

        def put_plan(self, body):
            self.plan = w.host.agent_plan(b["id"], body)

    made = {}

    def launch(do, build_id, token, cache=None, on_exit=None):
        link = Link()
        agent.plan(link, Path("."))
        made["plan"] = link.plan
        return SimpleNamespace(wait=lambda *_a: True, stop=lambda: None)
    monkeypatch.setattr(w.host.runner, "launch", launch)
    b = w.host.draft(kmod.READING_GOAL, [{"name": t, "does": t} for t in SIX])     # the id Link reads
    w.host.store.change(b["id"], lambda x: x.update(books_only=True, knowledge_only=list(kmod.READING_SOURCES)))
    got = w.host.plan_build(b["id"])
    assert not asked                                                       # no model, for a plan of books
    assert got["knowledge_plan"]["topics"] == SIX and got["jobs"] == [] and got["books_only"] is True
    assert got["knowledge_plan"]["hosts"] == ["en.wikipedia.org", "www.gutenberg.org"]
    assert got["consent"]["id"] and not got.get("yes") and not got.get("knowledge_run")
    assert got["card"]["title"] == "Get these for your library?"
    text = " ".join(got["card"]["lines"])
    assert "en.wikipedia.org" in text and "www.gutenberg.org" in text
    assert not any(w in text for w in ("Appropedia", "iFixit", "Data.gov", "Hugging Face", "Kiwix"))
    assert got["looked"]["asked"] == [] and got["looked"]["line"].startswith("Wikipedia and Project Gutenberg are searched only after your Yes")


def test_a_lists_topics_judge_one_another_not_its_title():
    b = {"goal": kmod.READING_GOAL, "knowledge_only": ["wikipedia"], "knowledge_plan": {"topics": SIX}}
    goal = "" if b.get("knowledge_only") else b["goal"]
    need = kmod.fit_words(goal, SIX)["World building"]
    assert "book" not in need and "articl" not in need and "world" not in need and "plot" in need


# -- Recommended -----------------------------------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, state):
    from toolsmith_fakes import FakePorts, World
    w = World(tmp_path, state, ports=FakePorts(tmp_path))
    dev = {"Authorization": "Bearer " + w.core.tokens.issue_device("Test laptop")}
    client = w.app.test_client()
    return SimpleNamespace(world=w, get=lambda path: client.get(path, headers=dev), post=lambda path, body=None: client.post(path, json=body or {}, headers=dev))


def test_recommended_on_a_moved_bot_offers_the_list(api, state):
    bot_move.apply(state, {"_folder": str(state), "bot": {"name": "Editor"}, "knowledge": {"topics": SIX}})
    got = api.get("/v1/knowledge/recommended").json
    assert got["reading"]["topics"] == SIX and got["reading"]["state"] == "offer" and got["rows"] == []
    assert [s["name"] for s in got["reading"]["sources"]] == ["Wikipedia", "Project Gutenberg"]


def test_recommended_on_an_old_bot_after_the_updates_seed(api, state):
    """A bot set up from the bundle before topics has no list; ``update`` writes only this one key (install-writersbot.sh)."""
    assert api.get("/v1/knowledge/recommended").json == {"rows": [], "reading": None}
    jsonfile.write(state / "settings" / bot_move.LIST, {"topics": SIX})
    got = api.get("/v1/knowledge/recommended").json
    assert got["reading"]["topics"] == SIX and got["reading"]["state"] == "offer"


def test_once_read_the_list_says_so_and_offers_nothing(api, state):
    jsonfile.write(state / "settings" / bot_move.LIST, {"topics": SIX})
    _read_build(state, knowledge_only=["wikipedia", "gutenberg"])
    got = api.get("/v1/knowledge/recommended").json["reading"]
    assert got["state"] == "done" and "Read 3" in got["line"]


def test_the_source_bots_own_first_build_counts_as_read(api, state):
    jsonfile.write(state / "settings" / bot_move.LIST, {"topics": [t.lower() for t in SIX]})
    _read_build(state)                                                     # its plan had every topic, and read something
    assert api.get("/v1/knowledge/recommended").json["reading"]["state"] == "done"


def test_a_try_that_read_nothing_offers_again_and_says_why(api, state):
    jsonfile.write(state / "settings" / bot_move.LIST, {"topics": SIX})
    _read_build(state, count=0, knowledge_only=["wikipedia"])
    got = api.get("/v1/knowledge/recommended").json["reading"]
    assert got["state"] == "offer" and "Read 0" in got["line"]


def test_no_list_no_plan(api):
    assert api.post("/v1/knowledge/recommended/reading").status_code == 404


# -- can it look? ---------------------------------------------------------------------------------------------------------

CATALOG_HOSTS = ["opds.library.kiwix.org", "catalog.data.gov", "huggingface.co"]


def test_books_can_says_each_reason(api):
    host = api.world.host
    opened = {"setting": "open", "search": True, "search_hosts": CATALOG_HOSTS}
    got = host.books_can(opened)
    assert got["can"] == "ok" and got["ask"] == ["Kiwix", "Data.gov", "Hugging Face"]
    assert got["after_yes"] == ["Wikipedia", "Project Gutenberg", "Appropedia", "iFixit"] and not got["line"]
    closed = host.books_can({"setting": "closed", "search": False})
    assert closed["can"] == "bridge_closed" and "The Bridge is closed" in closed["line"]
    off = host.books_can({"setting": "open", "search": False, "search_hosts": []})
    assert off["can"] == "no_search" and "Toolsmith's search is off" in off["line"] and "Kiwix, Data.gov and Hugging Face" in off["line"]
    assert "Wikipedia" in off["line"] and not off["ask"]
    older = host.books_can({"setting": "open", "search": True, "search_hosts": ["api.github.com"]})     # a Yes from before these places
    assert older["can"] == "no_search"
    host._household = lambda: None
    locked = host.books_can(opened)
    assert locked["can"] == "locked" and "vault is locked" in locked["line"]
    host.paused_reason = lambda: "A safety check failed."
    assert host.books_can(opened)["can"] == "paused" and "safety check failed" in host.books_can(opened)["line"]


def test_the_routes_refuse_with_the_reason_and_the_words_are_checked(api, monkeypatch):
    from puffpai_core.zone3.service import for_app
    z = for_app(api.world.app)
    monkeypatch.setattr(z.bridge, "view", lambda _n=None: {"setting": "closed", "search": False})
    got = api.post("/v1/knowledge/find", {"words": "lighthouses"})
    assert got.status_code == 409 and got.json["code"] == "bridge_closed" and "Bridge is closed" in got.json["error"]
    assert api.get("/v1/knowledge/find").json["can"] == "bridge_closed"
    assert api.post("/v1/knowledge/find", {"words": "1984"}).status_code == 400
    assert api.world.host.store.all() == []                                # nothing was started


@pytest.mark.parametrize("said,word", [("Alice's Adventures in Wonderland", "Alice Adventures in Wonderland"),
                                       ("The Time Machine: an Invention", "The Time Machine an Invention"),
                                       ("Les Mis\u00e9rables", "Les Miserables"),
                                       ("Fahrenheit 451", "Fahrenheit"),
                                       ("Twenty Thousand Leagues Under the Sea", "Twenty Thousand Leagues Sea"),
                                       ("Moby-Dick", "Moby Dick"),
                                       ("George MacDonald", "George Macdonald"),
                                       ("1984", "")])
def test_a_title_becomes_words_a_topic_can_take(said, word):
    assert kmod.find_words(said) == word
    assert not word or not kmod.topic_problem(word)


# -- what each place did --------------------------------------------------------------------------------------------------

@pytest.fixture
def catalogs(tmp_path):
    store = Builds(tmp_path)
    store.write({"id": BID, "goal": "garden plants", "planning": True, "jobs": [], "jobs_in": []})
    ports = CatalogPorts(CATALOG_HOSTS)
    host = SimpleNamespace(store=store, ports=ports, state=tmp_path, registry=None, _stopping=False)
    return SimpleNamespace(ports=ports, store=store, fetcher=kmod.Fetcher(host))


def _looked(c, topic="garden plants"):
    c.fetcher.find(BID, [topic])
    b = c.store.read(BID)
    kp = kmod.plan_of([topic], finds=b.get("knowledge_finds"), context=topic, goal=topic)
    return b, kmod.looked(kp, b.get("knowledge_finds"), b.get("knowledge_down"), [topic])


def test_found_says_each_place_and_what_comes_after_her_yes(catalogs):
    b, got = _looked(catalogs)
    assert got["found"] >= 3 and got["failed"] == []
    assert got["line"].startswith('Found on "garden plants": Kiwix 1, Data.gov 1, Hugging Face 1.')
    assert got["line"].endswith("Wikipedia, Project Gutenberg, Appropedia and iFixit are searched only after your Yes.")
    assert "Writing your plan" in b["now"]                                 # the last thing it said while it looked


def test_a_place_that_fails_is_named(catalogs):
    catalogs.ports.down.add("catalog.data.gov")
    b, got = _looked(catalogs)
    assert got["failed"] == ["Data.gov"] and "Data.gov did not answer." in got["line"] and "Kiwix 1" in got["line"]


def test_nothing_found_is_said_in_plain_words(catalogs):
    catalogs.ports.web.update({"opds.library.kiwix.org": b"<feed></feed>", "catalog.data.gov": b"{}", "huggingface.co": b"[]"})
    _b, got = _looked(catalogs, "lighthouses")
    assert got["found"] == 0
    assert got["line"].startswith('Kiwix, Data.gov and Hugging Face had nothing on "lighthouses".')


def test_it_says_what_it_is_asking_while_it_asks(catalogs):
    said = []
    real = catalogs.store.change

    def change(bid, fn):
        got = real(bid, fn)
        said.append((catalogs.store.read(bid) or {}).get("now"))
        return got
    catalogs.store.change = change
    catalogs.fetcher.find(BID, ["garden plants"])
    assert said[0] == 'Looking in Kiwix, Data.gov and Hugging Face for "garden plants"'
    assert any(s and "Still looking in" in s for s in said)


def test_with_no_catalog_asked_the_line_only_says_after_your_yes():
    kp = kmod.plan_of(["bees"], finds={}, context="bees", goal="bees")
    got = kmod.looked(kp, None, None, ["bees"])
    assert got["asked"] == [] and got["line"] == "Wikipedia, Project Gutenberg, Appropedia and iFixit are searched only after your Yes."


def test_words_the_screen_drops_get_a_plain_line_not_a_card(state, tmp_path):
    from puffpai_core.zone3.toolsmith_host.service import BOOKS_NO_WORDS_LINE
    from toolsmith_fakes import FakePorts, World
    w = World(tmp_path, state, ports=FakePorts(tmp_path))
    b = w.host.draft("Mark", [{"name": "Mark", "does": "Mark"}])
    w.ports.household_names = lambda: ["Mark"]
    w.host.store.change(b["id"], lambda x: x.update(books_only=True, planning=True))
    w.host.agent_plan(b["id"], {"jobs": [], "knowledge": {"topics": ["Mark"], "asked": {}}})
    got = w.host.view(b["id"])
    assert not got["consent"]["id"] and got["consent"]["said"] == BOOKS_NO_WORDS_LINE


# -- the words on the page ------------------------------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js")
def test_the_words_for_a_plan_of_books():
    code = r"""
    const L = require(process.argv[1]);
    const out = {
      books: L.planningWords(L.buildView({ books_only: true, planning: true, goal: "bees" })),
      apps: L.planningWords(L.buildView({ planning: true, goal: "bees" })),
      title: L.buildTitle("bees", true), list: L.buildTitle("Books and articles for this bot", true), app: L.buildTitle("a recipe box", false),
      looking: L.buildChip({ books_only: true, planning: true }).words, reading: L.buildChip({ books_only: true, library: { state: "fetching" } }).words,
      done: L.buildChip({ books_only: true, library: { state: "done" } }).words, no: L.buildChip({ books_only: true, library: { state: "waiting" } }).words,
      waiting: L.buildChip({ books_only: true }, true).words, starting: L.buildChip({ books_only: true, yes: true, library: { state: "waiting" } }).words,
      plain: L.buildChip({ jobs: [] }).words,
      live: L.findLive("Looking in Kiwix", null, Date.now() - 12000, Date.now()),
      still: L.findLive("Looking in Kiwix", new Date(Date.now() - 125000).toISOString(), Date.now() - 125000, Date.now()),
      ask: ["books about lighthouses", "a book on tides", "find me novels by Jane Austen", "the book Moby Dick", "something to read about whales"].map((t) => L.addSource(t).words),
      apps_stay: ["a book tracker", "a recipe book for my family", "an app for my books", "book club app", "books about my app"].map((t) => L.addSource(t).kind),
      github: L.addSource("https://github.com/a/b").kind, folder: L.addSource("C:\\Books").kind, id: L.addSource("books").kind,
    };
    console.log(JSON.stringify(out));
    """
    got = json.loads(subprocess.run([NODE, "-e", code, str(WEB / "logic.js")], capture_output=True, text=True, check=True, timeout=60).stdout)
    assert got["books"] == "Toolsmith is looking through the libraries for books." and "apps" not in got["books"]
    assert got["apps"] == "Toolsmith is planning: finding apps for each job."
    assert (got["title"], got["list"], got["app"]) == ("Books: bees", "Books and articles for this bot", "a recipe box")
    assert (got["looking"], got["reading"], got["done"], got["no"], got["waiting"], got["starting"]) == ("looking", "reading", "done", "no Yes", "waiting on you", "starting")
    assert got["plain"] == "starting"                                      # an app build reads as it did
    assert got["live"] == "Looking in Kiwix, 12 seconds so far." and got["still"] == "Still working: Looking in Kiwix, 2 minutes so far."
    assert got["ask"] == ["lighthouses", "tides", "Jane Austen", "Moby Dick", "whales"]
    assert got["apps_stay"] == ["describe"] * 5
    assert (got["github"], got["folder"], got["id"]) == ("github", "folder", "builtin")


def test_every_screen_uses_those_words():
    assert "L.planningWords(v)" in (WEB / "pages" / "building.js").read_text(encoding="utf-8")
    window = (WEB / "window.js").read_text(encoding="utf-8")
    assert "L.planningWords(v)" in window and "L.buildTitle(b.goal, b.books_only)" in window
    assert "finding apps for each job" not in window and "finding apps for each job" not in (WEB / "pages" / "building.js").read_text(encoding="utf-8")


# -- the page's states, in real Edge on a real Core, in the dashboard and the window ------------------------------------------

def _answers(b, can, post=None, polls=None):
    """Recorded answers for Find books, so each state can be seen with no network. Anything else goes to the real Core."""
    b.js("""window.__real = window.__real || window.fetch; window.fetch = (u, o) => { const s = String(u), m = (o && o.method) || 'GET';
      const send = (x, st) => Promise.resolve(new Response(JSON.stringify(x), {status: st, headers: {'Content-Type': 'application/json'}}));
      if (s.indexOf('/v1/knowledge/find') >= 0 && m === 'GET') return send(window.__can, 200);
      if (s.indexOf('/v1/knowledge/find') >= 0) return send(window.__post, window.__postStatus);
      if (s.indexOf('/v1/toolsmith/builds/x1') >= 0) return send(window.__polls.length > 1 ? window.__polls.shift() : window.__polls[0], 200);
      return window.__real(u, o); }; true""")
    b.js(f"window.__can = {json.dumps(can)}; window.__post = {json.dumps(post or {})}; window.__postStatus = {201 if post else 409}; window.__polls = {json.dumps(polls or [])}; true")


def _look(b, words="lighthouses"):
    b.js(f"""(() => {{ const q = document.querySelector("input[aria-label='What to find books about']"); q.value = {json.dumps(words)};
      q.form.requestSubmit(); return true; }})()""")


def _states(b, where):
    box = "(document.querySelector('[data-find-books]').innerText)"
    build = {"id": "x1", "goal": "lighthouses", "books_only": True, "jobs": [], "planning": True, "created_at": "2026-11-01T10:00:00"}
    ok = {"can": "ok", "line": "", "ask": ["Kiwix", "Data.gov", "Hugging Face"], "after_yes": ["Wikipedia", "Project Gutenberg"]}
    # working: named places, then the build's own line with the time
    _answers(b, ok, build, [{**build, "now": 'Hugging Face had nothing. Still looking in Kiwix and Data.gov for "lighthouses"'}])
    _look(b)
    assert b.wait("(document.querySelector('[data-find-now]')||{textContent:''}).textContent.includes('Still looking in Kiwix and Data.gov')", timeout_s=15), (where, b.js(box))
    assert b.wait("/\\d+ seconds? so far/.test(document.querySelector('[data-find-now]').textContent)", timeout_s=10), (where, b.js(box))
    # the POST came back while it is still planning, and it keeps going until the plan comes: found, one line above the card
    line = 'Found on "lighthouses": Kiwix 2. Data.gov did not answer. Wikipedia and Project Gutenberg are searched only after your Yes.'
    done = {**build, "planning": False, "consent": {"id": ""}, "looked": {"line": line, "found": 2}, "card": {"lines": ["Knowledge: Toolsmith reads about lighthouses."]}}
    b.js(f"window.__polls = [{json.dumps({**done, 'consent': {'id': 'gone', 'said': ''}})}]; true")
    assert b.wait("!!document.querySelector('[data-find-found]')", timeout_s=15), (where, b.js(box))
    assert line in b.js(box) and not b.js("document.querySelector('[data-find-now]')")
    # nothing could be looked up: plain words, no card
    said = "Nothing was looked up: those words can't be sent."
    _answers(b, ok, build, [{**build, "planning": False, "consent": {"id": None, "said": said}}])
    _look(b)
    assert b.wait("!!document.querySelector('[data-find-nothing]')", timeout_s=15), (where, b.js(box))
    assert said in b.js(box) and not b.js("document.querySelector('[data-find-books] .card')")
    # the Bridge closed: said, with the Bridge's own button
    _answers(b, {"can": "bridge_closed", "line": "The Bridge is closed, so your bot can't reach any library, and nothing was looked up.", "ask": [], "after_yes": []})
    _look(b)
    assert b.wait("(document.querySelector('[data-find-cant=bridge_closed]')||{}).isConnected === true", timeout_s=15), (where, b.js(box))
    assert b.wait("Array.from(document.querySelectorAll('[data-find-books] button')).some(x => x.textContent.includes('Open the Bridge'))", timeout_s=10), (where, b.js(box))
    # no search Yes: said, with a button for its card, and a plan is still made
    off = {"can": "no_search", "line": "Toolsmith's search is off, so Kiwix can't be asked. Wikipedia can still be read after your Yes.", "ask": [], "after_yes": ["Wikipedia"]}
    _answers(b, off, build, [{**build, "planning": False, "consent": {"id": "gone"}, "looked": {"line": "Wikipedia is searched only after your Yes.", "found": 0}, "card": {"lines": []}}])
    _look(b)
    assert b.wait("!!document.querySelector('[data-find-allow]') && !!document.querySelector('[data-find-found]')", timeout_s=15), (where, b.js(box))
    # the vault locked: the real reason, and the way there
    _answers(b, {"can": "locked", "line": "Your vault is locked", "ask": [], "after_yes": []})
    _look(b)
    assert b.wait("Array.from(document.querySelectorAll('[data-find-books] button')).some(x => x.textContent.includes('Open the Vault page'))", timeout_s=15), (where, b.js(box))
    # paused
    _answers(b, {"can": "paused", "line": "Toolsmith is paused: A safety check failed.", "ask": [], "after_yes": []})
    _look(b)
    assert b.wait("(document.querySelector('[data-find-cant=paused]')||{}).textContent === 'Toolsmith is paused: A safety check failed.'", timeout_s=15), (where, b.js(box))
    assert "free books in the public domain" in b.js("document.querySelector('[data-find-what]').textContent")


def test_each_find_books_state_on_the_page(state, tmp_path, monkeypatch, drawbridge):
    from test_i10a_desktop_clicks import EDGE, WHERE, _desktop, _point
    if EDGE is None:
        pytest.skip("Microsoft Edge")
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        b.click(*_point(b, ".rail a.item[href='#knowledge']", 0))
        assert b.wait("!!document.querySelector('[data-find-books]')", timeout_s=30), b.js(WHERE)
        _states(b, "dashboard")
        b.js("location.assign('/window/'); true")
        assert b.wait("!!document.querySelector('[data-rail=knowledge]')", timeout_s=30), b.js(WHERE)
        for _try in range(3):
            b.click(*_point(b, "[data-rail=knowledge]", 0))
            if b.wait("!!document.querySelector('[data-find-books]')", timeout_s=10):
                break
        _states(b, "window")


def test_the_add_tab_sends_books_to_knowledge_and_apps_as_before(state, tmp_path, monkeypatch, drawbridge):
    from test_i10a_desktop_clicks import EDGE, WHERE, _desktop, _point
    if EDGE is None:
        pytest.skip("Microsoft Edge")
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        b.js("location.hash = '#apps/add'; true")
        assert b.wait("!!document.querySelector('[data-find-on-knowledge] button')", timeout_s=30), b.js(WHERE)
        assert b.js("document.querySelector('[data-find-on-knowledge]').textContent") == "Looking for books or reference? Find books on Knowledge"
        b.js("""window.__plans = []; const real = window.fetch; window.fetch = (u, o) => { const s = String(u);
          if (s.indexOf('/v1/toolsmith/plan') >= 0) { window.__plans.push(o.body); return Promise.resolve(new Response('{"error": "stopped here"}', {status: 409, headers: {'Content-Type': 'application/json'}})); }
          return real(u, o); }; true""")
        b.js("""const t = document.querySelector("textarea[aria-label='What to add']"); t.value = 'a recipe box for my family'; t.form.requestSubmit(); true""")
        assert b.wait("window.__plans.length === 1", timeout_s=10), b.js(WHERE)                      # an app request: as before
        assert "recipe box" in b.js("window.__plans[0]")
        b.js("""const t2 = document.querySelector("textarea[aria-label='What to add']"); t2.value = 'books about lighthouses'; t2.form.requestSubmit(); true""")
        assert b.wait("location.hash.indexOf('#knowledge') === 0 && (document.querySelector(\"input[aria-label='What to find books about']\")||{}).value === 'lighthouses'", timeout_s=15), b.js(WHERE)
        assert b.js("window.__plans.length") == 1                                                    # nothing planned for it
        b.click(*_point(b, ".rail a.item[href='#apps']", 0))
        assert b.wait("!document.getElementById('view-apps').hidden", timeout_s=10), b.js(WHERE)


def test_the_tile_shows_the_list_and_one_button(state, tmp_path, monkeypatch, drawbridge):
    from test_i10a_desktop_clicks import EDGE, WHERE, _call, _desktop, _point
    if EDGE is None:
        pytest.skip("Microsoft Edge")
    (state / "settings").mkdir(parents=True, exist_ok=True)
    jsonfile.write(state / "settings" / bot_move.LIST, {"topics": SIX})
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        b.click(*_point(b, ".rail a.item[href='#knowledge']", 0))
        assert b.wait("!!document.querySelector('[data-reading-get]')", timeout_s=30), b.js(WHERE)
        said = b.js("document.querySelector('[data-tile=recommended]').innerText")
        assert "Books and articles for this bot" in said and all(t in said for t in SIX)
        assert "Wikipedia: up to 40 articles." in said and "Project Gutenberg: up to 10 books." in said and "About 7 MB in all" in said
        # the Bridge is closed on a new bot: pressing says so, and nothing is planned or fetched
        b.click(*_point(b, "[data-reading-get]", 0))
        assert b.wait("!!document.querySelector('[data-reading-box] [data-find-cant=bridge_closed]')", timeout_s=20), b.js(WHERE)
        assert _call(b, "GET", "/v1/knowledge/sources")["data"]["count"] == 0
