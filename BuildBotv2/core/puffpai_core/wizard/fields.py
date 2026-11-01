"""Field rules both renderers share (03 1.1, D36(b)): show_when, examples, path expansion.

Nothing here draws anything. The engine uses these to decide what to ask and
what an answer means; the browser and the terminal only show what it returns.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import sys
from typing import Any, Mapping

SECRET_TYPES = ("secret", "secret_confirm")


def this_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def visible(field: dict, answers: Mapping[str, Any]) -> bool:
    """A field shows only when every field named in its ``show_when`` has one of the listed values."""
    rule = field.get("show_when") or {}
    return all(answers.get(other) in wanted for other, wanted in rule.items())


def example(field: dict, platform: str, board: str | None = None) -> str | None:
    """The one example for this computer: the board's, else the platform's; a plain string is everywhere."""
    ex = field.get("examples")
    if ex is None:
        return None
    if isinstance(ex, str):
        return ex
    if board and board in ex:
        return ex[board]
    return ex.get(platform)


class PathError(ValueError):
    """A path answer that can't be made into a real folder. The message is plain words."""


_VAR = re.compile(r"%([^%]+)%|\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _lookup(env: Mapping[str, str], name: str, platform: str) -> str | None:
    if name in env:
        return env[name]
    if platform == "windows":                 # Windows variable names ignore case
        low = name.lower()
        for key, value in env.items():
            if key.lower() == low:
                return value
    return None


def expand_path(text: Any, platform: str | None = None, env: Mapping[str, str] | None = None,
                home: str | None = None) -> str:
    """``~``, ``%VAR%``, ``$VAR`` and ``${VAR}`` expanded, then tidied for this platform.

    An unknown variable is a plain error, never a folder named ``$FOO``.
    """
    platform = platform or this_platform()
    env = os.environ if env is None else env
    if not isinstance(text, str) or not text.strip():
        raise PathError("Type a folder, or press Enter to use the suggested one.")
    raw = text.strip().strip('"')
    pathmod = ntpath if platform == "windows" else posixpath
    if raw == "~" or raw.startswith("~/") or raw.startswith("~\\"):
        if home is None:
            home = _lookup(env, "USERPROFILE", platform) if platform == "windows" else _lookup(env, "HOME", platform)
            home = home or os.path.expanduser("~")
        raw = home + raw[1:]

    def sub(m: re.Match) -> str:
        name = m.group(1) or m.group(2) or m.group(3)
        value = _lookup(env, name, platform)
        if value is None:
            raise PathError(f"There is no variable named {name} on this computer, so BuildBot can't tell "
                            f"which folder you mean. Type the folder in full.")
        return value

    expanded = _VAR.sub(sub, raw)
    return pathmod.normpath(expanded)


SYNCED = ("onedrive", "dropbox", "google drive", "googledrive", "icloud drive", "icloud")


def synced_folder(path: str) -> str | None:
    """The cloud-sync service a folder sits in, or None."""
    low = path.lower().replace("\\", "/")
    for name in SYNCED:
        if f"/{name}" in low or low.startswith(name):
            return {"onedrive": "OneDrive", "dropbox": "Dropbox", "google drive": "Google Drive",
                    "googledrive": "Google Drive", "icloud drive": "iCloud Drive", "icloud": "iCloud"}[name]
    return None
