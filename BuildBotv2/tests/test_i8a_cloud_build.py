"""Lane I8a, item 1: her cloud model for Toolsmith goes out only while her build runs, and only with her Yes.

From the owner's live test on 10-25: she picked Anthropic in the wizard and said Yes to the plan, yet every
call to api.anthropic.com was refused (subject toolsmith:cloud, code switched_off). The wizard kept her
choice only in settings\\toolsmith.json; no cloud Yes record existed, so the Bridge never turned the switch
on and never made the build grant.

Here the wizard's own Toolsmith-model step runs on the real Core (consent store, Bridge, policy, ledger,
Toolsmith host and its routes). The company's host is a fake one on 127.0.0.1: the Core's own sender asks
the real Bridge about api.anthropic.com, and only the connection itself is sent to the local server, so
nothing reaches Anthropic. The key is a made-up string the test lends as hers."""

import json
import threading
import time
import urllib.error
import urllib.request
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from puffpai_core.api import create_app
from puffpai_core.api.app import wire_core
from puffpai_core.zone3.toolsmith.errors import Refused
from puffpai_core.zone3.toolsmith_host import ToolsmithHost, sender
from puffpai_core.zone3.toolsmith_host.service import NO_CLOUD_YES_LINE
from test_i2a_wizard_toolsmith import Ports as WizardPorts, ThreadRunner
from wizard_kit import Kit

KEY = "sk-ant-" + "T" * 40                     # made up: the fake host checks it arrives, nothing else uses it
CLOUD = "toolsmith:cloud"
HOST = "api.anthropic.com"


from toolsmith_fakes import find_reply


