"""Lane I6b: small rough edges from the third live fresh-install proof (10-23, main 138b0070).

1. The Bridge read "open... Nothing is let through right now." after her Yes on an adopted app that
   declared no hosts. Why: the app's card carried setup hosts (pypi.org and friends, ``adopt.INSTALL_HOSTS``),
   so its install grants turned ``pkg:<id>`` on; the install grants closed, the switch stayed on. Now the
   switch goes when nothing is left behind it, and the owner-facing reading (``Bridge.view``) says "open"
   only when something can actually go out. ``state``, ``shown``, ``/v1/settings`` ``bridge`` and
   ``puffpai bridge status`` are one reading.
2. A No on the plan card left the build on ``waiting_yes``. Now it ends: "You said No, so nothing was
   built.", Toolsmith's process for it ends, and its build grants close.
"""

import json

import pytest

from z3helpers import grant, manifest, zone
from puffpai_core.zone3.bridge import (CLOSED_LINE, SEARCH_BUILDING_LINE, SEARCH_ONLY_LINE, SEARCH_PLACES, build_target,
                                      is_build_grant)
from puffpai_core.zone3.policy import SEARCH_HOSTS as POLICY_SEARCH_HOSTS
from puffpai_core.zone3.toolsmith_host import ToolsmithHost
from puffpai_core.zone3.toolsmith_host import store as build_store
from puffpai_core.zone3.toolsmith_host.ports import Ports

SEARCH_HOSTS = list(POLICY_SEARCH_HOSTS)
NOTHING = "Nothing is let through right now."


def _search_yes(z):
    """Her wizard step 4 "search" choice, as WizardBridge records it."""
    z.bridge.set_switch("toolsmith:search", True)
    z.bridge.open()
    card = z.consents.ask("host", "toolsmith:search", {"hosts": SEARCH_HOSTS, "phase": "build"})
    return z.consents.answer(card["consent_id"], "yes", "tap", by="owner (setup)", card_sha=card["card_sha"])


def _agrees(view):
    """The view never contradicts itself (the proof had state open, shown closed)."""
    assert view["state"] == view["shown"] in ("open", "closed")
    text = " ".join(view["said"])
    if view["state"] == "open":
        assert view["said"][0].startswith("The Bridge is open") and NOTHING not in text
        assert view["live"] and view["setting"] == "open"
    else:
        assert "The Bridge is open" not in text and view["said"][0].startswith("The Bridge is closed.")
    return view


# -- 1a. why a Yes on an app with no hosts turned a switch on, and that it no longer stays on ---------------
def test_setup_hosts_open_only_while_it_installs_and_leave_no_switch(state):
    z = zone(state)
    _search_yes(z)
    m = manifest("flask-web-app-tutorial", install_hosts=["github.com", "pypi.org", "files.pythonhosted.org"])
    grant(z, m)
    during = _agrees(z.bridge.view())
    assert during["state"] == "open"                                     # true while it installs
    assert "Let through now: flask-web-app-tutorial (while it installs)." in during["said"]
    assert z.bridge.posture()["switches"].get("pkg:flask-web-app-tutorial") is True
    z.bridge.end_install("flask-web-app-tutorial")
    after = _agrees(z.bridge.view())
    assert "pkg:flask-web-app-tutorial" not in after["switches"]          # nothing behind it: gone
    assert after["state"] == "closed" and after["said"] == [SEARCH_ONLY_LINE] and after["search"] is True
    assert not z.bridge.allowed("pkg:flask-web-app-tutorial", "pypi.org", phase="install").allowed


def test_close_stale_forgets_a_package_switch_left_on_with_nothing_behind_it(state):
    """The proof's policy (``pkg:flask-web-app-tutorial`` on, no grants) reads closed after the next start."""
    z = zone(state)
    _search_yes(z)
    grant(z, manifest("freshtable", hosts=["api.example.com"]))          # a real run grant: stays
    z.bridge.set_switch("pkg:flask-web-app-tutorial", True)              # left on, nothing behind it
    z.bridge.set_switch("pkg:quiet", False)                              # her own Off: kept as she set it
    z.bridge.close_stale()
    switches = z.bridge.posture()["switches"]
    assert "pkg:flask-web-app-tutorial" not in switches
    assert switches["pkg:quiet"] is False and switches["pkg:freshtable"] is True
    assert switches["toolsmith:search"] is True                         # build switches wait for a build
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed


