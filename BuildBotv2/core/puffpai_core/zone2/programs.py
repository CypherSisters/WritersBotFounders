# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/programs.py (adapted): the list, register, the help reading, match,
# run_cli, finding and starting desktop programs, open_in_program. Changed for v2: runs in a job through
# zone2.commands, the audit line holds no arguments (X14), Always allow and open_yes carry a seal (X10), a batch file is
# never a plain call (D16, steward.noshell), and the file rules read v2's own folders.
"""Her own programs: command line tools and desktop apps she adds herself (D16, D138). PROPOSED, not tested by the owner.

The owner asked: "If I have Office on my computer, can I add those? If I have my own programs, can I connect them?"
Yes. One list, ``<state>/programs.json`` (``{"programs": [row ...], "open_yes": [program id ...], "seal": mac}``), one
row per program she added::

    {"id", "name", "kind": "cli" | "desktop", "path", "command", "added_at",
     "help": "<first lines of its own help>", "commands": [specs], "always_allow": false}

* **A command line program** (``kind: "cli"``) is added by its full path or by a name on ``PATH`` (``ffmpeg``), found
  once, here, and kept by its full path. Its own ``--help`` (then ``-h``, then ``/?``) runs once, 10 seconds at most, no
  shell, no window; the first 200 lines are kept and read with ``program_help``. The brain sees it in the installed
  list and in ``run_command``'s description, and runs it with ``run_command``: her Yes for every command, unless she
  turned on **Always allow** for that program. Then a plain call of THAT program (one program, its arguments, no shell
  features at all) runs as a list of arguments by its full path, never through a shell, and every run writes an audit
  line. Anything with shell features still asks.
* **A batch file** (``.bat``, ``.cmd``) can be listed, but Windows runs those only through ``cmd.exe``, a shell, so it
  is never a plain call: it asks each time, and Always allow can't be on for it.
* **A desktop program** (``kind: "desktop"``): an ``.exe`` or Start Menu shortcut on Windows, an ``.app`` on a Mac, a
  ``.desktop`` file on Linux. It is started on purpose with its window showing (``os.startfile``, ``open -a``, the
  ``.desktop`` file's own ``Exec``), optionally with a file. The brain opens a file in one with ``open_in_program``,
  which asks her Yes the first time per program. ``"default"`` stands for the file's own app, shared by every file.
* **Opening a file** works only for files inside her data folder, a linked folder or one she pinned, never the vault or
  the bot's own folder. A program file (``.exe``, ``.bat``, ``.js`` ...) is never opened in its default app: that would
  run it.

X10: a Yes comes only from the owner. Always allow and ``open_yes`` are Yeses, so the file carries a seal under the
machine key (``anchor``); a list that fails it asks again. Honest limit: a program written to target PuffPAI, running as
her, can read that key (09 section 3), and can replace a program's file at its path.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shlex
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .. import anchor as anchor_mod
from .. import devshell, jsonfile
from ..steward import noshell, procflags
from ..zone1 import folders as zone1_folders
from . import commands, program_help

logger = logging.getLogger(__name__)

FILE = "programs.json"
KINDS = ("cli", "desktop")
SEAL = "own_programs"
HELP_SECONDS = 10
HELP_LINES = 200
#: A program with subcommands (git, docker, npm): at most this many ``<program> <sub> --help`` reads, this many seconds
#: for all of them, and this many commands kept.
SUB_HELP_MAX = 25
SUB_HELP_BUDGET = 60
COMMANDS_MAX = 60
#: The flag that prints a subcommand's help as text. ``git <sub> --help`` opens a web page on Windows (or a pager);
#: ``-h`` prints the usage.
SUB_HELP_FLAG = {"git": "-h"}
#: Subcommands whose help is never run (listed all the same): ones that change or remove things, publish, or start
#: something long-running, in case a program ignores ``--help`` and does the thing.
NO_SUB_HELP = frozenset({
    "help", "server", "serve", "daemon", "watch", "shell", "repl", "console", "start",
    "stop", "restart", "kill", "run", "exec", "up", "down", "rm", "rmi", "remove",
    "delete", "del", "uninstall", "unpublish", "publish", "deploy", "push", "prune",
    "purge", "clean", "reset", "destroy", "drop", "wipe", "format", "update",
    "upgrade", "self-update", "selfupdate", "install", "login", "logout", "auth",
    "edit", "open", "docs", "bugs", "repo", "home", "browse"})
#: Installed desktop programs offered to pick from.
DESKTOP_MAX = 300
#: Installed command line programs named in run_command's description.
CLI_LISTED = 12

_IS_WINDOWS = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"
ID_RE = re.compile(r"^p[0-9a-f]{8}\Z")
_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
#: What a plain call never has: anything a shell would read as more than one program and its arguments (chaining, pipes,
#: redirection, variables, command substitution, grouping, a second line).
_SHELL_CHARS = frozenset(";|&$`<>(){}\n\r")
_CLI_EXTS_WIN = (".exe", ".com", ".bat", ".cmd", ".ps1", ".py", ".sh")
_DESKTOP_EXTS_WIN = (".exe", ".lnk")
#: Files that would RUN, not open, in their default app.
RUNNABLE_EXTS = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".hta", ".msi", ".msp", ".mst", ".lnk", ".url", ".scr",
    ".pif", ".cpl", ".jar", ".reg", ".application", ".appref-ms", ".sh", ".command",
    ".app", ".desktop", ".py", ".pyw", ".scf", ".inf", ".msc", ".gadget", ".diagcab",
    ".settingcontent-ms", ".library-ms", ".search-ms", ".xll", ".xbap", ".chm",
    ".appimage", ".run", ".bin", ".pkg", ".dmg", ".deb", ".rpm"})

#: Programs that run any other command: with Always allow on, the bot could do anything on this computer without asking.
#: Said plainly, never refused.
_RUNS_ANYTHING = frozenset({
    "cmd", "powershell", "pwsh", "bash", "sh", "zsh", "fish", "dash", "wsl", "python",
    "python3", "py", "pythonw", "node", "deno", "bun", "ruby", "perl", "php", "lua",
    "wscript", "cscript", "mshta", "rundll32", "regsvr32", "env", "sudo", "osascript",
    "java", "dotnet", "conhost", "start", "xargs", "find", "git"})
#: Shells and interpreters: their "help" is a banner, a prompt or an error (cmd, powershell), and some open a window.
#: It is never run for them; the row says what they are instead.
_SHELLS_NO_HELP = frozenset({
    "cmd", "powershell", "powershell_ise", "pwsh", "bash", "sh", "zsh", "fish", "dash",
    "ksh", "csh", "tcsh", "wsl", "python", "python3", "py", "pythonw", "pyw", "node",
    "deno", "bun", "ruby", "perl", "php", "lua", "wscript", "cscript", "mshta",
    "osascript", "forfiles", "wmic"})
_PY_VERSION_RE = re.compile(r"^(?:python|pythonw|python3|pypy3?|node|ruby|perl|php|lua)[0-9.]*\Z", re.IGNORECASE)
SHELL_NOTE = "A command shell: BuildBot passes it one command at a time."

ALWAYS_ALLOW_WARNING = ("This program can start any other program, so with Always allow "
                        "on, the bot can do anything on this computer without asking you.")
BATCH_NO_ALWAYS = ("This is a Windows batch file, and Windows runs those only through cmd.exe, a shell. So it asks you "
                   "each time, and Always allow can't be on for it.")
NO_SEAL = "BuildBot can't protect this choice on this computer (no machine key), so it stays off."
NO_AUDIT = "BuildBot could not write the audit line, so nothing was run."

NOT_FOUND = "There is no program at that place. Check the path and try again."
NOT_ON_PATH = ("I could not find a program called {name} on this computer. Give its "
               "full path instead.")
NOT_A_PROGRAM = "That file is not a program BuildBot can start here."
NO_SUCH = "There is no program by that name in your list."
FILE_OUTSIDE = ("I can only open files in your data folder, a file you pinned, or a folder you "
                "linked. That file is somewhere else.")
FILE_MISSING = "There is no file at that place."
FILE_RUNS = ("That file is a program, not a document, so opening it would run it. "
             "I only open documents this way.")

_lock = threading.Lock()


def is_shell(command: str) -> bool:
    """True for a shell or interpreter (its help is never run)."""
    word = str(command or "").lower()
    return word in _SHELLS_NO_HELP or bool(_PY_VERSION_RE.match(word))


class ProgramError(ValueError):
    """A plain reason for the owner."""


# -- the list -----------------------------------------------------------------------------------------------------
def _file(state: Path | str) -> Path:
    return Path(state) / FILE


def _grants(data: dict) -> dict:
    """What her Yes granted: what the seal covers."""
    where = {str(r.get("id")): str(r.get("path")) for r in data.get("programs") or []}
    return {"allow": sorted([str(r.get("id")), str(r.get("path")), str(r.get("command"))]
                            for r in data.get("programs") or [] if r.get("always_allow") is True),
            "open": sorted([str(i), where.get(str(i), "")] for i in data.get("open_yes") or [])}


def _seal_ok(state: Path | str, raw: dict) -> bool:
    grants = _grants(raw)
    if not grants["allow"] and not grants["open"]:
        return True
    return anchor_mod.for_state(state).check(SEAL, anchor_mod.canon(grants), raw.get("seal"))


def _load(state: Path | str) -> dict:
    """The list. A file or a row that can't be read is no program: chat goes on."""
    try:
        raw = jsonfile.read(_file(state), {})
    except OSError:
        raw = {}
    if not isinstance(raw, dict):
        return {"programs": [], "open_yes": []}
    programs_ = raw.get("programs") if isinstance(raw.get("programs"), list) else []
    rows_ = [r for r in programs_ if isinstance(r, dict) and ID_RE.match(str(r.get("id") or ""))
             and r.get("kind") in KINDS]
    said = raw.get("open_yes") if isinstance(raw.get("open_yes"), list) else []
    yes = [str(i) for i in said if isinstance(i, str)]
    if not _seal_ok(state, {"programs": rows_, "open_yes": yes, "seal": raw.get("seal")}):
        logger.warning("programs.json failed its seal: every program asks again")
        for r in rows_:
            r["always_allow"] = False
        yes = []
    return {"programs": rows_, "open_yes": yes}


