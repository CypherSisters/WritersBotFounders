"""Floor X22, D27, D41 (lane L3 verifier items 3 and 4): a running package gets a key only if it is ticked.

- ``inject`` hands out ticked keys only, vault open or locked (the bug: unticked
  keys while the vault was open).
- Its lease ends when the package stops (``end_package``, module seam ``end_lease``).
- ``package_env`` is Steward's purpose only: no other holder reaches the ticked
  tier while the vault is locked (the bug: any holder could).
"""

import pytest

from conftest import FakeMachine
from puffpai_core.zone1 import archivist as archivist_mod
from puffpai_core.zone1.archivist import LeaseRefused
from puffpai_core.zone1.vault import Locked
from z1kit import PIN, canary_key, make


@pytest.mark.parametrize("vault_open", [True, False])
def test_inject_hands_out_ticked_keys_only(state, vault_open):
    a, *_ = make(state)
    ticked, unticked = canary_key(), canary_key()
    a.put_secret("TICKED_KEY", ticked, unattended=True)
    a.put_secret("UNTICKED_KEY", unticked)
    if not vault_open:
        a.lock()
    env = a.inject("hass", ["TICKED_KEY", "UNTICKED_KEY", "MISSING_KEY"])
    assert env == {"TICKED_KEY": ticked}
    assert unticked not in str(env)


def test_untick_stops_the_next_start(state):
    a, *_ = make(state)
    a.put_secret("HASS_TOKEN", canary_key(), unattended=True)
    assert set(a.inject("hass", ["HASS_TOKEN"])) == {"HASS_TOKEN"}
    a.vault.set_unattended("HASS_TOKEN", False)
    assert a.inject("hass", ["HASS_TOKEN"]) == {}


def test_inject_lease_ends_when_the_package_stops(state):
    a, *_ = make(state)
    a.put_secret("HASS_TOKEN", canary_key(), unattended=True)
    a.inject("hass", ["HASS_TOKEN"])
    a.inject("other", ["HASS_TOKEN"])
    assert {x["holder"] for x in a.leases()} == {"pkg:hass", "pkg:other"}
    assert a.end_package("hass") == 1
    assert {x["holder"] for x in a.leases()} == {"pkg:other"}


@pytest.mark.parametrize("vault_open", [True, False])
@pytest.mark.parametrize("holder", ["toolsmith", "steward", "pkg:hass", "concierge"])
def test_package_env_purpose_is_never_lent_directly(state, vault_open, holder):
    a, *_ = make(state)
    a.put_secret("TICKED_KEY", canary_key(), unattended=True)
    if not vault_open:
        a.lock()
    with pytest.raises(LeaseRefused):
        a.lend("TICKED_KEY", holder, "package_env")
    assert a.leases() == []


def test_pkg_holders_cannot_borrow_another_purpose(state):
    a, *_ = make(state)
    a.put_secret("SOME_KEY", canary_key())
    with pytest.raises(LeaseRefused):
        a.lend("SOME_KEY", "pkg:hass", "cloud_call")


def test_locked_lend_is_refused_for_every_other_purpose(state):
    a, *_ = make(state)
    a.put_secret("TICKED_KEY", canary_key(), unattended=True)
    a.lock()
    with pytest.raises(Locked):
        a.lend("TICKED_KEY", "toolsmith", "cloud_call")


def test_module_seams_for_steward(state, monkeypatch):
    """L1's ``seams.py`` looks up ``zone1.archivist.lease_for_start``, ``end_lease`` and ``verify_pin``."""
    a = archivist_mod.for_state(state, watch=False)
    try:
        a.vault.machine = FakeMachine()                       # never real DPAPI in tests
        a.setup(PIN)
        key = canary_key()
        a.put_secret("HASS_TOKEN", key, unattended=True)
        a.put_secret("OTHER_TOKEN", canary_key())
        assert archivist_mod.lease_for_start("hass", ["HASS_TOKEN", "OTHER_TOKEN"]) == {"HASS_TOKEN": key}
        assert archivist_mod.end_lease("hass") == 1
        assert archivist_mod.end_lease("hass") == 0
        assert archivist_mod.verify_pin(PIN) is True
        assert archivist_mod.verify_pin("Wrong#pin99") is False
    finally:
        a.lock()
        archivist_mod._BY_STATE.pop(str(state.resolve()), None)


