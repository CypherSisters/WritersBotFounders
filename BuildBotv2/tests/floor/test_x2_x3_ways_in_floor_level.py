"""Floor X2, X3, D29, D47 (lane L3 verifier items 2, 6, 7).

- Adding or removing a way in needs the current PIN or the recovery code (the
  bug: add a passphrase, then remove the PIN, with no PIN at all).
- The idle lock can't be turned off from settings.json (``"nan"`` did it).
- F10 on read: a keyring edited on disk to drop a way in opens only in safe mode.
"""

import json

import pytest

from puffpai_core.api import create_app
from puffpai_core.zone1.vault import IDLE_DEFAULT_S, Locked, SafeMode, WrongSecret
from z1kit import PIN, FakeClock, canary_key, make

SPARE = "violet kettle under stairs"


def test_add_and_remove_need_the_current_pin_or_recovery_code(state):
    a, _, _, code = make(state)
    with pytest.raises(WrongSecret):
        a.vault.add_way("passphrase", "Spare", SPARE)
    with pytest.raises(WrongSecret):
        a.vault.add_way("passphrase", "Spare", SPARE, pin="Wrong#pin99")
    way = a.vault.add_way("passphrase", "Spare", SPARE, code=code)       # the recovery code works too
    with pytest.raises(WrongSecret):
        a.vault.remove_way("pin")
    with pytest.raises(WrongSecret):
        a.vault.remove_way("pin", code="WRONG-CODE0-00000-00000-00000")
    assert {w["id"] for w in a.vault.ways_in()} == {"pin", "recovery", way["id"]}
    a.vault.remove_way(way["id"], pin=PIN)
    assert {w["id"] for w in a.vault.ways_in()} == {"pin", "recovery"}


def test_routes_add_then_remove_pin_needs_the_pin(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    code = client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).json["recovery_code"]
    body = {"kind": "passphrase", "label": "Spare", "secret": SPARE}
    assert client.post("/v1/vault/ways-in", json=body, headers=dev).status_code == 401
    r = client.post("/v1/vault/ways-in", json=body | {"pin": "Wrong#pin99"}, headers=dev)
    assert r.status_code == 401 and "Wrong#pin99" not in r.get_data(as_text=True)
    assert client.post("/v1/vault/ways-in", json=body | {"pin": PIN}, headers=dev).status_code == 201
    assert client.delete("/v1/vault/ways-in/pin", headers=dev).status_code == 401           # the verifier's repro
    assert client.delete("/v1/vault/ways-in/pin", json={"pin": SPARE + "x"}, headers=dev).status_code == 401
    kinds = [w["kind"] for w in client.get("/v1/vault/ways-in", headers=dev).json["ways_in"]]
    assert "pin" in kinds
    assert client.delete("/v1/vault/ways-in/pin", json={"code": code}, headers=dev).status_code == 200
    kinds = [w["kind"] for w in client.get("/v1/vault/ways-in", headers=dev).json["ways_in"]]
    assert sorted(kinds) == ["passphrase", "recovery"]


def test_way_in_checks_are_throttled(state):
    a, *_ = make(state, clock=FakeClock())
    for _ in range(6):
        with pytest.raises(WrongSecret):
            a.vault.add_way("passphrase", "Spare", SPARE, pin="Wrong#pin99")
    assert a.vault.retry_after() > 0


@pytest.mark.parametrize("bad", ["nan", "NaN", float("nan"), "inf", 1e309, -1, 0, 10, 99999, True, None, [], {},
                                 "300"])
def test_idle_lock_cannot_be_turned_off_from_settings(state, bad):
    clock = FakeClock()
    a, *_ = make(state, clock=clock)
    raw = json.dumps({"idle_s": bad}) if not isinstance(bad, float) else '{"idle_s": %s}' % (
        "NaN" if bad != bad else "Infinity")
    (state / "vault" / "settings.json").write_text(raw, encoding="utf-8")
    assert a.vault.idle_s == IDLE_DEFAULT_S
    clock.advance(IDLE_DEFAULT_S + 1)
    assert not a.is_open()


def test_idle_settings_file_that_is_not_an_object(state):
    a, *_ = make(state)
    (state / "vault" / "settings.json").write_text("[1, 2]", encoding="utf-8")
    assert a.vault.idle_s == IDLE_DEFAULT_S
    with pytest.raises(ValueError):
        a.vault.set_idle_minutes(float("nan"))


def _drop(state, kind):
    path = state / "vault" / "keyring.json"
    ring = json.loads(path.read_text(encoding="utf-8"))
    ring["ways"] = [w for w in ring["ways"] if w["kind"] != kind]
    path.write_text(json.dumps(ring), encoding="utf-8")


