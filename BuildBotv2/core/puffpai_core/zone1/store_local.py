# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/vault.py (_atomic_write :2155, VaultMissing :767,
# _refuse_if_missing :1061; adapted to one file per blob)
"""``vault-store-local``: the default ``zone1.vault-store`` provider (Z1V 4.1, 6).

Blobs are files in the vault folder she picked. This store sees ciphertext only:
the Core seals every blob before ``put`` and checks every tag after ``get``.
It answers the slot contract (``zone1.slots.VaultStore``) with errors returned,
never raised.

Rules kept from v1:
- ``put`` is atomic: a temp file, flushed, then ``os.replace`` (old or new, never torn).
- A missing folder is ``unavailable`` ("Your vault drive is not plugged in."),
  never ``not_found`` and never a new empty vault (F8).

One file per blob (``<id>.blob``: 8-byte version, then the sealed bytes), so a
bad sector or a torn write loses one item, not the vault (the v1 whole-store
rewrite, ``_flush`` ``vault.py:2144``, is gone).
"""

from __future__ import annotations

import os
import re
import struct
import threading
from pathlib import Path

CONTRACT = 1
MAX_BLOB_BYTES = 4 * 1024 * 1024
MISSING = "Your vault drive is not plugged in."
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SUFFIX = ".blob"


class LocalStore:
    """Sealed blobs as files in one folder. Exactly one active store per vault."""

    def __init__(self, folder: Path | str, location_text: str = "this computer") -> None:
        self.folder = Path(folder)
        self.location_text = location_text
        self._lock = threading.Lock()

    # -- slot operations ------------------------------------------------------
    def info(self) -> dict:
        return {"contract": CONTRACT, "location_text": self.location_text, "remote": False,
                "max_blob_bytes": MAX_BLOB_BYTES}

    def health(self) -> dict:
        if not self.folder.is_dir():
            return {"ok": False, "reason": MISSING}
        return {"ok": True, "reason": ""}

    def create(self) -> None:
        """Make the folder at setup. Only setup calls this; nothing else ever creates it."""
        self.folder.mkdir(parents=True, exist_ok=True)

    def put(self, id: str, version: int, blob: bytes, expect_version: int | None) -> dict:
        if not _ID.match(str(id)) or not isinstance(version, int) or version < 1:
            return {"error": "refused", "reason": "bad id or version"}
        if not isinstance(blob, (bytes, bytearray)) or len(blob) > MAX_BLOB_BYTES:
            return {"error": "refused", "reason": "blob too big"}
        if not self.folder.is_dir():
            return {"error": "unavailable", "reason": MISSING}
        with self._lock:
            current = self._version(id)
            if expect_version is not None and current != expect_version:
                return {"error": "conflict", "current": current}
            path = self._path(id)
            tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
            try:
                with open(tmp, "wb") as fh:
                    fh.write(struct.pack(">Q", version) + bytes(blob))
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except OSError as exc:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                if not self.folder.is_dir():
                    return {"error": "unavailable", "reason": MISSING}
                return {"error": "full" if getattr(exc, "errno", None) == 28 else "unavailable",
                        "reason": "The vault folder could not be written."}
        return {"ok": True}

    def get(self, id: str) -> dict:
        if not self.folder.is_dir():
            return {"error": "unavailable", "reason": MISSING}
        if not _ID.match(str(id)):
            return {"error": "not_found"}
        try:
            data = self._path(id).read_bytes()
        except FileNotFoundError:
            return {"error": "not_found"}
        except OSError:
            return {"error": "unavailable", "reason": "The vault folder could not be read."}
        if len(data) < 8:
            return {"version": 0, "blob": b""}          # torn or damaged: the Core's tag check refuses it
        (version,) = struct.unpack(">Q", data[:8])
        return {"version": version, "blob": data[8:]}

    def list(self, after: str | None = None) -> dict:
        if not self.folder.is_dir():
            return {"error": "unavailable", "reason": MISSING}
        items = []
        for p in sorted(self.folder.glob("*" + _SUFFIX)):
            bid = p.name[:-len(_SUFFIX)]
            if after is not None and bid <= after:
                continue
            try:
                size = p.stat().st_size
                with open(p, "rb") as fh:
                    head = fh.read(8)
            except OSError:
                continue
            version = struct.unpack(">Q", head)[0] if len(head) == 8 else 0
            items.append({"id": bid, "version": version, "size": max(0, size - 8)})
        return {"items": items, "next": None}

    def delete(self, id: str, expect_version: int | None) -> dict:
        if not self.folder.is_dir():
            return {"error": "unavailable", "reason": MISSING}
        with self._lock:
            current = self._version(id)
            if current is None:
                return {"error": "not_found"}
            if expect_version is not None and current != expect_version:
                return {"error": "conflict", "current": current}
            try:
                self._path(id).unlink()
            except FileNotFoundError:
                return {"error": "not_found"}
            except OSError:
                return {"error": "unavailable", "reason": "The vault folder could not be written."}
        return {"ok": True}

    # -- helpers ----------------------------------------------------------------
    def _path(self, id: str) -> Path:
        return self.folder / (id + _SUFFIX)

    def _version(self, id: str) -> int | None:
        try:
            with open(self._path(id), "rb") as fh:
                head = fh.read(8)
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return struct.unpack(">Q", head)[0] if len(head) == 8 else 0
