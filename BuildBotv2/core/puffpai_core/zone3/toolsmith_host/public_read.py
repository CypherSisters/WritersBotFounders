"""One public page, read by the Core for a cloud build (D121, 09 section 3).

After her one plan Yes, a build on a cloud model may read any public web page, read only, and follow its links. Toolsmith
asks over its token route; the Core sends, so nothing here runs in Toolsmith's process and no key or cookie is ever on it.

What stops a read, each checked at send time and again on every redirect: her live Yes holding ``reads_public``, a cloud
build still running, the switch (``policy.READ_PUBLIC_PAGES``), the Bridge (closed, her Off, a block), a plain https address
on a public name, and the outbound screen on the whole address. Money limits how much it reads: her cap and a job's share,
in the sender on every model turn. What comes back is data: text kinds only, lines aimed at an AI hidden, keys scrubbed, a
plain window of the text. Nothing in a page is acted on here.
"""

from __future__ import annotations

import html
import http.client
import json
import re
import urllib.parse
from typing import Any

from .. import hosts, policy
from ...zone1 import secret_detector
from ..toolsmith import find as find_mod
from ..toolsmith.errors import Refused
from . import address, knowledge as knowledge_mod, redact
from .ports import Redirected, strip_hidden

WINDOW_CHARS = 15_000
MAX_BYTES = 2_000_000
TIMEOUT_S = 20.0
MAX_REDIRECTS = 5
MAX_LINKS = 200
LINK_TEXT_CHARS = 120
LINK_URL_CHARS = 2048

OFF_LINE = "Reading public pages is off on this bot, so nothing was sent."
LOCAL_LINE = "Toolsmith reads public pages only on a cloud model, so nothing was sent."
NO_YES_LINE = "Your Yes for this plan does not cover reading public pages, or it was taken back, so nothing was sent."
ENDED_LINE = "This build is not running, so nothing was sent."
NOT_PLAIN_LINE = "That address is not a plain https address on the usual port, so nothing was sent."
NOT_PUBLIC_LINE = "That address is on this computer or your home network, not a public name, so nothing was sent."
HELD_LINE = "That address held a key or a personal detail, so nothing was sent."
CANT_SCREEN_LINE = "BuildBot could not run its privacy check on that address, so nothing was sent."
CANT_SEND_LINE = "BuildBot can't send a public read here, so nothing was sent."
MOVED_NOWHERE_LINE = "That page answered with a redirect to nowhere, so nothing more was sent."
TOO_MANY_LINE = "That page redirected too many times, so nothing more was sent."
NOT_TEXT_LINE = "That is not text, so it was not read."
CANT_CLEAN_LINE = "BuildBot could not check that page's words, so none of it was passed on."
BIG_LINE = "[BuildBot read only the first 2 MB of this page.]"
FOUND_CLOUD_LINE = "Toolsmith tries a found app only on a cloud build."
FOUND_NO_YES_LINE = "Your Yes for this plan does not cover trying other apps, or it was taken back, so it was not tried."

_HTML = ("text/html", "application/xhtml+xml")
_MARKDOWN = ("text/markdown", "text/x-markdown")
_CHARSET = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]{1,40})", re.I)
_MD_LINK = re.compile(r"\[([^\[\]\n]{0,300})\]\((https?://[^\s()<>]{1,2048})\)")
_BARE_LINK = re.compile(r"https?://[^\s<>\"'()\[\]{}\\]{1,2048}")
NL = chr(10)
_TAG = re.compile(r"<!--|<(/?)([a-zA-Z][a-zA-Z0-9:-]{0,40})([^<>]*)>")
_HREF = re.compile(r"(?:^|\s)href\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))", re.I)
_SKIPPED = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "object"})
_BLOCKS = frozenset({"address", "article", "aside", "blockquote", "br", "dd", "details", "div", "dl", "dt", "footer", "form",
                     "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
                     "summary", "table", "tr", "ul"})


def _said(line: str) -> dict:
    return {"ok": False, "said": line}


