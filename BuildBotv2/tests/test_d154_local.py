"""D154: the same abilities on a local plan after her Yes. The owner: "even without opus, toolsmith shoukd be able to see what
is in the store, the whole goal, sesarch more than github, and put together some simple app that runs."

A model that can drive tools has the same conversation as a cloud build. Where a small model can't, the Core still gets
somewhere: a store match from the listing's own words, the plan's apps, then a simple app (``assemble.make_app``). The Core's
Package Manager, its cards and the apps are real; Drawbridge is a double that passes. Scripted stand-ins for the local model."""

from __future__ import annotations

import json

import pytest

from puffpai_core.zone2 import brain as brain_mod, catalog
from puffpai_core.zone3.toolsmith import find, scout
from puffpai_core.zone3.toolsmith_host import public_read
from test_d121_find_loop import _queries, _web
from test_d152_composes import Owner, Ports, WEDDING, _go
from toolsmith_fakes import World

GOALS = {"wedding": WEDDING, "bakery": ("Run a small bakery", [("Orders", "the cake orders and their pickup dates")]),
         "garage": ("Keep my garage in order", [("Tool shelf", "what is on each shelf")])}


class Local:
    """This computer's model: *turns(jobs)* in order, then no more tool calls. None as turns: it answers only in words."""

    def __init__(self, turns=None):
        self.turns = turns
        self.asked = []

    def __call__(self, system, messages, tools):
        self.asked.append({"system": system, "messages": messages, "tools": [t["name"] for t in tools]})
        turn = sum(1 for m in messages if m["role"] == "assistant")
        got = None
        if self.turns:
            task = messages[0]["content"][0]["text"]
            jobs = {j["name"]: j["id"] for j in json.loads(task.split("Its jobs: ", 1)[1].split("\n", 1)[0])}
            script = self.turns(jobs)
            got = script[turn] if turn < len(script) else None
        usage = {"in": 1, "out": 1, "cache_read": 0, "cache_write": 0}
        if got is None:
            return {"content": [{"type": "text", "text": "I think that is fine."}], "stop": "end_turn", "usage": usage}
        return {"content": [{"type": "tool_use", "id": f"l{turn}", "name": got[0], "input": got[1]}], "stop": "tool_use", "usage": usage}


@pytest.fixture
def world(tmp_path, state, drawbridge):
    made = []

    def make(talks=False, local=None, brain=_queries):
        ports = Ports(tmp_path, brain=brain)
        ports.http_send_fn = _web(lambda body: pytest.fail("no company model on a local build"), [])
        ports.local_goal = lambda: {"talks": talks, "makes": True}
        ports.local_replies = local
        w = World(tmp_path, state, ports)
        ports.core = w.core
        made.append(w)
        return w
    yield make
    for w in made:
        w.host.stop(timeout=10)
        w.core.caretaker.stop()
        w.core.steward.stop_all(remember=False)


# -- a small model that can't drive tools: the Core still gets somewhere ------------------------------------------------
@pytest.mark.parametrize("which", sorted(GOALS))
def test_with_no_model_that_can_drive_tools_each_job_ends_with_an_app_that_runs(world, which):
    w = world(talks=False)
    end, owner = _go(w, GOALS[which])
    for j in end["jobs"]:
        assert j["run"]["state"] == "working", j["run"]["line"]
        row = w.registry.get(j["run"]["result"]["package"])
        assert row["status"] == "running" and row["ui"]["pages"]
    assert owner.cards                                                         # her Yes on each one's own real card


def test_a_model_that_only_talks_is_given_up_on_and_the_core_goes_on(world):
    local = Local()
    w = world(talks=True, local=local)
    end, _owner = _go(w, GOALS["garage"])
    assert end["jobs"][0]["run"]["state"] == "working"
    assert len(local.asked) == scout.MAX_NUDGES + 1


def test_a_model_that_says_nothing_fits_does_not_end_the_job(world):
    def turns(jobs):
        return [("state_needs", {"needs": ["a shelf list"]}), ("finish", {"reason": "Nothing fits."})]
    w = world(talks=True, local=Local(turns))
    end, _owner = _go(w, GOALS["garage"])
    assert end["jobs"][0]["run"]["state"] == "working"


def test_her_no_on_the_made_apps_card_is_said_plainly(world):
    w = world(talks=False)
    end, owner = _go(w, GOALS["garage"], answer=lambda title: "no")
    run = end["jobs"][0]["run"]
    assert owner.cards and run["state"] in ("not_yet", "nothing_found") and run["line"]
    assert not [r for r in w.registry.list() if r["origin"] == "made"]


