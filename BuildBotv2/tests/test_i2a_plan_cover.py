"""Integration lane I2a: the build plan's ONE Yes covers an adopted app (03 3.3), on the real Package
Manager and consent store. Inside the plan: installed with no card for her, recorded against the plan's
Yes. Outside it: one card that shows only what was not on the plan first."""

import json
import os
from pathlib import Path

import pytest

from conftest import yes
from puffpai_core.consent import card_view
from puffpai_core.packages import plan_cover
from puffpai_core.packages.manager import AddError
from puffpai_core.zone3.toolsmith_host import allowlist

URL = "https://github.com/example/hello-tool"
ENVELOPE = {"goal": "a simple notes app", "jobs": [{"name": "Notes", "reuse": None,
                                                     "candidates": ["github:example/hello-tool"]}],
            "setup": [], "install_hosts": ["github.com", "pypi.org", "files.pythonhosted.org"]}


@pytest.fixture
def quiet(core):
    core.packages.start_after_install = False
    return core


def _plan(core, envelope=None, answer=True, build="20261022-120000-abcdef"):
    env = envelope or ENVELOPE
    covers = {"build": build, "plan_sha": plan_cover.envelope_sha(env),
              "card": {"title": "Build this plan?", "sentence": "Toolsmith finds and installs apps for 1 job.",
                       "lines": ["Notes: try hello-tool first."]}, "install_hosts": env["install_hosts"]}
    card = core.consent.ask("package", f"toolsmith-build-{build.rsplit('-', 1)[-1]}", covers,
                            bound={"plan_sha": covers["plan_sha"]})
    if answer:
        core.consent.answer(card["consent_id"], "yes", by="owner", card_sha=card["card_sha"])
    return {"consent_id": card["consent_id"], "envelope": ENVELOPE, "candidate": "github:example/hello-tool",
            "job": "Notes", "ran": ran_record()}


#: Where the try of hello-tool ran (only compared as words: the record holds the try's own paths).
TRY = os.path.abspath(os.path.join(os.sep, "bb", "packages", "hello-tool"))
RUN = {"type": "command", "python": True, "start": ["{python}", "server.py"]}


def ran_record(*more):
    """The broker's record of a try that started hello-tool with its own .venv Python (Broker.record's shape)."""
    return {"folder": TRY, "python": str(allowlist.venv_python(Path(TRY))), "node": "", "port": 18555,
            "commands": [{"kind": "start", "program": "{python}", "args": ["server.py"], "cwd": ""}, *more]}


