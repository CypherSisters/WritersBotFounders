"""D122 (lane i25b): Stop BuildBot stops BuildBot during setup too, and a question answered elsewhere does not hold setup.

Her words: "the stop buildbot command is not working."

The real-console tests run the real ``puffpai setup`` and the real ``puffpai shutdown`` in a hidden console of their
own, in the test's own Job Object, on free ports (never 8420-8422). Toolsmith's process is a script
(``console_setup_d122.py``); nothing needs a brain, a key or a paid call. Three unrelated goals run through the words.
"""

from __future__ import annotations

import io
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from puffpai_core.api.app import STOPPED_LINE
from puffpai_core.wizard import Terminal
from puffpai_core.wizard import lines as L
from puffpai_core.wizard import terminal as term
from wizard_kit import Kit

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "tests" / "console_setup_d122.py"
JOBS = ["Home recipe box", "Small-business invoices", "Birdwatching log"]    # any goal, the same behaviour
windows_only = pytest.mark.skipif(os.name != "nt", reason="a real Windows console")
PIN = "correct-horse-917"
BEFORE_YES = ("scan", "brain", "vault", "bridge", "toolsmith", "goal", "name", "look", "plan")


# -- Answers: a read that blocks never holds up a stop ------------------------------------------------------------
def _blocked_read(_prompt):
    threading.Event().wait()                           # a read of a console nobody types in


def test_a_stop_ends_a_read_that_blocks():
    stop = threading.Event()
    a = term.Answers(stop.is_set, read_line=_blocked_read, read_hidden=_blocked_read)
    for read in (a.line, a.hidden):
        stop.clear()
        threading.Timer(0.2, stop.set).start()
        t0 = time.monotonic()
        with pytest.raises(term.SetupStopped):
            read("  > ")
        assert time.monotonic() - t0 < 2.0
        a._box = None                                  # noqa: SLF001 - the next read in this test is a new one
    assert not issubclass(term.SetupStopped, Exception)        # no step's except Exception takes it for a failure
    assert not issubclass(term.SetupStopped, KeyboardInterrupt)


def test_a_stop_ends_the_wait_between_two_polls():
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    t0 = time.monotonic()
    with pytest.raises(term.SetupStopped):
        term.Answers(stop.is_set).sleep(30)
    assert time.monotonic() - t0 < 2.0


def test_answers_pass_through_and_eof_and_ctrl_c_stay_what_they_were():
    a = term.Answers(lambda: False, read_line=lambda p: f"said to {p}", read_hidden=lambda p: "hidden")
    assert a.line("q") == "said to q" and a.hidden("q") == "hidden" and not a.read_is_out

    def raises(exc):
        def read(_p):
            raise exc
        return read
    for exc in (EOFError, KeyboardInterrupt):
        with pytest.raises(exc):
            term.Answers(lambda: False, read_line=raises(exc)).line("q")


def test_a_read_left_by_an_overtaken_question_takes_the_next_answer_and_says_the_new_prompt():
    typed = threading.Event()
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        typed.wait()
        return "1"

    out = io.StringIO()
    a = term.Answers(lambda: False, read_line=read, out=out)

    def gone():
        raise term.Overtaken({"status": "working"})
    with pytest.raises(term.Overtaken):
        a.line("first > ", gone, 0.0)
    assert a.read_is_out
    threading.Timer(0.3, typed.set).start()
    assert a.line("second > ") == "1"
    assert prompts == ["first > "] and out.getvalue() == "second > " and not a.read_is_out


def test_a_line_typed_for_a_question_that_went_is_not_used_for_the_next_one():
    a = term.Answers(lambda: False, read_line=lambda p: "new answer")
    a._box = queue.SimpleQueue()                       # noqa: SLF001 - the old read has its line by now
    a._box.put((True, "old answer"))                   # noqa: SLF001
    assert a.line("q") == "new answer"


