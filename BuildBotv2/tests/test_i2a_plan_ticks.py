"""Lane I2a part B (D60): the plan's Yes covers only the setup steps the owner ticked during this build.

The Core records her tick itself (``Checklist.on_done`` -> the Toolsmith host -> ``build.json`` ``ticked``),
with the step's fingerprint. An adopted app's setup step is inside the plan only when that exact step was
ticked; anything else gets a card for just that difference. A Drawbridge "warn" and a step that uses a
changing tool (D57) always get a card, ticked or not."""

from types import SimpleNamespace

import pytest

from conftest import drawbridge_double
from puffpai_core.packages import plan_cover
from puffpai_core.zone3.toolsmith_host import ToolsmithHost
from puffpai_core.zone3.toolsmith_host.ports import Ports
from test_i2a_plan_cover import URL, _adopted, _plan, quiet  # noqa: F401 - the shared fixture and helpers
from toolsmith_fakes import InlineRunner

ACCOUNT = {"id": "ts-account", "kind": "confirm", "title": "Make your notes account",
           "why": "The app shows notes only after you sign up.", "text": "Open the sign-up page and sign up."}
RUN = {"id": "ts-run-1a2b", "kind": "run_command", "title": "Run node for Notes",
       "why": "It runs as you.", "text": "node --version --small"}


def _ticked(*steps) -> dict:
    return {s["id"]: {"step": "step_x", "sha": plan_cover.step_sha(s), "by": "owner"} for s in steps}


def _add(core, folder, ticked=None, **manifest):
    _adopted(folder, **manifest)
    plan = _plan(core)
    if ticked is not None:
        plan["ticked"] = ticked
    return core.packages.add(str(folder), adopted=URL, plan=plan)


def test_an_unticked_setup_step_gets_a_card(quiet, drawbridge, hello_copy):
    staged = _add(quiet, hello_copy, setup_steps=[ACCOUNT])                 # she never ticked it
    assert "covered_by" not in staged
    assert quiet.consent.get(staged["consent_id"])["status"] == "pending"
    assert staged["card"]["outside_plan"] == [
        "It has setup steps you did not tick Done during this build: Make your notes account."]


def test_a_ticked_setup_step_is_covered_by_the_plan(quiet, drawbridge, hello_copy):
    staged = _add(quiet, hello_copy, ticked=_ticked(ACCOUNT, RUN), setup_steps=[ACCOUNT, RUN])
    assert staged.get("covered_by"), staged["card"].get("outside_plan")
    assert quiet.packages.wait(staged["consent_id"], 60)["status"] == "installed"


@pytest.mark.parametrize("changed", [
    {"text": "npm --version --small"},                                                  # another program
    {"text": "node --version --all"},                                                  # other arguments
    {"shell": "cmd"},                                                                  # another shell
    {"admin": True},
])
def test_a_ticked_step_whose_command_changed_gets_a_card(quiet, drawbridge, hello_copy, changed):
    staged = _add(quiet, hello_copy, ticked=_ticked(RUN), setup_steps=[{**RUN, **changed}])
    assert "covered_by" not in staged
    lines = staged["card"]["outside_plan"]
    assert any(("not the ones you ticked" in x or "administrator steps" in x) and "Run node" in x
               for x in lines), lines


def test_another_builds_or_toolsmiths_word_for_a_tick_is_not_a_tick(quiet, drawbridge, hello_copy):
    staged = _add(quiet, hello_copy, ticked={"ts-account": {"sha": "0" * 64}}, setup_steps=[ACCOUNT])
    assert "covered_by" not in staged
    assert any("not the ones you ticked" in x for x in staged["card"]["outside_plan"])


def test_a_drawbridge_warn_gets_a_card_even_when_the_step_was_ticked(quiet, monkeypatch, hello_copy):
    drawbridge_double(monkeypatch, lambda p, m: {
        "verdict": "warn", "reasons": ["hosts_not_listed"], "line": "safe to add, with something to know first",
        "rows": [{"check": "hosts_vs_code", "result": "note", "code": "hosts_not_listed", "why": "cdn.example.org"}]})
    staged = _add(quiet, hello_copy, ticked=_ticked(ACCOUNT), setup_steps=[ACCOUNT])
    assert "covered_by" not in staged
    assert staged["card"]["outside_plan"] == [
        "Drawbridge found something to know first, and your plan's Yes did not cover it: cdn.example.org."]


