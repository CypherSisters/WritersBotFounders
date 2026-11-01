"""What a try may run: one program and its arguments, never a shell, from an allowlist (D60, D61).

Toolsmith asks the Core to run ``{program, args, cwd}``. The Core starts it
directly as an argument list inside the try's job object: no ``bash``, no
``cmd``, no PowerShell host, so pipes, ``&&``, ``eval``, ``$(...)`` and
backslash-escaped names do not exist. An older single command line is taken
only when it is ONE plain program with literal arguments (:func:`parse_line`);
any shell syntax is refused with :data:`SHELL_LINE`.

The program is first resolved to a real file (:func:`decide`), searching only
the try's own ``.venv``, the known Node.js install, the known Docker CLI, and
the try folder. The decision is made on that file, never on the typed name:
- the try's own Python, ``.venv`` at the top of its folder, made by the Core
  (``python -m venv .venv`` is a Core step, and the Core records the venv's
  interpreter and ``pyvenv.cfg``, so a changed or copied interpreter is not
  trusted); its ``pip`` runs as ``python -m pip``;
- ``node``, ``npm`` and ``npx`` from the Node.js install the Core found on its
  own PATH, only a real ``node.exe`` (``npm``/``npx`` run as ``node <npm-cli.js>``:
  their ``.cmd`` wrappers would need ``cmd.exe``). Any other ``.cmd`` or ``.bat``
  is not run (Windows starts those through ``cmd.exe``, ``steward.noshell``);
- ``docker compose``, handed to the Docker wall path (``trials``);
- a native executable (``.exe``/``.com`` on Windows) that was in the download
  Drawbridge checked, unchanged since (its sha256 recorded at the check).
Inline code is refused (``python -c``, ``node -e``/``-p``/``--eval``); a script
FILE inside the try folder may run. ``python -m`` runs only ``pip`` (only into
the app's own .venv: one known subcommand first, no ``--python``, ``--target``, ``--prefix``,
``--root``, ``--user``, ``--src``, in any spelling pip reads, requirement files only inside the try and
holding only plain requirements, :func:`_pip_args`), a module
that is a file in the try folder or in its own .venv's site-packages (found by
path, never imported by the Core, :func:`module_home`), and ``http.server`` on
127.0.0.1 and the try's own port; never another standard-library module
(``python -m venv .venv`` is the Core's own step). Anything else is not run:
every refusal comes back as ONE ``not_run`` record (:meth:`Refusal.reply`)
naming the exact program and arguments, with ``kind``: ``your_turn`` (a program
not on the list: Toolsmith turns it into a "Your turn" step for her), ``hint``
(a Core tool does it, ``use`` says which) or ``denied`` (never run, by anyone:
a shell, inline code, a stop of another process, a change to the computer, a
path outside the app; ``why`` says it).

Honest limit: a script file the try runs (``python app.py``, an npm script, a
program from the download) runs as the owner, and can do whatever she can.
"""

from __future__ import annotations

import codecs
import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...steward import noshell

IS_WINDOWS = os.name == "nt"
SHELL_LINE = "Send one program and its arguments; BuildBot doesn't use a shell."
INLINE_LINE = ("BuildBot doesn't run inline code ({flag}). Write the code to a file in the app's folder with "
               "write_file, then run that file.")
SHELL_PROGRAM = "BuildBot never runs a shell ({name}). Send one program and its arguments."
SCRIPT_OUTSIDE = "Run a script file inside the app's folder."
BATCH_WHY = ("it is a Windows batch file (.cmd or .bat), and Windows runs those only through cmd.exe, a shell.")
BATCH_USE = "Run the program the batch file runs, with its arguments, or write a Python or Node.js script instead."
CWD_LINE = "Give cwd as a folder inside the app's folder."
NO_VENV = ("The app has no Python of its own yet. Run python -m venv .venv first (the Core makes it), "
           "then python runs the app's own.")
VENV_WHERE = "Make the app's own Python as .venv at the top of its folder: python -m venv .venv"
VENV_CHANGED = ("The app's own Python (.venv) changed after the Core made it, so it is not run. "
                "Delete .venv with delete_path and make it again with python -m venv .venv.")
VENV_PIP_SETTINGS = ("The app's own Python (.venv) holds a pip settings file ({name}) the Core never made, so it "
                     "is not run. Delete .venv with delete_path and make it again with python -m venv .venv.")
#: The files pip reads as its site settings from the folder of the Python it runs in (sys.prefix).
_PIP_SETTING_NAMES = frozenset({"pip.ini", "pip.conf"})
MAX_ARGS = 200
MAX_ARG = 8000

_PY_NAME = re.compile(r"(?i)^(?:python|pythonw)(?:\d+(?:\.\d+)?)?(?:\.exe)?$")
_PIP_NAME = re.compile(r"(?i)^pip(?:\d+(?:\.\d+)?)?(?:\.exe)?$")
_NODE_NAME = re.compile(r"(?i)^node(?:\.exe)?$")
_NPM_NAME = re.compile(r"(?i)^(npm|npx)(?:\.cmd|\.ps1|\.exe)?$")

#: Programs never run in a try, whatever folder they are in: shells and Windows' script hosts (D60).
SHELLS = frozenset({"cmd", "command", "powershell", "powershell_ise", "pwsh", "bash", "sh", "zsh", "dash", "ksh",
                    "csh", "tcsh", "fish", "ash", "busybox", "wsl", "wslhost", "wscript", "cscript", "mshta",
                    "rundll32", "regsvr32", "msiexec", "conhost", "start", "env", "nohup", "sudo", "gsudo", "runas",
                    "schtasks", "at", "reg", "regedit", "sc", "net", "net1", "netsh", "wmic", "bitsadmin",
                    "certutil", "forfiles", "pcalua", "explorer", "winget", "choco", "scoop", "py", "pyw", "su", "doas",
                    "pkexec", "crontab", "systemctl", "launchctl"})
#: Interpreters that may come in a download: their inline-code flags are refused too.
_OTHER_INTERP = re.compile(r"(?i)^(?:deno|bun|perl\d*|ruby\d*|php\d*|lua\d*|luajit|tclsh\d*|wish\d*|julia|"
                           r"rscript|jshell|osascript|node\d+)$")
_INLINE_GENERIC = re.compile(r"^(?:-e|-E|-c|-r|-p|-x|--eval|--print|--exec|--execute|--command|--code)(?:=.*)?$")

#: Typed names a Core tool does instead (a hint, never a step for the owner).
_HINTS = {
    "read": ("cat", "type", "head", "tail", "less", "more", "get-content", "gc", "ls", "dir", "tree", "find",
             "grep", "egrep", "fgrep", "rg", "findstr", "wc", "stat", "file", "pwd", "which", "where",
             "get-childitem", "select-string"),
    "files": ("cp", "copy", "xcopy", "robocopy", "copy-item", "mv", "move", "ren", "rename", "move-item", "mkdir",
              "md", "new-item", "rm", "del", "erase", "rd", "rmdir", "remove-item", "touch"),
    "kill": ("taskkill", "tskill", "kill", "pkill", "killall", "stop-process", "spps", "pskill", "fkill"),
    "git": ("git", "gh"),
    "pm": ("pnpm", "yarn", "corepack"),
    "compose": ("docker-compose",),
    "venv_tool": ("uvicorn", "gunicorn", "flask", "streamlit", "django-admin", "hypercorn", "waitress-serve",
                  "fastapi", "celery", "alembic", "pytest"),
}
_HINT_TEXT = {
    "read": "Read files with read_file and list folders with list_files.",
    "files": "Use copy_path, move_path, make_folder or delete_path for files in the app's folder.",
    "kill": "Stop a server this try started with stop_server.",
    "git": "The Core downloads the app with download; git does not run in a try.",
    "pm": "Run it through npx (for example npx -y pnpm install).",
    "compose": "Run it as docker compose (docker with compose as its first argument).",
    "venv_tool": "Run it with the app's own Python: python -m {name} ...",
}
_BUILTINS = frozenset("""cd chdir pushd popd set export unset source . eval exec echo printf exit call if for
goto shift setlocal endlocal alias read test [ true false time title ver vol cls color prompt assoc ftype path
pause mklink local declare typeset let trap wait jobs fg bg disown ulimit umask history""".split())
_CMDLET = re.compile(r"(?i)^(?:get|set|new|remove|invoke|start|stop|copy|move|add|clear|out|write|read|select|where|"
                     r"foreach|test|import|export|expand|compress|rename|join|split|resolve|push|pop|enable|disable|"
                     r"install|uninstall|register|unregister|wait|measure|format|convertto|convertfrom|update|"
                     r"restart|suspend|resume|show|find|save|send|receive|use|enter|exit|tee|sort|group|compare|"
                     r"trace|debug|mount|dismount|unblock|grant|revoke|protect|unprotect|lock|unlock)-[a-z]+$")
