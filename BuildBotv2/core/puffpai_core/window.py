r"""The PuffPAI window (D81, amended by D94): this bot's own page in a native window, on pywebview.

The default way in (the owner's change of 10-30; ``puffpai_core.opens``), and a view only: the Core runs apart from it,
with or without a window, and the chat and the dashboard stay a choice away. No new auth (D79): the page signs in with
the same one-time code, cookie and second half as any browser.

- :func:`open_window` starts the window as its own process with no console and returns at once; ``puffpai app`` is
  the same for a shell. ``puffpai app --here`` (:func:`run_here`) is the process that shows it. One window per bot:
  a second open brings the first one forward (a lock in ``<bot folder>\window``, as ``corelock`` does for the Core).
- **Her sign-in stays in ``<bot folder>\window\profile``** (cookie and second half, per bot), which the bot folder
  protects like ``tokens.db`` and ``pairing.json``: inside her own LOCALAPPDATA, off limits to Toolsmith (its zone
  covers the whole bot folder, X23). Never the data folder or the vault.
- **Sign-in (:func:`_sign_in_and_show`).** The window starts hidden on ``/v1/signin`` (public, no page code runs).
  There this process asks the profile itself: is a second half stored, and does ``GET /v1/settings`` take it? Only
  the status comes back here, never the key. 200: no code is made, so a second launch adds no device row. No key or
  401 (a removed device, say): a code is made here, in this process, so it is on no other process's command line,
  and goes to the page in ``#pair=``. Then it is burned with ``pairing.redeem`` whether the page used it or not, so
  no live code stays behind for the 10 minutes. Anything else (no answer, a 500) makes no code.
- **The lock (:func:`lock_down`, D13, D83).** pywebview alone is not safe here, tested on 6.2.1: it gives
  ``ms-settings:`` from a sandboxed frame to ``webbrowser.open``, with no click; it lets the top page go anywhere;
  ``js_bridge_call`` walks any dotted name with no underscore check (``__class__.__init__.__globals__.get``); and
  with no WebView2 it falls back to MSHTML, which none of this covers. So it runs on ``edgechromium`` only, and these
  handlers replace pywebview's: the top page may only be ``/window/`` on this bot's own address (and the hidden
  ``/v1/signin`` it starts on); a navigation WebView2 calls hers goes to her real browser (http and https only), anything else is refused; and of the
  page's calls only :class:`WindowApi` and ``alert`` pass, from the top page only.
- **Only PuffPAI's own page (D108).** The Core serves the active ``ui`` package at this same address, and a third-party
  one there would hold her key and the bridge. So the window never loads it: its page is ``/window/``, the built-in
  Desktop's own files whatever is active, and the top page may be nothing else on this address. A third-party Desktop
  she chose still runs in her browser, with that browser's own login; this profile is not reachable from there.

- **On another device (D139).** ``puffpai app --remote <address>`` runs this window on a computer with no bot, against the
  address the bot's home-network listener answers on (D130). All of the above holds, for that one host. Only the sign-in
  differs: no code can be made here, so the page goes to that listener's pair screen, and she types the bot's code once.

Importing this file never imports webview (the Core and the chat never need it).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from urllib.parse import urlsplit

from . import bot, corelock, desktop, jsonfile, pairing
from .paths import bot_name, state_dir

DESKTOP_HOST = "puffpai.localhost"
PAGE = "/window/"
PROBE_PAGE = "/v1/signin"
PAIR_PAGE = "/pair"
FOLDER = "window"
STARTING = "starting"
START_MARK_S = 30.0
KIND = "window"
MAX_URL = 2048
LOAD_WAIT_S = 30.0
SIGN_IN_WAIT_S = 15.0
DEFAULT_PORT = 8420
ICON = Path(__file__).with_name("puff.ico")

CANT_LINE = "The window can't open on this computer: {why}. Your dashboard works: {origin}"
OPENING_LINE = "The PuffPAI window is opening."
ALREADY_LINE = "The PuffPAI window for this bot is already open."
SHORTCUT_TEXT = "Opens the PuffPAI window for this bot."
NOT_RUNNING_LINE = "BuildBot is not running, so its window can't open yet. Start BuildBot first."
REMOTE_NO_ANSWER = ("Nothing answers at {origin}. Check that the bot's computer is on and BuildBot runs there, that this computer is on the same "
                    "home network, and the address: on the bot's computer, puffpai pair prints it.")
REMOTE_BAD = "That is not an address this window can use. Give the one puffpai pair prints on the bot's computer, like http://192.168.1.20:8423"
REMOTE_HERE = "That address is this computer. For a bot on this computer, run puffpai app with no address."

#: What may change the window's own browser, cleared before it starts: a debug port or a folder set from outside.
_WEBVIEW_ENV = ("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER",
                "WEBVIEW2_USER_DATA_FOLDER", "WEBVIEW2_RELEASE_CHANNEL_PREFERENCE",
                "WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER", "PYWEBVIEW_LOG", "PYWEBVIEW_GUI", "PYTHONNET_RUNTIME")
#: pywebview's settings, all off (D13). Dev tools stay off through ``debug=False``.
_PYWEBVIEW_SETTINGS = {"ALLOW_FILE_URLS": False, "ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": False,
                       "OPEN_DEVTOOLS_IN_DEBUG": False, "REMOTE_DEBUGGING_PORT": None, "IGNORE_SSL_ERRORS": False,
                       "SHOW_DEFAULT_MENUS": False, "WEBVIEW2_RUNTIME_PATH": None}
#: WebView2's own settings: no typed text kept in the profile (a key goes in a vault field), no sending addresses
#: to a reputation check (only this computer's pages load here), nothing exposed to pages.
_BROWSER_SETTINGS = {"IsPasswordAutosaveEnabled": False, "IsGeneralAutofillEnabled": False,
                     "IsReputationCheckingRequired": False, "AreHostObjectsAllowed": False}
#: The only calls a page's message may make, and then only from the top page. Everything else pywebview's bridge
#: would take (window moves, state, dotted names) is dropped.
_PAGE_CALLS = frozenset({"open_browser", "open_dashboard", "_pywebviewAlert"})
_CALL_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
#: Not in a real address (RFC 3986), and what a browser's command line or a path could take another way.
_BAD_URL = re.compile(r'[\s\x00-\x1f\x7f\\"<>^`|]')
_HOOKS = ("on_navigation_start", "on_new_window_request", "on_script_notify", "on_webview_ready",
          "on_web_resource_request")

#: Runs in the profile's own origin: says whether this profile is signed in. Only the status leaves the page.
_PROBE_JS = """(function () {
  var k = null;
  try { k = localStorage.getItem("puffpai.device_key"); } catch (e) { return "no-storage"; }
  if (!k) return "no-key";
  try {
    var x = new XMLHttpRequest();
    x.open("GET", "/v1/settings", false);
    x.setRequestHeader("X-PuffPAI-Device", k);
    x.send();
    return x.status;
  } catch (e) { return 0; }
})()"""
_SIGNED_OUT = ("no-key", 401)


#: The bot's host when this window runs on another device (D139), set once, by :func:`use_remote`. None: this computer.
_remote: str | None = None


def _host() -> str:
    return _remote or DESKTOP_HOST


def parse_remote(text: object) -> tuple[str, int]:
    """``(host, port)`` from the address ``puffpai pair`` prints, or ValueError with the plain line. http only, as D130's
    listener is; a host and a port, nothing else: this goes into a folder's name and a launcher."""
    text = str(text or "").strip().rstrip("/")
    text = text if "://" in text else "http://" + text
    try:
        parts = urlsplit(text)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise ValueError(REMOTE_BAD) from None
    if parts.scheme != "http" or not port or parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(REMOTE_BAD)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
        raise ValueError(REMOTE_BAD)
    if host == "localhost" or host.endswith(".localhost") or host.startswith("127."):
        raise ValueError(REMOTE_HERE)
    return host, port


