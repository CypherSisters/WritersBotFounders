"""Knowledge fetched from outside, kept in the bot's data folder (lane I11).

``<data folder>/knowledge/<source>/<slug>.txt``, one plain text file per article or book, and one
``<data folder>/knowledge/<source>/meta.json`` that says, per file, where it came from: its source,
title, address, license, when it was fetched and its size. The Library (``folders.Library``) indexes the
text files like any file in the data folder (the same gate, the same index), so the Concierge's
``search_knowledge`` finds them with no network. ``meta.json`` itself is not indexed.

Only the Core writes here (``toolsmith_host.knowledge``), after Drawbridge's text rules screened the text.
Everything read back (:func:`sources`) is checked again: the data folder is hers and a file there may
have been changed by hand.

Public text is not her data (lane I18d). :func:`save` also writes the Core's own record of each file it wrote
(``<state>/index/fetched.json``, outside the data folder): its path and the SHA-256 of its bytes. The Library
indexes a file whole, without the privacy screen and the vault inbox, only when :func:`written_by_fetch` says
this record has that exact path and those exact bytes. ``meta.json``, a file's name, folder or words never
decide it: a file she put there, or one of these files she changed, gets the full privacy screen.

Where it all goes (D103). She may keep books and downloads in a folder or drive of her own:
``settings/folders.json`` ``knowledge`` names that folder, and the files live in ``<that folder>/knowledge/``, so
her other files there, or a whole drive, are never walked. No such key: ``<data folder>/knowledge`` as before.
:func:`root` says where that is, for reading. :func:`folder_for` is the ONE way to the folder a write goes in, for
every book, article and download: it says plainly when the drive is gone or full, and checks room first.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

#: The sources the Core fetches from, and the only address each item may have.
SOURCES = {"wikipedia": "https://en.wikipedia.org/", "gutenberg": "https://www.gutenberg.org/",
           "appropedia": "https://www.appropedia.org/", "ifixit": "https://www.ifixit.com/",
           "commons": "https://commons.wikimedia.org/"}
#: A knowledge package's own folder (D99): ``kn-<source>-<name>``, what a ticked download keeps. Its address is any
#: https one, since a dataset lives on its agency's own host.
PACKAGE = re.compile(r"^kn-[a-z0-9][a-z0-9-]{1,36}$")
#: A ``.zim`` she dropped in (``zone1.books``): its text is kept in parts, which are not listed one by one.
OWN = "kn-own-"
META = "meta.json"
#: The Core's own record of what its fetch wrote (in ``<state>/index``, never in the data folder).
LEDGER = "fetched.json"
#: The move's record (``zone1.knowledge_store``): until a move has switched over, nothing new is written.
MOVE = "knowledge_move.json"
#: A folder she chose says which bot's it is (``zone1.knowledge_folder``); it is not knowledge.
MARKER = ".puffpai-knowledge.json"
#: Room left on a drive after a write.
SPARE = 100_000_000
#: Room left after a big download on the drive this bot itself runs from.
SPARE_OWN_DRIVE = 5_000_000_000
#: FAT32's largest file: a stick formatted that way can't hold a big offline library.
FAT32_MAX = 4 * 1024 ** 3 - 1
SETTINGS_JUNK = ("BuildBot can't read where your books and downloads go (settings\\folders.json), so nothing new is "
                 "saved until that file is put right.")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
_lock = threading.Lock()


class StoreAway(OSError):
    """The place for books and downloads can't take this now. ``str()`` is the one plain line for her. An OSError,
    so a download's own handling of a disk problem ends that download and nothing else."""


def _setting(state: Path | str, key: str) -> str:
    try:
        got = json.loads((Path(state) / "settings" / "folders.json").read_text(encoding="utf-8")).get(key)
    except (OSError, ValueError, AttributeError):
        got = None
    return got if isinstance(got, str) and os.path.isabs(got) else ""     # never a place relative to wherever this runs


def settings_line(state: Path | str) -> str:
    """The plain line when ``settings/folders.json`` is there but can't be believed, "" when it can or is not there.
    Reading goes on from the default place, but a write there could be in the wrong place, so nothing new is saved."""
    path = Path(state) / "settings" / "folders.json"
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, ValueError):
        return SETTINGS_JUNK
    if not isinstance(got, dict) or not isinstance(got.get("linked") or [], list):
        return SETTINGS_JUNK
    for key in ("data", "knowledge"):
        value = got.get(key)
        if value is not None and not (isinstance(value, str) and value.strip() and os.path.isabs(value)):
            return SETTINGS_JUNK
    return ""


