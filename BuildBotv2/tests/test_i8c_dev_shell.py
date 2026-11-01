"""Lane i8c: the Developer page's command line (``/v1/dev/shell``), a full shell with no Yes per command.

The shells here run only harmless commands (echo, a sleeper this test starts with its own Python) in the
test's own folder. Every shell is ended through its Job Object; nothing is stopped by name, tree or PID walk.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

from puffpai_core import devshell
from puffpai_core.api import app as app_module
from puffpai_core.api import create_app
from puffpai_core.api.groups import dev
from puffpai_core.steward import jobs

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the Developer command line is Windows only")

PY = sys.executable                     # this worktree's .venv Python: the sleeper
SLEEPER = f'& "{PY}" -c "import time; time.sleep(120)"'


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def app(state, home):
    a = create_app(state)
    mgr = dev.shells(a)
    mgr.home = home
    mgr._reaper_on = False
    yield a
    dev.close_all(a, "test_end")


@pytest.fixture
def core(app):
    return app.extensions["puffpai"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def device(core):
    return bearer(core.tokens.issue_device("Sam's laptop"))


def _open(client, device, shell="powershell"):
    r = client.post("/v1/dev/shell", json={"shell": shell}, headers=device)
    assert r.status_code == 201, r.json
    return r.json


def _idle(sh, wait_s=20.0):
    end = time.monotonic() + wait_s
    while sh.busy and time.monotonic() < end:
        time.sleep(0.05)
    assert not sh.busy, "the shell did not finish the command"


def _send(client, device, sh_id, text):
    r = client.post(f"/v1/dev/shell/{sh_id}/input", json={"text": text}, headers=device)
    assert r.status_code == 200, r.json
    return r.json


def _events(client, device, sh_id, after=0, timeout=0):
    r = client.get(f"/v1/dev/shell/{sh_id}/events?after={after}&timeout={timeout}", headers=device)
    assert r.status_code == 200, r.get_data(as_text=True)
    out = []
    for frame in r.get_data(as_text=True).split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def _text(events, stream="out"):
    return "".join(e.get("text", "") for e in events if e.get("stream") == stream)


def _extra_pids(sh, wait_s=20.0):
    """The processes in the shell's job besides the shell and its console host (the sleeper)."""
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        extra = [p for p in sh.job.pids() if p not in sh._base]
        if extra:
            return extra
        time.sleep(0.1)
    raise AssertionError("the sleeper never started in the shell's job")


def _audit_lines(state):
    path = state / "logs" / "audit.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# -- the shell works ---------------------------------------------------------------------------------

@pytest.mark.parametrize("shell", ["powershell", "cmd"])
def test_echo_round_trip(client, device, app, home, shell):
    got = _open(client, device, shell)
    assert set(got) == {"id", "cwd"} and os.path.normcase(got["cwd"]) == os.path.normcase(str(home))
    sh = dev.shells(app).get(got["id"])
    assert _send(client, device, got["id"], "echo i8c-round-trip")["kind"] == "line"
    _idle(sh)
    events = _events(client, device, got["id"])
    assert "i8c-round-trip" in _text(events)
    assert all(set(e) == {"t", "stream", "text"} and e["stream"] in ("out", "err") for e in events)
    assert "PUFFDONE" not in _text(events)                     # the done line never shows
    rows = client.get("/v1/dev/shell", headers=device).json
    assert [(r["id"], r["shell"], r["mine"]) for r in rows] == [(got["id"], shell, True)]
    assert isinstance(rows[0]["idle_s"], int) and rows[0]["started"]


def test_errors_come_on_the_err_stream_and_the_folder_carries(client, device, app, home):
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    (home / "sub").mkdir()
    _send(client, device, got["id"], "Write-Error i8c-boom")
    _idle(sh)
    _send(client, device, got["id"], "cd sub")
    _idle(sh)
    assert os.path.normcase(sh.cwd) == os.path.normcase(str(home / "sub"))
    assert "i8c-boom" in _text(_events(client, device, got["id"]), "err")


def test_exit_sends_the_exit_event_and_ends_the_stream(client, device, app):
    got = _open(client, device)
    _send(client, device, got["id"], "exit 7")
    events = _events(client, device, got["id"], timeout=20)
    assert events[-1] == {"exit": 7}
    assert client.get("/v1/dev/shell", headers=device).json == []
    lines = [x for x in _audit_lines(app.extensions["puffpai"].state) if x.get("shell_id") == got["id"]]
    assert [x["action"] for x in lines][-1] == "dev_shell_closed" and lines[-1]["why"] == "exited"