# -- the build: a question answered on the Desktop, and setup ending by itself ------------------------------------
class Build:
    """The real Engine and BuildStep on a fake Toolsmith; a test moves the build along."""

    def __init__(self, state, job):
        self.kit = Kit(state)
        self.eng = self.kit.engine()
        self.eng.set_build("b1")
        prog = self.eng._progress()                    # noqa: SLF001
        for sid in BEFORE_YES + ("yes",):
            prog["steps"][sid] = {"state": "done", "at": "t", "by": "terminal"}
        self.eng._save(prog)                           # noqa: SLF001
        self.job = job
        self.status, self.cards, self.turns, self.finished = f"building: {job}", [], [], False
        self.answered = []
        ts = self.kit.toolsmith
        ts.progress = lambda build: {"jobs": [{"id": job, "name": job, "status": self.status, "line": "", "now": "",
                                               "lines": list(self.lines), "since": ""}],
                                     "steps": list(self.turns), "cards": list(self.cards), "finished": self.finished}
        def answer_card(build, cid, yes, picks=None):
            self.answered.append(cid)
            self.cards = []
            return ""
        ts.answer_card = answer_card
        ts.answer_step = lambda build, sid, answer: self.answered.append(sid) or ""
        ts.results = lambda build: [{"name": job, "state": "held", "reason": "Check again to retry.", "tried": []}]
        self.lines = []

    def card(self, cid="c1", title="Let Toolsmith try an app?"):
        self.cards = [{"consent_id": cid, "title": title, "lines": ["It needs your Yes."], "kind": "package"}]

    def terminal(self, stopped=lambda: False, read=None):
        self.out = io.StringIO()
        self.read = read or _blocked_read
        answers = term.Answers(stopped, read_line=self.read, out=self.out)
        return Terminal(self.eng, out=self.out, answers=answers)

    def in_thread(self, t):
        got: dict = {}

        def run():
            try:
                got["code"] = t.run()
            except BaseException as exc:  # noqa: BLE001 - the test reads it
                got["raised"] = exc
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th, got

    def wait_for(self, text, timeout_s=15.0):
        end = time.monotonic() + timeout_s
        while time.monotonic() < end and text not in self.out.getvalue():
            time.sleep(0.05)
        assert text in self.out.getvalue(), self.out.getvalue()


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch):
    from puffpai_core.wizard.engine import BuildStep
    monkeypatch.setattr(BuildStep, "POLL_S", 0.1)


@pytest.mark.parametrize("job", JOBS)
def test_a_card_answered_on_the_desktop_lets_the_window_go_on_and_setup_finish(state, job):
    """Her test8: the window sat at a hold card she had answered in the browser, so every later step waited."""
    b = Build(state, job)
    b.card()
    t = b.terminal()
    th, got = b.in_thread(t)
    b.wait_for("Say Yes to this?")
    b.cards, b.status, b.finished = [], "held", True   # answered in the browser, and every job has ended
    th.join(20)
    assert got.get("code") == 0, (got, b.out.getvalue())
    screen = b.out.getvalue()
    assert L.ANSWERED_ELSEWHERE in screen and L.BUILD_ENDED in screen
    over = b.eng.overview()
    assert over["complete"] is True and {r["state"] for r in over["steps"]} == {"done"}
    assert b.answered == []                            # the window answered nothing for her


@pytest.mark.parametrize("job", JOBS)
def test_a_new_card_after_the_old_one_is_shown_and_only_its_own_answer_counts(state, job):
    b = Build(state, job)
    b.card("c1", "First card")
    lines = []
    release = threading.Event()

    def read(prompt):
        lines.append(prompt)
        release.wait()
        return "yes"
    t = b.terminal(read=read)
    th, got = b.in_thread(t)
    b.wait_for("First card")
    b.card("c2", "Second card")
    b.wait_for("Second card")
    b.wait_for(L.ANSWERED_ELSEWHERE)
    b.status, b.finished = "held", True
    release.set()
    th.join(20)
    assert b.answered == ["c2"], (b.answered, b.out.getvalue())
    assert got.get("code") == 0


