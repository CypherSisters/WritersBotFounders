"""Lane I8a, from the owner's live test of a wedding planner bot (10-25), and the verifier round after it.

1. Every common form of a GitHub address is her project alone; anything else is said not to be one.
2. Her pasted project gets its try even when its README has no run steps; when GitHub can't be reached, that is
   the reason; a failed try says why.
3. The subject check leaves out only filler words ("them", "you", "across", "limit"); a job's domain words always
   count. The brain's one yes/no ask: a plain no drops it, with a plain "Not used" line (D66 b, lane I18b).
4. A newer plan for the same goal closes the old plan's card ("Replaced by a newer plan."), with no race
   against start.
5. Cards and job lines say what a tool does, never its name, and "Nothing on your bot does this yet."; a GitHub
   address is asked for only when she pasted none.

Fixture: tests/data/wedding (see its SOURCE.md). No network: every answer comes from the fixture."""

import json
import re
from pathlib import Path

import pytest

from puffpai_core.consent import ConsentError
from puffpai_core.zone3.toolsmith import find
from puffpai_core.zone3.toolsmith_host import store
from test_i2a_wizard_toolsmith import _kit, real  # noqa: F401 - the real Core fixture
from toolsmith_fakes import FakePorts, Script, World, tool
from wizard_kit import Kit, TerminalRun

DATA = Path(__file__).parent / "data" / "wedding"
JOBS = json.loads((DATA / "jobs.json").read_text(encoding="utf-8"))
FILES = json.loads((DATA / "readmes.json").read_text(encoding="utf-8"))
SEARCH = {"vendor": "search_vendor_tracking.json", "budget": "search_budget_monitor.json",
          "theme": "search_theme_designer.json"}
RAW = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+/[^/]+)/[^/]+/(.+)$")


def _web(url):
    """GitHub, as the fixture has it: the search per job's first word, raw READMEs and run files, repo facts."""
    if url.startswith("https://api.github.com/search/repositories?q="):
        first = url.split("q=", 1)[1].split("+", 1)[0]
        name = SEARCH.get(first)
        return (200, (DATA / name).read_text(encoding="utf-8")) if name else (200, '{"items": []}')
    if url.startswith("https://api.github.com/repos/"):
        full = url[len("https://api.github.com/repos/"):]
        got = FILES["repos"].get(full)
        return (200, json.dumps(got)) if got else (404, "")
    if url == find.sources()["awesome_selfhosted"]["url"]:
        return 200, (DATA / "awesome.fixture.md").read_text(encoding="utf-8")
    m = RAW.match(url)
    if m:
        full, name = m.group(1), m.group(2)
        if name in find.README_NAMES:
            text = FILES["readmes"].get(full)
            return (200, text) if text and name == "README.md" else (404, "")
        if FILES["run_files"].get(full) == name:
            return 200, "services: {}\n"
    return 404, ""


class Ports(FakePorts):
    """FakePorts on the fixture, keeping every address it was asked for."""

    def __init__(self, tmp_path, brain=None):
        super().__init__(tmp_path, web={"https://*": _web}, brain=brain)
        self.asked: list[str] = []

    def http_send(self, method, url, **kw):
        self.asked.append(url)
        return super().http_send(method, url, **kw)


def _ports(tmp_path, brain=None):
    return Ports(tmp_path, brain)


def _plan(tmp_path, job, brain=None, cache="c"):
    f = find.Finder(_ports(tmp_path, brain), tmp_path / cache)
    j = {k: v for k, v in job.items() if k != "phrases"}
    return f.plan_job(j, job["phrases"], [], {})


def _names(plan):
    return [c["full_name"] for c in plan["candidates"]]


def _why(plan, full):
    return next(n["why"] for n in plan["not_used"] if n["ref"] == f"github:{full}")


def _job(name):
    return next(j for j in JOBS["jobs"] if j["name"] == name)


class Brain:
    """The local brain as a double: it says no to the projects in *no*, and records each question."""

    def __init__(self, no=()):
        self.no, self.asked = set(no), []

    def __call__(self, system, user):
        if system.startswith("TASK=fits_job"):
            self.asked.append(user)
            return {"fits": not any(f"Is {n}:" in user for n in self.no)}
        return None


