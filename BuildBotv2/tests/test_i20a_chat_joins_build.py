"""Lane I20a (D77): a new ask in chat while a build she started in the same chat is still open joins that
build, unless she says the new one comes first. Three unrelated goals; a stub Toolsmith host over the real
build store, so no Toolsmith process, no network and no keys."""

import threading
import time
import types

import pytest

from zone2_doubles import FakeRuntime, call, make_brain
from puffpai_core import wiring
from puffpai_core.api import app as api_app
from puffpai_core.zone3.toolsmith_host import store as store_mod

GOALS = [("a recipe box for my family", "add a shopping list"),
         ("invoices for a small shop", "it should email receipts"),
         ("a birdwatching log", "with photos")]


class Consent:
    """The consent store's shape chat uses (get, wait, expire). *answer* "yes" grants, "no" declines.
    *during(cid)*: runs while a card waits, like her typing in chat before she answers."""

    def __init__(self, answer="yes"):
        self.answer, self.status, self.during, self.n = answer, {}, None, 0

    def new(self, build_id):
        self.n += 1
        cid = f"card-{self.n}"
        self.status[cid] = "pending"
        return cid

    def get(self, cid):
        return {"status": self.status.get(cid, "pending"), "card": {"lines": [cid]}, "card_sha": "s", "floor": False}

    def wait(self, cid, timeout_s=None):
        if self.during is not None:
            during, self.during = self.during, None
            during(cid)
        if self.status.get(cid) == "pending":
            self.status[cid] = "granted" if self.answer == "yes" else "declined"
        return self.get(cid)

    def expire(self, cid, line):
        self.status[cid] = "expired"

    def pending(self):
        return [c for c, s in self.status.items() if s == "pending"]


class Host:
    """A stub Toolsmith host over the real build store: every job gets one app to try."""

    def __init__(self, state, consent):
        self.store = store_mod.Builds(state)
        self.consent, self.live, self.started, self.replaced = consent, set(), [], []

    def draft(self, goal, jobs):
        b = self.store.create(goal, jobs, {"where": "local"})
        self.store.change(b["id"], lambda x: x.update(planning=False))
        time.sleep(1.01)                         # build ids are made to the second
        return self.view(b["id"])

    def plan_build(self, bid):
        cid = self.consent.new(bid)

        def fn(x):
            x["jobs"] = [{"id": f"{bid}-{n}", "n": n, "name": j["name"], "does": j["does"],
                          "plan": {"candidates": [{"ref": "example/app", "name": "app"}]},
                          "run": {"state": "planned", "line": "", "trying": None, "tried": []}}
                         for n, j in enumerate(x["jobs_in"])]
            x.update(plan_sha="sha-" + bid, planning=False, consent={"id": cid, "said": ""},
                     card={"lines": [f"{j['name']}: one app to try." for j in x["jobs_in"]]})
        self.store.change(bid, fn)
        return self.view(bid)

    def view(self, bid):
        b = self.store.read(bid)
        if b is None:
            raise KeyError(bid)
        for j in b["jobs"]:
            j["status"] = store_mod.job_status(b, j)
        return b

    def running(self, bid):
        return bid in self.live

    def start(self, bid, cid):
        def fn(x):
            x["yes"] = {"given": True, "consent_id": cid}
            for j in x["jobs"]:
                j["run"]["state"] = "building"
        self.store.change(bid, fn)
        self.live.add(bid)
        self.started.append(bid)
        return self.view(bid)

    def replace(self, old, new):
        cid = self.store.end_replaced(old, new)
        if cid:
            self.consent.expire(cid, store_mod.REPLACED_LINE)
        self.replaced.append((old, new))
        return cid is not None

    def finish(self, bid):
        def fn(x):
            for j in x["jobs"]:
                j["run"].update(state="working", line="Works.")
        self.store.change(bid, fn)
        self.live.discard(bid)


