"""Lane I2a part C: what the Core starts later is what ran in the try, and the allowed programs stay inside it.

1. The plan's Yes covers a command the Core itself starts from an adopted app's manifest (run block setup
   and start, a health command, a stdio MCP server, ui.open) only when the broker ran exactly that command
   with success in this build's try (``Broker.record``, the Core's own memory). Anything else is on a card
   that lists the exact commands, and nothing installs or starts before she answers.
2. An adopted app runs only from the ``.buildbot/puffpai.json`` the Core wrote, never a top-level
   ``puffpai.json`` the download shipped; Toolsmith can't write, move or delete either one.
3. pip runs only into the try's own .venv (no ``--python``, ``--target``, ``--prefix``, ``--root``,
   ``--user``, ``--src``, in any spelling pip reads).
4. ``python -m`` runs only pip, http.server (127.0.0.1 and the try's port) and modules that are files of the
   try or its own .venv.
5. Every refusal from the allowlist and the broker is one structured ``not_run`` record.
6. A try gets a short TEMP of its own, so npm answers even when the test's own TEMP is deep.

Process rule for these tests: the only process any line here asks to stop is one this test started itself
(by its recorded pid), and it is ended by that same Popen object at the end.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_d61_allowlist import HELLO, WIN, _checked, _never_ran, needs_jobs, world  # noqa: F401 - shared
from toolsmith_fakes import FakePorts, World, make_venv, script
from puffpai_core.packages import manifest as manifest_mod
from puffpai_core.packages import plan_cover
from puffpai_core.packages.manager import AddError
from puffpai_core.steward import noshell
from puffpai_core.zone3.toolsmith_host import allowlist as A
from puffpai_core.zone3.toolsmith_host import broker as broker_mod
from puffpai_core.zone3.toolsmith_host.ports import Ports

URL = "https://github.com/example/hello-page"
SERVE = ("import http.server, os\n"
         "http.server.ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])),"
         " http.server.SimpleHTTPRequestHandler).serve_forever()\n")
NODE = noshell.node_exe()
needs_node = pytest.mark.skipif(not NODE, reason="Node.js is not on this computer")


# -- helpers -----------------------------------------------------------------------------------------------

def _adopted_manifest(pkg_id: str, url: str = URL, **run) -> dict:
    block = {"type": "command", "python": True, "start": ["{python}", "serve.py"],
             "env": {"PORT": "{port}", "HOST": "127.0.0.1"}}
    block.update(run)
    return {"schema": "puffpai-package/1", "id": pkg_id, "kind": "app", "name": "Hello Page", "version": "0.1.0",
            "description": "A test page.", "author": "example", "license": "MIT", "homepage": url,
            "origin": f"adopted:{url}", "run": {"windows": block},
            "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 60},
            "ui": {"pages": [{"id": "main", "title": "Hello Page", "url": "http://127.0.0.1:{port}/",
                              "show": "new_tab"}]},
            "needs": {"hosts": [], "install_hosts": ["github.com", "pypi.org", "files.pythonhosted.org"]},
            "consent": "Hello Page is an outside app. It runs as you."}


class RealPackages(FakePorts):
    """Toolsmith's fake outside world (the fixture repo for download), with the REAL Package Manager, Steward
    and Drawbridge behind the add (the Core's own ``Ports`` methods over the World's Core)."""

    def attach(self, core, registry, state):
        self.real = Ports(state, registry=registry, consent=core.consent, bus=core.bus, packages=core.packages,
                          steward=core.steward)

    def packages_add(self, folder, plan=None, adopted=None):
        self.plans = getattr(self, "plans", []) + [plan]
        return self.real.packages_add(folder, plan=plan, adopted=adopted)

    def packages_wait(self, cid, timeout_s=0.0):
        return self.real.packages_wait(cid, timeout_s)

    def steward_start(self, pkg_id):
        return self.real.steward_start(pkg_id)


@pytest.fixture
def real_world(tmp_path, state):
    # Its own small repo: the shared hello-page fixture carries words Drawbridge stops on purpose.
    repo = tmp_path / "repo-hello-page"
    repo.mkdir()
    (repo / "README.md").write_text("# Hello Page\n\nA tiny page of today's notes.\n", encoding="utf-8")
    (repo / "index.html").write_text("<!doctype html><title>Hello</title><p>Hello</p>\n", encoding="utf-8")
    ports = RealPackages(tmp_path)
    w = World(tmp_path, state, ports=ports)
    ports.attach(w.core, w.registry, state)
    ports.fixture_repo = repo
    w.core.packages.start_after_install = False          # setup only: nothing of the app keeps running
    yield w
    w.host.stop(timeout=10)
    w.core.steward.stop_all(remember=False)


def _plan_yes(w, b):
    """Her real plan Yes for build *b* in the World's own consent store (what the plan card records)."""
    job = b["jobs"][0]
    envelope = {"goal": "a page", "jobs": [{"name": job["name"], "candidates": ["github:example/hello-page"]}],
                "setup": [], "install_hosts": ["github.com", "pypi.org", "files.pythonhosted.org"]}
    covers = {"build": b["id"], "plan_sha": plan_cover.envelope_sha(envelope),
              "card": {"title": "Build this plan?", "sentence": "Toolsmith finds and installs apps for 1 job.",
                       "lines": ["Daily headlines: try hello-page first."]},
              "install_hosts": envelope["install_hosts"]}
    card = w.core.consent.ask("package", f"toolsmith-build-{b['id'].rsplit('-', 1)[-1]}", covers,
                              bound={"plan_sha": covers["plan_sha"]})
    w.core.consent.answer(card["consent_id"], "yes", by="owner", card_sha=card["card_sha"])
    w.host.store.change(b["id"], lambda x: x.update(consent={"id": card["consent_id"]}, envelope=envelope))
    return card["consent_id"]


