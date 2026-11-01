"""Lane I19e (D71): a job no app does can be proved by the bot's library; honest lines when nothing is found.

From the owner's live test: a guides-and-manuals goal read 40 articles and 10 books, yet every job ended "Nothing
found yet ... Paste a GitHub address", because a job could only end through an app hunt. Now, after the jobs and
the knowledge fetch end, a job with no working app is searched for in that build's own fetched items with the
brain's own search (``Library.search``, the index ``search_knowledge`` reads). Also: the "Nothing found" line names
a candidate the cloud rank dropped first (RC5), "Check again" on a job with no app is refused plainly (RC6), and
a give-up reason or a chat job name is never cut mid-word or left on "and" (RC8).

Three unrelated goals throughout: a home recipe bot, a small-business invoices bot, a birdwatching bot. Fake
fetch, fake cloud rank, fake ports; the search is the real Library over files the fake fetch wrote. No network,
no real key, no bot port.
"""

from __future__ import annotations

import json

import pytest

from puffpai_core import wiring
from puffpai_core.zone1 import fetched
from puffpai_core.zone1.classify import CHUNK
from puffpai_core.zone1.folders import Library
from puffpai_core.zone3.toolsmith import agent, find, tries
from puffpai_core.zone3.toolsmith.errors import Refused
from puffpai_core.zone3.toolsmith.names import cut_text, short_name
from puffpai_core.zone3.toolsmith_host import knowledge as kmod
from puffpai_core.zone3.toolsmith_host import service, store
from test_i18e_check_again import Recorder
from toolsmith_fakes import World

GOALS = {
    "recipe": {"goal": "a home recipe bot for family dinners",
               "job": {"name": "Recipe guides", "does": "Guides for cooking family recipes and baking bread",
                       "sample_ask": "How do I bake sourdough bread?"},
               "items": [("wikipedia", "Sourdough", "Sourdough bread is baked from a starter of wild yeast. "
                                                    "To bake sourdough you feed the starter, shape and proof. ", 2),
                         ("gutenberg", "The Bread Book", "Bread of every kind. Bake the sourdough loaf hot. ", 2)]},
    "invoices": {"goal": "a small-business invoices bot",
                 "job": {"name": "Send invoices", "does": "Makes and sends invoices to customers",
                         "sample_ask": "Send the March invoice to the bakery"},
                 "items": [("wikipedia", "Accounting", "Accounting is the measurement of economic activity. ", 3)]},
    "birds": {"goal": "a birdwatching bot for my garden",
              "job": {"name": "Bird ID guides", "does": "Helps identify garden birds by song and colour",
                      "sample_ask": "Which bird has a red breast and sings in winter?"},
              "items": [("wikipedia", "European robin", "The robin is a small bird with a red breast. It sings "
                                                       "in winter in the garden. ", 2),
                        ("gutenberg", "Birds of the Garden", "Of the robin: a red breast, and it sings. ", 1)]},
}


def _fetch(w, b, items):
    """The fake fetch: each item kept the way the Core's fetch keeps it, indexed by the real Library, and the
    build's ``knowledge_run`` ended as done. An item's last number is how many passages (index chunks) it has,
    each starting with its text."""
    rows = []
    for k, (sid, title, text, chunks) in enumerate(items):
        body = "".join((text + " filler" * 400)[:CHUNK] for _ in range(chunks))
        path = fetched.save(w.state, sid, fetched.slug(title, prefix=f"t{k}"), body,
                            {"title": title, "url": f"https://example.org/{k}", "license": "x", "credit": "x"})
        Library(None, w.state).scan_file(path)
        rows.append({"source": sid, "title": title, "file": path.name, "size": path.stat().st_size,
                     "indexed": True})

    def fn(x):
        x["knowledge_plan"] = {"topics": ["a topic"], "fetch": [], "hosts": []}
        x["knowledge_run"] = {"state": "done", "items": rows, "counts": {}}
    w.host.store.change(b["id"], fn)


def _world(tmp_path, state, searches=None):
    w = World(tmp_path, state)
    lib = Library(None, w.state)

    def search(q, limit):                          # the brain's own search, recorded
        if searches is not None:
            searches.append(q)
        return lib.search(q, limit)
    w.ports.knowledge_search = search
    return w


