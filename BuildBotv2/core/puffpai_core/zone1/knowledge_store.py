"""Moving her books and downloads to another folder or drive (D103). Where they are: ``fetched.root``.

The move loses nothing, and a stop at any moment can be picked up again:

1. Copying. Each file is copied beside its new name as ``<name>.puffpai-moving``, flushed, compared byte for byte with
   the original, and only then given its name. A file on the same drive is renamed instead, which needs no room. Each
   finished file gets a line in ``<state>/index/knowledge_move.log``. The originals stay.
2. Switching. The Core's record of what its fetch wrote gets the new paths beside the old ones, then
   ``settings/folders.json`` names the new place, then the index's rows take the new paths. Each step can run twice.
3. Cleaning. Each original that was copied, and has not changed since, is removed. Only then. A file that changed,
   or an old drive that is gone, leaves the old copy there, and the last line says so.

``<state>/index/knowledge_move.json`` says how far it is. Until step 2 has switched, the old place stays the one in
use: search still answers from the index, nothing new is written (``fetched.folder_for`` says why) and the Library
does not rescan it. A stopped move stays stopped until she picks it up or cancels it. Cancel works until the switch,
and takes back only what the move itself put in the new place.

A link in the old place (a junction, a symlink) is never followed and never copied, and a place that is a link, or
goes through one, is not moved out of or into at all (``knowledge_folder.source_problem``). Every walk here is :func:`_tree`:
``os.walk`` goes through junctions on this Python. The new place goes through the one check
(``knowledge_folder.problem``) first. The vault is never touched: it is not in this folder, and the checks refuse any
folder that overlaps it.

Cancel removes only what this move made. A file that was already at the new place with the same bytes is logged as not
made and stays; a copy she changed since stays too, and the last line says so.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Callable, Iterator

from .. import jsonfile
from . import fetched, knowledge_folder
from .folders import _overlap, canon

logger = logging.getLogger(__name__)

LOG = "knowledge_move.log"
PART = ".puffpai-moving"
BLOCK = 4 * 1024 * 1024
#: A move whose record was touched this recently, by another program, is running there.
BEAT_S = 15.0

_runs: dict[str, "_Run"] = {}
_runs_lock = threading.Lock()


class MoveRefused(Exception):
    """Why this move does not start: ``str()`` is the plain line. *code* tells a caller what she can do next."""

    def __init__(self, line: str, code: str = "refused") -> None:
        super().__init__(line)
        self.code = code


class _Stopped(Exception):
    """She cancelled, or the terminal was stopped: the record stays as it is."""


def _journal(state: Path) -> Path:
    return state / "index" / fetched.MOVE


def _log(state: Path) -> Path:
    return state / "index" / LOG


def _read(state: Path) -> dict:
    got = jsonfile.read(_journal(state), {})
    return got if isinstance(got, dict) else {}


def _write(state: Path, j: dict) -> None:
    jsonfile.write(_journal(state), j)


def _done_rows(state: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        lines = _log(state).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue                                   # the last line of a hard stop: that file is done again
        if isinstance(row, dict) and isinstance(row.get("rel"), str):
            out[row["rel"]] = row
    return out


def _note_done(state: Path, row: dict) -> None:
    with _log(state).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _tree(top: Path, *, strict: bool = False) -> Iterator[tuple[Path, list[Path], list[Path], int]]:
    """Every real folder at and under *top*, each before the folders in it: ``(folder, folders in it, files in it, links
    in it)``. Never enters a link, and *top* being one is nothing at all. *strict*: a folder that can't be read raises;
    else it is passed over."""
    if knowledge_folder.is_link(top):
        return
    stack = [top]
    while stack:
        here = stack.pop()
        try:
            with os.scandir(here) as it:
                entries = list(it)
        except OSError:
            if strict:
                raise
            continue
        dirs, files, links = [], [], 0
        for entry in entries:
            p = Path(entry.path)
            if knowledge_folder.is_link(p):
                links += 1
            elif entry.is_dir(follow_symlinks=False):
                dirs.append(p)
            else:
                files.append(p)
        yield here, dirs, files, links
        stack.extend(dirs)


def _walk(top: Path) -> tuple[list[tuple[Path, str, os.stat_result]], int]:
    """Every file under *top*: ``([(file, its path from top, its stat)], links left out)``. Never through a link.
    A folder that can't be read raises: a move never goes on with part of the list."""
    if knowledge_folder.is_link(top):
        return [], 1
    out, links = [], 0
    for _here, _dirs, names, here_links in _tree(top, strict=True):
        links += here_links
        for p in names:
            if p.name != fetched.MARKER and not p.name.endswith(PART):
                out.append((p, p.relative_to(top).as_posix(), p.stat()))
    return out, links


