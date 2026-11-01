"""D113 (lane i24b): a locked vault during a build is said where she is, a long step shows it is alive, and pages
keep a worker on the Core address.

Her words: "I had to run the start buildbot command to open the dashboard and unlock the vaut but there was nothing
telling me that. and the page on the dashboard looks like its timed out on the build part?"

Fake Toolsmith for the step; a real waitress Core on free ports (never 8420-8422) for the workers."""

import os
import io
import logging
import shutil
import socket
import subprocess
import threading
import time
import types
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from conftest import REPO
from puffpai_core.wizard import Terminal
from puffpai_core.wizard import lines as L
from puffpai_core.wizard import terminal as term
from wizard_kit import Kit

NODE = shutil.which("node")
WEB = REPO / "packages" / "desktop" / "web"
JOBS = ["Home recipe box", "Small-business invoices", "Birdwatching log"]    # any goal, the same words


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _job(name, status, *, now="", lines=(), since=""):
    return {"id": name, "name": name, "status": status, "line": "", "now": now, "lines": list(lines), "since": since}


class Step:
    """The real Engine and BuildStep on a fake Toolsmith whose jobs a test sets."""

    def __init__(self, state, jobs):
        kit = Kit(state)
        self.eng = kit.engine()
        self.eng.set_build("b1")
        self.jobs = jobs
        kit.toolsmith.progress = lambda build: {"jobs": [dict(j) for j in self.jobs], "steps": [], "cards": []}
        self.hook = self.eng.hook("build")

    def poll(self, renderer="terminal"):
        return self.eng.submit("build", {}, renderer=renderer)


def _vault_lines(screen):
    return [x for x in screen["lines"] if "vault is locked" in x]


@pytest.mark.parametrize("name", JOBS)
def test_the_line_comes_once_per_wait_and_again_after_a_new_lock(state, name):
    s = Step(state, [_job(name, "building: An app")])
    assert _vault_lines(s.poll()) == []                  # the vault is open: not said
    s.jobs = [_job(name, "waiting_vault")]
    assert len(_vault_lines(s.poll())) == 1
    for _ in range(5):                                   # not on every poll
        assert _vault_lines(s.poll()) == []
    s.jobs = [_job(name, "building: An app")]            # she unlocked it
    assert _vault_lines(s.poll()) == []
    s.jobs = [_job(name, "waiting_vault")]               # it locked again
    assert len(_vault_lines(s.poll())) == 1


def test_a_second_job_waiting_on_the_same_lock_is_not_a_new_wait(state):
    s = Step(state, [_job("A", "waiting_vault"), _job("B", "building: X")])
    assert len(_vault_lines(s.poll())) == 1
    s.jobs = [_job("A", "waiting_vault"), _job("B", "waiting_vault")]
    assert _vault_lines(s.poll()) == []
    s.jobs = [_job("A", "building: X"), _job("B", "waiting_vault")]      # one at a time after her unlock
    assert _vault_lines(s.poll()) == []


def test_the_terminal_really_prints_it_again_after_a_new_lock(state):
    """Its window never prints a line twice, so the line itself is marked to go through."""
    s = Step(state, [_job("Birdwatching log", "waiting_vault")])
    out = io.StringIO()
    t = Terminal(s.eng, out=out, read_line=lambda p: "", read_hidden=lambda p: "")
    t._lines(s.poll())
    t._lines(s.poll())
    s.jobs = [_job("Birdwatching log", "building: X")]
    t._lines(s.poll())
    s.jobs = [_job("Birdwatching log", "waiting_vault")]
    t._lines(s.poll())
    assert out.getvalue().count("vault is locked") == 2
    t._lines({"lines": [term.vault_wait_line()]})         # any other screen: still never twice
    assert out.getvalue().count("vault is locked") == 2


