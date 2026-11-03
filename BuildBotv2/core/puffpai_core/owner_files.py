# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/office_docs.py, src/puffpai/programs.py, src/puffpai/knowledge/my_data.py (adapted)
r"""Her own files, for a package with ``needs.owner_files: "write"`` (02, D17, D136, D140).

Changing a file is OFF until she turns it on (:func:`edits_on`, ``settings\document_edits.json``, D140): an
Editor-in-Chief doesn't make the changes herself, so the bot shows a fix and she makes it. A new file may still be made.
With it on, it works as v1 did:

``<data folder>\My files\Made by <bot name>`` is where such a package makes files. A file in there changes at once.
Any other file in her data folder changes only after her Yes, one file per question. A file in a folder she linked
needs her Yes to :data:`LINKED_LINE`, and the package keeps the original beside it first. Anywhere else, never.

- :func:`edits_on`, :func:`set_edits`: the one setting. :func:`hidden`: such a tool isn't offered to the brain while it's off.
- :func:`takes_real_text`: while it's on, such a package's tool may get the real text of a private detail, never a key.
- :func:`where`: the one reading of a path, for the review below and for the package (``GET /v1/owner-files``).
- :func:`question`: what ``wiring.tool_review`` asks for one tool call: a tool the package lists under
  ``abilities.changes`` with a ``path`` argument changes that file. It only ever adds a Yes or a refusal.
- :func:`recent`, :func:`open_file`, :func:`remove_file`: the Knowledge page's list of the newest files in My files, Open and Remove.
  A folder holding ``.app-files`` (``folders.APP_FILES``, WB2) is an app's own bookkeeping, and the list leaves it out.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MY_FILES = "My files"
LINKED_LINE = "This file is in a folder you linked. BuildBot normally never changes those. Change it anyway?"
EDITS_FILE = ("settings", "document_edits.json")
#: An argument with one of these names says which of her files a tool changes.
FILE_ARGS = ("path", "file", "file_path", "filepath", "filename", "file_name", "document")
EDITS_OFF = "Changing your documents is turned off, so nothing was changed. You can turn it on in Settings."
OUTSIDE = "I can only change files in your data folder or a folder you linked."
NOT_FOUND = "I could not find that file."
NO_FILE = "Say which file."
OPEN_OUTSIDE = "I can only open files in your data folder or a folder you linked."
OPEN_RUNS = "That kind of file runs as a program, so it isn't opened from here."
OPEN_FAILED = "That file did not open on this computer."
REMOVE_OUTSIDE = "I can only delete files in My files."
REMOVE_NOT_LISTED = "That is a hidden file or a link, so it isn't deleted from here."
RECENT_MAX = 30
_LOOK_AT = 5000
#: Never opened from a button: each of these runs as a program (v1 ``programs.RUNNABLE_EXTS``).
RUNNABLE = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".hta", ".msi", ".msp", ".mst", ".lnk", ".url", ".scr",
    ".pif", ".cpl", ".jar", ".reg", ".application", ".appref-ms", ".sh", ".command",
    ".app", ".desktop", ".py", ".pyw", ".scf", ".inf", ".msc", ".gadget", ".diagcab",
    ".settingcontent-ms", ".library-ms", ".search-ms", ".xll", ".xbap", ".chm",
    ".appimage", ".run", ".bin", ".pkg", ".dmg", ".deb", ".rpm"})


def edits_on(state: Path | str) -> bool:
    """Has she turned on changing her documents? Off for every bot until she does; anything unreadable is off."""
    from . import jsonfile
    try:
        got = jsonfile.read(Path(state).joinpath(*EDITS_FILE), {})
    except Exception:  # noqa: BLE001 - a file that breaks the reader (RecursionError too) is off
        return False
    return isinstance(got, dict) and got.get("on") is True


def set_edits(state: Path | str, on: bool) -> bool:
    from . import jsonfile
    jsonfile.write(Path(state).joinpath(*EDITS_FILE), {"on": on is True})
    return on is True


def changes_a_file(manifest: dict | None, tool: str, args_named: Any) -> bool:
    """This tool of this package changes one of her files: it says ``needs.owner_files: "write"``, lists the tool under
    ``abilities.changes``, and the tool has a ``path`` argument. Making a new file has no path."""
    return _writes(manifest, tool) and _file_named(args_named) is not None


def _writes(manifest: Any, tool: str) -> bool:
    """It says ``needs.owner_files: "write"`` and lists this tool under ``abilities.changes``. Any other shape: no."""
    needs = manifest.get("needs") if isinstance(manifest, dict) else None
    abilities = manifest.get("abilities") if isinstance(manifest, dict) else None
    listed = abilities.get("changes") if isinstance(abilities, dict) else None
    return isinstance(needs, dict) and needs.get("owner_files") == "write" and isinstance(listed, list) and tool in listed


def _file_named(value: Any, depth: int = 0) -> Any:
    """The value of the first argument that names a file, at any depth, or None. In a tool's schema the same walk finds
    the property's name, under ``allOf`` too. A tool can wrap its arguments in one object, or say ``file``."""
    if isinstance(value, dict) and depth < 12:
        for key in FILE_ARGS:
            if key in value:
                return value[key]
        for v in value.values():
            got = _file_named(v, depth + 1)
            if got is not None:
                return got
    elif isinstance(value, list) and depth < 12:
        for v in value:
            got = _file_named(v, depth + 1)
            if got is not None:
                return got
    return None


