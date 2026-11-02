"""One book as it lies in the shared folder, ``My files/Manuscripts/<title>/`` (Manuscript's CONTRACT.md), and the one
place section text is ever written: :func:`save_section`, for the writer's own hand only, keeping the old version.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from brainline import read_json, sha_of, write_atomic, write_json

SHELF = "Manuscripts"
INDEX = "manuscript.json"
BIBLE = "bible.json"
STATUS = "status.json"
CACHE = "cache"
DESK_OLD = "desk.json"
SHARED = "editors-desk.json"     # the one file of this app in the book's folder: what the desk is doing, for a sibling to see
STAMP = re.compile(r"^\d{8}-\d{6}-\d{3}(?:-use|-restored)?$")
BY_MARK = {"you": "", "restored": "-restored", "Use this": "-use"}
KEEP_RECENT = 20                 # the last saves, all of them
KEEP_DAILY = 30                  # then the newest of each day for this many days, then the newest of each month
KEEP_MOST = 60                   # and never more than this for one section
TEXT_MAX = 1_500_000
HEAD = re.compile(r"\A# [^\n]*\n*")


PRIVATE = re.compile(r"\{\{vault:[^}]*\}\}")    # a handle the vault left in a bible read before the vault switch
KEPT = "[something kept private]"


def scrub(text: str) -> str:
    return PRIVATE.sub(KEPT, text or "")


class Refused(Exception):
    """A plain line for the writer: nothing was changed."""


# An e-book's own wrapping, which is not the story: a Project Gutenberg header and footer (Manuscript keeps it, as section 0
# "Before the first chapter" and at the end of the last section), and lines about the edition. Never searched for problems.
GUTENBERG_START = re.compile(r"\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG[^\n]*", re.I)
GUTENBERG_END = re.compile(r"\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG", re.I)
MATTER = re.compile(r"project gutenberg|gutenberg\.org|most recently updated|\bupdated\s*:|release date|\bproduced by\b|"
                    r"\btranscribed by\b|this e-?book is for the use|\bcopyright\b|all rights reserved|\bisbn\b|"
                    r"^\s*(?:title|author|language|credits|character set encoding|e-?text|edition)\s*:", re.I | re.M)
MATTER_WORDS = 80                # a paragraph about the edition is short; a story paragraph that says "copyright" is not
PARAGRAPH = re.compile(r"[^\n]+(?:\n(?![ \t]*\n)[^\n]+)*")


class Section:
    __slots__ = ("n", "heading", "file", "head", "text", "sha", "words", "story")

    def __init__(self, n: int, heading: str, file: str, head: str, text: str, sha: str) -> None:
        self.n, self.heading, self.file, self.head, self.text, self.sha = n, heading, file, head, text, sha
        self.words = len(re.findall(r"\S+", text))
        self.story = text              # the text with front matter blanked, same length, so a place in one is a place in the other


def _blank(chars: list[str], a: int, b: int) -> None:
    for i in range(a, min(b, len(chars))):
        if chars[i] != "\n":
            chars[i] = " "


def mark_matter(sections: list[Section]) -> None:
    """Blank what is the e-book's wrapping and not the story: everything before a Gutenberg START line and from its END line
    on, and any short paragraph about the edition, its licence, dates of release or update."""
    start = next(((i, m.end()) for i, s in enumerate(sections) for m in [GUTENBERG_START.search(s.text)] if m), None)
    end = next(((i, m.start()) for i, s in enumerate(sections) for m in [GUTENBERG_END.search(s.text)] if m), None)
    for i, s in enumerate(sections):
        chars = list(s.text)
        if start and i <= start[0]:
            _blank(chars, 0, len(chars) if i < start[0] else start[1])
        if end and i >= end[0]:
            _blank(chars, 0 if i > end[0] else end[1], len(chars))
        for m in PARAGRAPH.finditer(s.text):
            if len(m.group().split()) <= MATTER_WORDS and MATTER.search(m.group()):
                _blank(chars, m.start(), m.end())
        s.story = "".join(chars)


class Book:
    """Read fresh for each step of a check, so a save in the middle is seen at once."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.index = read_index(folder)
        self.title = str(self.index.get("title") or folder.name)
        self.sections: list[Section] = []
        for c in self.index.get("sections") or []:
            try:
                raw = section_path(folder, self.index, c).read_bytes()
            except (OSError, Refused):
                continue
            text = raw.decode("utf-8", errors="replace")
            m = HEAD.match(text)
            head = m.group() if m else ""
            self.sections.append(Section(int(c.get("n") or 0), str(c.get("heading") or ""), str(c.get("file")), head,
                                         text[len(head):], sha_of(raw)))
        mark_matter(self.sections)
        got = read_json(folder / BIBLE, {})
        self.bible: dict = got if isinstance(got, dict) else {}
        self.private = PRIVATE.search(json.dumps(self.bible, ensure_ascii=False)) is not None if self.bible else False

    def section(self, n: Any) -> Section | None:
        return next((s for s in self.sections if str(s.n) == str(n).strip()), None)

    def list_of(self, key: str) -> list[dict]:
        v = self.bible.get(key)
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    def unread(self) -> list[int]:
        """Sections changed since the book was last read: no cache entry for the text as it is now. Going back to an
        old version finds its old entry, so the notice clears by itself."""
        if not self.bible or not (self.folder / CACHE).is_dir():
            return []
        return [s.n for s in self.sections if not (self.folder / CACHE / f"{s.sha}.json").exists()]