def _move_problem(state: Path, j: dict) -> str:
    """Why a move on this record may not go on: its settings, or either of its places being or going through a link,
    or overlapping the vault. "" when it may. Asked again at every start, since either may have changed."""
    line = fetched.settings_line(state)
    for key in ("from_root", "to_root"):
        if not line and isinstance(j.get(key), str):
            line = knowledge_folder.source_problem(state, j[key])
    return line


def _copy_unchanged(dst: Path, row: dict) -> bool:
    """The copy at *dst* is still as this move logged it. She may have edited it since."""
    st = dst.stat()
    return st.st_size == row.get("size") and st.st_mtime_ns == row.get("copy_mtime_ns")


def _same_drive(a: Path, b: Path) -> bool:
    """Both on one volume (a rename is enough). *b* need not exist yet."""
    try:
        for at in (b, *b.parents):
            if at.exists():
                return os.stat(a).st_dev == os.stat(at).st_dev
    except OSError:
        pass
    return False


def _drive_gone(path: Path) -> bool:
    return fetched.free_bytes(path) is None


def _plain_stop(exc: OSError, j: dict) -> str:
    """Why the copy stopped, naming the drive. Nothing is lost either way."""
    to, frm = Path(j["to_root"]), Path(j["from_root"])
    tail = "Nothing is lost. Pick it up with: puffpai knowledge resume"
    if _drive_gone(to):
        return f"{fetched.drive_of(to)} is not there, so the move stopped. Plug it in. {tail}"
    if _drive_gone(frm):
        return f"{fetched.drive_of(frm)} is not there, so the move stopped. Plug it in. {tail}"
    if getattr(exc, "errno", 0) == 28 or getattr(exc, "winerror", 0) in (39, 112):
        return f"{fetched.drive_of(to)} is full, so the move stopped. Free some space there. {tail}"
    return f"The move stopped: {getattr(exc, 'strerror', '') or type(exc).__name__}. {tail}"