def test_a_store_app_the_brain_picked_is_asked_about_before_a_simple_app(world):
    """From a local plan she did not tick: the brain's pick gets its own card."""
    w = world(talks=False)
    w.ports.brain_picks = lambda package, ask, store=None: {"ok": bool(store), "asked": True, "package": "freshtable" if store else "",
                                                            "called": ["freshtable.add_shopping"] if store else [], "reason": ""}
    end, owner = _go(w, ("A helper", [("Shopping list", "what to buy this week")]))
    assert end["jobs"][0]["plan"]["store"]["package"] == "freshtable" and sum("FreshTable" in c for c in owner.cards) == 1
    assert end["jobs"][0]["run"]["result"]["package"] == "freshtable"


def test_a_small_model_on_a_wedding_still_gets_the_store_apps_then_makes_the_rest(world):
    """With no model at all: the listing's own words find Calendar and FreshTable, through their normal cards."""
    w = world(talks=False, brain=lambda system, user: None)
    end, owner = _go(w, WEDDING)
    run = {j["name"]: j["run"] for j in end["jobs"]}
    assert run["Timeline"]["result"]["package"] == "calendar" and run["Food"]["result"]["package"] == "freshtable"
    assert sum("Calendar" in c for c in owner.cards) == 1 and sum("FreshTable" in c for c in owner.cards) == 1
    assert w.registry.get(run["Guest list"]["result"]["package"])["origin"] == "made"
    assert {r["state"] for r in run.values()} == {"working"}


def test_two_jobs_that_want_one_store_app_ask_her_once(world):
    w = world(talks=False, brain=lambda system, user: None)
    end, owner = _go(w, ("A helper", [("Medicine times", "my medicine"), ("Pills", "the medicine in the box")]))
    assert sum("Calendar" in c for c in owner.cards) == 1
    assert [j["run"]["result"]["package"] for j in end["jobs"]] == ["calendar", "calendar"]


def test_her_no_on_a_store_card_is_final_and_a_simple_app_goes_on(world):
    w = world(talks=False, brain=lambda system, user: None)
    end, owner = _go(w, ("A helper", [("Medicine times", "my medicine")]), answer=lambda title: "no" if "Calendar" in title else "yes")
    assert w.registry.get("calendar") is None and sum("Calendar" in c for c in owner.cards) == 1
    assert w.registry.get(end["jobs"][0]["run"]["result"]["package"])["origin"] == "made"


# -- a model that can: the same conversation ---------------------------------------------------------------------------
def test_a_local_model_that_can_drive_tools_has_the_same_conversation(world):
    def turns(jobs):
        names = list(jobs)
        return [("state_needs", {"needs": ["a calendar", "a shopping list"]}),
                ("add_store_app", {"id": "calendar", "covers": [jobs[names[0]], jobs[names[1]]]}),
                ("add_store_app", {"id": "freshtable", "covers": [jobs[names[2]]]}),
                ("build_app", {"kind": "app", "what": "what happens when on the day", "covers": [jobs[names[3]]]})]
    local = Local(turns)
    w = world(talks=True, local=local)
    end, owner = _go(w, WEDDING)
    assert {j["run"]["state"] for j in end["jobs"]} == {"working"}, [j["run"]["line"] for j in end["jobs"]]
    assert len(owner.cards) == 3 and w.registry.get("calendar")["status"] == "running"
    assert "budget" not in local.asked[0]["system"] and '"tool": "calendar.add_event"' in local.asked[0]["messages"][0]["content"][0]["text"]


def test_a_local_conversation_has_an_end(world, monkeypatch):
    def turns(jobs):
        return [("state_needs", {"needs": ["x"]})] + [("ask_sources", {"words": ["boxes"]})] * 500
    local = Local(turns)
    w = world(talks=True, local=local)
    end, _owner = _go(w, GOALS["garage"])
    assert end["jobs"][0]["run"]["state"] == "working" and len(local.asked) <= 21     # the budget a local try has


def test_a_model_that_repeats_one_wrong_call_is_given_up_on_soon(world):
    """Seen for real on llama3.2:1b: state_needs with its own schema for arguments, over and over."""
    def turns(jobs):
        return [("state_needs", {"type": "object"})] * 500
    local = Local(turns)
    w = world(talks=True, local=local)
    end, _owner = _go(w, GOALS["garage"])
    assert end["jobs"][0]["run"]["state"] == "working" and len(local.asked) == scout.MAX_NUDGES + 1


