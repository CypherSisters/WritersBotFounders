"""Integration lane I2a: the wizard's Toolsmith steps for real, in BOTH renderers (03 1.6).

Toolsmith's model (step 5), the goal on the local brain (6), the plan with the owner's four questions (7), the
ONE Yes (8), the build with its "Your turn" step (9) and prove (10) run on lane L6's real host, lane L1's
Package Manager, consent store and Steward, lane L4's Zone 3 (Drawbridge, HookGuard, toolsmith_zone) and the
Checklist. Recorded only: GitHub, the model's turns and the page reader (``MainPorts``), so nothing reaches
the internet and no real model runs. The adopted app installs under the plan's Yes (no new card)."""

import json
import threading

import pytest

from puffpai_core.api import create_app
from puffpai_core.api.app import wire_core
from puffpai_core.zone3.toolsmith import agent
from puffpai_core.zone3.toolsmith.link import Link
from puffpai_core.zone3.toolsmith_host import ToolsmithHost, sender
from test_toolsmith_main_e2e import MainPorts
from toolsmith_fakes import flask_transport
from wizard_kit import CONSENT, KEY, RENDERERS, BrowserRun, Kit, TerminalRun


class Ports(MainPorts):
    """MainPorts, and a pasted project's README read at HEAD (it has no default branch from a search)."""

    def http_get(self, url, **kw):
        return super().http_get(url.replace("/HEAD/", "/main/"), **kw)


class _Child:
    def __init__(self) -> None:
        self.done = threading.Event()

    def wait(self, timeout=None) -> bool:
        return self.done.wait(timeout)

    def stop(self) -> None:
        pass


class ThreadRunner:
    """Toolsmith's process body in a background thread (the real one is a child process), through the real
    Core API routes and auth gate, so the wizard's build step sees it run, pause and go on."""

    def __init__(self, app) -> None:
        self.app = app
        self.runs = []

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.runs.append((do, build_id))
        child = _Child()

        def go():
            code = 1
            try:
                code = agent.run({"do": do, "build": build_id, "cache": str(cache)},
                                 Link(flask_transport(self.app, token), build_id))
            except Exception:  # noqa: BLE001 - as a crashed process
                code = 1
            finally:
                on_exit(code)
                child.done.set()
        threading.Thread(target=go, daemon=True).start()
        return child


@pytest.fixture
def real(state):
    app = create_app(state)
    core = wire_core(app)
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "toolsmith.json").write_text(json.dumps({"timing": {"poll_s": 0.1}}), encoding="utf-8")
    ports = Ports(state, registry=core.registry, checklist=app.extensions.get("puffpai.checklist"),
                      consent=core.consent, bus=core.bus, zone=lambda: app.extensions.get("puffpai.zone3"),
                      packages=core.packages, steward=core.steward, caretaker=core.caretaker)
    host = ToolsmithHost(state, ports, core.registry, core.tokens, runner=ThreadRunner(app), sleep=lambda s: None)
    app.extensions["puffpai_toolsmith"] = host
    try:
        yield app, core, host
    finally:
        host.stop(timeout=10)
        core.steward.stop_all(remember=False)
        if app.extensions.get("puffpai.zone3") is not None:
            app.extensions["puffpai.zone3"].stop()


def _kit(state, core, **kw):
    kit = Kit(state, vault="open", **kw)
    kit.toolsmith = core.toolsmith                       # lane L6's real host, through the wiring
    return kit


@pytest.mark.parametrize("Run", RENDERERS, ids=lambda r: r.name)
def test_goal_plan_one_yes_build_your_turn_and_prove(real, state, Run):
    app, core, host = real
    run = Run(_kit(state, core, providers=False))
    assert run.step("brain", {"model": ""})["status"] == "done"
    assert run.step("toolsmith", {"where": "local"})["status"] == "done"
    assert json.loads((state / "settings" / "toolsmith.json").read_text(encoding="utf-8"))["where"] == "local"

    goal = run.step("goal", {"goal": "daily news headlines"})
    assert goal["status"] == "done" and goal["jobs"][0]["name"]
    build = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))["build"]

    plan = run.step("plan", {"project": ""})
    assert plan["status"] == "done", plan
    b = host.view(build)
    assert b["plan_sha"] and b["consent"]["id"]
    assert b["jobs"][0]["plan"]["candidates"][0]["full_name"] == "example/hello-page"
    shown = run.all_text()
    assert "Toolsmith finds and installs apps for 1 job" in shown and "try hello-page first" in shown
    assert "While it installs, Toolsmith reaches only: " in shown and "github.com" in shown

    yes = run.step("yes", {"yes": "yes"})
    assert yes["status"] == "done", yes
    rec = core.consent.get(b["consent"]["id"])
    assert rec["status"] == "granted" and rec["by"] == "owner"

    built = run.step("build", {"turn": "done"})
    assert built["status"] == "done", built
    text = run.all_text()
    assert "Your turn: Let Toolsmith reach outside hello-page's folder" in text     # one step, and she ticked it
    assert "installed under the plan you said Yes to" in text
    steps = app.extensions["puffpai.checklist"].list("toolsmith", include_closed=True)
    assert steps and all(s["state"] == "done" for s in steps)

    proved = run.step("prove", {})
    assert proved["status"] == "done" and "works" in " ".join(proved["lines"])
    row = core.registry.get("hello-page")
    assert row["status"] == "running" and core.steward.alive("hello-page")
    cards = [r for r in core.consent.list() if r["scope"] == {"kind": "package", "target": "hello-page"}]
    assert len(cards) == 1 and cards[0]["by"] == f"plan:{b['consent']['id']}"       # no new card for her
    # the manifest the Core wrote from Toolsmith's (an adopted app runs only from .buildbot/puffpai.json)
    manifest = json.loads((state / "packages" / "hello-page" / ".buildbot" / "puffpai.json")
                          .read_text(encoding="utf-8"))
    assert manifest["origin"] == "adopted:https://github.com/example/hello-page"
    assert manifest["ui"]["pages"][0]["show"] == "new_tab"


