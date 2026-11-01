"""Tamper anchors (09 section 3): a MAC under the machine key, for files the Core alone writes.

The Bridge policy, the consent records and the sealed heads of the outside
log and the audit log each carry an HMAC-SHA256 made with one 32-byte key.
That key lives at ``<state>/settings/anchor.key``, wrapped by the machine
wrap, with its own entropy (Windows: DPAPI, CurrentUser scope; elsewhere the
file wrap, :mod:`.machine_file`). A same-user script that edits a file and
recomputes its plain sha still fails the MAC, so the Core reads the file as
damaged and closes (X24).

Honest limit (said in 09 section 3): the machine wrap unwraps for any program
running as the owner's user on this computer. A program written to target
PuffPAI can unwrap the key, then forge any MAC here. This stops a stray or
generic edit; only the vault copy (sealed at unlock, lane L3) stops a targeted
one. Off Windows, a backup that copies the whole home folder carries the wrap
key too.

No machine wrap (the wrap key can't be used, or Windows refused it):
:attr:`Anchor.available` is False, nothing can be MAC'd or checked, and every
anchored file reads as damaged (closed), with a plain reason. Fail closed,
never "unchecked".

Tests pass a fake ``machine`` (``protect``/``unprotect``/``available``); the
test suite's guard makes any real machine wrap call fail.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
from pathlib import Path
from typing import Any, Protocol

from . import machine_file

KEY_NAME = "anchor.key"
_ENTROPY = b"puffpai-anchor/1"
_DOMAIN = b"puffpai-anchor/1:"
NO_KEY_LINE = ("PuffPAI can't protect its own settings on this computer (no machine key), "
               "so the Bridge stays closed.")


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode("ascii")).hexdigest()


class AnchorUnavailable(Exception):
    """No machine key: nothing can be MAC'd or checked."""


class Machine(Protocol):
    def available(self) -> bool: ...
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, data: bytes) -> bytes: ...


def _dpapi(protect: bool, data: bytes) -> bytes:
    """One real DPAPI call (CurrentUser, no UI). Windows only. Never called by the test suite."""
    if sys.platform != "win32":
        raise AnchorUnavailable("not_windows")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    src_buf = ctypes.create_string_buffer(data, len(data))
    ent_buf = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    src = Blob(len(data), ctypes.cast(src_buf, ctypes.POINTER(ctypes.c_char)))
    ent = Blob(len(_ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(src), "PuffPAI anchor", ctypes.byref(ent), None, None, 0x1,
                                      ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1,
                                        ctypes.byref(out))
    if not ok:
        raise AnchorUnavailable("dpapi_refused")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


class Dpapi:
    """The real machine wrap for anchors (its own entropy, separate from Zone 1's unattended tier)."""

    def available(self) -> bool:
        return sys.platform == "win32"

    def protect(self, data: bytes) -> bytes:
        return _dpapi(True, bytes(data))

    def unprotect(self, data: bytes) -> bytes:
        return _dpapi(False, bytes(data))


def default_machine() -> Machine:
    """The machine wrap the Core uses: DPAPI on Windows, the file wrap elsewhere. The test suite replaces this
    function with a fake."""
    if sys.platform == "win32":
        return Dpapi()
    return machine_file.FileWrap(_ENTROPY, error=AnchorUnavailable)


class Anchor:
    """One machine key for one bot folder. ``mac(purpose, data)`` and ``check(purpose, data, mac)``."""

    def __init__(self, key_path: Path | str, machine: Machine | None = None) -> None:
        self.key_path = Path(key_path)
        self.machine = machine if machine is not None else default_machine()
        self.problem = ""
        self.why = ""           # the file wrap's plain reason, when it has one; DPAPI and the fakes have none
        self._key: bytes | None = None
        self._load()

    def _load(self) -> None:
        m = self.machine
        try:
            if m is None or not m.available():
                self.problem = "no_machine_key"
                self.why = getattr(m, "reason", "")
                return
            if self.key_path.exists():
                key = m.unprotect(self.key_path.read_bytes())
                if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
                    raise ValueError("key length")
            else:
                key = secrets.token_bytes(32)
                blob = m.protect(key)
                self.key_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.key_path.with_name(self.key_path.name + f".{secrets.token_hex(4)}.tmp")
                tmp.write_bytes(blob)
                os.replace(tmp, self.key_path)
            self._key = bytes(key)
        except Exception:  # noqa: BLE001 - an unreadable key is no key (fail closed); the file is never replaced
            self._key = None
            self.problem = self.problem or "machine_key_unreadable"
            self.why = getattr(m, "reason", "")

    def no_key_line(self) -> str:
        """The owner's line for a missing key, with the plain reason when there is one."""
        return f"{NO_KEY_LINE} {self.why}" if self.why else NO_KEY_LINE

    @property
    def available(self) -> bool:
        return self._key is not None

    def mac(self, purpose: str, data: str | bytes) -> str:
        if self._key is None:
            raise AnchorUnavailable(self.problem or "no_machine_key")
        sub = hmac.new(self._key, _DOMAIN + purpose.encode("ascii"), hashlib.sha256).digest()
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        return hmac.new(sub, raw, hashlib.sha256).hexdigest()

    def check(self, purpose: str, data: str | bytes, mac: Any) -> bool:
        """True only when the key is here and *mac* is right. No key: False (closed)."""
        if not isinstance(mac, str) or not mac:
            return False
        try:
            want = self.mac(purpose, data)
        except AnchorUnavailable:
            return False
        return hmac.compare_digest(want, mac)


_SHARED: dict[str, Anchor] = {}
_SHARED_LOCK = threading.Lock()


def for_state(state: Path | str, machine: Machine | None = None) -> Anchor:
    """The one Anchor of a bot folder (shared, so the key is made once)."""
    path = Path(state) / "settings" / KEY_NAME
    key = os.path.normcase(os.path.abspath(path))
    with _SHARED_LOCK:
        a = _SHARED.get(key)
        if a is None or (machine is not None and a.machine is not machine) or not a.available:
            a = Anchor(path, machine)
            _SHARED[key] = a
        return a


__all__ = ["Anchor", "AnchorUnavailable", "Dpapi", "KEY_NAME", "Machine", "NO_KEY_LINE", "canon",
           "default_machine", "for_state", "sha"]
