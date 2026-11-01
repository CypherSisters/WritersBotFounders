"""Ctrl+C and Ctrl+Break in a real Windows console, for the tests (lane I4a item 8).

``run(argv, mode, env, ready)`` starts this file again as a helper in a NEW hidden console. The helper starts
*argv* in that console (for Ctrl+Break in a new process group, CREATE_NEW_PROCESS_GROUP), waits until the
output has *ready* in it, sends the event with GenerateConsoleCtrlEvent, and waits up to *wait_s* for the
target to end. It reports, as JSON: how long the stop took, the exit code, the output, the processes that
were in the target's job before the stop, and the ones still in it afterwards.

Every process is held in a Windows Job Object this kit makes (kill-on-close; the Core's own
``steward.jobs``): the helper in one, the target (and everything it starts) in another, each started
suspended and put in its job before it runs a single instruction. "Still running" means "still in that
job", read from the job itself (never a walk over parent PIDs, which Windows reuses). Cleanup is
TerminateJobObject on those jobs and nothing else: no taskkill, no PID numbers, no names.

Why a helper console: GenerateConsoleCtrlEvent reaches the processes of the caller's own console, so the
test runner's own console (and the test itself) is never sent anything.

``screen=True`` (lane I6b): the target gets the console itself for its input and output, the way a
double-clicked ``Start BuildBot.cmd`` does (cmd.exe asks "Terminate batch job (Y/N)?" on the console, and
``pause`` waits on it). The output in the report is then the console's own screen, read from its buffer.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

from puffpai_core.steward import jobs as _jobs

CTRL_C, CTRL_BREAK = 0, 1
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _held(argv: list[str], flags: int, **kw) -> "_jobs.Job":
    """Start *argv* suspended, put it in a new kill-on-close job, then let it run (``steward.jobs``)."""
    proc = subprocess.Popen(argv, creationflags=flags | _jobs.CREATE_SUSPENDED, **kw)
    job = _jobs.Job(proc, _jobs._contain(proc))       # noqa: SLF001 - the Core's own job maker
    if not job.contained:
        job.proc.kill()                               # its own handle, this one process only
        raise RuntimeError("Windows could not hold the test's process in a job, so the test did not run it.")
    return job


def _type(text: str) -> bool:
    """Type *text* into this process's console as key presses (``WriteConsoleInputW`` on ``CONIN$``), the way
    her keyboard does; "\r" is Enter (lane term-chat). True when every key went in."""
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32

    class KEY(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                    ("wVirtualScanCode", wintypes.WORD), ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]

    class EVENT(ctypes.Union):
        _fields_ = [("KeyEvent", KEY), ("pad", ctypes.c_byte * 16)]

    class RECORD(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", EVENT)]
    k32.CreateFileW.restype = wintypes.HANDLE
    h = k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)    # read+write, shared, open existing
    if not h or h == wintypes.HANDLE(-1).value:
        return False
    try:
        recs = (RECORD * (2 * len(text)))()
        for i, ch in enumerate(text):
            for j, down in enumerate((True, False)):
                r = recs[2 * i + j]
                r.EventType = 1                               # KEY_EVENT
                k = r.Event.KeyEvent
                k.bKeyDown, k.wRepeatCount, k.uChar = down, 1, ch
                k.wVirtualKeyCode = 0x0D if ch == "\r" else 0
        done = wintypes.DWORD()
        ok = k32.WriteConsoleInputW(wintypes.HANDLE(h), recs, len(recs), ctypes.byref(done))
        return bool(ok) and done.value == len(recs)
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


def _screen() -> str:
    """The text on this process's console screen, row by row (trailing spaces cut), up to the cursor's row."""
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT), ("Right", wintypes.SHORT),
                    ("Bottom", wintypes.SHORT)]

    class INFO(ctypes.Structure):
        _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wintypes.WORD),
                    ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]
    k32.CreateFileW.restype = wintypes.HANDLE
    h = k32.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)    # read+write, shared, open existing
    if not h or h == wintypes.HANDLE(-1).value:
        return ""
    try:
        info = INFO()
        if not k32.GetConsoleScreenBufferInfo(wintypes.HANDLE(h), ctypes.byref(info)):
            return ""
        width, rows = info.dwSize.X, info.dwCursorPosition.Y + 1
        buf = ctypes.create_unicode_buffer(width * rows)
        got = wintypes.DWORD()
        k32.ReadConsoleOutputCharacterW(wintypes.HANDLE(h), buf, width * rows, COORD(0, 0), ctypes.byref(got))
        text = buf[:got.value]
        return "\n".join(text[i:i + width].rstrip() for i in range(0, len(text), width))
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


def _end(job: "_jobs.Job") -> None:
    job.terminate(wait_s=10)                          # TerminateJobObject: only what is in this job
    job.close()


def _helper(spec_path: str) -> None:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    k32 = ctypes.windll.kernel32
    report: dict = {"pids": [], "left": []}
    out = Path(spec["out"])
    k32.SetConsoleCtrlHandler(None, False)           # Ctrl+C on (it may come in turned off): the target inherits it
    flags = CREATE_NEW_PROCESS_GROUP if spec["mode"] == "break" else 0
    screen = bool(spec.get("screen"))

    def seen() -> str:
        return _screen() if screen else out.read_text(encoding="utf-8", errors="replace")
    with open(out, "w", encoding="utf-8") as log:
        io = {} if screen else {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.PIPE}
        job = _held(spec["argv"], flags, env=spec["env"], cwd=spec.get("cwd"), **io)
        p = job.proc
        try:
            k32.SetConsoleCtrlHandler(None, True)    # the helper itself ignores the event it sends
            if spec.get("stdin") and not screen:
                p.stdin.write(spec["stdin"].encode("utf-8"))
                p.stdin.flush()
            end = time.monotonic() + spec["ready_s"]
            while time.monotonic() < end and p.poll() is None:
                if spec["ready"] in seen():
                    break
                time.sleep(0.25)
            report["ready"] = spec["ready"] in seen()
            if spec.get("during"):                   # a request left open at the moment of the stop
                report["during"] = subprocess.Popen(spec["during"], env=spec["env"]).pid
            time.sleep(spec.get("settle_s", 2.0))
            end = time.monotonic() + spec["ready_s"]       # *min_pids*: wait until that many are in the job
            while len(job.pids()) < spec.get("min_pids", 0) and time.monotonic() < end and p.poll() is None:
                time.sleep(0.25)
            report["pids"] = job.pids()
            if spec.get("keys"):                     # her keys, typed into the console (lane term-chat)
                report["typed"] = _type(spec["keys"])
            t0 = time.monotonic()
            if spec["mode"] == "none":
                report["sent"] = False               # nothing sent: what happens when it ends by itself
            elif spec["mode"] == "break":
                report["sent"] = bool(k32.GenerateConsoleCtrlEvent(CTRL_BREAK, p.pid))
            else:
                report["sent"] = bool(k32.GenerateConsoleCtrlEvent(CTRL_C, 0))
            try:
                p.wait(spec["wait_s"])
                report["exit"] = p.returncode
            except subprocess.TimeoutExpired:
                report["exit"] = None
            report["stop_s"] = round(time.monotonic() - t0, 2)
            time.sleep(1.0)
            report["left"] = job.pids()              # still in the target's job: a real leak of ours
            if screen:
                report["screen"] = _screen()         # before the clean-up: what she would see now
        finally:
            _end(job)
    report["output"] = report["screen"] if screen else out.read_text(encoding="utf-8", errors="replace")
    Path(spec["report"]).write_text(json.dumps(report), encoding="utf-8")


def run(argv: list[str], mode: str, env: dict, ready: str, folder: Path, *, stdin: str = "",
        ready_s: float = 120.0, wait_s: float = 30.0, settle_s: float = 2.0, during: list[str] | None = None,
        min_pids: int = 0, screen: bool = False, cwd: str | None = None, keys: str = "") -> dict:
    """Start *argv* in its own console, send Ctrl+C (*mode* ``c``) or Ctrl+Break (``break``) once *ready*
    shows in its output, and report what happened (see the module note). *during*: a program the helper
    starts just before the stop (in the helper's job), e.g. a request that is still open."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    spec = {"argv": argv, "mode": mode, "env": env, "ready": ready, "stdin": stdin, "ready_s": ready_s,
            "wait_s": wait_s, "settle_s": settle_s, "during": during, "min_pids": min_pids, "screen": screen,
            "cwd": cwd, "keys": keys, "out": str(folder / f"out-{mode}.txt"),
            "report": str(folder / f"report-{mode}.json")}
    spec_path = folder / f"spec-{mode}.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0                                # hidden: no window pops up while the tests run
    helper = _held([sys.executable, str(Path(__file__).resolve()), str(spec_path)], CREATE_NEW_CONSOLE,
                   startupinfo=si, env=env)
    try:
        helper.proc.wait(ready_s + wait_s + 60)
    finally:
        _end(helper)                                  # the helper's job, and anything still in it
    report = json.loads(Path(spec["report"]).read_text(encoding="utf-8"))
    report["helper_pid"] = helper.pid
    return report


