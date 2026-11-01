"""The PuffPAI Store: the indexes the Core installs from (D6, D89).

A store is one ``index.json`` (``puffpai-store-index/1``): rows that each name a GitHub repo, a folder in it,
one pinned commit and a tier, plus a ``yanked`` list of (id, commit, why). The Core reads:

- **The official store**, :data:`OFFICIAL_SOURCE`, a constant. No setting removes or replaces it.
- **The owner's own sources**, in ``<bot>\\settings\\store.json``: more https ``index.json`` addresses, or a
  folder or file on this computer holding one (her own store, or offline work). She adds and removes them.

Fetching an index is a Core action she asked for, made the way ``PackageManager._clone`` reaches git: straight
from the Core process (not through the Bridge proxy, which is for packages). A plain GET, at most
:data:`MAX_INDEX_BYTES`, :data:`FETCH_TIMEOUT_S` per read, redirects only to https. The whole file must have
the index shape; then each row is checked with the rules of the store's own ``scripts/store.py``
(``row_problems``). A bad row is dropped with its reason logged, never fatal. The last good copy of each source
is kept in ``<bot>\\settings\\store-cache``, so the store still lists offline, and it then says the copy is old.

**Nothing leaves the computer without the owner's consent (D96).** An index is fetched only from something she
did: opening the store panel or its refresh, ``puffpai store``, or an add. What runs by itself (Home's question,
Toolsmith's suggestions while it plans) reads the saved copies only: :meth:`Store.saved_listing` and
:meth:`Store.suggestions` never touch the network. A row's ``icon`` is an https address the Desktop never fetches.

**Only the official source can make a package Verified.** A row's tier as the Core records it
(``origin``) is ``store:verified`` only when the row came from the built-in official source AND says
``verified``. Every other row, a second source that says ``verified`` included, is ``store:community``, and
Drawbridge's and Steward's community rules apply to it.

Installing is ``PackageManager.add("store:<id>")``: the same path as any package (Drawbridge, staging, the one
Yes card, the Registry). This module only lists and resolves; it never fetches package code.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .. import jsonfile

logger = logging.getLogger(__name__)

#: The official PuffPAI store. A constant: no setting can remove or replace it (D89(a)).
OFFICIAL_SOURCE = "https://raw.githubusercontent.com/CypherSisters/puffpai-store/main/index.json"
#: What a Registry row records as the source of a package from the official store (never its address, so a
#: setting that names the same address is never the official store).
OFFICIAL = "official"
INDEX_SCHEMA = "puffpai-store-index/1"
MAX_INDEX_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_S = 15
#: The most one whole index download may take, however slowly it comes.
FETCH_TOTAL_S = 60
#: How long a fetched index is used again without asking the source (one look at the store, then an add).
FRESH_S = 60.0
MAX_SOURCES = 20
MAX_ROWS = 2000
MAX_SOURCE_TEXT = 500

#: Tests and offline work only: a repo address to a git folder on this computer, used in its place by the
#: Package Manager's fetch. Empty in a real Core; never read from a setting or a route.
REPO_MIRRORS: dict[str, str] = {}

#: The rules of the store's own ``scripts/store.py`` (``row_problems``). Always used with ``fullmatch``, and each
#: ends in ``\Z``: ``$`` would also match before a last newline.
ID = re.compile(r"[a-z][a-z0-9-]{1,39}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
#: Every package kind but ``wizard`` (setup is part of BuildBot, never a store package).
KINDS = frozenset({"app", "tool", "skill", "page", "ui", "avatar", "look", "agent", "knowledge", "bundle", "provider"})
TIERS = ("community", "verified")
#: One folder name in a row's ``path``: letters, digits, ``_ . -``, starting with a letter, digit or ``_``. Stricter
#: than store.py (which only refuses ``..``, a leading slash and a backslash): no drive letter, colon or space.
PATH_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}\Z")
#: Names Windows keeps for devices, with or without an extension (``nul.txt`` is the device too).
RESERVED = frozenset({"con", "prn", "aux", "nul"} | {f"com{n}" for n in range(1, 10)}
                     | {f"lpt{n}" for n in range(1, 10)})

#: What a row may say about its package's tools (D96), kept as plain text. Anything else in ``abilities`` is dropped.
TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
MAX_TOOLS = 30
MAX_TOOL_TEXT = 300
MAX_EXAMPLES = 3
MAX_EXAMPLE_TEXT = 200
#: The kinds a plan may suggest from the official store (D96).
SUGGESTED = ("app", "tool")

#: Plain text fields of a row, and the most of each that is kept.
_TEXT = {"name": 80, "description": 600, "version": 40, "author": 120, "license": 60, "verified_by": 120}
#: Unicode categories that show nothing or can hide text (D96): control, format, surrogate, private use, unassigned.
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


class StoreError(ValueError):
    """A store call that can't go ahead. ``problems`` holds plain sentences; ``status`` the HTTP answer."""

    def __init__(self, problems: list[str] | str, status: int = 400) -> None:
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        self.status = status
        super().__init__("; ".join(self.problems))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hidden(ch: str) -> bool:
    """A character that shows nothing or can hide text: by Unicode category, plus the variation selectors and the
    combining grapheme joiner. Letters, accents and emoji that work without them stay."""
    n = ord(ch)
    return unicodedata.category(ch) in _HIDDEN_CATEGORIES or n == 0x34F or 0xFE00 <= n <= 0xFE0F or 0xE0100 <= n <= 0xE01EF


