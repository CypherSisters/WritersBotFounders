"""Documents pinned to the bot (D125): one file she keeps coming back to, in every chat, with no linking again.

- The paths live in ``settings\\folders.json`` ``pinned`` (``folders.Library.pin``), so a pin is per bot. The file is
  read where it is and never changed, as a linked folder is.
- :func:`read_document`: the words of a .txt, .md, .docx or .pdf. .docx needs only the standard library: a zip, and
  its XML read with no DOCTYPE allowed.
- ``Library.scan_file`` runs the gate over the whole file, as for any file of hers: every private find is held and
  the index gets the locked form, so ``search_knowledge`` finds it too.
- :func:`read`: one part at a time, about 4,000 characters, so a whole novel works on a small local model. Nothing
  of a document goes in a prompt but its name. A local brain with the vault open gets the whole file through the gate
  (``prepare``: real values, never keys). Anyone else gets the index's locked form, which holds nothing new. The tool
  result then passes the turn's own gate, like every tool result (D50). A document she marked "no vault" (D146) skips all that.
"""

from __future__ import annotations

import bisect
import io
import re
import threading
import xml.parsers.expat
import zipfile
from pathlib import Path
from typing import Any

from .classify import Binary, DecodedFile, MAX_FILE_BYTES, TooBig, Unsupported, _check_clean, decode_file
from .gate import OwnWords, prepare

#: One part, in characters. A local brain's tool result holds 6,000 (``zone2.gate.MAX_MODEL_CHARS``).
PART = 4000
#: The most parts :func:`read` names for ``find``.
FIND_MAX = 10
#: What :func:`show` gives around a passage, each side.
AROUND = 240
PASSAGE_MAX = 2000
_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main "
_FALLBACK = "http://schemas.openxmlformats.org/markup-compatibility/2006 Fallback"
#: Text in a .docx that isn't there to read: the plain copy of a text box, and tracked text moved away.
_SKIP = {_FALLBACK, _WORD + "moveFrom"}
_DOCX_PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")


class Unreadable(Unsupported):
    """A document that can't give its words, with the plain reason."""


def _word_xml(data: bytes) -> str:
    """The words of one part of a .docx, a blank line between paragraphs. Refuses a DOCTYPE, so no entities."""
    paragraphs: list[str] = []
    now: list[str] = []
    state = {"text": 0, "skip": 0}

    def start(name: str, _attrs: dict) -> None:
        if name in _SKIP:
            state["skip"] += 1
        elif state["skip"]:
            return
        elif name == _WORD + "t":
            state["text"] += 1
        elif name == _WORD + "tab":
            now.append("\t")
        elif name in (_WORD + "br", _WORD + "cr"):
            now.append("\n")
        elif name == _WORD + "noBreakHyphen":
            now.append("-")

    def end(name: str) -> None:
        if name in _SKIP:
            state["skip"] -= 1
        elif state["skip"]:
            return
        elif name == _WORD + "t":
            state["text"] -= 1
        elif name == _WORD + "p":
            paragraphs.append("".join(now))
            now.clear()

    def chars(text: str) -> None:
        if state["text"] and not state["skip"]:
            now.append(text)

    def no_doctype(*_a: Any) -> None:
        raise Unreadable("This Word file can't be read.")

    parser = xml.parsers.expat.ParserCreate(namespace_separator=" ")
    parser.buffer_text = True
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = chars
    parser.StartDoctypeDeclHandler = no_doctype
    parser.Parse(data, True)
    paragraphs.append("".join(now))
    return "\n\n".join(p for p in paragraphs if p.strip())


def words_of(name: str, data: bytes) -> str | None:
    """The words of a file handed over as bytes (D127), or None when it isn't a kind this reader knows or can't be read."""
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".docx":
            return docx_text(io.BytesIO(data))
        if suffix == ".pdf":
            return pdf_text(io.BytesIO(data))
    except Unsupported:
        return None
    from .folders import TEXT_SUFFIXES
    return data.decode("utf-8", "replace") if suffix in TEXT_SUFFIXES else None


def docx_text(path: Path | io.BytesIO) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if _DOCX_PARTS[0] not in names:
                raise Unreadable("This Word file can't be read.")
            parts = [z.getinfo(n) for n in _DOCX_PARTS if n in names]
            if sum(i.file_size for i in parts) > MAX_FILE_BYTES:
                raise TooBig()
            text = "\n\n".join(t for t in (_word_xml(z.read(i)) for i in parts) if t)
    except (Unreadable, TooBig):
        raise
    except (zipfile.BadZipFile, xml.parsers.expat.ExpatError, RuntimeError, NotImplementedError, EOFError,
            ValueError) as exc:
        # Word saves a file with a password as something that isn't a zip.
        raise Unreadable("This Word file can't be read. It may have a password.") from exc
    if not text.strip():
        raise Unreadable("This Word file has no words in it.")
    return text


