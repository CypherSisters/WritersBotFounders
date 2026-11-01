# Harvested from PuffPAIBuildBot bb-next:src/puffpai/catalogs/searchers/{huggingface,commons}.py (adapted)
"""One small reader per kind of knowledge source (D99): an outside answer in, plain rows out. Every answer is data.
Rows name their reader in ``toolsmith/data/sources.json``. No network here, and nothing here decides what is kept:
that is ``toolsmith_host.knowledge`` and ``knowledge_downloads``. Read with patterns or JSON, no XML parser, so no
entity tricks.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Any

_TAG = re.compile(r"<[^>]{0,500}>")
_BLOCK = re.compile(r"</?(?:p|div|br|li|ul|ol|pre|h[1-6]|tr|table|blockquote)\b[^>]{0,200}>", re.I)
_PICTURE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}
#: The first bytes of the only picture files kept: what a file says it is never decides.
_PICTURE_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"), (b"GIF89a", "gif"))
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")


def _json(body: bytes) -> Any:
    try:
        return json.loads(bytes(body).decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return None


def _s(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def plain(markup: Any) -> str:
    """HTML as plain text: tags out, entities read once, paragraphs kept."""
    text = _BLOCK.sub("\n", str(markup or ""))
    text = html.unescape(_TAG.sub(" ", text))
    return "\n".join(ln for ln in (" ".join(p.split()) for p in text.splitlines()) if ln)


def https_on(url: Any, hosts: Any = None, suffix: str = "") -> str:
    """*url* when it is a plain https address on one of *hosts* (or a name ending in *suffix*), else ""."""
    from ..text_rules import url_is_plain
    text = str(url or "").strip()
    if not text.startswith("https://") or not url_is_plain(text) or len(text) > 600:
        return ""
    try:
        parts = urllib.parse.urlsplit(text)
        if parts.username or parts.port or parts.password:
            return ""
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    ok = (host in set(hosts or ())) or (bool(suffix) and host.endswith(suffix) and bool(_HOST.fullmatch(host)))
    return text if ok else ""


def picture_kind(data: bytes) -> str:
    """"jpg", "png", "gif" or "webp" from a file's own first bytes, "" for anything else (no SVG, no HTML)."""
    head = bytes(data[:16])
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return next((kind for magic, kind in _PICTURE_MAGIC if head.startswith(magic)), "")


# -- iFixit -----------------------------------------------------------------------------------------------------

def ifixit_found(body: bytes) -> list[tuple]:
    """Guides: ``(guide id, title, {url})``."""
    got = _json(body)
    out = []
    for r in (got.get("results") if isinstance(got, dict) else None) or []:
        if not isinstance(r, dict) or r.get("dataType") != "guide" or not isinstance(r.get("guideid"), int):
            continue
        url = https_on(r.get("url"), {"www.ifixit.com"})
        title = _s(r.get("title"), 200)
        if url and title:
            out.append((r["guideid"], title, {"url": url}))
    return out


def ifixit_guide(body: bytes) -> str | None:
    """A guide's own words, step by step."""
    got = _json(body)
    if not isinstance(got, dict) or not isinstance(got.get("steps"), list):
        return None
    parts = [plain(got.get("introduction_rendered"))] if got.get("introduction_rendered") else []
    for n, step in enumerate(got["steps"], 1):
        if not isinstance(step, dict):
            continue
        lines = [_s(ln.get("text_raw"), 2000) for ln in step.get("lines") or [] if isinstance(ln, dict)]
        said = " ".join(ln for ln in lines if ln)
        if said:
            parts.append(f"Step {n}: {_s(step.get('title'), 200)}\n{said}".replace(": \n", "\n"))
    return "\n\n".join(p for p in parts if p) or None


# -- pictures ---------------------------------------------------------------------------------------------------

def _meta(info: dict, key: str) -> str:
    rec = (info.get("extmetadata") or {}).get(key)
    return _s(plain(rec.get("value")), 200) if isinstance(rec, dict) else ""


def commons_found(body: bytes, hosts: Any) -> list[tuple]:
    """Plain pictures with a license Commons names: ``(page id, title, {file, license, who, url})``. No SVG."""
    got = _json(body)
    pages = ((got.get("query") or {}).get("pages") if isinstance(got, dict) else None) or []
    out = []
    for pg in sorted((p for p in pages if isinstance(p, dict)), key=lambda p: p.get("index") or 99):
        infos = pg.get("imageinfo")
        info = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else None
        title = str(pg.get("title") or "")
        if info is None or not title.startswith("File:") or not isinstance(pg.get("pageid"), int):
            continue
        licence = _meta(info, "LicenseShortName")
        file_url = https_on(info.get("thumburl"), hosts)
        if str(info.get("mime") or "") not in _PICTURE_TYPES or not licence or not file_url:
            continue
        page = https_on(info.get("descriptionurl"), {"commons.wikimedia.org"})
        out.append((pg["pageid"], _s(re.sub(r"\.[A-Za-z0-9]{2,5}$", "", title[5:]), 200),
                    {"file": file_url, "license": licence, "who": _meta(info, "Artist") or "Wikimedia Commons",
                     "url": page or f"https://commons.wikimedia.org/?curid={pg['pageid']}",
                     "description": _meta(info, "ImageDescription")}))
    return out


# -- catalogs: what a build could download ----------------------------------------------------------------------

_ENTRY = re.compile(r"<entry\b.*?</entry>", re.S | re.I)
_ACQ = re.compile(r"<link\b[^>]*opds-spec\.org/acquisition[^>]*>", re.I)


def _tag(entry: str, name: str) -> str:
    m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", entry, re.S | re.I)
    return html.unescape(_TAG.sub(" ", m.group(1))) if m else ""