def test_a_turn_question_answered_elsewhere_is_gone_too(state):
    b = Build(state, "Birdwatching log")
    b.turns = [{"id": "s1", "title": "Plug in the camera", "kind": "do", "text": "Plug it in."}]
    t = b.terminal()
    th, got = b.in_thread(t)
    b.wait_for("Did you do it?")
    b.turns, b.status, b.finished = [], "held", True
    th.join(20)
    assert got.get("code") == 0 and L.ANSWERED_ELSEWHERE in b.out.getvalue()


def test_lines_that_come_while_she_is_asked_wait_for_the_next_screen(state):
    b = Build(state, "Home recipe box")
    b.card()
    answer = threading.Event()

    def read(_p):
        answer.wait()
        return "yes"
    t = b.terminal(read=read)
    th, got = b.in_thread(t)
    b.wait_for("Say Yes to this?")
    b.lines = ["Found an app."]
    time.sleep(0.6)
    assert "Found an app." not in b.out.getvalue()     # nothing is written over what she types
    b.finished, b.status = True, "held"
    answer.set()
    th.join(20)
    assert "Found an app." in b.out.getvalue() and got.get("code") == 0


@pytest.mark.parametrize("job", JOBS)
def test_a_stop_ends_the_window_at_a_card_and_during_a_build(state, job):
    for card in (True, False):
        stop = threading.Event()
        b = Build(state, job)
        b.lines = ["Looking for an app."]
        if card:
            b.card()
        t = b.terminal(stopped=stop.is_set)
        th, got = b.in_thread(t)
        b.wait_for("Say Yes to this?" if card else "Looking for an app.")
        stop.set()
        th.join(5)
        assert isinstance(got.get("raised"), term.SetupStopped), got


def test_a_screen_with_no_asked_is_never_polled(state):
    """Every other step's question stands until she answers it: no extra call to the engine."""
    kit = Kit(state)
    eng = kit.engine()
    t = Terminal(eng, out=io.StringIO(), answers=term.Answers(lambda: False, read_line=lambda p: ""))
    assert t._watch("goal", {"status": "ask", "field": {"id": "goal"}}) is None    # noqa: SLF001


def test_after_a_stop_the_setup_page_walks_the_same_steps_to_the_end(state):
    """Start BuildBot.cmd, then Setup: no terminal, the same engine. Toolsmith's stop held the job, so the build ended."""
    b = Build(state, "Small-business invoices")
    b.status, b.finished = "held", True
    assert b.eng.overview()["current"] == "build" and b.eng.overview()["complete"] is False
    assert b.eng.submit("build", {}, renderer="browser")["next"] == "prove"
    assert "Check again to retry." in " ".join(b.eng.submit("prove", {}, renderer="browser")["lines"])
    assert b.eng.submit("done", {}, renderer="browser")["status"] == "done"
    assert b.eng.overview()["complete"] is True


def test_an_install_a_stop_cut_short_says_so_and_never_waits_again(state, monkeypatch, drawbridge):
    """Its row stayed "installing" for good, so a build waited 30 minutes on it in every run after the stop."""
    from puffpai_core.api import create_app
    from puffpai_core.cli import main
    from puffpai_core.packages import manager
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["add", str(REPO / "tests" / "fixtures" / "hello-tool")]) == 0
    core = create_app(state).extensions["puffpai"]
    core.registry.update("hello-tool", status="installing")
    core.packages.recover_after_restart()
    row = core.registry.get("hello-tool")
    assert row["status"] == "broken" and row["health"]["reason"] == manager.LEFT_INSTALLING


