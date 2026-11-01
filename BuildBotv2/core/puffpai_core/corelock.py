r"""One Core per bot (D91): the bot's lock, and listening sockets no other program can share.

The lock (:func:`acquire`) is ``<bot folder>\core.lock``. The running Core holds the operating system's own
lock on it (``msvcrt.locking`` on Windows, ``fcntl.flock`` elsewhere) for as long as it runs. The OS lets it go
when that process ends, even after a crash or a hard kill, so a lock left by a Core that is gone is simply
taken by the next start: nothing guesses from a pid that Windows may have given to another program since. The
file also says which process holds it (pid, when it took the lock) and on which port, so a second start can say
plainly where the running one is. The file is never deleted (a delete could race with the next start).

The sockets (:func:`exclusive_socket`): waitress sets ``SO_REUSEADDR``, and on Windows that lets a second
process bind a port another process already listens on; the two then share the connections. So the Core's
own listeners and the Bridge proxy bind with ``SO_EXCLUSIVEADDRUSE`` on Windows: a port in use is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

LOCK_NAME = "core.lock"
#: The one byte the OS lock covers, far past the file's text, so a second start can still read who holds it.
_LOCK_AT = 1 << 20


#: Who holds the lock: a running Core, or a ``puffpai`` command that changes the bot with no Core running.
CORE, TOOL = "core", "tool"
#: How long a start waits for the lock (a probe from a ``puffpai`` command holds it for a moment, and Windows
#: can take up to about a second to let it go after a hard kill).
START_WAIT_S = 2.0
PROBE_WAIT_S = 0.5
BUSY_LINE = ("Another puffpai command (add, remove or update) is changing this bot right now. "
             "Try again when it is done.")


class LockError(Exception):
    """This bot's lock can't be taken. :meth:`lines` says why, in plain words."""

    def lines(self) -> list[str]:
        return [str(self)]


class AlreadyRunning(LockError):
    """Something holds this bot's lock. ``info``: what its lock file says (kind, pid, port), maybe empty."""

    def __init__(self, info: dict) -> None:
        super().__init__("BuildBot is already running for this bot.")
        self.info = info

    def lines(self) -> list[str]:
        return already_running_lines(self.info)


class LockUnusable(LockError):
    """The lock file can't be opened or written (a read-only file or folder, a permission)."""


class PortInUse(OSError):
    """A port the Core needs is taken by another program (raised by ``api.app.make_servers``)."""


@dataclass
class CoreLock:
    path: Path
    fd: int
    info: dict = field(default_factory=dict)
    held: bool = True

    def release(self) -> None:
        """Let the lock go (the OS does it anyway when this process ends). Safe to call twice."""
        if not self.held:
            return
        self.held = False
        try:
            _unlock(self.fd)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __enter__(self) -> "CoreLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def _lock(fd: int) -> None:
    os.lseek(fd, _LOCK_AT, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)        # raises OSError at once when another handle holds it
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    os.lseek(fd, _LOCK_AT, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_info(fd: int) -> dict:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096)
        info = json.loads(raw.decode("utf-8").strip() or "{}")
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def acquire(state: Path | str, *, port: int, pages_port: int | None = None, kind: str = CORE,
            record: bool = True, wait_s: float = 0.0) -> CoreLock:
    """Take this bot's lock, or raise :class:`AlreadyRunning` (held) or :class:`LockUnusable` (can't open it).
    It is tried again for up to *wait_s* seconds. With *record*, write who holds it (kind, pid, when, ports)
    once the lock is ours; a probe (``record=False``) writes nothing."""
    path = Path(state) / LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        why = getattr(exc, "strerror", None) or type(exc).__name__
        raise LockUnusable(f"BuildBot can't use its lock file {path} ({why}). Make sure that file and its folder "
                           "can be written, then try again.") from None
    end = time.monotonic() + max(0.0, wait_s)
    while True:
        try:
            _lock(fd)
            break
        except OSError:
            if time.monotonic() >= end:
                info = _read_info(fd)
                os.close(fd)
                raise AlreadyRunning(info) from None
            time.sleep(0.1)
    info = {"kind": kind, "pid": os.getpid(), "started": time.time(), "port": int(port)}
    if pages_port is not None:
        info["pages_port"] = int(pages_port)
    if record:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(info).encode("utf-8"))
        except OSError:
            pass                                       # the lock still holds; only the note is missing
    return CoreLock(path=path, fd=fd, info=info)


def holder(state: Path | str) -> dict | None:
    """What holds this bot's lock now (its lock file's note, at least ``{"kind": "core"}``), or None when
    nothing does or the lock file can't be used. A probe: it holds the lock for a moment only, and a start
    waits that out (:data:`START_WAIT_S`)."""
    try:
        probe = acquire(state, port=0, record=False, wait_s=PROBE_WAIT_S)
    except AlreadyRunning as exc:
        return {"kind": CORE, **exc.info}
    except LockError:
        return None
    probe.release()
    return None


def bot_id(state: Path | str) -> str:
    """Which bot a Core runs for (D91): a hash of its bot folder's full path, the same in the Core and in a
    ``puffpai`` command for that bot. Says nothing about the folder itself."""
    path = os.path.normcase(str(Path(state).resolve()))
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]


def exclusive_socket(host: str, port: int) -> socket.socket:
    """A TCP socket bound to *host*:*port* (not yet listening) that no other socket can share. Raises OSError
    when the port is in use. On Windows ``SO_EXCLUSIVEADDRUSE``; elsewhere ``SO_REUSEADDR``, which there never
    lets a second listener in (only a quick restart past TIME_WAIT)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, int(port)))
    except OSError:
        sock.close()
        raise
    return sock


def already_running_lines(info: dict | None) -> list[str]:
    """What a second start says (D91): plain words, how to open the running one, how to stop it. When a
    ``puffpai`` command holds the lock, that it is busy."""
    from .wizard.terminal import cli_command, how_to_run, signin_launcher, stop_launcher
    info = info or {}
    if info.get("kind") == TOOL:
        return [BUSY_LINE]
    try:
        port = int(info.get("port") or 0) or 8420
    except (TypeError, ValueError):
        port = 8420
    signin, stop = signin_launcher(), stop_launcher()
    how_signin = (f"To sign a browser in, {how_to_run(signin)}."
                  if signin.is_file() else f"To sign a browser in, run this in a terminal: {cli_command('pair', '--running')}")
    close = "close its window, or " if os.name == "nt" else ""
    how_stop = (f"To stop it, {close}{how_to_run(stop)}."
                if stop.is_file() else f"To stop it, {close}run this in a terminal: {cli_command('shutdown')}")
    return [
        "BuildBot is already running, in another window. This window did not start a second one."
        if os.name == "nt" else "BuildBot is already running. This did not start a second one.",
        f"Its Desktop is at http://puffpai.localhost:{port}/ . {how_signin}",
        how_stop,
    ]
