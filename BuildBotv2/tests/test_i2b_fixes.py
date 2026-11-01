"""Integration lane I2b, verifier fixes: Toolsmith paused in safe mode, check item 6 when the watch can't
see, opening the Bridge in safe mode (the PIN), Zone 3's card listener once, and the one safe-mode fallback."""

import inspect
import sys
import time
from pathlib import Path

import pytest

from conftest import wait_for
from toolsmith_fakes import FIXTURES, World, make_venv, script

PY = Path(sys.executable).as_posix()
REASON = "A test reason."
PAUSED = f"BuildBot is in safe mode: {REASON} Restart BuildBot to leave it."


@pytest.fixture
def core(core):
    from puffpai_core.api.app import wire_core
    wire_core(core)
    return core


@pytest.fixture
def started(core, drawbridge):
    from puffpai_core.api.app import start_core, stop_core
    terminal = start_core(core)
    yield core
    stop_core(core, terminal)


@pytest.fixture
def world(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.fixture_repo = FIXTURES / "toolsmith" / "hello-page"
    yield w
    w.host.stop(timeout=10)


def _vault(core):
    from puffpai_core.zone1 import archivist
    from z1kit import PIN
    a = archivist.for_state(core.state, core.bus)
    a.setup(PIN)
    a.unlock("pin", PIN)
    return a, PIN


def _titles(core):
    return [n["title"] for n in core.notices.list()]


class Recording:
    """A runner that records every launch and starts nothing."""

    def __init__(self):
        self.launched = []

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.launched.append((do, build_id))
        raise AssertionError("Toolsmith's process must not start in safe mode")


# -- 1. Toolsmith is paused in safe mode: every entry point refuses ------------------------------------
def _agent_args(fn, build_id):
    values = {"build_id": build_id, "try_id": "t1", "job_id": f"{build_id}-1", "pkg_id": "notes",
              "step_id": "s1", "body": {}}
    return [values[p] for p in inspect.signature(fn).parameters]


def test_paused_toolsmith_refuses_plan_start_launch_and_every_agent_route(world):
    from puffpai_core.zone3.toolsmith_host.service import Paused
    host = world.host
    b = world.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    assert host.pause(REASON) == "Toolsmith is paused"
    with pytest.raises(Paused) as e:
        host.plan("make me a notes app")
    assert str(e.value) == PAUSED
    with pytest.raises(Paused):
        host.start(b["id"], "c_test")
    with pytest.raises(Paused):
        host._launch("build", b["id"])
    assert world.runner.runs == []                                       # no process ever started
    names = sorted(n for n in dir(host) if n.startswith("agent_") and callable(getattr(host, n)))
    # + agent_try_stop (lane I2a), agent_try_fs (D60), agent_now, agent_fits (lane I3a), agent_holds_ask and
    # agent_holds_state (lane I4b), agent_knowledge (lane I11), agent_queries (lane I15), agent_library (lane I19e),
    # agent_knowledge_find (D99), agent_found and agent_read (D121), agent_try_mcp (D124)
    assert len(names) == 35
    for name in names:
        fn = getattr(host, name)
        with pytest.raises(Paused) as e:
            fn(*_agent_args(fn, b["id"]))
        assert str(e.value) == PAUSED, name
    assert "\u2014" not in PAUSED


def test_every_agent_route_and_the_owner_routes_answer_409_in_safe_mode(world):
    import re
    app = world.app
    b = world.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    world.host.pause(REASON)
    c = app.test_client()
    agent = {"Authorization": "Bearer " + world.core.tokens.issue_toolsmith(b["id"])}
    rules = [r for r in app.url_map.iter_rules() if r.rule.startswith("/v1/toolsmith/agent")]
    assert len(rules) >= 22
    for rule in rules:
        path = re.sub(r"<[^>]+>", "x1", rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            r = c.open(path, method=method, json={}, headers=agent)
            assert r.status_code == 409 and r.json["kind"] == "refused", (method, path, r.status_code, r.json)
            assert r.json["error"] == PAUSED
    owner = {"Authorization": "Bearer " + world.core.tokens.issue_owner_key()}
    r = c.post("/v1/toolsmith/plan", json={"goal": "make me a notes app"}, headers=owner)
    assert r.status_code == 409 and r.json["error"] == PAUSED and r.json["safe_mode"] is True
    r = c.post(f"/v1/toolsmith/builds/{b['id']}/start", json={"consent_id": "c_test"}, headers=owner)
    assert r.status_code == 409 and r.json["error"] == PAUSED
    assert world.runner.runs == []


def test_after_caretaker_safe_mode_plan_never_launches(started):
    core = started
    host = core.app.extensions["puffpai_toolsmith"]
    host.runner = Recording()
    core.caretaker.safe_mode("x")
    from puffpai_core.zone3.toolsmith_host.service import Paused
    with pytest.raises(Paused) as e:
        host.plan("make me a notes app")
    assert str(e.value) == "BuildBot is in safe mode: x. Restart BuildBot to leave it."
    assert host.runner.launched == []


def test_a_host_made_after_safe_mode_began_is_paused_too(started):
    from puffpai_core.zone3.toolsmith_host import ToolsmithHost
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    from puffpai_core.zone3.toolsmith_host.service import Paused
    core = started
    core.caretaker.safe_mode(REASON)
    fresh = ToolsmithHost(core.state, Ports(core.state, caretaker=core.caretaker), core.registry, core.tokens,
                          runner=Recording())
    with pytest.raises(Paused):
        fresh.plan("make me a notes app")


def test_a_caretaker_that_cant_say_counts_as_paused(world):
    from puffpai_core.zone3.toolsmith_host.service import Paused

    class Broken:
        @property
        def in_safe_mode(self):
            raise RuntimeError("no")
    world.ports.caretaker = Broken()
    with pytest.raises(Paused) as e:
        world.host.plan("make me a notes app")
    assert "could not tell" in str(e.value)


def test_pause_stops_running_tries_by_their_job_object_and_toolsmiths_process(world):
    from puffpai_core.steward.jobs import pid_alive
    host = world.host
    b = world.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = world.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    assert link.try_download(opened["try_id"])["ok"] and link.try_check(opened["try_id"])["ok"]
    make_venv(link, opened["try_id"])                       # the try's own Python runs its server (D61)
    got = script(link, opened["try_id"], "sleep.py", "import time\ntime.sleep(120)\n", start=True)
    assert got["ok"] and pid_alive(got["pid"])

    class Child:
        stopped = False

        def stop(self):
            Child.stopped = True

        def wait(self, timeout=None):
            return Child.stopped
    host._children[b["id"]] = Child()
    host.pause(REASON)
    assert Child.stopped                                                   # Toolsmith's own process
    assert wait_for(lambda: not pid_alive(got["pid"]), 15, 0.2)            # the try's server, by its job
    assert host.trials.of_build(b["id"]) == []


def test_hookguards_safe_mode_pauses_toolsmith_even_when_the_ports_break(world):
    from puffpai_core.zone3.toolsmith_host.service import Paused

    def broken(reason):
        raise RuntimeError("ports down")
    world.ports.safe_mode = broken
    with pytest.raises(RuntimeError):
        world.host.safe_mode("core_python_hook_changed")
    with pytest.raises(Paused) as e:
        world.host.plan("make me a notes app")
    assert "Python start-up files" in str(e.value)


# -- 2. check item 6: a blind or stopped watch can't PASS ----------------------------------------------
def _quiet_package(tmp_path):
    from test_i2b_seams import _caller_package
    return _caller_package(tmp_path, [], ["declared.example.com"])


def _item6(report):
    return next((s, w) for lv, i, s, w in report.items if (lv, i) == (1, 6))


def test_check_item_6_fails_when_steward_pids_raises_inside_the_watch(drawbridge, no_brain, tmp_path, monkeypatch):
    from puffpai_core.packages.check import FAIL, check
    from puffpai_core.zone3.bypass import BypassWatch
    real = BypassWatch.check_once
    seen = []

    def blind_once(self):
        steward = self.steward

        class Blind:
            def __getattr__(self, name):
                return getattr(steward, name)

            def pids(self, package_id):
                seen.append(package_id)
                raise OSError("its Job Object can't be read")
        self.steward = Blind()
        try:
            return real(self)
        finally:
            self.steward = steward
    monkeypatch.setattr(BypassWatch, "check_once", blind_once)
    status, words = _item6(check(_quiet_package(tmp_path), start_timeout_s=30))
    assert seen and status == FAIL
    assert "couldn't watch it, so this can't be checked" in words and "could not list its programs" in words


def test_check_item_6_fails_when_the_watch_is_not_running(drawbridge, no_brain, tmp_path, monkeypatch):
    from puffpai_core.packages.check import FAIL, check
    from puffpai_core.zone3.bypass import BypassWatch
    monkeypatch.setattr(BypassWatch, "check_once", lambda self: self._broken("no_tcp_table"))
    status, words = _item6(check(_quiet_package(tmp_path), start_timeout_s=30))
    assert status == FAIL and "couldn't watch it, so this can't be checked" in words


def test_check_item_6_still_passes_a_quiet_package_the_watch_could_see(drawbridge, no_brain, tmp_path):
    from puffpai_core.packages.check import PASS, check
    status, words = _item6(check(_quiet_package(tmp_path), start_timeout_s=30))
    assert status == PASS, words


# -- 3. opening the Bridge in safe mode needs the PIN ----------------------------------------------------
def _owner(core):
    return {"Authorization": "Bearer " + core.tokens.issue_owner_key()}


def test_in_safe_mode_the_bridge_opens_only_with_the_pin(started):
    core = started
    _a, pin = _vault(core)
    c = core.app.test_client()
    owner = _owner(core)
    assert c.post("/v1/bridge/open", headers=owner).json["setting"] == "open"   # normal: her everyday choice
    core.caretaker.safe_mode(REASON)
    assert core.zone3.bridge.posture()["state"] == "closed"
    view = c.get("/v1/bridge", headers=owner).json
    assert view["safe_mode"] == {"on": True, "reasons": [REASON]}
    r = c.post("/v1/bridge/open", headers=owner)
    assert r.status_code == 403 and r.json["needs_pin"] and r.json["code"] == "safe_mode_pin"
    assert r.json["error"].startswith(f"BuildBot is in safe mode: {REASON} Opening the Bridge now needs your PIN.")
    assert r.json["safe_mode"]["reasons"] == [REASON]
    r = c.post("/v1/bridge/open", headers=owner, json={"pin": "000000" if pin != "000000" else "111111"})
    assert r.status_code == 403 and REASON in r.json["error"]
    assert core.zone3.bridge.posture()["state"] == "closed"
    r = c.post("/v1/bridge/open", headers=owner, json={"pin": pin})
    assert r.status_code == 200 and r.json["setting"] == "open"
    assert core.caretaker.in_safe_mode                                         # safe mode lasts until restart
    assert "\u2014" not in view["safe_mode"]["reasons"][0]


def test_in_safe_mode_without_a_vault_the_bridge_stays_closed(started):
    core = started
    core.caretaker.safe_mode(REASON)
    r = core.app.test_client().post("/v1/bridge/open", headers=_owner(core), json={"pin": "123456"})
    assert r.status_code in (403, 503) and core.zone3.bridge.posture()["state"] == "closed"


def test_the_wizards_search_posture_is_refused_in_safe_mode(started):
    from puffpai_core.wiring import WizardBridge
    core = started
    core.caretaker.safe_mode(REASON)
    with pytest.raises(ValueError) as e:
        WizardBridge(core.zone3).set_posture("search")
    assert REASON in str(e.value)
    p = core.zone3.bridge.posture()
    assert p["state"] == "closed" and not p["switches"].get(WizardBridge.SEARCH)


def _cli_on(core, monkeypatch):
    from puffpai_core import cli
    device = core.tokens.issue_device("terminal test")
    c = core.app.test_client()
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 0, "token": device})

    def call(info, method, path, body=None):
        r = c.open(path, method=method, json=body, headers={"Authorization": f"Bearer {info['token']}"})
        return r.status_code, (r.get_json(silent=True) or {})
    monkeypatch.setattr(cli, "_call", call)
    return cli


