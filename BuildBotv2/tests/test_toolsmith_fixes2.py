"""Regression tests for the L6 re-check: opaque blocks, lane L3's real interfaces, Zone 3 at start,
unsaved run lines, and the Core's Checklist steps against lane L5's real Checklist."""

import importlib.util
import json
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from toolsmith_fakes import FakePorts, World
from test_toolsmith_cloud import reply_for
from puffpai_core.zone3.toolsmith import adopt
from puffpai_core.zone3.toolsmith.errors import CloudError, Refused
from puffpai_core.zone3.toolsmith_host import sender, wire
from puffpai_core.zone3.toolsmith_host.ports import KeyUnavailable, Ports

KEY = "sk-ant-" + "A" * 40
CONV = [{"role": "user", "content": [{"type": "text", "text": "Install the app"}]}]
TOOLS = [{"name": "terminal", "description": "Run", "input_schema": {"type": "object", "properties": {}}}]
SMUGGLED = "SSN 123-45-6789 key sk-ant-api03-Z" + "q" * 40


def anthropic(tmp_path, *, build="b1", record=None, replies=None):
    ports = FakePorts(tmp_path)
    ports.keys = {"toolsmith.anthropic": KEY}
    queue = list(replies or [])

    def send(method, url, headers, body):
        nxt = queue.pop(0) if queue else reply_for("Anthropic", "text")
        return 200, {}, json.dumps(nxt).encode()
    ports.http_send_fn = send
    p = sender.load_providers()["anthropic"]
    s = sender.FrontierSender(ports, p, model="claude-opus-5-5", build_id=build, cap_usd=5,
                              ledger=sender.Ledger(tmp_path / f"{build}.json"), consent_given=sender.consent_sha(p),
                              sleep=lambda s: None, opaque=record)
    return s, ports


# -- 1. opaque blocks: only what the Core itself received, unchanged ---------------------

def test_a_made_up_opaque_block_never_leaves(tmp_path):
    """The verifier's repro: Toolsmith's process puts private data in an assistant opaque block."""
    s, ports = anthropic(tmp_path)
    convo = CONV + [{"role": "assistant", "content": [{"type": "opaque", "data": [{"type": "text", "text": SMUGGLED}]}]},
                    {"role": "user", "content": [{"type": "text", "text": "go on"}]}]
    with pytest.raises(CloudError) as e:
        s.turn("sys", convo, TOOLS)
    assert e.value.code == "privacy_check" and "never sent" in e.value.message
    assert ports.sent == []