# -- 3. the subject check: only filler words were taken out ---------------------------------------------------
LIVE_OFF_TOPIC = ("Swetrix/selfhosting", "pascalebeier/hitkeep", "apache/airflow", "kestra-io/kestra",
                  "middlewarehq/middleware", "AlexSciFier/neonlink")


def test_filler_words_never_count_but_a_jobs_domain_words_do():
    vendor = find.subject_of(_job("Vendor Tracker"), _job("Vendor Tracker")["phrases"])
    assert vendor == {"vendor", "wed"}                         # a plain vendor manager still matches
    budget = find.subject_of(_job("Budget Monitor"), _job("Budget Monitor")["phrases"])
    assert {"budget", "spend", "wed"} <= budget
    theme = find.subject_of(_job("Theme Designer"), _job("Theme Designer")["phrases"])
    assert {"theme", "design", "color", "palette", "event"} <= theme
    for subject in (vendor, budget, theme):
        for word in ("them", "you", "across", "limit", "limits", "overall"):
            assert find.stem(word) not in subject, word


def test_the_live_off_topic_projects_never_come_up_and_good_apps_stay(tmp_path):
    """Her cached candidates: Swetrix, HitKeep, Airflow, Kestra, Middleware and NeonLink each share one word with a
    query ("track", "monitor", "design"); a list row must name every word of a query that says what it is about
    (lane I18b), so none of them comes up. A vendor manager, a budget app, an envelope-budget app and a color-theme
    app all stay."""
    brain = Brain(no={"Middleware", "NeonLink"})
    vendor = _plan(tmp_path, _job("Vendor Tracker"), brain=brain, cache="v")
    assert {"example/wedding-vendors", "example/vendor-book"} <= set(_names(vendor))
    budget = _plan(tmp_path, _job("Budget Monitor"), brain=brain, cache="b")
    assert {"blessyjoybj18/budget_tracker", "example/envelope-budget"} <= set(_names(budget))
    theme = _plan(tmp_path, _job("Theme Designer"), brain=brain, cache="t")
    assert _names(theme)[0] == "example/palette-board"
    for plan in (vendor, budget, theme):
        seen = {c["full_name"] for c in plan["candidates"]} | {n["ref"].split(":", 1)[1] for n in plan["not_used"]}
        assert not seen & (set(LIVE_OFF_TOPIC) | {"Swetrix/selfhosting"}), seen


def test_every_dropped_reason_is_true(tmp_path):
    """A "never mention" reason names words that really are in none of the project's name, description,
    topics or README (as the fixture has them)."""
    search = {}
    for name in SEARCH.values():
        for i in json.loads((DATA / name).read_text(encoding="utf-8"))["items"]:
            search[i["full_name"]] = i
    rows = find.awesome_rows((DATA / "awesome.fixture.md").read_text(encoding="utf-8"),
                             ["track", "monitor", "design", "tracking"], 50)
    for r in rows:
        search.setdefault(r["full_name"], r)
    seen = 0
    for n, job in enumerate(JOBS["jobs"]):
        plan = _plan(tmp_path, job, cache=f"r{n}")
        for note in plan["not_used"]:
            if not note["why"].startswith("its name, description, topics and README never mention"):
                continue
            seen += 1
            full = note["ref"].split(":", 1)[1]
            said = re.findall(r"\"([^\"]+)\"", note["why"])
            text = find._word_stems(find._item_text(search[full])) | find._word_stems(FILES["readmes"].get(full, ""))
            assert said and not ({find.stem(w) for w in said} & text), (full, said)
    # Since lane I18b the live off-topic list rows never come up (a row must name every word of a query), so
    # few reach the subject check here; test_i4b_build_flow's own check still sees one.