def names_a_file(args: Any) -> bool:
    return _file_named(args) is not None


def blocks_a_send(state: Path | str, manifest: Any, tool: str) -> bool:
    """While it's off, a file isn't handed to a tool that changes files: its arguments are the file itself, with no path."""
    return _writes(manifest, tool) and not edits_on(state)


def hidden(state: Path | str, manifest: dict | None, tool: str, schema: dict | None) -> bool:
    """While it's off, the brain isn't offered such a tool at all, so it gives a suggestion instead of trying."""
    return changes_a_file(manifest, tool, schema if isinstance(schema, dict) else {}) and not edits_on(state)


def takes_real_text(state: Path | str, manifest: dict | None, tool: str) -> bool:
    """The owner's one exception to "an app gets a mark, never the value" (D140): while changing her documents is on, as
    in v1, the real text of a private detail goes in her file. Only for a tool the package lists under
    ``abilities.changes``, ``make_document`` too (it has no path), of a package with ``needs.owner_files: "write"`` that
    declares no way to reach anywhere. Any other shape of manifest: no. ``Concierge._package_args`` checks the rest at the
    same moment: no address allowed, a local brain, an open vault."""
    m = manifest if isinstance(manifest, dict) else {}
    needs, abilities = m.get("needs"), m.get("abilities")
    if not isinstance(needs, dict) or not isinstance(abilities, dict) or not isinstance(abilities.get("changes"), list):
        return False
    return (needs.get("owner_files") == "write" and tool in abilities["changes"]
            and not needs.get("hosts") and not needs.get("addresses") and edits_on(state))


def safe_name(name: Any, fallback: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(name or ""))
    text = " ".join(text.split()).strip(" .")[:80]
    return text or fallback


def my_files(state: Path | str) -> Path:
    from .zone1.fetched import data_folder
    return data_folder(state) / MY_FILES


def made_path(state: Path | str) -> Path:
    from .bot import read
    return my_files(state) / f"Made by {safe_name(read(state).get('name'), 'BuildBot')}"


def made_folder(state: Path | str) -> Path:
    """``<data folder>\\My files\\Made by <bot name>``, made when missing."""
    folder = made_path(state)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _resolve(state: Path | str, raw: str) -> str:
    """A full path as given; a bare name is looked for in what the bot made, then in My files."""
    if os.path.isabs(raw):
        return raw
    for base in (made_path(state), my_files(state)):
        got = base / raw
        if got.is_file():
            return str(got)
    return ""


def where(state: Path | str, path: Any) -> dict:
    """``{"path": real, "place": "made" | "mine" | "linked"}``, or ``{"line": plain reason}``."""
    from .zone1.fetched import data_folder
    from .zone1.folders import _inside, _vault_places, canon, Library
    raw = str(path or "").strip().strip('"')
    if not raw or "\x00" in raw or len(raw) > 1000:
        return {"line": NO_FILE}
    found = _resolve(state, raw)
    if not found or not os.path.isfile(found):
        return {"line": NOT_FOUND}
    real = canon(found)
    try:
        if any(_inside(real, canon(p)) for p in _vault_places(Path(state))):
            return {"line": OUTSIDE}
        linked = Library(None, state).linked()
    except (OSError, ValueError, TypeError):
        return {"line": OUTSIDE}                   # settings that can't be read: nothing changes
    shown = os.path.realpath(found)
    if _inside(real, canon(made_path(state))):
        return {"path": shown, "place": "made"}
    if _inside(real, canon(data_folder(state))):
        return {"path": shown, "place": "mine"}
    if any(_inside(real, canon(p)) for p in linked):
        return {"path": shown, "place": "linked"}
    return {"line": OUTSIDE}


def question(state: Path | str, manifest: dict | None, tool: str, args: dict) -> dict | None:
    """For one tool call: None when this rule says nothing (no such package or tool, or no ``path``, or a file the bot
    made), ``{"line"}`` when the file may not change, or ``{"title", "what"}``, the words of her Yes for that one file."""
    if not changes_a_file(manifest, tool, args):
        return None
    if not edits_on(state):
        return {"line": EDITS_OFF}
    got = where(state, _file_named(args))
    if got.get("line"):
        return {"line": got["line"]}
    if got["place"] == "made":
        return None
    real = Path(got["path"])
    if got["place"] == "linked":
        return {"title": LINKED_LINE, "what": f"{real.name}, in {real.parent}. A copy of the original is kept next to it first."}
    return {"title": f"May I change {real.name}?", "what": f"It is in your files: {real}"}


