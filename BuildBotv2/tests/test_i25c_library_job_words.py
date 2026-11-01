"""Lane i25c (D123): a job with no app never reads "working". It says "no app yet, your library has some reading on
this", on each screen, and a build of only such jobs never reads as done. The match, the library proof and the state
(`working`) did not change.

Three unrelated goals: a home recipe bot, a small-business invoices bot, a birdwatching bot. The library proof and
the Core's views are the real ones (fake fetch, real index). The Desktop's half is tests/js/i25c.test.js, fed these
same views. No network, no real key, no bot port.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess

import pytest

from conftest import REPO
from puffpai_core import termchat, wiring
from puffpai_core.termchat import Look
from puffpai_core.wizard import engine as engine_mod
from puffpai_core.wizard import lines as L
from puffpai_core.zone3.toolsmith_host import store
from test_i19e_library_jobs import GOALS, _check, _ended, _fetch, _world

NODE = shutil.which("node")
WORDS = "no app yet, your library has some reading on this"
SITE_LINE = "A free site does this, so nothing was installed: Open Recipes, https://recipes.example. Open it to use it."
#: Enough for the real proof on each goal: its own words, in passages from two fetched items.
ITEMS = {"recipe": GOALS["recipe"]["items"],
         "invoices": [("wikipedia", "Invoice", "Send invoices to customers. Makes and sends each March invoice. ", 2),
                      ("gutenberg", "Bakery Accounts", "Send the March invoice to the bakery customers. ", 2)],
         "birds": GOALS["birds"]["items"]}


def _proved(w, key):
    """A build whose one job the library proved, the real way."""
    b = _ended(w, key)
    _fetch(w, b, ITEMS[key])
    assert _check(w, b)["working"] is True
    return b


def _jobs(w, states):
    """A build with one job per entry of *states*, each ``(state, line, result)``, as the Core keeps them."""
    st = w.host.settings()
    jobs = [{"name": f"Job {i}", "does": f"does thing {i}"} for i in range(len(states))]
    b = w.host.store.create("a garden club bot", jobs, st)
    w.host.store.set_plan(b["id"], {"jobs": [{**j, "plan": {"candidates": []}} for j in jobs]}, st, "")

    def fn(x):
        x["yes"] = {"given": True, "consent_id": "c_test", "sha": x["plan_sha"]}
        for j, (state, line, result) in zip(x["jobs"], states):
            j["run"].update(state=state, line=line, result=result)
    w.host.store.change(b["id"], fn)
    return b["id"]


LIB = ("working", "No app does this yet, but your library has some reading on this: 3 passages from 2 items.",
       {"by": "knowledge", "count": 3, "sources": {"wikipedia": 2}, "titles": ["A"], "proof": {"result": "working"}})
APP = ("working", "The brain read Mealie: it lists recipes.", {"package": "mealie", "proof": {"result": "working"}})
SITE = ("working", SITE_LINE, {"url": "https://recipes.example", "proof": {"result": "working"}})
NOT_YET = ("not_yet", "No app worked yet. x: it did not start.", None)


class Api:
    """The Core API for the terminal chat: one build, as the Core's own view gives it."""

    def __init__(self, view):
        self.view = json.loads(json.dumps(view))

    def call(self, method, path, body=None, **kw):
        if path.startswith("/v1/toolsmith/builds/"):
            return 200, self.view
        return 200, []


def _chat(view):
    out = io.StringIO()
    return termchat.Chat(Api(view), out=out, look=Look(out, {}), sleep=lambda s: None), out


def _watched(view):
    chat, out = _chat(view)
    assert chat.watch(view["id"]) == 0
    return out.getvalue()


def _done(results):
    step = engine_mod.DoneStep.__new__(engine_mod.DoneStep)

    class Ts:
        @staticmethod
        def results(build):
            return results

    class E:
        services = type("S", (), {"toolsmith": Ts})

        @staticmethod
        def build_id():
            return "b"
    step.e = E
    return step._said()