def use_remote(host: str) -> None:
    global _remote
    _remote = host


def remote_bot_name(host: str, port: int) -> str:
    """The window's own folder, one per bot it opens. Not a bot: it holds only this window's profile and lock."""
    return f"window-{host}-{port}"[:64]


def origin_of(port: int) -> str:
    return f"http://{_host()}:{port}"


def _folder(state: Path | str) -> Path:
    return Path(state) / FOLDER


def profile_dir(state: Path | str) -> Path:
    r"""``<bot folder>\window\profile``: her sign-in, per bot (private_mode off)."""
    return _folder(state) / "profile"


# -- what the window may reach ------------------------------------------------------------------------------
def _own_path(url: object, port: int) -> str | None:
    """The path of *url* when it is on ``http://puffpai.localhost:<port>``, else None."""
    if not isinstance(url, str) or _BAD_URL.search(url):
        return None
    try:
        parts = urlsplit(url)
        got = parts.port
    except ValueError:
        return None
    ok = (parts.scheme == "http" and parts.hostname == _host() and got == port
          and parts.username is None and parts.password is None)
    return parts.path if ok else None


def is_page(url: object, port: int) -> bool:
    """True only for the window's own page at ``http://puffpai.localhost:<port>`` (D108): ``/window/``. Not ``/``, not
    ``/ui/``: those are the active Desktop's, and not any other file, which would leave her with no page."""
    return _own_path(url, port) in (PAGE, PAGE + "window.html")