def _save(state: Path | str, data: dict) -> None:
    grants = _grants(data)
    seal = ""
    if grants["allow"] or grants["open"]:
        try:
            seal = anchor_mod.for_state(state).mac(SEAL, anchor_mod.canon(grants))
        except anchor_mod.AnchorUnavailable:
            raise ProgramError(NO_SEAL) from None
    jsonfile.write(_file(state), {"programs": data["programs"], "open_yes": data["open_yes"], "seal": seal})


def _public(row: dict) -> dict:
    """The row in the pinned shape (and nothing else)."""
    return {"id": str(row.get("id") or ""), "name": str(row.get("name") or ""),
            "kind": row.get("kind") if row.get("kind") in KINDS else "cli",
            "path": str(row.get("path") or ""), "command": str(row.get("command") or ""),
            "added_at": str(row.get("added_at") or ""), "help": str(row.get("help") or ""),
            "commands": [c for c in row.get("commands") if isinstance(c, dict)]
            if isinstance(row.get("commands"), list) else [],
            "always_allow": row.get("always_allow") is True}


def rows(state: Path | str) -> list[dict]:
    """Every program she added, in the pinned shape."""
    return [_public(r) for r in _load(state)["programs"]]


def get(state: Path | str, pid: Any) -> Optional[dict]:
    for r in rows(state):
        if r["id"] == pid:
            return r
    return None


