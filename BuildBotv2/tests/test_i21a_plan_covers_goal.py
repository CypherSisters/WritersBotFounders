"""Lane I21a (D84): the plan covers the whole goal, says what it left out, and asks topics per job.

From the owner's test5: a goal with five asks got 3 jobs (the goal step allowed 1 to 3) and two asks were
dropped with no word; one topic ask for the whole goal gave no knowledge topics and the card said nothing;
after her Yes on an app's add card the job still read "It waits for your Yes"; and the persona promised the
dropped asks. Every test uses goals that have nothing to do with hers (the factory rule) and a stub brain.
No network, no ports.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from toolsmith_fakes import World
from puffpai_core.wiring import WizardToolsmith
from puffpai_core.zone2 import persona
from puffpai_core.zone3.toolsmith import adopt, find
from puffpai_core.zone3.toolsmith import knowledge as ts_knowledge
from puffpai_core.zone3.toolsmith_host import interpret
from puffpai_core.zone3.toolsmith_host.knowledge import NO_TOPICS_LINE

RECIPES = "a home recipe box, a weekly meal planner, a grocery list, wine pairing and a kitchen timer"
SHOP = "invoices, receipts, a customer list, appointment reminders and tax notes for my small shop"
BIRDS = "a birdwatching log, bird song ID, migration maps, a photo journal and a field guide"

FIVE = {
    RECIPES: [("Recipe box", "Keeps your recipes"), ("Meal planner", "Plans meals for the week"),
              ("Grocery list", "Keeps the shopping items"), ("Wine pairing", "Suggests a wine for a dish"),
              ("Kitchen timer", "Times what is cooking")],
    SHOP: [("Invoices", "Makes and tracks invoices"), ("Receipts", "Keeps receipts"),
           ("Customer list", "Keeps customers"), ("Appointment reminders", "Reminds you of appointments"),
           ("Tax notes", "Keeps notes for taxes")],
    BIRDS: [("Birdwatching log", "Logs the birds you see"), ("Bird song ID", "Names a bird by its song"),
            ("Migration maps", "Shows where birds migrate"), ("Photo journal", "Keeps bird photos"),
            ("Field guide", "Describes each bird")],
}

EIGHT = ("a to-do list, a habit tracker, a reading log, a budget sheet, a workout plan, a plant watering "
         "reminder, a travel packing list, a birthday calendar")
EIGHT_JOBS = [("To-do list", "Keeps tasks"), ("Habit tracker", "Tracks habits"), ("Reading log", "Logs books"),
              ("Budget sheet", "Tracks spending"), ("Workout plan", "Plans workouts"),
              ("Plant watering reminder", "Reminds you to water plants"),
              ("Travel packing list", "Lists what to pack"), ("Birthday calendar", "Keeps birthdays")]


def _jobs_answer(rows):
    return {"jobs": [{"name": n, "does": d, "ask": f"What about {n.lower()}?"} for n, d in rows]}


def _interpret_brain(rows):
    def brain(system, user):
        if "TASK=interpret" in system:
            return _jobs_answer(rows)
        return None
    return brain


# -- F1: one job per part, up to 6, and a plain line for the rest ------------------------------------------
def test_the_goal_step_asks_for_up_to_six_jobs_and_never_merges():
    assert interpret.SHORT_GOAL_JOBS == 6
    assert "1 to 6" in interpret.SYSTEM and "1 to 3" not in interpret.SYSTEM
    assert "never leave one out" in interpret.SYSTEM


@pytest.mark.parametrize("goal", [RECIPES, SHOP, BIRDS])
def test_a_five_part_goal_gets_five_jobs_and_nothing_left_out(goal):
    asked = []

    def brain(system, user):
        asked.append(json.loads(user))
        return _jobs_answer(FIVE[goal])
    jobs, problem, left = interpret.jobs_for_goal(goal, brain, True)
    assert [j["name"] for j in jobs] == [n for n, _ in FIVE[goal]]
    assert problem is None and left == [] and interpret.left_out_line(left) == ""
    assert asked[0]["goal"] == goal and len(asked) == 1


@pytest.mark.parametrize("goal", [RECIPES, SHOP, BIRDS])
def test_a_part_the_brain_dropped_is_named_not_lost(goal):
    kept = FIVE[goal][:3]
    jobs, _problem, left = interpret.jobs_for_goal(goal, lambda s, u: _jobs_answer(kept), True)
    assert len(jobs) == 3 and left, left
    line = interpret.left_out_line(left)
    assert line.startswith("Not a job on this plan yet: ") and "ask your bot in chat" in line
    for name, _ in FIVE[goal][3:]:
        assert name.lower().split()[0] in line.lower(), (name, line)
    for name, _ in kept:
        assert name.lower() not in line.lower()


def test_eight_parts_give_six_jobs_and_name_the_other_two():
    jobs, problem, left = interpret.jobs_for_goal(EIGHT, lambda s, u: _jobs_answer(EIGHT_JOBS), True)
    assert len(jobs) == 6 and problem is None
    assert left[:2] == ["Travel packing list", "Birthday calendar"]
    line = interpret.left_out_line(left)
    assert line == ('Not a job on this plan yet: Travel packing list; Birthday calendar. To add one, ask your bot '
                    'in chat, for example "add Travel packing list".')


def test_with_no_brain_the_words_split_keeps_six_and_names_the_rest():
    jobs, problem, left = interpret.jobs_for_goal(EIGHT, None, False)
    assert len(jobs) == 6 and problem is None
    assert left == ["a travel packing list", "a birthday calendar"]
    # The words split does not cut at a plain "and" ("reminders and tax notes" stays one job), but it drops
    # nothing: every part is in a job.
    jobs, _p, left = interpret.jobs_for_goal(SHOP, None, False)
    assert len(jobs) == 4 and left == [] and "tax notes" in jobs[-1]["does"]


def test_the_wizard_goal_step_and_the_plan_card_say_the_left_out_line(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.brain = _interpret_brain(EIGHT_JOBS)
    w.ports.http_send_fn = lambda method, url, headers, body: (404, {}, b"")     # GitHub finds nothing
    ts = WizardToolsmith(w.app)
    ts.host = lambda: w.host
    got = ts.interpret(EIGHT, True)
    assert len(got["jobs"]) == 6 and got["left_out"] == ["Travel packing list", "Birthday calendar"]
    assert got["left_out_line"] == interpret.left_out_line(got["left_out"])
    b = w.host.plan_build(got["build"])
    assert got["left_out_line"] in b["card"]["lines"]
    # The goal step's own lines (the wizard engine) carry it under the jobs.
    from puffpai_core.wizard.engine import GoalStep
    step = GoalStep.__new__(GoalStep)
    step.e = SimpleNamespace(services=SimpleNamespace(toolsmith=ts), task=lambda k, fn: SimpleNamespace(
        done=True, error=None, result=({"state": "ready"}, got)), drop_task=lambda k: None,
        set_build=lambda b: None)
    out = step.finish({}, {"goal": EIGHT}, None)
    assert out["status"] == "done" and out["lines"][-1] == got["left_out_line"]
    assert sum(1 for ln in out["lines"] if ln.startswith("Job ")) == 6


def test_each_of_six_jobs_still_gets_a_github_search(tmp_path):
    """The 10 unkeyed searches per build are shared over the jobs: with 6 jobs each still gets at least one."""
    f = find.Finder(SimpleNamespace(), tmp_path)
    assert f.cap == 10
    per_job = []

    def search(words):
        if f.searches >= f.cap:
            return []
        f.searches += 1
        per_job[-1] += 1
        return []
    f.search, f.awesome, f.clear = search, (lambda words: []), (lambda queue: None)
    jobs = [{"name": n} for n, _ in EIGHT_JOBS[:6]]
    for i, job in enumerate(jobs):
        per_job.append(0)
        f.jobs_left = len(jobs) - i
        f.search_job(job, [["first", "words"], ["second", "words"], ["third", "words"], ["fourth", "words"]])
    assert all(n >= 1 for n in per_job), per_job
    assert sum(per_job) <= 10


# -- F2: topics per learnable job, the no-topics line, and the counts -------------------------------------
class _TopicBrain:
    """Topics for the jobs there is something to learn about; none for a plain tool."""

    def __init__(self, by_job, none=()):
        self.by_job, self.none, self.asked = by_job, set(none), []

    def brain_json(self, system, user):
        ask = json.loads(user)
        self.asked.append((system, ask))
        if ask["job"] in self.none:
            return None
        return {"topics": list(self.by_job.get(ask["job"], []))}


def test_topics_are_asked_per_job_so_each_learnable_job_gets_its_own():
    link = _TopicBrain({"Recipe box": ["home cooking basics", "recipe writing"],
                        "Meal planner": ["meal planning", "balanced diet"],
                        "Grocery list": [],
                        "Wine pairing": ["wine and food pairing", "wine grape varieties"],
                        "Kitchen timer": []})
    jobs = [{"name": n, "does": d} for n, d in FIVE[RECIPES]]
    report = {}
    got = ts_knowledge.topics(link, RECIPES, jobs, report)
    assert got == ["home cooking basics", "recipe writing", "meal planning", "balanced diet",
                   "wine and food pairing", "wine grape varieties"]
    assert [a["job"] for _s, a in link.asked] == [n for n, _ in FIVE[RECIPES]]     # one small ask per job
    system = link.asked[0][0]
    assert "TASK=knowledge_topics" in system and "ONE of its jobs" in system and "2 to 3" in system
    assert all(a["goal"] == RECIPES for _s, a in link.asked)
    assert report == {"jobs": 5, "gave": 6, "no_answer": 0, "timed_out": 0}


@pytest.mark.parametrize("goal", [SHOP, BIRDS])
def test_topics_are_shared_and_capped_over_the_jobs(goal):
    link = _TopicBrain({n: [f"{n.lower()} topic {w}" for w in "abcdefgh"] for n, _ in FIVE[goal]})
    got = ts_knowledge.topics(link, goal, [{"name": n, "does": d} for n, d in FIVE[goal]])
    assert len(got) == 15                                        # 3 per job, 15 in all
    for n, _ in FIVE[goal]:
        assert sum(1 for t in got if t.startswith(n.lower())) == 3


def test_one_job_keeps_the_old_range_and_a_timeout_is_counted():
    link = _TopicBrain({}, none={"Field guide"})
    ticks = iter([0.0, 120.0])
    report = {}
    assert ts_knowledge.topics(link, BIRDS, [{"name": "Field guide", "does": "Describes birds"}], report,
                               clock=lambda: next(ticks)) == []
    assert "5 to 15" in link.asked[0][0]
    assert report == {"jobs": 1, "gave": 0, "no_answer": 1, "timed_out": 1}


def _plan_world(tmp_path, state, topics_for):
    w = World(tmp_path, state)

    def brain(system, user):
        if "TASK=knowledge_topics" in system:
            return {"topics": list(topics_for(json.loads(user)["job"]))}
        return None
    w.ports.brain = brain
    w.ports.http_send_fn = lambda method, url, headers, body: (404, {}, b"")
    return w


def test_the_plan_card_says_no_topics_plainly_and_the_build_keeps_counts(tmp_path, state):
    w = _plan_world(tmp_path, state, lambda job: [])
    b = w.host.plan(SHOP, [{"name": n, "does": d} for n, d in FIVE[SHOP]])
    assert NO_TOPICS_LINE in b["card"]["lines"]
    assert b["knowledge_asked"] == {"jobs": 5, "gave": 0, "no_answer": 0, "timed_out": 0, "kept": 0}


def _job(name, **plan):
    return {"n": 0, "name": name, "pasted": [], "plan": {"reuse": None, "candidates": [], **plan}}


FOUND = [{"name": "Some app", "source": "github", "ref": "github:a/b"}]
PASTED = [{"name": "Their app", "source": "pasted", "ref": "github:c/d"}]
QUIET = {"jobs": 1, "gave": 0, "no_answer": 0, "timed_out": 0}
TIMED_OUT = {"jobs": 1, "gave": 0, "no_answer": 1, "timed_out": 1}


@pytest.mark.parametrize("jobs, asked, said", [
    ([_job("Recipe box", reuse="recipes")], TIMED_OUT, False),             # an app already on the bot
    ([_job("Invoices", offer={"package": "inv", "name": "Inv", "tool": "t"})], TIMED_OUT, False),
    ([_job("Bird song ID", candidates=PASTED)], TIMED_OUT, False),          # only her pasted address
    ([_job("Meal planner", candidates=FOUND)], QUIET, False),               # an app found, the ask answered
    ([_job("Meal planner", candidates=FOUND)], TIMED_OUT, True),            # the ask failed: said
    ([_job("Tax notes")], QUIET, True),                                     # no app: it could need the library
    ([_job("Recipe box", reuse="recipes"), _job("Field guide")], None, True),
])
def test_the_no_topics_line_only_where_it_matters(jobs, asked, said):
    from puffpai_core.zone3.toolsmith_host import store
    lines = store.card("g", jobs, [], {"where": "local"}, knowledge={"topics": []}, asked=asked)["lines"]
    assert (NO_TOPICS_LINE in lines) is said


def test_topics_for_several_jobs_reach_the_plan_card(tmp_path, state):
    topics = {"Birdwatching log": ["birdwatching basics"], "Bird song ID": ["bird song", "bird vocalization"],
              "Migration maps": ["bird migration"], "Photo journal": [], "Field guide": ["bird identification"]}
    w = _plan_world(tmp_path, state, lambda job: topics.get(job, []))
    b = w.host.plan(BIRDS, [{"name": n, "does": d} for n, d in FIVE[BIRDS]])
    assert b["knowledge_plan"]["topics"] == ["birdwatching basics", "bird song", "bird vocalization",
                                             "bird migration", "bird identification"]
    assert NO_TOPICS_LINE not in b["card"]["lines"]
    assert b["knowledge_asked"]["gave"] == 5 and b["knowledge_asked"]["kept"] == 5


# -- F4: her answer on an app's add card is said ------------------------------------------------------------
class _Hooks:
    def __init__(self):
        self.said, self.states = [], []

    def status(self, state, line=""):
        self.states.append((state, line))

    def say(self, line):
        self.said.append(line)

    def wait_for(self, ready, limit_s):
        return ready()


class _AddLink:
    def __init__(self, answer):
        self.answer = answer

    def try_add(self, try_id, built_with, manifest):
        return {"ok": True, "waiting": True, "consent_id": "pm_1"}

    def try_add_state(self, try_id):
        if self.answer == "yes":
            return {"ok": True, "id": "plantlog", "said": "Installed Plant log."}
        return {"ok": False, "answer": "no", "said": "You said no to adding Plant log."}


@pytest.mark.parametrize("answer,line", [("yes", "You said Yes. Plant log is added."),
                                         ("no", "You said No. Plant log is not added.")])
def test_the_job_says_her_answer_after_the_add_card(monkeypatch, answer, line):
    from puffpai_core.packages import manifest as manifest_mod
    man = {"id": "plantlog", "name": "Plant log", "kind": "app"}
    monkeypatch.setattr(adopt, "manifest_for", lambda t: dict(man))
    monkeypatch.setattr(manifest_mod, "validate", lambda m: [])
    hooks = _Hooks()
    t = SimpleNamespace(hooks=hooks, try_id="t1", pkg_id="plantlog", cand={"name": "plantlog", "runtime": "python"},
                        notes=[], unsaved=[], built_with="local", clock=lambda: 0.0, paused_s=0.0, closed=False,
                        installed=False, compose_project="")
    got = adopt.install(t, _AddLink(answer))
    assert hooks.said[0] == "Plant log works. It waits for your Yes on its card before it is added."
    assert hooks.said[-1] == line and len(hooks.said) == 2
    assert got["ok"] is (answer == "yes")


def test_the_core_marks_a_no_on_the_add_card():
    from puffpai_core.zone3.toolsmith_host.trials import Trials
    rec = SimpleNamespace(pending="pm_1", covered=False, manifest={"name": "Plant log"}, pkg_id="plantlog")
    ended = []
    fake = SimpleNamespace(ports=SimpleNamespace(packages_wait=lambda cid, t: {"status": "declined"}),
                           end=lambda r: ended.append(r))
    got = Trials.add_state(fake, rec)
    assert got == {"ok": False, "answer": "no", "said": "You said no to adding Plant log."} and ended == [rec]


# -- F5: the persona is written from the jobs as kept ---------------------------------------------------
def test_the_persona_never_promises_a_part_that_is_not_a_job(state):
    seen = []
    answer = {"role": "a small shop helper",
              "can_help": ["draft an invoice for a customer", "sort receipts by month",
                           "keep your customer list tidy", "write tax notes for the year",
                           "send appointment reminders"],
              "talks": "It is calm and clear.", "first_questions": ["What do you sell?"]}

    def ask(system, user):
        seen.append((system, user))
        return answer, "local"
    jobs = ["Invoices: Makes and tracks invoices", "Receipts: Keeps receipts", "Customer list: Keeps customers"]
    left = ["appointment reminders", "tax notes for my small shop"]
    rec = persona.write_from_goal(state, SHOP, "b1", ask, jobs, left_out=left)
    helps = persona.knows_of(rec["text"])
    assert helps == ["draft an invoice for a customer", "sort receipts by month", "keep your customer list tidy"]
    system, user = seen[0]
    assert "Jobs (the only things this bot does): Invoices: Makes and tracks invoices" in user
    assert "Not a job yet (never list these): appointment reminders; tax notes for my small shop" in user
    assert "never one listed as not a job yet" in system


def test_the_wizard_writes_the_persona_from_the_kept_jobs(tmp_path, state):
    seen = []
    w = World(tmp_path, state)
    w.ports.brain = lambda s, u: seen.append(u) or {
        "role": "a kitchen helper", "can_help": ["find a recipe in your box", "suggest a wine for a dish",
                                                  "plan meals for the week"],
        "talks": "It is warm.", "first_questions": ["What do you like to cook?"]}
    ts = WizardToolsmith(w.app)
    ts.host = lambda: w.host
    b = w.host.draft(RECIPES, [{"name": n, "does": d} for n, d in FIVE[RECIPES][:3]],
                     left_out=["wine pairing", "a kitchen timer"])
    rec = ts.write_persona(b["id"])
    assert persona.knows_of(rec["text"]) == ["find a recipe in your box", "plan meals for the week"]
    assert "Recipe box: Keeps your recipes" in seen[0] and "Not a job yet" in seen[0]
