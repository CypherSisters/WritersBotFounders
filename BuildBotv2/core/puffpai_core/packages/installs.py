"""Where each installed package's folder is, and how it got there: ``<state>/packages/installs.json``.

The Registry row says what a package is; this file says where its files are,
which the Registry row has no field for: ``packages\\<id>`` for a copy or a
git clone, or the owner's own folder for ``--link`` (never deleted on remove).
The Package Manager is the one writer; Steward and Caretaker read it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


#: How many times a read or a replace refused by Windows is tried, and the wait between tries.
_TRIES = 20
_WAIT_S = 0.05


class Installs:
    def __init__(self, state: Path | str) -> None:
        self.root = Path(state) / "packages"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "installs.json"
        self._lock = threading.Lock()

    def _read(self, strict: bool = False) -> dict:
        """The file's entries. *strict* (before a write): a file Windows keeps refusing raises, so a write
        never starts from an empty read and drops every other entry."""
        for attempt in range(_TRIES):
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except PermissionError:            # Windows: the file is being replaced this moment
                if strict and attempt == _TRIES - 1:
                    raise
                time.sleep(_WAIT_S)
            except (OSError, ValueError):
                return {}
        return {}

    def get(self, id: str) -> dict | None:
        return self._read().get(id)

    def all(self) -> dict:
        return self._read()

    def folder(self, id: str) -> Path | None:
        entry = self.get(id)
        return Path(entry["folder"]) if entry else None

    def set(self, id: str, folder: Path | str, link: bool, source: str) -> None:
        with self._lock:
            data = self._read(strict=True)
            data[id] = {"folder": str(folder), "link": bool(link), "source": str(source)}
            self._write(data)

    def remove(self, id: str) -> None:
        with self._lock:
            data = self._read(strict=True)
            if data.pop(id, None) is not None:
                self._write(data)

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        for attempt in range(_TRIES):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                # Windows: a reader (Steward, Caretaker, a Desktop poll) has the file open this moment, and
                # replacing an open file is refused. Lane I18c: this left an install half done (its folder in
                # packages, no record), so it waits a moment and tries again.
                if attempt == _TRIES - 1:
                    raise
                time.sleep(_WAIT_S)


__all__ = ["Installs"]