def _try_that_served(w):
    """A real try: its own .venv, serve.py written, started as a server and stopped. ``(b, link, try_id, rec)``."""
    b, link, try_id, rec = _checked(w)
    make_venv(link, try_id)
    started = script(link, try_id, "serve.py", SERVE, start=True)
    assert started["ok"], started
    assert link.try_stop(try_id)["ok"]
    return b, link, try_id, rec


# -- 1. the run block gets a card unless it is exactly what ran in the try ----------------------------------

@needs_jobs
def test_the_broker_records_what_ran_and_started_but_never_what_failed_or_was_refused(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    assert script(link, try_id, "ok.py", "print('fine')\n", args=["--flag"])["ok"]
    assert not script(link, try_id, "bad.py", "raise SystemExit(3)\n")["ok"]
    _never_ran(link.try_run(try_id, {"program": "python", "args": ["-c", "print(1)"]}))
    started = script(link, try_id, "serve.py", SERVE, start=True)
    assert started["ok"], started
    link.try_stop(try_id)
    got = rec.broker.record()
    assert got["folder"] == os.path.realpath(rec.folder) and got["port"] == rec.port
    assert got["python"] == os.path.realpath(rec.folder / ".venv" / "Scripts" / "python.exe")
    assert {"kind": "run", "program": "{python}", "args": ["ok.py", "--flag"], "cwd": ""} in got["commands"]
    assert {"kind": "start", "program": "{python}", "args": ["serve.py"], "cwd": ""} in got["commands"]
    assert not any("bad.py" in c["args"] or "-c" in c["args"] for c in got["commands"]), got["commands"]
    # a copy: changing it changes nothing the Core keeps
    got["commands"].append({"kind": "start", "program": "{python}", "args": ["evil.py"], "cwd": ""})
    assert len(rec.broker.record()["commands"]) == 2


@needs_jobs
def test_a_run_block_that_is_exactly_what_ran_in_the_try_is_covered_by_the_plan(real_world):
    w = real_world
    b, link, try_id, rec = _try_that_served(w)
    _plan_yes(w, b)
    got = link.try_add(try_id, "local", _adopted_manifest(rec.pkg_id))
    assert got.get("ok") and got.get("covered"), got
    plan = w.ports.plans[-1]
    assert plan["ran"]["commands"] and not plan_cover.commands_outside(_adopted_manifest(rec.pkg_id), plan["ran"])
    state = w.core.packages.wait(got["consent_id"], 300)
    assert state["status"] == "installed", state
    assert w.core.consent.get(got["consent_id"])["by"].startswith("plan:")


@needs_jobs
@pytest.mark.parametrize("run,shown", [
    ({"setup": [["{python}", "-m", "pip", "install", "puffpai-never-a-package"]]},
     "setup step 1: {python} -m pip install puffpai-never-a-package"),
    ({"start": ["{python}", "other.py"]}, "start: {python} other.py"),
])
def test_a_run_block_command_that_never_ran_in_the_try_gets_a_card_and_nothing_installs_or_starts(
        real_world, run, shown):
    w = real_world
    b, link, try_id, rec = _try_that_served(w)
    if "other.py" in json.dumps(run):
        assert link.try_write(try_id, "other.py", "print('never run in the try')\n")["ok"]
    _plan_yes(w, b)
    got = link.try_add(try_id, "local", _adopted_manifest(rec.pkg_id, **run))
    assert got.get("ok") and got.get("waiting") and not got.get("covered"), got
    cid = got["consent_id"]
    card = w.core.consent.get(cid)
    assert card["status"] == "pending"
    lines = card["card"]["outside_plan"]
    assert any(shown in x and "did not run in its try" in x for x in lines), lines
    # Nothing installs or starts before she answers.
    assert w.core.packages.wait(cid, 2.0)["status"] == "pending"
    assert w.registry.get(rec.pkg_id) is None and w.core.steward.running() == []
    assert not (w.state / "packages" / rec.pkg_id).exists()
    assert "waits for your Yes" in link.try_add_state(try_id)["said"]
    w.core.consent.answer(cid, "no", by="owner", card_sha=card["card_sha"])
    assert w.core.packages.wait(cid, 30)["status"] == "declined"
    assert w.registry.get(rec.pkg_id) is None and w.core.steward.running() == []


def _ran(folder, *commands, python=True):
    folder = str(folder)
    return {"folder": folder, "python": folder + r"\.venv\Scripts\python.exe" if python else "", "node": NODE,
            "port": 18555, "commands": list(commands)}


START = {"kind": "start", "program": "{python}", "args": ["serve.py"], "cwd": ""}


@pytest.mark.parametrize("manifest_change,shown", [
    ({"run": {"windows": {"type": "command", "python": True, "start": ["{python}", "serve.py", "--debug"]}}},
     "start: {python} serve.py --debug"),                                                     # other arguments
    ({"run": {"windows": {"type": "command", "python": True, "start": ["{python}", "serve.py"], "cwd": "web"}}},
     "start: {python} serve.py (in its web folder)"),                                         # another folder
    ({"run": {"windows": {"type": "command", "start": ["{python}", "serve.py"]}}},
     "start: {python} serve.py"),                                       # no python: true = the Core's own Python
    ({"run": {"windows": {"type": "command", "python": True, "start": ["python", "serve.py"]}}},
     "start: python serve.py"),                                         # a name looked up on the PATH
    ({"run": {"windows": {"type": "command", "python": True, "setup": [["{python}", "serve.py"]],
                          "start": ["{python}", "serve.py"]}}},
     "setup step 1: {python} serve.py"),                                # ran as a server, not to its end
    ({"run": {"windows": {"type": "command", "python": True, "start": ["{python}", "serve.py", "{data}"]}}},
     "start: {python} serve.py {data}"),                                # no data folder in a try
    ({"health": {"command": ["{python}", "check.py"]}}, "health check: {python} check.py"),
    ({"abilities": {"mcp": {"transport": "stdio", "command": ["{python}", "mcp.py"]}}},
     "its MCP server: {python} mcp.py"),
    ({"ui": {"pages": [], "open": {"program": "{python}", "args": ["gui.py"]}}}, "its Open button: {python} gui.py"),
])
def test_each_command_the_core_starts_is_on_the_card_unless_it_ran_exactly(tmp_path, manifest_change, shown):
    manifest = {**_adopted_manifest("hello-page"), **manifest_change}
    lines = plan_cover.commands_outside(manifest, _ran(tmp_path, START))
    assert lines and shown in lines[0], lines


def test_a_run_block_setting_beyond_port_and_host_gets_a_card(tmp_path):
    manifest = _adopted_manifest("hello-page", env={"PORT": "{port}", "HOST": "127.0.0.1", "DEBUG_HOOK": "x"})
    assert plan_cover.commands_outside(manifest, _ran(tmp_path, START)) == [
        "Its programs get settings they did not have in its try: DEBUG_HOOK=x."]
    assert plan_cover.commands_outside(_adopted_manifest("hello-page"), _ran(tmp_path, START)) == []


def test_no_record_or_a_record_naming_another_program_covers_nothing(tmp_path):
    manifest = _adopted_manifest("hello-page")
    assert plan_cover.commands_outside(manifest, None)
    assert plan_cover.commands_outside(manifest, {})
    forged = dict(START, program="other:C:\\Windows\\py.exe")
    assert plan_cover.commands_outside(manifest, _ran(tmp_path, forged))


@needs_node
def test_npm_in_a_run_block_matches_the_npm_the_try_ran_as_node_and_its_script(tmp_path):
    ran = _ran(tmp_path, {"kind": "run", "program": "{npm}", "args": ["install"], "cwd": ""},
               {"kind": "start", "program": "{npm}", "args": ["start"], "cwd": ""})
    good = {**_adopted_manifest("hello-page"), "run": {"default": {"type": "command", "node": True,
                                                                   "setup": [["npm", "install"]],
                                                                   "start": ["npm", "start"]}}}
    assert plan_cover.commands_outside(good, ran) == []
    bad = {**good, "run": {"default": {"type": "command", "setup": [["npm", "install", "left-pad"]],
                                       "start": ["npm", "start"]}}}
    assert "setup step 1: npm install left-pad" in plan_cover.commands_outside(bad, ran)[0]


def test_a_program_from_the_download_counts_only_while_it_is_the_file_that_ran(tmp_path):
    staged = tmp_path / "staged"
    (staged / "bin").mkdir(parents=True)
    (staged / "bin" / "tool.exe").write_bytes(b"MZ the one that ran")
    import hashlib
    sha = hashlib.sha256(b"MZ the one that ran").hexdigest()
    try_folder = tmp_path / "try"
    ran = _ran(try_folder, {"kind": "start", "program": "pkg:bin/tool.exe", "args": ["--serve"], "cwd": "",
                            "sha": sha})
    manifest = {**_adopted_manifest("hello-page"),
                "run": {"windows": {"type": "command", "start": ["{pkg}\\bin\\tool.exe", "--serve"]}}}
    assert plan_cover.commands_outside(manifest, ran, staged) == []
    (staged / "bin" / "tool.exe").write_bytes(b"MZ another program now")
    assert plan_cover.commands_outside(manifest, ran, staged)


# -- 2. which puffpai.json counts ------------------------------------------------------------------------------

def _two_manifests(folder, top_start, core_start):
    top = _adopted_manifest("hello-page", start=top_start)
    core = _adopted_manifest("hello-page", start=core_start)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "serve.py").write_text(SERVE, encoding="utf-8")
    (folder / "puffpai.json").write_text(json.dumps(top), encoding="utf-8")
    (folder / ".buildbot").mkdir(exist_ok=True)
    (folder / ".buildbot" / "puffpai.json").write_text(json.dumps(core), encoding="utf-8")