def test_a_real_install_with_only_setup_hosts_reads_closed_everywhere(core, hello_copy, monkeypatch, capsys):
    """Through the Package Manager: her Yes, the install, then /v1/bridge, /v1/settings and the CLI."""
    from conftest import yes
    from puffpai_core import cli
    from puffpai_core.api.app import wire_core
    wire_core(core.app)
    core.packages.start_after_install = False
    z = core.zone3
    _search_yes(z)
    m = json.loads((hello_copy / "puffpai.json").read_text(encoding="utf-8"))
    m["needs"] = {"hosts": [], "install_hosts": ["pypi.org", "files.pythonhosted.org"]}
    (hello_copy / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    got = yes(core, core.packages.add(str(hello_copy)), timeout_s=60)
    assert got["status"] == "installed", got
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    c = core.app.test_client()
    view = _agrees(c.get("/v1/bridge", headers=owner).json)
    settings = c.get("/v1/settings", headers=owner).json
    assert "pkg:hello-tool" not in view["switches"]
    assert view["state"] == view["shown"] == settings["bridge"] == "closed"
    assert view["said"] == settings["bridge_said"] == [SEARCH_ONLY_LINE] and settings["bridge_search"] is True
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "t"})
    monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: (200, view))
    assert cli.main(["bridge", "status"]) == 0
    assert capsys.readouterr().out.strip() == SEARCH_ONLY_LINE


# -- 1b. one reading: open only when something can actually go out -------------------------------------
def _cloud_yes(z):
    card = z.consents.ask("cloud", "toolsmith:cloud", {"hosts": ["api.anthropic.com"], "phase": "build"})
    return z.consents.answer(card["consent_id"], "yes", "tap", by="owner", card_sha=card["card_sha"])


def test_the_bridge_reads_open_only_when_something_can_go_out(state):
    z = zone(state)
    v = _agrees(z.bridge.view())
    assert (v["state"], v["setting"], v["said"]) == ("closed", "closed", [CLOSED_LINE])
    z.bridge.open()                                                      # set open, nothing granted
    v = _agrees(z.bridge.view())
    assert (v["state"], v["setting"], v["said"]) == ("closed", "open", [CLOSED_LINE])
    z.bridge.set_switch("pkg:bare", True)                                # a switch with nothing behind it
    assert _agrees(z.bridge.view())["state"] == "closed"
    _search_yes(z)                                                       # build-time search only
    v = _agrees(z.bridge.view())
    assert (v["state"], v["said"], v["search"]) == ("closed", [SEARCH_ONLY_LINE], True)
    rec = grant(z, manifest("freshtable", hosts=["api.example.com"]))     # a run grant: something goes out
    v = _agrees(z.bridge.view({"freshtable": "FreshTable"}))
    assert v["state"] == "open" and v["said"] == ["The Bridge is open.", f"While Toolsmith builds, it may search {SEARCH_PLACES}.",
                                                  "Let through now: FreshTable (while it runs)."]
    z.bridge.set_switch("pkg:freshtable", False)                         # her Off beats the grant
    assert _agrees(z.bridge.view())["state"] == "closed"
    z.bridge.set_switch("pkg:freshtable", True)
    assert _agrees(z.bridge.view())["state"] == "open"
    z.bridge.close()                                                     # closed: the grant can't be used
    v = _agrees(z.bridge.view())
    assert (v["state"], v["setting"], v["live"], v["said"]) == ("closed", "closed", [], [CLOSED_LINE])
    z.bridge.open()
    assert z.consents.revoke(rec["consent_id"])
    assert _agrees(z.bridge.view())["state"] == "closed"                 # the Yes behind it is gone


def test_the_search_reads_open_only_while_a_build_really_uses_it(state):
    z = zone(state)
    _search_yes(z)
    before = _agrees(z.bridge.view())
    assert (before["state"], before["said"]) == ("closed", [SEARCH_ONLY_LINE])        # no build runs
    assert not z.bridge.allowed("toolsmith:search", "api.github.com", phase="build").allowed
    z.bridge.open_build("20261023-120000-abc123")                                    # Toolsmith's process starts
    during = _agrees(z.bridge.view())
    assert z.bridge.allowed("toolsmith:search", "api.github.com", phase="build").allowed
    assert (during["state"], during["shown"], during["said"]) == ("open", "open", [SEARCH_BUILDING_LINE])
    z.bridge.end_build("20261023-120000-abc123")                                     # it ended
    after = _agrees(z.bridge.view())
    assert (after["state"], after["said"]) == ("closed", [SEARCH_ONLY_LINE])