def may_request(url: object, port: int) -> bool:
    """What this profile may ask of the Core (D108): an allowlist on the raw path, since the Core decodes a path first
    (``/ui%2fx.js`` is ``/ui/x.js`` to it). Only under ``/window/`` or ``/v1/``, at its own name, with no ``%`` in a
    folder part, no escaped dot or slash anywhere, no backslash, no ``//`` and no dot segment; ``/window/`` has no ``%``
    at all, as its files have none.
    The Core's other names and any odd address are refused. Everything else (a package's own origin) goes on."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        got = parts.port
        name = (parts.hostname or "").lower()
    except ValueError:
        return False
    if got != port or name not in (_host(), DESKTOP_HOST, "127.0.0.1", "localhost", "::1"):
        return True
    if name != _host() or parts.username is not None or parts.password is not None or _BAD_URL.search(url):
        return False
    path = parts.path
    if _remote and path == PAIR_PAGE:
        return True
    if not path.startswith(("/window/", "/v1/")) or "//" in path:
        return False
    folders, _sep, last = path.rpartition("/")
    if "%" in folders or (path.startswith("/window/") and "%" in last) or re.search("%(2e|2f|5c)", last, re.I):
        return False
    return not {".", ".."} & set(path.split("/"))


def may_load(url: object, port: int) -> bool:
    """What the top page may be: the window's page (:func:`is_page`), or the hidden page it starts on, with no page
    code. The bridge takes :func:`is_page` only."""
    if is_page(url, port) or (_remote and _own_path(url, port) == PAIR_PAGE):
        return True
    return isinstance(url, str) and url == origin_of(port) + PROBE_PAGE


def outside_url(url: object) -> str | None:
    """*url* when it may go to her real browser: http or https, a host, no sign-in in it, a sane length. No other
    scheme and no file path: Windows would hand those to whatever program claims them."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL or _BAD_URL.search(url):
        return None
    try:
        parts = urlsplit(url)
        parts.port                                     # noqa: B018 - a bad port raises here
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    return url


def open_tab(url: str) -> bool:
    """A new tab of her real browser (D13). True when it was handed over."""
    import webbrowser
    try:
        return bool(webbrowser.open_new_tab(url))
    except Exception:  # noqa: BLE001 - no browser: the caller says it did not open
        return False


def to_browser(url: object) -> bool:
    clean = outside_url(url)
    return open_tab(clean) if clean else False


class WindowApi:
    """The page's ``window.pywebview.api``. Reachable from the top page only (:func:`lock_down`). Nothing else: no
    file access, no key access. A code made here goes to her browser, never back to the page."""

    def __init__(self, port: int, state: Path | str) -> None:
        self._port = port
        self._state = state

    def open_browser(self, url):
        """A new tab of her real browser. http and https only."""
        return to_browser(url)

    def open_dashboard(self):
        """Her dashboard in her real browser, with a one-time link only when the active Desktop is PuffPAI's own
        (``desktop.dashboard_link``, D108). ``{"ok", "line"}``: never the code, which goes to her browser alone. On another
        device (D139) no code can be made here: that browser pairs on its own, on the same address."""
        if _remote:
            return {"ok": open_tab(origin_of(self._port) + "/"), "line": ""}
        try:
            link = desktop.dashboard_link(self._state, self._port)
        except Exception:  # noqa: BLE001 - the state folder can't be written: say it did not open
            return {"ok": False, "line": ""}
        opened = open_tab(link.url)
        if link.code and not opened:
            try:
                pairing.redeem(self._state, link.code)    # no browser took it: no live code stays behind
            except Exception:  # noqa: BLE001 - it ends by itself in 10 minutes
                pass
        return {"ok": opened, "line": link.line}


