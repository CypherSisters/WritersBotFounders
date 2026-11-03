"""Random-looking tokens, more key prefixes, and keys split across lines (D50, Z1C F1).

Floor pack: Core code, always run by ``classify`` on every path (chat, files,
package data, outbound), next to ``secret_detector``.
``find(text) -> [(start, end, kind)]``, every span a value to hold:

- ``huggingface_token`` (``hf_...``) and ``puffpai_token`` (the Core's own
  ``ppk_``, ``ppd_``, ``ppt_``). The other D50 prefixes (``sk-``, ``ghp_``,
  ``github_pat_``, ``xox``, ``AKIA``, ``AIza``) are ``secret_detector`` shapes.
- ``keyword_token``: next to a key word (key, token, secret, password, passwd,
  bearer, api, apikey, credential), within 40 characters after it on the same
  line: 16 or more token characters with a letter and a digit, or 24 or more
  in mixed case that don't read as words.
- ``random_token``: 32 to 4,096 token characters of high randomness anywhere,
  unless a harmless shape: a git commit id (40 hex), a UUID, or a file hash
  labelled as such (sha1, sha256, sha512, md5, hash, checksum, digest,
  integrity, fingerprint just before it; ``sha256-`` / ``sha512-`` integrity
  strings; a ``sha256sum`` line, "<hash>  <file>"). 32 or more hex characters
  that are not one of those are held (a 64-hex value with no hash label too).
- Split keys: a token run that ends a line and goes on at the start of the
  next one (a key wrapped or pasted over lines) is joined, every rule above and
  the ``secret_detector`` shapes run over the joined run, and a hit that crosses
  the break covers every part of it, line breaks included.

"High randomness" is a measured guess: Shannon entropy, distinct characters,
and how many letter pairs are common English pairs (words and code names score
high, random strings low). Honest limits: a random value under 32 characters
with no key word or known prefix, a run over 4,096 characters, a key word more
than 40 characters before its value or after it, a split tail under 4
characters or one that reads as a plain word, and random values that happen to
read like words are not caught. A word-like value next to a key word is not
held by this rule (``secret_detector`` still holds ``NAME=value`` settings).
Values are never logged.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from . import secret_detector

_T = r"A-Za-z0-9_\-+/"
_RUN = re.compile(r"(?<![" + _T + r"])[" + _T + r"]{32,4096}={0,2}(?![" + _T + r"=])")
_RUN16 = re.compile(r"(?<![" + _T + r"])[" + _T + r"]{16,4096}={0,2}(?![" + _T + r"=])")
_RUN_START = re.compile(r"(?<![" + _T + r"])[" + _T + r"]")
_HEX = re.compile(r"[0-9a-fA-F]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_GIT_SHA = re.compile(r"[0-9a-fA-F]{40}")
_INTEGRITY = re.compile(r"(?i)(?:sha(?:1|224|256|384|512)|md5)[-:_]")
_HASH_LABEL = re.compile(r"(?i)(?<![A-Za-z0-9])(?:sha-?(?:1|224|256|384|512)(?:sum)?|sha3(?:-\d+)?|md5(?:sum)?|"
                         r"blake2[bs]?|blake3|hash(?:es)?|checksums?|digests?|integrity|fingerprints?)"
                         r"(?![A-Za-z])[^\n]{0,24}$")
_HASH_LIST_TAIL = re.compile(r"(?: \*| {2})\S")
_PREFIXED = re.compile(r"(?<![A-Za-z0-9_])(?:(hf_[A-Za-z0-9]{30,})|(pp[ktd]_[A-Za-z0-9_\-]{30,}))")
_KEYWORD = re.compile(r"(?i)(?<![A-Za-z0-9])(?:api[ _\-]?keys?|apikeys?|keys?|tokens?|secrets?|passwords?|"
                      r"passwd|bearer|api|credentials?)(?![A-Za-z0-9])")
_KEYWORD_REACH = 40
_WRAP = re.compile(r"(?<![" + _T + r"=])[" + _T + r"=]{6,}[ \t]*\\?\r?\n")  # a run is tried once, from its start
_TAIL = re.compile(r"[ \t]*([" + _T + r"]{4,4096}={0,2})(?![" + _T + r"=])")
_SETTING_AFTER = re.compile(r"[ \t]*(?:=|:)")
_LINE_END = re.compile(r"[ \t]*\\?\r?\n")
_MAX_WRAP_LINES = 64

# Common English letter pairs: words and code names are mostly made of these, random strings are not.
_BIGRAMS = frozenset("""
th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng se ha as ou io le ve co me de hi ri ro
ic ne ea ra ce li ch ll be ma si om ur ca el ta la ns di fo ho pe ec pr no ct us ac ot il tr ly nc et ut ss so
rs un lo wa ge ie wh ee wi em ad ol rt po we na ul ni ts mo ow pa im mi ai sh ir su id os iv ia am fi ci vi pl
ig tu ev ld ry mp fe bl ab gh ty op wo sa ay ex ke fr oo av ag if ap gr od bo sp rd do uc bu ei ov by rm ep tt
oc fa ef cu rn sc gi da yo cr cl du ga qu ue ff ba ey ls va um pp ua up lu go ht ru ug ds lt pi rc rr eg au ck
ew mu br bi pt ak pu ui rg ib tl ny ki rk ys ob mm fu ph og ms ye ud mb ip ub oi rl gu dr
""".split())
_LETTER_PIECE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
WORDLIKE_AT = 0.72                     # random strings score about 0.3 (p99 0.67); words and code names 0.77+


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values()) if n else 0.0


def _bigram_ratio(s: str) -> tuple[float, int]:
    total = hit = 0
    for piece in _LETTER_PIECE.findall(s):
        piece = piece.lower()
        for a, b in zip(piece, piece[1:]):
            total += 1
            hit += (a + b) in _BIGRAMS
    return (hit / total if total else 0.0), total


def looks_random(run: str) -> bool:
    """32 or more characters of high randomness (D50), harmless shapes aside (not the file-hash label)."""
    body = run.rstrip("=")
    if len(body) < 32 or _UUID.fullmatch(body) or _GIT_SHA.fullmatch(body):
        return False
    if _HEX.fullmatch(body):
        return bool(re.search(r"\d", body)) and bool(re.search(r"[A-Fa-f]", body)) and _entropy(body) >= 3.0
    if not re.search(r"[A-Za-z]", body):
        return False                    # digits and marks only: numbers, not tokens
    if len(set(body)) < 10 or _entropy(body) < 3.3:
        return False
    ratio, pairs = _bigram_ratio(body)
    if pairs < 6:
        return bool(re.search(r"\d", body))
    if ratio < WORDLIKE_AT:
        return True
    # Second signal, for mixed case with digits (base64, URL-safe tokens): digits switching in and out of the
    # letters often. Names with version numbers switch a few times and read as words (pairs 0.85 and up).
    if re.search(r"[a-z]", body) and re.search(r"[A-Z]", body) and re.search(r"\d", body):
        switches = sum(a.isdigit() != b.isdigit() for a, b in zip(body, body[1:]) if a.isalnum() and b.isalnum())
        return switches >= 4 and ratio < 0.85 and _entropy(body) >= 3.8
    return False


def _labelled_hash(text: str, start: int, end: int) -> bool:
    """A file hash labelled as such: a hash word just before it, an integrity prefix, or a sha256sum line."""
    run = text[start:end]
    if _INTEGRITY.match(run):
        return True
    reach = max(0, start - 40)                 # only this far back is read, so a long line isn't walked per run
    if _HASH_LABEL.search(text, max(text.rfind("\n", reach, start) + 1, reach), start):
        return True
    return bool(_HEX.fullmatch(run)) and _HASH_LIST_TAIL.match(text, end) is not None


def _keyish(run: str) -> bool:
    body = run.rstrip("=")
    if len(set(body)) < 6 or _entropy(body) < 3.0:
        return False
    has_letter, has_digit = bool(re.search(r"[A-Za-z]", body)), bool(re.search(r"\d", body))
    if has_letter and has_digit:
        return True
    if len(body) >= 24 and re.search(r"[a-z]", body) and re.search(r"[A-Z]", body):
        return _bigram_ratio(body)[0] < WORDLIKE_AT
    return False


def _direct(text: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for m in _PREFIXED.finditer(text):
        out.append((m.start(), m.end(), "huggingface_token" if m.group(1) else "puffpai_token"))
    for m in _KEYWORD.finditer(text):
        stop = text.find("\n", m.end(), m.end() + _KEYWORD_REACH)
        stop = m.end() + _KEYWORD_REACH if stop < 0 else stop
        for st in _RUN_START.finditer(text, m.end(), stop):
            r = _RUN16.match(text, st.start())
            if r and _keyish(r.group(0)):
                out.append((r.start(), r.end(), "keyword_token"))
    for m in _RUN.finditer(text):
        if looks_random(m.group(0)) and not _labelled_hash(text, m.start(), m.end()):
            out.append((m.start(), m.end(), "random_token"))
    return out


def _tail_ok(tail: str) -> bool:
    """A next-line run that reads like the rest of a key, not a word or a setting name."""
    body = tail.rstrip("=")
    if re.search(r"\d", body) or re.search(r"[a-z][A-Z]", body):
        return True
    return len(body) >= 12 and _bigram_ratio(body)[0] < WORDLIKE_AT


def _split(text: str) -> list[tuple[int, int, str]]:
    """Hits that cross a line break inside a token run (a key over two or more lines)."""
    out: list[tuple[int, int, str]] = []
    covered = -1
    for m in _WRAP.finditer(text):
        if m.start() < covered:
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        left_end = m.start() + len(m.group(0).rstrip(" \t\\\r\n"))
        # pieces: (start in text, end in text); joined = the pieces put together
        pieces = [(line_start, left_end)]
        at = m.end()
        for _ in range(_MAX_WRAP_LINES):
            t = _TAIL.match(text, at)
            if not t or not _tail_ok(t.group(1)) or _SETTING_AFTER.match(text, t.end()):
                break
            pieces.append((t.start(1), t.end(1)))
            nl = _LINE_END.match(text, t.end())
            if not nl:
                break
            at = nl.end()
        if len(pieces) < 2:
            continue
        covered = pieces[-1][1]
        joined = "".join(text[a:b] for a, b in pieces)
        offsets, breaks, n = [], [], 0
        for a, b in pieces:
            offsets.append((n, a))
            n += b - a
            breaks.append(n)
        breaks = breaks[:-1]

        def orig(j: int) -> int:
            for base, a in reversed(offsets):
                if j >= base:
                    return a + (j - base)
            return offsets[0][1]

        hits = [(h.start, h.end, h.kind) for h in secret_detector.find(joined)] + _direct(joined)
        for a, b, kind in hits:
            if any(a < k < b for k in breaks):
                out.append((orig(a), orig(b - 1) + 1, kind))
    return out


def find(text: str) -> list[tuple[int, int, str]]:
    """Every value these rules hold in *text*: ``[(start, end, kind)]``, whole text, may overlap."""
    if not text:
        return []
    return _direct(text) + _split(text)


__all__ = ["WORDLIKE_AT", "find", "looks_random"]