def test_routes_settings_and_the_cli_agree_while_a_build_searches(state, monkeypatch, capsys):
    from puffpai_core import cli
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    app = create_app(state)
    core = wire_core(app)
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    c = app.test_client()
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "t"})

    def all_four():
        b, s = c.get("/v1/bridge", headers=owner).json, c.get("/v1/settings", headers=owner).json
        monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: (200, b))
        capsys.readouterr()
        assert cli.main(["bridge", "status"]) == 0
        out = capsys.readouterr().out.strip().splitlines()
        assert b["state"] == b["shown"] == s["bridge"] and b["said"] == s["bridge_said"] == out
        return b["state"], out
    try:
        core.bridge.set_posture("search")
        assert all_four() == ("closed", [SEARCH_ONLY_LINE])
        core.zone3.bridge.open_build("20261023-120000-abc123")
        assert all_four() == ("open", [SEARCH_BUILDING_LINE])
        core.zone3.bridge.end_build("20261023-120000-abc123")
        assert all_four() == ("closed", [SEARCH_ONLY_LINE])
    finally:
        core.zone3.stop()


def test_a_cloud_yes_reads_open_with_its_own_line_and_no_empty_list(state):
    z = zone(state)
    z.bridge.open()
    _cloud_yes(z)
    v = _agrees(z.bridge.view())
    assert v["said"] == ["The Bridge is open.", "While Toolsmith builds, it may use the cloud model you picked."]


def test_a_damaged_or_missing_policy_reads_closed(state):
    z = zone(state)
    grant(z, manifest("freshtable", hosts=["api.example.com"]))
    assert _agrees(z.bridge.view())["state"] == "open"
    path = state / "settings" / "bridge" / "policy.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["body"]["switches"]["pkg:other"] = {"on": True, "at": "x"}         # edited outside PuffPAI
    path.write_text(json.dumps(raw), encoding="utf-8")
    v = _agrees(z.bridge.view())
    assert (v["state"], v["setting"], v["live"]) == ("closed", "closed", [])
    path.unlink()                                                        # gone, on a bot that wrote one
    v = _agrees(zone(state).bridge.view())
    assert (v["state"], v["setting"], v["live"]) == ("closed", "closed", [])


def test_a_provider_block_only_narrows(state):
    """Providers can't grant: a block never makes the Bridge read open, and blocked hosts stay refused."""
    z = zone(state)
    z.bridge.open()
    z.bridge.policy.add_block("api.example.com", "host", "provider:blocklist", "test")
    assert _agrees(z.bridge.view())["state"] == "closed"
    grant(z, manifest("freshtable", hosts=["api.example.com"]))
    assert not z.bridge.allowed("pkg:freshtable", "api.example.com").allowed


def test_routes_settings_and_the_cli_say_one_thing(state, monkeypatch, capsys):
    from puffpai_core import cli
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    app = create_app(state)
    core = wire_core(app)
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    c = app.test_client()
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "t"})

    def all_four():
        b, s = c.get("/v1/bridge", headers=owner).json, c.get("/v1/settings", headers=owner).json
        _agrees(b)
        monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: (200, b))
        capsys.readouterr()
        assert cli.main(["bridge", "status"]) == 0
        out = capsys.readouterr().out.strip().splitlines()
        assert b["state"] == b["shown"] == s["bridge"] and b["said"] == s["bridge_said"] == out
        return b["state"]
    try:
        assert all_four() == "closed"
        opened = c.post("/v1/bridge/open", headers=owner).json            # set open, nothing granted
        assert opened["setting"] == "open" and opened["state"] == opened["shown"] == "closed"
        assert all_four() == "closed"
        core.bridge.set_posture("search")
        assert all_four() == "closed"
        grant(core.zone3, manifest("freshtable", hosts=["api.example.com"]))
        assert all_four() == "open"
        closed = c.post("/v1/bridge/close", headers=owner).json
        assert closed["state"] == closed["setting"] == "closed"
        assert all_four() == "closed"
    finally:
        core.zone3.stop()


