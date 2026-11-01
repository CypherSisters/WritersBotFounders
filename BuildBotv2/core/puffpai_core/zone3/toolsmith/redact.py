"""Keys never leave the Core's send path (lane I15 fix).

A backstop under the strict key check in ``service``: every string the Core
hands back to Toolsmith from a send, and every string Toolsmith gives the Core
for the build log or the plan detail, goes through :func:`scrub`. It removes:

- the exact key that was lent for this send (and its pieces, so a key that
  holds a newline or a space is caught in an error's ``repr`` form too);
- any ``Bearer ...`` value;
- strings shaped like GitHub keys (``ghp_``, ``gho_``, ``ghu_``, ``ghs_``,
  ``ghr_``, ``github_pat_``).

Only the Core's own words replace them; the value is never logged.

It lives on Toolsmith's side (lane I18a) so Toolsmith's own log lines go
through the same scrub; ``toolsmith_host.redact`` is the Core's name for it.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

REMOVED = "[removed]"
#: ``Bearer`` and what follows, as far as a quote, a bracket or the end of the line.
_BEARER = re.compile(r"(?i)\bbearer\b[ \t]*[^\s'\"<>()\[\]{},;]*")
_TOKEN_SHAPES = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{6,}|github_pat_[A-Za-z0-9_]{6,})")
#: A key's pieces split on anything that is not a token character; pieces this long or longer are removed.
_PIECE_MIN = 6
_SPLIT = re.compile(r"[^A-Za-z0-9\-._~+/=]+")


def pieces(secrets: Iterable[Any]) -> list[str]:
    """The strings to remove for these secrets: each whole, in its escaped forms, and its long pieces,
    longest first."""
    out: set[str] = set()
    for s in secrets or ():
        s = str(s or "")
        if not s:
            continue
        whole = {s, s.encode("unicode_escape").decode("ascii"), repr(s)[1:-1],
                 repr(s.encode("utf-8", "backslashreplace"))[2:-1]}
        out.update(w for w in whole if w.strip())
        out.update(p for p in _SPLIT.split(s) if len(p) >= _PIECE_MIN)
    return sorted(out, key=len, reverse=True)


def scrub_text(text: str, cut: list[str] | None = None, bearer: bool = True) -> str:
    for p in cut or ():
        if p and p in text:
            text = text.replace(p, REMOVED)
    if bearer:
        text = _BEARER.sub("Bearer " + REMOVED, text)
    return _TOKEN_SHAPES.sub(REMOVED, text)


def scrub(value: Any, secrets: Iterable[Any] = (), bearer: bool = True) -> Any:
    """*value* (a string, or dicts and lists of them) with every key removed; other values as they are. *bearer* False:
    an answer from an API the owner added, whose words may say "Bearer" and whose own key is already out."""
    cut = pieces(secrets)

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return scrub_text(v, cut, bearer)
        if isinstance(v, dict):
            return {walk(k) if isinstance(k, str) else k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(walk(x) for x in v)
        return v
    return walk(value)


__all__ = ["REMOVED", "pieces", "scrub", "scrub_text"]
