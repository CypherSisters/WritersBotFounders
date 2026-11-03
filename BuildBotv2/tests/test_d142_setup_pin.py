"""D142: when setup is the Core and has a real terminal, it asks for the PIN right there at the build's wait for a locked vault.

Her words: she could not work out how to reach a chat in a second terminal, or a browser, from a setup over SSH.

The real Engine and its Plan and Build steps on a fake Toolsmith; a real Core on free ports (never 8420-8422) and a real
vault for the route. The PIN is made up. A real terminal's hidden read can't run under pytest: the reads are stand-ins,
and getpass itself is not exercised here."""

from __future__ import annotations

import getpass
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core.wizard import Terminal
from puffpai_core.wizard import lines as L
from puffpai_core.wizard import terminal as term
from wizard_kit import Kit

REAL_READ = REPO / "tests" / "real_read_d142.py"
CORE = str(Path(term.__file__).resolve().parents[2])      # the core under test
windows_only = pytest.mark.skipif(os.name != "nt", reason="a real Windows console")
posix_only = pytest.mark.skipif(os.name == "nt", reason="a real pty, with real termios: there is none on Windows")

PIN = "made-up-pin-917"
WRONG = "not-the-pin-204"
REFUSED = "Your vault did not open: That didn't match."
BEFORE_BUILD = ("scan", "brain", "vault", "bridge", "toolsmith", "goal", "name", "look", "plan", "yes")
JOBS = ["Home recipe box", "Small-business invoices", "Birdwatching log"]    # any goal, the same behaviour


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch):
    from puffpai_core.wizard.engine import BuildStep
    monkeypatch.setattr(BuildStep, "POLL_S", 0.05)


def _until(test, timeout_s=15.0, why=""):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if test():
            return
        time.sleep(0.02)
    raise AssertionError(why() if callable(why) else why or "it never came")


class Wait:
    """The real Engine on a fake Toolsmith that waits while ``locked``, and a stand-in for the vault route."""

    def __init__(self, state, job="Home recipe box"):
        self.state = state
        self.kit = Kit(state)
        self.eng = self.kit.engine()
        self.eng.set_build("b1")
        self.job = job
        self.locked = True
        self.stale_polls = 0                  # a job still says it waits for this many polls after the vault opened
        self.finish_when_open = True
        self.finished = False
        self.lines: list[str] = []
        self.cards: list[dict] = []
        self.answered: list[tuple] = []
        self.sent: list[str] = []
        self.polls = 0
        self.out = io.StringIO()
        ts = self.kit.toolsmith
        ts.start = lambda build: {"ok": True, "already": True}
        ts.progress = self._progress
        ts.results = lambda build: []

        def answer_card(build, cid, yes, picks=None):
            self.answered.append((cid, yes))
            self.cards = []
            return ""
        ts.answer_card = answer_card
        real = self.eng.submit

        def counted(*a, **kw):
            self.polls += 1
            return real(*a, **kw)
        self.eng.submit = counted

    def _progress(self, build):
        waiting = self.locked or self.stale_polls > 0
        if not self.locked and self.stale_polls > 0:
            self.stale_polls -= 1
        ended = self.finished or (self.finish_when_open and not waiting)
        return {"jobs": [{"id": self.job, "name": self.job, "status": "waiting_vault" if waiting else "building: an app",
                          "line": "", "now": "", "lines": list(self.lines), "since": ""}],
                "steps": [], "cards": list(self.cards), "finished": ended}

    def card(self, cid="c1", title="Let Toolsmith try an app?"):
        self.cards = [{"consent_id": cid, "title": title, "lines": ["It needs your Yes."], "kind": "package"}]

    def unlock(self, pin):
        self.sent.append(pin)
        if pin != PIN:
            return False, REFUSED
        self.locked = False
        return True, L.PIN_OPENED

    def terminal(self, hidden, line=None, stopped=lambda: False, unlock="the route"):
        self.reads = term.Answers(stopped, read_line=line or _no_line, read_hidden=hidden, out=self.out)
        return Terminal(self.eng, out=self.out, answers=self.reads, unlock=self.unlock if unlock == "the route" else unlock)

    def in_thread(self, t, step):
        got: dict = {}

        def run():
            try:
                got["screen"] = t.run_step(step)
            except BaseException as exc:  # noqa: BLE001 - the test reads it
                got["raised"] = exc
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th, got

    def wait_for(self, text, count=1):
        _until(lambda: self.out.getvalue().count(text) >= count, why=lambda: self.out.getvalue())

    def said(self, text):
        return self.out.getvalue().count(text)


def _no_line(prompt):
    raise AssertionError(f"a visible read was not expected here: {prompt}")


def _script(*typed):
    """A hidden read that gives these answers, one each, and says how often it was asked."""
    left, asked = list(typed), []

    def read(prompt):
        asked.append(prompt)
        return left.pop(0)
    read.asked = asked
    return read


