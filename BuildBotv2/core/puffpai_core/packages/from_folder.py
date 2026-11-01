r"""Her own code folder with no ``puffpai.json`` (D133, v1's ``from_local_folder``): the Core reads how it starts and writes
``.buildbot\puffpai.json`` in that folder, the place the Core's own file for a folder goes (02 "Adopted packages"), and
never a file at the top of her code. It then goes on as any linked folder: the Drawbridge check, one Yes card, run in place.

How it starts comes from Toolsmith's own table (``runbook.detect``: its package.json, pyproject, Dockerfile) and the same
no-shell rules as an adopted app (``adopt.run_block``). Nothing here guesses: with no start line it says so, and nothing is
written. v1 also read README steps with the model's help; here that is Toolsmith's job, so she is pointed to it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .fsutil import is_link
from .manifest import LINKED_MANIFEST

#: Any port: ``run_block`` turns it into ``{port}`` where it stands in a line.
_ANY_PORT = 18999
NOTHING_SAYS = ("Nothing in that folder says how to start it, so it was not added. Run puffpai new to start a package and "
                "move your code in, or ask Toolsmith in chat to make an app from it.")


def _read(folder: Path, name: str, limit: int = 100_000) -> str:
    try:
        return (folder / name).read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "!", "[", "<", "`", "=", "-")):
            return " ".join(line.split())[:300]
    return ""


def package_id(folder: Path) -> str:
    """The id its folder's name gives: the manifest's own rule (a letter first, lower case letters, digits, dashes)."""
    name = re.sub(r"[^a-z0-9-]+", "-", folder.name.lower()).strip("-")
    name = re.sub(r"^[^a-z]+", "", name)[:40].rstrip("-")
    return name if len(name) >= 2 else "my-code"


def write_manifest(folder: Path) -> str:
    """Write the Core's file for *folder*. "" when it is written, or the plain reason it was not."""
    from ..zone3.toolsmith import runbook
    from ..zone3.toolsmith.adopt import Unsaved, run_block
    readme = next((text for text in (_read(folder, n) for n in ("README.md", "README.txt", "README", "readme.md")) if text), "")
    try:
        pkg = json.loads(_read(folder, "package.json") or "{}")
    except ValueError:
        pkg = {}
    pkg = pkg if isinstance(pkg, dict) else {}
    row = runbook.detect(str(folder), _ANY_PORT, readme, folder.name)
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    run = next((s for s in ("start", "dev", "serve") if scripts.get(s)), "")
    if not row.get("start") and run:                 # v1: its own package.json scripts, how a person starts it
        row = {"stack": "Node", "via": "package.json", "install": "npm install", "start": "npm start" if run == "start" else f"npm run {run}"}
    if not row.get("start") or row.get("stack", "").lower().startswith("docker"):
        return NOTHING_SAYS
    setup = [row[k] for k in ("install", "build") if row.get(k)]
    try:
        block, _notes, _unsaved = run_block(row["start"], setup, _ANY_PORT)
    except Unsaved as exc:
        return f"{exc.reason} So it was not added."
    name = (str(pkg.get("name") or "").strip() or folder.name)[:60]
    page = "http://127.0.0.1:{port}/"
    manifest = {
        "schema": "puffpai-package/1", "id": package_id(folder), "kind": "app", "name": name, "version": "0.1.0",
        "description": (str(pkg.get("description") or "").strip() or _first_line(readme) or f"{name}: your own code on this computer.")[:300],
        "author": "you", "license": "Your own", "run": {"default": block},
        "health": {"url": page, "expect": 200, "every_s": 60},
        "ui": {"pages": [{"id": "main", "title": name, "url": page, "show": "new_tab"}], "tray": [{"label": name, "opens": "main"}]},
        "needs": {"hosts": [], "keys": []},
        "consent": f"{name} is your own code. It runs in place in {folder}, as a program on this computer, as you. BuildBot read how to start it from its "
                   f"{row.get('via') or 'files'}: {row.get('stack') or 'a program'}.",
    }
    target = folder / LINKED_MANIFEST
    if os.path.lexists(target.parent) and is_link(target.parent):
        return "Its .buildbot folder is a link to somewhere else, so it was not added."
    try:
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"Its .buildbot folder could not be written ({type(exc).__name__}), so it was not added."
    return ""


__all__ = ["NOTHING_SAYS", "package_id", "write_manifest"]