class _Run:
    def __init__(self, state: Path, tell: Callable[[dict], None] | None = None) -> None:
        self.state = state
        self.tell = tell
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_save = 0.0

    # -- the record ---------------------------------------------------------------------------------------
    def save(self, j: dict, *, now: bool = False) -> None:
        at = time.monotonic()
        if now or at - self.last_save >= 1.0:
            j.update(pid=os.getpid(), beat=time.time())
            _write(self.state, j)
            self.last_save = at
            if self.tell:
                self.tell(dict(j))

    def _check_stop(self) -> None:
        if self.stop.is_set():
            raise _Stopped

    # -- the whole move ---------------------------------------------------------------------------------------
    def run(self) -> dict:
        j = _read(self.state)
        try:
            if j.get("phase") == "stopped":
                j["phase"] = j.get("was") or "copying"
            j["line"] = ""
            self.save(j, now=True)
            line = _move_problem(self.state, j)
            if line:
                raise MoveRefused(line)
            if j["phase"] == "copying":
                self.copying(j)
                j["phase"] = "switching"
                self.save(j, now=True)
            if j["phase"] == "switching":
                self.switching(j)
                j["phase"] = "cleaning"
                self.save(j, now=True)
            if j["phase"] == "cleaning":
                self.cleaning(j)
        except _Stopped:
            j.update(was=j["phase"], phase="stopped", now="",
                     line="The move is stopped. Nothing is lost. Pick it up with: puffpai knowledge resume")
            self.save(j, now=True)
        except MoveRefused as exc:                     # the new place stopped being safe to use
            j.update(was=j["phase"], phase="stopped", now="", line=f"{exc} Nothing is lost. Cancel the move, or fix "
                                                                  "that and pick it up: puffpai knowledge resume")
            self.save(j, now=True)
        except OSError as exc:
            logger.warning("the knowledge move stopped: %r", exc)
            j.update(was=j["phase"], phase="stopped", now="", line=_plain_stop(exc, j))
            self.save(j, now=True)
        except Exception as exc:  # noqa: BLE001 - the record says it stopped; the originals are still there
            logger.exception("the knowledge move stopped")
            j.update(was=j.get("phase"), phase="stopped", now="",
                     line=f"The move stopped ({type(exc).__name__}). Nothing is lost. Pick it up with: "
                          "puffpai knowledge resume")
            self.save(j, now=True)
        return j

    # -- 1: copying ---------------------------------------------------------------------------------------
    def copying(self, j: dict) -> None:
        frm, to = Path(j["from_root"]), Path(j["to_root"])
        if j.get("to"):
            problem = knowledge_folder.problem(self.state, j["to"])
            if problem:
                raise MoveRefused(problem)
        if j.get("leave_old") or j.get("adopt") or j.get("nothing"):
            return
        if not frm.is_dir():
            raise OSError(2, f"{frm} is not there")
        files, links = _walk(frm)
        done = _done_rows(self.state)
        j.update(links=links, total_files=len(done) + sum(1 for _p, rel, _s in files if rel not in done),
                 total_bytes=sum(int(r.get("size") or 0) for r in done.values())
                 + sum(st.st_size for _p, rel, st in files if rel not in done))
        j["done_files"], j["done_bytes"] = 0, 0
        for row in done.values():
            j["done_files"] += 1
            j["done_bytes"] += int(row.get("size") or 0)
        to.mkdir(parents=True, exist_ok=True)
        if j.get("to"):
            knowledge_folder.write_marker(self.state, to)
        rename = _same_drive(frm, to)
        for src, rel, st in files:
            self._check_stop()
            row = done.get(rel)
            dst = to / rel
            if row and row.get("renamed") and not src.exists():
                continue
            if row and dst.is_file() and dst.stat().st_size == st.st_size == row.get("size") \
                    and row.get("mtime_ns") == st.st_mtime_ns:
                continue                               # finished before the stop
            if row:                                    # it changed after it was copied, or the copy is gone
                j["done_files"] -= 1
                j["done_bytes"] -= int(row.get("size") or 0)
            ours = bool(row and row.get("made") and dst.is_file() and _copy_unchanged(dst, row))
            self.one_file(j, src, dst, rel, st, rename, ours=ours)
        for rel, row in done.items():                  # a file already renamed must really be there
            if row.get("renamed") and not (to / rel).is_file():
                raise OSError(2, f"{to / rel} is not there")

    def one_file(self, j: dict, src: Path, dst: Path, rel: str, st: os.stat_result, rename: bool, *,
                 ours: bool) -> None:
        j["now"] = f"Moving {rel}"
        to, frm = Path(j["to_root"]), Path(j["from_root"])
        check = knowledge_folder.still_safe if j.get("to") else knowledge_folder.source_problem
        line = check(self.state, to) or knowledge_folder.inside_problem(to, rel) \
            or knowledge_folder.inside_problem(to, rel + PART) or knowledge_folder.inside_problem(frm, rel)
        if line:
            raise MoveRefused(line)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if ours:
                dst.unlink()                           # this move's own copy of what has changed since
            elif not self._same_bytes(j, src, dst):
                raise OSError(17, f"A different file is already at {dst}")
        row = {"rel": rel, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "renamed": False, "made": False}
        if not dst.exists() and rename:
            _note_done(self.state, dict(row, renamed=True))    # first: a hard stop then finds it in either place
            try:
                os.replace(src, dst)
                row["renamed"] = True
            except OSError:
                pass
        if dst.exists():
            j["done_bytes"] += st.st_size
        else:
            self._copy(j, src, dst, st)
            # This move made it. Its own time, read back: a USB stick's file system may round the one it was given.
            row.update(made=True, copy_mtime_ns=dst.stat().st_mtime_ns)
        if not row["renamed"]:
            _note_done(self.state, row)
        j["done_files"] += 1
        self.save(j)

    def _copy(self, j: dict, src: Path, dst: Path, st: os.stat_result) -> None:
        tmp = dst.with_name(dst.name + PART)
        for attempt in (0, 1):
            have = tmp.stat().st_size if tmp.exists() else 0
            if have > st.st_size:
                tmp.unlink()
                have = 0
            base = j["done_bytes"]
            with src.open("rb") as a, tmp.open("ab") as b:
                a.seek(have)
                sent = have
                while True:
                    self._check_stop()
                    block = a.read(BLOCK)
                    if not block:
                        break
                    b.write(block)
                    sent += len(block)
                    j["done_bytes"] = base + sent
                    self.save(j)
                b.flush()
                os.fsync(b.fileno())
            j["now"] = f"Checking {dst.name}"
            self.save(j, now=True)
            if self._same_bytes(j, src, tmp):
                break
            j["done_bytes"] = base
            tmp.unlink()                               # what was kept from before the stop was not good: once more
            if attempt:
                raise OSError(5, f"The copy of {src.name} did not match it")
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
        j["done_bytes"] = base + st.st_size

    def _same_bytes(self, j: dict, a: Path, b: Path) -> bool:
        if a.stat().st_size != b.stat().st_size:
            return False
        with a.open("rb") as fa, b.open("rb") as fb:
            while True:
                self._check_stop()
                one, two = fa.read(BLOCK), fb.read(BLOCK)
                if one != two:
                    return False
                if not one:
                    return True
                self.save(j)                           # comparing a big file is long work: the beat goes on

    # -- 2: switching -----------------------------------------------------------------------------------------
    def switching(self, j: dict) -> None:
        j["now"] = "Telling your bot where they are now"
        self.save(j, now=True)
        frm, to = Path(j["from_root"]), Path(j["to_root"])
        to.mkdir(parents=True, exist_ok=True)
        if not j.get("leave_old"):
            _ledger_add(self.state, frm, to)
        if j.get("to"):
            knowledge_folder.write_marker(self.state, to)
        _set_place(self.state, j.get("to") or "")
        j["switched"] = True
        self.save(j, now=True)                         # from here the new place is the one in use
        if not j.get("leave_old"):                     # left behind: what is indexed stays searchable as it is
            _rekey_index(self.state, frm, to)

    # -- 3: cleaning ----------------------------------------------------------------------------------------
    def cleaning(self, j: dict) -> None:
        frm, to = Path(j["from_root"]), Path(j["to_root"])
        j["now"] = "Clearing the old place"
        self.save(j, now=True)
        left = 0
        if not j.get("leave_old") and frm.is_dir():
            for rel, row in _done_rows(self.state).items():
                if row.get("renamed"):
                    continue
                src = frm / rel
                if knowledge_folder.inside_problem(frm, rel) or knowledge_folder.inside_problem(to, rel):
                    left += 1                          # a link came to be on the way: nothing is removed through it
                    continue
                try:
                    if not src.exists():
                        continue
                    st = src.stat()
                    there = to / rel                   # the copy, there and whole, right now: else the original stays
                    if st.st_size == row.get("size") and st.st_mtime_ns == row.get("mtime_ns") and there.is_file() \
                            and there.stat().st_size == st.st_size:
                        src.unlink()
                    else:
                        left += 1
                except OSError:
                    left += 1
            _drop_empty(frm)
        if not j.get("leave_old"):
            _ledger_drop(self.state, frm)
        j.update(phase="done", now="", left=left + int(j.get("links") or 0), line=_done_line(j, left))
        self.save(j, now=True)
        try:
            _log(self.state).unlink()
        except OSError:
            pass