def _ended(w, key, state_="nothing_found", *, cands=(), pasted=(), line="Nothing found yet. Paste a GitHub address "
           "for this job.", tried=()):
    g = GOALS[key]
    st = w.host.settings()
    job = {**g["job"], **({"pasted": list(pasted)} if pasted else {})}
    b = w.host.store.create(g["goal"], [job], st)
    w.host.store.set_plan(b["id"], {"jobs": [{**job, "plan": {"candidates": list(cands)}}]}, st, "")

    def fn(x):
        x["yes"] = {"given": True, "consent_id": "c_test", "sha": x["plan_sha"]}
        for j in x["jobs"]:
            j["run"].update(state=state_, line=line, tried=list(tried))
    return w.host.store.change(b["id"], fn)


def _check(w, b):
    return w.link(b["id"]).library_check(b["jobs"][0]["id"])


# -- RC2: a job proved by the library ----------------------------------------------------------------------

def test_a_recipe_job_with_no_app_works_from_the_library(tmp_path, state):
    w = _world(tmp_path, state)
    b = _ended(w, "recipe")
    _fetch(w, b, GOALS["recipe"]["items"])
    got = _check(w, b)
    assert got["working"] is True
    job = w.host.store.read(b["id"])["jobs"][0]
    assert job["run"]["state"] == "working"
    line = job["run"]["line"]
    assert line.startswith("No app does this yet, but your library has some reading on this: ")
    assert "1 Wikipedia article and 1 public domain book" in line and "\"Sourdough\"" in line
    assert "How do I bake sourdough bread?" in line and "Paste a GitHub address" not in line
    res = job["run"]["result"]
    assert res["by"] == "knowledge" and res["count"] == 4 and res["sources"] == {"wikipedia": 1, "gutenberg": 1}
    assert set(res["titles"]) == {"Sourdough", "The Bread Book"} and "package" not in res
    assert "\u2014" not in line


def test_a_birdwatching_job_at_both_thresholds_works_and_names_the_app_tried_first(tmp_path, state):
    assert (kmod.MIN_PASSAGES, kmod.MIN_ITEMS) == (3, 2)   # this goal has exactly 3 passages from 2 items
    w = _world(tmp_path, state)
    b = _ended(w, "birds", "not_yet", line="No app worked yet. birdnet-lite: it did not start.",
               tried=[{"name": "birdnet-lite", "ref": "github:example/birdnet-lite", "why": "it did not start"}])
    _fetch(w, b, GOALS["birds"]["items"])
    assert _check(w, b)["working"] is True
    run = w.host.store.read(b["id"])["jobs"][0]["run"]
    assert run["state"] == "working" and "European robin" in run["line"]
    assert run["line"].endswith("Apps tried first: birdnet-lite.") and run["result"]["count"] == 3
    assert "3 passages from 1 Wikipedia article and 1 public domain book" in run["line"]


@pytest.mark.parametrize("key,items,state_", [
    # just under the passages: 2 passages from 2 items
    ("recipe", [("wikipedia", "Sourdough", "Bake sourdough bread at home. ", 1),
                ("gutenberg", "The Bread Book", "Bake sourdough bread hot. ", 1)], "nothing_found"),
    # just under the items: 3 passages, all from 1 item (a glossary page alone is too thin)
    ("birds", [("wikipedia", "Glossary of birds", "The robin has a red breast and sings in winter. ", 3)], "not_yet"),
])
def test_too_thin_keeps_the_job_and_adds_one_honest_line(tmp_path, state, key, items, state_):
    w = _world(tmp_path, state)
    old = ("Nothing found yet. Paste a GitHub address for this job." if state_ == "nothing_found"
           else "No app worked yet. feeder-cam: it did not start.")
    b = _ended(w, key, state_, line=old)
    _fetch(w, b, items)
    got = _check(w, b)
    assert got["working"] is False
    run = w.host.store.read(b["id"])["jobs"][0]["run"]
    n = sum(c for *_x, c in items)
    thin = (f"Your library has {n} passage{'s' if n != 1 else ''} on this so far: "
            + ", ".join(f"\"{t}\"" for _s, t, _x, _c in items) + ". Not enough to call this job done.")
    assert run["state"] == state_ and run["line"] == f"{old} {thin}" and run["result"] is None
    _check(w, b)                                          # asked again (a later run): the line is said once
    assert w.host.store.read(b["id"])["jobs"][0]["run"]["line"] == f"{old} {thin}"


