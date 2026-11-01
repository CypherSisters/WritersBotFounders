"""Lane I21b (D85), from the owner's test5 chat build: clean job names and search words for ANY chat ask, GitHub's
limit kept and said on the card, a card sentence that ends at a word, README rules that read the common forms,
and a plain reason for a project that is a part of another app.

Three unrelated chat asks (a recipe box, small-shop invoices, a birdwatching log) and the test5 drawing ask.
No network: every answer comes from a fake link in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai_core import wiring
from puffpai_core.zone3.toolsmith import find, readme_steps
from puffpai_core.zone3.toolsmith_host import store

F = "```"


class Link:
    """Toolsmith's link as a double: GitHub search answers, READMEs, the brain's queries and the Core's screen."""

    def __init__(self, *, queries=None, results=0, status=200, headers=None, readmes=None, clock=None):
        self.queries, self.results, self.status = queries, results, status
        self.headers = headers if headers is not None else {"x-ratelimit-limit": "10", "x-ratelimit-remaining": "9"}
        self.readmes = readmes or {}
        self.clock = clock
        self.searched: list[str] = []
        self.sent_at: list[float] = []

    def http_get(self, url, *, accept="", max_bytes=0, keyed=False):
        if url.startswith("https://api.github.com/search/"):
            q = url.split("?q=", 1)[1].split("+in:", 1)[0]
            self.searched.append(q.replace("+", " "))
            if self.clock is not None:
                self.sent_at.append(self.clock())
            items = [{"full_name": f"ex/{q.replace('+', '-')}-{n}", "name": f"{q}-{n}", "language": "Python",
                      "stargazers_count": 50} for n in range(self.results)]
            return self.status, dict(self.headers), json.dumps({"items": items}).encode()
        for name, text in self.readmes.items():
            if f"/{name}/" in url and url.endswith("/README.md"):
                return 200, {}, text.encode()
        return 404, {}, b""

    def brain_json(self, system, user):
        if "search_queries" in system:
            return {"queries": self.queries} if self.queries is not None else None
        return None

    def screen_queries(self, queries):
        return list(queries)


class Clock:
    def __init__(self):
        self.t = 1_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


ASKS = [
    ("add a recipe box that suggests meals from what is in my fridge", "Recipe box",
     ["recipe manager", "meal suggestion", "fridge ingredients recipes"]),
    ("add small-shop invoices which email receipts to customers", "Small-shop invoices",
     ["invoice generator", "receipt email", "small business invoicing"]),
    ("add a birdwatching log with photos and song recordings", "Birdwatching log",
     ["bird sightings log", "birdwatching journal", "bird song recordings"]),
    ("Add a drawing tutorial tool that gives step-by-step guides for anime and cartoon styles, including "
     "techniques like eyes, hair and hands", "Drawing tutorial tool",
     ["anime drawing steps", "cartoon style tutorials", "step by step guide generator"]),
]


def _searched(tmp_path, text, queries, results=0):
    clock = Clock()
    link = Link(queries=queries, results=results, clock=clock)
    f = find.Finder(link, tmp_path / "c", clock=clock, sleep=clock.sleep)
    jobs = find.jobs_for(text, [wiring.chat_job(text)])
    phrases = f.search_words(jobs, text)
    f.search_job(jobs[0], phrases[0])
    return link, f, jobs[0], phrases[0]


# -- 1. chat names and "does" ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,name,_q", ASKS, ids=[a[1] for a in ASKS])
def test_a_chat_name_ends_where_the_subject_ends(text, name, _q):
    job = wiring.chat_job(text)
    assert job["name"] == name
    assert not job["does"].lower().startswith(("add ", "install ", "get "))
    assert job["does"].split()[0].lower() not in ("add", "install", "get")


def test_vague_heads_and_her_own_words():
    assert wiring.chat_job("get me something to keep recipes with photos")["name"] == "Keep recipes"
    assert wiring.chat_job("add my shopping list")["name"] == "My shopping list"
    assert wiring.chat_job("make a todo list")["name"] == "Todo list"           # "to" inside "todo" is no cut
    assert wiring.chat_job("add a toolbox for woodworking")["name"] == "Toolbox"


# -- 2. search words ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,name,queries", ASKS, ids=[a[1] for a in ASKS])
def test_searches_are_the_brains_queries_then_only_useful_pairs(tmp_path, text, name, queries):
    link, f, job, phrases = _searched(tmp_path, text, queries, results=1)        # thin: pairs get added
    first = [" ".join(p) for p in phrases]
    assert link.searched[:len(first)] == first                     # the first round is the brain's queries, in order
    assert len(link.searched) <= 10
    name_words = set(find.words_of(job["name"], 6))
    name_pairs = find.name_queries(job["name"])
    extra = [" ".join(p) for p in name_pairs if p not in phrases]
    assert link.searched[len(first):len(first) + len(extra)] == extra   # D88: thin round, the name's pairs next
    for q in link.searched:
        words = q.split()
        assert not all(w in find._FILLER or find.stem(w) in find.GENERIC for w in words), q
        # never a pair cut from her sentence: the brain's words, or the whole job name's (D85, D88)
        assert any(set(words) <= set(p) for p in phrases + name_pairs), q
    assert "tutorial gives" not in link.searched and "step by" not in link.searched
    assert name_words                                              # the name is about the subject