def test_keyring_edited_to_drop_the_recovery_way_opens_only_in_safe_mode(state):
    a, *_ = make(state)
    key = canary_key()
    a.put_secret("KEEP_KEY", key)
    a.lock()
    _drop(state, "recovery")
    st = a.vault.status()
    assert st["state"] == "safe_mode" and "safe mode" in st["message"]
    with pytest.raises(SafeMode):
        a.unlock("pin", PIN)                          # never a silent open
    assert not a.is_open()
    with pytest.raises(SafeMode):
        a.vault.get("KEEP_KEY")
    with pytest.raises((SafeMode, Locked)):
        a.lend("KEEP_KEY", "toolsmith", "cloud_call")
    assert a.vault.status()["state"] == "safe_mode"
    new_code = a.vault.rotate_recovery(PIN)          # the repair: a new recovery code, with the PIN
    assert a.is_open() and a.vault.status()["state"] == "open"
    assert a.vault.get("KEEP_KEY") == key.encode()
    a.lock()
    a.unlock("recovery", new_code)


def test_keyring_edited_to_drop_the_pin_repairs_with_the_recovery_code(state):
    a, _, _, code = make(state)
    a.put_secret("KEEP_KEY", canary_key())
    a.lock()
    _drop(state, "pin")
    with pytest.raises(SafeMode):
        a.unlock("recovery", code)
    new_code = a.vault.recover(code, "Ocean#Lamp42")
    assert a.is_open() and new_code != code
    a.lock()
    a.unlock("pin", "Ocean#Lamp42")


def test_safe_mode_route_answers_plainly(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev)
    client.post("/v1/vault/lock", headers=dev)
    _drop(state, "recovery")
    r = client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN}, headers=dev)
    assert r.status_code == 409 and r.json["code"] == "safe_mode" and "safe mode" in r.json["error"]
    assert client.get("/v1/vault/status", headers=dev).json["state"] == "safe_mode"


def _edit_ring(state, change):
    path = state / "vault" / "keyring.json"
    ring = json.loads(path.read_text(encoding="utf-8"))
    change(ring)
    path.write_text(json.dumps(ring), encoding="utf-8")


@pytest.mark.parametrize("change", [
    lambda r: r["ways"][0].update(label="Renamed outside"),         # F10 still holds; only the MAC can tell
    lambda r: r.update(inbox={"pub": "11" * 32}),                   # boxes would go to another key
    lambda r: r.pop("mac"),
    lambda r: r.update(mac="0" * 64),
])
def test_keyring_that_fails_its_mac_opens_only_in_safe_mode(state, change):
    a, *_ = make(state)
    key = canary_key()
    a.put_secret("KEEP_KEY", key)
    a.lock()
    _edit_ring(state, change)
    with pytest.raises(SafeMode) as e:
        a.unlock("pin", PIN)                                         # never "warn and go on"
    assert "safe mode" in e.value.message
    assert not a.is_open() and a.vault.status()["state"] == "safe_mode"
    with pytest.raises(SafeMode):
        a.vault.get("KEEP_KEY")
    a.vault.rotate_recovery(PIN)                                     # the repair re-MACs the keyring
    assert a.is_open() and a.vault.get("KEEP_KEY") == key.encode()
    a.lock()
    a.unlock("pin", PIN)                                             # opens normally again
    assert a.vault.status()["state"] == "open"


def test_mac_repair_drops_unattended_wraps_that_disagree(state):
    a, *_ = make(state)
    a.put_secret("HASS_TOKEN", canary_key(), unattended=True)
    a.lock()
    # a swapped machine wrap: another key than the one the vault-key wrap holds
    _edit_ring(state, lambda r: r["sdk"].update(by_machine=a.vault.machine.protect(b"\x01" * 32).hex()))
    with pytest.raises(SafeMode):
        a.unlock("pin", PIN)
    a.vault.rotate_recovery(PIN)
    ring = json.loads((state / "vault" / "keyring.json").read_text(encoding="utf-8"))
    assert ring["sdk"] is None                                       # she ticks the key again to bring it back


def test_safe_mode_from_a_bad_mac_says_so_over_the_api(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev)
    client.post("/v1/vault/lock", headers=dev)
    _edit_ring(state, lambda r: r["ways"][0].update(label="Renamed outside"))
    r = client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN}, headers=dev)
    assert r.status_code == 409 and r.json["code"] == "safe_mode"
    st = client.get("/v1/vault/status", headers=dev).json
    assert st["state"] == "safe_mode" and st["why"] == "keyring_changed"