def _attr(link: str, name: str) -> str:
    m = re.search(rf'\b{name}="([^"]*)"', link)
    return html.unescape(m.group(1)) if m else ""


def kiwix_found(body: bytes, meta_host: str) -> list[dict]:
    """Offline libraries in a Kiwix OPDS answer: one row per book, its smallest full issue (never "mini": it holds
    only the first paragraphs). TED and other video books are left out: their worth is video, not text."""
    best: dict[str, dict] = {}
    for m in _ENTRY.finditer(bytes(body).decode("utf-8", errors="replace")):
        entry = m.group(0)
        name, flavour = _s(_tag(entry, "name"), 120), _s(_tag(entry, "flavour"), 20)
        acq = _ACQ.search(entry)
        href = _attr(acq.group(0), "href") if acq else ""
        url = https_on(href, {meta_host})
        try:
            size = int(_attr(acq.group(0), "length")) if acq else 0
        except ValueError:
            size = 0
        if not name or not url or not url.endswith(".zim.meta4") or not size or flavour == "mini" \
                or _s(_tag(entry, "category"), 40) == "ted" or not re.fullmatch(r"[a-zA-Z0-9._-]+", name):
            continue
        row = {"id": name, "title": _s(_tag(entry, "title"), 200) or name, "summary": _s(_tag(entry, "summary"), 300),
               "meta": url, "bytes": size, "publisher": _s(_tag(entry, "name"), 80), "articles":
               int(_tag(entry, "articleCount")) if _tag(entry, "articleCount").strip().isdigit() else 0}
        if name not in best or size < best[name]["bytes"]:
            best[name] = row
    return list(best.values())


def meta4_of(body: bytes) -> dict | None:
    """A Kiwix .meta4 file: ``{file, size, sha256, urls}``. No sha256 or size: None, nothing is downloaded."""
    text = bytes(body).decode("utf-8", errors="replace")
    name = re.search(r'<file\s+name="([A-Za-z0-9._-]{1,200}\.zim)"', text)
    size = re.search(r"<size>(\d{1,16})</size>", text)
    sha = re.search(r'<hash\s+type="sha-256">([0-9a-f]{64})</hash>', text)
    if not (name and size and sha):
        return None
    urls = [html.unescape(u) for u in re.findall(r"<url\b[^>]*>(https://[^<\s]{1,400})</url>", text)]
    return {"file": name.group(1), "size": int(size.group(1)), "sha256": sha.group(1), "urls": urls}


def _pick_file(dists: Any, formats: dict, suffix: str) -> dict | None:
    picks = []
    for d in dists if isinstance(dists, list) else []:
        if not isinstance(d, dict):
            continue
        said = (str(d.get("format") or "") or str(d.get("mediaType") or "").split("/")[-1]).lower().strip()
        kind = formats.get(said)
        url = https_on(d.get("downloadURL"), None, suffix)
        if not kind or not url or "metadata" in str(d.get("title") or "").lower():
            continue
        picks.append((list(formats.values()).index(kind), url, kind))
    if not picks:
        return None
    picks.sort(key=lambda p: p[0])
    return {"url": picks[0][1], "format": picks[0][2]}


def datagov_found(body: bytes, row: dict) -> list[dict]:
    """Public datasets with a file on an agency's own https ``.gov`` host (each host goes on the plan card)."""
    got = _json(body)
    out = []
    for r in (got.get("results") if isinstance(got, dict) else None) or []:
        dcat = r.get("dcat") if isinstance(r, dict) else None
        if not isinstance(dcat, dict) or dcat.get("accessLevel") != "public":
            continue
        picked = _pick_file(dcat.get("distribution"), row["formats"], row["file_host_suffix"])
        slug = str(r.get("slug") or "")
        if not picked or not re.fullmatch(r"[a-z0-9-]{1,120}", slug):
            continue
        licence = _s(dcat.get("license"), 200)
        out.append({"id": slug, "title": _s(dcat.get("title"), 200) or slug, "summary": _s(plain(dcat.get("description")), 300),
                    "file": picked["url"], "format": picked["format"], "publisher": _s(r.get("publisher"), 100),
                    "page": f"https://catalog.data.gov/dataset/{slug}", "license": licence, "bytes": 0})
    return out


_HF_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")


_HF_LICENSE = re.compile(r"[A-Za-z0-9.+-]{1,60}")


def hf_found(body: bytes, row: dict) -> list[dict]:
    """Hugging Face datasets: public, not gated, one license it names. Only to look at (D99): its files answer with a
    redirect to a host that changes, and the Bridge follows no redirect."""
    got = _json(body)
    out = []
    for r in got if isinstance(got, list) else []:
        if not isinstance(r, dict) or r.get("private") is not False or r.get("gated") not in (False, None) \
                or r.get("disabled") is True or not _HF_ID.fullmatch(str(r.get("id") or "")):
            continue
        found = {t[8:] for t in r.get("tags") or [] if isinstance(t, str) and t.startswith("license:")}
        if len(found) != 1 or found & {"other", "unknown"} or not _HF_LICENSE.fullmatch(next(iter(found))):
            continue
        card = r.get("cardData") if isinstance(r.get("cardData"), dict) else {}
        out.append({"id": r["id"], "title": _s(card.get("pretty_name"), 200) or r["id"],
                    "summary": _s(plain(r.get("description")), 300), "license": _s(next(iter(found)), 60).upper(),
                    "page": row["page"].format(id=r["id"]), "publisher": str(r["id"]).split("/")[0]})
    return out


__all__ = ["commons_found", "datagov_found", "hf_found", "https_on", "ifixit_found", "ifixit_guide", "kiwix_found", "meta4_of",
           "picture_kind", "plain"]