def test_a_ticked_step_that_uses_a_changing_tool_gets_a_card(quiet, drawbridge, hello_copy):
    step = {**ACCOUNT, "text": "Open the app and press wipe_notes once to start clean."}
    staged = _add(quiet, hello_copy, ticked=_ticked(step), setup_steps=[step],
                  abilities={"changes": ["wipe_notes"]})
    assert "covered_by" not in staged
    lines = staged["card"]["outside_plan"]
    assert "Some setup steps use tools that change things: Make your notes account (wipe_notes)." in lines
    # D57's one rule, not only the manifest's list: an adopted app's unlabelled tool counts as changing.
    other = plan_cover.difference({"origin": "adopted:x", "abilities": {"examples": {"sync_all": ["x"]}},
                                   "setup_steps": [{**ACCOUNT, "text": "Run sync_all."}]},
                                  {"jobs": [], "setup": []}, "", {"verdict": "allow"},
                                  ticked=_ticked({**ACCOUNT, "text": "Run sync_all."}))
    assert any("sync_all" in x and "use tools" in x for x in other), other


# -- the record of her tick: written by the Core, from the Checklist's own row ------------------------------

@pytest.fixture
def host_and_build(core, state):
    app = core.app
    checklist = app.extensions["puffpai.checklist"]
    ports = Ports(state, registry=core.registry, checklist=checklist, consent=core.consent, bus=core.bus)
    host = ToolsmithHost(state, ports, core.registry, core.tokens, runner=InlineRunner(app), sleep=lambda s: None)
    b = host.store.create("a notes app", [{"name": "Notes"}], {"where": "local"})
    job_id = f"{b['id']}-0"
    host.store.change(b["id"], lambda x: x.update(
        planning=False, yes={"given": True}, jobs=[{"id": job_id, "n": 0, "name": "Notes", "plan": {"candidates": []},
                                                     "run": {"state": "building", "lines": [], "tried": []}}]))
    try:
        yield host, checklist, b["id"], job_id
    finally:
        host.stop(timeout=10)


def _raise(host, build_id, job_id, step):
    return host.agent_step_add(build_id, {"job_id": job_id, "step": dict(step)})["id"]


def test_her_done_writes_the_tick_and_its_fingerprint_into_the_build(host_and_build):
    host, checklist, build_id, job_id = host_and_build
    raw = {**ACCOUNT, "title": "Make your   notes account"}        # as Toolsmith wrote it (the Core tidies it)
    sid = _raise(host, build_id, job_id, raw)
    assert "ticked" not in (host.store.read(build_id) or {})
    ok, _row, _said = checklist.done(sid, by="owner")
    assert ok
    mark = host.store.read(build_id)["ticked"]["ts-account"]
    assert mark["step"] == sid and mark["by"] == "owner"
    assert mark["sha"] == plan_cover.step_sha(raw) == plan_cover.step_sha(ACCOUNT)   # the same step in a manifest
    run_sid = _raise(host, build_id, job_id, RUN)                     # the Core adds its own shell itself
    checklist.done(run_sid, by="owner")
    assert host.store.read(build_id)["ticked"]["ts-run-1a2b"]["sha"] == plan_cover.step_sha(RUN)
    assert host.store.read(build_id)["ticked"]["ts-run-1a2b"]["sha"] != plan_cover.step_sha(
        {**RUN, "text": RUN["text"].replace("--small", "--all")})


def test_only_her_done_on_a_toolsmith_build_step_is_a_tick(host_and_build):
    host, checklist, build_id, job_id = host_and_build
    sid = _raise(host, build_id, job_id, ACCOUNT)
    checklist.skip(sid, by="owner")                                    # skipped is not done
    other = checklist.add({**ACCOUNT, "id": "ts-other"}, "toolsmith", [f"job:{job_id}"], trust="owner")
    checklist.done(other["id"], by="owner")                           # not raised by the Core for Toolsmith
    pkg = checklist.add({**ACCOUNT, "id": "ts-pkg"}, "toolsmith", [f"job:{job_id}"], trust="core",
                        raised_by="toolsmith")
    checklist.done(pkg["id"], by="pkg:toolsmith")                      # a package's Done is not hers
    assert not (host.store.read(build_id) or {}).get("ticked")


def test_the_plan_the_core_hands_the_package_manager_carries_its_own_tick_record(host_and_build):
    host, checklist, build_id, job_id = host_and_build
    sid = _raise(host, build_id, job_id, ACCOUNT)
    checklist.done(sid, by="owner")
    host.store.change(build_id, lambda x: x.update(consent={"id": "c_test"}, envelope={"jobs": [], "setup": []}))
    rec = SimpleNamespace(build_id=build_id, job_id=job_id, cand={"ref": "github:example/hello-tool"})
    plan = host.trials._plan_for(rec)
    assert plan["ticked"]["ts-account"]["sha"] == plan_cover.step_sha(ACCOUNT)