# -- the route: the same auth, no more 409 ------------------------------------------------------------------------
def test_the_route_stops_and_keeps_who_may_call_it(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.groups.shutdown import STOP
    app = create_app(state)
    core = app.extensions["puffpai"]
    for token, code in ((None, 401), (core.tokens.issue_owner_key(), 403),
                        (core.tokens.issue_package_token("hello-tool"), 403), (core.tokens.issue_toolsmith("b-1"), 403)):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        assert app.test_client().post("/v1/shutdown", json={}, headers=headers).status_code == code
        assert STOP not in app.extensions or not app.extensions[STOP].is_set()
    r = app.test_client().post("/v1/shutdown", json={},
                               headers={"Authorization": f"Bearer {core.tokens.issue_device('this browser')}"})
    assert r.status_code == 202 and r.json["stopping"] is True and app.extensions[STOP].is_set()


def test_the_stopped_lines_name_a_way_that_works_here(tmp_path, monkeypatch):
    monkeypatch.setattr(term, "launcher", lambda: tmp_path / "Start BuildBot.cmd")
    said = term.setup_stopped_lines()
    assert said[0] == L.SETUP_STOPPED and said[1].startswith("To go on, run this in a terminal: ") and said[1].endswith("setup")
    (tmp_path / "Start BuildBot.cmd").write_text("@echo off\n", encoding="ascii")
    said = term.setup_stopped_lines()
    assert said[1] == f"To go on, double-click Start BuildBot.cmd in {tmp_path}, then open Setup."
    assert "\u2014" not in " ".join(said) + L.ANSWERED_ELSEWHERE


# -- the real setup window --------------------------------------------------------------------------------------
def _ports():
    socks = [socket.socket() for _ in range(3)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    assert not set(ports) & {8420, 8421, 8422, 3000}
    return ports


def _env(state, script=None, parts=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "PUFFPAI_", "D122_"))}
    env.update(PYTHONUNBUFFERED="1", PIP_REQUIRE_VIRTUALENV="1", PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
               PYTHONPATH=os.pathsep.join([str(REPO / "core"), str(REPO / "tests")]), PUFFPAI_STATE=str(state),
               OLLAMA_HOST="127.0.0.1:11435")
    if script:
        env["D122_SCRIPT"] = str(script)
    if not parts:
        env["D122_NO_PARTS"] = "1"
    return env


def _seed(state, done, build="b1"):
    settings = state / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    steps = {s: {"state": "done", "at": "2026-10-31T00:00:00Z", "by": "terminal"} for s in done}
    prog = {"steps": steps, "partial": {}, "answers": {s: {} for s in done}, "complete": False, "build": build}
    (settings / "wizard.json").write_text(json.dumps(prog), encoding="utf-8")


def _script_file(tmp_path, **data):
    path = tmp_path / "script.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _window(env, tmp_path, name, ready, *, script=(), stop=True, wait_s=30.0, settle_s=1.0, mode="none", stop_argv=None):
    """One real ``puffpai setup`` window. The owner types *script*, then Stop BuildBot runs. Returns (report, what the
    stop said). A read of the console stays blocked unless the setup window really ended it."""
    import console_kit
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    spec, said = folder / "owner.json", folder / "said.json"
    spec.write_text(json.dumps({"script": [*script, [ready, None]], "stop": stop, "settle_s": settle_s, "stop_argv": stop_argv}),
                    encoding="utf-8")
    p = _ports()
    argv = [sys.executable, str(DRIVER), "setup", "--port", str(p[0]), "--pages-port", str(p[1]), "--proxy-port", str(p[2])]
    rep = console_kit.run(argv, mode, env, ready if not script else script[0][0], folder, screen=True, settle_s=0.2,
                          during=[sys.executable, str(DRIVER), "--owner", str(spec), str(said)], wait_s=wait_s)
    time.sleep(0.2)
    return rep, json.loads(said.read_text(encoding="utf-8")) if said.is_file() else {}


