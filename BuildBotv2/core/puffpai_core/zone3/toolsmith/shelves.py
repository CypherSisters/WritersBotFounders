# Harvested from PuffPAIBuildBot bb-next:src/puffpai/catalogs/searchers/{codeberg,npm,pypi,mcp,hacs,web}.py (adapted)
"""More places to look for apps and tools while a plan is made (D99). One small reader per kind of place, each row
in ``data/sources.json`` naming its reader, so a new place of a known kind is a new row, not new code.

- What a reader gives: a project that can be tried (``tries``: ``git``, cloned from its own host, or
  ``github_link``, a package whose record links its GitHub project, tried as that project) or a row to look at
  (``look``: a skill, an MCP server, an add-on, a web result). A look row is never tried and never ticked.
- Every plan asks every place her search Yes names, all at once (:class:`Round`). A place that fails or runs late
  gets one plain line on the card ("No answer from ..."). It never loses the plan.
- Every word sent goes through the Core's own screen first (``find.Finder.cleared``), and the Core screens it again
  on the way out (``toolsmith_host.service._address_held``).

v1's source lists and goal tuning are left behind: rows here carry no goal's words.
"""

from __future__ import annotations

import concurrent.futures as futures
import hashlib
import json
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable

_TEXT_MAX = 300


def _text(value: Any, limit: int = _TEXT_MAX) -> str:
    return " ".join(str(value or "").split())[:limit]


def _json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return None


def _https(url: Any) -> str:
    from ..text_rules import url_is_plain
    text = str(url or "").strip()
    return text if text.startswith("https://") and url_is_plain(text) and len(text) <= 300 else ""


def _github_of(link: Any) -> str:
    from .find import github_of
    return github_of(re.sub(r"^git\+", "", str(link or "").strip()))


def look(row_id: str, name: Any, what: Any, url: Any, **more: Any) -> dict | None:
    """A row to look at, or None when it has no name or no plain https address."""
    name, page = _text(name, 100), _https(url)
    return {"look": True, "source": row_id, "name": name, "description": _text(what), "url": page, **more} \
        if name and page else None


def _project(row_id: str, full: str, name: Any, what: Any, *, language: str = "", stars: Any = 0, host: str = "",
             branch: str = "", topics: Any = None, **more: Any) -> dict:
    """A project as the search-result shape the rest of ``find`` reads."""
    return {"full_name": full, "name": _text(name, 100) or full.split("/")[-1], "description": _text(what),
            "stargazers_count": stars if isinstance(stars, int) and stars >= 0 else 0, "language": language,
            "topics": [str(t) for t in (topics if isinstance(topics, list) else [])][:20], "from": row_id,
            "default_branch": branch, **({"host": host} if host else {}), **more}


# -- the readers: an answer in, rows out ------------------------------------------------------------------------

_FORGEJO_FULL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}/[A-Za-z0-9_.-]{1,100}$")


def read_forgejo(row_id: str, row: dict, body: bytes) -> list[dict]:
    """Codeberg's project search. A mirror's facts belong to the original, so a mirror is left out."""
    got = _json(body)
    out = []
    for r in (got.get("data") if isinstance(got, dict) else None) or []:
        full = str(r.get("full_name") or "") if isinstance(r, dict) else ""
        if not _FORGEJO_FULL.match(full) or ".." in full or r.get("private") or r.get("mirror"):
            continue
        out.append(_project(row_id, full, r.get("name"), r.get("description"), language=str(r.get("language") or ""),
                            stars=r.get("stars_count"), host=row["host"], branch=str(r.get("default_branch") or ""),
                            topics=r.get("topics"), archived=bool(r.get("archived")), fork=bool(r.get("fork"))))
    return out


