"""``zone1.classify``: labels with spans, over the whole text (Z1C sections 3 and 4).

``classify(text=None, *, file=None, path="chat", people=None) -> Classified``

- The floor pack always runs (F1): the key detector (``secret_detector``), the
  D50 token rules (``token_rules``: more prefixes, random-looking tokens, keys
  split across lines) and the built-in ID, card, bank, password and
  one-time-code detectors, plus the default personal-data and health detectors
  (health always on in v2).
- Whole text, never cut (F9): no size limit inside a scan. A file bigger than
  ``MAX_FILE_BYTES`` is not indexed at all (``TooBig``) rather than half scanned.
- Reading a file (``decode_file``; ``read_text`` is its indexed reading): a
  byte-order mark decides first (UTF-8, UTF-16 LE/BE, UTF-32); without one,
  strict UTF-8, then Windows-1252 (latin-1 for its five unused bytes). UTF-16
  is never guessed without a mark. A UTF-16/32 mark may be a coincidence, so
  the Windows-1252 reading is always checked as well, and is the one indexed
  when the wide reading looks odd (one non-letter character or General
  Punctuation in bulk, NULs, or mostly CJK). No mark and NUL bytes, or many
  control characters or little printable text in the first 8 KB: ``Binary``.
  Then a garbage check per 2,000-character window: too many replacement or
  control characters means ``Garbled``. Both: not indexed, with a plain reason
  (fail closed).
- Child data is P4 (F10): a name on the people list with role ``child`` is
  labelled ``child.any``, and every other hit within 2,000 characters of a
  child mention, on either side, is raised to P4 (a sliding reach, so a chunk
  edge never splits them).
- Linkage: two or more quasi-identifiers in one 2,000-character chunk add
  ``linkage.quasi_id`` (P3) over the set.
- Union only (F2): labels are only added; overlapping spans merge and keep every
  label; the merged tier is the highest.
- A floor failure raises ``FloorError``; every caller fails closed on it (F3).

This is a direct Core call, never a Bus message, because it carries raw text.
Nothing here logs or stores a value.
"""

from __future__ import annotations

import bisect
import codecs
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import detectors, labels, secret_detector, token_rules

CHUNK = 2000
ACT_AT = 0.6
REVIEW_AT = 0.3
MAX_FILE_BYTES = 64 * 1024 * 1024
PATHS = ("chat", "files", "package_data", "outbound")
QUASI = frozenset({"person.name", "place.zip", "person.dob", "person.gender", "place.city", "person.school",
                   "person.employer"})

_KEY_LABEL = {
    "private_key": "secret.private_key",
    "openai_style_key": "secret.api_key", "stripe_style_key": "secret.api_key", "aws_access_key": "secret.api_key",
    "google_api_key": "secret.api_key",
    "url_password": "auth.password", "cli_password": "auth.password",
    "huggingface_token": "secret.api_key",
}


class FloorError(Exception):
    """The floor check could not run, so nothing is saved, indexed or sent."""


class TooBig(Exception):
    """This file is too big to check whole, so it is not indexed."""


class Unsupported(Exception):
    """This file isn't text PuffPAI can read yet, so it is not indexed."""


class Garbled(Unsupported):
    """This file's text doesn't read cleanly (it may be in an encoding PuffPAI can't tell), so it is not indexed."""


class Binary(Unsupported):
    """This looks like a binary file, so it wasn't indexed."""


@dataclass(frozen=True)
class DecodedFile:
    """``text`` is the reading that gets indexed; ``also`` are other readings of the same bytes. Every reading
    goes through the detectors, and anything any of them finds is held (``folders.Library.scan_file``)."""
    text: str
    also: tuple[str, ...] = ()
    how: str = "utf-8"


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    label: str
    tier: str
    confidence: float
    maybe: bool
    by: str


@dataclass
class Group:
    """Overlapping hits merged into one span; keeps every label."""
    start: int
    end: int
    labels: list[str]
    tier: str

    @property
    def secret(self) -> bool:
        return any(labels.is_secret(x) for x in self.labels)

    @property
    def scrub(self) -> bool:
        return any(labels.effects(x).get("keep") == "none" for x in self.labels)

    @property
    def hold(self) -> bool:
        return any(labels.effects(x).get("hold") for x in self.labels)

    @property
    def primary(self) -> str:
        order = sorted(self.labels, key=lambda x: (labels.TIERS.index(labels.tier_of(x)), labels.is_secret(x)))
        return order[-1]


