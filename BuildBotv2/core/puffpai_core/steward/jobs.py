# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (adapted): _SUSPENDED, _win, _contain, _end_job, _job_pids, job_work, pid_alive
"""One job per started program, so Stop leaves nothing running (D18): a Windows job object, or on Linux and
Mac a session of its own.

v1 lesson (06 "Steward"): a process-tree kill misses what a wrapper that
already exited left behind (vite and its esbuild behind npm). A job holds
everything the program starts, with no breakaway, so ending the job ends all
of it. v2 adds one thing v1 did not have: the job is made with
``KILL_ON_JOB_CLOSE``, so if the Core itself dies, Windows ends every package
it started (v1 left them running, 08 NEXT_FIXES 5).

``Job.spawn(argv, cwd, env, log)`` starts the program suspended, puts it in a
new job, then lets it run, with no console window (``procflags.hidden``).
A batch file (``.cmd``, ``.bat``) is never the program: Windows would run it through
``cmd.exe``, a shell (D60, ``steward.noshell``).

Linux and Mac (D132): the program starts in a new session, so its session id and process group are its own
pid. Until the leader is reaped it holds that number, so the job is every live process in that session or
group, plus their descendants, read fresh each time (Linux ``/proc``, Mac ``/bin/ps``). Every read remembers
who it found, by pid AND start time, and the leader's ``poll`` and ``wait`` read once more right before they
reap it. Once it is reaped the number can come back to any program, so it is never matched again: only the
remembered processes, still the same by start time, and their live descendants are the job. So a wrapper that
exited still leaves its children seen. A table that can't be read raises ``OSError``: unknown, never an empty
job. A zombie is not alive, not even the leader before it is reaped. Before each signal the table is read
again and the pid must still be in this job; never by name.

Honest limits off Windows:

- A process that made its own session (``setsid``, a double-forked daemon) and whose parent died before any
  read saw it is not seen and not ended (the same limit v1 had). Nothing in POSIX holds it the way a job
  object does.
- After the leader is reaped, a process whose parent ended before any read saw it is not seen either, even in
  the job's old session: that number no longer proves anything. Reads come with every Caretaker and watch pass.
- A reap that does not go through the leader's ``poll`` or ``wait`` (``os.waitpid`` by hand) gets no last read:
  only what earlier reads remembered is known.
- If the Core itself dies, what it started keeps running: there is no ``KILL_ON_JOB_CLOSE``. ``PR_SET_PDEATHSIG``
  is not used on purpose: it fires when the spawning THREAD exits, and the Core spawns from request threads.
- Between the last read and the signal a pid could change hands in theory. The gap is one system call.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

from . import noshell, procflags

IS_WINDOWS = os.name == "nt"
CREATE_SUSPENDED = 0x00000004
_KILL_ON_JOB_CLOSE = 0x00002000
_EXTENDED_LIMITS = 9          # JobObjectExtendedLimitInformation
_PROCESS_LIST = 3             # JobObjectBasicProcessIdList
_ACCOUNTING = 8               # JobObjectBasicAndIoAccountingInformation
_PS = "/bin/ps"               # Mac (no /proc), by a fixed path, never looked up on PATH
_PS_WAIT_S = 5

_WIN: Any = None


def _win() -> Any:
    global _WIN
    if _WIN is None:
        if os.name != "nt":
            raise OSError(0, "Windows job objects exist only on Windows")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        nt = ctypes.WinDLL("ntdll")
        vp = ctypes.c_void_p
        k32.CreateJobObjectW.argtypes, k32.CreateJobObjectW.restype = [vp, ctypes.c_wchar_p], vp
        k32.AssignProcessToJobObject.argtypes = [vp, vp]
        k32.TerminateJobObject.argtypes = [vp, ctypes.c_uint]
        k32.CloseHandle.argtypes = [vp]
        k32.SetInformationJobObject.argtypes = [vp, ctypes.c_int, vp, ctypes.c_uint32]
        k32.QueryInformationJobObject.argtypes = [vp, ctypes.c_int, vp, ctypes.c_uint32, vp]
        k32.OpenProcess.argtypes, k32.OpenProcess.restype = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], vp
        k32.GetExitCodeProcess.argtypes = [vp, ctypes.POINTER(ctypes.c_uint32)]
        k32.IsProcessInJob.argtypes = [vp, vp, ctypes.POINTER(ctypes.c_int)]
        k32.TerminateProcess.argtypes = [vp, ctypes.c_uint]
        nt.NtResumeProcess.argtypes, nt.NtResumeProcess.restype = [vp], ctypes.c_long
        _WIN = (k32, nt)
    return _WIN


class _BasicLimits(ctypes.Structure):
    _fields_ = [("per_process_user_time", ctypes.c_int64), ("per_job_user_time", ctypes.c_int64),
                ("limit_flags", ctypes.c_uint32), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                ("active_process_limit", ctypes.c_uint32), ("affinity", ctypes.c_size_t),
                ("priority_class", ctypes.c_uint32), ("scheduling_class", ctypes.c_uint32)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("r_ops", "w_ops", "o_ops", "r_bytes", "w_bytes", "o_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IoCounters), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):     # JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION
    _fields_ = [("user", ctypes.c_int64), ("kernel", ctypes.c_int64),
                ("this_user", ctypes.c_int64), ("this_kernel", ctypes.c_int64),
                ("faults", ctypes.c_uint32), ("procs", ctypes.c_uint32),
                ("active", ctypes.c_uint32), ("ended", ctypes.c_uint32),
                ("read_ops", ctypes.c_uint64), ("write_ops", ctypes.c_uint64), ("other_ops", ctypes.c_uint64),
                ("read_bytes", ctypes.c_uint64), ("write_bytes", ctypes.c_uint64), ("other_bytes", ctypes.c_uint64)]


class _Proc(NamedTuple):
    ppid: int
    pgid: int
    sid: int
    zombie: bool
    start: str = ""                               # when it started: with the pid, who this process is


def _stat_row(raw: bytes) -> tuple[int, _Proc] | None:
    """``(pid, row)`` from one ``/proc/<pid>/stat``. The program's name is in brackets and may hold anything,
    so the fields are read after its last bracket."""
    head, _, tail = raw.rpartition(b")")
    fields = tail.split()
    try:
        return int(head.split(b"(", 1)[0]), _Proc(int(fields[1]), int(fields[2]), int(fields[3]),
                                                  fields[0] in (b"Z", b"X", b"x"), fields[19].decode("ascii"))
    except (IndexError, ValueError):
        return None


def _only_thread(pid: int) -> bool:
    """A process whose first thread ended reads Z while its other threads still run: that one is alive."""
    try:
        return len(os.listdir(f"/proc/{pid}/task")) <= 1
    except OSError:
        return True


def _proc_table() -> dict[int, _Proc]:
    rows: dict[int, _Proc] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                got = _stat_row(fh.read())
        except OSError:
            continue                                # it ended while this read
        if got is not None:
            rows[got[0]] = got[1]._replace(zombie=got[1].zombie and _only_thread(got[0]))
    return rows


def _getsid(pid: int) -> int:
    try:
        return os.getsid(pid)
    except OSError:
        return -1


def _ps_rows(text: str, darwin: bool) -> dict[int, _Proc]:
    """The lines of ``ps -A -o pid=,ppid=,pgid=,sess=,stat=,lstart=``. A Mac's ``sess`` column is not a session id
    (a kernel pointer, or 0), so there the session comes from ``getsid``."""
    rows: dict[int, _Proc] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
            sid = _getsid(pid) if darwin else int(parts[3])
        except ValueError:
            continue
        rows[pid] = _Proc(ppid, pgid, sid, parts[4].startswith("Z"), " ".join(parts[5:]))
    return rows


def _ps_table() -> dict[int, _Proc]:
    got = subprocess.run([_PS, "-A", "-o", "pid=,ppid=,pgid=,sess=,stat=,lstart="], capture_output=True, text=True,
                         timeout=_PS_WAIT_S, check=False, stdin=subprocess.DEVNULL)
    rows = _ps_rows(got.stdout, sys.platform == "darwin") if got.returncode == 0 else {}
    if not rows:
        raise OSError(got.returncode, "ps gave no process table")
    return rows


def _process_table() -> dict[int, _Proc]:
    """Every process on this computer, read now. Raises ``OSError`` when it can't be read."""
    try:
        return _proc_table() if os.path.isfile("/proc/self/stat") else _ps_table()
    except subprocess.TimeoutExpired as exc:
        raise OSError(0, "ps did not answer") from exc