def test_the_line_names_a_way_that_works_here(tmp_path, monkeypatch):
    start = tmp_path / term.launcher_name("Start BuildBot.cmd")
    monkeypatch.setattr(term, "launcher", lambda: start)
    chat = term.chat_launcher()
    chat.write_text("rem", encoding="utf-8")
    line = term.vault_wait_line()
    assert term.how_to_run(chat) in line and "/unlock" in line and "PIN" in line
    if os.name == "nt":
        assert f"double-click Chat with BuildBot.cmd in {tmp_path}" in line
    assert "\u2014" not in line
    monkeypatch.setattr(term, "launcher", lambda: tmp_path / "no such folder" / "no such file")
    line = term.vault_wait_line()
    assert term.chat_launcher().name not in line and " chat" in line        # from source: the command itself
    assert "/unlock" in line


def test_start_says_already_running_and_opens_what_she_picked_on_the_setup_core(monkeypatch, capsys, state):
    """The path the line names: her double-click finds the Core setup started, starts no second one, opens the window."""
    from puffpai_core import cli, window
    monkeypatch.setenv("PUFFPAI_STATE", str(state))
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 18601, "pid": 1, "token": "t"})
    monkeypatch.setattr(cli, "_launch_serve", lambda *a: pytest.fail("a second Core"))
    opened = []
    monkeypatch.setattr(window, "open_window", lambda port: opened.append(port) or (True, "The window is open."))
    assert cli._cmd_start_bot(types.SimpleNamespace(opens=None, port=18601, wait=1)) == 0
    assert opened == [18601] and "BuildBot is already running." in capsys.readouterr().out


def test_both_renderers_get_every_line_on_the_one_engine(state):
    """terminal.py gives the browser's engine to the setup terminal: each poll used to take the lines from the other."""
    s = Step(state, [_job("Home recipe box", "building: X", now="step 1", lines=["one", "two"])])
    first = s.poll("terminal")
    assert "Home recipe box: one" in first["lines"] and "Home recipe box: step 1" in first["lines"]
    page = s.poll("browser")
    assert "Home recipe box: one" in page["lines"] and "Home recipe box: step 1" in page["lines"]
    assert s.poll("terminal")["lines"] == [] and s.poll("browser")["lines"] == []


def test_the_setup_page_gets_the_state_on_every_screen_so_a_reload_still_shows_it(state):
    s = Step(state, [_job("Small-business invoices", "waiting_vault", since="2026-10-31T05:55:00+00:00")])
    for _ in range(3):
        screen = s.poll("browser")
        assert screen["status"] == "working" and screen["alive"]["vault"] is True
        assert screen["alive"]["jobs"][0]["since"] == "2026-10-31T05:55:00+00:00"
        assert _vault_lines(screen) == []                # drawn in one place by the page, never added up
    s.jobs = [_job("Small-business invoices", "building: X")]
    assert s.poll("browser")["alive"]["vault"] is False
    assert "alive" not in s.poll("terminal")


@pytest.mark.parametrize("name", JOBS)
def test_a_long_step_says_it_is_alive_in_the_terminal_every_few_minutes(state, name):
    s = Step(state, [_job(name, "building: An app", now="step 4 of at most 20: running.", since=_ago(12))])
    t = [1000.0]
    s.hook.clock = lambda: t[0]
    s.poll()
    assert s.poll()["lines"] == []
    t[0] += s.hook.STILL_EVERY_S - 1
    assert s.poll()["lines"] == []
    t[0] += 2
    assert s.poll()["lines"] == [f"Still working: {name}: step 4 of at most 20: running, 12 minutes so far."]
    assert s.poll()["lines"] == []                       # not again until the next few minutes
    s.jobs = [_job(name, "building: An app", now="step 5", since=_ago(13))]
    t[0] += s.hook.STILL_EVERY_S + 1
    assert s.poll()["lines"] == [f"{name}: step 5"]      # a new line starts the quiet over
    s.jobs = [_job(name, "waiting_vault", now="step 5", since=_ago(13))]
    t[0] += s.hook.STILL_EVERY_S + 1
    assert not [x for x in s.poll()["lines"] if x.startswith("Still working")]     # a job that waits is not working


