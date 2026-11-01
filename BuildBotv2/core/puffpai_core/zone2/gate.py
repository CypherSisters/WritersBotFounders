"""The gate before Zone 2, as the brain uses it (X6, D25, D31(b), D50; 01 "Zone 1").

Every user turn, tool result and file chunk passes ``zone1.gate.prepare``
(lane L3) BEFORE the model sees it:

    prepare(text: str, vault_open: bool) -> (text_for_model: str, handles: list[str])

- vault open: the local runtime on this computer may get real values, never
  secrets or keys (D31(b));
- vault locked: held values are handles (``{{vault:<ref>}}``).

The brain also needs the locked form for everything it keeps (the
conversation store holds handles only), so it calls ``prepare(text, False)``
for what it saves and ``prepare(text, True)`` for the model while the vault is
open. L3's ``prepare`` must give the same ref for the same value, so the two
calls hold one item.

Until L3 lands there is no ``zone1.gate``: then :func:`production` raises
:class:`~.errors.BrainError` (``no_gate``) and no turn runs (fail closed).
Tests pass a double.

**The floor key check (this module, the one place).** After the gate,
:func:`no_keys` runs once more over what the model will read and takes out any
key still there (X6: keys never reach the brain, whatever a gate provider
does). It holds every shape ``zone1.secret_detector`` knows, and (D50) any
random-looking value that

1. starts with a known key prefix (``sk-``, ``ghp_``, ``github_pat_``,
   ``xox?-``, ``AKIA``, ``ppt_``, ``AIza`` and similar), or
2. sits next to a key word (key, token, secret, password, bearer, api), or
3. is 32 or more characters of high randomness and not a harmless shape.

Harmless shapes stay readable: a git commit id (40 hex), a UUID, a file hash
labelled as one (``sha256: ...``). A key word next to a value wins over its
shape (a UUID called "api key" is held).

**Every shape of value (D50).** :func:`as_json` turns any tool result into
JSON types first (tuples and sets to lists, bytes to UTF-8 text with bad bytes
replaced, keys to strings, anything else to its text), and :func:`clean_value`
gates every string in it, dict keys included. :meth:`Gate.clean` is the one
call the chat tool route, the brain loop and ``package_chat`` use.
"""

from __future__ import annotations

import importlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

from ..zone1 import secret_detector
from .errors import BrainError

Prepare = Callable[[str, bool], tuple[str, list]]

NO_GATE = ("The privacy gate is not running, so nothing goes to the brain. "
           "Your words were not sent anywhere.")

HELD = secret_detector.HELD

#: What one value for the model may be, in characters (the tool table's cut).
MAX_MODEL_CHARS = 6000
CUT_LINE = " ...(cut short)"

# -- the floor key check (D31(b), D50) ------------------------------------------

#: A run of characters a key is written in (base64, base64url, hex, ids); read from its first letter or digit.
_TOKEN = re.compile(r"[A-Za-z0-9_\-+/]+={0,2}")
#: Known key prefixes (D50 rule 1). Case matters: ``AKIA``, not ``akia``.
_PREFIX = re.compile(
    r"^(?:sk-(?:proj-|svcacct-|admin-|ant-(?:api\d\d-)?)?|sk_|rk_|pk_live_|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|"
    r"xox[abposre]-|xapp-|AKIA|ASIA|AIza|ppt_|ppk_|ppd_|glpat-|hf_|npm_|pypi-|shpat_|shpss_|dop_v1_|"
    r"sq0atp-|sq0csp-|whsec_|rzp_live_)")
#: A key word just before the value (D50 rule 2): "my token is X", "api key: X", "Bearer X".
_SEP = r"[\"'`\])]?(?:[ \t]*(?:[:=]|=>)[ \t]*|[ \t]+(?:is|was)[ \t]*:?[ \t]*)[\"'`(\[]?"
_SPACE = r"[\"'`\])]?[ \t]+[\"'`(\[]?"
#: Strong key words: a space is enough ("Bearer X", "my token X").
_STRONG = (r"(?:(?:api|access|secret|private|auth)[ _\-]?keys?|apikeys?|tokens?|secrets?|passwords?|passwd|"
           r"passphrase|bearer|credentials?)")