def _adopted(folder, **changes):
    """An adopted app as the Core writes it: .buildbot/puffpai.json (the top-level file is the project's own)."""
    data = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    data.update(kind="app", origin=f"adopted:{URL}", abilities={}, needs={"hosts": [],
                "install_hosts": ["github.com", "pypi.org"]}, run={"windows": dict(RUN), "default": dict(RUN)},
                ui={"pages": [{"id": "main", "title": "Hello", "url": "http://127.0.0.1:{port}/",
                                "show": "new_tab"}]})
    data.update(changes)
    (folder / ".buildbot").mkdir(exist_ok=True)
    (folder / ".buildbot" / "puffpai.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def test_inside_the_plan_it_installs_with_no_new_card(quiet, drawbridge, hello_copy):
    _adopted(hello_copy)
    plan = _plan(quiet)
    staged = quiet.packages.add(str(hello_copy), adopted=URL, plan=plan)
    assert staged["covered_by"] == plan["consent_id"]
    assert [c for c in quiet.consent.list(status="pending")] == []          # nothing waits for her
    assert quiet.packages.wait(staged["consent_id"], 60)["status"] == "installed"
    rec = quiet.consent.get(staged["consent_id"])
    assert rec["status"] == "granted" and rec["by"] == f"plan:{plan['consent_id']}"
    assert rec["card"]["plan"]["consent_id"] == plan["consent_id"]
    assert quiet.registry.get("hello-tool")["yes_record"] == staged["consent_id"]
    seen = card_view(rec["card"])
    assert seen["lines"][0].startswith("Covered by the plan you said Yes to")


def test_a_host_not_on_the_plan_asks_for_only_that_difference(quiet, drawbridge, hello_copy):
    _adopted(hello_copy, needs={"hosts": ["api.example.org"], "install_hosts": ["github.com"]})
    plan = _plan(quiet)
    staged = quiet.packages.add(str(hello_copy), adopted=URL, plan=plan)
    assert "covered_by" not in staged
    rec = quiet.consent.get(staged["consent_id"])
    assert rec["status"] == "pending"
    lines = card_view(rec["card"])["lines"]
    assert lines[0] == "Not on the plan you said Yes to:"
    assert lines[1] == "- It reaches places on the internet your plan did not list: api.example.org."
    assert "The rest was on that plan" in lines[2]
    assert staged["card"]["title"].endswith("Part of it was not on your plan")
    assert yes(quiet, staged)["status"] == "installed"                       # her Yes on the difference card


@pytest.mark.parametrize("change,line", [
    ({"needs": {"hosts": [], "keys": [{"name": "NOTES_KEY", "why": "x"}]}}, "It uses keys your plan did not list"),
    ({"abilities": {"changes": ["write_note"]}}, "tools that change things without asking"),
    ({"setup_steps": [{"id": "ts-admin", "kind": "run_command", "title": "Turn on long paths", "admin": True,
                       "shell": "powershell", "text": "x"}]}, "administrator steps your plan did not list"),
])
def test_what_the_plan_did_not_cover_is_on_the_card(quiet, drawbridge, hello_copy, change, line):
    _adopted(hello_copy, **change)
    staged = quiet.packages.add(str(hello_copy), adopted=URL, plan=_plan(quiet))
    assert "covered_by" not in staged
    assert any(line in x for x in staged["card"]["outside_plan"]), staged["card"]["outside_plan"]


def test_an_adopted_manifest_from_another_address_is_refused_before_any_card(quiet, drawbridge, hello_copy):
    _adopted(hello_copy, origin="adopted:https://github.com/other/thing")
    with pytest.raises(AddError, match="does not say it was adopted from"):
        quiet.packages.add(str(hello_copy), adopted=URL, plan=_plan(quiet))
    assert quiet.consent.list(status="pending") == []


def test_a_plan_with_no_yes_or_another_envelope_covers_nothing(quiet, drawbridge, hello_copy):
    _adopted(hello_copy)
    plan = _plan(quiet, answer=False)                                         # she has not said Yes yet
    staged = quiet.packages.add(str(hello_copy), adopted=URL, plan=plan)
    assert "covered_by" not in staged and "plan" not in staged["card"]
    assert quiet.consent.get(staged["consent_id"])["status"] == "pending"


def test_an_envelope_changed_after_her_yes_covers_nothing(quiet, drawbridge, hello_copy):
    _adopted(hello_copy)
    plan = _plan(quiet)
    plan["envelope"] = {**ENVELOPE, "install_hosts": ENVELOPE["install_hosts"] + ["evil.example"]}
    staged = quiet.packages.add(str(hello_copy), adopted=URL, plan=plan)
    assert "covered_by" not in staged and "plan" not in staged["card"]


def test_an_app_not_on_the_plan_is_outside_it(quiet, drawbridge, hello_copy):
    _adopted(hello_copy)
    plan = dict(_plan(quiet), candidate="github:example/other")
    staged = quiet.packages.add(str(hello_copy), adopted=URL, plan=plan)
    assert staged["card"]["outside_plan"][0] == "This app was not on the plan you said Yes to."


def test_a_drawbridge_hold_is_outside_the_plan(quiet, monkeypatch, hello_copy):
    from conftest import drawbridge_double
    drawbridge_double(monkeypatch, lambda p, m: {"verdict": "hold", "reasons": ["cant_check"], "line": "held",
                                                 "rows": [{"code": "cant_check", "result": "note",
                                                           "files": ["blob.bin"]}]})
    _adopted(hello_copy)
    staged = quiet.packages.add(str(hello_copy), adopted=URL, plan=_plan(quiet))
    assert "covered_by" not in staged
    assert any("blob.bin" in x for x in staged["card"]["outside_plan"])


def test_the_plan_card_shows_its_own_sentence(quiet):
    plan = _plan(quiet, answer=False)
    rec = quiet.consent.get(plan["consent_id"])
    seen = card_view(rec["card"])
    assert seen["title"] == "Build this plan?"
    assert seen["lines"][:2] == ["Toolsmith finds and installs apps for 1 job.", "Notes: try hello-tool first."]
    assert any(x.startswith("Places while it installs: github.com") for x in seen["lines"])