def has_hidden(text: str) -> bool:
    return any(_hidden(c) for c in text)


def plain(value: Any, most: int) -> str:
    """*value* as one line of plain text, at most *most* characters. A hidden character (:func:`_hidden`) is dropped,
    never turned into a space, so a word split by one reads as the word for every screen after this one. Lines and
    tabs become one space. A very long value is cut first: hidden padding can't make this slow."""
    if not isinstance(value, str):
        return ""
    kept = "".join(c for c in value[:most * 8] if c.isspace() or not _hidden(c))
    return " ".join(unicodedata.normalize("NFC", kept).split())[:most]


# -- rows ------------------------------------------------------------------------------------------------------

def path_problem(path: Any) -> str | None:
    """Why a row's ``path`` is not a plain folder inside the repo, or None. Empty is the repo's root."""
    if not isinstance(path, str):
        return "path must be a folder inside the repo, like 'my-app' (or empty for the repo root)"
    if path == "":
        return None
    if path.startswith(("/", "\\")) or "\\" in path or ":" in path or len(path) > 300:
        return "path must be a folder inside the repo, like 'my-app' (or empty for the repo root)"
    parts = path.split("/")
    if any(p in ("", ".", "..") or not PATH_PART.fullmatch(p) for p in parts):
        return "path must be a folder inside the repo, like 'my-app' (or empty for the repo root)"
    # Windows drops a last "." or space (so "app." opens "app") and keeps device names: refused, so the folder
    # read is always the one the card names.
    if any(p.endswith((".", " ")) or p.split(".", 1)[0].lower() in RESERVED for p in parts):
        return "path must be a folder inside the repo, like 'my-app' (or empty for the repo root)"
    if ".." in PurePosixPath(path).parts:
        return "path must be a folder inside the repo, like 'my-app' (or empty for the repo root)"
    return None


def row_problems(row: Any) -> list[str]:
    """The store's row rules (its ``scripts/store.py`` ``row_problems``) for one row of an ``index.json``."""
    if not isinstance(row, dict):
        return ["the row is not a JSON object"]
    out = []
    if not isinstance(row.get("id"), str) or not ID.fullmatch(row["id"]):
        out.append("id must be 2 to 40 characters: lower-case letters, digits or '-', starting with a letter")
    if not isinstance(row.get("kind"), str) or row["kind"] not in KINDS:   # a list here must be a problem, not a crash
        out.append(f"kind must be one of {', '.join(sorted(KINDS))}")
    if not isinstance(row.get("repo"), str) or not REPO.fullmatch(row["repo"]):
        out.append("repo must be https://github.com/<owner>/<repo>")
    problem = path_problem(row.get("path", ""))
    if problem:
        out.append(problem)
    if not isinstance(row.get("commit"), str) or not SHA.fullmatch(row["commit"]):
        out.append("commit must be a full 40-character commit id")
    if not isinstance(row.get("tier"), str) or row["tier"] not in TIERS:
        out.append("tier must be community or verified")
    if row.get("tier") == "verified" and "verified_commit" in row and row.get("verified_commit") != row.get("commit"):
        out.append("a verified row's verified_commit must equal its commit (a new commit needs a new review)")
    return out