#: Weak key words: only when set ("key: X", "api = X", "the key is X"), so "the API v2/users" stays.
_WEAK = r"(?:keys?|api)"
_WORD_BEFORE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:" + _STRONG + r"(?:" + _SEP + "|" + _SPACE + r")|" + _WEAK + _SEP + r")$")
#: ... or just after it: "X is my api key", "X (token)".
_WORD_AFTER = re.compile(
    r"(?i)[\"'`\])]?[ \t]*[(,\-:]?[ \t]*(?:(?:is|was)[ \t]+)?(?:(?:my|the|your|our|an?)[ \t]+)?"
    r"(?:api[ _\-]?)?(?:keys?|tokens?|secrets?|passwords?|passphrase|credentials?)(?![A-Za-z0-9])")
_LOOK_BACK = 48
#: Harmless shapes (D50): they stay readable unless a key word sits next to them.
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_GIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_HASH_LENGTHS = frozenset({32, 40, 56, 64, 96, 128})
_HASH_LABEL = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:sha-?(?:1|224|256|384|512)(?:sum)?|md5(?:sum)?|blake2[bs]?|blake3|xxh(?:32|64|128)|"
    r"checksum|hash|digest|integrity|etag)(?![A-Za-z0-9])[^\n]{0,80}?[\s:=\"'(/]$")
#: A handle the gate wrote, never taken out again.
_HANDLE = re.compile(r"\{\{vault:[^{}\r\n]{1,128}\}\}")
_SUBRUN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_VOWELS = frozenset("aeiouyAEIOUY")
_CONSONANT_RUN = re.compile(r"[^aeiouyAEIOUY]{5}")
_DIGITS = re.compile(r"\d+")
#: Measured 10-22 on every 32+ character run in the BuildBotv2 code and docs (166, none held but a
#: test's fake key) and on 20000 random keys each: about 0.1 to 1.7 in 100 random 32+ keys with no
#: prefix and no key word next to them still read as words and stay (base64url 0.6, lowercase+digit 1.7).
WORDY = 0.7
MIN_RANDOM = 32
MIN_NEAR_WORD = 12
MIN_AFTER_PREFIX = 16


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values()) if n else 0.0


def _wordy(s: str) -> float:
    """The share of letters that sit in pronounceable word runs (1.0 = all words)."""
    letters = words = 0
    for run in _SUBRUN.findall(s):
        if run.isdigit():
            continue
        n, vowels = len(run), sum(c in _VOWELS for c in run)
        letters += n
        if (n >= 3 and vowels / n >= 0.2 and not _CONSONANT_RUN.search(run)) or (n == 2 and vowels and run.islower()):
            words += n
    return words / letters if letters else 1.0


def _scattered_digits(s: str) -> bool:
    """Digits all through it, as in a made key (names keep a few, together: ``x86_64``, ``2024``)."""
    groups = _DIGITS.findall(s)
    return len(groups) >= 5 and sum(map(len, groups)) / len(s) >= 0.15


def _mixed(s: str) -> bool:
    """Letters and digits together, or both cases."""
    has_digit = any(c.isdigit() for c in s)
    has_alpha = any(c.isalpha() for c in s)
    return (has_digit and has_alpha) or (any(c.islower() for c in s) and any(c.isupper() for c in s))


def _random_looking(s: str, *, at_least: int) -> bool:
    if len(s) < at_least or not _mixed(s):
        return False
    core = s.rstrip("=")
    if _HEX.match(core.replace("-", "")):         # hex, or hex in groups (a UUID): never words
        return True
    return _entropy(core) >= 3.0 and (_wordy(core) < WORDY or _scattered_digits(core))


def harmless(token: str, before: str = "") -> bool:
    """A git commit id, a UUID, or a file hash labelled as one (D50)."""
    if _GIT_SHA.match(token) or _UUID.match(token):
        return True
    return bool(_HEX.match(token)) and len(token) in _HASH_LENGTHS and bool(_HASH_LABEL.search(before))


def _held(text: str, run: int, a: int, b: int) -> bool:
    """D50: is the token ``text[a:b]`` (its run of key characters starts at *run*) a key?"""
    token = text[a:b]
    prefix = _PREFIX.match(token)
    if prefix:
        rest = token[prefix.end():]
        if len(rest) >= MIN_AFTER_PREFIX and _mixed(rest):
            return True
    before = text[max(0, run - _LOOK_BACK):run]
    near = bool(_WORD_BEFORE.search(before) or _WORD_AFTER.match(text, b, min(len(text), b + _LOOK_BACK)))
    if near and len(token) >= MIN_NEAR_WORD and _mixed(token) and _entropy(token) >= 3.0:
        return True                 # next to a key word: no word test, and its shape does not save it
    return _random_looking(text[run:b], at_least=MIN_RANDOM) and not harmless(token, before)


