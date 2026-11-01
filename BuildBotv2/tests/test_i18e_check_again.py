"""Lane I18e: "Check again" retries a build's unfinished jobs (held, not working yet, nothing found).

Her Yes is reused only while the consent rules allow it (same plan sha, the Yes still live, same model and cost
cap); otherwise ONE new Yes card asks again, covering no more than the plan did. Jobs that work stay; a job tried
again starts its tries over with her hold-card answers kept. Fake ports, no network, no real key, no bot port."""

import json

import pytest

from puffpai_core.zone3.toolsmith import agent
from puffpai_core.zone3.toolsmith.errors import Refused
from puffpai_core.zone3.toolsmith_host import sender, service, store
from puffpai_core.zone3.toolsmith_host.service import Paused
from test_toolsmith_build import make_world
from toolsmith_fakes import World, _Done, end_held

KEY = "sk-ant-" + "Q" * 40                    # made up: nothing sends it


class Recorder:
    """Toolsmith's process, recorded: it starts and ends at once, so the jobs stay as the Core left them."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str]] = []

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.runs.append((do, build_id))
        return _Done(0)


CANDS = [{"full_name": "example/news-a", "name": "news-a", "runtime": "python"},
         {"full_name": "example/news-b", "name": "news-b", "runtime": "node"}]


def _world(tmp_path, state, *, cloud=False):
    w = World(tmp_path, state)
    w.runner = Recorder()
    w.host.runner = w.runner
    if cloud:
        prov = w.host.providers()["anthropic"]
        (state / "settings").mkdir(parents=True, exist_ok=True)
        (state / "settings" / "toolsmith.json").write_text(json.dumps({
            "where": "cloud", "provider": "anthropic", "model": "claude-opus-5-5", "cost_cap_usd": 5.0,
            "consent": {"sha": sender.consent_sha(prov), "at": "2026-10-27T20:52:20+00:00", "by": "owner"}}),
            encoding="utf-8")
        w.ports.keys = {"toolsmith.anthropic": KEY}
    return w


def _started(w, states=("held", "working"), *, legacy=False):
    """A started build with one job per state: its plan, its plan card granted, its Yes recorded."""
    st = w.host.settings()
    jobs = [{"name": f"Job {i}", "does": "Shows the news", "sample_ask": "What is new?"} for i in range(len(states))]
    b = w.host.store.create("daily news", jobs, st)
    w.host.store.change(b["id"], lambda x: x.update(model={"where": st["where"], "provider": st.get("provider"),
                                                           "model": st.get("model")}))
    plan = {"jobs": [{**j, "plan": {"candidates": CANDS[:1] if i == 0 else CANDS[1:]}} for i, j in enumerate(jobs)]}
    b = w.host.store.set_plan(b["id"], plan, st, "Anthropic")
    covers = {"build": b["id"], "plan_sha": b["plan_sha"], "card": b["card"],
              "install_hosts": b["envelope"]["install_hosts"]}
    if st["where"] == "cloud":                         # as a real cloud plan card holds them (D121)
        covers.update(reads_public=True, tries_found=True)
    cid = w.ports.consent_request("package", store.bridge_target(b["id"]), covers, bound={"plan_sha": b["plan_sha"]})
    w.ports.consent.say_yes(cid)

    def fn(x):
        x["consent"]["id"] = cid
        x["yes"] = {"given": True, "consent_id": cid, "sha": x["plan_sha"], "use": {}}
        for j, s in zip(x["jobs"], states):
            j["run"].update(state=s, line="Toolsmith stopped before this job finished, so it is held.",
                            tried=[{"name": "news-a", "ref": "github:example/news-a", "why": "it stopped"}])
        if legacy:
            x.pop("agreed", None)
    return w.host.store.change(b["id"], fn), cid


def _settings(state, **changes):
    p = state / "settings" / "toolsmith.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data.update(changes)
    p.write_text(json.dumps(data), encoding="utf-8")


# -- the Yes is reused only when the rules allow it -------------------------------------------

def test_same_plan_same_live_yes_same_model_starts_again_with_no_new_card(tmp_path, state):
    w = _world(tmp_path, state)
    b, cid = _started(w)
    cards = len(w.ports.consent.cards)
    got = w.host.again(b["id"])
    assert w.runner.runs == [("build", b["id"])] and len(w.ports.consent.cards) == cards
    held, working = got["jobs"]
    assert held["run"]["state"] == "planned" and held["run"]["tried"] == [] and held["run"]["line"] == ""
    assert held["run"]["lines"][-1] == service.AGAIN_SAY
    assert working["run"]["state"] == "working" and working["run"]["tried"]          # it stays as it was
    assert got["yes"]["consent_id"] == cid and got["again"]["state"] == "started"
    assert got["again"]["jobs"] == [held["id"]]


@pytest.mark.parametrize("state_", ["held", "not_yet", "nothing_found"])
def test_every_unfinished_state_is_tried_again(tmp_path, state, state_):
    w = _world(tmp_path, state)
    b, _cid = _started(w, (state_,))
    assert w.host.again(b["id"])["jobs"][0]["run"]["state"] == "planned"


def test_a_yes_taken_back_asks_again_with_one_card_that_widens_nothing(tmp_path, state):
    w = _world(tmp_path, state)
    b, cid = _started(w, ("working", "held"))
    w.ports.consent.records.pop(cid)                     # taken back
    got = w.host.again(b["id"])
    assert w.runner.runs == []                           # nothing starts before her Yes
    rec = got["again"]
    assert rec["state"] == "asking" and rec["jobs"] == [b["jobs"][1]["id"]]
    card = w.ports.consent.cards[rec["consent_id"]]
    assert card["scope"] == {"kind": "package", "target": store.bridge_target(b["id"])}
    assert card["bound"] == {"plan_sha": b["plan_sha"]}
    cov = card["covers"]
    assert cov["plan_sha"] == b["plan_sha"] and cov["build"] == b["id"] and cov["again"] == rec["jobs"]
    # only the job tried again: news-b (node), never news-a's python hosts, never a host the plan did not list
    assert cov["install_hosts"] == ["github.com", "registry.npmjs.org"]
    assert set(cov["install_hosts"]) <= set(b["envelope"]["install_hosts"]) and "knowledge_hosts" not in cov
    assert cov["card"]["title"] == "Check again?" and "Job 0" not in " ".join(cov["card"]["lines"])
    assert "\u2014" not in json.dumps(cov["card"], ensure_ascii=False)
    # a second click while the card waits raises no second card
    w.host.again(b["id"])
    assert len([c for c in w.ports.consent.cards.values() if (c["covers"] or {}).get("again")]) == 1
    # her Yes on it (the Desktop posts start with it, as for a plan card) starts the job; that Yes is the build's now
    new = rec["consent_id"]
    w.ports.consent.say_yes(new)
    got = w.host.start(b["id"], new)
    assert w.runner.runs == [("build", b["id"])]
    assert got["yes"]["consent_id"] == new and got["consent"]["id"] == new and got["yes"]["before"] == [cid]
    assert [j["run"]["state"] for j in got["jobs"]] == ["working", "planned"]


def test_a_changed_cost_cap_asks_again_and_the_card_counts_what_was_spent(tmp_path, state):
    w = _world(tmp_path, state, cloud=True)
    b, _cid = _started(w)
    w.host._ledger(b["id"]).add("anthropic", "claude-opus-5-5", {"in": 1, "out": 1}, 1.25, "earlier")
    _settings(state, cost_cap_usd=9.0)
    got = w.host.again(b["id"])
    card = w.ports.consent.cards[got["again"]["consent_id"]]["covers"]["card"]
    assert "up to $9 for this build" in card["sentence"] and "Anthropic" in card["sentence"]
    assert "This build has spent $1.25 of that already." in card["lines"]
    assert card["lines"][0] == "Toolsmith's model or its cost cap changed since your Yes."
    assert w.runner.runs == []


def test_a_changed_model_asks_again(tmp_path, state):
    w = _world(tmp_path, state, cloud=True)
    b, _cid = _started(w)
    _settings(state, model="claude-sonnet-5")
    assert w.host.again(b["id"])["again"]["state"] == "asking" and w.runner.runs == []


def test_the_same_cloud_choice_reuses_the_yes(tmp_path, state):
    w = _world(tmp_path, state, cloud=True)
    b, _cid = _started(w)
    w.host.again(b["id"])
    assert w.runner.runs == [("build", b["id"])]
    # D68 (lane I19a): the key the Check again borrowed is kept for the run it started, not dropped and lent again.
    assert list(w.host._senders) == [b["id"]] and w.host._senders[b["id"]]._handle in w.ports.leases


def test_a_build_from_before_this_lane_reuses_only_a_local_yes(tmp_path, state):
    w = _world(tmp_path, state)
    b, _cid = _started(w, legacy=True)
    w.host.again(b["id"])
    assert w.runner.runs == [("build", b["id"])]
    w2 = _world(tmp_path / "c", tmp_path / "c-state", cloud=True)
    b2, _ = _started(w2, legacy=True)                # its cost cap was never recorded: ask again
    assert w2.host.again(b2["id"])["again"]["state"] == "asking"


def test_a_yes_that_is_not_this_cards_is_refused(tmp_path, state):
    w = _world(tmp_path, state)
    b, cid = _started(w)
    w.ports.consent.records.pop(cid)
    w.host.again(b["id"])
    with pytest.raises(PermissionError):
        w.host.again(b["id"], "c_other")
    assert w.runner.runs == []


# -- plain refusals ----------------------------------------------------------------------------

def test_refused_while_toolsmith_works_on_the_build(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)

    class Running:
        def wait(self, _t):
            return False
    w.host._children[b["id"]] = Running()
    with pytest.raises(Refused, match="already working"):
        w.host.again(b["id"])


def test_refused_in_safe_mode(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)
    w.host._paused = "A safety check failed."
    with pytest.raises(Paused):
        w.host.again(b["id"])
    assert w.runner.runs == []


def test_a_locked_vault_with_a_cloud_model_says_unlock_your_vault_first(tmp_path, state):
    w = _world(tmp_path, state, cloud=True)
    b, _ = _started(w)
    w.host.end_keys(b["id"])                          # the stopped build's process ended: its keys went back (D68)
    w.ports.vault_locked = True
    with pytest.raises(Refused) as e:
        w.host.again(b["id"])
    assert str(e.value).startswith("Unlock your vault first")
    assert w.runner.runs == [] and w.host.view(b["id"])["jobs"][0]["run"]["state"] == "held"


def test_nothing_to_check_again_and_no_yes_yet(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w, ("working",))
    with pytest.raises(Refused, match="nothing to check again"):
        w.host.again(b["id"])
    fresh = w.host.store.create("x", [{"name": "A"}], w.host.settings())
    with pytest.raises(Refused, match="not started yet"):
        w.host.again(fresh["id"])


def test_a_launch_that_fails_holds_the_jobs_again_with_why(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)

    class Broken:
        def launch(self, *a, **kw):
            raise OSError("no")
    w.host.runner = Broken()
    got = w.host.again(b["id"])
    run = got["jobs"][0]["run"]
    assert run["state"] == "held" and run["line"] == service.AGAIN_NOT_STARTED_LINE.format(why="OSError")
    assert got["jobs"][1]["run"]["state"] == "working"


def test_the_owner_route(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)
    c = w.app.test_client()
    owner = {"Authorization": "Bearer " + w.core.tokens.issue_owner_key()}
    r = c.post(f"/v1/toolsmith/builds/{b['id']}/again", json={}, headers=owner)
    assert r.status_code == 200 and r.json["again"]["state"] == "started"
    r = c.post(f"/v1/toolsmith/builds/{b['id']}/again", json={}, headers=owner)
    assert r.status_code == 409 and r.json["error"]                     # its jobs are planned now: refused plainly
    tok = w.core.tokens.issue_toolsmith(b["id"])
    r = c.post(f"/v1/toolsmith/builds/{b['id']}/again", json={}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)


# -- each job's tries and her hold-card answers -------------------------------------------------

def test_toolsmith_runs_only_the_jobs_tried_again(monkeypatch):
    info = {"build": {"id": "b", "goal": "g", "yes": True, "knowledge": False,
                      "jobs": [{"id": "b-0", "run": {"state": "working"}}, {"id": "b-1", "run": {"state": "planned"}},
                               {"id": "b-2"}]},
            "settings": {"where": "local", "budgets": {}, "timing": {}}}

    class Link:
        def build(self):
            return info
    ran = []
    monkeypatch.setattr(agent.Builder, "_run_job", lambda self, j: ran.append(j["id"]))
    agent.Builder(Link()).run()
    assert sorted(ran) == ["b-1", "b-2"]


def _answered(w, b, job_id, approved=True):
    hold_cid = w.ports.consent_request("package", store.bridge_target(b["id"]) + ".holds-0", {})
    w.ports.consent.say_yes(hold_cid)
    w.ports.consent.records[hold_cid]["card_sha"] = "s" * 64      # the store keeps the card's sha
    ref = "github:example/news-a"
    rec = {"consent_id": hold_cid, "card_sha": "s" * 64, "title": "Let Toolsmith try news-a?", "names": ["news-a"],
           "answer": {"state": "answered", "try": [ref] if approved else [],
                      "skipped": [] if approved else [{"name": "news-a", "ref": ref, "why": "you said No to trying it"}]},
           "approved": {ref: {"tree_sha": "t" * 64, "files": {"bin/x.exe": "f" * 64}}} if approved else {}}
    w.host.store.change(b["id"], lambda x: x.setdefault("holds_answered", {}).update({job_id: rec}))
    return hold_cid, ref


def test_her_yes_on_a_hold_card_counts_again(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)
    jid = b["jobs"][0]["id"]
    hold_cid, ref = _answered(w, b, jid)
    w.host.again(b["id"])
    t = w.host.trials
    assert t._approved[(b["id"], jid, ref)] == {"consent_id": hold_cid, "tree_sha": "t" * 64,
                                                "files": {"bin/x.exe": "f" * 64}}
    assert t.holds_state(b["id"], jid)["try"] == [ref]
    assert t.holds_ask(b["id"], jid)["consent_id"] == ""           # no second card for it


def test_her_no_on_a_hold_card_stands(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)
    jid = b["jobs"][0]["id"]
    _cid, _ref = _answered(w, b, jid, approved=False)
    w.host.again(b["id"])
    assert w.host.trials.holds_state(b["id"], jid)["try"] == []
    assert w.host.trials.holds_ask(b["id"], jid)["consent_id"] == ""
    assert not w.host.trials._approved


def test_a_hold_yes_taken_back_is_not_used(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)
    jid = b["jobs"][0]["id"]
    hold_cid, _ref = _answered(w, b, jid)
    w.ports.consent.records.pop(hold_cid)
    w.host.again(b["id"])
    assert not w.host.trials._approved and (b["id"], jid) not in w.host.trials._hold_cards


def test_her_hold_answer_is_kept_in_the_build(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)
    jid = b["jobs"][0]["id"]
    ref = "github:example/news-a"
    item = {"ref": ref, "name": "news-a", "tree_sha": "t" * 64, "files": {"a.bin": "f" * 64}}
    w.host.trials._hold_cards[(b["id"], jid)] = {"consent_id": "c_h", "card_sha": "s", "title": "T",
                                                 "names": ["news-a"], "items": {ref: item}, "answer": None}
    w.ports.consent_state = lambda cid: {"status": "granted", "card_sha": "s"}
    w.host.store.change(b["id"], lambda x: x.update(hold_ticks={"c_h": [ref]}))
    assert w.host.trials.holds_state(b["id"], jid)["try"] == [ref]
    kept = w.host.store.read(b["id"])["holds_answered"][jid]
    assert kept["consent_id"] == "c_h" and kept["approved"] == {ref: {"tree_sha": "t" * 64, "files": {"a.bin": "f" * 64}}}


# -- the verifier's fix round: terms of the card, terms of the live Yes, and a hold card's own record ---------

def test_a_card_yes_never_launches_on_terms_changed_while_it_waited(tmp_path, state):
    w = _world(tmp_path, state, cloud=True)
    b, _cid = _started(w)
    _settings(state, cost_cap_usd=9.0)                   # a card naming $9 with claude-opus-5-5
    c2 = w.host.again(b["id"])["again"]["consent_id"]
    assert w.ports.consent.cards[c2]["covers"]["terms"]["cost_cap_usd"] == 9.0
    _settings(state, cost_cap_usd=500.0, model="claude-other")      # changed while the card waits
    w.ports.consent.say_yes(c2)
    got = w.host.again(b["id"])                          # answered elsewhere: picked up here
    assert w.runner.runs == []
    assert got["again"]["state"] == "asking" and got["again"]["consent_id"] != c2     # one fresh card instead
    new = w.ports.consent.cards[got["again"]["consent_id"]]["covers"]
    assert new["terms"] == {"where": "cloud", "provider": "anthropic", "model": "claude-other", "cost_cap_usd": 500.0}
    assert "up to $500" in new["card"]["sentence"]
    with pytest.raises(PermissionError):                 # the Desktop's start with the old card: refused
        w.host.start(b["id"], c2)
    assert w.runner.runs == []


def test_reuse_compares_with_the_terms_of_the_live_yes(tmp_path, state):
    w = _world(tmp_path, state, cloud=True)
    b, cid = _started(w)                                 # first Yes at $5
    w.ports.consent.records.pop(cid)                     # taken back
    _settings(state, cost_cap_usd=1.0)
    c2 = w.host.again(b["id"])["again"]["consent_id"]
    w.ports.consent.say_yes(c2)
    got = w.host.start(b["id"], c2)
    assert w.runner.runs == [("build", b["id"])] and got["agreed"]["cost_cap_usd"] == 1.0
    w.host.store.change(b["id"], lambda x: [j["run"].update(state="held") for j in x["jobs"]
                                            if j["run"]["state"] == "planned"])
    _settings(state, cost_cap_usd=5.0)                   # the taken-back Yes's cap: not what the live Yes says
    cards = len(w.ports.consent.cards)
    assert w.host.again(b["id"])["again"]["state"] == "asking"
    assert len(w.ports.consent.cards) == cards + 1 and len(w.runner.runs) == 1
    # at the live Yes's own terms ($1), it is reused with no card
    w.host.store.change(b["id"], lambda x: x.update(again={**x["again"], "state": "started"}))
    _settings(state, cost_cap_usd=1.0)
    w.host.again(b["id"])
    assert len(w.runner.runs) == 2 and len(w.ports.consent.cards) == cards + 1


def test_a_hold_answer_counts_only_from_that_jobs_own_hold_card(tmp_path, state):
    w = _world(tmp_path, state)
    b, cid = _started(w)
    jid = b["jobs"][0]["id"]
    _answered(w, b, jid)
    # an injected id (the plan's own Yes, granted) is not a hold card: nothing counts
    w.host.store.change(b["id"], lambda x: x["holds_answered"][jid].update(consent_id=cid))
    w.host.again(b["id"])
    assert not w.host.trials._approved and (b["id"], jid) not in w.host.trials._hold_cards
    # the job's own hold card, but files or card changed since: its card_sha no longer matches
    w2 = _world(tmp_path / "y", tmp_path / "y-state")
    b2, _ = _started(w2)
    jid2 = b2["jobs"][0]["id"]
    _answered(w2, b2, jid2)
    w2.host.store.change(b2["id"], lambda x: x["holds_answered"][jid2].update(card_sha="0" * 64))
    w2.host.again(b2["id"])
    assert not w2.host.trials._approved
    # another job's hold card (holds-0) stored under job 1: refused
    w3 = _world(tmp_path / "z", tmp_path / "z-state")
    b3, _ = _started(w3, ("working", "held"))
    _answered(w3, b3, b3["jobs"][1]["id"])
    w3.host.again(b3["id"])
    assert not w3.host.trials._approved


def test_a_hold_yes_that_expired_is_not_used(tmp_path, state):
    w = _world(tmp_path, state)
    b, _ = _started(w)
    jid = b["jobs"][0]["id"]
    hold_cid, _ref = _answered(w, b, jid)
    w.ports.consent_state = lambda c: {"status": "expired"} if c == hold_cid else {"status": "granted"}
    w.host.again(b["id"])
    assert not w.host.trials._approved


# -- end to end: a held job works after "Check again", under the same Yes -------------------------

def test_a_held_job_works_after_check_again(tmp_path, state):
    procs = []
    w = make_world(tmp_path, state, procs)
    try:
        b = w.host.plan("daily news headlines", [{"name": "Daily headlines", "does": "Shows news headlines"}])
        w.ports.consent.say_yes(b["consent"]["id"])
        w.ports.read_page_fn = lambda url: (_ for _ in ()).throw(TypeError("reader changed shape"))
        done = w.host.start(b["id"], b["consent"]["id"])
        assert done["jobs"][0]["status"] == "held"
        cards = len(w.ports.consent.cards)
        w.ports.read_page_fn = None
        got = w.host.again(b["id"])
        assert len(w.ports.consent.cards) == cards           # her Yes was reused: no new card
        assert got["jobs"][0]["status"] == "working", got["jobs"][0]["run"]
    finally:
        w.host.stop(timeout=10)
        for pr in procs:
            end_held(pr)