def clean_abilities(raw: Any) -> dict:
    """A row's ``abilities``, ``{tools: {name: description}, examples: {name: [ask, ...]}}``, within strict limits
    (D96): plain text, bounded counts and lengths, tool names of letters, digits, ``_`` and ``-``. A tool with no
    description, an example of no kept tool, or anything of another shape is dropped. ``{}`` when no tool is left.
    Never raises, so a bad one can't break its row."""
    told = raw.get("tools") if isinstance(raw, dict) else None
    tools: dict[str, str] = {}
    for name, words in (told.items() if isinstance(told, dict) else ()):
        said = plain(words, MAX_TOOL_TEXT)
        if said and TOOL_NAME.fullmatch(name):
            tools[name] = said
            if len(tools) >= MAX_TOOLS:
                break
    if not tools:
        return {}
    given = raw.get("examples") if isinstance(raw.get("examples"), dict) else {}
    examples: dict[str, list[str]] = {}
    for name in tools:
        asks = given.get(name) if isinstance(given.get(name), list) else []
        kept = [s for s in (plain(a, MAX_EXAMPLE_TEXT) for a in asks) if s][:MAX_EXAMPLES]
        if kept:
            examples[name] = kept
    return {"tools": tools, **({"examples": examples} if examples else {})}


def builtin_look(row: dict) -> bool:
    """A row that would be a second copy of a built-in look (D90: never two Cleans): its id is the look's, or
    ``look-<the look's id>`` (the name the store's Clean had), or it is a look with the same name."""
    from .. import bot
    id_, name = str(row.get("id") or ""), str(row.get("name") or "").strip().lower()
    return any(id_ in (t["id"], f"look-{t['id']}") or (row.get("kind") == "look" and name == t["name"].lower())
               for t in bot.THEMES)


def clean_row(row: dict) -> dict:
    """The fields of a checked row the Core keeps, as plain text (anything else is left out)."""
    out = {"id": row["id"], "kind": row["kind"], "repo": row["repo"], "path": row.get("path", ""),
           "commit": row["commit"], "tier": row["tier"]}
    for key, most in _TEXT.items():
        if isinstance(row.get(key), str):
            out[key] = plain(row[key], most)
    icon = row.get("icon")
    if isinstance(icon, str) and icon.startswith("https://") and len(icon) <= 500 and not has_hidden(icon) \
            and " " not in icon:
        out["icon"] = icon
    levels = row.get("levels")
    if isinstance(levels, list):
        out["levels"] = [n for n in levels if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 3][:3]
    abilities = clean_abilities(row.get("abilities"))
    if abilities:
        out["abilities"] = abilities
    return out


def parse_index(data: Any, source: str = "") -> tuple[dict, list[str]]:
    """``(index, dropped)``: the checked index and one plain reason per row or yanked entry left out.

    Raises :class:`StoreError` when the whole file is not a store index."""
    if not isinstance(data, dict) or data.get("schema") != INDEX_SCHEMA or not isinstance(data.get("packages"), list):
        raise StoreError(f"It is not a PuffPAI store index (it needs \"schema\": \"{INDEX_SCHEMA}\" and a "
                         "\"packages\" list).")
    dropped: list[str] = []
    rows: list[dict] = []
    seen: set[str] = set()
    for i, row in enumerate(data["packages"][:MAX_ROWS]):
        name = row.get("id") if isinstance(row, dict) and isinstance(row.get("id"), str) else f"row {i + 1}"
        name = plain(name, 40) or f"row {i + 1}"
        problems = row_problems(row)
        if not problems and row["id"] in seen:
            problems = ["the same id is listed twice in this index; the first one is kept"]
        if problems:
            dropped.append(f"{name}: {problems[0]}")
            continue
        seen.add(row["id"])
        rows.append(clean_row(row))
    if len(data["packages"]) > MAX_ROWS:
        dropped.append(f"rows past the first {MAX_ROWS} were left out")
    yanked: list[dict] = []
    raw_yanked = data.get("yanked", [])
    for i, y in enumerate(raw_yanked if isinstance(raw_yanked, list) else []):
        if (isinstance(y, dict) and isinstance(y.get("id"), str) and ID.fullmatch(y["id"])
                and isinstance(y.get("commit"), str) and SHA.fullmatch(y["commit"]) and isinstance(y.get("why"), str)):
            yanked.append({"id": y["id"], "commit": y["commit"], "why": plain(y["why"], 300) or "no reason given"})
        else:
            dropped.append(f"yanked entry {i + 1}: needs id, a 40-character commit and why")
    if not isinstance(raw_yanked, list):
        dropped.append("yanked must be a list")
    for line in dropped:
        logger.info("store index %s: left out %s", source or "?", line)
    return {"schema": INDEX_SCHEMA, "name": plain(data.get("name"), 80), "packages": rows, "yanked": yanked}, dropped


