# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (adapted)
r"""The Core's exec broker for Toolsmith: Toolsmith asks, the Core runs (09 section 3, point 2).

Toolsmith's process has no shell and starts no program. It asks the Core over
its Toolsmith-token routes, the Core checks Toolsmith's zone (``trials``,
L4's ``toolsmith_zone.check``) and wraps the program in L4's ``HookGuard``,
and only then this broker runs it:
- **no shell** (D60): every request is one program and its arguments
  (``{program, args, cwd}``, or one plain line read by ``allowlist.parse_line``),
  started directly as an argument list. Nothing is ever handed to ``bash``,
  ``cmd`` or PowerShell, and a ``.cmd`` or ``.bat`` file is never the program
  (Windows would start it through ``cmd.exe``; ``steward.noshell``).
- **an allowlist** (D61, ``allowlist.decide``): the try's own Python (its
  ``.venv``, made by the Core, :meth:`Broker.make_venv`) and ``python -m pip``,
  the known ``node``, ``npm`` and ``npx``, ``docker compose`` (the Docker wall
  path in ``trials``), and native programs from the download Drawbridge
  checked, matched by the resolved file. Anything else is not run; the
  reply's ``not_run`` names the exact program and arguments.
- :meth:`Broker.run` (a program that ends) or :meth:`Broker.start` (a server
  it keeps), in the try folder or a folder inside it, with no console window,
  **each inside its own Windows job object** (Steward's ``steward.jobs.Job``,
  lane L1), so a stop ends everything the program started. A program Windows
  can't put in a job is stopped at once and refused, as Steward does.
- the environment is built here: no ``PUFFPAI_*``, ``PIP_*`` or ``GIT_DIR``-style names from
  the Core's own (a ``PIP_TARGET`` or ``PIP_USER`` there would aim pip outside the
  app's .venv), ``GIT_CEILING_DIRECTORIES`` at the try folder's parent (``steward.gitfence``),
  and no Toolsmith token (the Core's own process never holds one
  in its environment), a short TEMP of the try's own (:func:`make_temp`,
  ``<bot>\t\<id>``: under a deep folder npm stops answering at Windows'
  260-character limit), the Bridge proxy with the try's own credential;
- **the record of what ran** (:meth:`Broker.record`): each program that ran to
  a good end, or started as a server, as the Core names it
  (``packages.plan_cover.program_of``), its arguments and folder. It lives in
  the Core's memory only; no Toolsmith route writes it. The plan's Yes covers a
  command the Core starts later from the app's manifest only when it is on this
  record (``plan_cover.commands_outside``);
- timing is data (``settings.timing``): a program waits ``command_wait_s``,
  and up to ``long_command_s`` while it keeps printing; a build or install up
  to ``build_command_s``, stopped after ``build_quiet_s`` with no output.
  Output goes to a log file in the try's scratch folder, which the broker watches;
- before anything starts, the broker's own text check (lane I2a, after the zone's): a program line that
  stops a process outside this try's job objects, or by name, or changes the computer outside the try,
  never runs. :meth:`Broker.stop` ends a server by its job (Toolsmith's ``stop_server``, the only way a
  try stops anything);
- :func:`fetch_repo` downloads a candidate (``git clone --depth 1``) the same
  way, so Toolsmith's process never runs git itself.

v1 sources in ``toolsmith_run.py``: the command helpers (now
``toolsmith.commands``), ``download`` :2507 (the clone :2573).

Honest limits: the job-object CPU check (v1 ``job_work``) is not here, so a
quiet build that still works is stopped after ``build_quiet_s``. A script file
the try runs (``python app.py``, an npm script, a program from its download)
runs as the owner's Windows user and can do what she can (01 "Toolsmith's
zone", honest limit); npm runs a package's own scripts through npm's script
shell.
"""

from __future__ import annotations

import ctypes
import json
import queue
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...steward import gitfence, jobs, noshell
from ..toolsmith.commands import is_build, never_ends
from . import allowlist
from .allowlist import Plan, Refusal

TAIL_CHARS = 4000
UNCHECKED = "PuffPAI could not check that command, so it was refused."
NOT_HELD = ("Windows could not hold that command in a job of its own, so it was stopped "
            "(PuffPAI could not stop or watch all of it).")