def _live_yes(host: Any, b: dict) -> dict | None:
    """Her live Yes for exactly this plan, or None. Can't tell: None."""
    cid = str((b.get("yes") or {}).get("consent_id") or "")
    try:
        rec = host.ports.consent_record(cid) if cid and not host._yes_ok(b, cid) \
            and host._consent_status(cid) == "granted" else None
    except Exception:  # noqa: BLE001 - can't tell is no Yes
        rec = None
    return rec if isinstance(rec, dict) else None


def found_why_not(host: Any, b: dict) -> str:
    """"" when this build may try an app found after her Yes, else the plain reason: the switch, a cloud model she still
    has chosen, her live Yes for exactly this plan holding ``tries_found``. Can't tell: no."""
    if not policy.TRY_FOUND_APPS or not host.looks_after_yes(b, host.settings()):
        return FOUND_CLOUD_LINE
    return "" if policy.tries_found_yes(_live_yes(host, b)) else FOUND_NO_YES_LINE


def why_not(host: Any, b: dict) -> tuple[str, dict | None]:
    """``("", her plan Yes)`` when this build may read a public page now, else ``(the plain reason, None)``: the switch, a
    cloud model she still has chosen, her live Yes for exactly this plan holding ``reads_public``, the build still running.
    The Bridge decides the rest at the send. Can't tell: no."""
    if not policy.READ_PUBLIC_PAGES:
        return OFF_LINE, None
    if not host.looks_after_yes(b, host.settings()):
        return LOCAL_LINE, None
    rec = _live_yes(host, b)
    if rec is None or not policy.reads_public_yes(rec):
        return NO_YES_LINE, None
    if not host.running(b["id"]):
        return ENDED_LINE, None
    return "", rec


def _joined_name(decoded: str, household: Any) -> bool:
    """A person's name of two or more parts, written as one word anywhere in the address: "marisolvega". A name of one
    part is a whole word only (:func:`.knowledge.names_a_household_name`), so "bookmarisol" stays."""
    folded = knowledge_mod._fold(decoded)
    return any(len(parts) >= 2 and "".join(parts) in folded
               for parts in (knowledge_mod._letters(knowledge_mod._fold(name)) for name in household or []))


def _held(host: Any, url: str) -> str:
    """"" when the whole address may go; else the line why it may not. The outbound screen and the key shapes, on the
    address as read back ("jane%40example.com" is an email to anyone who decodes it), with a hyphen as a space too, and the
    household's names. A screen that can't run sends nothing."""
    screen = getattr(host.ports, "screen_outbound", None)
    household = host._household()
    if not callable(screen) or household is None:
        return CANT_SCREEN_LINE
    decoded = urllib.parse.unquote_plus(url)
    parts = urllib.parse.urlsplit(url)
    # Each path part on its own: the screen reads "/" as a key's character, so "api.example.com/res/v1/web/search" whole
    # reads as a token beside the word "api". A key inside one part still shows, and the named key shapes read it whole.
    pieces = " | ".join([parts.hostname or "", *urllib.parse.unquote(parts.path).split("/"),
                         urllib.parse.unquote_plus(parts.query)])
    try:
        if secret_detector.find(decoded):
            return HELD_LINE
        for form in dict.fromkeys((pieces, pieces.replace("-", " "))):
            if screen(form) != form or secret_detector.find(form):
                return HELD_LINE
        if knowledge_mod.names_a_household_name(decoded, knowledge_mod.name_words(household)) \
                or _joined_name(decoded, household) \
                or knowledge_mod._spelled_address(re.findall(r"[^\W_]+", decoded)):
            return HELD_LINE
    except Exception:  # noqa: BLE001 - no privacy check: nothing is sent
        return CANT_SCREEN_LINE
    return ""