def read_npm(row_id: str, row: dict, body: bytes) -> list[dict]:
    """npm's search. A package that links its GitHub project is tried as that project; without a link it is only
    something to look at. Who published it is never kept."""
    got = _json(body)
    out = []
    for obj in (got.get("objects") if isinstance(got, dict) else None) or []:
        pkg = obj.get("package") if isinstance(obj, dict) else None
        if not isinstance(pkg, dict) or not isinstance(pkg.get("name"), str):
            continue
        links = pkg.get("links") if isinstance(pkg.get("links"), dict) else {}
        full = _github_of(links.get("repository"))
        if full:
            out.append(_project(row_id, full, pkg["name"], pkg.get("description"), language="JavaScript",
                                topics=pkg.get("keywords")))
            continue
        kept = look(row_id, pkg["name"], pkg.get("description"), f"https://www.npmjs.com/package/{pkg['name']}")
        if kept:
            out.append(kept)
    return out


def read_pypi(row_id: str, row: dict, body: bytes) -> list[dict]:
    """One exact PyPI name (PyPI's own search asks for a browser check, so it is not used). Tried as its GitHub
    project when its record links one, else only something to look at."""
    got = _json(body)
    info = got.get("info") if isinstance(got, dict) else None
    if not isinstance(info, dict) or not isinstance(info.get("name"), str):
        return []
    urls = info.get("project_urls") if isinstance(info.get("project_urls"), dict) else {}
    full = next((f for f in (_github_of(u) for u in list(urls.values())[:20]) if f), "") \
        or _github_of(info.get("home_page"))
    what = info.get("summary")
    if full:
        words = re.split(r"[,\s]+", str(info.get("keywords") or ""))
        return [_project(row_id, full, info["name"], what, language="Python", topics=[w for w in words if w])]
    kept = look(row_id, info["name"], what, f"https://pypi.org/project/{urllib.parse.quote(info['name'])}/")
    return [kept] if kept else []


def read_mcp(row_id: str, row: dict, body: bytes) -> list[dict]:
    """The official MCP server list: a server has no screen to try, so it is something to look at."""
    got = _json(body)
    out = []
    for r in (got.get("servers") if isinstance(got, dict) else None) or []:
        s = r.get("server") if isinstance(r, dict) else None
        if not isinstance(s, dict):
            continue
        repo = s.get("repository") if isinstance(s.get("repository"), dict) else {}
        kept = look(row_id, s.get("title") or s.get("name"), s.get("description"),
                    repo.get("url") or s.get("websiteUrl"))
        if kept:
            out.append(kept)
    return out


def read_hacs(row_id: str, row: dict, body: bytes) -> list[dict]:
    """A HACS list, read whole: add-ons that run inside Home Assistant, never by themselves."""
    got = _json(body)
    out = []
    for r in (got.values() if isinstance(got, dict) else []):
        full = _github_of(f"https://github.com/{r.get('full_name')}") if isinstance(r, dict) else ""
        if not full:
            continue
        kept = look(row_id, r.get("manifest_name") or r.get("full_name"), r.get("description"),
                    f"https://github.com/{full}", topics=[str(t) for t in r.get("topics") or []][:20])
        if kept:
            out.append(kept)
    return out


def read_clawhub(row_id: str, row: dict, body: bytes) -> list[dict]:
    """ClawHub's search. A skill ClawHub itself marks as suspicious is left out."""
    got = _json(body)
    out = []
    for r in (got.get("results") if isinstance(got, dict) else None) or []:
        if not isinstance(r, dict):
            continue
        native = r.get("native") if isinstance(r.get("native"), dict) else {}
        skill = native.get("skill") if isinstance(native.get("skill"), dict) else {}
        path = str(r.get("canonicalUrl") or "")
        if skill.get("isSuspicious") or not re.fullmatch(r"/[A-Za-z0-9_./-]{1,200}", path) or ".." in path:
            continue
        kept = look(row_id, r.get("displayName") or skill.get("displayName"), skill.get("summary"),
                    row["page"].format(path=path))
        if kept:
            out.append(kept)
    return out


