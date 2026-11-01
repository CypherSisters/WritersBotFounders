# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/dashboard_widgets.py (adapted)
r"""How the owner arranged her Desktop's Home page: one plain JSON file (06 "Desktop layout").

``<state>\data\desktop\layout.json``. Shape (every key optional; a missing or broken file means
the defaults)::

    {"version": 1,
     "tiles": {"order": [tile id, ...], "hidden": [tile id, ...], "pinned": [tile id, ...],
               "names": {tile id: "her name for it"}},
     "seen":  {"tiles": {tile id: name}},
     "apps":  {"view": "tiles" | "pages", "pages": [package id, ...]},
     "updated_at": "...", "updated_by": "owner" | "brain"}

Tile ids are the Desktop's own: its core tiles (``apps``, ``building``, ``your-turn``, and ``bridge`` and
``brain``, hidden until she shows them: the top bar says both), ``<package id>:<widget id>`` for each package widget, and
``app/<package id>`` for an app whose own page she put on Home (D82). ``apps.view`` "pages" gives every
app a page tile; "tiles" (the default) only the ones in ``apps.pages``. ``seen`` is what
the page last showed, written by the page, so :func:`arrange` can find a tile by the name she
says. Nothing here knows any one package.

v1 source: ``get_layout`` :754, ``save_layout`` :776, ``arrange`` :842. What changed: one
section (``tiles``; the v1 ``sidebar`` and ``sizes`` stay behind), the path is the bot's own
data folder, and the core tile names are v2's.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

LAYOUT_VERSION = 1
#: Always offered on Home (her names for them).
CORE_TILES: dict[str, str] = {"apps": "Your apps", "building": "Building now",
                              "your-turn": "Your turn", "bridge": "The Bridge", "brain": "The brain"}
#: Hidden on a new bot: the top bar says both already. Arrange or the chat shows them.
HIDDEN_AT_FIRST = ["bridge", "brain"]
APPS_VIEWS = ("tiles", "pages")
APP_TILE = "app/"
_PACKAGE_ID = re.compile(r"^[a-z][a-z0-9-]{1,39}\Z")
LAYOUT_ACTIONS = ("pin", "unpin", "hide", "show", "move", "rename")
BAD_POSITION = "Say where it goes: first, last, or a number from 1 to {n}."
_MAX_IDS = 200
_MAX_ID = 160
_MAX_NAME = 40
_SKIP_WORDS = frozenset("the my a an tile card page item home on of".split())
_lock = threading.Lock()


def path_for(state: Path) -> Path:
    return Path(state) / "data" / "desktop" / "layout.json"


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def default_layout() -> dict:
    return {"version": LAYOUT_VERSION,
            "tiles": {"order": list(CORE_TILES), "hidden": list(HIDDEN_AT_FIRST), "pinned": [], "names": {}},
            "seen": {"tiles": {}}, "apps": {"view": "tiles", "pages": []}}


def _ids(value: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    # Only the first few hundred entries are looked at: a long list can't hold the lock for minutes.
    for v in value[:_MAX_IDS * 5] if isinstance(value, list) else []:
        s = str(v).strip()[:_MAX_ID] if isinstance(v, str) or (isinstance(v, int) and not isinstance(v, bool)
                                                              and abs(v) < 10 ** 12) else ""
        if s and s not in seen:
            seen.add(s)
            out.append(s)
            if len(out) >= _MAX_IDS:
                break
    return out


def _names(value: Any, size: int = _MAX_NAME) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (value.items() if isinstance(value, dict) else []):
        k, v = str(k).strip()[:_MAX_ID], " ".join(str(v or "").split())[:size]
        if k and v:
            out[k] = v
        if len(out) >= _MAX_IDS:
            break
    return out


def _section(value: Any, base: dict) -> dict:
    value = value if isinstance(value, dict) else {}
    out: dict = {k: _ids(value.get(k, base.get(k))) for k in ("order", "hidden", "pinned")}
    out["names"] = _names(value.get("names", base.get("names")))
    return out


def _normal(data: Any) -> dict:
    data = data if isinstance(data, dict) else {}
    base = default_layout()
    seen = data.get("seen") if isinstance(data.get("seen"), dict) else {}
    apps = data.get("apps") if isinstance(data.get("apps"), dict) else {}
    out: dict = {"version": LAYOUT_VERSION, "tiles": _section(data.get("tiles"), base["tiles"]),
                 "seen": {"tiles": _names(seen.get("tiles"), 80)},
                 "apps": {"view": apps.get("view") if apps.get("view") in APPS_VIEWS else "tiles",
                          "pages": [i for i in _ids(apps.get("pages")) if _PACKAGE_ID.match(i)]}}
    order = out["tiles"]["order"]
    if "apps" not in data and isinstance(data.get("tiles"), dict) and "apps" not in order + out["tiles"]["hidden"]:
        order.insert(0, "apps")             # a layout saved before D82: Your apps comes first, once
    for k in ("updated_at", "updated_by"):
        if isinstance(data.get(k), str):
            out[k] = data[k][:40]
    return out


def get_layout(state: Path) -> dict:
    """The saved layout, or the defaults when there is none (or it is broken)."""
    try:
        return _normal(json.loads(path_for(state).read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeDecodeError):
        return _normal({})


def _write_layout(state: Path, layout: dict, by: str) -> dict:
    layout = _normal(layout)
    layout["updated_at"] = _utcnow()
    layout["updated_by"] = "brain" if by == "brain" else "owner"
    path = path_for(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(layout, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return layout


def save_layout(state: Path, patch: Any, by: str = "owner") -> dict:
    """``PUT /v1/settings/layout``: each top-level section given (``tiles``, ``seen``, ``apps``) replaces
    the saved one; the rest stays."""
    patch = patch if isinstance(patch, dict) else {}
    with _lock:
        layout = get_layout(state)
        for key in ("tiles", "seen", "apps"):
            if isinstance(patch.get(key), dict):
                layout[key] = patch[key]
        return _write_layout(state, layout, by)


def _norm_words(text: Any) -> str:
    return " ".join(w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in _SKIP_WORDS)


def _candidates(layout: dict) -> dict[str, str]:
    """``{id: name}`` she would recognize on Home (her names win)."""
    known = {**CORE_TILES, **layout["seen"]["tiles"]}
    known.update({k: v for k, v in layout["tiles"]["names"].items() if k in known})
    return known


def find_tile(layout: dict, ref: Any) -> tuple[str, str]:
    """``(id, name)`` for what she called *ref*, or ``("", "")``."""
    want = _norm_words(ref)
    raw = str(ref or "").strip()
    if not want and not raw:
        return "", ""
    known = _candidates(layout)
    if raw in known:
        return raw, known[raw]
    base = {**CORE_TILES, **layout["seen"]["tiles"]}
    rows = [(i, n, {_norm_words(n), _norm_words(i), _norm_words(base.get(i, ""))}) for i, n in known.items()]
    tests = (lambda names: want in names,
             lambda names: bool(want) and any(set(want.split()) <= set(x.split()) for x in names if x),
             lambda names: any(x and set(x.split()) <= set(want.split()) for x in names))
    for test in tests:
        hits = [(i, n) for i, n, names in rows if test(names)]
        if hits:
            return min(hits, key=lambda h: len(h[1]))
    return "", ""


def core_tile(ref: Any) -> str:
    """The core tile *ref* names exactly (its id, or its name with or without "the"), or ""."""
    want = _norm_words(ref)
    return next((i for i, n in CORE_TILES.items() if want and want in (_norm_words(i), _norm_words(n))), "")


def position_index(position: Any, n: int) -> int | None:
    """Where *position* (first, last or a number from 1) puts a tile among *n* others, or None."""
    pos = str(position if position is not None else "first").strip().lower()
    if pos in ("first", "top", "start", "1"):
        return 0
    if pos in ("last", "bottom", "end"):
        return n
    if re.fullmatch(r"[0-9]{1,6}", pos) and int(pos) >= 1:     # "00" is 0: refused
        return max(0, min(n, int(pos) - 1))                     # past the end: last
    return None                                  # 0, "inf", "1e400", "nan", words: a plain no, never a 500


def _full_order(layout: dict) -> list[str]:
    order = list(layout["tiles"]["order"])
    for i in list(CORE_TILES) + list(layout["seen"]["tiles"]):
        if i not in order:
            order.append(i)
    return order


def arrange(state: Path, action: Any, ref: Any, position: Any = None, name: Any = "", by: str = "owner",
            label: str = "") -> dict:
    """One change to Home, saved to the layout file.

    *action*: pin, unpin, hide, show, move (*position*: ``first``, ``last`` or a number from 1),
    rename (*name*; empty gives the own name back). *label* with an ``app/<id>`` *ref*: that app tile by
    its id, even before Home has drawn it (D82). Returns ``{ok, line, id, name}``."""
    action = str(action or "").strip().lower()
    if action not in LAYOUT_ACTIONS:
        return {"ok": False, "id": "", "name": "", "line": "I can pin, unpin, hide, show, move or rename a tile."}
    with _lock:
        layout = get_layout(state)
        raw = str(ref or "")
        if label and raw.startswith(APP_TILE) and _PACKAGE_ID.match(raw[len(APP_TILE):]):
            tid = raw
        else:
            tid, label = find_tile(layout, ref)
        if not tid:
            names = sorted(set(_candidates(layout).values()))[:20]
            return {"ok": False, "id": "", "name": str(ref or ""),
                    "line": f"I could not find \"{ref}\" on your Home page. On Home: {', '.join(names)}."}
        sec = layout["tiles"]
        new_name = " ".join(str(name or "").split())[:_MAX_NAME]
        if action == "hide":
            sec["hidden"] = [*[h for h in sec["hidden"] if h != tid], tid]
            sec["pinned"] = [p for p in sec["pinned"] if p != tid]
            line = f"{label} is hidden from your Home page. Show it again from Arrange."
        elif action == "show":
            sec["hidden"] = [h for h in sec["hidden"] if h != tid]
            line = f"{label} is back on your Home page."
        elif action in ("pin", "unpin"):
            sec["pinned"] = [p for p in sec["pinned"] if p != tid]
            if action == "pin":
                sec["pinned"].insert(0, tid)
                sec["hidden"] = [h for h in sec["hidden"] if h != tid]
                line = f"{label} is pinned to the top of your Home page."
            else:
                line = f"{label} is not pinned any more."
        elif action == "move":
            order = [i for i in _full_order(layout) if i != tid]
            at = position_index(position, len(order))
            if at is None:
                return {"ok": False, "id": tid, "name": label, "line": BAD_POSITION.format(n=len(order) + 1)}
            order.insert(at, tid)
            sec["order"] = order
            sec["hidden"] = [h for h in sec["hidden"] if h != tid]
            # Pinned tiles lead; "first" means before them too.
            sec["pinned"] = [p for p in sec["pinned"] if p != tid]
            if at == 0 and sec["pinned"]:
                sec["pinned"].insert(0, tid)
            line = (f"{label} is first on your Home page now." if at == 0
                    else f"{label} is last on your Home page now." if at == len(order) - 1
                    else f"{label} is number {at + 1} on your Home page now.")
        else:  # rename
            if new_name:
                sec["names"][tid] = new_name
                line = f"{label} is called {new_name} on your Home page now."
            else:
                sec["names"].pop(tid, None)
                line = f"{label} has its own name back."
        saved = _write_layout(state, layout, by)
    return {"ok": True, "line": line, "id": tid, "name": new_name or label, "layout": saved}


def app_page(state: Path, pid: Any, name: Any, on: bool, by: str = "owner", inside: bool = True) -> dict:
    """Put an app's own tile on Home (*on*), or take it off (D82). *pid* is an installed package id the caller
    found; *name* is how she knows it; *inside*: its page shows in the tile (built for PuffPAI), else the tile
    has Open, a new tab (an outside app, D83). Returns ``{ok, line, id, name}``."""
    pid, name = str(pid or "").strip(), " ".join(str(name or pid or "").split())[:80]
    if not _PACKAGE_ID.match(pid):
        return {"ok": False, "id": "", "name": name, "line": "Say which app, by its name."}
    tid = APP_TILE + pid
    with _lock:
        layout = get_layout(state)
        apps, sec = layout["apps"], layout["tiles"]
        apps["pages"] = [p for p in apps["pages"] if p != pid]
        if on:
            apps["pages"].insert(0, pid)
            sec["hidden"] = [h for h in sec["hidden"] if h != tid]
            # It goes first, so she sees it (pinned tiles still lead).
            sec["order"] = [tid] + [i for i in _full_order(layout) if i != tid]
            layout["seen"]["tiles"][tid] = name
            line = (f"{name} is on your Home page now, showing its own page. It is in Your apps too." if inside else
                    f"{name} is on your Home page now. It opens in your browser, in a new tab, from its tile.")
        else:
            sec["pinned"] = [p for p in sec["pinned"] if p != tid]
            if apps["view"] == "pages":        # every app shows as a page: this one is hidden instead
                sec["hidden"] = [*[h for h in sec["hidden"] if h != tid], tid]
            line = f"{name}'s tile is off your Home page. It is still in Your apps."
        saved = _write_layout(state, layout, by)
    return {"ok": True, "line": line, "id": tid, "name": name, "layout": saved}


def apps_view(state: Path, view: Any, by: str = "owner") -> dict:
    """Show every app on Home as its own page (``pages``) or as small tiles in Your apps (``tiles``)."""
    view = str(view or "").strip().lower()
    if view not in APPS_VIEWS:
        return {"ok": False, "line": "Apps on Home can be tiles or pages."}
    with _lock:
        layout = get_layout(state)
        layout["apps"]["view"] = view
        saved = _write_layout(state, layout, by)
    line = ("Every app has its own tile on your Home page now. Apps built for PuffPAI show their page in it; outside "
            "apps open in your browser, in a new tab." if view == "pages"
            else "Apps show as small tiles in Your apps now. Apps you put on Home one by one stay there.")
    return {"ok": True, "line": line, "layout": saved}


__all__ = ["APPS_VIEWS", "APP_TILE", "CORE_TILES", "LAYOUT_ACTIONS", "app_page", "apps_view", "arrange",
           "core_tile", "default_layout", "find_tile", "get_layout", "path_for", "position_index", "save_layout"]