def test_an_adopted_app_runs_only_from_the_manifest_the_core_wrote_never_a_shipped_top_level_one(core, tmp_path):
    folder = tmp_path / "hello-page"
    _two_manifests(folder, ["{python}", "evil.py"], ["{python}", "serve.py"])
    data, path = manifest_mod.load(folder, adopted=True)
    assert path == folder / ".buildbot" / "puffpai.json" and data["run"]["windows"]["start"][1] == "serve.py"
    assert manifest_mod.find(folder) == folder / ".buildbot" / "puffpai.json"     # origin unknown: still the Core's
    staged = core.packages.add(str(folder), adopted=URL)                  # the real Drawbridge
    assert staged["id"] == "hello-page"
    pending = core.packages._pending[staged["consent_id"]]
    assert pending.manifest["run"]["windows"]["start"] == ["{python}", "serve.py"]
    # with no Core-written file, an adopted add is refused (the shipped top-level one never counts)
    (folder / ".buildbot" / "puffpai.json").unlink()
    with pytest.raises(AddError, match="the Core wrote"):
        core.packages.add(str(folder), adopted=URL)
    with pytest.raises(manifest_mod.ManifestError):
        manifest_mod.load(folder, adopted=True)


def test_steward_reads_an_installed_adopted_app_from_the_core_s_manifest(core, drawbridge, tmp_path):
    folder = tmp_path / "hello-page"
    _two_manifests(folder, ["{python}", "evil.py"], ["{python}", "serve.py"])
    core.packages.start_after_install = False
    staged = core.packages.add(str(folder), adopted=URL)
    core.consent.answer(staged["consent_id"], "yes", by="owner", card_sha=staged["card_sha"])
    assert core.packages.wait(staged["consent_id"], 300)["status"] == "installed"
    assert core.steward.manifest("hello-page")["run"]["windows"]["start"] == ["{python}", "serve.py"]