def test_a_huge_line_with_the_marker_byte_is_capped_and_says_so(client, device, app):
    """The verifier's repro: a million characters after a \\x1e, with no newline until the end."""
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    _send(client, device, got["id"], "[Console]::Out.Write([string][char]30 + ('q' * 1000000)); ''")
    _idle(sh, wait_s=60)
    assert sh._kept <= devshell.KEEP_CHARS
    assert sum(len(e.get("text") or "") for _s, e in sh.events) == sh._kept
    events = _events(client, device, got["id"])
    assert _text(events).startswith(devshell.CUT)
    _send(client, device, got["id"], "echo i8c-after-the-flood")
    _idle(sh)
    assert "i8c-after-the-flood" in _text(_events(client, device, got["id"]))
    assert sh._kept <= devshell.KEEP_CHARS


def test_the_cap_cuts_old_events_and_inside_one_huge_event(app):
    sh = devshell.Shell(dev.shells(app), "powershell", "o", "d")       # no process: the buffer only
    for _ in range(40):
        sh._push({"t": "t", "stream": "out", "text": "a" * 10_000})
    assert sh._kept <= devshell.KEEP_CHARS and sh.events[0][1]["text"].startswith(devshell.CUT)
    sh._push({"t": "t", "stream": "err", "text": "b" * 1_000_000 + "END"})
    assert len(sh.events) == 1 and sh._kept <= devshell.KEEP_CHARS
    text = sh.events[0][1]["text"]
    assert text.startswith(devshell.CUT) and text.endswith("END") and sh.events[0][1]["stream"] == "err"
    sh._push({"exit": 0})
    assert sh.events[-1][1] == {"exit": 0} and sh._kept <= devshell.KEEP_CHARS


# -- Ctrl+C ------------------------------------------------------------------------------------------

def test_ctrl_c_ends_the_running_command_and_keeps_the_shell(client, device, app):
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    _send(client, device, got["id"], "$i8c = 'kept'")
    _idle(sh)
    shell_pid = sh.job.pid
    _send(client, device, got["id"], SLEEPER)
    sleeper = _extra_pids(sh)
    r = client.post(f"/v1/dev/shell/{got['id']}/input", json={"signal": "ctrl_c"}, headers=device)
    assert r.status_code == 200 and r.json["result"] == "stopped", r.json
    _idle(sh)
    assert not any(jobs.pid_alive(p) and p in sh.job.pids() for p in sleeper)
    _send(client, device, got["id"], "echo \"still-$i8c\"")
    _idle(sh)
    assert sh.job.pid == shell_pid and "still-kept" in _text(_events(client, device, got["id"]))


def test_ctrl_c_on_a_busy_shell_starts_it_again(client, device, app, home):
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    old = sh.job
    _send(client, device, got["id"], "Start-Sleep -Seconds 120")       # inside PowerShell itself
    time.sleep(0.5)
    r = client.post(f"/v1/dev/shell/{got['id']}/input", json={"signal": "ctrl_c"}, headers=device)
    assert r.json["result"] == "restarted"
    assert old.active() == 0 and sh.job is not old
    sh.ready.wait(20)
    _send(client, device, got["id"], "echo i8c-again")
    _idle(sh)
    out = _text(_events(client, device, got["id"]))
    assert "started again" in out and "i8c-again" in out
    assert {"exit": 1} not in _events(client, device, got["id"])       # the restart is not an exit


# -- DELETE ends everything the shell started --------------------------------------------------------

def test_delete_ends_the_job_and_every_child(client, device, app):
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    _send(client, device, got["id"], SLEEPER)
    sleeper = _extra_pids(sh)
    job = sh.job
    assert len(job.pids()) >= 3                                          # shell, console host, sleeper
    r = client.delete(f"/v1/dev/shell/{got['id']}", headers=device)
    assert r.status_code == 200 and r.json == {"ok": True}
    assert sh.job_empty is True                                          # the job held nothing at the end
    assert not any(jobs.pid_alive(p) for p in sleeper + [job.pid])
    assert sh.events[-1][1] == {"exit": 1}
    assert client.get("/v1/dev/shell", headers=device).json == []
    assert client.delete(f"/v1/dev/shell/{got['id']}", headers=device).status_code == 404


