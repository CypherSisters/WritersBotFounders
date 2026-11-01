# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/integrate/host_tools.py (adapted: page_text, plain_read, read_page, _read_question, _start_question)
"""``read_app(id)`` and ``open_app(id)``: the brain's way into apps with no MCP server (01 point 5).

- ``read_app`` reads the app's page the way a person sees it: a headless
  Chromium-family browser (the owner's default when it is one,
  ``page_reader``), else one plain HTTP read of its HTML. 127.0.0.1 only.
  On a stopped package it asks once (the same Yes as "start"), then Steward
  starts it.
- ``open_app`` gives the Desktop the app's address to open in the owner's
  real browser, in a new tab. Outside apps are never shown inside our own
  screen (standing rule "outside apps open in the owner's real browser").
- ``frame_view`` answers ``GET /v1/packages/<id>/frame`` (D82, D83): only a
  page built for PuffPAI, on its own origin (D53), shows inside a Home tile.
  An outside app always opens in her browser; it gets only its address for
  the tile's Open button.

A shown address never carries a sign-in (v1 ``8364d899``).
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Optional

from . import page_reader

READ_CHARS = 7000
READ_LINKS = 25
PLAIN_BYTES = 2 * 1024 * 1024
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


class _Text(HTMLParser):
    """The visible text of a page: title, headings, blocks, links."""

    SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})
    BLOCK = frozenset({"p", "div", "li", "tr", "section", "article", "header", "footer", "h1", "h2", "h3", "h4",
                       "h5", "h6", "br", "td", "th", "dt", "dd", "main", "nav", "aside", "ul", "ol", "table",
                       "button", "a"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title, self.lines, self.links, self._buf = "", [], [], []
        self._skip, self._in_title, self._href, self.description = 0, False, "", ""

    def _flush(self, prefix: str = "") -> None:
        text = " ".join(" ".join(self._buf).split())
        self._buf = []
        if text and (not self.lines or self.lines[-1] != prefix + text):
            self.lines.append(prefix + text)

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "meta" and not self.description and str(
                attrs.get("name") or attrs.get("property") or "").lower() in ("description", "og:description"):
            self.description = " ".join(str(attrs.get("content") or "").split())[:300]
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self._flush()
        if tag == "a":
            self._href = str(attrs.get("href") or "")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            if tag == "a" and self._buf and self._href and len(self.links) < READ_LINKS:
                self.links.append(f"{' '.join(' '.join(self._buf).split())[:60]} -> {self._href[:120]}")
            self._flush("# " if tag in ("h1", "h2", "h3") else "")

    def handle_data(self, data):  # noqa: ANN001
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._buf.append(data)


def page_text(html: str) -> dict:
    t = _Text()
    try:
        t.feed(str(html or ""))
        t.close()
    except Exception:  # noqa: BLE001 - a broken page still gives what was read
        pass
    t._flush()  # noqa: SLF001
    text = "\n".join(t.lines)
    return {"title": " ".join(t.title.split())[:200], "text": text[:READ_CHARS], "cut": len(text) > READ_CHARS,
            "links": t.links, "description": t.description}


#: Said when a page redirects anywhere but its own host and port.
LEFT_LINE = "That address redirects to another host or port, so it was not followed and nothing from there was used."


class _LeftThisPlace(OSError):
    """A redirect that leaves the page's own scheme, host and port."""


class _SamePlaceRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect on the same host and port (an app's own ``/`` to its ``/login``), and nothing else: a page
    can't send the Core's GET to another service on this computer, or off it. Compared as the request connects
    (``Request.host``), so a sign-in part or an odd spelling in the address can't hide where it goes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and (new.type, (new.host or "").lower()) != (req.type, (req.host or "").lower()):
            fp.close()
            raise _LeftThisPlace(code)
        return new


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _SamePlaceRedirects())


def _local(url: str) -> bool:
    return (urllib.parse.urlsplit(url).hostname or "").lower() in _LOOPBACK