def has_desktop(state: Path | str) -> bool:
    """Is there anything for ``open_in_program`` to open files in? Runs every turn: one small file."""
    return any(r["kind"] == "desktop" for r in rows(state))


def _exists(path: str) -> bool:
    if not isinstance(path, str) or not path or "\0" in path or not os.path.isabs(path):
        return False
    try:
        if _IS_MAC and path.lower().endswith(".app"):
            return os.path.isdir(path)
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


# -- finding a program --------------------------------------------------------------------------------------------
def _path_dirs() -> list[str]:
    """``PATH`` folders, full paths only (never the current folder, never a relative entry: a program planted where
    BuildBot stands is not found)."""
    out = []
    for d in (os.environ.get("PATH") or "").split(os.pathsep):
        d = d.strip().strip('"')
        if d and os.path.isabs(d) and d not in out:
            out.append(d)
    return out


def find_on_path(name: str) -> Optional[str]:
    """The full path of the program *name* on ``PATH``, or None."""
    if not _COMMAND_RE.match(str(name or "")):
        return None
    exts = [""]
    if _IS_WINDOWS:
        _base, ext = os.path.splitext(name)
        exts = [""] if ext.lower() in (".exe", ".com", ".bat", ".cmd") else [".exe", ".com", ".bat", ".cmd"]
    for d in _path_dirs():
        for ext in exts:
            cand = os.path.join(d, name + ext)
            try:
                if os.path.isfile(cand) and (_IS_WINDOWS or os.access(cand, os.X_OK)):
                    return os.path.abspath(cand)
            except (OSError, ValueError):
                continue
    return None


def _ok_kind(path: str, kind: str) -> bool:
    low = path.lower()
    if kind == "desktop":
        if _IS_WINDOWS:
            return low.endswith(_DESKTOP_EXTS_WIN)
        if _IS_MAC:
            return low.endswith(".app") or os.access(path, os.X_OK)
        return low.endswith(".desktop") or os.access(path, os.X_OK)
    if _IS_WINDOWS:
        return low.endswith(_CLI_EXTS_WIN)
    return os.access(path, os.X_OK) or low.endswith((".sh", ".py"))


def argv_prefix(path: str) -> Optional[list[str]]:
    """How a command line program at *path* starts, as a list (no shell): the program itself, or the interpreter
    BuildBot trusts for a script."""
    low = path.lower()
    if low.endswith(".py"):
        return [sys.executable, path]
    if low.endswith(".ps1"):
        if not _IS_WINDOWS:
            return None
        ps = os.path.join(devshell.system_dir(), "WindowsPowerShell", "v1.0", "powershell.exe")
        return [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path] \
            if os.path.isfile(ps) else None
    if low.endswith(".sh"):
        if _IS_WINDOWS:
            bash = commands.git_bash()
            return [bash, path] if bash else None
        return [path] if os.access(path, os.X_OK) else ["/bin/sh", path]
    return [path]


def _stem(path: str) -> str:
    name = os.path.basename(path.rstrip("\\/"))
    stem = os.path.splitext(name)[0] if not name.lower().endswith(".app") else name[:-4]
    return re.sub(r"[^A-Za-z0-9._+-]+", "-", stem).strip("-.")[:64] or "program"


def _batch(path: str) -> bool:
    """A file ``steward.jobs`` never starts as a program (D60): Windows would run it through cmd.exe, a shell."""
    return bool(noshell.refusal(path))


# -- its own help -------------------------------------------------------------------------------------------------
def _help_flags() -> tuple[str, ...]:
    return ("--help", "-h", "/?") if _IS_WINDOWS else ("--help", "-h")


def read_help(state: Path | str, prefix: list[str], flags: Optional[tuple[str, ...]] = None) -> str:
    """The program's own help: ``--help``, then ``-h``, then ``/?`` (Windows), each at most :data:`HELP_SECONDS`, no
    shell, no window, no input, in a job of its own. The first :data:`HELP_LINES` lines of the first one that says
    anything, secrets hidden."""
    folder = commands.folder_for(state)
    env = commands.clean_env(os.environ)
    for flag in (flags or _help_flags()):
        try:
            done = commands.run_program(prefix + [flag], folder, env, seconds=HELP_SECONDS)
        except (OSError, RuntimeError, ValueError):
            continue
        if done["text"]:
            return "\n".join(commands.hide_secrets(done["text"]).splitlines()[:HELP_LINES])
    return ""


def _sub_help_flag(command: str) -> str:
    return SUB_HELP_FLAG.get(str(command or "").lower(), "--help")