def _held_read(value=""):
    """A hidden read nobody has answered yet: ``read.go.set()`` is her Enter."""
    go = threading.Event()

    def read(prompt):
        read.asked.append(prompt)
        go.wait(30)
        return value
    read.asked, read.go = [], go
    return read


def _held_read_on(go):
    def read(prompt):
        go.wait(30)
        return PIN
    return read


def _finished(th, got):
    th.join(20)
    assert not th.is_alive() and "raised" not in got, got
    return got["screen"]


# -- the engine ------------------------------------------------------------------------------------------------------
def test_the_engine_says_vault_on_every_poll_of_the_wait_to_the_terminal_only(state):
    w = Wait(state)
    w.finish_when_open = False
    for _ in range(4):
        assert w.eng.submit("build", {}, renderer="terminal").get("vault") is True
        assert "vault" not in w.eng.submit("build", {}, renderer="browser")
    w.locked = False
    assert "vault" not in w.eng.submit("build", {}, renderer="terminal")


def test_the_card_in_the_middle_of_a_wait_carries_vault_and_a_build_with_no_wait_does_not(state):
    w = Wait(state)
    w.card()
    screen = w.eng.submit("build", {}, renderer="terminal")
    assert screen["status"] == "ask" and screen["vault"] is True
    w.locked = False
    assert "vault" not in w.eng.submit("build", {}, renderer="terminal")


# -- the wait --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("job", JOBS)
def test_asks_at_the_build_wait_and_goes_on(state, job):
    w = Wait(state, job)
    read = _script(PIN)
    screen = w.terminal(read).run_step("build")
    assert screen["status"] == "done" and read.asked == ["  > "] and w.sent == [PIN]
    text = w.out.getvalue()
    assert text.index("vault is locked") < text.index(L.PIN_HERE) < text.index(L.PIN_OPENED)    # the line stands, first
    assert L.BUILD_ENDED in text


def test_a_terminal_with_injected_readers_and_no_answers_still_asks(state):
    w = Wait(state)
    read = _script(PIN)
    t = Terminal(w.eng, out=w.out, read_line=_no_line, read_hidden=read, sleep=lambda s: None, unlock=w.unlock)
    assert t.run_step("build")["status"] == "done" and read.asked == ["  > "] and w.sent == [PIN]


def test_the_words_are_plain_and_cover_what_she_asked():
    assert L.PIN_HERE == ("Or type your PIN right here and press Enter. Nothing shows as you type, that is normal. "
                          "To skip this, just press Enter.")
    for line in (L.PIN_HERE, L.PIN_SKIPPED, L.PIN_OPENED, L.PIN_ENTER_FIRST, L.PIN_NOT_NEEDED, L.PIN_NOT_REACHED, L.HIDDEN_READ_OUT):
        assert "\u2014" not in line and "\t" not in line


# -- Enter skips -----------------------------------------------------------------------------------------------------
def test_enter_skips_the_line_stands_no_second_ask_and_a_new_lock_asks_again(state):
    w = Wait(state)
    w.finish_when_open = False
    read = _script("", "")
    th, got = w.in_thread(w.terminal(read), "build")
    w.wait_for(L.PIN_SKIPPED)
    before = w.polls
    _until(lambda: w.polls > before + 5)              # it keeps polling, and asks no more
    assert len(read.asked) == 1 and w.said("vault is locked") == 1 and w.sent == []
    w.locked = False                                  # opened elsewhere: the wait is over
    before = w.polls
    _until(lambda: w.polls > before + 2)
    w.locked = True                                   # it locked again: a new wait
    w.wait_for(L.PIN_SKIPPED, 2)
    assert len(read.asked) == 2 and w.said("vault is locked") == 2 and w.said(L.PIN_HERE) == 2
    w.locked, w.finished = False, True
    assert _finished(th, got)["status"] == "done" and w.sent == []


def test_a_skipped_wait_stays_skipped_past_a_card_and_the_card_loses_nothing(state):
    w = Wait(state)
    w.finish_when_open = False
    read = _script("")
    th, got = w.in_thread(w.terminal(read, line=lambda p: "yes"), "build")
    w.wait_for(L.PIN_SKIPPED)
    w.card()
    w.wait_for("Let Toolsmith try an app?")
    _until(lambda: w.answered == [("c1", True)])       # the card goes its own way, with its own answer
    before = w.polls
    _until(lambda: w.polls > before + 5)
    assert len(read.asked) == 1 and w.sent == []       # the same wait: not asked again
    w.locked, w.finished = False, True
    assert _finished(th, got)["status"] == "done"


def test_a_card_that_comes_first_is_answered_first_then_the_pin_is_asked(state):
    w = Wait(state)
    w.finish_when_open = True
    w.card()
    read = _script(PIN)
    screen = w.terminal(read, line=lambda p: "no").run_step("build")
    assert w.answered == [("c1", False)] and w.sent == [PIN] and screen["status"] == "done"
    text = w.out.getvalue()
    assert text.index("Say Yes to this?") < text.index(L.PIN_HERE)


