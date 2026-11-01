"""Lane I18b, from the owner's live test3 space bot (10-27): fewer, better candidates at plan time, for ANY goal.

1. An awesome-selfhosted row must name every word of a query that says what it is about, as GitHub's own search
   does: "event", "tracker" or "space" alone is no match (ticketing apps for a sky job, habit trackers for the ISS).
2. The brain's plain no drops a candidate, under "Not used" with a plain why (D66 b); the ask is in the build log.
3. Code about the job that does not start as an app is kept only when its words are about the job and the brain
   does not say no, and the plan card names it on its own line, so it is never quietly below the apps.
4. A candidate that needs a program this computer lacks (besides Docker) goes after the ones that do not, and the
   card says plainly that it needs that program installed on this computer.

Every check runs on six unrelated goals (tests/data/i18b/goals.json): space, garden, homework, book club, car
care and a coder bot. No network: every answer comes from the fixture."""

import json
import re
from pathlib import Path

import pytest

from puffpai_core.zone3.toolsmith import find, programs
from puffpai_core.zone3.toolsmith_host import store
from toolsmith_fakes import FakePorts

DATA = json.loads((Path(__file__).parent / "data" / "i18b" / "goals.json").read_text(encoding="utf-8"))["goals"]
AWESOME_URL = find.sources()["awesome_selfhosted"]["url"]
RAW = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+/[^/]+)/([^/]+)/(.+)$")
COMPOSE = "\n## Quick start\n\n```\ndocker compose up -d\n```\n"
NODE = "\n## Quick start\n\n```\nnpm install\nnpm start\n```\n"
MAKE = "\n## Quick start\n\n```\ndocker compose up -d\nmake setup\n```\n"


def _awesome(goal: dict) -> str:
    out, heading = ["# Awesome-Selfhosted", ""], ""
    for head, name, full, desc, docker in goal["awesome"]:
        if head != heading:
            out += ["", f"## {head}", ""]
            heading = head
        out.append(f"- [{name}](https://github.com/{full}) - {desc} `MIT` " + ("`Docker`" if docker else "`Go`"))
    return "\n".join(out) + "\n"


def _items(goal: dict) -> list[dict]:
    return [{"full_name": g["full_name"], "name": g.get("name") or g["full_name"].split("/")[1],
             "description": g["description"],
             "language": g["language"], "stargazers_count": g["stars"], "topics": g["topics"],
             "default_branch": "main"} for g in goal["github"]]


def _readme(goal: dict, full: str) -> str | None:
    for _h, name, f, desc, _d in goal["awesome"]:
        if f == full:
            return f"# {name}\n\n{desc}\n" + (MAKE if full in goal.get("needs_make", []) else COMPOSE)
    for g in goal["github"]:
        if g["full_name"] == full:
            how = {"docker": COMPOSE, "node": NODE}.get(g.get("runs", ""), "\nSee the API docs to use it in your code.\n")
            return f"# {full.split('/')[1]}\n\n{g['description']}\n" + how
    return None


def _web(goal: dict):
    """GitHub as a fake: its search needs every word of the query in a name, description or topic (as GitHub's
    ``in:name,description,topics`` does), the awesome list, and each project's README."""
    items = _items(goal)

    def answer(url: str):
        if url.startswith("https://api.github.com/search/repositories?q="):
            words = url.split("q=", 1)[1].split("+in:", 1)[0].split("+")
            got = [i for i in items if all(find.stem(w) in find._word_stems(find._item_text(i)) for w in words)]
            return 200, json.dumps({"items": sorted(got, key=lambda i: -i["stargazers_count"])})
        if url == AWESOME_URL:
            return 200, _awesome(goal)
        m = RAW.match(url)
        if m and m.group(3) == "README.md":
            text = _readme(goal, m.group(1))
            return (200, text) if text else (404, "")
        return 404, ""
    return answer


class Brain:
    """The local brain as a double: yes only to the projects the goal's fixture lists, no to the rest."""

    def __init__(self, goal: dict) -> None:
        self.yes, self.code_yes, self.asked = set(goal["yes"]), set(goal["code_yes"]), []

    def __call__(self, system: str, user: str):
        name = user[3:].split(":", 1)[0] if user.startswith("Is ") else ""
        if system.startswith("TASK=fits_job"):
            self.asked.append(("app", name))
            return {"fits": name in self.yes}
        if system.startswith("TASK=fits_code"):
            self.asked.append(("code", name))
            return {"fits": name in self.code_yes}
        return None                                # the rank ask: no answer, the score's order


