"""The big downloads a plan may tick (D99, 03 "The third leg"): an offline library from Kiwix or a file from Data.gov.

One at a time, in steps, by the Core itself under the build's own Bridge subject, into
``<data folder>/knowledge/<package id>/``, each with its own ``puffpai.json`` of kind ``knowledge`` and a Registry
row, so the brain sees it as installed. What the Library can search is the text kept as plain files in that folder,
screened by Drawbridge's text rules like every other fetched page. The Bridge follows no redirect, so an address that
answers with one ends that download with a plain line and nothing more is sent.

- Kiwix: its .meta4 first, for the file's size and SHA-256 (refused if they do not read, or if no mirror on her card is
  listed); bytes from the first of those mirrors that answers, in ranges, kept when one fails and another takes over
  (measured 10-30: Kiwix's own mirror gave 0.1 MB/s, the others 3 MB/s); refused if the hash does not match. Then the articles whose
  titles are about the topics, or all of them for a small library, up to the row's caps, are kept as text. The whole
  file stays in the folder.
- Data.gov: one file on an agency's own https ``.gov`` host, named on the card, cut at the row's size cap. A PDF is
  read to text. Anything else that is not text stays out.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ...zone1 import fetched, zim_reader

CHUNK_MIN = 16_000_000
#: Never fewer steps than a library's size asks, so a big one fits the fetch's step limit.
CHUNK_SHARE = 1000
STEP_TIMEOUT_S = 240.0
SCAN_PER_STEP = 20_000
READ_PER_STEP = 25
_PACKAGE_MAX = 39
#: The most text kept from one file (the Library reads a file whole; Drawbridge screens all of it first).
TEXT_MAX = 5_000_000


class DownloadFailed(Exception):
    """A plain line for her; the rest of the plan goes on."""


def package_id(source: str, item: str) -> str:
    """``kn-<source>-<item>``, in the Registry's id form (a letter first, at most 40)."""
    tail = fetched.slug(item)[: _PACKAGE_MAX - len(source) - 4].strip("-") or "item"
    digest = hashlib.sha256(item.encode("utf-8")).hexdigest()[:4]
    return f"kn-{source}-{tail[:_PACKAGE_MAX - len(source) - 9].strip('-')}-{digest}"


def size_said(n: int) -> str:
    return f"{n / 1_000_000_000:.1f} GB" if n >= 1_000_000_000 else f"{max(1, round(n / 1_000_000))} MB"


def fetch_range(send: Any, url: str, part: Path, size: int) -> int:
    """One step: the next range of *url* into *part*. Bytes held after it."""
    at = part.stat().st_size if part.exists() else 0
    want = max(CHUNK_MIN, size // CHUNK_SHARE)
    status, body = send(url, {"Range": f"bytes={at}-{at + want - 1}", "Accept": "*/*"}, want, STEP_TIMEOUT_S)
    if status not in (200, 206) or not body:
        raise DownloadFailed(f"it answered {status}")
    if status == 200 and at:
        raise DownloadFailed("it would not send the rest of the file")
    with part.open("ab") as fh:
        fh.write(body)
    return at + len(body)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(folder: Path, pid: str, offer: dict, url: str, sha: str, size: int) -> None:
    """The folder's ``puffpai.json`` (02 "Kinds": a ``sources`` list with URLs and hashes). The place's own words go
    through the same screen as titles."""
    from .knowledge import _safe_title
    title = _safe_title(offer.get("title"), pid)
    manifest = {"schema": 1, "id": pid, "kind": "knowledge", "name": title[:80],
                "version": "1.0.0", "description": _safe_title(offer.get("summary"), title)[:200],
                "sources": [{"url": url, "sha256": sha, "size_mb": round(size / 1_000_000, 1),
                             "license": _safe_title(offer.get("license"), "See its source")[:200], "title": title[:200]}]}
    (folder / "puffpai.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def picked_entries(path: Path, at: int, picked: list[int], need: set[str], every: bool, most: int,
                   about: Any) -> tuple[int, list[int], bool]:
    """One step's scan of a ZIM's list of entries: ``(next entry, entries picked so far, finished)``."""
    with zim_reader.ZIMReader(str(path)) as z:
        end = min(z.entry_count, at + SCAN_PER_STEP)
        for i in range(at, end):
            if len(picked) >= most:
                return z.entry_count, picked, True
            try:
                e = z.get_entry(i)
            except (ValueError, IndexError, OSError):
                continue
            if e.is_redirect or e.namespace not in ("A", "C") or not str(e.mime_type).startswith("text/html"):
                continue
            if every or about(f"{e.title} {e.url}", need):
                picked.append(i)
        return end, picked, end >= z.entry_count


def read_articles(path: Path, picked: list[int]) -> list[tuple[str, str]]:
    """``(title, plain text)`` of these entries, leaving out any that can't be read."""
    out = []
    with zim_reader.ZIMReader(str(path)) as z:
        for i in picked:
            try:
                a = z.get_article(i)
            except (ValueError, OSError):
                continue
            text = a.plain_text if a is not None else ""
            if a is not None and len(text.strip()) >= 200:
                out.append((str(a.title or a.url)[:200], text))
    return out


def pdf_text(path: Path) -> str:
    """A PDF's own words, "" when it has none it can give (a scan). The reader is optional at run time: without it
    the file is kept and the line says so."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DownloadFailed("PuffPAI's PDF reader is not installed, so its text could not be read") from None
    try:
        return "\n\n".join(t for t in ((p.extract_text() or "") for p in PdfReader(str(path)).pages) if t)
    except Exception as exc:  # noqa: BLE001 - a broken or hostile PDF is that one file's problem
        raise DownloadFailed(f"its text could not be read ({type(exc).__name__})") from None


def remove_part(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


__all__ = ["DownloadFailed", "SCAN_PER_STEP", "READ_PER_STEP", "STEP_TIMEOUT_S", "TEXT_MAX", "fetch_range", "package_id",
           "pdf_text", "picked_entries", "read_articles", "remove_part", "sha256_of", "size_said",
           "write_manifest"]