def test_a_card_that_comes_while_the_pin_is_asked_waits_and_only_its_own_answer_counts(state):
    w = Wait(state)
    w.finish_when_open = False
    read = _held_read(PIN)
    th, got = w.in_thread(w.terminal(read, line=lambda p: "no"), "build")
    w.wait_for(L.PIN_HERE)
    w.card()
    before = w.polls
    _until(lambda: w.polls > before + 2)
    assert w.said("Let Toolsmith try an app?") == 0    # nothing is written over the prompt
    read.go.set()
    w.wait_for("Let Toolsmith try an app?")
    _until(lambda: w.answered == [("c1", False)])
    w.finished = True
    assert _finished(th, got)["status"] == "done" and w.sent == [PIN]


# -- wrong and right -------------------------------------------------------------------------------------------------
def test_a_wrong_pin_says_the_routes_words_and_asks_again_then_the_right_one_opens(state):
    w = Wait(state)
    read = _script(WRONG, WRONG, PIN)
    screen = w.terminal(read).run_step("build")
    assert screen["status"] == "done" and w.sent == [WRONG, WRONG, PIN] and len(read.asked) == 3
    assert w.said(REFUSED) == 2 and w.said(L.PIN_OPENED) == 1 and w.said(L.PIN_HERE) == 3


def test_a_refusal_asks_again_at_once_with_no_sleep_and_no_poll_between(state):
    """F2. She retypes straight after a refusal, and until the next hidden read what she types shows. The order of what
    happens is real here; that nothing shows is only proved by the real pty below, off Windows."""
    w = Wait(state)
    order = []
    typed = [WRONG, WRONG, PIN]

    def read(prompt):
        order.append("read")
        return typed.pop(0)

    def unlock(pin):
        order.append("unlock")
        return w.unlock(pin)
    t = w.terminal(read, unlock=unlock)
    real_sleep, real_submit = t.sleep, w.eng.submit
    t.sleep = lambda s: (order.append("sleep"), real_sleep(s))[1]
    w.eng.submit = lambda *a, **kw: (order.append("poll"), real_submit(*a, **kw))[1]
    assert t.run_step("build")["status"] == "done"
    at = order.index("read")
    assert order[at:at + 6] == ["read", "unlock", "read", "unlock", "read", "unlock"], order
    assert w.sent == [WRONG, WRONG, PIN]


def test_a_card_that_came_while_she_typed_a_wrong_pin_goes_before_the_next_ask(state):
    w = Wait(state)
    w.finish_when_open = False
    go = threading.Event()
    asked = []

    def read(prompt):                                  # a wrong one, then Enter when she is asked after the card
        asked.append(prompt)
        if len(asked) == 1:
            go.wait(30)
            return WRONG
        return ""
    th, got = w.in_thread(w.terminal(read, line=lambda p: "no"), "build")
    w.wait_for(L.PIN_HERE)
    _until(lambda: asked)
    w.card()
    before = w.polls
    _until(lambda: w.polls > before + 2)
    go.set()
    _until(lambda: w.answered == [("c1", False)])
    w.wait_for(L.PIN_SKIPPED)
    w.locked, w.finished = False, True
    _finished(th, got)
    assert w.sent == [WRONG] and w.said(REFUSED) == 1 and len(asked) == 2
    text = w.out.getvalue()
    assert text.index(REFUSED) < text.index("Let Toolsmith try an app?") < text.index(L.PIN_SKIPPED)


def test_a_job_line_that_came_before_a_refusal_is_not_lost(state):
    w = Wait(state)
    typed = iter([WRONG, PIN])
    go = threading.Event()

    def read(prompt):
        w.reads_seen = getattr(w, "reads_seen", 0) + 1
        if w.reads_seen == 1:
            go.wait(30)
        return next(typed)
    th, got = w.in_thread(w.terminal(read), "build")
    w.wait_for(L.PIN_HERE)
    w.lines = ["Found an app."]
    before = w.polls
    _until(lambda: w.polls > before + 2)
    go.set()
    _finished(th, got)
    assert w.said("Found an app.") == 1 and w.sent == [WRONG, PIN]


def test_the_right_pin_is_not_asked_again_while_the_job_catches_up(state):
    """A job says waiting_vault until its own wait wakes up: a second ask for an open vault would be plain wrong."""
    w = Wait(state)
    w.stale_polls = 5
    read = _script(PIN)
    assert w.terminal(read).run_step("build")["status"] == "done"
    assert len(read.asked) == 1 and w.said(L.PIN_HERE) == 1


def test_no_answer_at_all_is_a_skip_and_never_spins(state):
    w = Wait(state)
    w.finish_when_open = False

    def ended(prompt):
        ended.asked += 1
        raise EOFError
    ended.asked = 0
    th, got = w.in_thread(w.terminal(ended), "build")
    w.wait_for(L.PIN_SKIPPED)
    before = w.polls
    _until(lambda: w.polls > before + 5)
    assert ended.asked == 1 and w.sent == []
    w.locked, w.finished = False, True
    _finished(th, got)


