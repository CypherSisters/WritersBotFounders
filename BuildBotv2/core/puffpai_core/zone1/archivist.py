# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/toolsmith_cloud.py (adapted: _Lease :238,
# take_lease :438, drop_lease :456, _on_vault_locked :309; the lease now lives in Archivist inside the Core,
# for any holder the Core names, and the borrower gets a handle, never the bytes)
"""Archivist: the only key holder (D33, D41, X21, X22).

Interface (Core-internal; agents and packages reach it only through Core code):

- ``Archivist(state, bus=None, clock=time.monotonic, watch=False)``; ``for_state(state, bus)`` gives the one
  Archivist per bot folder.
- ``lend(key_name, borrower, purpose, *, hosts=(), job=None, ttl_s=LEASE_MAX_S) -> handle``. A lease is one key,
  one holder, one purpose, its hosts and an end time. It ends at the first of: a hand lock, ``end_job(job)``,
  ``end_holder(borrower)``, ``end_package(package_id)`` (the package stopped), a change to that key, safe mode
  (lane I19a), or 3 hours. The idle lock does not end it. ``ttl_s`` must be a finite number of seconds in (0, 3 h]. The key is decrypted
  only while a lease is live, into a ``bytearray`` zeroed when it ends (best effort). The vault must be open;
  the purpose ``package_env`` is refused here (only ``inject`` uses it).
- ``use(handle, *, borrower, purpose, host=None) -> bytes``: the Core-side send path only (a cloud call, a device
  action, a package start). Holder, purpose, host and end time are checked on every use.
- ``verify_pin(pin) -> bool``: for floor-level Yes cards (D29). Throttled with the PIN.
- ``hold(value, label, source_ref=None, expire_days=None, tier=None) -> ref``: works while locked (inbox).
- ``expire(ref)``; ``resolve(ref) -> str`` (Core-side, vault open, for the gate's local-runtime path).
- ``inbox.seal(name, value)``: a secret typed before its home is ready (D36(a)); filed at setup or unlock.
  Filing never replaces a key that is already saved: that box waits (``inbox_conflicts``) until the owner says
  Yes with her PIN (``file_replacing(name, pin)``, floor-level, D29).
- ``put_secret(name, value, ...)``, ``delete_secret(name)``.
- ``inject(package_id, key_names) -> env``: Steward's package start. Ticked keys only, always (D27, D41), vault
  open or locked. Its lease ends when the package stops: Steward calls ``end_package(package_id)`` (module
  seam ``end_lease``). HONEST: bytes already in a running process's environment can't be pulled back; ending
  the lease stops new uses, the process keeps its copy until it stops.
- Anchors (D54, Core only): ``anchor_put(name, value: dict) -> bool`` and ``anchor_get(name) -> dict | None``,
  small facts sealed with the vault key (the Bridge's policy version and its ledger ``n`` and ``head``), so a
  same-user program that rolls back PuffPAI's own files is caught at the next unlock. Vault open only:
  ``anchor_put`` while locked keeps nothing (never the inbox, which anyone with its public key can write)
  and returns False; ``anchor_get`` while locked raises ``Locked`` (never "no anchor"). No route, package
  token or Toolsmith token reaches them.
- ``on_unlock(callback) -> remove``: ``callback(archivist)`` after every real unlock (and after a safe-mode
  repair), after the inbox is filed; never on a failed unlock or in safe mode. A callback that crashes is
  logged and audited by name; the unlock still stands, and the caller (the Bridge) decides what a failed
  check means.
- Module seams for lane L1 (``seams.py``): ``verify_pin(pin)``, ``lease_for_start(package_id, key_names)``,
  ``end_lease(package_id)``, all on the Archivist of ``paths.state_dir()``.

Nothing here ever returns key bytes to a package token or over HTTP; the vault
routes call only status, setup, unlock, lock, the ways in and write-only saves.

Honest limit (Windows, same user): during a lease, and while the vault is open,
key bytes sit in this process's memory; a program running as the owner's
Windows user can read them there (09 section 9). This is a rule about where the
Core puts keys, not a wall around Core memory.
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import crypto
from .inbox import Inbox
from .vault import ItemTampered, Locked, NotFound, Refused, Vault, VaultError, WrongSecret, check_name

logger = logging.getLogger(__name__)

LEASE_MAX_S = 3 * 60 * 60
HELD_PREFIX = "held:"
PACKAGE_ENV = "package_env"             # the one purpose only inject() may use (Steward's package start)
PACKAGE_HOLDER = "pkg:"
ANCHOR_PREFIX = "__anchor:"            # reserved: hidden from items(), unnameable over HTTP or from the inbox
ANCHOR_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
ANCHOR_MAX_BYTES = 16 * 1024


class LeaseRefused(VaultError):
    """That key lease can't be used here."""
    status, code = 403, "lease_refused"