def _check(host: Any, url: Any) -> tuple[str, str]:
    """``(line, "")`` when this hop stops here, else ``("", the address as sent)``."""
    plain = address.public_form(url)
    if plain is None:
        return NOT_PLAIN_LINE, ""
    held = _held(host, plain)
    if held:
        return held, ""
    name = urllib.parse.urlsplit(plain).hostname or ""
    if not hosts.is_dns_name(name):
        refused = getattr(host.ports, "public_refused", None)
        if callable(refused):
            refused(name, "inside_address")
        return NOT_PUBLIC_LINE, ""
    return "", plain


def _page(text: str) -> tuple[str, str, list[tuple[str, str]]]:
    """An HTML page's title, its words line by line (scripts and styles gone, comments never read) and its links. One
    pass over the page, in time that grows with its size: ``html.parser`` took minutes on a 2 MB page of ``<a <a <a``. A tag
    ends at its first ``>``, so a ``>`` inside an attribute leaves the rest of that tag in the words."""
    words: list[str] = []
    title: list[str] = []
    links: list[tuple[str, str]] = []
    link: list[Any] | None = None
    in_title = False
    at = 0

    def end_link() -> None:
        nonlocal link
        if link is not None:
            links.append((html.unescape(link[0]), html.unescape("".join(link[1]))))
            link = None

    while True:
        m = _TAG.search(text, at)
        data = text[at:m.start()] if m else text[at:]
        if data:
            (title if in_title else words).append(data)
            if link is not None and not in_title:
                link[1].append(data)
        if not m:
            break
        at = m.end()
        if m.group(0) == "<!--":
            shut = text.find("-->", at)
            if shut < 0:
                break
            at = shut + 3
            continue
        closing, tag, attrs = bool(m.group(1)), m.group(2).lower(), m.group(3)
        if tag in _SKIPPED and not closing:
            if attrs.endswith("/"):
                continue
            shut_m = re.compile(rf"</{tag}\s*>", re.I).search(text, at)
            if not shut_m:
                break
            at = shut_m.end()
            continue
        if tag == "title":
            in_title = not closing
        elif tag == "a":
            end_link()
            href = _HREF.search(attrs) if not closing else None
            if href:
                link = [href.group(1) or href.group(2) or href.group(3) or "", []]
        if tag in _BLOCKS:
            words.append(NL)
        elif tag in ("td", "th"):
            words.append(" ")
    end_link()
    lines = (" ".join(line.split()) for line in html.unescape("".join(words)).split(NL))
    return " ".join(html.unescape("".join(title)).split()), NL.join(x for x in lines if x), links


def _links(found: list[tuple[str, str]], base: str, hide: Any) -> list[dict]:
    """The page's links as ``{text, url}``: absolute http(s), each once, no fragment, at most :data:`MAX_LINKS`. Outside
    words: a link's text that reads like words for an AI goes, and so does a link whose address does."""
    out: list[dict] = []
    seen: set[str] = set()
    for href, text in found:
        if len(out) >= MAX_LINKS:
            break
        try:
            url = urllib.parse.urldefrag(urllib.parse.urljoin(base, href.strip())).url
            scheme = urllib.parse.urlsplit(url).scheme.lower()
        except ValueError:
            continue
        if scheme not in ("http", "https") or url in seen or len(url) > LINK_URL_CHARS or hide(url) != url:
            continue
        seen.add(url)
        said = strip_hidden(" ".join(str(text).split()))[:LINK_TEXT_CHARS]
        out.append({"text": said if hide(said) == said else "", "url": url})
    return out


def _text_links(text: str) -> list[tuple[str, str]]:
    """The links in markdown, plain text or JSON: markdown's first, for their words, then bare addresses."""
    found = [(m.group(2), m.group(1)) for m in _MD_LINK.finditer(text)]
    return found + [(m.group(0).rstrip(".,;:!?*_`"), "") for m in _BARE_LINK.finditer(text)]


