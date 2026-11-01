"""FIND with fake GitHub answers: stars, awesome-selfhosted, README check, reasons, reuse that fits."""

import json

import pytest

from toolsmith_fakes import FIXTURES, FakePorts
from puffpai_core.zone3.toolsmith import find

T = FIXTURES / "toolsmith"
RAW = "https://raw.githubusercontent.com/"

NEWS_MAP_README = """# News Map
A world news map.
## Quick start
```bash
npm install
npm run dev
```
Open http://localhost:5173 in your browser.
"""
LIB_README = """# news-lib
```python
import newslib
```
"""
BOARD_README = """# Headline Board
## Docker
```
docker compose up -d
```
"""


@pytest.fixture
def ports(tmp_path):
    web = {
        "https://api.github.com/search/repositories?*": (200, (T / "github_search.json").read_text()),
        RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md": (200, (T / "awesome.md").read_text()),
        RAW + "example/news-map/main/README.md": (200, NEWS_MAP_README),
        RAW + "example/news-lib/main/README.md": (200, LIB_README),
        RAW + "example/headline-board/HEAD/README.md": (200, BOARD_README),
        RAW + "example/headline-board/HEAD/compose.yaml": (200, "services: {}"),
    }
    return FakePorts(tmp_path, web=web)


def job(name="world news map", **kw):
    return {"n": 0, "name": name, "does": "Shows world news on a map", "sample_ask": "What is happening?",
            "pasted": [], **kw}


def test_words_and_jobs():
    assert find.words_of("Show me a WorldNews map please") == ["world", "news", "map"]
    jobs = find.jobs_for("A world news map", None)
    assert jobs[0]["name"] == "A world news map" and jobs[0]["sample_ask"]
    assert [j["name"] for j in find.jobs_for("x", [{"name": "News"}, {"name": ""}, "junk"])] == ["News"]


def test_plan_job_finds_apps_and_says_why_others_were_left_out(ports, tmp_path):
    f = find.Finder(ports, tmp_path / "cache")
    plan = f.plan_job(job(), [["world", "news"], ["news", "headline"]], [], {})
    refs = [c["ref"] for c in plan["candidates"]]
    assert refs[0] == "github:example/news-map"                 # most stars, it has run steps
    assert "github:example/headline-board" in refs              # from awesome-selfhosted, compose file
    board = next(c for c in plan["candidates"] if c["ref"] == "github:example/headline-board")
    assert board["tries_in"] == "docker" and board["source"] == "awesome-selfhosted"
    news = plan["candidates"][0]
    assert news["tries_in"] == "native" and news["programs"] == ["node"] and news["readme_port"] == 5173
    why = {n["ref"]: n["why"] for n in plan["not_used"]}
    assert why["github:example/awesome-news"] == "no code (a list or documents)"
    assert why["github:example/old-news"] == "archived or a copy"
    assert why["github:example/news-lib"] == find.CODE_WHY      # about the job: named on the card (I18b)
    assert plan["code"][0]["ref"] == "code:example/news-lib"   # kept for MAKE
    assert find.tries_line(news).startswith("Tries as a program on this computer, as you.")
    assert find.tries_line(board) == find.tries_line(news)       # D120: the label promises nothing


def test_search_is_cached_and_capped(ports, tmp_path):
    calls = []
    real = ports.http_send

    def counting(*a, **k):
        calls.append(a[1])
        return real(*a, **k)
    ports.http_send = counting
    f = find.Finder(ports, tmp_path / "cache")
    f.search(["news"])
    f2 = find.Finder(ports, tmp_path / "cache")
    f2.search(["news"])
    assert sum("search/repositories" in c for c in calls) == 1          # a day's cache
    f3 = find.Finder(ports, tmp_path / "other")
    for n in range(14):
        f3.search([f"topic{chr(97 + n)}"])                              # letters only: the screen sends no digits
    assert f3.searches == 10                                            # at most 10 searches per build (lane I15)


def test_rate_limit_stops_searching(tmp_path):
    p = FakePorts(tmp_path, web={"https://api.github.com/*": (403, "rate limited")})
    lines = []
    f = find.Finder(p, tmp_path / "c", say=lines.append)
    assert f.search(["news"]) == [] and f.rate_blocked
    assert f.search(["other"]) == [] and lines[0].startswith("GitHub asked us to slow down")