class LeaseEnded(VaultError):
    """That key lease has ended. Unlock your vault to go on."""
    status, code = 423, "lease_ended"


class LeaseCapped(LeaseEnded):
    """That key lease reached its 3 hour limit (X22, D41). Unlock your vault to go on."""
    code = "lease_capped"


@dataclass
class _Lease:
    handle: str
    key_name: str
    borrower: str
    purpose: str
    hosts: tuple[str, ...]
    job: str | None
    started: float
    ends_at: float
    buf: bytearray = field(repr=False)

    def __repr__(self) -> str:                  # never prints the key
        return f"<lease {self.key_name} for {self.borrower}>"

    __str__ = __repr__

    def zero(self) -> None:
        crypto.zero(self.buf)
        self.buf = bytearray()


class Archivist:
    def __init__(self, state: Path | str, *, bus: Any = None, clock: Callable[[], float] = time.monotonic,
                 watch: bool = False, store: Any = None, machine: Any = None) -> None:
        self.state = Path(state)
        self.bus = bus
        self.clock = clock
        self.vault = Vault(self.state, bus=bus, store=store, clock=clock, watch=watch, machine=machine)
        self.inbox = Inbox(self.vault)
        self._leases: dict[str, _Lease] = {}
        self._llock = threading.Lock()
        self.inbox_conflicts: list[str] = []     # names whose sealed box would replace a saved key (waits for a Yes)
        self._unlock_hooks: list[Callable[["Archivist"], None]] = []
        self.vault.add_listener(self._on_vault)

    # -- vault life ------------------------------------------------------------------
    def setup(self, pin: str) -> str:
        """Make the vault; returns the recovery code once. Files anything sealed before setup."""
        code = self.vault.setup(pin)
        return code

    def unlock(self, method: str, secret: str, **kw: Any) -> str:
        return self.vault.unlock(method, secret, **kw)

    def lock(self, reason: str = "owner") -> None:
        self.vault.lock(reason)
        if reason in ("owner", "safe_mode"):         # a hand lock or safe mode ends every lease, even if locked
            self.end_all("lock")

    def _on_vault(self, event: str, reason: str) -> None:
        if event == "locked" and reason in ("owner", "tamper", "provider_change", "safe_mode"):
            self.end_all("lock")
        if event == "opened":
            self._file_inbox()
            for fn in list(self._unlock_hooks):
                try:
                    fn(self)
                except Exception:                    # the unlock stands; the hook's owner decides what it means
                    logger.exception("an unlock check failed")
                    self.vault.audit("unlock_hook", "failed", hook=getattr(fn, "__qualname__", "hook")[:80])

    def on_unlock(self, callback: Callable[["Archivist"], None]) -> Callable[[], None]:
        """Core only (D54): ``callback(archivist)`` after every real unlock. Returns a function that removes it."""
        if not callable(callback):
            raise TypeError("on_unlock needs a callable")
        self._unlock_hooks.append(callback)

        def remove() -> None:
            if callback in self._unlock_hooks:
                self._unlock_hooks.remove(callback)
        return remove

    # -- anchors (D54, Core only) --------------------------------------------------------------
    @staticmethod
    def _anchor_name(name: str) -> str:
        if not isinstance(name, str) or not ANCHOR_NAME.fullmatch(name):
            raise ValueError("An anchor name is lower case letters, digits, '.', '_' or '-', up to 64.")
        return ANCHOR_PREFIX + name

    def anchor_put(self, name: str, value: dict) -> bool:
        """Seal *value* under *name* with the vault key. False while locked (nothing is kept anywhere)."""
        item = self._anchor_name(name)
        if not isinstance(value, dict):
            raise ValueError("An anchor is a JSON object.")
        body = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(body) > ANCHOR_MAX_BYTES:
            raise ValueError("An anchor is at most 16 KB.")
        if not self.vault.is_open():
            return False
        try:
            self.vault.put(item, body, kind="anchor", label="core.anchor", tier="P0", reserved=True)
        except Locked:
            return False
        self.vault.audit("anchor_put", name=name)
        return True

    def anchor_get(self, name: str) -> dict | None:
        """The anchor under *name*, or None when none was ever put. Raises ``Locked`` while locked (never None
        then) and ``ItemTampered`` when its blob was changed outside PuffPAI: treat both as "can't confirm"."""
        item = self._anchor_name(name)
        try:
            raw = self.vault.get(item)
        except NotFound:
            return None
        meta = self.vault.meta(item) or {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ItemTampered("That anchor was changed outside PuffPAI.") from None
        if meta.get("kind") != "anchor" or not isinstance(value, dict):
            raise ItemTampered("That anchor was changed outside PuffPAI.")
        return value

    def _file_inbox(self, replace: frozenset[str] = frozenset()) -> dict:
        """File every sealed box. A box never replaces something already saved (anyone who can read the inbox's
        public key can seal a box, 06 Z1V 6): a key of the same name waits for a floor-level Yes (``replace``
        holds the names she said Yes to); a held value under a ref already in use is set aside."""
        counts = {"secrets": 0, "held": 0, "waiting": 0, "set_aside": 0}
        try:
            self.inbox.ensure_key()
        except VaultError:
            return counts
        waiting: list[str] = []
        for item in self.inbox.drain():
            try:
                if item["type"] == "secret":
                    name = item["name"]
                    if not isinstance(name, str) or not name or name.startswith("__"):
                        raise ValueError("bad name")
                    if self.vault.meta(name) is not None and name not in replace:
                        if name not in waiting:
                            waiting.append(name)
                        counts["waiting"] += 1
                        self.inbox.keep(item)        # the box stays sealed in the inbox
                        continue
                    self.vault.put(name, bytes.fromhex(item["value"]), kind="secret",
                                   label=item.get("label", "secret.api_key"), tier=item.get("tier", "P4"))
                    self._end_key(name, "key")
                    counts["secrets"] += 1
                else:
                    if self.vault.meta(HELD_PREFIX + item["ref"]) is not None:
                        raise ValueError("ref in use")
                    self._put_held(item["ref"], item["value"], item["label"], item["tier"],
                                   item.get("expire_days"), path="inbox")
                    counts["held"] += 1
            except VaultError:
                continue
            except (KeyError, TypeError, ValueError):
                self.inbox.set_aside_item(item)
                counts["set_aside"] += 1
                continue
            Inbox.done(item)
        self.inbox_conflicts = waiting
        if any(counts.values()):
            self.vault.audit("inbox_filed", **counts)
        return counts

    def file_replacing(self, name: str, pin: str) -> bool:
        """Her floor-level Yes (D29): file the waiting box for *name*, replacing the saved key. Needs the PIN."""
        check_name(name)
        if not self.vault.is_open():
            raise Locked()
        if not self.verify_pin(pin):
            raise WrongSecret()
        before = self.vault.meta(name)
        self._file_inbox(replace=frozenset({name}))
        after = self.vault.meta(name)
        return before is not None and after is not None and after.get("v") != before.get("v")

    def verify_pin(self, pin: str) -> bool:
        return self.vault.verify_pin(pin)

    def is_open(self) -> bool:
        return self.vault.is_open()

    # -- secrets ----------------------------------------------------------------------------
    def put_secret(self, name: str, value: str, *, label: str = "secret.api_key", unattended: bool = False,
                   card_backed: bool = False) -> dict:
        """Save a key. Vault open: saved now. Locked or not set up: sealed into the inbox (D36(a)).

        Saving again replaces the value and drops the old ticked copy (``vault.put``); it is ticked again only
        when this save says ``unattended=True``, and never for a card-backed key (D27)."""
        check_name(name)                                     # never a Core-internal (__) name
        if unattended and card_backed:
            raise Refused("Keys linked to a payment card never work before your PIN.")
        if not self.vault.is_open():
            if unattended:
                raise Locked("Unlock your vault to let this key work after a restart.")
            kept = self.inbox.seal(name, value, label=label)["kept"]
            self._publish_stored(None, label, "inbox")
            self._end_key(name, "key")
            return {"saved": False, "kept": kept}
        ref = self.vault.put(name, value, kind="secret", label=label, tier="P4",
                             extra={"card_backed": bool(card_backed)})
        self._end_key(name, "key")
        if unattended:
            self.vault.set_unattended(name, True)
        self._publish_stored(ref, label, "direct")
        return {"saved": True, "kept": "vault"}

    def delete_secret(self, name: str) -> bool:
        check_name(name)
        self._end_key(name, "key")
        return self.vault.delete(name)

    def _publish_stored(self, ref: str | None, label: str, path: str, tier: str = "P4") -> None:
        self.vault.publish("vault.stored", {"ref": ref or ("i" + secrets.token_hex(8)), "label": label,
                                            "tier": tier, "path": path})

    # -- leases (D41, X22) --------------------------------------------------------------------
    @staticmethod
    def _ttl(ttl_s: Any) -> float:
        """A finite number of seconds in (0, 3 h]; anything else (NaN, infinity, a string, too long) is refused."""
        if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)):
            raise LeaseRefused("A lease lasts a number of seconds.")
        ttl = float(ttl_s)
        if not math.isfinite(ttl) or not 0 < ttl <= LEASE_MAX_S:
            raise LeaseRefused("A lease lasts more than 0 seconds and at most 3 hours.")
        return ttl

    def lend(self, key_name: str, borrower: str, purpose: str, *, hosts: tuple[str, ...] | list[str] = (),
             job: str | None = None, ttl_s: float = LEASE_MAX_S) -> str:
        """A lease for a Core sender (a cloud call, a device action). The vault must be open. Package starts go
        through ``inject`` only: the purpose ``package_env`` and ``pkg:`` holders are refused here, so no
        other holder can reach the ticked tier while the vault is locked (D27, D41)."""
        if purpose == PACKAGE_ENV or (isinstance(borrower, str) and borrower.startswith(PACKAGE_HOLDER)):
            raise LeaseRefused("Package keys are lent only by Steward at a package start.")
        if not self.vault.is_open():
            raise Locked("Unlock your vault to lend this key.")
        return self._lend(key_name, borrower, purpose, hosts=hosts, job=job, ttl_s=ttl_s, ticked_only=False)

    def _lend(self, key_name: str, borrower: str, purpose: str, *, hosts: tuple[str, ...] | list[str] = (),
              job: str | None = None, ttl_s: float = LEASE_MAX_S, ticked_only: bool) -> str:
        if not isinstance(borrower, str) or not borrower or not isinstance(purpose, str) or not purpose:
            raise LeaseRefused("A lease needs a holder and a purpose.")
        if not isinstance(key_name, str) or not key_name or key_name.startswith("__") \
                or key_name.startswith(HELD_PREFIX):
            raise LeaseRefused("That name is kept for PuffPAI itself; it is never lent.")
        ttl = self._ttl(ttl_s)
        if ticked_only:
            value = self._ticked(key_name)
        else:
            value = self.vault.get(key_name)
        now = self.clock()
        lease = _Lease(handle="lease-" + secrets.token_hex(12), key_name=key_name, borrower=borrower,
                       purpose=purpose, hosts=tuple(str(h).lower() for h in hosts), job=job, started=now,
                       ends_at=now + ttl, buf=bytearray(value))
        del value
        with self._llock:
            self._leases[lease.handle] = lease
        self.vault.audit("lease_taken", name=key_name, holder=borrower, purpose=purpose)
        return lease.handle

    def use(self, handle: str, *, borrower: str, purpose: str, host: str | None = None,
            sink: str = "lease") -> bytes:
        """The key bytes, for the Core's own sender at the moment of use. Never hand these to a borrower."""
        with self._llock:
            lease = self._leases.get(handle)
            if lease is None:
                raise LeaseEnded()
            if self.clock() >= lease.ends_at:
                self._end(lease, "cap")
                raise LeaseCapped("That key lease reached its 3 hour limit. Unlock your vault to go on.")
            if lease.borrower != borrower or lease.purpose != purpose:
                raise LeaseRefused("That key lease belongs to someone else.")
            if lease.hosts and (host or "").lower() not in lease.hosts:
                raise LeaseRefused("That key lease doesn't cover this host.")
            value = bytes(lease.buf)
            name = lease.key_name
        self.vault.publish("vault.accessed", {"ref": "lease-" + crypto.sha256(name.encode())[:16], "sink": sink,
                                              "holder": borrower})
        return value

    def live(self, handle: str) -> bool:
        with self._llock:
            lease = self._leases.get(handle)
            if lease is None:
                return False
            if self.clock() >= lease.ends_at:
                self._end(lease, "cap")
                return False
            return True

    def leases(self) -> list[dict]:
        """What is lent, never the bytes."""
        now = self.clock()
        with self._llock:
            for lease in [x for x in self._leases.values() if now >= x.ends_at]:
                self._end(lease, "cap")
            return [{"handle": x.handle, "key": x.key_name, "holder": x.borrower, "purpose": x.purpose,
                     "ends_in_s": int(x.ends_at - now)} for x in self._leases.values()]

    def _end(self, lease: _Lease, why: str) -> None:            # caller holds _llock
        self._leases.pop(lease.handle, None)
        lease.zero()
        self.vault.audit("lease_ended", name=lease.key_name, holder=lease.borrower, why=why)

    def end(self, handle: str, why: str = "done") -> None:
        with self._llock:
            lease = self._leases.get(handle)
            if lease:
                self._end(lease, why)

    def end_job(self, job: str) -> int:
        with self._llock:
            hit = [x for x in self._leases.values() if x.job == job]
            for x in hit:
                self._end(x, "job")
        return len(hit)

    def end_holder(self, borrower: str) -> int:
        """End every lease *borrower* holds. Returns how many ended."""
        with self._llock:
            hit = [x for x in self._leases.values() if x.borrower == borrower]
            for x in hit:
                self._end(x, "stopped")
        return len(hit)

    def end_package(self, package_id: str) -> int:
        """The package stopped (D41): Steward MUST call this on every stop (module seam ``end_lease``).
        HONEST: bytes already in that process's environment can't be pulled back; they go when it stops."""
        return self.end_holder(PACKAGE_HOLDER + str(package_id))

    def _end_key(self, name: str, why: str) -> None:
        with self._llock:
            for x in [x for x in self._leases.values() if x.key_name == name]:
                self._end(x, why)

    def end_all(self, why: str) -> None:
        with self._llock:
            for x in list(self._leases.values()):
                self._end(x, why)

    def _ticked(self, name: str) -> bytes:
        """A key the owner ticked to work while it runs (D27, D41). Vault open: the vault's own copy, if its
        tick is on. Locked: the ticked tier. Never an unticked key."""
        if self.vault.is_open():
            meta = self.vault.meta(name)
            if meta is None:
                raise NotFound()
            if not meta.get("unattended"):
                raise LeaseRefused("That key isn't ticked to work for a running app.")
            return self.vault.get(name)
        return self.vault.get_unattended(name)

    def inject(self, package_id: str, key_names: list[str]) -> dict[str, str]:
        """Steward's package start: the approved ``needs.keys``, ticked keys only, always (D27, D41).

        Each key gets a lease held by ``pkg:<package_id>``; ``end_package(package_id)`` ends them when the
        package stops. HONEST: once these bytes are in the started process's environment they can't be pulled
        back; ending the lease stops new uses, and the process keeps its copy until it stops."""
        if not isinstance(package_id, str) or not package_id:
            raise LeaseRefused("A package start needs the package id.")
        holder = PACKAGE_HOLDER + package_id
        env = {}
        for name in key_names:
            try:
                handle = self._lend(name, holder, PACKAGE_ENV, ticked_only=True)
            except VaultError:
                continue                             # not ticked, not found, locked tier missing: no key
            env[name] = self.use(handle, borrower=holder, purpose=PACKAGE_ENV, sink="package_env").decode("utf-8")
        return env

    # -- held values (Z1C F7, ZONES_TOGETHER "Vault calls") ----------------------------------------
    def hold(self, value: str, label: str, source_ref: str | None = None, expire_days: int | None = None,
             tier: str | None = None) -> str:
        """Hold one private value. Returns its ref (``{{vault:<ref>}}``). Works while locked."""
        from .labels import tier_of
        tier = tier or tier_of(label)
        if self.vault.is_open():
            existing = self._find_same(value, label)
            if existing:
                return existing
            ref = "h" + secrets.token_hex(10)
            self._put_held(ref, value, label, tier, expire_days, path="held")
            return ref
        ref = "h" + secrets.token_hex(10)
        self.inbox.seal_held(ref, value, label=label, tier=tier, source_ref=source_ref, expire_days=expire_days)
        self.vault.publish("vault.stored", {"ref": ref, "label": label, "tier": tier, "path": "inbox"})
        return ref

    def _same_key(self, value: str, label: str) -> str:
        return crypto.mac(self.vault._index_key, b"pz1/same", f"{label}\x00{value}".encode("utf-8"))[:24]

    def _find_same(self, value: str, label: str) -> str | None:
        key = self._same_key(value, label)
        for m in self.vault._root["items"].values():
            if m.get("same") == key and not self.vault._tombstoned(m["name"]):
                return m.get("ref")
        return None

    def _put_held(self, ref: str, value: str, label: str, tier: str, expire_days: int | None, *, path: str) -> None:
        extra = {"same": self._same_key(value, label)}
        if expire_days:
            extra["expire_at"] = int(time.time() + int(expire_days) * 86400)
        name = HELD_PREFIX + ref
        bid = self.vault._blob_id(name)
        with self.vault._lock:
            self.vault._require_open()
            record = {"name": name, "kind": "held", "label": label, "tier": tier, "ref": ref,
                      "value": value.encode("utf-8").hex(), **extra}
            self.vault._put_blob(bid, record, {"name": name, "kind": "held", "label": label, "tier": tier,
                                               "ref": ref, **extra})
        self.vault.audit("held", label=label)
        self.vault.publish("vault.stored", {"ref": ref, "label": label, "tier": tier, "path": path})

    def resolve(self, ref: str) -> str:
        """A held value, Core-side, vault open (the gate's local-runtime path, D25). Never for secrets."""
        value = self.vault.get(HELD_PREFIX + ref)
        return value.decode("utf-8")

    def expire(self, ref: str, why: str = "retention") -> bool:
        name = HELD_PREFIX + ref
        label = "unchecked.held"
        try:
            meta = self.vault.meta(name) if self.vault.is_open() else None
            label = (meta or {}).get("label", label)
        except VaultError:
            pass
        gone = self.vault.delete(name)
        if gone:
            self.vault.publish("vault.expired", {"ref": ref, "label": label, "why": why})
        return gone


