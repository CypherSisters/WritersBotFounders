# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (adapted): _REPARSE_POINT, _is_link, _remove_link, _remove_tree, remove_try_folder
"""Package folders: copy in, hash, and delete, never through a junction or link.

The 10-15 incident: removing a worktree followed a ``node_modules`` junction
into another install and deleted it. So every walk here treats a symlink, a
junction or any other reparse point as a link: removed as a link, never gone
through, and never copied (a junction to ``C:\\`` would copy the drive).

Names are matched case-insensitively (``.Venv`` is ``.venv`` on Windows), and
every walk here works on the Windows long-path form (the ``_LONG`` prefix), so a deep
git clone (over 260 characters) can be copied, hashed and deleted.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import time
from pathlib import Path

_REPARSE_POINT = 0x400          # FILE_ATTRIBUTE_REPARSE_POINT (a junction or link)

#: Never copied in and never part of a package's tree hash: rebuilt in place by setup. A git
#: clone has them deleted from staging before the Drawbridge check (``strip_regenerated``, X24).
SKIP_NAMES = frozenset({".venv", "node_modules", "__pycache__", ".git"})
#: Never needed to run an app, and left out the same way (D95): ``.doctrees``, Sphinx's build cache of
#: Python pickles, which Drawbridge would hold. Removing files loosens nothing; a loose ``.doctree`` or
#: ``.pickle`` anywhere else is still checked (and held).
UNUSED_NAMES = frozenset({".doctrees"})
#: Files the Core itself writes into a package folder (not the author's code).
CORE_WRITTEN = (Path(".buildbot") / "compose.puffpai.yml",)
_SKIP = frozenset(n.casefold() for n in SKIP_NAMES | UNUSED_NAMES)
_UNUSED = frozenset(n.casefold() for n in UNUSED_NAMES)
_CORE_WRITTEN = frozenset(p.as_posix().casefold() for p in CORE_WRITTEN)
_LONG = "\\\\?\\"


def is_skip(name: str) -> bool:
    """*name* is one of :data:`SKIP_NAMES` or :data:`UNUSED_NAMES`, in any case (``.Venv``, ``NODE_MODULES``)."""
    return name.casefold() in _SKIP


def long_path(path: str | Path) -> str:
    """*path* as an absolute path; on Windows with the long-path prefix (``_LONG``), which
    lifts the 260-character limit for the file calls that use it."""
    full = os.path.abspath(str(path))
    if os.name != "nt" or full.startswith(_LONG):
        return full
    if full.startswith("\\\\"):
        return _LONG + "UNC\\" + full[2:]
    return _LONG + full


def is_link(path: str | Path) -> bool:
    """*path* is a symlink, a junction or any other reparse point (or cannot
    be read: then it is treated as one and never gone through)."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _remove_link(path: str) -> None:
    """The link itself only, never what it points at."""
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)


def _remove_tree(path: str) -> None:
    """Remove *path* (a real folder) and what is in it; a link or junction
    inside is removed as a link, never followed."""
    with os.scandir(path) as entries:
        items = [e.path for e in entries]
    for p in items:
        if is_link(p):
            _remove_link(p)
        elif os.path.isdir(p):
            _remove_tree(p)
        else:
            try:
                os.unlink(p)
            except PermissionError:
                os.chmod(p, stat.S_IWRITE)      # git's read-only object files
                os.unlink(p)
    os.rmdir(path)


def remove_folder(folder: str | Path, root: str | Path) -> bool:
    """Delete *folder*, only when it is a real folder directly under *root*
    (``packages\\`` or ``packages\\.staging\\``), never through a junction or link.
    True when it is gone."""
    folder = long_path(folder)
    root = long_path(root)
    if os.path.normcase(os.path.dirname(folder)) != os.path.normcase(root):
        return False
    if not os.path.lexists(folder):
        return True
    if is_link(root) or is_link(folder) or not os.path.isdir(folder):
        return False
    for attempt in range(3):
        try:
            _remove_tree(folder)
            return True
        except OSError:
            if attempt == 2:
                return False
            time.sleep(1.0)     # a process that was just stopped can hold a file a moment
    return False


def copy_package(src: str | Path, dst: str | Path) -> list[str]:
    """Copy a package folder into *dst* (which must not exist). Links, junctions
    and :data:`SKIP_NAMES` are left out. Returns what was left out, as relative paths."""
    src, dst = Path(long_path(src)), Path(long_path(dst))
    skipped: list[str] = []
    dst.mkdir(parents=True)
    for here, dirs, files in os.walk(src):
        rel = Path(here).relative_to(src)
        keep = []
        for d in dirs:
            full = Path(here) / d
            if is_skip(d):
                continue
            if is_link(full):
                skipped.append(str(rel / d))
                continue
            keep.append(d)
            (dst / rel / d).mkdir()
        dirs[:] = keep
        for f in files:
            full = Path(here) / f
            if is_link(full):
                skipped.append(str(rel / f))
                continue
            shutil.copy2(full, dst / rel / f)
    return skipped


def regenerated(folder: str | Path, only_unused: bool = False) -> list[str]:
    """Relative paths of every :data:`SKIP_NAMES` and :data:`UNUSED_NAMES` folder (or link of that name, in any
    case) inside *folder*; with *only_unused*, the :data:`UNUSED_NAMES` ones only."""
    folder = Path(long_path(folder))
    names = _UNUSED if only_unused else _SKIP
    found: list[str] = []
    for here, dirs, _files in os.walk(folder):
        keep = []
        for d in dirs:
            full = Path(here) / d
            if d.casefold() in names:
                found.append(full.relative_to(folder).as_posix())
            elif not is_link(full):
                keep.append(d)
        dirs[:] = keep
    return sorted(found)


def strip_regenerated(folder: str | Path, only_unused: bool = False) -> list[str]:
    """Delete every :data:`SKIP_NAMES` and :data:`UNUSED_NAMES` folder inside a staged *folder* (a link of that
    name is removed as a link, never followed). Setup makes the first again, and an app never needs the second,
    so nothing unchecked runs (X24); the Drawbridge check and its tree hash come after, on what is left. With
    *only_unused*, only :data:`UNUSED_NAMES` (a try's download, D95). Returns what was deleted; raises OSError
    when something could not be deleted."""
    folder = Path(long_path(folder))
    gone = regenerated(folder, only_unused)
    for rel in gone:
        path = folder / rel
        if is_link(path):
            _remove_link(str(path))
        elif path.is_dir():
            _remove_tree(str(path))
    left = regenerated(folder, only_unused)
    if left:
        raise OSError(f"could not delete {left[0]}")
    return gone


def tree_hash(folder: str | Path) -> str:
    """SHA-256 over every file's relative path and bytes (links and :data:`SKIP_NAMES` left out).

    The Drawbridge verdict is bound to this; install re-hashes and refuses on a change (X24).
    """
    folder = Path(long_path(folder))
    h = hashlib.sha256()
    entries: list[Path] = []
    for here, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not is_skip(d) and not is_link(Path(here) / d))
        for f in files:
            full = Path(here) / f
            rel = full.relative_to(folder)
            if not is_link(full) and rel.as_posix().casefold() not in _CORE_WRITTEN:
                entries.append(rel)
    for rel in sorted(entries, key=lambda p: p.as_posix()):
        h.update(rel.as_posix().encode("utf-8") + b"\0")
        with open(folder / rel, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


__all__ = ["SKIP_NAMES", "UNUSED_NAMES", "copy_package", "is_link", "is_skip", "long_path", "regenerated",
           "remove_folder", "strip_regenerated", "tree_hash"]
