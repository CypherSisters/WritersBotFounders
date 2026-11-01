"""D27: a ticked key deleted while locked never comes back, even when the machine key is down at that moment.

The bug: deleting while locked needed the machine key to find the ticked copy and
silently skipped it without one; the next unlock removed the item but not the
copy and cleared the tombstone, so the old value was injected before the PIN.
Now the copy is found through the keyring's index (no machine key, no vault key),
a tombstone is cleared only once the copy is confirmed gone, and the ticked tier
refuses any name with a live tombstone or no index row.
"""

import json

import pytest

from conftest import FakeMachine
from puffpai_core.bus import Bus
from puffpai_core.zone1 import machine_key
from puffpai_core.zone1.archivist import Archivist, LeaseRefused
from puffpai_core.zone1.vault import ItemTampered, Refused
from z1kit import PIN, FakeClock, make


class DownableMachine(FakeMachine):
    """The machine key, which a test can take away (MachineKeyUnavailable) and bring back."""

    def __init__(self):
        super().__init__()
        self.down = False

    def protect(self, data):
        if self.down:
            raise machine_key.MachineKeyUnavailable("down")
        return super().protect(data)

    def unprotect(self, data):
        if self.down:
            raise machine_key.MachineKeyUnavailable("down")
        return super().unprotect(data)


def _vault_with_ticked_k(state):
    machine = DownableMachine()
    a = Archivist(state, bus=Bus(), clock=FakeClock(), machine=machine)
    a.setup(PIN)
    a.put_secret("K", "v1", unattended=True)
    return a, machine


def test_locked_delete_with_machine_key_down(state):
    a, machine = _vault_with_ticked_k(state)
    a.lock()
    machine.down = True
    assert a.delete_secret("K") is True
    machine.down = False
    a.unlock("pin", PIN)
    a.lock()
    assert a.inject("p", ["K"]) == {}
    assert list(a.vault.store.folder.glob("u*")) == []           # the copy's file is gone, not only hidden


def test_locked_delete_with_machine_key_down_then_restart(state):
    a, machine = _vault_with_ticked_k(state)
    a.lock()
    machine.down = True
    a.delete_secret("K")
    machine.down = False
    b = Archivist(state, bus=Bus(), clock=FakeClock(), machine=machine)     # restart before the unlock
    assert b.inject("p", ["K"]) == {}
    b.unlock("pin", PIN)
    b.lock()
    c = Archivist(state, bus=Bus(), clock=FakeClock(), machine=machine)     # and after it
    assert c.inject("p", ["K"]) == {}


def test_tombstone_stays_until_the_copy_is_confirmed_gone(state, monkeypatch):
    a, machine = _vault_with_ticked_k(state)
    a.lock()
    store = a.vault.store
    real_delete, real_get = store.delete, store.get
    monkeypatch.setattr(store, "delete", lambda id, v: {"error": "unavailable"} if id.startswith("u")
                        else real_delete(id, v))
    a.delete_secret("K")
    a.unlock("pin", PIN)                                         # the copy can't be removed this time
    assert json.loads((state / "vault" / "tombstones.json").read_text()) != []
    a.lock()
    assert a.inject("p", ["K"]) == {}                            # the live tombstone still refuses it
    monkeypatch.setattr(store, "delete", real_delete)
    monkeypatch.setattr(store, "get", real_get)
    a.unlock("pin", PIN)                                         # retried, confirmed gone, then cleared
    assert json.loads((state / "vault" / "tombstones.json").read_text()) == []
    a.lock()
    assert a.inject("p", ["K"]) == {}


def test_no_index_row_means_no_ticked_key(state):
    a, machine = _vault_with_ticked_k(state)
    a.lock()
    path = state / "vault" / "keyring.json"
    ring = json.loads(path.read_text(encoding="utf-8"))
    ring["sdk"]["u_index"] = {}
    path.write_text(json.dumps(ring), encoding="utf-8")
    assert a.inject("p", ["K"]) == {}


def test_an_index_row_pointing_elsewhere_is_tampered(state):
    a, machine = _vault_with_ticked_k(state)
    a.lock()
    path = state / "vault" / "keyring.json"
    ring = json.loads(path.read_text(encoding="utf-8"))
    ring["sdk"]["u_index"] = {k: "u" + "0" * 40 for k in ring["sdk"]["u_index"]}
    path.write_text(json.dumps(ring), encoding="utf-8")
    with pytest.raises(ItemTampered):
        a.vault.get_unattended("K")


def test_untick_removes_the_copy_and_the_row(state):
    a, machine = _vault_with_ticked_k(state)
    a.vault.set_unattended("K", False)
    ring = json.loads((state / "vault" / "keyring.json").read_text(encoding="utf-8"))
    assert ring["sdk"]["u_index"] == {}
    a.lock()
    assert a.inject("p", ["K"]) == {}


# -- Core-internal names, at every Python writer (re-check item 2) ------------------------------------------

@pytest.mark.parametrize("name", ["__anchor:bridge", "__inbox.private", "__anything", "held:h0123"])
def test_core_internal_names_refused_by_every_writer(state, name):
    a, *_ = make(state)
    with pytest.raises(Refused):
        a.put_secret(name, "x-value-123")
    with pytest.raises(Refused):
        a.vault.put(name, b"x")
    with pytest.raises(Refused):
        a.delete_secret(name)
    with pytest.raises(Refused):
        a.vault.set_unattended(name, True)
    with pytest.raises(Refused):
        a.file_replacing(name, PIN)
    with pytest.raises(LeaseRefused):
        a.lend(name, "toolsmith", "cloud_call")
    assert a.inject("p", [name]) == {}


def test_anchors_are_still_written_by_the_core(state):
    a, *_ = make(state)
    assert a.anchor_put("bridge", {"n": 1}) is True and a.anchor_get("bridge") == {"n": 1}
    a.inbox.ensure_key()                                         # the inbox's own reserved items still work


def test_a_wrong_type_anchor_read_is_tampered(state):
    from puffpai_core.zone1.archivist import ANCHOR_PREFIX
    a, *_ = make(state)
    a.vault.put(ANCHOR_PREFIX + "bridge", b"\xff\xfe not json", kind="anchor", reserved=True)
    with pytest.raises(ItemTampered):
        a.anchor_get("bridge")
    a.vault.put(ANCHOR_PREFIX + "bridge", b"[1, 2]", kind="anchor", reserved=True)
    with pytest.raises(ItemTampered):
        a.anchor_get("bridge")
    a.vault.put(ANCHOR_PREFIX + "bridge", b'{"n": 1}', kind="secret", reserved=True)
    with pytest.raises(ItemTampered):
        a.anchor_get("bridge")