def _kind(headers: dict) -> str:
    """``html``, ``text`` (markdown, plain), ``json``, or "" for anything else, and for an answer still packed."""
    got = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if got.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
        return ""
    main = got.get("content-type", "").split(";")[0].strip().lower()
    if main in _HTML:
        return "html"
    if main in _MARKDOWN or main == "text/plain":
        return "text"
    if main in ("application/json", "text/json") or (main.startswith("application/") and main.endswith("+json")):
        return "json"
    return ""


def _decode(headers: dict, data: bytes) -> str:
    charset = next((m.group(1) for k, v in (headers or {}).items() if str(k).lower() == "content-type"
                    for m in [_CHARSET.search(str(v))] if m), "utf-8")
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def page_reply(ports: Any, url: str, status: int, headers: dict, data: bytes, start: int = 0) -> dict:
    """What Toolsmith gets of one answer. Everything is cleaned over the whole page, and only then is a window cut, so a
    line split by a window can't slip past the screen."""
    kind = _kind(headers)
    if not kind or b"\x00" in data[:4096]:
        return _said(NOT_TEXT_LINE)
    text = _decode(headers, data)
    title = ""
    if kind == "html":
        title, text, found = _page(text)
    else:
        if kind == "json":
            try:
                text = json.dumps(json.loads(text), indent=1, ensure_ascii=False)    # one line per value, so one hit hides one line
            except (ValueError, RecursionError):
                pass
        found = _text_links(text)
    if len(data) >= MAX_BYTES:
        text += "\n" + BIG_LINE
    try:
        hide = ports.hide_ai_lines
        text = strip_hidden(hide(text))
        title = strip_hidden(title)
        title = (title if hide(title) == title else "")[:300]
        links = _links(found, url, hide) if start == 0 else []
    except Exception:  # noqa: BLE001 - no screen: nothing reaches Toolsmith
        return _said(CANT_CLEAN_LINE)
    text = redact.scrub_text(text)
    # Addresses lose key shapes only: "/docs/bearer-auth" is a page, not a key.
    links = [{"text": redact.scrub_text(x["text"]), "url": redact.scrub_text(x["url"], bearer=False)} for x in links]
    return {"ok": True, "status": int(status), "url": url, "title": redact.scrub_text(title),
            "text": text[start:start + WINDOW_CHARS], "links": links, "cut": start + WINDOW_CHARS < len(text),
            "total": len(text)}


def read(host: Any, b: dict, body: dict) -> dict:
    """One read, redirects followed by hand: every hop is checked again as if it were asked for by itself."""
    start = body.get("start")
    start = start if isinstance(start, int) and not isinstance(start, bool) and start > 0 else 0
    send = getattr(host.ports, "public_get", None)
    if not callable(send):
        return _said(CANT_SEND_LINE)
    current = body.get("url")
    for _hop in range(MAX_REDIRECTS + 1):
        why, rec = why_not(host, host.store.read(b["id"]) or b)     # her Yes taken back or the build ended: the next hop stops
        if why:
            return _said(why)
        line, url = _check(host, current)
        if line:
            return _said(line)
        try:
            status, headers, data = send(url, build_id=b["id"], consent=rec, max_bytes=MAX_BYTES, timeout_s=TIMEOUT_S)
        except Redirected as exc:
            if not exc.location:
                return _said(MOVED_NOWHERE_LINE)
            try:
                current = urllib.parse.urljoin(url, exc.location)
            except ValueError:
                return _said(NOT_PLAIN_LINE)
            continue
        except Refused as exc:
            return _said(redact.scrub_text(str(exc)))
        except (OSError, http.client.HTTPException) as exc:
            why = find_mod.net_reason(exc)             # fixed words, never the error's own text
            return _said("That address could not be reached" + (f": {why}." if why else "."))
        except Exception:  # noqa: BLE001 - never echoed
            return _said("That address could not be reached.")
        return page_reply(host.ports, url, status, headers, data, start)
    return _said(TOO_MANY_LINE)


__all__ = ["MAX_BYTES", "MAX_LINKS", "MAX_REDIRECTS", "TIMEOUT_S", "WINDOW_CHARS", "found_why_not", "page_reply", "read",
           "why_not"]
