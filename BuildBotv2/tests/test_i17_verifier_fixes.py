"""Lane I17, the verifier's findings on d5f6dd2d, each as a test.

1. A stale store-phase owner does nothing: a second host takes over with resume(), the card is granted, and
   there is one download, one role set and one launch.
2. A launch that fails says so plainly, never "started", and the build can be started again.
3. Roles take only local models: cloud tags, slashes, addresses, and what Ollama's /api/show calls remote
   are refused, in the routes, the ports and the brain step's "Use another model".
4. A build left "starting" by a Core that stopped is finished on resume: done when it ran, started once when
   it never did.
5. Six pull asks at once raise one card.
6. A download can be cancelled: its card closes or its stream stops, and no role is set.
No real Ollama and no real download.
"""

from __future__ import annotations

import http.server
import json
import threading
import time

import pytest

from test_i17_model_roles import (CAND, JOB, FakeOllama, Launches, ModelPorts, _plan, api, census,  # noqa: F401
                                  ollama, world)
from toolsmith_fakes import World
from puffpai_core import jsonfile
from puffpai_core.zone2 import roles
from puffpai_core.zone3.toolsmith_host import store_phase as sp

CODER = "a coder bot that helps me write Python scripts"


def _wait(fn, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.02)
    return fn()


def _rec(w, b):
    return (w.host.store.read(b["id"]) or {}).get("store_adds") or {}


# -- 1. a stale owner does nothing -----------------------------------------------------------------------------

def test_a_host_that_took_over_is_the_only_one_that_downloads_sets_and_launches(world, ollama, state):
    """The verifier's repro: the first host waits on the download card; a second host on the same folder takes
    over with resume(); then the card is granted. One download, one role set, one launch."""
    from puffpai_core.zone3.toolsmith_host import ToolsmithHost
    w = world
    b = _plan(w, CODER)
    cid = b["consent"]["id"]
    w.host.use_offers(b["id"], cid, ["model:code"])
    w.ports.consent.say_yes(cid)
    assert _wait(lambda: (_rec(w, b).get("apps") or [{}])[0].get("state") == "asking")
    card = _rec(w, b)["apps"][0]["card"]
    new = ToolsmithHost(w.state, w.ports, w.registry, w.core.tokens, runner=w.launches, sleep=lambda s: None)
    new.store_phase.poll_s = 0.02
    assert new.store_phase.resume() == [b["id"]]
    time.sleep(0.2)                                                    # both threads poll the same card now
    w.ports.cards[card] = "granted"
    assert _wait(lambda: _rec(w, b).get("state") == "done")
    time.sleep(0.5)                                                    # a stale thread would act by now
    assert ollama.pulled == ["qwen2.5-coder:7b"]
    assert w.ports.order.count("card") == 1 and w.ports.order.count("pull") == 1
    assert w.ports.order.count("role") == 1 and w.launches.runs == ["build"]
    assert w.ports.expired_cards == []
    assert roles.role_map(state)["code"] == "qwen2.5-coder:7b"
    new.stop(timeout=10)


