"""Floor X2, X3, X9 (Z1V F6, F8, F10, F13, F14): PIN floor, throttle, idle lock, two ways in, locked means refuse."""

import shutil

import pytest

from puffpai_core.zone1.pin_rules import RECOVERY_CODE_ENTROPY_BITS, generate_recovery_code
from puffpai_core.zone1.vault import (IDLE_MAX_S, Locked, Refused, StoreMissing, Throttled, WeakSecret,
                                      WrongSecret)
from z1kit import PIN, FakeClock, canary_key, make


@pytest.mark.parametrize("weak", ["1234", "12345678", "abcdefgh", "11111111", "password", "9517538426",
                                  "qwertyuiop", "1470258369"])
def test_weak_pins_refused(state, weak):
    from puffpai_core.zone1.archivist import Archivist
    with pytest.raises(WeakSecret):
        Archivist(state).setup(weak)


def test_recovery_code_shape():
    code = generate_recovery_code()
    assert len(code) == 29 and code.count("-") == 4
    assert RECOVERY_CODE_ENTROPY_BITS >= 110


def test_throttle_before_kdf_and_counted_apart(state):
    clock = FakeClock()
    a, _, events, code = make(state, clock=clock, open_=False)
    for _ in range(5):
        with pytest.raises(WrongSecret):
            a.unlock("pin", "Wrong#pin99")
    with pytest.raises(WrongSecret):
        a.unlock("pin", "Wrong#pin99")
    with pytest.raises(Throttled) as e:
        a.unlock("pin", PIN)                      # even the right PIN waits: no KDF runs while throttled
    assert 0 < e.value.retry_after <= 300
    a.unlock("recovery", code)                    # the recovery path has its own counter
    a.lock()
    clock.advance(301)
    a.unlock("pin", PIN)
    fails = [m for m in events if m["type"] == "vault.unlock_failed"]
    assert fails and all(set(m["payload"]) == {"factor", "count", "backoff_s"} for m in fails)


def test_backoff_never_over_five_minutes(state):
    clock = FakeClock()
    a, *_ = make(state, clock=clock, open_=False)
    for _ in range(30):
        try:
            a.unlock("pin", "Wrong#pin99")
        except (WrongSecret, Throttled):
            pass
        clock.advance(400)
        assert a.vault.retry_after() <= 300


def test_idle_lock_always_on_and_capped(state):
    clock = FakeClock()
    a, _, events, _ = make(state, clock=clock)
    assert a.vault.set_idle_minutes(600) == 60                 # at most 60 minutes
    assert a.vault.idle_s == IDLE_MAX_S
    a.vault.set_idle_minutes(0)
    assert a.vault.idle_s >= 60                                # never off
    a.vault.set_idle_minutes(5)
    clock.advance(299)
    assert a.is_open()
    clock.advance(301)
    assert not a.is_open()
    assert [m["payload"]["reason"] for m in events if m["type"] == "vault.locked"] == ["idle"]


def test_locked_means_refuse_not_empty(state):
    a, *_ = make(state)
    a.put_secret("SOME_KEY", canary_key())
    a.lock()
    with pytest.raises(Locked):
        a.vault.get("SOME_KEY")
    with pytest.raises(Locked):
        a.vault.items()


def test_missing_store_means_wait_never_a_new_vault(state):
    a, *_ = make(state)
    a.put_secret("SOME_KEY", canary_key())
    a.lock()
    moved = state / "unplugged"
    shutil.move(str(a.vault.store.folder), str(moved))
    assert a.vault.status()["state"] == "missing"
    with pytest.raises(StoreMissing):
        a.unlock("pin", PIN)
    assert not a.vault.store.folder.exists()                   # nothing made a fresh empty store
    shutil.move(str(moved), str(a.vault.store.folder))
    a.unlock("pin", PIN)
    assert a.vault.get("SOME_KEY")


def test_two_ways_in_and_recovery_never_removed(state):
    a, *_ = make(state)
    ways = {w["id"]: w["kind"] for w in a.vault.ways_in()}
    assert ways == {"pin": "pin", "recovery": "recovery"}
    with pytest.raises(Refused):
        a.vault.remove_way("pin", pin=PIN)                     # would leave one
    with pytest.raises(Refused):
        a.vault.remove_way("recovery", pin=PIN)                # never removable
    extra = a.vault.add_way("passphrase", "Kitchen passphrase", "violet kettle under stairs", pin=PIN)
    a.vault.remove_way("pin", pin=PIN)                         # passphrase + recovery remain
    with pytest.raises(Refused):
        a.vault.remove_way(extra["id"], pin="violet kettle under stairs")   # the last PIN-like way stays
    with pytest.raises(Refused):
        a.vault.add_way("factor:yubikey", "Key", "x" * 12, pin=PIN)   # community factors: later, AND only
    a.lock()
    a.unlock("pin", "violet kettle under stairs")


def test_unlock_provider_alone_refused(state):
    a, *_ = make(state, open_=False)
    with pytest.raises(Refused):
        a.unlock("factor:any-provider", "00" * 32)
    assert not a.is_open()


def test_pin_change_is_a_rewrap_and_recover_rekeys(state):
    a, *_ = make(state)
    key = canary_key()
    a.put_secret("KEEP_KEY", key)
    a.vault.change_pin(PIN, "Ocean#Lamp42")
    a.lock()
    with pytest.raises(WrongSecret):
        a.unlock("pin", PIN)
    a.unlock("pin", "Ocean#Lamp42")
    assert a.vault.get("KEEP_KEY") == key.encode()


def test_recover_with_code(state):
    a, _, _, code = make(state)
    key = canary_key()
    a.put_secret("KEEP_KEY", key)
    a.lock()
    new_code = a.vault.recover(code, "Ocean#Lamp42")
    assert new_code != code
    a.lock()
    with pytest.raises(WrongSecret):
        a.unlock("recovery", code)                              # the old code no longer opens
    a.unlock("recovery", new_code)
    assert a.vault.get("KEEP_KEY") == key.encode()
    a.lock()
    a.unlock("pin", "Ocean#Lamp42")