def _command_row(path: str, command: str, sub: str, about: str, info: Optional[dict]) -> dict:
    """One subcommand as a ``commands`` entry: what the brain may type (``argv``, the program's name first) and what the
    page shows (``name``, ``what``)."""
    pos = list((info or {}).get("positionals") or [])
    files = [p for p in pos if p.get("file")][:1]
    strings = [p for p in pos if not p.get("file")][:3]
    argv = [command, sub] + ["{" + p["name"] + "}" for p in pos if p in files or p in strings]
    name = re.sub(r"[^a-z0-9]+", "_", f"{command}_{sub}".lower()).strip("_")[:48].rstrip("_")
    row = {"name": name, "command": f"{command} {sub}", "what": about, "argv": argv, "bin": path,
           "strings": [{"name": p["name"], "required": bool(p.get("required"))} for p in strings],
           "has_output_option": bool((info or {}).get("has_output_option"))}
    if files:
        row["files"] = {"file": {"from": "folder", "ext": list((info or {}).get("exts") or [])}}
    return row


def specs_from_help(state: Path | str, path: str, command: str, text: str, prefix: Optional[list[str]] = None) -> list[dict]:
    """What the program can do, from its own help.

    A program with subcommands (git, docker, npm): one entry per subcommand, :data:`COMMANDS_MAX` at most, and for the
    first :data:`SUB_HELP_MAX` of them ``<program> <sub> --help`` is read the same way as the program's own help.
    Subcommands that change things or start something long-running (:data:`NO_SUB_HELP`) are listed but their help is
    never run: a program that ignores ``--help`` must not do the thing itself.

    A program without subcommands: ``derive_specs`` over the one help text already read (no more runs)."""
    try:
        subs = program_help.list_subcommands(text, COMMANDS_MAX)
    except Exception:  # noqa: BLE001 - help we cannot read is no commands
        subs = []
    if subs:
        out: list[dict] = []
        reads, started = 0, time.monotonic()
        flag = _sub_help_flag(command)
        for sub, about in subs:
            info = None
            if (prefix and reads < SUB_HELP_MAX and sub not in NO_SUB_HELP
                    and time.monotonic() - started < SUB_HELP_BUDGET):
                reads += 1
                try:
                    info = program_help.parse_help(read_help(state, prefix + [sub], (flag,)))
                except Exception:  # noqa: BLE001 - one unreadable help is no details
                    info = None
            out.append(_command_row(path, command, sub, about, info))
        return out[:COMMANDS_MAX]
    try:
        return program_help.derive_specs(path, command, lambda argv: text if argv == [path] else "", max_calls=1,
                                         host=True)
    except Exception as exc:  # noqa: BLE001 - help we cannot read is no commands
        logger.debug("programs: help not read (%s)", type(exc).__name__)
        return []


# -- adding, changing, removing -----------------------------------------------------------------------------------
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def register(state: Path | str, kind: str, path: str = "", command: str = "", name: str = "", *,
             read_help_now: bool = True) -> dict:
    """Add a program she chose. Refused only when it is not there."""
    kind = str(kind or "")
    if kind not in KINDS:
        raise ProgramError("Say whether it is a command line program or a desktop program.")
    path = str(path or "").strip().strip('"')
    command = str(command or "").strip()
    if path:
        path = os.path.expanduser(path)
        if not os.path.isabs(path) or not _exists(path):
            raise ProgramError(NOT_FOUND)
        path = os.path.abspath(path)
    elif command:
        found = find_on_path(command)
        if not found:
            raise ProgramError(NOT_ON_PATH.format(name=command[:64]))
        path = found
    else:
        raise ProgramError("Give the program's full path, or its name.")
    if not _ok_kind(path, kind):
        raise ProgramError(NOT_A_PROGRAM)
    word = _stem(path) if kind == "cli" else ""
    label = " ".join(str(name or "").split())[:80] or _stem(path)
    help_text, specs = "", []
    if kind == "cli" and read_help_now and is_shell(word):
        help_text = SHELL_NOTE                  # never run: a banner or a prompt
    elif kind == "cli" and read_help_now and not _batch(path):
        prefix = argv_prefix(path)
        if prefix:
            help_text = read_help(state, prefix)
            specs = specs_from_help(state, path, word, help_text, prefix)
    row = {"id": "p" + secrets.token_hex(4), "name": label, "kind": kind, "path": path,
           "command": word, "added_at": _now(), "help": help_text, "commands": specs,
           "always_allow": False}
    with _lock:
        data = _load(state)
        same = [r for r in data["programs"] if os.path.normcase(str(r.get("path"))) == os.path.normcase(path)
                and r.get("kind") == kind]
        if same:
            # Added again: the same row, its help read again; her choices kept.
            old = same[0]
            row["id"], row["always_allow"] = old["id"], old.get("always_allow") is True
            if not name:
                row["name"] = old.get("name") or label
            data["programs"] = [row if r is old else r for r in data["programs"]]
        else:
            data["programs"].append(row)
        _save(state, data)
    return _public(row)


def remove(state: Path | str, pid: str) -> bool:
    with _lock:
        data = _load(state)
        keep = [r for r in data["programs"] if r.get("id") != pid]
        if len(keep) == len(data["programs"]):
            return False
        data["programs"] = keep
        data["open_yes"] = [i for i in data["open_yes"] if i != pid]
        _save(state, data)
    return True


