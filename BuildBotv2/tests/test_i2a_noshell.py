"""Lane I2a part B: npm and npx on Windows with no shell (D60). npm.cmd and npx.cmd would start cmd.exe, so
BuildBot runs node.exe with npm-cli.js / npx-cli.js, and never starts any other .cmd or .bat file: not in a
try (the broker's allowlist, a structured "not run"), not in an installed package's setup, start or health
command (Steward), and not at all (``jobs.Job.spawn``). The fake Node.js folders here are never run."""

import json
import os
import sys
from pathlib import Path

import pytest

from conftest import yes
from puffpai_core.steward import StewardError, jobs, noshell
from puffpai_core.zone3.toolsmith_host import allowlist as A
from puffpai_core.zone3.toolsmith_host.broker import Broker

windows = pytest.mark.skipif(os.name != "nt", reason="batch files and .cmd wrappers are Windows only")


def _fake_node(root: Path) -> Path:
    """A Node.js folder as the installer lays it out: node.exe, npm.cmd, npx.cmd, node_modules\\npm\\bin."""
    root.mkdir(parents=True)
    (root / "node.exe").write_bytes(b"MZ" + b"\0" * 64)              # starts like a program; never started
    for tool in ("npm", "npx"):
        (root / f"{tool}.cmd").write_text(f'@"%~dp0node.exe" "%~dp0node_modules\\npm\\bin\\{tool}-cli.js" %*\r\n')
        if os.name != "nt":                                           # the names npm goes by off Windows
            (root / tool).write_text("#!/bin/sh\n")
    cli = root / "node_modules" / "npm" / "bin"
    cli.mkdir(parents=True)
    for tool in ("npm", "npx"):
        (cli / f"{tool}-cli.js").write_text("// fake\n")
    return root / "node.exe"


@pytest.fixture
def node(tmp_path):
    return str(Path(os.path.realpath(_fake_node(tmp_path / "nodejs"))))


# -- the resolver --------------------------------------------------------------------------------------

def test_npm_and_npx_become_node_and_their_cli_script(node):
    cli = Path(node).parent / "node_modules" / "npm" / "bin"
    assert noshell.resolve(["npm", "install", "--no-audit"], node) == [node, str(cli / "npm-cli.js"), "install",
                                                                       "--no-audit"]
    assert noshell.resolve(["npx.cmd", "-y", "serve"], node) == [node, str(cli / "npx-cli.js"), "-y", "serve"]
    beside = str(Path(node).parent / "npm.cmd")                            # the wrapper itself, by its path
    assert noshell.resolve([beside, "start"], node) == [node, str(cli / "npm-cli.js"), "start"]


@windows
@pytest.mark.parametrize("program", ["build.cmd", "setup.bat", "tools\\run.CMD", "x.bat.", "x.cmd  ", "C:/a/b.Bat"])
def test_any_other_batch_file_is_refused_with_a_plain_reason(node, program):
    with pytest.raises(noshell.NoShell, match="batch file"):
        noshell.resolve([program, "arg"], node)


@windows
def test_an_npm_cmd_that_is_not_next_to_the_known_node_is_refused(node, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "npm.cmd").write_text("@echo off\r\n")
    with pytest.raises(noshell.NoShell, match="batch file"):
        noshell.resolve([str(other / "npm.cmd"), "install"], node)


def test_npm_with_no_node_is_refused_not_guessed():
    with pytest.raises(noshell.NoShell, match="needs Node.js"):
        noshell.resolve(["npm", "install"], "")


@windows
def test_the_known_node_is_only_ever_a_real_node_exe(node, tmp_path, monkeypatch):
    wrapper = tmp_path / "shims" / "node.cmd"
    wrapper.parent.mkdir()
    wrapper.write_text("@echo off\r\n")
    monkeypatch.setattr(noshell.shutil, "which", lambda name: str(wrapper))
    assert noshell.node_exe() == ""                                       # a node.cmd wrapper is not Node.js
    monkeypatch.setattr(noshell.shutil, "which", lambda name: node if name == "node.exe" else str(wrapper))
    assert noshell.node_exe() == node
    assert A.known_node() == node


@windows
@pytest.mark.parametrize("name", ["hello.cmd", "hello.bat"])
def test_job_spawn_never_starts_a_batch_file(tmp_path, name):
    marker = tmp_path / "ran.txt"
    script = tmp_path / name
    script.write_text(f'@echo ran > "{marker}"\r\n')
    with pytest.raises(noshell.NoShell, match="batch file"):
        jobs.Job.spawn([str(script)], tmp_path, dict(os.environ))
    assert not marker.exists()


# -- a try: the broker's allowlist ----------------------------------------------------------------------

def _broker(tmp_path, node) -> Broker:
    folder = tmp_path / "try"
    folder.mkdir()
    b = Broker(folder, tmp_path / "scratch", timing={})
    b.node = node                                    # the fake Node.js stands in for the one on PATH
    return b