def test_the_companys_own_items_go_back_and_altered_ones_do_not(tmp_path):
    record = sender.OpaqueRecord()
    s, ports = anthropic(tmp_path, record=record, replies=[reply_for("Anthropic", "tool")])
    got = s.turn("sys", CONV, TOOLS)
    item = next(b for b in got["content"] if b["type"] == "opaque")
    assert item["id"].startswith("op_")
    back = json.loads(json.dumps(got["content"]))                       # through Toolsmith's process and back
    convo = CONV + [{"role": "assistant", "content": back},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok"}]}]
    s.turn("sys", convo, TOOLS)
    assert b"SIG" in ports.sent[-1]["body"]                              # the signed item went back as it was
    altered = json.loads(json.dumps(back))
    for b in altered:
        if b["type"] == "opaque":
            b["data"].append({"type": "text", "text": SMUGGLED})
    with pytest.raises(CloudError):
        s.turn("sys", CONV + [{"role": "assistant", "content": altered},
                              {"role": "user", "content": [{"type": "text", "text": "go on"}]}], TOOLS)
    assert SMUGGLED.encode() not in b"".join(x["body"] for x in ports.sent)
    other, _p2 = anthropic(tmp_path, build="b2", record=record)          # same Core, another build
    with pytest.raises(CloudError):
        other.turn("sys", convo, TOOLS)


def test_field_names_are_screened_too(tmp_path, monkeypatch):
    s, ports = anthropic(tmp_path)
    convo = CONV + [{"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "terminal",
                                                       "input": {"123-45-6789": "x", "command": "ls"}}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]
    s.turn("sys", convo, TOOLS)
    assert b"123-45-6789" not in ports.sent[-1]["body"]
    real = wire.build_request

    def named(provider, req):
        out = real(provider, req)
        out["body"]["metadata"] = {"owner 123-45-6789": "a value that is fine"}
        return out
    monkeypatch.setattr(wire, "build_request", named)
    n = len(ports.sent)
    with pytest.raises(CloudError) as e:
        s.turn("sys", CONV, TOOLS)
    assert e.value.code == "privacy_check" and len(ports.sent) == n


def test_unknown_block_shapes_never_leave(tmp_path):
    s, ports = anthropic(tmp_path)
    for convo in ([{"role": "user", "content": [{"type": "opaque", "data": SMUGGLED}]}],
                  [{"role": "system", "content": [{"type": "text", "text": SMUGGLED}]}],
                  [{"role": "user", "content": [{"type": "image", "source": SMUGGLED}]}]):
        with pytest.raises(CloudError):
            s.turn("sys", convo, TOOLS)
    assert ports.sent == []


def test_smuggling_through_the_model_route_is_refused(tmp_path, state):
    w = World(tmp_path, state)
    p = sender.load_providers()["anthropic"]
    w.host.choose({"where": "cloud", "provider": "anthropic", "consent_sha": sender.consent_sha(p)})
    w.ports.keys["toolsmith.anthropic"] = KEY
    w.ports.http_send_fn = lambda *a: (200, {}, json.dumps(reply_for("Anthropic", "text")).encode())
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = w.link(b["id"])
    convo = CONV + [{"role": "assistant", "content": [{"type": "opaque", "id": "op_madeup",
                                                       "data": [{"type": "text", "text": SMUGGLED}]}]},
                    {"role": "user", "content": [{"type": "text", "text": "go on"}]}]
    with pytest.raises(CloudError):
        link.model_turn("sys", convo, [])
    assert w.ports.sent == []
    w.host._child_done(b["id"], "build", 0)


# -- 2. lane L3's real interfaces (lane/l3-vault 936bcc5d) -------------------------------

REAL3 = importlib.util.find_spec("puffpai_core.zone1.archivist") is not None


@dataclass
class _Group:                                  # L3 classify.Group
    start: int
    end: int
    labels: list
    tier: str

    @property
    def secret(self):
        return any(x.startswith("secret.") for x in self.labels)


@dataclass
class _Classified:                             # L3 classify.Classified
    length: int
    found: list = field(default_factory=list)
    checked: str = "full"

    def groups(self):
        return self.found


class Locked(Exception):
    pass


class NotFound(Exception):
    pass


class LeaseEnded(Exception):
    pass


@pytest.fixture
def l3(monkeypatch, state):
    if REAL3:
        pytest.skip("L3 is merged: its own tests and the overlay check cover the real calls")
    calls = SimpleNamespace(classify=[], lend=[], use=[], end=[], fail=None, checked="full")

    def classify(text=None, *, file=None, path=None, people=None):
        calls.classify.append(path)
        found = [_Group(m.start(), m.end(), ["id.ssn"], "P4") for m in re.finditer(r"\d{3}-\d{2}-\d{4}", text)]
        found += [_Group(m.start(), m.end(), ["secret.api_key"], "P4") for m in re.finditer(r"sk-[\w-]{20,}", text)]
        return _Classified(len(text), found, calls.checked)

    class Archivist:
        bus = None

        def lend(self, key_name, borrower, purpose, *, hosts=(), job=None, ttl_s=3 * 3600):
            calls.lend.append((key_name, borrower, purpose, tuple(hosts), job))
            if calls.fail:
                raise calls.fail
            return "lease-1"

        def use(self, handle, *, borrower, purpose, host=None, sink="lease"):
            calls.use.append((handle, borrower, purpose, host))
            if calls.fail:
                raise calls.fail
            return KEY.encode()

        def end(self, handle, why="done"):
            calls.end.append((handle, why))
    one = Archivist()

    def for_state(state_dir, bus=None, *, watch=True):
        calls.for_state = (Path(state_dir), bus)
        return one
    for name, attrs in {"puffpai_core.zone1.classify": {"classify": classify},
                        "puffpai_core.zone1.archivist": {"for_state": for_state, "Archivist": Archivist}}.items():
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)
    return calls