def set_always_allow(state: Path | str, pid: str, on: bool) -> dict:
    """Her choice, from a paired device only (the route checks). Returns ``{"program": row, "warning"?: line}``."""
    with _lock:
        data = _load(state)
        row = next((r for r in data["programs"] if r.get("id") == pid), None)
        if row is None:
            raise ProgramError(NO_SUCH)
        if on and _batch(str(row.get("path"))):
            raise ProgramError(BATCH_NO_ALWAYS)
        row["always_allow"] = bool(on)
        _save(state, data)
    out: dict = {"program": _public(row)}
    word = str(row.get("command") or "").lower()
    if on and (word in _RUNS_ANYTHING or is_shell(word)):
        out["warning"] = ALWAYS_ALLOW_WARNING
    return out


# -- what the brain sees ------------------------------------------------------------------------------------------
def listing(state: Path | str) -> list[dict]:
    """Her programs in the installed list's shape, with how the brain uses each."""
    out = []
    for r in rows(state):
        pid = f"program:{r['id']}"
        if r["kind"] == "cli":
            how = "run_command " + r["command"] + " ..."
            if r["always_allow"]:
                how += " (Always allow: a plain call runs without asking)"
            names = [str(c.get("command") or "") for c in r["commands"][:4]]
            what = "Your own command line program"
            if any(names):
                what += ": " + ", ".join(n for n in names if n)
            out.append({"id": pid, "name": r["name"], "kind": "program", "status": "ready",
                        "description": f"{what}. {how}", "has_tools": False})
        else:
            out.append({"id": pid, "name": r["name"], "kind": "desktop", "status": "ready",
                        "description": f'Your own desktop program. Can open files in it: open_in_program with program '
                                       f'"{r["name"]}"', "has_tools": False})
    return out


def cli_commands(state: Path | str) -> dict[str, str]:
    """``{command: how the brain runs it}`` for ``run_command``'s description."""
    out: dict[str, str] = {}
    for r in rows(state):
        if r["kind"] != "cli" or not r["command"] or not _exists(r["path"]):
            continue
        shown = [" ".join(a for a in c["argv"] if isinstance(a, str)) for c in r["commands"][:3]
                 if isinstance(c.get("argv"), list)]
        word = f'"{r["path"]}"' if _batch(r["path"]) else r["command"]     # a shell finds a batch file by its path
        line = f"{word} ... (your own program {r['name']}"
        if shown:
            line += "; for example " + ", ".join(s.strip() for s in shown)
        out[r["command"]] = line + ")"
    return out


# -- the brain runs her command line programs (run_command) -------------------------------------------------------
def _words(text: str) -> list[str]:
    """One command line as its words. On Windows a backslash is part of a path, not an escape (v1 lost them)."""
    return shlex.split(text.replace("\\", "\\\\") if _IS_WINDOWS else text, posix=True)


def match(state: Path | str, command: str) -> Optional[tuple[dict, list[str]]]:
    """``(row, arguments)`` when *command* is ONE plain call of a command line program she added (by its name or its
    full path), else None. Plain: no shell features at all, so it can run as a list of arguments. A batch file is never
    plain: Windows would run it through cmd.exe."""
    text = str(command or "").strip()
    if not text or any(c in _SHELL_CHARS for c in text):
        return None
    try:
        words = _words(text)
    except ValueError:
        return None
    if not words:
        return None
    head = words[0]
    for r in rows(state):
        if r["kind"] != "cli" or not _exists(r["path"]):
            continue
        same = (head.lower() == r["command"].lower() if _IS_WINDOWS else head == r["command"]) \
            or os.path.normcase(head) == os.path.normcase(r["path"])
        if not same:
            continue
        return None if _batch(r["path"]) else (r, words[1:])
    return None


def argv_for(row: dict, args: list[str]) -> list[str]:
    """What runs: the program by its full path and *args*, a list. ProgramError when it can't start here."""
    prefix = argv_prefix(row["path"]) if _exists(row["path"]) else None
    if not prefix:
        raise ProgramError(f"{row['name']} cannot start on this computer.")
    return prefix + [str(a) for a in args]


def _log(ctx: dict, record: dict) -> None:
    """One audit line, or nothing runs: "every run is logged" (D16). Names and counts, never arguments (X14)."""
    audit = (ctx or {}).get("audit")
    if not callable(audit):
        raise ProgramError(NO_AUDIT)
    try:
        audit({"actor": "concierge", **record})
    except Exception:  # noqa: BLE001 - not logged: not run
        raise ProgramError(NO_AUDIT) from None


def run_cli(row: dict, args: list[str], *, folder: str, asked: bool, ctx: Optional[dict] = None) -> dict:
    """Run her program by its full path with *args*, a list, never a shell, in a job of its own. One audit line per run
    (which program, asked or Always allow)."""
    argv = argv_for(row, args)
    _log(ctx or {}, {"action": "own_program_run", "program": row["id"], "command": devshell.first_word(row["command"]),
                     "how": "asked" if asked else "always_allow", "arguments": len(args)})
    try:
        done = commands.run_program(argv, folder, commands.clean_env(os.environ), cancelled=(ctx or {}).get("cancelled"))
    except OSError as exc:
        return {"exit_code": None, "worked": False, "folder": folder, "output": "",
                "note": f"{row['name']} could not start ({type(exc).__name__})."}
    return commands.outcome(done, folder)


# -- files she may open -------------------------------------------------------------------------------------------
def _folders(state: Path | str) -> tuple[str, list[str], list[str]]:
    """Her data folder, linked folders and pinned files, as she has them."""
    data = jsonfile.read(Path(state) / "settings" / "folders.json", {})
    data = data if isinstance(data, dict) else {}
    home = str(data.get("data") or (Path(state) / "data"))
    linked = [p for p in data.get("linked") or [] if isinstance(p, str) and p.strip()]
    return home, linked, [str(p) for p in zone1_folders.pinned_paths(state)]


