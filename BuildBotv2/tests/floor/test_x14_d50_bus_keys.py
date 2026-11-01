"""Floor X14, D50 on the Bus: one shared key rule with the gate (``secret_detector`` + ``token_rules``).

The bug: the Bus skipped ``generated_key`` hits and had no random-token rule, so
a random-looking key travelled in an event. Both directions: the shapes the Core
itself mints (``ev-`` message ids, sessions, hashes in hash fields, UUIDs, git
commit ids) still travel.
"""

import hashlib
import json
import secrets
import string
import uuid

import pytest

from puffpai_core.bus import Bus, Source

CORE = Source("core", "core:archivist")


def _alnum(n):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


@pytest.fixture
def bus():
    b = Bus()
    b.seen = []
    b.subscribe("*", b.seen.append, audience="core")
    return b


def _posture(value):
    return Bus.message("bridge.posture", {"state": "closed", "by": "owner", "hosts_changed": [value]}, CORE)


def _why(b):
    return [m["payload"]["why"] for m in b.seen if m["type"] == "bus.rejected"]


@pytest.mark.parametrize("make", [
    lambda: secrets.token_urlsafe(32),                                  # a random token, no prefix, no key word
    lambda: secrets.token_hex(32),                                      # 64 hex, not in a hash field
    lambda: "hf_" + _alnum(34),
    lambda: "ppt_" + secrets.token_urlsafe(32),
    lambda: "MY_VALUE=" + _alnum(28),                                   # generated_key, no longer skipped
    lambda: "the token is " + _alnum(14) + "9z",                        # next to a key word
    lambda: "sk-ant-api03-" + secrets.token_hex(6) + "\n" + secrets.token_hex(14),   # split over two lines
])
def test_random_looking_keys_never_travel(bus, make):
    value = make()
    assert bus.publish(_posture(value)) is False
    assert _why(bus) == ["key_shape"]
    assert value not in json.dumps(bus.seen) and value not in json.dumps(list(bus.audit_lines))


def test_a_key_as_a_dict_key_never_travels(bus):
    tok = secrets.token_urlsafe(32)
    msg = Bus.message("vault.unlocked", {"factor": "pin"}, CORE)
    msg["payload"] = {"factor": "pin", tok: 1}
    assert bus.publish(msg) is False and _why(bus) == ["key_shape"]


def test_message_ids_have_one_recognised_shape(bus):
    msg = Bus.message("vault.unlocked", {"factor": "pin"}, CORE)
    assert msg["id"].startswith("ev-") and len(msg["id"]) == 19
    assert bus.publish(msg) is True
    for other in (secrets.token_hex(8), "ev-" + secrets.token_hex(10), secrets.token_urlsafe(32)):
        bad = dict(msg, id=other)
        assert bus.publish(bad) is False
    assert _why(bus) == ["schema", "schema", "schema"]


def test_core_shapes_still_travel(bus):
    session = uuid.uuid4().hex[:16]
    assert bus.publish(Bus.message("vault.unlocked", {"factor": "pin", "session": session}, CORE)) is True
    card_sha = hashlib.sha256(b"the card").hexdigest()
    assert bus.publish(Bus.message("consent.requested", {
        "consent_id": str(uuid.uuid4()), "scope": {"kind": "send", "target": "pkg:freshtable"}, "floor": True,
        "card_sha": card_sha}, CORE)) is True
    assert bus.publish(Bus.message("device.changed", {
        "plan_id": secrets.token_hex(20), "consent_id": "c_" + secrets.token_hex(12), "applied": True,
        "read_back_ok": True, "new_hash": hashlib.sha256(b"cfg").hexdigest()}, CORE)) is True
    assert _why(bus) == []


def test_a_hash_shape_outside_a_hash_field_is_not_waved_through(bus):
    msg = Bus.message("consent.requested", {
        "consent_id": secrets.token_hex(32), "scope": {"kind": "send", "target": "pkg:freshtable"}, "floor": True,
        "card_sha": hashlib.sha256(b"x").hexdigest()}, CORE)
    assert bus.publish(msg) is False and _why(bus) == ["key_shape"]


def _with_version(hex32, version, variant="8"):
    return hex32[:12] + str(version) + hex32[13:16] + variant + hex32[17:]


def test_a_bare_32_hex_uuid_travels_only_when_it_parses(bus):
    """Re-check item 4: uuid4().hex was refused. Version 4 or 7 with the RFC variant only."""
    def send(value):
        return bus.publish(Bus.message("device.changed", {"plan_id": value, "consent_id": "c_1", "applied": True,
                                                          "read_back_ok": True}, CORE))
    assert send(uuid.uuid4().hex) is True
    assert send(_with_version(secrets.token_hex(16), 7, "a")) is True
    assert send(_with_version(secrets.token_hex(16), 1)) is False            # not a version allowed bare
    assert send(_with_version(secrets.token_hex(16), 4, "c")) is False       # wrong variant bits
    assert send(_with_version(secrets.token_hex(16), 0)) is False
    assert send(uuid.uuid4().hex.upper()) is False                            # not the lowercase form
    assert _why(bus) == ["key_shape"] * 4
