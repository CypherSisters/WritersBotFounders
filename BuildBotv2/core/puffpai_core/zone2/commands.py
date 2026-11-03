# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/brain_commands.py (adapted): the limits, the refusals, the shell
# choice, the environment, the output tail and the notes. Changed for v2: a job that holds the whole tree
# (steward.jobs) in place of subprocess.run, v2's own protected places and secret finder, no skills, no remote-API
# check, no "Install what's missing" card, no puffpai shim.
"""``run_command``: chat runs one command on this computer, after her Yes for that one (D138). PROPOSED, not tested
by the owner.

Like Hermes Agent's default approval mode (``tools/approval.py``, MIT, by way of v1), every command is shown to the
owner exactly as it will run, with its folder, and runs only after she says Yes. There is no allow list and no "yes
to all". The record of a Yes is the consent store itself; the command is written nowhere else.

The command runs as her, outside the Bridge, the same as the Developer page's command line (D138, her choice with
awareness), so the Bridge does not check what it reaches. It runs in ``<state>/workspace``, with this computer's
environment minus every ``PUFFPAI*`` name, anything holding a Core token and anything whose name says key, token,
secret, password or PIN. ``HOME`` is the work folder. ``python3`` and ``python`` start the bot's own Python.

A command that names the vault, a key file or the Core's own records is refused before she is asked: by name, by a
wildcard that fits one, or by a path that leads into one or to a folder that holds one. Honest limit: this catches
what a command says, not what it does. A script or a built-up path can still reach them, because it runs as her.

X6: a Yes card lives in a plain store and must show the exact command. So a command that holds a private detail or a
key, or a ``{{vault:...}}`` handle in place of one, is refused before she is asked.

How it runs: Git for Windows' bash where it is installed (never ``System32\\bash.exe``, which starts Linux under WSL,
and never found through PATH), else ``cmd.exe`` from Windows' own System32 folder; elsewhere bash, else ``/bin/sh``.
It runs inside a job of its own (``steward.jobs``), so the time limit, a closed chat, or the command ending ends
everything it started. A program it left running in the background ends with it. Stdin is empty. What it prints is
kept only in memory, with known secret shapes hidden in the whole text before the tail is cut.

:func:`run_program` is the run path on its own: any program, its arguments, its folder and its environment.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Optional

from .. import devshell
from ..steward import jobs
from ..zone1 import folders as zone1_folders
from ..zone1 import secret_detector
from ..zone3.toolsmith_zone import _fold, _plain_device, _through_a_process
from .gate import _HANDLE

IS_WINDOWS = os.name == "nt"
TOOL = "run_command"
#: The question's kind. ``wiring.core_question`` raises its card.
KIND = "command"
TITLE = "May I run this command?"
COMMAND_SECONDS = 120
MAX_COMMAND = 2000
MAX_OUTPUT = 4000
KEEP_BYTES = 256_000             # kept per stream while it runs (memory only); the tail comes from this
READER_WAIT_S = 2.0
POLL_S = 0.2
WORKSPACE = "workspace"
SHIMS = "command_bin"
HIDDEN = "[a secret, hidden]"

_SECRET_NAME = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PIN|CREDENTIAL|AUTH|(?:^|_)PAT$)")
#: File names a command may never name, wherever it says they are.
_SECRET_FILES = ("anchor.key", "machine.key", "terminal.json", "tokens.db", "consent.db", "audit.jsonl", "outside.jsonl",
                 "vault.log", "vault.json", "keyring.json", "tombstones.json", "vault.inbox", "pairing.json",
                 # her own programs and their Always allow switch: changed only from a paired device, never by a command
                 "programs.json")
#: The Core's own places in the bot's folder (the vault's own places come from ``zone1.folders``).
_CORE_PLACES = ("settings", "logs", "tokens.db", "consent.db", "pairing.json", "programs.json")
_POSIX_BASH = ("/bin/bash", "/usr/bin/bash")
#: First words that start Linux (WSL) on Windows when Git's bash is missing.
_WSL_WORDS = ("bash", "bash.exe", "sh", "wsl", "wsl.exe")

NO_SECRETS_LINE = "commands never touch the vault or key files"
ONLY_CHAT = "a command runs only from the owner's own chat"
SAFE_MODE = "BuildBot is in safe mode, so commands are closed until you restart BuildBot"
NOT_APPROVED = "this command was not approved"
CHAT_CLOSED = "the chat closed before it started, so it did not run"
HOLDS_PRIVATE = ("that command holds a private detail or a key, and a Yes card must show a command exactly, so it "
                 "was not run. Tell the owner she can run it herself in the Developer page's command line")
NOT_A_COMMAND_JSON = ("that is a tool call written as JSON, not a command. Nothing was run. Use the tool it belongs to "
                      "if one is listed; otherwise tell the owner it needs to be connected first")
CMD_ONE_LINE = ("the Windows command prompt runs only the first line of a command, so this was not run. Join the lines "
                "with && in one line")
NO_GIT_BASH = ("this command needs bash, and this computer has no Git Bash. Here bash would start Linux (WSL), which "
               "BuildBot does not use. Installing Git for Windows adds Git Bash")
NOT_CONTAINED = "Windows would not hold the command in a job of its own, so it was not run"
#: One short line on the card: where it runs and what her Yes means (D138).
CARD_WHAT = "It runs once in {shell}, as you, outside the Bridge, the same as the Developer page's command line."
CARD_OWN = ("It runs your own program {name} once, directly, not through the command line, as you, outside the Bridge, "
            "the same as the Developer page's command line.")
#: Words for a command that did not work, so the model never says it was done.
FAILED_NOTE = ("The command did not work (exit code {code}). Nothing it was meant to do is done. Tell the owner "
               "plainly, with the reason from the output.")
#: v1 said an "Install what's missing" card was shown; v2 has no such card, so this says only the part that is true.
MISSING_NOTE = ("The command did not work (exit code {code}). A program or package it needs is missing. Tell the owner "
                "plainly what is missing; do not try to install it with another command.")
TIMED_OUT_NOTE = "It was stopped after {seconds} seconds, so it did not finish."
CLOSED_NOTE = "The chat closed, so it was stopped and did not finish."

_MISSING = re.compile(r"(?m)(?:^|:\s)[A-Za-z0-9][A-Za-z0-9._+-]{0,63}: (?:command )?not found\s*$"
                      r"|'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}' is not recognized as an internal or external command"
                      r"|ModuleNotFoundError: No module named '"
                      r"|Cannot find (?:module|package) '[^'./]")
_GLOB_CHARS = re.compile(r"[*?\[]")
#: Where one word of a command ends (shell operators, assignments, quotes).
_WORD_SPLIT = re.compile(r"[\s;&|<>()`=,{}]+|\$\(")

SCHEMA = {
    "type": "object",
    "properties": {"command": {"type": "string", "description": "The exact command line to run."}},
    "required": ["command"],
    "additionalProperties": False,
}


def description(state: Path | str | None = None) -> str:
    """What the model reads, with her own programs by name. v1 said "Commands run in bash"; this names the shell this
    computer really uses."""
    from . import programs
    mine = programs.cli_commands(state) if state is not None else {}
    listed = (" Her own programs (they run best as one plain call, no && or |): "
              + "; ".join(list(mine.values())[:programs.CLI_LISTED]) + ".") if mine else ""
    return ("Run one command on this computer, for example a script or a quick check the owner asks for. The owner "
            "sees the exact command and must say yes first, every time. It runs in a work folder; write new files to "
            f"~/ to keep them there. Commands run in {shell_name()} and python3 works. When one job needs several "
            "commands, join them with && in one command. The output and whether it worked come back to you. Never "
            "to add an app or a tool: that is ask_toolsmith." + listed)


# -- where it runs and what it may not name --------------------------------------------------------------------
def workspace(state: Path | str) -> str:
    return os.path.abspath(os.path.join(str(state), WORKSPACE))


def _protected(state: Path | str) -> list[str]:
    """The vault's places and the Core's own, as full paths. A vault setting that can't be read refuses everything."""
    try:
        places = [str(p) for p in zone1_folders._vault_places(Path(state))]   # noqa: SLF001 - the one list of them
    except Exception:  # noqa: BLE001 - unsure where the vault is: nothing runs
        raise ValueError(NO_SECRETS_LINE) from None
    places += [os.path.join(str(state), name) for name in _CORE_PLACES]
    # Where each really is too (a Mac's /tmp is /private/tmp), but never looked up on a server (X28).
    real = [os.path.realpath(p) for p in places if not p.startswith(("//", "\\\\"))]
    return list(dict.fromkeys(_fold(os.path.abspath(p)) for p in [*places, *real]))