def file_refusal(state: Path | str, path: Any, *, default_app: bool = True) -> str:
    """"" when *path* may be opened, else the plain reason."""
    text = str(path or "").strip().strip('"')
    if not text or "\0" in text or not os.path.isabs(text) or zone1_folders._pin_odd_path(text):  # noqa: SLF001
        return FILE_OUTSIDE
    real = zone1_folders.canon(text)
    if zone1_folders._pin_odd_path(real):  # noqa: SLF001 - and where it really leads
        return FILE_OUTSIDE
    home, linked, pinned = _folders(state)
    home, linked, pinned = (zone1_folders.canon(home), [zone1_folders.canon(f) for f in linked],
                            [zone1_folders.canon(f) for f in pinned])
    try:
        vaults = [zone1_folders.canon(v) for v in zone1_folders._vault_places(Path(state))]  # noqa: SLF001
    except (OSError, ValueError):
        return FILE_OUTSIDE                  # unsure where the vault is: nothing opens
    if any(zone1_folders._inside(real, v) for v in vaults):  # noqa: SLF001
        return FILE_OUTSIDE
    inside = real in pinned or any(zone1_folders._inside(real, f) and real != f.rstrip("\\/")  # noqa: SLF001
                                   for f in [home, *linked])
    own = zone1_folders._inside(real, zone1_folders.canon(state))  # noqa: SLF001
    if not inside or (own and not zone1_folders._inside(real, home)):  # noqa: SLF001
        return FILE_OUTSIDE
    if not os.path.isfile(real):
        return FILE_MISSING
    if default_app and os.path.splitext(real)[1].lower() in RUNNABLE_EXTS:
        return FILE_RUNS
    return ""


def resolve_file(state: Path | str, file: Any) -> str:
    """A full path for *file*: as given when full, else inside her data folder or a linked folder, else the pinned file
    of that name."""
    text = str(file or "").strip().strip('"')
    if not text or os.path.isabs(text):
        return text
    home, linked, pinned = _folders(state)
    for base in (home, *linked):
        cand = os.path.join(base, text)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    for p in pinned:
        if os.path.basename(p).lower() == text.lower():
            return p
    return os.path.join(home, text)


# -- starting a desktop program -----------------------------------------------------------------------------------
_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm%]")


def desktop_exec(desktop_file: str) -> Optional[list[str]]:
    """The ``Exec`` of a Linux ``.desktop`` file as a list, its program by full
    path (found on ``PATH`` once), the file codes (``%f`` ...) left in."""
    entry = _read_desktop(desktop_file)
    if not entry or not entry.get("exec"):
        return None
    try:
        words = shlex.split(entry["exec"], posix=True)
    except ValueError:
        return None
    if not words:
        return None
    head = words[0]
    full = head if os.path.isabs(head) else find_on_path(head)
    if not full or not os.path.isfile(full):
        return None
    return [full] + words[1:]


def _fill(words: list[str], file: str) -> list[str]:
    out, placed = [], False
    for w in words:
        if w in ("%f", "%F", "%u", "%U"):
            if file and not placed:
                out.append(file)
                placed = True
            continue
        w = _FIELD_CODES.sub(lambda m: "%" if m.group(0) == "%%" else "", w)
        if w:
            out.append(w)
    if file and not placed:
        out.append(file)
    return out


def _start_window(target: str, file: str = "") -> None:
    """Start *target* (a program, shortcut, app or ``.desktop`` file; empty:
    the file's default app) with its window SHOWING: that window is the
    point. Never a shell."""
    if _IS_WINDOWS:
        if target:
            if file:
                os.startfile(target, "open", subprocess.list2cmdline([file]))  # type: ignore[attr-defined]  # noqa: S606
            else:
                os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
        else:
            os.startfile(file)  # type: ignore[attr-defined]  # noqa: S606
        return
    if _IS_MAC:
        if target and target.lower().endswith(".app"):
            argv = ["/usr/bin/open", "-a", target] + ([file] if file else [])
        elif target:
            argv = [target] + ([file] if file else [])
        else:
            argv = ["/usr/bin/open", file]
    elif target and target.lower().endswith(".desktop"):
        words = desktop_exec(target)
        if not words:
            raise ProgramError("That program's .desktop file does not say how to start it.")
        argv = _fill(words, file)
    elif target:
        argv = [target] + ([file] if file else [])
    else:
        argv = ["/usr/bin/xdg-open", file]
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                     cwd=os.path.expanduser("~"), **procflags.hidden())


def launch(state: Path | str, pid: str, file: str = "") -> dict:
    """Start her desktop program *pid*, optionally with *file*."""
    row = get(state, pid)
    if not row or row["kind"] != "desktop":
        raise ProgramError(NO_SUCH)
    if not _exists(row["path"]):
        raise ProgramError(f"{row['name']} is not at {row['path']} any more.")
    if file:
        file = resolve_file(state, file)
        why = file_refusal(state, file, default_app=False)
        if why:
            raise ProgramError(why)
    _start_window(row["path"], file)
    return {"ok": True}


def open_file(state: Path | str, path: str) -> dict:
    """Open *path* in its default app (Word for a .docx)."""
    path = resolve_file(state, path)
    why = file_refusal(state, path, default_app=True)
    if why:
        raise ProgramError(why)
    _start_window("", path)
    return {"ok": True}