def lock_down(edge: Any, port: int, on_fail: Callable[[str], None]) -> None:
    """Replace pywebview's handlers on its WebView2 class (see the file's top). A handler that fails refuses, and a
    version of pywebview without these hooks does not open at all: this file never runs without the lock."""
    missing = [name for name in _HOOKS if not callable(getattr(edge, name, None))]
    if missing:
        raise RuntimeError("this pywebview has no " + ", ".join(missing))
    real_start, real_notify, real_ready = edge.on_navigation_start, edge.on_script_notify, edge.on_webview_ready
    real_resource = edge.on_web_resource_request

    def on_navigation_start(self, sender, args):
        try:
            uri = str(args.get_Uri())
            if not may_load(uri, port):
                args.Cancel = True
                # WebView2's word for it. A frame only gets it from her click. The top page, which is only ours
                # (is_page), also gets it for a script just after a load, and a redirect of a navigation that
                # had it keeps it. So this is no proof of a click, only of who may ask: http and https, one tab.
                if args.IsUserInitiated:
                    to_browser(uri)
                return None
        except Exception:  # noqa: BLE001 - fail closed
            args.Cancel = True
            return None
        return real_start(self, sender, args)

    def on_web_resource_request(self, sender, args):
        # NavigationStarting says no only after the request has gone out, so without this a refused top page would
        # still have fetched the file. Answered here, it never leaves this process (D108). A failure refuses; one that
        # can't even refuse closes the window.
        try:
            if may_request(str(args.Request.Uri), port):
                return real_resource(self, sender, args)
        except Exception:  # noqa: BLE001 - fail closed
            pass
        try:
            args.Response = sender.Environment.CreateWebResourceResponse(None, 403, "Refused", "")
        except Exception:  # noqa: BLE001
            on_fail("its browser part could not refuse a request")
        return None

    def on_new_window_request(self, sender, args):
        args.set_Handled(True)                    # never a second window, and pywebview's own hand-over never runs
        try:
            if args.IsUserInitiated:              # WebView2 has no popup blocker of its own
                to_browser(str(args.get_Uri()))
        except Exception:  # noqa: BLE001 - fail closed: nothing opens
            pass

    def on_script_notify(self, sender, args):
        try:
            if not is_page(str(args.get_Source()), port):
                return None                       # a frame never reaches the bridge
            call = json.loads(args.get_WebMessageAsJson())
            if not (isinstance(call, list) and len(call) == 3 and call[0] in _PAGE_CALLS
                    and isinstance(call[2], str) and _CALL_ID.fullmatch(call[2])):
                return None
        except Exception:  # noqa: BLE001 - fail closed
            return None
        return real_notify(self, sender, args)

    def on_webview_ready(self, sender, args):
        if not args.IsSuccess:                    # pywebview only logs this, and a hidden window would wait forever
            on_fail("its browser part (WebView2) did not start")
            return None
        core = sender.CoreWebView2
        for name, value in _BROWSER_SETTINGS.items():
            try:
                setattr(core.Settings, name, value)
            except Exception:  # noqa: BLE001 - an older WebView2 has no such setting
                pass
        try:
            sender.AllowExternalDrop = False      # a dropped file would try to open as the page
        except Exception:  # noqa: BLE001 - the lock refuses that navigation anyway
            pass
        return real_ready(self, sender, args)

    edge.on_navigation_start = on_navigation_start
    edge.on_new_window_request = on_new_window_request
    edge.on_web_resource_request = on_web_resource_request
    edge.on_script_notify = on_script_notify
    edge.on_webview_ready = on_webview_ready


