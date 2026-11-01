"""Floor X1, X15 (Z1V F1 to F5, F9, F12): ciphertext only, records bound to names, one bad blob loses one item."""

import json
import shutil

import pytest

from puffpai_core.zone1 import crypto
from puffpai_core.zone1.vault import ItemTampered, RolledBack
from z1kit import PIN, canary_key, every_byte_under, make


def _blob_file(a, name):
    return a.vault.store.folder / (a.vault._blob_id(name) + ".blob")


def test_store_sees_ciphertext_only(state):
    a, *_ = make(state)
    key = canary_key()
    a.put_secret("CANARY_KEY", key)
    a.hold("Sam Canaryfield 415-555-0199", "person.phone")
    store_bytes = every_byte_under(a.vault.store.folder)
    keyring = a.vault.keyring_path.read_bytes()
    for blob in (store_bytes, keyring):
        assert key.encode() not in blob
        assert b"CANARY_KEY" not in blob                  # blob ids are HMACs: the store never learns names
        assert b"415-555-0199" not in blob


def test_crypto_self_test_and_aad_binding():
    crypto.self_test()
    dek = crypto.new_key()
    blob = crypto.seal(dek, "v1", "ia", 3, b"hello")
    assert crypto.open_blob(dek, "v1", "ia", 3, blob) == b"hello"
    for args in (("v1", "ib", 3), ("v1", "ia", 2), ("v2", "ia", 3)):
        with pytest.raises(crypto.Tampered):
            crypto.open_blob(dek, *args, blob)


def test_unknown_algorithm_and_weak_kdf_refused():
    with pytest.raises(crypto.UnknownAlgorithm):
        crypto.seal(crypto.new_key(), "v", "b", 1, b"x", alg="aes-128-cbc/0")
    with pytest.raises(crypto.WeakKdf):
        crypto.wrap(crypto.new_key(), "secret", b"aad", {"alg": "argon2id", "m": 1024, "t": 1, "p": 1})
    with pytest.raises(crypto.WeakKdf):
        crypto.wrap(crypto.new_key(), "secret", b"aad", {"alg": "pbkdf2", "m": 0, "t": 600000, "p": 1})
    assert crypto.KDF_FLOOR == {"alg": "argon2id", "m": 65536, "t": 3, "p": 1}


def test_fresh_subkey_and_nonce_per_write():
    dek = crypto.new_key()
    one = crypto.seal(dek, "v", "b", 1, b"same")
    two = crypto.seal(dek, "v", "b", 1, b"same")
    assert one != two and one[:-16] != two[:-16]


def test_swapped_records_are_detected(state):
    """Records are bound to their names: two blobs swapped on disk read as tampered, never as each other."""
    a, *_ = make(state)
    a.put_secret("ALPHA_KEY", canary_key())
    a.put_secret("BETA_KEY", canary_key())
    fa, fb = _blob_file(a, "ALPHA_KEY"), _blob_file(a, "BETA_KEY")
    da, db = fa.read_bytes(), fb.read_bytes()
    fa.write_bytes(db)
    fb.write_bytes(da)
    for name in ("ALPHA_KEY", "BETA_KEY"):
        with pytest.raises(ItemTampered):
            a.vault.get(name)


def test_swap_with_forged_root_still_detected(state):
    """Even if the root's hashes were made to match, the AAD and the name inside each blob refuse a swap."""
    a, *_ = make(state)
    a.put_secret("ALPHA_KEY", "alpha-value-1234")
    a.put_secret("BETA_KEY", "beta-value-5678")
    v = a.vault
    ia, ib = v._blob_id("ALPHA_KEY"), v._blob_id("BETA_KEY")
    fa, fb = _blob_file(a, "ALPHA_KEY"), _blob_file(a, "BETA_KEY")
    da, db = fa.read_bytes(), fb.read_bytes()
    fa.write_bytes(db)
    fb.write_bytes(da)
    # Forge what a hostile store could not (it has no DEK), to test the inner bindings alone.
    v._root["items"][ia]["sha"] = crypto.sha256(db[8:])
    v._root["items"][ia]["v"] = int.from_bytes(db[:8], "big")
    with pytest.raises(ItemTampered):
        v.get("ALPHA_KEY")
    assert ib in v._root["items"]


def test_one_bad_blob_loses_one_item_only(state):
    a, *_ = make(state)
    values = {f"KEY_{i}": canary_key() for i in range(5)}
    for name, value in values.items():
        a.put_secret(name, value)
    bad = _blob_file(a, "KEY_2")
    data = bytearray(bad.read_bytes())
    data[-5] ^= 0xFF
    bad.write_bytes(bytes(data))
    a.lock()
    a.unlock("pin", PIN)
    for name, value in values.items():
        if name == "KEY_2":
            with pytest.raises(ItemTampered):
                a.vault.get(name)
        else:
            assert a.vault.get(name) == value.encode()


def test_one_item_per_blob_not_whole_store_rewrite(state):
    a, *_ = make(state)
    a.put_secret("FIRST_KEY", canary_key())
    first = _blob_file(a, "FIRST_KEY").read_bytes()
    for i in range(3):
        a.put_secret(f"OTHER_{i}", canary_key())
    assert _blob_file(a, "FIRST_KEY").read_bytes() == first       # untouched by later writes


def test_rollback_is_detected(state, tmp_path):
    """A store served back in time (old root and blobs) stops with the plain question, F12."""
    a, *_ = make(state)
    a.put_secret("OLD_KEY", canary_key())
    snapshot = tmp_path / "old"
    shutil.copytree(a.vault.store.folder, snapshot)
    a.put_secret("NEW_KEY", canary_key())
    a.lock()
    shutil.rmtree(a.vault.store.folder)
    shutil.copytree(snapshot, a.vault.store.folder)
    with pytest.raises(RolledBack) as e:
        a.unlock("pin", PIN)
    assert "went back in time" in e.value.message
    assert not a.is_open()
    a.unlock("pin", PIN, accept_older=True)          # her Yes for a real restore
    assert [i["name"] for i in a.vault.items()] == ["OLD_KEY"]


def test_dek_never_wrapped_by_machine_alone(state):
    a, *_ = make(state)
    a.put_secret("TICKED_KEY", canary_key(), unattended=True)
    ring = json.loads(a.vault.keyring_path.read_text())
    assert {w["kind"] for w in ring["ways"]} == {"pin", "recovery"}
    assert all("kdf" in w for w in ring["ways"])                  # every way in is a PIN or code wrap
    assert set(ring["sdk"]) - {"u_index"} == {"by_dek", "by_machine"}   # the machine wraps only the SDK
    index = ring["sdk"]["u_index"]                                # ids of ticked copies, never key bytes
    assert all(isinstance(v, str) and len(v) == 41 and v.startswith("u") for v in index.values())
