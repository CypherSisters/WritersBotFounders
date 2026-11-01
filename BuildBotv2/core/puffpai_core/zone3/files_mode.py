r"""The owner's one setting for files Drawbridge can't read as text (D95): ``settings\drawbridge.json``.

``{"files": "normal"}`` (the default) or ``{"files": "locked"}``. No file reads as Normal (her default). A
file that is there but broken, or holds any other value, reads as Locked down: a setting that can't be read
never loosens a check. Only the owner changes it, at a paired device (``PUT /v1/settings/drawbridge``, one
audit line per change); Toolsmith and packages have no route that writes it (X24). Drawbridge reads it at
every check (``zone3.service`` hands it :func:`read`), and its verdict records the setting it was made under.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import jsonfile
from .kinds import LOCKED, MODES, NORMAL

DEFAULT = NORMAL
FILE = ("settings", "drawbridge.json")
TITLE = "Files Drawbridge can't read"
CHOICES = (
    {"id": NORMAL, "name": "Normal",
     "line": "Pictures, sound, video, fonts, model weights, documents, and archives of those, pass when they check "
             "clean. Files that can run code still ask you."},
    {"id": LOCKED, "name": "Locked down", "line": "Every file Drawbridge can't read asks you first."},
)
ASK = 'Send {"files": "normal"} or {"files": "locked"}.'


def _path(state: Path | str) -> Path:
    return Path(state).joinpath(*FILE)


def read(state: Path | str) -> str:
    """"normal" or "locked". Missing: "normal". Broken, unreadable or any other value: "locked"."""
    try:
        data = json.loads(_path(state).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT
    except (OSError, ValueError):
        return LOCKED
    got = data.get("files") if isinstance(data, dict) else None
    return got if got in MODES else LOCKED


def save(state: Path | str, value: object) -> str:
    """Save "normal" or "locked". ValueError with the plain ask for anything else; nothing is saved then."""
    if value not in MODES:
        raise ValueError(ASK)
    jsonfile.write(_path(state), {"files": value})
    return str(value)


def view(state: Path | str) -> dict:
    """What the Developer page shows: the title, the choice now and both choices in plain words."""
    now = read(state)
    return {"files": now, "title": TITLE, "choices": [dict(c, on=c["id"] == now) for c in CHOICES]}


__all__ = ["ASK", "CHOICES", "DEFAULT", "FILE", "LOCKED", "MODES", "NORMAL", "TITLE", "read", "save", "view"]
