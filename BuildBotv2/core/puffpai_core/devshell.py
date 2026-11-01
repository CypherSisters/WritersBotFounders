# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/developer_terminal.py (adapted): the done line and its
# parsing, busy tracking (a line while a command runs is typed input, never logged as text), the idle reaper,
# the open cap, close-all at stop. Changed for v2: a Job Object per shell (steward.jobs) instead of a tree
# kill, separate out/err streams, the owner's home folder, audit lines with only length and first word.
"""The Developer page's command line: a FULL shell on the owner's own computer (2026-10-06).

No Yes before each command. What gates it: her owner login on a paired device (the route group
``api.groups.dev``), the idle timeout, the open cap, and the audit lines. What is typed here is not checked
by the Bridge; the Desktop says so. Chat's own command running keeps its per-command Yes; nothing here
touches it or can answer one of its questions.

One shell (PowerShell or cmd, on Linux and Mac bash or sh, by full path, never looked up on PATH, on pipes as
on Windows, no terminal) is one persistent process in a job the Core makes
(``steward.jobs.Job.spawn_piped``), so the folder and variables carry from one command
to the next, and ending the job ends everything the shell started. It starts in the owner's home folder
with the Core's own environment minus every ``PUFFPAI*`` name and anything holding one of the Core's tokens.

- At most :data:`MAX_OPEN` open at once.
- :data:`IDLE_S` with no input closes a shell (``reap``, on an injectable clock).
- Core stop closes every shell (``close_all``); if the Core dies, ``KILL_ON_JOB_CLOSE`` ends them on Windows.
  On Linux and Mac they keep running until their input closes (``steward.jobs``, honest limits).
- Ctrl+C ends what the shell is running (every process in its job but the shell and its console host);
  if the shell itself is still busy (a loop inside PowerShell), it starts again in the same folder and
  says so, since Windows has no Ctrl+C for a shell on pipes.
- The shell exiting by itself ends its job too: nothing it started outlives it unseen.
- A Windows limit: a program started through WMI (``Win32_Process`` Create) is started by Windows' WMI
  service, not by the shell, so it runs outside the shell's job and is not ended with it.
- Output kept for the stream is capped at :data:`KEEP_CHARS` characters (old output goes first; one huge
  piece keeps only its end), and the stream says "(output cut)" where it was cut.
- Audit: one line per shell opened or closed, one per line sent with only its length and first word
  (a first word shaped like a key is hidden), one per typed answer with only its length. Output is never
  logged anywhere; it lives in memory only, for the event stream.
"""

from __future__ import annotations

import codecs
import ctypes
import os
import re
import secrets
import threading
import time
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .steward import jobs

IDLE_S = 30 * 60
MAX_OPEN = 2
MAX_LINE = 8000
KEEP_CHARS = 256_000             # output kept per shell for the event stream (memory only)
PARTIAL_MAX = 16_384             # a line with no newline yet is sent on once it is this long, whatever it holds
CUT = "(output cut)\n"
ENDED_KEEP_S = 60                # an exited shell's events stay readable this long
REAP_EVERY_S = 30
READY_WAIT_S = 20
CTRL_C_WAIT_S = 3.0
BASH, SH = "/bin/bash", "/bin/sh"
SHELLS = ("powershell", "cmd") if os.name == "nt" else ("bash", "sh")
DEFAULT_SHELL = "powershell" if os.name == "nt" else ("bash" if os.path.isfile(BASH) else "sh")
HIDDEN = "[hidden]"

TOO_MANY = "There are already {n} command lines open. Close one first, or wait until one ends by itself."
NO_SHELL = "This computer has no {shell} the command line can start."
NOT_READY = "The command line did not start in time, so it was closed."
NOT_CONTAINED = "Windows would not hold the command line in a job of its own, so it was not started."
RESTARTED = "Stopped. The command line started again in {cwd}. Anything you set before is gone.\n"
IDLE_LINE = f"The command line closed after {IDLE_S // 60} minutes with nothing typed.\n"

