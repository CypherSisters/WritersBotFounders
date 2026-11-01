"""``puffpai shutdown``, ``POST /v1/shutdown`` and ``Stop BuildBot.cmd`` (lane I7).

The real-Core tests start ``serve`` on free ports (never 8420-8422) inside a Job Object the test makes
(``console_kit._held``: started suspended, put in the job, then let run). "Left" means "still in that job",
read from the job itself. Cleanup is that job's own TerminateJobObject, nothing else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import FIXTURES

REPO = Path(__file__).resolve().parents[1]
windows_only = pytest.mark.skipif(os.name != "nt", reason="Job Objects and cmd.exe launchers")


# -- the route: who may ask ----------------------------------------------------------------------------
@pytest.fixture
def app(state):
    from puffpai_core.api import create_app
    return create_app(state)


def _post(app, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return app.test_client().post("/v1/shutdown", json={}, headers=headers)


def test_only_the_owner_at_this_computer_may_stop_it(app):
    from puffpai_core.api.app import accept_shutdown
    core = app.extensions["puffpai"]
    stop = accept_shutdown(app)
    for token, code in ((None, 401), (core.tokens.issue_owner_key(), 403),
                        (core.tokens.issue_package_token("hello-tool"), 403),
                        (core.tokens.issue_toolsmith("b-1"), 403), ("ppd_not-a-real-token", 401)):
        r = _post(app, token)
        assert r.status_code == code, (token[:4] if token else None, r.status_code, r.json)
        assert not stop.is_set()
    r = _post(app, core.tokens.issue_device("this computer's browser"))
    assert r.status_code == 202 and r.json["stopping"] is True
    assert stop.is_set()


def test_a_terminal_key_from_another_run_is_refused(app):
    from puffpai_core.api.app import TERMINAL_LABEL, accept_shutdown
    core = app.extensions["puffpai"]
    stop = accept_shutdown(app)
    core.terminal_label = f"{TERMINAL_LABEL} (run now)"
    old = core.tokens.issue_device(f"{TERMINAL_LABEL} (run before)")
    assert _post(app, old).status_code == 401 and not stop.is_set()
    now = core.tokens.issue_device(core.terminal_label)
    assert _post(app, now).status_code == 202 and stop.is_set()


def test_the_route_stops_a_core_that_has_not_said_it_accepts_yet(app):
    """D122: there is no more 409 while setup asks its questions (``test_d122_stop_during_setup.py``)."""
    from puffpai_core.api.groups.shutdown import STOP
    core = app.extensions["puffpai"]
    assert STOP not in app.extensions
    r = _post(app, core.tokens.issue_device("this computer's browser"))
    assert r.status_code == 202 and app.extensions[STOP].is_set()


def test_shutdown_with_nothing_running_says_so(state, capsys):
    from puffpai_core.cli import NOT_RUNNING, main
    assert main(["shutdown"]) == 1
    assert capsys.readouterr().out.strip() == NOT_RUNNING
    (state / "settings").mkdir(parents=True, exist_ok=True)       # a key left by a Core that crashed
    (state / "settings" / "terminal.json").write_text(json.dumps({"port": _free_ports(1)[0], "token": "ppd_x"}),
                                                      encoding="utf-8")
    assert main(["shutdown"]) == 1
    assert capsys.readouterr().out.strip() == NOT_RUNNING


def test_the_running_screen_names_the_stop_file(tmp_path, monkeypatch):
    from puffpai_core.wizard import terminal
    monkeypatch.setattr(terminal, "launcher", lambda: tmp_path / "Start BuildBot.cmd")
    lines = terminal.running_lines(18430, "AAAA-BBBB-CCCC")
    assert any(ln.startswith("Or run this in another terminal to stop it from anywhere: ") and ln.endswith("shutdown") for ln in lines)
    (tmp_path / "Stop BuildBot.cmd").write_text("@echo off\n", encoding="ascii")
    lines = terminal.running_lines(18430, "AAAA-BBBB-CCCC")
    assert f"Or double-click Stop BuildBot.cmd in {tmp_path} to stop it from anywhere." in lines


# -- a real Core in the test's own job ------------------------------------------------------------------
def _free_ports(n):
    import socket
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    assert not set(ports) & {8420, 8421, 8422}
    return ports


def _env(**more):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "PUFFPAI_"))}
    env.update(PYTHONUNBUFFERED="1", PIP_REQUIRE_VIRTUALENV="1", PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
               PYTHONPATH=os.pathsep.join([str(REPO / "core"), str(REPO / "tests")]), **more)
    return env


def _bot_with_a_running_package(monkeypatch):
    """hello-tool installed (in the test's state folder) and marked to start with the Core."""
    from puffpai_core.cli import main
    from puffpai_core.paths import state_dir
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["add", str(FIXTURES / "hello-tool")]) == 0
    s = state_dir()
    (s / "settings").mkdir(parents=True, exist_ok=True)
    (s / "settings" / "steward.json").write_text(json.dumps({"was_running": ["hello-tool"]}), encoding="utf-8")
    return s