# -- the PIN goes nowhere else ---------------------------------------------------------------------------------------
def test_the_pin_is_in_no_output_and_not_in_the_progress_file(state):
    w = Wait(state)
    assert w.terminal(_script(WRONG, PIN)).run_step("build")["status"] == "done"
    text = w.out.getvalue()
    assert PIN not in text and WRONG not in text
    progress = (state / "settings" / "wizard.json").read_text(encoding="utf-8")
    assert PIN not in progress and WRONG not in progress


def test_the_pin_never_goes_through_the_engine(state):
    w = Wait(state)
    seen = []
    real = w.eng.submit

    def spy(step, answers=None, renderer="browser"):
        seen.append(answers)
        return real(step, answers, renderer)
    w.eng.submit = spy
    w.terminal(_script(WRONG, PIN)).run_step("build")
    assert seen and all(a == {} for a in seen)


# -- no unlock: today's behaviour ------------------------------------------------------------------------------------
def test_with_no_unlock_there_is_no_prompt_only_the_line(state):
    w = Wait(state)
    w.finish_when_open = False

    def never(prompt):
        raise AssertionError("no read at all with no unlock")
    th, got = w.in_thread(w.terminal(never, unlock=None), "build")
    w.wait_for("vault is locked")
    before = w.polls
    _until(lambda: w.polls > before + 5)
    w.locked, w.finished = False, True
    assert _finished(th, got)["status"] == "done"
    for words in (L.PIN_HERE, L.PIN_SKIPPED, L.PIN_OPENED, L.PIN_ENTER_FIRST):
        assert w.said(words) == 0


def _free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert port not in (8420, 8421, 8422, 3000)
    return port


def _ports():
    return ["--port", str(_free()), "--pages-port", str(_free()), "--proxy-port", "0"]


@pytest.mark.parametrize("stdin, stdout, passed", [(True, True, True), (False, True, False), (True, False, False), (False, False, False)],
                         ids=["terminal", "stdin-a-pipe", "stdout-a-pipe", "both-pipes"])
def test_only_setup_as_the_core_on_a_real_terminal_passes_unlock(monkeypatch, capsys, state, stdin, stdout, passed):
    from puffpai_core.zone2 import settings as model_settings
    model_settings.save(state, address="http://127.0.0.1:9")       # nothing listens: no real model server
    fake_in = object()
    monkeypatch.setattr(sys, "stdin", fake_in)
    monkeypatch.setattr(term, "is_console", lambda s: stdin if s is fake_in else stdout)
    monkeypatch.setattr(term, "builtins", lambda core, ask, write: [])
    got = []

    class Spy(term.Terminal):
        def __init__(self, *a, **kw):
            got.append(kw.get("unlock"))
            super().__init__(*a, **kw)

        def run(self):
            return 1
    monkeypatch.setattr(term, "Terminal", Spy)
    assert term.main(_ports() + ["--exit"]) == 1
    assert got == [term.core_unlock if passed else None]


def test_the_real_nul_is_not_a_console():
    """F3, no stand-in: the real device. On Windows ``isatty`` says yes to it, which is the fault."""
    with open(os.devnull, encoding="utf-8") as nul:
        assert nul.isatty() is (os.name == "nt")
        assert term.is_console(nul) is False


@pytest.mark.parametrize("stream", [io.StringIO(), None, types.SimpleNamespace(isatty=lambda: True)], ids=["text", "none", "no-file"])
def test_what_has_no_real_handle_is_not_a_console_on_windows(stream):
    assert term.is_console(stream) is (os.name != "nt" and stream is not None and stream.isatty())


@windows_only
def test_a_real_setup_with_no_console_and_its_output_on_nul_passes_no_unlock(tmp_path):
    """F3. A detached process with stdin and stdout on NUL, as nul_run does: there getpass spins at full CPU."""
    report = tmp_path / "nul.json"
    with open(os.devnull, encoding="utf-8") as stdin, open(os.devnull, "w", encoding="utf-8") as stdout:
        ran = subprocess.run([sys.executable, str(REAL_READ), CORE, "nul", str(report)], stdin=stdin, stdout=stdout,
                             stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS, timeout=60, check=False)
    assert ran.returncode == 0
    got = json.loads(report.read_text(encoding="utf-8"))
    assert got["stdin_isatty"] is True and got["stdout_isatty"] is True     # what used to pass
    assert got["setup_passes_unlock"] is False and got["stdin_is_console"] is False


def test_setup_on_a_core_another_window_runs_passes_no_unlock(monkeypatch, capsys, state):
    monkeypatch.setattr(term, "is_console", lambda s: True)
    got = []

    class Spy(term.Terminal):
        def __init__(self, *a, **kw):
            got.append(kw.get("unlock"))
            super().__init__(*a, **kw)

        def run(self):
            return 0
    monkeypatch.setattr(term, "Terminal", Spy)
    assert term._setup_remote({"port": 1, "token": "x"}, lambda *a: (200, {})) == 0   # noqa: SLF001
    assert got == [None]