def data_folder(state: Path | str) -> Path:
    """The bot's data folder (``settings/folders.json`` ``data``, else ``<state>/data``), as the Library reads it."""
    got = _setting(state, "data")
    return Path(got) if got else Path(state) / "data"


def chosen(state: Path | str) -> str:
    """The folder she chose for books and downloads, "" when they stay with the data folder."""
    return _setting(state, "knowledge")


def place(state: Path | str) -> Path:
    """The folder that holds the ``knowledge`` folder: the one she chose, else the data folder."""
    got = chosen(state)
    return Path(got) if got else data_folder(state)


def root(state: Path | str) -> Path:
    """Where the books and downloads are, to read them. To write, use :func:`folder_for`."""
    return place(state) / "knowledge"


def drive_of(path: Path | str) -> str:
    r"""``E:``, or ``\\server\share``, or the folder itself where there are no drives."""
    return os.path.splitdrive(str(path))[0] or str(path)


def free_bytes(path: Path | str) -> int | None:
    """Free room on the drive *path* would be on; it need not exist yet. None when the drive is not there."""
    p = Path(path)
    for at in (p, *p.parents):
        try:
            if at.exists():
                return shutil.disk_usage(at).free
        except OSError:
            return None
    return None


def filesystem(path: Path | str) -> str:
    """The drive's file system in capitals (``NTFS``, ``FAT32``, ``EXFAT``), "" when it can't be told."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        drive = os.path.splitdrive(os.path.abspath(str(path)))[0]
        buf = ctypes.create_unicode_buffer(64)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(drive + "\\", None, 0, None, None, None, buf, 64)
        return buf.value.upper() if ok else ""
    except (OSError, AttributeError, ValueError):
        return ""


def size_said(n: int) -> str:
    if n >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.1f} TB"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} GB"
    return f"{max(1, round(n / 1_000_000))} MB" if n else "0 MB"


def moving(state: Path | str) -> bool:
    """A move has not switched to her new place yet (running or stopped): nothing new is written, nothing rescanned."""
    try:
        got = json.loads((Path(state) / "index" / MOVE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(got, dict) and got.get("phase") not in (None, "done") and not got.get("switched")


def _same_drive(one: Path | str, other: Path | str) -> bool:
    """Both on one drive. Linux and Mac have no letters: the disk each is on, or its nearest folder that is there. One
    that can't be read counts as the same: the bigger room is kept."""
    if os.name == "nt":
        return drive_of(one).lower() == drive_of(other).lower()

    def disk(path: Path | str) -> int | None:
        p = Path(path)
        for at in (p, *p.parents):
            try:
                return os.stat(at).st_dev
            except FileNotFoundError:
                continue
            except OSError:
                return None
        return None
    a, b = disk(one), disk(other)
    return a is None or b is None or a == b


def _spare(state: Path | str, where: Path, need: int) -> int:
    big = need >= SPARE and _same_drive(where, Path(state).resolve())
    return SPARE_OWN_DRIVE if big else SPARE


def away_line(state: Path | str, need: int = 0) -> str:
    """Why books and downloads can't be written now, in one plain line that says which drive and what to do;
    "" when they can. Only what is new waits: the rest of the bot, and everything already indexed, keeps working."""
    line = settings_line(state)
    if line:
        return line
    if moving(state):
        return ("Your books and downloads are being moved, so nothing new is saved until the move ends. "
                "If it stopped, pick it up with: puffpai knowledge resume")
    where = place(state)
    drive = drive_of(where)
    free = free_bytes(where)
    if free is None:
        return (f"Your books and downloads are kept on {drive}, and {drive} is not there. Plug it in, or pick "
                "another place with: puffpai knowledge move <folder>")
    if free < need + _spare(state, where, need):
        asks = f" and this needs {size_said(need)}" if need else ""
        return (f"{drive} has {size_said(free)} free{asks}, so nothing new is saved there. Free some space on "
                f"{drive}, or pick another place with: puffpai knowledge move <folder>")
    if need > FAT32_MAX and filesystem(where) == "FAT32":
        return (f"{drive} is formatted FAT32, which can't hold a file of {size_said(need)}. Pick a drive formatted "
                "NTFS or exFAT with: puffpai knowledge move <folder>")
    return ""


def has_files(state: Path | str) -> bool:
    """Anything is kept in the place in use now (a drive that is gone counts as nothing here)."""
    try:
        return any(files for _dir, _subdirs, files in os.walk(root(state)))
    except OSError:
        return False