def test_the_download_and_the_role_set_are_safe_to_ask_twice(state, ollama):
    ollama.release.clear()
    got = []
    threads = [threading.Thread(target=lambda: got.append(roles.pull_now(state, "qwen2.5-coder:7b"))) for _ in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    ollama.release.set()
    for t in threads:
        t.join(10)
    assert got == [(True, "")] * 3 and ollama.pulled == ["qwen2.5-coder:7b"]     # one download, three answers
    assert roles.pull_now(state, "qwen2.5-coder:7b") == (True, "") and ollama.pulled == ["qwen2.5-coder:7b"]
    roles.set_role(state, "code", "qwen2.5-coder:7b", ollama.names)
    before = (state / "settings" / "models.json").stat().st_mtime_ns
    time.sleep(0.05)
    roles.set_role(state, "code", "qwen2.5-coder:7b", ollama.names)
    assert (state / "settings" / "models.json").stat().st_mtime_ns == before       # set already: not written


# -- 2. a failed launch ------------------------------------------------------------------------------------

class BrokenRunner:
    def __init__(self):
        self.tries = 0

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.tries += 1
        raise OSError("the program could not start")


def test_a_launch_that_raises_is_said_plainly_and_the_build_can_start_again(world, ollama):
    w = world
    ollama.names.append("qwen2.5-coder:7b")                            # here already: no card, no download
    b = _plan(w, CODER)
    cid = b["consent"]["id"]
    assert b["card"]["model_offers"][0]["installed"] is True
    w.host.use_offers(b["id"], cid, ["model:code"])
    broken = BrokenRunner()
    w.host.runner = broken
    w.ports.consent.say_yes(cid)
    assert _wait(lambda: _rec(w, b).get("state") == "done")
    said = _rec(w, b)["said"]
    assert said[-1] == "Toolsmith could not start the build: OSError." and sp.STARTED not in said, said
    v = w.host.view(b["id"])
    assert broken.tries == 1 and not v.get("yes")                      # startable again
    assert all(j["run"]["state"] == "planned" for j in v["jobs"])
    w.host.runner = w.launches
    got = w.host.start(b["id"], cid)
    assert got.get("yes") and w.launches.runs == ["build"]
    assert _rec(w, b)["said"][-1] == sp.STARTED


def test_no_token_store_is_a_plain_failure_too(world, ollama):
    w = world
    ollama.names.append("qwen2.5-coder:7b")
    b = _plan(w, CODER)
    cid = b["consent"]["id"]
    w.host.use_offers(b["id"], cid, ["model:code"])
    w.host.tokens = None
    w.ports.consent.say_yes(cid)
    assert _wait(lambda: _rec(w, b).get("state") == "done")
    assert _rec(w, b)["said"][-1] == "Toolsmith could not start the build: the Core has no token store."
    assert w.launches.runs == [] and not w.host.view(b["id"]).get("yes")


# -- 3. roles take only local models -------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["gpt-oss:120b-cloud", "kimi-k2:cloud", "GPT-OSS:20B-CLOUD", "hf.co/someone/model:Q4",
                                  "registry.example.org/library/x:1", "http://192.0.2.7:11434/m", "me@host:7b",
                                  "a\\b:1"])
def test_a_role_refuses_a_hosted_or_addressed_model(api, ollama, state, name):
    client, device, _ = api
    ollama.names.append(name)                                          # Ollama lists it: still refused
    for role in ("code", "chat", "vision", "fast"):
        r = client.put("/v1/models/roles", json={role: name}, headers=device)
        assert r.status_code == 400 and "not a model that runs on this computer" in r.json["error"], (role, r.json)
    assert roles.role_map(state) == {"chat": "qwen3.5:9b", "code": "", "vision": "", "fast": ""}
    with pytest.raises(roles.RoleError):
        roles.set_role(state, "code", name, ollama.names)


def test_only_a_catalog_model_or_the_one_picked_in_setup(api, ollama, state):
    client, device, _ = api
    ollama.names.append("myown:8b")
    r = client.put("/v1/models/roles", json={"code": "myown:8b"}, headers=device)
    assert r.status_code == 400 and "not in the list" in r.json["error"]
    record = jsonfile.read(state / "settings" / "model.json", {})
    jsonfile.write(state / "settings" / "model.json", {**record, "other": "myown:8b"})   # the brain step's pick
    assert client.put("/v1/models/roles", json={"code": "myown:8b"}, headers=device).status_code == 200
    ollama.remote = {"myown:8b"}                                       # Ollama's /api/show: hosted
    roles.set_role(state, "code", "", ollama.names)
    r = client.put("/v1/models/roles", json={"code": "myown:8b"}, headers=device)
    assert r.status_code == 400 and "not a model that runs on this computer" in r.json["error"]
    assert roles.role_map(state)["code"] == ""


def test_a_saved_cloud_role_is_never_used(state, ollama):
    jsonfile.write(roles.path(state), {"roles": {"code": "gpt-oss:120b-cloud"}})   # written outside PuffPAI
    assert roles.model_for(state, "code") == "qwen3.5:9b"


