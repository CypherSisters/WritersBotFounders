"""Search APIs the owner adds herself (lane add-api): the ONE way past the fixed lists in ``policy``.

Every row we ship, and anything a bundle, a store package, Toolsmith or the brain proposes, stays on the fixed lists
(``toolsmith_host.address``). A row here is hers alone: kept in ``<state>/settings/owner_apis.json``, never mixed into
the shipped ``sources.json``, written only by her owner routes (``api/groups/owner_apis.py``).

Nothing in this file opens anything. A row counts only with her Yes on record (:func:`yes_of`): kind ``host``, subject
``toolsmith:search``, exactly its one host, and ``covers.owner_api`` naming the row and :func:`row_sha`, the hash of its
address and key place. Change either and the old Yes stops counting. The consent store seals that record, so a hand-edited
file gets no further than an edited row with no Yes. Every row is checked again each time it is read: one that fails is
dropped, so it can't name a shipped host or another key. Toolsmith's own reads go through :func:`for_reads`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from .. import hosts
from ..policy import SEARCH_HOSTS
from ..text_rules import has_hidden, owner_text_flag
from ..toolsmith.redact import REMOVED
from . import address

logger = logging.getLogger(__name__)

FILE = "owner_apis.json"
SEARCH_SUBJECT = "toolsmith:search"
KEY_PREFIX = "toolsmith.api."
ID_RE = re.compile(r"[a-z][a-z0-9-]{1,30}")
MAX_ADDRESS = 500
MAX_NAME = 60
FIELD_NAMES = ("list", "title", "link", "snippet")
#: The only words that may stand before a key in its header. The vault keeps the bare key, in its own strict shape.
KEY_PREFIXES = ("", "Bearer", "Token")
_FIELD = re.compile(r"[A-Za-z0-9_.-]{1,80}")
_HEADER_KEY = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,40}")
_QUERY_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,40}")
#: Headers a caller must not set: the HTTP layer owns them, and a key there would go to the wrong place.
NO_KEY_HEADERS = frozenset({"host", "cookie", "content-length", "transfer-encoding", "connection", "referer",
                            "user-agent"})
#: A query name that holds a key, whatever its value looks like.
_KEY_WORD = re.compile(r"(?i)(?:^|[-_])(?:key|apikey|token|secret|password|passwd|auth|sig|signature)(?:$|[-_])"
                       r"|api[-_]?key|access[-_]?key|subscription[-_]?key")
_KEY_CHARS = re.compile(r"[A-Za-z0-9\-._~+/=]+")
#: A key's character as an API might echo it: a word of its answer that a person would read as other characters.
_NAMED_ENTITY = {"+": "plus", "/": "sol", "=": "equals", ".": "period", "_": "lowbar", "-": "hyphen|dash"}
MOVED_KEY_LINE = "Save the key again for this address."
_WORD = "puffword"                                 # stands in for {q} and {n} while the address is put in plain form
_COUNT = "puffcount"

#: The Core's fixed values: not hers to set, so a row can't ask for a longer wait, a bigger read or a kept answer.
FIXED = {"reader": "generic", "tries": "look", "timeout_s": 10, "gap_s": 1, "per_job": 1, "max_bytes": 1_000_000,
         "cache_hours": 0}
#: What decides where her words and her key go. A change here asks again (:func:`row_sha`).
BOUND = ("host", "search", "n", "words_param", "words_in_path", "key", "key_in", "key_name", "key_prefix")

_lock = threading.RLock()


class ApiRefused(ValueError):
    """A row the Core will not keep. Its one argument is the plain line she reads."""


def path(state: Path | str) -> Path:
    return Path(state) / "settings" / FILE


def key_name(api_id: str) -> str:
    """Its own vault name, so it can never read or overwrite a shipped row's key or GitHub's."""
    return KEY_PREFIX + api_id


# -- the address she typed -------------------------------------------------------------------------------------

def _reads_as_word(part: str) -> bool:
    """One part of a longer value, as a person writes it: a word, a short number, or a short mark like v2."""
    if part.isdigit():
        return len(part) <= 8
    if not part.isalpha():
        return len(part) <= 4
    if len(part) < 20:
        return True
    flips = sum(a.islower() != b.islower() for a, b in zip(part, part[1:]))
    counts = [part.count(c) / len(part) for c in set(part)]
    return flips < 4 and -sum(p * math.log2(p) for p in counts) < 3.8


def _opaque(text: str) -> bool:
    """20 characters or more of a key's characters that do not read as words joined up. Real paths and fixed values
    pass: ``search``, ``v1``, ``web``, a short site id, ``very-long-descriptive-path-segment``."""
    return len(text) >= 20 and _KEY_CHARS.fullmatch(text) is not None \
        and not all(_reads_as_word(part) for part in re.split(r"[-._~]", text) if part)


def _looks_like_key(text: str) -> bool:
    from ...zone1 import secret_detector
    try:
        if secret_detector.find(text):
            return True
    except Exception:  # noqa: BLE001 - can't tell: treated as a key
        return True
    return any(_opaque(word) for word in text.split())


def _key_in_address(text: str, pairs: list[tuple[str, str]]) -> bool:
    fixed = [(k, v) for k, v in pairs if v not in ("{q}", "{n}")]
    segments = [urllib.parse.unquote(s) for s in urllib.parse.urlsplit(text).path.split("/") if s and s != "{q}"]
    return _looks_like_key(re.sub(r"\{[qn]\}", _WORD, text)) or any(v and _KEY_WORD.search(k) for k, v in fixed) \
        or any(_opaque(v) for _k, v in fixed) or any(_opaque(s) for s in segments)


def check_address(search: Any, n: int) -> dict:
    """``{host, search, words_param | words_in_path}`` with ``search`` in its one plain form, or :class:`ApiRefused`."""
    text = search.strip() if isinstance(search, str) else ""
    if not text:
        raise ApiRefused("Paste the search address, with {q} where the search words go.")
    if len(text) > MAX_ADDRESS or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        raise ApiRefused("That address is not one plain line. Paste it again with no spaces.")
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        raise ApiRefused("That is not a web address BuildBot can read.") from None
    if parts.scheme.lower() != "https":
        raise ApiRefused("Use an https address. Toolsmith only sends to https.")
    if "@" in parts.netloc:
        raise ApiRefused("Take the user name and password out of the address. A key goes in the key field.")
    host = hosts.clean(parts.hostname)
    if hosts.is_ip_literal(host):
        raise ApiRefused("Use the place's name, not a number address.")
    if host == "localhost" or host.endswith(hosts.LOCAL_SUFFIXES) or host.rsplit(".", 1)[-1] in hosts.LOCAL_TLDS:
        raise ApiRefused("That name is on your own network. Toolsmith searches only public places.")
    if host.endswith(".onion"):
        raise ApiRefused("Toolsmith can't reach a .onion name. It searches only public web places.")
    if not hosts.is_dns_name(host):
        raise ApiRefused("That is not a public web name.")
    if hosts.is_public_suffix(host):
        raise ApiRefused("That name is shared by many sites. Use the place's own full name.")
    if port not in (None, 443):
        raise ApiRefused("Use the normal https port. An address with another port is not taken.")
    if "#" in text:
        raise ApiRefused("Take the # part out of the address.")
    if host in SEARCH_HOSTS:
        raise ApiRefused("Toolsmith already searches this place.")
    decoded = urllib.parse.unquote(text)
    if text.count("{q}") != 1 or decoded.count("{q}") != 1:
        raise ApiRefused("Put {q} in the address exactly once, where the search words go.")
    if text.count("{n}") > 1 or decoded.count("{n}") != text.count("{n}") \
            or re.search(r"[{}]", re.sub(r"\{[qn]\}", "", decoded)):
        raise ApiRefused("The only marks an address may hold are {q} and one {n}.")
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    in_path = "{q}" in parts.path.split("/")
    param = [k for k, v in pairs if v == "{q}"]
    if in_path == bool(param) or len(param) > 1:
        raise ApiRefused("{q} must be a whole value, as in ?q={q}, or a whole part of the path, as in /search/{q}.")
    if "{n}" in text and sum(v == "{n}" for _k, v in pairs) != 1:
        raise ApiRefused("{n} must be a whole value, as in &count={n}.")
    if _key_in_address(text, pairs):
        raise ApiRefused("That address holds what looks like a key. Take it out and use the key field.")
    plain = urllib.parse.urlunsplit(("https", host, parts.path, parts.query, ""))
    done = address.canonical(plain.replace("{q}", _WORD).replace("{n}", _COUNT))
    if done is None:
        raise ApiRefused("That address is not in a plain form, so it is not taken.")
    got = {"host": host, "search": done.replace(_WORD, "{q}").replace(_COUNT, "{n}"), "n": n,
           **({"words_in_path": True} if in_path else {"words_param": param[0]})}
    sent = urllib.parse.urlsplit(address.canonical(got["search"].format(q="dog", n=n)) or "")
    if address._search_words(got, sent.path, sent.query) != ("dog",):     # its own searches must pass its own shape
        raise ApiRefused("That address is not in a plain form, so it is not taken.")
    return got


def _key_place(body: dict, search: str) -> tuple[str, str, str]:
    key_in, name, prefix = body.get("key_in"), body.get("key_name"), body.get("key_prefix")
    name = name.strip() if isinstance(name, str) else ""
    if name and _looks_like_key(name):
        raise ApiRefused("That looks like the key itself. Name where the key goes here, and paste the key in the key field.")
    prefix = "" if prefix is None else prefix
    if key_in in (None, ""):
        key_in = "header" if name else "none"
    if not isinstance(prefix, str) or prefix not in KEY_PREFIXES:
        raise ApiRefused("The word before a key is Bearer, Token or nothing.")
    if prefix and key_in != "header":
        raise ApiRefused("The word before a key works only when the key goes in a header.")
    if key_in == "none":
        return "none", "", ""
    if key_in == "header":
        if not _HEADER_KEY.fullmatch(name):
            raise ApiRefused("Name the header the key goes in: letters, digits and hyphens, as in X-API-Key.")
        if name.lower() in NO_KEY_HEADERS:
            raise ApiRefused(f"A caller must not set the {name} header, so it can't carry a key.")
        return "header", name, prefix
    if key_in == "query":
        if not _QUERY_KEY.fullmatch(name):
            raise ApiRefused("Name the part of the address the key goes in: letters, digits, _ and -, as in api_key.")
        query = urllib.parse.urlsplit(search).query
        if name in {k for k, _v in urllib.parse.parse_qsl(query, keep_blank_values=True)}:
            raise ApiRefused(f"The address already uses {name}, so the key can't go there.")
        return "query", name, ""
    raise ApiRefused("A key goes in a header, in the address, or nowhere.")


def _fields(raw: Any) -> dict:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ApiRefused("Name the fields as list, title, link and snippet.")
    out = {}
    for name in FIELD_NAMES:
        value = raw.get(name)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not _FIELD.fullmatch(value):
            raise ApiRefused(f"The {name} field is a name like results or data.items: letters, digits and dots.")
        out[name] = value
    return out


def _name(raw: Any) -> str:
    name = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not name or len(name) > MAX_NAME or has_hidden(name) or any(ord(c) < 0x20 for c in name):
        raise ApiRefused(f"Give it a plain name, up to {MAX_NAME} characters.")
    if owner_text_flag(name):
        raise ApiRefused("Give it a plain name. A name can't hold a link, markup or words aimed at an AI.")
    if _looks_like_key(name):
        raise ApiRefused("That name looks like a key. Paste the key in the key field, and give this a plain name.")
    return name


def _build(api_id: str, body: dict, tested: bool = False, key_host: Any = "") -> dict:
    """One whole row from what she gave (or a saved row). Only these fields: anything else in *body* is never kept.
    ``key_host``: where her key was saved for (:func:`set_key_host`). Never taken from what she sends."""
    n = body.get("n", 5)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 50:
        raise ApiRefused("Ask for between 1 and 50 results.")
    name = _name(body.get("name"))
    got = check_address(body.get("search"), n)
    key_in, name_of_key, prefix = _key_place(body, got["search"])
    short = body.get("short")
    return {"id": api_id, "name": name, "short": _name(short)[:30] if short else name[:30], **got, **FIXED,
            "key": key_name(api_id) if key_in != "none" else "", "key_in": key_in, "key_name": name_of_key,
            "key_prefix": prefix, "key_host": key_host if isinstance(key_host, str) and key_in != "none" else "",
            "fields": _fields(body.get("fields")), "tested": bool(tested)}


def _shipped_ids() -> set[str]:
    from ..toolsmith.find import sources
    src = sources()
    return {str(rid) for group in ("shelves", "catalogs", "web", "fetch") for rid in (src.get(group) or {})} | {"github"}


def _new_id(name: str, taken: set[str]) -> str:
    """A slug the Core makes from the name, never a shipped row's id or another of hers."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = re.sub(r"^[^a-z]+", "", slug)[:28].strip("-")
    slug = slug if len(slug) >= 2 else "api"
    made, i = slug, 2
    while made in taken:
        made, i = f"{slug}-{i}", i + 1
    return made