def _stopped_clean(rep, said, state, *, code=1):
    screen = rep["output"]
    assert said.get("code") == 0 and said["out"].splitlines()[-1] == STOPPED_LINE, (said, screen)
    assert rep["exit"] == code, (rep["exit"], screen)
    assert rep["left"] == [], "something setup started is still running"
    assert STOPPED_LINE in screen and "Traceback" not in screen and "Fatal" not in screen, screen
    assert not (state / "settings" / "terminal.json").exists()
    return screen


def _unfinished(screen):
    flat = screen.replace("\n", "")
    assert L.SETUP_STOPPED in flat and "To go on, run this in a terminal" in flat, screen


@windows_only
def test_stop_at_a_system_part_card_then_coming_back_asks_it_again(state, tmp_path):
    env = _env(state, parts=True)
    rep, said = _window(env, tmp_path, "first", "Say Yes to this?")
    assert said.get("seen") == [["Say Yes to this?", True]], said
    _unfinished(_stopped_clean(rep, said, state))
    rep, said = _window(env, tmp_path, "again", "Say Yes to this?")
    screen = _stopped_clean(rep, said, state)
    assert "Add PuffPAI Desktop?" in screen and "Traceback" not in screen    # the same card: no dead end


@windows_only
def test_stop_at_each_vault_prompt_never_leaves_a_half_made_vault(state, tmp_path):
    """The read that never echoes (getpass) blocks in another way than input(): the stop ends it too. Stop before the
    vault is made, then after it is made and before its code is typed back: coming back goes on, with no dead end."""
    _seed(state, ("scan", "brain"), build=None)
    env = _env(state)
    hidden = "What you type is hidden."
    rep, said = _window(env, tmp_path, "pin", hidden, script=[["Type a number", ""]])
    _unfinished(_stopped_clean(rep, said, state))
    prog = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))
    assert prog["steps"].get("vault", {}).get("state") != "done"
    assert PIN not in json.dumps(prog) and not (state / "vault" / "vault.db").exists()

    # Back: her first answer stands. The PIN makes the vault, the code shows once; she stops before typing it back.
    rep, said = _window(env, tmp_path, "code", "Type the recovery code back", script=[[hidden, PIN], ["Type your PIN again", PIN]])
    screen = _stopped_clean(rep, said, state)
    assert "Where should the vault live?" not in screen, screen
    _unfinished(screen)
    prog = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))
    assert prog["steps"].get("vault", {}).get("state") != "done" and PIN not in json.dumps(prog)

    # Back again: the vault is whole. Her PIN opens it, and the code she was shown can be replaced.
    rep, said = _window(env, tmp_path, "back", "Write it down now", script=[["type new", "new"], ["PIN", PIN]])
    screen = _stopped_clean(rep, said, state)
    assert L.CODE_LOST in screen.replace(chr(10), "") and PIN not in screen, screen


@windows_only
@pytest.mark.parametrize("job", JOBS)
def test_stop_during_a_build_then_coming_back_finishes_setup_with_what_happened(state, tmp_path, job):
    """A real app runs while the build does: Stop ends it, the window ends, and the next start goes on."""
    _seed(state, BEFORE_YES + ("yes",))
    working = _script_file(tmp_path, jobs=[{"id": job, "name": job, "status": "building: an app", "line": "", "now": "",
                                            "lines": [f"looking for {job}"], "since": ""}], finished=False)
    env = _env(state, working)
    rep, said = _window(env, tmp_path, "build", f"looking for {job}")
    _unfinished(_stopped_clean(rep, said, state))
    prog = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))
    assert prog["build"] == "b1" and prog["steps"]["yes"]["state"] == "done"       # the build is not lost
    assert prog["complete"] is False

    # Back again: Toolsmith's real stop marked the job held, so the build has ended. No question is asked again.
    working.write_text(json.dumps({"jobs": [{"id": job, "name": job, "status": "held", "line": "", "now": "", "lines": []}],
                                   "results": [{"name": job, "state": "held", "reason": "Check again to retry.", "tried": []}],
                                   "finished": True}), encoding="utf-8")
    rep, said = _window(env, tmp_path, "back", "Setup is done.")
    screen = _stopped_clean(rep, said, state, code=0)       # setup is finished: code 0, no "go on" line
    assert L.SETUP_STOPPED not in screen.replace("\n", "") and L.BUILD_ENDED in screen.replace("\n", "")
    assert "Say Yes" not in screen and "Pick the brain" not in screen
    assert json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))["complete"] is True