def _reaches(path: str, protected: list[str]) -> bool:
    """Is *path* a protected one, inside one, or a folder that holds one?"""
    path = _fold(os.path.abspath(path))
    if not IS_WINDOWS:
        path = "/" + path.lstrip("/")          # the kernel reads //home as /home: there it names no server
    for p in protected:
        if path == p or p.startswith(path.rstrip("\\/") + os.sep) or path.startswith(p.rstrip("\\/") + os.sep):
            return True
    return False


def _as_path(word: str, folder: str) -> Optional[str]:
    """*word* as a full path when it looks like one, else None."""
    if not word or "://" in word or word.startswith("-") or word == "/":
        return None          # a bare / is far more often a division than a path
    if IS_WINDOWS and re.fullmatch(r"/[A-Za-z]", word):
        return None          # cmd's /c, dir's /s: a switch far more often than Git Bash's name for a whole drive
    if not (word in (".", "..", "~") or "/" in word or word.startswith("~")):
        return None
    if word.startswith("~"):
        word = folder + word[1:]
    elif IS_WINDOWS and re.match(r"^/[A-Za-z]/", word):
        word = word[1] + ":/" + word[3:]          # Git Bash's /c/Users/...
    return os.path.join(folder, word)


def _climbs(word: str) -> str:
    """*word* with each folder part a shell may read as ``..`` written so: bash before 5.2 lets ``.*`` and ``.?`` find
    ``..`` (Ubuntu 22.04's and a Jetson's do). Only a part with a slash after it: a bare ``.*`` is far more often a pattern
    for grep or find. The word itself when it has no such part."""
    parts = word.split("/")
    return "/".join(".." if n < len(parts) - 1 and part.startswith(".") and _GLOB_CHARS.search(part)
                    and fnmatch.fnmatch("..", part) else part for n, part in enumerate(parts))