@pytest.fixture
def chat(state, monkeypatch):
    consent = Consent()
    host = Host(state, consent)
    monkeypatch.setattr(api_app, "start_toolsmith", lambda app: host)
    notices = types.SimpleNamespace(posted=[])
    notices.post = lambda package, body: notices.posted.append((package, body))
    core = types.SimpleNamespace(consent=consent, notices=notices)
    ask = wiring.chat_toolsmith(core, None, wait_s=1, every_s=0.02)
    return types.SimpleNamespace(ask=ask, host=host, consent=consent, core=core)


def _until(fn, s=10.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def _ctx(cards, said, conversation="main", first=False):
    return {"notify": cards.append, "say_later": said.append, "conversation": conversation, "first": first}


def _closed(host, bid):
    return (host.store.read(bid) or {}).get("chat", {}).get("closed") is True


@pytest.mark.parametrize("goal,more", GOALS)
def test_a_second_ask_joins_the_running_build_then_is_planned_when_it_ends(chat, goal, more):
    cards, said = [], []
    got = chat.ask(goal, _ctx(cards, said))
    assert got["ok"] is True and len(cards) == 1
    bid = got["build"]
    again = chat.ask(more, _ctx(cards, said))
    assert again["ok"] is True and again["build"] == bid and again.get("joined") is True
    assert again["said"].startswith("Added to the build that is running: ") and "(building)" in again["said"]
    assert len(cards) == 1 and len(chat.host.store.all()) == 1      # no second build, no second card
    assert chat.host.store.read(bid)["chat"]["adds"] == [more]
    assert chat.host.started == [bid]
    # When it ends, what she added is planned with the build's goal: its own Yes, because it needs an app.
    chat.host.finish(bid)
    assert _until(lambda: len(chat.host.started) == 2), chat.host.store.all()
    follow = chat.host.store.read(chat.host.started[1])
    assert follow["goal"] == f"{goal}. {more}" and len(follow["jobs"]) == 1
    assert any(t.startswith("Toolsmith finished.") for t in said)
    assert wiring.CHAT_NEXT.format(words=more) in said and wiring.CHAT_FOLLOW_CARD in said
    assert _closed(chat.host, bid)
    assert "\u2014" not in " ".join(said)


@pytest.mark.parametrize("goal,more", GOALS)
def test_first_starts_a_new_build_and_the_running_one_keeps_going(chat, goal, more):
    cards, said = [], []
    bid = chat.ask(goal, _ctx(cards, said))["build"]
    got = chat.ask(more, _ctx(cards, said, first=True))
    assert got["ok"] is True and got["build"] != bid and len(cards) == 2
    assert got["said"].startswith("Started a new build first, as you asked. The other build stays as it is: ")
    assert chat.host.running(bid) and not chat.host.store.read(bid).get("replaced_by")
    assert chat.host.store.read(bid)["chat"]["adds"] == []


@pytest.mark.parametrize("goal,more", GOALS)
def test_after_the_build_ended_a_new_ask_makes_a_new_build(chat, goal, more):
    cards, said = [], []
    bid = chat.ask(goal, _ctx(cards, said))["build"]
    chat.host.finish(bid)
    assert _until(lambda: _closed(chat.host, bid))
    got = chat.ask(more, _ctx(cards, said))
    assert got["ok"] is True and got["build"] != bid and not got.get("joined")
    assert not got["said"].startswith("Added")
    assert chat.host.started == [bid, got["build"]]


def test_a_build_from_the_wizard_or_apps_add_is_never_joined(chat):
    b = chat.host.plan_build(chat.host.draft("a recipe box for my family", [wiring.chat_job("a recipe box")])["id"])
    chat.host.start(b["id"], b["consent"]["id"])                   # started outside chat: no chat mark
    cards, said = [], []
    got = chat.ask("add a shopping list", _ctx(cards, said))
    assert got["ok"] is True and got["build"] != b["id"] and not got.get("joined")


def test_another_conversation_is_not_joined(chat):
    cards, said = [], []
    bid = chat.ask("a birdwatching log", _ctx(cards, said, "main"))["build"]
    got = chat.ask("invoices for a small shop", _ctx(cards, said, "other"))
    assert got["build"] != bid and not got.get("joined")


@pytest.mark.parametrize("goal,more", GOALS)
def test_an_ask_while_the_plan_waits_for_her_yes_makes_one_plan_with_one_card(chat, goal, more):
    cards, said, second = [], [], {}
    chat.consent.during = lambda cid: second.update(got=chat.ask(more, _ctx(cards, said)))
    first = chat.ask(goal, _ctx(cards, said))
    assert first == {"ok": False, "error": wiring.CHAT_REPLACED}
    got = second["got"]
    assert got["ok"] is True and got["said"].startswith("Added to the plan that was waiting for your Yes: ")
    old, new = [b["id"] for b in chat.host.store.all()]
    assert chat.host.replaced == [(old, new)] and got["build"] == new
    assert chat.host.store.read(old)["replaced_by"] == new
    b = chat.host.store.read(new)
    assert b["goal"] == f"{goal}. {more}" and len(b["jobs"]) == 2       # her first job and the added one
    assert chat.host.started == [new] and chat.consent.pending() == []  # one Yes, for the one plan


def test_said_no_to_the_first_plan_then_a_new_ask_is_a_new_build(state, monkeypatch):
    consent = Consent("no")
    host = Host(state, consent)
    monkeypatch.setattr(api_app, "start_toolsmith", lambda app: host)
    ask = wiring.chat_toolsmith(types.SimpleNamespace(consent=consent, notices=None), None, wait_s=1, every_s=0.02)
    assert ask("a recipe box for my family", {})["error"] == wiring.CHAT_SAID_NO
    consent.answer = "yes"
    host.store.change(host.store.all()[0]["id"], lambda x: [j["run"].update(state="said_no") for j in x["jobs"]])
    got = ask("add a shopping list", {})
    assert got["ok"] is True and not got.get("joined") and len(host.store.all()) == 2


def test_the_brain_passes_first_only_when_it_is_true(state, freshtable):
    rt = FakeRuntime(call("ask_toolsmith", job="add a shopping list", first=True), {"text": "Started."})
    brain, _, _ = make_brain(state, freshtable, rt)
    seen = []
    brain.toolsmith = lambda job, ctx: (seen.append(ctx.get("first")), {"ok": True, "said": "ok"})[1]
    brain.turn("add a shopping list, do this first")
    tool = next(t["function"] for t in rt.calls[0]["tools"] if t["function"]["name"] == "ask_toolsmith")
    assert tool["parameters"]["properties"]["first"]["type"] == "boolean"
    assert "do this first" in tool["parameters"]["properties"]["first"]["description"]
    rt2 = FakeRuntime(call("ask_toolsmith", job="add a shopping list"), {"text": "Added."})
    brain2, _, _ = make_brain(state / "b", freshtable, rt2)
    brain2.toolsmith = lambda job, ctx: (seen.append(ctx.get("first")), {"ok": True, "said": "ok"})[1]
    brain2.turn("add a shopping list")
    assert seen == [True, False]


def test_two_asks_at_once_while_a_build_runs_both_join_it(chat):
    cards, said = [], []
    bid = chat.ask("a recipe box for my family", _ctx(cards, said))["build"]
    got = []
    ts = [threading.Thread(target=lambda w=w: got.append(chat.ask(w, _ctx(cards, said))))
          for w in ("add a shopping list", "with a meal plan")]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert all(g.get("joined") and g["build"] == bid for g in got) and len(got) == 2
    assert sorted(chat.host.store.read(bid)["chat"]["adds"]) == ["add a shopping list", "with a meal plan"]