# -- 3. the brain's one yes/no ask: a plain no drops it (D66 b, lane I18b) ----------------------------------------
def test_the_brains_no_drops_it_with_a_plain_line_and_the_ask_in_the_detail(tmp_path):
    """A plain no drops the candidate: her "Not used" line says the brain says it is not an app for this job; the
    ask itself is in the build log's detail only, never on the card."""
    job = _job("Budget Monitor")
    words_only = _names(_plan(tmp_path, job, cache="a"))
    noes = ["blessyjoybj18/budget_tracker", "example/envelope-budget"]
    assert set(noes) <= set(words_only)
    brain = Brain(no={"budget_tracker", "envelope-budget"})
    plan = _plan(tmp_path, job, brain=brain, cache="b")
    assert _names(plan) == [n for n in words_only if n not in noes]                    # dropped, order kept
    for full in noes:
        assert _why(plan, full) == find.BRAIN_NO_WHY == "your bot's brain says it is not an app for this job"
    said = [d for d in plan["detail"] if "answered no" in d]
    assert len(said) == 2 and all(" not tried, your bot's brain answered no to " in d for d in said)
    asked = next(a for a in brain.asked if "budget_tracker" in a)
    assert asked == ("Is budget_tracker: A web app to record spending and compare it with a budget. an app for "
                     "Keeps track of your spending limits across all wedding categories?")
    card = store.card("g", [{"n": 0, "name": job["name"], "pasted": [], "plan": dict(plan, reuse=None)}], [],
                      {"where": "local"})
    assert "answered no" not in json.dumps(card)
    # A brain that says yes to all, or can't answer, keeps exactly the word check's order.
    assert _names(_plan(tmp_path, job, brain=Brain(), cache="c")) == words_only
    assert _names(_plan(tmp_path, job, brain=lambda s, u: "maybe", cache="d")) == words_only


def test_worldmonitor_stays_first_with_the_brain_check(tmp_path):
    from test_i4b_build_flow import _wm_ports
    ports = _wm_ports(tmp_path)
    ports.brain = Brain()
    f = find.Finder(ports, tmp_path / "c")
    plan = f.plan_job({"n": 0, "name": "World news dashboard", "does": "a live map of what is happening in the world",
                       "sample_ask": "", "pasted": []}, [["world", "news"]], [], {})
    assert plan["candidates"][0]["full_name"] == "koala73/worldmonitor"


# -- 1. every common form of a GitHub address -------------------------------------------------------------------
PASTED = "https://github.com/nonomu/Wedding-Planner"
FORMS = ["https://github.com/nonomu/Wedding-Planner", "github.com/nonomu/Wedding-Planner",
         "www.github.com/nonomu/Wedding-Planner", "https://www.github.com/nonomu/Wedding-Planner",
         "https://github.com/nonomu/Wedding-Planner/tree/main", "https://github.com/nonomu/Wedding-Planner/tree/dev/src",
         "https://github.com/nonomu/Wedding-Planner/blob/main/README.md", "https://github.com/nonomu/Wedding-Planner#readme",
         "https://github.com/nonomu/Wedding-Planner?tab=readme-ov-file", "https://github.com/nonomu/Wedding-Planner.git",
         "git@github.com:nonomu/Wedding-Planner.git", "http://GitHub.com/nonomu/Wedding-Planner/",
         "  <https://github.com/nonomu/Wedding-Planner>  "]
NOT_FORMS = ["wedding planner", "https://gitlab.com/nonomu/Wedding-Planner", "https://github.com/nonomu",
             "https://github.com/topics/wedding", "https://github.com/orgs/nonomu/repositories",
             "https://github.com.example.net/nonomu/Wedding-Planner", "https://notgithub.com/nonomu/Wedding-Planner"]


@pytest.mark.parametrize("form", FORMS)
def test_every_github_form_is_her_project_alone(tmp_path, form):
    assert find.github_of(form) == "nonomu/Wedding-Planner" and find.github_url(form) == PASTED
    assert store._pasted([form]) == [PASTED]
    ports = _ports(tmp_path)
    job = dict({k: v for k, v in _job("Vendor Tracker").items() if k != "phrases"}, pasted=[form])
    plan = find.Finder(ports, tmp_path / "c").plan_job(job, [["vendor", "tracking"]], [], {})
    assert _names(plan) == ["nonomu/Wedding-Planner"]                              # tried alone
    assert not any("search/repositories" in u for u in ports.asked)                # no search, no fallbacks


@pytest.mark.parametrize("form", NOT_FORMS)
def test_what_is_not_a_github_project_address_is_not_one(form):
    assert find.github_of(form) == "" and store._pasted([form]) == []


@pytest.mark.parametrize("form", FORMS[1:4] + FORMS[6:9])
def test_the_wizard_says_only_what_really_parsed(state, form):
    eng = Kit(state).engine()
    eng.set_build("b1")
    screen = eng.submit("plan", {"project": form})
    assert screen["status"] == "done"
    assert "Toolsmith tries github.com/nonomu/Wedding-Planner only." in screen["lines"]