def _proc_by_wildcard(path: str) -> bool:
    """A wildcard that can spell ``/proc/self`` or ``/proc/<number>``, each a way into any folder. What it leads to is
    only there for the program that runs it, so it is judged by its words. ``/proc/c*`` can't: it is no such place."""
    parts = [p for p in os.path.normpath(path).split("/") if p]
    return len(parts) > 1 and fnmatch.fnmatch("proc", parts[0]) and (parts[1].isdigit() or any(
        fnmatch.fnmatch(name, parts[1]) for name in ("self", "thread-self", "1", "12", "123", "1234", "12345", "123456")))


def _unreadable_place(path: str) -> bool:
    """Linux and Mac: a path through a running program (``/proc/self/cwd/..``) leads where only that program knows, here
    the folder commands run in, so no text check can read it. The devices every program uses are no such thing."""
    return not IS_WINDOWS and not _plain_device(path) and (_through_a_process(path) or _proc_by_wildcard(path))


def check_command(command: Any, state: Path | str) -> str:
    """The command, or ValueError when it may not run at all."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("there is no command to run")
    command = command.strip()
    if len(command) > MAX_COMMAND or "\x00" in command:
        raise ValueError("that command is too long")
    protected = _protected(state)
    low = command.lower().replace("\\", "/")
    if any(name in low for name in _SECRET_FILES) or any(p.lower().replace("\\", "/") in low for p in protected):
        raise ValueError(NO_SECRETS_LINE)
    folder = workspace(state)
    for written in _WORD_SPLIT.split(command.replace("\\", "/")):
        word = written.strip("\"'")
        wild = bool(_GLOB_CHARS.search(word))
        if wild:
            base = word.rsplit("/", 1)[-1].lower()
            literal = _GLOB_CHARS.sub("", base)
            if len(literal) >= 3 and any(fnmatch.fnmatch(n, base) for n in _SECRET_FILES):
                raise ValueError(NO_SECRETS_LINE)      # tokens.d? and the like
        # A shell never spreads a quoted word: there ".*/" is a pattern for the program it goes to.
        climbs = _climbs(word) if wild and written[:1] not in ("'", '"') else word
        for said in dict.fromkeys((word, climbs)):
            path = _as_path(said, folder)
            if path is None:
                # A bare name: only what is really in the folder, which may be a link out of it.
                if not said or "/" in said or said.startswith("-") or os.path.splitdrive(said)[0]:
                    continue
                path = os.path.join(folder, said)
                if not wild and not os.path.lexists(path):
                    continue
            # On Windows two slashes name a server, and looking it up would reach out before her Yes (X28).
            server = IS_WINDOWS and path.startswith(("//", "\\\\"))
            # Also tidied: the folder commands run in may not be made yet, and the kernel climbs out of nothing that is
            # not there, but by the time the command runs it is.
            hits = glob.glob(path) + glob.glob(os.path.normpath(path)) if wild and not server else []
            for one in [path, *hits]:
                if _unreadable_place(one) or _reaches(one, protected) or (
                        not server and _reaches(os.path.realpath(one), protected)):
                    raise ValueError(NO_SECRETS_LINE)
    text = command
    if text[:1] in ("{", "["):
        try:
            json.loads(text)
        except ValueError:
            pass
        else:
            raise ValueError(NOT_A_COMMAND_JSON)
    return command


def folder_for(state: Path | str) -> str:
    """The folder a command runs in (made on first use). Raises ValueError with plain words."""
    folder = workspace(state)
    if _reaches(folder, _protected(state)):
        raise ValueError("commands never run in the vault folder")
    os.makedirs(folder, exist_ok=True)
    return folder


# -- the shell -------------------------------------------------------------------------------------------------
def _is_wsl_bash(path: str) -> bool:
    low = os.path.normcase(os.path.abspath(path))
    return any(part in low for part in ("system32", "sysnative", "windowsapps"))


def git_bash() -> Optional[str]:
    """Git for Windows' ``bash.exe`` from where its installer puts it, or None (always None off Windows). Never looked
    up by name on the path. ``PUFFPAI_BASH`` names another full path."""
    if not IS_WINDOWS:
        return None
    wanted = os.environ.get("PUFFPAI_BASH", "").strip()
    if wanted:
        return wanted if os.path.isfile(wanted) and not _is_wsl_bash(wanted) else None
    found: list[str] = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base and os.path.isabs(base):
            found.append(os.path.join(base, "Git", "bin", "bash.exe"))
    for path in found:
        if os.path.isfile(path) and not _is_wsl_bash(path):
            return os.path.abspath(path)
    return None


def _posix_bash() -> Optional[str]:
    for path in _POSIX_BASH:
        if os.path.isfile(path):
            return path
    return None


def shell_name() -> str:
    """The shell commands run in, in plain words."""
    if not IS_WINDOWS:
        return "bash" if _posix_bash() else "the system shell"
    return "Git Bash" if git_bash() else "the Windows command prompt"


def _for_git_bash(text: str) -> str:
    """*text* as Git Bash's own start-up reads a double-quoted word: every backslash doubled, every quote after one."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _shell_call(command: str, bash: Optional[str]) -> tuple[list[str], Optional[str]]:
    """``(argv, exact Windows command line or None)`` for this command in this computer's shell."""
    if bash:
        # Git Bash reads ' and wildcards on its own command line, so anything not in double quotes changes before bash
        # sees it: echo';'whoami would run whoami. Always in double quotes, bash gets exactly what the card shows.
        return [bash, "-c", command], f'"{bash}" -c {_for_git_bash(command)}' if IS_WINDOWS else None
    if not IS_WINDOWS:
        return [_posix_bash() or "/bin/sh", "-c", command], None
    # cmd.exe does not read arguments the way Popen quotes them (a " would come out as \"), so what ran would not be
    # what she read. /S takes off exactly the outer quotes; /D skips AutoRun, like the Developer shell.
    exe = os.path.join(devshell.system_dir(), "cmd.exe")
    return [exe, "/D", "/S", "/C", command], f'"{exe}" /D /S /C "{command}"'