@dataclass
class Classified:
    length: int
    hits: list[Hit] = field(default_factory=list)
    checked: str = "full"

    @property
    def top_tier(self) -> str:
        return labels.top([h.tier for h in self.hits])

    def label_rows(self) -> list[dict]:
        """For ``data.labeled``: one row per label, kinds only."""
        seen: dict[str, dict] = {}
        for h in self.hits:
            row = seen.get(h.label)
            if row is None or labels.TIERS.index(h.tier) > labels.TIERS.index(row["tier"]):
                seen[h.label] = {"label": h.label, "tier": h.tier, "confidence": round(h.confidence, 2),
                                 "maybe": h.maybe, "by": h.by}
        return list(seen.values())

    def groups(self) -> list[Group]:
        out: list[Group] = []
        for h in sorted(self.hits, key=lambda h: (h.start, -h.end)):
            if h.label == "linkage.quasi_id":
                continue                       # a set label, not a value of its own
            if out and h.start < out[-1].end:
                g = out[-1]
                g.end = max(g.end, h.end)
                if h.label not in g.labels:
                    g.labels.append(h.label)
                g.tier = labels.top([g.tier, h.tier])
            else:
                out.append(Group(h.start, h.end, [h.label], h.tier))
        return out


def _people_spans(text: str, people: list[dict] | None) -> list[tuple[int, int, str, float]]:
    out = []
    for person in people or []:
        if str(person.get("role", "")).lower() != "child":
            continue
        names = [person.get("name", "")] + list(person.get("names", []))
        for name in {n.strip() for n in names if isinstance(n, str) and len(n.strip()) >= 2}:
            for m in re.finditer(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text, re.IGNORECASE):
                out.append((m.start(), m.end(), "child.any", 0.95))
    return out


def _raw_spans(text: str, people: list[dict] | None) -> list[tuple[int, int, str, float, str]]:
    spans: list[tuple[int, int, str, float, str]] = []
    for h in secret_detector.find(text):
        spans.append((h.start, h.end, _KEY_LABEL.get(h.kind, "secret.token"), 0.95, "core:floor"))
    for s, e, kind in token_rules.find(text):
        spans.append((s, e, _KEY_LABEL.get(kind, "secret.token"), 0.9, "core:floor"))
    for s, e, lab, conf in detectors.pii_spans(text):
        spans.append((s, e, lab, conf, "core:floor" if labels.is_floor(lab) else "core:default"))
    for s, e, lab, conf in _people_spans(text, people):
        spans.append((s, e, lab, conf, "core:floor"))
    return spans


def classify_text(text: str, *, path: str = "chat", people: list[dict] | None = None) -> Classified:
    if path not in PATHS:
        raise ValueError(f"unknown path {path!r}")
    if not isinstance(text, str):
        raise FloorError("not text")
    try:
        raw = _raw_spans(text, people)
    except Exception as exc:                 # the floor engine broke: fail closed (F3)
        raise FloorError(type(exc).__name__) from None
    hits: list[Hit] = []
    for s, e, lab, conf, by in raw:
        if not (0 <= s < e <= len(text)) or conf < REVIEW_AT:
            continue
        maybe = conf < ACT_AT
        hits.append(Hit(s, e, lab, labels.tier_of(lab), conf, maybe, by))
    # Child data is P4 (F10): every hit within CHUNK characters of a child mention, either side, goes up.
    # A sliding reach, not fixed chunks, so a chunk edge between them never lets one through.
    child = sorted((h.start, h.end) for h in hits if h.label == "child.any")
    if child:
        starts = [c[0] for c in child]

        def near_child(h: Hit) -> bool:
            i = bisect.bisect_right(starts, h.end + CHUNK)      # mentions starting no later than its reach
            return any(h.start <= ce + CHUNK for _, ce in child[:i])
        hits = [Hit(h.start, h.end, h.label, "P4", h.confidence, h.maybe, h.by) if near_child(h) else h
                for h in hits]
    # Linkage (Z1C 3.1): two or more quasi-identifiers in one chunk.
    by_chunk: dict[int, list[Hit]] = {}
    for h in hits:
        if h.label in QUASI:
            by_chunk.setdefault(h.start // CHUNK, []).append(h)
    for chunk_hits in by_chunk.values():
        if len({h.label for h in chunk_hits}) >= 2:
            s = min(h.start for h in chunk_hits)
            e = max(h.end for h in chunk_hits)
            hits.append(Hit(s, e, "linkage.quasi_id", labels.tier_of("linkage.quasi_id"), 0.7, False, "core:floor"))
    hits.sort(key=lambda h: (h.start, h.end, h.label))
    return Classified(length=len(text), hits=hits, checked="full")


_BOMS = ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF8, "utf-8-sig"),
         (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))
# Characters a clean decode of a text file almost never has: replacement marks, control characters (tab,
# line ends, form feed and ESC aside), private-use and lone surrogate code points.
_JUNK = re.compile("[\x00-\x08\x0e-\x1a\x1c-\x1f\x7f-\x9f\ufffd\ue000-\uf8ff\ud800-\udfff]")
# What a non-UTF-16 file read as UTF-16 turns into: CJK, Hangul and Yi characters.
_WIDE_JUNK = re.compile("[\u1100-\u11ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\ua000-\ua4cf\uac00-\ud7af"
                        "\uf900-\ufaff]")
