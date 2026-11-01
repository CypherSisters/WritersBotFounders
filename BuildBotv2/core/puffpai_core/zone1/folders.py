# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/knowledge/my_data.py (adapted: data_dir :109,
# linked_folders :135, set_data_dir :219, link_folder :275, unlink_folder :299, _walk :390, ingest_one :509,
# scan_once :654; one Library over the v2 bot folder, the gate before the index) and
# src/puffpai/security/owner_folders.py (pattern only: is_link :106, deny_roots :245, check :378; v2 refuses
# only PuffPAI's own folder so far, the full deny list is not carried over yet)
"""The data folder and linked folders (D28(16), Z1C 3.4 ``path: files``), with a small FTS5 index.

- ``Library(archivist, state)``: the data folder is ``<state>/data`` unless set;
  linked folders are her own folders, registered by path in
  ``<state>/settings/folders.json``.
- A document pinned to the bot (D125, ``zone1.pinned``) is one more file for ``scan()``, listed in that same file.
- ``scan()`` reads every text, Word and PDF file whole (D126), runs the gate (``path: files``), holds
  every private find in the vault automatically (or the sealed inbox while
  locked), and indexes only the gated text (handles and kinds, never values).
- Originals are never modified, moved or renamed: the Library only reads them.
- A file the floor check can't finish is not indexed at all, never half indexed
  (F3, F9). Too big, binary-looking, not text, or text that doesn't decode
  cleanly (``Garbled``, see ``classify.decode_file``): not indexed, with a plain
  reason. A file with a UTF-16/32 mark is also read the Windows-1252 way, and
  both readings go through the gate (anything either finds is held).
- One exception (lane I18d): a file the Core's own knowledge fetch wrote, with exactly the bytes it wrote
  (``fetched.written_by_fetch``, the Core's record in ``<state>/index``), is public reference text, not her
  data: it is indexed whole and nothing of it is held or sealed into the vault inbox. Every other file,
  including one she put in the knowledge folder or a fetched file she changed, gets the gate as above.

Indexing is minimal this week: one FTS5 table of 2,000-character chunks.
Archivist is the one writer of this index (01 Zone 2).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import stat
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from . import pinned
from .classify import CHUNK, Binary, Garbled, TooBig, Unsupported
from .gate import GateClosed, prepare

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml", ".ini", ".cfg",
                 ".conf", ".toml", ".html", ".htm", ".xml", ".rst", ".env"}
#: Read through ``pinned.read_document`` (D126), by the same gate as text.
DOCUMENT_SUFFIXES = {".docx", ".pdf"}
GATE_CLOSED_LINE = "The privacy check couldn't finish, so it isn't searchable."
CANT_READ_LINE = "It can't be read."
#: One background scan or search scan at a time (D126).
SCAN_LOCK = threading.Lock()
#: A folder's row names this many files it couldn't read, and walks this many.
REPORT_NAMES = 3
REPORT_MAX = 20000
#: The files a row lists, to send to an app (D127).
REPORT_FILES = 100
#: Windows's own small files in a folder: not documents she would miss.
_NOT_DOCUMENTS = {"desktop.ini", "thumbs.db"}


def _reads(name: str) -> bool:
    """A file the walk reads, by its name. Word's lock file (``~$novel.docx``) and Windows's own are not documents."""
    if name.startswith("~$") or name.lower() in _NOT_DOCUMENTS:
        return False
    return Path(name).suffix.lower() in TEXT_SUFFIXES or Path(name).suffix.lower() in DOCUMENT_SUFFIXES \
        or name.lower() == ".env"


def _files_said(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"
#: The index's note on a file the Core's own knowledge fetch wrote, indexed whole (lane I18d).
PUBLIC_REASON = "Public reference text this bot fetched, indexed whole; none of it is your data."


class FolderRefused(Exception):
    pass


# -- the one folder check (lane I18f): Library.link, the wizard's vault step and the Developer routes ---------

def canon(path: Path | str) -> str:
    """One spelling per folder: variables and ``~`` expanded, junctions and links followed, 8.3 names made long,
    case folded (Windows). A path that can't be read this way is taken as written, absolute."""
    text = os.path.expandvars(os.path.expanduser(str(path)))
    try:
        real = os.path.realpath(text)
    except (OSError, ValueError):
        real = os.path.abspath(text)
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetLongPathNameW(real, buf, 32768):
                real = buf.value
        except (OSError, AttributeError, ValueError):
            pass
    return os.path.normcase(os.path.normpath(real))


