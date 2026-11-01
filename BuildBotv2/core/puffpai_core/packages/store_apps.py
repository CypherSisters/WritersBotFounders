"""The PuffPAI store as this computer has it: the packages that ship with PuffPAI and are not installed yet (lane
I8b's ``GET /v1/packages/available``; lane I10c gave it one home, so the plan's store suggestions read the same
list), and for those suggestions the saved copy of the official store's index (D96).

Nothing here fetches anything or runs anything. A built-in app's tools come from its manifest's
``abilities.tools`` (tool name to its plain description) and ``abilities.examples``: the app is not running
yet, so there is no MCP server to ask. An index row's come from the row (``store_index.clean_abilities``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The fields of each row of ``GET /v1/packages/available`` (each row also has ``icon``, lane I10a).
ROW_KEYS = ("id", "name", "kind", "description", "version", "author")


def _manifests(registry: Any) -> list[dict]:
    return [m for _folder, m in _folders(registry)]


def _folders(registry: Any) -> list[tuple[Any, dict]]:
    from ..desktop import builtin_dir
    from .manager import builtin_source
    base, out = builtin_dir(), []
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        if registry.get(folder.name) is not None or builtin_source(folder.name) is None:
            continue
        try:
            m = json.loads((folder / "puffpai.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("id") == folder.name:
            out.append((folder, m))
    return out


def available(registry: Any) -> list[dict]:
    """Every built-in package not installed: ``[{id, name, kind, description, version, author, icon}]``,
    ``icon`` a data: URL of the package's picture or None (lane I10a)."""
    from ..api.groups.packages import _icon_data
    return [{**{k: m.get(k) for k in ROW_KEYS}, "icon": _icon_data(folder, m.get("icon"))}
            for folder, m in _folders(registry)]


def _indexed(store: Any, registry: Any) -> list[dict]:
    """The official store's rows a plan may suggest (D96), from its saved copy, never the network: never one that
    ships built in (the built-in row wins), and no other source's. ``package`` is what adds it: ``store:<id>``."""
    from .manager import builtin_source
    out = []
    for r in store.suggestions():
        if builtin_source(r["id"]) is not None or registry.get(r["id"]) is not None:
            continue
        ab = r["abilities"]
        out.append({"id": r["id"], "name": str(r.get("name") or r["id"])[:80], "kind": r["kind"],
                    "description": str(r.get("description") or "")[:300], "package": f"store:{r['id']}",
                    "abilities": [{"tool": f"{r['id']}.{t}", "description": d} for t, d in ab["tools"].items()],
                    "examples": dict(ab.get("examples") or {})})
    return out


def store_apps(registry: Any, store: Any = None) -> list[dict]:
    """The apps a plan may suggest, each with what its tools do:
    ``[{id, name, kind, description, abilities: [{tool: "<id>.<tool>", description}], examples}]``: the built-in
    apps (kind ``app``, the same list as ``available``), then, with *store* (``store_index.Store``), the official
    store's verified rows from its saved copy (D96), each also with ``package``: ``store:<id>``."""
    out = _built_in_apps(registry)
    if store is not None:
        try:
            out += _indexed(store, registry)
        except Exception:  # noqa: BLE001 - a saved copy that can't be read: the built-in apps still list
            logger.exception("the saved store index could not be read")
    return out


def _built_in_apps(registry: Any) -> list[dict]:
    out = []
    for m in _manifests(registry):
        if m.get("kind") != "app":
            continue
        ab = m.get("abilities") if isinstance(m.get("abilities"), dict) else {}
        told = ab.get("tools") if isinstance(ab.get("tools"), dict) else {}
        examples = ab.get("examples") if isinstance(ab.get("examples"), dict) else {}
        names = list(dict.fromkeys([str(t) for t in told] + [str(t) for t in examples]))
        out.append({"id": m["id"], "name": str(m.get("name") or m["id"])[:80], "kind": "app",
                    "description": str(m.get("description") or "")[:300],
                    "abilities": [{"tool": f"{m['id']}.{t}", "description": str(told.get(t) or "")[:600]}
                                  for t in names],
                    "examples": {str(t): [str(x) for x in v][:3] for t, v in examples.items() if isinstance(v, list)}})
    return out