GARBLED_AT = 0.3


def _check_clean(text: str, *, wide: bool) -> None:
    """``Garbled`` when any 2,000-character window looks like a wrong decode (fail closed, F3, F9)."""
    bad: dict[int, int] = {}
    for m in _JUNK.finditer(text):
        bad[m.start() // CHUNK] = bad.get(m.start() // CHUNK, 0) + 1
    if wide:
        for m in _WIDE_JUNK.finditer(text):
            bad[m.start() // CHUNK] = bad.get(m.start() // CHUNK, 0) + 1
    for n, count in bad.items():
        window = text[n * CHUNK:(n + 1) * CHUNK]
        letters = len(window) - sum(window.count(c) for c in " \t\r\n")
        if count >= 4 and count > GARBLED_AT * max(letters, 1):
            raise Garbled()


SNIFF_BYTES = 8192
_CONTROL = re.compile("[\x00-\x08\x0e-\x1a\x1c-\x1f\x7f-\x9f]")
_GENERAL_PUNCT = re.compile("[\u2000-\u206f]")


def _windows_text(data: bytes) -> str:
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _looks_binary(text: str) -> bool:
    """The first 8 KB: many control characters, or little printable text."""
    head = text[:SNIFF_BYTES]
    if not head:
        return False
    controls = len(_CONTROL.findall(head))
    printable = sum(1 for c in head if c.isprintable() or c in "\t\n\r\f\v\x1b")
    return controls > 0.1 * len(head) or printable < 0.9 * len(head)


def _odd_wide(text: str) -> bool:
    """A UTF-16/32 reading that looks like other bytes read wrongly: a large share of one non-letter
    character, or of General Punctuation (U+2000 to U+206F, e.g. two Windows-1252 spaces become U+2020)."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    n = len(chars)
    if len(_GENERAL_PUNCT.findall(text)) > 0.1 * n:
        return True
    counts = Counter(c for c in chars if not c.isalpha())
    return bool(counts) and counts.most_common(1)[0][1] > 0.3 * n


def decode_file(file: Path | str) -> DecodedFile:
    """The whole file, every reading worth checking, or ``TooBig`` / ``Unsupported`` / ``Binary`` /
    ``Garbled``. Never a part of it.

    A byte-order mark decides first; then strict UTF-8; then Windows-1252 (latin-1 for its unused bytes).
    UTF-16 is never guessed without a mark (an even-length Windows-1252 file would read as CJK, so its keys
    would not be found, and the index would give them back). A UTF-16/32 mark can be a coincidence (a
    Windows-1252 file that starts with the bytes FF FE), so the Windows-1252 reading is always checked too,
    and a UTF-16/32 reading that looks odd is not the one indexed."""
    p = Path(file)
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise TooBig()
    data = p.read_bytes()
    for bom, enc in _BOMS:
        if not data.startswith(bom):
            continue
        if enc == "utf-8-sig":
            try:
                text = data.decode(enc)
            except UnicodeDecodeError:
                break                                   # not UTF-8 after all: read it the Windows way below
            if "\x00" in text:
                raise Binary()
            _check_clean(text, wide=False)
            return DecodedFile(text, (), enc)
        try:
            wide = data.decode(enc)
        except UnicodeDecodeError:
            wide = None
        narrow = _windows_text(data)
        if wide is None or "\x00" in wide or _odd_wide(wide) or not _is_clean(wide, wide=True):
            # The mark looks like a coincidence: index the Windows-1252 reading, still check the other one.
            if "\x00" in narrow or _looks_binary(narrow):
                raise Garbled()
            _check_clean(narrow, wide=False)
            return DecodedFile(narrow, () if wide is None else (wide,), "cp1252")
        return DecodedFile(wide, (narrow,), enc)
    if b"\x00" in data:
        raise Binary()                      # binary, or UTF-16 with no mark: never guessed
    try:
        text = data.decode("utf-8")
        how = "utf-8"
    except UnicodeDecodeError:
        text, how = _windows_text(data), "cp1252"
    if _looks_binary(text):
        raise Binary()
    _check_clean(text, wide=False)
    return DecodedFile(text, (), how)


def _is_clean(text: str, *, wide: bool) -> bool:
    try:
        _check_clean(text, wide=wide)
    except Garbled:
        return False
    return True


def read_text(file: Path | str) -> str:
    """The reading of *file* that gets indexed (``decode_file(file).text``)."""
    return decode_file(file).text


def classify(text: str | None = None, *, file: Path | str | None = None, path: str | None = None,
             people: list[dict] | None = None) -> Classified:
    """Labels with spans for *text*, or for the whole of *file* (path defaults to ``files`` then)."""
    if (text is None) == (file is None):
        raise ValueError("give text or file")
    if file is not None:
        return classify_text(read_text(file), path=path or "files", people=people)
    return classify_text(text, path=path or "chat", people=people)