def test_the_wizard_asks_again_for_an_address_that_is_not_github(state):
    from puffpai_core.wiring import NOT_GITHUB_LINE
    eng = Kit(state).engine()
    eng.set_build("b1")
    screen = eng.submit("plan", {"project": "wedding planner app"})
    assert screen.get("error") == NOT_GITHUB_LINE and screen["field"]["id"] == "project", screen
    assert not any("only." in x for x in screen.get("lines") or [])


def test_the_core_plans_nothing_again_for_an_address_that_is_not_github(real, state):  # noqa: F811
    from puffpai_core.wiring import NOT_GITHUB_LINE
    app, core, host = real
    run = TerminalRun(_kit(state, core, providers=False))
    run.step("brain", {"model": ""})
    run.step("toolsmith", {"where": "local"})
    run.step("goal", {"goal": "daily news headlines"})
    first = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))["build"]
    got = core.toolsmith.plan(first, "https://gitlab.com/example/hello-page")
    assert got == {"summary": None, "items": [NOT_GITHUB_LINE]}
    assert "replaced_by" not in host.view(first)


# -- 2. her pasted project always gets its try ---------------------------------------------------------------
def test_a_pasted_project_with_no_run_steps_is_still_tried(tmp_path):
    job = dict(_job("Vendor Tracker"), pasted=[PASTED])
    plan = _plan(tmp_path, job)
    (cand,) = plan["candidates"]
    assert cand["full_name"] == "nonomu/Wedding-Planner" and cand["source"] == "pasted"
    assert cand["readme_steps"] == [] and cand["runtime"] == "node" and cand["readme_port"] == 3000
    assert not any(n["ref"] == "github:nonomu/Wedding-Planner" for n in plan["not_used"])
    line = store.card("g", [{"n": 0, "name": job["name"], "plan": dict(plan, reuse=None)}], [],
                      {"where": "local"})["lines"][0]
    assert line.startswith("Vendor Tracker: try Wedding-Planner only, the project you gave")
    assert find.NO_STEPS_LINE in line and "Paste a GitHub address" not in line


def test_when_github_cant_be_reached_that_is_the_reason(tmp_path):
    """The Bridge closed: the card and the job line say GitHub could not be reached, never "no README"."""
    from puffpai_core.zone3.toolsmith.errors import Refused
    ports = _ports(tmp_path)

    def closed(url, **kw):
        raise Refused("The Bridge is closed for raw.githubusercontent.com, so nothing was sent.")
    ports.http_get = closed
    notes = []
    f = find.Finder(ports, tmp_path / "c", say=notes.append)
    job = dict({k: v for k, v in _job("Vendor Tracker").items() if k != "phrases"}, pasted=[PASTED])
    plan = f.plan_job(job, [], [], {})
    plan["notes"] = list(dict.fromkeys(notes))
    reason = "GitHub could not be reached: the Bridge is closed, so nothing was sent"
    assert plan["candidates"] == [] and _why(plan, "nonomu/Wedding-Planner") == reason
    line = find.nothing_line(dict(job, plan=plan))
    assert line == f"Nothing found yet: Wedding-Planner: {reason}."
    assert "no README" not in line and line.count("Bridge is closed") == 1
    card = store.card("g", [dict(job, n=0, plan=dict(plan, reuse=None))], [], {"where": "local"})
    assert card["lines"] == [f"Vendor Tracker: {line}"]
    assert find.unreachable_line("That address could not be reached.") == "GitHub could not be reached"


def test_a_failed_try_of_a_project_with_no_steps_says_why(tmp_path, state):
    ports = _ports(tmp_path)
    ports.local_replies = Script([{"content": [tool("give_up", "g1", reason="no start command was found")],
                                   "stop": "tool_use"}] * 2)
    w = World(tmp_path, state, ports)
    try:
        job = {k: v for k, v in _job("Vendor Tracker").items() if k != "phrases"}
        b = w.host.plan("A wedding planner bot", [dict(job, pasted=["github.com/nonomu/Wedding-Planner#readme"])])
        assert b["jobs_in"][0]["pasted"] == [PASTED]                             # kept in the one form
        assert [c["full_name"] for c in b["jobs"][0]["plan"]["candidates"]] == ["nonomu/Wedding-Planner"]
        assert "registry.npmjs.org" in b["envelope"]["install_hosts"]          # how it runs is on the card
        ports.consent.say_yes(b["consent"]["id"])
        done = w.host.start(b["id"], b["consent"]["id"])
        run = done["jobs"][0]["run"]
        assert run["state"] == "not_yet"
        assert ("Wedding-Planner: its README does not say how to start it, and Toolsmith's try from its files "
                "ended: no start command was found.") in run["line"]
        first = ports.local_replies.seen[0][0]["content"][0]["text"]
        assert "Its README does not say how to start it. The owner gave you this project" in first
    finally:
        w.host.stop(timeout=10)


