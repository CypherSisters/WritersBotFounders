r"""The PuffPAI window on Linux and Mac (D144, PROPOSED): the same ``/window/`` page in Chromium or Chrome app mode.

Windows is not here: it keeps its native window and its lock (:mod:`.window`, D81, D83, D108). This has no such lock,
so it is as protected as a browser tab, which the owner accepted for this release.

- **The same sign-in as the Windows window.** :func:`show` hands an adapter to ``window._sign_in_and_show`` itself. The
  probe runs inside the profile's origin and only the status comes back here, never the key. A code is made in this
  process only when the profile's key is absent or gets a 401, so a signed-in profile adds no device row. The code
  goes to the page in ``#pair=`` over the pipe, never on a command line, and is burned whether the page used it or not.
- **A pipe, never a port.** The browser is driven by DevTools over ``--remote-debugging-pipe``: it reads commands on
  file 3 and writes answers on file 4, JSON ended by one NUL byte. A debugging port would let any local program of any
  user drive the profile that holds her key. The pipe's other ends stay in this process, and when this process dies the
  pipe ends. A program of her own user can still reach it through ``/proc``, and can read the profile's files: the browser's
  password store is off, so no keyring prompt shows, and only the 0700 folder guards the key and cookie. A used code stays
  in the browser's history there, dead.
- **The file numbers.** A small Python step puts the ends on 3 and 4 just before it becomes the browser. Not a shell: dash
  (``/bin/sh`` on Debian, Ubuntu and a Jetson) takes only one-digit numbers in a redirect, and those can all be taken.
- **Links out.** The page opens them with ``window.open``. A new page on another address goes to her real browser
  (``window.to_browser``) and its window closes. A new page on the bot's own address (the dashboard) stays here.

PROPOSED: not run on a real Linux desktop, a Jetson or a Mac.
"""

from __future__ import annotations

import itertools
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from . import window

NO_SCREEN = "there is no screen here"
NO_BROWSER = "neither Chromium nor Chrome is installed"
NO_START = "its browser did not start"

HELLO_WAIT_S = 20.0
TARGET_WAIT_S = 5.0
CALL_WAIT_S = 10.0
LOAD_POLL_S = 0.1
WATCH_POLL_S = 1.0
CLOSE_WAIT_S = 5.0
MAX_BUFFER = 32 * 1024 * 1024

#: What a browser's start script reads to add flags (a debug port, say). Cleared as ``window._WEBVIEW_ENV`` is on Windows.
_BROWSER_ENV = frozenset({"CHROMIUM_FLAGS", "CHROMIUM_USER_FLAGS", "CHROME_USER_FLAGS"})
#: Becomes the browser, with the pipe on 3 and 4. Its numbers, path and flags come as arguments, so nothing is in the text.
_SPAWN_SCRIPT = ("import os, sys\n"
                 "r, w = int(sys.argv[1]), int(sys.argv[2])\n"
                 "os.dup2(r, 3)\n"
                 "os.dup2(w, 4)\n"
                 "os.close(r)\n"
                 "os.close(w)\n"
                 "os.execv(sys.argv[3], sys.argv[3:])\n")
#: What the browser asks for by itself even with the flags in :func:`_args`, seen with a network capture on Chromium 156
#: in a container, and its reputation and field-trial checks. Not asked here means no answer, so nothing goes. An app's own
#: page may still load what it wants from elsewhere, and an address not listed goes out: this is no wall.
_BROWSER_OWN_CALLS = ("update.googleapis.com", "optimizationguide-pa.googleapis.com", "content-autofill.googleapis.com",
                      "*.gvt1.com", "clients*.google.com", "accounts.google.com", "safebrowsing.googleapis.com",
                      "clientservices.googleapis.com", "android.clients.google.com")
#: A new page is on about:blank before its first load, and that is "complete" too, with no storage to ask.
_READY_JS = 'location.protocol === "about:" ? "loading" : document.readyState'


# -- can it open here --------------------------------------------------------------------------------------
def browser() -> str:
    """The full path of the browser, or ""."""
    from .zone2 import page_reader
    found = page_reader._installed()
    return found[0][1] if found else ""


def has_screen() -> bool:
    if sys.platform == "darwin":
        return not (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
    return bool(os.environ.get("DISPLAY", "").strip() or os.environ.get("WAYLAND_DISPLAY", "").strip())


def why_not() -> str:
    """Why the window can't open on this computer, in plain words, or "" when it can."""
    if not has_screen():
        return NO_SCREEN
    return "" if browser() else NO_BROWSER


# -- the profile -------------------------------------------------------------------------------------------
def _is_snap(path: str) -> bool:
    from .zone2 import page_reader
    return page_reader.is_snap(path)


def _private(folder: Path) -> None:
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)                       # one from before may be wider


