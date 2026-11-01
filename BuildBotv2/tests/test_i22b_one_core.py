"""One Core per bot, and the terminal key (lane I22b, D91).

Her test6 bot (2026-10-30): the setup Core from install.ps1 still ran when Start BuildBot.cmd started
``serve``. Waitress sets SO_REUSEADDR, so on Windows the second Core bound 8420 and 8422 beside the first
(only the Bridge proxy, bound without it, was refused). The second Core's start revoked every earlier
terminal key and wrote its own to terminal.json; the CLI's calls to 8420 reached the FIRST Core, which
honours only its own run's key, so ``puffpai shutdown`` was refused with the API's sign-in text.

Every port here is a free one the OS picked (never 8420-8422, conftest guard).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading

import pytest

from conftest import REPO
from puffpai_core import corelock
from puffpai_core.api import create_app
from puffpai_core.api.app import make_servers, start_core, stop_core
from puffpai_core.api.auth import NO_CREDENTIAL

windows_only = pytest.mark.skipif(os.name != "nt", reason="Job Objects and Windows port sharing")
OWNER_WORDS_NEVER = ("Bearer", "token", "API key")


def _free_ports(n):
    socks = [corelock.exclusive_socket("127.0.0.1", 0) for _ in range(n)]
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    assert not set(ports) & {8420, 8421, 8422}
    return ports


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _close(servers):
    for s in servers:
        s.close()


# -- the root cause, in one process: two Cores on one bot folder --------------------------------------------
def test_root_cause_two_cores_on_one_bot_make_the_first_refuse_the_key_the_cli_reads(state):
    """What her two Cores did: the second start revoked the first's key and wrote its own to terminal.json; a
    call that reaches the first Core with that key is refused as "a terminal key from another Core run"."""
    p1, p2 = _free_ports(2)
    first = create_app(state, port=p1).extensions["puffpai"]
    first.proxy_port = 0
    old = start_core(first)
    second = None
    try:
        assert first.app.test_client().get("/v1/consent", headers=_bearer(old)).status_code == 200
        second = create_app(state, port=p2).extensions["puffpai"]
        second.proxy_port = 0
        new = start_core(second)
        info = json.loads((state / "settings" / "terminal.json").read_text(encoding="utf-8"))
        assert info["token"] == new and info["port"] == p2
        r = first.app.test_client().post("/v1/shutdown", json={}, headers=_bearer(info["token"]))
        assert r.status_code == 401 and r.json["error"] == NO_CREDENTIAL      # her Stop BuildBot.cmd's refusal
        assert first.app.test_client().get("/v1/consent", headers=_bearer(old)).status_code == 401
        assert second.app.test_client().get("/v1/consent", headers=_bearer(new)).status_code == 200
    finally:
        if second is not None:
            stop_core(second, second.app.extensions.get("puffpai.started"))
        stop_core(first, None)


def test_the_core_ports_can_not_be_shared_by_a_second_core_or_any_program(state):
    """The fix at the sockets: a port a Core listens on refuses a second Core and a plain waitress server
    (SO_REUSEADDR), which on Windows used to bind beside it."""
    from waitress import create_server
    app = create_app(state, port=0, pages_port=0)
    servers = make_servers(app, 0, 0)
    try:
        core_port, pages_port = (int(s.effective_port) for s in servers)
        for port in (core_port, pages_port):
            with pytest.raises(OSError):
                make_servers(create_app(state, port=port, pages_port=0), port, 0)
            with pytest.raises(OSError):
                create_server(lambda e, s: [b""], host="127.0.0.1", port=port)
    finally:
        _close(servers)


# -- the bot's lock -----------------------------------------------------------------------------------------
def test_the_lock_says_who_holds_it_and_lets_go_on_release(state):
    held = corelock.acquire(state, port=18430, pages_port=18432)
    try:
        with pytest.raises(corelock.AlreadyRunning) as got:
            corelock.acquire(state, port=1)
        assert got.value.info["pid"] == os.getpid() and got.value.info["port"] == 18430
    finally:
        held.release()
    held.release()                                         # twice is fine
    with corelock.acquire(state, port=2) as again:
        assert again.info["port"] == 2


def test_the_lock_of_a_core_that_was_killed_is_taken_by_the_next_start(state, tmp_path):
    """No stale lock: the OS lets go when the holder ends, even on a hard kill (a crash, Task Manager)."""
    script = ("import sys, time; from puffpai_core import corelock; "
              "corelock.acquire(sys.argv[1], port=18440); print('held', flush=True); time.sleep(120)")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PUFFPAI_")}
    env["PYTHONPATH"] = str(REPO / "core")
    python = getattr(sys, "_base_executable", sys.executable)       # not the venv's launcher: the holder itself
    proc = subprocess.Popen([python, "-c", script, str(state)], stdout=subprocess.PIPE, text=True, env=env)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(corelock.AlreadyRunning) as got:
            corelock.acquire(state, port=1)
        assert got.value.info["pid"] == proc.pid and got.value.info["port"] == 18440
    finally:
        proc.kill()                                        # our own child, by its handle
        proc.wait(10)
        proc.stdout.close()
    with corelock.acquire(state, port=3, wait_s=corelock.START_WAIT_S) as taken:   # Windows may take a moment
        assert taken.info["pid"] == os.getpid()


@pytest.mark.parametrize("launcher_there", [False, True])
def test_a_second_serve_for_the_same_bot_says_so_plainly_and_starts_nothing(state, capsys, monkeypatch, tmp_path,
                                                                           launcher_there):
    from puffpai_core.cli import main
    from puffpai_core.wizard import terminal
    monkeypatch.setattr(terminal, "launcher", lambda: tmp_path / "Start BuildBot.cmd")
    if launcher_there:
        for name in ("Stop BuildBot.cmd", "Sign in a browser.cmd"):
            (tmp_path / name).write_text("@echo off\n", encoding="ascii")
    p1, p2 = _free_ports(2)
    with corelock.acquire(state, port=18431, pages_port=18433):
        assert main(["serve", "--port", str(p1), "--pages-port", str(p2), "--proxy-port", "0"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == ("BuildBot is already running, in another window. This window did not start "
                                   "a second one.")
    assert "http://puffpai.localhost:18431/" in out                                 # the running one's port
    assert (f"double-click Stop BuildBot.cmd in {tmp_path}" in out) is launcher_there
    assert (f"double-click Sign in a browser.cmd in {tmp_path}" in out) is launcher_there
    assert ("pair --running" in out) is not launcher_there and "run puffpai" not in out
    assert "Traceback" not in out and "PuffPAI Desktop at" not in out
    assert not (state / "tokens.db").exists() and not (state / "settings" / "terminal.json").exists()


def test_setup_with_a_core_already_running_says_so_and_starts_nothing(state, capsys):
    from puffpai_core.wizard.terminal import main
    p1, p2 = _free_ports(2)
    with corelock.acquire(state, port=18434):
        assert main(["--port", str(p1), "--pages-port", str(p2), "--proxy-port", "0"]) == 1
    out = capsys.readouterr().out
    assert "BuildBot is already running" in out and "http://puffpai.localhost:18434/" in out
    assert not (state / "tokens.db").exists()


def test_a_port_in_use_says_so_without_a_traceback(state, capsys):
    from puffpai_core.cli import main
    busy = corelock.exclusive_socket("127.0.0.1", 0)
    busy.listen(1)
    try:
        port = busy.getsockname()[1]
        (p2,) = _free_ports(1)
        assert main(["serve", "--port", str(port), "--pages-port", str(p2), "--proxy-port", "0"]) == 1
    finally:
        busy.close()
    out = capsys.readouterr().out
    assert f"BuildBot can't start: port {port} or {p2} is in use by another program" in out
    with corelock.acquire(state, port=1):                 # the failed start let the lock go
        pass


# -- the Bridge proxy port --------------------------------------------------------------------------------
def test_a_proxy_port_held_by_another_process_is_said_and_no_app_uses_it(state):
    """A Core that can't get its proxy port says so (a card), and its apps never get that port: their proxy
    address is one where nothing listens, and a Docker app does not start."""
    from puffpai_core import wiring
    held = corelock.exclusive_socket("127.0.0.1", 0)
    held.listen(1)
    port = held.getsockname()[1]
    core = create_app(state, port=_free_ports(1)[0]).extensions["puffpai"]
    core.proxy_port = port
    terminal = start_core(core)
    try:
        assert core.zone3.proxy is None and core.zone3.cards[-1]["kind"] == "proxy_down"
        assert f"could not start on port {port}" in core.notices.list()[0]["text"]
        env = core.steward.env("hello-tool", {}, 18500, "tok", {})
        assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == wiring.NO_PROXY_URL and str(port) not in json.dumps(env)
        with pytest.raises(RuntimeError):
            wiring.docker_wall_kw(core.zone3)
    finally:
        stop_core(core, terminal)
        held.close()


def test_the_proxy_port_can_not_be_taken_over_by_an_app(state):
    from waitress import create_server
    from puffpai_core.api.app import wire_core
    core = wire_core(create_app(state))
    port = core.zone3.start_proxy(0)
    try:
        with pytest.raises(OSError):
            create_server(lambda e, s: [b""], host="127.0.0.1", port=port)
    finally:
        core.zone3.stop()


# -- what the owner reads when the key is refused ------------------------------------------------------------
@pytest.fixture
def refusing_core(state):
    """A stand-in for this bot's Core once it stops taking the terminal key (a key that stops working between
    two calls): /v1/health and /v1/core (this bot) answer, everything else is 401 with the API's own text."""
    from waitress import create_server
    me = json.dumps({"bot_id": corelock.bot_id(state), "pid": 1}).encode()

    def app(environ, start_response):
        if environ.get("PATH_INFO") == "/v1/health":
            body, status = b'{"ok": true}', "200 OK"
        elif environ.get("PATH_INFO") == "/v1/core":
            body, status = me, "200 OK"
        else:
            body, status = json.dumps({"error": NO_CREDENTIAL}).encode(), "401 UNAUTHORIZED"
        start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]
    server = create_server(app, sockets=[corelock.exclusive_socket("127.0.0.1", 0)], threads=2)
    port = int(server.effective_port)
    from puffpai_core.api.app import run_servers
    stop = threading.Event()                           # the Core's own stop: each listener closes in its own loop
    thread = threading.Thread(target=run_servers, args=([server],), kwargs={"stop": stop}, daemon=True)
    thread.start()
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "terminal.json").write_text(
        json.dumps({"port": port, "pid": 1, "token": "ppd_not-this-cores-key"}), encoding="utf-8")
    yield port
    stop.set()
    thread.join(10)