def test_step_by_step_stays_one_phrase():
    assert find.query_words("step by step guide generator") == ["step-by-step", "guide", "generator"]
    assert find.query_words("day to day budget planner") == ["day-to-day", "budget", "planner"]
    assert find.query_words("bird song bird calls") == ["bird", "song", "bird", "calls"]   # repeats stay


def test_the_name_pairs_are_the_fallback_only(tmp_path):
    link, _f, _job, phrases = _searched(tmp_path, "add a birdwatching log with photos", None)
    assert phrases == [["birdwatching", "log"]] and link.searched == ["birdwatching log"]


def test_emoji_codes_are_not_words():
    item = {"full_name": "ex/CodeDesignTutorials", "name": "CodeDesignTutorials",
            "description": ":art: Tutorials for web design and :sparkles: code", "topics": []}
    assert not find.names_a_word(item, ["art"]) and not find.about(item, ":art: more :art:", {"art"})
    assert find.match_score(item, [["art"]]) == 0
    real = dict(item, description="Art tutorials for drawing")
    assert find.names_a_word(real, ["art"]) and find.about(real, "", {"art"})


# -- 3. GitHub's limit -------------------------------------------------------------------------------------------
def test_keyless_searches_are_spaced_so_no_minute_has_ten(tmp_path):
    clock = Clock()
    link = Link(clock=clock)
    f = find.Finder(link, tmp_path / "c", clock=clock, sleep=clock.sleep)
    for n in range(10):
        f.search([f"word{chr(97 + n)}", "thing"])
    assert len(link.sent_at) == 10
    assert all(b - a > 6.0 for a, b in zip(link.sent_at[1:], link.sent_at[2:]))
    assert link.sent_at[-1] - link.sent_at[0] > 60                  # never 10 inside one minute
    assert not f.slowed


def test_a_key_is_not_paced(tmp_path):
    clock = Clock()
    link = Link(clock=clock, headers={"x-ratelimit-limit": "30", "x-ratelimit-remaining": "29"})
    f = find.Finder(link, tmp_path / "c", clock=clock, sleep=clock.sleep)
    for n in range(6):
        f.search([f"word{chr(97 + n)}", "thing"])
    assert link.sent_at[-1] == link.sent_at[0]


def test_slow_down_and_skipped_searches_are_said_on_the_card(tmp_path):
    clock = Clock()
    link = Link(queries=["recipe manager", "meal suggestion", "fridge recipes"], status=403, clock=clock,
                headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "999999"})
    f = find.Finder(link, tmp_path / "c", clock=clock, sleep=clock.sleep)
    job = find.jobs_for("g", [wiring.chat_job(ASKS[0][0])])[0]
    plan = f.plan_job(job, f.search_words([job], "g")[0], [], {})
    assert plan["github"] == {"slowed": True, "skipped": 2}
    card = store.card("g", [dict(job, plan=plan)], [], {"where": "local"}, limited=plan["github"])
    line = next(ln for ln in card["lines"] if ln.startswith("GitHub asked Toolsmith to slow down"))
    assert "2 searches were not sent" in line and "Link your GitHub" in line and chr(0x2014) not in line
    quiet = store.card("g", [dict(job, plan=plan)], [], {"where": "local"}, limited={"slowed": False, "skipped": 0})
    assert not any("GitHub key" in ln for ln in quiet["lines"])
    assert find.limit_line(False, 1).startswith("1 search was not sent.")


# -- 4. the card sentence -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,_n,_q", ASKS, ids=[a[1] for a in ASKS])
def test_the_card_sentence_ends_at_a_word(text, _n, _q):
    goal = text + ", and keep everything private on this computer for the whole family to use every day"
    said = store.card(goal, [], [], {"where": "local"})["sentence"]
    quoted = said.split('"')[1]
    kept = quoted.removesuffix("...")                 # D104: a cut goal says it is cut
    assert len(quoted) <= 120 and goal.startswith(kept) and kept != quoted
    assert not goal[len(kept):][:1].isalnum() and not kept.endswith(",")   # a whole word, no dangling comma


