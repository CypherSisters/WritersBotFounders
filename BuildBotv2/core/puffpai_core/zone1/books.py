# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/api_server.py, src/puffpai/knowledge/auto_index.py (adapted)
r"""Manage her books (D136): remove one, read one, and drop in a ``.zim``. Finding one is Toolsmith's plan (D99).

Everything lives where :mod:`.fetched` says (D103): ``<store>\<source>\``. A ``kn-*`` folder is one library.

- :func:`libraries`: each ``kn-*`` folder, with its size, and how far learning a dropped file has got.
- :func:`remove`: one item, or a whole source, from the disk and the index. Its folder must not be a link.
- :func:`read_text`, :func:`articles`, :func:`article`: read one, in parts; list a ``.zim``'s articles by title; read one
  article as plain text. Only the Core reads, and nothing here reaches the network.
- :func:`look`: a ``.zim`` she dropped at the top of the store moves into ``kn-own-<name>\`` and is learned in the
  background, at about half of one CPU: each article through Drawbridge's text rules, as a download's are, then kept as
  text in parts of about 4 MB. Its ``.zim`` stays, so she can read it. Public text is not her data, so it goes through
  ``fetched.save`` and never reaches the vault. It picks up where it stopped after a restart.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import jsonfile
from . import fetched, zim_reader
from .folders import DOCUMENT_SUFFIXES, SCAN_LOCK, Library
from .knowledge_folder import is_link
from .pinned import part_starts

logger = logging.getLogger(__name__)

JOBS = "zim_learning.json"
#: One text file of a dropped ``.zim``, in bytes: the Library reads a file whole.
PART_TEXT = 4_000_000
PAGE = 50
ARTICLE_MAX = 2_000_000
NOT_YOURS = "That isn't one of your books."
GONE = "That book isn't there any more."
LINKED = "That book goes through a link to somewhere else, so it was not removed. Remove the link first."
MOVING = "Your books and downloads are being moved. Remove it when that ends."
NO_ZIM = "That book has no offline library to browse."
NO_ARTICLE = "That article isn't in this library."
CANT_READ_ZIM = "BuildBot can't read this file as an offline library, so it was left where it is."
WAITING = "This file isn't whole yet. If it is still copying, your bot learns it when that ends."
NO_SCREEN = "Drawbridge's text check isn't here, so this waits."
DOCUMENT_HERE = "Word and PDF files aren't read here. Use Add documents at the top of this page."
_FILE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}\.txt$")
_lock = threading.Lock()
_running: dict[str, threading.Thread] = {}
_stop: set[str] = set()
#: The library being learned now, by state folder: its file is open, so removing it waits for that to end.
_now_on: dict[str, str] = {}


class BookRefused(Exception):
    """A plain line for her: nothing was changed."""


def _jobs_path(state: Path | str) -> Path:
    return Path(state) / "index" / JOBS


def _load(state: Path | str) -> dict:
    got = jsonfile.read(_jobs_path(state), {})
    if not isinstance(got, dict):
        return {"jobs": {}, "bad": {}}
    return {"jobs": got.get("jobs") if isinstance(got.get("jobs"), dict) else {},
            "bad": got.get("bad") if isinstance(got.get("bad"), dict) else {}}


def _save(state: Path | str, data: dict) -> None:
    _jobs_path(state).parent.mkdir(parents=True, exist_ok=True)
    jsonfile.write(_jobs_path(state), data)


def _folder(state: Path | str, source: Any) -> Path:
    if not isinstance(source, str) or not fetched.known(source):
        raise BookRefused(NOT_YOURS)
    return fetched.root(state) / source


def _zim_in(folder: Path) -> Path | None:
    try:
        return next((p for p in sorted(folder.glob("*.zim")) if p.is_file() and not is_link(p)), None)
    except OSError:
        return None


def _size(folder: Path) -> int:
    n = 0
    for here, _dirs, names in os.walk(folder):
        for name in names:
            try:
                n += os.path.getsize(os.path.join(here, name))
            except OSError:
                pass
    return n


def _title(folder: Path) -> str:
    try:
        got = json.loads((folder / "puffpai.json").read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError, AttributeError):
        got = None
    return " ".join(str(got or folder.name).split())[:120]


def _learning_line(job: dict) -> str:
    n = int(job.get("articles") or 0)
    said = f"{n:,} article{'' if n == 1 else 's'}"
    if job.get("done"):
        return f"{said} can be searched. The whole file is kept here."
    return str(job.get("line") or "") or f"Your bot is learning this. {said} so far, and you can search them already."


def libraries(state: Path | str) -> list[dict]:
    """``[{source, title, size, zim, line}]``, one per ``kn-*`` folder."""
    store = fetched.root(state)
    jobs = _load(state)["jobs"]
    out = []
    try:
        folders = sorted(p for p in store.iterdir() if p.is_dir() and fetched.PACKAGE.match(p.name))
    except OSError:
        return []
    for folder in folders:
        job = jobs.get(folder.name)
        out.append({"source": folder.name, "title": _title(folder), "size": _size(folder),
                    "zim": bool(_zim_in(folder)), "line": _learning_line(job) if isinstance(job, dict) else ""})
    return out


def dropped(state: Path | str) -> list[Path]:
    """The ``.zim`` files at the top of the store, not yet taken in."""
    try:
        return sorted(p for p in fetched.root(state).glob("*.zim") if p.is_file() and not is_link(p))
    except OSError:
        return []


def waiting(state: Path | str) -> list[dict]:
    """``[{name, line}]``: a dropped file that isn't being learned, and why. A Word or PDF file left at the top of the store
    has a row too: the Library doesn't read it there, :func:`look` never takes it in, and it stays where it is."""
    bad = _load(state)["bad"]
    return [{"name": p.name, "line": str((bad.get(p.name) or {}).get("line") or WAITING)} for p in dropped(state)] \
        + [{"name": p.name, "line": DOCUMENT_HERE} for p in _documents_here(state)]


