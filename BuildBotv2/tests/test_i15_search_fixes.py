"""Lane I15 fixes after the verifier's review: the GitHub key, the search screen, the rate limits.

Every key here is an obviously fake test key; every name is made up. Nothing leaves this computer: every send
is recorded by the fake ports.
"""

from __future__ import annotations

import http.client
import json

import pytest

from toolsmith_fakes import FakePorts, World
from puffpai_core.zone3.toolsmith import agent, find

SEARCH = "https://api.github.com/search/repositories?q=dog+training+in:name,description,topics"
RUNS = "# An app\n## Quick start\n```bash\ndocker compose up -d\n```\n"
ITEMS = json.dumps({"items": [{"full_name": "ex/sit", "name": "sit", "language": "Go", "stargazers_count": 50,
                               "description": "Dog training log."}]}).encode()
JOB = {"name": "Dog training", "does": "Plans dog training", "sample_ask": ""}


def _world(tmp_path, state, *, household=()):
    ports = FakePorts(tmp_path)
    ports.household_names = lambda: list(household)
    return World(tmp_path, state, ports=ports)


def _like_http_client(sent):
    """A send that checks header values the way http.client does (a newline or a character latin-1 can't
    carry raises, with the value in the message), then answers like GitHub."""
    def send(method, url, headers, body):
        for k, v in (headers or {}).items():
            raw = str(v).encode("latin-1")             # UnicodeEncodeError, as http.client's putheader
            if b"\n" in raw or b"\r" in raw:
                raise ValueError(f"Invalid header value {raw!r}")
        sent.append((url, dict(headers or {})))
        if url.startswith("https://api.github.com/search"):
            return 200, {"X-RateLimit-Limit": "10"}, ITEMS
        if url.startswith("https://api.github.com/repos/"):
            return 200, {}, json.dumps({"full_name": "ex/sit", "language": "Go"}).encode()
        return 200, {}, RUNS.encode()
    return send


def _every_file_bytes(*roots):
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file():
                yield path, path.read_bytes()


def _pieces(key):
    return [p for p in key.replace("\n", " ").split() if len(p) >= 6] + [key]


# -- 1. a key that can't go in a header is never used, never echoed ---------------------------------------------
BAD_KEYS = {
    "newline": "ghp_" + "N3wLnKy" * 5 + "\n" + "TailPartZ9",
    "spaces": "ghp_" + "Sp4ceKy" * 5 + " " + "MoreKeyQ7",
    "unicode": "ghp_" + "Un1c0de" * 5 + "é☃" + "RestPart8",
}


@pytest.mark.parametrize("kind", sorted(BAD_KEYS))
def test_a_bad_saved_key_is_not_usable_and_never_shows_anywhere(tmp_path, state, caplog, kind):
    key = BAD_KEYS[kind]
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = key
    sent = []
    world.ports.http_send_fn = _like_http_client(sent)
    world.ports.brain = lambda system, user: None
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    link = world.link(bid)
    caplog.set_level("DEBUG")
    replies = [link._call("POST", "/web", {"url": SEARCH, "accept": "application/vnd.github+json",
                                           "keyed": True})]
    agent.plan(link, tmp_path / "cache")
    replies.append(link.build())
    assert sent and not any("Authorization" in h for _u, h in sent)              # searched without it
    b = world.host.store.read(bid)
    notes = [n for j in b["jobs"] for n in j["plan"].get("notes") or []]
    assert "The saved GitHub key is not usable, so BuildBot searched without it." in notes
    text = json.dumps(replies, ensure_ascii=False) + caplog.text
    for piece in _pieces(key):
        assert piece not in text
        for path, data in _every_file_bytes(state, tmp_path / "cache"):
            assert piece.encode("utf-8") not in data, path
            assert piece.encode("unicode_escape") not in data, path


def test_a_send_that_raises_with_the_key_in_its_message_returns_a_fixed_line(tmp_path, state):
    key = "ghp_" + "G00dShape" * 4
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = key

    def boom(method, url, headers, body):
        raise ValueError(f"Invalid header value b'Bearer {key}'")
    world.ports.http_send_fn = boom
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    got = world.link(bid)._call("POST", "/web", {"url": SEARCH, "keyed": True})
    assert got["status"] == 0 and got["said"] == "That address could not be reached."
    assert key not in json.dumps(got) and "Bearer" not in json.dumps(got)


def test_the_core_scrubs_bearer_values_and_key_shapes_from_plan_detail_and_the_build_log(tmp_path, state):
    world = _world(tmp_path, state)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    link = world.link(bid)
    fake = "ghp_" + "Scrub0ut" * 5
    link.put_plan({"jobs": [{**JOB, "plan": {"candidates": [], "detail": [f"answered 400 (Bearer {fake})",
                                                                          f"a key {fake} in a line"]}}]})
    link.now(f"Authorization: Bearer {fake}")
    for _path, data in _every_file_bytes(state):
        assert fake.encode() not in data