# -- 5. her words on cards and job lines -----------------------------------------------------------------------
class FitLink:
    def __init__(self, called):
        self.called = called

    def fits(self, package, ask):
        return {"ok": False, "asked": True, "package": "", "called": [self.called],
                "reason": f"the brain called {self.called} instead"}


ROWS = [{"id": "freshtable", "name": "FreshTable", "kind": "app", "status": "running",
         "abilities": [{"tool": "freshtable.list_pantry",
                        "description": "List what is in the pantry. Filter by a category."},
                       {"tool": "freshtable.add_shopping", "description": "Put an item on the shopping list."},
                       {"tool": "freshtable.odd", "description": ""}]}]


def test_the_card_says_nothing_on_your_bot_without_tool_names(tmp_path):
    f = find.Finder(FitLink("search_knowledge"), tmp_path)
    not_used = []
    job = _job("Theme Designer")
    assert f.brain_reuse(job, ROWS, not_used) is None
    (note,) = not_used
    assert note["why"] == find.NOTHING_ON_BOT
    assert "search_knowledge" in note["detail"] and job["sample_ask"][:40] in note["detail"]   # the build log
    line = find.nothing_line({"name": job["name"], "pasted": [], "plan": {"not_used": not_used}})
    assert line == "Nothing found yet: nothing on your bot does this yet. Paste a GitHub address for this job."
    kept = store._note(note)
    assert kept["why"] == find.NOTHING_ON_BOT and "search_knowledge" in kept["detail"]
    card = store.card("g", [{"n": 0, "name": job["name"], "pasted": [],
                             "plan": {"reuse": None, "candidates": [], "not_used": [kept]}}], [], {"where": "local"})
    assert "search_knowledge" not in json.dumps(card) and "asked" not in json.dumps(card)


def test_a_tool_is_said_by_what_it_does():
    assert find.tool_does("freshtable.list_pantry", ROWS) == "list what is in the pantry"
    assert find.tool_does("freshtable.odd", ROWS) == "" and find.tool_does("nope.x", ROWS) == ""
    assert find.tools_said(["freshtable.odd"], ROWS) == find.NO_TOOL_WORDS == "it has a tool for this job"
    reuse = {"n": 0, "name": "Pantry", "plan": {"reuse": "freshtable", "reuse_name": "FreshTable", "candidates": [],
                                                 "picked": [{"tool": "freshtable.list_pantry"}]}}
    offer = {"n": 1, "name": "Shopping", "plan": {"reuse": None, "candidates": [], "not_used": [],
                                                  "offer": {"package": "freshtable", "name": "FreshTable",
                                                            "tool": "freshtable.add_shopping"}}}
    card = store.card("g", [reuse, offer], [], {"where": "local"}, rows=ROWS)
    assert card["lines"][0] == "Pantry: FreshTable, already on your bot: it would list what is in the pantry."
    assert card["lines"][1] == ("Shopping: FreshTable may cover this: it would put an item on the shopping list. "
                                "Tick to use it instead of searching.")
    assert card["offers"][0]["does"] == "put an item on the shopping list"
    shown = " ".join(card["lines"])
    assert "list_pantry" not in shown and "add_shopping" not in shown


def test_an_address_is_asked_for_only_when_she_pasted_none():
    nothing = {"reuse": None, "candidates": [], "not_used": []}
    jobs = [{"n": 0, "name": "Vendor Tracker", "pasted": [PASTED], "plan": dict(nothing)},
            {"n": 1, "name": "Theme Designer", "pasted": [], "plan": dict(nothing)}]
    lines = store.card("g", jobs, [], {"where": "local"})["lines"]
    assert all("Paste a GitHub address" not in x for x in lines)
    lines = store.card("g", [dict(jobs[1])], [], {"where": "local"})["lines"]
    assert lines == ["Theme Designer: Nothing found yet. Paste a GitHub address for this job."]