# -- how a job is told apart: one place, and `working` stays -----------------------------------------------------

@pytest.mark.parametrize("key", list(GOALS))
def test_the_state_stays_working_and_the_core_says_library(tmp_path, state, key):
    w = _world(tmp_path, state)
    b = _proved(w, key)
    raw = w.host.store.read(b["id"])["jobs"][0]
    assert raw["run"]["state"] == "working" and raw["run"]["result"]["by"] == "knowledge"   # unchanged
    assert store.library_job(raw) is True
    view = w.host.view(b["id"])["jobs"][0]
    assert view["status"] == "working" and view["library"] is True
    row = next(x for x in w.host.status()["builds"] if x["id"] == b["id"])["jobs"][0]
    assert row["status"] == "working" and row["library"] is True
    got = wiring.job_result(view)
    assert got["state"] == "working" and got["library"] is True


def test_a_real_app_a_free_site_and_a_job_not_ended_are_not_library_jobs(tmp_path, state):
    w = _world(tmp_path, state)
    bid = _jobs(w, [APP, SITE, NOT_YET])
    view = w.host.view(bid)
    assert [("library" in j) for j in view["jobs"]] == [False, False, False]
    row = next(x for x in w.host.status()["builds"] if x["id"] == bid)
    assert all("library" not in j for j in row["jobs"])
    assert all("library" not in wiring.job_result(j) for j in view["jobs"])


def test_a_result_by_knowledge_counts_only_while_the_job_is_working():
    assert store.library_job({"run": {"state": "building", "result": {"by": "knowledge"}}}) is False
    assert store.library_job({"run": {"state": "working", "result": None}}) is False
    assert store.library_job({}) is False


# -- the line ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", list(GOALS))
def test_the_line_says_some_reading_and_never_that_the_library_has_it(tmp_path, state, key):
    w = _world(tmp_path, state)
    b = _proved(w, key)
    line = w.host.store.read(b["id"])["jobs"][0]["run"]["line"]
    assert line.startswith("No app does this yet, but your library has some reading on this: ")
    assert "has it" not in line and "\u2014" not in line


# -- Prove, Done, the chat's result ------------------------------------------------------------------------------

@pytest.mark.parametrize("key", list(GOALS))
def test_prove_done_and_the_chat_never_say_works(tmp_path, state, key):
    w = _world(tmp_path, state)
    b = _proved(w, key)
    view = w.host.view(b["id"])
    results = [wiring.job_result(j) for j in view["jobs"]]
    name = GOALS[key]["job"]["name"]
    prove = L.prove_lines(results)
    assert prove[0].startswith(f"{name}: No app does this yet, but your library has some reading on this: ")
    assert L.job_result_lines(results) == [f"{name}: {WORDS}."]
    said = _done(results)
    assert said[0] == L.DONE_READING and said[0] not in (L.DONE_ALL, L.DONE_NONE)
    assert said[1] == f"{name}: {WORDS}." and said[-1] == L.DONE_NEXT
    chat = wiring.chat_result_lines(view["jobs"])
    assert chat[0].startswith(f"{name}: No app does this yet")
    for text in (prove, said, chat):
        joined = " ".join(text).lower()
        assert ": works" not in joined and "every job works" not in joined


def test_a_library_line_with_no_words_still_says_no_app():
    got = L.prove_lines([{"name": "Bird ID", "state": "working", "reason": "", "library": True}])
    assert got == [f"Bird ID: {WORDS}."]


def test_a_real_app_still_reads_works_and_every_job_works():
    apps = [{"name": "Pantry", "state": "working", "reason": "It read the pantry.", "tried": []}]
    assert L.prove_lines(apps) == ["Pantry: works. It read the pantry."]
    assert _done(apps) == [L.DONE_ALL, "Pantry: works."]