def _documents_here(state: Path | str) -> list[Path]:
    """Word and PDF files at the top of the store, by their names. Word's own lock files are not documents."""
    try:
        return sorted(p for p in fetched.root(state).iterdir() if p.suffix.lower() in DOCUMENT_SUFFIXES
                      and not p.name.startswith("~$") and p.is_file() and not is_link(p))
    except OSError:
        return []


# -- remove ---------------------------------------------------------------------------------------

def remove(state: Path | str, source: Any, file: Any = None, registry: Any = None) -> dict:
    """One text file with its row and its pictures, or (no *file*) the whole source. Off the disk and out of the index,
    so search can't find what is gone. Her Yes is the page's second click, as in v1's dialog."""
    folder = _folder(state, source)
    if fetched.moving(state):
        raise BookRefused(MOVING)
    if is_link(fetched.root(state)) or is_link(folder):
        raise BookRefused(LINKED)
    if not folder.is_dir():
        raise BookRefused(GONE)
    if file is not None and (not isinstance(file, str) or not _FILE.match(file)):
        raise BookRefused(NOT_YOURS)
    library = Library(None, state)
    got = SCAN_LOCK.acquire(timeout=30)                # a scan running now would index it again
    try:
        if file is None:
            return _remove_source(state, str(source), folder, library, registry)
        path = folder / file
        if not path.is_file() or is_link(path):
            raise BookRefused(GONE)
        stem = path.stem
        for p in folder.iterdir():
            if p.is_file() and p.stem == stem and not is_link(p):
                library.forget(p)
                p.unlink()
        fetched.forget(state, str(source), stem)
        return {"removed": file}
    except OSError:
        raise BookRefused("BuildBot could not remove all of that. If it is open somewhere, close it and try again.") from None
    finally:
        if got:
            SCAN_LOCK.release()


def _remove_source(state: Path | str, source: str, folder: Path, library: Library, registry: Any) -> dict:
    _stop.add(source)                                  # a learner on it stops at its next article
    ends = time.monotonic() + 20
    while _now_on.get(str(Path(state))) == source and time.monotonic() < ends:
        time.sleep(0.05)
    library.forget(folder, whole=True)
    shutil.rmtree(folder)
    fetched.forget_source(state, source)
    data = _load(state)
    if data["jobs"].pop(source, None) is not None:
        _save(state, data)
    _stop.discard(source)
    if registry is not None and fetched.PACKAGE.match(source):
        try:
            if (registry.get(source) or {}).get("kind") == "knowledge":
                registry.remove(source)
        except Exception:  # noqa: BLE001 - the book is gone either way
            logger.exception("knowledge row %s not removed", source)
    return {"removed": source}


# -- read -----------------------------------------------------------------------------------------

