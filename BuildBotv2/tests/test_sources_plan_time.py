"""D99: more places must not push a plan past its 10-minute limit, and one place failing must not lose the plan.

A 6-job plan through the real ``agent.plan`` and ``find.Finder``, on a fake link, with every wait scaled down by
:data:`SCALE` so a 10-minute plan takes seconds. Times are said in plan seconds (real seconds x SCALE). The numbers are
printed (``pytest -s``) and the lane's report quotes them. A slow local brain is a brain that takes 20 s per ask.
"""

from __future__ import annotations

import copy
import json
import time
from functools import partial

import pytest

from puffpai_core.zone3.toolsmith import agent, find, shelves

SCALE = 50.0
LIMIT = 600.0
JOBS = [("Recipes", "Keeps family recipes"), ("Invoices", "Sends invoices"), ("Birds", "Logs birds seen"),
        ("Shopping", "Keeps the shopping list"), ("Repairs", "Tracks house repairs"), ("Photos", "Shows family photos")]
README = "# App\n\n## Quick start\n```\nnpm install\nnpm start\n```\nOpen http://localhost:3000\n"
#: Measured live 2026-10-30, seconds per answer.
PLACE_S = {"codeberg.org": 10.0, "registry.npmjs.org": 0.3, "pypi.org": 0.1, "registry.modelcontextprotocol.io": 0.2,
           "clawhub.ai": 6.3, "skills.sh": 3.1, "nousresearch.github.io": 8.0, "data-v2.hacs.xyz": 0.3}


class Link:
    """The Core as Toolsmith's plan sees it. *brain_s*, *web_s*: plan seconds per answer."""

    def __init__(self, brain_s: float, places: list[str], hang: set[str] = frozenset(), boom: set[str] = frozenset()) -> None:
        self.brain_s, self.places, self.hang, self.boom = brain_s, places, set(hang), set(boom)
        self.plan = None
        self.asks = 0
        self.web: list[str] = []

    def _wait(self, plan_s: float) -> None:
        time.sleep(plan_s / SCALE)

    def build(self) -> dict:
        return {"build": {"id": "b", "goal": "a home helper bot", "jobs_in": [{"name": n, "does": d} for n, d in JOBS]},
                "settings": {"timing": {"plan_wait_s": LIMIT / SCALE}}, "search_places": list(self.places), "web_ready": []}

    def registry(self) -> dict:
        return {"rows": [{"id": "other", "name": "Other", "kind": "app", "status": "running"}], "proven": {}, "store": []}

    def brain_json(self, system, user):
        self.asks += 1
        self._wait(self.brain_s)
        if "TASK=search_queries" in system:
            job = json.loads(user)["job"].lower()
            return {"queries": [f"{job} app", f"{job} tracker"]}
        if "TASK=knowledge_topics" in system:
            return {"topics": [f"{json.loads(user)['job'].lower()} basics"]}
        return {"fits": True}

    def fits(self, package, ask):
        self.asks += 1
        self._wait(self.brain_s)
        return {"ok": True, "asked": True, "called": [], "reason": "none"}

    def screen_queries(self, queries):
        return list(queries)

    def knowledge_find(self, topics):
        self._wait(3.0 if not self.hang else 45.0)      # the Core's own asks, within their own budget
        return {"ok": True}

    def put_plan(self, body):
        self.plan = body

    def now(self, line):
        pass

    def http_get(self, url, *, accept="", max_bytes=2_000_000, keyed=False, timeout_s=None, web_key=""):
        self.web.append(url)
        host = url.split("/")[2]
        if host in self.boom:
            raise OSError("no answer")
        if host in self.hang:
            self._wait(float(timeout_s or 30) * SCALE)  # already in plan seconds: a place that never answers
            raise OSError("timed out")
        if host == "api.github.com" and "/search/" in url:
            self._wait(1.0)
            word = url.split("q=")[1].split("+")[0].replace("%20", "")
            items = [{"full_name": f"gh/{word}-{i}", "name": f"{word}-{i}", "description": f"A {word} app", "language": "JavaScript",
                      "stargazers_count": 100 - i, "default_branch": "main", "topics": [word]} for i in range(10)]
            return 200, {"x-ratelimit-limit": "10"}, json.dumps({"items": items}).encode()
        if host == "raw.githubusercontent.com":
            self._wait(0.5)
            if "awesome-selfhosted" in url:
                return 200, {}, b"# list\n"
            return 200, {}, README.encode() if url.endswith("README.md") else b""
        self._wait(PLACE_S.get(host, 0.5))
        return 200, {}, b"{}"


@pytest.fixture
def scaled(monkeypatch, tmp_path):
    """Every time in sources.json divided by SCALE, and Toolsmith's waits too. README reads and GitHub run real code."""
    src = copy.deepcopy(find.sources())
    src["budget"].update(shelves_s_per_job=src["budget"]["shelves_s_per_job"] / SCALE)
    for row in [*src["shelves"].values(), *src["web"].values()]:
        row["timeout_s"] = float(row.get("timeout_s") or 10) / SCALE
        row["gap_s"] = float(row.get("gap_s") or 1) / SCALE
        row["cache_hours"] = 0
    monkeypatch.setattr(find, "sources", lambda: copy.deepcopy(src))
    real = find.Finder
    monkeypatch.setattr(find, "Finder", partial(real, sleep=lambda s: time.sleep(s / SCALE)))
    monkeypatch.setattr(agent, "log", lambda _line: None)

    def run(link: Link) -> float:
        began = time.time()
        agent.plan(link, tmp_path / f"cache{time.time()}")
        return (time.time() - began) * SCALE
    return run


