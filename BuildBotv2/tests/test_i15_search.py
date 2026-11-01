"""Lane I15: app search that finds useful things for any goal.

1. Short search queries from this computer's brain, one ask per job, screened by the Core like the knowledge
   topics (no names, numbers or personal detail); the job name's words when the ask fails.
2. Every query against GitHub and the awesome-selfhosted list, merged, each project once, then scored
   (match, stars, recent work, signs it runs here) before the brain sees the top 8.
3. Plain lines when GitHub fails: the status, the rate-limit headers and the error class in the build log; a
   rate limit that lifts within a minute is waited out once; her GitHub key (if saved) on api.github.com only.

Every network answer here is recorded; nothing leaves this computer. The live test (``-m live``) asks the real
model at 127.0.0.1:11435 only.
"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error

import pytest

from toolsmith_fakes import FakePorts, World
from puffpai_core.zone3.toolsmith import find
from puffpai_core.zone3.toolsmith_host import knowledge

SEARCH = "https://api.github.com/search/repositories?*"
RAW = "https://raw.githubusercontent.com/"
AWESOME = RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md"
RUNS = "# An app\n## Quick start\n```bash\ndocker compose up -d\n```\n"

#: Five unrelated goals (domain words live only here, in tests), what a brain might answer for each job, and
#: what the screen keeps. Each answer carries some noise the screen must drop.
GOALS = [
    ("I want a bot that helps me train my new puppy",
     {"name": "Training tracker", "does": "Tracks the puppy's training sessions and the commands it knows"},
     ["dog training", "training log", "pet tracker", "Train Biscuit in 7 days"],
     [["dog", "training"], ["training", "log"], ["pet", "tracker"]]),
    ("A Spanish study buddy",
     {"name": "Vocabulary practice", "does": "Quizzes me on Spanish words every day"},
     ["flashcards", "spaced repetition", "language learning", "Learn Spanish with a teacher in thirty days"],
     [["flashcards"], ["spaced", "repetition"], ["language", "learning"]]),
    ("Bookkeeping for my small bakery",
     {"name": "Bakery bookkeeping", "does": "Keeps the bakery's income, costs and invoices"},
     ["bookkeeping", "invoicing", "expense tracker", "inventory 2026"],
     [["bookkeeping"], ["invoicing"], ["expense", "tracker"]]),
    ("Help me look after my garden",
     {"name": "Garden care", "does": "Reminds me when to water and feed the plants"},
     ["garden planner", "plant care", "watering schedule"],
     [["garden", "planner"], ["plant", "care"], ["watering", "schedule"]]),
    ("A home media library for our family",
     {"name": "Media library", "does": "Keeps our movies, shows and music in one place"},
     ["media server", "movie library", "music streaming", "marisol movie list"],
     [["media", "server"], ["movie", "library"], ["music", "streaming"]]),
]


def _world(tmp_path, state, *, web=None, household=()):
    ports = FakePorts(tmp_path, web=web or {})
    ports.household_names = lambda: list(household)
    return World(tmp_path, state, ports=ports)


def _build(world, goal, jobs):
    return world.host.store.create(goal, jobs, world.host.settings())["id"]


# -- 1. search words from the brain --------------------------------------------------------------------------
@pytest.mark.parametrize("goal,job,said,kept", GOALS, ids=[g[1]["name"] for g in GOALS])
def test_queries_for_any_goal_come_from_the_brain_and_the_core_screen(tmp_path, state, goal, job, said, kept):
    world = _world(tmp_path, state, household=["Marisol"])
    asked = []

    def brain(system, user):
        asked.append((system, json.loads(user)))
        return {"queries": said} if "search_queries" in system else None
    world.ports.brain = brain
    bid = _build(world, goal, [job])
    f = find.Finder(world.link(bid), tmp_path / "c")
    jobs = find.jobs_for(goal, [job])
    got = f.search_words(jobs, goal)
    assert got == {0: kept}
    assert len(asked) == 1 and asked[0][0] == find.QUERIES_SYSTEM           # one small ask per job
    assert asked[0][1]["job"] == job["name"] and asked[0][1]["does"] == job["does"]
    for q in got[0]:
        assert 1 <= len(q) <= 4 and all(w.isalpha() for w in q)


def test_one_ask_per_job_and_each_job_keeps_its_own_queries(tmp_path, state):
    world = _world(tmp_path, state)
    answers = iter([{"queries": ["recipe box", "meal planner"]}, {"queries": ["shopping list"]}])
    world.ports.brain = lambda system, user: next(answers)
    jobs = [{"name": "Recipes", "does": "Keeps recipes"}, {"name": "Groceries", "does": "Keeps the list"}]
    bid = _build(world, "Kitchen helper", jobs)
    got = find.Finder(world.link(bid), tmp_path / "c").search_words(find.jobs_for("Kitchen helper", jobs),
                                                                     "Kitchen helper")
    assert got == {0: [["recipe", "box"], ["meal", "planner"]], 1: [["shopping", "list"]]}


def test_no_answer_or_no_screen_falls_back_to_the_job_names_words(tmp_path, state):
    world = _world(tmp_path, state)
    job = {"name": "Seed swap ledger", "does": "Keeps who has which seeds"}
    bid = _build(world, "garden club", [job])
    jobs = find.jobs_for("garden club", [job])
    PAIRS = [["seed", "swap"], ["swap", "ledger"]]          # D85: the name's own pairs, only when the brain gave none
    world.ports.brain = None                                                    # the ask fails
    assert find.Finder(world.link(bid), tmp_path / "c").search_words(jobs, "g") == {0: PAIRS}
    world.ports.brain = lambda s, u: {"queries": ["seed library"]}
    world.ports.screen_missing = True                                           # no privacy screen: none sent
    assert find.Finder(world.link(bid), tmp_path / "c").search_words(jobs, "g") == {0: PAIRS}
    bare = FakePorts(tmp_path, brain=lambda s, u: {"queries": ["seed library"]})
    bare.screen_queries = None                                                  # a link with no screen route
    f = find.Finder(bare, tmp_path / "d")
    assert f.search_words(jobs, "g") == {0: PAIRS}
    assert f.search(["seed", "swap"]) == [] and f.log == [find.HELD_LOG]        # and nothing unscreened is sent


def test_the_screen_drops_a_planted_name_numbers_and_sentences(tmp_path, state):
    world = _world(tmp_path, state, household=["Marisol Quint"])
    bid = _build(world, "A movie night bot", [{"name": "Movie night", "does": "Picks a movie"}])
    link = world.link(bid)
    kept = link.screen_queries(["movie picker", "marisol movie list", "quint family films", "Top 10 movies",
                                "watch list 123-45-6789", "a sentence that is much too long to be a query"])
    assert kept == ["movie picker"]
    world.ports.household_names = lambda: (_ for _ in ()).throw(RuntimeError("locked"))
    assert link.screen_queries(["movie picker"]) == []                         # can't read the household: none


# -- 2. merge, rank -----------------------------------------------------------------------------------------
def test_merge_keeps_each_project_once_with_every_sources_facts():
    got = find.merge([
        {"full_name": "Acme/Shelf", "name": "Shelf", "listed": True, "topics": ["Books"], "runs_hint": True,
         "language": ""},
        {"full_name": "acme/shelf", "name": "shelf", "stargazers_count": 900, "language": "Python",
         "topics": ["library"]},
        {"full_name": "other/thing", "name": "thing"},
        {"full_name": "acme/shelf", "name": "shelf", "stargazers_count": 900, "language": "Python"},
        {"name": "no address"},
    ])
    assert [g["full_name"].lower() for g in got] == ["acme/shelf", "other/thing"]
    shelf = got[0]
    assert shelf["hits"] == 3 and shelf["stargazers_count"] == 900 and shelf["language"] == "Python"
    assert shelf["listed"] and shelf["runs_hint"] and shelf["topics"] == ["Books", "library"]
    assert got[1]["hits"] == 1 and "runs_hint" not in got[1]


def test_search_job_asks_every_query_of_both_sources_and_merges(tmp_path):
    awesome = ("## Pets\n- [PawLog](https://github.com/ex/pawlog) - Training log for dogs. `MIT` `Docker`\n"
               "- [Kennel](https://github.com/ex/kennel) - A dog training diary. `MIT` `PHP`\n")
    asked = []

    def search(url):
        q = url.split("?q=", 1)[1].split("+in:", 1)[0]
        asked.append(q)
        return 200, json.dumps({"items": [{"full_name": "ex/pawlog", "name": "pawlog", "language": "Go",
                                           "stargazers_count": 300}]})
    f = find.Finder(FakePorts(tmp_path, web={SEARCH: search, AWESOME: (200, awesome)}), tmp_path / "c")
    got = f.search_job({"name": "Puppy school", "does": ""}, [["dog", "training"], ["training", "log"]])
    assert asked == ["dog+training", "training+log", "puppy+school"]   # D88: a thin round adds the name's words
    names = {g["full_name"]: g for g in got}
    assert set(names) == {"ex/pawlog", "ex/kennel"}
    assert names["ex/pawlog"]["hits"] >= 3 and names["ex/pawlog"]["language"] == "Go"
    assert names["ex/pawlog"]["runs_hint"] and names["ex/pawlog"]["listed"]


def test_score_puts_the_match_first_then_stars_recent_work_and_runs_here():
    now = time.time()
    year = 365 * 86400
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))   # noqa: E731
    q = [["seed", "library"]]
    good = {"full_name": "a/seedkeeper", "name": "seedkeeper", "description": "A seed library you host.",
            "stargazers_count": 40, "language": "Python", "pushed_at": iso(now - 30 * 86400)}
    popular = {"full_name": "b/big", "name": "big", "description": "A photo library.", "stargazers_count": 90000,
               "language": "Go", "pushed_at": iso(now - 30 * 86400)}
    assert find.score(good, q, now) > find.score(popular, q, now)              # the match beats stars
    old = dict(good, pushed_at=iso(now - 4 * year))
    assert find.score(good, q, now) > find.score(old, q, now)                  # recent work counts
    hinted = dict(good, topics=["self-hosted"])
    assert find.score(hinted, q, now) > find.score(good, q, now)               # signs it runs here
    phone = dict(good, language="Swift")
    assert find.score(good, q, now) > find.score(phone, q, now)
    assert find.match_score({"full_name": "x/worldmonitor", "name": "worldmonitor"}, [["world"]]) == 4.0


def test_the_brain_sees_the_top_eight_by_score_not_the_first_found(tmp_path):
    seen = []

    def brain(system, user):
        if "rank_apps" in system:
            seen.append(json.loads(user)["projects"])
        return None
    f = find.Finder(FakePorts(tmp_path, brain=brain), tmp_path / "c")
    pool = [{"full_name": f"z/off{n}", "name": f"off{n}", "description": "something else",
             "stargazers_count": 50000} for n in range(10)]
    pool += [{"full_name": f"a/quilt{n}", "name": f"quilt-pattern-{n}", "description": "Quilt patterns.",
              "stargazers_count": 25} for n in range(3)]
    ranked = f.rank({"name": "Quilt patterns", "does": ""}, pool, [["quilt", "pattern"]])
    assert [r["name"] for r in ranked[:3]] == ["quilt-pattern-0", "quilt-pattern-1", "quilt-pattern-2"]
    assert len(seen[0]) == find.RANK_MAX == 8 and seen[0][0]["name"] == "quilt-pattern-0"
    assert len(ranked) == len(pool)                                             # never removes one


def test_awesome_rows_best_match_first_with_the_heading_as_topic():
    text = ("## Pets\n"
            "- [Vet](https://github.com/ex/vet) - Pet health records. `MIT` `PHP`\n"
            "## Tools\n"
            "- [Clicker](https://github.com/ex/clicker) - A training timer. `MIT` `PHP`\n"
            "## Training\n"
            "- [Sit](https://github.com/ex/sit) - Plans and a log. `MIT` `Docker`\n")
    rows = find.awesome_rows(text, ["training", "log"], 5)
    assert [r["full_name"] for r in rows] == ["ex/sit"]       # every word of the query (lane I18b): one is no match
    rows = find.awesome_rows(text, ["training"], 5)
    assert [r["full_name"] for r in rows] == ["ex/sit", "ex/clicker"]           # its heading counts more
    assert rows[0]["topics"] == ["Training"] and rows[0]["runs_hint"] and "runs_hint" not in rows[1]
    assert [r["full_name"] for r in find.awesome_rows(text, ["pet"], 5)] == ["ex/vet"]     # by its heading too


# -- 3. plain reasons when GitHub fails ------------------------------------------------------------------------
def _search_ports(tmp_path, answers, awesome="# none\n"):
    """Search answers in turn: (status, headers, body); README reads all run."""
    seq = list(answers)
    calls = []

    def send(method, url, headers, body):
        calls.append((url, dict(headers or {})))
        if url.startswith("https://api.github.com/search"):
            return seq.pop(0) if len(seq) > 1 else seq[0]
        if url == AWESOME:
            return 200, {}, awesome.encode()
        return 200, {}, RUNS.encode()
    ports = FakePorts(tmp_path)
    ports.http_send_fn = send
    return ports, calls


ITEMS = json.dumps({"items": [{"full_name": "ex/sit", "name": "sit", "language": "Go", "stargazers_count": 50,
                               "description": "Dog training log."}]}).encode()


def test_a_rate_limit_within_a_minute_is_waited_out_once(tmp_path):
    now = 1_800_000_000.0
    limited = (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(now) + 20),
                     "X-RateLimit-Limit": "10"}, b'{"message": "API rate limit exceeded"}')
    ports, calls = _search_ports(tmp_path, [limited, (200, {}, ITEMS)])
    slept, said = [], []
    f = find.Finder(ports, tmp_path / "c", say=said.append, clock=lambda: now, sleep=slept.append)
    got = f.search(["dog", "training"])
    assert [i["full_name"] for i in got] == ["ex/sit"] and not f.rate_blocked and said == []
    assert slept == [21.0] and f.searches == 1
    assert sum("search/repositories" in u for u, _h in calls) == 2              # asked once more, once
    assert any("answered 403" in line and "x-ratelimit-remaining: 0" in line for line in f.log)


def test_a_second_limit_or_a_long_one_moves_on_with_the_time(tmp_path):
    now = 1_800_000_000.0
    reset = int(now) + 600
    limited = (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}, b"")
    ports, _calls = _search_ports(tmp_path, [limited])
    slept, said = [], []
    f = find.Finder(ports, tmp_path / "c", say=said.append, clock=lambda: now, sleep=slept.append)
    assert f.search(["dog"]) == [] and f.rate_blocked and slept == []
    at = time.strftime("%H:%M", time.localtime(reset))
    assert said == [f"GitHub asked us to slow down; it lets more searches through at {at}. With a GitHub key, "
                    "it allows many more."]
    # retried once, limited again: moves on
    soon = (429, {"Retry-After": "5"}, b"")
    ports, calls = _search_ports(tmp_path, [soon, soon])
    said.clear()
    f = find.Finder(ports, tmp_path / "d", say=said.append, clock=lambda: now, sleep=slept.append)
    assert f.search(["dog"]) == [] and f.rate_blocked and slept == [6.0]
    assert said[0].startswith("GitHub asked us to slow down; it lets more searches through at ")


def test_after_a_rate_limit_the_list_still_answers_every_query(tmp_path):
    awesome = "- [Sit](https://github.com/ex/sit) - Dog training log. `MIT` `Docker`\n" \
              "- [Map](https://github.com/ex/walkmap) - Walk routes on a map. `MIT` `Docker`\n"
    ports, _calls = _search_ports(tmp_path, [(403, {}, b"")], awesome=awesome)
    said = []
    f = find.Finder(ports, tmp_path / "c", say=said.append)
    got = f.search_job({"name": "Dog walks", "does": ""}, [["dog", "training"], ["walk", "map"]])
    assert {g["full_name"] for g in got} == {"ex/sit", "ex/walkmap"} and f.rate_blocked
    assert said == ["GitHub asked us to slow down, so its search is skipped for the rest of this build. "
                    "With a GitHub key, it allows many more."]


@pytest.mark.parametrize("status,line", [
    (500, "GitHub could not be reached (it answered 500)"),
    (503, "GitHub could not be reached (it answered 503)"),
])
def test_github_busy_is_its_status(tmp_path, status, line):
    ports, _c = _search_ports(tmp_path, [(status, {}, b"")])
    said = []
    f = find.Finder(ports, tmp_path / "c", say=said.append)
    assert f.search(["dog"]) == [] and said == [line] and f.log == [f"api.github.com answered {status}"]


def test_a_refused_query_and_an_unreadable_answer_go_to_the_build_log(tmp_path):
    ports, _c = _search_ports(tmp_path, [(422, {}, b"{}")])
    f = find.Finder(ports, tmp_path / "c")
    assert f.search(["dog"]) == []
    assert f.log == ["api.github.com answered 422", "api.github.com would not run the search \"dog\" (it answered 422)"]
    ports, _c = _search_ports(tmp_path, [(200, {}, b"not json")])
    f = find.Finder(ports, tmp_path / "d")
    assert f.search(["dog"]) == [] and f.log == ["api.github.com: its answer to the search could not be read"]


@pytest.mark.parametrize("exc,line", [
    (socket.timeout("timed out"), "GitHub could not be reached: the connection timed out"),
    (urllib.error.URLError(TimeoutError(10060, "A connection attempt failed")),
     "GitHub could not be reached: the connection timed out"),
    (urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")),
     "GitHub could not be reached: its address could not be looked up (is this computer online?)"),
    (ConnectionRefusedError(10061, "refused"), "GitHub could not be reached: the connection was refused"),
    (ConnectionResetError(10054, "reset"), "GitHub could not be reached: the connection was cut off"),
    (urllib.error.URLError(ssl.SSLError("bad handshake")), "GitHub could not be reached: the secure connection failed"),
    (OSError("something odd"), "GitHub could not be reached"),
])
def test_no_answer_through_the_core_says_why_and_logs_the_class(tmp_path, state, exc, line):
    world = _world(tmp_path, state)

    def down(url, **kw):
        raise exc
    world.ports.http_get = down
    bid = _build(world, "dog school", [{"name": "Dog training", "does": "Plans training"}])
    said = []
    f = find.Finder(world.link(bid), tmp_path / "c", say=said.append)
    assert f.search(["dog"]) == [] and said == [line]
    assert f.log == [f"api.github.com: no answer ({type(exc).__name__}: That address could not be reached"
                     + (line.split("reached", 1)[1] + ".)" if ":" in line else ".)")]


def test_her_test_case_one_readme_timeout_is_a_note_with_its_class(tmp_path, state):
    """The live test: one README read got no answer, and the plan said only "GitHub could not be reached".
    Now the note says why and the build log keeps the class."""
    world = _world(tmp_path, state)
    real = world.ports.http_get

    def flaky(url, **kw):
        if url.endswith("/ex/slow/HEAD/README.md"):
            raise TimeoutError(10060, "A connection attempt failed because the connected party did not respond")
        return real(url, **kw)
    world.ports.http_get = flaky
    world.ports.web = {SEARCH: (200, json.dumps({"items": [
        {"full_name": "ex/slow", "name": "slow", "language": "Go", "stargazers_count": 90,
         "description": "Dog training log."},
        {"full_name": "ex/fine", "name": "fine", "language": "Go", "stargazers_count": 80,
         "description": "Dog training planner."}]})),
        AWESOME: (200, "# none\n"), RAW + "*": (200, RUNS)}
    job = {"n": 0, "name": "Dog training", "does": "Plans dog training", "sample_ask": "", "pasted": []}
    bid = _build(world, "dog school", [job])
    notes = []
    f = find.Finder(world.link(bid), tmp_path / "c", say=notes.append)
    plan = f.plan_job(job, [["dog", "training"]], [], {})
    assert "GitHub could not be reached: the connection timed out" in notes
    assert any("raw.githubusercontent.com: no answer (TimeoutError" in d for d in plan["detail"])
    assert [c["full_name"] for c in plan["candidates"]][-1] == "ex/fine"


# -- her GitHub key: api.github.com only, never logged ---------------------------------------------------------
KEY = "ghp_" + "Q7x" * 12


def test_a_saved_github_key_goes_to_api_github_com_only_and_is_never_logged(tmp_path, state, caplog):
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = KEY
    sent = []

    def send(method, url, headers, body):
        sent.append((url, dict(headers)))
        if url.startswith("https://api.github.com/search"):
            return 200, {"X-RateLimit-Limit": "30", "X-RateLimit-Remaining": "29"}, ITEMS
        return 200, {}, RUNS.encode()
    world.ports.http_send_fn = send
    job = {"n": 0, "name": "Dog training", "does": "Plans dog training", "sample_ask": "", "pasted": []}
    bid = _build(world, "dog school", [job])
    f = find.Finder(world.link(bid), tmp_path / "c")
    caplog.set_level("DEBUG")
    plan = f.plan_job(job, [["dog", "training"]], [], {})
    api = [h for u, h in sent if u.startswith("https://api.github.com/")]
    other = [h for u, h in sent if not u.startswith("https://api.github.com/")]
    assert api and all(h.get("Authorization") == f"Bearer {KEY}" for h in api)
    assert other and not any("Authorization" in h or KEY in json.dumps(h) for h in other)
    assert f.cap == 24                                                          # a key lets more searches through
    assert all(lease["ended"] and lease["hosts"] == ("api.github.com",) for lease in world.ports.leases.values())
    # never in the plan, the build record, the logs or any file in the bot's folder
    assert KEY not in json.dumps(plan) and KEY not in " ".join(f.log) and KEY not in caplog.text
    for path in state.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path


def test_no_key_saved_or_the_vault_locked_searches_unkeyed(tmp_path, state):
    world = _world(tmp_path, state)
    sent = []
    world.ports.http_send_fn = lambda m, url, headers, body: (sent.append(dict(headers)), (200, {}, ITEMS))[1]
    bid = _build(world, "dog school", [{"name": "Dog training", "does": "Plans"}])
    find.Finder(world.link(bid), tmp_path / "c").search(["dog"])
    world.ports.keys["toolsmith.github"] = KEY
    world.ports.vault_locked = True
    find.Finder(world.link(bid), tmp_path / "d").search(["dog"])
    assert len(sent) == 2 and not any("Authorization" in h for h in sent)


# -- the real model on this computer (11435 only; run with -m live) --------------------------------------------
@pytest.mark.live
def test_live_queries_for_five_goals(state, freshtable, request):
    if "live" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("live test: run with -m live")
    from puffpai_core.zone2.ollama import OllamaRuntime
    from zone2_doubles import make_brain
    address, model = "http://127.0.0.1:11435", "qwen3.5:9b"
    rt = OllamaRuntime(address, timeout_s=600)
    try:
        here = [m["name"] for m in rt.models()]
    except OSError:
        pytest.skip(f"no model server answers at {address}")
    if model not in here:
        pytest.skip(f"{model} is not installed; this test never downloads one")
    brain, _, _ = make_brain(state, freshtable, rt, settings={"address": address, "model": model})

    class Link:
        context = ""

        def brain_json(self, system, user):
            return brain.ask_json(system, user)

        def screen_queries(self, queries):
            return knowledge.clean_topics(queries, lambda t: t, household=[], context=self.context)
    link = Link()
    out = []
    for goal, job, _said, _kept in GOALS:
        link.context = f"{goal} {job['name']} {job['does']}"
        got = find.Finder(link, state / "c").search_words(find.jobs_for(goal, [job]), goal)[0]
        out.append(f"{job['name']}: " + "; ".join(" ".join(q) for q in got))
        assert 1 <= len(got) <= 4 and all(1 <= len(q) <= 4 for q in got)
    print("\n--- live queries (" + model + ") ---\n" + "\n".join(out) + "\n---")