def test_cli_bridge_open_shows_the_reason_first_then_asks_for_the_pin(started, monkeypatch, capsys):
    core = started
    _a, pin = _vault(core)
    cli = _cli_on(core, monkeypatch)
    core.caretaker.safe_mode(REASON)
    before_prompt = []

    def ask(prompt):
        before_prompt.append(capsys.readouterr().out)
        return pin
    monkeypatch.setattr(cli.getpass, "getpass", ask)
    assert cli.main(["bridge", "open"]) == 0
    assert "BuildBot is in safe mode:" in before_prompt[0] and REASON in before_prompt[0]
    assert "needs your PIN" in before_prompt[0]
    # Set open, but nothing is let through yet, so it says closed in the same words as status (lane I6b)
    assert "The Bridge is closed. Nothing leaves your computer without your consent." in capsys.readouterr().out
    assert core.zone3.bridge.posture()["state"] == "open"


def test_cli_bridge_open_without_the_pin_keeps_it_closed(started, monkeypatch, capsys):
    core = started
    _vault(core)
    cli = _cli_on(core, monkeypatch)
    core.caretaker.safe_mode(REASON)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "")
    assert cli.main(["bridge", "open"]) == 1
    assert "The Bridge stays closed." in capsys.readouterr().out
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "999999")
    assert cli.main(["bridge", "open"]) == 1
    assert core.zone3.bridge.posture()["state"] == "closed"
    assert cli.main(["bridge", "status"]) == 0
    out = capsys.readouterr().out
    assert "The Bridge is closed." in out and f"Safe mode: {REASON}" in out


