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
- PROPOSED for v3, built, not yet tested by her: the one list on Knowledge is the pinned documents, the ones she added
  (``added``, in that same file, not named in chats) and the copies. A copy is a file ``uploads.save`` recorded
  (``copies``, by its whole real path), or any file in the data folder's ``uploads`` while that folder is one BuildBot set up
  (under the bot folder, or setup's own default). In a data folder she chose, an ``uploads`` folder may be her own, so an
  unrecorded file there is only an ordinary file the walk reads. ``Library.remove_document`` deletes a copy's file. Nothing
  else here deletes anything of hers.
- ``scan()`` reads every text, Word and PDF file whole (D126), runs the gate (``path: files``), holds
  every private find in the vault automatically (or the sealed inbox while
  locked), and indexes only the gated text (handles and kinds, never values).
- Originals are never modified, moved, renamed or deleted: the Library only reads them.
- A file the floor check can't finish is not indexed at all, never half indexed
  (F3, F9). Too big, binary-looking, not text, or text that doesn't decode
  cleanly (``Garbled``, see ``classify.decode_file``): not indexed, with a plain
  reason. A file with a UTF-16/32 mark is also read the Windows-1252 way, and
  both readings go through the gate (anything either finds is held).
- A folder holding a file named :data:`APP_FILES` belongs to an app (WB2, 02): it and everything below it are not walked,
  so not read, not gated and not listed, and what the index already held from it goes at the next ``scan()``. Bookkeeping,
  not her documents. A document she pinned in there is still read: that was her own choice.
- One exception (lane I18d): a file the Core's own knowledge fetch wrote, with exactly the bytes it wrote
  (``fetched.written_by_fetch``, the Core's record in ``<state>/index``), is public reference text, not her
  data: it is indexed whole and nothing of it is held or sealed into the vault inbox. Every other file,
  including one she put in the knowledge folder or a fetched file she changed, gets the gate as above.
- One more (D146): a pinned document she marked "no vault" (``fiction``) is an ordinary file: indexed whole, nothing held.

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
import sys
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from .. import jsonfile
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
#: D159: what a scan is doing now, by bot folder. Only in memory, so a stop can't leave it saying "indexing".
_SCANS: dict[str, dict] = {}
LAST_SCAN = "last_scan.json"
#: How long a change to her list waits for a scan to end, in seconds, as removing a book does.
SCAN_WAIT = 30
BUSY_LINE = "Your bot is reading your documents right now. Try again in a moment."
#: A folder's row names this many files it couldn't read, and walks this many.
REPORT_NAMES = 3
REPORT_MAX = 20000
#: The files a row lists, to send to an app (D127).
REPORT_FILES = 100
#: Windows's own small files in a folder: not documents she would miss.
_NOT_DOCUMENTS = {"desktop.ini", "thumbs.db"}
#: A folder holding a file of this name belongs to an app (WB2): lists and the index leave it out, with all below it.
APP_FILES = ".app-files"


def _storable(text: str) -> bool:
    """Text the index can hold. A lone surrogate is a name Windows and Linux both allow, and sqlite can't take it."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _reads(name: str) -> bool:
    """A file the walk reads, by its name. Word's lock file (``~$novel.docx``) and Windows's own are not documents."""
    if name.startswith("~$") or name.lower() in _NOT_DOCUMENTS:
        return False
    return Path(name).suffix.lower() in TEXT_SUFFIXES or Path(name).suffix.lower() in DOCUMENT_SUFFIXES \
        or name.lower() == ".env"


def _files_said(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _row_line(row: tuple | None, fiction: bool) -> str:
    """One plain line on what a file that is there gave, from its row in the index."""
    if row is None:
        return "Being read now." if SCAN_LOCK.locked() else "Waiting to be read."
    if row[0] != "indexed":
        return row[1]
    if fiction:
        return FICTION_LINE
    if row[2]:
        return (f"{FOUND} {_files_said(row[2], 'detail looks', 'details look')} private, so your vault keeps "
                f"{'it' if row[2] == 1 else 'them'}. Your bot sees {'it' if row[2] == 1 else 'them'} only "
                "while your vault is open.")
    return FOUND
#: The index's note on a file the Core's own knowledge fetch wrote, indexed whole (lane I18d).
PUBLIC_REASON = "Public reference text this bot fetched, indexed whole; none of it is your data."
#: The index's note on a document she marked "no vault" (D146).
FICTION_REASON = "No vault for this document, by her choice: indexed as an ordinary file."
FOUND = "Your bot can find this."
FICTION_LINE = FOUND + " No vault for this document: your bot reads it as an ordinary file."
GONE_PINNED = "It isn't there right now. It stays pinned, and your bot reads it again when it's back."
GONE_LISTED = "It isn't there right now. It stays on your list, and your bot reads it again when it's back."


class FolderRefused(Exception):
    pass


class LibraryBusy(FolderRefused):
    """A plain line for her: a scan is running or a file is held open, and nothing on her list changed."""


@contextmanager
def _no_scan() -> Iterator[None]:
    """No scan while her list changes: one reading a document now would put back the words that just went."""
    if not SCAN_LOCK.acquire(timeout=SCAN_WAIT):
        raise LibraryBusy(BUSY_LINE)                   # refused, not gone ahead: D146's plain words must not stay
    try:
        yield
    finally:
        SCAN_LOCK.release()


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


#: A Mac's disk reads ``~/.SSH`` as ``~/.ssh``, and ``normcase`` leaves the letters alone there. Only these two rules
#: fold: :func:`canon` keeps the spelling, since a path it gives may be saved and shown.
_FOLD_CASE = sys.platform == "darwin"


def _same_case(path: str) -> str:
    return path.casefold() if _FOLD_CASE else path


def _overlap(a: str, b: str) -> bool:
    """*a* and *b* are the same folder, or one holds the other (both from :func:`canon`)."""
    a, b = _same_case(a), _same_case(b)
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
PIN_VAULT_UNREADABLE = "PuffPAI can't read where your vault is kept, so no file can be added right now."
PIN_VAULT = "That file is part of your vault, so it can't be added."
PIN_OWN = "That file is one of PuffPAI's own, so it can't be added."
PIN_GONE = "That file isn't there."
PIN_FOLDER = "That is a folder. Add one file, or add the whole folder."
PIN_KIND = "PuffPAI can read .txt, .md, .docx and .pdf files."
PIN_CANT_CHECK = "PuffPAI can't check that file, so it can't be added."

#: ``\\?\`` and ``\\.\`` (after any slash is made a backslash, with any number of them), the same thing in NT's own
#: names (``\??\``, ``\Device\``), and a path that starts with three slashes or more, which names no plain place.
_DEVICE_PATH = re.compile(r"(?i)^(?:\\{2,}[?.]+(?:\\|$)|\\+\?\?(?:\\|$)|\\+device(?:\\|$)|\\{3,})")


def _inside(path: str, place: str) -> bool:
    """*path* is *place* or below it (both from :func:`canon`). A file holds nothing, so this is one way only."""
    return (_same_case(path).rstrip("\\/") + os.sep).startswith(_same_case(place).rstrip("\\/") + os.sep)


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
    if not _storable(text) or _pin_odd_path(text):     # before canon: a share on this computer is not even touched
        return PIN_ODD_PATH
    if not os.path.isabs(text) or (os.name == "nt" and not os.path.splitdrive(text)[0]):
        return PIN_WHOLE_PATH                          # "\x.txt" is on whichever drive this runs from
    p = canon(raw)
    if not _storable(p) or _pin_odd_path(p):
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


def _strings(data: Any, key: str) -> list[str]:
    """The paths under *key* in ``folders.json``. Any other shape is none: a hand-edited file never stops a turn or a page."""
    got = data.get(key) if isinstance(data, dict) else None
    return [p for p in got if _plain_text(p)] if isinstance(got, list) else []


def _plain_text(p: Any) -> bool:
    return isinstance(p, str) and bool(p.strip()) and "\x00" not in p and _storable(p)


def _saved_paths(state: Path | str, key: str) -> list[Path]:
    try:
        data = json.loads((Path(state) / "settings" / "folders.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    return [Path(p) for p in _strings(data, key)]


def pinned_paths(state: Path | str) -> list[Path]:
    """The documents pinned to this bot (``settings\\folders.json`` ``pinned``). Runs every turn: one small file."""
    return _saved_paths(state, "pinned")


def fiction_paths(state: Path | str) -> list[Path]:
    """The pinned documents she marked "no vault" (``settings\\folders.json`` ``fiction``, D146)."""
    return _saved_paths(state, "fiction")


def added_paths(state: Path | str) -> list[Path]:
    """The documents she added and didn't pin (``settings\\folders.json`` ``added``): searched, not named in chats."""
    return _saved_paths(state, "added")


def _key(path: Path) -> str:
    """What the index calls this file. A link that leads back to itself can't be resolved: as it is listed."""
    try:
        return str(path.resolve())
    except (OSError, RuntimeError, ValueError):
        return str(path)


def _forget_rows(db: sqlite3.Connection, keys: set[str]) -> None:
    for key in keys:
        db.execute("DELETE FROM chunks WHERE path = ?", (key,))
        db.execute("DELETE FROM files WHERE path = ?", (key,))


def index_view(state: Path | str) -> dict:
    """D159: is it indexing now, how far along, and when it last finished. Counts and one file's name. Only reads, and needs
    no vault. ``files`` and ``pieces`` are what can be searched now; ``of`` is None while it is still listing the files."""
    state = Path(state)
    run = _SCANS.get(str(state))
    view = {"running": run is not None, "done": 0, "of": None, "now": "", "started_at": 0, "files": 0, "pieces": 0, "not_read": 0}
    if run is not None:
        view.update({k: run[k] for k in ("done", "of", "now", "started_at")})
    db_path = state / "index" / "knowledge.db"
    if db_path.is_file():
        try:
            with closing(sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True, timeout=2)) as db:
                for status, n in db.execute("SELECT status, COUNT(*) FROM files GROUP BY status"):
                    view["files" if status == "indexed" else "not_read"] += n
                view["pieces"] = db.execute("SELECT COUNT(*) FROM chunks_docsize").fetchone()[0]
        except sqlite3.Error:                          # busy for a moment: the page asks again soon
            pass
    last = jsonfile.read(state / "index" / LAST_SCAN, {})
    last = last if isinstance(last, dict) else {}
    view.update(last_finished=last.get("finished_at") or 0, last_took_s=last.get("took_s") or 0, last_new=last.get("new") or 0)
    return view


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
            got = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return {}
        return got if isinstance(got, dict) else {}

    def _save(self, data: dict) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.settings_path)

    @property
    def data_folder(self) -> Path:
        got = self._settings().get("data")
        return Path(got) if _plain_text(got) else self.state / "data"

    def linked(self) -> list[Path]:
        return [Path(p) for p in _strings(self._settings(), "linked")]

    # -- pinned documents (D125, zone1.pinned) ------------------------------------------
    def pinned(self) -> list[Path]:
        return pinned_paths(self.state)

    def pin(self, file: Path | str) -> Path:
        problem = file_problem(self.state, file, data=self.data_folder)      # the one check, on what she gave
        if problem:
            raise FolderRefused(problem)
        p = Path(os.path.expandvars(os.path.expanduser(str(file)))).resolve()
        data = self._settings()
        pins = data["pinned"] = _strings(data, "pinned")
        if canon(p) in [canon(x) for x in pins]:
            raise FolderRefused("That file is pinned already.")
        pins.append(str(p))
        if "added" in data:                            # one list only
            data["added"] = [x for x in _strings(data, "added") if canon(x) != canon(p)]
        self._save(data)
        return p

    def unpin(self, file: Path | str) -> bool:
        """Takes the pin off. Its words leave the index unless a linked folder reads the file anyway. The file and
        what the vault holds are not touched."""
        data = self._settings()
        gone, plain = self._off_pinned(data, canon(file))
        if not gone:
            return False
        self._save(data)
        self._leave_index(gone, plain)
        return True

    def _off_pinned(self, data: dict, want: str) -> tuple[list[Path], bool]:
        """Out of ``pinned`` and ``fiction`` in *data*, not saved yet: ``(the paths that came off, was it "no vault")``."""
        before = _strings(data, "pinned")
        marked = [str(x) for x in fiction_paths(self.state)]
        plain = any(canon(x) == want for x in marked)
        data["pinned"] = [x for x in before if canon(x) != want]
        if plain:
            data["fiction"] = [x for x in marked if canon(x) != want]
        return [Path(x) for x in before if canon(x) == want], plain

    def _leave_index(self, gone: list[Path], plain: bool) -> None:
        """Their words leave the index unless a walk reads the file anyway. Plain words never stay: that walk gates it again."""
        walked = {canon(p) for p in self._files()}
        with closing(sqlite3.connect(self.db_path)) as db, db:
            for p in gone:
                if plain or canon(p) not in walked:
                    _forget_rows(db, {str(p), _key(p)})

    def is_fiction(self, path: Path | str) -> bool:
        """She marked this pinned document "no vault" (D146), and only a pinned one is. Runs in scans and searches: no
        ``canon`` while none is marked."""
        marked = fiction_paths(self.state)
        if not marked:
            return False
        want = canon(path)
        return want in {canon(x) for x in marked} and want in {canon(x) for x in self.pinned()}

    def set_fiction(self, path: Path | str, on: bool) -> bool:
        """The "no vault" switch on a pinned document (D146). False when it isn't pinned. On any change the file's rows
        go, so no old form stays and the next ``scan_file`` reads it again."""
        want = canon(path)
        pin = next((p for p in self.pinned() if canon(p) == want), None)
        if pin is None:
            return False
        marked = [str(x) for x in fiction_paths(self.state)]
        if any(canon(x) == want for x in marked) == bool(on):
            return True
        data = self._settings()
        data["fiction"] = [x for x in marked if canon(x) != want] + ([str(pin)] if on else [])
        self._save(data)
        self.forget(pin)
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
            fiction = self.is_fiction(p)
            line = _row_line(row, fiction) if there else GONE_PINNED
            out.append({"name": name, "path": str(p), "there": there, "line": line, "fiction": fiction})
        return out

    # -- the one list on Knowledge: pinned, added, and the copies in uploads ----------------------------------
    def added(self) -> list[Path]:
        return added_paths(self.state)

    def add(self, file: Path | str) -> Path:
        """On her list, not named in chats. The same one check as a pin. Already on the list is no error, and a copy is
        never written to ``added``: the folder is its list."""
        problem = file_problem(self.state, file, data=self.data_folder)
        if problem:
            raise FolderRefused(problem)
        p = Path(os.path.expandvars(os.path.expanduser(str(file)))).resolve()
        want = canon(p)
        have = next((x for x in self._documents() if canon(x) == want), None)
        if have is not None:
            return have
        if self.copy_place(want):                      # where a copy goes, and no copy: Word's own lock file
            raise FolderRefused(PIN_KIND)
        data = self._settings()
        data["added"] = _strings(data, "added") + [str(p)]
        self._save(data)
        return p

    def uploads_place(self) -> str:
        """Where the copies are, from :func:`canon`. "" when that is a link, or not really in the data folder: then
        nothing in it is a copy, so nothing in it can ever be deleted."""
        from .knowledge_folder import is_link
        from .uploads import FOLDER
        data = self.data_folder
        if not os.path.isabs(str(data)) or is_link(data / FOLDER):
            return ""
        place = canon(data / FOLDER)
        return place if os.path.dirname(place) == canon(data) else ""

    def _default_data(self) -> bool:
        """The data folder is one BuildBot set up: under the bot folder, or setup's own default for this bot. A link there
        leads to a place of hers."""
        from ..wizard.fields import default_data_folder
        from .knowledge_folder import is_link
        real = canon(self.data_folder)
        return any(own and not is_link(own) and real == canon(own) for own in (self.state / "data", default_data_folder(self.state)))

    def counts_as_copy(self, real: str) -> bool:
        """*real* (from :func:`canon`), if it is in ``uploads``, is BuildBot's own: everything there in a data folder BuildBot
        set up, and only what ``uploads.save`` recorded in one she chose. By whole path, so a moved folder matches nothing."""
        return self._default_data() or real in {canon(x) for x in _strings(self._settings(), "copies")}

    def record_copy(self, file: Path | str) -> None:
        """``uploads.save`` put this file here."""
        real = canon(file)
        data = self._settings()
        have = _strings(data, "copies")
        if real not in [canon(x) for x in have]:
            data["copies"] = have + [real]
            self._save(data)

    def copy_place(self, real: str) -> bool:
        """*real* is right in a real ``uploads`` folder and BuildBot's own. Not when that folder is a link: it holds no copies."""
        place = self.uploads_place()
        return bool(place) and os.path.dirname(real) == place and self.counts_as_copy(real)

    def _is_copy(self, p: Path, place: str) -> bool:
        """One regular file right in *place*, BuildBot's own, with an ending a pin takes, by its own name and by where it
        leads, past the one check. Never a link, a ``.part`` or Word's ``~$`` lock file. Asked again just before a copy is
        deleted."""
        from .knowledge_folder import is_link
        if not _reads(p.name) or p.suffix.lower() not in PIN_SUFFIXES:
            return False
        try:
            regular = stat.S_ISREG(os.lstat(p).st_mode)
        except OSError:
            return False
        real = canon(p)
        return (regular and not is_link(p) and os.path.dirname(real) == place and self.counts_as_copy(real)
                and not file_problem(self.state, p, data=self.data_folder))

    def copies(self) -> list[Path]:
        """What was sent from a device, as it is on the disk, so a copy from an earlier build nobody pinned has a row in
        a data folder BuildBot set up. In one she chose, only what was recorded: the rest of that folder is hers."""
        from .uploads import FOLDER
        place = self.uploads_place()
        if not place:
            return []
        folder = self.data_folder / FOLDER
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return []
        return [folder / n for n in names if self._is_copy(folder / n, place)]

    def _documents(self) -> list[Path]:
        """Her list, each document once: pinned, then added, then the copies."""
        seen: dict[str, Path] = {}
        for p in [*self.pinned(), *self.added(), *self.copies()]:
            seen.setdefault(canon(p), p)
        return list(seen.values())

    def documents_view(self) -> list[dict]:
        """Her one list: each document, whether it is there, one plain line on what it gave, and what it is."""
        paths = self._documents()
        called = {canon(p): name for name, p in pinned.names(paths).items()}
        named = {canon(p) for p in self.pinned()}
        copies = {canon(p) for p in self.copies()}
        out = []
        for p in paths:
            real = canon(p)
            row = self._row(_key(p))
            there = os.path.isfile(p)
            fiction = self.is_fiction(p)
            problem = file_problem(self.state, p, data=self.data_folder) if there else ""   # the walk leaves this one out
            if problem:
                line = problem
            elif not there:
                line = GONE_PINNED if real in named else GONE_LISTED
            else:
                line = _row_line(row, fiction)
            out.append({"name": called.get(real, str(p)), "path": str(p), "there": there, "line": line,   # names can share one
                        "ok": there and not problem and row is not None and row[0] == "indexed",
                        "waiting": there and not problem and row is None,
                        "named": real in named, "copy": real in copies or (not there and self.copy_place(real)),
                        "fiction": fiction})
        return out

    def set_named(self, file: Path | str, on: bool) -> bool:
        """"Name it in every chat". On, a pin. Off, out of ``pinned`` and into ``added`` unless it is a copy, "no vault"
        cleared, and read again through the gate. False when it is not on her list."""
        want = canon(file)
        with _no_scan():
            data = self._settings()
            copy = next((c for c in self.copies() if canon(c) == want), None)
            listed = next((x for x in _strings(data, "added") if canon(x) == want), None)
            pin = next((x for x in _strings(data, "pinned") if canon(x) == want), None)
            if pin is None and listed is None and copy is None:
                return False
            if on:
                if pin is None:
                    self.pin(copy or listed)           # the one check, and out of ``added``
                elif listed is not None:               # hand-edited into both: one list only
                    data["added"] = [x for x in _strings(data, "added") if canon(x) != want]
                    self._save(data)
                return True
            if pin is None:
                return True
            gone, plain = self._off_pinned(data, want)
            if listed is None and copy is None and not self.copy_place(want):
                data["added"] = _strings(data, "added") + [str(gone[0])]
            self._save(data)                           # before the index: the walk then knows it still reads this one
            self._leave_index(gone, plain)
            for p in gone:
                if os.path.isfile(p) and not file_problem(self.state, p, data=self.data_folder):
                    self.scan_file(p)                  # through the gate, so no old form stays and its line is right
        return True

    def remove_document(self, file: Path | str) -> str | None:
        """Off every list, and what the page says about that; None when it is not on her list. Its words leave the index
        unless a walk still reads the file. Only a copy's file is deleted, found on the disk here and never by the path
        given: an original never is. What the vault holds is not touched. :class:`LibraryBusy`, with nothing changed,
        when a scan is running or the copy is held open."""
        want = canon(file)
        with _no_scan():
            data = self._settings()
            listed = [x for key in ("pinned", "added") for x in _strings(data, key) if canon(x) == want]
            # A device path or a stream can reach a copy too: it may take a listing off, and never deletes a file.
            odd = _pin_odd_path(os.path.expandvars(os.path.expanduser(str(file))))
            copy = None if odd else next((c for c in self.copies() if canon(c) == want), None)
            if not listed and copy is None:
                return None
            p = copy if copy is not None else Path(listed[0])
            kept = (self._row(_key(p)) or (None, None, 0))[2]
            if copy is not None:
                self._delete_copy(copy)
            lost = copy is None and self.copy_place(want) and not os.path.lexists(p)    # a pinned copy, already gone
            _, plain = self._off_pinned(data, want)
            data["added"] = [x for x in _strings(data, "added") if canon(x) != want]
            if "copies" in data:
                data["copies"] = [x for x in _strings(data, "copies") if canon(x) != want]
            self._save(data)
            self._leave_index([Path(x) for x in listed], plain)
            walked = want in {canon(x) for x in self._files()}
        said = f"{p.name} is deleted from your bot." if copy is not None or lost \
            else f"{p.name} is off your list. The file itself stays where it is."
        if walked:
            said += f" Your bot still reads it, because it is inside {self._walk_place(want)}. Move it out of there to stop that."
        if kept:
            said += " What your vault kept from it stays in your vault."
        return said

    def _walk_place(self, real: str) -> str:
        """Which walk reads this file, in her words."""
        from .fetched import chosen, root
        for x in self.linked():
            if _inside(real, canon(x)):
                return f"a folder you added, {x}"
        if chosen(self.state) and _inside(real, canon(root(self.state))):
            return "your books and downloads folder"
        return f"your data folder, {self.data_folder}"

    def _delete_copy(self, copy: Path) -> None:
        """Its words first, so a crash leaves a file the next walk reads again, never words of a file that is gone. Then
        the file itself. Held open: read again, and nothing else changes."""
        place = self.uploads_place()
        if not place or not self._is_copy(copy, place):
            raise LibraryBusy(f"{copy.name} could not be deleted.")
        with closing(sqlite3.connect(self.db_path)) as db, db:
            _forget_rows(db, {str(copy), _key(copy)})
        try:
            os.unlink(copy)                            # never follows a last link: it can take only that one name
        except FileNotFoundError:
            pass
        except OSError:
            self.scan_file(copy)
            raise LibraryBusy(f"{copy.name} could not be deleted. If it is open somewhere, close it and try again.") from None

    def read_now(self, p: Path) -> tuple[bool, str]:
        """Through the gate for a route: ``(ok, what the page says)``. A route doesn't wait out a long scan, so this holds
        no lock: if she removed the document while it was being read, its words go again."""
        try:
            got = self.scan_file(p)
        except Exception:  # noqa: BLE001 - it is on her list; the bot's next walk reads it
            logger.exception("a document could not be read now")
            got = {"status": "not_indexed", "reason": CANT_READ_LINE, "held": 0}
        if canon(p) not in {canon(x) for x in self._documents()}:
            self.forget(p)
        row = self._row(_key(p))
        if got["status"] == "not_indexed" or row is None or row[0] != "indexed":
            return False, f"{p.name} was added, but your bot can't read it. {got['reason'] or CANT_READ_LINE}"
        if self.is_fiction(p):
            return True, f"{p.name} is ready."
        if row[2]:
            return True, (f"{p.name} is ready. {_files_said(row[2], 'detail looks', 'details look')} private, so your vault "
                          f"keeps {'it' if row[2] == 1 else 'them'}.")
        return True, f"{p.name} is ready. Your bot can find it now."

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
            if APP_FILES in filenames:
                dirnames[:] = []
                continue
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
        parts = [f"Your bot can find {_files_said(read, 'file', 'files')} here."] if read or not (waiting or missed) else []
        if waiting:
            parts.append(f"{_files_said(waiting, 'file is', 'files are')} {'being read now' if SCAN_LOCK.locked() else 'waiting to be read'}.")
        if missed:
            shown = ", ".join(missed[:REPORT_NAMES]) + (f" and {len(missed) - REPORT_NAMES} more"
                                                        if len(missed) > REPORT_NAMES else "")
            why = f" {reasons[0]}" if len(missed) == 1 and reasons else ""
            parts.append(f"{_files_said(len(missed), 'file', 'files')} BuildBot can't read yet: {shown}.{why}")
        return {"read": read, "waiting": waiting, "not_read": len(missed), "line": " ".join(parts), "files": files}

    def handable(self, file: Path | str) -> Path | None:
        """The file, when it is on her list or inside a folder she linked (D127), else None. Followed through any link
        first, and never in a vault. Only such a file is ever sent by its path. An added path is checked again, as it is
        before each read: it can lead somewhere else now."""
        want = canon(file)
        try:
            vaults = [canon(v) for v in _vault_places(self.state)]
        except (OSError, ValueError):
            return None
        if any(_inside(want, v) for v in vaults) or not os.path.isfile(want):
            return None
        if want in [canon(p) for p in self.pinned()] or any(_inside(want, canon(p)) for p in self.linked()):
            return Path(file)
        if want in [canon(p) for p in self.added() if not file_problem(self.state, p, data=self.data_folder)] \
                or want in [canon(p) for p in self.copies()]:
            return Path(file)
        return None

    def known_documents(self) -> list[Path]:
        """What chat can name: the documents on her list and the files in linked folders (a row's first few)."""
        found = self._documents()
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
        links = data["linked"] = _strings(data, "linked")
        if str(p) not in links:
            links.append(str(p))
        self._save(data)
        return p

    def unlink(self, folder: Path | str) -> bool:
        data = self._settings()
        p = str(Path(folder).resolve())
        before = _strings(data, "linked")
        data["linked"] = [x for x in before if x != p]
        self._save(data)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DELETE FROM chunks WHERE path LIKE ?", (p + "%",))
            db.execute("DELETE FROM files WHERE path LIKE ?", (p + "%",))
        return len(before) != len(data["linked"])

    # -- scanning --------------------------------------------------------------------
    def _files(self, app_folders: list[Path] | None = None) -> list[Path]:
        """Every file a scan reads. *app_folders*, when given, gets each folder it left out as an app's (WB2)."""
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
                if APP_FILES in filenames:
                    dirnames[:] = []
                    if app_folders is not None:
                        app_folders.append(here)
                    continue
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
                    if _reads(name) and not (inside and p.suffix.lower() in DOCUMENT_SUFFIXES) and _storable(str(p)):
                        out.append(p)
        for p in [*self.pinned(), *self.added()]:      # checked again each time: a path can change after the pin
            if os.path.isfile(p) and not file_problem(self.state, p, data=data):
                out.append(p)
        return out

    def _sweep_plain_words(self) -> None:
        """X6: plain words of a document that is no longer "no vault" go, whatever left them there: a crash between saving
        her list and clearing the index, or a read that held no lock. If a walk reads it, it is gated again after this."""
        with closing(sqlite3.connect(self.db_path)) as db, db:
            for path in [r[0] for r in db.execute("SELECT path FROM files WHERE reason = ?", (FICTION_REASON,))]:
                if not self.is_fiction(path):
                    _forget_rows(db, {path})

    def scan(self) -> dict:
        self._sweep_plain_words()
        report = {"indexed": 0, "held": 0, "unchanged": 0, "not_indexed": []}
        run = {"done": 0, "of": None, "now": "", "started_at": time.time()}
        _SCANS[str(self.state)] = run
        try:
            app_folders: list[Path] = []
            files = self._files(app_folders)
            self._forget_app_files(app_folders)
            run["of"] = len(files)
            for p in files:
                run["now"] = p.name
                r = self.scan_file(p)
                run["done"] += 1
                if r["status"] == "indexed":
                    report["indexed"] += 1
                    report["held"] += r["held"]
                elif r["status"] == "unchanged":
                    report["unchanged"] += 1
                else:
                    report["not_indexed"].append({"path": str(p), "reason": r["reason"]})
        finally:
            _SCANS.pop(str(self.state), None)
        try:
            jsonfile.write(self.state / "index" / LAST_SCAN, {"finished_at": time.time(), "took_s": round(time.time() - run["started_at"]),
                                                              "new": report["indexed"]})
        except OSError:                                # only what the Knowledge page says about the last one
            logger.info("the last scan's time could not be kept")
        return report

    def _forget_app_files(self, folders: list[Path]) -> None:
        """What the index held from these folders from before they were marked. It looks in ``files`` first, so a pass
        with nothing to drop never walks the words. A document she pinned there stays."""
        if not folders:
            return
        pins = {str(p.resolve()) for p in self.pinned()}
        with closing(sqlite3.connect(self.db_path)) as db, db:
            for folder in folders:
                inside = str(folder.resolve()) + os.sep
                rows = db.execute("SELECT path FROM files WHERE substr(path, 1, ?) = ?", (len(inside), inside)).fetchall()
                _forget_rows(db, {r[0] for r in rows if r[0] not in pins})

    def scan_file(self, p: Path) -> dict:
        try:
            st = p.stat()
        except OSError:
            return {"status": "not_indexed", "reason": "It can't be read.", "held": 0}
        key = str(p.resolve())
        with closing(sqlite3.connect(self.db_path)) as db:
            row = db.execute("SELECT size, mtime, status, reason FROM files WHERE path = ?", (key,)).fetchone()
        # Plain words of a document that is no longer "no vault" (a crash, or a read that held no lock): read it again.
        stale = bool(row) and row[3] == FICTION_REASON and not self.is_fiction(p)
        if row and row[0] == st.st_size and row[1] == st.st_mtime and not stale:
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
            if self.is_fiction(p):                         # D146: an ordinary file, nothing held or published
                return self._index(key, st, decoded.text, held=0, top="P0", reason=FICTION_REASON)
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