# -- the brain's open_in_program ----------------------------------------------------------------------------------
OPEN_TOOL = "open_in_program"
OPEN_KIND = "open_program"
OPEN_WORDS = ("Open a file from the owner's folders (her data folder, her linked folders, a file she pinned) on this "
              "computer's screen, in one of her desktop programs (like Word or Excel) or in the file's own app. The "
              "owner says yes the first time per program.")
OPEN_SCHEMA = {"type": "object", "properties": {
    "program": {"type": "string", "description": "The name of one of the owner's desktop "
                "programs (from the installed list), or empty for the file's own app."},
    "file": {"type": "string", "description": "The file: a full path, or its name."}},
    "required": ["file"], "additionalProperties": False}


def _desktop_named(state: Path | str, name: Any) -> Optional[dict]:
    want = " ".join(str(name or "").split()).lower()
    if not want:
        return None
    for r in rows(state):
        if r["kind"] == "desktop" and want in (r["name"].lower(), r["id"], _stem(r["path"]).lower()):
            return r
    return None


def _yes_key(state: Path | str, args: dict) -> str:
    row = _desktop_named(state, args.get("program"))
    return row["id"] if row else "default"


def open_question(state: Path | str, args: dict, ctx: dict) -> Optional[dict]:
    """Asks the first time per program; None once she said Yes for it. Raises for a refusal: she is not asked."""
    commands.check_not_safe_mode(ctx)
    keep = (ctx or {}).get("keep")
    if not callable(keep):
        raise ProgramError(commands.ONLY_CHAT)
    if args.get("program") and not _desktop_named(state, args.get("program")):
        raise ProgramError(NO_SUCH)
    file = resolve_file(state, args.get("file"))
    row = _desktop_named(state, args.get("program"))
    why = file_refusal(state, file, default_app=row is None)
    if why:
        raise ProgramError(why)
    if keep(file) != file:
        raise ProgramError(commands.HOLDS_PRIVATE)      # X6: a card is a plain store, and this one shows the path
    if _yes_key(state, args) in _load(state)["open_yes"] and ctx.get("her_device") is True:
        return None
    where = row["name"] if row else "its own app"
    q = {"kind": OPEN_KIND, "tool": OPEN_TOOL, "title": f"May I open files in {where}?", "program": where, "file": file,
         "detail": f"Open {os.path.basename(file)} in {where}. After this yes I open files from your folders in {where} "
                   "without asking again.", "end_turn": True}
    if callable(ctx.get("notify")):
        q["notify"] = ctx["notify"]
    return q


def open_card(q: dict) -> dict:
    """The stored card for one ``open_in_program`` question: only what her Yes covers."""
    return {"title": str(q.get("title") or ""), "what": commands.visible(str(q.get("detail") or "")), "tool": OPEN_TOOL,
            "program": commands.visible(str(q.get("program") or "")), "open_file": commands.visible(str(q.get("file") or ""))}


def open_tool(state: Path | str, args: dict, ctx: dict) -> dict:
    commands.check_not_safe_mode(ctx)
    key = _yes_key(state, args)
    with _lock:
        data = _load(state)
        said_yes = (ctx or {}).get("said_yes") is True
        if not said_yes and not (key in data["open_yes"] and (ctx or {}).get("her_device") is True):
            raise ProgramError("this was not approved")
        if key not in data["open_yes"]:
            data["open_yes"].append(key)
            _save(state, data)
    row = _desktop_named(state, args.get("program"))
    _log(ctx, {"action": "own_program_opened", "program": row["id"] if row else "default"})
    if row:
        launch(state, row["id"], str(args.get("file") or ""))
        return {"opened": True, "in": row["name"]}
    open_file(state, str(args.get("file") or ""))
    return {"opened": True, "in": "its own app"}


