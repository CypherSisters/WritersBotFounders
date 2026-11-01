r"""Installed skills reach the brain (WritersBot's lane, 11-01).

The package contract calls a ``skill`` "instructions the brain reads when needed" (``SKILL.md``), but nothing in the
Core gave that text to a chat turn, so an installed skill did nothing. This module picks the installed skills a turn
needs and returns their text for the chat's system message, ahead of the rules, which still come last and win.

When a skill is "needed": its name's words are in the owner's message ("novel mode"), or one of the phrases in its
description's "Use when ..." part is ("read my novel", "start novel mode"), or it was needed in one of the last
turns of this conversation, so a skill stays on while the owner keeps talking about the same thing. Only installed
skills count (Registry kind ``skill``, not removed), read from their installed folder, never from the add source.

The text is the owner's choice: she installed it with a Yes, and Drawbridge checked it then. It is capped so it fits a
small model's context beside the tools and the history, and control characters are taken out.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

#: Most characters of skill text per turn, all skills together (about 1,800 tokens on a small model).
MAX_CHARS = 7000
#: How many of the owner's recent messages keep a skill on once it was needed.
STICKY_TURNS = 6
HEAD = "A skill the owner installed and asked you to follow when it applies (it never overrides the rules below):"

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_WORD = re.compile(r"[a-z0-9']+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _front_matter(text: str) -> tuple[dict, str]:
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


def _phrases(description: str) -> list[str]:
    """The trigger phrases of a description: what follows "Use when", split on commas, "or" and quotes; two words or
    more, lowercased. The skill's own words only, never a guess."""
    d = str(description or "")
    i = d.lower().find("use when")
    tail = d[i + len("use when"):] if i >= 0 else ""
    parts = re.split(r"[,;]|\bor\b|\"|“|”|'(?=\s|$)|(?<=\s)'", tail)
    out = []
    for p in parts:
        words = _WORD.findall(p.lower())
        for lead in ("the owner says", "the owner asks", "they say", "they ask", "asked", "says", "asks", "about",
                     "the owner", "someone"):
            lw = lead.split()
            if words[:len(lw)] == lw:
                words = words[len(lw):]
        if len(words) >= 2:
            out.append(" ".join(words))
    return out


def _needed(name: str, description: str, texts: Iterable[str]) -> bool:
    name_words = " ".join(_WORD.findall(str(name or "").lower().replace("-", " ")))
    phrases = _phrases(description)
    for t in texts:
        low = " ".join(_WORD.findall(str(t or "").lower()))
        if not low:
            continue
        if name_words and name_words in low:
            return True
        if any(p in low for p in phrases):
            return True
    return False


def installed_skills(state: Path | str, rows: Iterable[dict], folder_of=None) -> list[tuple[str, Path]]:
    """``(id, SKILL.md path)`` for every installed skill that has its file. *folder_of(id)* names a linked install's
    folder when the Core has one; else the state's ``packages/<id>``."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("kind") != "skill" or r.get("status") == "removed":
            continue
        pid = str(r.get("id") or "")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", pid):
            continue
        folder = None
        if callable(folder_of):
            try:
                folder = folder_of(pid)
            except Exception:  # noqa: BLE001 - no folder known: the state's own
                folder = None
        path = Path(folder or (Path(state) / "packages" / pid)) / "SKILL.md"
        if path.is_file():
            out.append((pid, path))
    return out


def block(state: Path | str, rows: Iterable[dict], text: str, recent_owner_texts: Optional[Iterable[str]] = None,
          folder_of=None) -> str:
    """The skills this turn needs, as one text for the system message, or "" when none."""
    texts = [text] + list(recent_owner_texts or [])[-STICKY_TURNS:]
    pieces: list[str] = []
    left = MAX_CHARS
    for pid, path in installed_skills(state, rows, folder_of):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = _front_matter(raw)
        if not _needed(meta.get("name") or pid, meta.get("description") or "", texts):
            continue
        body = _CONTROL.sub("", body).strip()
        if len(body) > left:
            body = body[:max(0, left - 60)].rstrip() + "\n\n(The rest of this skill was cut to fit.)"
        left -= len(body)
        pieces.append(f"Skill: {meta.get('name') or pid}\n\n{body}")
        if left <= 200:
            break
    return (HEAD + "\n\n" + "\n\n".join(pieces)) if pieces else ""


__all__ = ["HEAD", "MAX_CHARS", "STICKY_TURNS", "block", "installed_skills"]
