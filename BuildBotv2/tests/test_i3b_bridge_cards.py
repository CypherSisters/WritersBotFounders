"""Lane I3b: Drawbridge's hold during a try is ONE card for her; build and install grants close when the
build or install ends; the Bridge's posture is read from one source.

From the live fresh-install proof (10-22): the Prove step said "Drawbridge did not pass it: X: waiting for
your Yes" but no card ever reached her and the job gave up; after a build `puffpai bridge status` said open
(GitHub grants kept, phase build) while GET /v1/settings said `not_set`.
"""

import base64
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from test_toolsmith_main_e2e import MainPorts, README
from toolsmith_fakes import FIXTURES, InlineRunner, tool
from z3helpers import grant, manifest, zone
from puffpai_core.zone3.bridge import build_target, is_build_grant
from puffpai_core.zone3.toolsmith_host import ToolsmithHost
from puffpai_core.zone3.toolsmith_host.ports import Ports

BID = "20261022-210000-abcdef"
SEARCH_HOSTS = ["api.github.com", "raw.githubusercontent.com", "github.com"]


def _fast(state):
    """Toolsmith polls every 0.2 s here (her answers come from a test thread)."""
    p = Path(state) / "settings" / "toolsmith.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"where": "local", "timing": {"poll_s": 0.2}}), encoding="utf-8")


def _search_yes(z):
    """Her wizard step 4 "search" choice, as WizardBridge records it: switch on, Bridge open, one host Yes."""
    z.bridge.set_switch("toolsmith:search", True)
    z.bridge.open()
    card = z.consents.ask("host", "toolsmith:search", {"hosts": SEARCH_HOSTS, "phase": "build"})
    return z.consents.answer(card["consent_id"], "yes", "tap", by="owner (setup)", card_sha=card["card_sha"])


def _plan_yes(z, build_id=BID, hosts=("github.com", "pypi.org")):
    """A build plan's Yes (lane L6's card: covers the tries' install hosts)."""
    card = z.consents.ask("package", build_target(build_id), {"build": build_id, "plan_sha": "a" * 64,
                                                              "install_hosts": list(hosts)},
                          bound={"plan_sha": "a" * 64})
    return z.consents.answer(card["consent_id"], "yes", "tap", by="owner", card_sha=card["card_sha"])


def _build_grants(z):
    return [g for g in z.bridge.posture()["grants"] if is_build_grant(g)]


def _allowed(z, subject, host, phase="build"):
    return z.bridge.allowed(subject, host, phase=phase).verdict == "allow"


# -- 2. the Bridge: build grants live only while a build runs ---------------------------------------
def test_search_grants_open_only_while_a_build_runs(state):
    z = zone(state)
    rec = _search_yes(z)
    assert _build_grants(z) == [] and not _allowed(z, "toolsmith:search", "api.github.com")   # no build yet
    assert z.bridge.open_build(BID) == 3
    assert _allowed(z, "toolsmith:search", "api.github.com")
    assert z.bridge.end_build(BID) == 3
    assert _build_grants(z) == [] and not _allowed(z, "toolsmith:search", "api.github.com")
    assert z.consents.granted(rec["consent_id"]) is not None                    # her Yes stays
    assert z.bridge.posture()["switches"]["toolsmith:search"] is True          # and so does her switch
    assert z.bridge.open_build(BID) == 3                                        # the next build opens again
    z.bridge.end_build(BID)


def test_a_plan_yes_grants_its_tries_only_while_its_build_runs(state):
    z = zone(state)
    z.bridge.open()
    rec = _plan_yes(z)
    subject = f"pkg:{build_target(BID)}"
    assert _build_grants(z) == [] and not _allowed(z, subject, "pypi.org", "install")
    assert z.bridge.open_build("20261022-210000-999999", plan_consent=None) == 0   # another build: not its grants
    z.bridge.end_build("20261022-210000-999999")
    assert z.bridge.open_build(BID, plan_consent=rec["consent_id"]) == 2
    assert _allowed(z, subject, "pypi.org", "install")
    z.bridge.end_build(BID)
    assert _build_grants(z) == [] and subject not in z.bridge.posture()["switches"]


def test_her_switch_off_beats_a_build(state):
    z = zone(state)
    _search_yes(z)
    z.bridge.set_switch("toolsmith:search", False)                 # wizard step 4 answered "closed" later
    assert z.bridge.open_build(BID) == 0 and _build_grants(z) == []
    assert z.bridge.posture()["switches"]["toolsmith:search"] is False      # never turned back on
    z.bridge.end_build(BID)


