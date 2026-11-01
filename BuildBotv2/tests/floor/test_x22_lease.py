"""Floor X22 (D41): a lease is one key, one holder, one purpose, its hosts and an end time."""

import pytest

from puffpai_core.zone1.archivist import LEASE_MAX_S, LeaseEnded, LeaseRefused
from puffpai_core.zone1.vault import Locked
from z1kit import PIN, FakeClock, canary_key, make


def _lent(state, clock=None):
    clock = clock or FakeClock()
    a, _, events, _ = make(state, clock=clock)
    key = canary_key()
    a.put_secret("TOOLSMITH_ANTHROPIC", key)
    h = a.lend("TOOLSMITH_ANTHROPIC", "toolsmith", "cloud_call", hosts=["api.anthropic.com"], job="build-7")
    return a, h, key, clock, events


def _use(a, h):
    return a.use(h, borrower="toolsmith", purpose="cloud_call", host="api.anthropic.com")


def test_lease_ends_on_hand_lock(state):
    a, h, key, *_ = _lent(state)
    assert _use(a, h) == key.encode()
    a.lock("owner")
    with pytest.raises(LeaseEnded):
        _use(a, h)
    assert a.leases() == []


def test_idle_lock_does_not_end_the_lease(state):
    a, h, key, clock, _ = _lent(state)
    clock.advance(a.vault.idle_s + 1)
    assert not a.is_open()                           # idle locked
    assert _use(a, h) == key.encode()                # the build keeps going


def test_lease_ends_after_three_hours(state):
    a, h, key, clock, _ = _lent(state)
    a.vault.set_idle_minutes(60)
    clock.advance(LEASE_MAX_S - 1)
    assert _use(a, h) == key.encode()
    clock.advance(2)
    with pytest.raises(LeaseEnded):
        _use(a, h)


@pytest.mark.parametrize("ttl", [24 * 3600, LEASE_MAX_S + 1, float("nan"), float("inf"), float("-inf"), 0, -5,
                                 "60", None, True])
def test_ttl_must_be_finite_and_at_most_three_hours(state, ttl):
    a, *_ = make(state)
    a.put_secret("SOME_KEY", canary_key())
    with pytest.raises(LeaseRefused):
        a.lend("SOME_KEY", "toolsmith", "cloud_call", ttl_s=ttl)
    assert a.leases() == []


def test_a_short_ttl_ends_on_time(state):
    clock = FakeClock()
    a, *_ = make(state, clock=clock)
    a.put_secret("SOME_KEY", canary_key())
    h = a.lend("SOME_KEY", "toolsmith", "cloud_call", ttl_s=60)
    clock.advance(59)
    assert a.live(h)
    clock.advance(2)
    assert not a.live(h)


def test_lease_ends_when_job_ends_or_key_changes(state):
    a, h, *_ = _lent(state)
    assert a.end_job("build-7") == 1
    with pytest.raises(LeaseEnded):
        _use(a, h)
    h2 = a.lend("TOOLSMITH_ANTHROPIC", "toolsmith", "cloud_call", hosts=["api.anthropic.com"])
    a.put_secret("TOOLSMITH_ANTHROPIC", canary_key())          # a new key ends the old lease
    with pytest.raises(LeaseEnded):
        _use(a, h2)


def test_wrong_holder_purpose_or_host_refused(state):
    a, h, *_ = _lent(state)
    with pytest.raises(LeaseRefused):
        a.use(h, borrower="steward", purpose="cloud_call", host="api.anthropic.com")
    with pytest.raises(LeaseRefused):
        a.use(h, borrower="toolsmith", purpose="device_action", host="api.anthropic.com")
    with pytest.raises(LeaseRefused):
        a.use(h, borrower="toolsmith", purpose="cloud_call", host="evil.example.com")


def test_lend_needs_the_vault_open_except_ticked_keys_at_package_start(state):
    a, *_ = make(state)
    a.put_secret("UNTICKED_KEY", canary_key())
    ticked = canary_key()
    a.put_secret("TICKED_KEY", ticked, unattended=True)
    a.lock()
    with pytest.raises(Locked):
        a.lend("UNTICKED_KEY", "toolsmith", "cloud_call")
    with pytest.raises(Locked):
        a.lend("TICKED_KEY", "toolsmith", "cloud_call")         # only a package start uses the ticked tier
    assert a.inject("hass", ["TICKED_KEY", "UNTICKED_KEY"]) == {"TICKED_KEY": ticked}
    assert a.end_package("hass") == 1                           # the lease ends when the package stops
    assert a.end_holder("pkg:hass") == 0


def test_card_backed_keys_never_unattended(state):
    from puffpai_core.zone1.vault import Refused
    a, *_ = make(state)
    with pytest.raises(Refused):
        a.put_secret("PAID_KEY", canary_key(), unattended=True, card_backed=True)
    a.put_secret("PAID_KEY", canary_key(), card_backed=True)
    with pytest.raises(Refused):
        a.vault.set_unattended("PAID_KEY", True)


def test_lease_events_are_core_only_and_valueless(state):
    a, h, key, _, events = _lent(state)
    _use(a, h)
    accessed = [m for m in events if m["type"] == "vault.accessed"]
    assert accessed and accessed[-1]["payload"]["sink"] == "lease"
    assert all(m["source"]["kind"] == "core" for m in events if m["type"].startswith("vault."))
    assert key not in str(events)


def test_verify_pin_for_floor_level_yes(state):
    a, *_ = make(state, open_=False)
    assert a.verify_pin(PIN) is True
    assert a.verify_pin("Wrong#pin99") is False
    assert not a.is_open()                                      # checking the PIN never opens the vault