def read_skills_sh(row_id: str, row: dict, body: bytes) -> list[dict]:
    got = _json(body)
    out = []
    for r in (got.get("skills") if isinstance(got, dict) else None) or []:
        sid = str(r.get("id") or "") if isinstance(r, dict) else ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){1,3}", sid) or ".." in sid:
            continue
        kept = look(row_id, r.get("name") or r.get("skillId"), f"From {_text(r.get('source'), 100)}.",
                    row["page"].format(id=sid))
        if kept:
            out.append(kept)
    return out


def read_hermes_index(row_id: str, row: dict, body: bytes) -> list[dict]:
    """The Hermes skills index, read whole, so no word of hers is sent to read it. Only rows that say where their
    GitHub project and folder are."""
    got = _json(body)
    out = []
    for r in (got.get("skills") if isinstance(got, dict) else None) or []:
        if not isinstance(r, dict) or not isinstance(r.get("path"), str):
            continue
        full = _github_of(f"https://github.com/{r.get('repo')}")
        if not full or not re.fullmatch(r"[A-Za-z0-9_./ -]{1,200}", r["path"]) or ".." in r["path"]:
            continue
        kept = look(row_id, r.get("name"), r.get("description"),
                    f"https://github.com/{full}/tree/HEAD/{urllib.parse.quote(r['path'])}",
                    topics=[str(t) for t in r.get("tags") or []][:20])
        if kept:
            out.append(kept)
    return out


def read_gh_tree(row_id: str, row: dict, body: bytes) -> list[dict]:
    """One GitHub skill collection's file list, read whole: each folder with a SKILL.md is one skill."""
    got = _json(body)
    tree = got.get("tree") if isinstance(got, dict) else None
    m = re.search(r"/repos/([^/]+/[^/]+)/git/trees/", str(row.get("_url") or ""))
    full = _github_of(f"https://github.com/{m.group(1)}") if m else ""
    if not full:
        return []
    out = []
    for t in tree if isinstance(tree, list) else []:
        path = str(t.get("path") or "") if isinstance(t, dict) else ""
        if not path.endswith("/SKILL.md") or not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", path) or ".." in path:
            continue
        folder = path[:-len("/SKILL.md")]
        kept = look(row_id, folder.rsplit("/", 1)[-1].replace("-", " "), "A skill in a maker's own collection.",
                    f"https://github.com/{full}/tree/HEAD/{folder}")
        if kept:
            out.append(kept)
    return out