# -- D27: every write to a key drops its old ticked copy (re-check item 1) ------------------------------------

def _locked_inject(a, name):
    a.lock()
    try:
        return a.inject("pp", [name])
    finally:
        a.unlock("pin", PIN)


def test_card_backed_resave_drops_the_old_ticked_copy(state):
    a, *_ = make(state)
    a.put_secret("STRIPE", "old-" + canary_key(), unattended=True)
    a.put_secret("STRIPE", "new-" + canary_key(), card_backed=True)
    assert a.vault.meta("STRIPE")["card_backed"] is True and not a.vault.meta("STRIPE").get("unattended")
    assert _locked_inject(a, "STRIPE") == {}


def test_plain_resave_is_not_ticked_and_drops_the_old_copy(state):
    a, *_ = make(state)
    a.put_secret("HASS_TOKEN", "old-" + canary_key(), unattended=True)
    a.put_secret("HASS_TOKEN", "new-" + canary_key())
    assert _locked_inject(a, "HASS_TOKEN") == {}


def test_ticked_resave_wraps_the_new_value(state):
    a, *_ = make(state)
    a.put_secret("HASS_TOKEN", "old-" + canary_key(), unattended=True)
    new = "new-" + canary_key()
    a.put_secret("HASS_TOKEN", new, unattended=True)
    assert _locked_inject(a, "HASS_TOKEN") == {"HASS_TOKEN": new}


def test_inbox_replace_drops_the_old_ticked_copy(state):
    a, *_ = make(state)
    a.put_secret("K_KEY", "v1-" + canary_key(), unattended=True)
    a.lock()
    a.put_secret("K_KEY", "v2-" + canary_key())                # sealed into the inbox
    a.unlock("pin", PIN)
    assert a.file_replacing("K_KEY", PIN) is True
    assert _locked_inject(a, "K_KEY") == {}


def test_put_with_an_unattended_extra_cannot_tick_by_itself(state):
    a, *_ = make(state)
    a.put_secret("HASS_TOKEN", canary_key(), unattended=True)
    a.vault.put("HASS_TOKEN", "x-" + canary_key(), extra={"unattended": True})
    assert not a.vault.meta("HASS_TOKEN").get("unattended")
    assert _locked_inject(a, "HASS_TOKEN") == {}


def test_untick_and_delete_drop_the_copy(state):
    a, *_ = make(state)
    a.put_secret("A_KEY", canary_key(), unattended=True)
    a.put_secret("B_KEY", canary_key(), unattended=True)
    a.vault.set_unattended("A_KEY", False)
    a.lock()
    a.delete_secret("B_KEY")                                   # works while locked
    assert a.inject("pp", ["A_KEY", "B_KEY"]) == {}


def test_resave_then_restart_never_brings_the_old_value_back(state):
    from puffpai_core.bus import Bus
    from puffpai_core.zone1.archivist import Archivist
    a, *_ = make(state)
    machine = a.vault.machine
    a.put_secret("STRIPE", "old-" + canary_key(), unattended=True)
    a.put_secret("STRIPE", "new-" + canary_key(), card_backed=True)
    a.lock()
    b = Archivist(state, bus=Bus(), machine=machine)            # a restart: fresh Core, locked
    assert b.inject("pp", ["STRIPE"]) == {}
    assert sorted(p.name for p in b.vault.store.folder.glob("u*")) == []   # gone from disk, not just hidden


def test_a_wrap_that_cannot_be_removed_stops_the_save(state, monkeypatch):
    from puffpai_core.zone1.vault import Refused
    a, *_ = make(state)
    old = "old-" + canary_key()
    a.put_secret("HASS_TOKEN", old, unattended=True)
    real = a.vault.store.delete
    monkeypatch.setattr(a.vault.store, "delete",
                        lambda id, v: {"error": "conflict"} if id.startswith("u") else real(id, v))
    with pytest.raises(Refused):
        a.put_secret("HASS_TOKEN", "new-" + canary_key())
    assert a.vault.get("HASS_TOKEN") == old.encode()            # nothing was written