def test_a_built_in_or_hand_made_package_keeps_its_top_level_manifest(tmp_path, freshtable):
    folder = tmp_path / "freshtable"
    folder.mkdir()
    (folder / "puffpai.json").write_text(json.dumps(freshtable), encoding="utf-8")
    (folder / ".buildbot").mkdir()
    (folder / ".buildbot" / "puffpai.json").write_text(json.dumps({**freshtable, "name": "Linked copy"}),
                                                       encoding="utf-8")
    assert manifest_mod.find(folder) == folder / "puffpai.json"
    assert manifest_mod.load(folder)[0]["name"] == freshtable["name"]
    with pytest.raises(manifest_mod.ManifestError, match="adopted"):
        manifest_mod.load(folder, adopted=True)                       # not adopted: never its .buildbot one


@needs_jobs
def test_toolsmith_cannot_write_move_or_delete_the_core_s_manifest_files(world):
    _b, link, try_id, rec = _checked(world)
    from puffpai_core.zone3.toolsmith_host.trials import CORE_FILE_LINE
    (rec.folder / "puffpai.json").write_text("{}", encoding="utf-8")       # as if the download shipped one
    for rel in ("puffpai.json", "PuffPAI.JSON", "puffpai.json.", ".buildbot/puffpai.json", ".BuildBot/other.txt",
                "./.buildbot/x"):
        got = link.try_write(try_id, rel, "{}")
        assert not got["ok"] and got["said"] == CORE_FILE_LINE, (rel, got)
    assert link.try_write(try_id, "notes.txt", "x")["ok"]
    for op, fields in (("copy", {"from": "notes.txt", "to": "puffpai.json"}),
                       ("copy", {"from": "notes.txt", "to": ".buildbot/puffpai.json"}),
                       ("move", {"from": "notes.txt", "to": ".buildbot"}),
                       ("move", {"from": "puffpai.json", "to": "old.json"}),
                       ("mkdir", {"path": ".buildbot"}),
                       ("delete", {"path": "puffpai.json"}),
                       ("delete", {"path": ".buildbot"})):
        got = link.try_fs(try_id, op, **fields)
        assert not got["ok"] and got["said"] == CORE_FILE_LINE, (op, fields, got)
    assert (rec.folder / "puffpai.json").read_text(encoding="utf-8") == "{}"
    assert not (rec.folder / ".buildbot").exists()
    # the app's own package.json stays the app's (its scripts run as her, the "runs as you" line)
    assert link.try_write(try_id, "package.json", '{"name": "hello-page"}')["ok"]