def test_a_mixed_build_says_what_is_true_on_done():
    mix = [{"name": "Pantry", "state": "working", "reason": "", "tried": []},
           {"name": "Bird ID", "state": "working", "reason": "x", "library": True, "tried": []}]
    assert _done(mix) == [L.DONE_SOME, "Pantry: works.", f"Bird ID: {WORDS}.", L.DONE_NEXT]
    none = [mix[1], {"name": "Invoices", "state": "not_yet", "reason": "No app worked yet.", "tried": []}]
    said = _done(none)
    assert said[0] == L.DONE_NONE and said[1] == f"Bird ID: {WORDS}." and said[-1] == L.DONE_NEXT


def test_a_free_site_stand_in_is_never_called_a_library_job():
    site = {"name": "Recipes", "state": "working", "reason": SITE_LINE, "tried": []}
    for text in (L.prove_lines([site]), _done([site])):
        assert "library" not in " ".join(text) and "reading" not in " ".join(text)


# -- the terminal chat -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", list(GOALS))
def test_the_terminal_says_no_app_yet_with_no_check_mark(tmp_path, state, key):
    w = _world(tmp_path, state)
    b = _proved(w, key)
    view = w.host.view(b["id"])
    name = GOALS[key]["job"]["name"]
    assert termchat._build_lines(view)[0] == f"{name}: {WORDS}"
    text = _watched(view)
    chat, _ = _chat(view)
    assert f"- {name}: {WORDS}" in text
    assert f"{chat.look.check} {name}" not in text and f"{name}: working" not in text


def test_the_terminal_keeps_working_for_a_real_app_and_a_site_and_tells_a_mixed_build_apart(tmp_path, state):
    w = _world(tmp_path, state)
    view = w.host.view(_jobs(w, [APP, SITE, LIB]))
    text = _watched(view)
    check = _chat(view)[0].look.check
    assert f"{check} Job 0: working" in text and f"{check} Job 1: working" in text
    assert f"- Job 2: {WORDS}" in text and "Job 2: working" not in text


def test_a_live_terminal_step_for_a_library_job_gets_no_check_mark():
    chat, out = _chat({"id": "b"})
    for line in (f"Job: {WORDS}", f"Job: {LIB[1]}"):
        chat.doing = line
        chat._step_end(True)
    assert chat.look.check not in out.getvalue()


# -- a refusal that would say a working app ----------------------------------------------------------------------

def test_the_two_refusals_do_not_claim_an_answer_or_an_app():
    from puffpai_core.api.groups import github
    from puffpai_core.zone3.toolsmith_host import service
    assert "has its answer" not in service.AGAIN_NOTHING_LINE
    assert "no app yet" in github.JOB_LIBRARY_LINE and "working app" not in github.JOB_LIBRARY_LINE


# -- the Desktop: logic.js fed these same views ------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_desktop_reads_the_core_s_own_views(tmp_path, state):
    w = _world(tmp_path, state)
    goals = {key: _proved(w, key)["id"] for key in GOALS}
    mixed, site, app = _jobs(w, [APP, LIB]), _jobs(w, [SITE]), _jobs(w, [APP])
    views = {bid: w.host.view(bid) for bid in [*goals.values(), mixed, site, app]}
    fixture = tmp_path / "views.json"
    fixture.write_text(json.dumps({"goals": goals, "mixed": mixed, "site": site, "app": app, "status": w.host.status(),
                                   "views": views}), encoding="utf-8")
    proc = subprocess.run([NODE, str(REPO / "tests" / "js" / "i25c.test.js"), str(fixture)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_each_page_reads_the_one_rule_never_the_raw_state():
    web = REPO / "packages" / "desktop" / "web"
    for name in ("window.js", "pages/building.js"):
        src = (web / name).read_text(encoding="utf-8")
        assert 'j.status === "working"' not in src, name
        assert "L.jobGood(j)" in src, name
    assert "L.buildReading(" in (web / "pages" / "home.js").read_text(encoding="utf-8")
    assert "L.buildReading(" in (web / "pages" / "building.js").read_text(encoding="utf-8")
