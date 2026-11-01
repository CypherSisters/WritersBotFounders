"""Core API groups bridge, consent and audit (lane L4)."""

import pytest

from puffpai_core.api import create_app
from puffpai_core.zone3.service import for_app
from z3helpers import manifest


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


@pytest.fixture
def app(state):
    return create_app(state)


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


def test_the_owner_answers_a_card_and_the_grant_appears(app):
    core = app.extensions["puffpai"]
    c = app.test_client()
    owner = _bearer(core.tokens.issue_owner_key())
    device = _bearer(core.tokens.issue_device("Sam's laptop"))
    z = for_app(app)
    card = z.bridge.package_card(manifest(hosts=["api.example.com"]))
    r = c.post(f"/v1/consent/{card['consent_id']}/answer", headers=owner, json={"answer": "yes"})
    assert r.status_code == 403, "an API key never answers a card"
    r = c.post(f"/v1/consent/{card['consent_id']}/answer", headers=device,
               json={"answer": "yes", "factor": "tap", "card_sha": card["card_sha"]})
    assert r.status_code == 200 and r.json["factor"] == "tap"
    body = c.get("/v1/bridge", headers=owner).json
    assert body["state"] == "closed" and [g["host"] for g in body["grants"]] == ["api.example.com"]
    assert c.post("/v1/bridge/open", headers=owner).json["state"] == "open"
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    ledger = c.get("/v1/bridge/ledger?limit=5", headers=owner).json
    assert ledger and ledger[0]["host"] == "api.example.com" and "req_id" in ledger[0]
    consents = c.get("/v1/consent", headers=owner).json
    assert [x["scope"]["kind"] for x in consents] == ["package"]
    assert c.delete(f"/v1/consent/{card['consent_id']}", headers=owner).status_code == 200
    assert not z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    assert c.post("/v1/bridge/close", headers=owner).json["state"] == "closed"


def test_a_card_answered_twice_or_changed_is_refused(app):
    core = app.extensions["puffpai"]
    c = app.test_client()
    device = _bearer(core.tokens.issue_device("kiosk"))
    card = for_app(app).bridge.package_card(manifest(hosts=["api.example.com"]))
    r = c.post(f"/v1/consent/{card['consent_id']}/answer", headers=device, json={"answer": "yes", "card_sha": "ab" * 32})
    assert r.status_code == 409 and r.json["code"] == "card_changed"
    r = c.post(f"/v1/consent/{card['consent_id']}/answer", headers=device, json={"answer": "yes"})
    assert r.status_code == 400 and r.json["code"] == "no_card_sha", "a Yes must name the card it answers"
    yes = {"answer": "yes", "card_sha": card["card_sha"]}
    assert c.post(f"/v1/consent/{card['consent_id']}/answer", headers=device, json=yes).status_code == 200
    assert c.post(f"/v1/consent/{card['consent_id']}/answer", headers=device, json=yes).status_code == 409


def test_a_floor_card_answered_with_a_tap_is_403(app):
    core = app.extensions["puffpai"]
    c = app.test_client()
    device = _bearer(core.tokens.issue_device("kiosk"))
    card = for_app(app).consents.ask("package", "rawreader", {}, floor=True)
    r = c.post(f"/v1/consent/{card['consent_id']}/answer", headers=device, json={"answer": "yes", "factor": "tap",
                                                                                    "card_sha": card["card_sha"]})
    assert r.status_code == 403 and r.json["code"] == "needs_pin"


def test_audit_route_shows_drawbridge_lines(app):
    core = app.extensions["puffpai"]
    c = app.test_client()
    owner = _bearer(core.tokens.issue_owner_key())
    z = for_app(app)
    z.drawbridge.review_action("nothing", "turn_on", {})
    body = c.get("/v1/audit", headers=owner).json
    assert body["chain_ok"] and body["lines"][0]["actor"] == "drawbridge" and body["lines"][0]["verdict"] == "deny"