def plans(goal_name: str, tmp_path: Path, brain: bool = True) -> list[tuple[dict, dict]]:
    """Each job of the goal with its plan, as ``Finder.plan_job`` makes it on the fixture."""
    goal = DATA[goal_name]
    ports = FakePorts(tmp_path, web={"https://*": _web(goal)}, brain=Brain(goal) if brain else None)
    f = find.Finder(ports, tmp_path / f"cache-{goal_name}")
    out = []
    for n, j in enumerate(goal["jobs"]):
        job = {"n": n, "name": j["name"], "does": j["does"], "sample_ask": "", "pasted": []}
        f.jobs_left = len(goal["jobs"]) - n
        out.append((job, f.plan_job(job, j["phrases"], [], {})))
    return out


def _names(plan: dict) -> list[str]:
    return [c["name"] for c in plan["candidates"]]


def _seen(plan: dict) -> set[str]:
    return set(_names(plan)) | {n["name"] for n in plan["not_used"]}


def _card(job: dict, plan: dict) -> list[str]:
    return store.card("g", [{"n": 0, "name": job["name"], "pasted": [], "plan": dict(plan, reuse=None)}], [],
                      {"where": "local"})["lines"]


# -- 1 to 3, on the live space bot ---------------------------------------------------------------------------------
def test_the_space_bot_gets_no_ticketing_or_habit_apps_and_its_code_is_on_the_card(tmp_path):
    (j0, events), (j1, iss), (j2, photos) = plans("space", tmp_path)
    # Celestial events: the ticketing apps the brain said no to are dropped with a plain why, the one it said yes
    # to stays, and the sky code is on the card.
    assert _names(events) == ["EventSchedule"]
    for name in ("Hi.Events", "Alf.io", "ANALOG"):              # they name "event": the brain's no drops them
        # D85: the name's pair "event tracker" is no longer searched next to the brain's queries, so they may
        # not come up at all; when they do, the brain's no drops them.
        assert next((n["why"] for n in events["not_used"] if n["name"] == name), find.BRAIN_NO_WHY)             == find.BRAIN_NO_WHY and name not in _names(events)
    assert "Bracket" not in _seen(events)                        # "schedule" alone: no match
    assert [c["name"] for c in events["code"]] == ["astronomy-bundle-js", "sky-tonight-card"]
    assert any(ln.startswith("Celestial event tracker: code about this was found too") and "sky-tonight-card" in ln
               for ln in _card(j0, events))
    # The ISS: no archive, habit, fitness, speed test or file app comes up for "space" or "tracker" alone.
    assert iss["candidates"] == []
    assert not {"ArchivesSpace", "OpenHabitTracker", "FitTrackee", "Speedtest Tracker", "Peergos"} & _seen(iss)
    assert iss["code"] == []                                     # a file organizer and a weather station: the brain's no
    # Space photos: the brain said no to every photo app live, so none is tried; the space-image code is named.
    assert photos["candidates"] == []
    assert {n["name"] for n in photos["not_used"] if n["why"] == find.BRAIN_NO_WHY} >= {"photoview", "Lychee"}
    code = [c["name"] for c in photos["code"]]
    assert "WebbCompare" in code and "PhotoSwipe" in code and "photometric_optimization" not in code
    line = next(ln for ln in _card(j2, photos) if ln.startswith("Space photo gallery: code about this"))
    assert line.endswith("To try one, paste its GitHub address for this job.") and chr(0x2014) not in line


def test_the_brains_no_is_in_the_detail_only_never_on_the_card(tmp_path):
    _events, _iss, (j2, photos) = plans("space", tmp_path)
    said = [d for d in photos["detail"] if "answered no" in d and "as code" not in d]
    assert said and all(": not tried, your bot's brain answered no to \"Is " in d for d in said)
    assert not any("answered no" in ln for ln in _card(j2, photos))