# -- installed desktop programs on this computer ------------------------------------------------------------------
def _lnk_target(path: str) -> str:
    """The target of a Windows shortcut (.lnk, MS-SHLLINK): its LinkInfo's
    local path, or "" (an advertised shortcut, a folder, a damaged file)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(65536)
    except OSError:
        return ""
    try:
        if len(data) < 0x4C or struct.unpack_from("<I", data, 0)[0] != 0x4C:
            return ""
        flags = struct.unpack_from("<I", data, 0x14)[0]
        at = 0x4C
        if flags & 0x1:                                  # HasLinkTargetIDList
            at += 2 + struct.unpack_from("<H", data, at)[0]
        if not flags & 0x2:                              # no LinkInfo
            return ""
        size, head = struct.unpack_from("<II", data, at)
        info = data[at:at + size]
        info_flags = struct.unpack_from("<I", info, 8)[0]
        if not info_flags & 0x1:                         # VolumeIDAndLocalBasePath
            return ""
        base_off = struct.unpack_from("<I", info, 16)[0]
        suffix_off = struct.unpack_from("<I", info, 24)[0]
        if head >= 0x24:
            ubase = struct.unpack_from("<I", info, 28)[0]
            usuf = struct.unpack_from("<I", info, 32)[0]
            base = _utf16z(info, ubase)
            suffix = _utf16z(info, usuf) if usuf else ""
        else:
            base = _ansiz(info, base_off)
            suffix = _ansiz(info, suffix_off) if suffix_off else ""
        return (base + suffix).strip()
    except (struct.error, IndexError, UnicodeDecodeError):
        return ""


def _ansiz(buf: bytes, off: int) -> str:
    end = buf.index(b"\0", off)
    return buf[off:end].decode("mbcs" if _IS_WINDOWS else "latin-1", "replace")


def _utf16z(buf: bytes, off: int) -> str:
    end = off
    while end + 1 < len(buf) and buf[end:end + 2] != b"\0\0":
        end += 2
    return buf[off:end].decode("utf-16-le", "replace")


def _start_menu_dirs() -> list[str]:
    out = []
    for var in ("ProgramData", "APPDATA"):
        base = os.environ.get(var)
        if base and os.path.isabs(base):
            out.append(os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs"))
    return out


_SKIP_NAME = re.compile(r"(?i)\b(uninstall|uninst|remove|readme|help|license|website|"
                        r"release notes|documentation)\b")


def _start_menu() -> list[dict]:
    out = []
    for root in _start_menu_dirs():
        for folder, _dirs, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(".lnk"):
                    continue
                name = f[:-4]
                if _SKIP_NAME.search(name):
                    continue
                target = _lnk_target(os.path.join(folder, f))
                if target and not target.lower().endswith(".exe"):
                    continue            # a document, a web page, a folder
                path = target if target and os.path.isfile(target) else \
                    os.path.join(folder, f)
                out.append({"name": name, "path": path})
    return out


def _app_paths(reg: Any = None) -> list[dict]:
    """Programs in ``App Paths`` (HKLM then HKCU), where installers such as
    Office (WINWORD.EXE, EXCEL.EXE) register themselves."""
    if reg is None:
        try:
            import winreg as reg  # type: ignore[no-redef]
        except ImportError:
            return []
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    out = []
    for hive in (reg.HKEY_LOCAL_MACHINE, reg.HKEY_CURRENT_USER):
        try:
            key = reg.OpenKey(hive, sub)
        except OSError:
            continue
        i = 0
        while i < 4000:
            try:
                exe = reg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            if not exe.lower().endswith(".exe"):
                continue
            try:
                with reg.OpenKey(key, exe) as k:
                    value, kind = reg.QueryValueEx(k, "")
            except OSError:
                continue
            if not isinstance(value, str):
                continue
            if kind == getattr(reg, "REG_EXPAND_SZ", -1):
                value = reg.ExpandEnvironmentStrings(value)
            value = value.strip().strip('"').strip()
            if os.path.isabs(value) and value.lower().endswith(".exe") and os.path.isfile(value):
                out.append({"name": exe[:-4], "path": value})
        try:
            reg.CloseKey(key)
        except Exception:  # noqa: BLE001
            pass
    return out


def _read_desktop(path: str) -> dict:
    """``{"name", "exec", "hidden"}`` from a Linux ``.desktop`` file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(65536)
    except OSError:
        return {}
    out: dict = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
            continue
        if section != "[Desktop Entry]" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key == "Name":
            out["name"] = value.strip()
        elif key == "Exec":
            out["exec"] = value.strip()
        elif key == "Type":
            out["type"] = value.strip()
        elif key in ("NoDisplay", "Hidden") and value.strip().lower() == "true":
            out["hidden"] = True
    return out


def _linux_dirs() -> list[str]:
    home = os.path.expanduser("~")
    return ["/usr/share/applications", "/usr/local/share/applications",
            os.path.join(home, ".local", "share", "applications"),
            "/var/lib/flatpak/exports/share/applications",
            "/var/lib/snapd/desktop/applications"]


def _linux() -> list[dict]:
    out = []
    for d in _linux_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for f in names:
            if not f.endswith(".desktop"):
                continue
            p = os.path.join(d, f)
            e = _read_desktop(p)
            if e.get("name") and e.get("exec") and not e.get("hidden") \
                    and e.get("type", "Application") == "Application":
                out.append({"name": e["name"], "path": p})
    return out


def _mac_dirs() -> list[str]:
    return ["/Applications", os.path.join(os.path.expanduser("~"), "Applications")]


def _mac() -> list[dict]:
    out = []
    for d in _mac_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        out += [{"name": n[:-4], "path": os.path.join(d, n)} for n in names
                if n.endswith(".app")]
    return out


def installed_desktop(*, finders: Optional[list[Callable[[], list[dict]]]] = None) -> list[dict]:
    """Desktop programs installed on this computer, to pick from: ``[{"name",
    "path"}]``, one per program, sorted by name, at most :data:`DESKTOP_MAX`.
    Read with her own rights only (no administrator)."""
    if finders is None:
        if _IS_WINDOWS:
            finders = [_start_menu, _app_paths]
        elif _IS_MAC:
            finders = [_mac]
        else:
            finders = [_linux]
    got: list[dict] = []
    for find in finders:
        try:
            got += list(find() or [])
        except Exception as exc:  # noqa: BLE001 - one place never hides the others
            logger.debug("programs: %s (%s)", getattr(find, "__name__", "?"),
                         type(exc).__name__)
    out, seen = [], set()
    for g in got:
        key = os.path.normcase(str(g.get("path") or ""))
        if not key or key in seen or not g.get("name"):
            continue
        seen.add(key)
        out.append({"name": str(g["name"])[:120], "path": str(g["path"])})
    out.sort(key=lambda r: r["name"].lower())
    return out[:DESKTOP_MAX]


__all__ = ["ALWAYS_ALLOW_WARNING", "ID_RE", "KINDS", "NO_SUCH", "OPEN_KIND", "OPEN_SCHEMA", "OPEN_TOOL", "OPEN_WORDS",
           "ProgramError", "argv_for", "cli_commands", "file_refusal", "find_on_path", "get", "has_desktop",
           "installed_desktop", "launch", "listing", "match", "open_card", "open_file", "open_question", "open_tool",
           "read_help", "register", "remove", "rows", "run_cli", "set_always_allow"]