def _plain(out):
    for word in OWNER_WORDS_NEVER:
        assert word not in out, (word, out)


@pytest.mark.parametrize("argv", [["shutdown"], ["start", "a-recipe-box"], ["stop", "an-invoice-helper"],
                                  ["bridge"], ["remove", "a-bird-log"]])
def test_every_cli_verb_says_the_plain_line_when_the_key_is_refused(refusing_core, capsys, argv):
    from puffpai_core.cli import KEY_REFUSED, main
    assert main(argv) == 1
    out = capsys.readouterr().out
    assert KEY_REFUSED in out, out
    _plain(out)
    if argv == ["shutdown"]:
        assert out.splitlines() == ["BuildBot did not stop:", f"  - {KEY_REFUSED}"]


@pytest.mark.parametrize("verb", ["cmd_turn", "cmd_plan", "cmd_vault"])
def test_the_terminal_chat_says_the_plain_line_when_the_key_is_refused(refusing_core, verb):
    from puffpai_core import termchat
    from puffpai_core.cli import KEY_REFUSED
    out = io.StringIO()
    assert termchat.run_verb(verb, "", out=out) == 1
    assert out.getvalue().strip() == KEY_REFUSED
    out = io.StringIO()
    assert termchat.main(out=out) == 1
    assert out.getvalue().strip() == KEY_REFUSED
    api = termchat.Api({"port": refusing_core, "token": "ppd_not-this-cores-key"})
    assert api.call("GET", "/v1/checklist") == (401, {"error": KEY_REFUSED})
    status, body, _events = api.stream("/v1/chat/stream", {"text": "plan my week of dinners"})
    assert status == 401 and body == {"error": KEY_REFUSED}


