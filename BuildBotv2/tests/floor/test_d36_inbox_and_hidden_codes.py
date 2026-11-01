"""D36(a) (PB-001 shape): a secret typed before its home is ready is sealed and filed, never dropped.
D36(c): secrets are never echoed, and the recovery code typed back is hidden in the API contract."""

import pytest
import yaml

from conftest import REPO, FakeMachine
from puffpai_core.api import create_app
from puffpai_core.zone1.archivist import Archivist
from z1kit import PIN, canary_key, every_byte_under, make

OPENAPI = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
SECRET_FIELDS = {"pin", "new_pin", "secret", "code", "value"}


def test_pb001_key_sealed_while_locked_then_filed(state):
    a, *_ = make(state, open_=False)
    key = canary_key()
    out = a.put_secret("TOOLSMITH_ANTHROPIC", key)          # the wizard's key, vault idle-locked since step 3
    assert out == {"saved": False, "kept": "inbox"}
    assert a.inbox.pending() == 1
    assert key.encode() not in every_byte_under(state)       # sealed, never plain on disk
    a.unlock("pin", PIN)
    assert a.inbox.pending() == 0
    assert a.vault.get("TOOLSMITH_ANTHROPIC") == key.encode()
    h = a.lend("TOOLSMITH_ANTHROPIC", "toolsmith", "cloud_call")   # the plan Yes takes the lease
    assert a.use(h, borrower="toolsmith", purpose="cloud_call") == key.encode()


def test_pb001_key_typed_before_setup_is_filed_by_setup(state):
    a = Archivist(state, machine=FakeMachine())
    key = canary_key()
    out = a.inbox.seal("TOOLSMITH_ANTHROPIC", key)
    assert out == {"kept": "memory"}                          # honest: lost if the Core restarts before setup
    a.setup(PIN)
    assert a.vault.get("TOOLSMITH_ANTHROPIC") == key.encode()


def test_held_value_keeps_its_ref_through_the_inbox(state):
    a, *_ = make(state, open_=False)
    ref = a.hold("415-555-0137", "person.phone")
    a.unlock("pin", PIN)
    assert a.resolve(ref) == "415-555-0137"


def test_tampered_box_is_set_aside_not_filed(state):
    a, *_ = make(state, open_=False)
    a.put_secret("SOME_KEY", canary_key())
    (box,) = list(a.inbox.dir.glob("*.box"))
    box.write_text(box.read_text().replace('"ct": "', '"ct": "00'), encoding="utf-8")
    a.unlock("pin", PIN)
    assert a.inbox.set_aside == 1
    assert [i["name"] for i in a.vault.items()] == []


def _schema(ref):
    node = OPENAPI
    for part in ref["$ref"].lstrip("#/").split("/"):
        node = node[part]
    return node


def test_secret_fields_are_hidden_in_the_contract():
    seen = 0
    for path, item in OPENAPI["paths"].items():
        if not path.startswith("/v1/vault/"):
            continue
        for op in item.values():
            if not isinstance(op, dict) or "requestBody" not in op:
                continue
            body = _schema(op["requestBody"]) if "$ref" in op["requestBody"] else op["requestBody"]
            props = body["content"]["application/json"]["schema"]["properties"]
            for name, spec in props.items():
                if name in SECRET_FIELDS:
                    seen += 1
                    assert spec.get("writeOnly") is True and spec.get("format") == "password", (path, name)
    assert seen >= 8
    assert "/v1/vault/recovery-code/test" in OPENAPI["paths"]