def test_the_core_ports_refuse_a_hosted_model(state, ollama):
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    ollama.names.append("gpt-oss:120b-cloud")
    with pytest.raises(roles.RoleError):
        Ports(state).model_set_role("code", "gpt-oss:120b-cloud")


@pytest.mark.parametrize("name,refused", [("gpt-oss:120b-cloud", True), ("deepseek:cloud", True),
                                          ("hf.co/x/y", True), ("http://127.0.0.1:1234", True),
                                          ("sneaky:7b", True), ("llama3.1:8b", False)])
def test_the_brain_steps_other_field_takes_local_models_only(name, refused):
    import types
    from puffpai_core.wizard import engine
    models = types.SimpleNamespace(is_remote=lambda n: n == "sneaky:7b")
    step = engine.BrainStep(types.SimpleNamespace(services=types.SimpleNamespace(models=models)))
    if refused:
        with pytest.raises(engine.Ask, match="not a model that runs on this computer"):
            step.check({"id": "other"}, name, {})
    else:
        step.check({"id": "other"}, name, {})
    step.check({"id": "model"}, "anything", {})                         # only the "other" box is checked


def test_the_wizard_asks_ollama_whether_a_pick_is_hosted(state, ollama):
    from puffpai_core.wiring import WizardModels
    ollama.names += ["sneaky:7b", "plain:7b"]
    ollama.remote = {"sneaky:7b"}
    m = WizardModels(state)
    assert m.is_remote("sneaky:7b") is True and m.is_remote("plain:7b") is False
    assert m.is_remote("not-here:1b") is False                         # can't ask: its name was checked


# -- 4. "starting" left by a Core that stopped --------------------------------------------------------------

def _left_starting(w, b, job_state):
    def fn(x):
        x["store_adds"].update(state="starting")
        x["store_adds"]["said"] = [s for s in x["store_adds"]["said"] if s != sp.STARTED]
        for j in x["jobs"]:
            j["run"]["state"] = job_state
    w.host.store.change(b["id"], fn)


def _started_plan(w, ollama):
    ollama.names.append("qwen2.5-coder:7b")
    b = _plan(w, CODER)
    cid = b["consent"]["id"]
    w.host.use_offers(b["id"], cid, ["model:code"])
    w.ports.consent.say_yes(cid)
    assert _wait(lambda: _rec(w, b).get("state") == "done") and w.launches.runs == ["build"]
    return b


def test_resume_marks_a_build_that_ran_done(world, ollama):
    from puffpai_core.zone3.toolsmith_host import ToolsmithHost
    w = world
    b = _started_plan(w, ollama)
    _left_starting(w, b, "building")                                   # it ran: a job moved on
    new = ToolsmithHost(w.state, w.ports, w.registry, w.core.tokens, runner=w.launches, sleep=lambda s: None)
    assert new.store_phase.resume() == []
    assert _rec(w, b)["state"] == "done" and _rec(w, b)["said"][-1] == sp.STARTED
    assert w.launches.runs == ["build"]                                # never launched again
    new.stop(timeout=10)


def test_resume_starts_a_build_that_never_started_once(world, ollama):
    from puffpai_core.zone3.toolsmith_host import ToolsmithHost
    w = world
    b = _started_plan(w, ollama)
    _left_starting(w, b, "planned")                                    # the Core stopped before the launch
    w.launches.runs.clear()
    new = ToolsmithHost(w.state, w.ports, w.registry, w.core.tokens, runner=w.launches, sleep=lambda s: None)
    new.store_phase.poll_s = 0.02
    assert new.store_phase.resume() == [b["id"]]
    new.store_phase.resume()                                           # a second resume never starts it twice
    assert _wait(lambda: _rec(w, b).get("state") == "done")
    time.sleep(0.3)
    assert w.launches.runs == ["build"] and w.host.view(b["id"]).get("yes")
    assert _rec(w, b)["said"][-1] == sp.STARTED
    new.stop(timeout=10)