def folder_for(state: Path | str, source: str, *, need: int = 0) -> Path:
    """THE ONE WAY to the folder a knowledge download or a book goes in: ``<root>/<source>``, made if need be.

    *need* is how many bytes this will write, so a download checks for room before it starts. Raises
    :class:`StoreAway` with the plain line when the drive is gone or full, a move is under way, the settings can't
    be read, the folder she chose is no longer safe, or the store or the source's folder is a link, in either place.
    Nothing is ever written to the default place instead."""
    if not _SLUG.match(str(source or "")):
        raise ValueError("Only a plain folder name can hold knowledge.")
    line = away_line(state, need)
    if line:
        raise StoreAway(line)
    store = root(state)
    own = bool(chosen(state))
    from .knowledge_folder import is_link, still_safe, write_marker
    if own:
        line = still_safe(state, store)
        if line:
            raise StoreAway(line)
    folder = store / source
    for part in (store, folder):
        if is_link(part):
            raise StoreAway(f"{part} is a link to somewhere else, so nothing new is saved there. Remove the link, or "
                            "pick another place with: puffpai knowledge move <folder>")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if own:
            write_marker(state, store)
    except OSError:
        raise StoreAway(f"BuildBot can't write to {store}. Check {drive_of(store)}, or pick another place with: "
                        "puffpai knowledge move <folder>") from None
    return folder


def slug(text: Any, prefix: str = "") -> str:
    """A safe file name part: lower-case letters, digits and dashes only, never empty."""
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    out = "-".join(w for w in [prefix.strip("-").lower()] + words if w)[:90].strip("-")
    return out if _SLUG.match(out or "") else "item"


def known(source: Any) -> bool:
    return str(source) in SOURCES or bool(PACKAGE.match(str(source)))


def _base(source: str) -> str:
    return SOURCES.get(source, "https://")


def _folders(state: Path | str) -> list[str]:
    """The source folders that can hold fetched files: the fixed sources and each package there."""
    try:
        kept = sorted(p.name for p in root(state).iterdir() if p.is_dir() and PACKAGE.match(p.name))
    except OSError:
        kept = []
    return list(SOURCES) + kept


def package_folder(state: Path | str, package_id: str) -> Path:
    """A knowledge package's folder, made if need be. Refuses anything but the package form."""
    if not PACKAGE.match(str(package_id or "")):
        raise ValueError("Only a knowledge package's own name can be a folder here.")
    return folder_for(state, package_id)


def save_file(state: Path | str, source: str, name: str, data: bytes) -> Path:
    """One kept file next to its text (a picture): never indexed, since the Library reads text files only."""
    if not known(source) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,110}\.(?:jpg|png|gif|webp)", str(name or "")):
        raise ValueError("Only a known source and a plain picture name can be kept.")
    folder = folder_for(state, source, need=len(data))
    path = folder / name
    tmp = folder / f".{name}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def _meta_path(state: Path | str, source: str) -> Path:
    return root(state) / source / META


def _read_meta(path: Path) -> dict:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = got.get("items") if isinstance(got, dict) else None
    return items if isinstance(items, dict) else {}


def save(state: Path | str, source: str, name: str, text: str, meta: dict) -> Path:
    """Write one item's text and its row in ``meta.json``. *name* is a :func:`slug`. Returns the text file."""
    if not known(source) or not _SLUG.match(str(name or "")):
        raise ValueError("Only a known source and a plain file name can be kept.")
    data = text.encode("utf-8")
    folder = folder_for(state, source, need=len(data))
    path = folder / f"{name}.txt"
    if path.resolve().parent != folder.resolve():
        raise ValueError("That file would be outside the knowledge folder.")
    _note_written(state, path, source, data)    # first: until the file is there, its record matches nothing
    tmp = folder / f".{name}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, path)
    row = {k: meta.get(k) for k in ("source", "title", "url", "license", "license_url", "credit", "fetched_at",
                                    "revision", "topic", "cut")}
    row.update(source=source, file=path.name, size=len(data))
    with _lock:
        mp = _meta_path(state, source)
        items = _read_meta(mp)
        items[name] = row
        tmp = mp.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": items}, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, mp)
    return path


def _ledger_path(state: Path | str) -> Path:
    return Path(state) / "index" / LEDGER