# -- the plan: a store match is not lost ------------------------------------------------------------------------------
JOB = {"n": 0, "name": "Shopping list", "does": "what to buy this week"}
ROWS = [{"id": "freshtable", "name": "FreshTable", "description": "Pantry, shopping list, meal plan and recipes.",
         "abilities": [{"tool": "freshtable.add_shopping", "description": "Put a thing on the shopping list."}]},
        {"id": "calendar", "name": "Calendar", "description": "Events and tasks on one month view.", "abilities": []}]


def _finder(tmp_path, link, **kw):
    f = find.Finder(link, tmp_path / "c", **kw)
    f.store_rows = ROWS
    return f


class Asks:
    def __init__(self, *answers):
        self.answers = list(answers)

    def fits_store(self, _ask):
        return self.answers.pop(0)


def test_two_asks_that_differ_still_find_the_store_match(tmp_path):
    link = Asks({"ok": True, "asked": True, "package": "freshtable", "called": ["freshtable.add_shopping"]},
                {"ok": True, "asked": True, "package": "calendar", "called": ["calendar.add"]})
    assert _finder(tmp_path, link).store_reuse(JOB)["package"] == "freshtable"


def test_no_time_left_still_finds_the_store_match(tmp_path):
    f = _finder(tmp_path, Asks(), ends_at=0.0)
    got = f.store_reuse(JOB)
    assert got["package"] == "freshtable" and got["tool"] == "freshtable.add_shopping" and got["picked"] == []


def test_a_brain_that_can_not_be_asked_still_finds_the_store_match(tmp_path):
    link = Asks({"ok": False, "asked": False, "package": "", "called": []})
    assert _finder(tmp_path, link).store_reuse(JOB)["package"] == "freshtable"


def test_two_asks_that_agree_still_decide(tmp_path):
    yes = {"ok": True, "asked": True, "package": "calendar", "called": ["calendar.add"]}
    assert _finder(tmp_path, Asks(yes, dict(yes))).store_reuse(JOB)["package"] == "calendar"


@pytest.mark.parametrize("name,does", [("Bird log", "which birds came to the feeder"), ("A list", "the app that helps")])
def test_words_about_something_else_or_only_generic_words_match_nothing(name, does):
    assert find.store_by_words({"name": name, "does": does}, ROWS) == {}


def test_one_word_no_other_store_app_says_is_a_match_and_a_word_they_all_say_is_not():
    rows = [{"id": "a", "name": "A", "description": "Events on a date, each day."}, {"id": "b", "name": "B", "description": "Food for each day."}]
    assert find.store_by_words({"name": "Timeline", "does": "the dates"}, rows)["package"] == "a"
    assert find.store_by_words({"name": "Dinner", "does": "the food"}, rows)["package"] == "b"
    assert find.store_by_words({"name": "Sheet", "does": "the day"}, rows) == {}


# -- her Yes says so, and it is what lets it look -----------------------------------------------------------------------
def test_the_local_card_says_what_her_yes_covers(world):
    w = world(talks=False)
    title, jobs = GOALS["garage"]
    b = w.host.plan(title, [{"name": n, "does": d, "sample_ask": ""} for n, d in jobs])
    text = json.dumps(b["card"])
    assert "may read any public web page" in text and "put together a simple app" in text and b["consent"]["id"]
    covers = w.ports.consent.cards[b["consent"]["id"]]["covers"]
    assert covers["reads_public"] is True and covers["tries_found"] is True


def test_no_local_goal_means_a_plan_as_it_was(tmp_path, state, drawbridge):
    w = World(tmp_path, state, Ports(tmp_path, brain=_queries))
    w.ports.core = w.core
    try:
        b = w.host.plan("A garage", [{"name": "Shelves", "does": "lists shelves", "sample_ask": ""}])
        assert b["consent"]["id"] is None and "public web page" not in json.dumps(b["card"])
        assert public_read.found_why_not(w.host, b) == public_read.FOUND_CLOUD_LINE
    finally:
        w.host.stop(timeout=10)


def test_every_model_tries_the_conversation_but_one_the_catalog_says_has_no_tools(monkeypatch):
    class S:
        def __init__(self, model):
            self.model = model

        def settings(self, role):
            return self
    no_tools = next(m["id"] for m in catalog.load_catalog()["models"] if m.get("tools") is False)
    for model, says in (("qwen3.5:9b", True), ("some-model-nobody-listed:1b", True), (no_tools, False), ("", False)):
        monkeypatch.setattr(brain_mod, "_brain", lambda m=model: S(m))
        assert brain_mod.drives_tools() is says, model
    monkeypatch.setattr(brain_mod, "_brain", lambda: 1 / 0)
    assert brain_mod.drives_tools() is False
