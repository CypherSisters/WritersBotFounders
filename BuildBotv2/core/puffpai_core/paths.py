r"""Where things live (01 "Where things are").

One folder per bot, under :func:`data_root`: ``%LOCALAPPDATA%\PuffPAI\<bot-name>\`` on
Windows, ``~/.local/share/puffpai/<bot-name>/`` on Linux (or under ``$XDG_DATA_HOME``),
``~/Library/Application Support/PuffPAI/<bot-name>/`` on a Mac. Tests and
developers point the Core somewhere else with ``PUFFPAI_STATE`` (the whole
state folder). ``PUFFPAI_BOT`` names the bot when ``PUFFPAI_STATE`` is not
set; the default name is ``buildbot``. ``PUFFPAI_HOME`` moves the folder that holds every bot, for a computer
whose home disk is small (``install.sh --data-dir``).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DEFAULT_BOT = "buildbot"
_BOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


def bot_name() -> str:
    name = os.environ.get("PUFFPAI_BOT", "").strip() or DEFAULT_BOT
    if not _BOT_NAME.match(name) or ".." in name:
        raise ValueError(f"PUFFPAI_BOT is not a usable bot name: {name!r}")
    return name


def data_root() -> Path:
    """The folder that holds every bot of this user on this computer (D132). Not created here."""
    moved = os.environ.get("PUFFPAI_HOME", "").strip()
    if os.path.isabs(moved):
        return Path(moved)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "PuffPAI"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PuffPAI"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local" / "share") / "puffpai"


def state_dir() -> Path:
    """The bot's own folder. Created on first use."""
    override = os.environ.get("PUFFPAI_STATE", "").strip()
    if override:
        path = Path(override)
    else:
        path = data_root() / bot_name()
    path.mkdir(parents=True, exist_ok=True)
    return path


def contracts_dir() -> Path:
    """The ``contracts/`` folder: ``PUFFPAI_CONTRACTS`` or the repo root next to ``core/``."""
    override = os.environ.get("PUFFPAI_CONTRACTS", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "contracts"