def test_strict_key_shape():
    from puffpai_core.zone3.toolsmith_host import service
    assert service.github_key_ok("ghp_" + "a1B2" * 9)
    assert service.github_key_ok("github_pat_" + "x" * 82)
    for bad in BAD_KEYS.values():
        assert not service.github_key_ok(bad)
    for bad in ["", "short", "ghp_" + "a" * 300, "ghp_" + "a" * 30 + "\t", " ghp_" + "a" * 30, "ghp_" + "a" * 30 + "\n",
                "ghp_" + "a" * 30 + "\x00", "ghp_" + "a" * 30 + "​"]:
        assert not service.github_key_ok(bad), repr(bad)


# -- 2. every query that can reach a search URL passes the Core's screen -----------------------------------------
NAMED_JOB = {"name": "Marisol puppy training", "does": "Plans the puppy lessons for Marisol", "sample_ask": ""}
NAMED_GOAL = "Help Marisol Quint train the new puppy"


def _recorder(urls):
    def send(method, url, headers, body):
        urls.append(url)
        if url.startswith("https://api.github.com/search"):
            return 200, {}, ITEMS
        return 200, {}, RUNS.encode()
    return send


@pytest.mark.parametrize("brain_says", [
    {"queries": ["marisol puppy", "puppy training", "Quint dog tricks", "marisol"]},
    None,                                                              # no answer: the job name's own words
])
def test_a_household_name_in_the_job_goal_or_queries_never_reaches_a_url(tmp_path, state, brain_says):
    world = _world(tmp_path, state, household=["Marisol Quint"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    world.ports.brain = lambda system, user: brain_says if "search_queries" in system else None
    bid = world.host.store.create(NAMED_GOAL, [NAMED_JOB], world.host.settings())["id"]
    agent.plan(world.link(bid), tmp_path / "cache")
    searches = [u for u in urls if "/search/" in u]
    assert searches                                                    # "puppy training" still goes out
    for url in urls:
        assert "marisol" not in url.lower() and "quint" not in url.lower(), url


def test_the_finder_searches_only_what_the_screen_kept(tmp_path):
    ports = FakePorts(tmp_path)
    ports.household_names = lambda: ["Marisol Quint"]
    urls = []
    ports.http_send_fn = _recorder(urls)
    f = find.Finder(ports, tmp_path / "c")
    f.search_job(NAMED_JOB, [["marisol", "puppy", "training", "log"], ["quint"]])
    assert urls and not any("marisol" in u or "quint" in u for u in urls)
    assert f.search(["marisol", "dog"]) == [] and find.HELD_LOG in f.log
    assert not any("marisol" in u for u in urls)


@pytest.mark.parametrize("url", [
    "https://api.github.com/search/repositories?q=marisol+dog+in:name,description,topics+stars:>=20",
    "https://api.github.com/search/repositories?q=dog+user:marisol",
    "https://api.github.com/search/code?q=quint",
    "https://github.com/search?q=Marisol&type=repositories",
])
def test_the_core_holds_a_search_url_with_a_household_name_whoever_built_it(tmp_path, state, url):
    world = _world(tmp_path, state, household=["Marisol Quint"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    from puffpai_core.zone3.toolsmith.errors import Refused
    with pytest.raises(Refused, match="never sends"):
        world.link(bid).http_get(url)
    assert urls == []
    world.link(bid).http_get(SEARCH + "+stars:>=20+archived:false+fork:false&sort=stars")   # a clean one goes
    assert len(urls) == 1


# -- 3. the key goes on two GitHub API paths only, and never on a try's read ---------------------------------------
GOOD_KEY = "ghp_" + "Fak3Test" * 5


@pytest.mark.parametrize("url,keyed,gets_key", [
    ("https://api.github.com/search/repositories?q=dog+in:name", True, True),
    ("https://api.github.com/repos/ex/sit", True, True),
    ("https://api.github.com/user/emails", True, False),
    ("https://api.github.com/user", True, False),
    ("https://api.github.com/repos/ex/sit/contents/README.md", True, False),
    ("https://api.github.com/search/code?q=dog", True, False),
    ("https://api.github.com:443/search/repositories?q=dog", True, False),
    ("https://api.github.com/search/repositories?q=dog+in:name", False, False),
    ("https://api.github.com/repos/ex/sit", False, False),
])
def test_the_key_goes_only_on_the_search_and_repository_paths_when_asked(tmp_path, state, url, keyed, gets_key):
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = GOOD_KEY
    sent = []
    world.ports.http_send_fn = _like_http_client(sent)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    world.link(bid).http_get(url, keyed=keyed)
    assert len(sent) == 1
    assert (sent[0][1].get("Authorization") == f"Bearer {GOOD_KEY}") is gets_key
    assert gets_key or "Authorization" not in sent[0][1]


@pytest.mark.parametrize("url", ["https://api.github.com/search/repositories?q=dog+in:name",
                                 "https://api.github.com/repos/ex/sit", "https://api.github.com/user/emails"])
def test_a_tries_docs_read_never_gets_the_key(tmp_path, state, url):
    from types import SimpleNamespace
    from puffpai_core.zone3.toolsmith import tries
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = GOOD_KEY
    sent = []
    world.ports.http_send_fn = _like_http_client(sent)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    tries.Try.t_read_docs(SimpleNamespace(link=world.link(bid)), {"url": url})
    assert len(sent) == 1 and "Authorization" not in sent[0][1]
    assert not world.ports.leases                                     # the vault was not even asked


def test_the_finder_asks_for_the_key_on_its_search_and_repository_reads_only(tmp_path, state):
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = GOOD_KEY
    sent = []
    world.ports.http_send_fn = _like_http_client(sent)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    f = find.Finder(world.link(bid), tmp_path / "c")
    f.search(["dog", "training"])
    f.repo_info("ex/sit")
    f.readme("ex/sit")
    keyed = {u.split("?")[0]: "Authorization" in h for u, h in sent}
    assert keyed["https://api.github.com/search/repositories"] and keyed["https://api.github.com/repos/ex/sit"]
    assert not any(v for u, v in keyed.items() if not u.startswith("https://api.github.com/"))


# -- 4. no provider package may borrow the GitHub key ------------------------------------------------------------
@pytest.mark.parametrize("pid", ["github", "GitHub", "github "])
def test_a_provider_package_named_github_is_never_loaded(tmp_path, pid):
    from puffpai_core.zone3.toolsmith_host import sender
    real = next(iter(sender.builtin_providers().values()))
    fake = json.loads(json.dumps(real))
    fake["id"] = pid
    fake["toolsmith_model"]["hosts"] = ["models.example.test"]
    assert pid not in sender._with_builtins([fake])
    folder = tmp_path / "providers" / "gh"
    folder.mkdir(parents=True)
    (folder / "puffpai.json").write_text(json.dumps(fake), encoding="utf-8")
    assert pid not in sender.load_providers(tmp_path / "providers")
    other = dict(fake, id="examplecloud")
    assert "examplecloud" in sender._with_builtins([other])          # any other new id still loads


# -- 5. GitHub's headers can't end the plan step -------------------------------------------------------------------
NOW = 1_800_000_000.0
LATER = ("GitHub asked us to slow down, so its search is skipped for the rest of this build. "
         "With a GitHub key, it allows many more.")


@pytest.mark.parametrize("headers", [
    {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "99999999999"},
    {"retry-after": "1" * 30},
    {"retry-after": "9" * 5000},
    {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "-99999999999999999999"},
    {"retry-after": "not a number"},
])
def test_absurd_rate_limit_headers_give_the_plain_line(headers):
    line = find.rate_line(headers, NOW)
    assert line == LATER or line.startswith("GitHub asked us to slow down; it lets more searches through at ")
    wait = find.rate_wait(headers, NOW)
    assert wait is None or 0 <= wait <= 24 * 3600


@pytest.mark.parametrize("headers", [{"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "99999999999"},
                                     {"Retry-After": "1" * 30}])
def test_a_search_with_absurd_headers_moves_on(tmp_path, headers):
    ports = FakePorts(tmp_path)
    ports.http_send_fn = lambda m, url, h, b: (429, headers, b"")
    said, slept = [], []
    f = find.Finder(ports, tmp_path / "c", say=said.append, clock=lambda: NOW, sleep=slept.append)
    assert f.search(["dog"]) == [] and f.rate_blocked and slept == [] and said == [LATER]


# -- 6. one wait per plan, 60 s at most --------------------------------------------------------------------------
def test_one_rate_limit_wait_per_plan_then_github_is_skipped(tmp_path):
    """Each limit lifts after its wait (the case that added up to 610 s): only the first is waited out."""
    awesome = "- [Sit](https://github.com/ex/sit) - Dog training log. `MIT` `Docker`\n"
    calls = []
    limited = [True]

    def send(method, url, headers, body):
        calls.append(url)
        if url.startswith("https://api.github.com/search"):
            limited[0] = not limited[0]
            return (200, {}, ITEMS) if limited[0] else (429, {"Retry-After": "45"}, b"")
        if "awesome-selfhosted" in url:
            return 200, {}, awesome.encode()
        return 200, {}, RUNS.encode()
    ports = FakePorts(tmp_path)
    ports.http_send_fn = send
    said, slept = [], []
    f = find.Finder(ports, tmp_path / "c", say=said.append, clock=lambda: NOW, sleep=slept.append)
    f.cap = 24
    for job in ({"name": "Dog training", "does": ""}, {"name": "Dog walks", "does": ""},
                {"name": "Puppy food", "does": ""}):
        got = f.search_job(job, [["dog", "training"], ["training", "log"]])
        assert "ex/sit" in {g["full_name"] for g in got}                 # the list still answers
    assert slept == [46.0] and sum(slept) <= find.WAIT_MAX_S             # one wait in the whole plan
    assert f.rate_blocked and sum("/search/" in u for u in calls) == 3   # limited, waited, found; limited: done
    assert len(said) == 1 and said[0].startswith("GitHub asked us to slow down; it lets more searches through at ")


def test_a_wait_longer_than_the_cap_is_never_slept(tmp_path):
    ports = FakePorts(tmp_path)
    ports.http_send_fn = lambda m, url, h, b: (429, {"Retry-After": "60"}, b"")
    slept = []
    f = find.Finder(ports, tmp_path / "c", clock=lambda: NOW, sleep=slept.append)
    assert f.search(["dog"]) == [] and f.rate_blocked and slept == []


# -- 7. a refused key: one plain line, the search once more without it, no key for the rest ----------------------
def test_a_refused_key_says_so_retries_without_it_and_stays_keyless(tmp_path, state):
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = GOOD_KEY
    sent = []

    def send(method, url, headers, body):
        sent.append((url, dict(headers)))
        if "Authorization" in headers:
            return 401, {}, b'{"message": "Bad credentials"}'
        if url.startswith("https://api.github.com/search"):
            return 200, {}, ITEMS
        return 200, {}, json.dumps({"full_name": "ex/sit"}).encode()
    world.ports.http_send_fn = send
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    said = []
    f = find.Finder(world.link(bid), tmp_path / "c", say=said.append)
    assert [i["full_name"] for i in f.search(["dog", "training"])] == ["ex/sit"]
    assert said == ["GitHub did not accept the saved GitHub key, so BuildBot searched without it."]
    assert [("Authorization" in h) for _u, h in sent] == [True, False]
    f.search(["training", "log"])
    f.repo_info("ex/sit")
    assert [("Authorization" in h) for _u, h in sent] == [True, False, False, False]   # none for the rest
    assert len(said) == 1 and f.keyless


# -- 8. private projects are never cached; /queries is capped per job ---------------------------------------------
PRIVATE = json.dumps({"items": [
    {"full_name": "ex/sit", "name": "sit", "language": "Go", "stargazers_count": 50, "description": "Dog log."},
    {"full_name": "own/hiddenkennel", "name": "hiddenkennel", "private": True, "visibility": "private",
     "stargazers_count": 90, "description": "Dog training notes."}]}).encode()


def _private_answers(m, url, h, b):
    if "/search/" in url:
        return 200, {}, PRIVATE
    return 200, {}, json.dumps({"full_name": "own/secretpens", "private": True}).encode()


def test_a_private_project_is_filtered_before_it_is_cached(tmp_path):
    ports = FakePorts(tmp_path)
    ports.http_send_fn = _private_answers
    f = find.Finder(ports, tmp_path / "c")
    assert [i["full_name"] for i in f.search(["dog"])] == ["ex/sit"]
    assert f.repo_info("own/secretpens") == {}
    files = list(_every_file_bytes(tmp_path / "c"))
    assert files
    for path, data in files:
        assert b"hiddenkennel" not in data and b"secretpens" not in data, path


def test_a_keyed_answer_never_brings_a_private_project_to_toolsmith(tmp_path, state):
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = GOOD_KEY
    world.ports.http_send_fn = _private_answers
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    link = world.link(bid)
    _s, _h, body = link.http_get(SEARCH, keyed=True)
    assert b"hiddenkennel" not in body and b"ex/sit" in body
    _s, _h, body = link.http_get("https://api.github.com/repos/own/secretpens", keyed=True)
    assert body == b""


def test_queries_are_capped_per_job(tmp_path, state):
    world = _world(tmp_path, state)
    jobs = [JOB, {"name": "Dog walks", "does": "Plans walks"}]
    bid = world.host.store.create("dog school", jobs, world.host.settings())["id"]
    link = world.link(bid)
    for _ in range(3 * len(jobs)):
        assert link.screen_queries(["dog training"]) == ["dog training"]
    assert link.screen_queries(["dog training"]) == []                  # over the cap: none kept


# -- rebalance 2. household names match whole words, after a proper fold ------------------------------------------
HOUSE = ["Mark", "Grace", "Will", "Rose", "Hope", "Marisol Quenby"]
#: Ordinary searches with a household name inside a longer word: every one is kept.
ORDINARY = ["markdown editor", "bookmark manager", "marketing calendar", "landmark guide", "trademark tracker",
            "watermark photos", "benchmark dashboard", "remarkable notes", "market prices", "willow care",
            "goodwill donations", "willpower habits", "hopeful journal", "primrose garden", "rosewood furniture",
            "roses care", "prose writing", "graceful aging", "grocery list", "meal planner", "recipe box",
            "photo library", "budget tracker", "chore chart", "marisolpuppy tracker"]
CY_A, ZWSP, SHY, ACUTE = chr(0x430), chr(0x200B), chr(0xAD), chr(0x301)
#: The verifier's disguises of "mark": each one is held.
DISGUISED = ["mar" + ZWSP + "k notes", "mar" + SHY + "k notes", "ma" + ACUTE + "rk notes", "m" + chr(0xE1) + "rk notes",
             "m" + CY_A + "rk notes", chr(0xFF2D) + "ark notes", "MARK notes", "mark-notes", "mark's notes"]


def test_ordinary_searches_with_a_name_inside_a_word_are_kept():
    from puffpai_core.zone3.toolsmith_host import knowledge
    assert knowledge.clean_topics(ORDINARY, household=HOUSE) == ORDINARY[:15]          # the screen keeps 15 a call
    assert knowledge.clean_topics(ORDINARY[15:], household=HOUSE) == ORDINARY[15:]


def test_a_household_name_as_a_whole_word_is_held_and_disguises_do_not_hide_it():
    from puffpai_core.zone3.toolsmith_host import knowledge
    for topic in DISGUISED + ["mark quenby", "quenby family", "mark homework"]:
        assert knowledge.clean_topics([topic, "homework planner"], household=["Mark Quenby"]) == \
            ["homework planner"], repr(topic)
    # a name that is a common word is held as a whole word too (the documented limit)
    assert knowledge.clean_topics(["will planning", "rose garden planner"], household=HOUSE) == []


def test_a_hidden_character_or_mixed_letters_are_never_sent_whatever_the_household():
    from puffpai_core.zone3.toolsmith_host import knowledge
    for topic in ["dog" + ZWSP + " training", "dog" + SHY + "training", "d" + chr(0x43E) + "g walks",
                  "training l" + chr(0x3BF) + "g"]:
        assert knowledge.clean_topics([topic], household=[]) == [], repr(topic)
    assert knowledge.clean_topics(["dog training", chr(0x441) + chr(0x43E) + chr(0x431) + "aka"], household=[]) \
        == ["dog training"]                                                    # Cyrillic and Latin in one word
    assert knowledge.clean_topics([chr(0x441) + chr(0x43E) + chr(0x431) + chr(0x430) + chr(0x43A) + chr(0x430)],
                                  household=[]) == []                      # plain ASCII letters only (allow-list)


@pytest.mark.parametrize("q", ["mar%E2%80%8Bk+notes", "mar%C2%ADk+notes", "ma%CC%81rk+notes", "m%C3%A1rk+notes",
                               "m%D0%B0rk+notes", "Mark+Quenby", "notes+in:name,mark"])
def test_the_core_holds_a_disguised_name_in_q_and_sends_nothing(tmp_path, state, q):
    from puffpai_core.zone3.toolsmith.errors import Refused
    world = _world(tmp_path, state, household=["Mark Quenby"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    bid = world.host.store.create("homework help", [JOB], world.host.settings())["id"]
    with pytest.raises(Refused):
        world.link(bid).http_get("https://api.github.com/search/repositories?q=" + q)
    assert urls == []


def test_a_hidden_character_anywhere_in_an_address_is_refused(tmp_path, state):
    from puffpai_core.zone3.toolsmith.errors import Refused
    world = _world(tmp_path, state)
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    for url in ["https://raw.githubusercontent.com/ex/sit%E2%80%8B/HEAD/README.md",
                "https://api.github.com/search/repositories?q=dog&sort=st%C2%ADars"]:
        with pytest.raises(Refused):
            world.link(bid).http_get(url)
    assert urls == []


@pytest.mark.parametrize("goal,job,said", [
    ("Homework help for Mark Quenby", {"name": "Mark Quenby homework", "does": "Tracks the homework"}, None),
    ("help mark with his homework", {"name": "Homework helper", "does": "Tracks the homework"},
     {"queries": ["mark homework", "homework planner"]}),
])
def test_a_name_from_the_goal_or_job_never_reaches_a_url_but_the_rest_is_searched(tmp_path, state, goal, job, said):
    world = _world(tmp_path, state, household=["Mark Quenby"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    world.ports.brain = lambda system, user: said if "search_queries" in system else None
    bid = world.host.store.create(goal, [job], world.host.settings())["id"]
    agent.plan(world.link(bid), tmp_path / "cache")
    searches = [u for u in urls if "/search/" in u]
    assert all("homework" in u for u in searches) and (searches or said is None)   # every name pair held
    for url in urls:
        assert "mark" not in url.split("?")[-1].lower() and "quenby" not in url.lower(), url


def test_a_name_run_into_another_word_is_the_known_limit_and_is_on_the_card(tmp_path, state):
    """The run-together case is allowed now (whole words only); the plan card lists it before the Yes."""
    world = _world(tmp_path, state, household=["Marisol Quenby"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    world.ports.brain = lambda system, user: None
    job = {"name": "marisolpuppy tracker", "does": "Tracks the puppy", "sample_ask": ""}
    bid = world.host.store.create("marisolpuppy tracker", [job], world.host.settings())["id"]
    agent.plan(world.link(bid), tmp_path / "cache")
    assert any("q=marisolpuppy+tracker" in u for u in urls)
    assert "GitHub searches: marisolpuppy tracker." in world.host.view(bid)["card"]["lines"]


# -- re-check b. one plain form for every address, and every part of a GitHub address screened -------------------
Q = "q=marisol+dog+in:name,description,topics"
REFUSED = [
    ("https://api.github.com//search/repositories?q=dog", "a double slash"),
    ("https://api.github.com/./search/repositories?q=dog", "a /./ segment"),
    ("https://api.github.com/x/../search/repositories?q=dog", "a /../ segment"),
    ("https://api.github.com/repos/ex/..", "a .. repository name"),
    ("https://api.github.com/search/repositories/?q=dog", "an empty last segment on a GitHub host"),
    ("https://api.github.com/%73earch/repositories?" + Q, "a percent-encoded path with a name"),
    ("https://api.github.com/search/%2573/repositories?q=dog", "a path still encoded after one decode"),
    ("https://api.github.com/search/repositories;x=1?q=dog", "a ;param"),
    ("https://api.github.com./search/repositories?" + Q, "a trailing dot on the host"),
    ("https://API.GitHub.com/search/repositories?" + Q, "an upper-case host"),
    ("https://api.github.com/search/repositories?q=dog+in:name,marisol", "an in: value that is a name"),
    ("https://api.github.com/search/repositories?Q=marisol", "an upper-case Q"),
    ("https://user@api.github.com/search/repositories?q=dog", "a user part"),
    ("http://api.github.com/search/repositories?q=dog", "not https"),
]


#: GitHub's own address parts (from its search answers): public, never screened for names (lane I15 rebalance).
PUBLIC_PARTS = [
    "https://api.github.com/search/repositories?q=dog&sort=marisol",
    "https://api.github.com/repos/marisol/puppy",
    "https://api.github.com/users/marisol",
    "https://api.github.com/repos/markedjs/marked",
    "https://raw.githubusercontent.com/marisol/puppy/HEAD/README.md",
    "https://raw.githubusercontent.com/markedjs/marked/HEAD/README.md",
    "https://github.com/marisol",
]


@pytest.mark.parametrize("url", PUBLIC_PARTS)
def test_githubs_own_address_parts_are_sent_whatever_names_they_hold(tmp_path, state, url):
    world = _world(tmp_path, state, household=["Marisol Quenby", "Mark"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    world.link(bid).http_get(url)
    assert urls == [url]


@pytest.mark.parametrize("url,why", REFUSED, ids=[w for _u, w in REFUSED])
def test_an_address_with_no_plain_form_or_a_name_anywhere_is_refused_and_nothing_sent(tmp_path, state, url, why):
    from puffpai_core.zone3.toolsmith.errors import Refused
    world = _world(tmp_path, state, household=["Marisol Quenby"])
    world.ports.keys["toolsmith.github"] = GOOD_KEY
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    with pytest.raises(Refused):
        world.link(bid).http_get(url, keyed=True)
    assert urls == [], why
    assert not world.ports.leases, why                                  # the vault was not even asked


@pytest.mark.parametrize("url,sent", [
    ("https://api.github.com/%73earch/repositories?q=dog+training+in:name,description,topics+stars:>=20",
     "https://api.github.com/search/repositories?q=dog+training+in:name,description,topics+stars:>=20"),
    ("https://raw.githubusercontent.com/ex/sit/HEAD/README.md#top",
     "https://raw.githubusercontent.com/ex/sit/HEAD/README.md"),
    ("https://docs.example.test/guide/", "https://docs.example.test/guide/"),
])
def test_only_the_canonical_form_is_sent(tmp_path, state, url, sent):
    world = _world(tmp_path, state, household=["Marisol Quenby"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    world.ports.allowed_hosts.add("docs.example.test")
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    world.link(bid).http_get(url)
    assert urls == [sent]


def test_the_finders_own_addresses_keep_their_form(tmp_path, state):
    world = _world(tmp_path, state, household=["Marisol Quenby"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    f = find.Finder(world.link(bid), tmp_path / "c")
    f.search(["dog", "training"])
    f.readme("ex/sit")
    f.repo_info("ex/sit")
    gh = find.sources()["github"]
    assert urls[0] == gh["search"].format(q="dog+training", stars=gh["min_stars"], n=gh["per_page"])
    assert "https://raw.githubusercontent.com/ex/sit/HEAD/README.md" in urls
    assert "https://api.github.com/repos/ex/sit" in urls


# -- re-check c. a /repos read with a query string never gets the key ----------------------------------------------
def test_a_repository_read_with_a_query_string_goes_without_the_key(tmp_path, state):
    world = _world(tmp_path, state)
    world.ports.keys["toolsmith.github"] = GOOD_KEY
    sent = []
    world.ports.http_send_fn = _like_http_client(sent)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    world.link(bid).http_get("https://api.github.com/repos/ex/sit?per_page=1", keyed=True)
    world.link(bid).http_get("https://api.github.com/repos/ex/sit", keyed=True)
    assert ["Authorization" in h for _u, h in sent] == [False, True]


# -- rebalance 3. the plan card lists every GitHub search before the Yes -----------------------------------------
def _planned(tmp_path, state, said, household=()):
    world = _world(tmp_path, state, household=household)
    world.ports.http_send_fn = _recorder([])
    world.ports.brain = lambda system, user: said if "search_queries" in system else None
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    world.host.store.change(bid, lambda x: x.update(planning=True))
    agent.plan(world.link(bid), tmp_path / "cache")
    return world, world.host.view(bid)


def test_the_plan_card_lists_the_github_search_words_the_core_sent(tmp_path, state):
    from puffpai_core.zone3.toolsmith_host import store
    world, b = _planned(tmp_path, state, {"queries": ["dog training", "training log"]})
    lines = b["card"]["lines"]
    i = next(n for n, ln in enumerate(lines) if ln.startswith("GitHub searches: "))
    assert lines[i] == "GitHub searches: dog training, training log."
    assert lines[i + 1] == store.GITHUB_SENT_LINE == "These words are sent to GitHub as searches."
    assert b["card"]["searches"]["words"] == ["dog training", "training log"]


def test_the_search_words_show_in_the_wizard_and_on_the_desktop_yes_card(tmp_path, state):
    from puffpai_core import wiring
    from puffpai_core.consent import card_view
    world, b = _planned(tmp_path, state, {"queries": ["dog training"]})
    want = ["GitHub searches: dog training.", "These words are sent to GitHub as searches."]
    # the terminal and browser wizard: the plan step's lines (one engine for both)
    ts = wiring.WizardToolsmith.__new__(wiring.WizardToolsmith)
    ts.app, ts.host = world.app, (lambda: world.host)
    items = ts.plan(b["id"])["items"]
    assert items[items.index(want[0]) + 1] == want[1]
    # the Yes card itself (the Desktop's Your turn, the wizard's Yes step, puffpai consent): its own lines
    seen = card_view(world.ports.consent.cards[b["consent"]["id"]])
    assert seen["lines"][seen["lines"].index(want[0]) + 1] == want[1]


def test_a_held_search_is_never_in_the_sent_list_only_in_its_own_line(tmp_path, state):
    world, b = _planned(tmp_path, state, {"queries": ["mark homework", "dog training"]}, household=["Mark Quenby"])
    assert b["card"]["searches"]["words"] == ["dog training"]
    held = "1 search was not sent because it used a name from your household: mark homework."
    assert [ln for ln in b["card"]["lines"] if "mark" in ln.lower()] == [held]


# -- a search or topic held for a household name is said on her own card, and nowhere else -----------------------
WILL_HOUSE = ["Will Quenby", "Mark", "Grace", "Rose", "Hope"]
HELD_SEARCH = "1 search was not sent because it used a name from your household: will planning."
HELD_TOPIC = "1 knowledge topic was not sent because it used a name from your household: will planning guide."


def _will_plan(tmp_path, state):
    world = _world(tmp_path, state, household=WILL_HOUSE)
    urls = []
    world.ports.http_send_fn = _recorder(urls)

    def brain(system, user):
        if "search_queries" in system:
            return {"queries": ["will planning", "estate planning documents"]}
        if "knowledge_topics" in system:
            return {"topics": ["will planning guide", "estate law basics"]}
        return None
    world.ports.brain = brain
    job = {"name": "Estate papers", "does": "Keeps the estate papers in order", "sample_ask": ""}
    bid = world.host.store.create("estate papers", [job], world.host.settings())["id"]
    link = world.link(bid)
    replies = []
    real_call = link._call
    link._call = lambda method, path, *a, **k: (lambda got: (replies.append((path, got)), got)[1])(
        real_call(method, path, *a, **k))
    agent.plan(link, tmp_path / "cache")
    link.build()
    return world, world.host.view(bid), urls, replies


def test_a_held_search_and_a_held_topic_each_get_one_plain_line_on_the_card(tmp_path, state):
    world, b, urls, _r = _will_plan(tmp_path, state)
    lines = b["card"]["lines"]
    assert HELD_SEARCH in lines and HELD_TOPIC in lines
    assert any("q=estate+planning+documents" in u for u in urls)              # the brain's other query searches
    assert b["knowledge_plan"]["topics"] == ["estate law basics"]


def test_two_held_searches_are_said_in_one_line():
    from puffpai_core.zone3.toolsmith_host import store
    assert store.held_line(["will planning", "mark notes"], "searches") == \
        "2 searches were not sent because they used a name from your household: will planning, mark notes."
    assert store.held_line(["rose garden", "hope chest"], "topics") == \
        "2 knowledge topics were not sent because they used a name from your household: rose garden, hope chest."
    assert store.held_line([], "searches") == ""


def test_the_held_lines_show_in_the_wizard_and_on_the_desktop_card(tmp_path, state):
    from puffpai_core import wiring
    from puffpai_core.consent import card_view
    world, b, _u, _r = _will_plan(tmp_path, state)
    ts = wiring.WizardToolsmith.__new__(wiring.WizardToolsmith)
    ts.app, ts.host = world.app, (lambda: world.host)
    items = ts.plan(b["id"])["items"]                       # the terminal and browser wizard's plan step (one engine)
    assert HELD_SEARCH in items and HELD_TOPIC in items
    seen = card_view(world.ports.consent.cards[b["consent"]["id"]])    # the Yes card: Desktop, wizard, puffpai consent
    assert HELD_SEARCH in seen["lines"] and HELD_TOPIC in seen["lines"]
    assert HELD_SEARCH in b["card"]["lines"]                # the Desktop Building page shows card.lines as they are


def test_the_held_words_never_leave_her_screen(tmp_path, state):
    world, b, urls, replies = _will_plan(tmp_path, state)
    for url in urls:
        assert "will" not in url.lower(), url                       # never in an address
    core_said = [got for path, got in replies if "brain" not in path]     # the brain's own answer is its own words
    assert any(path == "/build" for path, _g in replies) and any(path == "/queries" for path, _g in replies)
    assert "will planning" not in json.dumps(core_said).lower()     # never in a Core reply to Toolsmith
    assert all("will planning" not in json.dumps(s).lower() for s in world.ports.sent)   # nor to a cloud model
    for path in state.rglob("*"):                                    # the outside log, the build log, every file
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", "replace")
        text = text.replace(HELD_SEARCH, "").replace(HELD_TOPIC, "")
        assert "will planning" not in text.lower(), path            # only her card's own lines say it


# -- allow-list: owner-derived words are plain ASCII letters; hyphen-joined words compared joined ------------------
#: The verifier's letters that are not plain ASCII after NFKC: dotless i, small capital M, Cherokee M, Armenian o.
NOT_PLAIN = ["mar" + chr(0x131) + "sol dog", chr(0x1D0D) + "arisol dog", chr(0x13B7) + "arisol dog",
             "maris" + chr(0x585) + "l dog", "jard" + chr(0xED) + "n care"]


@pytest.mark.parametrize("topic", NOT_PLAIN + ["Mari-sol dog", "mari-sol-dog walks"],
                         ids=["dotless-i", "small-cap-m", "cherokee-m", "armenian-o", "accent", "hyphen-split",
                              "hyphen-split-3"])
def test_wikipedia_and_gutenberg_topics_refuse_other_letters_and_hyphen_split_names(topic):
    from puffpai_core.zone3.toolsmith_host import knowledge
    assert knowledge.clean_topics([topic, "dog training"], household=["Marisol Quenby"]) == ["dog training"]
    if topic in NOT_PLAIN:                                            # refused whatever the household
        assert knowledge.clean_topics([topic], household=[]) == []


def test_a_hyphen_joined_word_is_compared_joined_but_not_inside_words():
    from puffpai_core.zone3.toolsmith_host import knowledge
    assert knowledge.clean_topics(["rose-garden planner"], household=["Rose"]) == []      # held for "rose"
    assert knowledge.clean_topics(["rose-garden planner"], household=["Garden Quenby"]) == []
    assert knowledge.clean_topics(["self-care routine", "rose-garden planner"], household=["Mark"]) == \
        ["self-care routine", "rose-garden planner"]
    assert knowledge.clean_topics(["marisol dog"], household=["Mari-Sol"]) == []          # a hyphenated name too
    assert knowledge.clean_topics(ORDINARY[:15], household=HOUSE) == ORDINARY[:15]
    assert knowledge.clean_topics(ORDINARY[15:], household=HOUSE) == ORDINARY[15:]


@pytest.mark.parametrize("q", ["Mari-sol+dog", "mar%C4%B1sol+dog", "%E1%B4%8Darisol+dog", "%E1%8E%B7arisol+dog",
                               "maris%D6%85l+dog"],
                         ids=["hyphen-split", "dotless-i", "small-cap-m", "cherokee-m", "armenian-o"])
def test_the_core_refuses_those_cases_in_q_and_sends_nothing(tmp_path, state, q):
    from puffpai_core.zone3.toolsmith.errors import Refused
    world = _world(tmp_path, state, household=["Marisol Quenby"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    bid = world.host.store.create("dog school", [JOB], world.host.settings())["id"]
    with pytest.raises(Refused):
        world.link(bid).http_get("https://api.github.com/search/repositories?q=" + q)
    assert urls == []


def test_a_job_name_fallback_with_a_hyphen_split_name_never_reaches_a_url(tmp_path, state):
    world = _world(tmp_path, state, household=["Marisol Quenby"])
    urls = []
    world.ports.http_send_fn = _recorder(urls)
    world.ports.brain = lambda system, user: None
    job = {"name": "Mari-sol dog walks", "does": "Plans the walks", "sample_ask": ""}
    bid = world.host.store.create("dog walks", [job], world.host.settings())["id"]
    agent.plan(world.link(bid), tmp_path / "cache")
    for url in urls:
        q = url.split("?q=", 1)[-1].split("&", 1)[0].lower() if "?q=" in url else ""
        assert "mari" not in q and "sol" not in q.replace("+", " ").split(), url