ALL = ["api.github.com", "raw.githubusercontent.com", "github.com", *PLACE_S]
OLD = ["api.github.com", "raw.githubusercontent.com", "github.com"]


def _said(name: str, seconds: float, link: Link) -> None:
    jobs = link.plan["jobs"]
    n = sum(len(j["plan"]["candidates"]) for j in jobs)
    print(f"\n  {name}: {seconds:.0f} s of {LIMIT:.0f} s, {link.asks} brain asks, {len(link.web)} web asks, {n} apps on the plan")


@pytest.mark.parametrize("brain_s", [5.0, 20.0])
def test_a_six_job_plan_stays_inside_its_limit(scaled, brain_s):
    before = Link(brain_s, OLD)
    took_before = scaled(before)
    _said(f"brain {brain_s:g} s, GitHub only (as before)", took_before, before)
    healthy = Link(brain_s, ALL)
    took = scaled(healthy)
    _said(f"brain {brain_s:g} s, every place healthy", took, healthy)
    assert healthy.plan and len(healthy.plan["jobs"]) == 6
    assert took < LIMIT
    # Every place is asked at once, beside the rest of the plan, so all of them cost little more than one.
    assert took - took_before < 60
    asked = healthy.plan["places"]["asked"]
    assert set(asked) >= {"codeberg", "npm", "pypi", "mcp_registry", "hacs", "clawhub", "skills_sh", "hermes_index"}


@pytest.mark.parametrize("brain_s", [5.0, 20.0])
def test_one_place_hanging_or_failing_does_not_lose_the_plan(scaled, brain_s):
    slow = Link(brain_s, ALL, hang={"codeberg.org"})
    took = scaled(slow)
    _said(f"brain {brain_s:g} s, Codeberg hangs", took, slow)
    assert slow.plan and took < LIMIT
    assert "codeberg" in slow.plan["places"]["down"]
    asked = [u for u in slow.web if u.startswith("https://codeberg.org/api/")]
    assert len(asked) == 1                               # asked once, then out for the rest of the plan
    assert "npm" in slow.plan["places"]["asked"] and "npm" not in slow.plan["places"]["down"]
    bad = Link(brain_s, ALL, boom={"clawhub.ai", "pypi.org"})
    took = scaled(bad)
    _said(f"brain {brain_s:g} s, two places fail at once", took, bad)
    assert bad.plan and took < LIMIT and {"clawhub", "pypi"} <= set(bad.plan["places"]["down"])


def test_every_place_hanging_still_makes_the_plan(scaled):
    link = Link(5.0, ALL, hang=set(PLACE_S))
    took = scaled(link)
    _said("brain 5 s, every other place hangs", took, link)
    assert link.plan and took < LIMIT
    assert any(j["plan"]["candidates"] for j in link.plan["jobs"])     # GitHub's finds still make the plan


def test_a_very_slow_brain_writes_the_plan_with_what_it_has(scaled):
    link = Link(45.0, ALL)
    took = scaled(link)
    _said("brain 45 s per ask", took, link)
    assert link.plan and took < LIMIT                    # today this lost the plan at the limit
    assert link.plan["places"]["ran_late"] is True
    assert len(link.plan["jobs"]) == 6
    # Even so, every place was asked for every job: the brain's slowness never leaves one out.
    assert set(link.plan["places"]["asked"]) >= {"codeberg", "npm", "pypi", "clawhub", "skills_sh", "hermes_index"}
    npm = [u for u in link.web if "registry.npmjs.org" in u]
    assert len(npm) >= 6


def test_a_plan_of_only_her_pasted_projects_searches_nowhere_and_says_nothing_about_it(tmp_path, monkeypatch):
    class L(Link):
        def build(self):
            got = Link.build(self)
            got["build"]["jobs_in"] = [{"name": n, "does": d, "pasted": [f"https://github.com/her/{n.lower()}"]} for n, d in JOBS[:2]]
            return got
    link = L(0.0, ALL)
    monkeypatch.setattr(agent, "log", lambda _line: None)
    agent.plan(link, tmp_path / "cache")
    assert not [u for u in link.web if u.split("/")[2] in PLACE_S or "/search/" in u]
    assert link.plan["places"] == {}


def test_a_bot_whose_search_yes_names_only_github_asks_nothing_else(scaled):
    link = Link(1.0, OLD)
    scaled(link)
    hosts = {u.split("/")[2] for u in link.web}
    assert hosts <= set(OLD)
    assert {"codeberg", "npm", "pypi", "clawhub"} <= set(link.plan["places"]["not_asked"])


def test_look_rows_come_back_only_when_about_the_job_and_are_never_candidates(tmp_path, monkeypatch):
    class L(Link):
        def http_get(self, url, **kw):
            if "skills.sh" in url:
                return 200, {}, json.dumps({"skills": [{"id": "a/b/recipes-box", "source": "a/b", "name": "Recipes box"},
                                                       {"id": "a/b/other", "source": "a/b", "name": "Chess openings"}]}).encode()
            return Link.http_get(self, url, **kw)
    link = L(0.0, ["skills.sh", *OLD])
    monkeypatch.setattr(agent, "log", lambda _line: None)
    agent.plan(link, tmp_path / "cache")
    recipes = next(j for j in link.plan["jobs"] if j["name"] == "Recipes")["plan"]
    assert [r["name"] for r in recipes["look"]] == ["Recipes box"]
    assert all("skills.sh" not in c["url"] for j in link.plan["jobs"] for c in j["plan"]["candidates"])
