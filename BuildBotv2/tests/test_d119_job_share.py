"""D119: on a cloud build, each job starts no new try once its own spend has reached its share of her cap.

Three unrelated goals. The company is a stub and each job's cost a test's number; no real key, nothing is sent.
"""

from __future__ import annotations

import json
import re

import pytest

from puffpai_core.wizard import lines
from puffpai_core.zone3.toolsmith import find
from puffpai_core.zone3.toolsmith_host import sender
from toolsmith_fakes import FakePorts, Script, World, find_reply, tool

KEY = "sk-ant-" + "T" * 40                         # made up: nothing real is reached
COMPANY = "api.anthropic.com"
IN_USD = 4.0 / 1_000_000                           # claude-opus-5-5, the default model's input price
GOALS = {
    "recipe": ("A recipe box and a shopping helper", "pasta", "groceries"),
    "garden": ("A garden helper and a compost log", "seedling", "compost"),
    "home": ("A home inventory and a repair log", "basement", "repair"),
}
STARTS = "\n## Run\n\n```\nnpm install\nnpm start\n```\n"


def _item(name, word):
    return {"full_name": f"ex/{name}", "name": name, "stargazers_count": 500, "description": f"All about {word}.",
            "language": "JavaScript", "default_branch": "main", "topics": []}