_UNQUOTED_META = set("|&;<>()$`*?{}!^\n\r")
_ENV_SUB = re.compile(r"%[A-Za-z_][\w()]*%")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


YOUR_TURN, HINT, DENIED = "your_turn", "hint", "denied"


class Refusal(Exception):
    """Not run. ``reply`` is what Toolsmith gets back: ``said`` and the one ``not_run`` record
    (:func:`complete` fills it in where the refusal came before the program was known)."""

    def __init__(self, said: str, not_run: dict | None = None, kind: str = DENIED) -> None:
        super().__init__(said)
        self.said, self.not_run, self.kind = said, not_run, kind

    def reply(self) -> dict:
        nr = dict(self.not_run) if self.not_run is not None else {
            "program": "", "args": [], "cwd": "", "folder": "", "resolved": "", "why": self.said}
        nr.setdefault("kind", HINT if nr.get("use") else self.kind)
        return {"ok": False, "said": self.said, "refused": True, "not_run": nr}


def parse_words(line: str) -> list[str]:
    """The words of a request line the Core made itself (``Request.line``); the line as one word if it
    can't be read back."""
    try:
        program, args = parse_line(line)
        return [program, *args]
    except Refusal:
        return [str(line or "")]


def complete(exc: Refusal, req: "Request | None", folder: Any, raw: Any = None) -> Refusal:
    """*exc* with its ``not_run`` record filled in: the program and arguments asked for (or the line as
    sent, when it could not be read as one program), and why. A record already there keeps its words."""
    if exc.not_run is not None:
        exc.not_run.setdefault("kind", HINT if exc.not_run.get("use") else YOUR_TURN)
        return exc
    if req is not None:
        program, args, cwd = req.program, list(req.args), req.cwd
    else:
        body = raw if isinstance(raw, dict) else {}
        program = str(body.get("program") or body.get("command") or (raw if isinstance(raw, str) else ""))[:2000]
        args = [str(a)[:MAX_ARG] for a in body.get("args") or []][:MAX_ARGS] if isinstance(body.get("args"), list) \
            else []
        cwd = str(body.get("cwd") or "") if isinstance(body.get("cwd"), str) else ""
    exc.not_run = {"program": program, "args": args, "cwd": cwd, "folder": str(folder or ""), "resolved": "",
                   "why": exc.said, "kind": exc.kind}
    return exc


@dataclass
class Request:
    """What Toolsmith asked: one program, its literal arguments, and a folder inside the try (``""`` = the top)."""
    program: str
    args: list[str]
    cwd: str = ""

    def line(self) -> str:
        """The request as one line (for the zone check, the record and her step). Never run by a shell."""
        return join([self.program, *self.args])


@dataclass
class Place:
    """What one try's programs are judged against."""
    folder: Path
    venv: dict | None = None                 # {"python": sha256, "cfg": sha256}, made by the Core
    checked: dict = field(default_factory=dict)   # rel path -> sha256 of executables in the checked download
    node: str = ""                           # the Node.js the Core found on its own PATH
    docker: str = ""                         # the Docker CLI the Core found on its own PATH
    port: int | None = None                  # a server's own port (start_server), else None


@dataclass
class Plan:
    """What the broker starts: ``kind`` run | venv | compose."""
    kind: str
    argv: list[str]
    cwd: Path
    line: str
    rest: list[str] = field(default_factory=list)      # compose: the arguments after ``compose``
    venv_flags: list[str] = field(default_factory=list)


# -- the request -------------------------------------------------------------------------------------

def join(words: list[str]) -> str:
    """Words as one line: a word with a space or a quote is double-quoted (backslashes stay as they are)."""
    out = []
    for w in words:
        w = str(w)
        out.append(w if w and re.fullmatch(r"[\w@%+=:,./\\{}\[\]-]+", w) else '"' + w.replace('"', "'") + '"')
    return " ".join(out)


def parse_line(line: str) -> tuple[str, list[str]]:
    """``(program, args)`` from ONE plain program line, or :class:`Refusal` (:data:`SHELL_LINE`).

    A strict subset of shell words: words split on spaces; '...' and "..." group (no escapes inside);
    backslashes are literal (Windows paths). Any operator, substitution, redirection, glob, comment,
    assignment or builtin is refused."""
    text = str(line or "").strip()
    if not text:
        raise Refusal("Give a program to run.")
    if _ENV_SUB.search(text):
        raise Refusal(SHELL_LINE)
    words: list[str] = []
    cur: list[str] = []
    in_word = False
    quote = ""
    for ch in text:
        if quote:
            if ch == quote:
                quote = ""
                continue
            if quote == '"' and ch in "$`!":
                raise Refusal(SHELL_LINE)
            cur.append(ch)
            continue
        if ch in "'\"":
            quote, in_word = ch, True
            continue
        if ch in " \t":
            if in_word:
                words.append("".join(cur))
                cur, in_word = [], False
            continue
        if ch in _UNQUOTED_META or (not in_word and ch in "#~"):
            raise Refusal(SHELL_LINE)
        cur.append(ch)
        in_word = True
    if quote:
        raise Refusal(SHELL_LINE)
    if in_word:
        words.append("".join(cur))
    if not words or not words[0]:
        raise Refusal("Give a program to run.")
    first = words[0]
    if _ASSIGN.match(first):
        raise Refusal(SHELL_LINE + " The Core gives servers PORT and HOST; write other settings to a .env file "
                                   "with write_file.")
    base = _base(first)
    if first.lower() in _BUILTINS or base in _BUILTINS or (_CMDLET.match(first) and base not in _hint_names()):
        hint = " Use cwd for the folder." if base in ("cd", "chdir", "pushd", "set-location") else ""
        raise Refusal(SHELL_LINE + hint)
    return first, words[1:]


def request_from(body: Any) -> Request:
    """A :class:`Request` from ``{"program", "args", "cwd"}``, ``{"command": "<one line>"}`` or a line."""
    if isinstance(body, Request):
        return body
    cwd = ""
    if isinstance(body, dict):
        cwd = body.get("cwd") or ""
        if not isinstance(cwd, str):
            raise Refusal(CWD_LINE)
        if body.get("program"):
            program, args = body.get("program"), body.get("args") if body.get("args") is not None else []
            if not isinstance(program, str) or not isinstance(args, list) \
                    or not all(isinstance(a, (str, int, float)) and not isinstance(a, bool) for a in args):
                raise Refusal("Give program as text and args as a list of text.")
            args = [str(a) for a in args]
            program = program.strip()
            if len(program) >= 2 and program[0] == program[-1] and program[0] in "\"'":
                program = program[1:-1]
        else:
            program, args = parse_line(str(body.get("command") or ""))
    else:
        program, args = parse_line(str(body or ""))
    if not program or "\x00" in program or len(program) > 1000:
        raise Refusal("Give a program to run.")
    if len(args) > MAX_ARGS or any(len(a) > MAX_ARG or "\x00" in a or "\n" in a or "\r" in a for a in args):
        raise Refusal("Its arguments are too long, or hold a line break.")
    return Request(program=program, args=args, cwd=cwd.strip())