def _overlap(a: str, b: str) -> bool:
    """*a* and *b* are the same folder, or one holds the other (both from :func:`canon`)."""
    if a == b:
        return True
    a_, b_ = a.rstrip("\\/") + os.sep, b.rstrip("\\/") + os.sep
    return a_.startswith(b_) or b_.startswith(a_)


def _vault_places(state: Path) -> list[Path]:
    """Every place a vault of this bot lives: in its folder, and any folder ``settings\\vault.json`` names.
    A vault.json that is there but can't be read raises (fail closed: then no folder is linked)."""
    places = [state / "vault", state / "vault.inbox"]
    path = state / "settings" / "vault.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        got = data.get("folder") if isinstance(data, dict) else None
        for item in ([got] if isinstance(got, str) else list(got or [])):
            p = item.get("path") if isinstance(item, dict) else item
            if not isinstance(p, str) or not p.strip():
                raise ValueError("a vault place in settings is not a path")
            places += [Path(p), Path(p + ".inbox")]
    return places


def folder_problem(state: Path | str, folder: Path | str, *, data: Path | str | None = None,
                   as_data: bool = False) -> str:
    """Why *folder* may not be linked (or, *as_data*, be the data folder), in plain words; "" when it may.

    Never PuffPAI's own folder or any vault of this bot, nor a folder inside one or holding one (by
    :func:`canon`: junctions, case and 8.3 names are the same folder). A linked folder must be there, and is
    not the data folder *data* or inside it. The data folder may be the default ``<bot folder>\\data``."""
    state = Path(state)
    raw = str(folder or "").strip()
    if not raw:
        return "No folder was given."
    if not os.path.isabs(os.path.expandvars(os.path.expanduser(raw))):
        return ("Use the folder's whole path, like C:\\Users\\you\\Documents\\Recipes." if os.name == "nt"
                else "Use the folder's whole path, like /home/you/Documents/Recipes.")
    p = canon(raw)
    if as_data and p == canon(state / "data"):
        return ""
    try:
        vaults = _vault_places(state)
    except (OSError, ValueError):
        return "BuildBot can't read where your vault is kept, so no folder is linked right now."
    if any(_overlap(p, canon(v)) for v in vaults):
        return "That folder holds your vault, or is inside it, so it can't be used."
    if _overlap(p, canon(state)):
        return "PuffPAI's own folder, a folder inside it or one that holds it, can't be used."
    from .fetched import chosen, root
    if chosen(state) and _overlap(p, canon(root(state))):
        return "That is where your books and downloads are kept, or it overlaps it. Your bot reads it already."
    if as_data:
        return ""
    if not os.path.isdir(p):
        return "That folder doesn't exist."
    if data is not None and (p.rstrip("\\/") + os.sep).startswith(canon(data).rstrip("\\/") + os.sep):
        return "That is your data folder, or inside it. Your bot reads it already."
    return ""


# -- the one check for a pinned file (D125): at pin time and again before each read ----------------------------

PIN_SUFFIXES = (".txt", ".md", ".docx", ".pdf")
PIN_BLANK = "No file was given."
PIN_WHOLE_PATH = ("Use the file's whole path, like C:\\Users\\you\\Documents\\My Novel.docx." if os.name == "nt"
                  else "Use the file's whole path, like /home/you/Documents/My Novel.docx.")
PIN_ODD_PATH = "That path is written in a way PuffPAI can't check. Use the file's normal path."
PIN_VAULT_UNREADABLE = "PuffPAI can't read where your vault is kept, so no file can be pinned right now."
PIN_VAULT = "That file is part of your vault, so it can't be pinned."
PIN_OWN = "That file is one of PuffPAI's own, so it can't be pinned."
PIN_GONE = "That file isn't there."
PIN_FOLDER = "That is a folder. Pin one file."
PIN_KIND = "PuffPAI can pin .txt, .md, .docx and .pdf files."
PIN_CANT_CHECK = "PuffPAI can't check that file, so it can't be pinned."