HAS_VENV = "It has its own Python already (.venv)."
NEEDS_WALL = "Docker apps start only behind the Docker wall, through the try's run."
VENV_TIMEOUT_S = 600.0
_PROXY_NAMES = frozenset({"http_proxy", "https_proxy", "all_proxy", "no_proxy", "npm_config_proxy",
                          "npm_config_https_proxy", "node_use_env_proxy"})
#: Settings from the Core's own environment that would point a try's Python or Node.js at code or places
#: outside the app (like their own flags would): never passed to a try.
_NEVER_PASSED = frozenset({"pythonpath", "pythonhome", "pythonstartup", "pythonuserbase", "pythoninspect",
                           "node_options", "node_path"})


def base_env() -> dict:
    """The Core's environment without its own names (``PUFFPAI_*``) or the settings that tell git which
    repository to use (``GIT_DIR`` and the rest, ``steward.gitfence``)."""
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith("PUFFPAI") and k.lower() not in gitfence.GIT_LOCATORS}


def _in_its_real_folder(path: Path | str) -> str:
    """The real folder of *path*, with the file's own name: the file itself is not followed if it is a link."""
    return os.path.join(os.path.realpath(os.path.dirname(os.fspath(path))), os.path.basename(os.fspath(path)))


#: A TEMP longer than this is given in its short (8.3) form when Windows has one: npm stops answering
#: when TEMP passes about 240 characters (seen 10-22), and npm and pip add their own folders under it.
SHORT_TEMP = 120


def _short_path(path: Path) -> str:
    """The Windows short (8.3) form of an existing folder, or the path as it is."""
    if os.name != "nt":
        return str(path)
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        buf = ctypes.create_unicode_buffer(32768)
        n = k32.GetShortPathNameW(str(path), buf, 32768)
        return buf.value if 0 < n < 32768 and buf.value and len(buf.value) < len(str(path)) else str(path)
    except (OSError, AttributeError, ValueError):
        return str(path)


def make_temp(bot: Path) -> Path:
    r"""A new, empty TEMP folder for one try, ``<bot>\t\<6 hex>``, made by the Core (removed by
    :func:`remove_temp`)."""
    root = Path(bot) / "t"
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        folder = root / secrets.token_hex(3)
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise OSError("no free name for a try's TEMP folder")


def temp_env_path(folder: Path) -> str:
    """What TEMP and TMP say for *folder*: as it is, or its short form when it is long."""
    text = str(folder)
    return _short_path(Path(folder)) if len(text) > SHORT_TEMP else text


def remove_temp(folder: Path | None, bot: Path) -> bool:
    r"""Remove a try's TEMP folder: only one directly under ``<bot>\t``, never through a link."""
    if folder is None:
        return False
    from ...packages import fsutil
    root = Path(bot) / "t"
    folder = Path(folder)
    try:
        if os.path.normcase(str(folder.parent.resolve())) != os.path.normcase(str(root.resolve())):
            return False
    except OSError:
        return False
    if not os.path.lexists(folder):
        return True
    if fsutil.is_link(folder):
        fsutil._remove_link(str(folder))
        return not os.path.lexists(folder)
    return bool(fsutil.remove_folder(folder, root))


#: The exact name :func:`make_temp` gives a try's TEMP folder (``secrets.token_hex(3)``).
_TEMP_NAME = re.compile(r"^[0-9a-f]{6}$")


def clean_temps(bot: Path) -> int:
    r"""At Core start (no try is open yet): remove the try TEMP folders a run before left in ``<bot>\t``.

    Only real folders named the way :func:`make_temp` names them; anything else there (another name, a
    file, a link or junction) is left as it is. A ``t`` that is itself a link is never gone into. Nothing
    is followed through a link while deleting (``fsutil.remove_folder``). Returns how many went."""
    from ...packages import fsutil
    root = Path(bot) / "t"
    if not os.path.lexists(root) or fsutil.is_link(root) or not root.is_dir():
        return 0
    removed = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        return 0
    for entry in entries:
        path = Path(entry.path)
        if not _TEMP_NAME.match(entry.name) or fsutil.is_link(path) or not entry.is_dir(follow_symlinks=False):
            continue
        removed += int(bool(fsutil.remove_folder(path, root)))
    return removed


