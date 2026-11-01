"""Floor X14 (ledger and audit: Core-written, closed codes, no values, hash chain)
and X19 (labelled data stays: secrets never leave; personal data needs a Yes bound to the payload)."""

import json

import pytest

from puffpai_core.zone3.chain import ChainLog, verify
from puffpai_core.zone3.ledger import build_record
from z3helpers import grant, manifest, zone

KEY = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


# -- X14 ---------------------------------------------------------------------

def test_ledger_tamper_is_detected_and_closes_the_bridge(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    for _ in range(3):
        z.bridge.allowed("pkg:freshtable", "api.example.com")
    path = z.bridge.ledger.path
    assert verify(path) == (True, 0)
    lines = path.read_text(encoding="ascii").splitlines()
    edited = json.loads(lines[1])
    edited["code"] = "granted" if edited["code"] != "granted" else "not_on_list"
    lines[1] = json.dumps(edited, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    assert verify(path) == (False, 2)
    z.bridge.allowed("pkg:freshtable", "api.example.com")     # the next line written notices
    assert z.bridge.policy.fresh().state == "closed"
    assert any(c["kind"] == "ledger_damaged" for c in z.cards)
    assert list(path.parent.glob("outside.damaged-*.jsonl")), "the damaged file is kept aside, never deleted"
    assert verify(path) == (True, 0)
    assert json.loads(path.read_text(encoding="ascii").splitlines()[0])["event"] == "chain_restarted"


def test_a_cut_line_is_detected(state, tmp_path):
    log = ChainLog(tmp_path / "audit.jsonl")
    for i in range(4):
        log.append({"action": "x", "i": i})
    lines = log.path.read_text(encoding="ascii").splitlines()
    log.path.write_text("\n".join(lines[:2] + lines[3:]) + "\n", encoding="ascii")
    assert verify(log.path)[0] is False
    told = []
    log.on_tamper = told.append
    log.append({"action": "y"})
    assert told and told[0]["why"] == "head_mismatch"


def test_truncating_the_end_is_detected_by_the_head(tmp_path):
    log = ChainLog(tmp_path / "audit.jsonl")
    for i in range(3):
        log.append({"action": "x", "i": i})
    lines = log.path.read_text(encoding="ascii").splitlines()
    log.path.write_text("\n".join(lines[:2]) + "\n", encoding="ascii")   # still a valid chain on its own
    assert verify(log.path)[0] is True
    told = []
    log.on_tamper = told.append
    log.append({"action": "y"})
    assert told, "the in-memory head catches a cut at the end"


def test_ledger_lines_hold_codes_never_values(state):
    rec = build_record("pkg:freshtable", host=f"{KEY}.example.com", outcome="refused", code="my own words")
    assert rec["code"] == "not_allowed" and KEY not in json.dumps(rec)
    assert build_record("pkg:freshtable", host="x", outcome="leaked") is None
    assert build_record("free text subject", host="a.example.com", outcome="done") is None


def test_audit_has_no_write_route_and_packages_cannot_read_it(state):
    from puffpai_core.api import create_app
    app = create_app(state)
    core = app.extensions["puffpai"]
    c = app.test_client()
    pkg = {"Authorization": "Bearer " + core.tokens.issue_package_token("freshtable")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    for method, path in (("get", "/v1/audit"), ("get", "/v1/bridge"), ("post", "/v1/bridge/open"),
                         ("get", "/v1/bridge/ledger"), ("get", "/v1/consent"), ("delete", "/v1/consent/x"),
                         ("post", "/v1/consent/x/answer")):
        assert getattr(c, method)(path, headers=pkg).status_code == 403, path
    for method in ("post", "put", "delete", "patch"):
        assert getattr(c, method)("/v1/audit", headers=owner).status_code in (404, 405)
    r = c.get("/v1/audit", headers=owner)
    assert r.status_code == 200 and r.json["chain_ok"] is True


# -- X19 ---------------------------------------------------------------------

@pytest.fixture
def granted(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    return z


def test_secrets_never_leave_even_with_a_yes(granted):
    z = granted
    for label in ("secret.api_key", "secret.unknown", "auth.password"):
        d = z.bridge.allowed("pkg:freshtable", "api.example.com", [label])
        assert (d.verdict, d.by, d.reason_code) == ("deny", "floor", "labelled"), label
    card = z.consents.ask("send_always", "pkg:freshtable", bound={"package": "freshtable",
                                                                       "labels": ["secret.api_key"]})
    z.consents.verify_pin = lambda pin: True
    z.consents.answer(card["consent_id"], "yes", "pin", "x", card_sha=card["card_sha"])
    assert z.bridge.allowed("pkg:freshtable", "api.example.com", ["secret.api_key"]).reason_code == "labelled"


def test_personal_data_needs_a_yes_bound_to_that_payload(granted):
    z = granted
    sha_a, sha_b = "aa" * 32, "bb" * 32
    d = z.bridge.allowed("pkg:freshtable", "api.example.com", ["person.phone"], payload_sha=sha_a)
    assert (d.verdict, d.reason_code) == ("ask", "needs_yes")
    card = z.consents.ask("send", "pkg:freshtable", bound={"payload_sha": sha_a, "labels": ["person.phone"]})
    z.consents.verify_pin = lambda pin: True
    z.consents.answer(card["consent_id"], "yes", "pin", "x", card_sha=card["card_sha"])
    d = z.bridge.allowed("pkg:freshtable", "api.example.com", ["person.phone"], payload_sha=sha_a)
    assert (d.verdict, d.by) == ("allow", "owner")
    assert z.bridge.allowed("pkg:freshtable", "api.example.com", ["person.phone"],
                            payload_sha=sha_b).verdict == "ask", "a Yes never covers another payload"
    assert z.bridge.allowed("pkg:freshtable", "api.example.com", ["person.phone", "health.condition"],
                            payload_sha=sha_a).verdict == "ask", "nor more labels than it named"


def test_always_for_this_app_never_covers_p4(granted):
    z = granted
    card = z.consents.ask("send_always", "pkg:freshtable", bound={"package": "freshtable",
                                                                       "labels": ["person.phone"]})
    z.consents.verify_pin = lambda pin: True
    z.consents.answer(card["consent_id"], "yes", "pin", "x", card_sha=card["card_sha"])
    assert z.bridge.allowed("pkg:freshtable", "api.example.com", ["person.phone"]).allowed
    assert z.bridge.allowed("pkg:freshtable", "api.example.com", ["person.phone"],
                            top_tier="P4").verdict == "ask"
    assert not z.bridge.allowed("pkg:other", "api.example.com", ["person.phone"]).allowed


def test_a_broken_label_effect_reads_as_never(state):
    def broken(label):
        raise RuntimeError("label pack unreadable")
    z = zone(state, share_of=broken)
    grant(z, manifest(hosts=["api.example.com"]))
    assert z.bridge.allowed("pkg:freshtable", "api.example.com", ["person.phone"]).reason_code == "labelled"


def test_a_tainted_package_carries_its_labels_through_the_proxy_path(state):
    from puffpai_core.registry import Registry
    reg = Registry(state / "registry.db")
    reg.add({"id": "freshtable", "kind": "app", "name": "FreshTable", "origin": "made", "status": "running",
             "taint": ["person.phone"]})
    z = zone(state, registry=reg)
    grant(z, manifest(hosts=["api.example.com"]))
    d = z._proxy_decide(subject="pkg:freshtable", host="api.example.com", port=443, phase="run", scheme="https",
                        method="CONNECT")
    assert d.verdict == "ask"