def profile_dir(state: Path | str, browser_path: str) -> Path:
    """The browser's profile folder for this bot, made, for her alone. A snap browser can't write to hidden folders under
    home, where the bot folder is, so its profile is in the snap's own folder. Holds her sign-in."""
    if not _is_snap(browser_path):
        folder = window.profile_dir(state)
        folder.parent.mkdir(parents=True, exist_ok=True)
        _private(folder)
        return folder
    folder = Path.home() / "snap" / os.path.basename(browser_path) / "common"
    folder.mkdir(parents=True, exist_ok=True)
    for part in ("puffpai-window", Path(state).name, "profile"):
        folder = folder / part
        _private(folder)
    return folder


# -- one window per bot ------------------------------------------------------------------------------------
def ignore_forward() -> None:
    """A second ``puffpai app`` can't kill a window that is still starting: its signal does nothing until :func:`show`
    is ready for it."""
    sig = getattr(signal, "SIGUSR1", None)
    if sig is None:
        return
    try:
        signal.signal(sig, signal.SIG_IGN)
    except ValueError:                            # not the main thread
        pass


def bring_forward(pid: object) -> bool:
    """The running window to the front. *pid* comes from the note of a lock the OS says is held."""
    sig = getattr(signal, "SIGUSR1", None)
    if os.name == "nt" or sig is None or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, sig)
    except OSError:
        return False
    return True


def _catch_signals(forward: Any) -> Callable[[], None]:
    """From here one signal brings ``forward.pages`` to the front, once it is set, and being told to end (a logout, say)
    ends as Ctrl+C does, so a live code is still burned. Returns what puts things back. Set before the browser starts: an
    ignored signal stays ignored in a program started from here, a caught one does not."""
    def to_front(_number, _frame):
        if forward.pages is not None:
            forward.pages.bring_forward()

    def end(_number, _frame):
        raise KeyboardInterrupt
    before = []
    try:
        for name, handler in (("SIGUSR1", to_front), ("SIGTERM", end), ("SIGHUP", end)):
            sig = getattr(signal, name, None)
            if sig is not None:
                before.append((sig, signal.signal(sig, handler)))
    except ValueError:                            # not the main thread
        pass

    def put_back():
        for sig, was in reversed(before):
            signal.signal(sig, was if was is not None else signal.SIG_IGN)
    return put_back


# -- the browser and its pipe ------------------------------------------------------------------------------
class _Failed(Exception):
    """DevTools refused, did not answer in time, or the browser is gone. Its text never holds what was sent."""


def _args(port: int, profile: Path | str) -> list[str]:
    """The browser's flags. Starts on the page itself, so no raw JSON shows in a visible window. Never a code here."""
    return [f"--app={window.origin_of(port)}{window.PAGE}", f"--user-data-dir={profile}", "--remote-debugging-pipe",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--window-size=1200,850",
            "--password-store=basic", "--use-mock-keychain",            # no keyring prompt at sign-in
            # Nothing leaves your computer without your consent: no updates, no reporting, no sync, and no proxy between
            # this window and her own bot, as it may be plain http on her home network.
            "--disable-sync", "--disable-background-networking", "--no-pings", "--disable-component-update",
            "--disable-domain-reliability", "--disable-client-side-phishing-detection", "--no-proxy-server",
            "--host-resolver-rules=" + ", ".join(f"MAP {name} ~NOTFOUND" for name in _BROWSER_OWN_CALLS),
            *(["--no-sandbox"] if os.environ.get("PUFFPAI_BROWSER_NO_SANDBOX") else [])]     # D132's container


class _Process:
    """The real browser and this process's ends of its pipe."""

    def __init__(self, proc: Any, read_fd: int, write_fd: int) -> None:
        self._proc = proc
        self._read = read_fd
        self._write = write_fd

    def write(self, data: bytes) -> None:
        while data:
            data = data[os.write(self._write, data):]

    def read(self, timeout: float) -> bytes | None:
        """What the browser wrote, None for nothing yet, b"" when it is gone."""
        try:
            if not select.select([self._read], [], [], max(0.0, timeout))[0]:
                return None
            return os.read(self._read, 65536)
        except (OSError, ValueError):
            return b""

    def close(self) -> None:
        ends, self._read, self._write = (self._read, self._write), -1, -1
        for fd in ends:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        for ending in (None, self._proc.terminate, self._proc.kill):
            try:
                if ending is not None:
                    ending()
                self._proc.wait(CLOSE_WAIT_S)
                return
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                return