# -- places ------------------------------------------------------------------------------------------

def _base(word: str) -> str:
    name = re.split(r"[\\/]", str(word))[-1].lower()
    for ext in (".exe", ".com", ".cmd", ".bat", ".ps1"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _hint_names() -> set[str]:
    return {n for names in _HINTS.values() for n in names}


ODD_PATH = ("That path is written in a way BuildBot can't check (a network share, a device path or a hidden "
            "part of a file), so nothing was run.")


def odd_path(text: str) -> bool:
    """A network share or device path (two slashes first), or an NTFS stream (a colon past the drive)."""
    t = str(text or "").strip().strip("\"'")
    if len(t) >= 2 and t[0] in "\\/" and t[1] in "\\/":
        return True
    return IS_WINDOWS and ":" in t[2:]


def _real(path: Path | str) -> Path:
    """The real path, never looked up when it names a network share or device (that could send her sign-in)."""
    if odd_path(str(path)):
        raise Refusal(ODD_PATH)
    return Path(os.path.realpath(str(path)))


def _inside(path: Path, root: Path) -> bool:
    try:
        rp, rr = os.path.normcase(str(path)), os.path.normcase(str(root))
        return rp == rr or rp.startswith(rr.rstrip("\\/") + os.sep)
    except (TypeError, ValueError):
        return False


def _is_link(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    import stat as _st
    return _st.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def venv_dir(folder: Path) -> Path:
    return Path(folder) / ".venv"


def venv_bin(folder: Path) -> Path:
    return venv_dir(folder) / ("Scripts" if IS_WINDOWS else "bin")


def venv_python(folder: Path) -> Path:
    return venv_bin(folder) / ("python.exe" if IS_WINDOWS else "python")


def venv_pip_settings(folder: Path) -> str:
    """The name of a pip settings file (``pip.ini`` or ``pip.conf``, any letter case, trailing dots or spaces)
    at the top of the try's .venv, where pip reads its site settings; "" when there is none. A .venv that
    can't be listed counts as holding one (fail closed); no .venv at all holds none."""
    v = venv_dir(folder)
    try:
        if not os.path.lexists(v):
            return ""
        names = os.listdir(v)
    except OSError:
        return "pip.ini"
    for name in names:
        if name.rstrip(" .").casefold() in _PIP_SETTING_NAMES:
            return name
    return ""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def venv_record(folder: Path) -> dict | None:
    """The record the Core keeps of a venv it made: its interpreter and ``pyvenv.cfg`` (None if unreadable)."""
    py, cfg = venv_python(folder), venv_dir(folder) / "pyvenv.cfg"
    try:
        if (not cfg.is_file() or (IS_WINDOWS and _is_link(py)) or not py.exists() or _is_link(venv_dir(folder))
                or _is_link(venv_bin(folder)) or _is_link(cfg)):
            return None
        return {"python": sha256(py), "cfg": sha256(cfg)}
    except OSError:
        return None


def _native(path: Path) -> bool:
    """A native program file (not a script a shell or script host runs)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    if IS_WINDOWS:
        return path.suffix.lower() in (".exe", ".com") and head[:2] == b"MZ"
    return head in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def checked_programs(folder: Path, files: Any) -> dict:
    """``{rel: sha256}`` for the native programs in a checked download (*files*: its ``a/b`` paths)."""
    out = {}
    for rel in files:
        path = Path(folder) / rel
        if _is_link(path) or not _native(path):
            continue
        try:
            out[str(rel)] = sha256(path)
        except OSError:
            continue
    return out


def known_node() -> str:
    """The Node.js on the Core's own PATH, only a real program (``node.exe``), never a ``.cmd`` wrapper."""
    return noshell.node_exe()


def known_docker() -> str:
    """The Docker CLI on the Core's own PATH, only a real program (``docker.exe`` on Windows)."""
    found = shutil.which("docker.exe" if IS_WINDOWS else "docker")
    if not found:
        return ""
    real = _real(found)
    return str(real) if _native(real) else ""


def npm_cli(node: str, tool: str) -> Path | None:
    """``npm-cli.js`` / ``npx-cli.js`` of the Node.js install at *node*."""
    if not node or odd_path(node):
        return None
    return noshell.npm_cli(node, tool)


def cwd_of(req: Request, folder: Path) -> Path:
    """The folder the program runs in: inside the try, not through a link out of it."""
    root = _real(folder)
    if odd_path(req.cwd):
        raise Refusal(ODD_PATH)
    raw = req.cwd.replace("\\", "/").strip()
    if not raw or raw in (".", "./"):
        return root
    where = Path(raw) if os.path.isabs(raw) else Path(folder) / raw
    real = _real(where)
    if not _inside(real, root) or not real.is_dir():
        raise Refusal(CWD_LINE)
    return real


def _rel(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(str(path), str(root)).replace("\\", "/")
    except ValueError:
        return str(path)


# -- resolving and deciding ---------------------------------------------------------------------------

def _candidates(path: Path) -> list[Path]:
    if not IS_WINDOWS or path.suffix:
        return [path] + ([Path(str(path) + ".exe")] if IS_WINDOWS and path.suffix.lower() not in (".exe", ".com") else [])
    return [Path(str(path) + ext) for ext in (".exe", ".com", ".cmd")] + [path]


def resolve(program: str, place: Place, cwd: Path) -> Path | None:
    """The file *program* names, searching only the try's .venv, the known Node.js and Docker, and the try
    folder. A path is taken as written (relative to cwd). None when nothing is found there."""
    raw = program.strip()
    if odd_path(raw):
        raise Refusal(ODD_PATH)
    has_dir = bool(re.search(r"[\\/]", raw)) or os.path.isabs(raw)
    if has_dir:
        base = Path(raw) if os.path.isabs(raw) else cwd / raw
        for c in _candidates(base):
            if c.is_file():
                return c
        return None
    dirs: list[Path] = []
    folder = Path(place.folder)
    if venv_bin(folder).is_dir():
        dirs.append(venv_bin(folder))
    low = raw.lower()
    if place.node and (_NODE_NAME.match(low) or _NPM_NAME.match(low)):
        dirs.append(Path(place.node).parent)
    if place.docker and _base(low) in ("docker", "docker-compose"):
        dirs.append(Path(place.docker).parent)
    dirs += [cwd, folder]
    for d in dirs:
        for c in _candidates(d / raw):
            if c.is_file():
                return c
    return None


def _not_run(req: Request, cwd: Path, place: Place, resolved: Path | None, why: str, use: str = "") -> Refusal:
    rec = {"program": req.program, "args": list(req.args), "cwd": _rel(cwd, _real(place.folder)),
           "folder": str(cwd), "resolved": str(resolved) if resolved else "", "why": why,
           "kind": HINT if use else YOUR_TURN}
    if use:
        rec["use"] = use
    said = f"BuildBot didn't run {req.program}: {why}" + (f" {use}" if use else "")
    return Refusal(said, rec)


def _hint_for(name: str, place: Place | None = None) -> str:
    if place is not None and not place.venv and (_PY_NAME.match(name) or _PIP_NAME.match(name)):
        return NO_VENV
    for kind, names in _HINTS.items():
        if name in names:
            return _HINT_TEXT[kind].format(name=name)
    return ""


def decide(req: Request, place: Place) -> Plan:
    """The :class:`Plan` for an allowed request, else :class:`Refusal` (with ``not_run`` naming it)."""
    folder = Path(place.folder)
    cwd = cwd_of(req, folder)
    typed = _base(req.program)
    if typed in SHELLS:
        raise Refusal(SHELL_PROGRAM.format(name=typed))
    line = req.line()
    if (_PY_NAME.match(typed) or typed in ("py", "python3")) and req.args[:2] == ["-m", "venv"]:
        return _venv_plan(req, place, cwd, line)
    found = resolve(req.program, place, cwd)
    if found is None and (_PY_NAME.match(typed) or _PIP_NAME.match(typed)) and not place.venv:
        raise _not_run(req, cwd, place, None, "the app has no Python of its own yet.", NO_VENV)
    if found is None:
        return _raise(_not_run(req, cwd, place, None, "it is not the app's own Python, Node.js, npm, npx, "
                               "docker compose or a program from its checked download.", _hint_for(typed, place)))
    located = _real(found.parent) / found.name          # the folder resolved; the file itself as found
    real = _real(found)
    name = _base(found.name)
    if name in SHELLS or _base(real.name) in SHELLS:
        raise Refusal(SHELL_PROGRAM.format(name=name))
    if noshell.batch_file(found.name) or noshell.batch_file(real.name):
        # Only npm.cmd / npx.cmd next to the known node.exe: they run as node <tool>-cli.js (below).
        node_dir = os.path.normcase(str(_real(Path(place.node).parent))) if place.node else ""
        if not (node_dir and _NPM_NAME.match(found.name)
                and os.path.normcase(str(located.parent)) == node_dir):
            raise _not_run(req, cwd, place, found, BATCH_WHY, BATCH_USE)
    root = _real(folder)
    vbin = _real(venv_bin(folder)) if venv_bin(folder).is_dir() else None
    # 1. the try's own Python (and its pip, as python -m pip)
    if vbin is not None and os.path.normcase(str(located.parent)) == os.path.normcase(str(vbin)):
        if not place.venv:
            raise _not_run(req, cwd, place, found, "its .venv was not made by the Core.", NO_VENV)
        if venv_record(folder) != place.venv:
            raise Refusal(VENV_CHANGED)
        settings = venv_pip_settings(folder)
        if settings:
            raise Refusal(VENV_PIP_SETTINGS.format(name=settings[:40]))
        py = str(venv_python(folder))
        if _PY_NAME.match(found.name):
            _python_args(req.args, cwd, root, place)
            return Plan("run", [py, *req.args], cwd, line)
        if _PIP_NAME.match(found.name):
            args = ["-m", "pip", *req.args]
            _python_args(args, cwd, root, place)
            return Plan("run", [py, *args], cwd, line)
        raise _not_run(req, cwd, place, found, "only the app's own python and pip run from its .venv.",
                       _HINT_TEXT["venv_tool"].format(name=name))
    # 2. the known Node.js: node, npm, npx
    if place.node:
        node = Path(place.node)
        ndir = _real(node.parent)
        here = os.path.normcase(str(located.parent)) == os.path.normcase(str(ndir))
        npm_js, npx_js = npm_cli(place.node, "npm"), npm_cli(place.node, "npx")
        if (here and _NODE_NAME.match(found.name)) or os.path.normcase(str(real)) == os.path.normcase(str(node)):
            _node_args(req.args, cwd, root)
            return Plan("run", [str(node), *req.args], cwd, line)
        m = _NPM_NAME.match(found.name)
        tool = m.group(1).lower() if (here and m) else ""
        if not tool and npm_js is not None and real == npm_js:
            tool = "npm"
        if not tool and npx_js is not None and real == npx_js:
            tool = "npx"
        if tool:
            cli = npm_js if tool == "npm" else npx_js
            if cli is None:
                raise _not_run(req, cwd, place, found, f"{tool} was not found with Node.js.")
            _npm_args(tool, req.args, cwd, root)
            return Plan("run", [str(node), str(cli), *req.args], cwd, line)
    # 3. docker compose, behind the Docker wall
    if place.docker and os.path.normcase(str(real)) == os.path.normcase(str(place.docker)):
        if req.args[:1] != ["compose"]:
            raise _not_run(req, cwd, place, found, "a try runs Docker only as docker compose, behind the Docker "
                           "wall.", "Write a compose file with write_file and run docker compose up -d.")
        if cwd != root:
            raise Refusal("Run docker compose from the app's top folder (no cwd).")
        return Plan("compose", [str(place.docker), "compose"], cwd, line, rest=list(req.args[1:]))
    # 4. a native program that came in the checked download, unchanged
    if _inside(real, root) and not _inside(real, _real(venv_dir(folder))):
        rel = _rel(real, root)
        want = place.checked.get(rel)
        if want is None:
            raise _not_run(req, cwd, place, found, "it was not in the download Drawbridge checked.",
                           _hint_for(typed, place))
        try:
            same = sha256(real) == want
        except OSError:
            same = False
        if not same:
            raise _not_run(req, cwd, place, found, "it changed after Drawbridge checked it.")
        if _PY_NAME.match(real.name):
            _python_args(req.args, cwd, root, place)
        elif _NODE_NAME.match(real.name):
            _node_args(req.args, cwd, root)
        elif _OTHER_INTERP.match(_base(real.name)):
            for a in req.args:
                if _INLINE_GENERIC.match(a) or a == "eval":
                    raise Refusal(INLINE_LINE.format(flag=a.split("=")[0]))
        return Plan("run", [str(real), *req.args], cwd, line)
    raise _not_run(req, cwd, place, found, "it is not the app's own Python, Node.js, npm, npx, docker compose "
                   "or a program from its checked download.", _hint_for(typed, place))


def _raise(r: Refusal) -> Plan:
    raise r


def _venv_plan(req: Request, place: Place, cwd: Path, line: str) -> Plan:
    """``python -m venv .venv``: a Core step (the Core's own base Python makes it), only as ``.venv`` at the top."""
    rest = req.args[2:]
    flags = [a for a in rest if a.startswith("-")]
    targets = [a for a in rest if not a.startswith("-")]
    if len(targets) != 1 or any(f not in ("--without-pip", "--clear", "--upgrade-deps") for f in flags):
        raise Refusal(VENV_WHERE)
    target = _real(Path(targets[0]) if os.path.isabs(targets[0]) else cwd / targets[0])
    if os.path.normcase(str(target)) != os.path.normcase(str(_real(venv_dir(place.folder)))):
        raise Refusal(VENV_WHERE)
    return Plan("venv", [], _real(place.folder), line, venv_flags=[f for f in flags if f != "--clear"])


def _script_inside(arg: str, cwd: Path, root: Path) -> None:
    where = _real(Path(arg) if os.path.isabs(arg) else cwd / arg)
    if not _inside(where, root):
        raise Refusal(SCRIPT_OUTSIDE)


_PY_FLAGS = set("bBdEhiIOPqRsSuvVx")


def _python_args(args: list[str], cwd: Path, root: Path, place: Place | None = None) -> None:
    """Python's options: inline code (``-c``) and stdin (``-``) refused; a script must be inside the try;
    ``-m`` only as :func:`_module_args` allows."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a == "-":
            raise Refusal(INLINE_LINE.format(flag="-"))
        if a in ("--version", "--help", "-?"):
            return
        if a.startswith("--"):
            if a.startswith("--check-hash-based-pycs"):
                i += 1 if "=" in a else 2
                continue
            raise Refusal(f"BuildBot doesn't pass the Python option {a.split('=')[0]}.")
        if a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch == "c":
                    raise Refusal(INLINE_LINE.format(flag="-c"))
                if ch == "m":
                    module = a[j + 1:] or (args[i + 1] if i + 1 < len(args) else "")
                    if not module:
                        raise Refusal("Give the module after -m.")
                    rest = args[i + (1 if a[j + 1:] else 2):]
                    _module_args(module, rest, cwd, root, place)
                    return
                if ch in "WX":
                    if not a[j + 1:]:
                        i += 1
                    break
                if ch not in _PY_FLAGS:
                    raise Refusal(f"BuildBot doesn't pass the Python option -{ch}.")
                j += 1
            i += 1
            continue
        break
    if i < len(args):
        _script_inside(args[i], cwd, root)


#: pip options that aim it at another Python or another place: never passed (in any spelling pip reads).
_PIP_NEVER = ("--python", "--target", "--prefix", "--root", "--user", "--src", "--global-option",
              "--install-option", "--break-system-packages")
#: pip options that write a file where they are told: only inside the try folder.
_PIP_INSIDE = ("--log", "--log-file", "--local-log", "--cache-dir", "--report", "--dest", "--wheel-dir")
#: pip options that read a requirement or constraint file: the file only inside the try, its lines checked.
_PIP_REQFILE = ("--requirement", "--constraint")
#: pip's editable install: only a folder inside the try.
_PIP_EDITABLE = ("--editable",)
#: Where pip looks for packages: these come only from the README route, on a card, never from a try.
_PIP_INDEX = ("--index-url", "--extra-index-url", "--trusted-host", "--find-links")
#: pip long options that are also the start of one above: pip reads the exact word as itself.
_PIP_EXACT = frozenset({"--pre", "--log"})
#: pip short options that take a value (the rest of the word, or the next word).
_PIP_SHORT_VALUE = frozenset("rcefidwbCt")
#: pip long options (not ours above) that take a value, so the next word is not a requirement.
_PIP_LONG_VALUE = frozenset({
    "--platform", "--python-version", "--implementation", "--abi", "--progress-bar", "--exists-action",
    "--timeout", "--retries", "--proxy", "--cert", "--client-cert", "--keyring-provider", "--use-feature",
    "--use-deprecated", "--upgrade-strategy", "--root-user-action", "--no-binary", "--only-binary",
    "--config-settings", "--format", "--path", "--exclude", "--resume-retries"})
#: The pip subcommands a try may run (the very first word after ``-m pip``), and ``--version`` alone.
PIP_COMMANDS = frozenset({"install", "uninstall", "list", "show", "freeze", "check", "download", "wheel"})
#: The subcommands whose plain words are requirements (a name, or a path that must be inside the try).
_PIP_REQ_COMMANDS = frozenset({"install", "download", "wheel"})
PIP_OUTSIDE = ("pip installs only into the app's own .venv, so {flag} is not passed (it aims pip at another "
               "Python or another place).")
PIP_WRITE = "pip writes its files only inside the app's folder, so {flag} there is not passed."
PIP_COMMAND_LINE = ("BuildBot runs pip in a try only as python -m pip followed by one of: install, uninstall, "
                    "list, show, freeze, check, download, wheel (or --version alone), so {word} was not run.")
PIP_INDEX = ("Where pip looks for packages ({flag}) comes only from the app's README, on a card for the owner's "
             "Yes, never from a try, so it was not run.")
PIP_REQ_OUTSIDE = "pip reads requirement files only inside the app's folder, so {what} was not run."
PIP_REQ_LINE = ("A requirement file in a try holds only plain requirements (a name and its version) and -r or "
                "-c to other files inside the app's folder; {path} line {n} has {what}, so it was not run.")
PIP_REQ_PATH = "pip installs only from inside the app's folder in a try, so {what} was not run."
_REQ_MAX_BYTES = 1 << 20
_REQ_MAX_DEPTH = 10
#: A plain requirement: a name, extras, version specifiers, and an environment marker after ";".
_PLAIN_REQ = re.compile(r"""^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\s*(?:\[[A-Za-z0-9._,\s-]*\])?\s*
    (?:\(?\s*(?:(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+\s*,?\s*)*\)?)?\s*(?:;[^@]*)?$""", re.X)
_URL_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]+):")
#: Options a requirement-file line of its own may hold (what pip's requirement-file parser knows).
_REQ_FILE_OPTIONS = ("--requirement", "--constraint", "--editable", "--index-url", "--extra-index-url",
                     "--no-index", "--find-links", "--pre", "--trusted-host", "--use-feature", "--prefer-binary",
                     "--require-hashes", "--only-binary", "--no-binary", "--global-option", "--config-settings",
                     "--hash")
_REQ_FILE_SHORT = {"r": "--requirement", "c": "--constraint", "e": "--editable", "i": "--index-url",
                   "f": "--find-links"}
_ENV_SETTING = "a ${...} setting (pip fills it in from the environment)"
REQ_ENCODING = "This requirements file uses a text encoding BuildBot doesn't read. Save it as plain UTF-8."
#: pip's own coding-line pattern (pip/_internal/utils/encoding.py ENCODING_RE), looked for in the first two
#: lines as pip splits them (on b"\n"); here on any such line, not only one starting with "#" (stricter).
_PIP_CODING = re.compile(rb"coding[:=]\s*([-\w.]+)")
#: Every byte-order mark pip knows but UTF-8's (UTF-32 LE starts like UTF-16 LE: all refused alike).
_OTHER_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)


def _pip_long(word: str) -> tuple[str, str]:
    """``(the option pip reads, "never" | "inside" | "reqfile" | "editable" | "index" | "")`` for a long
    option word. pip reads any unambiguous start of a long option as that option (``--tar`` is
    ``--target``); a word that starts one of ours is judged as it, unless it is exactly another pip option."""
    name = word.split("=", 1)[0].lower()
    groups = (("never", _PIP_NEVER), ("inside", _PIP_INSIDE), ("reqfile", _PIP_REQFILE),
              ("editable", _PIP_EDITABLE), ("index", _PIP_INDEX))
    for kind, names in groups:
        if name in names:
            return name, kind
    if name in _PIP_EXACT:
        return name, ""
    if len(name) < 3:
        return name, ""
    for kind, names in groups:
        for full in names:
            if full.startswith(name):
                return full, kind
    return name, ""


def _under(value: str, bases: list[Path], root: Path) -> bool:
    """*value* (a path, relative to each of *bases*) is inside the try folder *root* from every base."""
    if not value or odd_path(value):
        return False
    for base in bases:
        where = Path(value) if os.path.isabs(value) else base / value
        if not _inside(_real(where), root):
            return False
    return True


def _file_url_path(value: str) -> str | None:
    """The local path of a ``file:`` URL ("" for one that names another computer), or None when *value*
    is not a ``file:`` URL."""
    m = _URL_SCHEME.match(value)
    if not m or len(m.group(1)) == 1 or m.group(1).lower() != "file":   # one letter: a drive (C:), not a URL
        return None
    from urllib.parse import unquote, urlsplit
    from urllib.request import url2pathname
    parts = urlsplit(value)
    if parts.netloc and parts.netloc.lower() != "localhost":
        return ""                                                          # file://server/share: never
    return url2pathname(unquote(parts.path)) if parts.path else ""


def _is_url(value: str) -> bool:
    m = _URL_SCHEME.match(value)
    return bool(m and len(m.group(1)) > 1) or "://" in value


def _local_ok(value: str, bases: list[Path], root: Path) -> bool:
    """A path, or a ``file:`` URL, that is inside the try from every base. Any other URL: no."""
    if _is_url(value):
        local = _file_url_path(value)
        return bool(local) and _under(local, bases, root)
    return _under(value, bases, root)


def _requirement_ok(spec: str, bases: list[Path], root: Path) -> str:
    """"" when *spec* (one requirement as pip reads it) is a plain requirement, or a path or ``file:`` URL
    inside the try; else what is wrong, in plain words."""
    spec = spec.strip()
    if not spec:
        return ""
    if "${" in spec:
        return _ENV_SETTING
    head = spec.split(";", 1)[0].strip()
    if "@" in head:                                                        # name @ url
        url = head.split("@", 1)[1].strip()
        return "" if _is_url(url) and _local_ok(url, bases, root) else f"a requirement from {url[:80]}"
    if _is_url(head):
        return "" if _local_ok(head, bases, root) else f"a requirement from {head[:80]}"
    if _PLAIN_REQ.match(spec):
        return ""
    return "" if _under(head, bases, root) else f"the path {head[:80]}"


def _req_value(value: str, kind: str, cwd: Path, root: Path, seen: set) -> None:
    """The value of -r/-c (a file) or -e (a folder) on pip's command line, judged from *cwd*."""
    if kind == "editable":
        if "${" in value or not _local_ok(value, [cwd], root):
            raise Refusal(PIP_REQ_PATH.format(what="-e " + (value[:80] or "with no folder")))
        return
    _req_file(value, cwd, root, seen, 0)


def _req_file(value: str, base: Path, root: Path, seen: set, depth: int) -> None:
    """A requirement or constraint file pip will read: inside the try, and every line plain (nested files
    the same, relative to the file they are named in, as pip reads them)."""
    shown = (value or "a requirement file with no name")[:80]
    if not value or "${" in value:
        raise Refusal(PIP_REQ_OUTSIDE.format(what=shown))
    if _is_url(value):
        local = _file_url_path(value)
        if not local:
            raise Refusal(PIP_REQ_OUTSIDE.format(what=shown))          # a file on the internet, never
        value = local
    if odd_path(value):
        raise Refusal(PIP_REQ_OUTSIDE.format(what=shown))
    path = _real(Path(value) if os.path.isabs(value) else base / value)
    if not _inside(path, root):
        raise Refusal(PIP_REQ_OUTSIDE.format(what=shown))
    key = os.path.normcase(str(path))
    if key in seen:
        return
    if depth > _REQ_MAX_DEPTH:
        raise Refusal(PIP_REQ_OUTSIDE.format(what="requirement files nested this deep"))
    seen.add(key)
    try:
        if not path.is_file() or path.stat().st_size > _REQ_MAX_BYTES:
            raise Refusal(PIP_REQ_OUTSIDE.format(what=shown + " (not a file of the app)"))
        raw = path.read_bytes()
    except OSError:
        raise Refusal(PIP_REQ_OUTSIDE.format(what=shown + " (it can't be read)")) from None
    text = _req_text(raw)
    for n, line in _req_lines(text):
        why = _req_line(line, path.parent, root, seen, depth)
        if why:
            raise Refusal(PIP_REQ_LINE.format(path=shown, n=n, what=why))


def _req_text(raw: bytes) -> str:
    """A requirement file's text exactly as pip decodes it (``auto_decode``), else :data:`REQ_ENCODING`.

    pip takes a byte-order mark first, then a coding line (starting with "#") in the first two lines, then
    the try Python's own locale encoding (a Windows code page unless UTF-8 mode is on). Read the same here
    only: a UTF-8 mark (stripped); or no mark, every coding name in the first two lines UTF-8, and either
    pip takes one of them or the file is ASCII only (every code page reads ASCII the same)."""
    if raw.startswith(codecs.BOM_UTF8):
        body = raw[len(codecs.BOM_UTF8):]
    elif raw.startswith(_OTHER_BOMS):
        raise Refusal(REQ_ENCODING)
    else:
        body = raw
        pip_takes = False
        for line in raw.split(b"\n")[:2]:
            for m in _PIP_CODING.finditer(line):
                try:
                    utf8 = codecs.lookup(m.group(1).decode("ascii")).name == "utf-8"
                except (LookupError, UnicodeDecodeError):
                    utf8 = False
                if not utf8:
                    raise Refusal(REQ_ENCODING)
                pip_takes = pip_takes or line[:1] == b"#"
        if not pip_takes and not body.isascii():
            raise Refusal(REQ_ENCODING)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal(REQ_ENCODING) from None


_COMMENT = re.compile(r"(^|\s+)#.*$")


def _req_lines(text: str):
    """``(first line number, logical line)`` as pip reads a requirement file: a line ending in a backslash
    goes on with the next one, and ``#`` at the start or after a space begins a comment."""
    joined: list[str] = []
    first = 0
    for n, line in enumerate(text.splitlines(), 1):
        if not joined:
            first = n
        if line.endswith("\\") and not _COMMENT.search(line):
            joined.append(line[:-1])
            continue
        joined.append(line)
        logical = _COMMENT.sub("", "".join(joined)).strip()
        joined = []
        if logical:
            yield first, logical
    if joined:
        logical = _COMMENT.sub("", "".join(joined)).strip()
        if logical:
            yield first, logical


def _resolve_req_option(word: str) -> str | None:
    """The requirement-file option *word* names (pip takes an unambiguous start), or None."""
    if word.startswith("--"):
        name = word.split("=", 1)[0].lower()
        if name in _REQ_FILE_OPTIONS:
            return name
        found = [o for o in _REQ_FILE_OPTIONS if o.startswith(name)] if len(name) > 2 else []
        return found[0] if len(found) == 1 else None
    if len(word) >= 2 and word[0] == "-":
        return _REQ_FILE_SHORT.get(word[1])
    return None


def _req_line(line: str, here: Path, root: Path, seen: set, depth: int) -> str:
    """"" when one logical requirement-file line is allowed in a try, else what it has (plain words).
    Paths in it are judged from the try folder and from the file's own folder: both must stay inside."""
    import shlex
    if "${" in line:
        return _ENV_SETTING
    tokens = line.split()
    cut = next((i for i, t in enumerate(tokens) if t.startswith("-")), len(tokens))
    spec, rest = " ".join(tokens[:cut]), " ".join(tokens[cut:])
    try:
        options = shlex.split(rest, posix=True) if rest else []
    except ValueError:
        return "words BuildBot can't read the way pip does"
    bases = [root, here]
    if spec:
        why = _requirement_ok(spec, bases, root)
        if why:
            return why
        i = 0
        while i < len(options):                       # after a requirement: only its --hash values
            if _resolve_req_option(options[i]) != "--hash":
                return f"the option {options[i].split('=', 1)[0][:40]}"
            i += 1 if "=" in options[i] else 2
        return ""
    word = options[0]
    opt = _resolve_req_option(word)
    if opt in ("--requirement", "--constraint", "--editable"):
        if word.startswith("--"):
            value, used = (word.split("=", 1)[1], 1) if "=" in word else (
                options[1] if len(options) > 1 else "", 2)
        else:
            value, used = (word[2:], 1) if len(word) > 2 else (options[1] if len(options) > 1 else "", 2)
            if value.startswith("="):
                return f"{word[:2]} with a value that starts with ="
        if len(options) > used:
            return f"more after {opt}"
        if opt == "--editable":
            return "" if _local_ok(value, bases, root) else f"-e {value[:80]}"
        try:
            _req_file(value, here, root, seen, depth + 1)
        except Refusal as r:
            if r.said == REQ_ENCODING:
                raise                                 # said as it is, whichever file it was
            return r.said
        return ""
    if opt in _PIP_INDEX:
        return f"{opt} (where pip looks for packages comes only from the README, on a card)"
    return f"the option {word.split('=', 1)[0][:40]}"


def _pip_args(args: list[str], cwd: Path, root: Path) -> None:
    """``python -m pip``: only into the app's own .venv. The very first word is the subcommand, one of
    :data:`PIP_COMMANDS` (or ``--version`` alone): pip's settings, cache, debug and the rest are never run.
    Options that aim pip at another Python or place are refused; one that writes a file (a log, a report, a
    download folder) only inside the try; requirement and constraint files only inside the try, holding only
    plain requirements (:func:`_req_file`); where pip looks for packages never from a try."""
    if args == ["--version"]:
        return
    if not args or args[0] not in PIP_COMMANDS:
        raise Refusal(PIP_COMMAND_LINE.format(word=(args[0][:40] if args else "nothing")))
    command, rest = args[0], args[1:]
    seen: set = set()
    i = 0
    plain = False                                     # after "--": every word is a requirement
    while i < len(rest):
        a = rest[i]
        if plain or not a.startswith("-") or a == "-":
            if command in _PIP_REQ_COMMANDS:
                why = _requirement_ok(a, [cwd], root)
                if why:
                    raise Refusal(PIP_REQ_PATH.format(what=why))
            i += 1
            continue
        if a == "--":
            plain = True
            i += 1
            continue
        if a.startswith("--"):
            flag, kind = _pip_long(a)
            if a.split("=", 1)[0].lower() in ("--global", "--system"):
                kind = "never"
            value = a.split("=", 1)[1] if "=" in a else (rest[i + 1] if i + 1 < len(rest) else "")
            step = 1 if "=" in a else 2
            if kind == "never":
                raise Refusal(PIP_OUTSIDE.format(flag=flag))
            if kind == "index":
                raise Refusal(PIP_INDEX.format(flag=flag))
            if kind == "inside":
                if not value or not _inside(_real(Path(value) if os.path.isabs(value) else cwd / value), root):
                    raise Refusal(PIP_WRITE.format(flag=flag))
                i += step
                continue
            if kind in ("reqfile", "editable"):
                _req_value(value, kind, cwd, root, seen)
                i += step
                continue
            i += 2 if ("=" not in a and flag in _PIP_LONG_VALUE) else 1
            continue
        took = False
        for j, ch in enumerate(a[1:], 1):
            if ch == "t":
                raise Refusal(PIP_OUTSIDE.format(flag="-t (--target)"))
            if ch in "if":
                raise Refusal(PIP_INDEX.format(flag="-" + ch))
            if ch in _PIP_SHORT_VALUE:
                attached = a[j + 1:]
                value = attached or (rest[i + 1] if i + 1 < len(rest) else "")
                took = not attached
                if ch in "dw":
                    value = value[1:] if value.startswith("=") else value
                    if not value or not _inside(_real(Path(value) if os.path.isabs(value) else cwd / value), root):
                        raise Refusal(PIP_WRITE.format(flag="-" + ch))
                if ch in "rce":
                    if value.startswith("="):
                        raise Refusal(PIP_REQ_OUTSIDE.format(what=f"-{ch} with a value that starts with ="))
                    _req_value(value, "editable" if ch == "e" else "reqfile", cwd, root, seen)
                break                                  # the rest of the word is its value
        i += 2 if took else 1


#: The one standard-library module a try may run with -m: a plain web server for the app's own files.
HTTP_SERVER = "http.server"
HTTP_SERVER_LINE = ("python -m http.server runs only as a server of the try, on 127.0.0.1 and its own port: "
                    "start_server with python -m http.server {port} --bind 127.0.0.1 (and --directory inside the "
                    "app's folder).")
MODULE_LINE = ("BuildBot runs python -m only for pip, the app's own modules and what is installed in its own "
               ".venv; {module} is not one of them, so it was not run.")
STDLIB_LINE = ("{module} is part of Python itself, not the app, so python -m {module} is not run in a try "
               "(only pip, and http.server as a server on 127.0.0.1; venv is the Core's own step).")


def site_packages(folder: Path) -> list[Path]:
    """The try's own .venv site-packages folders."""
    v = venv_dir(folder)
    if IS_WINDOWS:
        return [v / "Lib" / "site-packages"]
    return sorted((v / "lib").glob("python*/site-packages"))


def _stdlib(module: str) -> bool:
    import sys
    top = module.split(".", 1)[0]
    names = getattr(sys, "stdlib_module_names", frozenset())
    return top in names or top in sys.builtin_module_names or _frozen_or_built_in(module)


def _frozen_or_built_in(module: str) -> bool:
    """*module*, or a package above it, is one Python carries inside itself: a frozen module (spec origin
    "frozen", like ``__phello__``, which ``sys.stdlib_module_names`` leaves out) or a built-in one
    ("built-in"). Python finds those before any file, so a file of that name in the try is not what runs.
    Asked of Python's own frozen and built-in finders only: nothing is imported, no folder is searched."""
    from importlib.machinery import BuiltinImporter, FrozenImporter
    parts = module.split(".")
    for n in range(1, len(parts) + 1):
        name = ".".join(parts[:n])
        for finder in (FrozenImporter, BuiltinImporter):
            try:
                spec = finder.find_spec(name)
            except Exception:  # noqa: BLE001 - a finder that can't answer: treated as Python's own
                return True
            if spec is not None and getattr(spec, "origin", None) in ("frozen", "built-in"):
                return True
    return False


def module_home(module: str, cwd: Path, place: Place) -> Path | None:
    """Where ``python -m <module>`` would find *module*, by path only (nothing is imported by the Core):
    the folder it runs in first (Python puts it first for -m), then the try's own .venv site-packages.
    The real path of the file or package folder, or None when it is not found in those places."""
    parts = module.split(".")
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x or "") for x in parts):
        return None
    top = parts[0]
    for base in [cwd, *site_packages(Path(place.folder))]:
        if not base.is_dir():
            continue
        pkg = base / top
        if pkg.is_dir() and any((pkg / f"__init__{ext}").is_file() for ext in (".py", ".pyc")):
            return _real(pkg)
        # A folder with no __init__ is only part of a namespace: Python goes on looking, and may find the
        # module somewhere else (a path a .pth added), so it is not counted as found here.
        for c in [base / (top + ext) for ext in (".py", ".pyc", ".pyw")]:
            if c.is_file():
                return _real(c)
        # an extension module (top.cp311-win_amd64.pyd and the like)
        for c in base.glob(top + ".*"):
            if c.suffix.lower() in (".pyd", ".so"):
                return _real(c)
    return None


def _module_args(module: str, rest: list[str], cwd: Path, root: Path, place: Place | None) -> None:
    """``python -m <module> <rest>``: pip (only into the .venv), ``http.server`` (127.0.0.1, its own port),
    or a module that is a file of the try (its folder, or its own .venv's site-packages). ``venv`` only as the
    Core's own step (``python -m venv .venv``)."""
    module = str(module or "").strip()
    top = module.split(".", 1)[0]
    if top == "pip":
        if module not in ("pip", "pip.__main__"):
            raise Refusal("Run pip as python -m pip.")      # pip's inner modules would skip its checks here
        _pip_args(rest, cwd, root)
        return
    if top == "venv":
        raise Refusal(VENV_WHERE)
    if module == HTTP_SERVER:
        _http_server_args(rest, cwd, root, place)
        return
    if _stdlib(module):
        raise Refusal(STDLIB_LINE.format(module=module))
    if place is None:
        raise Refusal(MODULE_LINE.format(module=module))
    home = module_home(module, cwd, place)
    folder = _real(Path(place.folder))
    if home is None or not _inside(home, folder):
        raise Refusal(MODULE_LINE.format(module=module))
    venv = _real(venv_dir(Path(place.folder)))
    if _inside(home, venv) and not any(_inside(home, _real(sp)) for sp in site_packages(Path(place.folder))
                                       if sp.is_dir()):
        raise Refusal(MODULE_LINE.format(module=module))


def _http_server_args(rest: list[str], cwd: Path, root: Path, place: Place | None) -> None:
    port = place.port if place is not None else None
    if not port:
        raise Refusal(HTTP_SERVER_LINE.format(port="<its port>"))
    line = HTTP_SERVER_LINE.format(port=port)
    seen_port = bind = False
    i = 0
    while i < len(rest):
        a = rest[i]
        flag = a.split("=", 1)[0]
        value = a.split("=", 1)[1] if "=" in a else (rest[i + 1] if i + 1 < len(rest) else "")
        step = 1 if "=" in a else 2
        if flag in ("-b", "--bind"):
            if value != "127.0.0.1":
                raise Refusal(line)
            bind, i = True, i + step
            continue
        if flag in ("-d", "--directory"):
            if not value or not _inside(_real(Path(value) if os.path.isabs(value) else cwd / value), root):
                raise Refusal(line)
            i += step
            continue
        if flag in ("-p", "--protocol"):
            if value not in ("HTTP/1.0", "HTTP/1.1"):
                raise Refusal(line)
            i += step
            continue
        if not a.startswith("-") and a.isdigit() and not seen_port:
            if int(a) != int(port):
                raise Refusal(line)
            seen_port, i = True, i + 1
            continue
        raise Refusal(line)                              # --cgi, -h, another word: not this server
    if not (seen_port and bind):
        raise Refusal(line)


_NODE_INLINE = ("-e", "--eval", "-p", "--print", "-pe", "-ep")
_NODE_VALUE = frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader", "-C", "--conditions",
                         "--env-file", "--title", "--watch-path", "--inspect-port", "--disable-warning"})