#: ``\\?\`` and ``\\.\`` (after any slash is made a backslash, with any number of them), the same thing in NT's own
#: names (``\??\``, ``\Device\``), and a path that starts with three slashes or more, which names no plain place.
_DEVICE_PATH = re.compile(r"(?i)^(?:\\{2,}[?.]+(?:\\|$)|\\+\?\?(?:\\|$)|\\+device(?:\\|$)|\\{3,})")


def _inside(path: str, place: str) -> bool:
    """*path* is *place* or below it (both from :func:`canon`). A file holds nothing, so this is one way only."""
    return (path.rstrip("\\/") + os.sep).startswith(place.rstrip("\\/") + os.sep)


def _pin_odd_path(path: str) -> bool:
    r"""A path the other rules can't read on Windows: a device path, an NTFS stream (a colon after the drive part),
    or a share on this computer (``\\localhost\c$\...`` is a second name for a place here, so the vault rule would
    miss it). Asked of the path as written and of where it really leads, so a mapped drive can't dodge it either."""
    if os.name != "nt":
        return False
    from .knowledge_folder import _this_computer
    text = path.replace("/", "\\")
    if _DEVICE_PATH.match(text) or ":" in re.sub(r"^[A-Za-z]:", "", text, count=1):
        return True
    return text.startswith("\\\\") and _this_computer(text[2:].split("\\", 1)[0])


def _pin_problem(state: Path, raw: str, data: Path | str | None) -> str:
    from .fetched import root
    from .knowledge_folder import _vaults
    text = os.path.expandvars(os.path.expanduser(raw))
    if _pin_odd_path(text):                            # before canon: a share on this computer is not even touched
        return PIN_ODD_PATH
    if not os.path.isabs(text) or (os.name == "nt" and not os.path.splitdrive(text)[0]):
        return PIN_WHOLE_PATH                          # "\x.txt" is on whichever drive this runs from
    p = canon(raw)
    if _pin_odd_path(p):
        return PIN_ODD_PATH
    try:
        vaults = _vaults(state)                        # _vault_places, and refuses a shape it would read as no place
    except Exception:  # noqa: BLE001 - fail closed
        return PIN_VAULT_UNREADABLE
    if any(_inside(p, canon(v)) for v in vaults):
        return PIN_VAULT
    own = canon(state)
    if _inside(p, own):
        # Her documents and books live in these. Each must be below the bot folder: a data setting naming the bot
        # folder itself, or one that holds it, gives no pass to everything in it.
        homes = [canon(h) for h in (data, state / "data", root(state)) if h and os.path.isabs(str(h))]
        if not any(h != own and _inside(h, own) and _inside(p, h) for h in homes):
            return PIN_OWN
    try:
        mode = os.stat(p).st_mode                      # never the content
    except (FileNotFoundError, NotADirectoryError):
        return PIN_GONE
    if stat.S_ISDIR(mode):
        return PIN_FOLDER
    if not stat.S_ISREG(mode):
        return PIN_ODD_PATH                            # a device, a pipe
    if any(os.path.splitext(x)[1].lower() not in PIN_SUFFIXES for x in (text, p)):
        return PIN_KIND                                # both: a link named novel.txt that leads to keys.pem
    return ""


def file_problem(state: Path | str, file: Path | str, *, data: Path | str | None = None) -> str:
    """Why *file* may not be pinned, in plain words; "" when it may.

    Same spirit as a linked folder: never any vault of this bot, never PuffPAI's own folder except the data folder
    (*data*, and the default ``<bot folder>\\data``) and the books and downloads. Must be one regular file of
    :data:`PIN_SUFFIXES`, by its written name and by where it really leads. A real network share is allowed, as a
    linked folder is, and :func:`canon` and ``os.stat`` do reach for that host. Reads no file content. A check that
    can't finish refuses."""
    raw = str(file or "")
    if not raw.strip():
        return PIN_BLANK
    try:
        return _pin_problem(Path(state), raw, data)    # as given, spaces too: what passes here is what gets opened
    except Exception:  # noqa: BLE001 - a check that can't finish refuses
        return PIN_CANT_CHECK