@windows_only
def test_stop_while_the_plan_card_waits_then_coming_back_shows_it_and_her_yes_counts(state, tmp_path):
    _seed(state, BEFORE_YES)
    plan = _script_file(tmp_path, plan={"summary": "A small-business invoices bot: one job.", "items": ["Invoices: a try"]},
                        jobs=[], finished=False)
    env = _env(state, plan)
    rep, said = _window(env, tmp_path, "card", "Invoices: a try", settle_s=1.5)
    assert "Invoices: a try" in rep["output"]
    _unfinished(_stopped_clean(rep, said, state))
    assert not json.loads(plan.read_text(encoding="utf-8")).get("yes")              # no Yes without her

    rep, said = _window(env, tmp_path, "yes", "Toolsmith starts the build", script=[["Invoices: a try", "y"]])
    screen = _stopped_clean(rep, said, state)
    assert json.loads(plan.read_text(encoding="utf-8")).get("yes") is True
    assert "Pick the brain" not in screen


@windows_only
def test_stop_at_the_setup_done_prompt_and_in_the_chat_after_it(state, tmp_path):
    _seed(state, BEFORE_YES + ("yes",))
    ended = _script_file(tmp_path, jobs=[], results=[], finished=True)
    env = _env(state, ended)
    rep, said = _window(env, tmp_path, "done", "[Enter] open the window")
    screen = _stopped_clean(rep, said, state, code=0)
    assert L.SETUP_STOPPED not in screen.replace("\n", "")
    assert "Setup is done." in screen

    _seed(state, BEFORE_YES + ("yes",))
    rep, said = _window(env, tmp_path, "chat", "BuildBot", script=[["[Enter] open the window", "t"]], settle_s=4.0)
    _stopped_clean(rep, said, state, code=0)


@windows_only
def test_ctrl_c_at_a_prompt_still_stops_it(state, tmp_path):
    """Ctrl+C in the window now reaches a main thread that waits on another thread's read."""
    _seed(state, BEFORE_YES)
    plan = _script_file(tmp_path, plan={"summary": "A birdwatching bot: one job.", "items": ["Birds: a try"]}, jobs=[])
    env = _env(state, plan)
    rep, _said = _window(env, tmp_path, "ctrlc", "Birds: a try", stop=False, mode="c", settle_s=0.2)
    assert rep["sent"] and rep["exit"] == 1, rep
    assert rep["left"] == [] and STOPPED_LINE in rep["output"] and "Traceback" not in rep["output"], rep["output"]
    assert not (state / "settings" / "terminal.json").exists()