def test_a_pasted_project_goes_first_in_a_new_plan(real, state):
    app, core, host = real
    run = TerminalRun(_kit(state, core, providers=False))
    run.step("brain", {"model": ""})
    run.step("toolsmith", {"where": "local"})
    run.step("goal", {"goal": "daily news headlines"})
    first = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))["build"]
    plan = run.step("plan", {"project": "https://github.com/example/hello-page"})
    assert plan["status"] == "done"
    now = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))["build"]
    assert now != first
    cand = host.view(now)["jobs"][0]["plan"]["candidates"][0]
    assert cand["source"] == "pasted" and cand["full_name"] == "example/hello-page"


def test_no_plan_waits_and_the_next_open_plans_again(real, state, monkeypatch):
    app, core, host = real
    monkeypatch.setattr(agent, "plan", lambda link, cache: (_ for _ in ()).throw(OSError("down")))  # no plan comes
    eng = _kit(state, core, providers=False).engine()
    eng.submit("brain", {"model": ""})
    eng.submit("goal", {"goal": "daily news headlines"})
    first = eng.build_id()
    screen = eng.submit("plan", {"project": ""})
    assert screen["status"] == "waiting"
    assert eng.build_id() != first and host.view(eng.build_id())["plan_sha"] is None


def test_the_cloud_choice_goes_through_toolsmiths_own_check(real, state):
    app, core, host = real
    kit = _kit(state, core)
    eng = kit.engine()
    providers = core.toolsmith.providers()
    anthropic = next(p for p in providers if p["id"] == "anthropic")
    screen = eng.submit("toolsmith", {"where": "cloud", "provider": "anthropic"})
    assert screen["field"]["id"] == "consent" and screen["field"]["label"] == anthropic["consent"]
    screen = eng.submit("toolsmith", {"consent": "yes", "key": KEY, "cost_cap": "", "github": ""})
    assert screen["status"] == "done", screen
    st = host.settings()
    assert st["where"] == "cloud" and st["provider"] == "anthropic" and st["cost_cap_usd"] == 5
    assert st["consent"]["sha"] == sender.consent_sha(host.providers()["anthropic"])
    assert kit.vault.secrets == {"toolsmith.anthropic": KEY}                         # the vault, by the field's home
    assert CONSENT                                                                  # (the kit's sentence is v1's)


def test_a_card_for_what_was_not_on_the_plan_is_asked_in_the_build_step(real, state, monkeypatch):
    """A host seen while adopting that was not on the plan: the build step shows that card and her Yes adds it."""
    from puffpai_core.zone3.toolsmith import adopt
    real_manifest = adopt.manifest_for

    def with_host(t):
        m = real_manifest(t)
        m["needs"]["hosts"] = ["news.example.org"]
        return m
    monkeypatch.setattr(adopt, "manifest_for", with_host)
    app, core, host = real
    run = BrowserRun(_kit(state, core, providers=False))
    run.step("brain", {"model": ""})
    run.step("toolsmith", {"where": "local"})
    run.step("goal", {"goal": "daily news headlines"})
    run.step("plan", {"project": ""})
    run.step("yes", {"yes": "yes"})
    built = run.step("build", {"turn": "done", "card": True})
    assert built["status"] == "done", built
    text = run.all_text()
    assert "Not on the plan you said Yes to:" in text and "news.example.org" in text
    cards = [r for r in core.consent.list() if r["scope"] == {"kind": "package", "target": "hello-page"}]
    assert len(cards) == 1 and cards[0]["by"] == "owner" and cards[0]["status"] == "granted"
    assert core.registry.get("hello-page")["status"] == "running"