def end_job(job: Any) -> None:
    """End a job: everything in it. Never a pid-only kill once our own process has gone (a reused pid
    could be another program's)."""
    if job is None:
        return
    if job.contained or job.proc.poll() is None:
        job.terminate(wait_s=10)
    job.close()


def spawn(argv: list[str], cwd: Path, env: dict, log: Path | None, stdin: Any = None) -> Any:
    """Steward's job object for one program (an argument list, no shell), or None when Windows could not
    hold it in one."""
    kw = {} if stdin is None else {"stdin": stdin}
    job = jobs.Job.spawn(argv, cwd, env, log, **kw)
    if not job.contained:
        end_job(job)
        return None
    return job


def _tail(path: Path, chars: int = TAIL_CHARS) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - chars * 4))
            return fh.read().decode("utf-8", errors="replace")[-chars:]
    except OSError:
        return ""


@dataclass
class Started:
    """A server the broker started for a try: its recorded pid and its job."""
    pid: int
    line: str
    log: Path
    port: int | None = None
    job: Any = field(default=None, repr=False)


class Broker:
    """Runs what the Core already checked, in one try folder, with no shell (D60, D61)."""

    def __init__(self, folder: Path, scratch: Path, timing: dict, proxy_env: dict | None = None,
                 checked: dict | None = None, temp: Path | None = None, port: int | None = None) -> None:
        self.folder, self.scratch = Path(folder), Path(scratch)
        self.temp = Path(temp) if temp is not None else None    # the try's own short TEMP (make_temp)
        self.port = port                                         # the try's own port (for the record)
        self._ran: list[dict] = []                                # the record of what ran (record())
        self.timing = timing
        self.proxy_env = dict(proxy_env or {})
        self.checked = dict(checked or {})          # the checked download's native programs: rel -> sha256
        self.venv: dict | None = None               # the .venv the Core made: its interpreter and pyvenv.cfg
        self.node = allowlist.known_node()           # found on the Core's own PATH, never the try's
        self.docker = allowlist.known_docker()
        self.started: list[Started] = []
        self._stopped: list[Started] = []            # stopped by stop_server (their logs stay readable)
        self._running: list[Any] = []
        self._lock = threading.Lock()

    def env(self, port: int | None = None) -> dict:
        env = {k: v for k, v in base_env().items()
               if k.lower() not in _PROXY_NAMES and k.lower() not in _NEVER_PASSED and not k.upper().startswith("PIP_")}
        self.scratch.mkdir(parents=True, exist_ok=True)
        temp = self.temp if self.temp is not None else self.scratch
        temp.mkdir(parents=True, exist_ok=True)
        env["TEMP"] = env["TMP"] = temp_env_path(temp)
        env.update(self.proxy_env)
        # pip installs only into a virtual environment (the app's own .venv), never into the owner's own
        # Python on the PATH (03 3.3: no global installs). Found by the live adopt run (lane I2a).
        env["PIP_REQUIRE_VIRTUALENV"] = "1"
        if self.venv:
            key = next((k for k in env if k.upper() == "PATH"), "PATH")
            env["VIRTUAL_ENV"] = str(allowlist.venv_dir(self.folder))
            env[key] = str(allowlist.venv_bin(self.folder)) + os.pathsep + env.get(key, "")
        if port:
            env["PORT"], env["HOST"] = str(port), "127.0.0.1"
        # git never looks above the try folder (lane I2a part D): the try has its own repository
        # (Trials.check), and a home folder that is a repository is never reached from inside it.
        return gitfence.fence(env, self.folder)

    def place(self, port: int | None = None) -> allowlist.Place:
        return allowlist.Place(folder=self.folder, venv=self.venv, checked=self.checked, node=self.node,
                               docker=self.docker, port=port)

    # -- the record of what ran (the plan's Yes covers only these, plan_cover.commands_outside) -------------

    def _remember(self, kind: str, planned: Plan) -> None:
        """Add one program that ran to a good end (``run``) or started as a server (``start``)."""
        if planned.kind != "run" or not planned.argv:
            return
        from ...packages import plan_cover
        try:
            folder = os.path.realpath(self.folder)
            python = self._own_python() if self.venv else ""
            first = os.path.realpath(planned.argv[0])
            if python and _in_its_real_folder(planned.argv[0]) == python:
                first = python
            word, args = plan_cover.program_of([first, *planned.argv[1:]], folder, python, self.node)
            if word.startswith("other:"):
                return
            entry = {"kind": kind, "program": word, "args": list(args),
                     "cwd": plan_cover.rel_cwd(os.path.relpath(os.path.realpath(planned.cwd), folder))}
            if word.startswith("pkg:"):
                entry["sha"] = allowlist.sha256(Path(first))
        except (OSError, ValueError):
            return                                   # a record that can't be made covers nothing (fail closed)
        with self._lock:
            if entry not in self._ran:
                self._ran.append(entry)

    def _own_python(self) -> str:
        """The try's own Python as a path of its own. On Linux and Mac that file is a link to the base Python: followed
        to the end, the base Python would count as the try's own in the record."""
        return _in_its_real_folder(allowlist.venv_python(self.folder))

    def record(self) -> dict:
        """A copy of the record: the try's own values and every command that ran (never written by a route)."""
        with self._lock:
            commands = [dict(c, args=list(c["args"])) for c in self._ran]
        return {"folder": os.path.realpath(self.folder),
                "python": self._own_python() if self.venv else "",
                "node": self.node, "port": self.port, "commands": commands}

    def own_pids(self) -> set[int]:
        """Every process in this try's own job objects (its servers and the command running now). Only a job
        still held counts: a closed job's number could belong to another program by now."""
        with self._lock:
            held = [s.job for s in self.started if s.job is not None] + list(self._running)
        out: set[int] = set()
        for job in held:
            try:
                if job.contained:
                    out.update(int(p) for p in job.pids())
            except Exception:  # noqa: BLE001 - a job that can't be read adds no pid (fail closed)
                continue
        return out

    def refusal(self, line: str) -> str:
        """The broker's own text check, after the zone's (09 section 3): a program line that stops a process
        outside this try's jobs, or changes the computer outside the try, never runs. "" = it may go on."""
        try:
            from .. import toolsmith_zone as tz
            return (tz.process_refusal(line, self.own_pids())
                    or tz.machine_refusal(line, folder=str(self.folder), cwd=str(self.folder)))
        except Exception:  # noqa: BLE001 - a check that breaks refuses
            return UNCHECKED

    def plan(self, request: Any, port: int | None = None) -> Plan | dict:
        """What *request* would run (an ``allowlist.Plan``), or the reply saying it is not run: always the one
        structured ``not_run`` record (``allowlist.Refusal.reply``). *port*: a server's port (start_server)."""
        req = None
        try:
            req = allowlist.request_from(request)
            refused = self.refusal(req.line())
            if refused:
                raise Refusal(refused, kind=allowlist.DENIED)
            return allowlist.decide(req, self.place(port))
        except Refusal as exc:
            return allowlist.complete(exc, req, self.folder, request).reply()
        except Exception:  # noqa: BLE001 - a decision that breaks refuses (fail closed)
            return allowlist.complete(Refusal(UNCHECKED, kind=allowlist.DENIED), req, self.folder, request).reply()

    def refused(self, said: str, request: Any = None, planned: Plan | None = None) -> dict:
        """A refusal after the plan (a batch file, a compose program outside the wall) as the same record."""
        req = None
        if planned is not None:
            words = allowlist.parse_words(planned.line)
            req = allowlist.Request(program=words[0] if words else "", args=words[1:], cwd="")
        return allowlist.complete(Refusal(said, kind=allowlist.DENIED), req, self.folder, request).reply()

    def run(self, request: Any = None, *, planned: Plan | None = None) -> dict:
        """Run one program to its end: ``{ok, exit, output, said}``. A request that is not run has no ``exit``."""
        if planned is None:
            planned = self.plan(request)
            if isinstance(planned, dict):
                return planned
            if planned.kind == "compose":
                return self.refused(NEEDS_WALL, request, planned)
        if planned.kind == "venv":
            return self.make_venv(planned.venv_flags)
        got = self.core_run(planned.argv, planned.cwd, planned.line)
        if got.get("refused") and "not_run" not in got:
            return self.refused(str(got.get("said") or ""), request, planned)
        if got.get("ok") and got.get("exit") == 0:
            self._remember("run", planned)
        return got

    def make_venv(self, flags: list[str] | None = None) -> dict:
        """The try's own Python (``.venv`` at its top), made by the Core's base Python (D51 ``{"venv": true}``).
        The Core records its interpreter and ``pyvenv.cfg``; a changed one is not run (``allowlist``)."""
        if self.venv and allowlist.venv_record(self.folder) == self.venv:
            return {"ok": True, "exit": 0, "output": "", "said": HAS_VENV, "venv": True}
        from ...steward.steward import base_python
        target = allowlist.venv_dir(self.folder)
        if allowlist._is_link(target):
            return {"ok": False, "said": "Its .venv is a link to somewhere else, so it was not used.", "refused": True}
        self.venv = None
        # -I and the scratch folder as cwd: nothing in the try folder is imported by the Core's base Python.
        argv = [base_python(), "-I", "-m", "venv", "--clear", *[f for f in (flags or []) if f != "--clear"],
                str(target)]
        self.scratch.mkdir(parents=True, exist_ok=True)
        got = self.core_run(argv, self.scratch, "python -m venv .venv", limit_s=VENV_TIMEOUT_S)
        if got.get("ok"):
            self.venv = allowlist.venv_record(self.folder)
            if not self.venv:
                return {"ok": False, "exit": got.get("exit"), "output": got.get("output", ""),
                        "said": "Its own Python (.venv) could not be read after it was made."}
            got["said"], got["venv"] = "Made its own Python (.venv).", True
        return got

    def pip_settings_refusal(self, argv: list[str]) -> str:
        """Just before the try's own Python (and so its pip) starts: "" unless its .venv holds a ``pip.ini``
        or ``pip.conf`` (pip's site settings, which a try script could have written since the plan). A path
        that can't be compared counts as the try's Python (fail closed)."""
        if not argv:
            return ""
        try:
            ours = os.path.normcase(os.path.realpath(argv[0])) == os.path.normcase(
                os.path.realpath(allowlist.venv_python(self.folder)))
        except (OSError, ValueError, TypeError):
            ours = True
        name = allowlist.venv_pip_settings(self.folder) if ours else ""
        return allowlist.VENV_PIP_SETTINGS.format(name=name[:40]) if name else ""

    def core_run(self, argv: list[str], cwd: Path, line: str, *, limit_s: float | None = None) -> dict:
        """Run an argument list the Core decided on (never through a shell) to its end, in its own job."""
        settings = self.pip_settings_refusal(argv)
        if settings:
            return {"ok": False, "output": "", "said": settings, "refused": True}
        t = self.timing
        build = is_build(line)
        limit = float(limit_s or (t["build_command_s"] if build else t["long_command_s"]))
        quiet = float(limit_s or (t["build_quiet_s"] if build else t["command_wait_s"]))
        forever = never_ends(line)
        log = self.scratch / f"cmd-{secrets.token_hex(4)}.log"
        env = self.env()
        try:
            job = spawn(list(argv), Path(cwd), env, log)
        except noshell.NoShell as exc:
            return {"ok": False, "output": "", "said": exc.reason, "refused": True}
        except OSError as exc:
            return {"ok": False, "exit": None, "output": "", "said": f"It could not start ({type(exc).__name__})."}
        if job is None:
            return {"ok": False, "exit": None, "output": "", "said": NOT_HELD}
        with self._lock:
            self._running.append(job)
        began = last = time.monotonic()
        size = 0
        stopped = ""
        try:
            while job.proc.poll() is None:
                now = time.monotonic()
                try:
                    grown = log.stat().st_size
                except OSError:
                    grown = size
                if grown != size:
                    size, last = grown, now
                if forever and now - began >= float(t["command_wait_s"]):
                    stopped = "It keeps running by itself, so it was stopped; start servers with start_server."
                elif now - began >= limit:
                    stopped = f"It ran longer than {int(limit // 60)} minutes, so it was stopped."
                elif now - last >= quiet:
                    stopped = f"It printed nothing for {int(quiet)} seconds, so it was stopped."
                if stopped:
                    break
                time.sleep(0.2)
        finally:
            end_job(job)                     # whatever the program left behind ends with it
            with self._lock:
                if job in self._running:
                    self._running.remove(job)
        code = job.proc.returncode
        output = _tail(log)
        try:
            log.unlink()
        except OSError:
            pass
        ok = not stopped and code == 0
        said = stopped or ("Done." if ok else f"It ended with exit code {code}.")
        return {"ok": ok, "exit": code, "output": output, "said": said}

    def ask_mcp(self, planned: Plan, tool: str = "", arguments: Any = None) -> dict:
        """D124: start a program of this try as an MCP server over stdio, in its own job with the same env as any
        program. Ask for its tools and, with *tool*, make one real call, then end it. ``{ok, tools, answer, said}``."""
        if planned.kind != "run" or not planned.argv:
            return {"ok": False, "said": "An MCP server is one program and its arguments."}
        try:
            job = jobs.Job.spawn_piped(list(planned.argv), Path(planned.cwd), self.env())
        except noshell.NoShell as exc:
            return {"ok": False, "said": exc.reason}
        except OSError as exc:
            return {"ok": False, "said": f"It could not start ({type(exc).__name__})."}
        if not job.contained:
            end_job(job)
            return {"ok": False, "said": NOT_HELD}
        with self._lock:
            self._running.append(job)
        lines: "queue.Queue[bytes | None]" = queue.Queue()
        tail: list[bytes] = []

        def read_out() -> None:
            for raw in iter(job.proc.stdout.readline, b""):
                lines.put(raw)
            lines.put(None)

        def read_err() -> None:
            for raw in iter(job.proc.stderr.readline, b""):
                tail[:] = (tail + [raw])[-20:]
        for fn in (read_out, read_err):
            threading.Thread(target=fn, daemon=True, name="try-mcp").start()
        ends = time.monotonic() + float(self.timing["command_wait_s"])

        def ask(n: int, method: str, params: dict) -> dict:
            job.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params}) + "\n").encode("utf-8"))
            while True:
                got = lines.get(timeout=max(0.01, ends - time.monotonic()))
                if got is None:
                    raise EOFError
                try:
                    said = json.loads(got)
                except ValueError:
                    continue                         # a line it printed for itself
                if isinstance(said, dict) and said.get("id") == n:
                    return said
        try:
            ask(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "PuffPAI Toolsmith", "version": "1"}})
            job.proc.stdin.write(b'{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
            listed = ((ask(2, "tools/list", {}).get("result") or {}).get("tools")) or []
            names = [str(t.get("name")) for t in listed if isinstance(t, dict) and t.get("name")]
            if not names:
                return {"ok": False, "said": "Its MCP server lists no tools."}
            answer = ""
            if tool:
                if tool not in names:
                    return {"ok": False, "said": f"Its MCP server has no tool {tool}. It has: {', '.join(names[:20])}."}
                said = ask(3, "tools/call", {"name": tool, "arguments": arguments if isinstance(arguments, dict) else {}})
                result = said.get("result") if isinstance(said.get("result"), dict) else {}
                answer = " ".join(str(b.get("text") or "") for b in result.get("content") or [] if isinstance(b, dict)).strip()
                if said.get("error") or result.get("isError") or not answer:
                    return {"ok": False, "said": f"That call gave no real answer: {answer or said.get('error') or 'nothing'}"[:2000]}
        except (queue.Empty, EOFError, OSError, ValueError):
            words = b"".join(tail).decode("utf-8", "replace")[-TAIL_CHARS:]
            return {"ok": False, "said": f"Its MCP server did not answer as one, over stdio. It printed: {words or 'nothing'}"}
        finally:
            end_job(job)                         # whatever it left behind ends with it
            with self._lock:
                if job in self._running:
                    self._running.remove(job)
        self._remember("start", planned)         # the Core starts this command on demand from now on (plan_cover)
        return {"ok": True, "tools": names, "answer": answer[:3000], "said": f"It lists its tools: {', '.join(names[:20])}."}

    def start(self, request: Any, port: int | None, log: Path, *, planned: Plan | None = None) -> dict:
        """Start a server that keeps running: ``{ok, pid, said}``."""
        if planned is None:
            planned = self.plan(request, port)
            if isinstance(planned, dict):
                return planned
        if planned.kind != "run":
            return self.refused("Start Docker apps with run (docker compose up -d), and make .venv with run.",
                                request, planned)
        settings = self.pip_settings_refusal(planned.argv)
        if settings:
            return self.refused(settings, request, planned)
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            job = spawn(planned.argv, planned.cwd, self.env(port), log)
        except noshell.NoShell as exc:
            return self.refused(exc.reason, request, planned)
        except OSError as exc:
            return {"ok": False, "said": f"It could not start ({type(exc).__name__})."}
        if job is None:
            return {"ok": False, "said": NOT_HELD}
        with self._lock:
            self.started.append(Started(pid=job.pid, line=planned.line, log=log, port=port, job=job))
        self._remember("start", planned)
        return {"ok": True, "pid": job.pid, "said": f"Started (process {job.pid}); its output goes to its log. "
                                                    "Stop it with stop_server."}

    def stop(self, pid: int | None = None) -> dict:
        """Stop a server this try started, by its job object (everything it started ends with it): the one
        whose job holds process *pid*, or all of them when *pid* is None. Never a kill by number."""
        with self._lock:
            if pid is None:
                chosen = list(self.started)
            else:
                chosen = [s for s in self.started if s.pid == pid
                          or (s.job is not None and s.job.contained and pid in s.job.pids())]
            for s in chosen:
                self.started.remove(s)
                self._stopped.append(s)
        if not chosen:
            return {"ok": False, "said": "No server this try started has that process number."}
        for s in chosen:
            end_job(s.job)
        return {"ok": True, "said": f"Stopped {len(chosen)} server(s) this try started, and what they started."}

    def log_tail(self, chars: int = TAIL_CHARS) -> str:
        return "\n".join(_tail(s.log, chars) for s in list(self._stopped) + list(self.started))[-chars:]

    def alive(self) -> list[Started]:
        return [s for s in list(self.started) if s.job is not None and s.job.alive()]

    def stop_all(self) -> None:
        """Stop every server and command this try started, by its job."""
        with self._lock:
            started, running = list(self.started), list(self._running)
            self.started, self._running = [], []
        for s in started:
            end_job(s.job)
        for job in running:
            end_job(job)


#: The only places :func:`fetch_repo` clones from: exactly these, https, no port and no user part.
PROJECT_HOSTS = ("github.com", "codeberg.org")


def fetch_repo(url: str, dest: Path, *, timeout_s: float = 600.0) -> str:
    """A shallow clone of a public GitHub or Codeberg project into *dest*, in its own job. "" = done, else the reason.

    The caller has the Bridge's yes for ``toolsmith:search`` to that host first."""
    parts = urllib.parse.urlsplit(str(url or ""))
    if parts.scheme != "https" or parts.netloc not in PROJECT_HOSTS:
        return "Toolsmith downloads projects from github.com and codeberg.org only."
    git = shutil.which("git.exe" if os.name == "nt" else "git")     # the program, never a git.cmd wrapper
    if not git:
        return "Git is not on this computer."
    env = base_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    log = dest.parent / f".clone-{secrets.token_hex(4)}.log"
    # Codeberg: no redirect, git would follow one to a host the Bridge never named. GitHub keeps git's own default, since
    # it answers a renamed project (her pasted old address) with a redirect on github.com itself.
    held = ["-c", "http.followRedirects=false"] if parts.hostname != "github.com" else []
    argv = [git, "-c", "core.longpaths=true", "-c", "core.autocrlf=false", *held, "clone", "--depth", "1",
            "--single-branch", "--no-tags", "--", url, str(dest)]
    try:
        job = spawn(argv, dest.parent, env, log)
    except OSError as exc:
        return getattr(exc, "reason", "") or f"Git could not start ({type(exc).__name__})."
    if job is None:
        return NOT_HELD
    try:
        try:
            code = job.proc.wait(timeout=timeout_s)
        except Exception:  # noqa: BLE001 - subprocess.TimeoutExpired
            return "The download took longer than 10 minutes."
    finally:
        end_job(job)
        out = _tail(log, 2000)
        try:
            log.unlink()
        except OSError:
            pass
    if code != 0:
        last = out.strip().splitlines()
        return f"The download failed: {last[-1][:200]}" if last else "The download failed."
    return ""


__all__ = ["Broker", "HAS_VENV", "NEEDS_WALL", "NOT_HELD", "SHORT_TEMP", "Started", "TAIL_CHARS", "UNCHECKED",
           "base_env", "clean_temps", "end_job", "fetch_repo", "make_temp", "remove_temp", "spawn", "temp_env_path"]
