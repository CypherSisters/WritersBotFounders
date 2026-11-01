"""The Registry: one SQLite file, one row per installed thing (01 "Registry").

Every consumer reads it: the brain, the Desktop, Drawbridge, Caretaker, the
wizard, Toolsmith and outside AIs. Rows follow ``contracts/registry.schema.json``.

Three rules, from HomeBot's capability registry (06 "Registry", rewritten-from):
1. One row per thing.
2. ``health`` is probed, never declared. A new row starts "not checked yet";
   only :meth:`Registry.set_health` (Caretaker's probe) changes it. ``add`` and
   ``update`` refuse a ``health`` value, and a manifest's ``health`` block (the
   probe to run) is never copied into the row.
3. :meth:`Registry.validate` fails when a row claims a tool its MCP server does
   not list.

The Core is the one writer. Each call opens its own connection, so the API's
threads never share one.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from ..paths import contracts_dir, state_dir

NOT_CHECKED = {"ok": False, "checked_at": None, "reason": "Not checked yet."}

_UI_KEYS = ("pages", "tray", "widgets", "open")
_NEEDS_KEYS = ("packages", "hosts", "install_hosts", "keys", "hardware", "brain", "owner_files", "addresses", "admin")


class RegistryError(ValueError):
    """A Registry write that was refused. The message is plain words."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_validator() -> Draft202012Validator:
    schema = json.loads((contracts_dir() / "registry.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def row_from_manifest(manifest: dict, origin: str, **extra: Any) -> dict:
    """A new Registry row for a validated ``puffpai.json``.

    ``abilities`` start empty: they come from the package's MCP ``list_tools``
    (02: the server is the source of truth), filled in by whoever reads it.
    ``extra`` may set ``status``, ``port``, ``data_dir``, ``built_with``,
    ``yes_record``. It may not set ``health``.
    """
    if "health" in extra:
        raise RegistryError("health is probed by Caretaker, never set from a manifest.")
    row: dict[str, Any] = {
        "id": manifest["id"],
        "kind": manifest["kind"],
        "name": manifest["name"],
        "version": manifest.get("version", ""),
        "description": manifest.get("description", ""),
        "origin": origin,
        "status": "installing",
        "health": dict(NOT_CHECKED),
        "abilities": [],
        "ui": {k: manifest["ui"][k] for k in _UI_KEYS if k in manifest.get("ui", {})},
        "needs": {k: manifest["needs"][k] for k in _NEEDS_KEYS if k in manifest.get("needs", {})},
        "port": None,
        "installed_at": _now(),
    }
    if "provides" in manifest:
        row["provides"] = [dict(p) for p in manifest["provides"]]
    if "reads" in manifest:
        row["reads"] = manifest["reads"]
    row.update(extra)
    return row


class Registry:
    """The Registry store at ``<state>/registry.db``."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else state_dir() / "registry.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._validator = _row_validator()
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS rows ("
                " id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,"
                " row TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    # -- checks -------------------------------------------------------------
    def _check(self, row: dict) -> None:
        errors = sorted(self._validator.iter_errors(row), key=lambda e: list(e.absolute_path))
        if errors:
            e = errors[0]
            where = ".".join(str(p) for p in e.absolute_path) or "the row"
            raise RegistryError(f"Registry row {row.get('id', '?')!r} is not valid at {where}: {e.message}")
        prefix = f"{row['id']}."
        for ability in row.get("abilities", []):
            if not ability["tool"].startswith(prefix):
                raise RegistryError(
                    f"Registry row {row['id']!r} lists tool {ability['tool']!r}, "
                    f"which does not start with {prefix!r}."
                )

    def _write(self, db: sqlite3.Connection, row: dict) -> None:
        db.execute(
            "INSERT OR REPLACE INTO rows (id, kind, status, row, updated_at) VALUES (?, ?, ?, ?, ?)",
            (row["id"], row["kind"], row["status"], json.dumps(row, sort_keys=True), _now()),
        )

    # -- reads --------------------------------------------------------------
    def get(self, id: str) -> dict | None:
        with closing(self._connect()) as db:
            hit = db.execute("SELECT row FROM rows WHERE id = ?", (id,)).fetchone()
        return json.loads(hit[0]) if hit else None

    def list(self, kind: str | None = None, status: str | None = None) -> list[dict]:
        sql, args = "SELECT row FROM rows", []
        where = []
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if status:
            where.append("status = ?")
            args.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id"
        with closing(self._connect()) as db:
            return [json.loads(r[0]) for r in db.execute(sql, args).fetchall()]

    # -- writes -------------------------------------------------------------
    def add(self, row: dict) -> dict:
        """Add a new row. Its health always starts "not checked yet"."""
        row = dict(row)
        if row.get("health") not in (None, NOT_CHECKED):
            raise RegistryError("health is probed by Caretaker, never set when a row is added.")
        row["health"] = dict(NOT_CHECKED)
        self._check(row)
        with closing(self._connect()) as db, db:
            if db.execute("SELECT 1 FROM rows WHERE id = ?", (row["id"],)).fetchone():
                raise RegistryError(f"A package with id {row['id']!r} is already installed.")
            self._write(db, row)
        return row

    def update(self, id: str, /, **fields: Any) -> dict:
        """Change fields of an existing row (not ``id``, not ``health``)."""
        if "id" in fields:
            raise RegistryError("A row's id never changes. Remove it and add it again.")
        if "health" in fields:
            raise RegistryError("health is probed by Caretaker; use set_health from the probe.")
        with closing(self._connect()) as db, db:
            hit = db.execute("SELECT row FROM rows WHERE id = ?", (id,)).fetchone()
            if not hit:
                raise RegistryError(f"No package with id {id!r}.")
            row = json.loads(hit[0])
            row.update(fields)
            self._check(row)
            self._write(db, row)
        return row

    def set_health(self, id: str, ok: bool, reason: str, checked_at: str | None = None) -> dict:
        """Record a probe result. Only Caretaker's health probe calls this."""
        with closing(self._connect()) as db, db:
            hit = db.execute("SELECT row FROM rows WHERE id = ?", (id,)).fetchone()
            if not hit:
                raise RegistryError(f"No package with id {id!r}.")
            row = json.loads(hit[0])
            row["health"] = {"ok": bool(ok), "checked_at": checked_at or _now(), "reason": str(reason)}
            self._check(row)
            self._write(db, row)
        return row

    def remove(self, id: str) -> bool:
        """Delete the row. True if there was one."""
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM rows WHERE id = ?", (id,)).rowcount > 0

    # -- honesty ------------------------------------------------------------
    def validate(self, live_tool_names: Iterable[str]) -> list[dict]:
        """Every tool a row claims that its MCP server does not list.

        ``live_tool_names`` holds ``<pkg>.<tool>`` names from the live
        ``list_tools`` answers. Returns ``[{"id", "tool", "problem"}]``;
        an empty list means the Registry is honest.
        """
        live = set(live_tool_names)
        problems = []
        for row in self.list():
            for ability in row.get("abilities", []):
                if ability["tool"] not in live:
                    problems.append({
                        "id": row["id"],
                        "tool": ability["tool"],
                        "problem": f"{row['name']} claims {ability['tool']}, but its MCP server does not list it.",
                    })
        return problems