def test_one_build_ending_keeps_the_other_builds_grants(state):
    z = zone(state)
    _search_yes(z)
    other = "20261022-220000-123456"
    rec = _plan_yes(z, other)
    z.bridge.open_build(BID)
    z.bridge.open_build(other, plan_consent=rec["consent_id"])
    z.bridge.end_build(BID)
    assert _allowed(z, "toolsmith:search", "github.com") and _allowed(z, f"pkg:{build_target(other)}", "pypi.org",
                                                                      "install")
    z.bridge.end_build(other)
    assert _build_grants(z) == []


def test_a_yes_during_a_build_grants_at_once(state):
    z = zone(state)
    z.bridge.open()
    z.bridge.open_build(BID)
    _plan_yes(z)                                                     # her Yes while this build already runs
    assert _allowed(z, f"pkg:{build_target(BID)}", "github.com", "install")
    z.bridge.end_build(BID)
    assert _build_grants(z) == []


def test_close_stale_drops_build_and_install_grants_only(state):
    z = zone(state)
    _search_yes(z)
    z.bridge.open_build(BID)
    grant(z, manifest("freshtable", hosts=["api.example.com"], install_hosts=["pypi.org"]))
    assert _allowed(z, "pkg:freshtable", "pypi.org", "install")
    assert z.bridge.close_stale() >= 4
    assert _build_grants(z) == [] and not _allowed(z, "pkg:freshtable", "pypi.org", "install")
    assert _allowed(z, "pkg:freshtable", "api.example.com", "run")             # her run grant stays
    assert z.bridge.builds_running() == []


# -- 2. every way a build ends, through Toolsmith's host --------------------------------------------
class EndingRunner:
    """Toolsmith's process, ending the way *how* says: ``done`` (exit 0, a build that finished, said not
    yet or gave up all exit 0), ``error`` (exit 1), ``no_start`` (it never started), ``running`` (until
    stopped). Records the Bridge's build grants while it runs."""

    def __init__(self, z, how):
        self.z, self.how, self.seen, self.child = z, how, [], None

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.seen.append(_build_grants(self.z))
        if self.how == "no_start":
            raise RuntimeError("Windows could not start it")
        runner = self

        class Child:
            done = False

            def wait(self, timeout=None):
                return self.done

            def stop(self):
                if not self.done:
                    self.done = True
                    on_exit(None)
        child = self.child = Child()
        if self.how in ("done", "error"):
            child.done = True
            on_exit(0 if self.how == "done" else 1)
        return child