# -- 5. one card for six asks at once -----------------------------------------------------------------------

def test_six_pull_asks_at_once_raise_one_card(api, core, ollama):
    client, device, _ = api
    gate = threading.Barrier(6)
    got = []

    def ask():
        c = core.app.test_client()
        gate.wait(10)
        r = c.post("/v1/models/pull", json={"model": "qwen2.5-coder:7b"}, headers=device)
        got.append((r.status_code, (r.json or {}).get("consent_id")))
    threads = [threading.Thread(target=ask) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert [s for s, _ in got] == [202] * 6 and len({c for _, c in got}) == 1, got
    pending = [r for r in core.consent.list("pending") if r["scope"]["kind"] == "model_pull"]
    assert len(pending) == 1


# -- 6. cancel -------------------------------------------------------------------------------------------------

def test_cancel_a_waiting_card_and_a_running_download(api, core, ollama, state):
    client, device, owner = api
    assert client.post("/v1/models/pull/cancel", json={"model": "gemma3:4b"}, headers=owner).status_code == 403
    assert client.post("/v1/models/pull/cancel", json={"model": "gemma3:4b"}, headers=device).status_code == 404
    r = client.post("/v1/models/pull", json={"model": "gemma3:4b"}, headers=device)
    cid = r.json["consent_id"]
    c = client.post("/v1/models/pull/cancel", json={"model": "gemma3:4b"}, headers=device)
    assert c.status_code == 200 and c.json["pull"]["state"] == "cancelled"
    assert core.consent.get(cid)["status"] == "expired"                # its card closed
    time.sleep(0.3)
    assert roles.pulls(state)["gemma3:4b"]["state"] == "cancelled" and ollama.pulled == []
    # a download that runs: the stream stops, it is marked cancelled, and no role is set
    ollama.release.clear()
    r = client.post("/v1/models/pull", json={"model": "qwen2.5-coder:7b"}, headers=device)
    rec = core.consent.get(r.json["consent_id"])
    core.consent.answer(rec["consent_id"], "yes", "tap", by="owner", card_sha=rec["card_sha"])
    assert _wait(lambda: roles.pulls(state)["qwen2.5-coder:7b"].get("state") == "downloading")
    c = client.post("/v1/models/pull/cancel", json={"model": "qwen2.5-coder:7b"}, headers=device)
    assert c.status_code == 200 and ollama.cancelled
    time.sleep(0.3)
    assert roles.pulls(state)["qwen2.5-coder:7b"]["state"] == "cancelled" and ollama.pulled == []
    assert roles.role_map(state)["code"] == ""


class SlowPull(http.server.BaseHTTPRequestHandler):
    """An Ollama that streams a download's progress forever, until the asker goes (never a real download)."""
    gone = threading.Event()

    def log_message(self, *_a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        n = 0
        try:
            while n < 400:
                n += 1
                self.wfile.write(json.dumps({"status": "pulling", "total": 1000, "completed": n}).encode() + b"\n")
                self.wfile.flush()
                time.sleep(0.02)
        except OSError:
            SlowPull.gone.set()                # the asker closed the stream


def test_the_runtime_stops_a_running_download_when_cancelled():
    from puffpai_core.zone2.ollama import OllamaRuntime
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowPull)
    port = server.server_address[1]
    assert port not in (8420, 8421, 8422, 11434, 11435)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        rt = OllamaRuntime(f"http://127.0.0.1:{port}")
        got = []
        th = threading.Thread(target=lambda: got.append(rt.pull("qwen2.5-coder:7b")))
        th.start()
        assert _wait(lambda: "qwen2.5-coder:7b" in rt._streams and rt._pulls["qwen2.5-coder:7b"].get("total_bytes"))
        assert rt.cancel("qwen2.5-coder:7b") is True
        th.join(5)
        assert got == [False] and not th.is_alive()
        assert rt._pulls["qwen2.5-coder:7b"]["error"] == "you cancelled it"
        assert SlowPull.gone.wait(5)                                   # Ollama's side saw the stream close
    finally:
        server.shutdown()
        server.server_close()