def _read_web(row_id: str, row: dict, rows: Iterable[Any]) -> list[dict]:
    """A web search's pages. A page that is a GitHub project is tried as that project. Any other page is something
    to look at, only where the row's own terms let a result be kept (``keep_results``: Brave's do not)."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        title = re.sub(r"<[^>]{0,200}>", " ", str(r.get("title") or ""))
        what = re.sub(r"<[^>]{0,200}>", " ", str(r.get("description") or ""))
        full = _github_of(r.get("url"))
        if full:
            out.append(_project(row_id, full, full.split("/")[-1], what or title, listed=True))
        elif row.get("keep_results"):
            kept = look(row_id, title, what, r.get("url"))
            if kept:
                out.append(kept)
    return out


def read_brave(row_id: str, row: dict, body: bytes) -> list[dict]:
    got = _json(body)
    web = got.get("web") if isinstance(got, dict) else None
    return _read_web(row_id, row, (web.get("results") if isinstance(web, dict) else None) or [])


def read_marginalia(row_id: str, row: dict, body: bytes) -> list[dict]:
    got = _json(body)
    return _read_web(row_id, row, (got.get("results") if isinstance(got, dict) else None) or [])


#: What a field is usually called in an answer nobody wrote a reader for (lane add-api).
_TITLE_KEYS = ("title", "name", "headline", "label", "displayName", "full_name")
_LINK_KEYS = ("url", "link", "href", "html_url", "uri", "canonical_url", "permalink", "website", "homepage")
_TEXT_KEYS = ("description", "snippet", "summary", "abstract", "excerpt", "text", "content", "body")
_READ_DEPTH = 6


def _dig(obj: Any, dotted: str) -> Any:
    """``a.b.c`` in nested dictionaries, or None."""
    for part in str(dotted or "").split("."):
        obj = obj.get(part) if isinstance(obj, dict) and part else None
    return obj


def _first(row: dict, given: Any, keys: tuple[str, ...]) -> Any:
    if given:
        return _dig(row, given)
    return next((row[k] for k in keys if isinstance(row.get(k), str) and row[k].strip()), None)


def _reads(row: dict) -> int:
    """How much a dictionary looks like one result: 2 with a title and a plain address, else 0."""
    return 2 if any(isinstance(row.get(k), str) and row[k].strip() for k in _TITLE_KEYS)         and any(_https(row.get(k)) for k in _LINK_KEYS) else 0


def _result_lists(obj: Any, depth: int = 0) -> Iterable[list]:
    if depth > _READ_DEPTH:
        return
    if isinstance(obj, list):
        yield obj
        for item in obj[:3]:
            yield from _result_lists(item, depth + 1)
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _result_lists(value, depth + 1)


def read_generic(row_id: str, row: dict, body: bytes) -> list[dict]:
    """Any JSON answer (lane add-api): the longest list whose rows have a title and a plain address, or the row's own
    ``fields`` (dotted names ``list``, ``title``, ``link``, ``snippet``) when guessing fails. Rows to look at only: an
    answer nobody checked is never tried, never fetched and never ticked. [] when nothing reads."""
    got = _json(body)
    fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
    if fields.get("list"):
        rows = _dig(got, fields["list"])
        rows = rows if isinstance(rows, list) else []
    else:
        lists = [lst for lst in _result_lists(got) if any(isinstance(r, dict) for r in lst)]
        scored = [(sum(_reads(r) for r in lst if isinstance(r, dict)), lst) for lst in lists]
        rows = max(scored, key=lambda s: s[0], default=(0, []))[1] if any(s[0] for s in scored) else []
        if not rows and lists and (fields.get("title") or fields.get("link")):
            rows = max(lists, key=len)
    out = []
    for r in rows[:int(row.get("n") or 20)]:
        if not isinstance(r, dict):
            continue
        title = re.sub(r"<[^>]{0,200}>", " ", str(_first(r, fields.get("title"), _TITLE_KEYS) or ""))
        what = re.sub(r"<[^>]{0,200}>", " ", str(_first(r, fields.get("snippet"), _TEXT_KEYS) or ""))
        kept = look(row_id, title, what, _first(r, fields.get("link"), _LINK_KEYS))
        if kept:
            out.append(kept)
    return out


READERS: dict[str, Callable[[str, dict, bytes], list[dict]]] = {
    "forgejo": read_forgejo, "npm": read_npm, "pypi": read_pypi, "mcp": read_mcp, "hacs": read_hacs,
    "clawhub": read_clawhub, "skills_sh": read_skills_sh, "hermes_index": read_hermes_index, "gh_tree": read_gh_tree,
    "brave": read_brave, "marginalia": read_marginalia, "generic": read_generic}


def slim_hermes(body: bytes) -> bytes:
    """Only what the reader keeps, before an answer of 40 MB is written to the day's cache."""
    got = _json(body)
    rows = got.get("skills") if isinstance(got, dict) else None
    keep = ("name", "description", "repo", "path", "tags")
    return json.dumps({"skills": [{k: r.get(k) for k in keep} for r in rows
                                  if isinstance(r, dict) and r.get("repo") and r.get("path")]
                       if isinstance(rows, list) else []}).encode("utf-8")


def says(text: Any, words: Iterable[str]) -> bool:
    """*text* has one of *words* at the start of a word ("repair" in "repairs", "rain" never in "training")."""
    low = str(text or "").lower()
    return any(re.search(rf"\b{re.escape(str(w).lower())}", low) for w in words)