def test_core_stop_closes_every_shell(client, device, app):
    a, b = _open(client, device)["id"], _open(client, device, "cmd")["id"]
    shs = [dev.shells(app).get(a), dev.shells(app).get(b)]
    app_module.end_streams(app)                                          # what serve's stop runs first
    assert all(not s.open and s.job_empty for s in shs)
    assert client.get("/v1/dev/shell", headers=device).json == []


def test_at_most_two_open(client, device):
    _open(client, device)
    _open(client, device, "cmd")
    r = client.post("/v1/dev/shell", json={}, headers=device)
    assert r.status_code == 429 and r.json["code"] == "too_many"


# -- idle timeout ------------------------------------------------------------------------------------

def test_idle_timeout_on_an_injected_clock(client, device, app, core):
    now = [1000.0]
    mgr = dev.shells(app)
    mgr.clock = lambda: now[0]
    got = _open(client, device)
    sh = mgr.get(got["id"])
    now[0] += 29 * 60
    assert mgr.reap() == 0 and sh.open
    _send(client, device, got["id"], "echo i8c-keepalive")              # input resets the clock
    _idle(sh)
    now[0] += 29 * 60
    assert mgr.reap() == 0 and sh.open
    now[0] += 61
    assert mgr.reap() == 1
    assert not sh.open and sh.job_empty
    closed = [x for x in _audit_lines(core.state) if x.get("action") == "dev_shell_closed"]
    assert closed and closed[-1]["why"] == "idle"
    assert client.get("/v1/dev/shell", headers=device).json == []


# -- who may call ------------------------------------------------------------------------------------

def test_refused_without_an_owner_at_a_paired_device(client, core, app):
    package = bearer(core.tokens.issue_package_token("freshtable"))
    toolsmith = bearer(core.tokens.issue_toolsmith("b-i8c"))
    api_key = bearer(core.tokens.issue_owner_key())
    core.terminal_label = f"{app_module.TERMINAL_LABEL} (run i8c)"
    terminal = bearer(core.tokens.issue_device(core.terminal_label))
    for who, want in ((package, 403), (toolsmith, 403), (api_key, 403), ({}, 401), (terminal, 403)):
        assert client.post("/v1/dev/shell", json={}, headers=who).status_code == want, who
        assert client.get("/v1/dev/shell", headers=who).status_code == want, who
        assert client.post("/v1/dev/shell/x/input", json={"text": "echo"}, headers=who).status_code == want
        assert client.get("/v1/dev/shell/x/events", headers=who).status_code == want
        assert client.delete("/v1/dev/shell/x", headers=who).status_code == want
    assert dev.shells(app).list() == []


def test_a_shell_belongs_to_the_device_that_opened_it(client, device, core, app):
    got = _open(client, device)
    other = bearer(core.tokens.issue_device("Sam's phone"))
    assert client.post(f"/v1/dev/shell/{got['id']}/input", json={"text": "echo"}, headers=other).status_code == 404
    assert client.get(f"/v1/dev/shell/{got['id']}/events?timeout=0", headers=other).status_code == 404
    rows = client.get("/v1/dev/shell", headers=other).json
    assert [(r["id"], r["mine"]) for r in rows] == [(got["id"], False)]
    assert client.delete(f"/v1/dev/shell/{got['id']}", headers=other).status_code == 200   # closing only narrows


def test_safe_mode_closes_every_shell_and_refuses_new_ones(client, device, core, app):
    app_module.wire_core(app)                    # Caretaker's safe steps, as at a real Core start
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    result = core.caretaker.safe_mode("i8c test")
    assert "the command lines are closed" in result["done"]
    assert not sh.open and sh.job_empty
    r = client.post("/v1/dev/shell", json={}, headers=device)
    assert r.status_code == 409 and r.json["code"] == "safe_mode"
    assert client.get("/v1/dev/shell", headers=device).status_code == 200


def test_the_safe_mode_fallback_closes_shells_too(client, device, app):
    """With no working Caretaker, Toolsmith's ports run the same shared steps (``safe_mode_steps``)."""
    from puffpai_core.caretaker.caretaker import safe_mode_steps
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    core = app.extensions["puffpai"]
    steps = safe_mode_steps(zone3=lambda: None, state=core.state, bus=core.bus, toolsmith=lambda: None)
    assert [f() for f in steps if f.__name__ == "close_shells"] == ["the command lines are closed"]
    assert not sh.open and sh.job_empty