def pinned_paths(state: Path | str) -> list[Path]:
    """The documents pinned to this bot (``settings\\folders.json`` ``pinned``). Runs every turn: one small file."""
    try:
        data = json.loads((Path(state) / "settings" / "folders.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    got = data.get("pinned") if isinstance(data, dict) else None
    return [Path(p) for p in got if isinstance(p, str) and p.strip()] if isinstance(got, list) else []


class Library:
    def __init__(self, archivist: Any, state: Path | str | None = None) -> None:
        self.archivist = archivist
        self.state = Path(state or archivist.state)
        self.settings_path = self.state / "settings" / "folders.json"
        self.db_path = self.state / "index" / "knowledge.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(path UNINDEXED, n UNINDEXED, text)")
            db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime REAL,"
                       " status TEXT, reason TEXT, held INTEGER, top_tier TEXT)")

    # -- settings ------------------------------------------------------------------
    def _settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.settings_path)

    @property
    def data_folder(self) -> Path:
        return Path(self._settings().get("data") or (self.state / "data"))

    def linked(self) -> list[Path]:
        return [Path(p) for p in self._settings().get("linked", [])]

    # -- pinned documents (D125, zone1.pinned) ------------------------------------------
    def pinned(self) -> list[Path]:
        return pinned_paths(self.state)

    def pin(self, file: Path | str) -> Path:
        problem = file_problem(self.state, file, data=self.data_folder)      # the one check, on what she gave
        if problem:
            raise FolderRefused(problem)
        p = Path(os.path.expandvars(os.path.expanduser(str(file)))).resolve()
        data = self._settings()
        pins = data.setdefault("pinned", [])
        if canon(p) in [canon(x) for x in pins]:
            raise FolderRefused("That file is pinned already.")
        pins.append(str(p))
        self._save(data)
        return p

    def unpin(self, file: Path | str) -> bool:
        """Takes the pin off. Its words leave the index unless a linked folder reads the file anyway. The file and
        what the vault holds are not touched."""
        data = self._settings()
        want = canon(file)
        before = [str(x) for x in data.get("pinned", [])]
        data["pinned"] = [x for x in before if canon(x) != want]
        if len(data["pinned"]) == len(before):
            return False
        self._save(data)
        gone = [Path(x) for x in before if canon(x) == want]
        walked = {canon(p) for p in self._files()}
        with closing(sqlite3.connect(self.db_path)) as db, db:
            for p in gone:
                if canon(p) not in walked:
                    key = str(p.resolve())
                    db.execute("DELETE FROM chunks WHERE path = ?", (key,))
                    db.execute("DELETE FROM files WHERE path = ?", (key,))
        return True

    def indexed_text(self, file: Path | str) -> str:
        """One file's words as the index holds them: the locked form, whole, in order."""
        with closing(sqlite3.connect(self.db_path)) as db:
            rows = db.execute("SELECT text FROM chunks WHERE path = ? ORDER BY CAST(n AS INTEGER)",
                              (str(Path(file).resolve()),)).fetchall()
        return "".join(r[0] for r in rows)

    def _row(self, key: str) -> tuple | None:
        with closing(sqlite3.connect(self.db_path)) as db:
            return db.execute("SELECT status, reason, held FROM files WHERE path = ?", (key,)).fetchone()

    def pinned_view(self) -> list[dict]:
        """Her Pinned list: each document, whether it is there, and one plain line on what it gave."""
        out = []
        for name, p in pinned.names(self.pinned()).items():
            row = self._row(str(p.resolve()))
            there = p.is_file()
            if not there:
                line = "It isn't there right now. It stays pinned, and your bot reads it again when it's back."
            elif row is None:
                line = "Waiting to be read."
            elif row[0] != "indexed":
                line = row[1]
            elif row[2]:
                line = (f"Ready. {_files_said(row[2], 'detail looks', 'details look')} private, so your vault keeps "
                        f"{'it' if row[2] == 1 else 'them'}. Your bot sees {'it' if row[2] == 1 else 'them'} only "
                        "while your vault is open.")
            else:
                line = "Ready."
            out.append({"name": name, "path": str(p), "there": there, "line": line})
        return out

    def folder_report(self, folder: Path | str) -> dict:
        """What a linked folder gave (D126): how many files were read, how many wait, and which it couldn't read,
        in one line. Names only, and only the first few. A walk of a very big folder stops at REPORT_MAX files."""
        read = waiting = 0
        files: list[dict] = []
        missed: list[str] = []
        reasons: list[str] = []
        with closing(sqlite3.connect(self.db_path)) as db:
            rows = {r[0]: (r[1], r[2]) for r in db.execute("SELECT path, status, reason FROM files")}
        seen = 0
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
            for name in filenames:
                if seen >= REPORT_MAX:
                    break
                if name.startswith((".", "~$")) and name.lower() != ".env" or name.lower() in _NOT_DOCUMENTS:
                    continue
                seen += 1
                if len(files) < REPORT_FILES:
                    files.append({"name": name, "path": str(Path(dirpath, name))})
                if not _reads(name):
                    missed.append(name)
                    continue
                got = rows.get(str(Path(dirpath, name)))
                if got is None:
                    waiting += 1
                elif got[0] == "indexed":
                    read += 1
                else:
                    missed.append(name)
                    reasons.append(got[1])
        parts = [f"{_files_said(read, 'file', 'files')} read."] if read or not (waiting or missed) else []
        if waiting:
            parts.append(f"{_files_said(waiting, 'file is', 'files are')} waiting to be read.")
        if missed:
            shown = ", ".join(missed[:REPORT_NAMES]) + (f" and {len(missed) - REPORT_NAMES} more"
                                                        if len(missed) > REPORT_NAMES else "")
            why = f" {reasons[0]}" if len(missed) == 1 and reasons else ""
            parts.append(f"{_files_said(len(missed), 'file', 'files')} BuildBot can't read yet: {shown}.{why}")
        return {"read": read, "waiting": waiting, "not_read": len(missed), "line": " ".join(parts), "files": files}

    def handable(self, file: Path | str) -> Path | None:
        """The file, when it is one she pinned or one inside a folder she linked (D127), else None. Followed through
        any link first, and never in a vault. Only such a file is ever sent by its path."""
        want = canon(file)
        try:
            vaults = [canon(v) for v in _vault_places(self.state)]
        except (OSError, ValueError):
            return None
        if any(_inside(want, v) for v in vaults) or not os.path.isfile(want):
            return None
        if want in [canon(p) for p in self.pinned()] or any(_inside(want, canon(p)) for p in self.linked()):
            return Path(file)
        return None

    def known_documents(self) -> list[Path]:
        """What chat can name: the pinned documents and the files in linked folders (a row's first few)."""
        found = list(self.pinned())
        for folder in self.linked():
            if folder.is_dir():
                found += [Path(f["path"]) for f in self.folder_report(folder)["files"]]
        return list(dict.fromkeys(found))

    def scan_soon(self) -> None:
        """Read the folders now, in the background: a folder she just linked is read before she has to search."""
        def run() -> None:
            with SCAN_LOCK:
                try:
                    self.scan()
                except Exception:  # noqa: BLE001 - the next search scans again
                    logger.exception("the folders could not be read")
        threading.Thread(target=run, name="library-scan", daemon=True).start()

    def link(self, folder: Path | str) -> Path:
        p = Path(os.path.expandvars(os.path.expanduser(str(folder)))).resolve()
        problem = folder_problem(self.state, p, data=self.data_folder)      # the one check (lane I18f)
        if problem:
            raise FolderRefused(problem)
        data = self._settings()
        links = data.setdefault("linked", [])
        if str(p) not in links:
            links.append(str(p))
        self._save(data)
        return p

    def unlink(self, folder: Path | str) -> bool:
        data = self._settings()
        p = str(Path(folder).resolve())
        before = list(data.get("linked", []))
        data["linked"] = [x for x in before if x != p]
        self._save(data)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DELETE FROM chunks WHERE path LIKE ?", (p + "%",))
            db.execute("DELETE FROM files WHERE path LIKE ?", (p + "%",))
        return len(before) != len(data["linked"])

    # -- scanning --------------------------------------------------------------------
    def _files(self) -> list[Path]:
        from .fetched import chosen, is_meta, moving, root as store_root
        from .knowledge_folder import _way_problem, is_link
        data = self.data_folder
        store = store_root(self.state)
        own = bool(chosen(self.state))                 # books and downloads in a place of her own (D103)
        hold = moving(self.state)                      # being moved: what is indexed stays, nothing is rescanned
        roots = [data] + ([store] if own and not hold else []) + self.linked()
        out = []
        for root in roots:
            if not root.is_dir():
                continue
            if own and root == store and _way_problem(str(store)):
                continue                               # through a link: that is somewhere else, not her store
            for dirpath, dirnames, filenames in os.walk(root):
                here = Path(dirpath)
                inside = here == store or store in here.parents   # os.walk goes through junctions unless told not to
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"
                               and not (hold and here / d == store)
                               and not ((inside or here / d == store) and is_link(here / d))]
                for name in filenames:
                    p = here / name
                    if is_meta(p, store):
                        continue                       # where fetched knowledge came from, not knowledge
                    if inside and is_link(p):
                        continue                       # a link there is never followed out of that folder
                    # A fetched PDF is public, with its text beside it: only her own Word and PDF files are hers (D126).
                    if _reads(name) and not (inside and p.suffix.lower() in DOCUMENT_SUFFIXES):
                        out.append(p)
        for p in self.pinned():                        # checked again each time: a path can change after the pin
            if p.is_file() and not file_problem(self.state, p, data=data):
                out.append(p)
        return out

    def scan(self) -> dict:
        report = {"indexed": 0, "held": 0, "unchanged": 0, "not_indexed": []}
        for p in self._files():
            r = self.scan_file(p)
            if r["status"] == "indexed":
                report["indexed"] += 1
                report["held"] += r["held"]
            elif r["status"] == "unchanged":
                report["unchanged"] += 1
            else:
                report["not_indexed"].append({"path": str(p), "reason": r["reason"]})
        return report

    def scan_file(self, p: Path) -> dict:
        try:
            st = p.stat()
        except OSError:
            return {"status": "not_indexed", "reason": "It can't be read.", "held": 0}
        key = str(p.resolve())
        with closing(sqlite3.connect(self.db_path)) as db:
            row = db.execute("SELECT size, mtime, status, reason FROM files WHERE path = ?", (key,)).fetchone()
        if row and row[0] == st.st_size and row[1] == st.st_mtime:
            if row[2] == "indexed":
                return {"status": "unchanged", "reason": "", "held": 0}
            # A scan or a Word file with a password stays that way until she changes it: a big PDF isn't opened
            # again every minute. A check that couldn't finish is tried again.
            if p.suffix.lower() in DOCUMENT_SUFFIXES and row[3] not in (GATE_CLOSED_LINE, CANT_READ_LINE):
                return {"status": "not_indexed", "reason": row[3], "held": 0}
        public = self._public_text(p, st)
        if public is not None:
            return self._index(key, st, public, held=0, top="P0", reason=PUBLIC_REASON)
        try:
            decoded = pinned.read_document(p)              # one reader: linked folders and pinned documents (D126)
            _, handles = prepare(decoded.text, False, archivist=self.archivist, path="files", source_ref=None)
            store = handles.store_text
            held = len([h for h in handles if h.ref])
            private_in_main = any(h.ref or h.kept == "scrubbed" for h in handles)
            for other in decoded.also:
                # The same bytes read another way: hold anything found there too. If only that reading
                # finds something private, index it (with the finds held), never the reading that hid them.
                _, more = prepare(other, False, archivist=self.archivist, path="files", source_ref=None)
                held += len([h for h in more if h.ref])
                if any(h.ref or h.kept == "scrubbed" for h in more):
                    if private_in_main:
                        return self._record(key, st, "not_indexed", "It reads two ways, with private details "
                                            "in both, so it isn't searchable.", held=held)
                    store, handles = more.store_text, more
        except TooBig:
            return self._record(key, st, "not_indexed", "It's too big to check whole, so it isn't searchable.")
        except Binary:
            return self._record(key, st, "not_indexed", "This looks like a binary file, so it wasn't indexed.")
        except Garbled:
            return self._record(key, st, "not_indexed", "Its text doesn't read cleanly (it may be in an encoding "
                                "PuffPAI can't tell), so it isn't searchable.")
        except pinned.Unreadable as exc:
            return self._record(key, st, "not_indexed", str(exc))
        except Unsupported:
            return self._record(key, st, "not_indexed", "It isn't text PuffPAI can read yet.")
        except GateClosed:
            return self._record(key, st, "not_indexed", GATE_CLOSED_LINE)
        except OSError:
            return self._record(key, st, "not_indexed", CANT_READ_LINE)
        return self._index(key, st, store, held=held, top=handles.top_tier)

    def _public_text(self, p: Path, st: os.stat_result) -> str | None:
        """The text of a file only when the Core's own knowledge fetch wrote exactly these bytes there
        (``fetched.written_by_fetch``): public reference text (Wikipedia, Project Gutenberg) with its source and
        license, already through Drawbridge's text rules. It is not the household's data, so it is indexed
        whole and nothing of it goes to the vault inbox. None for every other file, which gets the full screen.
        The text indexed is decoded from the very bytes that were checked."""
        from .classify import MAX_FILE_BYTES
        from .fetched import written_by_fetch
        if st.st_size > MAX_FILE_BYTES or p.suffix.lower() != ".txt":
            return None
        try:
            data = p.read_bytes()
            if not written_by_fetch(self.state, p, data):
                return None
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        return None if "\x00" in text else text

    def _index(self, key: str, st: os.stat_result, store: str, *, held: int, top: str, reason: str = "") -> dict:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DELETE FROM chunks WHERE path = ?", (key,))
            for n, at in enumerate(range(0, max(len(store), 1), CHUNK)):
                db.execute("INSERT INTO chunks (path, n, text) VALUES (?, ?, ?)", (key, n, store[at:at + CHUNK]))
        return self._record(key, st, "indexed", reason, held=held, top=top)

    def _record(self, key: str, st: os.stat_result, status: str, reason: str, *, held: int = 0,
                top: str = "P0") -> dict:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            if status != "indexed":
                db.execute("DELETE FROM chunks WHERE path = ?", (key,))
            db.execute("INSERT OR REPLACE INTO files (path, size, mtime, status, reason, held, top_tier)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?)", (key, st.st_size, st.st_mtime, status, reason, held, top))
        return {"status": status, "reason": reason, "held": held}

    def forget(self, path: Path | str, *, whole: bool = False) -> None:
        """A removed book out of the index (``zone1.books``): one file, or with *whole* every file in a folder. A scan
        never does this itself, so search would go on finding what is gone."""
        key = str(Path(path).resolve())
        with closing(sqlite3.connect(self.db_path)) as db, db:
            if whole:
                inside = key + os.sep
                for table in ("chunks", "files"):
                    db.execute(f"DELETE FROM {table} WHERE substr(path, 1, ?) = ?", (len(inside), inside))
            else:
                for table in ("chunks", "files"):
                    db.execute(f"DELETE FROM {table} WHERE path = ?", (key,))

    def search(self, query: str, limit: int = 10) -> list[dict]:
        terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if t]
        if not terms:
            return []
        q = " ".join('"' + t + '"' for t in terms)
        with closing(sqlite3.connect(self.db_path)) as db:
            rows = db.execute("SELECT path, n, snippet(chunks, 2, '[', ']', '...', 12) FROM chunks"
                              " WHERE chunks MATCH ? LIMIT ?", (q, int(limit))).fetchall()
        return [{"path": r[0], "chunk": r[1], "snippet": r[2]} for r in rows]