def recent(state: Path | str, limit: int = RECENT_MAX) -> list[dict]:
    """The newest files in My files: ``[{name, folder, path, modified}]``. Hidden files and Office's own lock files
    (``~$``) are left out, and so is a folder an app marked as its own (WB2). At most 5,000 files are looked at."""
    from .zone1.folders import APP_FILES
    root = my_files(state)
    found: list[tuple[float, str, str]] = []
    seen = 0
    for here, dirs, names in os.walk(root):
        if APP_FILES in names:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            seen += 1
            if seen > _LOOK_AT:
                break
            if n.startswith((".", "~$")):
                continue
            p = os.path.join(here, n)
            try:
                found.append((os.path.getmtime(p), p, os.path.relpath(here, root)))
            except OSError:
                continue
    found.sort(reverse=True)
    return [{"name": os.path.basename(p), "folder": "" if rel == "." else rel, "path": p,
             "modified": datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")}
            for t, p, rel in found[:limit]]


def _start(path: str) -> None:
    """In the file's own app, on any system, with no console window."""
    if sys.platform == "win32":
        os.startfile(path)                         # noqa: S606 - her file, in the app Windows has for it
        return
    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def open_file(state: Path | str, path: Any) -> str:
    """Open a file in her data folder or a linked folder in its own app, on the bot's computer. "" or a plain reason."""
    got = where(state, path)
    if got.get("line"):
        return OPEN_OUTSIDE if got["line"] == OUTSIDE else got["line"]
    if Path(got["path"]).suffix.lower() in RUNNABLE:
        return OPEN_RUNS
    try:
        _start(got["path"])
    except OSError:
        return OPEN_FAILED
    return ""


def for_device(state: Path | str, path: Any) -> tuple[str, str]:
    """``(path, "")``, the file to hand to a device on the network (D130), by the same check as :func:`open_file`, or
    ``("", a plain reason)``. It opens on that device, not on a screen she can't see."""
    got = where(state, path)
    if got.get("line"):
        return "", OPEN_OUTSIDE if got["line"] == OUTSIDE else got["line"]
    if Path(got["path"]).suffix.lower() in RUNNABLE:
        return "", OPEN_RUNS
    return str(got["path"]), ""


def remove_file(state: Path | str, path: Any) -> str:
    """Delete one regular file inside My files, by its real path: "" or a plain reason. Never by way of a link, and never
    a name :func:`recent` leaves out. Its words leave the index with it, with no scan running to put them back."""
    from .zone1.folders import Library, LibraryBusy, _inside, _no_scan, _pin_odd_path, canon
    from .zone1.knowledge_folder import is_link
    raw = str(path or "").strip().strip('"')
    try:
        if _pin_odd_path(raw) or ".." in re.split(r"[\\/]", raw):
            return REMOVE_OUTSIDE
        got = where(state, raw)                    # blank, not there, a folder, any vault, settings that can't be read
        if got.get("line"):
            return REMOVE_OUTSIDE if got["line"] == OUTSIDE else got["line"]
        given = Path(_resolve(state, raw))
        real, root = given.resolve(), my_files(state).resolve()
        parts = real.relative_to(root).parts       # both readings must agree: canon also expands variables in a name
        if not _inside(canon(real), canon(root)):
            return REMOVE_OUTSIDE
        for step in (given, *given.parents):
            if step.resolve() == root:
                break
            if is_link(step):
                return REMOVE_NOT_LISTED
        if any(p.startswith(".") for p in parts) or real.name.startswith("~$"):
            return REMOVE_NOT_LISTED
    except (OSError, RuntimeError, ValueError):
        return REMOVE_OUTSIDE
    try:
        library = Library(None, state)             # the index only, which needs no vault. It can't open: nothing changes
        with _no_scan():
            os.unlink(real)
            library.forget(real)
    except LibraryBusy as exc:
        return str(exc)
    except (OSError, sqlite3.Error):
        if os.path.lexists(real):
            return f"{real.name} could not be deleted. If it is open somewhere, close it and try again."
        return f"{real.name} is deleted, but a search may still find its words."
    return ""


__all__ = ["EDITS_OFF", "changes_a_file", "edits_on", "hidden", "set_edits", "LINKED_LINE", "MY_FILES", "OPEN_OUTSIDE", "OPEN_RUNS", "OUTSIDE", "REMOVE_NOT_LISTED", "REMOVE_OUTSIDE", "RUNNABLE",
           "for_device", "made_folder", "my_files", "open_file", "question", "recent", "remove_file", "safe_name", "takes_real_text", "where"]
