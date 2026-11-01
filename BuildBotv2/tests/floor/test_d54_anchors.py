"""D54: Core-only anchors sealed with the vault key, and an unlock hook for the Bridge (lane L4).

The Bridge keeps its policy version and ledger position (n, head) here and
compares them at every unlock, so a same-user program that rolls back
PuffPAI's own files is caught then.
"""

import json

import pytest

from puffpai_core.api import create_app
from puffpai_core.zone1.archivist import ANCHOR_PREFIX
from puffpai_core.zone1.vault import ItemTampered, Locked, SafeMode
from z1kit import PIN, canary_key, every_byte_under, make

ANCHOR = {"policy_version": 7, "ledger": {"n": 41, "head": "9f" * 32}}


def test_anchor_round_trip_and_survives_lock_and_recover(state):
    a, _, _, code = make(state)
    assert a.anchor_get("bridge") is None                              # none yet
    assert a.anchor_put("bridge", ANCHOR) is True
    assert a.anchor_get("bridge") == ANCHOR
    a.lock()
    a.unlock("pin", PIN)
    assert a.anchor_get("bridge") == ANCHOR
    a.lock()
    a.vault.recover(code, "Ocean#Lamp42")                              # a re-key keeps it
    assert a.anchor_get("bridge") == ANCHOR


def test_anchor_is_sealed_hidden_and_not_a_key(state):
    a, *_ = make(state)
    marker = "anchor-canary-" + canary_key()[-16:]
    a.anchor_put("bridge", {"note": marker})
    assert marker.encode() not in every_byte_under(state)              # sealed with the vault key
    assert a.vault.items() == []                                       # reserved: not listed
    assert a.vault.status()["items"] == 0
    a.lock()
    assert a.inject("bridge", [ANCHOR_PREFIX + "bridge"]) == {}        # never lent as a key


def test_anchor_put_while_locked_keeps_nothing(state):
    a, *_ = make(state)
    a.anchor_put("bridge", ANCHOR)
    a.lock()
    before = a.inbox.pending()
    assert a.anchor_put("bridge", {"policy_version": 1}) is False
    assert a.inbox.pending() == before                                 # nothing queued, not even the inbox
    with pytest.raises(Locked):
        a.anchor_get("bridge")                                         # locked is never "no anchor"
    a.unlock("pin", PIN)
    assert a.anchor_get("bridge") == ANCHOR                            # the locked write left no trace


def test_anchor_in_safe_mode_is_not_readable_or_writable(state):
    a, *_ = make(state)
    a.anchor_put("bridge", ANCHOR)
    a.lock()
    path = state / "vault" / "keyring.json"
    ring = json.loads(path.read_text(encoding="utf-8"))
    ring["ways"][0]["label"] = "Renamed outside"
    path.write_text(json.dumps(ring), encoding="utf-8")
    with pytest.raises(SafeMode):
        a.unlock("pin", PIN)
    assert a.anchor_put("bridge", {"policy_version": 1}) is False
    with pytest.raises(SafeMode):
        a.anchor_get("bridge")


def test_a_tampered_anchor_reads_as_tampered_never_as_missing(state):
    a, *_ = make(state)
    a.anchor_put("bridge", ANCHOR)
    blob = next(p for p in a.vault.store.folder.glob("*.blob") if p.read_bytes() and
                a.vault._blob_id(ANCHOR_PREFIX + "bridge") in p.name)
    raw = bytearray(blob.read_bytes())
    raw[-1] ^= 1
    blob.write_bytes(bytes(raw))
    with pytest.raises(ItemTampered):
        a.anchor_get("bridge")


@pytest.mark.parametrize("name,value", [("Bridge", {}), ("../x", {}), ("", {}), ("a" * 65, {}),
                                        ("bridge", ["not", "a", "dict"]), ("bridge", {"x": float("nan")}),
                                        ("bridge", {"x": "y" * 20000})])
def test_bad_anchor_names_and_values_refused(state, name, value):
    a, *_ = make(state)
    with pytest.raises(ValueError):
        a.anchor_put(name, value)


def test_on_unlock_runs_after_each_real_unlock_only(state):
    a, *_ = make(state, open_=False)
    seen = []
    remove = a.on_unlock(lambda arch: seen.append(arch.anchor_get("bridge")))
    with pytest.raises(Exception):
        a.unlock("pin", "Wrong#pin99")
    assert seen == []                                                  # not on a failed unlock
    a.unlock("pin", PIN)
    assert seen == [None]
    a.anchor_put("bridge", ANCHOR)
    a.lock()
    a.unlock("pin", PIN)
    assert seen == [None, ANCHOR]                                      # the Bridge compares its version here
    remove()
    a.lock()
    a.unlock("pin", PIN)
    assert len(seen) == 2


def test_on_unlock_not_called_in_safe_mode(state):
    a, *_ = make(state, open_=False)
    seen = []
    a.on_unlock(lambda arch: seen.append(1))
    path = state / "vault" / "keyring.json"
    ring = json.loads(path.read_text(encoding="utf-8"))
    ring["ways"] = [w for w in ring["ways"] if w["kind"] != "recovery"]
    path.write_text(json.dumps(ring), encoding="utf-8")
    with pytest.raises(SafeMode):
        a.unlock("pin", PIN)
    assert seen == []
    a.vault.rotate_recovery(PIN)                                       # the repair opens it for real
    assert seen == [1]


def test_a_crashing_unlock_hook_does_not_block_the_unlock(state):
    a, *_ = make(state, open_=False)

    def broken(_arch):
        raise RuntimeError("bridge check crashed")
    a.on_unlock(broken)
    a.unlock("pin", PIN)
    assert a.is_open()
    assert "unlock_hook" in (state / "logs" / "vault.log").read_text(encoding="utf-8")


def test_no_route_or_token_reaches_anchors(state, freshtable):
    app = create_app(state)
    assert not [r.rule for r in app.url_map.iter_rules() if "anchor" in r.rule.lower()]
    from puffpai_core.registry import row_from_manifest
    core = app.extensions["puffpai"]
    core.registry.add(row_from_manifest(freshtable, "made"))
    client = app.test_client()
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    pkg = {"Authorization": "Bearer " + core.tokens.issue_package_token("freshtable")}
    client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev)
    for headers in (dev, pkg):
        r = client.put("/v1/vault/secrets/" + ANCHOR_PREFIX + "bridge", json={"value": "x"}, headers=headers)
        assert r.status_code in (400, 403, 404)
        r = client.delete("/v1/vault/secrets/" + ANCHOR_PREFIX + "bridge", headers=headers)
        assert r.status_code in (400, 403, 404)