def _done_line(j: dict, left: int) -> str:
    frm, to = Path(j["from_root"]), Path(j["to_root"])
    n = int(j.get("done_files") or 0)
    bits = [f"Moved {n} {'file' if n == 1 else 'files'} ({fetched.size_said(int(j.get('done_bytes') or 0))}) to {to}."]
    if j.get("leave_old"):
        bits = [f"Your books and downloads now go to {to}. Anything already at {frm} stays there, untouched."]
    elif j.get("adopt") or j.get("nothing"):
        bits = [f"Your books and downloads are at {to} now."]
    if left:
        bits.append(f"{left} changed while moving, so their old copies stay at {frm}.")
    if j.get("links"):
        bits.append(f"{j['links']} links there were not copied, and stay at {frm}.")
    return " ".join(bits)


def _drop_empty(top: Path) -> None:
    """Empty folders under *top*, and *top* when it ends up empty. Never its parent, never a link, never through one."""
    if knowledge_folder.is_link(top):
        return
    try:
        (top / fetched.MARKER).unlink()
    except OSError:
        pass
    for _here, dirs, _files, _links in reversed(list(_tree(top))):     # the deepest first
        for p in dirs:
            try:
                p.rmdir()
            except OSError:
                pass
    try:
        top.rmdir()
    except OSError:
        pass