def test_routes_never_echo_what_was_typed(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    r = client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev)
    code = r.json["recovery_code"]
    assert r.headers["Cache-Control"] == "no-store"
    bodies = []
    for typed in (code, code.lower(), "WRONG-CODE0-00000-00000-00000"):
        r = client.post("/v1/vault/recovery-code/test", json={"code": typed}, headers=dev)
        assert set(r.json) == {"ok"}
        bodies.append(r.get_data(as_text=True))
    assert [b.count("true") for b in bodies] == [1, 1, 0]
    weak = client.post("/v1/vault/pin", json={"pin": PIN, "new_pin": "12345678"}, headers=dev)
    wrong = client.post("/v1/vault/unlock", json={"method": "pin", "secret": "Wrong#pin99"}, headers=dev)
    for body in bodies + [weak.get_data(as_text=True), wrong.get_data(as_text=True)]:
        for typed in (code, code.lower(), "12345678", "Wrong#pin99", PIN):
            assert typed not in body
    assert code not in client.get("/v1/vault/status", headers=dev).get_data(as_text=True)
    assert code.encode() not in every_byte_under(state)       # the code is never stored in the clear


@pytest.mark.parametrize("route", ["/v1/vault/setup", "/v1/vault/unlock", "/v1/vault/recover",
                                   "/v1/vault/recovery-code/test", "/v1/vault/ways-in"])
def test_secret_entry_needs_a_paired_device(state, route):
    app = create_app(state)
    core = app.extensions["puffpai"]
    owner_key = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    r = app.test_client().post(route, json={"pin": PIN}, headers=owner_key)
    assert r.status_code == 403


def test_a_box_never_replaces_a_saved_key_without_her_pin(state):
    """Verifier item 9: anyone with the public key can seal a box; filing it must not swap a real key."""
    from puffpai_core.zone1.vault import WrongSecret
    a, *_ = make(state)
    real = canary_key()
    a.put_secret("HASS_TOKEN", real)
    a.lock()
    a.inbox.seal("HASS_TOKEN", "planted-" + canary_key())    # e.g. a same-user program sealing its own value
    a.unlock("pin", PIN)
    assert a.vault.get("HASS_TOKEN") == real.encode()
    assert a.inbox_conflicts == ["HASS_TOKEN"] and a.inbox.pending() == 1
    a.lock()
    a.unlock("pin", PIN)                                       # it keeps waiting, still sealed
    assert a.vault.get("HASS_TOKEN") == real.encode() and a.inbox.pending() == 1
    with pytest.raises(WrongSecret):
        a.file_replacing("HASS_TOKEN", "Wrong#pin99")
    assert a.vault.get("HASS_TOKEN") == real.encode()
    assert a.file_replacing("HASS_TOKEN", PIN) is True        # her floor-level Yes
    assert a.vault.get("HASS_TOKEN").startswith(b"planted-") and a.inbox.pending() == 0


def test_inbox_file_route_needs_the_pin(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev)
    client.put("/v1/vault/secrets/MY_KEY", json={"value": canary_key()}, headers=dev)
    client.post("/v1/vault/lock", headers=dev)
    client.put("/v1/vault/secrets/MY_KEY", json={"value": canary_key(), "seal_if_locked": True}, headers=dev)
    client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN}, headers=dev)
    assert client.get("/v1/vault/status", headers=dev).json["inbox_conflicts"] == ["MY_KEY"]
    assert client.post("/v1/vault/inbox/file", json={"name": "MY_KEY", "pin": "Wrong#pin99"},
                       headers=dev).status_code == 401
    r = client.post("/v1/vault/inbox/file", json={"name": "MY_KEY", "pin": PIN}, headers=dev)
    assert r.status_code == 200 and r.json == {"replaced": True}


def test_a_held_box_never_replaces_a_held_value(state):
    a, *_ = make(state, open_=False)
    ref = a.hold("415-555-0137", "person.phone")
    a.unlock("pin", PIN)
    a.lock()
    a.inbox.seal_held(ref, "planted text", label="person.phone", tier="P2", source_ref=None, expire_days=None)
    a.unlock("pin", PIN)
    assert a.resolve(ref) == "415-555-0137"
    assert a.inbox.set_aside == 1 and a.inbox.pending() == 0