def _start(path: str, args: list[str]) -> _Process:
    """The browser, with a pipe on its files 3 and 4 and no other file of this process. OSError when it can't start."""
    import fcntl
    held: list[int] = []

    def moved(fd: int, low: int) -> int:
        new = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, low)
        held.append(new)
        held.remove(fd)
        os.close(fd)
        return new
    try:
        held.extend(os.pipe())
        held.extend(os.pipe())
        r, commands_out, answers_in, w = (moved(fd, 10) for fd in list(held))     # all clear of 3 and 4
        # -I and -S: nothing from her folders or her Python's add-ons runs before the browser does.
        proc = subprocess.Popen([sys.executable, "-I", "-S", "-c", _SPAWN_SCRIPT, str(r), str(w), path, *args],   # noqa: S603
                                pass_fds=(r, w), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                env={k: v for k, v in os.environ.items() if k not in _BROWSER_ENV})
    except BaseException:
        for fd in held:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    os.close(r)
    os.close(w)
    return _Process(proc, answers_in, commands_out)


class _Cdp:
    """DevTools over *ends* (``write``, ``read``, ``close``). One thread. A signal handler may :meth:`send` between two
    other steps here: a short message is one write, and each id comes from one call."""

    def __init__(self, ends: Any, on_event: Callable[[str, dict], None] | None = None) -> None:
        self.ends = ends
        self.on_event = on_event or (lambda _method, _params: None)
        self.gone = False
        self._ids = itertools.count(1)
        self._buffer = b""

    def send(self, method: str, params: dict | None = None, session: str | None = None) -> int:
        number = next(self._ids)
        message = {"id": number, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        try:
            self.ends.write(json.dumps(message).encode() + b"\0")
        except OSError:
            self.gone = True
            raise _Failed("the browser is gone") from None
        return number

    def _frame(self, timeout: float) -> dict | None:
        """The next message, or None when none came in *timeout*."""
        end = time.monotonic() + timeout
        while True:
            cut = self._buffer.find(b"\0")
            if cut >= 0:
                raw, self._buffer = self._buffer[:cut], self._buffer[cut + 1:]
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    return message
                continue
            chunk = self.ends.read(end - time.monotonic())
            if chunk == b"" or len(self._buffer) > MAX_BUFFER:
                self.gone = True
                raise _Failed("the browser is gone")
            if chunk:
                self._buffer += chunk
            elif time.monotonic() >= end:
                return None

    def _hand(self, message: dict) -> None:
        params = message.get("params")
        if isinstance(message.get("method"), str) and not message.get("sessionId"):
            self.on_event(message["method"], params if isinstance(params, dict) else {})

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float | None = None) -> dict:
        """The answer's result. Other messages on the way go to the event handling."""
        want = self.send(method, params, session)
        end = time.monotonic() + (CALL_WAIT_S if timeout is None else timeout)
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise _Failed("no answer in time")
            message = self._frame(left)
            if message is None:
                continue
            if message.get("id") == want:
                result = message.get("result")
                if "error" in message or not isinstance(result, dict):
                    raise _Failed("refused")
                return result
            self._hand(message)

    def pump(self, timeout: float) -> None:
        message = self._frame(timeout)
        if message is not None:
            self._hand(message)


class _Pages:
    """The browser's pages, from its own events: what goes to her real browser, and when the window is closed."""

    def __init__(self, cdp: _Cdp, port: int) -> None:
        self.cdp = cdp
        self.port = port
        self.seen: dict[str, str] = {}
        self.main: str | None = None
        self.done = False
        self._handed: set[str] = set()

    def _note(self, info: Any) -> None:
        if not isinstance(info, dict) or info.get("type") != "page" or not isinstance(info.get("targetId"), str):
            return
        url = info.get("url")
        self.seen[info["targetId"]] = url if isinstance(url, str) else ""
        self._hand_over(info["targetId"])

    def _ours(self, url: str) -> bool:
        return window._own_path(url, self.port) is not None

    def _hand_over(self, target: str) -> None:
        url = self.seen.get(target, "")
        if self.main is None or target == self.main or target in self._handed:
            return
        if not url or url == "about:blank" or self._ours(url):
            return
        self._handed.add(target)
        if window.to_browser(url):
            try:
                self.cdp.send("Target.closeTarget", {"targetId": target})
            except _Failed:
                pass

    def on_event(self, method: str, params: dict) -> None:
        if method in ("Target.targetCreated", "Target.targetInfoChanged"):
            self._note(params.get("targetInfo"))
        elif method == "Target.targetDestroyed":
            target = params.get("targetId")
            if isinstance(target, str) and self.seen.pop(target, None) is not None and not self.seen and self.main:
                self.close()

    def find_main(self) -> str:
        """The page the window opened on. Nothing else is handed over before it is known."""
        end = time.monotonic() + TARGET_WAIT_S
        while not self.seen:
            for info in self.cdp.call("Target.getTargets").get("targetInfos") or []:
                self._note(info)
            if self.seen:
                break
            if time.monotonic() >= end:
                raise _Failed("no page")
            self.cdp.pump(LOAD_POLL_S)
        self.main = next((t for t, url in self.seen.items() if self._ours(url)), next(iter(self.seen)))
        for target in list(self.seen):
            self._hand_over(target)
        return self.main

    def bring_forward(self) -> None:
        target = self.main if self.main in self.seen else next(iter(self.seen), None)
        if target:
            try:
                self.cdp.send("Target.activateTarget", {"targetId": target})
            except _Failed:
                pass

    def close(self) -> None:
        """No page is left. On a Mac the browser would go on running with no window."""
        self.done = True
        try:
            self.cdp.send("Browser.close")
        except _Failed:
            pass