def read_index(folder: Path) -> dict:
    got = read_json(folder / INDEX, {})
    if not isinstance(got, dict):
        return {}
    secs = got.get("sections") if isinstance(got.get("sections"), list) else got.get("chapters")
    if not isinstance(secs, list):
        return {}
    got["_dir"] = "sections" if isinstance(got.get("sections"), list) else "chapters"      # 0.1.0's folder
    got["sections"] = [c for c in secs if isinstance(c, dict)]
    return got


def section_path(folder: Path, idx: dict, c: dict) -> Path:
    near = folder / idx["_dir"] / str(c.get("file"))
    base = (folder / idx["_dir"]).resolve()
    if near.resolve().parent != base or near.is_symlink():
        raise Refused("I could not find that section.")
    return near.resolve()


def all_books(shelf: Path) -> list[Path]:
    if not shelf.is_dir():
        return []
    return [f for f in sorted(shelf.iterdir(), key=lambda p: p.name.lower())
            if f.is_dir() and not f.is_symlink() and read_index(f)]


def find_book(shelf: Path, title: Any) -> Path:
    want = " ".join(str(title or "").split()).lower()
    books = all_books(shelf)
    if not books:
        raise Refused("There are no manuscripts yet. Add one on the Manuscript page.")
    if not want and len(books) == 1:
        return books[0]
    titled = [(f, str(read_index(f).get("title") or f.name)) for f in books]
    for f, t in titled:
        if want in (t.lower(), f.name.lower()):
            return f
    near = [f for f, t in titled if want and want in t.lower()]
    if len(near) == 1:
        return near[0]
    raise Refused("I could not find that manuscript. These are here: " + ", ".join(t for _f, t in titled) + ".")


# -- finding a quote ----------------------------------------------------------------------------------------------

_SAME = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "\u2014": "-", "\u2013": "-"})


def _folded(text: str) -> tuple[str, list[int]]:
    """The text in one spacing and one kind of quote mark, lowercase, with where each letter came from."""
    out, where = [], []
    space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not space and out:
                out.append(" ")
                where.append(i)
            space = True
            continue
        space = False
        out.append(ch.translate(_SAME).lower()[0])
        where.append(i)
    return "".join(out), where


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """Where a quote really is in this text, by any spacing, case and kind of quote mark. Exact words or nothing."""
    q = _folded(quote.strip().strip('"“”'))[0].strip()
    if not q:
        return None
    hay, where = _folded(text)
    i = hay.find(q)
    if i < 0:
        return None
    return where[i], where[i + len(q) - 1] + 1


# -- saving: the writer's own hand ----------------------------------------------------------------------------------

def _stamp() -> str:
    t = time.time()
    return datetime.fromtimestamp(t).strftime("%Y%m%d-%H%M%S-") + f"{int(t * 1000) % 1000:03d}"