# -- a stale terminal.json and another bot's Core on that port (lane cli-default's Start asks _terminal first) ---
@pytest.fixture
def other_bot(tmp_path):
    """Another bot's real Core API (its own folder and token store), listening on a free port."""
    from puffpai_core.api.app import run_servers
    app = create_app(tmp_path / "another-bot", port=0, pages_port=0)
    servers = make_servers(app, 0, 0)
    stop = threading.Event()
    thread = threading.Thread(target=run_servers, args=(servers,), kwargs={"stop": stop}, daemon=True)
    thread.start()
    yield app.extensions["puffpai"], int(servers[0].effective_port)
    stop.set()
    thread.join(10)


def _stale(state, port, token):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "terminal.json").write_text(json.dumps({"port": port, "pid": 1, "token": token}),
                                                      encoding="utf-8")


def test_a_stale_key_on_another_bots_core_is_not_this_bot_running(state, other_bot, capsys):
    """Another bot's Core refuses this bot's old key (its token store never had it): not running."""
    from puffpai_core import termchat
    from puffpai_core.api.auth import TokenStore
    from puffpai_core.cli import NOT_RUNNING, _terminal, main
    _other, port = other_bot
    _stale(state, port, TokenStore(state / "tokens.db").issue_device("this computer's terminal (run old)"))
    assert _terminal() is None
    assert main(["shutdown"]) == 1 and capsys.readouterr().out.strip() == NOT_RUNNING
    out = io.StringIO()
    assert termchat.main(out=out) == 1 and out.getvalue().startswith(termchat.NOT_RUNNING)