@needs_jobs
def test_the_core_writes_the_manifest_toolsmith_sends_and_refuses_one_for_another_app(world):
    _b, link, try_id, rec = _checked(world)
    assert "Send the app's puffpai.json" in link.try_add(try_id, "local")["said"]
    other = _adopted_manifest("someone-else")
    assert "does not name this app" in link.try_add(try_id, "local", other)["said"]
    wrong_url = _adopted_manifest(rec.pkg_id, url="https://github.com/example/other")
    assert "does not name this app" in link.try_add(try_id, "local", wrong_url)["said"]
    assert not (rec.folder / ".buildbot" / "puffpai.json").exists()
    got = link.try_add(try_id, "local", _adopted_manifest(rec.pkg_id))
    assert got.get("ok"), got
    added = json.loads((world.ports.pm[got["consent_id"]]["staged"] / ".buildbot" / "puffpai.json")
                       .read_text(encoding="utf-8"))
    assert added == _adopted_manifest(rec.pkg_id) and world.ports.adopted[-1] == URL


# -- 3. pip stays inside the try's own venv ---------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["install", "--python", "C:\\Python311\\python.exe", "x"], ["--python=C:\\Python311\\python.exe", "install", "x"],
    ["install", "--pyth=C:\\py.exe", "x"], ["install", "--py", "C:\\py.exe", "x"],
    ["install", "--target", "lib", "x"], ["install", "--target=lib", "x"], ["install", "--tar", "lib", "x"],
    ["install", "-t", "lib", "x"], ["install", "-tlib", "x"], ["install", "-qt", "lib", "x"],
    ["install", "--prefix", "C:\\elsewhere", "x"], ["install", "--prefi=C:\\x", "x"], ["install", "--pref", "x", "y"],
    ["install", "--root", "C:\\x", "y"], ["install", "--ro=C:\\x", "y"],
    ["install", "--user", "x"], ["install", "--us", "x"], ["install", "--USER", "x"],
    ["install", "--src", "src", "-e", "git+https://example.org/x"], ["install", "--sr=src", "x"],
    ["install", "--global-option=--install-lib=C:\\x", "x"], ["install", "--install-option", "--prefix=C:\\x", "y"],
    ["install", "--break-system-packages", "x"],
    ["config", "set", "global.target", "C:\\elsewhere"], ["config", "--site", "set", "global.target", "C:\\x"],
    ["config", "unset", "global.index-url"], ["config", "edit"], ["config", "list"],   # part E: no config at all
])
def test_pip_options_that_aim_pip_at_another_python_or_place_are_refused(tmp_path, args):
    with pytest.raises(A.Refusal) as e:
        A._pip_args(args, tmp_path, tmp_path)
    reply = e.value.reply()
    assert reply["not_run"]["kind"] == A.DENIED and not reply["ok"]


