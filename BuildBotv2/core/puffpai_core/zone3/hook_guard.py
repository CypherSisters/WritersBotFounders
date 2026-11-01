"""Python start-up hooks, checked before and after every Toolsmith command (X23, the L4 re-check).

The text check in :mod:`.toolsmith_zone` reads a command the way a shell would,
but no text check covers every shape a write can take (``Copy-Item
-Destination:``, ``cp -r evil/. .venv/Lib/``, ``Copy-Item -Recurse``,
``Expand-Archive``, ``git clone`` into site-packages, a program that writes
the file itself). So the Core's exec broker (lane L6) also wraps every command
Toolsmith runs in a :class:`HookGuard`:

1. Before the command: a hash list of every Python start-up hook in the try
   folder (any ``*.pth`` under a ``site-packages`` or ``dist-packages`` folder,
   at any depth, and every ``sitecustomize.py`` and ``usercustomize.py``) and
   in the Python that runs the Core (its site-packages folders, and her user
   site folder). Each hook's bytes are kept so a change can be undone.
2. After the command: the same list again, compared.
3. Anything new, changed or removed **in the try folder** is undone (a new
   hook removed, a changed or removed one written back), the try is stopped
   (``stop_try``), and a card says why. **One exception, so normal installs
   work:** a new or changed ``.pth`` in the try folder is allowed when it is
   listed, with the same sha256, in the ``RECORD`` of a ``*.dist-info`` that
   the same command wrote (pip's own record of what it installed, in the same
   site-packages folder). A ``.pth`` no such ``RECORD`` lists, a removed one,
   and any ``sitecustomize.py`` or ``usercustomize.py`` (recorded or not)
   still stop the try. Anything new, changed or removed **in
   the Core's own Python** puts the Core into safe mode (``safe_mode``) with a
   card; that change is left in place as evidence and is not touched.

A scan that can't finish counts as a change (fail closed). Honest limits: a
command that changes a hook and puts it back before it ends is not seen; a
program the command started that keeps running can change a hook later (the
next command's scan sees it). The ``RECORD`` exception trusts the command's
own record: a command that writes a fake ``*.dist-info`` listing its ``.pth``
passes too. It narrows what a copy can slip in; it is not proof that pip wrote it.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import site
import stat
import sys
from typing import Any, Callable

SITE_DIRS = ("site-packages", "dist-packages")
HOOK_FILES = ("sitecustomize.py", "usercustomize.py")
MAX_KEEP = 4 * 1024 * 1024                      # bytes kept per hook so a change can be undone
_PRUNE = frozenset({"node_modules", ".git"})    # never on a Python path; walking them is slow
TRY_LINE = ("A command in Toolsmith's try changed Python's start-up files, so the try was stopped and the change "
            "was undone.")
CORE_LINE = ("Something changed the Python start-up files PuffPAI itself runs, so PuffPAI went into safe mode. "
             "Check what changed before you start it normally.")


def _is_hook(rel_parts: list[str], name: str) -> bool:
    low = name.lower()
    if low in HOOK_FILES:
        return True
    return low.endswith(".pth") and any(p.lower() in SITE_DIRS for p in rel_parts[:-1])


def core_site_dirs() -> list[str]:
    """The site folders of the Python that runs the Core (and her user site folder)."""
    out = []
    try:
        out += list(site.getsitepackages())
    except Exception:  # noqa: BLE001
        pass
    try:
        if site.ENABLE_USER_SITE:
            out.append(site.getusersitepackages())
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt":
        out.append(os.path.join(sys.prefix, "Lib", "site-packages"))
    else:
        version = f"python{sys.version_info[0]}.{sys.version_info[1]}"
        out += [os.path.join(sys.prefix, lib, version, "site-packages") for lib in ("lib", "lib64")]
    seen, keep = set(), []
    for d in out:
        c = os.path.normcase(os.path.realpath(d))        # lib64 is often a link to lib: one folder, one scan
        if c not in seen and os.path.isdir(d):
            seen.add(c)
            keep.append(d)
    return keep


def scan(root: str, *, site_root: bool = False) -> dict[str, tuple[str, bytes | None]]:
    """``{path: (sha256, bytes or None)}`` for every hook under *root* (links never followed).
    *site_root*: *root* itself is a site folder, so every ``*.pth`` in it counts. Raises OSError on a
    folder it can't read (the caller fails closed)."""
    out: dict[str, tuple[str, bytes | None]] = {}
    if not os.path.isdir(root):
        return out
    base = os.path.abspath(root)

    def err(exc: OSError) -> None:
        raise exc

    for dirpath, dirnames, filenames in os.walk(base, followlinks=False, onerror=err):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE]
        rel = os.path.relpath(dirpath, base)
        parts = ([] if rel == "." else rel.replace("\\", "/").split("/")) + ["x"]
        if site_root:
            parts = ["site-packages"] + parts
        for name in filenames:
            if not _is_hook(parts, name):
                continue
            full = os.path.join(dirpath, name)
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode):
                out[full] = ("link-or-other", None)
                continue
            with open(full, "rb") as fh:
                data = fh.read(MAX_KEEP + 1)
            out[full] = (hashlib.sha256(data).hexdigest(), data if len(data) <= MAX_KEEP else None)
    return out