def _first_word(command: str) -> str:
    word = command.strip().split(None, 1)[0] if command.strip() else ""
    return os.path.basename(word.strip("\"'").replace("\\", "/")).lower()


def _check_shell(command: str) -> None:
    """On Windows without Git's bash, ``bash`` would start Linux (WSL)."""
    if IS_WINDOWS and not git_bash():
        if _first_word(command) in _WSL_WORDS:
            raise ValueError(NO_GIT_BASH)
        if "\n" in command or "\r" in command:
            raise ValueError(CMD_ONE_LINE)


# -- the environment -------------------------------------------------------------------------------------------
def clean_env(base: Mapping[str, str]) -> dict[str, str]:
    """*base* without ``PUFFPAI*`` names or Core tokens (``devshell.shell_env``), a name that says secret, or a value
    shaped like a key."""
    return {k: v for k, v in devshell.shell_env(base).items()
            if not _SECRET_NAME.search(k) and not secret_detector.find(v)}


def _git_bin_dirs(bash: str) -> list[str]:
    """Git's tool folders, mingw first, so ``cat`` and friends are Git's own and never the System32 look-alikes."""
    parent = os.path.dirname(os.path.dirname(bash))
    root = os.path.dirname(parent) if os.path.basename(parent).lower() == "usr" else parent
    dirs = (os.path.join(root, *sub.split("/")) for sub in ("mingw64/bin", "usr/local/bin", "usr/bin", "bin"))
    return list(dict.fromkeys(d for d in dirs if os.path.isdir(d)))