# -- the same rules on five unrelated goals -------------------------------------------------------------------------
OFF_TOPIC = {"garden": {"OpenHabitTracker", "TRIP"},
             "homework": {"Planka"},
             "bookclub": {"Mobilizon"},
             "car": {"Graylog", "Traccar"},
             "coder": {"code-server", "Gitea"}}


@pytest.mark.parametrize("goal", sorted(OFF_TOPIC))
def test_any_goal_keeps_the_apps_the_brain_says_yes_to_and_drops_one_word_matches(goal, tmp_path):
    ((job, plan),) = plans(goal, tmp_path)
    assert set(_names(plan)) == set(DATA[goal]["yes"]), plan
    assert not OFF_TOPIC[goal] & _seen(plan), goal                  # one shared word: never even read
    for n in plan["not_used"]:
        if n["why"] == find.BRAIN_NO_WHY:
            assert n["name"] not in DATA[goal]["yes"]
    assert [c["name"] for c in plan["code"]] == DATA[goal]["code_yes"]
    assert any(ln.startswith(f"{job['name']}: code about this was found too") for ln in _card(job, plan))


@pytest.mark.parametrize("goal", sorted(DATA))
def test_without_a_brain_answer_nothing_is_dropped_for_a_no(goal, tmp_path):
    for _job, plan in plans(goal, tmp_path, brain=False):
        assert not any(n["why"] == find.BRAIN_NO_WHY for n in plan["not_used"])


def test_a_list_row_names_every_word_that_says_what_it_is_about():
    row = {"full_name": "ex/tickets", "name": "Tickets", "description": "Sell tickets for an event.", "topics": []}
    assert not find.names_every_word(row, ["celestial", "event"])     # one shared word is no match
    assert find.names_every_word(row, ["event", "tracker"])           # a kind-of-app word need not be there
    assert find.names_every_word(row, ["event"])
    gallery = {"full_name": "photoview/photoview", "name": "photoview", "description": "Self-hosted gallery."}
    assert find.names_every_word(gallery, ["photo", "gallery"])       # a name that runs the words together
    assert find.names_every_word({"name": "Planner", "description": ""}, ["planner"])   # only kind words: those


# -- 4. a program to install on the computer ---------------------------------------------------------------------------
@pytest.fixture
def no_make(monkeypatch):
    """Call it to make this computer lack make (only make, whatever else is here); everything else is real."""
    real = programs.missing

    def lacks_make():
        monkeypatch.setattr(programs, "missing", lambda tools: [t for t in tools if t == "make"] +
                            [t for t in real([t for t in tools if t != "make"])])
    return lacks_make


@pytest.fixture
def has_make(monkeypatch):
    real = programs.missing
    monkeypatch.setattr(programs, "missing", lambda tools: real([t for t in tools if t != "make"]))


def test_a_candidate_that_needs_a_program_goes_after_the_ones_that_do_not(tmp_path, has_make, no_make):
    ((_job, plain),) = plans("car", tmp_path / "plain")
    assert _names(plain)[0] == "Hammond"                         # the best match when nothing is missing
    no_make()
    ((job, plan),) = plans("car", tmp_path)
    assert _names(plan) == ["LubeLogger", "Hammond"]
    hammond = plan["candidates"][1]
    assert "make" in hammond["programs"] and find.system_needs(hammond) == ["make"]
    line = _card(job, plan)[0]
    assert line.startswith("Car maintenance log: try LubeLogger first")
    assert line.endswith("1 of the others needs a program installed on this computer.")


def test_the_card_says_plainly_when_the_first_one_needs_a_program(tmp_path, no_make):
    no_make()
    ((job, plan),) = plans("car", tmp_path)
    only = dict(plan, candidates=[c for c in plan["candidates"] if c["name"] == "Hammond"])
    line = _card(job, only)[0]
    assert "Hammond needs make installed on this computer." in line and chr(0x2014) not in line


def test_docker_alone_never_ranks_a_candidate_down(tmp_path, monkeypatch):
    monkeypatch.setattr(programs, "missing", lambda tools: list(tools))          # nothing is on this computer
    cand = {"name": "x", "programs": ["docker"]}
    assert find.system_needs(cand) == [] and find.needs_line(cand) == ""