def test_one_passage_is_never_enough():
    assert not kmod.enough({"count": 1, "items": 1}) and not kmod.enough({"count": 2, "items": 2})
    assert not kmod.enough({"count": 9, "items": 1}) and kmod.enough({"count": 3, "items": 2})


def test_the_agent_route_is_for_toolsmiths_own_token_only(tmp_path, state):
    w = _world(tmp_path, state)
    b = _ended(w, "recipe")
    _fetch(w, b, GOALS["recipe"]["items"])
    path = f"/v1/toolsmith/agent/jobs/{b['jobs'][0]['id']}/library"
    c = w.app.test_client()
    tokens = w.core.tokens
    for tok in (tokens.issue_owner_key(), tokens.issue_device("a paired browser"),
                tokens.issue_package_token("recipe-box")):
        r = c.post(path, json={}, headers={"Authorization": "Bearer " + tok})
        assert r.status_code == 403, (r.status_code, r.json)
    assert c.post(path, json={}).status_code == 401
    assert w.host.store.read(b["id"])["jobs"][0]["run"]["state"] == "nothing_found"
    other = _ended(w, "birds")                            # another build's token can't reach this build's job
    r = c.post(path, json={}, headers={"Authorization": "Bearer " + tokens.issue_toolsmith(other["id"])})
    assert r.status_code == 404
    r = c.post(path, json={}, headers={"Authorization": "Bearer " + tokens.issue_toolsmith(b["id"])})
    assert r.status_code == 200 and r.json["working"] is True


def test_an_invoices_job_that_needs_an_app_keeps_its_line_when_nothing_comes_back(tmp_path, state):
    searches = []
    w = _world(tmp_path, state, searches)
    line = "Nothing found yet: invoice-junk: no code (a list or documents). Paste a GitHub address for this job."
    b = _ended(w, "invoices", line=line)
    _fetch(w, b, GOALS["invoices"]["items"])
    assert _check(w, b)["working"] is False
    run = w.host.store.read(b["id"])["jobs"][0]["run"]
    assert run["state"] == "nothing_found" and run["line"] == line and run["result"] is None
    assert searches and all(" " in q for q in searches)     # never one lone word when the job has more


def test_an_app_that_worked_wins_and_is_never_searched(tmp_path, state):
    searches = []
    w = _world(tmp_path, state, searches)
    b = _ended(w, "birds", "working", line="The brain read birdcam: robins today.")
    _fetch(w, b, GOALS["birds"]["items"])
    assert _check(w, b)["working"] is False and searches == []
    run = w.host.store.read(b["id"])["jobs"][0]["run"]
    assert run["state"] == "working" and run["line"] == "The brain read birdcam: robins today."


@pytest.mark.parametrize("key", ["recipe", "birds"])
def test_a_job_she_pasted_an_app_for_still_needs_the_app(tmp_path, state, key):
    searches = []
    w = _world(tmp_path, state, searches)
    b = _ended(w, key, "not_yet", pasted=["https://github.com/example/her-app"], line="No app worked yet.")
    _fetch(w, b, GOALS[key]["items"])
    assert _check(w, b)["working"] is False and searches == []
    assert w.host.store.read(b["id"])["jobs"][0]["run"]["state"] == "not_yet"


def test_only_this_builds_own_items_count(tmp_path, state):
    w = _world(tmp_path, state)
    other = _ended(w, "recipe")
    _fetch(w, other, GOALS["recipe"]["items"])            # another build read the bread pages
    b = _ended(w, "recipe")
    _fetch(w, b, GOALS["invoices"]["items"])              # this build read only accounting
    assert _check(w, b)["working"] is False
    assert w.host.store.read(b["id"])["jobs"][0]["run"]["state"] == "nothing_found"