def test_search_url_has_stars_and_no_identifier(ports, tmp_path):
    seen = []
    real = ports.http_send
    ports.http_send = lambda *a, **k: (seen.append((a, k)), real(*a, **k))[1]
    find.Finder(ports, tmp_path / "c").search(["news", "map"])
    url = seen[0][0][1]
    assert "stars:>=20" in url and "archived:false" in url and "fork:false" in url and "sort=stars" in url
    assert "@" not in url


def test_rank_never_vetoes(ports, tmp_path):
    ports.brain = lambda system, user: {"order": [2]} if "rank_apps" in system else None
    f = find.Finder(ports, tmp_path / "c")
    pool = [{"full_name": f"a/{n}", "name": str(n), "stargazers_count": s} for n, s in enumerate([30, 20, 10])]
    ranked = f.rank(job(), pool)
    assert [i["name"] for i in ranked] == ["2", "0", "1"]               # its pick first, the rest by stars
    ports.brain = lambda *_: None
    assert [i["name"] for i in f.rank(job(), pool)] == ["0", "1", "2"]   # no answer: stars


def test_search_words_from_brain_or_name(ports, tmp_path):
    ports.screen_queries = None                                         # a link with no screen route
    f = find.Finder(ports, tmp_path / "c")
    jobs = [job("Daily headlines")]
    assert f.search_words(jobs, "news") == {0: [["headlines"]]}           # "daily" is filler
    ports.brain = lambda system, user: {"queries": ["news reader", "rss"]}
    assert f.search_words(jobs, "news") == {0: [["headlines"]]}           # no screen to ask: never sent (lane I15)
    ports.screen_queries = lambda qs: list(qs)
    assert f.search_words(jobs, "news") == {0: [["news", "reader"], ["rss"]]}


def test_pasted_project_goes_first(ports, tmp_path):
    f = find.Finder(ports, tmp_path / "c")
    plan = f.plan_job(job(pasted=["https://github.com/example/headline-board"]), [["world", "news"]], [], {})
    assert plan["candidates"][0]["ref"] == "github:example/headline-board"
    assert plan["candidates"][0]["source"] == "pasted"


def test_reuse_needs_fit_not_running(ports, tmp_path):
    rows = [{"id": "otel", "origin": "adopted:https://github.com/x/otel", "status": "running"},
            {"id": "newsmap", "origin": "adopted:https://github.com/example/news-map", "status": "stopped"}]
    f = find.Finder(ports, tmp_path / "c")
    assert f.plan_job(job(), [], rows, {})["reuse"] is None                       # running is not proof
    assert f.plan_job(job(), [], rows, {"world news map": ["newsmap"]})["reuse"] == "newsmap"   # proven
    pasted = job(pasted=["https://github.com/example/news-map"])
    assert f.plan_job(pasted, [], rows, {})["reuse"] == "newsmap"                 # her own pick


def test_closed_bridge_finds_nothing_and_says_so(tmp_path):
    p = FakePorts(tmp_path)
    p.allowed_hosts = set()
    lines = []
    f = find.Finder(p, tmp_path / "c", say=lines.append)
    assert f.plan_job(job(), [["news"]], [], {})["candidates"] == []
    assert any("Bridge is closed" in ln for ln in lines)


@pytest.mark.live
def test_live_github_search_read_only(tmp_path):
    """Read-only, unauthenticated, a generic User-Agent. Run with: pytest -m live."""
    import os
    if os.environ.get("PUFFPAI_LIVE") != "1":
        pytest.skip("set PUFFPAI_LIVE=1 to search GitHub for real")
    from puffpai_core.zone3.toolsmith_host.ports import Ports

    class Open(Ports):
        def bridge_decision(self, subject, host, phase="build"):
            return "allow" if host == "api.github.com" else "deny"
    got = find.Finder(Open(tmp_path), tmp_path / "c").search(["world", "news"])
    assert got and all(int(i["stargazers_count"]) >= 20 for i in got)
    stars = [int(i["stargazers_count"]) for i in got]
    assert stars == sorted(stars, reverse=True)
