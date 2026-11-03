"""Hand one file to a package that takes files (D127; 02 ``accepts``, D48c), and the owner's own picker.

- :func:`sendable`: the installed packages with ``accepts``, read from the manifest her install Yes covered.
- :func:`hand`: the button's and the chat's one way. The package's own tool gets the whole file as the contract says
  (``{file_name, mime, text}`` or ``{file_name, mime, content_b64}``), after Drawbridge's review of that one call, as
  every package tool call has. A file she hands over is her choice, so it isn't turned into handles (a Word file's
  bytes can't be). A package with internet hosts gets Drawbridge's existing check on what the file holds: a key is
  refused, and anything else private is a Yes card for that one send.
- :func:`pick`: Windows's own Open dialog, on this computer, so she never types a path. It only answers a path, or with
  ``files`` one path a line; Pin, Add and Link check each as they check a typed one.
"""

from __future__ import annotations

import base64
import fnmatch
import mimetypes
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Optional

#: The most one send takes. A package has its own smaller limit, and says so.
MAX_SEND = 64 * 1024 * 1024
#: A conversion can take a while: LibreOffice on a long document.
SEND_TIMEOUT_S = 300.0
PICK_TIMEOUT_S = 600.0
NO_REVIEW = "Drawbridge isn't running, so nothing was sent."
#: Words that can't be read are treated as private: only a package with internet hosts ever asks about it.
UNREAD_LABEL = "private.unread"


class SendRefused(Exception):
    """A plain line for the owner: the file was not sent."""


def types_said(manifest: dict | None) -> str:
    kinds = [t for a in (manifest or {}).get("accepts") or [] for t in a.get("types") or [] if t.startswith(".")]
    return ", ".join(dict.fromkeys(kinds))


def tool_for(manifest: dict | None, name: str) -> Optional[str]:
    """The tool that imports a file of this name, by its ending or its kind (``text/*``), or None."""
    suffix = Path(name).suffix.lower()
    mime = mimetypes.guess_type(name)[0] or ""
    for a in (manifest or {}).get("accepts") or []:
        for t in a.get("types") or []:
            if t.lower() == suffix or (mime and fnmatch.fnmatch(mime, t.lower())):
                return str(a.get("tool") or "") or None
    return None


def sendable(core: Any) -> list[dict]:
    """``[{id, name, types, status}]``: every installed package that takes files."""
    out = []
    for row in core.registry.list():
        if row.get("status") == "removed":
            continue
        try:
            manifest = core.steward.read_manifest(row["id"])
        except Exception:  # noqa: BLE001 - a manifest that can't be read: nothing goes to it
            continue
        accepts = (manifest or {}).get("accepts") or []
        if accepts:
            out.append({"id": row["id"], "name": row.get("name") or row["id"], "status": row.get("status"),
                        "types": list(dict.fromkeys(t for a in accepts for t in a.get("types") or []))})
    return out


def args_for(name: str, data: bytes) -> dict:
    """D48c: a text file as text, anything else as base64."""
    from .zone1.folders import TEXT_SUFFIXES
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if Path(name).suffix.lower() in TEXT_SUFFIXES:
        try:
            return {"file_name": name, "mime": mime, "text": data.decode("utf-8-sig")}
        except UnicodeDecodeError:
            pass
    return {"file_name": name, "mime": mime, "content_b64": base64.b64encode(data).decode("ascii")}


def _labels(manifest: dict, name: str, data: bytes) -> list[str]:
    """What the file holds, by kind, only for a package with internet hosts (Drawbridge's existing check)."""
    if not (manifest.get("needs") or {}).get("hosts"):
        return []
    from .zone1.classify import classify_text
    from .zone1.pinned import words_of
    words = words_of(name, data)
    if words is None:
        return [UNREAD_LABEL]
    return sorted({h.label for h in classify_text(words, path="outbound").hits})


def _target(core: Any, pid: str, row: dict, manifest: dict) -> Any:
    from .zone2 import mcp
    if not row.get("port"):
        try:
            row = core.steward.start(pid) or core.registry.get(pid) or row
        except Exception as exc:  # noqa: BLE001 - its plain reason
            raise SendRefused(f"{row.get('name') or pid} could not be started: {exc}") from None
    try:
        return mcp.target_for(row, manifest, mcp.usable_token(core.steward.token(pid)), core.steward.folder(pid))
    except mcp.McpError as exc:
        raise SendRefused(exc.message) from None