class FakeCompany:
    """The company's API, on 127.0.0.1. Each request is kept with what the Bridge's policy held at that moment."""

    def __init__(self, policy, ports) -> None:
        self.policy, self.ports = policy, ports
        self.seen: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):                       # quiet
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                grants = [(g["subject"], g["host"], g["port"], g["phase"]) for g in outer.policy.fresh().grants
                          if g["subject"] == CLOUD]
                outer.seen.append({"path": self.path, "key": self.headers.get("x-api-key"), "grants": grants,
                                   "other_host": outer.ports.bridge_decision(CLOUD, "api.openai.com", "build"),
                                   "system": str(body.get("system") or ""),
                                   "tools": [t.get("name") for t in body.get("tools") or []]})
                look = find_reply(body)
                if look is not None:                          # a job's look (D121): it tries the plan's apps, then finishes
                    content, stop = look["content"], "tool_use"
                else:                                         # a try's turn: it gives up (twice: the first is sent back)
                    content = [{"type": "tool_use", "id": f"tu_{len(outer.seen)}", "name": "give_up",
                                "input": {"reason": "the fake company gives up"}}]
                    stop = "tool_use"
                data = json.dumps({"content": content, "stop_reason": stop,
                                   "usage": {"input_tokens": 10, "output_tokens": 5}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ToLocal(urllib.request.HTTPSHandler):
    """The Core's opener after the Bridge said yes: api.anthropic.com is reached at the fake on 127.0.0.1.
    Any other host is refused here, so nothing in this test can reach the internet."""

    def __init__(self, port: int) -> None:
        super().__init__()
        self.port = port

    def https_open(self, req):
        if req.host != HOST:
            raise urllib.error.URLError(f"the test reaches only {HOST}, not {req.host}")
        port = self.port
        return self.do_open(lambda _host, **kw: HTTPConnection("127.0.0.1", port, timeout=kw.get("timeout")), req)


class CloudPorts(WizardPorts):
    """The real ports (the real Bridge decides every cloud call); recorded: GitHub, the page reader, her key."""

    company: FakeCompany | None = None

    def _handlers(self):
        return (ToLocal(self.company.port),) if self.company else ()

    def lend_key(self, key_name, borrower, purpose, *, hosts=(), job=None):
        assert key_name == "toolsmith.anthropic" and tuple(hosts) == (HOST,)
        return "lease-1"

    def key_value(self, handle, *, borrower, purpose, host=None):
        assert handle == "lease-1" and host == HOST
        return KEY

    def end_lease(self, handle):
        pass

    def screen_outbound(self, text):
        return text


@pytest.fixture
def cloud(state):
    app = create_app(state)
    core = wire_core(app)
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "toolsmith.json").write_text(json.dumps({"timing": {"poll_s": 0.1}}), encoding="utf-8")
    ports = CloudPorts(state, registry=core.registry, checklist=app.extensions.get("puffpai.checklist"),
                       consent=core.consent, bus=core.bus, zone=lambda: app.extensions.get("puffpai.zone3"),
                       packages=core.packages, steward=core.steward, caretaker=core.caretaker)
    runner = ThreadRunner(app)
    host = ToolsmithHost(state, ports, core.registry, core.tokens, runner=runner, sleep=lambda s: None)
    app.extensions["puffpai_toolsmith"] = host
    company = FakeCompany(core.zone3.bridge.policy, ports)
    ports.company = company
    try:
        yield app, core, host, ports, company, runner
    finally:
        company.close()
        host.stop(timeout=10)
        core.steward.stop_all(remember=False)
        if app.extensions.get("puffpai.zone3") is not None:
            app.extensions["puffpai.zone3"].stop()


def _engine(state, core):
    kit = Kit(state, vault="open")
    kit.toolsmith = core.toolsmith                       # the Core's real host, through the wiring
    kit.bridge = core.bridge                             # the Core's real Bridge posture (step 4)
    eng = kit.engine()
    assert eng.submit("brain", {"model": ""})["status"] == "done"
    assert eng.submit("bridge", {"posture": "search"})["status"] == "done"
    return eng


def _pick_anthropic(eng):
    screen = eng.submit("toolsmith", {"where": "cloud", "provider": "anthropic"})
    assert screen["field"]["id"] == "consent"
    screen = eng.submit("toolsmith", {"consent": "yes", "key": KEY, "cost_cap": "", "github": ""})
    assert screen["status"] == "done", screen


def _plan_yes_start(eng, host, runner):
    assert eng.submit("goal", {"goal": "daily news headlines"})["status"] == "done"
    build = eng.build_id()
    plan = eng.submit("plan", {"project": ""})
    assert plan["status"] == "done", plan
    assert host.view(build)["jobs"][0]["plan"]["candidates"][0]["full_name"] == "example/hello-page"
    assert eng.submit("yes", {"yes": "yes"})["status"] == "done"
    eng.submit("build", {})                              # the wizard's build step starts it (ts.start)
    end = time.monotonic() + 60
    while time.monotonic() < end and (host.running(build) or ("build", build) not in runner.runs):
        time.sleep(0.05)
    assert ("build", build) in runner.runs and not host.running(build)
    return build


def _ledger(state):
    path = state / "logs" / "outside.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if r.get("subject") == CLOUD]


def test_her_cloud_model_is_reached_while_her_build_runs_and_closed_after(cloud, state):
    app, core, host, ports, company, runner = cloud
    eng = _engine(state, core)
    _pick_anthropic(eng)

    # Her Yes to Anthropic's own sentence is the Bridge's record: only its model host, only while a build runs.
    (rec,) = [r for r in core.consent.live("cloud") if r["scope"]["target"] == CLOUD]
    prov = host.providers()["anthropic"]
    assert rec["covers"]["hosts"] == [HOST] and rec["covers"]["phase"] == "build" and rec["by"] == "owner"
    assert rec["covers"]["consent_sha"] == sender.consent_sha(prov) == host.settings()["consent"]["sha"]
    policy = core.zone3.bridge.policy
    assert policy.fresh().switches[CLOUD]["on"] is True
    assert not [g for g in policy.fresh().grants if g["subject"] == CLOUD]     # no build runs: no grant yet
    assert ports.bridge_decision(CLOUD, HOST) == "deny"

    build = _plan_yes_start(eng, host, runner)

    # During the build: the Core's sender reached the fake company with her key, under a grant for exactly
    # that host and the build phase; another company's host was refused at that same moment.
    assert company.seen, "nothing reached the company during the build"
    assert any("state_needs" in s["tools"] for s in company.seen)                  # a job's look went out (D121)
    for s in company.seen:
        assert s["path"] == "/v1/messages" and s["key"] == KEY
        assert s["grants"] == [(CLOUD, HOST, 443, "build")]
        assert s["other_host"] == "deny"
    done = [r for r in _ledger(state) if r["outcome"] == "done"]
    assert done and {(r["host"], r["phase"], r["via"]) for r in done} == {(HOST, "build", "core")}
    b = host.view(build)
    assert all("Bridge is closed" not in json.dumps(j["run"]) for j in b["jobs"])

    # After the build: its grant is gone, the switch stays with her Yes, and a call is refused.
    assert not [g for g in policy.fresh().grants if g["subject"] == CLOUD]
    assert policy.fresh().switches[CLOUD]["on"] is True and core.consent.granted(rec["consent_id"])
    seen = len(company.seen)
    with pytest.raises(Refused):
        ports.http_send("POST", f"https://{HOST}/v1/messages", subject=CLOUD, body=b"{}")
    assert len(company.seen) == seen
    assert _ledger(state)[-1]["outcome"] == "refused" and _ledger(state)[-1]["code"] == "not_on_list"


def test_without_her_cloud_yes_nothing_goes_out(cloud, state):
    """Her bot from before this fix: settings say Anthropic with the sentence's sha, but no Yes is on record.
    The build holds its job with a plain line, and nothing reaches the company."""
    app, core, host, ports, company, runner = cloud
    eng = _engine(state, core)
    prov = host.providers()["anthropic"]
    path = state / "settings" / "toolsmith.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update({"where": "cloud", "provider": "anthropic", "model": "claude-opus-5-5", "cost_cap_usd": 5.0,
                "consent": {"sha": sender.consent_sha(prov), "at": "2026-10-25T21:52:10+00:00", "by": "owner"}})
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert not core.consent.live("cloud")

    build = _plan_yes_start(eng, host, runner)
    assert company.seen == []
    assert CLOUD not in core.zone3.bridge.policy.fresh().switches
    (job,) = host.view(build)["jobs"]
    assert job["run"]["state"] == "held"
    assert NO_CLOUD_YES_LINE.format(company="Anthropic") in job["run"]["line"]