def _zombie_now(pid: int) -> bool:
    """Off Windows: has *pid* ended and only waits to be reaped? Raises when that can't be told."""
    if os.path.isfile("/proc/self/stat"):
        with open(f"/proc/{pid}/stat", "rb") as fh:
            got = _stat_row(fh.read())
        return bool(got and got[1].zombie and _only_thread(pid))
    return _process_table()[pid].zombie


def _zombie(pid: int) -> bool:
    try:
        return _zombie_now(pid)
    except (OSError, KeyError):
        return False


class _Leader(subprocess.Popen):
    """Off Windows, the program a job started. Its ``poll`` and ``wait`` never reap it unseen: while it is a
    zombie it still holds its number, so the job reads its members one last time first (``before_reap``)."""

    before_reap: Any = None

    def poll(self) -> int | None:
        if self.returncode is None:
            try:
                if not _zombie_now(self.pid):
                    return None
            except (OSError, KeyError):          # can't tell, or reaped by hand: the real poll says
                return super().poll()
            if self.before_reap is not None:
                self.before_reap()
        return super().poll()

    def wait(self, timeout: float | None = None) -> int:
        end = None if timeout is None else time.monotonic() + timeout
        pause = 0.02 if os.path.isfile("/proc/self/stat") else 0.25      # a Mac runs ps for each look
        while self.poll() is None:
            if end is not None and time.monotonic() >= end:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(pause)
        return self.returncode