def _set_place(state: Path, folder: str) -> None:
    """``settings/folders.json`` ``knowledge``: the folder she chose, or gone for the data folder."""
    path = state / "settings" / "folders.json"
    data = jsonfile.read(path, {})
    data = data if isinstance(data, dict) else {}
    if folder:
        data["knowledge"] = folder
    else:
        data.pop("knowledge", None)
    jsonfile.write(path, data)


def _ledger_add(state: Path, frm: Path, to: Path) -> None:
    """The record of what the Core's fetch wrote: each row under *frm* also under *to*. Public text stays public."""
    def change(files: dict) -> dict:
        old = fetched._key(frm).rstrip("\\/") + os.sep  # noqa: SLF001 - the record's own spelling of a path
        new = fetched._key(to).rstrip("\\/") + os.sep  # noqa: SLF001
        more = {new + k[len(old):]: v for k, v in files.items() if k.startswith(old)}
        return {**files, **more}
    _ledger_change(state, change)


def _ledger_drop(state: Path, frm: Path) -> None:
    def change(files: dict) -> dict:
        old = fetched._key(frm).rstrip("\\/") + os.sep  # noqa: SLF001
        return {k: v for k, v in files.items() if not k.startswith(old)}
    _ledger_change(state, change)


def _ledger_change(state: Path, change: Callable[[dict], dict]) -> None:
    path = state / "index" / fetched.LEDGER
    got = jsonfile.read(path, {})
    files = got.get("files") if isinstance(got, dict) else None
    if isinstance(files, dict):
        jsonfile.write(path, {"files": change(files)})


def _rekey_index(state: Path, frm: Path, to: Path) -> None:
    """The index's rows follow the files, so nothing is read or screened again and search answers throughout."""
    db_path = state / "index" / "knowledge.db"
    if not db_path.exists():
        return
    old = str(frm.resolve()).rstrip("\\/") + os.sep
    new = str(to.resolve()).rstrip("\\/") + os.sep
    with closing(sqlite3.connect(db_path)) as db, db:
        for table, verb in (("files", "UPDATE OR REPLACE"), ("chunks", "UPDATE")):
            db.execute(f"{verb} {table} SET path = ? || substr(path, ?) WHERE lower(substr(path, 1, ?)) = lower(?)",
                       (new, len(old) + 1, len(old), old))