def _start_core(env, folder: Path):
    """``serve`` in a Job Object of the test's own; returns ``(job, log path)``."""
    import console_kit
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / "serve.txt"
    log = open(log_path, "w", encoding="utf-8")
    core_port, pages_port, proxy_port = _free_ports(3)
    argv = [sys.executable, str(REPO / "tests" / "console_core.py"), "serve", "--port", str(core_port),
            "--pages-port", str(pages_port), "--proxy-port", str(proxy_port)]
    job = console_kit._held(argv, subprocess.CREATE_NEW_PROCESS_GROUP, env=env, stdout=log,   # noqa: SLF001
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    log.close()                                       # the child has its own handle
    return job, log_path


def _wait_running(state: Path, job, package: str | None, timeout_s=90.0):
    from puffpai_core.cli import _call
    key = state / "settings" / "terminal.json"
    end = time.monotonic() + timeout_s
    while time.monotonic() < end and job.proc.poll() is None:
        if key.is_file():
            try:
                info = json.loads(key.read_text(encoding="utf-8"))
                if package is None:
                    if _call(info, "GET", "/v1/health")[0] == 200:
                        return info
                else:
                    status, row = _call(info, "GET", f"/v1/registry/{package}")
                    if status == 200 and row.get("status") == "running":
                        return info
            except (OSError, ValueError):
                pass
        time.sleep(0.5)
    raise AssertionError("the Core did not get ready in time")


def _job_empty(job, timeout_s=20.0) -> list[int]:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end and job.pids():
        time.sleep(0.25)
    return job.pids()


def _cli(env, *args, timeout=120):
    return subprocess.run([sys.executable, str(REPO / "tests" / "console_core.py"), *args], env=env,
                          capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


@windows_only
def test_shutdown_stops_the_core_and_every_app_and_a_second_one_says_nothing_runs(state, monkeypatch, tmp_path,
                                                                                    drawbridge):
    import console_kit
    _bot_with_a_running_package(monkeypatch)
    env = _env(PUFFPAI_STATE=str(state))
    job, log_path = _start_core(env, tmp_path / "core")
    try:
        _wait_running(state, job, "hello-tool")
        before = job.pids()
        assert len(before) >= 2, before                    # the Core and hello-tool (its own job, inside ours)
        got = _cli(env, "shutdown")
        assert got.returncode == 0, got.stdout + got.stderr
        assert got.stdout.splitlines()[-1] == "BuildBot stopped.", got.stdout
        assert _job_empty(job) == [], "something the Core started is still running"
        assert job.proc.wait(10) == 0
        out = log_path.read_text(encoding="utf-8", errors="replace")
        assert out.rstrip().endswith("BuildBot stopped.") and "Traceback" not in out, out[-2000:]
        assert not (state / "settings" / "terminal.json").exists()
        assert json.loads((state / "settings" / "steward.json").read_text(encoding="utf-8"))["was_running"] == \
            ["hello-tool"]                                 # a clean stop: it starts again next time
        again = _cli(env, "shutdown")
        assert again.returncode == 1 and again.stdout.strip() == "BuildBot is not running, so there is nothing to stop."
    finally:
        console_kit._end(job)                              # noqa: SLF001 - our own job only


# -- Stop BuildBot.cmd, as install.ps1 writes it ------------------------------------------------------------
WRITE_LAUNCHERS = r'''
$ErrorActionPreference = "Stop"
$text = Get-Content -LiteralPath $args[0] -Raw
$m = [regex]::Match($text, '(?ms)^\$StopLauncher = .*?^Set-Content -LiteralPath \$StopLauncher[^\r\n]*')
if (-not $m.Success) { throw "install.ps1 has no Stop launcher block" }
$RepoDir = $args[1]
$Name = $args[2]
Invoke-Expression $m.Value
'''


def _stop_launcher_folder(tmp_path):
    """A clone's folder: ``Stop BuildBot.cmd`` written by install.ps1's own block, and a real console
    ``.venv\\Scripts\\puffpai.exe`` running ``console_core`` on this worktree's Python."""
    from pip._vendor.distlib.scripts import ScriptMaker
    repo = tmp_path / "Build Bot"
    scripts = repo / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    maker = ScriptMaker(None, str(scripts))
    maker.executable, maker.variants = sys.executable, {""}
    maker.make("puffpai = console_core:main")
    ps1 = tmp_path / "write_launchers.ps1"
    ps1.write_text(WRITE_LAUNCHERS, encoding="utf-8")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(ps1), str(REPO / "install.ps1"), str(repo), "testbot"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    return repo, repo / "Stop BuildBot.cmd"


@windows_only
def test_stop_launcher_text():
    text = (REPO / "install.ps1").read_text(encoding="ascii")
    block = text[text.index("$StopLauncher = "):text.index("Set-Content -LiteralPath $StopLauncher")]
    assert '""%~dp0.venv\\Scripts\\puffpai.exe"" shutdown' in block
    assert '"if not ""%BUILDBOT_EXIT%""==""0"" pause"' in block            # compared as text: negatives pause
    assert '"setlocal"' in block and '"set ""PUFFPAI_BOT=$Name"""' in block
    assert "To stop it from any window, double-click Stop BuildBot.cmd there." in text


@windows_only
def test_stop_buildbot_cmd_stops_a_running_bot_then_pauses_when_nothing_runs(tmp_path):
    import console_kit
    repo, stop_cmd = _stop_launcher_folder(tmp_path)
    text = stop_cmd.read_text(encoding="ascii")
    assert "testbot" in text and "shutdown" in text
    lad = tmp_path / "lad"
    env = _env(LOCALAPPDATA=str(lad), PUFFPAI_BOT="testbot")       # the bot the launcher names
    job, log_path = _start_core(env, tmp_path / "core")
    try:
        _wait_running(lad / "PuffPAI" / "testbot", job, None)
        cmd = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
        got = console_kit.run([cmd, "/c", str(stop_cmd)], "none", env, "BuildBot stopped.", tmp_path / "stop1",
                              screen=True, cwd=str(repo), settle_s=1.0, wait_s=20.0)
        screen = got["output"]
        assert got["exit"] == 0, (got["exit"], screen)
        assert "BuildBot stopped." in screen and "Press any key" not in screen, screen
        assert _job_empty(job) == [], "the Core is still running"
        got = console_kit.run([cmd, "/c", str(stop_cmd)], "none", env, "Press any key", tmp_path / "stop2",
                              screen=True, cwd=str(repo), settle_s=1.0, wait_s=3.0)
        screen = got["output"]
        assert got["ready"] and got["exit"] is None, screen          # waiting on her key: she can read why
        assert "BuildBot is not running, so there is nothing to stop." in screen, screen
    finally:
        console_kit._end(job)                              # noqa: SLF001 - our own job only