def test_a_key_another_bots_core_takes_is_still_not_this_bot(state, other_bot, capsys):
    """Even a key that Core takes (one of its own) is not this bot's Core: it names another bot."""
    from puffpai_core.cli import NOT_RUNNING, _call, _terminal, main
    other, port = other_bot
    key = other.tokens.issue_device("a program of the other bot")
    status, body = _call({"port": port, "token": key}, "GET", "/v1/core")
    assert status == 200 and body["bot_id"] == corelock.bot_id(other.state) != corelock.bot_id(state)
    _stale(state, port, key)
    assert _terminal() is None
    assert main(["shutdown"]) == 1 and capsys.readouterr().out.strip() == NOT_RUNNING
    assert other.app.extensions.get("puffpai.stop") is None or not other.app.extensions["puffpai.stop"].is_set()


def test_this_bots_own_core_counts_as_running(other_bot, monkeypatch):
    """The same check passes for the bot whose Core it is (here the "other" bot is this one)."""
    from puffpai_core.cli import _terminal
    other, port = other_bot
    monkeypatch.setenv("PUFFPAI_STATE", str(other.state))
    _stale(other.state, port, other.tokens.issue_device("a program of this bot"))
    assert _terminal() == {"port": port, "pid": 1, "token": json.loads(
        (other.state / "settings" / "terminal.json").read_text(encoding="utf-8"))["token"]}