_NODE_BARE = re.compile(r"^--(?:inspect|inspect-brk|watch|no-[\w-]+|experimental-[\w-]+|trace-[\w-]+|"
                        r"enable-source-maps|preserve-symlinks(?:-main)?|expose-gc|harmony[\w-]*|"
                        r"abort-on-uncaught-exception|openssl-legacy-provider|use-openssl-ca|"
                        r"pending-deprecation|throw-deprecation|unhandled-rejections|max-[\w-]+|"
                        r"stack-size|heapsnapshot-[\w-]+|frozen-intrinsics|version|help)$")


def _node_args(args: list[str], cwd: Path, root: Path) -> None:
    """Node's options: inline code (-e, -p, --eval, --print) and ``--run`` refused; a script inside the try."""
    i = 0
    while i < len(args):
        a = args[i]
        flag = a.split("=")[0]
        if a == "--":
            i += 1
            break
        if a == "-" or flag in _NODE_INLINE or (a.startswith("-") and not a.startswith("--")
                                                 and len(a) > 1 and set(a[1:]) & {"e", "p"}):
            raise Refusal(INLINE_LINE.format(flag=flag if flag != "-" else "- (stdin)"))
        if flag == "--run":
            raise Refusal("node --run runs a package.json script; use npm run.")
        if flag in _NODE_VALUE:
            value = a.split("=", 1)[1] if "=" in a else (args[i + 1] if i + 1 < len(args) else "")
            if flag in ("-r", "--require", "--import", "--loader", "--experimental-loader", "--env-file") \
                    and re.search(r"[\\/]|^\.", value):
                _script_inside(value, cwd, root)
            i += 1 if "=" in a else 2
            continue
        if a.startswith("-"):
            if "=" in a and flag not in _NODE_INLINE:
                i += 1
                continue
            if _NODE_BARE.match(flag) or a in ("-i", "--interactive", "-v", "-h"):
                i += 1
                continue
            raise Refusal(f"BuildBot doesn't pass the Node.js option {flag}.")
        break
    if i < len(args):
        _script_inside(args[i], cwd, root)