def plain_read(url: str) -> Optional[dict]:
    """One plain GET of *url* (127.0.0.1 only): title, description and visible text. None unless 200."""
    if not _local(url):
        return None
    try:
        with _opener().open(url, timeout=15) as resp:
            if resp.status != 200:
                return None
            raw = resp.read(PLAIN_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
    except (OSError, ValueError):
        return None
    got = page_text(raw.decode(charset, "replace"))
    if got.get("description"):
        got["text"] = (got["description"] + "\n" + got["text"]).strip()[:READ_CHARS]
    return got


def _answers(url: str) -> bool:
    try:
        with _opener().open(url, timeout=10):
            return True
    except _LeftThisPlace:
        raise
    except urllib.error.HTTPError:
        return True                 # it answered (a login redirect, a 401, a 404 page)
    except (OSError, ValueError):
        return False


def read_page(url: str, *, browser: bool = True) -> dict:
    """Read *url* (127.0.0.1 only) in a headless browser, else as plain HTML."""
    if not _local(url):
        return {"ok": False, "said": "Only apps on this computer can be read."}
    try:
        if not _answers(url):
            return {"ok": False, "said": "Nothing answers at that address yet."}
    except _LeftThisPlace:
        return {"ok": False, "said": LEFT_LINE}
    seen = page_reader.read(url) if browser else None
    if seen is not None:
        lines = [ln.strip() for ln in str(seen.get("text") or "").splitlines() if len(ln.strip()) > 2]
        text = "\n".join(lines)
        got = {"title": str(seen.get("title") or "")[:200], "text": text[:READ_CHARS], "cut": len(text) > READ_CHARS,
               "links": [f"{t} -> {h}" for t, h in (seen.get("links") or [])][:READ_LINKS], "how": "browser"}
    else:
        got = plain_read(url)
        if got is None:
            return {"ok": False, "said": "Its page could not be opened (no browser could be driven here, "
                                         "and a plain read got no page)."}
        got["how"] = "plain"
    if not got["text"].strip():
        return {"ok": False, "said": "Its page showed no text yet; it may still be loading.", "title": got["title"]}
    return {"ok": True, "url": url, **got}


def app_url(row: dict, manifest: Optional[dict] = None) -> str:
    """The app's main page on this computer, or "" (its first ``ui.pages`` entry, then its health URL)."""
    port = row.get("port")
    pages = (row.get("ui") or {}).get("pages") or ((manifest or {}).get("ui") or {}).get("pages") or []
    for page in pages:
        if not isinstance(page, dict):
            continue
        if page.get("url"):
            url = str(page["url"])
            if "{port}" in url and not port:
                continue
            return url.replace("{port}", str(port or ""))
        if port and page.get("path") is not None:
            return f"http://127.0.0.1:{port}{page['path'] if str(page['path']).startswith('/') else '/' + str(page['path'])}"
    health = ((manifest or {}).get("health") or {}).get("url")
    if health and port:
        return str(health).replace("{port}", str(port))
    return ""


def _plain_url(url: str) -> str:
    """The address without a sign-in in it."""
    parts = urllib.parse.urlsplit(url)
    if parts.username or parts.password:
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        parts = parts._replace(netloc=netloc)
    return urllib.parse.urlunsplit(parts)


Find = Callable[[str], Optional[tuple[dict, Optional[dict]]]]      # id or name -> (row, manifest)
Start = Callable[[str], dict]                                       # id -> {ok, port?, said?}


def start_question(row: dict) -> dict:
    """The one Yes a read of a stopped app asks. ``no_card``/``said_no``: the plain answer when no Yes
    card can be shown here, or she says No, so the brain can tell her and never stalls."""
    name = row.get("name") or row["id"]
    return {"title": f"May I start {name}?", "package": row["id"], "kind": "start",
            "detail": "It runs on this computer, bound to 127.0.0.1.",
            "no_card": f"{name} is stopped, and no Yes card to start it can be shown here. Start it from "
                       "the Desktop's tray (or: puffpai start " + row["id"] + "), then ask again.",
            "said_no": f"You said No, so {name} was not started and its page was not read."}


def read_app(find: Find, start: Optional[Start], args: dict, browser: bool = True) -> dict:
    hit = find(str(args.get("id") or ""))
    if hit is None:
        return {"ok": False, "said": "There is no app by that name here."}
    row, manifest = hit
    if row.get("status") != "running":
        if start is None:
            return {"ok": False, "said": f"{row['name']} is not running, and the Core cannot start packages yet."}
        got = start(row["id"]) or {}
        if not got.get("ok"):
            return {"ok": False, "said": got.get("said") or f"{row['name']} is not running and did not start."}
        row = {**row, "status": "running", "port": got.get("port") or row.get("port")}
    url = app_url(row, manifest)
    if not url:
        return {"ok": False, "said": f"{row['name']} has no page to read."}
    path = str(args.get("path") or "").strip()
    if path:
        if not path.startswith("/") or path.startswith("//"):
            return {"ok": False, "said": "Give a path on the app, like /news."}
        url = urllib.parse.urljoin(url, path)
    return read_page(url, browser=browser)


def read_question(find: Find, args: dict) -> Optional[dict]:
    """Reading asks nothing; reading a stopped app starts it, and that start asks once."""
    try:
        hit = find(str((args or {}).get("id") or ""))
    except Exception:  # noqa: BLE001 - unsure if it runs: ask, never skip the Yes
        return {"title": "May I start that app?", "kind": "start"}
    if hit is None or hit[0].get("status") == "running":
        return None
    return start_question(hit[0])


def open_app(find: Find, args: dict) -> dict:
    hit = find(str(args.get("id") or ""))
    if hit is None:
        return {"ok": False, "said": "There is no app by that name here."}
    row, manifest = hit
    url = app_url(row, manifest)
    if not url:
        return {"ok": False, "said": f"{row['name']} has no page to open."}
    if row.get("status") != "running":
        return {"ok": False, "said": f"{row['name']} is not running. Ask me to read it, and I will start it first."}
    return {"ok": True, "open": [_plain_url(url)], "said": f"{row['name']} opens in a new tab of your browser."}


# -- D82, D83: an app's page inside a Home tile -------------------------------------------------

#: A page built for PuffPAI, on its own origin (D53): the same sandbox as the Desktop's PAGE_SANDBOX.
PAGE_SANDBOX = "allow-scripts allow-forms allow-popups allow-same-origin"


def _first_page(row: dict) -> Optional[dict]:
    """The first ``ui.pages`` entry of the Registry row."""
    for page in (row.get("ui") or {}).get("pages") or []:
        if isinstance(page, dict) and (page.get("url") or page.get("path") is not None or page.get("file")):
            return page
    return None


def puffpai_own(row: object) -> bool:
    """D13, D83, the owner's words of 10-30: "all third party apps still open in their own webpage. all puffpai store
    apps and tools will open on the side pane or in the dashboard itself." And: "Apps my own bot builds should open in
    the side pane, like PuffPAI Store apps." One rule on the Registry row's origin, which only the Core writes, never
    per app and never from the manifest's kind or show. An allowlist: built in (the repo's own folder for this id), from
    the official PuffPAI store, either tier, or exactly ``made``. Anything else is third party: adopted, a git address,
    her own folder, another store she added, unknown, empty. An app Toolsmith adopts is ``adopted:<url>``, so it stays
    third party: someone else's code. A built-in is compared as plain text and its own folder may not be a link now
    (D108, ``packages.manager.is_builtin_path``): a source folder swapped for a junction to the repo's must not make
    someone else's files count, while a repo under OneDrive or a junction still does. An update with her Yes keeps a row ``made`` while its files change: the make path must decide this when it is built."""
    if not isinstance(row, dict):
        return False
    origin = row.get("origin")
    if not isinstance(origin, str):
        return False
    if origin in ("core", "made"):
        return True
    if origin in ("store:verified", "store:community"):
        from ..packages.store_index import OFFICIAL
        store = row.get("store")
        return isinstance(store, dict) and store.get("source") == OFFICIAL
    if origin.startswith("local:"):
        # WritersBot's rule (the owner, 10-31): a folder she added herself, with her Yes, is her own, so it shows in
        # the side pane like a store app. BuildBot's template keeps the built-in-only rule above this line's history.
        return True
    return False


def shows_inside(row: dict) -> bool:
    """True when the row's first page may show inside the Desktop or the window: its origin is PuffPAI's own
    (:func:`puffpai_own`), and the page is on the pages address (no url of its own), not asked to open in a tab."""
    page = _first_page(row)
    return bool(page) and puffpai_own(row) and not page.get("url") and page.get("show") != "new_tab"


def frame_view(row: dict, *, core_ports: Iterable[Optional[int]], pages_link: Callable[[str], str]) -> dict:
    """``GET /v1/packages/<id>/frame`` (D82, D83): ``{ok, url, kind, sandbox, running, line}``.

    kind "page": a page of PuffPAI's own origin (:func:`puffpai_own`; a path or file, no url, not "new_tab") on
    its own address (D53); always ok. kind "app": anything else with a page. It is never framed (D83): ok is false
    and url is only for the Open button: its own server, http(s) on 127.0.0.1, localhost or ::1 and not one of the
    Core's own ports, else its page on the pages address (where no owner login reaches, D49, D53). kind "none": no
    page."""
    name = row.get("name") or row.get("id") or "This app"
    running = row.get("status") == "running"
    page = _first_page(row)
    if page is None:
        return {"ok": False, "url": "", "kind": "none", "sandbox": "", "running": running,
                "line": f"{name} has no page to show here."}
    if shows_inside(row):
        where = str(page.get("path") or "/" + str(page.get("file") or ""))
        return {"ok": True, "url": pages_link(where), "kind": "page", "sandbox": PAGE_SANDBOX, "running": running,
                "line": f"{name}'s page shows here."}
    url = _plain_url(app_url(row, None))
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme.lower())
    except ValueError:
        port = None
    if (parts.scheme.lower() not in ("http", "https") or (parts.hostname or "").lower() not in _LOOPBACK
            or port is None or port in {int(p) for p in core_ports if p}):
        url = ""
    if not url and not page.get("url"):        # third party with no server of its own: its files, in her browser
        url = pages_link(str(page.get("path") or "/" + str(page.get("file") or "")))
    return {"ok": False, "url": url, "kind": "app", "sandbox": "", "running": running,
            "line": f"{name} opens in your browser, in a new tab."}


__all__ = ["PAGE_SANDBOX", "app_url", "frame_view", "open_app", "puffpai_own", "shows_inside",
           "page_text", "plain_read", "read_app", "read_page", "read_question", "start_question"]
