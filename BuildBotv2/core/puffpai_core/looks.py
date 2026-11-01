r"""Looks from packages (lane looks, D76): a ``look`` package is values only.

A look is one ``puffpai.json`` with ``"kind": "look"`` and a ``look`` block::

    "look": {"mode": "both", "line": "...", "tokens": {"--bg": "#0E1116", ...},
             "day_tokens": {...}, "preview": "preview.svg"}

No CSS file, no script, no font file: only CSS variable values the Desktop already reads
(``packages/desktop/web/desktop.css``). A value may use colours, numbers, lengths, quoted font names
and a few plain CSS functions (:data:`FUNCTIONS`); nothing that loads anything (no ``url()``, no
``image()``, no ``@``). The Desktop checks the same rule again before it sets a value
(``logic.js`` ``themeTokens``).

An installed look shows on Looks next to the built-in ones (``bot.THEMES``); removing it takes it off,
and a bot whose look was removed reads as the default again (``bot.read``). Nothing here knows any one
look: they are found by kind in the Registry, read through the Core's one manifest reader (the installed
file her Yes covered, by its sha256; a changed file marks the row broken and the look is not offered).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

KIND = "look"
MODES = ("night", "day", "both")
MAX_TOKENS = 80
MAX_VALUE = 120
NAME = re.compile(r"^--[A-Za-z0-9-]{1,40}$")
#: Characters a value may have: letters, digits, space, # % . , ( ) " ' + * / - _
CHARS = re.compile(r"^[A-Za-z0-9 #%.,()\"'+*/_-]+$")
#: The only CSS functions a value may call. None of them loads anything.
FUNCTIONS = frozenset({"rgb", "rgba", "hsl", "hsla", "color-mix", "linear-gradient", "radial-gradient",
                       "calc", "var", "min", "max", "clamp"})
_CALL = re.compile(r"([A-Za-z-]*)\s*\(")
#: Statuses that are not a working install (the look is not offered then).
_NOT_IN_USE = ("removed", "broken", "installing")


def value_problem(value: Any) -> str | None:
    """Why *value* can't be a look's token value, or None when it can."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "a value must be text or a number"
    s = str(value).strip()
    if not s or len(s) > MAX_VALUE:
        return f"a value must be 1 to {MAX_VALUE} characters"
    if not CHARS.match(s):
        return "a value may only have letters, digits, spaces and # % . , ( ) \" ' + * / - _"
    for name in _CALL.findall(s):
        if name.lower() not in FUNCTIONS:
            return f"{name or 'a bare ('}() is not allowed; only {', '.join(sorted(FUNCTIONS))}"
    return None


def token_problems(tokens: Any, where: str) -> list[str]:
    """Plain problems with a look's token map; empty when every name and value is safe."""
    if not isinstance(tokens, dict):
        return [f"{where} must be an object of CSS variables, like {{\"--bg\": \"#ffffff\"}}."]
    out = []
    if len(tokens) > MAX_TOKENS:
        out.append(f"{where} has more than {MAX_TOKENS} values.")
    for k, v in tokens.items():
        if not NAME.match(str(k)):
            out.append(f"{where}: {k!r} is not a CSS variable name like --bg.")
            continue
        why = value_problem(v)
        if why:
            out.append(f"{where}.{k}: {why}.")
    return out


def manifest_problems(manifest: dict) -> list[str]:
    """The look block's values (the schema checks its shape): every token name and value is safe."""
    block = manifest.get("look") if isinstance(manifest, dict) else None
    if not isinstance(block, dict):
        return []
    out = token_problems(block.get("tokens"), "look.tokens")
    if "day_tokens" in block:
        out += token_problems(block.get("day_tokens"), "look.day_tokens")
    return out


def safe_tokens(tokens: Any) -> dict:
    """Only the safe pairs of *tokens* (a reader never trusts a file it did not just check)."""
    if not isinstance(tokens, dict):
        return {}
    out = {}
    for k, v in list(tokens.items())[:MAX_TOKENS]:
        if NAME.match(str(k)) and value_problem(v) is None:
            out[str(k)] = str(v).strip()
    return out


def installed(state: Path | str | None, registry: Any, taken: set[str] | frozenset = frozenset()) -> list[dict]:
    """Every installed look as a theme row ``{id, name, mode, line, tokens, day_tokens?, package}``, by name.
    An id in *taken* (a built-in theme's) is skipped, so a package can never stand in for one."""
    if state is None or registry is None:
        return []
    from .desktop import _installed
    out = []
    for row in registry.list(kind=KIND):
        if row.get("status") in _NOT_IN_USE or row.get("id") in taken:
            continue
        got = _installed(Path(state), registry, row["id"])   # only the file her Yes covered, never a fallback
        data = got[1] if got is not None else {}
        block = data.get("look") if data.get("kind") == KIND else None
        if not isinstance(block, dict):
            continue
        mode = block.get("mode") if block.get("mode") in MODES else "both"
        theme = {"id": row["id"], "name": str(data.get("name") or row["id"])[:40], "mode": mode,
                 "line": str(block.get("line") or "")[:200], "tokens": safe_tokens(block.get("tokens")),
                 "package": row["id"]}
        if mode == "both":
            theme["day_tokens"] = safe_tokens(block.get("day_tokens"))
        if isinstance(block.get("preview"), str):
            theme["preview"] = f"/pkg/{row['id']}/{block['preview']}"
        out.append(theme)
    return sorted(out, key=lambda t: t["name"].lower())