def _spans(text: str) -> list[tuple[int, int]]:
    """``[(start, end)]`` of every key in *text*, merged, handles never touched."""
    if not text:
        return []
    kept = [m.span() for m in _HANDLE.finditer(text)]

    def in_handle(a: int, b: int) -> bool:
        return any(a < hb and ha < b for ha, hb in kept)
    out = []
    for h in secret_detector.find(text):
        if h.kind == "generated_key" and harmless(h.value.strip("\"' "), text[max(0, h.start - _LOOK_BACK):h.start]):
            continue
        if not in_handle(h.start, h.end):
            out.append((h.start, h.end))
    for m in _TOKEN.finditer(text):
        run, b = m.span()
        a = run + len(m.group(0)) - len(m.group(0).lstrip("_-+/"))
        if b - a >= MIN_NEAR_WORD and not in_handle(run, b) and _held(text, run, a, b):
            out.append((run, b))
    out.sort()
    merged: list[list[int]] = []
    for a, b in out:
        if merged and a < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _redact(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    out, at = [], 0
    for a, b in spans:
        out.append(text[at:a])
        out.append(HELD)
        at = b
    out.append(text[at:])
    return "".join(out)


def no_keys(text: str) -> str:
    """*text* with every key, token and password held (D31(b), D50). The one floor key check."""
    text = str(text if text is not None else "")
    return _redact(text, _spans(text))


def no_keys_after(label: str, text: str) -> str:
    """*text* checked as the value of *label* (a dict key): ``{"api_key": "X"}`` holds X (D50 rule 2)."""
    label = re.sub(r"\s+", "_", str(label))[:64]
    if not label:
        return no_keys(text)
    pre = label + ": "
    full = pre + text
    spans = [(max(0, a - len(pre)), b - len(pre)) for a, b in _spans(full) if b > len(pre)]
    return _redact(text, spans)


# -- every shape of value (D50) ---------------------------------------------------

MAX_DEPTH = 32
MAX_NODES = 20000
TOO_DEEP = "[too deep to show]"
LOOP = "[the same value again]"


def _key(k: Any) -> str:
    if isinstance(k, str):
        return k
    if isinstance(k, (bytes, bytearray, memoryview)):
        return bytes(k).decode("utf-8", errors="replace")
    if k is None or isinstance(k, bool):
        return json.dumps(k)
    v = as_json(k)
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)