# -- trap a: a visible read left out -------------------------------------------------------------------------------
def _overtaken(answers):
    """Leave a visible read out, as a question answered on the Desktop does (D122)."""
    def gone():
        raise term.Overtaken({"status": "working"})
    with pytest.raises(term.Overtaken):
        answers.line("  > ", gone, 0.0)
    assert answers.read_is_out


def test_with_a_visible_read_out_the_pin_is_not_read_through_it(state):
    w = Wait(state)
    typed = threading.Event()
    visible = []

    def line(prompt):
        visible.append(prompt)
        typed.wait(30)
        return PIN                                     # she typed it anyway: it shows, and it is dropped
    read = _script(PIN)
    t = w.terminal(read, line=line)
    _overtaken(w.reads)
    th, got = w.in_thread(t, "build")
    w.wait_for(L.PIN_ENTER_FIRST)
    before = w.polls
    _until(lambda: w.polls > before + 5)              # polls hold on: nothing waits on the read
    assert read.asked == [] and w.said(L.PIN_HERE) == 0 and w.said(L.PIN_ENTER_FIRST) == 1 and w.sent == []
    typed.set()                                        # Enter: the left-over line is dropped, and a later poll asks
    assert _finished(th, got)["status"] == "done"
    assert read.asked == ["  > "] and len(visible) == 1 and w.sent == [PIN]
    assert w.said(L.PIN_HERE) == 1


def test_answers_never_read_a_secret_through_a_visible_read():
    go = threading.Event()
    out = io.StringIO()

    def line(prompt):
        go.wait(30)
        return "a line that shows"
    a = term.Answers(lambda: False, read_line=line, read_hidden=lambda p: "the hidden answer", out=out)
    _overtaken(a)
    threading.Timer(0.3, go.set).start()
    assert a.hidden("  > ") == "the hidden answer"     # its own hidden read, after that one ended
    assert L.PIN_ENTER_FIRST in out.getvalue()


# -- trap b: a hidden read left out ----------------------------------------------------------------------------------
def test_a_hidden_read_left_out_never_answers_a_later_question():
    go = threading.Event()
    stop = threading.Event()

    def hidden(prompt):
        go.wait(30)
        return PIN
    a = term.Answers(stop.is_set, read_line=lambda p: "her own answer", read_hidden=hidden, out=io.StringIO())
    threading.Timer(0.1, stop.set).start()
    with pytest.raises(term.SetupStopped):
        a.hidden("  > ")
    assert a.read_is_out
    stop.clear()
    threading.Timer(0.3, go.set).start()
    assert a.line("  > ") == "her own answer"


@pytest.mark.parametrize("job", JOBS)
def test_the_flow_leaves_no_hidden_read_out(state, job):
    w = Wait(state, job)
    th, got = w.in_thread(w.terminal(_script("")), "build")
    w.wait_for(L.PIN_SKIPPED)
    assert not w.reads.read_is_out
    w.locked, w.finished = False, True
    _finished(th, got)
    assert not w.reads.read_is_out


# -- trap c: her terminal is not left with no echo ---------------------------------------------------------------------
class FakeTermios:
    TCSANOW = 0
    TCSAFLUSH = 2

    def __init__(self):
        self.set = []

    def tcgetattr(self, fd):
        return ["the settings of", fd]

    def tcsetattr(self, fd, when, saved):
        self.set.append((fd, when, saved))


@pytest.fixture
def fake_terminal(monkeypatch):
    """Runs on every computer, Windows too: a stand-in for ``termios``, and an ``os._exit`` that only writes it down."""
    fake = FakeTermios()
    monkeypatch.setattr(term, "termios", fake)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(fileno=lambda: 7))
    fake.exits = []
    monkeypatch.setattr(term.os, "_exit", fake.exits.append)
    return fake


def test_a_stop_at_the_hidden_read_gives_her_terminal_back(fake_terminal):
    stop = threading.Event()
    read = _held_read()
    a = term.Answers(stop.is_set, read_hidden=read)
    threading.Timer(0.1, stop.set).start()
    t0 = time.monotonic()
    with pytest.raises(term.SetupStopped):
        a.hidden("  > ")
    assert time.monotonic() - t0 < 2.0 and a.read_is_out
    assert fake_terminal.set == []                     # not before the way out
    term._leave(a, 1)                                  # noqa: SLF001
    # TCSAFLUSH, or a half-typed PIN goes to her shell (F4). The real pty test below shows that for real.
    assert fake_terminal.set == [(7, FakeTermios.TCSAFLUSH, ["the settings of", 7])] and fake_terminal.exits == [1]
    read.go.set()


def test_ctrl_c_at_the_hidden_read_gives_her_terminal_back(fake_terminal):
    read = _held_read()
    a = term.Answers(lambda: False, read_hidden=read)

    def ctrl_c():
        raise KeyboardInterrupt                        # off Windows the signal comes to the main thread, not the read
    with pytest.raises(KeyboardInterrupt):
        a.hidden("  > ", ctrl_c, 0.0)
    term._leave(a, 1)                                  # noqa: SLF001
    assert len(fake_terminal.set) == 1 and fake_terminal.exits == [1]
    read.go.set()


