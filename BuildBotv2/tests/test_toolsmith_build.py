"""Goal -> plan -> ONE Yes -> build -> adopt -> Steward restart from the manifest -> prove.

Recorded-fixture adoption of a tiny sample app (tests/fixtures/toolsmith/hello-page,
a README Quick Start of ``python -m http.server``): fake GitHub answers, a
scripted model, the real Core broker and routes, Toolsmith's process body run
in a thread (``InlineRunner``), and a fake Package Manager that starts the app
again from the ``puffpai.json`` Toolsmith wrote (as Steward will). Every
process started here is stopped here.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from toolsmith_fakes import FIXTURES, Script, World, tool
from puffpai_core.packages import manifest as manifest_mod
from puffpai_core.registry import row_from_manifest
from puffpai_core.zone3.toolsmith import adopt
from puffpai_core.zone3.toolsmith_host import sender, settings as settings_mod
from toolsmith_fakes import end_held, start_held
from puffpai_core.zone3.toolsmith_host.trials import free_port

T = FIXTURES / "toolsmith"
PY = Path(sys.executable).as_posix()
SEARCH = {"items": [{"full_name": "example/hello-page", "name": "hello-page", "description": "A tiny news headlines board",
                     "stargazers_count": 120, "language": "HTML", "topics": ["news"], "default_branch": "main",
                     "license": {"spdx_id": "MIT"}}]}


def make_world(tmp_path, state, procs):
    w = World(tmp_path, state)
    p, reg = w.ports, w.registry
    p.web = {
        "https://api.github.com/search/repositories?*": (200, json.dumps(SEARCH)),
        "https://raw.githubusercontent.com/example/hello-page/main/README.md": (200, (T / "hello-page" / "README.md").read_text()),
    }
    p.fixture_repo = T / "hello-page"

    def add(folder, origin, yes):
        man, _ = manifest_mod.load(folder)                       # what Steward reads
        assert manifest_mod.validate(man) == [] and origin == man["origin"] and yes
        port = free_port()
        start = man["run"][adopt.run_key()]["start"]
        argv = [a.replace("{port}", str(port)) for a in start]
        argv[0] = sys.executable if argv[0] in ("python", "{python}") else argv[0]
        procs.append(start_held(argv, folder))
        reg.add(row_from_manifest(man, man["origin"], status="running", port=port))
        p.added.append(man)
        return {"ok": True, "id": man["id"]}
    p.add_fn = add

    def model(messages):
        port = re.search(r"Your port: (\d+)", messages[0]["content"][0]["text"]).group(1)
        steps = {1: tool("download"),
                 # no shell and only the try's own Python (D60, D61): its .venv first, then the server
                 2: tool("run", program="python", args=["-m", "venv", "--without-pip", ".venv"]),
                 3: tool("start_server", program="python", args=["-m", "http.server", port, "--bind", "127.0.0.1"]),
                 4: tool("wait_for_page", url=f"http://127.0.0.1:{port}/"),
                 5: tool("done", url=f"http://127.0.0.1:{port}/")}
        n = sum(1 for m in messages if m["role"] == "assistant") + 1
        return steps.get(n, {"content": [{"type": "text", "text": "ok"}], "stop": "end_turn"})
    p.local_replies = Script([model] * 10)
    return w


@pytest.fixture
def world(tmp_path, state):
    procs = []
    w = make_world(tmp_path, state, procs)
    yield w.host, w.ports, w.registry, w
    w.host.stop(timeout=10)
    for pr in procs:
        end_held(pr)


def test_goal_to_a_proven_package(world):
    svc, ports, reg, w = world
    b = svc.plan("daily news headlines", [{"name": "Daily headlines", "does": "Shows news headlines",
                                          "sample_ask": "What are the headlines today?"}])
    assert w.runner.runs == [("plan", b["id"])]                  # Toolsmith's process made the plan
    job = b["jobs"][0]
    assert job["status"] == "waiting_yes" and job["plan"]["candidates"][0]["full_name"] == "example/hello-page"
    assert b["card"]["sentence"].startswith("Toolsmith finds and installs apps for 1 job")
    assert "Tries as a program on this computer, as you." in b["card"]["lines"][0]
    assert b["knowledge"] == [] and b["knowledge_plan"]["topics"] == []    # no topics from the brain: none read
    cid = b["consent"]["id"]
    card = ports.consent.cards[cid]
    assert card["scope"] == {"kind": "package", "target": f"toolsmith-build-{b['id'].rsplit('-', 1)[-1]}"}
    assert card["covers"]["plan_sha"] == b["plan_sha"] and "pypi.org" in card["covers"]["install_hosts"]
    with pytest.raises(PermissionError):
        svc.start(b["id"], cid)                                  # no Yes yet: nothing builds
    ports.consent.say_yes(cid)
    done = svc.start(b["id"], cid)
    job = done["jobs"][0]
    assert job["status"] == "working", job["run"]
    man = ports.added[0]
    assert man["origin"] == "adopted:https://github.com/example/hello-page"
    assert man["ui"]["pages"][0] == {"id": "main", "title": "hello-page", "url": "http://127.0.0.1:{port}/",
                                     "show": "new_tab"}
    assert man["run"][adopt.run_key()]["start"] == ["{python}", "-m", "http.server", "{port}", "--bind", "127.0.0.1"]
    assert man["run"][adopt.run_key()]["python"] is True and man["run"][adopt.run_key()]["env"]["PORT"] == "{port}"
    assert man["health"]["url"] == "http://127.0.0.1:{port}/"
    assert "as a program on this computer, as you" in man["consent"]
    proof = job["run"]["result"]["proof"]
    assert proof["result"] == "working" and "headlines" in proof["read"].lower()
    assert reg.get("hello-page")["built_with"] == "local"
    # The Package Manager (L1) took the try with ONE card of its own, which she answered; Toolsmith never did.
    (card,) = ports.pm.values()
    assert card["status"] == "installed" and card["manifest"]["id"] == "hello-page"
    assert any("waits for your Yes on its card" in ln for ln in job["run"]["lines"])
    # Toolsmith's token for that build is gone once its process ended.
    assert w.core.tokens.revoke_toolsmith(b["id"]) == 0
    # Proven for this job: the next plan reuses it instead of searching.
    again = svc.plan("daily news headlines", [{"name": "Daily headlines"}])
    assert again["jobs"][0]["plan"]["reuse"] == "hello-page"


def test_a_yes_for_another_plan_does_not_start_this_one(world):
    svc, ports, _, _w = world
    a = svc.plan("news", [{"name": "News"}])
    b = svc.plan("weather", [{"name": "Weather"}])
    ports.consent.say_yes(a["consent"]["id"])
    with pytest.raises(PermissionError) as e:
        svc.start(b["id"], a["consent"]["id"])
    assert "not for this plan" in str(e.value)


def test_nothing_found(world):
    svc, ports, _, _w = world
    ports.web = {}
    got = svc.plan("an underwater basket weaving planner", None)
    # Nothing to install: no Yes card at all (lane I3a), and the job says so.
    assert got["consent"]["id"] is None and ports.consent.cards == {}
    assert got["consent"]["said"] == "Toolsmith found nothing to install, so there is nothing to say Yes to."
    assert got["jobs"][0]["status"] == "nothing_found"
    assert got["jobs"][0]["run"]["line"] == "Nothing found yet. Paste a GitHub address for this job."


def test_cloud_without_a_key_holds_the_build(world, state):
    svc, ports, _, _w = world
    prov = svc.providers()["anthropic"]
    svc.choose({"where": "cloud", "provider": "anthropic", "consent_sha": sender.consent_sha(prov)})
    b = svc.plan("daily news headlines", [{"name": "Daily headlines"}])
    assert "Anthropic (claude-opus-5-5), up to $5 for this build" in b["card"]["sentence"]
    assert ports.sent == []                                      # nothing went to a company before the Yes
    ports.consent.say_yes(b["consent"]["id"])
    got = svc.start(b["id"], b["consent"]["id"])
    job = got["jobs"][0]
    assert job["status"] == "held" and "key isn't saved yet" in job["run"]["line"]
    assert ports.sent == [] and ports.added == []


def test_choose_needs_her_consent_to_the_exact_sentence(world, state):
    svc, _p, _, _w = world
    with pytest.raises(ValueError):
        svc.choose({"where": "cloud", "provider": "openai", "consent_sha": "nope"})
    with pytest.raises(ValueError):
        svc.choose({"where": "cloud", "provider": "nobody", "consent_sha": "x"})
    prov = svc.providers()["openai"]
    got = svc.choose({"where": "cloud", "provider": "openai", "model": "gpt-6.1-sol", "cost_cap_usd": 2,
                      "consent_sha": sender.consent_sha(prov)})
    assert (got["where"], got["model"], got["cost_cap_usd"]) == ("cloud", "gpt-6.1-sol", 2.0)
    assert svc.choose({"where": "local"})["where"] == "local"
    assert settings_mod.read(state)["where"] == "local"


def test_api_routes(world, state):
    svc, ports, reg, w = world
    app, core = w.app, w.core
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    device = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    c = app.test_client()
    assert c.get("/v1/toolsmith").status_code == 401
    provs = c.get("/v1/toolsmith/providers", headers=owner).json
    assert {p["id"] for p in provs} == {"anthropic", "openai", "google"}
    assert all(p["problems"] == [] for p in provs)
    sha = next(p for p in provs if p["id"] == "google")["consent_sha"]
    assert c.put("/v1/toolsmith/model", headers=owner, json={"where": "local"}).status_code == 403  # a screen only
    r = c.put("/v1/toolsmith/model", headers=device, json={"where": "cloud", "provider": "google", "consent_sha": sha})
    assert r.status_code == 200 and r.json["model"] == "gemini-4-argon"
    c.put("/v1/toolsmith/model", headers=device, json={"where": "local"})
    assert c.post("/v1/toolsmith/plan", headers=owner, json={}).status_code == 400
    b = c.post("/v1/toolsmith/plan", headers=owner, json={"goal": "daily news headlines"}).json
    r = c.post(f"/v1/toolsmith/builds/{b['id']}/start", headers=owner, json={"consent_id": b["consent"]["id"]})
    assert r.status_code == 409 and "no Yes" in r.json["error"]
    ports.consent.say_yes(b["consent"]["id"])
    r = c.post(f"/v1/toolsmith/builds/{b['id']}/start", headers=owner, json={"consent_id": b["consent"]["id"]})
    assert r.status_code == 200 and r.json["jobs"][0]["status"] == "working"
    jid = b["jobs"][0]["id"]
    assert c.get(f"/v1/toolsmith/jobs/{jid}", headers=owner).json["status"] == "working"
    assert c.post(f"/v1/toolsmith/jobs/{jid}/next", headers=owner).status_code == 200
    assert c.get("/v1/toolsmith/builds/nope", headers=owner).status_code == 404
    assert c.get("/v1/toolsmith", headers=owner).json["builds"][0]["jobs"][0]["status"] == "working"
    # Toolsmith's routes are Toolsmith's only.
    assert c.get("/v1/toolsmith/agent/build", headers=owner).status_code == 403
    assert c.get("/v1/toolsmith/agent/build").status_code == 401