def test_this_computer_takes_her_cloud_yes_back(cloud, state):
    """She picks this computer after Anthropic: the cloud Yes is taken back and toolsmith:cloud closes, so even
    while a build runs the company's host stays refused."""
    app, core, host, ports, company, runner = cloud
    eng = _engine(state, core)
    _pick_anthropic(eng)
    (rec,) = core.consent.live("cloud")
    bridge = core.zone3.bridge
    bridge.open_build("20261025-170000-aaaaaa")             # with her Yes, a running build opens it
    assert [g["host"] for g in bridge.policy.fresh().grants if g["subject"] == CLOUD] == [HOST]
    assert ports.bridge_decision(CLOUD, HOST) == "allow"
    bridge.end_build("20261025-170000-aaaaaa")
    assert eng.submit("toolsmith", {"where": "local", "github": ""})["status"] == "done"
    assert core.consent.granted(rec["consent_id"]) is None and core.consent.get(rec["consent_id"])["status"] == "revoked"
    assert CLOUD not in bridge.policy.fresh().switches
    bridge.open_build("20261025-170001-bbbbbb")             # a build runs again: nothing opens for the cloud
    try:
        assert not [g for g in bridge.policy.fresh().grants if g["subject"] == CLOUD]
        assert ports.bridge_decision(CLOUD, HOST) == "deny"
        assert _ledger(state)[-1]["code"] == "switched_off"
    finally:
        bridge.end_build("20261025-170001-bbbbbb")
    assert company.seen == []


def test_picking_again_keeps_only_the_newest_yes(cloud, state):
    """Each pick is one Yes: the one before is taken back, so only the company she picked last can open."""
    app, core, host, ports, company, runner = cloud
    prov = host.providers()
    host.choose({"where": "cloud", "provider": "anthropic", "consent_sha": sender.consent_sha(prov["anthropic"])})
    host.choose({"where": "cloud", "provider": "openai", "consent_sha": sender.consent_sha(prov["openai"])})
    live = core.consent.live("cloud")
    assert [r["covers"]["hosts"] for r in live] == [["api.openai.com"]]
    assert ports.cloud_live("openai", sender.consent_sha(prov["openai"]))
    assert not ports.cloud_live("anthropic", sender.consent_sha(prov["anthropic"]))