#: Names a Core token starts with (``api.auth._PREFIX``): an environment value holding one is dropped.
_TOKEN = re.compile(r"\b(?:ppk|ppd|ppt|pts)_[A-Za-z0-9_\-]{16,}")
_WORD = re.compile(r"[A-Za-z0-9_.:\\/$\-]+")


class TooMany(RuntimeError):
    pass


class StartFailed(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def system_dir() -> str:
    """Windows' System32 folder, asked of Windows itself (not read from an environment variable)."""
    if os.name == "nt":
        buf = ctypes.create_unicode_buffer(520)
        n = ctypes.windll.kernel32.GetSystemDirectoryW(buf, 520)
        if 0 < n < 520:
            return buf.value
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32")


def shell_argv(shell: str) -> list[str]:
    """How *shell* starts, by full path. PowerShell without her profile and cmd without AutoRun, so a
    profile that asks a question can't hang a shell that has no keyboard. The same for bash and sh."""
    if shell in ("bash", "sh"):
        exe = BASH if shell == "bash" else SH
        if not os.path.isfile(exe):
            raise StartFailed(NO_SHELL.format(shell=shell))
        return [exe, "--noprofile", "--norc"] if shell == "bash" else [exe]
    root = system_dir()
    if shell == "powershell":
        exe = os.path.join(root, "WindowsPowerShell", "v1.0", "powershell.exe")
        argv = [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"]
    elif shell == "cmd":
        exe = os.path.join(root, "cmd.exe")
        argv = [exe, "/Q", "/D"]
    else:
        raise ValueError(shell)
    if not os.path.isfile(exe):
        raise StartFailed(NO_SHELL.format(shell=shell))
    return argv


def shell_env(base: Mapping[str, str], tokens: tuple[str, ...] = ()) -> dict[str, str]:
    """The owner's environment for her shell: *base* without any ``PUFFPAI*`` name and without any value
    holding one of the Core's tokens (by shape, and *tokens* exactly)."""
    out: dict[str, str] = {}
    for k, v in base.items():
        if k.upper().startswith("PUFFPAI") or (os.name != "nt" and k in ("BASH_ENV", "ENV")):
            continue                            # (the last two: a start file a shell with no keyboard still reads)
        if _TOKEN.search(str(v)) or any(t and t in str(v) for t in tokens):
            continue
        out[k] = str(v)
    return out


def first_word(line: str) -> str:
    """The audit's first word of *line*: the name part only (``$env:X='v'`` -> ``$env:X``), at most 32
    characters, and hidden when it is shaped like a key (the floor key rule, D50)."""
    parts = line.split()
    if not parts:
        return ""
    m = _WORD.match(parts[0])
    word = m.group(0)[:32] if m else ""
    if not word:
        return HIDDEN
    try:
        from .zone1 import secret_detector, token_rules
        if secret_detector.find(parts[0]) or token_rules.find(parts[0]) or _TOKEN.search(parts[0]):
            return HIDDEN
    except Exception:  # noqa: BLE001 - can't check it: don't keep it
        return HIDDEN
    if len(word) >= 20 and re.search(r"\d", word) and re.search(r"[A-Za-z]", word) and not re.search(r"[\\/.]", word):
        return HIDDEN                           # long, mixed letters and digits, not a path: may be a key
    return word


class Shell:
    """One open command line. Only :class:`Shells` makes these."""

    def __init__(self, mgr: "Shells", shell: str, owner: str, device: str) -> None:
        self.mgr = mgr
        self.id = "sh_" + secrets.token_hex(8)
        self.shell = shell
        self.owner = owner                       # the hash of the device token that opened it
        self.device = device[:60]
        self.cwd = str(mgr.home)
        self.started = _now_iso()
        self.last_input = mgr.clock()
        self.ended_at: float | None = None
        self.exit_code: int | None = None
        self.closed_why = ""
        self.busy = True
        self.ready = threading.Event()
        self.cond = threading.Condition()
        self.events: list[tuple[int, dict]] = []
        self.seq = 0
        self._kept = 0
        self._nonce = secrets.token_hex(6)
        self._gen = 0
        self._base: frozenset[int] = frozenset()
        self._expect_cwd = False
        self.job: jobs.Job | None = None
        self._lock = threading.Lock()            # one writer to stdin at a time
        self.audited_close = False
        self._job_lock = threading.RLock()       # terminate and close of a job never overlap
        self.job_empty: bool | None = None       # after end(): did the job end with nothing left running

    # -- the process ----------------------------------------------------------------
    def _mark(self) -> str:
        return "\x1ePUFFDONE-" + self._nonce + " "

    def done_line(self) -> str:
        if self.shell == "powershell":
            return ("$__puffOk = $?; $__puffCode = $LASTEXITCODE; "
                    "$__puffC = if ($__puffOk) { 0 } elseif ($__puffCode) { $__puffCode } else { 1 }; "
                    f"[Console]::Out.WriteLine([string][char]30 + 'PUFFDONE-{self._nonce} ' + $__puffC + ' ' "
                    "+ $PWD.ProviderPath); [Console]::Out.Flush()\n")
        if self.shell in ("bash", "sh"):
            return f"""__puff_c=$?; printf '\\036PUFFDONE-{self._nonce} %s %s\\n' "$__puff_c" "$PWD"\n"""
        # cmd: the code (digits), then ``cd`` prints the folder on the next line (a folder name with & in it
        # is never put through cmd's parser).
        return f"echo \x1ePUFFDONE-{self._nonce} %errorlevel%& cd\n"

    def _preamble(self) -> str:
        if self.shell == "powershell":
            return ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8; "
                    "$ProgressPreference = 'SilentlyContinue'\n")
        if self.shell in ("bash", "sh"):
            return ""
        return "chcp 65001 >nul\n"

    def start(self) -> None:
        env = shell_env(self.mgr.env_source(), self.mgr.tokens())
        argv = shell_argv(self.shell)
        job = jobs.Job.spawn_piped(argv, self.cwd, env)
        if not job.contained:
            job.terminate(wait_s=5)
            job.close()
            raise StartFailed(NOT_CONTAINED)
        with self.cond:
            self._gen += 1
            gen = self._gen
            self.job = job
            self.busy = True
            self._expect_cwd = False
            self.ready.clear()
        out = threading.Thread(target=self._read, args=(job, gen, "out"), daemon=True, name=f"devshell-{self.id}-o")
        err = threading.Thread(target=self._read, args=(job, gen, "err"), daemon=True, name=f"devshell-{self.id}-e")
        out.start()
        err.start()
        threading.Thread(target=self._watch, args=(job, gen, out, err), daemon=True,
                         name=f"devshell-{self.id}-w").start()
        self._write(job, self._preamble() + self.done_line())

    def _write(self, job: jobs.Job, text: str) -> None:
        with self._lock:
            pipe = job.proc.stdin
            assert pipe is not None
            pipe.write(text.encode("utf-8"))
            pipe.flush()

    def _read(self, job: jobs.Job, gen: int, stream: str) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pipe = job.proc.stdout if stream == "out" else job.proc.stderr
        pending = ""
        while True:
            try:
                chunk = pipe.read(4096) if pipe else b""
            except (OSError, ValueError):
                chunk = b""
            if not chunk:
                break
            text = decoder.decode(chunk).replace("\r\n", "\n")
            if stream == "err":
                self._emit(gen, "err", text)
                continue
            pending += text
            shown = []
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                shown.append(self._line(gen, line.rstrip("\r")))
            if pending and "\x1e" not in pending and not self._expect_cwd:
                shown.append(pending)            # a prompt with no newline yet
                pending = ""
            elif len(pending) > PARTIAL_MAX:     # never held without end: a long line goes on as it is
                shown.append(pending)
                pending = ""
                self._expect_cwd = False         # that was no folder line
            self._emit(gen, "out", "".join(shown))
        if pending and stream == "out":
            self._emit(gen, "out", self._line(gen, pending))

    def _line(self, gen: int, line: str) -> str:
        """One whole line of stdout: the done line is taken out (busy ends, the folder is noted)."""
        if self._expect_cwd:
            self._expect_cwd = False
            if line.strip():
                with self.cond:
                    self.cwd = line.strip()
            return ""
        mark = self._mark()
        if mark not in line:
            return line + "\n"
        before, after = line.split(mark, 1)
        code, _, cwd = after.strip().partition(" ")
        with self.cond:
            if gen == self._gen:
                self.busy = False
                if cwd:
                    self.cwd = cwd
                self.cond.notify_all()
        if self.shell == "cmd":
            self._expect_cwd = True
            before = ""                          # cmd's own prompt for the done line: it prints again after
        if not self.ready.is_set() and gen == self._gen:
            if self.job is not None:
                try:
                    self._base = frozenset(self.job.pids())  # the shell and its console host, nothing else yet
                except OSError:
                    self._base = frozenset({self.job.pid})
            self.ready.set()
            return before
        return before + (f"(exit code {code})\n" if code not in ("", "0") else "")

    def _watch(self, job: jobs.Job, gen: int, out: threading.Thread, err: threading.Thread) -> None:
        out.join()
        err.join()
        try:
            code = job.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            code = -1
        with self._job_lock:                     # never at the same time as end() on this job
            job.terminate(wait_s=5)              # what the shell started ends with it
            job.close()
        for pipe in (job.proc.stdin, job.proc.stdout, job.proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass
        with self.cond:
            if gen != self._gen:                 # an old shell a Ctrl+C restart replaced
                return
            self.busy = False
            self.exit_code = int(code) if code is not None else -1
        self.mgr._ended(self, "exited")          # the closed line, then the exit event, then it is ended
        self._push({"exit": self.exit_code})
        with self.cond:
            self.ended_at = self.mgr.clock()
            self.ready.set()
            self.cond.notify_all()

    # -- events ---------------------------------------------------------------------
    def _emit(self, gen: int, stream: str, text: str) -> None:
        if not text or gen != self._gen:
            return
        self._push({"t": _now_iso(), "stream": stream, "text": text})

    def _push(self, ev: dict) -> None:
        """Keep *ev* for the stream. At most :data:`KEEP_CHARS` of text is kept: old events go first, and one
        event over the cap on its own keeps only its end. Where output was cut, the stream says so."""
        with self.cond:
            text = ev.get("text")
            if isinstance(text, str) and len(text) > KEEP_CHARS - len(CUT):
                ev = dict(ev, text=CUT + text[-(KEEP_CHARS - len(CUT)):])
            self.seq += 1
            self.events.append((self.seq, ev))
            self._kept += len(ev.get("text") or "")
            cut = False
            while self._kept + len(CUT) > KEEP_CHARS and len(self.events) > 1:
                _s, old = self.events.pop(0)
                self._kept -= len(old.get("text") or "")
                cut = True
            if cut:
                s, first = self.events[0]
                said = first.get("text")
                if isinstance(said, str) and not said.startswith(CUT):
                    self.events[0] = (s, dict(first, text=CUT + said))
                    self._kept += len(CUT)
            self.cond.notify_all()

    def since(self, after: int) -> list[tuple[int, dict]]:
        with self.cond:
            return [(s, e) for s, e in self.events if s > after]

    def wait(self, after: int, timeout: float) -> bool:
        with self.cond:
            if self.seq > after:
                return True
            self.cond.wait(timeout)
            return self.seq > after

    @property
    def open(self) -> bool:
        return self.ended_at is None

    # -- input ----------------------------------------------------------------------
    def claim(self) -> bool:
        """True when the next line is a new command (the shell waits; it is now busy with it), False when a
        command is running, so the line is typed input for it (an answer to its prompt)."""
        with self.cond:
            new = not self.busy
            if new:
                self.busy = True
            return new

    def unclaim(self) -> None:
        with self.cond:
            self.busy = False

    def send(self, text: str, new: bool) -> None:
        """Write *text* and Enter (and the done line after a new command line)."""
        job = self.job
        if job is None or not self.open:
            raise StartFailed("ended")
        self.last_input = self.mgr.clock()
        self._write(job, text + "\n" + (self.done_line() if new else ""))

    def ctrl_c(self) -> str:
        """Ctrl+C: "idle" (nothing ran), "stopped" (what ran was ended) or "restarted"."""
        self.last_input = self.mgr.clock()
        job = self.job
        if job is None or not self.open:
            raise StartFailed("ended")
        if self.ready.is_set():
            # Always, busy or not: a line typed while a command ran may be running now with no done line.
            ended = job.end_others(self._base)
            end = time.monotonic() + CTRL_C_WAIT_S
            with self.cond:
                while self.busy and self.open and time.monotonic() < end:
                    self.cond.wait(0.1)
                stopped = not self.busy or not self.open
            if stopped:
                self._push({"t": _now_iso(), "stream": "out", "text": "^C\n"})
                return "stopped" if ended else "idle"
        old = job
        self.start()                             # the new shell first, so the old one's end never ends it
        with self._job_lock:
            old.terminate(wait_s=10)
        self._push({"t": _now_iso(), "stream": "out", "text": RESTARTED.format(cwd=self.cwd)})
        return "restarted"

    def end(self) -> bool:
        """End the job: the shell and everything it started. True when nothing in the job runs any more."""
        job = self.job
        if job is None:
            return True
        with self._job_lock:
            self.job_empty = job.terminate(wait_s=10)
        return self.job_empty

    def idle_s(self) -> float:
        return max(0.0, self.mgr.clock() - self.last_input)

    def row(self) -> dict:
        return {"id": self.id, "shell": self.shell, "started": self.started, "idle_s": int(self.idle_s())}


class Shells:
    """Every open command line of this Core. ``audit(record)`` writes one audit line (it may raise: then
    opening and sending are refused, closing still happens)."""

    def __init__(self, home: Path | str | None = None, *, audit: Callable[[dict], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 env_source: Callable[[], Mapping[str, str]] = lambda: os.environ,
                 tokens: Callable[[], tuple[str, ...]] = lambda: (),
                 idle_s: float = IDLE_S, max_open: int = MAX_OPEN, reaper: bool = True) -> None:
        self.home = Path(home) if home else Path.home()
        self.audit = audit or (lambda _r: None)
        self.clock = clock
        self.env_source = env_source
        self.tokens = tokens
        self.idle_s = idle_s
        self.max_open = max_open
        self._shells: dict[str, Shell] = {}
        self._lock = threading.Lock()
        self._open_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._stop = threading.Event()
        self._reaper_on = reaper
        self._reaper: threading.Thread | None = None
        self.stopping = 0                        # moves on at close_all, so open streams end
        _LIVE.add(self)

    def _line(self, action: str, sh: Shell, **extra: Any) -> None:
        self.audit({"actor": "owner", "action": action, "shell_id": sh.id, "shell": sh.shell,
                    "device": sh.device, **extra})

    def open(self, shell: str, owner: str, device: str = "") -> Shell:
        if shell not in SHELLS:
            raise ValueError(shell)
        with self._open_lock:
            self.reap()
            with self._lock:
                if sum(1 for s in self._shells.values() if s.open) >= self.max_open:
                    raise TooMany(TOO_MANY.format(n=self.max_open))
            sh = Shell(self, shell, owner, device)
            self.home = Path(self.home)
            self.home.mkdir(parents=True, exist_ok=True)
            sh.start()
            if not sh.ready.wait(READY_WAIT_S) or not sh.open:
                sh.end()
                raise StartFailed(NOT_READY)
            try:
                self._line("dev_shell_opened", sh)
            except Exception:
                sh.end()
                raise
            with self._lock:
                self._shells[sh.id] = sh
        self._ensure_reaper()
        return sh

    def get(self, id: str, owner: str | None = None) -> Shell | None:
        with self._lock:
            sh = self._shells.get(str(id or ""))
        if sh is None or (owner is not None and sh.owner != owner):
            return None
        return sh

    def line(self, sh: Shell, text: str) -> bool:
        """Send one line; its audit line first (length and first word, or length only for typed input)."""
        new = sh.claim()
        try:
            if new:
                self._line("dev_shell_line", sh, length=len(text), first_word=first_word(text))
            else:
                self._line("dev_shell_typed", sh, length=len(text))
        except Exception:
            if new:
                sh.unclaim()
            raise
        sh.send(text, new)
        return new

    def ctrl_c(self, sh: Shell) -> str:
        how = sh.ctrl_c()
        try:
            self._line("dev_shell_ctrl_c", sh, result=how)
        except Exception:  # noqa: BLE001 - stopping something never waits on the log
            pass
        return how

    def close(self, id: str, why: str = "closed") -> bool:
        with self._lock:
            sh = self._shells.pop(str(id or ""), None)
        if sh is None:
            return False
        was_open = sh.open
        sh.closed_why = sh.closed_why or why
        sh.end()
        if was_open:
            end = time.monotonic() + 10
            with sh.cond:                        # until the exit event is out, so a stream sees it
                while sh.open and time.monotonic() < end:
                    sh.cond.wait(0.1)
            self._closed_line(sh, why)
        return True

    def close_device(self, device_id: str, why: str = "device_removed") -> int:
        """Close every shell a device opened, by its short id (``TokenStore.device_id``, the start of ``owner``). Its
        streams end with the exit event. How many."""
        with self._lock:
            ids = [sid for sid, sh in self._shells.items() if sh.owner[:len(device_id)] == device_id]
        return sum(1 for sid in ids if self.close(sid, why))

    def _closed_line(self, sh: Shell, why: str) -> None:
        with self._close_lock:                   # held while writing, so a second caller waits for the line
            if sh.audited_close:
                return
            sh.audited_close = True
            try:
                self._line("dev_shell_closed", sh, why=why)
            except Exception:  # noqa: BLE001 - closing never fails on the log
                pass

    def _ended(self, sh: Shell, why: str) -> None:
        """The shell's process ended (exit, a DELETE, idle, Core stop): one closed line."""
        self._closed_line(sh, sh.closed_why or why)

    def list(self) -> list[dict]:
        with self._lock:
            rows = [s for s in self._shells.values() if s.open]
        return [s.row() for s in rows]

    def reap(self) -> int:
        """Close shells with no input for ``idle_s``; forget exited ones after a while. How many closed."""
        now = self.clock()
        with self._lock:
            rows = list(self._shells.values())
        n = 0
        for sh in rows:
            if not sh.open:
                if sh.ended_at is not None and now - sh.ended_at > ENDED_KEEP_S:
                    with self._lock:
                        self._shells.pop(sh.id, None)
                continue
            if now - sh.last_input >= self.idle_s:
                sh._push({"t": _now_iso(), "stream": "out", "text": IDLE_LINE})
                self.close(sh.id, "idle")
                n += 1
        return n

    def _ensure_reaper(self) -> None:
        if not self._reaper_on:
            return
        with self._lock:
            if self._reaper is not None and self._reaper.is_alive():
                return

            def loop() -> None:
                while not self._stop.wait(REAP_EVERY_S):
                    try:
                        self.reap()
                    except Exception:  # noqa: BLE001
                        pass
            self._stop.clear()
            self._reaper = threading.Thread(target=loop, daemon=True, name="devshell-reaper")
            self._reaper.start()

    def close_all(self, why: str = "core_stop") -> int:
        """Every shell ends (the Core is stopping, or safe mode). How many were open."""
        self._stop.set()
        with self._lock:
            ids = list(self._shells)
        n = 0
        for id in ids:
            sh = self.get(id)
            if sh is not None and sh.open:
                n += 1
            self.close(id, why)
        self.stopping += 1
        return n


#: Every Shells made in this process (one per Core), so safe mode's shared step reaches them with no app at hand.
_LIVE: "weakref.WeakSet[Shells]" = weakref.WeakSet()


def close_every(why: str) -> int:
    """Every open command line in this process ends (safe mode). How many were open."""
    return sum(mgr.close_all(why) for mgr in list(_LIVE))


__all__ = ["DEFAULT_SHELL", "IDLE_S", "close_every", "MAX_LINE", "MAX_OPEN", "SHELLS", "Shell", "Shells", "StartFailed", "TooMany",
           "first_word", "shell_argv", "shell_env"]
