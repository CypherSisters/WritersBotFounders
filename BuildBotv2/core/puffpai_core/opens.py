r"""What opens when BuildBot starts (D94): ``settings\opens.json``.

``{"opens": "window"}`` (the default, the owner's change of 10-30: the PuffPAI window, D81), ``{"opens": "chat"}`` (the
chat in a terminal, D86) or ``{"opens": "dashboard"}`` (the Desktop in her browser). A missing or broken file, or any
other value, reads as "window". ``puffpai start`` reads it;
``PUT /v1/settings/opens``, the Desktop's Settings page and ``puffpai start --opens`` save it through
:func:`save`, so one rule holds everywhere.
"""

from __future__ import annotations

from pathlib import Path

from . import jsonfile

CHAT, DASHBOARD, WINDOW = "chat", "dashboard", "window"
CHOICES = (WINDOW, CHAT, DASHBOARD)
DEFAULT = WINDOW
FILE = ("settings", "opens.json")


def _path(state: Path | str) -> Path:
    return Path(state).joinpath(*FILE)


def read(state: Path | str) -> str:
    """"window", "chat" or "dashboard"; anything else reads as "window"."""
    try:
        data = jsonfile.read(_path(state), {})
    except (OSError, ValueError):                # a folder at that path, no permission
        data = {}
    got = data.get("opens") if isinstance(data, dict) else None
    return got if got in CHOICES else DEFAULT


def save(state: Path | str, value: object) -> str:
    """Save "window", "chat" or "dashboard". ValueError with the plain reason for anything else; nothing is saved then."""
    if value not in CHOICES:
        raise ValueError('What opens when BuildBot starts is "window", "chat" or "dashboard".')
    jsonfile.write(_path(state), {"opens": value})
    return str(value)
