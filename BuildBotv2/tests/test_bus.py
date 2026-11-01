import json
import time

import pytest

from conftest import REPO
from puffpai_core.bus import Bus, Envelope, Source

CORE = Source("core", "core:archivist")
AGENT = Source("agent", "drawbridge")
PROVIDER = Source("provider", "evil-store")
PACKAGE = Source("package", "freshtable")
KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


@pytest.fixture
def bus():
    b = Bus()
    b.seen = []
    b.subscribe("*", b.seen.append, audience="core")
    return b


def rejected(b):
    return [m for m in b.seen if m["type"] == "bus.rejected"]


def test_contract_is_complete():
    data = json.loads((REPO / "contracts" / "events.json").read_text(encoding="utf-8"))
    for name, spec in data["messages"].items():
        assert spec["form"] in ("event", "check") and spec["publishers"] and spec["visible_to"], name
        if spec["form"] == "check":
            assert spec["safe_verdict"] in spec["verdicts"], name
    for name in ("vault.locked", "egress.decision", "consent.granted", "provider.installed"):
        assert data["messages"][name]["publishers"] == ["core"]


@pytest.mark.parametrize("type_, payload", [
    ("consent.granted", {"consent_id": "c1", "scope": {"kind": "host", "target": "pkg:freshtable"}, "factor": "tap"}),
    ("vault.unlocked", {"factor": "pin"}),
    ("egress.decision", {"req_id": "r1", "verdict": "allow", "by": "owner", "reason_code": "granted"}),
    ("provider.installed", {"slot": "zone1.vault-store", "id": "evil-store", "version": "1.0.0",
                            "origin": "store:community", "floor_suite": "pass"}),
])
@pytest.mark.parametrize("who", [PROVIDER, PACKAGE, AGENT])
def test_core_only_namespaces(bus, type_, payload, who):
    assert bus.publish(Bus.message(type_, payload, who)) is False
    assert not [m for m in bus.seen if m["type"] == type_]
    r = rejected(bus)[-1]["payload"]
    assert r["why"] == "publisher" and r["type"] == type_ and r["source"]["id"] == who.id


def test_core_publishes_and_audiences(bus):
    got = []
    bus.subscribe("vault.*", got.append, audience="package")
    assert bus.publish(Bus.message("vault.locked", {"reason": "idle"}, CORE))
    assert bus.publish(Bus.message("vault.accessed", {"ref": "r1", "sink": "lease", "holder": "toolsmith"}, CORE))
    assert [m["type"] for m in got] == ["vault.locked"]          # vault.accessed: Core and audit only
    assert {m["type"] for m in bus.seen} == {"vault.locked", "vault.accessed"}


def test_no_values_in_events(bus):
    ok = {"item": {"ref": "w1"}, "surfaces": {"chat_store": "done"}}
    assert bus.publish(Bus.message("data.wiped", ok, CORE))
    assert not bus.publish(Bus.message("data.wiped", {**ok, "value": "555-0134"}, CORE))
    assert rejected(bus)[-1]["payload"]["field"] == "payload.value"
    nested = {"item": {"ref": "w1", "text": "my phone"}, "surfaces": {}}
    assert not bus.publish(Bus.message("data.wiped", nested, CORE))
    assert not bus.publish(Bus.message("data.wiped", {**ok, "note": "x"}, CORE))     # unknown field
    assert not bus.publish(Bus.message("data.unknown", ok, CORE))                   # unknown type
    assert [r["payload"]["why"] for r in rejected(bus)] == ["schema"] * 4
    assert "555-0134" not in json.dumps(bus.seen)


def test_key_shape_drops_the_message(bus):
    msg = Bus.message("bridge.posture", {"state": "open", "by": KEY}, CORE)
    assert bus.publish(msg) is False
    r = rejected(bus)[-1]
    assert r["payload"]["why"] == "key_shape" and r["payload"]["field"] == "payload.by"
    assert KEY not in json.dumps(bus.seen) and KEY not in json.dumps(list(bus.audit_lines))


def test_hashes_and_ids_pass_the_key_check(bus):
    payload = {"consent_id": "c-" + "ab12" * 8, "scope": {"kind": "send", "target": "pkg:freshtable"},
               "floor": True, "card_sha": "0123456789abcdef" * 4}
    assert bus.publish(Bus.message("consent.requested", payload, CORE))


def egress(deadline_ms=200):
    return Bus.message("egress.request", {
        "req_id": "q1", "subject": "pkg:freshtable", "phase": "run", "dest": {"host": "tracker.example", "port": 443},
        "carries": {"labels": ["person.phone"]}}, CORE, deadline_ms=deadline_ms)


def test_check_without_answerer_is_no(bus):
    reply = bus.ask(egress())
    assert reply["verdict"] == "deny" and reply["reason_code"] == "no_answerer"


def test_check_deadline_is_no(bus):
    def slow(_m):
        time.sleep(2)
        return {"verdict": "allow", "reason_code": "granted"}

    bus.answer("egress.request", slow, AGENT)
    t0 = time.monotonic()
    reply = bus.ask(egress(deadline_ms=100))
    assert reply == {"id": reply["id"], "verdict": "deny", "reason_code": "no_answer", "by": "core:bus"}
    assert time.monotonic() - t0 < 1.5


def test_check_answers(bus):
    bus.answer("egress.request", lambda m: {"verdict": "ask", "reason_code": "needs_yes"}, AGENT)
    reply = bus.ask(egress())
    assert reply["verdict"] == "ask" and reply["by"] == "drawbridge"


@pytest.mark.parametrize("answer, code", [
    (lambda m: 1 / 0, "answer_error"),
    (lambda m: {"verdict": "maybe", "reason_code": "x"}, "bad_answer"),
    (lambda m: "allow", "bad_answer"),
])
def test_bad_answers_are_no(bus, answer, code):
    bus.answer("egress.request", answer, AGENT)
    reply = bus.ask(egress())
    assert reply["verdict"] == "deny" and reply["reason_code"] == code


def test_only_core_or_agents_answer_checks(bus):
    with pytest.raises(ValueError):
        bus.answer("egress.request", lambda m: {}, PROVIDER)
    with pytest.raises(ValueError):
        bus.answer("vault.locked", lambda m: {}, AGENT)


def test_bad_check_is_no(bus):
    msg = egress()
    del msg["deadline_ms"]
    assert bus.ask(msg)["reason_code"] == "rejected"


def test_envelope_changes_need_a_reviewer():
    class Steward:
        name = "steward"

        def handle(self, env):
            return {"ok": True}

    class Drawbridge:
        name = "drawbridge"

        def review(self, env, target):
            return {"allowed": env.intent == "start"}

        def handle(self, env):
            return {"ok": True}

    b = Bus()
    b.register(Steward())
    assert b.send(Envelope(intent="start", changes=True), "steward")["denied"] is True   # no reviewer = deny
    assert b.send(Envelope(intent="status"), "steward") == {"ok": True}
    b.register(Drawbridge())
    assert b.send(Envelope(intent="start", changes=True), "steward") == {"ok": True}
    assert b.send(Envelope(intent="wipe", changes=True), "steward")["denied"] is True