def as_json(value: Any) -> Any:
    """*value* in JSON types only: tuples and sets become lists, bytes UTF-8 text
    (bad bytes replaced), dict keys strings, anything else its text."""
    budget = [MAX_NODES]

    def walk(v: Any, depth: int, path: frozenset) -> Any:
        budget[0] -= 1
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        if budget[0] < 0 or depth >= MAX_DEPTH:
            return TOO_DEEP
        if isinstance(v, (bytes, bytearray, memoryview)):
            return bytes(v).decode("utf-8", errors="replace")
        if id(v) in path:
            return LOOP
        inner = path | {id(v)}
        if isinstance(v, dict):
            out: dict = {}
            for k, x in v.items():
                name = _key(k)
                while name in out:
                    name += "'"
                out[name] = walk(x, depth + 1, inner)
            return out
        if isinstance(v, (list, tuple)):
            return [walk(x, depth + 1, inner) for x in v]
        if isinstance(v, (set, frozenset)):
            items = [walk(x, depth + 1, inner) for x in v]
            return sorted(items, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
        try:
            return str(v)
        except Exception:  # noqa: BLE001 - a value that cannot say itself is shown by its kind
            return f"[a {type(v).__name__}]"
    return walk(value, 0, frozenset())


def clean_value(value: Any, fn: Callable[[str], str]) -> Any:
    """*value* in JSON types (:func:`as_json`) with *fn* on every string, dict
    keys included; a dict's string value is also key-checked with its key next
    to it (D50 rule 2)."""
    def walk(v: Any, label: str | None) -> Any:
        if isinstance(v, str):
            out = fn(v)
            return no_keys_after(label, out) if label else out
        if isinstance(v, list):
            return [walk(x, None) for x in v]
        if isinstance(v, dict):
            out: dict = {}
            for k, x in v.items():
                name = fn(k)
                while name in out:
                    name += "'"
                out[name] = walk(x, k)
            return out
        return v
    return walk(as_json(value), None)


def _text(value: Any, limit: int) -> str:
    v = as_json(value)
    text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return _cut(text, limit)


def _cut(text: str, limit: int) -> str:
    """*text* at most *limit* characters; a word cut in half is dropped, so no part of a key is left."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    if text[limit:limit + 1].isalnum() or text[limit:limit + 1] in "_-+/=":
        head = re.sub(r"[A-Za-z0-9_\-+/=]+$", "", head)
    return head + CUT_LINE


def model_messages(messages: list[dict], specs: list | None = None) -> tuple[list[dict], list | None]:
    """The last check before the runtime: every message's words and tool calls, and every tool
    spec, through :func:`no_keys`. Pictures are left as they are."""
    out = []
    for m in messages:
        m = dict(m)
        c = m.get("content")
        if isinstance(c, str):
            m["content"] = no_keys(c)
        else:
            m["content"] = "" if c is None else _text(clean_value(c, no_keys), 10 ** 12)
        if m.get("tool_calls"):
            m["tool_calls"] = clean_value(m["tool_calls"], no_keys)
        out.append(m)
    return out, (clean_value(specs, no_keys) if specs is not None else None)


@dataclass
class Gate:
    """``prepare`` plus whether the vault is open, bound for one brain."""

    prepare: Prepare
    vault_open: Callable[[], bool] = lambda: False

    def for_model(self, text: str, *, real_values: bool = True) -> tuple[str, str, list]:
        """``(text_for_model, text_to_keep, handles)``.

        ``real_values=False`` (a brain on another computer, a package caller)
        always gets the locked form.
        """
        text = str(text or "")
        kept, handles = self.prepare(text, False)
        if real_values and self._open():
            model_text, more = self.prepare(text, True)
            handles = list(dict.fromkeys([*handles, *more]))
        else:
            model_text = kept
        return no_keys(str(model_text)), no_keys(str(kept)), list(handles)

    def keep(self, text: str) -> str:
        """The locked form, for stores and anything that leaves the brain."""
        return no_keys(str(self.prepare(str(text or ""), False)[0]))

    def clean(self, value: Any, *, for_model: bool, real_values: bool = True,
              limit: int | None = MAX_MODEL_CHARS) -> Any:
        """Any value (a tool result, an error, a message's content) through the gate (D50).

        ``for_model=True``: one text for the model, at most *limit* characters
        (``None``: no cut; the whole text is gated, so a key next to its name in
        another field is seen). ``for_model=False``: the same shape in JSON
        types, every string and key in the locked form (for pages and stores).
        """
        if not for_model:
            return clean_value(value, self.keep)
        if limit is None:
            return self.for_model(_text(value, 10 ** 12), real_values=real_values)[0]
        text = _text(value, limit * 2)                 # gated before the last cut: no key is cut in half
        return _cut(self.for_model(text, real_values=real_values)[0], limit)

    def with_real_values(self, value: Any) -> Any:
        """*value* in the same shape with every held value real and every key held, while the vault is open, else None.
        For the one tool that may have them (``Concierge._package_args``, D140). A ``{{vault:...}}`` mark already in the
        text stays a mark: ``prepare`` finds values and never opens a mark."""
        if not self._open():
            return None
        return clean_value(value, lambda text: self.for_model(text)[0])

    def _open(self) -> bool:
        try:
            return bool(self.vault_open())
        except Exception:  # noqa: BLE001 - unsure means locked
            return False


def production() -> Gate:
    """The real gate from lane L3, or BrainError(no_gate)."""
    try:
        mod = importlib.import_module("puffpai_core.zone1.gate")
        l3_prepare = getattr(mod, "prepare")
        closed = getattr(mod, "GateClosed")
    except (ImportError, AttributeError):
        raise BrainError(NO_GATE, "no_gate") from None

    def prepare(text: str, vault_open: bool) -> tuple[str, list]:
        # L3 raises GateClosed when its floor check can't run or no Archivist is bound: no turn (fail closed).
        try:
            return l3_prepare(text, vault_open)
        except closed:
            raise BrainError(NO_GATE, "no_gate") from None
    vault_open = getattr(mod, "vault_open", None)
    return Gate(prepare=prepare, vault_open=vault_open if callable(vault_open) else (lambda: False))


__all__ = ["Gate", "HELD", "MAX_MODEL_CHARS", "NO_GATE", "Prepare", "as_json", "clean_value", "harmless",
           "model_messages", "no_keys", "no_keys_after", "production"]