def test_the_outbound_screen_is_l3_classify_outbound(l3, state):
    p = Ports(state)
    got = p.screen_outbound(f"My SSN is 123-45-6789 and my key {KEY}. Thanks.")
    assert got == "My SSN is [personal detail] and my key [key]. Thanks." and l3.classify == ["outbound"]
    assert p.screen_outbound(got) == got                                 # what it gives back passes again
    l3.checked = "partial"
    with pytest.raises(Refused):
        p.screen_outbound("anything")


def test_keys_are_lent_and_used_through_the_archivist_exactly(l3, state, tmp_path):
    bus = object()
    p = Ports(state, bus=bus)
    s = sender.FrontierSender(p, sender.load_providers()["anthropic"], model="claude-opus-5-5", build_id="b9",
                              cap_usd=5, ledger=sender.Ledger(tmp_path / "u.json"),
                              consent_given=sender.consent_sha(sender.load_providers()["anthropic"]))
    assert s._key("api.anthropic.com") == KEY
    assert l3.for_state == (Path(state), bus)
    assert l3.lend == [("toolsmith.anthropic", "toolsmith", "build:b9", ("api.anthropic.com",), "build:b9")]
    assert l3.use == [("lease-1", "toolsmith", "build:b9", "api.anthropic.com")]
    s.close()
    assert l3.end == [("lease-1", "build finished")]
    for exc, reason in ((Locked(), "locked"), (NotFound(), "missing"), (LeaseEnded(), "ended")):
        l3.fail = exc
        with pytest.raises(KeyUnavailable) as e:
            p.key_value("lease-1", borrower="toolsmith", purpose="build:b9", host="api.anthropic.com")
        assert e.value.reason == reason


def test_the_lease_ends_when_the_build_ends(tmp_path, state):
    w = World(tmp_path, state)
    p = sender.load_providers()["openai"]
    w.host.choose({"where": "cloud", "provider": "openai", "consent_sha": sender.consent_sha(p)})
    w.ports.keys["toolsmith.openai"] = "sk-" + "a" * 40
    b = w.host.store.create("x", [{"name": "News"}], w.host.settings())
    w.host._sender(b["id"]).probe()
    (lease,) = w.ports.leases.values()
    assert lease["hosts"] == ("api.openai.com",) and lease["job"] == f"build:{b['id']}" and not lease["ended"]
    w.host._child_done(b["id"], "build", 0)
    assert lease["ended"]


# -- 3. Zone 3 starts with the Core; a Core without it fails closed ---------------------------