def test_cli_open_with_nothing_granted_says_closed_in_the_status_words(state, monkeypatch, capsys):
    from puffpai_core import cli
    got = {"state": "closed", "shown": "closed", "setting": "open", "said": [CLOSED_LINE]}
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "t"})
    monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None:
                        (200, {"safe_mode": {"on": False}} if method == "GET" else got))
    assert cli.main(["bridge", "open"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == CLOSED_LINE and "The Bridge is open" not in out


# -- 2. a No on the plan card ends the build ---------------------------------------------------------
class RunningRunner:
    """Toolsmith's process: runs until stopped. Records its token and the build grants while it ran."""

    def __init__(self, z):
        self.z, self.children, self.tokens, self.seen = z, [], [], []

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.tokens.append(token)
        self.seen.append([g for g in self.z.bridge.posture()["grants"] if is_build_grant(g)])

        class Child:
            done = False

            def wait(self, timeout=None):
                return self.done

            def stop(self):
                if not self.done:
                    self.done = True
                    on_exit(None)
        child = Child()
        self.children.append(child)
        return child


@pytest.fixture
def wired(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    app = create_app(state)
    core = wire_core(app)
    yield app, core, core.zone3
    core.zone3.stop()


def _planned(app, core, z, *, process_running):
    ports = Ports(core.state, registry=core.registry, consent=core.consent, bus=core.bus,
                  zone=lambda: app.extensions.get("puffpai.zone3"))
    runner = RunningRunner(z)
    host = ToolsmithHost(core.state, ports, core.registry, core.tokens, runner=runner, sleep=lambda s: None)
    b = host.store.create("world news", [{"name": "World news dashboard", "does": "Shows world news"}],
                          host.settings())
    if process_running:
        host._launch("plan", b["id"])                  # its plan process, still running when she answers
    host.agent_plan(b["id"], {"jobs": [{"name": "World news dashboard", "plan": {"candidates": [
        {"name": "Hello Page", "full_name": "example/hello-page", "runtime": "python"}]}}]})
    cid = host.store.read(b["id"])["consent"]["id"]
    assert [j["status"] for j in host.view(b["id"])["jobs"]] == ["waiting_yes"]
    return host, runner, b["id"], cid


def _build_grants(z):
    return [g for g in z.bridge.posture()["grants"] if is_build_grant(g)]


@pytest.mark.parametrize("process_running", [True, False])
def test_a_no_on_the_plan_card_ends_the_build(wired, process_running):
    app, core, z = wired
    _search_yes(z)
    host, runner, build_id, cid = _planned(app, core, z, process_running=process_running)
    if process_running:
        assert _build_grants(z) and z.bridge.builds_running() == [build_id]
    core.consent.answer(cid, "no", "tap", by="owner")
    b = host.view(build_id)
    assert [(j["status"], j["run"]["line"]) for j in b["jobs"]] == [("said_no", "You said No, so nothing was built.")]
    assert b["consent"]["said"] == build_store.SAID_NO_LINE and not b.get("yes")
    assert _build_grants(z) == [] and z.bridge.builds_running() == []          # every build grant closed
    assert f"pkg:{build_target(build_id)}" not in z.bridge.posture()["switches"]
    if process_running:
        assert runner.children[0].done                                       # Toolsmith's process ended
        assert core.tokens.lookup(runner.tokens[0]) is None                   # and its token went
        assert not host.running(build_id)
    with pytest.raises(PermissionError):
        host.start(build_id, cid)                                            # nothing starts after a No
    from puffpai_core.wizard.lines import job_result_lines, prove_lines
    from puffpai_core.wiring import job_result
    results = [job_result(j) for j in b["jobs"]]
    assert prove_lines(results) == ["World news dashboard: stopped at the plan. You said No, so nothing was built."]
    assert job_result_lines(results) == prove_lines(results)


def test_a_plan_card_with_no_answer_ends_the_build_too(wired):
    app, core, z = wired
    host, _runner, build_id, cid = _planned(app, core, z, process_running=False)
    assert core.consent.expire(cid, "test")
    assert [(j["status"], j["run"]["line"]) for j in host.view(build_id)["jobs"]] == [
        ("no_answer", "The plan's Yes card got no answer in time, so nothing was built.")]


def test_a_yes_still_starts_and_another_cards_no_changes_nothing(wired):
    app, core, z = wired
    host, runner, build_id, cid = _planned(app, core, z, process_running=False)
    other = core.consent.ask("package", "freshtable", {"hosts": []})
    core.consent.answer(other["consent_id"], "no", "tap", by="owner")         # not this build's card
    assert [j["status"] for j in host.view(build_id)["jobs"]] == ["waiting_yes"]
    rec = core.consent.get(cid)
    core.consent.answer(cid, "yes", "tap", by="owner", card_sha=rec["card_sha"])
    host.start(build_id, cid)
    assert [j["status"] for j in host.view(build_id)["jobs"]] == ["planned"]
    host.stop(timeout=1)


def test_toolsmith_cant_say_she_said_no(state):
    """Only the Core writes the no-Yes ends, from her answer: Toolsmith's status route refuses them."""
    builds = build_store.Builds(state)
    b = builds.create("x", [{"name": "A job", "does": "x"}], {"where": "local"})
    for word in build_store.NO_YES_ENDS:
        with pytest.raises(ValueError):
            builds.job_update(b["id"], b["id"] + "-0", {"state": word})


# -- 3. Ctrl+C in a window started by Start BuildBot.cmd: no "Terminate batch job (Y/N)?" --------------
import os  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
windows_only = pytest.mark.skipif(os.name != "nt", reason="cmd.exe and Ctrl+C in a Windows console")
WRITE_LAUNCHER = r'''
$ErrorActionPreference = "Stop"
$text = Get-Content -LiteralPath $args[0] -Raw
$m = [regex]::Match($text, '(?ms)^\$Launcher = .*?^Set-Content -LiteralPath \$Launcher[^\r\n]*')
if (-not $m.Success) { throw "install.ps1 has no launcher block" }
$RepoDir = $args[1]
$Name = $args[2]
Invoke-Expression $m.Value
'''


def _launcher_folder(tmp_path):
    r"""A clone's folder as install.ps1 leaves it: ``Start BuildBot.cmd`` written by install.ps1's own step 4
    (run by PowerShell), and ``.venv\Scripts\puffpai.exe``, a real console launcher of the kind pip makes,
    here running ``console_core`` (the test suite's guards) on this worktree's Python."""
    from pip._vendor.distlib.scripts import ScriptMaker
    repo = tmp_path / "Build Bot"                         # a space in the path, as in many real folders
    scripts = repo / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    maker = ScriptMaker(None, str(scripts))
    maker.executable, maker.variants = sys.executable, {""}
    maker.make("puffpai = console_core:main")
    assert (scripts / "puffpai.exe").is_file()
    ps1 = tmp_path / "write_launcher.ps1"
    ps1.write_text(WRITE_LAUNCHER, encoding="utf-8")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(ps1), str(REPO / "install.ps1"), str(repo), "testbot"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    return repo, repo / "Start BuildBot.cmd"


def _free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    assert not set(ports) & {8420, 8421, 8422}
    return ports


def _launcher_env(tmp_path, **more):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "PUFFPAI_"))}
    env.update(LOCALAPPDATA=str(tmp_path / "lad"), PYTHONUNBUFFERED="1", PIP_REQUIRE_VIRTUALENV="1",
               PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
               PYTHONPATH=os.pathsep.join([str(REPO / "core"), str(REPO / "tests")]), **more)
    return env