@windows_only
def test_stop_with_no_setup_and_with_setup_on_a_core_another_window_runs(state, tmp_path, monkeypatch, drawbridge):
    """``puffpai serve`` with the wizard open in a second window: both end, and a second Stop says nothing runs."""
    import console_kit
    _seed(state, BEFORE_YES)
    plan = _script_file(tmp_path, plan={"summary": "A home recipe bot: one job.", "items": ["Recipes: a try"]}, jobs=[])
    env = _env(state, plan)
    p = _ports()
    log_path = tmp_path / "serve.txt"
    with open(log_path, "w", encoding="utf-8") as log:
        core = console_kit._held([sys.executable, str(DRIVER), "serve", "--port", str(p[0]), "--pages-port", str(p[1]),   # noqa: SLF001
                                  "--proxy-port", str(p[2])], subprocess.CREATE_NEW_PROCESS_GROUP, env=env, stdout=log,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        end = time.monotonic() + 90
        key = state / "settings" / "terminal.json"
        while time.monotonic() < end and not key.is_file():
            time.sleep(0.25)
        assert key.is_file(), log_path.read_text(encoding="utf-8", errors="replace")
        rep, said = _window(env, tmp_path, "remote", "Recipes: a try", settle_s=1.5)
        screen = _stopped_clean(rep, said, state)
        assert "setup uses it" in screen
        _unfinished(screen)
        assert core.proc.wait(20) == 0
        out = log_path.read_text(encoding="utf-8", errors="replace")
        assert out.rstrip().endswith(STOPPED_LINE) and "Traceback" not in out, out[-2000:]
        again = subprocess.run([sys.executable, str(REPO / "tests" / "console_core.py"), "shutdown"], env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        assert again.returncode == 1 and "not running" in again.stdout
    finally:
        console_kit._end(core)                                                                                          # noqa: SLF001


@windows_only
def test_coming_back_with_start_shows_setup_not_finished_and_a_stop_with_no_setup_window_works(state, tmp_path):
    """Start BuildBot.cmd runs ``serve``: what the window's "Setup is not finished" row reads, and the plain Stop."""
    import console_kit
    from puffpai_core.cli import _call
    _seed(state, BEFORE_YES)
    plan = _script_file(tmp_path, plan={"summary": "A birdwatching bot: one job.", "items": ["Birds: a try"]}, jobs=[])
    env = _env(state, plan)
    rep, said = _window(env, tmp_path, "first", "Birds: a try", settle_s=1.5)
    _unfinished(_stopped_clean(rep, said, state))
    p = _ports()
    log_path = tmp_path / "serve.txt"
    with open(log_path, "w", encoding="utf-8") as log:
        core = console_kit._held([sys.executable, str(DRIVER), "serve", "--port", str(p[0]), "--pages-port", str(p[1]),   # noqa: SLF001
                                  "--proxy-port", str(p[2])], subprocess.CREATE_NEW_PROCESS_GROUP, env=env, stdout=log,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        key = state / "settings" / "terminal.json"
        end = time.monotonic() + 90
        while time.monotonic() < end and not key.is_file():
            time.sleep(0.25)
        info = json.loads(key.read_text(encoding="utf-8"))
        assert _call(info, "GET", "/v1/settings")[1]["wizard_complete"] is False     # the row shows, and is true
        assert _call(info, "GET", "/v1/wizard")[1]["current"] == "yes"               # the row's page goes on at the card
        status, screen = _call(info, "POST", "/v1/wizard/yes", {"answers": {}, "renderer": "browser"})
        assert status == 200 and "Birds: a try" in screen["lines"] and screen["status"] == "ask"
        got = subprocess.run([sys.executable, str(REPO / "tests" / "console_core.py"), "shutdown"], env=env,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        assert got.returncode == 0 and got.stdout.splitlines()[-1] == STOPPED_LINE, got.stdout
        assert core.proc.wait(20) == 0
    finally:
        console_kit._end(core)                                                                                          # noqa: SLF001


@windows_only
def test_the_real_stop_buildbot_cmd_at_a_prompt(tmp_path):
    """The .cmd install.ps1 writes, run in the setup window's console. Not a double-click, the same file and program."""
    from test_i7_shutdown import _stop_launcher_folder
    repo, stop_cmd = _stop_launcher_folder(tmp_path)
    lad = tmp_path / "lad"
    state = lad / "PuffPAI" / "testbot"
    _seed(state, BEFORE_YES)
    plan = _script_file(tmp_path, plan={"summary": "A home recipe bot: one job.", "items": ["Recipes: a try"]}, jobs=[])
    env = _env(state, plan)
    del env["PUFFPAI_STATE"]
    env.update(LOCALAPPDATA=str(lad), PUFFPAI_BOT="testbot")
    cmd = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
    rep, said = _window(env, tmp_path, "cmd", "Recipes: a try", settle_s=1.5, stop_argv=[cmd, "/c", str(stop_cmd)])
    screen = _stopped_clean(rep, said, state)
    assert "stopping its apps" in said["out"] and L.SETUP_STOPPED in screen.replace(chr(10), "")


@windows_only
def test_a_stop_with_answers_from_a_pipe_ends_without_a_crash(state, tmp_path):
    """A read blocked on a pipe, not a console: Python could crash on its way out with one still running."""
    _seed(state, BEFORE_YES)
    plan = _script_file(tmp_path, plan={"summary": "An invoices bot: one job.", "items": ["Invoices: a try"]}, jobs=[])
    env = _env(state, plan)
    p = _ports()
    proc = subprocess.Popen([sys.executable, str(DRIVER), "setup", "--port", str(p[0]), "--pages-port", str(p[1]),
                             "--proxy-port", str(p[2])], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    try:
        key = state / "settings" / "terminal.json"
        end = time.monotonic() + 90
        while time.monotonic() < end and not key.is_file():
            time.sleep(0.25)
        time.sleep(3.0)
        got = subprocess.run([sys.executable, str(REPO / "tests" / "console_core.py"), "shutdown"], env=env,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        assert got.returncode == 0, got.stdout + got.stderr
        out, _ = proc.communicate(timeout=30)
        assert proc.returncode == 1, out
        assert STOPPED_LINE in out and L.SETUP_STOPPED in out and "Fatal" not in out and "Traceback" not in out, out
    finally:
        if proc.poll() is None:
            proc.kill()                                # this process only, the one this test started


def test_the_stopped_lines_name_the_installer_around_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(term, "launcher", lambda: tmp_path / "start-buildbot.sh")
    (tmp_path / "start-buildbot.sh").write_text("x", encoding="ascii")
    monkeypatch.delenv("PUFFPAI_SETUP_AGAIN", raising=False)
    assert "setup --from" in term.setup_stopped_lines("/data/bot/from/writersbot")[1], "never the plain questions"
    monkeypatch.setenv("PUFFPAI_SETUP_AGAIN", "./install-writersbot.sh setup")
    assert term.setup_stopped_lines("/data/bot/from/writersbot")[1] == "To go on, run this in a terminal: ./install-writersbot.sh setup"


@pytest.mark.parametrize("when, says", [("in the steps", True), ("after it finished", False)])
def test_ctrl_c_in_setup_says_how_to_go_on_only_if_setup_is_not_finished(state, monkeypatch, capsys, when, says):
    import argparse
    monkeypatch.setenv("PUFFPAI_SETUP_AGAIN", "./install-writersbot.sh setup")
    monkeypatch.setattr(term, "builtins", lambda *_a: [])

    class Steps:
        def __init__(self, *_a, **_k):
            pass

        def run(self):
            if when == "in the steps":
                raise KeyboardInterrupt
            return 0

    def ctrl_c(*_a, **_k):
        raise KeyboardInterrupt
    monkeypatch.setattr(term, "Terminal", Steps)
    monkeypatch.setattr(term, "setup_done", lambda *_a, **_k: None)
    monkeypatch.setattr(term, "running_lines", ctrl_c)
    p = _ports()
    args = argparse.Namespace(port=p[0], pages_port=p[1], proxy_port=p[2], exit=False, from_folder=None)
    term._setup_core(args, term.Answers(lambda: False))
    out = capsys.readouterr().out
    assert STOPPED_LINE in out
    assert (L.SETUP_INTERRUPTED in out) is says, out
    assert ("./install-writersbot.sh setup" in out) is says, out