def test_zone3_starts_once_with_the_core_and_each_core_uses_its_own(monkeypatch, tmp_path, state):
    from puffpai_core.api import create_app
    made = []

    class Drawbridge:
        def __init__(self, n):
            self.n = n

        def check_package(self, path, manifest, *, origin="local", tools=None):
            return {"verdict": "allow", "reasons": [], "tree_sha": f"zone-{self.n}", "line": "ok"}

    def for_app(app):
        z = app.extensions.get("puffpai.zone3")
        if z is None:
            z = SimpleNamespace(drawbridge=Drawbridge(len(made)))
            made.append(z)
            app.extensions["puffpai.zone3"] = z
        return z
    m = types.ModuleType("puffpai_core.zone3.service")
    m.for_app, m.current = for_app, lambda: made[-1]
    import puffpai_core.zone3 as zone3_pkg
    monkeypatch.setitem(sys.modules, "puffpai_core.zone3.service", m)        # what create_app imports
    monkeypatch.setattr(zone3_pkg, "service", m, raising=False)             # also when L4's real one is loaded
    from puffpai_core.api import app as app_mod
    a = create_app(state / "a")
    b = create_app(state / "b")
    assert made == []                                   # a bare app gets no Zone 3 (nor L3's vault and gate)
    assert app_mod.start_zone3(a) is made[0] and app_mod.start_zone3(a) is made[0]      # once, idempotent
    app_mod.start_zone3(b)
    assert len(made) == 2 and b.extensions["puffpai.zone3"] is made[1]
    # D57: serve() calls the one start, start_core(app), once; start_core is what brings Zone 3 up
    # (servers stubbed: nothing listens here).
    order = []
    real_wire = app_mod.wire_core
    monkeypatch.setattr(app_mod, "make_servers", lambda *x, **k: [])
    monkeypatch.setattr(app_mod, "start_core", lambda app: order.append(("core", real_wire(app) and len(made))) or None)
    monkeypatch.setattr(app_mod, "run_servers", lambda servers, **kw: None)
    monkeypatch.setattr(app_mod, "stop_core", lambda core, terminal: order.append(("stop", len(made))))
    app_mod.serve(port=0, state=state / "c", pages_port=0)
    assert order == [("core", 3), ("stop", 3)]          # one start, and Zone 3 was up inside it
    with a.test_request_context():
        from puffpai_core.api.groups.toolsmith import service
        host = service()
    assert host.ports.drawbridge_check(tmp_path, {})["tree_sha"] == "zone-0"   # its own Core's, not the latest
    a.extensions.pop("puffpai.zone3")
    got = host.ports.drawbridge_check(tmp_path, {})
    assert got["verdict"] == "block" and "not running in this Core" in got["line"]


def test_without_l4_code_there_is_no_zone3_and_it_says_so(state, tmp_path):
    if importlib.util.find_spec("puffpai_core.zone3.service") is not None:
        pytest.skip("L4 is merged")
    from puffpai_core.api import create_app
    app = create_app(state)
    assert "puffpai.zone3" not in app.extensions
    p = Ports(state, zone=lambda: app.extensions.get("puffpai.zone3"))
    assert "not built yet (lane L4)" in p.drawbridge_check(tmp_path, {})["line"]
    assert p.bridge_decision("toolsmith:search", "github.com") == "deny"


# -- 4. lines that can't be saved: a note on the job AND a Checklist step -----------------------

class StepLink:
    def __init__(self):
        self.steps, self.writes = [], []

    def step_add(self, job_id, step, try_id=""):
        self.steps.append((job_id, step, try_id))
        return {"id": "step_1"}

    def try_write(self, try_id, rel, text):
        self.writes.append(rel)
        return {"ok": True}

    def try_add(self, try_id, built_with, manifest=None):
        self.added = manifest
        return {"ok": True, "id": "app"}


def _try(tmp_path, start, ran=()):
    folder = tmp_path / "app"
    folder.mkdir(exist_ok=True)
    link = StepLink()
    t = SimpleNamespace(cand={"name": "App", "full_name": "x/app", "url": "https://github.com/x/app", "runtime": "node"},
                        result={"port": 18555, "path": "/"}, pkg_id="app", compose_project="", compose_files=[],
                        started_lines=[start] if start else [], port=18555, folder=folder,
                        ran=[{"command": c, "ok": True, "build": True} for c in ran], human_steps=[], notes=[],
                        link=link, job={"id": "b-0"}, try_id="t1", built_with="local")
    return t, link


def test_an_unsaveable_start_line_fails_that_app_plainly_and_raises_a_step(tmp_path):
    t, link = _try(tmp_path, "npm start | tee log.txt")
    got = adopt.install(t, link)
    assert not got["ok"] and "needs a shell" in got["said"] and link.writes == []        # no manifest written
    (job_id, step, try_id), = link.steps
    assert (job_id, try_id) == ("b-0", "t1") and step["text"] == "npm start | tee log.txt"
    assert step["kind"] == "confirm" and "Check how App starts" == step["title"]
    assert any("needs a shell" in n for n in t.notes)