def read_text(state: Path | str, source: Any, file: Any, part: Any = 0) -> dict:
    """One kept text, a part at a time: ``{title, text, part, parts}``."""
    folder = _folder(state, source)
    if not isinstance(file, str) or not _FILE.match(file):
        raise BookRefused(NOT_YOURS)
    path = folder / file
    if is_link(folder) or is_link(path) or not path.is_file():
        raise BookRefused(GONE)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise BookRefused(GONE) from None
    starts = part_starts(text)
    at = max(0, min(_int(part), len(starts) - 1))
    end = starts[at + 1] if at + 1 < len(starts) else len(text)
    row = fetched.meta_for(state, path) or {}
    return {"title": str(row.get("title") or path.stem), "text": text[starts[at]:end], "part": at, "parts": len(starts)}


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _reader(state: Path | str, source: Any) -> zim_reader.ZIMReader:
    folder = _folder(state, source)
    zim = _zim_in(folder) if not is_link(folder) else None
    if zim is None:
        raise BookRefused(NO_ZIM)
    try:
        return zim_reader.ZIMReader(str(zim))
    except (OSError, ValueError):
        raise BookRefused(NO_ZIM) from None


def articles(state: Path | str, source: Any, query: Any = "", page: Any = 0, limit: int = PAGE) -> dict:
    """A page of a library's articles by title, or those whose title has *query*: ``{articles: [{title, url}], has_more}``.
    ZIM v5 keeps articles in namespace A, v6 in C: whichever has them (v1)."""
    words = " ".join(str(query or "").split())[:200]
    skip = _int(page) * limit
    with _reader(state, source) as z:
        for ns in ("A", "C"):
            found = z.search(words, namespace=ns, max_results=skip + limit + 1) if words \
                else z.iter_articles(namespace=ns, mime_filter="text/html")
            got = []
            for n, e in enumerate(found):
                if n >= skip + limit + 1:
                    break
                got.append({"title": str(e.title or e.url)[:200], "url": f"{e.namespace}/{e.url}"})
            if got:
                return {"articles": got[skip:skip + limit], "has_more": len(got) > skip + limit}
    return {"articles": [], "has_more": False}


def article(state: Path | str, source: Any, url: Any) -> dict:
    """One article as plain text: ``{title, url, text}``. v1 served its HTML and pictures; this page shows words."""
    if not isinstance(url, str) or not url or len(url) > 1000:
        raise BookRefused(NO_ARTICLE)
    with _reader(state, source) as z:
        try:
            got = z.get_article_by_url(url)
        except (OSError, ValueError, IndexError):
            got = None
        if got is None or not str(got.mime_type).startswith("text/"):
            raise BookRefused(NO_ARTICLE)
        return {"title": str(got.title or url)[:200], "url": url, "text": got.plain_text[:ARTICLE_MAX]}


# -- drop in a .zim -------------------------------------------------------------------------------

def _plain_name(stem: str) -> str:
    words = re.sub(r"[_-]+", " ", stem)
    words = re.sub(r"\s+\d{4}(?:\s\d{2})?$", "", words).strip()
    return (words[:1].upper() + words[1:])[:80] or "A library you added"