def shim_dir(state: Path | str) -> str:
    """A folder holding ``python3`` and ``python`` that start the bot's own Python."""
    folder = os.path.join(str(state), SHIMS)
    os.makedirs(folder, exist_ok=True)
    exe = sys.executable or "python"
    texts: dict[str, str] = {}
    for name in ("python3", "python"):
        texts[name] = '#!/bin/sh\nexec "{}" "$@"\n'.format(exe.replace("\\", "/"))
        if IS_WINDOWS:
            texts[name + ".cmd"] = f'@"{exe}" %*\r\n'
    for name, text in texts.items():
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                if fh.read() == text:
                    continue
        except OSError:
            pass
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if not IS_WINDOWS:
            os.chmod(path, 0o755)
    return folder


def command_env(state: Path | str, bash: Optional[str] = None) -> dict[str, str]:
    """:func:`clean_env` with ``HOME`` in the work folder, and the bot's Python (and Git's tools, when the shell is
    Git's bash) first on the path."""
    env = clean_env(os.environ)
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    first = [shim_dir(state)] + (_git_bin_dirs(bash) if bash else [])
    env[path_key] = os.pathsep.join(first + ([env[path_key]] if env.get(path_key) else []))
    home = workspace(state)
    env["HOME"] = home
    if IS_WINDOWS:
        env["USERPROFILE"] = home
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


# -- the output ------------------------------------------------------------------------------------------------
def hide_secrets(text: str) -> str:
    """*text* with every key shape v2 knows hidden: the secret finder's shapes (JSON web tokens among them) and the
    Core's own tokens."""
    text = secret_detector.redact_only(text or "", anywhere=True).replace(secret_detector.HELD, HIDDEN)
    return devshell._TOKEN.sub(HIDDEN, text)   # noqa: SLF001 - the one pattern for a Core token