def hand(core: Any, review: Optional[Callable[[dict], dict]], pid: str, name: str, data: bytes) -> str:
    """Send one file to one package. Its answer, in the locked form, or :class:`SendRefused`. The file isn't kept."""
    from .zone2 import mcp
    from .zone2.gate import production
    name = Path(str(name).replace("\\", "/")).name
    row = core.registry.get(pid)
    if not row or row.get("status") == "removed":
        raise SendRefused("That app is not installed.")
    app = row.get("name") or pid
    manifest = core.steward.read_manifest(pid)
    tool = tool_for(manifest, name)
    if not tool:
        said = types_said(manifest)
        raise SendRefused(f"{app} doesn't take that kind of file." + (f" It takes {said}." if said else ""))
    if len(data) > MAX_SEND:
        raise SendRefused("That file is too big to send.")
    from .owner_files import EDITS_OFF, blocks_a_send
    if (manifest.get("needs") or {}).get("owner_files") == "write" and blocks_a_send(core.state, manifest, tool):
        raise SendRefused(EDITS_OFF)                   # D140
    if not callable(review):
        raise SendRefused(NO_REVIEW)
    verdict = review({"package": pid, "tool": f"{pid}.{tool}", "name": tool, "changes": True, "entry": None,
                      "args": {"file_name": name, "size": len(data)}, "labels": _labels(manifest, name, data),
                      "summary": [f"file_name: {name}", f"size: {len(data)} bytes"], "notify": lambda _card: None})
    if not isinstance(verdict, dict) or verdict.get("run") is not True:
        raise SendRefused(str((verdict or {}).get("line") or "Nothing was sent."))
    got = mcp.call_tool(_target(core, pid, row, manifest), tool, args_for(name, data), timeout=SEND_TIMEOUT_S)
    text = production().keep(str(got.get("text") or ""))
    if not got.get("ok"):
        raise SendRefused(text or f"{app} could not take it.")
    return text or f"{app} has it."


# -- the owner's picker ---------------------------------------------------------------------------

_PICK_HEAD = ("$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Add-Type -AssemblyName System.Windows.Forms;"
              "$o=New-Object Windows.Forms.Form -Property @{TopMost=$true};")
_PICK = {
    "file": ("$d=New-Object Windows.Forms.OpenFileDialog;$d.Title='Pick a document for your bot';"
             "$d.Filter='Documents (*.txt;*.md;*.docx;*.pdf)|*.txt;*.md;*.docx;*.pdf';$d.Multiselect=$false;",
             "$d.FileName"),
    # All files too: she can pick an old .doc and be told why not. The Core checks each one she picks.
    "files": ("$d=New-Object Windows.Forms.OpenFileDialog;$d.Title='Pick documents for your bot';"
              "$d.Filter='Documents (*.txt;*.md;*.docx;*.pdf)|*.txt;*.md;*.docx;*.pdf|All files (*.*)|*.*';$d.Multiselect=$true;",
              "($d.FileNames -join [char]10)"),
    "folder": ("$d=New-Object Windows.Forms.FolderBrowserDialog;$d.Description='Pick a folder for your bot to read';"
               "$d.ShowNewFolderButton=$false;", "$d.SelectedPath"),
}
_picking = threading.Lock()


def pick_script(kind: str) -> str:
    start, field = _PICK[kind]
    return (_PICK_HEAD + start + f"if($d.ShowDialog($o) -eq 'OK'){{[Console]::Out.Write({field})}}")


def can_pick() -> bool:
    return os.name == "nt" and os.path.isfile(_powershell())


def _powershell() -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                        "powershell.exe")


def _no_window(kind: str) -> str:
    return ("The window to pick from could not open. Choose them from this browser instead." if kind == "files"
            else "The window to pick from could not open. Type the path instead.")


def pick(kind: str) -> str:
    """The path she picks (with ``files``, one a line), or "" if she closes it. :class:`SendRefused` when one is open
    already, or it can't open."""
    if kind not in _PICK:
        raise ValueError(kind)
    if not _picking.acquire(blocking=False):
        raise SendRefused("A window to pick from is open already. Look for it on this computer.")
    try:
        encoded = base64.b64encode(pick_script(kind).encode("utf-16-le")).decode("ascii")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        got = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded],
                             capture_output=True, stdin=subprocess.DEVNULL, timeout=PICK_TIMEOUT_S, creationflags=flags)
        path = got.stdout.decode("utf-8", "replace").strip()
        # A path counts whatever PowerShell's exit code says (seen on the real dialog); Pin and Link check it anyway.
        if got.returncode != 0 and not path:
            raise SendRefused(_no_window(kind))
        return path
    except subprocess.TimeoutExpired:
        return ""                                       # subprocess.run has ended our own process
    except OSError:
        raise SendRefused(_no_window(kind)) from None
    finally:
        _picking.release()


__all__ = ["MAX_SEND", "SendRefused", "args_for", "can_pick", "hand", "pick", "pick_script", "sendable", "tool_for",
           "types_said"]