def pid_alive(pid: Any) -> bool:
    """Is process *pid* still running (never signals it on Windows)? A zombie has ended: not alive."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return not _zombie(pid)
    k32, _nt = _win()
    handle = k32.OpenProcess(0x1000, 0, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_uint32()
        return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        k32.CloseHandle(handle)


class Job:
    """A started program and everything it starts. ``proc`` is its ``Popen``."""

    def __init__(self, proc: subprocess.Popen, handle: int | None) -> None:
        self.proc = proc
        self.pid = int(proc.pid)
        self._handle = handle
        self._gone = False                       # off Windows: reaped, and nothing it started is left
        self._known: dict[int, str] = {}         # off Windows: who the last read found, pid -> start time
        if isinstance(proc, _Leader):
            proc.before_reap = self._last_read

    @classmethod
    def spawn(cls, argv: list[str], cwd: Path | str, env: dict, log: Path | str | None = None,
              stdin: Any = subprocess.DEVNULL) -> "Job":
        """Start *argv* (no shell) in *cwd* with exactly *env*; output appends to *log*.
        A batch file as the program is :class:`noshell.NoShell` (an ``OSError``), never started."""
        why = noshell.refusal(str(argv[0])) if argv else ""
        if why:
            raise noshell.NoShell(why)
        out: Any = subprocess.DEVNULL
        if log is not None:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            out = open(log, "ab")
        try:
            if IS_WINDOWS:
                proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT,
                                        stdin=stdin, **procflags.hidden(CREATE_SUSPENDED))
            else:
                proc = _Leader(argv, cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT,
                               stdin=stdin, start_new_session=True)
        finally:
            if out is not subprocess.DEVNULL:
                out.close()
        return cls(proc, _contain(proc))

    @classmethod
    def spawn_piped(cls, argv: list[str], cwd: Path | str, env: dict, stdin: Any = subprocess.PIPE,
                    line: str | None = None) -> "Job":
        """Like :meth:`spawn`, but stdin, stdout and stderr are pipes the caller reads and writes (the
        Developer page's command line, ``puffpai_core.devshell``). Same rules: no batch file as the program,
        started suspended, put in a job of its own before it runs a single instruction, no window.
        *stdin*: another stdin (``DEVNULL`` for a chat command, D138). *line* (Windows): the exact command line
        for *argv*, for ``cmd.exe``, which does not read its arguments the way Popen quotes them."""
        why = noshell.refusal(str(argv[0])) if argv else ""
        if why:
            raise noshell.NoShell(why)
        pipes = dict(stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        if IS_WINDOWS:
            proc = subprocess.Popen(line or argv, cwd=str(cwd), env=env, **pipes, **procflags.hidden(CREATE_SUSPENDED))
        else:
            proc = _Leader(argv, cwd=str(cwd), env=env, **pipes, start_new_session=True)
        return cls(proc, _contain(proc))

    def end_others(self, keep: set[int] | frozenset[int]) -> int:
        """End every process in this job except the ones in *keep* (the shell itself and its console host).
        Each is opened by its number from the job's own list, then checked to be in THIS job on that handle
        (so a number Windows gave to some other program since is never touched), and ended on that handle.
        Returns how many were ended. Off Windows: each one is checked to be in this job right before the signal,
        children before parents (a child that made its own session is known only through its parent)."""
        if not IS_WINDOWS:
            return self._end_members(keep)
        if not self._handle:
            return 0
        k32, _nt = _win()
        ended = 0
        for pid in self.pids():
            if pid in keep:
                continue
            h = k32.OpenProcess(0x0001 | 0x1000, 0, pid)       # PROCESS_TERMINATE | QUERY_LIMITED_INFORMATION
            if not h:
                continue
            try:
                inside = ctypes.c_int(0)
                if k32.IsProcessInJob(h, self._handle, ctypes.byref(inside)) and inside.value:
                    if k32.TerminateProcess(h, 1):
                        ended += 1
            finally:
                k32.CloseHandle(h)
        return ended

    def _members(self) -> dict[int, _Proc]:
        """Off Windows: every live process in this job, parents before their children, read now. Raises
        ``OSError`` when the table can't be read.

        A number is matched as a session or group only while the leader is not reaped (even as a zombie it
        holds that number). After that, only who an earlier read found, same pid and same start time, and their
        live descendants. So a daemon of another program that got the number later is never this job."""
        if self._gone:
            return {}
        reaped = self.proc.poll() is not None
        found = self._read(by_number=not reaped)
        if reaped and not found:
            self._gone = True
        return found

    def _read(self, by_number: bool) -> dict[int, _Proc]:
        table = _process_table()
        kids: dict[int, list[int]] = {}
        for pid, row in table.items():
            kids.setdefault(row.ppid, []).append(pid)
        order = [pid for pid, start in self._known.items()
                 if start and pid in table and table[pid].start == start]
        seen = set(order)
        if by_number:
            for pid, row in table.items():
                if self.pid in (row.pgid, row.sid) and pid not in seen:
                    seen.add(pid)
                    order.append(pid)
        for pid in order:                        # grows as it goes: the descendants, however deep
            for kid in kids.get(pid, ()):
                if kid not in seen:
                    seen.add(kid)
                    order.append(kid)
        found = {pid: table[pid] for pid in order if not table[pid].zombie}
        self._known = {pid: row.start for pid, row in found.items()}
        return found

    def _last_read(self) -> None:
        """The leader is a zombie, about to be reaped: its number still counts, for the last time."""
        try:
            self._read(by_number=True)
        except OSError:
            pass                                 # what earlier reads found stays known

    def _end_members(self, keep: set[int] | frozenset[int] = frozenset()) -> int:
        """Off Windows: signal everything in the job but *keep*, children first, each checked to be in this job
        right before its signal. How many were signalled (0 when the table can't be read)."""
        ended = 0
        try:
            for pid in reversed(list(self._members())):
                if pid in keep or pid not in self._members():
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                    ended += 1
                except OSError:
                    pass
        except OSError:
            pass
        return ended

    def _end_group(self) -> None:
        """Off Windows: signal the job's process group, only while the leader, not yet reaped, holds its number."""
        if self.proc.poll() is None:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except OSError:
                pass

    def pids(self) -> list[int]:
        """Every process in the job (what it started, even past a wrapper that exited). Off Windows it raises
        ``OSError`` when the process table can't be read: unknown, never an empty job."""
        if not IS_WINDOWS:
            return sorted(self._members())
        if not self._handle:
            return [self.pid] if pid_alive(self.pid) else []
        k32, _nt = _win()
        size = 4096

        class _List(ctypes.Structure):
            _fields_ = [("assigned", ctypes.c_uint32), ("listed", ctypes.c_uint32), ("ids", ctypes.c_size_t * size)]
        buf = _List()
        if not k32.QueryInformationJobObject(self._handle, _PROCESS_LIST, ctypes.byref(buf), ctypes.sizeof(buf), None):
            return []
        return [int(buf.ids[i]) for i in range(min(buf.listed, size))]

    def active(self) -> int:
        """How many processes in the job are still running."""
        if not IS_WINDOWS:
            try:
                return len(self._members())
            except OSError:                      # no table: the leader, and who was last known
                return 1 if self.proc.poll() is None or self._known else 0
        if not self._handle:
            return 1 if self.proc.poll() is None else 0
        k32, _nt = _win()
        got = _Accounting()
        if not k32.QueryInformationJobObject(self._handle, _ACCOUNTING, ctypes.byref(got), ctypes.sizeof(got), None):
            return 1 if self.proc.poll() is None else 0
        return int(got.active)

    def alive(self) -> bool:
        return self.active() > 0

    def terminate(self, wait_s: float = 15.0) -> bool:
        """End everything in the job. True when nothing in it runs any more."""
        if IS_WINDOWS and self._handle:
            k32, _nt = _win()
            k32.TerminateJobObject(self._handle, 1)
        elif IS_WINDOWS:
            # No job could be made: end this one process by its own handle (never a tree by PID number,
            # which Windows reuses). What it started may outlive it; that is why every start asks for a job.
            try:
                self.proc.kill()
            except OSError:
                pass
        else:
            self._end_members()                  # first, while each one's parent still lives
            self._end_group()
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            if not self.alive():
                break
            if not IS_WINDOWS:                   # no job holds them all at once: what started since then
                self._end_members()
            time.sleep(0.1)
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return not self.alive()

    @property
    def contained(self) -> bool:
        """Windows: the program is in a job object (so pids() and Stop reach everything it starts). Elsewhere
        always: it was started in a session of its own, or it did not start at all."""
        return bool(self._handle) or not IS_WINDOWS

    def close(self) -> None:
        """Let go of the job handle. With KILL_ON_JOB_CLOSE this also ends what is left; off Windows the same
        rule, by signal."""
        if IS_WINDOWS and self._handle:
            k32, _nt = _win()
            k32.CloseHandle(self._handle)
            self._handle = None
        elif not IS_WINDOWS:
            self._end_members()
            self._end_group()


def _contain(proc: subprocess.Popen) -> int | None:
    """Windows: put *proc* (started suspended) in a job of its own, then let it run.

    It runs only in a job. When the job can't be made or *proc* can't be put in it, *proc* is ended while it
    is still suspended (it never ran a single instruction), by the handle Popen holds, so it can't be some
    other program that got the same PID; None then, and the caller refuses it (no tree walk, ever)."""
    if not IS_WINDOWS:
        return None
    job = None
    try:
        k32, nt = _win()
        handle = int(proc._handle)  # noqa: SLF001 - the process handle Popen holds
    except (OSError, AttributeError):          # no way to make a job here: no job, so it does not run
        try:
            proc.kill()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None
    try:
        job = k32.CreateJobObjectW(None, None)
        if job:
            limits = _ExtendedLimits()
            limits.basic.limit_flags = _KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(job, _EXTENDED_LIMITS, ctypes.byref(limits), ctypes.sizeof(limits))
            if not k32.AssignProcessToJobObject(job, handle):
                k32.CloseHandle(job)
                job = None
    finally:
        if job:
            nt.NtResumeProcess(handle)
        else:
            try:
                proc.kill()                    # TerminateProcess on its own handle, still suspended
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
    return int(job) if job else None


def run(argv: list[str], cwd: Path | str, env: dict, log: Path | str | None, timeout_s: float) -> int:
    """Run *argv* to the end inside its own job. The exit code; -1 when it ran out of time (then it is ended)."""
    job = Job.spawn(argv, cwd, env, log)
    try:
        return job.proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return -1
    finally:
        job.terminate(wait_s=10)
        job.close()


__all__ = ["IS_WINDOWS", "Job", "pid_alive", "run"]