def test_no_proof_before_the_fetch_has_ended(tmp_path, state):
    w = _world(tmp_path, state)
    b = _ended(w, "recipe")
    _fetch(w, b, GOALS["recipe"]["items"])
    w.host.store.change(b["id"], lambda x: x["knowledge_run"].update(state="fetching"))
    assert _check(w, b)["working"] is False


def test_the_words_searched_are_the_jobs_own():
    assert kmod.prove_words("How do I bake sourdough bread?", "Guides for cooking family recipes") == \
        ["bake", "sourdough", "bread", "cooking", "family", "recipes"]
    # kind-of-want words and web addresses are never searched; a plural is also asked as its singular
    assert kmod.prove_words("Step-by-step guides and manuals, https://github.com/x/y") == []
    assert "invoice" in kmod.prove_queries(["invoices"]) and "invoices" in kmod.prove_queries(["invoices"])
    assert kmod.prove_queries(["robin", "red", "breast"])[0] == "robin red breast"


# -- the agent asks after the jobs and the fetch end --------------------------------------------------------

class _Link:
    def __init__(self, knowledge=True):
        self.calls: list[tuple] = []
        self.info = {"build": {"id": "b", "goal": "g", "yes": True, "knowledge": knowledge,
                               "jobs": [{"id": "b-0", "run": {"state": "planned"}},
                                        {"id": "b-1", "run": {"state": "planned"}}]},
                     "settings": {"where": "local", "budgets": {}, "timing": {}}}

    def build(self):
        return self.info

    def knowledge_step(self):
        self.calls.append(("fetch",))
        return {"done": True, "line": "Read 2 Wikipedia articles."}

    def library_check(self, job_id):
        self.calls.append(("library", job_id))
        if job_id == "b-1":
            raise RuntimeError("the Core said no")         # one job's problem never stops the others
        return {"working": True}


def test_the_library_is_asked_once_per_job_after_every_job_and_the_fetch_end(monkeypatch):
    link = _Link()
    monkeypatch.setattr(agent.Builder, "_run_job", lambda self, j: link.calls.append(("job", j["id"])))
    agent.Builder(link).run()
    kinds = [c[0] for c in link.calls]
    assert kinds.count("library") == 2 and kinds.index("library") > max(kinds.index("job"), kinds.index("fetch"))
    assert all(k == "library" for k in kinds[kinds.index("library"):])


def test_no_library_check_when_the_build_reads_no_knowledge(monkeypatch):
    link = _Link(knowledge=False)
    monkeypatch.setattr(agent.Builder, "_run_job", lambda self, j: None)
    agent.Builder(link).run()
    assert not [c for c in link.calls if c[0] == "library"]


# -- RC5: a candidate the cloud rank dropped is named first ----------------------------------------------

def test_nothing_line_without_drops_is_as_before():
    job = {"plan": {"not_used": [{"name": "x", "why": "no code"}], "notes": []}, "pasted": []}
    assert find.nothing_line(job) == "Nothing found yet: x: no code. Paste a GitHub address for this job."


# -- RC6: Check again with nothing new to try ----------------------------------------------------------------

def _started(w, key, cands):
    g = GOALS[key]
    st = w.host.settings()
    b = w.host.store.create(g["goal"], [g["job"]], st)
    b = w.host.store.set_plan(b["id"], {"jobs": [{**g["job"], "plan": {"candidates": list(cands)}}]}, st, "")
    covers = {"build": b["id"], "plan_sha": b["plan_sha"], "card": b["card"],
              "install_hosts": b["envelope"]["install_hosts"]}
    cid = w.ports.consent_request("package", store.bridge_target(b["id"]), covers, bound={"plan_sha": b["plan_sha"]})
    w.ports.consent.say_yes(cid)

    def fn(x):
        x["consent"]["id"] = cid
        x["yes"] = {"given": True, "consent_id": cid, "sha": x["plan_sha"], "use": {}}
        x["jobs"][0]["run"].update(state="nothing_found", line="Nothing found yet.")
    return w.host.store.change(b["id"], fn)


