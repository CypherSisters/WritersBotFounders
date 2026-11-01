"""The addresses the Core sends for Toolsmith (``/web``), in one plain form (lane I15 re-check).

Every address is put in its canonical form BEFORE any check, and only that form is sent; one that has no
plain form is refused (nothing is sent):

- https only, no user part; the host lower-cased, one trailing dot dropped, letters, digits, dots and hyphens;
- the path percent-decoded once and refused if it still holds ``%``, a ``;`` parameter, a control character or
  a backslash; nothing is collapsed: ``//``, ``/./``, ``/../`` or an empty segment is refused (a trailing ``/``
  is allowed off the GitHub hosts only, where docs pages use it);
- the query parsed and written again; the fragment dropped.

:func:`searches_of` gives what the Core's screen must pass: on the GitHub hosts the search words of every ``q``,
the one part made from the owner's own words, and on another search place the words of its row's search address.
Repository names and README paths come from the place's own answers and are never screened for names.

:func:`row_read` says what an address is on the other search places (``sources.json``, rows ``shelves``, ``catalogs``
and ``web``, each host also on a code list in ``policy``). Only an address shaped like its row reaches them from
Toolsmith's process: its search, its whole lists, or a project's raw file. Anything else there is refused, so no word
can go in another query key or another path. Data, not a table here; a row whose host no code list names is never
reached (:func:`off_the_lists`).

:func:`public_form` is the same plain form for a cloud build's read of any public page (D121): the default port only.

The one other way in is a row the owner added herself (``owner_apis``): the service passes those as *owner*, each
marked ``_owner`` and ``_live``. They never come from ``sources.json``: :func:`rows` clears that mark on every shipped row.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from ..policy import KNOWLEDGE_HOSTS, SEARCH_HOSTS, WEB_KEY_HOSTS, knowledge_host_ok

#: The hosts whose searches (``q``) the Core screens; every address keeps the canonical rules.
SCREENED_HOSTS = frozenset({"api.github.com", "raw.githubusercontent.com", "github.com"})
#: A ``list`` row's whole file, at most (the Hermes index); every other read stays at 3,000,000 bytes.
LIST_MAX_BYTES = 48_000_000
#: The only values of GitHub's ``in:`` qualifier that are not search words.
IN_VALUES = frozenset({"name", "description", "topics", "readme"})
#: GitHub's other qualifiers Toolsmith's own search uses; any other ``word:value`` is a search word.
_QUALIFIER = re.compile(r"stars:>=?\d{1,7}|archived:(?:true|false)|fork:(?:true|false)", re.I)
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")
_PATH_SAFE = "/-._~!$&'()*+,=:@"
_QUERY_SAFE = ":,<>="
_PATH_PART = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_HEADER_NAME = re.compile(r"[A-Za-z][A-Za-z-]{0,40}")


@dataclass(frozen=True)
class RowRead:
    """What an address on a search place is. ``kind``: search (``words``: what the screen must pass), list (a row's
    exact URL, ``max_bytes`` its own), raw (a project's file) or refused."""
    kind: str
    row: str = ""
    words: tuple[str, ...] = ()
    max_bytes: int = 0
    keyed: bool = False


REFUSED = RowRead("refused")


def canonical(url: Any) -> str | None:
    """*url* in its one plain form, or None when it has none (then nothing is sent)."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or "@" in parts.netloc or not parts.hostname:
        return None
    host = parts.hostname.lower()
    host = host[:-1] if host.endswith(".") else host
    if not _HOST.fullmatch(host):
        return None
    raw = parts.path or "/"
    if ";" in raw:
        return None
    try:
        path = urllib.parse.unquote(raw, errors="strict")
    except UnicodeDecodeError:
        return None
    if "%" in path or ";" in path or "\\" in path or not path.startswith("/") \
            or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        return None
    if path != "/":
        segs = path.split("/")[1:]
        if host not in SCREENED_HOSTS and segs[-1] == "":
            segs = segs[:-1]                           # a docs page's trailing slash, off the GitHub hosts
        if any(s in ("", ".", "..") for s in segs):
            return None
    try:
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True, errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    from ..text_rules import has_hidden
    if has_hidden(path) or any(has_hidden(k) or has_hidden(v) for k, v in pairs):
        return None                                    # a zero-width space or soft hyphen, even %-encoded
    query = urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote_plus, safe=_QUERY_SAFE)
    netloc = host + (f":{port}" if port is not None else "")
    return urllib.parse.urlunsplit(("https", netloc, urllib.parse.quote(path, safe=_PATH_SAFE), query, ""))


def public_form(url: Any) -> str | None:
    """*url* as a public read sends it (D121), or None: :func:`canonical`, on the default port only. A link's trailing
    ``/`` on a GitHub host goes first: :func:`canonical` keeps those paths exact for their keys, and a read carries none."""
    text = str(url or "")
    plain = canonical(text)
    if plain is None:
        try:
            parts = urllib.parse.urlsplit(text)
            if (parts.hostname or "").lower() in SCREENED_HOSTS and parts.path.endswith("/"):
                plain = canonical(urllib.parse.urlunsplit(parts._replace(path=parts.path.rstrip("/"))))
        except ValueError:
            return None
    if plain is None:
        return None
    parts = urllib.parse.urlsplit(plain)
    try:
        if parts.port not in (None, 443):
            return None
    except ValueError:
        return None
    return urllib.parse.urlunsplit(parts._replace(netloc=parts.hostname or ""))


def search_words(value: str) -> str:
    """A GitHub ``q`` value's search words: every word but Toolsmith's own qualifiers, and any ``in:`` value
    other than :data:`IN_VALUES` ("in:name,marisol" gives "marisol")."""
    free: list[str] = []
    for w in str(value or "").split():
        if w.lower().startswith("in:"):
            free += [v for v in w[3:].split(",") if v and v.lower() not in IN_VALUES]
        elif not _QUALIFIER.fullmatch(w):
            free.append(w)
    return " ".join(free)


def rows(src: dict | None = None) -> dict[str, dict]:
    """Every ``shelves``, ``catalogs`` and ``web`` row by id, each with ``_web`` when it is a web row."""
    if src is None:
        from ..toolsmith.find import sources
        src = sources()
    out: dict[str, dict] = {}
    for group in ("shelves", "catalogs", "web"):
        for rid, row in (src.get(group) or {}).items():
            if isinstance(row, dict) and isinstance(row.get("host"), str):
                out[str(rid)] = dict(row, _web=group == "web", _owner=False)
    return out


def _known(rid: str, row: dict) -> bool:
    """Is this row's host on a code list? A web row must also have this code's own host, vault key name and a plain
    header name, so a bundle's row can never borrow a key or send one somewhere else. A row she added herself counts
    only while its Yes is live, and never on a host of the fixed search list."""
    if row.get("_owner"):
        return row.get("_live") is True and row["host"] not in SEARCH_HOSTS
    if row.get("_web"):
        return WEB_KEY_HOSTS.get(rid) == row["host"] and row.get("key") == f"toolsmith.{rid}" \
            and _HEADER_NAME.fullmatch(str(row.get("header") or "")) is not None
    return row["host"] in SEARCH_HOSTS


def key_rows(src: dict | None = None) -> dict[str, dict]:
    """The web rows this code knows: each is off until she links her own key and says Yes to its card."""
    return {rid: row for rid, row in rows(src).items() if row.get("_web") and _known(rid, row)}


def off_the_lists(src: dict | None = None) -> list[str]:
    """Every row of ``sources.json`` naming a host no code list names ("<row id>: <host>"): none is ever reached."""
    if src is None:
        from ..toolsmith.find import sources
        src = sources()
    out = [f"{rid}: {row['host']}" for rid, row in rows(src).items() if not _known(rid, row)]
    for rid, row in (src.get("fetch") or {}).items():
        out += [f"{rid}: {h}" for h in [row.get("host"), *(row.get("file_hosts") or [])] if h not in KNOWLEDGE_HOSTS]
    for rid, row in (src.get("catalogs") or {}).items():
        out += [f"{rid}: {row[k]}" for k in ("meta_host", "download_host")
                if row.get(k) and not knowledge_host_ok(row[k])]
    return out


def _search_words(row: dict, path: str, query: str) -> tuple[str, ...] | None:
    """The words of an address shaped like *row*'s ``search``, or None: the same path, exactly the template's query
    keys, each once, each with the template's own value, the row's own ``n`` for ``{n}``, and the words in the row's
    one place for them (``words_param``, or the ``{q}`` path segment of a ``words_in_path`` row)."""
    template = urllib.parse.urlsplit(str(row.get("search") or ""))
    if template.scheme != "https" or template.netloc != row["host"]:
        return None
    words: list[str] = []
    want_path, got_path = template.path.split("/"), urllib.parse.unquote(path).split("/")
    if len(want_path) != len(got_path):
        return None
    for want, got in zip(want_path, got_path):
        if want == "{q}" and row.get("words_in_path") is True:
            words.append(got)
        elif want != got:
            return None
    want_query = urllib.parse.parse_qsl(template.query, keep_blank_values=True)
    got_query = urllib.parse.parse_qsl(query, keep_blank_values=True)
    if sorted(k for k, _v in want_query) != sorted(k for k, _v in got_query) \
            or len({k for k, _v in got_query}) != len(got_query):
        return None
    given = dict(got_query)
    for key, want in want_query:
        if want == "{q}" and key == row.get("words_param"):
            words.append(given[key])
        elif want == "{n}":
            if given[key] != str(row.get("n")):
                return None
        elif want != given[key]:
            return None
    return tuple(words) if len(words) == 1 and words[0].strip() else None


def _is_raw(row: dict, path: str, query: str) -> bool:
    """*path* is a file of one project, shaped like *row*'s ``raw`` (Codeberg's ``/raw/branch/``), with no query."""
    template = urllib.parse.urlsplit(str(row.get("raw") or ""))
    if query or template.scheme != "https" or template.netloc != row["host"]:
        return False
    want = template.path.replace("{full_name}", "{owner}/{repo}").split("/")
    got = path.split("/")
    return len(want) == len(got) and all(
        _PATH_PART.fullmatch(g) is not None if w.startswith("{") and w.endswith("}") else w == g
        for w, g in zip(want, got))


def row_read(url: str, src: dict | None = None, owner: dict | None = None, prefer: str = "") -> RowRead | None:
    """What canonical *url* is on the search places. None: GitHub's rules (a ``list`` row there still says so) or a
    host that is no search place (the Bridge decides). ``REFUSED``: a search place's host, not shaped like its row.
    *owner*: the rows she added (``owner_apis.for_reads``). *prefer*: the row asked for, when two rows on one host
    have the same search (a shipped row and her own), so each stays on its own key."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    all_rows = {**rows(src), **(owner or {})}
    here = {rid: row for rid, row in all_rows.items() if row["host"] == host}
    usable = {rid: row for rid, row in here.items() if _known(rid, row)}
    for rid, row in usable.items():
        if url in {canonical(u) for u in row.get("list") or []}:
            return RowRead("list", rid, max_bytes=max(0, min(int(row.get("max_bytes") or 0), LIST_MAX_BYTES)))
    if host in SCREENED_HOSTS:
        return None
    if not here and host not in SEARCH_HOSTS and host not in WEB_KEY_HOSTS.values():
        return None
    if parts.netloc != host:
        return REFUSED                                 # another port: not the host the row and the Bridge name
    for rid, row in sorted(usable.items(), key=lambda item: item[0] != prefer):
        words = _search_words(row, parts.path, parts.query)
        if words is not None:
            return RowRead("search", rid, words=words, keyed=bool(row.get("_web") or (row.get("_owner") and row.get("key"))))
        if _is_raw(row, parts.path, parts.query):
            return RowRead("raw", rid)
    return REFUSED


def searches_of(url: str, owner: dict | None = None) -> list[str]:
    """The words the Core's screen must pass in a canonical *url*. On a search place, its row's words. Else the search
    words of every ``q`` (any case): the only part of a GitHub address made from the owner's own words (the goal,
    the job names, the brain's queries), so the only part screened."""
    parts = urllib.parse.urlsplit(url)
    if parts.hostname not in SCREENED_HOSTS:
        read = row_read(url, owner=owner)
        if read is not None:
            return list(read.words)
    out = []
    for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() == "q":
            words = search_words(value)
            if words:
                out.append(words)
    return out


__all__ = ["IN_VALUES", "LIST_MAX_BYTES", "REFUSED", "RowRead", "SCREENED_HOSTS", "canonical", "key_rows",
           "off_the_lists", "public_form", "row_read", "rows", "search_words", "searches_of"]