class _Window:
    """What ``window._sign_in_and_show`` calls on a pywebview window."""

    def __init__(self, cdp: _Cdp, pages: _Pages, session: str) -> None:
        self._cdp = cdp
        self._pages = pages
        self._session = session
        self.events = SimpleNamespace(loaded=SimpleNamespace(wait=self._wait_loaded))

    def _wait_loaded(self, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while True:
            if self.run_js(_READY_JS) == "complete":
                return True
            if self._cdp.gone or time.monotonic() >= end:
                return False
            try:
                self._cdp.pump(LOAD_POLL_S)
            except _Failed:
                return False

    def run_js(self, js: str) -> Any:
        """The value, or None for any failure. A page in the middle of a reload has no context to run in, and the
        sign-in polls through that: raising here would burn the code before the page used it."""
        try:
            got = self._cdp.call("Runtime.evaluate", {"expression": js, "returnByValue": True}, self._session)
            return None if got.get("exceptionDetails") else got["result"].get("value")
        except Exception:  # noqa: BLE001 - see above
            return None

    def load_url(self, url: str) -> None:
        """With a code: there, and the page's own ``hashchange`` reloads it and reads the code. With none: nothing, as
        the window opened on that page, or has gone to its own pair screen."""
        if "#" in url:
            self._cdp.call("Page.navigate", {"url": url}, self._session, timeout=window.LOAD_WAIT_S)

    def show(self) -> None:
        self._pages.bring_forward()


def _run(ends: Any, port: int, state: Path | str, after: Callable[[Any], Any] | None, forward: Any) -> str:
    """Sign in on the browser behind *ends*, then wait until the window is closed. Always ends it."""
    cdp = _Cdp(ends)
    pages = _Pages(cdp, port)
    cdp.on_event = pages.on_event
    try:
        try:
            cdp.call("Browser.getVersion", timeout=HELLO_WAIT_S)
            cdp.call("Target.setDiscoverTargets", {"discover": True})
            main = pages.find_main()
            session = cdp.call("Target.attachToTarget", {"targetId": main, "flatten": True}).get("sessionId")
            if not isinstance(session, str) or not session:
                return NO_START
        except _Failed:
            return NO_START
        forward.pages = pages
        window._sign_in_and_show(_Window(cdp, pages, session), port, state, after)
        while not pages.done:
            try:
                cdp.pump(WATCH_POLL_S)
            except _Failed:
                break
        return ""
    except KeyboardInterrupt:                     # Ctrl+C in her terminal: the window closes, with no stack trace
        return ""
    finally:
        forward.pages = None
        ends.close()


def show(port: int, state: Path | str, after: Callable[[Any], Any] | None = None) -> str:
    """The window, until she closes it. Returns "" or the plain why (see ``window.CANT_LINE``)."""
    path = browser()
    if not path:
        return NO_BROWSER
    try:
        profile = profile_dir(state, path)
    except OSError as exc:
        return f"its profile folder could not be made ({type(exc).__name__})"
    forward = SimpleNamespace(pages=None)
    put_back = _catch_signals(forward)
    try:
        try:
            ends = _start(path, _args(port, profile))
        except (OSError, ImportError, ValueError) as exc:
            return f"its browser could not start ({type(exc).__name__})"
        return _run(ends, port, state, after, forward)
    except KeyboardInterrupt:                     # told to end before the browser was up
        return ""
    finally:
        put_back()