def _by(stem: str) -> str:
    return next((k for k, v in BY_MARK.items() if v and stem.endswith(v)), "you")


def versions(root: Path, n: int) -> list[dict]:
    """Earlier versions of one section, newest first. ``root`` is this book's versions folder in the app's own data."""
    base = root / str(n)
    out = []
    if base.is_dir():
        for p in sorted(base.glob("*.md"), reverse=True):
            if STAMP.match(p.stem):
                body = HEAD.sub("", p.read_text(encoding="utf-8", errors="replace"))
                out.append({"id": p.stem, "words": len(body.split()), "by": _by(p.stem),
                            "saved_at": datetime.strptime(p.stem[:15], "%Y%m%d-%H%M%S").isoformat(timespec="seconds")})
    return out


def version_text(root: Path, n: int, vid: Any) -> str:
    if not STAMP.match(str(vid or "")):
        raise Refused("I could not find that version.")
    path = root / str(n) / f"{vid}.md"
    if not path.is_file():
        raise Refused("I could not find that version.")
    return HEAD.sub("", path.read_text(encoding="utf-8", errors="replace")).rstrip("\n")


def thin(base: Path, today: datetime | None = None) -> None:
    """The rule for old versions, one section at a time: every one of the last 20 saves; before those, the newest of each
    day for 30 days and the newest of each month after that; always the very first, the text before she ever changed it;
    never more than 60, the oldest thinned first."""
    today = today or datetime.now()
    files = sorted((p for p in base.glob("*.md") if STAMP.match(p.stem)), key=lambda p: p.stem, reverse=True)
    keep = files[:KEEP_RECENT]
    seen: set[str] = set()
    for p in files[KEEP_RECENT:]:
        when = datetime.strptime(p.stem[:8], "%Y%m%d")
        slot = p.stem[:8] if (today - when).days <= KEEP_DAILY else p.stem[:6]
        if slot not in seen:
            seen.add(slot)
            keep.append(p)
    if files and files[-1] not in keep:
        keep.append(files[-1])
    if len(keep) > KEEP_MOST:
        keep = keep[:KEEP_MOST - 1] + [keep[-1]]
    for p in files:
        if p not in keep:
            p.unlink(missing_ok=True)


def save_section(folder: Path, root: Path, n: Any, text: Any, base_sha: Any, by: str = "you") -> dict:
    """The one writer of section text. The old file is kept first; the heading line stays; a stale page is refused, so
    nothing she typed elsewhere is lost. Reached only from the page's routes, never from a tool."""
    if not isinstance(text, str):
        raise Refused("Say what the section's text is.")
    if len(text) > TEXT_MAX:
        raise Refused("That is too long for one section.")
    idx = read_index(folder)
    c = next((x for x in idx.get("sections") or [] if str(x.get("n")) == str(n).strip()), None)
    if c is None:
        raise Refused("I could not find that section.")
    path = section_path(folder, idx, c)
    raw = path.read_bytes()
    if base_sha and base_sha != sha_of(raw):
        raise Refused("This section changed somewhere else since you opened it, so nothing was saved. Copy your words, "
                      "open it again and put them back.")
    old = raw.decode("utf-8", errors="replace")
    m = HEAD.match(old)
    head = m.group() if m else ""
    body = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    new = head + body + "\n" if body else head.rstrip("\n") + "\n"
    if new == old:
        return {"sha": sha_of(raw), "words": len(body.split()), "changed": False}
    stamp = _stamp() + BY_MARK.get(by, "")
    base = root / str(int(c.get("n") or 0))
    base.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, base / f"{stamp}.md")
    write_atomic(path, new)
    thin(base)
    fresh = sha_of(new.encode("utf-8"))
    words = len(re.findall(r"\S+", body))
    stored = read_json(folder / INDEX, {})                 # as Manuscript wrote it, with this section's new sha and words
    for key in ("sections", "chapters"):
        for e in stored.get(key) or []:
            if isinstance(e, dict) and e.get("n") == c.get("n"):
                e["sha"], e["words"] = fresh, words
    write_json(folder / INDEX, stored)
    return {"sha": fresh, "words": words, "changed": True, "version": stamp}
