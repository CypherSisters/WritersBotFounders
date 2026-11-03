# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/vault.py (adapted: create_with_recovery :1148,
# unlock :1203, check_pin :1239, redeem_recovery_code :1319, _rekey :1975, _check_throttle :1865,
# _note_failure :1895, enroll_service_wrap :1753, read_service_secret_unattended :1834; Fernet + PBKDF2 became
# AES-256-GCM + Argon2id; records are now bound to their names and stored one item per blob)
"""The vault: the Core key scheme over one ``zone1.vault-store`` (Z1V sections 3 to 5, 8).

What lives where:
- ``<state>/vault/keyring.json``: the ways in (one wrap of the vault key per way)
  and the unattended-tier wraps. Kept by the Core, never given to a store.
- ``<state>/vault/epoch``: the highest root epoch seen (rollback check, F12).
- ``<state>/vault/tombstones.json``: names deleted while locked (off is one step).
- the store (default ``<state>/vault/store``): sealed blobs only. Blob ids are
  ``HMAC(index_key, name)``, so the store never learns item names.

Every item is its own blob, sealed with AAD that binds vault id, blob id and
version, and its plaintext names itself; a read checks both, so two swapped
blobs, or an old copy of one, read as tampered (the v1 fix for records sealed
with AAD ``None``, ``vault.py:1811``). A bad blob loses that one item only.
The root blob lists every item's id, version and SHA-256 plus an epoch; a store
that drops, adds or rolls back blobs is caught there.

Two ways in at least, one of them the recovery code (F10); the recovery code
can be rotated but never removed. F10 is checked on every keyring write and on
every read at unlock, and so is the keyring's MAC (keyed by the vault key): a
keyring edited on disk (a way in dropped, or anything changed that the MAC
covers: the ways, the inbox public key, the unattended wraps) opens only in
safe mode (``SafeMode``), where the one thing allowed is the repair (a new
recovery code with the PIN, or a recovery with the code); the repair rewrites
the keyring under a fresh MAC and drops unattended wraps that don't agree. Adding or
removing a way in needs the current PIN or the recovery code (D29). Throttle before any KDF work, in memory, at
most 5 minutes, PIN and recovery counted apart (F13). Idle lock always on, 5
minutes by default, at most 60 (F14); while a live build or an unfinished setup
exists it is the 60 (D153, ``Vault.hold``). Only a real vault use resets it; the
Core's own anchor seals do not.
Locked means refuse (``Locked``, 423),
never empty; a missing store means wait (``StoreMissing``, 503), never a new
empty vault (F8).

Every unlock, failure and change is logged by name, never value
(``<state>/logs/vault.log``, F16). ``vault.*`` events are published by the Core only.

Honest limits (Windows, same user): a program running as the owner's Windows
user can copy the sealed files and try PINs offline (slowed only by Argon2id),
can read the vault key from this process's memory while the vault is open, can
roll back the epoch file together with the store, and can delete the
tombstones file to bring back a key deleted while locked.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import crypto, machine_key
from .crypto import Tampered
from .pin_rules import generate_recovery_code, normalize_recovery_code, validate_vault_secret
from .store_local import MISSING, LocalStore

logger = logging.getLogger(__name__)

KEYRING_SCHEMA = "pz1-keyring/1"
ROOT_ID = "root"
RESERVED = "__"

IDLE_DEFAULT_S = 5 * 60
IDLE_MAX_S = 60 * 60
IDLE_MIN_S = 60

FREE_ATTEMPTS = 5
BACKOFF_BASE_S = 5.0
BACKOFF_MAX_S = 300.0

SOURCE = {"kind": "core", "id": "core:archivist"}
PIN_KINDS = ("pin", "passphrase")


# -- errors (each carries an HTTP status and plain words) -----------------------

class VaultError(Exception):
    status = 400
    code = "vault_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.__doc__ or self.code)
        self.message = message or (self.__doc__ or self.code)


class NotSetUp(VaultError):
    """Your vault isn't set up yet."""
    status, code = 409, "not_set_up"


class AlreadySetUp(VaultError):
    """Your vault is already set up."""
    status, code = 409, "already_set_up"


class Locked(VaultError):
    """Your vault is locked. Unlock it with your PIN first."""
    status, code = 423, "locked"


class StoreMissing(VaultError):
    """Your vault drive is not plugged in."""
    status, code = 503, "store_missing"


class WrongSecret(VaultError):
    """That didn't match."""
    status, code = 401, "wrong"


class Throttled(VaultError):
    """Too many tries. Wait a moment and try again."""
    status, code = 429, "throttled"

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Too many tries. Try again in {int(retry_after) + 1} seconds.")
        self.retry_after = retry_after


class WeakSecret(VaultError):
    status, code = 400, "weak"


class Refused(VaultError):
    status, code = 409, "refused"


class ItemTampered(VaultError):
    """This item was changed outside PuffPAI, so it was not used."""
    status, code = 409, "tampered"


class NotFound(VaultError):
    """Nothing is saved under that name."""
    status, code = 404, "not_found"


class SafeMode(VaultError):
    """Your vault's list of ways in was changed outside PuffPAI, so it is in safe mode. Your PIN or recovery code still opens it, only to make a new recovery code. Nothing else in it can be used until then."""
    status, code = 409, "safe_mode"


class RolledBack(VaultError):
    """Your vault went back in time. An older copy of it is in place."""
    status, code = 409, "rolled_back"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def check_name(name: str, *, reserved: bool = False) -> None:
    """A name a caller may write. ``__`` names (the inbox key, anchors, ...) are the Core's own: only Core code
    that passes ``reserved=True`` writes them. ``held:`` names come from Archivist's held path only."""
    if not isinstance(name, str) or not name:
        raise Refused("A key needs a name.")
    if (name.startswith(RESERVED) or name.startswith("held:")) and not reserved:
        raise Refused("That name is kept for PuffPAI itself.")