@windows_only
def test_the_start_window_opens_the_chat_and_quit_leaves_buildbot_running(tmp_path):
    """D94: Start BuildBot.cmd runs ``puffpai start``: BuildBot in its own window, then the chat here, reading her
    keys from this console. /quit ends the chat with 0, no pause and no batch question; BuildBot keeps running
    in its own window (the test's job ends it afterwards). A real Ctrl+C key at the prompt is not covered: the
    test's GenerateConsoleCtrlEvent does not end a pending console read the way a key press does (lane
    term-chat's note)."""
    import console_kit
    repo, launcher = _launcher_folder(tmp_path)
    text = launcher.read_text(encoding="ascii")
    assert "testbot" in text and "Terminate" not in text
    ports = _free_ports(3)
    env = _launcher_env(tmp_path, PUFFPAI_TEST_PORTS=",".join(map(str, ports)))
    got = console_kit.run([os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"), "/c", str(launcher)], "none",
                          env, "Type to chat.", tmp_path / "console", screen=True, cwd=str(repo),
                          settle_s=2.0, wait_s=30.0, keys="/quit\r")
    screen = got["output"]
    assert got["ready"] and got.get("typed"), screen
    assert got["exit"] == 0 and got["stop_s"] < 10, (got["exit"], got["stop_s"], screen)    # the chat ended
    assert got["left"], got                                        # BuildBot still runs, in its own window
    assert "Starting BuildBot..." in screen
    assert f"Your dashboard: http://puffpai.localhost:{ports[0]}. Type /dashboard to open it." in screen
    assert "Bye. BuildBot keeps running." in screen
    assert "Terminate batch job" not in screen and "Press any key" not in screen, screen
    assert (tmp_path / "lad" / "PuffPAI" / "testbot").is_dir()    # the bot the launcher names, in the test's folder


@windows_only
@pytest.mark.parametrize("code", ["1", "-1073741819"], ids=["one", "crash-0xC0000005"])
def test_a_buildbot_that_does_not_start_keeps_the_window_open_to_read(tmp_path, code):
    """D94: serve ends with an error (a crash code below zero too) in its own window, which pauses there; Start
    says so plainly, names that window, and pauses too (still no batch question)."""
    import console_kit
    repo, launcher = _launcher_folder(tmp_path)
    ports = _free_ports(3)
    env = _launcher_env(tmp_path, PUFFPAI_TEST_EXIT=code, PUFFPAI_TEST_PORTS=",".join(map(str, ports)),
                        PUFFPAI_TEST_START_WAIT="3")
    got = console_kit.run([os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"), "/c", str(launcher)], "none",
                          env, "Press any key", tmp_path / "console", screen=True, cwd=str(repo),
                          settle_s=1.0, wait_s=3.0)
    screen = got["output"]
    assert got["ready"] and got["exit"] is None, screen            # waiting on her key, not gone
    assert "BuildBot did not start within 3 seconds." in screen, screen
    assert '"BuildBot (Puff) is running. Close this window to stop it."' in screen.replace("\n", ""), screen
    assert "Terminate batch job" not in screen and "Type to chat." not in screen, screen


# -- 4. wizard and package wording ------------------------------------------------------------------
def test_the_pin_prompt_says_the_minimum_length_the_vault_checks(state):
    from wizard_kit import Kit
    from puffpai_core.wiring import WizardVault
    screen = Kit(state).engine().submit("vault", {"where": "here"})
    assert screen["field"]["id"] == "pin" and screen["field"]["label"] == \
        "Pick a PIN for your vault, at least 8 characters"
    assert "at least 8 characters" in WizardVault.check_pin("482913")          # the vault's own rule says 8 too
    assert WizardVault.check_pin("48291375") == ""
    other = state.parent / "other-bot"                                       # a fresh wizard, not the one above
    bot = Kit(other)
    bot.vault._status = "locked"
    opening = bot.engine().submit("vault", {"where": "existing", "folder": str(other)})
    assert opening["field"]["id"] == "pin" and opening["field"]["label"] == "Type your vault's PIN"


@pytest.mark.parametrize("renderer", ["terminal", "browser"])
def test_the_data_folder_prompt_shows_the_exact_folder_enter_uses(state, renderer):
    from wizard_kit import CODE, Kit
    kit = Kit(state)
    eng = kit.engine()
    answers = {"where": "here", "pin": "482913", "pin_again": "482913", "recovery_confirm": CODE}
    screen = eng.submit("vault", {"where": "here"}, renderer=renderer)
    for _ in range(6):                                   # one screen at a time, never an endless loop
        fid = (screen.get("field") or {}).get("id")
        if fid in ("data_folder", None) or fid not in answers:
            break
        screen = eng.submit("vault", {fid: answers[fid]}, renderer=renderer)
    field = screen["field"]
    exact = str(state / "home" / "PuffPAI Data" / state.name)
    assert field["id"] == "data_folder" and field["default"] == exact
    assert exact in field["hint"]
    assert "example_line" not in field and "%USERPROFILE%" not in json.dumps(field)


def test_setup_says_the_same_status_words_as_puffpai_list(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    from puffpai_core.cli import status_word
    from puffpai_core.wizard import terminal as term_mod
    core = wire_core(create_app(state))
    core.packages.start_after_install = False
    try:
        out = []
        done = term_mod.builtins(core, lambda prompt: "y", out.append)
        assert done == list(term_mod.BUILTINS)
        added = [line.strip() for line in out if line.strip().startswith("Added ")]
        want = [f"Added {core.registry.get(pid)['name']} ({status_word(core.registry.get(pid), core.registry)})."
                for pid in done]
        assert added == want
        for name in ("BuildBot setup", "Puff"):
            line = next((a for a in added if a.startswith(f"Added {name} (")), None)
            assert line == f"Added {name} (ready).", added          # static: nothing to start, as list says
    finally:
        core.steward.stop_all(remember=False)
        core.zone3.stop()
