"""Floor X10 on the Bus path: only the Core mints consent; no answer is a no; no key travels."""

import json

import pytest

from puffpai_core.bus import Bus, Source

CORE_ONLY = [
    ("consent.granted", {"consent_id": "c1", "scope": {"kind": "send", "target": "pkg:x"}, "factor": "pin"}),
    ("consent.revoked", {"consent_id": "c1", "scope": {"kind": "send", "target": "pkg:x"}}),
    ("vault.unlocked", {"factor": "recovery"}),
    ("egress.decision", {"req_id": "r1", "verdict": "allow", "by": "owner", "reason_code": "granted"}),
    ("provider.changed", {"slot": "zone1.vault-store", "from": None, "to": "x-store", "migration": "verified"}),
]


@pytest.mark.parametrize("type_, payload", CORE_ONLY)
@pytest.mark.parametrize("kind", ["provider", "package", "agent"])
def test_only_the_core_publishes_core_namespaces(type_, payload, kind):
    bus = Bus()
    got = []
    bus.subscribe("*", got.append, audience="core")
    assert bus.publish(Bus.message(type_, payload, Source(kind, "intruder"))) is False
    assert [m["type"] for m in got] == ["bus.rejected"]
    assert bus.publish(Bus.message(type_, payload, Source("core", "core"))) is True


def test_no_answer_is_no():
    bus = Bus()
    check = Bus.message("device.proposal", {
        "plan_id": "p1", "device": "fw", "base_hash": "ab" * 16, "steps": [{"what": "block"}], "undo": [],
        "effects": {"touches_dns_dhcp": False, "touches_mgmt_path": False},
        "expires_at": "2026-10-21T12:00:00Z", "risk": "low"}, Source("core", "core"), deadline_ms=50)
    assert bus.ask(check)["verdict"] == "deny"


def test_a_key_never_travels():
    bus = Bus()
    got = []
    bus.subscribe("*", got.append, audience="core")
    key = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    assert bus.publish(Bus.message("bridge.posture", {"state": "closed", "by": "owner", "hosts_changed": [key]},
                                   Source("core", "core"))) is False
    assert key not in json.dumps(got) and key not in json.dumps(list(bus.audit_lines))