def test_a_read_that_ended_or_a_visible_one_touches_no_terminal_settings(fake_terminal):
    a = term.Answers(lambda: False, read_hidden=lambda p: "x")
    assert a.hidden("  > ") == "x"
    assert term._leave(a, 0) == 0                      # noqa: SLF001
    assert fake_terminal.set == [] and fake_terminal.exits == []

    go = threading.Event()

    def line(prompt):
        go.wait(30)
        return ""
    a = term.Answers(lambda: False, read_line=line)
    _overtaken(a)
    term._leave(a, 1)                                  # noqa: SLF001
    assert fake_terminal.set == [] and fake_terminal.exits == [1]
    go.set()


def test_a_stdin_with_no_terminal_has_nothing_to_give_back(monkeypatch):
    monkeypatch.setattr(term, "termios", FakeTermios())
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(fileno=lambda: (_ for _ in ()).throw(ValueError("closed"))))
    assert term._echo_back() is None                   # noqa: SLF001


@windows_only
def test_on_windows_with_no_console_there_is_nothing_to_give_back(monkeypatch):
    def no_console(set_to=None):
        raise OSError("no console")
    monkeypatch.setattr(term, "_conin_mode", no_console)
    assert term._echo_back() is None                   # noqa: SLF001
    monkeypatch.setattr(term, "_conin_mode", lambda set_to=None: None)
    assert term._echo_back() is None                   # noqa: SLF001


