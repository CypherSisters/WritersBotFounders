"""Pairing a browser: a one-time code from ``puffpai pair`` becomes a paired-device cookie.

The code is made and shown on this computer only (the CLI prints it, or the link
``http://puffpai.localhost:8420/#pair=<code>``, D79, or the Settings tile on this computer, D130). The Core keeps a
SHA-256 hash of it with an end time; it works once, for 10 minutes. ``POST /v1/pair`` trades it for a new
paired-device token (``auth.TokenStore.issue_device``), set as this Core's device cookie (``auth.cookie_name``, D129) on
``puffpai.localhost`` (D79). Unless the owner has turned off her home network (D130, ``api.network``), a device
there can type the code on its pair screen, with a limit on wrong tries, and gets the same two halves, its cookie named
for that listener's port. Package pages never get it (D49, D53).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path

from . import jsonfile

LIFETIME_S = 600
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().replace("-", "").encode("utf-8")).hexdigest()


def _path(state: Path) -> Path:
    return Path(state) / "pairing.json"


def new_code(state: Path, now: float | None = None) -> str:
    now = time.time() if now is None else now
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    with jsonfile.LOCK:
        codes = [c for c in jsonfile.read(_path(state), []) if c["until"] > now]
        codes.append({"hash": _hash(code), "until": now + LIFETIME_S})
        jsonfile.write(_path(state), codes)
    return code


def redeem(state: Path, code: object, now: float | None = None) -> bool:
    if not isinstance(code, str) or not code.strip():
        return False
    now = time.time() if now is None else now
    want = _hash(code)
    with jsonfile.LOCK:
        codes = jsonfile.read(_path(state), [])
        hit = next((c for c in codes if secrets.compare_digest(c["hash"], want) and c["until"] > now), None)
        left = [c for c in codes if c is not hit and c["until"] > now]
        jsonfile.write(_path(state), left)
    return hit is not None
