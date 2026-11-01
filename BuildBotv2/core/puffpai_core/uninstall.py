r"""``puffpai uninstall``: remove this one bot (D133, v1's ``remove_cli`` for one bot).

It removes the bot's own folder, its shortcuts and its launchers. It says all of that first and asks. What can't come back
goes last: everything else in the folder, then the vault, then the folder itself, so a stop part way leaves a vault to keep.

As v1: the vault has its own question ("k" keeps a copy in Documents), then one y or yes for the whole. ``--yes`` asks
nothing and removes the vault too, unless ``--keep-vault``. No answer (no terminal) removes nothing.

It never touches another bot, a data folder she chose outside the bot's folder, a linked folder or the code, which any
number of bots may share (D129): deleting the code folder is hers to do when no bot uses it.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable

from . import jsonfile
from .packages import fsutil

TITLE = "This removes this bot from this computer:"
VAULT_LINE = "  - its vault, with every key in it. A vault can't come back once it is gone."
VAULT_ASK = "Press k to keep a copy of the vault in your Documents folder, or Enter to remove it: "
CONFIRM = "Remove this bot? Type yes: "
KEPT = "Nothing was removed."
WINDOW_OPEN = "This bot's PuffPAI window is open. Close it first, then run this again."
BUSY = "BuildBot did not stop, so nothing was removed. Close its window, then run this again."
#: The file every Core makes in its own folder, and no other folder has.
MARK = "tokens.db"
NOT_THIS = "That is not a bot's folder, so nothing was removed: {path}"
LAUNCHERS = ("Start BuildBot.cmd", "Stop BuildBot.cmd", "Chat with BuildBot.cmd", "Sign in a browser.cmd", "Open BuildBot window.cmd",
             "start-buildbot.sh", "stop-buildbot.sh", "chat-buildbot.sh", "signin-buildbot.sh", "Start BuildBot.command", "Stop BuildBot.command")
SHUTDOWN_WAIT_S = 90.0


def _size_words(n: int) -> str:
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} bytes"


def _size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not fsutil.is_link(os.path.join(root, d))]
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _same(a: Path | str, b: Path | str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def refused(state: Path) -> str:
    """Why *state* can't be removed. Only a real bot's own folder may go: its Core made ``tokens.db`` there, and it holds no
    other bot. A folder named ``settings`` proves nothing, and neither does any one check alone, since a mistaken
    ``PUFFPAI_STATE`` could name a parent of bots, Documents or a drive."""
    no = NOT_THIS.format(path=state)
    if str(state).startswith(("\\\\", "//")):            # \\?\C:\... and \\localhost\C$\... would read as another path
        return no
    real = Path(os.path.realpath(state))
    if fsutil.is_link(state) or not (state / "settings").is_dir() or not (state / MARK).is_file():
        return no
    home = Path(os.path.realpath(Path.home()))
    if real == Path(real.anchor) or len(real.parts) < 3 or real == home or real in home.parents:
        return no
    if any(_same(real, home / name) for name in ("Documents", "Desktop", "Downloads", "OneDrive", "Pictures")):
        return no
    code = Path(os.path.realpath(Path(__file__).resolve().parents[2]))
    if real == code or real in code.parents or code in real.parents:
        return no
    try:
        if any((child / MARK).is_file() for child in real.iterdir() if child.is_dir() and not fsutil.is_link(child)):
            return no                                    # it holds another bot
    except OSError:
        return no
    return ""


def data_inside(state: Path) -> Path | None:
    """Her data folder when it is inside the bot's folder (the default): it goes with the bot, so she is told. A folder
    she chose elsewhere stays."""
    got = jsonfile.read(state / "settings" / "data.json", {})
    folder = got.get("data_folder") if isinstance(got, dict) else None
    path = Path(folder) if isinstance(folder, str) and folder else state / "data"
    try:
        real = path.resolve()
        return path if path.is_dir() and (real == state.resolve() or state.resolve() in real.parents) else None
    except OSError:
        return None


def own_launchers(state: Path, bot: str) -> list[Path]:
    """The launchers this bot's name is written in. Never another bot's. None for a scratch bot (``PUFFPAI_STATE``)."""
    if os.environ.get("PUFFPAI_STATE", "").strip():
        return []
    repo = Path(__file__).resolve().parents[2]
    out = []
    for folder in (repo / "bots" / bot, repo):
        for name in LAUNCHERS:
            f = folder / name
            try:
                if f.is_file() and re.search(rf"""PUFFPAI_BOT=["']?{re.escape(bot)}["']?(\s|$)""", f.read_text(encoding="ascii", errors="replace"), re.M):
                    out.append(f)
            except OSError:
                pass
    return out


def plan_lines(state: Path, vault: bool, launchers: list[Path], outside_data: str) -> list[str]:
    lines = [TITLE, f"  - its folder, {state} ({_size_words(_size(state))}): settings, apps, what it read and its chats"]
    inside = data_inside(state)
    if inside is not None:
        lines.append(f"  - its data folder, {inside}, which is inside that folder")
    if launchers:
        lines.append(f"  - its launchers, in {launchers[0].parent}")
    lines.append("  - its shortcuts, if it made any")
    if vault:
        lines.append(VAULT_LINE)
    lines.append("It leaves the code folder, other bots and any folder you linked.")
    if outside_data:
        lines.append(f"It leaves your data folder, {outside_data}.")
    return lines


def keep_vault_copy(vault: Path, bot: str) -> Path:
    """A copy of the vault outside the bot's folder, before anything goes. A vault folder is useless without its recovery
    code or PIN, so this holds no key in the clear."""
    home = Path.home() / "Documents"
    base = home if home.is_dir() else Path.home()
    dest = base / f"PuffPAI vault {bot} {time.strftime('%Y-%m-%d %H%M%S')}"
    try:
        shutil.copytree(vault, dest)
    except OSError:
        shutil.rmtree(dest, ignore_errors=True)          # never half a vault that looks whole
        raise
    return dest


def remove_all(state: Path) -> list[str]:
    """Everything but the vault, then the vault, then the folder. Links are removed as links, never followed. The paths
    that could not go."""
    left = []

    def gone(path: Path) -> None:
        try:
            if fsutil.is_link(path):
                fsutil._remove_link(fsutil.long_path(path))
            elif path.is_dir():
                fsutil._remove_tree(fsutil.long_path(path))
            else:
                path.unlink(missing_ok=True)
        except OSError:
            left.append(str(path))
    for entry in [p for p in state.iterdir() if p.name != "vault"]:
        gone(entry)
    if left:                                             # the vault goes last, and only if all else went: a stop leaves it
        return left
    if (state / "vault").exists() or fsutil.is_link(state / "vault"):
        gone(state / "vault")
    if not left:
        try:
            state.rmdir()
        except OSError:
            left.append(str(state))
    return left


def run(args: argparse.Namespace, ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> int:
    from . import corelock, window
    from .cli import _call, _terminal
    from .paths import bot_name, state_dir
    # Absolute before anything moves: a relative PUFFPAI_STATE would mean another folder after a chdir.
    state, bot = Path(os.path.abspath(state_dir())), bot_name()
    why = refused(state)
    if why:
        say(why)
        return 2
    if corelock.holder(window._folder(state)) is not None:
        say(WINDOW_OPEN)
        return 1
    vault = state / "vault"
    has_vault = vault.is_dir() and any(vault.iterdir())
    launchers = own_launchers(state, bot)
    got = jsonfile.read(state / "settings" / "data.json", {})
    chosen = got.get("data_folder") if isinstance(got, dict) else None
    outside = str(chosen) if isinstance(chosen, str) and chosen and data_inside(state) is None and Path(chosen).exists() else ""
    for line in plan_lines(state, has_vault, launchers, outside):
        say(line)
    keep = bool(args.keep_vault)
    if not args.yes:
        try:
            if has_vault and not keep:
                keep = ask(VAULT_ASK).strip().lower() == "k"
            if ask(CONFIRM).strip().lower() not in ("y", "yes"):
                say(KEPT)
                return 1
        except (EOFError, KeyboardInterrupt, RuntimeError, OSError):     # no terminal at all is no answer too
            say(KEPT)
            return 1
    info = _terminal()
    if info is not None:
        say("Stopping BuildBot.")
        try:
            _call(info, "POST", "/v1/shutdown", {})
        except OSError:
            pass
        end = time.monotonic() + SHUTDOWN_WAIT_S
        while (state / "settings" / "terminal.json").exists() and time.monotonic() < end:
            time.sleep(0.25)
    if corelock.holder(window._folder(state)) is not None:          # it may have opened while the question waited
        say(WINDOW_OPEN)
        return 1
    if corelock.holder(state) is not None:
        say(BUSY)
        return 1
    if has_vault and keep:
        try:
            say(f"A copy of your vault is in {keep_vault_copy(vault, bot)}.")
        except OSError as exc:
            say(f"The vault could not be copied ({type(exc).__name__}), so nothing was removed.")
            return 1
    try:
        say(window.remove_shortcuts(state))
    except OSError:
        pass
    for f in launchers:
        f.unlink(missing_ok=True)
    if launchers and launchers[0].parent.parent.name == "bots":
        try:
            launchers[0].parent.rmdir()
        except OSError:
            pass
    if Path.cwd().resolve() == state.resolve() or state.resolve() in Path.cwd().resolve().parents:
        os.chdir(state.parent)
    left = remove_all(state)
    if left:
        say("These could not be removed, because something still holds them. Close it, then run this again:")
        for path in left:
            say(f"  - {path}")
        return 1
    say(f"This bot is removed. The code folder stays: {Path(__file__).resolve().parents[2]}. Delete it by hand when no bot uses it.")
    return 0


__all__ = ["run"]