def test_the_core_route_needs_a_key_and_says_only_the_bot_id(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    assert client.get("/v1/core").status_code == 401
    got = client.get("/v1/core", headers=_bearer(core.tokens.issue_device("a program")))
    assert got.status_code == 200 and set(got.json) == {"bot_id", "pid"}
    assert got.json["bot_id"] == corelock.bot_id(state) and str(state) not in got.get_data(as_text=True)


# -- her flow, with real processes: setup's Core runs, Start BuildBot.cmd starts serve, Stop stops it ------------
@windows_only
def test_her_flow_a_second_start_is_refused_and_the_terminal_key_stops_the_running_core(state, tmp_path):
    import console_kit
    from test_i7_shutdown import _cli, _env, _job_empty, _start_core, _wait_running
    env = _env(PUFFPAI_STATE=str(state))
    job, _log = _start_core(env, tmp_path / "core")
    try:
        info = _wait_running(state, job, None)
        p1, p2, p3 = _free_ports(3)
        second = _cli(env, "serve", "--port", str(p1), "--pages-port", str(p2), "--proxy-port", str(p3), timeout=60)
        assert second.returncode == 1, second.stdout + second.stderr
        assert "BuildBot is already running" in second.stdout
        assert f"http://puffpai.localhost:{info['port']}/" in second.stdout
        assert json.loads((state / "settings" / "terminal.json").read_text(encoding="utf-8")) == info  # untouched
        for verb in (["bridge"], ["turn"]):                                 # the CLI and the chat's verbs
            got = _cli(env, *verb)
            assert got.returncode == 0 and "did not accept" not in got.stdout, (verb, got.stdout + got.stderr)
        stop = _cli(env, "shutdown")
        assert stop.returncode == 0 and stop.stdout.splitlines()[-1] == "BuildBot stopped.", stop.stdout
        assert _job_empty(job) == []
        assert job.proc.wait(10) == 0
        with corelock.acquire(state, port=1):            # the stopped Core let the lock go
            pass
    finally:
        console_kit._end(job)                              # noqa: SLF001 - our own job only


# -- fix round: a REAL Core of this bot (holding its lock, as serve does) that refuses the terminal key --------
class _Wrote(Exception):
    """The offline path ran: it would have written beside the running Core."""


@pytest.fixture
def no_offline_writes(monkeypatch):
    import puffpai_core.api.app as app_mod

    def spy(*_a, **_k):
        raise _Wrote("wire_core ran with no Core reachable")
    monkeypatch.setattr(app_mod, "wire_core", spy)


@pytest.fixture
def refused_core(state):
    """This bot's real Core API on a free port, holding this bot's lock as ``serve`` does, with a terminal.json
    whose key it never issued (a revoked or old key)."""
    from puffpai_core.api.app import run_servers
    port, pages_port = _free_ports(2)
    lock = corelock.acquire(state, port=port, pages_port=pages_port)    # as serve does, before it binds
    app = create_app(state, port=port, pages_port=pages_port)
    servers = make_servers(app, port, pages_port)
    stop = threading.Event()
    thread = threading.Thread(target=run_servers, args=(servers,), kwargs={"stop": stop}, daemon=True)
    thread.start()
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "terminal.json").write_text(
        json.dumps({"port": port, "pid": os.getpid(), "token": "ppd_revoked-old-key"}), encoding="utf-8")
    yield app, port
    stop.set()
    thread.join(10)
    lock.release()


def _registry_ids(state):
    from puffpai_core.registry import Registry
    return [r["id"] for r in Registry(state / "registry.db").list()]


@pytest.mark.parametrize("argv", [["add", "freshtable"], ["remove", "a-bird-log"], ["update", "a-recipe-box"]])
def test_offline_writes_refuse_while_this_bots_core_runs_with_a_refused_key(refused_core, no_offline_writes,
                                                                             state, capsys, argv):
    from puffpai_core.cli import KEY_REFUSED, main
    before = _registry_ids(state)
    assert main(argv) == 1
    out = capsys.readouterr().out
    assert out.strip() == KEY_REFUSED, out
    assert _registry_ids(state) == before
    _plain(out)


@pytest.mark.parametrize("argv", [["start", "an-invoice-helper"], ["stop", "an-invoice-helper"], ["bridge"]])
def test_verbs_say_the_key_was_refused_never_not_running(refused_core, capsys, argv):
    from puffpai_core.cli import KEY_REFUSED, main
    assert main(argv) == 1
    assert capsys.readouterr().out.strip() == KEY_REFUSED


def test_shutdown_says_the_key_was_refused_and_stops_nothing(refused_core, capsys):
    from puffpai_core.api.groups.shutdown import STOP
    from puffpai_core.cli import KEY_REFUSED, main
    app, _port = refused_core
    assert main(["shutdown"]) == 1
    assert capsys.readouterr().out.splitlines() == ["BuildBot did not stop:", f"  - {KEY_REFUSED}"]
    assert STOP not in app.extensions or not app.extensions[STOP].is_set()


@pytest.mark.parametrize("verb", ["cmd_turn", "cmd_plan", "cmd_vault", None])
def test_the_chat_says_the_key_was_refused(refused_core, verb):
    from puffpai_core import termchat
    from puffpai_core.cli import KEY_REFUSED
    out = io.StringIO()
    got = termchat.main(out=out) if verb is None else termchat.run_verb(verb, "", out=out)
    assert got == 1 and out.getvalue().strip() == KEY_REFUSED