# -- 4. a newer plan closes the old one's card ------------------------------------------------------------------
def test_a_newer_plan_closes_the_old_plans_card(real, state):  # noqa: F811 - the fixture
    app, core, host = real
    run = TerminalRun(_kit(state, core, providers=False))
    run.step("brain", {"model": ""})
    run.step("toolsmith", {"where": "local"})
    run.step("goal", {"goal": "daily news headlines"})
    first = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))["build"]
    assert run.step("plan", {"project": ""})["status"] == "done"
    old = host.view(first)
    cid = old["consent"]["id"]
    assert cid and core.consent.get(cid)["status"] == "pending"

    got = core.toolsmith.plan(first, "github.com/example/hello-page/tree/main")     # she pasted an address
    new = got["build"]
    assert new != first and host.view(new)["consent"]["id"] != cid
    assert host.view(new)["jobs_in"][0]["pasted"] == ["https://github.com/example/hello-page"]

    rec = core.consent.get(cid)
    assert rec["status"] == "expired" and rec["note"] == store.REPLACED_LINE
    assert cid not in [r["consent_id"] for r in core.consent.list("pending")]          # gone from the Desktop
    with pytest.raises(ConsentError) as e:                                            # a Yes there starts nothing
        core.consent.answer(cid, "yes", "tap", by="owner", card_sha=rec["card_sha"])
    assert str(e.value) == store.REPLACED_LINE
    with pytest.raises(PermissionError) as e:
        host.start(first, cid)
    assert str(e.value) == store.REPLACED_LINE
    old = host.view(first)
    assert old["replaced_by"] == new and old["consent"]["said"] == store.REPLACED_LINE
    assert all(j["status"] == "replaced" and j["run"]["line"] == store.REPLACED_LINE for j in old["jobs"])
    assert core.toolsmith.yes(first)["ok"] is False
    assert not host.replace(first, new)                                               # once only
    assert core.consent.get(host.view(new)["consent"]["id"])["status"] == "pending"   # the new card is hers to answer


def _planned(tmp_path, state):
    w = World(tmp_path, state, _ports(tmp_path))
    b = w.host.plan("A wedding planner bot", [{k: v for k, v in _job("Vendor Tracker").items() if k != "phrases"}])
    assert b["consent"]["id"], b
    w.ports.consent.say_yes(b["consent"]["id"])
    return w, b


def test_a_replace_between_starts_check_and_its_write_wins(tmp_path, state, monkeypatch):
    """The race: start's Yes check passes, then a newer plan replaces the build before start writes its Yes.
    The write itself (under the store's lock) refuses: nothing starts."""
    w, b = _planned(tmp_path, state)
    try:
        check = w.host._yes_ok

        def check_then_replace(build, cid):
            why = check(build, cid)
            assert why == ""                                             # the check passed...
            assert w.host.replace(b["id"], "20261025-000000-abcdef")     # ...and the replace lands right after
            return why
        monkeypatch.setattr(w.host, "_yes_ok", check_then_replace)
        with pytest.raises(PermissionError) as e:
            w.host.start(b["id"], b["consent"]["id"])
        assert str(e.value) == store.REPLACED_LINE
        assert ("build", b["id"]) not in w.runner.runs and not w.host.view(b["id"]).get("yes")
    finally:
        w.host.stop(timeout=10)


def test_a_start_that_wrote_its_yes_first_is_never_replaced(tmp_path, state, monkeypatch):
    """The other order: start's write lands first, then replace finds a started build and leaves it alone."""
    w, b = _planned(tmp_path, state)
    try:
        results = []

        def replace_then_launch(do, build_id):
            results.append(w.host.replace(build_id, "20261025-000000-abcdef"))
            return None                                        # the build itself is not needed here
        monkeypatch.setattr(w.host, "_launch", replace_then_launch)
        w.host.start(b["id"], b["consent"]["id"])
        assert results == [False] and w.host.view(b["id"])["yes"] and "replaced_by" not in w.host.view(b["id"])
    finally:
        w.host.stop(timeout=10)


def test_a_build_her_yes_started_is_never_replaced(tmp_path, state):
    w = World(tmp_path, state)
    try:
        b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
        assert w.host.replace(b["id"], "20261025-000000-abcdef") is False
        assert "replaced_by" not in w.host.view(b["id"])
    finally:
        w.host.stop(timeout=10)
