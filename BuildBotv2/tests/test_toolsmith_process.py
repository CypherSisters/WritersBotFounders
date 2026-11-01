"""Toolsmith in its OWN process (09 section 3): the boundary, the token, and the real child once.

- The real child process: the Core serves its API on a free 127.0.0.1 port,
  starts ``python -I -m puffpai_core.zone3.toolsmith`` in a job object with
  its token on stdin, and the fixture app is planned, tried, installed and
  proven through the Toolsmith routes.
- Toolsmith's side imports no part of Drawbridge, the consent store, Archivist
  or the Core's own side, and starts no program (both checked).
- A Toolsmith token opens only Toolsmith's routes, for its one build.
- When Toolsmith's process dies mid-way, the Core ends what it left.
"""

import ast
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from test_toolsmith_build import make_world
from toolsmith_fakes import FIXTURES, World, make_venv
from puffpai_core.zone3.toolsmith_host import ToolsmithHost, process, service
from toolsmith_fakes import end_held

AGENT_DIR = Path(__file__).resolve().parents[1] / "core" / "puffpai_core" / "zone3" / "toolsmith"
#: Not on this list: ``zone3.text_rules``. It is fixed text patterns on the standard library alone, with no keys and no
#: state, and Toolsmith's own screens use it (``find.py``, ``shelves.py``, ``scout.py``). Having it changes nothing the
#: checker decides: Drawbridge still runs in the Core, called by the Core (09 section 3).
FORBIDDEN = ("puffpai_core.zone3.drawbridge", "puffpai_core.zone3.consent", "puffpai_core.zone3.service",
             "puffpai_core.zone1.archivist", "puffpai_core.zone1.vault",
             "puffpai_core.zone3.toolsmith_host", "puffpai_core.api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_toolsmith_side_imports_no_checker_no_keeper_no_core_side():
    """Run in a fresh interpreter: what importing Toolsmith's process pulls in."""
    code = ("import json, sys\n"
            "import puffpai_core.zone3.toolsmith.__main__\n"
            "from puffpai_core.zone3.toolsmith import agent, adopt, find, link, prove, tries, runbook\n"
            "print(json.dumps(sorted(sys.modules)))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert out.returncode == 0, out.stderr
    loaded = json.loads(out.stdout)
    bad = [m for m in loaded if m.startswith(FORBIDDEN) or "drawbridge" in m or "archivist" in m]
    assert bad == []
    assert "subprocess" not in loaded and "ctypes" not in loaded


def test_toolsmith_side_never_starts_a_program():
    """No module on Toolsmith's side imports or calls anything that starts a process."""
    banned_modules = {"subprocess", "multiprocessing", "ctypes", "pty", "_winapi", "_posixsubprocess"}
    banned_calls = {"system", "popen", "startfile", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
                    "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp",
                    "execvpe", "fork", "forkpty", "posix_spawn", "posix_spawnp", "create_subprocess_exec",
                    "create_subprocess_shell"}
    found = []
    for path in sorted(AGENT_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [f"{path.name}: import {a.name}" for a in node.names if a.name.split(".")[0] in banned_modules]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] in banned_modules or any(x in mod for x in ("drawbridge", "archivist", "consent",
                                                                                 "toolsmith_host")):
                    found.append(f"{path.name}: from {mod}")
                if node.level and any(a.name in ("toolsmith_host", "drawbridge", "consent", "service") for a in node.names):
                    found.append(f"{path.name}: from {'.' * node.level}{mod} import ...")
            elif isinstance(node, ast.Attribute) and node.attr in banned_calls:
                found.append(f"{path.name}:{node.lineno}: .{node.attr}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
                found.append(f"{path.name}:{node.lineno}: __import__")
    assert found == []


def test_a_toolsmith_token_opens_only_its_own_routes_for_its_own_build(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.fixture_repo = FIXTURES / "toolsmith" / "hello-page"
    a = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    tok_a = w.core.tokens.issue_toolsmith(a["id"])
    tok_b = w.core.tokens.issue_toolsmith(b["id"])
    c = w.app.test_client()
    ha, hb = {"Authorization": f"Bearer {tok_a}"}, {"Authorization": f"Bearer {tok_b}"}
    assert c.get("/v1/toolsmith/agent/build", headers=ha).json["build"]["id"] == a["id"]
    for path in ("/v1/toolsmith", "/v1/registry", "/v1/toolsmith/providers"):
        assert c.get(path, headers=ha).status_code == 403                   # no owner route
    assert c.put("/v1/toolsmith/model", headers=ha, json={"where": "local"}).status_code == 403
    assert c.get("/v1/health", headers=ha).status_code == 200               # public stays public
    opened = c.post("/v1/toolsmith/agent/tries", headers=ha,
                    json={"job_id": a["jobs"][0]["id"], "ref": "github:example/hello-page"}).json
    # Build B's token can't touch build A's try or job.
    assert c.post(f"/v1/toolsmith/agent/tries/{opened['try_id']}/download", headers=hb).status_code == 404
    assert c.post(f"/v1/toolsmith/agent/jobs/{a['jobs'][0]['id']}/status", headers=hb, json={}).status_code == 404
    # Other credentials can't call Toolsmith's routes.
    owner = {"Authorization": "Bearer " + w.core.tokens.issue_owner_key()}
    pkg = {"Authorization": "Bearer " + w.core.tokens.issue_package_token("freshtable")}
    assert c.get("/v1/toolsmith/agent/build", headers=owner).status_code == 403
    assert c.get("/v1/toolsmith/agent/build", headers=pkg).status_code == 403
    c.set_cookie("puffpai_device", tok_a)                                    # never by cookie
    assert c.get("/v1/toolsmith/agent/build").status_code == 401
    w.core.tokens.revoke_toolsmith(a["id"])
    assert c.get("/v1/toolsmith/agent/build", headers=ha).status_code == 401
    w.host.stop(timeout=10)


def test_when_toolsmith_dies_the_core_ends_what_it_left(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.fixture_repo = FIXTURES / "toolsmith" / "hello-page"
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = w.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    assert link.try_download(opened["try_id"])["ok"] and link.try_check(opened["try_id"])["ok"]
    port = opened["port"]
    make_venv(link, opened["try_id"])                       # the try's own Python runs its server (D61)
    assert link.try_start(opened["try_id"], {"program": "python",
                                             "args": ["-m", "http.server", str(port), "--bind", "127.0.0.1"]})["ok"]
    rec = w.host.trials.get(b["id"], opened["try_id"])
    server = rec.broker.started[0]
    assert server.job.alive()
    w.host._child_done(b["id"], "build", 1)                     # as if its process crashed
    assert not server.job.alive()                               # stopped by its job object
    assert not (state / "packages" / "hello-page").exists()     # removed, junction-safe
    job = w.host.job(b["jobs"][0]["id"])
    assert job["status"] == "held" and job["run"]["line"] == service.CRASHED_LINE
    from puffpai_core.zone3.toolsmith.errors import LinkError
    with pytest.raises(LinkError):
        link.build()                                            # its token is gone


def test_child_env_has_no_core_names_and_no_proxy(monkeypatch):
    monkeypatch.setenv("PUFFPAI_SOMETHING", "x")
    monkeypatch.setenv("HTTPS_PROXY", "http://example.invalid:1")
    env = process.child_env()
    assert not any(k.upper().startswith("PUFFPAI") for k in env)
    assert "HTTPS_PROXY" not in {k.upper() for k in env} and env["NO_PROXY"].startswith("127.0.0.1")


@pytest.fixture
def served(tmp_path, state):
    """The Core API on a free port, Toolsmith's host with the REAL process runner."""
    from waitress import create_server
    procs = []
    w = make_world(tmp_path, state, procs)
    port = _free_port()
    server = create_server(w.app, host="127.0.0.1", port=port, threads=4)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    host = ToolsmithHost(state, w.ports, w.registry, w.core.tokens, api=f"http://127.0.0.1:{port}",
                         sleep=lambda s: None)
    w.app.extensions["puffpai_toolsmith"] = host
    spawned = []
    real_launch = host.runner.launch

    def launch(*a, **kw):
        child = real_launch(*a, **kw)
        spawned.append(child)
        return child
    host.runner.launch = launch
    try:
        yield host, w, spawned
    finally:
        host.stop(timeout=30)
        for c in spawned:                    # each by its own job object
            c.stop()
        server.close()
        for pr in procs:
            end_held(pr)


def test_the_real_toolsmith_process_builds_the_fixture_app(served, state):
    host, w, spawned = served
    b = host.plan("daily news headlines", [{"name": "Daily headlines", "does": "Shows news headlines",
                                           "sample_ask": "What are the headlines today?"}])
    assert spawned and spawned[0].code == 0, (state / "logs" / "toolsmith").glob("agent-*.log")
    assert b["jobs"][0]["plan"]["candidates"][0]["full_name"] == "example/hello-page"
    w.ports.consent.say_yes(b["consent"]["id"])
    host.start(b["id"], b["consent"]["id"])
    assert spawned[1].wait(180), "Toolsmith's process did not finish"
    assert spawned[1].code == 0
    job = host.job(b["jobs"][0]["id"])
    assert job["status"] == "working", job["run"]
    # Its token never went on its command line, into its environment or its log.
    args = " ".join(map(str, spawned[1].job.proc.args))
    assert "pts_" not in args and "-I" in args
    log = (state / "logs" / "toolsmith" / f"agent-{b['id']}.log").read_text(encoding="utf-8", errors="replace")
    assert "pts_" not in log
    assert not spawned[1].job.alive()
    assert w.core.tokens.revoke_toolsmith(b["id"]) == 0          # revoked when it ended