def test_cli_bridge_open_outside_safe_mode_needs_no_pin(started, monkeypatch):
    core = started
    cli = _cli_on(core, monkeypatch)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: pytest.fail("no PIN outside safe mode"))
    assert cli.main(["bridge", "open"]) == 0 and core.zone3.bridge.posture()["state"] == "open"
    assert cli.main(["bridge", "close"]) == 0 and core.zone3.bridge.posture()["state"] == "closed"


# -- 5. Zone 3's card listener: once per Core --------------------------------------------------------------
def test_one_bypass_card_gives_one_notice_after_stop_and_start(core, drawbridge):
    from puffpai_core.api.app import start_core, stop_core
    core.zone3.cards.append({"kind": "watch_blind", "package": "x", "line": "Blind at start."})
    terminal = start_core(core)
    stop_core(core, terminal)
    terminal = start_core(core)
    try:
        core.zone3.cards.append({"kind": "bypass", "package": "x", "line": "Went around."})
        assert _titles(core).count("An app went around the Bridge") == 1
        assert _titles(core).count("BuildBot can't watch an app") == 1      # not routed again at the 2nd start
        assert len(core.zone3.cards.listeners) == 1
    finally:
        stop_core(core, terminal)


# -- 6. the one safe-mode fallback: the same effect, the same notice ----------------------------------------
def test_the_ports_fallback_has_caretakers_effect_and_notice(started, monkeypatch):
    from puffpai_core.caretaker.caretaker import SAFE_LINE
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    from puffpai_core.zone3.toolsmith_host.service import Paused
    core = started
    a, _pin = _vault(core)
    core.zone3.bridge.open(by="owner")
    host = core.app.extensions["puffpai_toolsmith"]
    assert host.ports.toolsmith is host

    def broken(reason):
        raise RuntimeError("caretaker broke")
    monkeypatch.setattr(core.caretaker, "safe_mode", broken)
    done = host.ports.safe_mode(REASON)
    assert done == ["the Bridge is closed", "the vault is locked", "Toolsmith is paused"]
    assert core.zone3.bridge.posture()["state"] == "closed" and not a.is_open()
    with pytest.raises(Paused):
        host.plan("make me a notes app")
    notes = [n for n in core.notices.list() if n["title"] == "BuildBot is in safe mode"]
    assert len(notes) == 1 and REASON in notes[0]["text"]
    head = SAFE_LINE.split("{why}")[0]
    assert notes[0]["text"].startswith(head) and "nothing new is added" not in notes[0]["text"]
    assert core.caretaker.cards[-1]["kind"] == "safe_mode"

    # with no Caretaker at all it still fails safe: the Bridge closes and the vault locks
    a.unlock("pin", _pin)
    core.zone3.bridge.open(by="owner")
    bare = Ports(core.state, zone=lambda: core.zone3, bus=core.bus)
    assert bare.safe_mode(REASON) == ["the Bridge is closed", "the vault is locked"]
    assert core.zone3.bridge.posture()["state"] == "closed" and not a.is_open()


def test_caretaker_and_the_fallback_run_the_same_steps(started):
    from puffpai_core import wiring
    core = started
    names = [f.__name__ for f in wiring.safe_steps(core, core.app)]
    from puffpai_core.caretaker.caretaker import safe_mode_steps
    same = [f.__name__ for f in safe_mode_steps(zone3=lambda: None, state=core.state, bus=core.bus,
                                                 toolsmith=lambda: None)]
    assert names == same == ["close_bridge", "lock_vault", "pause_toolsmith", "close_shells"]
    assert [f.__name__ for f in core.caretaker.safe_steps] == names