def recorded_tier(official: bool, row: dict) -> str:
    """The origin the Core records for *row* (D89(d)): ``store:verified`` only from the official source."""
    return "store:verified" if official and row.get("tier") == "verified" else "store:community"


# -- sources ---------------------------------------------------------------------------------------------------

def normal_source(text: Any) -> tuple[str, str]:
    """``(kind, source)`` for something the owner gave as a store source: ``("url", address)`` or
    ``("folder", path)`` (a folder holding ``index.json``, or the file itself). Raises :class:`StoreError`."""
    if not isinstance(text, str) or not text.strip():
        raise StoreError("Give a store's index address (https://...) or a folder on this computer.")
    text = text.strip()
    if len(text) > MAX_SOURCE_TEXT or has_hidden(text):
        raise StoreError("That store source is too long or has characters that can't be in one.")
    low = text.lower()
    if low.startswith("https://"):
        parts = urllib.parse.urlsplit(text)
        if not parts.hostname or "@" in parts.netloc or any(c.isspace() for c in text):
            raise StoreError("Give a plain https address, like https://example.com/index.json (no name or "
                             "password in it).")
        return "url", text
    if "://" in low or low.startswith(("http:", "file:")):
        raise StoreError("A store address must start with https:// (plain http and other kinds are not used).")
    if text.startswith(("\\\\", "//")):
        raise StoreError("A store folder must be on this computer, not a network share.")
    path = Path(text)
    if not path.is_absolute():
        raise StoreError("Give the full path of the store's folder or index file.")
    return "folder", str(path)


def _same(a: str, b: str) -> bool:
    """The same source: an address compared without case, a path as the computer compares paths."""
    if a.lower().startswith("https://") or b.lower().startswith("https://"):
        return a.lower() == b.lower()
    return os.path.normcase(str(Path(a))) == os.path.normcase(str(Path(b)))