# -- the file ----------------------------------------------------------------------------------------------------

def _read_raw(state: Path | str) -> dict:
    try:
        got = json.loads(path(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    kept = got.get("rows") if isinstance(got, dict) else None
    return kept if isinstance(kept, dict) else {}


def rows(state: Path | str) -> dict[str, dict]:
    """Every good row by id, on or off. A row that fails its checks, here or edited by hand, is not there."""
    out: dict[str, dict] = {}
    shipped = _shipped_ids()
    for rid, raw in _read_raw(state).items():
        try:
            if not isinstance(raw, dict) or not ID_RE.fullmatch(str(rid)) or rid in shipped:
                raise ApiRefused("not a row of hers")
            out[rid] = _build(rid, raw, raw.get("tested") is True, raw.get("key_host"))
        except ApiRefused:
            logger.warning("an owner API row failed its checks and is ignored")
    return out


def _write(state: Path | str, kept: dict[str, dict]) -> None:
    target = path(state)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps({"rows": kept}, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def add(state: Path | str, body: dict) -> dict:
    """Keep one new row, off until her Yes and a Test. :class:`ApiRefused` when any check fails; nothing is written."""
    with _lock:
        kept = rows(state)
        name = _name(body.get("name"))
        api_id = _new_id(name, _shipped_ids() | set(kept))
        row = _build(api_id, body)
        kept[api_id] = row
        _write(state, kept)
        return dict(row)


def edit(state: Path | str, api_id: str, body: dict) -> tuple[dict, bool]:
    """``(row, asks_again)``. Her address or key place changed: ``tested`` goes and the old Yes stops counting. Other
    fields she did not send stay. New field names mean an unread reader: ``tested`` goes, her Yes stays."""
    with _lock:
        kept = rows(state)
        old = kept[api_id]
        merged = {**old, **{k: body[k] for k in ("name", "short", "search", "n", "key_in", "key_name", "key_prefix",
                                                 "fields") if k in body}}
        if "key_in" in body and body["key_in"] != old["key_in"]:
            for part in ("key_name", "key_prefix"):      # a new place starts clean, unless she named these too
                if part not in body:
                    merged[part] = ""
        row = _build(api_id, merged, old["tested"], old["key_host"])
        changed = row_sha(row) != row_sha(old)
        if changed or row["fields"] != old["fields"]:
            row["tested"] = False
        kept[api_id] = row
        _write(state, kept)
        return dict(row), changed


def remove(state: Path | str, api_id: str) -> bool:
    with _lock:
        kept = rows(state)
        if api_id not in kept:
            return False
        del kept[api_id]
        _write(state, kept)
        return True


def key_bound(row: dict) -> bool:
    """Was her key saved for the host this row names now? A new host, or a row added again under one id, gets no key
    until she saves it again, so a key never follows a row's id somewhere new."""
    return bool(row.get("key")) and row.get("key_host") == row.get("host")


def set_key_host(state: Path | str, api_id: str, host: str = "") -> None:
    """The host her key was saved for, kept beside the row. Only the key routes call this; "" when the key is gone."""
    with _lock:
        kept = rows(state)
        if api_id in kept:
            kept[api_id]["key_host"] = host
            _write(state, kept)


def set_tested(state: Path | str, api_id: str, sha: str, tested: bool) -> None:
    """What a Test found, for the address it tested: an edit made meanwhile is never marked good."""
    with _lock:
        kept = rows(state)
        if api_id in kept and row_sha(kept[api_id]) == sha and kept[api_id]["tested"] != tested:
            kept[api_id]["tested"] = bool(tested)
            _write(state, kept)


# -- her Yes -------------------------------------------------------------------------------------------------------

def row_sha(row: dict) -> str:
    """The hash her Yes binds: the address and the key place, never a key."""
    bound = {k: row.get(k) for k in BOUND}
    return hashlib.sha256(json.dumps(bound, sort_keys=True).encode("utf-8")).hexdigest()


def card(row: dict) -> dict:
    """Her Yes card: this one host, this API's name, what is sent, and where the key goes (never its value)."""
    name, host = row["name"], row["host"]
    word = f", after the word {row['key_prefix']}," if row.get("key_prefix") else ""
    key = {"header": f"Your key stays in your vault. It goes only to {host}, in the {row['key_name']} header{word} only "
                     "for those searches.",
           "query": f"Your key stays in your vault. It goes only to {host}, in the address as {row['key_name']}, since "
                    "that is all this API takes. It is never shown or logged.",
           "none": "This place takes no key."}[row["key_in"]]
    return {"title": f"Let Toolsmith search {name} while it builds?",
            "sentence": f"While Toolsmith makes a plan, it may send search words like \"world news map\" to {host}. "
                        "Only search words go out, nothing else.",
            "lines": [key, "It works only while Toolsmith's search is on. Remove it to take this Yes back."]}


def covers(row: dict) -> dict:
    return {"hosts": [row["host"]], "phase": "build", "owner_api": {"id": row["id"], "sha": row_sha(row)},
            "card": card(row)}


def names(rec: dict, api_id: str) -> bool:
    """Is this Yes record for this row, whatever address it was given for?"""
    named = (rec.get("covers") or {}).get("owner_api")
    return isinstance(named, dict) and named.get("id") == api_id


def yes_of(row: dict, records: Any) -> dict | None:
    """Her live Yes for this row as it is now: subject, exactly its one host and its hash. Else None."""
    for rec in records or ():
        cov = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
        named = cov.get("owner_api") if isinstance(cov.get("owner_api"), dict) else {}
        if (rec.get("scope") or {}).get("target") == SEARCH_SUBJECT and cov.get("hosts") == [row["host"]] \
                and named.get("id") == row["id"] and named.get("sha") == row_sha(row):
            return rec
    return None


def for_reads(state: Path | str, records: Any) -> dict[str, dict]:
    """The rows as ``address.row_read`` takes them. ``_live``: her Yes for it stands and a Test read results. *records*
    None means her Yes can't be read: none is live. Any host a Yes names whose row is gone stays a place with no way
    in, so a Yes left behind never opens a whole host."""
    out = {rid: dict(row, header=row["key_name"], _owner=True,
                     _live=row["tested"] and yes_of(row, records) is not None)
           for rid, row in rows(state).items()}
    known = {row["host"] for row in out.values()}
    for rec in records or ():
        cov = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
        named = cov.get("hosts") if isinstance(cov.get("hosts"), list) else []
        if isinstance(cov.get("owner_api"), dict) and len(named) == 1 and named[0] not in known:
            out[f"_gone_{rec.get('consent_id')}"] = {"host": named[0], "_owner": True, "_live": False}
    return out


def public(row: dict) -> dict:
    """A row as Toolsmith's process gets it: no Core mark. ``key`` is the vault's name, never a value."""
    return {k: v for k, v in row.items() if not k.startswith("_") and k not in ("header", "key_host")}


# -- what an API says back ------------------------------------------------------------------------------------------

def _char_forms(c: str) -> str:
    code, hx = ord(c), f"{ord(c):02x}"
    forms = [re.escape(c), rf"%(?:25)*(?i:{hx})", rf"&(?:amp;)*#(?i:x)0*(?i:{hx});?", rf"&(?:amp;)*#0*{code};?",
             rf"\\+(?i:u00{hx}|x{hx})"]
    if c in _NAMED_ENTITY:
        forms.append(rf"&(?:amp;)*(?:{_NAMED_ENTITY[c]});")
    if c == "/":
        forms.append(r"\\+/")
    if c == "+":
        forms.append(" ")                              # what a place that decodes an address turns it into
    return "(?:" + "|".join(forms) + ")"


def key_pattern(key: str) -> re.Pattern:
    """*key* in every form an answer could carry it, each character on its own: plain, percent-encoded in either case
    and more than once, an HTML entity, a JSON escape left in a string. With the word before it, when there is one."""
    word = r"(?:(?i:Bearer|Token)(?:[ \t+]|%20|&nbsp;|&#32;|&#(?i:x)20;)+)?"
    return re.compile(word + "".join(_char_forms(c) for c in key))


def clean_reply(data: bytes, key: str) -> bytes:
    """A keyed row's answer as it may go on: parsed, every string walked after decoding (its keys too), each form of her
    key out, written again. Before any cut, so a name cut at 100 characters can't leave part of a key. An answer that
    is not JSON can't be walked, so it does not go on at all: b"". Only the word and the key go, never the word alone."""
    pattern = key_pattern(key)

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return pattern.sub(REMOVED, v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {walk(k): walk(x) for k, x in v.items()}
        if isinstance(v, (int, float)) and not isinstance(v, bool) and pattern.search(str(v)):
            return REMOVED
        return v
    try:
        return json.dumps(walk(json.loads(data.decode("utf-8", errors="replace"))), ensure_ascii=False).encode("utf-8", "replace")
    except (ValueError, RecursionError):
        return b""


def shown_rows(found: Any) -> list[dict]:
    """A Test's rows through the card's own screen (``knowledge.shown_look``, ``text_rules.url_is_plain``): a flagged name
    or an odd address loses the row, and flagged words lose only the description."""
    from . import knowledge
    from ..text_rules import url_is_plain
    out = []
    for r in found if isinstance(found, list) else []:
        name, url = knowledge.shown_look(r.get("name")), str(r.get("url") or "")
        if name and url.startswith("https://") and url_is_plain(url) and len(url) <= 300 \
                and not has_hidden(urllib.parse.unquote(url)):
            out.append({"name": name, "url": url, "description": knowledge.shown_look(r.get("description"))})
    return out


__all__ = ["ApiRefused", "FILE", "ID_RE", "KEY_PREFIX", "KEY_PREFIXES", "MOVED_KEY_LINE", "add", "card", "check_address",
           "clean_reply", "covers", "edit", "for_reads", "key_bound", "key_name", "key_pattern", "names", "path", "public",
           "remove", "row_sha", "rows", "set_key_host", "set_tested", "shown_rows", "yes_of"]