_NPM_NEVER = frozenset({"explore", "edit", "link", "ln", "publish", "unpublish", "adduser", "add-user", "login",
                        "logout", "owner", "token", "profile", "team", "access", "deprecate", "dist-tag", "org",
                        "hook", "star", "unstar", "set", "unlink"})


_NPM_VALUE = frozenset({"--prefix", "-C", "--workspace", "-w", "--registry", "--cache", "--loglevel", "--tag",
                        "--omit", "--include", "--install-strategy", "--userconfig", "--location", "--package",
                        "-p"})


def _npm_args(tool: str, args: list[str], cwd: Path, root: Path) -> None:
    """npm and npx: nothing for the whole computer (-g, user settings) and no shell lines (-c, --call)."""
    for i, a in enumerate(args):
        flag = a.split("=")[0]
        value = a.split("=", 1)[1] if "=" in a else (args[i + 1] if i + 1 < len(args) else "")
        if flag in ("-g", "--global") or (flag == "--location" and value in ("global", "user")):
            raise Refusal(f"{tool} installs only into the app's own folder, so a global install is not run.")
        if flag in ("-c", "--call") or flag.startswith(("--script-shell", "--shell")):
            raise Refusal(f"{tool} {flag} runs a shell line, and BuildBot doesn't use a shell.")
        if flag in ("--userconfig", "--globalconfig", "--global-style"):
            raise Refusal(f"{tool} changes only the app's own settings.")
        if flag in ("--prefix", "-C") and value and not _inside(
                _real(Path(value) if os.path.isabs(value) else cwd / value), root):
            raise Refusal(f"{tool} works only inside the app's folder.")
        if a == "--":
            break
    if tool == "npm":
        words, skip = [], False
        for a in args:
            if skip:
                skip = False
            elif a == "--":
                break
            elif a.startswith("-"):
                skip = "=" not in a and a in _NPM_VALUE
            else:
                words.append(a)
        sub = words[0].lower() if words else ""
        if sub in _NPM_NEVER:
            raise Refusal(f"npm {sub} is not run in a try (it changes things outside the app or runs a shell).")
        if sub in ("config", "c") and (words[1:2] or ["list"])[0] not in ("get", "list", "ls"):
            raise Refusal("npm settings are not changed by a try.")