@windows_only
def test_a_real_console_gets_its_input_mode_back_after_a_stop(tmp_path):
    """F1, no stand-in: real getpass and its getwch, a real hidden console, the real ``_leave``. Nobody types, as at a
    stop. The console is gone with the process here; that a later program in the same window can read is ``after2.py``."""
    report = tmp_path / "console.json"
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0                                 # hidden: no window pops up while the tests run
    proc = subprocess.Popen([sys.executable, str(REAL_READ), CORE, "console", str(report)],
                            creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
    try:
        assert proc.wait(60) == 7
    finally:
        if proc.poll() is None:
            proc.kill()                                # this process only, the one this test started
    got = json.loads(report.read_text(encoding="utf-8"))
    assert got["stdin_is_console"] is True and got["stdout_is_console"] is True     # F3: a real console still counts
    assert got["left_out"] is True and got["code"] == 7
    assert got["before"] and got["during"] == 0        # what getwch does: the fault itself, or this proves nothing
    assert got["after"] == got["before"]


@posix_only
def test_a_retyped_pin_never_shows_on_a_real_pty():
    """F2, no stand-in: real getpass on a real pty, retyped straight after the refusal."""
    ran = subprocess.run([sys.executable, str(REAL_READ), CORE, "gap"], capture_output=True, text=True, timeout=60, check=False)
    got = json.loads(ran.stdout)
    assert got["refused"] and got["asked_again"] and not got["retyped_visible"] and not got["first_visible"], got
    assert got["echo_on_ms"] is not None and got["echo_on_ms"] < 500, got     # the route here answers at once; it was 2000


@posix_only
def test_a_half_typed_pin_does_not_reach_her_real_shell_at_a_stop():
    """F4, no stand-in: a real shell on a real pty, and echo works again in it."""
    ran = subprocess.run([sys.executable, str(REAL_READ), CORE, "flush"], capture_output=True, text=True, timeout=60, check=False)
    got = json.loads(ran.stdout)
    assert got["stopped"] and not got["half_reached_the_shell"] and got["shell_echoes_again"], got


def test_a_question_after_a_hidden_read_left_out_says_to_press_enter():
    """F6. Ctrl+C at the chat's /unlock leaves its read out, off Windows, and the chat's next line waits on it."""
    go = threading.Event()
    out = io.StringIO()
    a = term.Answers(lambda: False, read_line=lambda p: "next line", read_hidden=_held_read_on(go), out=out)

    def ctrl_c():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        a.hidden("  > ", ctrl_c, 0.0)
    threading.Timer(0.3, go.set).start()
    assert a.line("  > ") == "next line"
    assert out.getvalue().count(L.HIDDEN_READ_OUT) == 1 and L.PIN_ENTER_FIRST not in out.getvalue()


def test_ctrl_c_at_a_prompt_puts_the_stopped_line_on_a_line_of_its_own(monkeypatch, capsys, state):
    """F7. The real main; the Ctrl+C is raised where a read that ended with it raises it, with no read left out."""
    from puffpai_core.api.app import STOPPED_LINE
    from puffpai_core.zone2 import settings as model_settings
    model_settings.save(state, address="http://127.0.0.1:9")       # nothing listens: no real model server
    monkeypatch.setattr(term, "builtins", lambda core, ask, write: [])

    def at_the_prompt(self):
        print("  > ", end="", flush=True)
        raise KeyboardInterrupt
    monkeypatch.setattr(term.Terminal, "run", at_the_prompt)
    term.main(_ports() + ["--exit"])
    out = capsys.readouterr().out
    assert STOPPED_LINE in out.splitlines()


@posix_only
def test_a_real_pty_gets_its_echo_back(monkeypatch):
    import pty
    import termios
    master, slave = pty.openpty()
    try:
        monkeypatch.setattr(sys, "stdin", os.fdopen(os.dup(slave)))
        monkeypatch.setattr(term.os, "_exit", lambda code: None)
        go, off, stop = threading.Event(), threading.Event(), threading.Event()

        def read(prompt):                              # what getpass does to the terminal; its finally never comes
            now = termios.tcgetattr(slave)
            now[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSAFLUSH, now)
            off.set()
            go.wait(30)
            return ""
        a = term.Answers(stop.is_set, read_hidden=read)
        threading.Timer(0.3, stop.set).start()
        with pytest.raises(term.SetupStopped):
            a.hidden("  > ")
        assert off.wait(5) and not termios.tcgetattr(slave)[3] & termios.ECHO
        term._leave(a, 1)                              # noqa: SLF001
        assert termios.tcgetattr(slave)[3] & termios.ECHO
        go.set()
    finally:
        os.close(master)
        os.close(slave)


def test_stop_ends_setup_while_the_prompt_is_up(state):
    stop = threading.Event()
    w = Wait(state)
    w.finish_when_open = False
    read = _held_read()
    th, got = w.in_thread(w.terminal(read, stopped=stop.is_set), "build")
    w.wait_for(L.PIN_HERE)
    _until(lambda: read.asked)
    stop.set()
    th.join(5)
    assert not th.is_alive() and isinstance(got.get("raised"), term.SetupStopped), got
    assert w.reads.read_is_out and w.sent == []
    read.go.set()


# -- trap d: the vault opened elsewhere while the prompt is up -----------------------------------------------------------
@pytest.mark.parametrize("typed", ["", PIN, WRONG], ids=["enter", "the-pin", "a-wrong-one"])
def test_opened_elsewhere_says_it_once_and_drops_what_comes_back(state, typed):
    w = Wait(state)
    w.finish_when_open = False
    read = _held_read(typed)
    th, got = w.in_thread(w.terminal(read), "build")
    w.wait_for(L.PIN_HERE)
    _until(lambda: read.asked)
    w.lines = ["Found an app."]
    before = w.polls
    _until(lambda: w.polls > before + 2)
    assert w.said("Found an app.") == 0                # nothing is written over the prompt, and it is not lost
    w.locked = False
    w.wait_for(L.PIN_NOT_NEEDED)
    before = w.polls
    time.sleep(0.5)
    assert w.said(L.PIN_NOT_NEEDED) == 1 and w.polls == before     # said once; the next screen waits for her Enter
    read.go.set()
    w.wait_for("Found an app.")
    w.finished = True
    assert _finished(th, got)["status"] == "done"
    assert w.sent == [] and w.said(L.PIN_SKIPPED) == 0 and w.said(L.PIN_HERE) == 1 and w.said(L.PIN_OPENED) == 0
    assert w.said(REFUSED) == 0 and not w.reads.read_is_out
    text = w.out.getvalue()
    assert PIN not in text and WRONG not in text


def test_a_build_that_ended_while_the_prompt_is_up_is_not_lost(state):
    w = Wait(state)
    w.finish_when_open = False
    read = _held_read(PIN)
    th, got = w.in_thread(w.terminal(read), "build")
    w.wait_for(L.PIN_HERE)
    w.locked, w.finished, w.lines = False, True, ["The last line."]
    w.wait_for(L.PIN_NOT_NEEDED)
    read.go.set()
    assert _finished(th, got)["status"] == "done" and w.sent == []
    assert w.said(L.BUILD_ENDED) == 1 and w.said("The last line.") == 1
    assert w.eng.overview()["current"] != "build"


def test_what_comes_back_is_never_the_answer_to_the_card_that_comes_next(state):
    w = Wait(state)
    w.finish_when_open = False
    read = _held_read(PIN)
    th, got = w.in_thread(w.terminal(read, line=lambda p: "yes"), "build")
    w.wait_for(L.PIN_HERE)
    w.locked = False
    w.card()
    w.wait_for(L.PIN_NOT_NEEDED)
    read.go.set()
    _until(lambda: w.answered == [("c1", True)])       # her own visible answer, not what the hidden read took
    w.finished = True
    assert _finished(th, got)["status"] == "done" and w.sent == []


def test_a_poll_that_fails_while_the_prompt_is_up_does_not_leave_the_read_out(state):
    w = Wait(state)
    read = _held_read(PIN)
    t = w.terminal(read)
    real = w.eng.submit
    failing = threading.Event()

    def flaky(*a, **kw):
        if failing.is_set():
            raise RuntimeError("the poll failed")
        return real(*a, **kw)
    w.eng.submit = flaky
    th, got = w.in_thread(t, "build")
    w.wait_for(L.PIN_HERE)
    failing.set()
    time.sleep(0.5)
    failing.clear()
    read.go.set()
    assert _finished(th, got)["status"] == "done" and w.sent == [PIN]


# -- the unlock callable -------------------------------------------------------------------------------------------------
def test_core_unlock_sends_the_one_call_and_clears_the_body(monkeypatch):
    from puffpai_core import cli
    calls = []

    def call(info, method, path, body=None):
        calls.append((method, path, dict(body), body))
        return 200, {"state": "open"}
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "the-terminal-key"})
    monkeypatch.setattr(cli, "_call", call)
    assert term.core_unlock(PIN) == (True, L.PIN_OPENED)
    (method, path, sent, body), = calls
    assert (method, path, sent) == ("POST", "/v1/vault/unlock", {"method": "pin", "secret": PIN}) and body == {}


