"""Her account name becomes "you" in what goes to a model, and back again in paths (01 "Toolsmith's zone").
Also: text cut at a whole word, and short names that never end on "and" (lane I19e).

Used by the Core's sender (``scrub_name``, before anything leaves) and by
Toolsmith's tools (``unscrub_path``, on the paths a model copies back).
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def account_names() -> list[str]:
    names = {os.environ.get("USERNAME", ""), os.environ.get("USER", ""), Path.home().name}
    return sorted((n for n in names if n and len(n) >= 3), key=len, reverse=True)


def scrub_name(text: str) -> str:
    """Her account name becomes "you"."""
    for name in account_names():
        text = re.sub(rf"(?i)(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", "you", text)
    return text


def unscrub_path(text: str) -> str:
    """Paths the model copies back get her real folder again."""
    names = account_names()
    if not names:
        return text
    return re.sub(r"(?i)([\\/]Users[\\/])you(?=[\\/])", lambda m: m.group(1) + names[0], text)


#: Words a short name never ends on (lane I19e: "2010 Toyota Camry battery change and").
DANGLING = frozenset("a an and as at but by for from in into nor of on or so than that the then to with".split())


def cut_text(text: str, limit: int, more: str = "") -> str:
    """*text* in at most *limit* characters, cut at the last sentence end, else the last whole word (lane I19e:
    a give-up reason ended "Gradle/Maven depen."). Nothing is added, except *more* when it was cut, within the
    limit, in place of a sentence's own last mark (D104: the plan card's "...")."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    limit -= len(more)
    part = text[:limit + 1]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", part) if m.end() <= limit]
    if ends and ends[-1] >= limit // 3:
        got = part[:ends[-1]]
    else:
        cut = part.rfind(" ")
        got = (part[:cut] if cut > 0 else text[:limit]).rstrip(" ,;:-")
    return got.rstrip(".!?") + more if more else got


def short_name(text: str, words: int = 6, limit: int = 80) -> str:
    """The first *words* words of *text* as a name, never ending on a small word like "and" or "the"."""
    got = cut_text(" ".join(str(text or "").split()[:words]), limit).split()
    while len(got) > 1 and got[-1].lower().strip(",.;:") in DANGLING:
        got.pop()
    return " ".join(got).rstrip(" ,;:-")


#: Where a job's subject ends in her sentence (D85; the same cut as ``interpret.name_of``, plus "with"):
#: "drawing tutorial tool that gives step-by-step guides" -> "drawing tutorial tool".
_SUBJECT_END = re.compile(r"\s+(?:i|we|you|that|which|so|to|where|who|for|with)\s+", re.I)
#: A head that says nothing on its own: the words after the cut name the job instead ("something to keep
#: recipes" -> "keep recipes").
_VAGUE = frozenset("app apps tool tools program programs something thing things one way bot helper assistant".split())


def subject_name(text: str, words: int = 6, limit: int = 80) -> str:
    """A job's name from her words: the phrase before "that", "which", "for", "with"... (D85), no leading
    "a", "an", "the" or "some" ("my shopping list" stays hers), never cut mid-phrase, never ending on a small
    word."""
    rest = " ".join(str(text or "").split())
    for _ in range(3):
        parts = _SUBJECT_END.split(rest, maxsplit=1)
        head = re.sub(r"(?i)^(?:(?:a|an|the|some|me)\s+)+", "", parts[0].strip(" ,.;:!?"))
        if len(parts) == 2 and all(w.lower().strip(",.;:") in _VAGUE for w in head.split()):
            rest = parts[1]                        # "something" said nothing: the words after it name it
            continue
        return short_name(head or rest, words, limit)
    return short_name(rest, words, limit)


__all__ = ["DANGLING", "account_names", "cut_text", "scrub_name", "short_name", "subject_name", "unscrub_path"]