def _key(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _read_ledger(state: Path | str) -> dict:
    try:
        got = json.loads(_ledger_path(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = got.get("files") if isinstance(got, dict) else None
    return files if isinstance(files, dict) else {}


def _note_written(state: Path | str, path: Path, source: str, data: bytes) -> None:
    """Record that the Core's fetch writes *data* to *path* (rows whose file is gone are dropped)."""
    lp = _ledger_path(state)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        files = {k: v for k, v in _read_ledger(state).items() if isinstance(v, dict) and os.path.isfile(k)}
        files[_key(path)] = {"source": source, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        tmp = lp.with_suffix(".tmp")
        tmp.write_text(json.dumps({"files": files}, indent=1), encoding="utf-8")
        os.replace(tmp, lp)


def written_by_fetch(state: Path | str, path: Path | str, data: bytes) -> bool:
    """True only when *data* are exactly the bytes the Core's own fetch wrote to *path* (:func:`save`), and
    *path* is a text file right inside one source's folder. Anything else, including a file she put in the
    knowledge folder or one of these files she changed, is False: it is her data, and gets the full screen."""
    try:
        p = Path(path).resolve()
        if p.parent.parent != root(state).resolve() or not known(p.parent.name) or p.suffix != ".txt":
            return False
        row = _read_ledger(state).get(_key(p))
    except OSError:
        return False
    if not isinstance(row, dict) or row.get("source") != p.parent.name or row.get("size") != len(data):
        return False
    return hashlib.sha256(data).hexdigest() == row.get("sha256")


def forget(state: Path | str, source: str, name: str) -> None:
    """One removed item (``zone1.books``): its row in ``meta.json`` and the Core's record of it."""
    with _lock:
        mp = _meta_path(state, source)
        items = _read_meta(mp)
        if items.pop(name, None) is not None:
            tmp = mp.with_suffix(".tmp")
            tmp.write_text(json.dumps({"items": items}, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, mp)
        _drop_records(state, lambda k: k == _key(root(state) / source / f"{name}.txt"))


def forget_source(state: Path | str, source: str) -> None:
    """A removed source: the Core's records of its files. Its ``meta.json`` went with its folder."""
    inside = _key(root(state) / source) + os.sep
    with _lock:
        _drop_records(state, lambda k: k.startswith(inside))


def _drop_records(state: Path | str, gone: Any) -> None:
    files = _read_ledger(state)
    kept = {k: v for k, v in files.items() if not gone(k)}
    if len(kept) != len(files):
        lp = _ledger_path(state)
        tmp = lp.with_suffix(".tmp")
        tmp.write_text(json.dumps({"files": kept}, indent=1), encoding="utf-8")
        os.replace(tmp, lp)


def is_meta(path: Path | str, store: Path | str) -> bool:
    """*path* is one of the ``meta.json`` files in the knowledge folder *store*, a download package's ``puffpai.json``
    (``kn-*``), or that folder's marker: the Library leaves those out of the index."""
    p, s = Path(path), Path(store)
    if p.name == MARKER and p.parent == s:
        return True
    if p.parent.parent != s:
        return False
    return p.name == META or (p.name == "puffpai.json" and p.parent.name.startswith("kn-"))


def _s(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def sources(state: Path | str) -> list[dict]:
    """What was fetched, per item: ``{source, title, url, license, size, fetched_at, file}``, newest first.
    A row whose file is gone is left out; an address that is not its source's own is dropped."""
    out = []
    for source in _folders(state):
        if source.startswith(OWN):
            continue
        base = _base(source)
        folder = root(state) / source
        for name, row in _read_meta(folder / META).items():
            if not isinstance(row, dict) or not _SLUG.match(str(name)):
                continue
            path = folder / f"{name}.txt"
            if not path.is_file():
                continue
            url = str(row.get("url") or "")
            out.append({"source": source, "title": _s(row.get("title"), 200) or name,
                        "url": url if url.startswith(base) and len(url) < 500 and url.isascii() else "",
                        "license": _s(row.get("license"), 80), "size": path.stat().st_size,
                        "fetched_at": _s(row.get("fetched_at"), 40), "file": path.name})
    return sorted(out, key=lambda r: r["fetched_at"], reverse=True)


def meta_for(state: Path | str, path: Path | str) -> dict | None:
    """The ``meta.json`` row for one fetched text file (search hits carry its credit), or None."""
    p = Path(path)
    for source in _folders(state):
        base = _base(source)
        folder = root(state) / source
        try:
            if p.resolve().parent != folder.resolve():
                continue
        except OSError:
            continue
        row = _read_meta(folder / META).get(p.stem)
        if isinstance(row, dict):
            url = str(row.get("url") or "")
            return {"source": source, "title": _s(row.get("title"), 200),
                    "url": url if url.startswith(base) and url.isascii() else "",
                    "license": _s(row.get("license"), 80)}
    return None


__all__ = ["LEDGER", "MARKER", "META", "MOVE", "OWN", "PACKAGE", "SOURCES", "StoreAway", "away_line", "chosen", "data_folder",
           "drive_of", "filesystem", "folder_for", "forget", "forget_source", "free_bytes", "has_files", "is_meta", "known", "meta_for", "moving",
           "package_folder", "place", "root", "save", "save_file", "size_said", "slug", "sources", "written_by_fetch"]