def pdf_text(path: Path | io.BytesIO) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Unreadable("PuffPAI's PDF reader is not installed, so this PDF can't be read.") from None
    try:
        reader = PdfReader(path if isinstance(path, io.BytesIO) else str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise Unreadable("This PDF has a password, so it can't be read.")
        text = "\n\n".join(t for t in ((p.extract_text() or "") for p in reader.pages) if t.strip())
    except Unreadable:
        raise
    except Exception as exc:  # noqa: BLE001 - a broken PDF is that one file's problem
        raise Unreadable("This PDF can't be read.") from exc
    if not text.strip():
        raise Unreadable("This PDF has no words to read. It may be a scan, a picture of the pages.")
    return text


def read_document(path: Path | str) -> DecodedFile:
    """The words of one document, or the ``classify`` errors and :class:`Unreadable`. Never a part of it."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in (".docx", ".pdf"):
        return decode_file(p)
    if p.stat().st_size > MAX_FILE_BYTES:
        raise TooBig()
    text = docx_text(p) if suffix == ".docx" else pdf_text(p)
    if "\x00" in text:
        raise Binary()
    _check_clean(text, wide=False)
    return DecodedFile(text, (), suffix[1:])


# -- by name --------------------------------------------------------------------------------------

def names(paths: list[Path]) -> dict[str, Path]:
    """What each pinned document is called: its file name, with its folder when two share one."""
    count: dict[str, int] = {}
    for p in paths:
        count[p.name.lower()] = count.get(p.name.lower(), 0) + 1
    return {(p.name if count[p.name.lower()] == 1 else f"{p.parent.name}\\{p.name}"): p for p in paths}


def named(paths: list[Path], name: str) -> Path | None:
    """The document she or the brain means: its whole name, then without its ending, then the one that holds the
    words. None when that fits none, or more than one."""
    all_names = names(paths)
    want = str(name or "").strip().strip("\"'").lower()
    if not want:
        return next(iter(all_names.values())) if len(all_names) == 1 else None
    for fits in (lambda n: n.lower() == want, lambda n: Path(n).stem.lower() == want, lambda n: want in n.lower()):
        hits = [p for n, p in all_names.items() if fits(n)]
        if hits:
            return hits[0] if len(hits) == 1 else None
    return None


# -- in parts -------------------------------------------------------------------------------------

def part_starts(text: str) -> list[int]:
    """Where each part starts. A part ends at the last paragraph break in its last quarter, else the last space, so
    no word and no ``{{vault:...}}`` is cut, and it is never longer than :data:`PART`."""
    starts, at = [0], 0
    while len(text) - at > PART:
        end = at + PART
        cut = text.rfind("\n\n", at + PART * 3 // 4, end)
        if cut < 0:
            cut = max(text.rfind(c, at + 1, end) for c in " \n\t")
        at = cut + 1 if cut > at else end
        starts.append(at)
    return starts


#: The last two documents read, whole and gated: reading one again is one dictionary look, a novel's PDF is seconds.
_read: dict[tuple, tuple[str, list[int]]] = {}
_read_lock = threading.Lock()


def _words(library: Any, path: Path, st: Any, real: bool) -> tuple[str, list[int]]:
    open_now = bool(real and library.archivist.is_open())
    plain = library.is_fiction(path)
    key = (str(path), st.st_size, st.st_mtime, open_now, plain)
    with _read_lock:
        got = _read.get(key)
    if got:
        return got
    text = None
    if open_now and not plain:
        decoded = read_document(path)
        if not decoded.also:                 # a file that reads two ways is read from the index, as the scan chose
            text = prepare(decoded.text, True, archivist=library.archivist, path="files")[0]
    if text is None:
        text = library.indexed_text(path)
    if plain:
        text = OwnWords(text)
    got = (text, part_starts(text))
    with _read_lock:
        if len(_read) >= 2:
            _read.pop(next(iter(_read)))
        _read[key] = got
    return got


def read(library: Any, name: str, part: int = 1, find: str = "", *, real: bool = False) -> dict | str:
    """One part as the text for the brain, what ``find`` finds, or ``{"ok": False, "error"}`` in plain words."""
    got = _opened(library, name, real)
    if isinstance(got, dict):
        return got
    shown, text, starts = got
    if find.strip():
        return _found(shown, text, starts, find)
    if not 1 <= part <= len(starts):
        return {"ok": False, "error": f"{shown} has parts 1 to {len(starts)}."}
    end = starts[part] if part < len(starts) else len(text)
    next_up = f" Part {part + 1} is next." if part < len(starts) else " That is the end."
    words = f"{shown}, part {part} of {len(starts)}.{next_up}\n\n{text[starts[part - 1]:end]}"
    return OwnWords(words) if isinstance(text, OwnWords) else words


#: Either kind of quote mark, since a model copies those loosely.
_QUOTES = {c: "['\u2018\u2019]" for c in "'\u2018\u2019"} | {c: '["\u201c\u201d]' for c in '"\u201c\u201d'}


def _locate(text: str, passage: str, start: int = 0) -> tuple[int, int] | None:
    """Where *passage* is: word for word first, then with any spacing, any case and either kind of quote mark, since a
    model copies those loosely. Never a guess at anything else: no match is no highlight."""
    at = text.find(passage, start)
    if at >= 0:
        return at, at + len(passage)
    words = passage.split()
    if not words:
        return None
    loose = r"\s+".join("".join(_QUOTES.get(c) or re.escape(c) for c in w) for w in words)
    m = re.compile(loose, re.IGNORECASE).search(text, start)
    return (m.start(), m.end()) if m else None


def show(library: Any, name: str, passage: str, suggestion: str = "", why: str = "", part: int = 0, *,
         real: bool = False) -> dict:
    """``show_passage`` (D140): the exact words of a passage in a pinned document, where it is, and what is around it, with
    the bot's suggestion beside them. It reads only: nothing in the file changes."""
    passage = str(passage or "").strip()
    if not passage:
        return {"ok": False, "error": "Say which passage, in the document's own words."}
    if len(passage) > PASSAGE_MAX:
        return {"ok": False, "error": f"That passage is too long to show. Keep it under {PASSAGE_MAX} characters."}
    got = _opened(library, name, real)
    if isinstance(got, dict):
        return got
    shown, text, starts = got
    first = starts[part - 1] if 1 <= part <= len(starts) else 0
    span = _locate(text, passage, first) or (_locate(text, passage) if first else None)
    if span is None:
        return {"ok": False, "error": f"Those words aren't in {shown}. Copy them exactly with read_pinned, and try again."}
    at, end = span
    where = bisect.bisect_right(starts, at)
    mine = OwnWords if isinstance(text, OwnWords) else str
    return {"ok": True, "show_passage": True, "document": shown, "part": where, "of": len(starts),
            "before": mine(text[max(0, at - AROUND):at]), "passage": mine(text[at:end]), "after": mine(text[end:end + AROUND]),
            "suggestion": str(suggestion or "").strip(), "why": str(why or "").strip(),
            "said": "She sees this passage highlighted, with your suggestion beside it. Nothing in her file changed. "
                    "In your answer, quote the passage and your suggested wording, and say which part it is in."}


def _opened(library: Any, name: str, real: bool) -> tuple[str, str, list[int]] | dict:
    """``(shown name, words, part starts)``, or ``{"ok": False, "error"}`` in plain words."""
    from .folders import file_problem
    paths = library.pinned()
    path = named(paths, name)
    if path is None:
        have = ", ".join(names(paths)) or "none"
        return {"ok": False, "error": f"No one pinned document is called that. Pinned: {have}."}
    shown = next(n for n, p in names(paths).items() if p == path)
    problem = file_problem(library.state, path, data=library.data_folder)   # again: a path can change after the pin
    if problem:
        return {"ok": False, "error": f"{shown} can't be read right now. {problem} It stays pinned."}
    got = library.scan_file(path)                                           # the file changed: the gate first
    if got["status"] not in ("indexed", "unchanged"):
        return {"ok": False, "error": f"{shown} can't be read. {got['reason']}"}
    try:
        text, starts = _words(library, path, path.stat(), real)
    except OSError:
        return {"ok": False, "error": f"{shown} can't be read right now. It stays pinned."}
    except Unsupported as exc:
        return {"ok": False, "error": f"{shown} can't be read. {exc}"}
    return shown, text, starts


def _found(shown: str, text: str, starts: list[int], find: str) -> dict:
    want = find.lower().split()
    hits = []
    for i, at in enumerate(starts):
        words = text[at:starts[i + 1] if i + 1 < len(starts) else len(text)].lower()
        if all(w in words for w in want):
            hits.append(i + 1)
    if not hits:
        return {"ok": True, "document": shown, "parts": [], "of": len(starts), "said": f"No part of {shown} has that."}
    return {"ok": True, "document": shown, "parts": hits[:FIND_MAX], "of": len(starts), "found_in": len(hits)}


__all__ = ["FIND_MAX", "PART", "Unreadable", "show", "docx_text", "named", "names", "part_starts", "pdf_text", "read",
           "read_document", "words_of"]