def usable(src: dict, places: set[str], web_ready: set[str], context: str) -> tuple[dict[str, dict], list[str]]:
    """``(rows to ask, rows her search Yes does not name)``. Every place, every plan. A row with ``only_when`` fits one
    kind of job only and says which."""
    rows, not_asked = {}, []
    shelves = dict(src.get("shelves") or {})
    for rid, row in shelves.items():
        if row.get("reader") not in READERS:
            continue
        if row.get("only_when") and not says(context, row["only_when"]):
            continue
        if row.get("host") in places:
            rows[rid] = row
        else:
            not_asked.append(rid)
    for rid, row in (src.get("web") or {}).items():
        if rid in web_ready and row.get("reader") in READERS and row.get("host") in places:
            rows[rid] = row                        # her own key and her own Yes for it: off otherwise
    return rows, not_asked


#: The tag of an ask that is not for one job: a list read whole, once a plan.
WHOLE = "whole"


class Round:
    """Every place this plan asks, all at once (the owner, 10-30: "We should be searching ALL of the databases").
    :meth:`begin` starts one worker per place, in the background, right after the queries are known; each place takes
    its own asks in turn, with its own pause. A job waits at most a little for its own answers (:meth:`wait`), and a
    slow place never holds up another or the plan. A place that fails is out of this plan after that one ask and the
    card says it gave no answer. Budgets limit how many asks a place gets, never whether it is asked."""

    def __init__(self, link: Any, cache_dir: Path, src: dict, clock: Callable[[], float] = time.monotonic) -> None:
        self.link, self.cache_dir, self.src = link, Path(cache_dir), src
        self.clock = clock
        self.down: dict[str, str] = {}             # row id -> why it gave no answer (plain words)
        self.asked: list[str] = []                 # row ids asked in this plan, in order
        self.answered = 0                          # asks that gave an answer, from the cache too (D151)
        self.whole: dict[str, list[dict]] = {}     # a list row's rows, read once per plan
        self.keys_refused: set[str] = set()
        self._got: dict[Any, list[dict]] = {}
        self._waiting: dict[Any, set[str]] = {}    # tag -> the places that still owe it an answer
        self._cond = threading.Condition()
        self._pool: futures.ThreadPoolExecutor | None = None

    def _read(self, rid: str, row: dict, url: str) -> list[dict]:
        hours = float(row.get("cache_hours") or 0)
        path = self.cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:32]}.json"
        status, body = 0, b""
        if hours > 0:
            try:
                hit = json.loads(path.read_text(encoding="utf-8"))
                if time.time() - float(hit["at"]) < hours * 3600:
                    status, body = int(hit["status"]), hit["body"].encode("utf-8")
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if not status:
            status, headers, body = self.link.http_get(url, accept="application/json",
                                                       max_bytes=int(row.get("max_bytes") or 1_000_000),
                                                       timeout_s=float(row.get("timeout_s") or 10),
                                                       **({"web_key": rid} if row.get("key") else {}))
            if isinstance(headers, dict) and headers.get("x-puffpai-key") == "refused":
                self.keys_refused.add(rid)
                raise OSError("it did not accept your key")
            if status == 200 and row.get("reader") == "hermes_index":
                body = slim_hermes(body)
            if hours > 0 and status in (200, 404):
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"at": time.time(), "status": status,
                                            "body": body.decode("utf-8", errors="replace")}), encoding="utf-8")
        if status == 404:
            return []                              # PyPI: no package has this exact name
        if status != 200:
            raise OSError(f"it answered {status}")
        return READERS[row["reader"]](rid, dict(row, _url=url), body)

    def _settle(self, rid: str, tags: Iterable[Any]) -> None:
        with self._cond:
            for tag in tags:
                self._waiting.get(tag, set()).discard(rid)
            self._cond.notify_all()

    def _work(self, rid: str, row: dict, asks: list[tuple[str, Any]]) -> None:
        """One place's asks of this plan, one after the other (npm asks for one a second, Codeberg two seconds; a place
        is never asked twice at once). Its first failure ends them."""
        for n, (url, tag) in enumerate(asks):
            if n:
                time.sleep(float(row.get("gap_s") or 1))
            try:
                rows = self._read(rid, row, url)
            except Exception as exc:  # noqa: BLE001 - one place's problem is one line, never the plan's end
                with self._cond:
                    self.down.setdefault(rid, _text(exc, 120) or type(exc).__name__)
                self._settle(rid, [t for _u, t in asks[n:]])
                return
            with self._cond:
                self.answered += 1
                if tag == WHOLE:
                    self.whole.setdefault(rid, []).extend(rows)
                else:
                    self._got.setdefault(tag, []).extend(rows)
            if all(t != tag for _u, t in asks[n + 1:]):
                self._settle(rid, [tag])           # this place's last ask for this job or list

    def begin(self, asks: list[tuple[str, dict, str, Any]]) -> None:
        """Start every place's asks ``[(row id, row, address, tag)]``. Returns at once."""
        per: dict[str, tuple[dict, list[tuple[str, Any]]]] = {}
        for rid, row, url, tag in asks:
            per.setdefault(rid, (row, []))[1].append((url, tag))
            self._waiting.setdefault(tag, set()).add(rid)
        if not per:
            return
        self.asked = list(per)
        self._pool = futures.ThreadPoolExecutor(max_workers=len(per), thread_name_prefix="shelf")
        for rid, (row, urls) in per.items():
            self._pool.submit(self._work, rid, row, urls)

    def wait(self, tags: Iterable[Any], wait_s: float) -> None:
        """Wait for these tags' answers, at most *wait_s* (real seconds, since it is the places' own time)."""
        tags = list(tags)
        ends = time.monotonic() + max(0.0, wait_s)
        with self._cond:
            while any(self._waiting.get(t) for t in tags):
                left = ends - time.monotonic()
                if left <= 0:
                    return
                self._cond.wait(left)

    def take(self, tag: Any) -> list[dict]:
        with self._cond:
            return self._got.pop(tag, [])

    def whole_rows(self) -> list[dict]:
        with self._cond:
            return [r for rows in self.whole.values() for r in rows]

    def finish(self) -> None:
        """The plan is being written: a place that still owes an answer gave none. Nothing more is sent after this."""
        with self._cond:
            for owed in self._waiting.values():
                for rid in owed:
                    self.down.setdefault(rid, "it did not answer in time")
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def run(self, asks: list[tuple[str, dict, str]], wait_s: float) -> list[dict]:
        """One round at once, for one tag: :meth:`begin`, :meth:`wait`, the answers that came in time."""
        self.begin([(rid, row, url, "one") for rid, row, url in asks if rid not in self.down])
        self.wait(["one"], wait_s)
        got = self.take("one")
        with self._cond:
            for rid in self._waiting.get("one", set()):
                self.down.setdefault(rid, "it did not answer in time")
            self._waiting.pop("one", None)
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        return got


def word_for(row: dict, words: list[str], generic: frozenset | set = frozenset()) -> str:
    """What one query sends to this row: all its words, its words joined by the row's ``join`` (one PyPI name), or
    its one best word (``one_word``: Codeberg matches several words only as an exact phrase, v1 measured it)."""
    if row.get("join"):
        return str(row["join"]).join(words)
    if row.get("one_word"):
        return max([w for w in words if w not in generic] or words, key=len, default="")
    return " ".join(words)


def search_url(row: dict, query: str) -> str:
    return str(row["search"]).format(q=urllib.parse.quote(query, safe=""), n=int(row.get("n") or 10))


def down_line(names: list[str]) -> str:
    """Her one line for the places asked that gave no answer this plan."""
    if not names:
        return ""
    said = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"No answer from {said}, so nothing from {'it' if len(names) == 1 else 'them'} is on this plan."


__all__ = ["READERS", "Round", "WHOLE", "down_line", "read_generic", "look", "says", "search_url", "slim_hermes", "usable", "word_for"]