def test_a_try_runs_npm_as_node_and_npm_cli(tmp_path, node):
    b = _broker(tmp_path, node)
    plan = b.plan({"program": "npm", "args": ["install"]})
    assert not isinstance(plan, dict), plan
    assert plan.argv[:2] == [node, str(Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js")]
    plan = b.plan({"program": "npx", "args": ["-y", "serve"]})
    assert plan.argv[1].endswith("npx-cli.js") and not any(a.lower().endswith(".cmd") for a in plan.argv)


@windows
@pytest.mark.parametrize("request_body", [
    {"program": "build"},                             # found as build.cmd in the try folder
    {"program": "build.cmd"},
    {"program": "./tools/setup.bat", "args": ["x"]},
    {"program": "tools/npm.cmd", "args": ["install"]},   # the try's own npm.cmd, not the one next to node.exe
])
def test_a_try_never_runs_a_batch_file_and_says_so_structured(tmp_path, node, request_body):
    b = _broker(tmp_path, node)
    (b.folder / "build.cmd").write_text("@echo off\r\n")
    (b.folder / "tools").mkdir()
    (b.folder / "tools" / "setup.bat").write_text("@echo off\r\n")
    (b.folder / "tools" / "npm.cmd").write_text("@echo off\r\n")
    if request_body["program"] != "tools/npm.cmd":
        b.node = ""                                   # no Node.js: nothing may map a .cmd to node
    got = b.plan(request_body)
    assert isinstance(got, dict) and got["ok"] is False and got.get("refused"), got
    nr = got["not_run"]
    assert nr["program"] == request_body["program"] and "batch file" in nr["why"], nr
    got = b.run(request_body)                         # run says the same, and nothing starts
    assert "exit" not in got and "batch file" in got["not_run"]["why"]


# -- an installed package: Steward's setup, start and health ---------------------------------------------

def _package(folder: Path, setup: list, start: list) -> Path:
    folder.mkdir(parents=True)
    (folder / "server.py").write_text("print('hi')\n", encoding="utf-8")
    manifest = {
        "schema": "puffpai-package/1", "id": "npm-app", "kind": "app", "name": "Npm App", "version": "1.0.0",
        "description": "A test app whose run block uses npm.", "author": "Cypher Sisters", "license": "MIT",
        "run": {"default": {"type": "command", "setup": setup, "start": start,
                            "env": {"PORT": "{port}", "HOST": "127.0.0.1"}}},
        "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 60},
        "ui": {"pages": [{"id": "main", "title": "Npm", "url": "http://127.0.0.1:{port}/", "show": "new_tab"}]},
        "needs": {"hosts": []},
        "consent": "A test app. It does not use the internet.",
    }
    (folder / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_steward_runs_a_saved_bare_npm_setup_step_as_node_and_npm_cli(core, drawbridge, tmp_path, node,
                                                                       monkeypatch):
    ran = []
    monkeypatch.setattr(noshell, "node_exe", lambda: node)
    monkeypatch.setattr(jobs, "run", lambda argv, cwd, env, log, timeout_s: ran.append(list(argv)) or 0)
    core.packages.start_after_install = False
    folder = _package(tmp_path / "src" / "npm-app", ["npm install --no-audit", ["npx", "-y", "build-it"]],
                      ["{node}", "server.js"])
    assert yes(core, core.packages.add(str(folder)))["status"] == "installed"
    cli = Path(node).parent / "node_modules" / "npm" / "bin"
    assert ran == [[node, str(cli / "npm-cli.js"), "install", "--no-audit"],
                   [node, str(cli / "npx-cli.js"), "-y", "build-it"]]
    assert core.steward.values("npm-app", core.steward.folder("npm-app"), {}, None)["node"] == node


@windows
def test_steward_refuses_a_batch_file_setup_step_with_a_plain_reason(core, drawbridge, tmp_path):
    core.packages.start_after_install = False
    folder = _package(tmp_path / "src" / "npm-app", ["build.cmd --all"], ["{python}", "server.py"])
    (folder / "build.cmd").write_text("@echo off\r\n")
    got = yes(core, core.packages.add(str(folder)))
    assert got["status"] == "failed", got
    said = " ".join(got["problems"])
    assert "Setup step 1" in said and "batch file" in said and "cmd.exe" in said, said


@windows
def test_steward_refuses_a_batch_file_start_and_health_command(core, drawbridge, tmp_path):
    core.packages.start_after_install = False
    folder = _package(tmp_path / "src" / "npm-app", [], ["start.bat"])
    (folder / "start.bat").write_text("@echo off\r\n")
    assert yes(core, core.packages.add(str(folder)))["status"] == "installed"
    with pytest.raises(StewardError, match="batch file"):
        core.steward.start("npm-app")
    assert core.registry.get("npm-app")["status"] != "running"
    assert core.steward.run_check_command("npm-app", ["start.bat"], timeout_s=5) == 1
    assert "health command not run" in core.steward.log_path("npm-app").read_text(encoding="utf-8")


@pytest.mark.skipif(not noshell.node_exe() or not noshell.npm_cli(noshell.node_exe(), "npm"),
                    reason="Node.js (node.exe with npm) is not on this computer")
def test_with_the_real_node_a_saved_npm_setup_step_really_runs_with_no_cmd(core, drawbridge, tmp_path):
    core.packages.start_after_install = False
    folder = _package(tmp_path / "src" / "npm-app", ["npm --version"], ["{python}", "server.py"])
    assert yes(core, core.packages.add(str(folder)), timeout_s=180)["status"] == "installed"
    log = core.steward.log_path("npm-app").read_text(encoding="utf-8")
    line = next(ln for ln in log.splitlines() if "setup step 1:" in ln)
    assert "npm-cli.js" in line and ".cmd" not in line.lower(), line
    assert any(ln.strip()[:1].isdigit() for ln in log.splitlines()), log[-500:]      # npm printed its version


def test_python_is_never_mapped(node):
    assert noshell.resolve([sys.executable, "-V"], node) == [sys.executable, "-V"]