# -- what the rest of the Core calls -------------------------------------------------------------------------

def view(state: Path | str) -> dict:
    """Where they are, how much room is there, and how a move is going, in plain words."""
    state = Path(state)
    where = fetched.place(state)
    free = fetched.free_bytes(where)
    drive = fetched.drive_of(where)
    out = {"where": str(where), "store": str(fetched.root(state)), "default": not fetched.chosen(state),
           "drive": drive, "there": free is not None, "free_bytes": free,
           "free": f"{fetched.size_said(free)} free on {drive}" if free is not None else f"{drive} is not there",
           "line": fetched.away_line(state), "move": move_view(state)}
    from ..wizard.fields import synced_folder
    service = synced_folder(str(where))
    out["warning"] = (f"{where} is inside {service}, which copies it to the internet. Pick a folder outside "
                      f"{service} to keep it on this computer.") if service else ""
    return out


def move_view(state: Path | str) -> dict | None:
    """How the last move went: None when there was none."""
    state = Path(state)
    j = _read(state)
    if not j.get("phase"):
        return None
    total, done = int(j.get("total_bytes") or 0), int(j.get("done_bytes") or 0)
    running = _running(state, j)
    # A hard stop (the Core went away) leaves a record that says it was running: it is stopped now.
    stopped = j["phase"] == "stopped" or (j["phase"] != "done" and not running)
    line = j.get("line") or ""
    if stopped and not line:
        line = "The move was stopped before it ended. Nothing is lost. Pick it up with: puffpai knowledge resume"
    return {"phase": "stopped" if stopped else j["phase"], "running": running, "from": j.get("from_root"),
            "to": j.get("to_root"), "done_files": int(j.get("done_files") or 0),
            "total_files": int(j.get("total_files") or 0), "done_bytes": done, "total_bytes": total,
            "percent": 100 if j["phase"] == "done" else (min(99, done * 100 // total) if total else 0),
            "now": str(j.get("now") or ""), "line": line,
            "can_resume": stopped, "can_cancel": j["phase"] != "done" and not _switched(state, j),
            "switched": bool(j.get("switched")), "left": int(j.get("left") or 0)}


def _switched(state: Path, j: dict) -> bool:
    """The new place is in use (also when a hard stop came between saying so and writing it down)."""
    return bool(j.get("switched")) or canon(fetched.root(state)) == canon(j.get("to_root") or "")


def _running(state: Path, j: dict | None = None) -> bool:
    with _runs_lock:
        run = _runs.get(canon(state))
    if run is not None and run.thread is not None and run.thread.is_alive():
        return True
    j = _read(state) if j is None else j
    if j.get("phase") in (None, "done", "stopped") or j.get("pid") == os.getpid():
        return False
    return time.time() - float(j.get("beat") or 0) < BEAT_S


def check_new(state: Path | str, folder: str | None, *, leave_old: bool = False) -> dict:
    """Everything a new move must pass, before anything is written: the new record. *folder* None or "" is back to
    the data folder. Raises :class:`MoveRefused`."""
    state = Path(state)
    j = _read(state)
    if j.get("phase") not in (None, "done"):
        raise MoveRefused("A move is not finished yet. Pick it up or cancel it first.", "unfinished")
    line = fetched.settings_line(state)
    if line:
        raise MoveRefused(line)
    folder = str(folder or "").strip().strip('"')
    if folder:
        folder = os.path.normpath(os.path.expandvars(os.path.expanduser(folder)))
        if canon(folder) == canon(fetched.data_folder(state)):
            folder = ""                                # the data folder itself: that is the default place
    if folder:
        problem = knowledge_folder.problem(state, folder)
        if problem:
            raise MoveRefused(problem)
    frm = fetched.root(state)
    to = Path(folder) / "knowledge" if folder else fetched.data_folder(state) / "knowledge"
    # What the move would empty, and a default place, which the check above never saw: a link there is the vault emptied.
    line = knowledge_folder.source_problem(state, frm) or ("" if folder else knowledge_folder.source_problem(state, to))
    if line:
        raise MoveRefused(line)
    if canon(frm) == canon(to):
        raise MoveRefused(f"Your books and downloads are there already: {fetched.place(state)}", "same")
    if _overlap(canon(frm), canon(to)):
        raise MoveRefused("That folder overlaps where your books and downloads are now. Pick a folder beside it.")
    adopt = nothing = False
    if _drive_gone(frm) or not frm.is_dir():
        adopt = to.is_dir() and knowledge_folder.marker_bot(to) == knowledge_folder.bot_key(state)
        nothing = not adopt and not _drive_gone(frm)
        if not adopt and not leave_old and _drive_gone(frm):
            drive = fetched.drive_of(frm)
            raise MoveRefused(f"Your books and downloads are on {drive}, and {drive} is not there, so they can't be "
                              f"moved. Plug it in, or use the new place and leave them on {drive}: puffpai knowledge "
                              "move <folder> --leave-old", "source_away")
    total, big, count, links = 0, 0, 0, 0
    if frm.is_dir() and not leave_old:
        try:
            files, links = _walk(frm)
        except OSError as exc:
            raise MoveRefused(f"{frm} can't be read whole, so nothing was moved "
                              f"({getattr(exc, 'strerror', '') or type(exc).__name__}).") from None
        count = len(files)
        total = sum(st.st_size for _p, _rel, st in files)
        big = max([st.st_size for _p, _rel, st in files] or [0])
    if _drive_gone(to):
        raise MoveRefused(f"{fetched.drive_of(to)} is not there. Plug it in, or pick another folder.")
    if frm.is_dir() and not _same_drive(frm, to):
        free = fetched.free_bytes(to) or 0
        if free < total + fetched.SPARE:
            d = fetched.drive_of(to)
            raise MoveRefused(f"{d} has {fetched.size_said(free)} free, and your books and downloads need "
                              f"{fetched.size_said(total + fetched.SPARE)}. Free some space on {d}, or pick another "
                              "folder.", "no_room")
        if big > fetched.FAT32_MAX and fetched.filesystem(to) == "FAT32":
            raise MoveRefused(f"{fetched.drive_of(to)} is formatted FAT32, which can't hold a file over 4 GB, and "
                              f"yours has one of {fetched.size_said(big)}. Pick a drive formatted NTFS or exFAT.",
                              "too_big_for_drive")
    return {"phase": "copying", "from_root": str(frm), "to_root": str(to), "to": folder, "adopt": adopt, "nothing": nothing,
            "leave_old": bool(leave_old), "total_files": count, "total_bytes": total, "done_files": 0, "done_bytes": 0,
            "links": links, "now": "", "line": "", "started": time.time(), "pid": os.getpid(), "beat": time.time()}


def _go(state: Path, *, wait: bool, tell: Callable[[dict], None] | None) -> "_Run":
    run = _Run(state, tell)
    key = canon(state)
    with _runs_lock:
        have = _runs.get(key)
        if have is not None and have.thread is not None and have.thread.is_alive():
            raise MoveRefused("A move is running already.", "running")
        _runs[key] = run
        run.thread = threading.Thread(target=run.run, name="knowledge-move", daemon=True)
        run.thread.start()
    if wait:
        try:
            while run.thread.is_alive():
                run.thread.join(0.2)
        except KeyboardInterrupt:
            run.stop.set()
            run.thread.join()
    return run


def start(state: Path | str, folder: str | None, *, leave_old: bool = False, wait: bool = False,
          tell: Callable[[dict], None] | None = None) -> dict:
    """Start moving them to *folder* (None or "": back to the data folder). In the background, or to the end with
    *wait*. Returns :func:`view`. Raises :class:`MoveRefused` with the plain line."""
    state = Path(state)
    if _running(state):
        raise MoveRefused("A move is running already.", "running")
    j = check_new(state, folder, leave_old=leave_old)
    try:
        _log(state).unlink()
    except OSError:
        pass
    _write(state, j)
    _go(state, wait=wait, tell=tell)
    return view(state)


def resume(state: Path | str, *, wait: bool = False, tell: Callable[[dict], None] | None = None) -> dict:
    """Pick a stopped move up again, where it stopped. Raises :class:`MoveRefused`."""
    state = Path(state)
    if _running(state):
        raise MoveRefused("A move is running already.", "running")
    j = _read(state)
    if j.get("phase") in (None, "done"):
        raise MoveRefused("No move is waiting.", "none")
    line = _move_problem(state, j)
    if line:
        raise MoveRefused(line)
    _go(state, wait=wait, tell=tell)
    return view(state)


def cancel(state: Path | str) -> dict:
    """Stop a move and go back to how it was, until it has switched. What it copied to the new place is taken back;
    a file it renamed goes back. Her old place is left whole. After the switch only the clearing is left, so a
    cancel refuses: the move is already done in all that matters."""
    state = Path(state)
    key = canon(state)
    with _runs_lock:
        run = _runs.get(key)
    if run is not None and run.thread is not None and run.thread.is_alive():
        run.stop.set()
        run.thread.join(30.0)
        if run.thread.is_alive():
            raise MoveRefused("The move is finishing one file. Try again in a moment.", "running")
    elif _running(state):
        raise MoveRefused("Another program is running this move. Stop it there first.", "running")
    j = _read(state)
    if j.get("phase") in (None, "done"):
        raise MoveRefused("No move is waiting.", "none")
    if _switched(state, j):
        raise MoveRefused("Your bot already uses the new place, so this can't be taken back. "
                          "Finish it with: puffpai knowledge resume", "switched")
    frm, to = Path(j["from_root"]), Path(j["to_root"])
    line = knowledge_folder.source_problem(state, frm) or knowledge_folder.source_problem(state, to)
    if line:
        raise MoveRefused(line, "stuck")
    kept = 0
    for rel, row in _done_rows(state).items():
        dst = to / rel
        if knowledge_folder.inside_problem(to, rel) or knowledge_folder.inside_problem(frm, rel):
            kept += 1                                  # never through a link: it stays, and the line says so
            continue
        try:
            if row.get("renamed"):
                if dst.exists() and not (frm / rel).exists():
                    (frm / rel).parent.mkdir(parents=True, exist_ok=True)
                    os.replace(dst, frm / rel)
            elif row.get("made") and dst.exists() and (frm / rel).exists():
                if _copy_unchanged(dst, row):
                    dst.unlink()
                else:
                    kept += 1                          # she changed the copy: it is hers now
        except OSError:
            raise MoveRefused(f"{dst} could not be taken back. Check {fetched.drive_of(dst)}, then cancel again.",
                              "stuck") from None
    if to.is_dir():
        for _here, _dirs, names, _links in _tree(to):
            for p in names:
                if p.name.endswith(PART):
                    rel = p.relative_to(to).as_posix()[:-len(PART)]
                    if not knowledge_folder.inside_problem(frm, rel) and (frm / rel).is_file():
                        try:
                            p.unlink()         # a part of a file still whole in the old place, so nothing is lost
                        except OSError:
                            pass
        _drop_empty(to)
    words = "The move is cancelled. Everything is where it was."
    if kept:
        words = (f"The move is cancelled. {kept} {'copy' if kept == 1 else 'copies'} changed since, or behind a link, "
                 f"{'stays' if kept == 1 else 'stay'} at {to}. The rest is where it was.")
    j.update(phase="done", now="", cancelled=True, line=words, kept=kept)
    _write(state, j)
    try:
        _log(state).unlink()
    except OSError:
        pass
    return view(state)


__all__ = ["MoveRefused", "cancel", "check_new", "move_view", "resume", "start", "view"]