class Company:
    """Anthropic's API as a stub: *usd* says what one call costs for each job, by the job's name. Each try downloads,
    then gives up twice (a give_up before anything ran is sent back once)."""

    def __init__(self, usd):
        self.usd, self.tries = usd, []

    def __call__(self, body):
        req = json.loads(body)
        text = json.dumps(req["messages"], ensure_ascii=False)
        job = next(name for name in self.usd if name in text)
        usage = {"input_tokens": round(self.usd[job] / IN_USD), "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        look = find_reply(req, usage)
        if look:                                       # the job's look (D121): it tries the plan's apps, then finishes
            return look
        turns = sum(1 for m in req["messages"] if m["role"] == "assistant")
        if turns == 0:
            self.tries.append(re.search(r"The candidate: ([\w-]+)", text).group(1))
        name, args = (("download", {}), ("give_up", {"reason": "no page"}), ("give_up", {"reason": "no page"}))[min(turns, 2)]
        return {"content": [{"type": "tool_use", "id": f"t{turns}", "name": name, "input": args}],
                "stop_reason": "tool_use", "usage": usage}


def _brain(system, user):
    if system.startswith("TASK=search_queries"):
        return {"queries": [json.loads(user)["job"].lower() + " alpha"]}
    return {"fits": True} if system.startswith("TASK=fits_") else None


def _web(words, company):
    items = {w: [_item(f"{w}-{n}", w) for n in "abc"] for w in words}

    def send(method, url, headers, body):
        if url.startswith(f"https://{COMPANY}/"):
            return 200, {}, json.dumps(company(body)).encode()
        if url.startswith("https://api.github.com/search/repositories"):
            got = next((v for w, v in items.items() if f"={w}+" in url), [])
            return 200, {}, json.dumps({"items": got}).encode()
        m = re.match(r"https://raw\.githubusercontent\.com/ex/(\w+)-\w/[^/]+/README\.md$", url)
        return (200, {}, f"# {m.group(1)}\n{STARTS}".encode()) if m else (404, {}, b"")
    return send


def _build(tmp_path, state, words, *, usd=None, cap=10, cloud=True, brain=_brain):
    """Plan these jobs and say Yes: ``(world, company, the build when it ended)``. *words*: one job per word."""
    company = Company(dict(zip(words, usd or [0.0] * len(words))))
    ports = FakePorts(tmp_path, brain=brain)
    ports.http_send_fn = _web(words, company)
    w = World(tmp_path, state, ports)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# A project\n", encoding="utf-8")
    ports.fixture_repo = repo
    if cloud:
        p = sender.load_providers()["anthropic"]
        w.host.choose({"where": "cloud", "provider": "anthropic", "consent_sha": sender.consent_sha(p), "cost_cap_usd": cap})
        ports.keys["toolsmith.anthropic"] = KEY
    else:
        ports.local_replies = Script([{"content": [tool("give_up", f"g{n}", reason="no page")], "stop": "tool_use"}
                                      for n in range(40)])
    b = w.host.plan("A bot", [{"name": w_, "does": f"handles {w_}", "sample_ask": ""} for w_ in words])
    ports.consent.say_yes(b["consent"]["id"])
    return w, company, w.host.start(b["id"], b["consent"]["id"])


def _run(b, word):
    return next(j for j in b["jobs"] if j["name"] == word)["run"]


def _tried(run):
    return [t["name"] for t in run["tried"]]


@pytest.mark.parametrize("goal", sorted(GOALS))
def test_a_job_stops_at_its_share_and_the_other_job_keeps_its_own(goal, tmp_path, state):
    _title, dear, cheap = GOALS[goal]
    # Cap $10, two jobs: $5 each. The dear job's look (2 turns, $2) and first try (3 turns, $3) make $5, its share, so
    # it asks nothing more: no next turn and no next try (D121).
    w, company, b = _build(tmp_path, state, [dear, cheap], usd=[1.0, 0.01])
    try:
        run = _run(b, dear)
        assert run["state"] == "not_yet" and _tried(run) == [f"{dear}-a"]
        assert f"{dear}-b" not in company.tries
        assert run["line"].startswith("Toolsmith stopped trying apps for this job: it spent about $5.00, which reached "
                                      "its $5 share of your $10 limit. The other jobs keep their own shares. Check again "
                                      "on Building gives this job a new share and starts its apps over; the whole build "
                                      f"still stops at $10. Not tried: {dear}-b and {dear}-c.")
        assert f"{dear}-a: no page." in run["line"]
        assert run["trying"] is None
        # The other job was not touched by what the first one spent.
        other = _run(b, cheap)
        assert _tried(other) == [f"{cheap}-a", f"{cheap}-b", f"{cheap}-c"]
        assert "share" not in other["line"]
        assert b["cost"]["usd"] < 10                                       # the sender's cap was never reached
        assert chr(0x2014) not in run["line"]
    finally:
        w.host.stop(timeout=10)


@pytest.mark.parametrize("goal", sorted(GOALS))
def test_a_job_that_used_less_gives_nothing_to_the_others(goal, tmp_path, state):
    """Each job is held to its own spend. Cap $6, three jobs: $2 each. The third spends almost nothing, and the two
    dear ones still stop at their own $2, whichever job runs first."""
    _title, one, two = GOALS[goal]
    w, company, b = _build(tmp_path, state, [one, two, "ledger"], usd=[0.5, 0.5, 0.01], cap=6)
    try:
        for word in (one, two):                                            # 2 look turns and 2 try turns at $0.50
            run = _run(b, word)
            assert _tried(run) == [f"{word}-a"], run
            assert run["line"].startswith("Toolsmith stopped trying apps for this job: it spent about $2.00, which "
                                          "reached its $2 share of your $6 limit.")
        assert _tried(_run(b, "ledger")) == ["ledger-a", "ledger-b", "ledger-c"]
    finally:
        w.host.stop(timeout=10)


def test_a_share_that_is_not_whole_dollars_reads_plainly():
    assert "its $1.67 share of your $5 limit" in find.share_line(1.7, 5 / 3, 5, [])


def test_a_try_already_running_stops_at_the_share(tmp_path, state):
    """D121 changes D119 here: a try no longer runs past its share. Its next turn does not start."""
    words = ["pasta", "groceries"]
    w, company, b = _build(tmp_path, state, words, usd=[2.0, 0.01])        # 2 look turns and 1 try turn pass $5
    try:
        run = _run(b, "pasta")
        assert _tried(run) == ["pasta-a"] and company.tries.count("pasta-a") == 1
        assert "it spent about $6.00, which reached its $5 share" in run["line"] and run["state"] == "not_yet"
        assert run["tried"][0]["why"].endswith("this job reached its share of your limit")
    finally:
        w.host.stop(timeout=10)


def test_one_job_has_no_share_and_the_senders_cap_decides(tmp_path, state):
    w, company, b = _build(tmp_path, state, ["compost"], usd=[1.0], cap=5)
    try:
        run = _run(b, "compost")
        assert run["state"] == "held" and sender.CAP_LINE.replace("{cap}", "5") in run["line"]
        assert "share" not in run["line"]
    finally:
        w.host.stop(timeout=10)


def test_a_cloud_job_with_nothing_to_try_still_takes_a_share(tmp_path, state):
    """D121 changes D119 here: a cloud job with no app can spend, since it looks. Cap $8, two jobs: $4 each."""
    w, company, b = _build(tmp_path, state, ["compost", "zzzqqq"], usd=[0.5, 0.0], cap=8)
    try:
        run = _run(b, "compost")
        assert _tried(run) == ["compost-a", "compost-b"] and "reached its $4 share of your $8 limit" in run["line"]
    finally:
        w.host.stop(timeout=10)


def test_a_local_build_has_no_share(tmp_path, state):
    w, company, b = _build(tmp_path, state, ["seedling", "repair"], cloud=False)
    try:
        for word in ("seedling", "repair"):
            run = _run(b, word)
            assert _tried(run) == [f"{word}-a", f"{word}-b", f"{word}-c"]
            assert "share" not in run["line"] and "spent" not in run["line"]
        assert company.tries == []
    finally:
        w.host.stop(timeout=10)


def test_what_the_job_knows_is_only_a_number_on_the_reply(tmp_path, state):
    """The model never reads the cost: it is on the reply to Toolsmith, never in what is sent next."""
    seen = []
    w, company, b = _build(tmp_path, state, ["pasta", "groceries"], usd=[1.0, 0.01])
    try:
        for s in w.ports.sent:
            if s["url"].startswith(f"https://{COMPANY}/"):
                seen.append(s["body"].decode() if isinstance(s["body"], bytes) else str(s["body"]))
        assert seen and not any("cost_usd" in x for x in seen)
    finally:
        w.host.stop(timeout=10)


# -- the Prove step and the Done step say it too --------------------------------------------------------------------
LINE = ("Toolsmith stopped trying apps for this job: it spent about $7.00, which reached its $5 share of your $10 "
        "limit. The other jobs keep their own shares. Check again on Building gives this job a new share and starts "
        "its apps over; the whole build still stops at $10. Not tried: seed-c. seed-a: no page. seed-b: no page.")
RESULT = {"name": "Seeds", "state": "not_yet", "reason": LINE,
          "tried": [{"name": "seed-a", "why": "no page"}, {"name": "seed-b", "why": "no page"}]}


def test_the_prove_step_says_why_it_stopped_with_each_app_tried():
    said = lines.prove_lines([RESULT])
    assert said[0] == "Seeds: not working yet. Toolsmith tried seed-a, seed-b:"
    assert said[1:3] == ["seed-a: no page.", "seed-b: no page."]
    assert said[3].startswith("Toolsmith stopped trying apps for this job: it spent about $7.00")
    assert said[3].endswith("the whole build still stops at $10. Not tried: seed-c.")


def test_the_done_step_says_why_it_stopped():
    (line,) = lines.job_result_lines([RESULT])
    assert line.startswith("Seeds: not working yet: Toolsmith stopped trying apps for this job: it spent about $7.00")
    assert line.endswith("the whole build still stops at $10.")


def test_other_lines_on_those_steps_stay_as_they_were():
    plain = {"name": "Seeds", "state": "not_yet", "reason": "No app worked yet. seed-a: no page.",
             "tried": [{"name": "seed-a", "why": "no page"}]}
    assert lines.prove_lines([plain]) == ["Seeds: not working yet. Toolsmith tried seed-a:", "seed-a: no page."]
    assert lines.job_result_lines([plain]) == ["Seeds: not working yet: seed-a, the app Toolsmith tried, does not work yet."]


def test_the_share_line_says_one_app_left_and_many():
    assert find.share_line(1.5, 1.25, 5, ["a"]).endswith("Not tried: a.")
    assert find.share_line(1.5, 1.25, 5, ["a", "b", "c"]).endswith("Not tried: a, b and c.")
    assert find.share_line(1.5, 1.25, 5, []).endswith("still stops at $5.")
