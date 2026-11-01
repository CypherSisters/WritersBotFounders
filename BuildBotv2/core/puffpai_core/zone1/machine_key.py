"""The machine wrap for the unattended tier (D27, Z1V 3 "SDK").

Windows DPAPI (CurrentUser scope) on Windows; the file wrap (:mod:`..machine_file`)
everywhere else.

Only the SDK (a separate key for keys she ticked "keep working after a restart")
is ever wrapped here. The vault key (DEK) is never wrapped by anything the
machine alone supplies (F9).

HONEST LIMIT, said on her card too: the machine wrap unwraps for any program
running as her user on this computer. So a ticked key is only as safe as her
login: any program she runs could read it, with no PIN. That is the trade she
chooses per key. Card-backed keys are never allowed in this tier. Off Windows, a
backup that copies her whole home folder carries the wrap key too.

When the machine wrap can't be used this tier is not available (fail closed:
nothing works before the PIN), and her other keys are not touched.
"""

from __future__ import annotations

import sys

from .. import machine_file

_ENTROPY = b"puffpai-zone1-sdk/1"
CARD_LINE = ("This key works after a restart without your PIN. "
             "Anyone who can sign in to your Windows account could use it."
             if sys.platform == "win32" else
             "This key works after a restart without your PIN. "
             "Anyone who can sign in to your account on this computer could use it.")
NOT_AVAILABLE_LINE = "Keys that work after a restart can't be used on this computer right now."


class MachineKeyUnavailable(Exception):
    pass


_FILE_WRAP = machine_file.FileWrap(_ENTROPY, error=MachineKeyUnavailable)


def available() -> bool:
    return sys.platform == "win32" or _FILE_WRAP.available()


def not_available_line(machine: object = None) -> str:
    """Her line when the tier is refused, with the file wrap's plain reason when there is one."""
    why = getattr(machine, "reason", "")
    return f"{NOT_AVAILABLE_LINE} {why}" if why else NOT_AVAILABLE_LINE


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _UI_FORBIDDEN = 0x1

    def _blob(data: bytes) -> tuple[_Blob, object]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn, data: bytes) -> bytes:
        src, _keep = _blob(data)
        ent, _keep2 = _blob(_ENTROPY)
        out = _Blob()
        if fn is _crypt32.CryptProtectData:
            ok = fn(ctypes.byref(src), "PuffPAI", ctypes.byref(ent), None, None, _UI_FORBIDDEN, ctypes.byref(out))
        else:
            ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, _UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise MachineKeyUnavailable("Windows refused the machine wrap.")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return _FILE_WRAP.protect(bytes(data))
    return _call(_crypt32.CryptProtectData, bytes(data))


def unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return _FILE_WRAP.unprotect(bytes(data))
    return _call(_crypt32.CryptUnprotectData, bytes(data))


class Dpapi:
    """The real machine wrap (off Windows, the file wrap; the name stays). Tests replace it with a fake
    (``Vault(machine=...)``); a conftest guard makes any real machine wrap call fail inside the test suite."""

    @property
    def reason(self) -> str:
        return _FILE_WRAP.reason

    def available(self) -> bool:
        return available()

    def protect(self, data: bytes) -> bytes:
        return protect(data)

    def unprotect(self, data: bytes) -> bytes:
        return unprotect(data)


__all__ = ["CARD_LINE", "Dpapi", "MachineKeyUnavailable", "NOT_AVAILABLE_LINE", "available", "not_available_line",
           "protect", "unprotect"]