def test_pair_running_gives_a_link_that_works_on_the_running_core(refused_core, capsys):
    """The link needs no terminal key: the running Core reads the code from pairing.json. Its port comes
    from the bot's lock, and the real Core takes the code."""
    import urllib.request
    from puffpai_core.cli import main
    _app, port = refused_core
    assert main(["pair", "--running"]) == 0
    out = capsys.readouterr().out
    link = next(w for w in out.split() if w.startswith("http://"))
    assert link.startswith(f"http://puffpai.localhost:{port}/#pair=")
    code = link.split("#pair=", 1)[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/pair", method="POST",
                                 data=json.dumps({"code": code}).encode(),
                                 headers={"Host": f"puffpai.localhost:{port}", "Content-Type": "application/json",
                                          "Origin": f"http://puffpai.localhost:{port}", "X-PuffPAI": "1"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as resp:
        assert resp.status == 200


def test_while_this_bots_core_starts_the_verbs_say_so(refused_core, state, capsys):
    from puffpai_core.cli import STARTING, main
    (state / "settings" / "terminal.json").unlink()
    assert main(["bridge"]) == 1 and capsys.readouterr().out.strip() == STARTING


def test_an_offline_write_holds_the_lock_and_a_start_or_another_write_waits_its_turn(state, monkeypatch, capsys):
    """With nothing running, remove takes this bot's lock for as long as it runs: a Core start and an add
    then say it is busy, in plain words."""
    import puffpai_core.api.app as app_mod
    from puffpai_core.cli import main
    seen = {}

    def inside(*_a, **_k):
        seen["holder"] = corelock.holder(state)
        p1, p2 = _free_ports(2)
        seen["serve"] = main(["serve", "--port", str(p1), "--pages-port", str(p2), "--proxy-port", "0"])
        seen["add"] = main(["add", "freshtable"])
        raise _Wrote("stop here")
    monkeypatch.setattr(app_mod, "wire_core", inside)
    with pytest.raises(_Wrote):
        main(["remove", "a-bird-log"])
    out = capsys.readouterr().out
    assert seen["holder"]["kind"] == corelock.TOOL and seen["serve"] == 1 and seen["add"] == 1
    assert out.count(corelock.BUSY_LINE) == 2 and "already running" not in out
    with corelock.acquire(state, port=1):                 # the write let the lock go
        pass


def test_a_read_only_lock_file_is_a_plain_line(state, capsys):
    import stat
    from puffpai_core.cli import main
    state.mkdir(parents=True, exist_ok=True)
    lock = state / corelock.LOCK_NAME
    lock.write_text("{}", encoding="utf-8")
    os.chmod(lock, stat.S_IREAD)
    try:
        with pytest.raises(corelock.LockUnusable):
            corelock.acquire(state, port=1)
        p1, p2 = _free_ports(2)
        assert main(["serve", "--port", str(p1), "--pages-port", str(p2), "--proxy-port", "0"]) == 1
        out = capsys.readouterr().out
        assert f"BuildBot can't use its lock file {lock}" in out and "Traceback" not in out
        assert main(["add", "freshtable"]) == 1
        assert "can't use its lock file" in capsys.readouterr().out
    finally:
        os.chmod(lock, stat.S_IREAD | stat.S_IWRITE)


# -- the sign-in refusal's code (D91, D92) ------------------------------------------------------------------
def test_the_cores_sign_in_refusal_has_its_code_and_other_refusals_do_not(state):
    from puffpai_core.api.app import desktop_origin
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    r = client.get("/v1/consent")
    assert r.status_code == 401 and r.json == {"error": NO_CREDENTIAL, "code": "sign_in"}
    r = client.get("/v1/consent", headers=_bearer("ppd_not-a-key"))
    assert r.status_code == 401 and r.json["code"] == "sign_in"
    r = client.post("/v1/shutdown", json={}, headers=_bearer(core.tokens.issue_owner_key()))
    assert r.status_code == 403 and "code" not in r.json
    app2 = create_app(state, port=18450)
    r = app2.test_client().post("/v1/pair", json={"code": "AAAA-BBBB-CCCC"},
                                headers={"Host": f"puffpai.localhost:18450", "Origin": desktop_origin(18450),
                                         "X-PuffPAI": "1"})
    assert r.status_code == 401 and r.json["code"] == "pair_code" and "pairing code" in r.json["error"]