# -- 5. README rules ------------------------------------------------------------------------------------------------
NEW_FORMS = {
    "sudo": (f"## Quick start\n\n{F}bash\nsudo docker compose up -d\n{F}\n", ["docker compose up -d"]),
    "and-and": (f"## Install\n\n{F}\nnpm install && npm start\n{F}\n", ["npm install", "npm start"]),
    "semicolon": (f"## Run\n\n{F}\ncd app; python coach.py\n{F}\n", ["python coach.py"]),
    "any script": (f"## Usage\n\n{F}\npip install -r requirements.txt\npython invoices.py\n{F}\n",
                   ["pip install -r requirements.txt", "python invoices.py"]),
    "node script": (f"## Running\n\n{F}\nnode birds.js\n{F}\n", ["node birds.js"]),
    "indented": ("## Getting started\n\nRun this:\n\n    npm install\n    npm run dev\n\nThen open it.\n",
                 ["npm install", "npm run dev"]),
    "inline": ("## Usage\n\nCopy the compose file below, then run `docker compose up -d` and open it.\n",
               ["docker compose up -d"]),
    "continued": (f"## Docker\n\n{F}\ndocker run -d \\\n  -p 8080:80 \\\n  example/recipes\n{F}\n",
                  ["docker run -d -p 8080:80 example/recipes"]),
}
OLD_REFUSALS = {
    "only clone": f"## Quick start\n\n{F}\ngit clone https://github.com/ex/x\ncd x\n{F}\n",
    "global install": f"## Quick start\n\n{F}\nnpm install -g thing && thing start\n{F}\n",
    "tests only": f"## Run\n\n{F}\nnpm test\n{F}\n",
    "setup script": f"## Installation\n\n{F}\npython setup.py install\n{F}\n",
    "install heading script": f"## Installation\n\n{F}\npython coach.py\n{F}\n",
    "test script": f"## Usage\n\n{F}\npython test_birds.py\n{F}\n",
    "root removal": f"## Run\n\n{F}\nnpm install && sudo rm -rf /\n{F}\n",
    "prose only": "## Usage\n\nOpen the app and add a recipe. Works with `npm` too.\n",
    "yaml block": f"## Docker\n\n{F}yaml\nservices:\n  app:\n    image: ex/app\n{F}\n",
}


@pytest.mark.parametrize("form", NEW_FORMS)
def test_each_new_readme_form_gives_start_steps(form):
    text, steps = NEW_FORMS[form]
    got = readme_steps.read(text)
    assert got["steps"] == steps and got["runtime"]


@pytest.mark.parametrize("form", OLD_REFUSALS)
def test_the_old_refusals_still_refuse(form):
    got = readme_steps.read(OLD_REFUSALS[form])
    assert got["steps"] == [] and got["runtime"] == ""


# -- 6. a part for another app ------------------------------------------------------------------------------------
@pytest.mark.parametrize("item,readme,host", [
    ({"full_name": "ex/MMM-Chores", "name": "MMM-Chores"}, "", "MagicMirror"),
    ({"full_name": "ex/ha-home-keeper", "name": "ha-home-keeper"}, "", "Home Assistant"),
    ({"full_name": "ex/recipe-sync", "name": "recipe-sync", "topics": ["hacs"]}, "", "Home Assistant"),
    ({"full_name": "ex/birdnotes", "name": "birdnotes"}, "Copy it to .obsidian/plugins/birdnotes", "Obsidian"),
    ({"full_name": "ex/invoice-helper", "name": "invoice-helper", "topics": ["vscode-extension"]}, "", "VS Code"),
    ({"full_name": "ex/birdsong", "name": "birdsong"}, "Copy to custom_components/birdsong", "Home Assistant"),
])
def test_a_part_for_another_app_is_named(item, readme, host):
    assert find.part_of(item, readme) == host


def test_a_plain_project_is_not_a_part():
    assert find.part_of({"full_name": "ex/recipes", "name": "recipes", "topics": ["self-hosted"]},
                        "## Usage\nOpen it in your browser.") == ""


def test_the_part_reason_replaces_no_start_on_the_plan(tmp_path):
    no_start = "# A thing\n\nSee the docs.\n"
    link = Link(queries=["chores tracker"], results=0, readmes={"MMM-Chores": no_start, "chorelist": no_start})
    f = find.Finder(link, tmp_path / "c")
    f.search_job = lambda job, phrases: [  # noqa: E731 - two found projects, no network
        {"full_name": "ex/MMM-Chores", "name": "MMM-Chores", "description": "Chores on your mirror",
         "language": "JavaScript", "stargazers_count": 40},
        {"full_name": "ex/chorelist", "name": "chorelist", "description": "Chores", "language": "Go",
         "stargazers_count": 30}]
    plan = f.plan_job({"n": 0, "name": "Chores tracker", "does": "tracks chores", "pasted": []},
                      [["chores", "tracker"]], [], {})
    why = {n["name"]: n["why"] for n in plan["not_used"]}
    assert why["MMM-Chores"] == "a part for another app (MagicMirror)"
    assert "MMM-Chores" not in [c["name"] for c in plan["code"]]
    assert "how to start it" in why["chorelist"]