def _tail(text: str) -> str:
    text = text or ""
    return text if len(text) <= MAX_OUTPUT else "...(start cut)\n" + text[-MAX_OUTPUT:]


def finish_output(text: str) -> str:
    """Hide secrets in the WHOLE text, then trim: a key cut across the trim boundary cannot leak."""
    return _tail(hide_secrets(text))


def _read_tail(pipe: Any) -> tuple[threading.Thread, dict]:
    """Read *pipe* to its end in the background, keeping at most :data:`KEEP_BYTES` of its tail."""
    box: dict = {"data": bytearray(), "cut": False}

    def read() -> None:
        try:
            while True:
                piece = pipe.read(65536)
                if not piece:
                    return
                box["data"] += piece
                extra = len(box["data"]) - KEEP_BYTES
                if extra > 0:
                    del box["data"][:extra]
                    box["cut"] = True
        except (OSError, ValueError):
            pass
    t = threading.Thread(target=read, daemon=True, name="command-output")
    t.start()
    return t, box


def _text_of(box: dict) -> str:
    text = bytes(box["data"]).decode("utf-8", errors="replace")
    if box["cut"]:                     # the first line was cut: a half of a key is no shape the finder knows
        text = text.split("\n", 1)[-1] if "\n" in text else ""
    return text.replace("\r\n", "\n")


def run_program(argv: list[str], cwd: Path | str, env: Mapping[str, str], *, seconds: float | None = None,
                cancelled: Any = None, line: str | None = None) -> dict:
    """Run *argv* in a job of its own, with no stdin, to its end, the time limit, or *cancelled*; everything it started
    ends with it. ``{"code": exit code or None, "stopped": "" | "time" | "closed", "text": what it printed}``.
    Raises OSError when it can't start, and RuntimeError when it can't be held in a job."""
    limit = COMMAND_SECONDS if seconds is None else seconds
    job = jobs.Job.spawn_piped(argv, cwd, dict(env), stdin=subprocess.DEVNULL, line=line)
    if not job.contained:
        job.terminate(wait_s=5)
        job.close()
        raise RuntimeError(NOT_CONTAINED)
    readers = [_read_tail(job.proc.stdout), _read_tail(job.proc.stderr)]
    code: int | None = None
    stopped = ""
    end = time.monotonic() + limit
    try:
        while True:
            left = end - time.monotonic()
            if left <= 0:
                stopped = "time"
                break
            try:
                code = job.proc.wait(timeout=min(left, POLL_S))
                break
            except subprocess.TimeoutExpired:
                pass
            if cancelled is not None and cancelled.is_set():
                stopped = "closed"
                break
    finally:
        job.terminate(wait_s=10)
        job.close()
    for reader, _box in readers:
        reader.join(READER_WAIT_S)
    out, err = (_text_of(box) for _reader, box in readers)
    return {"code": code, "stopped": stopped, "text": (out + (("\n" + err) if err else "")).strip()}


# -- the tool --------------------------------------------------------------------------------------------------
def _checked(state: Path | str, args: dict) -> tuple[str, str]:
    command = check_command(args.get("command"), state)
    _check_shell(command)
    return command, folder_for(state)


def check_not_safe_mode(ctx: dict) -> None:
    """In safe mode chat's commands close, like the Developer shell (``api.groups.dev``). Can't tell: closed."""
    safe = (ctx or {}).get("safe_mode")
    if callable(safe):
        try:
            closed = bool(safe())
        except Exception:  # noqa: BLE001
            closed = True
        if closed:
            raise ValueError(SAFE_MODE)