@pytest.mark.parametrize("key", ["recipe", "invoices", "birds"])
def test_check_again_on_a_job_with_no_app_is_refused_plainly(tmp_path, state, key):
    w = World(tmp_path, state)
    w.runner = w.host.runner = Recorder()
    b = _started(w, key, [])
    with pytest.raises(Refused) as exc:
        w.host.again(b["id"])
    assert str(exc.value) == ("Nothing new to try for this job. Paste a GitHub address, or ask Toolsmith to look "
                              "again with other words.")
    assert w.runner.runs == [] and w.host.store.read(b["id"])["jobs"][0]["run"]["state"] == "nothing_found"


def test_check_again_on_a_job_with_an_app_still_relaunches(tmp_path, state):
    w = World(tmp_path, state)
    w.runner = w.host.runner = Recorder()
    b = _started(w, "invoices", [{"full_name": "example/tiny-ledger", "name": "tiny-ledger", "runtime": "python"}])
    got = w.host.again(b["id"])
    assert w.runner.runs == [("build", b["id"])] and got["jobs"][0]["run"]["state"] == "planned"


# -- RC8: whole words ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("reason", [
    "The recipe box needs a database server. " * 9 + "Its Gradle/Maven dependencies could not be fetched here.",
    "tiny-ledger is a library for invoices with no screen of its own so there is nothing to start " * 5,
    "feeder-cam wants a camera driver; " * 12,
])
def test_a_long_reason_ends_on_a_whole_word(reason):
    got = cut_text(reason, 300)
    assert len(got) <= 300 and reason.startswith(got) and got
    rest = reason[len(got):]
    assert rest[:1] in ("", " ") and not got.endswith((" ", ",", ";"))
    assert not got.endswith("...") and "\u2014" not in got


def test_a_give_up_reason_is_cut_at_a_sentence_end():
    class Hooks:
        said: list = []

        def say(self, line):
            self.said.append(line)
    t = tries.Try.__new__(tries.Try)
    t.cand, t.hooks, t.sent_back = {"name": "recipe-box"}, Hooks(), True
    t.ran, t.started_lines, t.compose_project, t.human_steps = [{"ok": False}], [], "", []
    t.desktop = ""
    t.close = lambda: None
    t.t_give_up({"reason": "It is only a library. " * 14 + "The only thing you can run is the Gradle build."})
    assert t.ended.endswith("It is only a library.") and len(t.ended) <= 300


@pytest.mark.parametrize("text,name", [
    ("add tool for 2010 Toyota Camry battery change and general maintenance", "2010 Toyota Camry battery change"),
    ("find me a recipe planner for the family dinners", "Recipe planner"),       # D85: cut at "for"
    ("make a birdwatching log with the feeder camera", "Birdwatching log"),    # D85: cut at "with"
    ("an invoices helper for my small shop or", "Invoices helper"),
])
def test_a_chat_job_name_is_a_whole_phrase(text, name):
    got = wiring.chat_job(text)["name"]
    assert got.split()[-1].lower() not in {"and", "or", "the", "for", "with", "a"}
    if name:
        assert got.lower() == name.lower()


def test_short_name_keeps_one_word():
    assert short_name("and") == "and" and short_name("Recipes and") == "Recipes"


# -- a program already on this computer gets no install card (asked by lane I19d) ---------------------------

@pytest.mark.parametrize("name,label", [("docker", "Docker Desktop"), ("Node.js", "Node.js")])
def test_no_install_card_for_a_program_already_here(monkeypatch, name, label):
    import types
    from puffpai_core.zone3.toolsmith import programs
    here = {"docker", "node"}
    monkeypatch.setattr(programs.shutil, "which", lambda prog: f"C:/bin/{prog}.exe" if prog in here else None)
    asked = []
    fake = types.SimpleNamespace(cand={"name": "feeder-cam"}, ask=lambda step: asked.append(step) or {"ok": False})
    got = tries.Try.t_install_program(fake, {"name": name, "why": "It needs it."})
    assert asked == [] and got["ok"] is False
    assert got["said"].startswith(f"{label} is already on this computer; installing it again will not help.")
    tries.Try.t_install_program(fake, {"name": "ruby"})   # one that is not here still gets its card
    assert asked and asked[0]["id"] == "install-ruby"