def records(root: str) -> dict[str, str]:
    """``{RECORD path: sha256}`` for every ``*.dist-info/RECORD`` under *root* (links never followed)."""
    out: dict[str, str] = {}
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(os.path.abspath(root), followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE]
        if dirpath.lower().endswith(".dist-info") and "RECORD" in filenames:
            full = os.path.join(dirpath, "RECORD")
            if stat.S_ISREG(os.lstat(full).st_mode):
                with open(full, "rb") as fh:
                    out[full] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _recorded(written: list[str]) -> dict[str, str]:
    """``{absolute path: sha256 hex}`` of every file the RECORDs list (paths relative to their site folder)."""
    out: dict[str, str] = {}
    for rec in written:
        site_dir = os.path.dirname(os.path.dirname(rec))
        try:
            with open(rec, "r", encoding="utf-8", errors="replace", newline="") as fh:
                rows = list(csv.reader(io.StringIO(fh.read())))
        except OSError:
            continue
        for row in rows:
            if len(row) < 2 or not row[1].startswith("sha256="):
                continue
            rel = row[0].replace("/", os.sep)
            if os.path.isabs(rel) or ".." in rel.split(os.sep):
                continue                           # a record never names a place outside its own folder
            try:
                raw = base64.urlsafe_b64decode(row[1][7:] + "=" * (-len(row[1][7:]) % 4))
            except (ValueError, TypeError):
                continue
            out[os.path.normcase(os.path.abspath(os.path.join(site_dir, rel)))] = raw.hex()
    return out


def _diff(before: dict, after: dict) -> list[dict]:
    changes = []
    for path in sorted(set(before) | set(after)):
        if path not in before:
            changes.append({"path": path, "kind": "new"})
        elif path not in after:
            changes.append({"path": path, "kind": "removed"})
        elif before[path][0] != after[path][0]:
            changes.append({"path": path, "kind": "changed"})
    return changes


def _inside(path: str, folder: str) -> bool:
    a, b = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


class HookGuard:
    """``with HookGuard(try_folder, stop_try=..., safe_mode=..., on_card=...) as g: run(command)``;
    then ``g.result``: ``{ok, try, core, undone, action}``."""

    def __init__(self, folder: str, *, core_sites: list[str] | None = None,
                 stop_try: Callable[[str], Any] | None = None, safe_mode: Callable[[str], Any] | None = None,
                 on_card: Callable[[dict], Any] | None = None) -> None:
        self.folder = os.path.abspath(folder)
        self.core_sites = list(core_sites) if core_sites is not None else core_site_dirs()
        self.stop_try = stop_try
        self.safe_mode = safe_mode
        self.on_card = on_card
        self.before: dict | None = None
        self.result: dict | None = None

    def _snapshot(self) -> dict:
        core: dict = {}
        for d in self.core_sites:
            core.update(scan(d, site_root=True))
        return {"try": scan(self.folder), "core": core, "records": records(self.folder)}

    def __enter__(self) -> "HookGuard":
        self.before = self._snapshot()            # an unreadable try folder raises here: no command runs
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.result = self.finish()
        return False

    def finish(self) -> dict:
        before = self.before or {"try": {}, "core": {}, "records": {}}
        allowed: list[dict] = []
        try:
            after = self._snapshot()
            try_changes, core_changes = _diff(before["try"], after["try"]), _diff(before["core"], after["core"])
            # pip's own record: RECORDs this command wrote (new or changed), and what they list
            written = [r for r, h in after["records"].items() if before.get("records", {}).get(r) != h]
            listed = _recorded(written)
            keep = []
            for ch in try_changes:
                path = ch["path"]
                sha_now = after["try"].get(path, ("", None))[0]
                if ch["kind"] in ("new", "changed") and path.lower().endswith(".pth") \
                        and listed.get(os.path.normcase(os.path.abspath(path))) == sha_now:
                    allowed.append(ch)              # installed by the same command, and pip recorded it
                else:
                    keep.append(ch)
            try_changes = keep
        except OSError:
            try_changes, core_changes = [{"path": self.folder, "kind": "cant_scan"}], []
        undone = []
        for ch in try_changes:
            path = ch["path"]
            if ch["kind"] == "cant_scan" or not _inside(os.path.dirname(path) or path, self.folder):
                continue
            try:
                old = before["try"].get(path)
                if os.path.lexists(path) and (os.path.islink(path) or os.path.isfile(path)):
                    os.unlink(path)               # the link or file itself, never what a link points to
                if old is not None and old[1] is not None:
                    with open(path, "wb") as fh:
                        fh.write(old[1])          # a changed or removed hook goes back as it was
                undone.append(path)
            except OSError:
                pass
        action = "none"
        if try_changes:
            action = "stopped_try"
            self._call(self.stop_try, "python_hook_changed")
            self._card({"kind": "try_hook_changed", "line": TRY_LINE, "buttons": ["ok"],
                        "changes": [{"kind": c["kind"], "name": os.path.basename(c["path"])} for c in try_changes]})
        if core_changes:
            action = "safe_mode"
            self._call(self.safe_mode, "core_python_hook_changed")
            self._card({"kind": "core_hook_changed", "line": CORE_LINE, "buttons": ["safe_mode"],
                        "changes": [{"kind": c["kind"], "name": os.path.basename(c["path"])} for c in core_changes]})
        self.result = {"ok": not try_changes and not core_changes, "try": try_changes, "core": core_changes,
                       "undone": undone, "action": action, "recorded": allowed}
        return self.result

    @staticmethod
    def _call(fn: Callable[[str], Any] | None, reason: str) -> None:
        if fn is not None:
            try:
                fn(reason)
            except Exception:  # noqa: BLE001 - the card still says what happened
                pass

    def _card(self, card: dict) -> None:
        if self.on_card is not None:
            try:
                self.on_card(card)
            except Exception:  # noqa: BLE001
                pass


__all__ = ["CORE_LINE", "HookGuard", "TRY_LINE", "core_site_dirs", "records", "scan"]