def question(state: Path | str, args: dict, ctx: dict) -> Optional[dict]:
    """What she is asked: the exact command and its folder. None only for a plain call of a program she set to Always
    allow: that one runs with no card, by its full path, logged (D16). Raises ValueError for a refusal: she is not
    asked."""
    from . import programs
    check_not_safe_mode(ctx)
    command, folder = _checked(state, args)
    keep = (ctx or {}).get("keep")
    if not callable(keep):
        raise ValueError(ONLY_CHAT)
    if _HANDLE.search(command) or keep(command) != command:
        raise ValueError(HOLDS_PRIVATE)
    own = programs.match(state, command)
    if own and own[0]["always_allow"] and ctx.get("her_device") is True:
        return None                    # any other owner caller (an API key) gets the card her paired device answers
    q = {"kind": KIND, "tool": TOOL, "title": TITLE, "command": command, "folder": folder,
         "detail": CARD_WHAT.format(shell=shell_name()), "end_turn": True}
    if own:
        q["detail"] = CARD_OWN.format(name=own[0]["name"])
        q["argv"] = programs.argv_for(own[0], own[1])
    if callable(ctx.get("notify")):
        q["notify"] = ctx["notify"]     # the card's id goes to the chat once it is raised: every surface draws it
    return q


def visible(text: str) -> str:
    """*text* for a card: line breaks stay, and every character that does not show (a control code, a bidi mark) is
    written out as ``[U+XXXX]``, so nothing can hide or reorder what she reads."""
    return "".join(ch if ch == "\n" or unicodedata.category(ch)[0] != "C" and ch not in "\u2028\u2029"
                   else f"[U+{ord(ch):04X}]" for ch in text)


def card_for(q: dict) -> dict:
    """The stored card for one question: only what her Yes covers."""
    card = {"title": str(q.get("title") or TITLE), "what": visible(str(q.get("detail") or "")), "tool": TOOL,
            "command": visible(str(q.get("command") or "")), "folder": visible(str(q.get("folder") or ""))}
    if q.get("argv"):
        card["argv"] = visible(json.dumps(q["argv"], ensure_ascii=False))
    return card


def run(state: Path | str, args: dict, ctx: dict) -> dict:
    """Run it (only reached after her Yes, or for a plain call of her own program she set to Always allow: checked again
    here, never trusted)."""
    from . import programs
    ctx = ctx or {}
    check_not_safe_mode(ctx)
    command, folder = _checked(state, args)
    said_yes = ctx.get("said_yes") is True
    own = programs.match(state, command)
    if not said_yes and not (own and own[0]["always_allow"] and ctx.get("her_device") is True):
        raise ValueError(NOT_APPROVED)
    cancelled = ctx.get("cancelled")
    if cancelled is not None and cancelled.is_set():
        raise ValueError(CHAT_CLOSED)            # a late Yes on a chat that is gone: nobody would see the result
    if own:
        return programs.run_cli(own[0], own[1], folder=folder, asked=said_yes, ctx=ctx)
    bash = git_bash()
    argv, line = _shell_call(command, bash)
    return outcome(run_program(argv, folder, command_env(state, bash), cancelled=cancelled, line=line), folder)


def outcome(done: dict, folder: str) -> dict:
    """What the model gets from one :func:`run_program`."""
    output = finish_output(done["text"])
    result: dict[str, Any] = {"exit_code": done["code"], "worked": done["code"] == 0, "folder": folder,
                              "output": output}
    if done["stopped"]:
        result["note"] = (TIMED_OUT_NOTE.format(seconds=COMMAND_SECONDS) if done["stopped"] == "time" else CLOSED_NOTE)
    elif done["code"] != 0:
        missing = _MISSING.search(output) or done["code"] == 127
        result["note"] = (MISSING_NOTE if missing else FAILED_NOTE).format(code=done["code"])
    return result


__all__ = ["COMMAND_SECONDS", "KIND", "MAX_COMMAND", "MAX_OUTPUT", "SCHEMA", "TITLE", "TOOL", "card_for", "check_command",
           "check_not_safe_mode", "clean_env", "command_env", "description", "finish_output", "folder_for", "git_bash",
           "hide_secrets", "outcome", "question", "run", "run_program", "shell_name", "visible", "workspace"]