def test_bad_input_is_refused(client, device):
    got = _open(client, device)
    for body in ({"text": "echo a\necho b"}, {"text": "x" * (devshell.MAX_LINE + 1)}, {"text": 5},
                 {"signal": "ctrl_z"}, {}):
        assert client.post(f"/v1/dev/shell/{got['id']}/input", json=body, headers=device).status_code == 400
    assert client.post("/v1/dev/shell", json={"shell": "bash"}, headers=device).status_code == 400


# -- the environment ---------------------------------------------------------------------------------

def test_no_puffpai_names_or_core_tokens_in_the_shell_environment(client, device, app, monkeypatch):
    monkeypatch.setenv("PUFFPAI_I8C_PROBE", "x")
    monkeypatch.setenv("I8C_HOLDS_A_TOKEN", "ppd_" + "A" * 43)
    monkeypatch.setenv("I8C_PLAIN", "kept")
    app.extensions["puffpai.started"] = "ppd_terminalkeyfortheshelltest_xyz"
    monkeypatch.setenv("I8C_HOLDS_THE_KEY", "a ppd_terminalkeyfortheshelltest_xyz b")
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    _send(client, device, got["id"], "Get-ChildItem env: | ForEach-Object { 'ENV:' + $_.Name }")
    _idle(sh)
    names = {x[4:].strip().upper() for x in _text(_events(client, device, got["id"])).splitlines()
             if x.startswith("ENV:")}
    assert "I8C_PLAIN" in names and "PATH" in names                     # her normal environment otherwise
    assert not [n for n in names if n.startswith("PUFFPAI")]
    assert "I8C_HOLDS_A_TOKEN" not in names and "I8C_HOLDS_THE_KEY" not in names


def test_shell_env_unit():
    env = devshell.shell_env({"PUFFPAI_STATE": "s", "puffpai_x": "y", "A": "b", "T": "pts_" + "z" * 30,
                              "K": "hold mykey", "PATH": "p"}, ("mykey",))
    assert env == {"A": "b", "PATH": "p"}


# -- audit -------------------------------------------------------------------------------------------

def test_audit_lines_hold_no_command_text_and_no_output(client, device, app, core):
    got = _open(client, device)
    sh = dev.shells(app).get(got["id"])
    _send(client, device, got["id"], "echo i8c-words-never-logged")
    _idle(sh)
    _send(client, device, got["id"], SLEEPER)
    _extra_pids(sh)
    assert _send(client, device, got["id"], "i8c-typed-answer")["kind"] == "typed"
    client.post(f"/v1/dev/shell/{got['id']}/input", json={"signal": "ctrl_c"}, headers=device)
    client.delete(f"/v1/dev/shell/{got['id']}", headers=device)
    lines = [x for x in _audit_lines(core.state) if x.get("shell_id") == got["id"]]
    actions = [x["action"] for x in lines]
    assert actions[0] == "dev_shell_opened" and actions[-1] == "dev_shell_closed"
    said = [x for x in lines if x["action"] == "dev_shell_line"]
    assert said[0]["length"] == len("echo i8c-words-never-logged") and said[0]["first_word"] == "echo"
    typed = [x for x in lines if x["action"] == "dev_shell_typed"]
    assert typed and set(typed[0]) - {"t", "n", "prev", "h"} == {"actor", "action", "shell_id", "shell",
                                                                    "device", "length"}
    # No command text and no output in any file the Core wrote.
    for p in core.state.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            for word in (b"i8c-words-never-logged", b"never-logged", b"i8c-typed-answer", b"time.sleep"):
                assert word not in data, (p, word)


@pytest.mark.parametrize("line, want", [
    ("echo hi", "echo"), ("git status", "git"), ("$env:API_KEY = 'abc123'", "$env:API_KEY"),
    ("$env:API_KEY='abc123'", devshell.HIDDEN),               # the value is in the first word: all of it hidden
    ("sk-ant-api03-" + "a1" * 30, devshell.HIDDEN), ("ghp_" + "A1b2" * 9, devshell.HIDDEN),
    ("ppd_" + "x" * 43, devshell.HIDDEN), ("aB3dE5gH7jK9mN1pQ3sT5vW7", devshell.HIDDEN),
    ("C:\\tools\\my-long-program-name-1234.exe /x", "C:\\tools\\my-long-program-name-12"), ("", ""),
])
def test_first_word(line, want):
    assert devshell.first_word(line) == want