_BY_STATE: dict[str, Archivist] = {}
_BY_LOCK = threading.Lock()


def for_state(state: Path | str, bus: Any = None, *, watch: bool = True) -> Archivist:
    """The one Archivist for a bot folder (the Core API, Steward and the gate share it)."""
    key = str(Path(state).resolve())
    with _BY_LOCK:
        a = _BY_STATE.get(key)
        if a is None:
            a = _BY_STATE[key] = Archivist(state, bus=bus, watch=watch)
            from . import gate
            gate.bind(a)
        elif bus is not None and a.bus is None:
            a.bus = a.vault.bus = bus
        return a


# -- module seams for lane L1 (``puffpai_core/seams.py`` looks these up by name; see seam/NOTE.md) -----------

def _here() -> Archivist:
    from ..paths import state_dir
    return for_state(state_dir())


def verify_pin(pin: str) -> bool:
    """Floor-level Yes cards (D29). Throttled with the PIN; never opens the vault."""
    return _here().verify_pin(pin) is True


def lease_for_start(package_id: str, key_names: list[str]) -> dict[str, str]:
    """Steward's package start: ticked keys only (D27, D41). Pair every call with ``end_lease`` on stop."""
    return _here().inject(package_id, list(key_names))


def end_lease(package_id: str) -> int:
    """Steward's package stop: ends the package's leases (D41). Returns how many ended."""
    return _here().end_package(package_id)