class Vault:
    """One vault. Archivist owns it; nothing else in the Core touches the DEK."""

    def __init__(self, state: Path | str, *, bus: Any = None, store: Any = None,
                 clock: Callable[[], float] = time.monotonic, watch: bool = False, machine: Any = None) -> None:
        self.state = Path(state)
        self.dir = self.state / "vault"
        self.keyring_path = self.dir / "keyring.json"
        self.epoch_path = self.dir / "epoch"
        self.tomb_path = self.dir / "tombstones.json"
        self.settings_path = self.dir / "settings.json"
        self.log_path = self.state / "logs" / "vault.log"
        self.bus = bus
        self.clock = clock
        self.store = store or LocalStore(self.dir / "store")
        self.machine = machine or machine_key.Dpapi()     # the unattended-tier wrap; tests pass a fake
        self._lock = threading.RLock()
        self._dek: bytearray | None = None
        self._index_key: bytes | None = None
        self._root: dict | None = None
        self._session: str | None = None
        self._last_touch = 0.0
        self._attempts: dict[str, tuple[int, float]] = {}
        self._listeners: list[Callable[[str, str], None]] = []
        self._watch = watch
        self._watch_stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self.warnings: list[str] = []
        self._vault_id_cache: str | None = None
        self._safe: str | None = None          # why the vault is open for repair only (F10 or the MAC failed)
        self._ring_ok = True
        self.hold: Callable[[], bool] | None = None   # D153: set at wiring. May run under self._lock: it must wait on no lock

    # -- small state ------------------------------------------------------------
    def is_set_up(self) -> bool:
        return self.keyring_path.is_file()

    def is_open(self) -> bool:
        """Open for use. In safe mode the vault key is in memory for repair only, and this is False."""
        with self._lock:
            if self._dek is None:
                return False
            if self._idle_expired():
                self._do_lock("idle")
                return False
            return self._safe is None

    @property
    def session(self) -> str | None:
        return self._session if self.is_open() else None

    def add_listener(self, fn: Callable[[str, str], None]) -> None:
        """``fn(event, reason)`` with event ``opened`` or ``locked``. Called inside the Core only."""
        self._listeners.append(fn)

    def _settings(self) -> dict:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def idle_s(self) -> float:
        """Always on, 1 to 60 minutes (F14). Anything else in the file (a string, NaN, infinity, a yes/no, out of
        bounds) means the 5 minute default, never off."""
        value = self._settings().get("idle_s", IDLE_DEFAULT_S)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return float(IDLE_DEFAULT_S)
        value = float(value)
        if not math.isfinite(value) or not IDLE_MIN_S <= value <= IDLE_MAX_S:
            return float(IDLE_DEFAULT_S)
        return value

    def set_idle_minutes(self, minutes: int) -> int:
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes):
            raise ValueError("The idle lock is a number of minutes.")
        seconds = min(max(int(minutes) * 60, IDLE_MIN_S), IDLE_MAX_S)
        data = self._settings()
        data["idle_s"] = seconds
        _atomic_write(self.settings_path, json.dumps(data).encode("utf-8"))
        return seconds // 60

    def _held(self) -> bool:
        """D153: does a live build or an unfinished setup exist? The Core's wiring answers; the vault knows neither.
        Anything but a plain True, or a check that fails, holds nothing: her setting applies."""
        fn = self.hold
        if fn is None:
            return False
        try:
            return fn() is True
        except Exception:  # noqa: BLE001 - can't tell: holds nothing
            logger.warning("the vault's hold check failed, so her idle setting applies")
            return False

    def _idle_limit(self) -> float:
        """The idle time in force now: 60 minutes while held, else her setting. Never held in safe mode. Her saved
        setting is not changed, and nothing here resets the timer or writes to the vault log (D68(e), D153)."""
        return float(IDLE_MAX_S) if self._safe is None and self._held() else self.idle_s

    def _idle_expired(self) -> bool:
        if self._dek is None:
            return False
        idle = self.clock() - self._last_touch
        return idle >= self.idle_s and idle >= self._idle_limit()     # the check is only asked once her setting is up

    def _touch(self) -> None:
        self._last_touch = self.clock()

    def idle_left_s(self) -> float | None:
        if not self.is_open():
            return None
        return max(0.0, self._idle_limit() - (self.clock() - self._last_touch))

    def watch_once(self) -> bool:
        """Lock if idle. True when it locked. Caretaker or the watcher thread calls it."""
        with self._lock:
            if self._idle_expired():
                self._do_lock("idle")
                return True
        return False

    def _start_watch(self) -> None:
        if not self._watch or (self._watcher and self._watcher.is_alive()):
            return
        self._watch_stop.clear()

        def run() -> None:
            while not self._watch_stop.wait(5.0):
                if self.watch_once() or self._dek is None:
                    return
        self._watcher = threading.Thread(target=run, name="vault-idle-lock", daemon=True)
        self._watcher.start()

    # -- audit and events ---------------------------------------------------------
    def audit(self, action: str, result: str = "ok", **names: Any) -> None:
        """One line per action, names and counts only, never a value (F16)."""
        line = {"at": _utc(), "action": action, "result": result, **names}
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
        except OSError:
            logger.warning("vault audit line not written: %s", action)

    def publish(self, type_: str, payload: dict) -> None:
        if self.bus is None:
            return
        from ..bus import Bus
        self.bus.publish(Bus.message(type_, payload, SOURCE))

    # -- keyring --------------------------------------------------------------------
    def _read_keyring(self) -> dict:
        try:
            ring = json.loads(self.keyring_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise NotSetUp() from None
        except (OSError, ValueError):
            raise ItemTampered("Your vault's list of ways in can't be read.") from None
        if not isinstance(ring, dict) or ring.get("schema") != KEYRING_SCHEMA \
                or not isinstance(ring.get("ways"), list) or not isinstance(ring.get("vault_id"), str):
            raise ItemTampered("Your vault's list of ways in can't be read.")
        for w in ring["ways"]:
            if not isinstance(w, dict) or not isinstance(w.get("id"), str) or not isinstance(w.get("kind"), str):
                raise ItemTampered("Your vault's list of ways in can't be read.")
        return ring

    @staticmethod
    def _f10_problem(ring: dict) -> str | None:
        """Why *ring* breaks F10 (two ways in at least, one the recovery code, one a PIN or passphrase), or None."""
        kinds = [w["kind"] for w in ring["ways"]]
        if "recovery" not in kinds:
            return "no_recovery"
        if not any(k in PIN_KINDS for k in kinds):
            return "no_pin"
        if len(kinds) < 2:
            return "too_few"
        return None

    def _ring_mac(self, ring: dict, dek: bytes | bytearray) -> str:
        body = json.dumps({"vault_id": ring["vault_id"], "ways": ring["ways"], "inbox": ring.get("inbox"),
                           "sdk": ring.get("sdk")}, sort_keys=True).encode("utf-8")
        return crypto.mac(dek, b"pz1/keyring", body)

    def _write_keyring(self, ring: dict, dek: bytes | bytearray) -> None:
        kinds = [w["kind"] for w in ring["ways"]]
        if len(ring["ways"]) < 2 or "recovery" not in kinds:     # F10, checked on every write
            raise Refused("Your vault needs at least two ways in, and one is the recovery code.")
        ring["mac"] = self._ring_mac(ring, dek)
        _atomic_write(self.keyring_path, json.dumps(ring, indent=1, sort_keys=True).encode("utf-8"))

    @property
    def vault_id(self) -> str:
        return self._read_keyring()["vault_id"]

    # -- throttle (F13) ---------------------------------------------------------------
    def _check_throttle(self, kind: str) -> None:
        failures, until = self._attempts.get(kind, (0, 0.0))
        left = until - self.clock()
        if left > 0:
            raise Throttled(left)

    def _note_failure(self, kind: str) -> tuple[int, float]:
        failures, _ = self._attempts.get(kind, (0, 0.0))
        failures += 1
        delay = 0.0
        if failures > FREE_ATTEMPTS:
            delay = min(BACKOFF_BASE_S * (2 ** (failures - FREE_ATTEMPTS - 1)), BACKOFF_MAX_S)
        self._attempts[kind] = (failures, self.clock() + delay if delay else 0.0)
        return failures, delay

    def retry_after(self) -> float:
        _, until = self._attempts.get("pin", (0, 0.0))
        return max(0.0, until - self.clock())

    # -- setup ------------------------------------------------------------------------
    def setup(self, pin: str) -> str:
        """Make the vault. Returns the recovery code ONCE; it is never stored in the clear."""
        with self._lock:
            if self.is_set_up():
                raise AlreadySetUp()
            ok, why = validate_vault_secret(pin)
            if not ok:
                raise WeakSecret(why)
            crypto.self_test()
            code = generate_recovery_code()
            vault_id = uuid.uuid4().hex
            dek = crypto.new_key()
            ring = {"schema": KEYRING_SCHEMA, "vault_id": vault_id, "created_at": _utc(), "ways": [], "sdk": None,
                    "inbox": None}
            ring["ways"].append(self._new_way(vault_id, dek, "pin", "PIN", pin, way_id="pin"))
            ring["ways"].append(self._new_way(vault_id, dek, "recovery", "Recovery code",
                                              normalize_recovery_code(code), way_id="recovery"))
            self.store.create()
            self.dir.mkdir(parents=True, exist_ok=True)
            self._dek, self._index_key = dek, crypto.hkdf(dek, b"pz1/index")
            self._root = {"epoch": 0, "items": {}}
            self._vault_id_cache = vault_id
            self._write_keyring(ring, dek)
            self._write_root()
            self._session = uuid.uuid4().hex[:16]
            self._touch()
            self.audit("setup", ways=2)
        self._opened("pin")
        return code

    def _new_way(self, vault_id: str, dek: bytes | bytearray, kind: str, label: str, secret: str,
                 way_id: str | None = None) -> dict:
        way_id = way_id or f"{kind}-{secrets.token_hex(4)}"
        rec = crypto.wrap(dek, secret, crypto.wrap_aad(vault_id, way_id, kind))
        return {"id": way_id, "kind": kind, "label": label, "added_at": _utc(), **rec}

    # -- unlock / lock --------------------------------------------------------------------
    def _health(self) -> dict:
        try:
            h = self.store.health()
        except Exception:
            return {"ok": False, "reason": MISSING}
        return h if isinstance(h, dict) else {"ok": False, "reason": MISSING}

    def _try_ways(self, ring: dict, kinds: tuple[str, ...], secret: str) -> tuple[bytearray, dict] | None:
        for way in ring["ways"]:
            if way["kind"] not in kinds:
                continue
            dek = crypto.unwrap(way, secret, crypto.wrap_aad(ring["vault_id"], way["id"], way["kind"]))
            if dek is not None:
                return dek, way
        return None

    def unlock(self, method: str, secret: str, *, accept_older: bool = False) -> str:
        """Open the vault with ``pin`` (a PIN or passphrase way) or ``recovery``. Returns the session id."""
        if method not in ("pin", "recovery"):
            if str(method).startswith("factor:"):
                raise Refused("No extra unlock methods are installed yet.")
            raise Refused("Unknown unlock method.")
        kind = "recovery" if method == "recovery" else "pin"
        with self._lock:
            ring = self._read_keyring()
            if not self._health().get("ok"):
                raise StoreMissing()
            self._check_throttle(kind)                     # before any KDF work
            secret = normalize_recovery_code(secret) if kind == "recovery" else str(secret or "")
            hit = self._try_ways(ring, ("recovery",) if kind == "recovery" else PIN_KINDS, secret) if secret else None
            if hit is None:
                count, delay = self._note_failure(kind)
                self.audit("unlock", "wrong", factor=method, count=count)
                self.publish("vault.unlock_failed", {"factor": method, "count": count, "backoff_s": int(delay)})
                raise WrongSecret()
            dek, way = hit
            self._attempts.pop(kind, None)
            self._open_with(ring, dek, accept_older=accept_older)
            if way.get("kdf", {}).get("m", 0) < crypto.KDF_DEFAULT["m"]:
                self.warnings.append("rewrap_due")
            problem = self._f10_problem(ring) or (None if self._ring_ok else "keyring_changed")
            if problem:
                # F10 on read: the keyring on disk lost a way in. Never a dead end (01 "Core start"): the key is
                # kept for repair only; is_open() stays False, so nothing is read, lent or filed.
                self._safe = problem
                self.warnings.append("safe_mode")
                self.audit("unlock", "safe_mode", factor=method, way=way["id"], why=problem)
                self.publish("vault.locked", {"reason": "tamper"})
                raise SafeMode()
            self.audit("unlock", factor=method, way=way["id"])
        self._opened(method)
        return self._session or ""

    def _open_with(self, ring: dict, dek: bytearray, *, accept_older: bool) -> None:
        vault_id = ring["vault_id"]
        index_key = crypto.hkdf(dek, b"pz1/index")
        got = self.store.get(ROOT_ID)
        if "error" in got:
            crypto.zero(dek)
            if got["error"] == "unavailable":
                raise StoreMissing()
            raise ItemTampered("Your vault's index is missing from the store.")
        try:
            root = json.loads(crypto.open_blob(dek, vault_id, ROOT_ID, got["version"], got["blob"]))
        except (Tampered, ValueError):
            crypto.zero(dek)
            self.audit("unlock", "tampered", item="root")
            self.publish("vault.locked", {"reason": "tamper"})
            raise ItemTampered("Your vault's index was changed outside PuffPAI.") from None
        seen = self._seen_epoch()
        if root.get("epoch", 0) != got["version"] or (root.get("epoch", 0) < seen and not accept_older):
            crypto.zero(dek)
            self.audit("unlock", "rolled_back", epoch=root.get("epoch", 0), seen=seen)
            self.publish("vault.locked", {"reason": "tamper"})
            raise RolledBack("Your vault went back in time. Use this older copy? Unlock again and choose to use it.")
        self.warnings = []
        mac = ring.get("mac")
        self._ring_ok = isinstance(mac, str) and hmac.compare_digest(mac, self._ring_mac(ring, dek))
        if not self._ring_ok:
            self.audit("keyring", "changed_outside")
        self._dek, self._index_key, self._root = dek, index_key, root
        self._session = uuid.uuid4().hex[:16]
        self._touch()
        if root.get("epoch", 0) < seen:                     # she chose the older copy
            self._write_epoch(root["epoch"])
        self._apply_tombstones()

    def _opened(self, factor: str) -> None:
        self.publish("vault.unlocked", {"factor": factor, "session": self._session})
        self._start_watch()
        for fn in list(self._listeners):
            try:
                fn("opened", factor)
            except Exception:
                logger.exception("vault open listener failed")

    def lock(self, reason: str = "owner") -> None:
        with self._lock:
            if self._dek is None:
                return
            self._do_lock(reason)

    def _do_lock(self, reason: str) -> None:
        crypto.zero(self._dek)
        self._dek = None
        self._safe = None
        self._index_key = None
        self._root = None
        self._session = None
        self._watch_stop.set()
        self.audit("lock", reason=reason)
        self.publish("vault.locked", {"reason": reason})
        for fn in list(self._listeners):
            try:
                fn("locked", reason)
            except Exception:
                logger.exception("vault lock listener failed")

    def _require_open(self, *, repair: bool = False, touch: bool = True) -> None:
        """Open for use; with *repair*, safe mode is enough (only the F10 repairs pass it). With ``touch=False`` (the
        Core's anchor seals, D153) it still needs an open vault, and the idle timer does not move."""
        if not self.is_set_up():
            raise NotSetUp()
        if self._safe is not None and self._dek is not None and not self._idle_expired():
            if not repair:
                raise SafeMode()
            self._touch()
            return
        if not self.is_open():
            raise Locked()
        if touch:
            self._touch()

    def _leave_safe_mode(self, ring: dict) -> bool:
        """After a repair write: out of safe mode when F10 holds again. True when it left."""
        if self._safe is None or self._f10_problem(ring):
            return False
        self._safe = None
        self.warnings = [w for w in self.warnings if w != "safe_mode"]
        self.audit("safe_mode_ended")
        return True

    def _repair_sdk(self, ring: dict) -> None:
        """Safe-mode repair: keep the unattended wraps only if the vault-key wrap and the machine wrap hold the
        same key; else drop them (her ticked keys stop working before the PIN until she ticks them again)."""
        sdk = ring.get("sdk")
        if not sdk:
            return
        a = b = None
        try:
            a = crypto.raw_unwrap(sdk["by_dek"], crypto.hkdf(self._dek, b"pz1/sdk"), self._sdk_aad())
            b = bytearray(self.machine.unprotect(bytes.fromhex(sdk["by_machine"])))
            same = hmac.compare_digest(bytes(a), bytes(b))
        except Exception:
            same = False
        finally:
            crypto.zero(a)
            crypto.zero(b)
        if not same:
            for uid in set((sdk.get("u_index") or {}).values()):
                if isinstance(uid, str):
                    self.store.delete(uid, None)       # the copies go with the wraps that opened them
            ring["sdk"] = None
            self.audit("unattended_reset", why="keyring_changed")

    def require_way_in_check(self, pin: str = "", code: str = "") -> None:
        """Adding or removing a way in is floor-level (D29, D47): the current PIN or the recovery code.
        Both are throttled (the PIN with unlock, the code with its own test counter)."""
        if pin and self.verify_pin(pin):
            return
        if code and self.test_recovery(code):
            return
        raise WrongSecret("Adding or removing a way in needs your current PIN or your recovery code.")

    # -- PIN checks without opening ---------------------------------------------------------
    def verify_pin(self, pin: str) -> bool:
        """True when *pin* opens a PIN way. Does not open the vault. Throttled with the PIN (D29)."""
        with self._lock:
            ring = self._read_keyring()
            self._check_throttle("pin")
            hit = self._try_ways(ring, PIN_KINDS, str(pin or "")) if pin else None
            if hit is None:
                count, _ = self._note_failure("pin")
                self.audit("verify_pin", "wrong", count=count)
                return False
            crypto.zero(hit[0])
            self._attempts.pop("pin", None)
            self.audit("verify_pin")
            return True

    def test_recovery(self, code: str) -> bool:
        """Check the recovery code without opening (its own throttle)."""
        with self._lock:
            ring = self._read_keyring()
            self._check_throttle("recovery_test")
            hit = self._try_ways(ring, ("recovery",), normalize_recovery_code(code)) if code else None
            if hit is None:
                self._note_failure("recovery_test")
                self.audit("recovery_test", "wrong")
                return False
            crypto.zero(hit[0])
            self._attempts.pop("recovery_test", None)
            self.audit("recovery_test")
            return True

    # -- ways in (F10) ------------------------------------------------------------------------
    def ways_in(self) -> list[dict]:
        ring = self._read_keyring()
        return [{"id": w["id"], "kind": w["kind"], "label": w["label"], "added_at": w.get("added_at")}
                for w in ring["ways"]]

    def add_way(self, kind: str, label: str, secret: str, *, pin: str = "", code: str = "") -> dict:
        """A new passphrase way in. Needs the vault open and the current PIN or the recovery code (D29)."""
        if kind != "passphrase":
            raise Refused("Only a PIN or passphrase can be added here; extra unlock methods come later.")
        ok, why = validate_vault_secret(secret)
        if not ok:
            raise WeakSecret(why)
        with self._lock:
            self._require_open()
            self.require_way_in_check(pin, code)
            ring = self._read_keyring()
            way = self._new_way(ring["vault_id"], self._dek, "passphrase", (label or "Passphrase")[:60], secret)
            ring["ways"].append(way)
            self._write_keyring(ring, self._dek)
            self.audit("way_added", way=way["id"], kind="passphrase")
            return {"id": way["id"], "kind": way["kind"], "label": way["label"]}

    def remove_way(self, way_id: str, *, pin: str = "", code: str = "") -> None:
        """Needs the vault open and the current PIN or the recovery code (D29); F10 decides what may go."""
        with self._lock:
            self._require_open()
            self.require_way_in_check(pin, code)
            ring = self._read_keyring()
            way = next((w for w in ring["ways"] if w["id"] == way_id), None)
            if way is None:
                raise NotFound("No way in has that id.")
            if way["kind"] == "recovery":
                raise Refused("The recovery code can't be removed. You can make a new one instead.")
            if len(ring["ways"]) - 1 < 2:
                raise Refused("Your vault needs at least two ways in, so this one stays.")
            if not any(w["kind"] in PIN_KINDS for w in ring["ways"] if w["id"] != way_id):
                raise Refused("Your vault needs a PIN or passphrase besides the recovery code.")
            ring["ways"] = [w for w in ring["ways"] if w["id"] != way_id]
            self._write_keyring(ring, self._dek)
            self.audit("way_removed", way=way_id)

    def change_pin(self, pin: str, new_pin: str) -> None:
        """Rewrap only (no new vault key), so backups stay valid. Needs the current PIN."""
        ok, why = validate_vault_secret(new_pin)
        if not ok:
            raise WeakSecret(why)
        if not self.verify_pin(pin):
            raise WrongSecret()
        with self._lock:
            self._require_open()
            ring = self._read_keyring()
            for i, w in enumerate(ring["ways"]):
                if w["id"] == "pin" or (w["kind"] == "pin"):
                    ring["ways"][i] = self._new_way(ring["vault_id"], self._dek, "pin", w["label"], new_pin,
                                                    way_id=w["id"])
                    break
            else:
                ring["ways"].append(self._new_way(ring["vault_id"], self._dek, "pin", "PIN", new_pin, way_id="pin"))
            self._write_keyring(ring, self._dek)
            self.audit("pin_changed")

    def rotate_recovery(self, pin: str) -> str:
        """A new recovery code (shown once); the old one stops working. Needs the PIN. Also the safe-mode
        repair when the keyring lost its recovery way (F10)."""
        if not self.verify_pin(pin):
            raise WrongSecret()
        with self._lock:
            self._require_open(repair=True)
            ring = self._read_keyring()
            if self._safe:
                self._repair_sdk(ring)
            code = generate_recovery_code()
            ring["ways"] = [w for w in ring["ways"] if w["kind"] != "recovery"]
            ring["ways"].append(self._new_way(ring["vault_id"], self._dek, "recovery", "Recovery code",
                                              normalize_recovery_code(code), way_id="recovery"))
            self._write_keyring(ring, self._dek)
            self.audit("recovery_rotated")
            left = self._leave_safe_mode(ring)
        if left:
            self._opened("repair")
        return code

    def recover(self, code: str, new_pin: str) -> str:
        """Forgotten PIN: open with the recovery code, re-key everything, set a new PIN, return a new code."""
        ok, why = validate_vault_secret(new_pin)
        if not ok:
            raise WeakSecret(why)
        try:
            self.unlock("recovery", code)
        except SafeMode:
            pass                                          # recovery re-keys every way in: the F10 repair
        with self._lock:
            self._require_open(repair=True)
            new_code = self._rekey(new_pin)
            # A right recovery code proves more than a PIN does, and the guesses counted were at a PIN and a code that
            # are gone now. Only here, once the new ones are in: a wrong code never gets this far and keeps its wait.
            self._attempts.clear()
            self.audit("recovered")
            left = self._leave_safe_mode(self._read_keyring())
        if left:
            self._opened("recovery")
        return new_code

    def _rekey(self, new_pin: str) -> str:
        """New vault key; every blob re-sealed; new PIN and recovery wraps; then one commit (the keyring)."""
        ring = self._read_keyring()
        if self._safe:
            self._repair_sdk(ring)
        vid = ring["vault_id"]
        old_dek, old_root = self._dek, self._root
        records = {}
        for bid, meta in old_root["items"].items():
            try:
                records[bid] = (meta, self._read_blob(bid, meta, old_dek))
            except VaultError:
                self.audit("rekey_skip", "tampered", item=meta.get("name"))
        new_dek = crypto.new_key()
        new_index = crypto.hkdf(new_dek, b"pz1/index")
        self._dek, self._index_key = new_dek, new_index
        new_root = {"epoch": old_root["epoch"], "items": {}}
        self._root = new_root
        for bid, (meta, plain) in records.items():
            nb = self._blob_id(meta["name"])
            self._put_blob(nb, plain, meta)
        code = generate_recovery_code()
        ways = [w for w in ring["ways"] if w["kind"] not in ("pin", "passphrase", "recovery")]
        ways.append(self._new_way(vid, new_dek, "pin", "PIN", new_pin, way_id="pin"))
        ways.append(self._new_way(vid, new_dek, "recovery", "Recovery code", normalize_recovery_code(code),
                                  way_id="recovery"))
        ring["ways"] = ways
        sdk = None
        if ring.get("sdk"):
            try:
                sdk = crypto.raw_unwrap(ring["sdk"]["by_dek"], crypto.hkdf(old_dek, b"pz1/sdk"),
                                        f"pz1/sdk|{vid}".encode())
                ring["sdk"]["by_dek"] = crypto.raw_wrap(sdk, crypto.hkdf(new_dek, b"pz1/sdk"), f"pz1/sdk|{vid}".encode())
            except Tampered:
                ring["sdk"] = None
        self._write_keyring(ring, new_dek)                 # the single commit point
        for bid in old_root["items"]:
            if bid not in new_root["items"]:
                self.store.delete(bid, None)
        crypto.zero(old_dek)
        crypto.zero(sdk)
        return code

    # -- root ------------------------------------------------------------------------------------
    def _seen_epoch(self) -> int:
        try:
            return int(self.epoch_path.read_text(encoding="ascii").strip() or 0)
        except (OSError, ValueError):
            return 0

    def _write_epoch(self, epoch: int) -> None:
        _atomic_write(self.epoch_path, str(int(epoch)).encode("ascii"))

    def _write_root(self) -> None:
        root = self._root
        prev = root["epoch"]
        root["epoch"] = prev + 1
        blob = crypto.seal(self._dek, self._vid(), ROOT_ID, root["epoch"],
                           json.dumps(root, sort_keys=True).encode("utf-8"))
        r = self.store.put(ROOT_ID, root["epoch"], blob, None)
        if "error" in r:
            root["epoch"] = prev
            if r["error"] == "unavailable":
                raise StoreMissing()
            raise Refused("The vault store refused the write.")
        self._write_epoch(root["epoch"])

    def _vid(self) -> str:
        vid = getattr(self, "_vault_id_cache", None)
        if not vid:
            vid = self._vault_id_cache = self._read_keyring()["vault_id"]
        return vid

    # -- items -------------------------------------------------------------------------------------
    def _blob_id(self, name: str, key: bytes | None = None) -> str:
        return "i" + crypto.mac(key or self._index_key, b"pz1/blob-id", name.encode("utf-8"))[:40]

    def _put_blob(self, bid: str, record: dict, meta: dict) -> None:
        old = self._root["items"].get(bid)
        version = (old["v"] + 1) if old else 1
        plain = json.dumps(record, sort_keys=True).encode("utf-8")
        blob = crypto.seal(self._dek, self._vid(), bid, version, plain)
        r = self.store.put(bid, version, blob, None)
        if "error" in r:
            if r["error"] == "unavailable":
                raise StoreMissing()
            raise Refused("The vault store refused the write.")
        self._root["items"][bid] = {**{k: v for k, v in meta.items() if k != "v"}, "v": version,
                                    "sha": crypto.sha256(blob)}
        self._write_root()

    def _read_blob(self, bid: str, meta: dict, dek: bytes | bytearray | None = None) -> dict:
        got = self.store.get(bid)
        if "error" in got:
            if got["error"] == "unavailable":
                raise StoreMissing()
            raise ItemTampered("This item is missing from the vault store.")
        if got["version"] != meta["v"] or crypto.sha256(got["blob"]) != meta["sha"]:
            raise ItemTampered()
        try:
            record = json.loads(crypto.open_blob(dek or self._dek, self._vid(), bid, got["version"], got["blob"]))
        except (Tampered, ValueError):
            raise ItemTampered() from None
        if record.get("name") != meta.get("name"):           # bound to its name, inside and in the AAD
            raise ItemTampered()
        return record

    def put(self, name: str, value: bytes | str, *, kind: str = "secret", label: str = "secret.api_key",
            tier: str = "P4", extra: dict | None = None, reserved: bool = False, touch: bool = True) -> str:
        """Seal one item under *name*. Returns its ref. Vault must be open.

        Every write to a name first deletes its machine-wrapped copy (the ticked tier, D27), so an old value
        can never come back before the PIN; if that delete fails, nothing is written. A write is never ticked
        by itself: only ``set_unattended(name, True)`` makes a new wrap, of the value saved now."""
        check_name(name, reserved=reserved)
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        extra = {k: v for k, v in (extra or {}).items() if k != "unattended"}
        with self._lock:
            self._require_open(touch=touch)
            self._delete_unattended(name, strict=True)
            bid = self._blob_id(name)
            old = self._root["items"].get(bid)
            ref = (old or {}).get("ref") or ("k" + bid[1:17])
            record = {"name": name, "kind": kind, "label": label, "tier": tier, "ref": ref,
                      "value": data.hex(), "saved_at": _utc(), **(extra or {})}
            self._put_blob(bid, record, {"name": name, "kind": kind, "label": label, "tier": tier, "ref": ref,
                                         **{k: v for k, v in (extra or {}).items() if k in ("unattended", "card_backed", "expire_at")}})
            self._clear_tombstone(name)
            self.audit("stored", name=self._safe_name(name), kind=kind)
            return ref

    def get(self, name: str, *, touch: bool = True) -> bytes:
        """The value of *name*. Core-internal (Archivist only); never returned over HTTP."""
        with self._lock:
            self._require_open(touch=touch)
            if self._tombstoned(name):
                raise NotFound()
            bid = self._blob_id(name)
            meta = self._root["items"].get(bid)
            if meta is None:
                raise NotFound()
            record = self._read_blob(bid, meta)
            return bytes.fromhex(record["value"])

    def meta(self, name: str, *, touch: bool = True) -> dict | None:
        with self._lock:
            self._require_open(touch=touch)
            if self._tombstoned(name):
                return None
            m = self._root["items"].get(self._blob_id(name))
            return dict(m) if m else None

    def items(self, kind: str | None = None) -> list[dict]:
        """Names and labels only, never values."""
        with self._lock:
            self._require_open()
            out = []
            for m in self._root["items"].values():
                if m["name"].startswith(RESERVED) or (kind and m["kind"] != kind):
                    continue
                if self._tombstoned(m["name"]):
                    continue
                out.append({k: m[k] for k in ("name", "kind", "label", "tier", "ref") if k in m}
                           | {"unattended": bool(m.get("unattended"))})
            return out

    def find_ref(self, ref: str) -> str | None:
        with self._lock:
            self._require_open()
            for m in self._root["items"].values():
                if m.get("ref") == ref:
                    return m["name"]
            return None

    def delete(self, name: str) -> bool:
        """Works while locked: off is one step. Locked: the ticked copy's file goes now (no machine key needed)
        and a tombstone stops every use of the name until the next unlock removes the item. The tombstone is
        cleared only once the ticked copy is confirmed gone."""
        with self._lock:
            if not self.is_set_up():
                raise NotSetUp()
            if not self.is_open():
                self._add_tombstone(name)            # first: from here on nothing hands the name out
                self._delete_unattended(name)
                self.audit("deleted", name=self._safe_name(name), when="locked")
                return True
            self._touch()
            gone = self._delete_unattended(name)
            if not gone:
                self._add_tombstone(name)            # retried at the next unlock; still blocks the ticked tier
            bid = self._blob_id(name)
            meta = self._root["items"].pop(bid, None)
            if meta is None:
                return False
            self.store.delete(bid, None)
            self._write_root()
            if gone:
                self._clear_tombstone(name)
            self.audit("deleted", name=self._safe_name(name))
            return True

    @staticmethod
    def _safe_name(name: str) -> str:
        return name if not name.startswith("held:") else "held"

    # -- tombstones (delete while locked) ----------------------------------------------------------
    def _tomb_key(self, name: str) -> str:
        return crypto.sha256(f"pz1/tomb|{self._vid()}|{name}".encode("utf-8"))

    def _tombs(self) -> list[str]:
        try:
            data = json.loads(self.tomb_path.read_text(encoding="utf-8"))
            return [t for t in data if isinstance(t, str)]
        except (OSError, ValueError):
            return []

    def _tombstoned(self, name: str) -> bool:
        return self._tomb_key(name) in self._tombs()

    def _add_tombstone(self, name: str) -> None:
        tombs = self._tombs()
        key = self._tomb_key(name)
        if key not in tombs:
            tombs.append(key)
            _atomic_write(self.tomb_path, json.dumps(tombs).encode("utf-8"))

    def _clear_tombstone(self, name: str) -> None:
        tombs = self._tombs()
        key = self._tomb_key(name)
        if key in tombs:
            tombs.remove(key)
            _atomic_write(self.tomb_path, json.dumps(tombs).encode("utf-8"))

    def _apply_tombstones(self) -> None:
        """At unlock: remove each tombstoned item and its ticked copy. A tombstone is cleared only when its
        ticked copy is confirmed gone; one that can't be confirmed stays (and keeps blocking the name)."""
        tombs = set(self._tombs())
        if not tombs:
            return
        changed = False
        names = {self._tomb_key(m["name"]): m["name"] for m in self._root["items"].values()}
        keep: list[str] = []
        for tk in sorted(tombs):
            if not self._delete_wraps(tk, names.get(tk)):
                keep.append(tk)
        for bid, meta in list(self._root["items"].items()):
            if self._tomb_key(meta["name"]) in tombs:
                self._root["items"].pop(bid)
                self.store.delete(bid, None)
                changed = True
        if changed:
            self._write_root()
        _atomic_write(self.tomb_path, json.dumps(keep).encode("utf-8"))

    # -- unattended tier (D27) -------------------------------------------------------------------------
    def _sdk_aad(self) -> bytes:
        return f"pz1/sdk|{self._vid()}".encode()

    def _sdk_open(self, create: bool) -> bytearray:
        """The SDK, from the DEK wrap (vault open)."""
        ring = self._read_keyring()
        if ring.get("sdk"):
            return crypto.raw_unwrap(ring["sdk"]["by_dek"], crypto.hkdf(self._dek, b"pz1/sdk"), self._sdk_aad())
        if not create:
            raise NotFound()
        if not self.machine.available():
            raise Refused(machine_key.not_available_line(self.machine))
        sdk = crypto.new_key()
        ring["sdk"] = {"by_dek": crypto.raw_wrap(sdk, crypto.hkdf(self._dek, b"pz1/sdk"), self._sdk_aad()),
                       "by_machine": self.machine.protect(bytes(sdk)).hex()}
        self._write_keyring(ring, self._dek)
        self.audit("unattended_enrolled")
        return sdk

    def _sdk_machine(self) -> bytearray:
        ring = self._read_keyring()
        if not ring.get("sdk"):
            raise Locked()
        try:
            return bytearray(self.machine.unprotect(bytes.fromhex(ring["sdk"]["by_machine"])))
        except (machine_key.MachineKeyUnavailable, ValueError):
            raise Locked() from None

    def _u_id(self, sdk: bytes | bytearray, name: str) -> str:
        return "u" + crypto.mac(sdk, b"pz1/u-id", name.encode("utf-8"))[:40]

    def set_unattended(self, name: str, on: bool) -> None:
        """Her tick per key (D27). Card-backed keys never. Vault must be open."""
        check_name(name)
        with self._lock:
            self._require_open()
            meta = self.meta(name)
            if meta is None:
                raise NotFound()
            if on and meta.get("card_backed"):
                raise Refused("Keys linked to a payment card never work before your PIN.")
            value = self.get(name)
            self._delete_unattended(name, strict=True)              # first, on or off: never a stale copy
            bid = self._blob_id(name)
            record = self._read_blob(bid, self._root["items"][bid])
            record["unattended"] = bool(on)
            meta = self._root["items"][bid]
            self._put_blob(bid, record, {**meta, "unattended": bool(on)})
            if on:
                sdk = self._sdk_open(create=True)
                uid = self._u_id(sdk, name)
                plain = json.dumps({"name": name, "value": value.hex()}).encode("utf-8")
                ring = self._read_keyring()
                ring["sdk"].setdefault("u_index", {})[self._tomb_key(name)] = uid
                self._write_keyring(ring, self._dek)               # the index first: a copy is always findable
                r = self.store.put(uid, 1, crypto.seal(sdk, self._vid(), uid, 1, plain), None)
                crypto.zero(sdk)
                if "error" in r:
                    raise StoreMissing() if r["error"] == "unavailable" else Refused("The vault store refused "
                                                                                       "the write.")
                self.audit("unattended_on", name=name)
            else:
                self.audit("unattended_off", name=name)

    def _u_index(self) -> dict:
        """``{tomb key of a name: ticked-copy id}``, kept in the keyring (covered by its MAC), readable while
        locked, so a ticked copy is found and deleted with no machine key and no vault key."""
        try:
            sdk = self._read_keyring().get("sdk") or {}
        except VaultError:
            return {}
        index = sdk.get("u_index") if isinstance(sdk, dict) else None
        return {k: v for k, v in (index or {}).items() if isinstance(k, str) and isinstance(v, str)}

    def _delete_wraps(self, tk: str, name: str | None) -> bool:
        """Delete every ticked copy of the name whose tomb key is *tk*; True once none is left in the store."""
        uids = set()
        index = self._u_index()
        if tk in index:
            uids.add(index[tk])
        if name is not None and self._dek is not None:
            try:                                  # older copies with no index row: the vault-key path finds them
                sdk = self._sdk_open(create=False)
                try:
                    uids.add(self._u_id(sdk, name))
                finally:
                    crypto.zero(sdk)
            except (NotFound, Tampered, VaultError):
                pass
        for uid in uids:
            self.store.delete(uid, None)
        for uid in uids:
            got = self.store.get(uid)
            if got.get("error") != "not_found":
                return False                      # still there, or the store can't say: not confirmed gone
        if tk in index and self._dek is not None:
            ring = self._read_keyring()
            ring["sdk"]["u_index"].pop(tk, None)
            self._write_keyring(ring, self._dek)
        return True

    def _delete_unattended(self, name: str, *, strict: bool = False) -> bool:
        """Remove *name*'s ticked copy, if any; True once it is confirmed gone. Never needs the machine key.
        With *strict*, a copy that can't be confirmed gone refuses (nothing is written)."""
        gone = self._delete_wraps(self._tomb_key(name), name)
        if strict and not gone:
            if not self._health().get("ok"):
                raise StoreMissing()
            raise Refused("The old copy of this key that works before your PIN couldn't be removed, so "
                          "nothing was saved.")
        return gone

    def get_unattended(self, name: str) -> bytes:
        """A ticked key while locked (package start after a restart). Refused for a name with a live
        tombstone or no current ticked-copy row (the keyring's index). Honest: no rollback check here."""
        with self._lock:
            if name.startswith(RESERVED) or self._tombstoned(name):
                raise NotFound()
            listed = self._u_index().get(self._tomb_key(name))
            if listed is None:
                raise NotFound()
            sdk = self._sdk_machine()
            try:
                uid = self._u_id(sdk, name)
                if uid != listed:
                    raise ItemTampered()
                got = self.store.get(uid)
                if "error" in got:
                    if got["error"] == "unavailable":
                        raise StoreMissing()
                    raise Locked()
                try:
                    record = json.loads(crypto.open_blob(sdk, self._vid(), uid, got["version"], got["blob"]))
                except (Tampered, ValueError):
                    raise ItemTampered() from None
                if record.get("name") != name:
                    raise ItemTampered()
                return bytes.fromhex(record["value"])
            finally:
                crypto.zero(sdk)

    # -- status ------------------------------------------------------------------------------------------
    def status(self) -> dict:
        if not self.is_set_up():
            return {"state": "not_set_up", "ways_in": [], "store": self._health()}
        health = self._health()
        with self._lock:                 # one moment: a lock during this read must not fail it (a page reads that as locked)
            open_ = self.is_open()
            rows = list(self._root["items"].values()) if open_ else []
        state = "open" if open_ else "locked"
        try:
            problem = self._safe or self._f10_problem(self._read_keyring())
        except VaultError:
            problem = "unreadable"
        if problem:
            return {"state": "safe_mode", "why": problem, "message": SafeMode.__doc__,
                    "ways_in": [], "store": {"ok": bool(health.get("ok")), "reason": health.get("reason", "")},
                    "retry_after": int(self.retry_after()), "warnings": ["safe_mode"]}
        if not health.get("ok"):
            state = "missing"
        elif not open_ and self.retry_after() > 0:
            state = "throttled"
        out: dict[str, Any] = {
            "state": state,
            "retry_after": int(self.retry_after()) if state == "throttled" else 0,
            "ways_in": [w["label"] for w in self.ways_in()],
            "store": {"ok": bool(health.get("ok")), "reason": health.get("reason", ""),
                      "location": self.store.info().get("location_text", "")},
            "idle_lock_minutes": int(self._idle_limit() // 60),     # in force now, so "locks after N" stays true
            "idle_own_minutes": int(self.idle_s // 60),             # her setting, which applies again after a hold
            "idle_left_s": int(self.idle_left_s() or 0) if open_ else None,
            "warnings": list(self.warnings) if open_ else [],
        }
        if open_:
            out["items"] = len([m for m in rows if not m["name"].startswith(RESERVED)])
            out["unattended_keys"] = [m["name"] for m in rows if m.get("unattended")]
        return out