def _take_in(state: Path, path: Path, data: dict, registry: Any) -> bool:
    """Into its own folder, once it reads as whole. True when it is taken."""
    try:
        size = path.stat().st_size
        with zim_reader.ZIMReader(str(path)):
            pass
    except zim_reader.ZIMTruncatedError:
        return False                                   # still copying, or cut short: asked again next time
    except OSError:
        return False
    except Exception:  # noqa: BLE001 - any other reading of it failed: said once, on the page
        data["bad"][path.name] = {"size": size, "line": CANT_READ_ZIM}
        return False
    pid = fetched.slug(path.stem, fetched.OWN.rstrip("-"))[:39].rstrip("-")
    base, n = pid, 2
    while (fetched.root(state) / pid).exists():
        pid = f"{base[:36]}-{n}"
        n += 1
    folder = fetched.package_folder(state, pid)
    target = folder / path.name
    try:
        os.replace(path, target)
    except OSError:                                    # still open somewhere: asked again next time
        folder.rmdir()
        return False
    title = _plain_name(path.stem)
    manifest = {"schema": 1, "id": pid, "kind": "knowledge", "name": title, "version": "1.0.0",
                "description": "An offline library you added.", "sources": [{"title": title, "size_mb": round(size / 1_000_000, 1)}]}
    (folder / "puffpai.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    data["bad"].pop(path.name, None)
    data["jobs"][pid] = {"zim": path.name, "title": title, "at": 0, "part": 0, "articles": 0, "done": False}
    if registry is not None:
        try:
            if registry.get(pid) is None:
                registry.add({"id": pid, "kind": "knowledge", "name": title, "origin": f"local:{target}", "status": "stopped"})
        except Exception:  # noqa: BLE001 - the text is in the Library either way
            logger.exception("knowledge row %s not added", pid)
    return True


def _keep_part(state: Path, pid: str, job: dict, parts: list[str], *, at: int, kept: int) -> None:
    n = int(job["part"]) + 1
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = fetched.save(state, pid, f"part-{n:05d}", "\n\n".join(parts),
                        {"title": f"{job['title']}, part {n}", "license": "As its publisher says", "fetched_at": now,
                         "credit": "A .zim file you added."})
    job.update(part=n, at=at, articles=kept)
    if SCAN_LOCK.acquire(timeout=5):
        try:
            Library(None, state).scan_file(path)
        except Exception:  # noqa: BLE001 - kept; the next search's scan indexes it
            logger.info("part %s of %s waits for the next scan", n, pid)
        finally:
            SCAN_LOCK.release()


def _learn(state: Path, pid: str, job: dict, screen: Callable[[str], tuple], save: Callable[[], None],
           ease: Callable[[float], None]) -> None:
    target = fetched.root(state) / pid / str(job["zim"])
    with zim_reader.ZIMReader(str(target)) as z:
        at, kept = int(job["at"]), int(job["articles"])
        parts: list[str] = []
        size, began = 0, time.monotonic()
        while at < z.entry_count:
            if pid in _stop:
                return
            i, at = at, at + 1
            try:
                e = z.get_entry(i)
                if e.is_redirect or e.namespace not in ("A", "C") or not str(e.mime_type).startswith("text/html"):
                    continue
                a = z.get_article(i)
                text = a.plain_text if a is not None else ""
                title = str(a.title or a.url)[:200] if a is not None else ""
            except Exception:  # noqa: BLE001 - one article that can't be read leaves out that one
                continue
            if len(text.strip()) < 200:
                continue
            good, _why = screen(f"{title}\n\n{text}")
            if good is None:
                continue
            parts.append(good)
            kept += 1
            size += len(good.encode("utf-8"))
            if size >= PART_TEXT:
                _keep_part(state, pid, job, parts, at=at, kept=kept)
                save()
                parts, size = [], 0
            if kept % 50 == 0:
                ease(time.monotonic() - began)         # about half of one CPU
                began = time.monotonic()
        if parts:
            _keep_part(state, pid, job, parts, at=at, kept=kept)
        job.update(at=at, articles=kept, done=True, line="")
        save()


def learn_now(state: Path | str, registry: Any, screen: Any, *, ease: Callable[[float], None] = time.sleep) -> None:
    """Take in what was dropped, and learn all that is unfinished, one at a time. Runs on :func:`look`'s thread."""
    state = Path(state)
    data = _load(state)
    if fetched.away_line(state):
        return

    def save() -> None:
        # A library she removed while this ran stays removed.
        data["jobs"] = {k: v for k, v in data["jobs"].items() if (fetched.root(state) / k).is_dir()}
        _save(state, data)
    for path in dropped(state):
        if path.name in data["bad"] and data["bad"][path.name].get("size") == _stat_size(path):
            continue
        _take_in(state, path, data, registry)
    save()
    for pid, job in list(data["jobs"].items()):
        if not isinstance(job, dict) or job.get("done") or pid in _stop or not (fetched.root(state) / pid).is_dir():
            continue
        if not callable(screen):
            job["line"] = NO_SCREEN
            save()
            continue
        job["line"] = ""
        _now_on[str(state)] = pid
        try:
            _learn(state, pid, job, screen, save, ease)
        except fetched.StoreAway as exc:               # the drive is gone or full: it goes on from here next time
            job["line"] = str(exc)
        except (OSError, ValueError):
            job["line"] = CANT_READ_ZIM
        finally:
            save()
            _now_on.pop(str(state), None)


def _stat_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def learning(state: Path | str) -> bool:
    thread = _running.get(str(Path(state)))
    return thread is not None and thread.is_alive()


def look(state: Path | str, registry: Any, screen: Any) -> bool:
    """Start the learner if there is work and it isn't running. Cheap: a look at the store and one small file."""
    key = str(Path(state))
    with _lock:
        if learning(state):
            return True
        unfinished = any(isinstance(j, dict) and not j.get("done") for j in _load(state)["jobs"].values())
        if not dropped(state) and not (unfinished and callable(screen)):
            return False
        thread = threading.Thread(target=_run, args=(state, registry, screen), name="puffpai-zim-learning", daemon=True)
        _running[key] = thread
        thread.start()
        return True


def _run(state: Path | str, registry: Any, screen: Any) -> None:
    try:
        learn_now(state, registry, screen)
    except Exception:  # noqa: BLE001 - nothing is lost: the next look goes on from the last part kept
        logger.exception("learning a dropped library stopped")


__all__ = ["BookRefused", "article", "articles", "dropped", "learn_now", "learning", "libraries", "look", "read_text",
           "remove", "waiting"]