@pytest.mark.parametrize("reply, words", [
    ((401, {"error": "That didn't match.", "code": "wrong"}), "Your vault did not open: That didn't match."),
    ((429, {"error": "Too many tries. Wait a minute.", "code": "throttled"}), "Your vault did not open: Too many tries. Wait a minute."),
    ((500, "<html>"), "Your vault did not open: the PIN was not taken."),
])
def test_core_unlock_says_the_routes_own_words(monkeypatch, reply, words):
    from puffpai_core import cli
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "k"})
    monkeypatch.setattr(cli, "_call", lambda *a: reply)
    assert term.core_unlock(PIN) == (False, words)


def test_core_unlock_with_no_core_or_a_failed_call_never_says_the_error_text(monkeypatch):
    from puffpai_core import cli
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    assert term.core_unlock(PIN) == (False, f"Your vault did not open: {L.PIN_NOT_REACHED}")
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "k"})

    def fails(*a):
        raise OSError(f"a message with {PIN} in it")
    monkeypatch.setattr(cli, "_call", fails)
    ok, words = term.core_unlock(PIN)
    assert ok is False and PIN not in words


# -- all of it, for real: a real Core, a real vault, the real route ---------------------------------------------------
def test_the_real_setup_opens_the_real_vault_through_the_real_route(monkeypatch, capsys, state):
    """``puffpai setup``'s real main on free ports. getpass and input are stand-ins, and is_console says yes: nothing here
    can be a real terminal. Everything after what she types is real: the Core, its terminal key, the route, the vault."""
    from puffpai_core.api.app import create_app, wire_core
    from puffpai_core.wiring import WizardToolsmith as W
    from puffpai_core.zone1 import archivist
    from puffpai_core.zone2 import settings as model_settings
    model_settings.save(state, address="http://127.0.0.1:9")       # nothing listens: no real model server

    core = wire_core(create_app(state))
    core.vault.create("here", None, PIN)
    vault = archivist.for_state(state).vault
    archivist.for_state(state).lock("owner")
    assert vault.is_set_up() and not vault.is_open()
    settings = state / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    steps = {s: {"state": "done", "at": "2026-10-31T00:00:00Z", "by": "terminal"} for s in BEFORE_BUILD}
    (settings / "wizard.json").write_text(json.dumps({"steps": steps, "partial": {}, "answers": {s: {} for s in BEFORE_BUILD},
                                                      "complete": False, "build": "b1"}), encoding="utf-8")

    card = {"consent_id": "c1", "title": "Let Toolsmith try an app?", "lines": ["It needs your Yes."], "kind": "package"}
    monkeypatch.setattr(W, "start", lambda self, build: {"ok": True, "already": True})
    monkeypatch.setattr(W, "progress", lambda self, build: {
        "jobs": [{"id": "j1", "name": "Birds", "status": "building: an app" if vault.is_open() else "waiting_vault",
                  "line": "", "now": "", "lines": [], "since": ""}],
        "steps": [], "cards": [card] if vault.is_open() else [], "finished": False})
    monkeypatch.setattr(term, "builtins", lambda core, ask, write: [])
    monkeypatch.setattr(term, "is_console", lambda s: True)
    typed = [WRONG, PIN]
    asked = []

    def hidden(prompt):
        asked.append(prompt)
        return typed.pop(0)
    open_at_the_question = []

    def visible(prompt=""):
        open_at_the_question.append(vault.is_open())
        raise EOFError                                 # the card's question: this test ends here
    monkeypatch.setattr(getpass, "getpass", hidden)
    monkeypatch.setattr("builtins.input", visible)
    code = term.main(_ports() + ["--exit"])
    out = capsys.readouterr().out
    assert code == 1 and asked == ["  > ", "  > "] and typed == []
    assert open_at_the_question == [True], out
    assert "Your vault did not open: That didn't match." in out and L.PIN_OPENED in out
    assert out.index("vault is locked") < out.index(L.PIN_HERE) < out.index("That didn't match.") < out.index(L.PIN_OPENED)
    assert not (settings / "terminal.json").exists()    # the Core really stopped
    assert PIN not in out and WRONG not in out
    for path in state.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert PIN.encode() not in data and WRONG.encode() not in data, path