@pytest.fixture
def wired(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    app = create_app(state)
    core = wire_core(app)
    yield app, core, core.zone3
    core.zone3.stop()


def _host_build(app, core, z, how):
    ports = Ports(core.state, registry=core.registry, consent=core.consent, bus=core.bus,
                  zone=lambda: app.extensions.get("puffpai.zone3"))
    runner = EndingRunner(z, how)
    host = ToolsmithHost(core.state, ports, core.registry, core.tokens, runner=runner, sleep=lambda s: None)
    b = host.store.create("daily news", [{"name": "Daily headlines", "does": "Shows the news"}], host.settings())
    host.agent_plan(b["id"], {"jobs": [{"name": "Daily headlines", "plan": {"candidates": [
        {"name": "Hello Page", "full_name": "example/hello-page", "runtime": "python"}]}}]})
    cid = host.store.read(b["id"])["consent"]["id"]
    rec = core.consent.get(cid)
    core.consent.answer(cid, "yes", "tap", by="owner", card_sha=rec["card_sha"])
    assert _build_grants(z) == []                       # her plan Yes alone opens nothing yet
    return host, runner, b["id"]


@pytest.mark.parametrize("how", ["done", "error", "no_start"])
def test_build_grants_close_however_the_process_ends(wired, how):
    app, core, z = wired
    _search_yes(z)
    host, runner, build_id = _host_build(app, core, z, how)
    try:
        host.start(build_id, host.store.read(build_id)["consent"]["id"])
    except Exception:  # noqa: BLE001 - a process that can't start: the build says so
        pass
    subjects = {g["subject"] for g in runner.seen[0]}
    assert subjects == {"toolsmith:search", f"pkg:{build_target(build_id)}"}   # open while it ran
    assert _build_grants(z) == [] and z.bridge.builds_running() == []        # closed when it ended


def test_build_grants_close_when_the_core_stops_toolsmith(wired):
    app, core, z = wired
    _search_yes(z)
    host, runner, build_id = _host_build(app, core, z, "running")
    host.start(build_id, host.store.read(build_id)["consent"]["id"])
    assert _build_grants(z) and z.bridge.builds_running() == [build_id]
    host.stop(timeout=1)                                 # stop_core stops Toolsmith first (D18)
    assert _build_grants(z) == [] and z.bridge.builds_running() == []


def test_stop_core_and_start_core_close_what_is_left(state):
    """A stop with a build's grants still on the policy (its process never told the Core), then a start."""
    from puffpai_core.api import create_app
    from puffpai_core.api.app import start_core, stop_core
    app = create_app(state)
    core = app.extensions["puffpai"]
    core.app, core.proxy_port = app, 0
    terminal = start_core(app)
    try:
        _search_yes(core.zone3)
        core.zone3.bridge.open_build(BID)
        assert _build_grants(core.zone3)
    finally:
        stop_core(core, terminal)
    assert _build_grants(core.zone3) == []


CHILD = textwrap.dedent('''
    import sys, time
    from puffpai_core import anchor, machine_file
    from puffpai_core.zone1 import machine_key

    class FakeMachine:                      # never real DPAPI (the suite's own rule)
        def available(self): return True
        def protect(self, data): return b"fake-wrap:" + bytes(b ^ 0x5A for b in data)
        def unprotect(self, data):
            if not data.startswith(b"fake-wrap:"): raise ValueError("not a fake wrap")
            return bytes(b ^ 0x5A for b in data[len(b"fake-wrap:"):])

    def refuse(*a, **k): raise AssertionError("real DPAPI")
    anchor._dpapi = refuse
    anchor.default_machine = FakeMachine
    machine_key.protect = machine_key.unprotect = machine_file._wrap_key = refuse
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    app = create_app(sys.argv[1])
    z = wire_core(app).zone3
    z.bridge.set_switch("toolsmith:search", True)
    z.bridge.open()
    card = z.consents.ask("host", "toolsmith:search", {"hosts": ["api.github.com", "github.com"], "phase": "build"})
    z.consents.answer(card["consent_id"], "yes", "tap", by="owner (setup)", card_sha=card["card_sha"])
    z.bridge.open_build("20261022-210000-abcdef")
    m = {"schema": "puffpai-package/1", "id": "freshtable", "kind": "app", "name": "Freshtable", "version": "1",
         "description": "x", "needs": {"hosts": ["api.example.com"], "install_hosts": ["pypi.org"]}}
    c = z.bridge.package_card(m)
    z.consents.answer(c["consent_id"], "yes", "tap", card_sha=c["card_sha"])   # an install that never ends
    print("READY", len(z.bridge.posture()["grants"]), flush=True)
    time.sleep(600)
''')


def test_a_hard_kill_leaves_grants_that_the_next_start_closes(state, tmp_path):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import start_core, stop_core
    script = tmp_path / "child_core.py"
    script.write_text(CHILD, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-I", str(script), str(state)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline()
        assert line.startswith("READY"), (line, proc.stderr.read() if proc.poll() is not None else "")
        assert int(line.split()[1]) == 4                 # 2 search, 1 run, 1 install
    finally:
        proc.kill()                                      # TerminateProcess on our own child: a hard kill
        proc.wait(30)
    left = json.loads((state / "settings" / "bridge" / "policy.json").read_text(encoding="utf-8"))["body"]
    left = json.loads(left) if isinstance(left, str) else left
    assert any(g["phase"] == "build" for g in left["grants"]) and any(g["phase"] == "install" for g in left["grants"])
    app = create_app(state)
    core = app.extensions["puffpai"]
    core.app, core.proxy_port = app, 0
    terminal = start_core(app)
    try:
        grants = core.zone3.bridge.posture()["grants"]
        assert [(g["subject"], g["host"], g["phase"]) for g in grants] == [("pkg:freshtable", "api.example.com", "run")]
    finally:
        stop_core(core, terminal)


# -- 2. install grants close when the install ends ---------------------------------------------------
def _with_install_host(src):
    m = json.loads((src / "puffpai.json").read_text(encoding="utf-8"))
    m["needs"] = {"hosts": [], "install_hosts": ["pypi.org"]}
    (src / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")


@pytest.mark.parametrize("fails", [False, True])
def test_install_grants_close_when_the_install_ends(core, hello_copy, monkeypatch, fails):
    from conftest import yes
    from puffpai_core.api.app import wire_core
    from puffpai_core.steward.steward import StewardError
    wire_core(core.app)
    core.packages.start_after_install = False
    z = core.zone3
    z.bridge.open()
    _with_install_host(hello_copy)
    during = []
    real = core.steward.setup

    def setup(id, expect_tree=None):
        during.append(_allowed(z, f"pkg:{id}", "pypi.org", "install"))
        if fails:
            raise StewardError("setup failed on purpose")
        return real(id, expect_tree=expect_tree)
    monkeypatch.setattr(core.steward, "setup", setup)
    staged = core.packages.add(str(hello_copy))
    got = yes(core, staged, timeout_s=60)
    assert got["status"] == ("failed" if fails else "installed"), got
    assert during == [True]                                    # open while it installed
    assert not _allowed(z, "pkg:hello-tool", "pypi.org", "install")
    assert [g for g in z.bridge.posture()["grants"] if g["phase"] == "install"] == []


# -- 2. one source for the posture: GET /v1/settings and GET /v1/bridge (puffpai bridge status) -------
def test_settings_and_bridge_status_agree(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    app = create_app(state)
    core = wire_core(app)
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    c = app.test_client()

    def both():
        s, b = c.get("/v1/settings", headers=owner).json, c.get("/v1/bridge", headers=owner).json
        return s["bridge"], b["state"], s["bridge_search"]
    assert both() == ("closed", "closed", False)
    core.bridge.set_posture("search")                    # the wizard's step 4 answer, as the wizard stores it
    # Only Toolsmith's build-time search: closed, with the search line (lane I6b: open only when something
    # can actually go out), and both places say so
    assert both() == ("closed", "closed", True)
    assert _build_grants(core.zone3) == []               # her answer opens nothing until a build runs
    core.bridge.set_posture("closed")
    assert both() == ("closed", "closed", False)
    assert not (state / "settings" / "bridge.json").exists()   # no second copy that could disagree
    core.zone3.stop()


def test_cli_bridge_status_reads_the_same_view(monkeypatch, capsys):
    from puffpai_core import cli
    view = {"state": "open", "switches": {"toolsmith:search": True}, "grants": [], "safe_mode": {"on": False}}
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "t"})
    monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: (200, view))
    assert cli._cmd_bridge(type("A", (), {"action": "status"})()) == 0
    out = capsys.readouterr().out
    # Only Toolsmith's build-time search, and nothing let through: it reads as closed (lane I4a)
    # A posture with no hosts: it can't be told which places her Yes names, so it never claims any
    assert out.strip() == "The Bridge is closed. While Toolsmith builds, it may search the places you said Yes to; nothing else goes out."


# -- 1. Drawbridge's hold during a try: ONE card, the build waits, Yes goes on, No moves on ----------
# Lane I4b: a held download is set aside first; the fully checked candidates are tried before any card, and
# only then ONE card per job lists what was set aside. Every I3b guarantee is kept (tests below).
class HeldPorts(MainPorts):
    """Two candidates on GitHub; *held* ones ship a file Drawbridge can't read (a .bson: a kind no list names,
    so it asks in both of the owner's settings, D95; a raw .bin of weights passes under Normal)."""

    held = {"example/hello-page", "example/news-board"}
    turns: list

    def fetch_repo(self, url, dest):
        super().fetch_repo(url, dest)
        full = url.split("github.com/", 1)[-1].removesuffix(".git")
        if full in self.held:
            (Path(dest) / "data").mkdir(exist_ok=True)
            (Path(dest) / "data" / "blob.bson").write_bytes(bytes(range(256)))
        return ""

    def http_get(self, url, *, subject="toolsmith:search", accept="", max_bytes=2_000_000):
        if url.startswith("https://api.github.com/search/repositories"):
            return 200, {}, json.dumps({"items": [
                {"full_name": "example/news-board", "name": "news-board", "description": "A news headlines board",
                 "stargazers_count": 300, "language": "HTML", "topics": ["news"], "default_branch": "main",
                 "license": {"spdx_id": "MIT"}},
                {"full_name": "example/hello-page", "name": "hello-page", "description": "A tiny news headlines board",
                 "stargazers_count": 120, "language": "HTML", "topics": ["news"], "default_branch": "main",
                 "license": {"spdx_id": "MIT"}}][: self.count]}).encode()
        if re.match(r"https://raw\.githubusercontent\.com/example/[\w-]+/main/README\.md$", url):
            return 200, {}, README.encode()
        return 404, {}, b""

    def local_turn(self, system, messages, tools):
        port = re.search(r"Your port: (\d+)", messages[0]["content"][0]["text"]).group(1)
        n = sum(1 for m in messages if m["role"] == "assistant") + 1
        self.turns.append(n)
        steps = {1: tool("download"),
                 2: tool("run", program="python", args=["-m", "venv", "--without-pip", ".venv"]),
                 3: tool("start_server", program="python", args=["-m", "http.server", port, "--bind", "127.0.0.1"]),
                 4: tool("wait_for_page", url=f"http://127.0.0.1:{port}/"),
                 5: tool("done", url=f"http://127.0.0.1:{port}/")}
        got = steps.get(n, {"type": "text", "text": "ok"})
        return {"content": [got], "stop": "tool_use" if got.get("type") == "tool_use" else "end_turn"}


@pytest.fixture
def held_core(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    _fast(state)
    app = create_app(state)
    core = wire_core(app)
    ports = HeldPorts(state, registry=core.registry, checklist=app.extensions.get("puffpai.checklist"),
                      consent=core.consent, bus=core.bus, zone=lambda: app.extensions.get("puffpai.zone3"),
                      packages=core.packages, steward=core.steward, caretaker=core.caretaker)
    ports.turns, ports.count = [], 1
    host = ToolsmithHost(state, ports, core.registry, core.tokens, runner=InlineRunner(app), sleep=lambda s: None)
    app.extensions["puffpai_toolsmith"] = host
    try:
        yield app, core, host, ports
    finally:
        host.stop(timeout=10)
        core.steward.stop_all(remember=False)
        core.zone3.stop()


def _hold_cards(core, status=None):
    return [r for r in core.consent.list(status=status) if isinstance((r.get("card") or {}).get("try_holds"), list)]


def _run_build(host, core, on_hold):
    """Plan, her plan Yes, the build. *on_hold(rec)* answers each hold card ("yes", "no", or ("yes", [refs])
    for her ticks, recorded by the Core's owner route first); any other card she would have to answer is
    recorded in ``extra`` (and said Yes to, so the build can end)."""
    b = host.plan("daily news headlines", [{"name": "Daily headlines", "does": "Shows news headlines",
                                           "sample_ask": "What are the headlines today?"}])
    cid = b["consent"]["id"]
    rec = core.consent.get(cid)
    core.consent.answer(cid, "yes", "tap", by="owner", card_sha=rec["card_sha"])
    stop, extra, answered = threading.Event(), [], []

    def her():
        while not stop.is_set():
            for r in core.consent.list(status="pending"):
                card = r.get("card") or {}
                if isinstance(card.get("try_holds"), list):
                    word = on_hold(r)
                    if isinstance(word, tuple):
                        word, refs = word
                        host.trials.tick_holds(card["build"], r["consent_id"], refs, by="owner")
                    answered.append((tuple(h["name"] for h in card["try_holds"]), word))
                    if word is not None:                  # None: the test answered it itself
                        core.consent.answer(r["consent_id"], word,
                                            card_sha=r["card_sha"] if word == "yes" else None, by="owner")
                elif r["scope"]["kind"] == "package" and not (card.get("plan") and not card.get("outside_plan")):
                    extra.append(r)
                    core.consent.answer(r["consent_id"], "yes", card_sha=r["card_sha"], by="owner")
            time.sleep(0.05)
    th = threading.Thread(target=her, daemon=True)
    th.start()
    try:
        done = host.start(b["id"], cid)
    finally:
        stop.set()
        th.join(timeout=5)
    return done, extra, answered


def test_a_held_download_gets_one_card_and_nothing_runs_before_her_yes(held_core, state):
    app, core, host, ports = held_core
    seen = {}

    def on_hold(rec):
        from conftest import wait_for
        b = host.store.all()[-1]
        said = lambda: any("couldn't check" in ln and "one card" in ln  # noqa: E731
                           for ln in host.job(b["jobs"][0]["id"])["run"].get("lines") or [])
        wait_for(said, timeout_s=10, every_s=0.05)        # the job's line comes a moment after the card
        job = host.job(b["jobs"][0]["id"])
        seen.update(status=job["status"], line=job["run"].get("line"), lines=list(job["run"].get("lines") or []),
                    turns=list(ports.turns),
                    installed=core.registry.get("news-board"), folder=(state / "packages" / "news-board").exists(),
                    staged=list((state / "packages" / ".staging").glob("*/data/blob.bson")),
                    grants={g["subject"] for g in _build_grants(core.zone3)},
                    view=app.test_client().get("/v1/consent?status=pending", headers={
                        "Authorization": "Bearer " + core.tokens.issue_owner_key()}).json)
        return "yes", ["github:example/news-board"]
    done, extra, answered = _run_build(host, core, on_hold)
    job = done["jobs"][0]
    # While it waited: the job said so, only "download" had been asked for, nothing installed, renamed or kept.
    assert seen["status"].startswith("waiting_you"), seen
    assert "couldn't check" in seen["line"] and seen["turns"] == [1]
    # D95: the line names the file by its base name and kind, never its path.
    assert any("blob.bson" in ln and "data/" not in ln and "set aside" in ln for ln in seen["lines"]), seen["lines"]
    assert seen["installed"] is None and seen["folder"] is False and seen["staged"] == []
    lines = " ".join(seen["view"][0]["lines"])
    assert "Drawbridge: 1 file of a kind Drawbridge can't read: blob.bson." in lines
    assert "Your Yes lets Toolsmith try it. Nothing from it runs before that." in lines
    assert "got this app working" not in lines and "No: Toolsmith tries none of them" in lines
    assert "A Yes with nothing ticked tries none of them." in lines
    # Her Yes went on with this same candidate (downloaded again, the same files): it works, and its install
    # needed no second card.
    assert answered == [(("news-board",), "yes")] and extra == []
    assert job["status"] == "working", job["run"]
    assert core.registry.get("news-board")["status"] == "running"
    holds = _hold_cards(core)
    assert len(holds) == 1 and holds[0]["status"] == "granted"
    install = [r for r in core.consent.list() if r["scope"] == {"kind": "package", "target": "news-board"}]
    assert len(install) == 1 and install[0]["by"] == f"plan:{done['consent']['id']}"   # covered: no card for her
    # The build's grants were open while it ran, and closed when it ended.
    assert f"pkg:{build_target(done['id'])}" in seen["grants"]
    assert _build_grants(core.zone3) == []


def test_a_fully_checked_candidate_goes_first_and_no_card_is_raised(held_core, state):
    """Lane I4b: the held one (most stars) is set aside, the fully checked one works: no card at all."""
    app, core, host, ports = held_core
    ports.count = 2
    ports.held = {"example/news-board"}
    done, extra, answered = _run_build(host, core, lambda rec: "no")
    job = done["jobs"][0]
    assert answered == [] and extra == [] and _hold_cards(core) == []
    assert job["status"] == "working", job["run"]
    assert core.registry.get("news-board") is None and not (state / "packages" / "news-board").exists()
    assert core.registry.get("hello-page")["status"] == "running"
    assert not list((state / "packages" / ".staging").glob("*/data/blob.bson"))    # its download is gone
    assert _build_grants(core.zone3) == []


def test_her_no_moves_on_and_ends_the_job(held_core, state):
    app, core, host, ports = held_core
    ports.count = 2                                         # both held
    done, extra, answered = _run_build(host, core, lambda rec: "no")
    job = done["jobs"][0]
    assert answered == [(("news-board", "hello-page"), "no")] and extra == []        # ONE card for the job
    assert job["status"].startswith("not_yet"), job["run"]
    tried = job["run"].get("tried") or []
    assert [t["name"] for t in tried] == ["news-board", "hello-page"]
    assert all(t["why"] == "you said No to trying it" for t in tried), tried
    assert "news-board: news-board" not in job["run"]["line"]                       # each name once
    assert not {r["id"] for r in core.registry.list()} & {"news-board", "hello-page"}
    assert [r["status"] for r in _hold_cards(core)] == ["declined"]
    assert not list((state / "packages" / ".staging").glob("*/data/blob.bson"))
    assert _build_grants(core.zone3) == []


def test_no_answer_to_any_hold_ends_as_not_yet_with_every_grant_closed(held_core, state):
    app, core, host, ports = held_core
    ports.count = 2
    done, extra, answered = _run_build(host, core, lambda rec: "no")
    job = done["jobs"][0]
    assert len(answered) == 1 and extra == []                                     # one card, not one per app
    assert job["status"].startswith("not_yet"), job["run"]
    assert not {r["id"] for r in core.registry.list()} & {"news-board", "hello-page"}
    assert _build_grants(core.zone3) == []


def test_her_ticks_pick_which_set_aside_apps_are_tried(held_core, state):
    app, core, host, ports = held_core
    ports.count = 2
    done, extra, answered = _run_build(host, core, lambda rec: ("yes", ["github:example/hello-page"]))
    job = done["jobs"][0]
    assert answered == [(("news-board", "hello-page"), "yes")] and extra == []
    assert job["status"] == "working", job["run"]
    assert core.registry.get("hello-page")["status"] == "running" and core.registry.get("news-board") is None
    tried = job["run"].get("tried") or []
    assert {"name": "news-board", "ref": "github:example/news-board", "why": "you did not tick it"} in tried
    b = host.store.read(done["id"])
    assert list(b["hold_ticks"].values()) == [["github:example/hello-page"]]       # the Core's record


def test_a_yes_goes_on_only_with_the_same_files(held_core, state):
    """Downloaded again after her Yes: a held file that changed since her card means it is not used."""
    app, core, host, ports = held_core
    real = ports.fetch_repo
    fetched = []

    def changing(url, dest):
        real(url, dest)
        fetched.append(url)
        if len(fetched) > 1:
            (Path(dest) / "data" / "blob.bson").write_bytes(b"\x00other bytes")
        return ""
    ports.fetch_repo = changing
    done, extra, answered = _run_build(host, core, lambda rec: ("yes", ["github:example/news-board"]))
    job = done["jobs"][0]
    assert answered == [(("news-board",), "yes")] and len(fetched) == 2
    assert job["status"].startswith("not_yet"), job["run"]
    assert "changed after Drawbridge's check" in json.dumps(job["run"]["tried"])
    assert core.registry.get("news-board") is None


def test_ticks_cant_change_after_her_answer(held_core, state):
    app, core, host, ports = held_core
    ports.count = 2
    refused = []

    def on_hold(rec):
        core.consent.answer(rec["consent_id"], "yes", card_sha=rec["card_sha"], by="owner")
        try:
            host.trials.tick_holds(rec["card"]["build"], rec["consent_id"], ["github:example/hello-page"])
        except PermissionError as exc:
            refused.append(str(exc))
        return None
    done, extra, answered = _run_build(host, core, on_hold)
    assert refused and "already answered" in refused[0]
    assert "hold_ticks" not in host.store.read(done["id"])
    job = done["jobs"][0]                                          # a bare Yes (no ticks): it tries none
    assert job["status"].startswith("not_yet"), job["run"]
    assert {t["why"] for t in job["run"]["tried"]} == {"you said Yes but ticked none, so Toolsmith tries none of them"}
    assert not {r["id"] for r in core.registry.list()} & {"news-board", "hello-page"}


def test_a_bare_yes_with_no_ticks_tries_none(held_core, state):
    app, core, host, ports = held_core
    ports.count = 2
    done, extra, answered = _run_build(host, core, lambda rec: "yes")
    job = done["jobs"][0]
    assert answered == [(("news-board", "hello-page"), "yes")] and extra == []
    assert job["status"].startswith("not_yet") and ports.turns.count(1) == 2       # only the two first downloads
    assert "ticked none" in job["run"]["line"]
    assert not {r["id"] for r in core.registry.list()} & {"news-board", "hello-page"}


def test_a_toolsmith_token_cant_answer_or_tick_a_hold_card(held_core, state):
    app, core, host, ports = held_core
    cards = {}

    def on_hold(rec):
        b = host.store.all()[-1]
        # A Toolsmith token (for another build: a new one for this build would end its running Toolsmith,
        # whose old token stops working). The routes refuse the role itself.
        tok = core.tokens.issue_toolsmith("20261023-000000-000000")
        c = app.test_client()
        hdr = {"Authorization": "Bearer " + tok}
        cards["answer"] = c.post(f"/v1/consent/{rec['consent_id']}/answer", headers=hdr,
                                 json={"answer": "yes", "card_sha": rec["card_sha"]}).status_code
        cards["tick"] = c.put(f"/v1/toolsmith/builds/{b['id']}/holds/{rec['consent_id']}", headers=hdr,
                              json={"try": ["github:example/news-board"]}).status_code
        cards["status"] = core.consent.get(rec["consent_id"])["status"]
        return "no"
    done, extra, answered = _run_build(host, core, on_hold)
    assert cards["answer"] in (401, 403) and cards["tick"] in (401, 403), cards
    assert cards["status"] == "pending"
    assert "hold_ticks" not in host.store.read(done["id"])


def test_a_block_still_has_no_card(held_core, state, monkeypatch):
    """Drawbridge's block is never a Yes card (09 section 3, point 4)."""
    app, core, host, ports = held_core
    real = ports.fetch_repo

    def loader(url, dest):
        real(url, dest)
        blob = base64.b64encode(os.urandom(6000)).decode()
        (Path(dest) / "page.html").write_text(f"<html><script>var b='{blob}';document.write(atob(b));</script></html>",
                                              encoding="utf-8")
        return ""
    monkeypatch.setattr(ports, "fetch_repo", loader)
    done, extra, answered = _run_build(host, core, lambda rec: "yes")
    assert answered == [] and extra == [] and _hold_cards(core) == []
    job = done["jobs"][0]
    assert job["status"].startswith("not_yet") and "hides encoded code" in json.dumps(job["run"]), job["run"]


# -- 1. the install's own check: the same held files are covered, a changed one is not ----------------
def test_plan_cover_takes_a_try_hold_yes_only_for_the_same_files(tmp_path):
    from puffpai_core.packages import plan_cover
    from puffpai_core.zone3.drawbridge import held_fingerprints
    folder = tmp_path / "app"
    (folder / "data").mkdir(parents=True)
    (folder / "data" / "blob.bson").write_bytes(b"\x00\x01")
    files = held_fingerprints(folder, ["data/blob.bson"])
    row = {"code": "cant_check", "result": "note", "files": ["data/blob.bson"], "all": ["data/blob.bson"],
           "cut": False, "why": "some of its files could not be checked"}
    checked = {"verdict": "hold", "reasons": ["cant_check"], "rows": [row]}
    assert plan_cover.held_covered(files, checked, folder)
    (folder / "data" / "blob.bson").write_bytes(b"\x00\x02")                 # changed after her Yes
    assert not plan_cover.held_covered(files, checked, folder)
    (folder / "data" / "blob.bson").write_bytes(b"\x00\x01")
    more = dict(row, all=["data/blob.bson", "data/other.bson"])
    assert not plan_cover.held_covered(files, dict(checked, rows=[more]), folder)   # a file she never saw
    assert not plan_cover.held_covered(files, dict(checked, rows=[dict(row, cut=True)]), folder)
    assert not plan_cover.held_covered(files, dict(checked, reasons=["cant_check", "raw_reader"]), folder)
    noted = dict(checked, rows=[row, {"code": "hosts_not_listed", "result": "note", "why": "api.example.com"}])
    assert plan_cover.other_notes(noted) == ["api.example.com"]


def test_plan_cover_reads_her_hold_yes_from_the_store_itself(state):
    from puffpai_core.consent import ConsentStore
    from puffpai_core.packages import plan_cover
    store = ConsentStore(state / "consent.db")
    files = {"data/blob.bson": "b" * 64}
    card = {"title": "t", "name": "Hello", "tree_sha": "c" * 64,
            "try_hold": {"candidate": "github:example/hello-page", "held_sha": plan_cover.held_sha(files)}}
    cid = store.request({"kind": "package", "target": "toolsmith-build-abcdef.hello-page"}, card, False)
    plan = {"candidate": "github:example/hello-page", "held": {"consent_id": cid, "files": files}}
    assert plan_cover.held_record(store, plan) is None                      # no answer yet
    store.answer(cid, "yes", card_sha=store.get(cid)["card_sha"], by="owner")
    assert plan_cover.held_record(store, plan) == files
    assert plan_cover.held_record(store, dict(plan, candidate="github:example/other")) is None
    assert plan_cover.held_record(store, dict(plan, held={"consent_id": cid, "files": {"x": "y"}})) is None


@pytest.mark.parametrize("picks, working", [(["github:example/hello-page"], "hello-page"), (None, None)])
def test_the_wizard_sends_her_picks_before_its_yes(held_core, state, picks, working):
    """The wizard's adapter (WizardToolsmith.answer_card): her picks are recorded by the Core, then the Yes.
    No picks sent with the Yes: none is tried, and the job says so."""
    from puffpai_core.wiring import WizardToolsmith
    app, core, host, ports = held_core
    ports.count = 2
    said = []

    def on_hold(rec):
        said.append(WizardToolsmith(app).answer_card(rec["card"]["build"], rec["consent_id"], True, picks))
        return None
    done, extra, answered = _run_build(host, core, on_hold)
    job = done["jobs"][0]
    assert said == [""] and extra == []
    assert core.consent.get(answered and _hold_cards(core)[0]["consent_id"])["status"] == "granted"
    if working:
        assert job["status"] == "working" and core.registry.get(working)["status"] == "running"
        assert list(host.store.read(done["id"])["hold_ticks"].values()) == [picks]
    else:
        assert job["status"].startswith("not_yet") and "ticked none" in job["run"]["line"]
        assert "hold_ticks" not in host.store.read(done["id"])