def test_the_still_line_with_no_time_to_read():
    at = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)
    assert L.still_line({"name": "Birds", "now": "", "since": ""}, at) == "Still working: Birds."
    assert L.still_line({"name": "Birds", "since": "2026-10-31T05:59:30+00:00"}, at) == "Still working: Birds."
    assert L.still_line({"name": "Birds", "since": "2026-10-31T05:59:00+00:00"}, at) == "Still working: Birds, 1 minute so far."


def test_the_real_progress_carries_since():
    from puffpai_core.wiring import WizardToolsmith
    b = {"id": "b1", "yes": True, "jobs": [{"id": "b1-1", "name": "J", "status": "building", "run": {"since": "S", "now": "N"}}]}
    host = types.SimpleNamespace(view=lambda _: b, running=lambda _: True, steps_of=lambda _: [],
                                 store_phase=types.SimpleNamespace(record=lambda _: None),
                                 trials=types.SimpleNamespace(hold_cards=lambda _: [], of_build=lambda _: []))
    ts = WizardToolsmith.__new__(WizardToolsmith)
    ts.host = lambda: host
    assert ts.progress("b1")["jobs"][0]["since"] == "S"


# -- the workers on the Core address ---------------------------------------------------------------------------------

def _free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert port not in (8420, 8421, 8422, 3000)
    return port


def test_queue_depth_is_quiet_and_real_waitress_errors_are_not(state):
    from puffpai_core.api.app import create_app, make_servers, stop_servers
    port, pages = _free(), _free()
    app = create_app(state, port=port, pages_port=pages)
    servers = make_servers(app, port, pages)
    try:
        assert not logging.getLogger("waitress.queue").isEnabledFor(logging.WARNING)
        assert logging.getLogger("waitress.queue").isEnabledFor(logging.ERROR)
        assert logging.getLogger("waitress").isEnabledFor(logging.WARNING)
    finally:
        stop_servers(servers)


def test_pages_still_answer_while_long_requests_hold_workers(state):
    """Her build's shape, and more: long calls and event streams. With 8 workers, 6 long calls and 2 streams left
    every page waiting (24 s measured, D113)."""
    from puffpai_core.api.app import CORE_WORKERS, create_app, make_servers, run_servers
    assert CORE_WORKERS == 32
    port, pages = _free(), _free()
    app = create_app(state, port=port, pages_port=pages)
    key = app.extensions["puffpai"].tokens.issue_device("test")
    done = threading.Event()
    inner = app.wsgi_app

    def holding(env, reply):
        if env["PATH_INFO"] == "/hold":                   # a worker taken, as an install in one request takes it
            done.wait(30)
            reply("200 OK", [("Content-Length", "0")])
            return [b""]
        return inner(env, reply)
    app.wsgi_app = holding
    servers = make_servers(app, port, pages)
    stop = threading.Event()
    threading.Thread(target=run_servers, args=(servers,), kwargs={"stop": stop}, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    head = {"Authorization": "Bearer " + key, "X-PuffPAI": "1"}

    def get(path, timeout):
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=head), timeout=timeout) as r:
            return r.status, r.read()
    for path in ["/hold"] * 12 + ["/v1/events"] * 4:
        threading.Thread(target=lambda p=path: _quiet(get, p), daemon=True).start()
    try:
        time.sleep(1.0)
        began = time.monotonic()
        for path in ("/v1/toolsmith", "/v1/vault/status", "/v1/consent?status=pending"):
            assert get(path, 10)[0] == 200
        assert time.monotonic() - began < 5
    finally:
        done.set()
        stop.set()


def _quiet(get, path):
    try:
        get(path, 40)
    except OSError:
        pass


# -- the Desktop's logic ---------------------------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_desktop_logic():
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "i24b.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr
    for name in ("logic.js", "desktop.js", "window.js", "pages/building.js", "pages/home.js"):
        chk = subprocess.run([NODE, "--check", str(WEB / name)], capture_output=True, text=True, timeout=60)
        assert chk.returncode == 0, chk.stderr