# -- can it open here --------------------------------------------------------------------------------------
def _reg_string(root: Any, path: str, name: str) -> Any:
    import winreg
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _has_webview2() -> bool:
    """The WebView2 runtime, where Edge's updater writes it (what pywebview reads too)."""
    import winreg
    guids = ("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
             "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}", "{65C35B14-6C1D-4122-AC46-7148CC9D6497}")
    for guid in guids:
        for root, path in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
                           (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
                           (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}")):
            build = _reg_string(root, path, "pv")
            if build and str(build) != "0.0.0.0":
                return True
    return False


def _has_dotnet() -> bool:
    """.NET Framework 4.6.2 or later (Release 394802), which pywebview needs on Windows."""
    import winreg
    release = _reg_string(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full", "Release")
    return isinstance(release, int) and release >= 394802


_imports_load: list[bool] = []


def _webview_imports() -> bool:
    """A real import of pywebview and its .NET bridge, in a child process, once per process: a broken install passes
    ``find_spec``, and this process (the Core, the chat) never imports them."""
    if not _imports_load:
        try:
            done = subprocess.run([sys.executable, "-P", "-c", "import webview, clr"], capture_output=True,  # noqa: S603
                                  stdin=subprocess.DEVNULL, timeout=60,
                                  creationflags=0x08000000 if os.name == "nt" else 0)   # CREATE_NO_WINDOW
            _imports_load.append(done.returncode == 0)
        except (OSError, subprocess.SubprocessError):
            _imports_load.append(False)
    return _imports_load[0]


def why_not() -> str:
    """Why the window can't open on this computer, in plain words, or "" when it can. Imports nothing into this
    process."""
    if os.name != "nt":
        return "it is built for Windows only so far"
    if importlib.util.find_spec("webview") is None:
        return "pywebview is not installed"
    if importlib.util.find_spec("pythonnet") is None:
        return "pythonnet, which pywebview needs, is not installed"
    if not _has_dotnet():
        return ".NET Framework 4.6.2 or later is not installed"
    if not _has_webview2():
        return "the Microsoft Edge WebView2 Runtime is not installed"
    if not _webview_imports():
        return "pywebview is installed but does not load"
    return ""


def cant_line(why: str, port: int) -> str:
    return CANT_LINE.format(why=why, origin=origin_of(port))


# -- saying things, with or without a console --------------------------------------------------------------
def _box(text: str, yes_no: bool = False) -> bool:
    """A plain Windows message, for a window process and a shortcut, which have no console. True for Yes."""
    if os.name != "nt":
        return False
    import ctypes
    MB_OK, MB_YESNO, MB_ICONINFORMATION, MB_SETFOREGROUND, IDYES = 0, 4, 0x40, 0x10000, 6
    flags = (MB_YESNO if yes_no else MB_OK) | MB_ICONINFORMATION | MB_SETFOREGROUND
    return ctypes.windll.user32.MessageBoxW(None, text, "PuffPAI", flags) == IDYES


def tell(line: str) -> None:
    """One plain line: on the console, or as a message when there is none (pythonw has no ``sys.stdout``)."""
    if sys.stdout is None:
        _box(line)
    else:
        print(line, flush=True)


def offer_dashboard(line: str, port: int, state: Path | str) -> None:
    """The window could not open (D81): the plain line, and an offer of the dashboard. Never a stack trace."""
    if sys.stdout is None:
        yes = _box(line + "\n\nOpen your dashboard now?", yes_no=True)
    else:
        print(line, flush=True)
        if sys.stdin is None or not sys.stdin.isatty():
            return
        try:
            yes = input("  [d] open the dashboard   [Enter] close  > ").strip().lower() == "d"
        except (EOFError, KeyboardInterrupt):
            return
    if yes:
        said = WindowApi(port, state).open_dashboard()["line"]
        if said:
            tell(said)


# -- one window per bot -------------------------------------------------------------------------------------
def _bring_forward(pid: object) -> bool:
    """The running window to the front. *pid* comes from the note of a lock the OS says is held, so it is that
    window's process, not a reused number. Windows may flash it on the taskbar instead."""
    if os.name != "nt" or not isinstance(pid, int) or pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    each_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [each_t, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    for name in ("IsWindowVisible", "IsIconic", "GetWindowTextLengthW", "SetForegroundWindow"):
        getattr(user32, name).argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    found: list[Any] = []

    def each(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
            return False
        return True
    user32.EnumWindows(each_t(each), 0)
    if not found:
        return False
    if user32.IsIconic(found[0]):
        user32.ShowWindow(found[0], 9)            # SW_RESTORE
    return bool(user32.SetForegroundWindow(found[0]))


def _windowless_python() -> str:
    """``pythonw.exe`` next to this Python: no console, so no flash."""
    here = Path(sys.executable)
    quiet = here.with_name("pythonw.exe")
    return str(quiet) if quiet.is_file() else str(here)


def _spawn(state: Path | str, remote: str = "") -> None:
    """``puffpai app --here`` as its own process: no console, and it outlives what asked for it. Its port comes from
    the running Core, not from this command line, or from *remote*, the bot's address. ``-P``: no module from the folder
    it starts in."""
    argv = [_windowless_python(), "-P", "-m", "puffpai_core.cli", "app", "--here"] + (["--remote", remote] if remote else [])
    if os.name != "nt":
        subprocess.Popen(argv, start_new_session=True, close_fds=True)    # noqa: S603 - this program's own window
        return
    DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP = 0x00000008, 0x00000200
    subprocess.Popen(argv, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,   # noqa: S603
                     close_fds=True, cwd=str(state))


def _claim_start(state: Path | str) -> bool:
    """One start at a time (a window takes a moment to take its lock): a mark file made atomically, which the window
    process removes once it holds the lock or has given up. Older than :data:`START_MARK_S` means it died. True when
    this caller may start it. OSError when the bot folder can't be written."""
    mark = _folder(state) / STARTING
    mark.parent.mkdir(parents=True, exist_ok=True)
    for _try in range(2):
        try:
            os.close(os.open(mark, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - mark.stat().st_mtime < START_MARK_S:
                    return False
                mark.unlink()
            except FileNotFoundError:
                pass
    return False


def _clear_start(state: Path | str) -> None:
    try:
        (_folder(state) / STARTING).unlink(missing_ok=True)
    except OSError:
        pass


def open_window(port: int, state: Path | str | None = None, remote: str = "") -> tuple[bool, str]:
    """The window for this bot, as its own process, so what asked is not blocked. Returns at once: (it opened, one
    plain line to say). With no way to open it here: (False, that line, with the dashboard's address). *state*: the
    bot folder, when the caller (the Core's own route) has it."""
    state = Path(state) if state is not None else state_dir()
    why = why_not()
    if why:
        return False, cant_line(why, port)
    held = corelock.holder(_folder(state))
    if held is not None:
        _bring_forward(held.get("pid"))
        return True, ALREADY_LINE
    try:
        if not _claim_start(state):
            return True, OPENING_LINE            # it is starting right now: never a second one
        _spawn(state, remote) if remote else _spawn(state)
    except OSError as exc:
        _clear_start(state)
        return False, cant_line(f"it could not start ({type(exc).__name__})", port)
    return True, OPENING_LINE


# -- the process that shows it -------------------------------------------------------------------------------
def running_port(state: Path | str) -> int | None:
    """The port of this bot's running Core, or None. The window never starts a Core of its own (D91). A key the Core
    refuses still counts (the lock says it runs, as ``pair --running`` reads it): sign-in needs no terminal key."""
    from .cli import _terminal
    info = _terminal()
    if info is not None:
        return int(info["port"])
    held = corelock.holder(state)
    if held is None or held.get("kind") == corelock.TOOL:
        return None
    try:
        return int(held.get("port") or 0) or None
    except (TypeError, ValueError):
        return None


def _title(state: Path | str) -> str:
    try:
        return bot.read(state)["name"] or "BuildBot"
    except Exception:  # noqa: BLE001 - no name to read: the plain one
        return "BuildBot"


def _wait_signed_in(window: Any) -> bool:
    end = time.monotonic() + SIGN_IN_WAIT_S
    while time.monotonic() < end:
        if window.run_js(_PROBE_JS) == 200:
            return True
        time.sleep(0.25)
    return False


def _sign_in_and_show(window: Any, port: int, state: Path | str, after: Callable[[Any], Any] | None = None) -> None:
    """Runs once the hidden window is on ``/v1/signin``. See the file's top for why it signs in this way."""
    code = None
    try:
        window.events.loaded.wait(LOAD_WAIT_S)
        if not _remote and window.run_js(_PROBE_JS) in _SIGNED_OUT:
            code = pairing.new_code(state)
        window.load_url(origin_of(port) + PAGE + (f"#pair={code}" if code else ""))
        window.events.loaded.wait(LOAD_WAIT_S)
        if code:
            _wait_signed_in(window)
    except Exception:  # noqa: BLE001 - she still gets the window, and its own sign-in screen
        pass
    finally:
        if code:
            try:
                pairing.redeem(state, code)   # used already: nothing happens. Not used: no live code stays behind
            except Exception:  # noqa: BLE001 - it ends by itself in 10 minutes
                pass
        try:
            window.show()
        except Exception:  # noqa: BLE001 - closed already
            pass
    if after is not None:
        after(window)


def _show(port: int, state: Path, after: Callable[[Any], Any] | None = None) -> str:
    """The window, until she closes it. Returns "" or why it could not open (the plain words of :func:`cant_line`)."""
    import webview
    from webview.guilib import initialize
    logging.getLogger("pywebview").setLevel(logging.CRITICAL)      # no address (no code) in any log
    for key, value in _PYWEBVIEW_SETTINGS.items():
        webview.settings[key] = value
    gui = initialize("edgechromium")
    if getattr(gui, "renderer", "") != "edgechromium":             # pywebview's fallback is MSHTML: never that
        return "the Microsoft Edge WebView2 Runtime did not load"
    from webview.platforms import edgechromium
    failed: list[str] = []

    def fail(why: str) -> None:
        if failed:
            return
        failed.append(why)
        threading.Thread(target=window.destroy, daemon=True).start()
    lock_down(edgechromium.EdgeChrome, port, fail)
    profile = profile_dir(state)
    profile.mkdir(parents=True, exist_ok=True)
    title = f"BuildBot on {_remote}" if _remote else _title(state)
    window = webview.create_window(title, origin_of(port) + PROBE_PAGE, js_api=WindowApi(port, state),
                                   width=1200, height=850, min_size=(800, 500), hidden=True, text_select=True)
    webview.start(_sign_in_and_show, args=(window, port, state, after), gui="edgechromium", debug=False,
                  private_mode=False, storage_path=str(profile), http_server=False,
                  icon=str(ICON) if ICON.is_file() else None)
    return failed[0] if failed else ""


def answers(port: int) -> bool:
    """True when the bot's listener answers at this address (D139), so a wrong address is said in words, not an empty
    window. No proxy: it is on her own network."""
    import urllib.request
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(origin_of(port) + PROBE_PAGE, timeout=5) as resp:
            return resp.status == 200
    except OSError:
        return False


def run_here(after: Callable[[Any], Any] | None = None, remote_port: int | None = None) -> int:
    """``puffpai app --here``: show the window in this process, until she closes it. *after* is for the tests that
    drive a real window; the command line can't set it. *remote_port*: the bot is on another device (D139)."""
    state = state_dir()
    try:
        port = remote_port if remote_port is not None else running_port(state)
        if port is None:
            tell(NOT_RUNNING_LINE)
            return 1
        why = why_not()
        if why:
            offer_dashboard(cant_line(why, port), port, state)
            return 1
        try:
            lock = corelock.acquire(_folder(state), port=port, kind=KIND, wait_s=corelock.START_WAIT_S)
        except corelock.AlreadyRunning as exc:
            _bring_forward(exc.info.get("pid"))
            return 0
        except corelock.LockError as exc:
            offer_dashboard(cant_line(str(exc).rstrip("."), port), port, state)
            return 1
    finally:
        _clear_start(state)
    for name in _WEBVIEW_ENV:
        os.environ.pop(name, None)
    try:
        why = _show(port, Path(state), after)
    except Exception as exc:  # noqa: BLE001 - no pythonnet, no .NET: a plain line, never a stack trace
        why = f"its window parts did not start ({type(exc).__name__})"
    finally:
        lock.release()
    if why:
        offer_dashboard(cant_line(why, port), port, state)
        return 1
    return 0


# -- shortcuts --------------------------------------------------------------------------------------------------
_RECORD = "shortcuts.json"
#: The values reach PowerShell in environment variables, never in this text, so no bot name can change the command.
_SHORTCUT_PS = ("$l=(New-Object -ComObject WScript.Shell).CreateShortcut($env:PUFFPAI_LNK_PATH);"
                "$l.TargetPath=$env:PUFFPAI_LNK_TARGET;$l.Arguments=$env:PUFFPAI_LNK_ARGS;"
                "$l.WorkingDirectory=$env:PUFFPAI_LNK_DIR;$l.Description=$env:PUFFPAI_LNK_TEXT;"
                "if($env:PUFFPAI_LNK_ICON){$l.IconLocation=$env:PUFFPAI_LNK_ICON};$l.Save()")


def _shell_folders() -> list[Path]:
    """Her Start menu's Programs folder and her desktop, where Windows says they are (OneDrive may move a desktop)."""
    import ctypes
    out = []
    for csidl in (0x0002, 0x0010):               # CSIDL_PROGRAMS, CSIDL_DESKTOPDIRECTORY
        buf = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            out.append(Path(buf.value))
    return out


def _link_stem(name: object) -> str:
    """"PuffPAI <bot>" as a file name. A bot's name is her own text: anything a file name can't hold goes, and so do
    control and direction characters."""
    text = "".join(c for c in str(name or "") if c not in '<>:"/\\|?*' and unicodedata.category(c)[0] != "C")
    return "PuffPAI " + (" ".join(text.split()).strip(" .") or "BuildBot")


def _read_record(state: Path | str) -> list[str]:
    data = jsonfile.read(_folder(state) / _RECORD, {})
    paths = data.get("paths") if isinstance(data, dict) else None
    return [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []


def _shortcut_args(state: Path | str, port: int) -> str:
    """What a shortcut runs: this bot's window through :func:`_shortcut_main`, no console, no code in a state file."""
    args = ["-P", "-m", "puffpai_core.window"]
    args += ["--state", str(state)] if os.environ.get("PUFFPAI_STATE", "").strip() else ["--bot", bot_name()]
    if port != DEFAULT_PORT:
        args += ["--port", str(port)]
    return subprocess.list2cmdline(args)


def _save_link(path: Path, target: str, args: str, text: str) -> bool:
    """One ``.lnk``. WScript.Shell can't save to a name outside the Windows code page (a bot called in Japanese), so
    it saves in a plain temporary folder and Python moves the file to its real name."""
    import shutil
    import tempfile
    shell = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32/WindowsPowerShell/v1.0/powershell.exe"
    CREATE_NO_WINDOW = 0x08000000
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="puffpai-link-") as scratch:
            first = Path(scratch) / "link.lnk"
            env = {**os.environ, "PUFFPAI_LNK_PATH": str(first), "PUFFPAI_LNK_TARGET": target, "PUFFPAI_LNK_ARGS": args,
                   "PUFFPAI_LNK_DIR": str(Path(target).parent), "PUFFPAI_LNK_TEXT": text,
                   "PUFFPAI_LNK_ICON": str(ICON) if ICON.is_file() else ""}
            done = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", _SHORTCUT_PS],   # noqa: S603
                                  env=env, capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                                  creationflags=CREATE_NO_WINDOW)
            if done.returncode != 0 or not first.is_file():
                return False
            shutil.move(str(first), str(path))
    except (OSError, subprocess.SubprocessError):
        return False
    return path.is_file()


def make_shortcuts(state: Path | str, name: object = None, port: int = DEFAULT_PORT,
                   folders: list[Path] | None = None) -> tuple[bool, str]:
    """A "PuffPAI <bot>" shortcut in the Start menu and on the desktop (*folders* in a test), running the window for
    THIS bot. Another bot's shortcut of the same name is left alone. Returns (made, one plain line).
    ``remove_shortcuts`` undoes it."""
    if os.name != "nt":
        return False, "Shortcuts are built for Windows only so far."
    state = Path(state)
    name = name if name is not None else _title(state)
    stem = _link_stem(name)
    ours = {os.path.normcase(p) for p in _read_record(state)}
    target = _windowless_python()
    made: list[str] = []
    for folder in (folders if folders is not None else _shell_folders()):
        path = folder / f"{stem}.lnk"
        if path.exists() and os.path.normcase(str(path)) not in ours:
            path = folder / f"{stem} ({state.name}).lnk"
        if _save_link(path, target, _shortcut_args(state, port), SHORTCUT_TEXT):
            made.append(str(path))
    if made:
        (_folder(state)).mkdir(parents=True, exist_ok=True)
        jsonfile.write(_folder(state) / _RECORD, {"paths": sorted({*_read_record(state), *made})})
        return True, "Shortcuts made: " + "; ".join(made) + ". puffpai app --no-shortcuts removes them."
    return False, "The shortcuts could not be made. puffpai app still opens the window."


def remove_shortcuts(state: Path | str) -> str:
    """Remove the shortcuts this bot made (its record says which; only ``.lnk`` files). One plain line."""
    gone = []
    for text in _read_record(state):
        path = Path(text)
        if path.suffix.lower() == ".lnk":
            try:
                path.unlink(missing_ok=True)
                gone.append(text)
            except OSError:
                pass
    jsonfile.write(_folder(state) / _RECORD, {"paths": [p for p in _read_record(state) if p not in gone]})
    return ("Shortcuts removed: " + "; ".join(gone) + ".") if gone else "There were no shortcuts to remove."


def _shortcut_main(argv: list[str] | None = None) -> int:
    """What a shortcut runs, since a ``.lnk`` can't set ``PUFFPAI_BOT``: names the bot, then ``puffpai app``."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--bot")
    p.add_argument("--state")
    p.add_argument("--port", type=int)
    args, _ = p.parse_known_args(argv)
    if args.state:
        os.environ["PUFFPAI_STATE"] = args.state
    else:
        os.environ.pop("PUFFPAI_STATE", None)
        if args.bot:
            os.environ["PUFFPAI_BOT"] = args.bot
    from .cli import main
    return main(["app"] + (["--port", str(args.port)] if args.port else []))


if __name__ == "__main__":
    sys.exit(_shortcut_main())