def hold_plan_argv(hang_port: int, state: Path) -> list[str]:
    """*during* for :func:`run`: a ``POST /v1/toolsmith/plan`` that stays open. The bot's model address must
    be ``127.0.0.1:<hang_port>``, where this program listens and never answers, so Toolsmith's own process
    waits on the brain and the request waits on Toolsmith."""
    return [sys.executable, str(Path(__file__).resolve()), "--hold-plan", str(hang_port), str(state)]


def _hold_plan(hang_port: int, state: Path) -> None:
    import socket
    import urllib.request
    held = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", hang_port))
    srv.listen(16)

    def accept() -> None:
        while True:
            conn, _ = srv.accept()
            held.append(conn)                         # read nothing, answer nothing
    import threading
    threading.Thread(target=accept, daemon=True).start()
    info = json.loads((Path(state) / "settings" / "terminal.json").read_text(encoding="utf-8"))
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/v1/toolsmith/plan", method="POST",
                                 data=json.dumps({"goal": "a shopping list for our family"}).encode(),
                                 headers={"Authorization": "Bearer " + info["token"],
                                          "Content-Type": "application/json"})
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=120).read()
    except Exception:  # noqa: BLE001 - the Core stopping ends it; that is the point
        pass
    time.sleep(120)                                   # ended with the helper's job


if __name__ == "__main__":
    if sys.argv[1] == "--hold-plan":
        _hold_plan(int(sys.argv[2]), Path(sys.argv[3]))
    else:
        _helper(sys.argv[1])