@pytest.mark.parametrize("args", [
    ["install", "-r", "requirements.txt"], ["install", "--pre", "x"], ["install", "-U", "x"],
    ["install", "--upgrade", "x"], ["install", "--root-user-action=ignore", "x"], ["--version"], ["list"],
    ["download", "--python-version", "3.11", "-d", "wheels", "x"], ["install", "--log", "pip.log", "x"],
    ["install", "-rrequirements.txt"],
])
def test_pip_inside_the_trys_own_venv_still_works(tmp_path, args):
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")   # part E: the file is read
    A._pip_args(args, tmp_path, tmp_path)


def test_pip_writes_its_logs_and_downloads_only_inside_the_try(tmp_path):
    for args in (["install", "--log", str(tmp_path.parent / "out.log"), "x"],
                 ["install", "--log-file=..\\out.log", "x"], ["download", "-d", "..\\wheels", "x"],
                 ["install", "--report", "C:\\report.json", "x"]):
        with pytest.raises(A.Refusal):
            A._pip_args(args, tmp_path, tmp_path)


@needs_jobs
def test_a_refused_pip_option_comes_back_as_not_run_through_the_route(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    for request in ({"program": "python", "args": ["-m", "pip", "install", "--tar", "lib", "x"]},
                    {"program": "pip", "args": ["install", "--user", "x"]},
                    {"program": "python", "args": ["-m", "pip._internal", "install", "--target", "lib", "x"]}):
        got = link.try_run(try_id, request)
        _never_ran(got)
        assert got["not_run"]["kind"] == A.DENIED and got["not_run"]["args"] == request["args"], got
    assert not (rec.folder / "lib").exists()


# -- 4. python -m is checked ------------------------------------------------------------------------------------

def _fake_try(tmp_path) -> A.Place:
    """A try folder with a Core-made-looking .venv (never run: decide only reads it) and installed modules."""
    folder = tmp_path / "app"
    sp = folder / ".venv" / "Lib" / "site-packages"
    for pkg in ("flask", "uvicorn", "pytest", "pip"):
        (sp / pkg).mkdir(parents=True)
        (sp / pkg / "__init__.py").write_text("", encoding="utf-8")
        (sp / pkg / "__main__.py").write_text("", encoding="utf-8")
    (folder / ".venv" / "Scripts").mkdir(parents=True)
    (folder / ".venv" / "Scripts" / "python.exe").write_bytes(b"MZ not a real python")
    (folder / ".venv" / "pyvenv.cfg").write_text("home = C:\\Python311\n", encoding="utf-8")
    (folder / "myapp.py").write_text("", encoding="utf-8")
    (folder / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (folder / "mypkg").mkdir()
    (folder / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "mypkg" / "__main__.py").write_text("", encoding="utf-8")
    (folder / "onlyaname").mkdir()                         # a folder with no __init__: a namespace part only
    return A.Place(folder=folder, venv=A.venv_record(folder))


def _decide(place, args, port=None):
    place.port = port
    return A.decide(A.Request("python", list(args)), place)


@pytest.mark.parametrize("args", [
    ["-m", "flask", "run"], ["-m", "uvicorn", "app:app", "--port", "18555"], ["-m", "pytest", "-q"],
    ["-m", "myapp"], ["-m", "mypkg"], ["-mflask", "run"], ["-I", "-m", "pip", "install", "x"],
    ["-m", "pip", "install", "-r", "requirements.txt"],
])
def test_python_dash_m_runs_pip_and_the_trys_own_modules(tmp_path, args):
    plan = _decide(_fake_try(tmp_path), args)
    assert plan.kind == "run" and plan.argv[1:] == args


@pytest.mark.parametrize("args", [
    ["-m", "json.tool", "x.json"], ["-m", "cProfile", "C:\\outside\\x.py"], ["-m", "ensurepip"], ["-m", "site"],
    ["-m", "webbrowser", "https://example.org"], ["-m", "zipapp", "."], ["-m", "http"], ["-m", "venv", "other"],
    ["-I", "-m", "venv", ".venv"], ["-m", "pip._internal", "install", "x"], ["-m", "no_such_module_anywhere"],
    ["-m", "onlyaname"], ["-m", "pdb", "myapp.py"], ["-m", "os"], ["-m", "_thread"],
])
def test_python_dash_m_refuses_python_itself_and_modules_from_nowhere_in_the_try(tmp_path, args):
    with pytest.raises(A.Refusal) as e:
        _decide(_fake_try(tmp_path), args)
    assert e.value.reply()["not_run"]["kind"] in (A.DENIED, A.HINT)


@pytest.mark.skipif(not WIN, reason="junctions are Windows only")
def test_a_module_reached_through_a_junction_out_of_the_try_is_refused(tmp_path):
    place = _fake_try(tmp_path)
    outside = tmp_path / "outside" / "sneaky"
    outside.mkdir(parents=True)
    (outside / "__init__.py").write_text("", encoding="utf-8")
    junction = Path(place.folder) / "sneaky"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)
    try:
        with pytest.raises(A.Refusal):
            _decide(place, ["-m", "sneaky"])
    finally:
        os.rmdir(junction)                                   # the junction only, never what it points at


def test_http_server_runs_only_as_a_server_on_127_0_0_1_and_the_trys_port(tmp_path):
    place = _fake_try(tmp_path)
    ok = _decide(place, ["-m", "http.server", "18555", "--bind", "127.0.0.1"], port=18555)
    assert ok.kind == "run"
    _decide(place, ["-m", "http.server", "18555", "-b", "127.0.0.1", "--directory", "mypkg"], port=18555)
    for args, port in ((["-m", "http.server", "18555", "--bind", "127.0.0.1"], None),       # run, not a server
                       (["-m", "http.server", "18555"], 18555),                            # every address
                       (["-m", "http.server", "18555", "--bind", "0.0.0.0"], 18555),
                       (["-m", "http.server", "8000", "--bind", "127.0.0.1"], 18555),       # not its port
                       (["-m", "http.server", "--bind", "127.0.0.1"], 18555),
                       (["-m", "http.server", "18555", "--bind", "127.0.0.1", "--cgi"], 18555),
                       (["-m", "http.server", "18555", "--bind", "127.0.0.1", "-d", "C:\\"], 18555)):
        with pytest.raises(A.Refusal):
            _decide(place, args, port=port)


@needs_jobs
def test_python_dash_m_runs_a_module_installed_in_the_trys_own_venv_and_http_server_serves(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    mod = rec.folder / ".venv" / "Lib" / "site-packages" / "hellomod"      # as pip would install it
    mod.mkdir(parents=True)
    (mod / "__init__.py").write_text("", encoding="utf-8")
    (mod / "__main__.py").write_text("print('hello from the venv')\n", encoding="utf-8")
    got = link.try_run(try_id, {"program": "python", "args": ["-m", "hellomod"]})
    assert got["ok"] and "hello from the venv" in got["output"], got
    refused = link.try_run(try_id, {"program": "python", "args": ["-m", "json.tool", "index.html"]})
    _never_ran(refused)
    assert refused["not_run"]["kind"] == A.DENIED and "part of Python itself" in refused["said"]
    started = link.try_start(try_id, {"program": "python",
                                      "args": ["-m", "http.server", str(rec.port), "--bind", "127.0.0.1"]})
    assert started["ok"], started
    import urllib.request
    deadline = time.monotonic() + 30
    body = ""
    while time.monotonic() < deadline and not body:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{rec.port}/", timeout=2) as r:
                body = r.read().decode("utf-8", "replace")
        except OSError:
            time.sleep(0.3)
    assert "<" in body
    assert link.try_stop(try_id)["ok"]


# -- 5. every refusal is the same structured not_run record -----------------------------------------------------

@needs_jobs
def test_every_refusal_from_the_allowlist_and_the_broker_is_one_not_run_record(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    outsider = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(120)"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        requests = [
            "echo hi | sh",                                                          # a shell line
            {"program": "cmd", "args": ["/c", "echo", "hi"]},                        # a shell program
            {"program": "python", "args": ["-c", "print(1)"]},                       # inline code
            f"taskkill /F /PID {outsider.pid}",                                      # a process not this try's
            "choco install puffpai-never-a-package -y",                              # a change to the computer
            {"program": "python", "args": ["-V"], "cwd": "..\\.."},                  # a folder outside
            {"program": "\\\\puffpai-no-such-host.invalid\\s\\x.exe"},              # a network path
            {"program": "python", "args": ["-m", "pip", "install", "--user", "x"]},  # pip outside its .venv
            {"program": "hostname"},                                                 # not on the list
        ]
        for request in requests:
            got = link.try_run(try_id, request)
            _never_ran(got)
            nr = got.get("not_run")
            assert isinstance(nr, dict), (request, got)
            assert set(nr) >= {"program", "args", "cwd", "folder", "resolved", "why", "kind"}, (request, nr)
            assert nr["kind"] in (A.DENIED, A.YOUR_TURN, A.HINT) and got["said"] and got["refused"]
        assert outsider.poll() is None                         # nothing reached the outside process
    finally:
        outsider.kill()                                        # the one this test started, by its own handle
        outsider.wait(timeout=10)


def test_toolsmith_says_a_denied_refusal_back_to_the_model_and_makes_no_step_for_her():
    from puffpai_core.zone3.toolsmith.tries import Try
    asked = []
    t = Try.__new__(Try)
    t.ask = lambda step: asked.append(step) or {"ok": True}
    got = t._not_run({"ok": False, "said": "BuildBot never runs a shell (cmd).",
                      "not_run": {"program": "cmd", "args": [], "kind": "denied", "why": "x"}})
    assert got == {"ok": False, "said": "BuildBot never runs a shell (cmd)."} and asked == []


# -- 6. a short TEMP of the try's own ---------------------------------------------------------------------------

@needs_jobs
@needs_node
def test_a_try_gets_a_short_temp_of_its_own_and_npm_answers_when_the_tests_temp_is_deep(tmp_path, monkeypatch):
    # A deep bot folder: the try's old TEMP (its scratch folder under logs) would be about 250 characters,
    # where npm stops answering (seen 10-22). The test's own TEMP is deep too, but under 260: past that,
    # Windows did not start any program at all for this process (seen 10-22, any program, not only npm).
    pad = max(1, 215 - len(str(tmp_path)) - len("\\state") - 1)
    state = tmp_path / ("b" * min(pad, 200)) / "state"
    state.mkdir(parents=True)
    own_temp = tmp_path / ("d" * max(1, 245 - len(str(tmp_path)) - 1))
    own_temp.mkdir()
    if len(str(own_temp)) < 258:
        monkeypatch.setenv("TEMP", str(own_temp))
        monkeypatch.setenv("TMP", str(own_temp))
    monkeypatch.setenv("PUFFPAI_STATE", str(state))
    w = World(tmp_path, state)
    w.ports.fixture_repo = HELLO
    try:
        _b, link, try_id, rec = _checked(w)
        assert len(str(rec.scratch)) >= 245                     # what TEMP was before: npm would not answer
        assert rec.temp is not None and rec.temp.parent == state / "t" and rec.temp.is_dir()
        seen = broker_mod.temp_env_path(rec.temp)
        assert len(seen) < 200
        began = time.monotonic()
        got = link.try_run(try_id, {"program": "npm", "args": ["--version"]})
        assert got["ok"] and got["output"].strip(), got
        assert time.monotonic() - began < 60
        assert link.try_write(try_id, "where.js", "console.log(process.env.TEMP); console.log(require('os').tmpdir())\n")["ok"]
        shown = link.try_run(try_id, {"program": "node", "args": ["where.js"]})
        assert shown["ok"] and shown["output"].lower().count(os.path.normcase(seen)) == 2, (seen, shown)
        temp = rec.temp
        w.host.trials.end(rec)
        assert not temp.exists() and list((state / "t").iterdir()) == []    # made and removed by the Core
    finally:
        w.host.stop(timeout=10)


def test_the_trys_temp_is_removed_only_under_the_bots_own_t_folder(tmp_path):
    bot = tmp_path / "bot"
    temp = broker_mod.make_temp(bot)
    other = tmp_path / "not-a-try-temp"
    other.mkdir()
    assert not broker_mod.remove_temp(other, bot) and other.is_dir()
    assert broker_mod.remove_temp(temp, bot) and not temp.exists()


def test_a_try_never_gets_the_cores_pip_or_python_path_settings(tmp_path, monkeypatch):
    for name in ("PIP_TARGET", "PIP_USER", "PIP_PREFIX", "PYTHONPATH", "NODE_OPTIONS", "PYTHONHOME"):
        monkeypatch.setenv(name, "C:\\elsewhere")
    b = broker_mod.Broker(tmp_path / "app", tmp_path / "scratch", {"command_wait_s": 1, "long_command_s": 1,
                                                                   "build_command_s": 1, "build_quiet_s": 1},
                          temp=broker_mod.make_temp(tmp_path / "bot"))
    env = {k.upper(): v for k, v in b.env().items()}
    assert env["PIP_REQUIRE_VIRTUALENV"] == "1"
    for name in ("PIP_TARGET", "PIP_USER", "PIP_PREFIX", "PYTHONPATH", "NODE_OPTIONS", "PYTHONHOME"):
        assert name not in env