class _HttpsRedirectsOnly(urllib.request.HTTPRedirectHandler):
    """A store index may move, but only to another https address (never down to plain http)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib's own signature
        if not str(newurl).lower().startswith("https://"):
            return None                       # urllib then raises the 30x as an error
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Unreachable(OSError):
    """A source that could not be read; its one argument is the plain reason."""


def http_get(url: str, timeout: float = FETCH_TIMEOUT_S, most: int = MAX_INDEX_BYTES) -> bytes:
    """A plain GET of *url*: at most *most* bytes. Raises :class:`Unreachable` with a plain reason."""
    import http.client
    opener = urllib.request.build_opener(_HttpsRedirectsOnly())
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "PuffPAI-Core"})
    try:
        with opener.open(req, timeout=timeout) as resp:      # noqa: S310 - https only (normal_source)
            if getattr(resp, "status", 200) != 200:
                raise Unreachable(f"it answered {resp.status}")
            if not str(resp.geturl()).lower().startswith("https://"):
                raise Unreachable("it moved to an address that is not https")
            data, end = b"", time.monotonic() + FETCH_TOTAL_S
            while len(data) <= most:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                data += chunk
                if time.monotonic() > end:
                    raise Unreachable(f"it took over {FETCH_TOTAL_S} seconds to send its index")
    except Unreachable:
        raise
    except urllib.error.HTTPError as exc:
        raise Unreachable(f"it answered {exc.code}") from None
    except urllib.error.URLError:
        raise Unreachable("it could not be reached (no network, or the address is wrong)") from None
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise Unreachable(f"it could not be reached ({type(exc).__name__})") from None
    if len(data) > most:
        raise Unreachable(f"its index is over {most // (1024 * 1024)} MB")
    return data


def read_local(source: str, most: int = MAX_INDEX_BYTES) -> bytes:
    """The bytes of a local source: ``index.json`` in the folder, or the file itself. Raises :class:`Unreachable`."""
    path = Path(source)
    try:
        if path.is_dir():
            path = path / "index.json"
        if not path.is_file():
            raise Unreachable("there is no index.json there")
        if path.stat().st_size > most:
            raise Unreachable(f"its index is over {most // (1024 * 1024)} MB")
        with open(path, "rb") as fh:
            data = fh.read(most + 1)
    except Unreachable:
        raise
    except OSError as exc:
        raise Unreachable(f"it could not be read ({exc.strerror or type(exc).__name__})") from None
    if len(data) > most:
        raise Unreachable(f"its index is over {most // (1024 * 1024)} MB")
    return data


class Store:
    """The store sources of one bot, their indexes (fetched, or the last good copy) and the rows in them."""

    def __init__(self, state: Path | str, registry: Any = None) -> None:
        self.state = Path(state)
        self.registry = registry
        self.settings = self.state / "settings" / "store.json"
        self.cache_dir = self.state / "settings" / "store-cache"
        self.fresh_s = FRESH_S
        self._mem: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    # -- sources ---------------------------------------------------------------
    def owner_sources(self) -> list[str]:
        """The owner's own sources, in order (a hand-edited entry that is not a source is skipped)."""
        got = jsonfile.read(self.settings, {})
        out: list[str] = []
        for entry in (got.get("sources") if isinstance(got, dict) and isinstance(got.get("sources"), list) else []):
            text = entry.get("source") if isinstance(entry, dict) else entry
            try:
                _kind, src = normal_source(text)
            except StoreError:
                continue
            if _same(src, OFFICIAL_SOURCE) or any(_same(src, s) for s in out):
                continue
            out.append(src)
        return out[:MAX_SOURCES]

    def sources(self) -> list[dict]:
        """Every source in the order rows are looked up: the official store first, then hers."""
        out = [{"source": OFFICIAL_SOURCE, "key": OFFICIAL, "official": True, "removable": False,
                "line": "The official PuffPAI store. Always on; it is the only source whose Verified packages "
                        "install as Verified."}]
        for src in self.owner_sources():
            out.append({"source": src, "key": src, "official": False, "removable": True,
                        "line": "Your own source. Everything from it installs as Community, whatever it says."})
        return out

    def add_source(self, text: Any) -> dict:
        kind, src = normal_source(text)
        if _same(src, OFFICIAL_SOURCE):
            raise StoreError("That is the official PuffPAI store, which is always on.", 409)
        if kind == "folder":
            p = Path(src)
            if not (p.is_file() or (p / "index.json").is_file()):
                raise StoreError(f"There is no index.json at {src}.", 404)
        with jsonfile.LOCK:
            have = self.owner_sources()
            if any(_same(src, s) for s in have):
                raise StoreError("That store source is already added.", 409)
            if len(have) >= MAX_SOURCES:
                raise StoreError(f"At most {MAX_SOURCES} store sources can be added. Remove one first.", 409)
            jsonfile.write(self.settings, {"sources": [{"source": s} for s in have + [src]]})
        return {"added": True, "source": src,
                "line": "Added. Packages from it install as Community, and each still needs your Yes."}

    def remove_source(self, text: Any) -> dict:
        try:
            _kind, src = normal_source(text)
        except StoreError:
            src = str(text or "").strip()
        if src and _same(src, OFFICIAL_SOURCE):
            raise StoreError("The official PuffPAI store can't be removed.", 409)
        with jsonfile.LOCK:
            have = self.owner_sources()
            keep = [s for s in have if not _same(s, src)]
            if len(keep) == len(have):
                raise StoreError("That store source is not one you added.", 404)
            jsonfile.write(self.settings, {"sources": [{"source": s} for s in keep]})
        gone = next(s for s in have if _same(s, src))
        self._cache_file(gone).unlink(missing_ok=True)
        self._mem.pop(gone, None)
        return {"removed": True, "source": gone,
                "line": "Removed. Packages you installed from it stay; they just get no updates from it."}

    def entry_for(self, key: str) -> dict | None:
        """The source a Registry row recorded (``official`` or her source), or None when it was removed."""
        return next((s for s in self.sources() if s["key"] == key or (not s["official"] and _same(s["key"], key))),
                    None)

    # -- indexes ---------------------------------------------------------------
    def _cache_file(self, source: str) -> Path:
        return self.cache_dir / (hashlib.sha256(source.encode("utf-8")).hexdigest()[:24] + ".json")

    def _fetch(self, source: str) -> bytes:
        if source.lower().startswith("https://"):
            return http_get(source)
        return read_local(source)

    def index(self, entry: dict, fresh: bool = False) -> dict:
        """One source's index: ``{source, key, official, ok, stale, fetched_at, line, packages, yanked, dropped}``.

        Fetched (or the copy fetched in the last :attr:`fresh_s` seconds); on any failure the last good copy,
        marked ``stale`` with a plain line; with none, ``ok`` false and no rows."""
        src = entry["source"]
        with self._lock:
            hit = self._mem.get(src)
            if hit is not None and not fresh and time.monotonic() - hit[0] < self.fresh_s:
                return dict(hit[1])
        base = {"source": src, "key": entry["key"], "official": entry["official"]}
        try:
            raw = self._fetch(src)
            try:
                data = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise StoreError("Its index is not valid JSON.") from None
            index, dropped = parse_index(data, src)
        except Exception as exc:  # noqa: BLE001 - one source's bad data can never take the others down
            if isinstance(exc, StoreError):
                reason = exc.problems[0]
            elif isinstance(exc, OSError):
                reason = str(exc) or type(exc).__name__
            else:
                logger.warning("store index %s could not be read: %s", src, type(exc).__name__)
                reason = "its index could not be read"
            out = self._from_cache(base, reason)
            with self._lock:                  # a source that failed is not asked again on every look either
                self._mem[src] = (time.monotonic(), out)
            return dict(out)
        fetched = _now()
        try:
            jsonfile.write(self._cache_file(src), {"source": src, "fetched_at": fetched, "index": index})
        except (OSError, ValueError, TypeError) as exc:     # a copy not saved: this read still counts
            logger.warning("store cache for %s not written: %s", src, type(exc).__name__)
            cache = self._cache_file(src)
            with contextlib.suppress(OSError):
                cache.with_name(cache.name + ".tmp").unlink(missing_ok=True)     # jsonfile.write leaves it on a bad value
        out = {**base, "ok": True, "stale": False, "fetched_at": fetched, "packages": index["packages"],
               "yanked": index["yanked"], "dropped": dropped, "name": index["name"],
               "line": f"{len(index['packages'])} packages." + (f" {len(dropped)} left out." if dropped else "")}
        with self._lock:
            self._mem[src] = (time.monotonic(), out)
        return dict(out)

    def _saved(self, source: str) -> tuple[dict, list[str], str] | None:
        """``(index, dropped, when)``: the last good copy of *source*, checked again; None when there is none.
        Reads one file: never the network."""
        try:
            saved = jsonfile.read(self._cache_file(source), {})
            if not isinstance(saved, dict) or saved.get("source") != source:
                return None
            index, dropped = parse_index(saved.get("index"), source)
        except Exception:  # noqa: BLE001 - RecursionError, a broken file: a copy that can't be read is no copy
            return None
        return index, dropped, plain(saved.get("fetched_at"), 40) or "an earlier time"

    def saved_index(self, entry: dict) -> dict:
        """One source's saved copy, in :meth:`index`'s shape, with no fetch (D96)."""
        base = {"source": entry["source"], "key": entry["key"], "official": entry["official"]}
        got = self._saved(entry["source"])
        if got is None:
            return {**base, "ok": False, "stale": True, "fetched_at": None, "packages": [], "yanked": [],
                    "dropped": [], "name": "", "line": "There is no saved copy yet, so nothing from it is listed."}
        index, dropped, when = got
        return {**base, "ok": True, "stale": True, "fetched_at": when, "packages": index["packages"],
                "yanked": index["yanked"], "dropped": dropped, "name": index["name"],
                "line": f"This is the copy saved {when}, which may be out of date."}

    def _from_cache(self, base: dict, reason: str) -> dict:
        reason = reason.rstrip(".")
        where = "the official PuffPAI store" if base["official"] else base["source"]
        got = self._saved(base["source"])
        if got is None:
            return {**base, "ok": False, "stale": True, "fetched_at": None, "packages": [], "yanked": [],
                    "dropped": [], "name": "", "error": reason,
                    "line": f"Could not reach {where}: {reason}. There is no saved copy yet, so nothing from it "
                            "is listed."}
        index, dropped, when = got
        return {**base, "ok": True, "stale": True, "fetched_at": when, "packages": index["packages"],
                "yanked": index["yanked"], "dropped": dropped, "name": index["name"], "error": reason,
                "line": f"Could not reach {where}: {reason}. This is the copy saved {when}, which may be out of "
                        "date."}

    def indexes(self, fresh: bool = False) -> list[dict]:
        return [self.index(e, fresh=fresh) for e in self.sources()]

    # -- rows ------------------------------------------------------------------
    def listing(self, fresh: bool = False, notices: Any = None) -> dict:
        """``GET /v1/store``: every source's rows, each with the tier the Core will record and its source.
        The row ``add store:<id>`` installs is marked ``used`` (the official store first, then hers, in order)."""
        idx = self.indexes(fresh=fresh)
        self.check_yanked(notices, idx)
        return {"sources": [_source_view(ix) for ix in idx], "packages": self._rows(idx)}

    def saved_listing(self) -> dict:
        """:meth:`listing` from the saved copies only: never the network, for what runs without her asking (D96)."""
        idx = [self.saved_index(e) for e in self.sources()]
        return {"sources": [_source_view(ix) for ix in idx], "packages": self._rows(idx)}

    def _rows(self, idx: list[dict]) -> list[dict]:
        official = next((ix for ix in idx if ix["official"]), None)
        seen: set[str] = set()
        rows = []
        for ix in idx:
            for r in ix["packages"]:
                why = self.yanked_why(ix, r["id"], r["commit"], official)    # the official list counts for every source
                rows.append({**r, "listed_tier": r["tier"], "tier": recorded_tier(ix["official"], r).split(":")[1],
                             "origin": recorded_tier(ix["official"], r), "source": ix["source"],
                             "official": ix["official"], "used": r["id"] not in seen,
                             "yanked": why is not None, **({"yanked_why": why} if why is not None else {}),
                             "builtin_look": builtin_look(r),
                             "installed": bool(self.registry is not None and self.registry.get(r["id"]))})
                seen.add(r["id"])
        return rows

    def suggestions(self) -> list[dict]:
        """The rows Toolsmith's plan may suggest (D96), from the SAVED copy of the official store only, never the
        network: tier verified, not yanked, not installed, kinds app and tool, with tools to show the brain. No
        other row's text ever reaches the brain, so a community or second-source row can't reach planning."""
        ix = self.saved_index(self.sources()[0])
        return [r for r in ix["packages"]
                if r["kind"] in SUGGESTED and r["tier"] == "verified" and (r.get("abilities") or {}).get("tools")
                and self.yanked_why(ix, r["id"], r["commit"]) is None and not builtin_look(r)
                and not (self.registry is not None and self.registry.get(r["id"]))]

    def resolve_official(self, id: str) -> tuple[dict, dict]:
        """``(row, index)`` for *id* from the official store as it reads right now, and from nowhere else (D96): what
        a plan's suggestion installs from. A saved copy does not count (its yank list may be old). Raises
        :class:`StoreError` when the store can't be read or no longer lists it."""
        if not ID.fullmatch(id or ""):
            raise StoreError("A store package's id is 2 to 40 lower-case letters, digits or '-'.")
        ix = self.index(self.sources()[0], fresh=True)
        if ix["stale"] or not ix["ok"]:
            raise StoreError(f"Could not reach the official PuffPAI store: {ix.get('error') or 'no answer'}.", 503)
        for r in ix["packages"]:
            if r["id"] == id:
                return r, ix
        raise StoreError(f"The official PuffPAI store no longer lists {id}.", 404)

    def official_row(self, id: str, fresh: bool = True) -> dict | None:
        """The official store's row for *id* as it reads now, with ``yanked_why`` when it was pulled; None when
        it no longer lists it. Raises :class:`StoreError` when there is nothing to tell by (never reached, no
        saved copy)."""
        ix = self.index(self.sources()[0], fresh=fresh)
        if not ix["ok"]:
            raise StoreError(ix["line"], 503)
        for r in ix["packages"]:
            if r["id"] == id:
                why = self.yanked_why(ix, r["id"], r["commit"])
                return {**r, **({"yanked_why": why} if why is not None else {})}
        return None

    def resolve(self, id: str, fresh: bool = True) -> tuple[dict, dict]:
        """``(row, index)`` for ``store:<id>``: the official store first, then her sources in order."""
        if not ID.fullmatch(id or ""):
            raise StoreError("A store package's id is 2 to 40 lower-case letters, digits or '-'.")
        idx = self.indexes(fresh=fresh)
        for ix in idx:
            for r in ix["packages"]:
                if r["id"] == id:
                    return r, ix
        said = [ix["line"] for ix in idx if not ix["ok"] or ix["stale"]]
        raise StoreError([f"No store lists a package {id!r}."] + said, 404)

    def resolve_in(self, key: str, id: str, fresh: bool = True) -> tuple[dict, dict]:
        """``(row, index)`` for *id* in the one source a Registry row recorded (an update)."""
        entry = self.entry_for(key)
        if entry is None:
            raise StoreError("The store source it came from was removed, so there is no update for it.", 404)
        ix = self.index(entry, fresh=fresh)
        for r in ix["packages"]:
            if r["id"] == id:
                return r, ix
        raise StoreError([f"Its store no longer lists {id!r}."] + ([ix["line"]] if ix["stale"] else []), 404)

    def yanked_why(self, ix: dict, id: str, commit: str, official: dict | None = None) -> str | None:
        """Why (*id*, *commit*) was pulled, or None. A source's own yanked list counts for its rows, and the
        official store's list counts for every source."""
        lists = [ix] + ([official] if official is not None and official is not ix else [])
        for one in lists:
            for y in one.get("yanked") or []:
                if y["id"] == id and y["commit"] == commit:
                    return y["why"]
        return None

    def installed(self) -> list[dict]:
        """Registry rows of packages installed from a store (with the ``store`` record)."""
        if self.registry is None:
            return []
        return [r for r in self.registry.list() if isinstance(r.get("store"), dict)
                and str(r.get("origin") or "").startswith("store:")]

    def check_yanked(self, notices: Any = None, idx: list[dict] | None = None) -> list[dict]:
        """Installed store packages whose (id, commit) a store pulled: one notice each, offering removal.
        Nothing is removed here: only her own remove does that."""
        idx = idx if idx is not None else self.indexes()
        official = next((ix for ix in idx if ix["official"]), None)
        hits = []
        for row in self.installed():
            rec = row["store"]
            ix = next((x for x in idx if x["key"] == rec.get("source")
                       or (not x["official"] and _same(x["key"], str(rec.get("source") or "")))), None)
            why = self.yanked_why(ix or {"yanked": []}, row["id"], str(rec.get("commit") or ""), official)
            if why is None:
                continue
            hits.append({"id": row["id"], "name": row.get("name") or row["id"], "why": why})
            from .manager import _notice_once
            _notice_once(notices, f"{row.get('name') or row['id']} was pulled from the store"[:120],
                         f"The store pulled the version of {row.get('name') or row['id']} you have installed: {why} "
                         f"Nothing was removed. To remove it, use Remove on its app, or run: puffpai remove "
                         f"{row['id']}")
        return hits

    def updates(self, fresh: bool = False, notices: Any = None) -> dict:
        """Installed store packages whose source now pins a different commit (``GET /v1/store/updates``)."""
        idx = self.indexes(fresh=fresh)
        out, other = [], []
        for row in self.installed():
            rec = row["store"]
            try:
                new, ix = self._row_in(idx, str(rec.get("source") or ""), row["id"])
            except StoreError as exc:
                other.append({"id": row["id"], "name": row.get("name") or row["id"], "line": exc.problems[0]})
                continue
            if new["commit"] == rec.get("commit"):
                continue
            out.append({"id": row["id"], "name": row.get("name") or row["id"],
                        "have": {"commit": rec.get("commit"), "version": row.get("version", ""),
                                 "origin": row.get("origin")},
                        "store": {"commit": new["commit"], "version": new.get("version", ""),
                                  "origin": recorded_tier(ix["official"], new), "path": new["path"]},
                        "source": ix["source"], "stale": ix["stale"],
                        "line": f"Update it with: puffpai update {row['id']} (it shows its Yes card first)."})
        self.check_yanked(notices, idx)
        return {"updates": out, "not_found": other, "sources": [_source_view(ix) for ix in idx]}

    def _row_in(self, idx: list[dict], key: str, id: str) -> tuple[dict, dict]:
        entry = self.entry_for(key)
        if entry is None:
            raise StoreError("The store source it came from was removed, so there is no update for it.", 404)
        ix = next(x for x in idx if x["key"] == entry["key"])
        for r in ix["packages"]:
            if r["id"] == id:
                return r, ix
        raise StoreError(f"Its store no longer lists {id}." + (f" {ix['line']}" if ix["stale"] else ""), 404)


def _source_view(ix: dict) -> dict:
    return {k: ix.get(k) for k in ("source", "official", "ok", "stale", "fetched_at", "line", "dropped")} | {
        "removable": not ix["official"], "count": len(ix.get("packages") or [])}


__all__ = ["INDEX_SCHEMA", "KINDS", "OFFICIAL", "OFFICIAL_SOURCE", "REPO_MIRRORS", "Store", "StoreError",
           "builtin_look", "clean_abilities", "clean_row", "normal_source", "parse_index", "path_problem",
           "recorded_tier", "row_problems"]