COMPOSE_OK = frozenset({"up", "start", "stop", "restart", "down", "ps", "logs", "build", "pull", "config", "images",
                        "top", "port", "version", "exec", "pause", "unpause", "kill", "rm", "create", "events"})
_COMPOSE_VALUE = frozenset({"--env-file", "--profile", "--ansi", "--progress", "--parallel"})
_COMPOSE_OURS = frozenset({"-f", "--file", "-p", "--project-name"})


def compose_rest(rest: list[str]) -> list[str]:
    """The arguments after ``docker compose`` with the model's own ``-f``/``-p`` taken off (the Core sets its
    project and files); only subcommands that stay inside the app's own project; :class:`Refusal` otherwise."""
    out: list[str] = []
    i = 0
    sub = ""
    while i < len(rest):
        a = rest[i]
        flag = a.split("=")[0]
        if not sub:
            if flag in _COMPOSE_OURS:
                i += 1 if "=" in a else 2
                continue
            if flag == "--project-directory":
                raise Refusal("The Core runs docker compose in the app's own folder (no --project-directory).")
            if flag in _COMPOSE_VALUE:
                out.append(a)
                if "=" not in a and i + 1 < len(rest):
                    out.append(rest[i + 1])
                i += 1 if "=" in a else 2
                continue
            if a.startswith("-"):
                out.append(a)
                i += 1
                continue
            sub = a.lower()
            if sub not in COMPOSE_OK:
                raise Refusal(f"docker compose {a} is not run in a try (it could reach outside the app's own "
                              "containers).")
        out.append(a)
        i += 1
    if not sub:
        raise Refusal("Give the docker compose command (for example up -d).")
    return out


__all__ = ["CWD_LINE", "DENIED", "HINT", "HTTP_SERVER", "INLINE_LINE", "IS_WINDOWS", "NO_VENV", "Place", "Plan",
           "Refusal", "Request", "SHELLS", "SHELL_LINE", "VENV_CHANGED", "VENV_PIP_SETTINGS", "YOUR_TURN",
           "checked_programs", "complete",
           "compose_rest", "cwd_of", "decide", "join", "known_docker", "known_node", "module_home", "npm_cli",
           "parse_line", "parse_words", "request_from", "resolve", "sha256", "site_packages", "venv_bin", "venv_dir",
           "venv_pip_settings", "venv_python", "venv_record"]