def test_unsaveable_setup_steps_are_listed_on_a_step_and_the_rest_installs(tmp_path):
    t, link = _try(tmp_path, "npm start", ran=["npm install; npm run build", "npm install"])
    got = adopt.install(t, link)
    # The manifest goes with the add (the Core writes .buildbot/puffpai.json); Toolsmith writes no manifest file.
    assert got["ok"] and link.writes == [] and link.added["id"] == "app"
    (_job, step, _try_id), = link.steps
    assert step["text"] == "npm install; npm run build"
    assert any("needs a shell" in n for n in t.notes)


def test_the_cores_steps_fit_lane_l5s_real_checklist(tmp_path, state):
    """Every step shape the Core files for Toolsmith is accepted by L5's Checklist (trust core)."""
    from puffpai_core.api import create_app
    from puffpai_core.zone3.toolsmith import programs
    from puffpai_core.zone3.toolsmith_host.service import _step
    app = create_app(state)
    checklist = app.extensions.get("puffpai.checklist")
    if checklist is None:
        pytest.skip("no Checklist in this Core")
    p = Ports(state, checklist=checklist)
    shapes = [
        {"id": "ts-outside-1a2b", "kind": "confirm", "title": "Let Toolsmith reach outside App's folder",
         "why": "To get App working, Toolsmith wants to run this command.", "text": "cp ../shared/x ."},
        {"id": "ts-unsaved-app", "kind": "confirm", "title": "Check how App starts", "why": "It needs a shell.",
         "text": "npm start | tee log.txt"},
        _step(programs.step_for("ruby", "App needs it.")),
        _step({"id": "ts-ab12", "kind": "open_url", "title": "Sign up", "get_it": "https://example.org/signup"}),
    ]
    for s in shapes:
        stored = p.checklist_add(s, "toolsmith", ["job:b-0"])
        assert stored["raised_by"] == "toolsmith" and p.checklist_state(stored["id"]) == "open"
    ruby = _step(programs.step_for("ruby", "App needs it."))
    assert ruby["check"]["command"][0] == "ruby"                       # the Core's own check, kept
    made_up = _step({"id": "ts-x", "kind": "run_command", "title": "Run it", "text": "winget install x",
                     "check": {"command": ["powershell", "-c", "evil"]}})
    assert "check" not in made_up                                      # Toolsmith never picks a check program


@pytest.mark.skipif(not REAL3, reason="L3 is not merged into this branch")
def test_real_l3_outbound_screen_and_lease(state, tmp_path):
    """L3's real classifier and Archivist (merged from main): the calls this lane makes, end to end."""
    from puffpai_core.bus import Bus
    from puffpai_core.zone1 import archivist
    bus = Bus()
    p = Ports(state, bus=bus)
    got = p.screen_outbound(f"SSN 123-45-6789, key {KEY}, port 3000.")
    assert "123-45-6789" not in got and "[key]" in got and "port 3000" in got
    assert p.screen_outbound(got) == got
    a = archivist.for_state(state, bus)
    a.setup("tq-8Zr41-Lm9w")
    a.put_secret("toolsmith.anthropic", KEY)
    prov = sender.load_providers()["anthropic"]
    s = sender.FrontierSender(p, prov, model="claude-opus-5-5", build_id="b1", cap_usd=5,
                              ledger=sender.Ledger(tmp_path / "u.json"), consent_given=sender.consent_sha(prov))
    assert s._key("api.anthropic.com") == KEY
    handle = s._handle
    with pytest.raises(KeyUnavailable):
        p.key_value(handle, borrower="toolsmith", purpose="build:b1", host="collector.example.com")
    s.close()
    assert not a.live(handle)
    a.lock("done")
