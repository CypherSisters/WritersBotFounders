"""Lane i23g (D104): the library proof reaches a job's own phrase, knowledge topics stay whole and leave a pasted
app alone, and the plan card's words read plainly. Each is tried on unrelated goals: a fitness bot, a writing
editor bot and a garden helper."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from puffpai_core.consent import shown
from puffpai_core.zone3.toolsmith import agent, find
from puffpai_core.zone3.toolsmith import knowledge as ts_knowledge
from puffpai_core.zone3.toolsmith.names import cut_text, short_name
from puffpai_core.zone3.toolsmith_host import interpret, store
from puffpai_core.zone3.toolsmith_host import knowledge as kmod
from puffpai_core.zone3.toolsmith_host import trials
from test_i19e_library_jobs import _ended, _fetch, _world
from test_i3b_bridge_cards import held_core  # noqa: F401 - lane I3b's real held try
from test_i4b_build_flow import _held_try
from toolsmith_fakes import Link, flask_transport

#: Each job: a 6-word ask with a plural, so the old order spent the cap before its 2-word phrase was asked.
JOBS = {
    "fitness": {"name": "Blood Pressure Monitor", "phrase": "blood pressure",
                "does": "Keep daily readings and spot trends over several months for healthy habits",
                "sample_ask": "Are morning readings getting closer to normal ranges lately?"},
    "writing": {"name": "Plot Structure Coach", "phrase": "plot structure",
                "does": "Compare novels and suggest chapter arcs with stronger tension for authors",
                "sample_ask": "Which novels handle chapter endings with lasting suspense?"},
    "garden": {"name": "Soil Acidity Advisor", "phrase": "soil acidity",
               "does": "Reads garden beds and suggests seasonal amendments for healthier vegetables",
               "sample_ask": "Should raised planters receive extra lime before planting vegetables?"},
}


# -- N15: the library proof reaches the job's own phrase ----------------------------------------------------------

@pytest.mark.parametrize("key", list(JOBS))
def test_the_jobs_own_two_word_phrase_is_asked_within_the_cap(key):
    job = JOBS[key]
    words = kmod.prove_words(job["sample_ask"], job["does"], job["name"])
    assert len(words) == kmod.PROVE_WORDS and any(w != kmod._singular(w) for w in words)
    assert job["phrase"].split()[0] not in words                    # her ask and its words fill the 6 first
    queries = kmod.prove_queries(words, kmod.prove_pairs(job["name"], job["sample_ask"], job["does"]))
    assert job["phrase"] in queries and len(queries) <= kmod.PROVE_QUERIES
    assert len(queries) == len(set(queries))
    assert queries[0] == " ".join(words)                            # still the most words first
    sizes = [len(q.split()) for q in queries]
    assert sizes == sorted(sizes, reverse=True)
    assert set(sizes) == set(range(2, kmod.PROVE_WORDS + 1))        # every size gets a turn


def test_without_the_pairs_each_size_still_gets_a_turn_and_the_side_by_side_pairs_come_first():
    words = ["readings", "morning", "ranges", "normal", "lately", "trends"]
    queries = kmod.prove_queries(words)
    assert len(queries) == kmod.PROVE_QUERIES
    assert {"reading morning", "morning ranges", "ranges normal", "normal lately", "lately trends"} <= set(queries)
    assert set(len(q.split()) for q in queries) == {2, 3, 4, 5, 6}
    two = [q for q in queries if len(q.split()) == 2]
    assert two[0] in ("readings morning", "reading morning")        # a real phrase before a loose pair


def test_one_word_and_no_word_are_as_before():
    assert sorted(kmod.prove_queries(["invoices"])) == ["invoice", "invoices"]
    assert kmod.prove_queries([]) == []
    assert kmod.prove_pairs("A guide to the") == []


@pytest.mark.parametrize("key,title", [("fitness", "Hypertension"), ("writing", "Narrative"), ("garden", "Liming")])
def test_a_job_the_library_has_is_proved_by_its_phrase(tmp_path, state, key, title):
    job = JOBS[key]
    a, b = job["phrase"].split()
    searches: list = []
    w = _world(tmp_path, state, searches)
    goals = {key: {"goal": f"a {key} bot", "job": {k: job[k] for k in ("name", "does", "sample_ask")}}}
    import test_i19e_library_jobs as i19e
    i19e.GOALS.update(goals)
    try:
        built = _ended(w, key)
    finally:
        i19e.GOALS.pop(key)
    _fetch(w, built, [("wikipedia", title, f"Notes about {a} {b} in general use today. ", 2),
                      ("wikipedia", f"{title} two", f"More about {a} and {b} for people. ", 2)])
    got = w.link(built["id"]).library_check(built["jobs"][0]["id"])
    assert job["phrase"] in searches
    assert got
    assert w.host.store.read(built["id"])["jobs"][0]["run"]["state"] == "working"


# -- T2 and N17: whole topics, and none for a pasted app ----------------------------------------------------------

class _Brain:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def brain_json(self, system, user):
        ask = json.loads(user)
        self.asked.append(ask["job"])
        return {"topics": list(self.answers.get(ask["job"], []))}


@pytest.mark.parametrize("given,kept", [
    (["character development", "character", "development", "narrative"], ["character development", "narrative"]),
    (["strength", "strength training", "training", "aerobics"], ["strength training", "aerobics"]),
    (["composting", "soil pH", "Soil", "plants", "garden plants"], ["composting", "soil pH", "garden plants"]),
])
def test_a_kept_phrase_has_no_single_word_copies_and_a_real_one_word_topic_stays(given, kept):
    report: dict = {}
    assert ts_knowledge.topics(_Brain({"The job": given}), "a goal", [{"name": "The job", "does": ""}], report) == kept
    assert report["gave"] == len(given)


def test_a_phrase_from_another_job_also_takes_out_the_single_word():
    brain = _Brain({"Coach": ["running"], "Plan": ["running shoes", "stretching"]})
    got = ts_knowledge.topics(brain, "a goal", [{"name": "Coach", "does": ""}, {"name": "Plan", "does": ""}])
    assert got == ["running shoes", "stretching"]


def test_a_single_word_copy_does_not_use_up_a_jobs_share():
    jobs = [{"name": str(n), "does": ""} for n in range(7)]        # 15 // 7 is a share of 2
    brain = _Brain({"0": ["plot", "plot twist", "pacing"]})
    assert ts_knowledge.topics(brain, "a goal", jobs) == ["plot twist", "pacing"]


@pytest.mark.parametrize("goal,address", [
    ("Add a workout timer from GitHub", "https://github.com/example/workout-timer"),
    ("Add sprint from GitHub", "https://github.com/example/sprint"),
    ("Add this seed calendar", "https://github.com/example/seed-calendar"),
])
def test_a_paste_only_job_asks_no_topics_while_a_normal_job_does(goal, address):
    brain = _Brain({"Pasted": ["git hub", "code repository"], "Normal": ["seed saving", "crop rotation"]})
    report: dict = {}
    pasted = {"name": "Pasted", "does": goal, "pasted": [address]}
    assert ts_knowledge.topics(brain, goal, [pasted], report) == []
    assert brain.asked == [] and report["jobs"] == 0 and report["gave"] == 0
    both = ts_knowledge.topics(brain, goal, [pasted, {"name": "Normal", "does": goal, "pasted": []}])
    assert both == ["seed saving", "crop rotation"] and brain.asked == ["Normal"]


def test_a_paste_only_plan_never_says_the_brain_picked_no_topics():
    pasted = {"name": "Add it", "n": 0, "pasted": ["https://github.com/example/x"],
              "plan": {"reuse": None, "candidates": [], "not_used": []}}          # GitHub could not be read
    assert store.no_topics_said([pasted]) is False
    normal = {"name": "Herb guide", "n": 0, "pasted": [], "plan": {"reuse": None, "candidates": [], "not_used": []}}
    assert store.no_topics_said([normal]) is True


# -- T1 and N4: only a near miss is named, and once -------------------------------------------------------------

def _nothing(*rows, code=(), notes=()):
    plan = {"reuse": None, "candidates": [], "code": list(code), "notes": list(notes),
            "not_used": [{"name": n, "ref": f"github:x/{n}", "why": why} for n, why in rows]}
    return {"n": 0, "name": "Mobility Coach", "does": "stretches", "pasted": [], "plan": plan}


@pytest.mark.parametrize("names", [
    ("FTC-Skystone-Team", "new-pac-wiki", "A1x"),
    ("hip-hop-lyrics", "x-list", "yarn-dump"),
    ("compost-copy", "garden-wiki", "zed-notes"),
])
def test_junk_is_one_count_and_never_named(names):
    job = _nothing((names[0], "a description stuffed with words"), (names[1], "no code (a list or documents)"),
                   (names[2], find.BRAIN_NO_WHY))
    line = find.nothing_line(job)
    assert line == "Nothing found yet: 3 projects that did not fit were left out. Paste a GitHub address for this job."
    assert not any(n in line for n in names)
    assert [n["why"] for n in job["plan"]["not_used"]][0] == "a description stuffed with words"     # kept for the job page


def test_one_is_said_as_one_and_a_near_miss_keeps_its_reason():
    job = _nothing(("bench-log", "a part for another app (Home Assistant)"), ("junk", "a list"))
    assert find.nothing_line(job, pasted=True) == ("Nothing found yet: bench-log: a part for another app (Home "
                                                   "Assistant); 1 project that did not fit was left out.")


def test_the_note_that_counts_the_off_subject_ones_is_not_said_twice():
    never = f"{find.NEVER_MENTIONS} \"herbs\""
    job = _nothing(("a", never), ("b", never), ("c", "a list"),
                   notes=['2 projects came up in the search, but none is about "herbs"'])
    assert find.nothing_line(job, pasted=True) == ('Nothing found yet: 2 projects came up in the search, but none is '
                                                   'about "herbs"; 1 project that did not fit was left out.')


@pytest.mark.parametrize("app", ["NutriVerse-Coach", "Novel-Notes", "Seed-Keeper"])
def test_code_about_the_job_is_named_once_on_the_card_and_kept_on_the_job_line(app):
    job = _nothing((app, find.CODE_WHY), code=[{"name": app, "ref": f"code:x/{app}", "url": "u", "why": find.CODE_WHY}])
    lines = store.card("a goal", [job], [], {"where": "local"})["lines"]
    mine = [ln for ln in lines if app in ln]
    assert len(mine) == 1 and "does not start as an app by itself" in mine[0]
    assert lines[0] == "Mobility Coach: Nothing found yet. Paste a GitHub address for this job."
    assert app in find.nothing_line(job)                             # no code line after a job's own run line


def test_a_read_project_that_is_not_about_the_job_says_so_and_is_counted(tmp_path):
    item = {"full_name": "someone/vpn-notes", "name": "vpn-notes", "description": "notes on proxies",
            "language": "Python", "stargazers_count": 90}
    f = find.Finder(SimpleNamespace(), tmp_path / "c")
    f.brain_reuse = lambda *a: None
    f.store_reuse = lambda job: {}
    f.search_job = lambda job, phrases: [item]
    f.rank = lambda job, items, phrases: items
    f.readme = lambda where, branch="": "# vpn notes\nJust words about proxies.\n"
    f.run_file = lambda where, branch="": ""
    job = {"n": 0, "name": "Herb Garden", "does": "keep herbs watered", "sample_ask": "", "pasted": []}
    plan = f.plan_job(job, [["herb", "garden"]], [], {})
    assert plan["not_used"][0]["why"].startswith(find.NEVER_MENTIONS)
    line = find.nothing_line({**job, "plan": plan})
    assert "vpn-notes" not in line and "1 project came up in the search" in line and "did not fit" not in line


# -- T3: one name is enough --------------------------------------------------------------------------------------

@pytest.mark.parametrize("goal,extra,job,word", [
    ("Help with outlines and plots.", "Plot Helper", "Outline writer", "plots"),
    ("Log my lifts, and my diets.", "Diet Tracker", "Lift log", "diets"),
    ("Watch my beds, and my birds.", "Bird Spotter", "Bed watcher", "birds"),
])
def test_a_plural_of_a_job_past_the_cap_is_not_said_twice(goal, extra, job, word):
    left = interpret.left_out(goal, [{"name": job, "does": "", "sample_ask": ""}], [extra])
    assert left == [extra]
    line = interpret.left_out_line(left)
    assert line.startswith(f"Not a job on this plan yet: {extra}. To add one") and f"; {word}" not in line


def test_a_short_word_that_only_looks_plural_is_not_counted_as_covered():
    jobs = [{"name": "New recipes", "does": "", "sample_ask": ""}]
    assert interpret.left_out("New recipes, and news", jobs) == ["news"]


# -- T4, N2, N3: the card's own words ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["FitTrackee", "prose-polish", "PlantKeeper"])
def test_no_double_stop_after_where_it_tries(name):
    docker = {"name": name, "ref": f"github:x/{name}", "source": "github", "tries_in": "docker", "readme_steps": ["a"]}
    plain = {**docker, "tries_in": "native"}
    for cands in ([docker], [docker, plain], [{**docker, "source": "pasted"}]):
        job = {"n": 0, "name": "A job", "pasted": [], "plan": {"reuse": None, "candidates": cands, "not_used": []}}
        line = store.card("g", [job], [], {"where": "local"})["lines"][0]
        assert ".)" not in line and "Docker" not in line and "(Tries as a program on this computer, as you." in line
    job["plan"]["candidates"] = [plain]
    line = store.card("g", [job], [], {"where": "local"})["lines"][0]
    assert line.endswith("It could read your files and change PuffPAI).")


@pytest.mark.parametrize("goal,ends", [
    ("I want to exercise more, lose weight, get my blood pressure down, and start eating better with fresh food each "
     "day of the week for my family", "each day of"),
    ("Help me outline my novel. Check my syntax and my spelling every day. Also help with book character development, "
     "world building, and plots.", "every day"),
    ("Tell me when to water my tomatoes and what to plant next spring in the raised beds behind the old shed near the "
     "fence and all along the path", "near the fence"),
])
def test_a_cut_goal_says_it_is_cut_and_ends_at_a_word(goal, ends):
    said = store.card(goal, [], [], {"where": "local"})["sentence"]
    quoted = said.split('"')[1]
    assert len(quoted) <= 120 and quoted.endswith("...") and not quoted.endswith("....")
    kept = quoted[:-3]
    assert goal.startswith(kept) and not goal[len(kept):][:1].isalnum() and not kept.endswith((",", " "))
    assert kept.endswith(ends)


def test_a_short_goal_is_not_touched_and_other_callers_add_nothing():
    assert store.card("Help my herbs grow.", [], [], {"where": "local"})["sentence"].count('"Help my herbs grow."') == 1
    long = "The recipe box needs a database server. " * 12
    got = cut_text(long, 100)
    assert not got.endswith("...") and got.endswith(".") and len(got) <= 100
    assert cut_text("short", 100, "...") == "short"
    assert not short_name("a very long name of an app for words here and more", 5).endswith("...")


def _kp(wiki: int, books: int, other: int = 2) -> dict:
    return {"topics": ["a topic"], "offered": [], "fetch": [
        {"source": "wikipedia", "name": "Wikipedia", "host": "w", "per_topic": wiki, "max_items": 40, "about_mb": 1,
         "license": "CC"},
        {"source": "gutenberg", "name": "Project Gutenberg", "host": "g", "per_topic": books, "max_items": 10,
         "max_kb": 1500, "about_mb": 1},
        {"source": "appropedia", "name": "Appropedia", "host": "a", "file_hosts": [], "per_topic": other, "max_items": 9,
         "kind": kmod._cfg()["fetch"]["appropedia"]["kind"], "about_mb": 1, "license": "CC"}]}


def test_one_per_topic_is_said_as_one_and_more_as_many():
    kind = kmod._cfg()["fetch"]["appropedia"]["kind"]
    one = " ".join(kmod.card_lines(_kp(1, 1, 1)))
    assert "up to 1 public domain book per topic" in one and "up to 1 article per topic" in one
    assert f"up to 1 {kind.rstrip('s')} per topic" in one
    many = " ".join(kmod.card_lines(_kp(2, 2, 2)))
    assert "up to 2 public domain books per topic" in many and "up to 2 articles per topic" in many
    assert f"up to 2 {kind} per topic" in many


# -- the GitHub key hint ----------------------------------------------------------------------------------------

def test_the_key_hint_names_the_page_the_wizard_names_and_the_block_there_has_those_words():
    assert "the Developer page, under Link your GitHub" in find.limit_line(False, 2)
    assert "Apps >" not in find.KEY_HINT
    root = Path(__file__).resolve().parents[1]
    block = (root / "packages/desktop/web/github.js").read_text(encoding="utf-8")
    page = (root / "packages/desktop/web/pages/developer.js").read_text(encoding="utf-8")
    wizard = (root / "core/puffpai_core/wizard/engine.py").read_text(encoding="utf-8")
    assert 'text: "Link your GitHub"' in block and "window.PuffGitHub.block" in page
    assert "add one later on the Developer page" in wizard


# -- N9: a store app she ticked -----------------------------------------------------------------------------------

class _Fits:
    def __init__(self, called):
        self.called, self.status = called, []

    def fits(self, package, ask):
        return {"ok": True, "asked": True, "package": package, "called": self.called, "reason": ""}

    def job_status(self, job_id, **fields):
        self.status.append(fields)
        return {}


@pytest.mark.parametrize("pid,name,job", [
    ("freshtable", "FreshTable", "Recipe Assistant"),
    ("draftdesk", "DraftDesk", "Novel notes"),
    ("sproutlog", "SproutLog", "Seed log"),
])
def test_a_ticked_store_app_reads_like_the_add_card_and_others_stay(pid, name, job):
    def line(reuse_by):
        b = agent.Builder.__new__(agent.Builder)
        b.link = _Fits([f"{pid}.do_it"])
        row = {"id": pid, "name": name, "status": "running", "health": {"ok": True}, "examples": {}}
        b._prove_tools({"id": "j1", "name": job, "does": "", "plan": {"reuse_by": reuse_by}}, row)
        return b.link.status[-1]["line"]
    assert line("store").startswith(f"You said Yes. {name} is added: ")
    assert "already on your bot" not in line("store")
    assert line("tick").startswith(f"{name} is already on your bot, you ticked it for this job")
    assert line(None).startswith(f"{name} is already on your bot:")


# -- N18: one app, set aside, with no other to try first ----------------------------------------------------------

ONE = "github:example/news-board"


def _one_app_build(held_core, cands):  # noqa: F811
    app, core, host, ports = held_core
    st = host.settings()
    job = {"name": "Stretch timer", "does": "Times my stretches", "sample_ask": "Start a timer"}
    b = host.store.create("a stretch timer", [job], st)
    host.store.set_plan(b["id"], {"jobs": [{**job, "plan": {"candidates": cands}}]}, st, "")

    def yes(x):
        x["yes"] = {"given": True, "consent_id": "c_test", "sha": x["plan_sha"]}
    b = host.store.change(b["id"], yes)
    return b, Link(flask_transport(app, core.tokens.issue_toolsmith(b["id"])), b["id"])


def test_one_held_app_is_asked_plainly_with_no_other_apps_named(held_core):  # noqa: F811
    from test_i4b_build_flow import CANDS2
    app, core, host, ports = held_core
    ports.held = {"example/news-board"}
    cand = CANDS2[0]
    b, link = _one_app_build(held_core, [cand])
    jid = b["jobs"][0]["id"]
    held = _held_try(link, jid, ONE)
    assert held.get("held")
    assert "news-board: set aside for now. Drawbridge couldn't check some of its files. Let Toolsmith try it anyway?" in held["said"]
    assert "first tries" not in held["said"]
    card = link.holds_ask(jid)
    assert card["alone"] is True and card["consent_id"]
    rec = core.consent.get(card["consent_id"])
    title, lines = shown.view(rec["card"], False)["title"], shown.view(rec["card"], False)["lines"]
    first = lines[0]
    assert title.startswith("Let Toolsmith try ")
    assert "Drawbridge couldn't check some of its files. Let Toolsmith try it anyway?" in first
    assert "No app Drawbridge could check fully worked" not in " ".join(lines)
    assert link.holds_ask(jid)["alone"] is True                     # the same card, asked again


def test_two_apps_keep_the_words_about_the_others(held_core):  # noqa: F811
    from test_i4b_build_flow import CANDS2
    app, core, host, ports = held_core
    ports.held = {"example/news-board"}
    b, link = _one_app_build(held_core, CANDS2)
    jid = b["jobs"][0]["id"]
    held = _held_try(link, jid, ONE)
    assert "first tries the apps it could check fully" in held["said"]
    assert _held_try(link, jid, "github:example/hello-page")["ok"]
    card = link.holds_ask(jid)
    assert card["alone"] is False
    rec = core.consent.get(card["consent_id"])
    assert "No app Drawbridge could check fully worked" in shown.view(rec["card"], False)["lines"][0]


@pytest.mark.parametrize("name", ["Virtual-Trainer", "harper", "seed-keeper"])
def test_the_hold_line_itself(name):
    shown_files = {"held_said": ["2 files of a kind Drawbridge can't read: a.bin, b.bin."]}
    alone = trials.hold_line(name, shown_files, True)
    assert alone == (f"{name}: set aside for now. Drawbridge couldn't check some of its files. Let Toolsmith try it "
                     "anyway? Nothing from it runs unless you say Yes. 2 files of a kind Drawbridge can't read: a.bin, b.bin.")
    assert "first tries" in trials.hold_line(name, shown_files)


def test_the_job_says_it_plainly_when_the_hold_card_is_up():
    class L:
        said: list = []

        def holds_ask(self, jid):
            return {"consent_id": "c", "title": "t", "names": ["harper"], "alone": True}

        def holds_state(self, jid):
            return {"state": "answered", "try": [], "skipped": []}

        def registry(self):
            return {"rows": []}

    class Hooks:
        label, said = "", []

        def status(self, *a, **k):
            pass

        def say(self, line):
            self.said.append(line)

        def wait_for(self, fn, secs):
            return fn()

    b = agent.Builder.__new__(agent.Builder)
    b.link = L()
    hooks = Hooks()
    b._ask_holds({"id": "j1"}, [{"name": "harper", "ref": "r"}], hooks, [])
    assert hooks.said[0].startswith("Drawbridge couldn't check some of the files of harper")
    assert "No app" not in hooks.said[0]


# -- the words themselves ---------------------------------------------------------------------------------------

def test_the_new_lines_keep_the_writing_rules():
    lines = [trials.hold_line("x", {}, True), find.nothing_line(_nothing(("a", "a list"))), find.KEY_HINT]
    for line in lines:
        assert chr(0x2014) not in line and not re.search(r"\bbeta\b|\bwarts\b", line)
