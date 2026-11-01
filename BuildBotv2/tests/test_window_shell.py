"""The PuffPAI window (D81, D13, D83, D79, D94): the shell's rules, with pywebview stubbed.

No test here opens a window or touches her real desktop or Start menu (conftest refuses both). What a real WebView2
does (the frames, the lock, the sign-in) is checked by hand with real windows; this file holds the rules in place:
what the top page may be, what goes to her browser, what a page may call, when a code is made, how it fails.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core import cli, corelock, opens, pairing, window

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows shortcuts")
PORT = 51234
WEB = REPO / "packages" / "desktop" / "web"
OWN = f"http://puffpai.localhost:{PORT}"


# -- importing it needs no webview --------------------------------------------------------------------------
def test_importing_the_window_and_the_cli_does_not_import_webview():
    code = ("import sys, puffpai_core.window, puffpai_core.cli, puffpai_core.termchat;"
            "sys.exit(1 if {'webview', 'clr', 'pythonnet'} & set(sys.modules) else 0)")
    env = {**os.environ, "PYTHONPATH": str(REPO / "core")}
    assert subprocess.run([sys.executable, "-c", code], env=env, timeout=120).returncode == 0


# -- what the top page may be, and what her browser may be given ----------------------------------------------
@pytest.mark.parametrize("url", [OWN + "/window/", OWN + "/window/#pair=AAAA", OWN + "/window/window.html",
                                 f"HTTP://PUFFPAI.LOCALHOST:{PORT}/window/"])
def test_own_pages(url):
    assert window.is_page(url, PORT) and window.may_load(url, PORT)


def test_the_hidden_start_may_load_and_gets_no_bridge():
    assert window.may_load(OWN + "/v1/signin", PORT) and not window.is_page(OWN + "/v1/signin", PORT)
    assert not window.may_load(OWN + "/v1/signin?x=1", PORT)


@pytest.mark.parametrize("url", [
    OWN + "/window/index.html", OWN + "/window/logic.js",
    OWN, OWN + "/", OWN + "/#pair=AAAA", OWN + "/ui/window.html", OWN + "/ui/x.html", OWN + "/v1/recover", OWN + "/window",
    OWN + "/WINDOW/", OWN + "/windowx/", OWN + "/window/../ui/window.html", OWN + "/window/%2e%2e/ui/window.html",
    OWN + "/window/%2E%2E/ui/window.html", OWN + "/window/..%2fui%2fwindow.html", OWN + "/window/x/%5c",
    f"https://puffpai.localhost:{PORT}/window/", "http://puffpai.localhost/window/", "http://puffpai.localhost:51235/window/",
    f"http://127.0.0.1:{PORT}/window/", f"http://localhost:{PORT}/window/", f"http://evilframe.localhost:{PORT}/window/",
    f"http://puffpai.localhost.evil.test:{PORT}/window/", f"http://x@puffpai.localhost:{PORT}/window/",
    f"http://puffpai.localhost:{PORT}@evil.test/window/", f"http://evil.test/#http://puffpai.localhost:{PORT}/window/",
    f"http://puffpai.localhost:{PORT}\\@evil.test/window/", "file:///C:/Windows/win.ini", "about:blank", "data:text/html,x",
    f"view-source:{OWN}/window/", f"{OWN}/window/a b", "http://[bad/", "", None, 5, ["x"]])
def test_not_own_pages(url):
    assert not window.may_load(url, PORT) and not window.is_page(url, PORT)


@pytest.mark.parametrize("url", ["http://127.0.0.1:18555/", "https://example.test/a?b=c#d", "HTTP://EXAMPLE.TEST/",
                                 "http://[::1]:8080/x%20y"])
def test_what_may_go_to_her_browser(url):
    assert window.outside_url(url) == url


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/win.ini", "ms-settings:", "javascript:alert(1)", "data:text/html,x", "mailto:a@b.test",
    "C:\\Windows\\win.ini", "\\\\host\\share\\x", "/etc/passwd", "win.ini", "http:///no-host", "http://", "https:foo",
    "http://u:p@example.test/", "http://u@example.test/", "http://example.test/a b", "http://example.test/\r\nX: y",
    'http://example.test/"--flag', "http://example.test\\@evil.test/", "http://example.test:99999/",
    "http://x/" + "a" * window.MAX_URL, "", None, 5, {"a": 1}, ["http://x/"]])
def test_what_never_goes_to_her_browser(url, monkeypatch):
    monkeypatch.setattr(window, "open_tab", lambda u: pytest.fail("this must not reach a browser"))
    assert window.outside_url(url) is None
    assert window.to_browser(url) is False


# -- the lock ----------------------------------------------------------------------------------------------
class FakeEdge:
    """pywebview's WebView2 class: which of its handlers ran."""

    def __init__(self):
        self.ran = []

    def on_navigation_start(self, sender, args):
        self.ran.append("start")

    def on_web_resource_request(self, sender, args):
        self.ran.append("the network")

    def on_new_window_request(self, sender, args):
        self.ran.append("pywebview's hand-over")

    def on_script_notify(self, sender, args):
        self.ran.append("notify")

    def on_webview_ready(self, sender, args):
        self.ran.append("ready")


class FakeArgs:
    def __init__(self, uri="", user=False, source="", message="[]", success=True):
        self._uri, self.IsUserInitiated, self._source, self._message = uri, user, source, message
        self.IsSuccess = success
        self.Cancel = False
        self.handled = False

    def get_Uri(self):
        if isinstance(self._uri, Exception):
            raise self._uri
        return self._uri

    def get_Source(self):
        return self._source

    def get_WebMessageAsJson(self):
        return self._message

    def set_Handled(self, value):
        self.handled = value


@pytest.fixture
def locked(monkeypatch):
    class Edge(FakeEdge):
        pass
    opened, failed = [], []
    monkeypatch.setattr(window, "open_tab", lambda url: opened.append(url) or True)
    window.lock_down(Edge, PORT, failed.append)
    return types.SimpleNamespace(edge=Edge(), opened=opened, failed=failed)


class _Request:
    def __init__(self, uri):
        self.Uri = uri


class _Env:
    def CreateWebResourceResponse(self, stream, code, why, headers):   # noqa: N802 - WebView2's own name
        return ("response", code, why)


@pytest.mark.parametrize("uri", [
    OWN, OWN + "/", OWN + "/?x=1", OWN + "/ui/", OWN + "/ui/x.js", OWN + "/ui/window.html",
    OWN + "/ui%2fx.js", OWN + "/%75i/x.js", OWN + "//ui/x.js", OWN + "/UI/x.js", OWN + "/ui\\x.js", OWN + "/x.js", OWN + "/favicon.ico",
    OWN + "/window", OWN + "/window//x.js", OWN + "/window/./x.js", OWN + "/window/../ui/x.js", OWN + "/window/%2e%2e/ui/x.js",
    OWN + "/window/..%2fui%2fx.js", OWN + "/window/%2e%2e%2fui%2fx.js", OWN + "/window/a%2fb.js", OWN + "/window/pages%2f/x.js",
    OWN + "/v1", OWN + "/v1//x", OWN + "/v1/./x", OWN + "/v1/../ui/x.js", OWN + "/v1/%2e%2e/ui/x.js", OWN + "/v1/a%2fb/c", OWN + "/v1/..%2fui%2fx.js", OWN + "/v1/%2E%2E%2Fui",
    f"http://x@puffpai.localhost:{PORT}/window/", f"http://127.0.0.1:{PORT}/ui/x.js", f"http://localhost:{PORT}/v1/settings",
    f"http://[::1]:{PORT}/ui/x.js", f"http://puffpai.localhost:{PORT}:9/", "http://[bad/"])
def test_this_profile_asks_the_core_only_for_its_own_paths(locked, uri):
    """An allowlist on the raw path: the Core decodes a path first, so a list of bad ones can't be right (D108)."""
    args = types.SimpleNamespace(Request=_Request(uri), Response=None)
    locked.edge.on_web_resource_request(types.SimpleNamespace(Environment=_Env()), args)
    assert args.Response == ("response", 403, "Refused") and locked.edge.ran == []


@pytest.mark.parametrize("uri", [OWN + "/window/", OWN + "/window/?x=1", OWN + "/window/logic.js", OWN + "/window/pages/x.js",
                                 OWN + "/window/fonts/nunito/Nunito.woff2", OWN + "/v1/settings", OWN + "/v1/events",
                                 OWN + "/v1/signin", OWN + "/v1/packages/notes/frame", OWN + "/v1/a/b%20c?x=%2f",
                                 "http://example-page.localhost:51235/", "http://example-page.localhost:51235/ui/x.js",
                                 "http://127.0.0.1:51235/ui/x.js", "http://puffpai.localhost:51235/ui/x.js", "data:text/plain,x"])
def test_everything_else_goes_on(locked, uri):
    args = types.SimpleNamespace(Request=_Request(uri), Response=None)
    locked.edge.on_web_resource_request(types.SimpleNamespace(Environment=_Env()), args)
    assert args.Response is None and locked.edge.ran == ["the network"]


def test_only_text_is_an_address():
    assert not window.may_request(None, PORT) and not window.may_request(5, PORT)


def test_a_request_handler_that_fails_refuses(locked, monkeypatch):
    def boom(*_a):
        raise RuntimeError("no")
    monkeypatch.setattr(window, "may_request", boom)
    args = types.SimpleNamespace(Request=_Request(OWN + "/window/"), Response=None)
    locked.edge.on_web_resource_request(types.SimpleNamespace(Environment=_Env()), args)
    assert args.Response == ("response", 403, "Refused") and locked.edge.ran == [] and locked.failed == []


def test_a_handler_that_cant_even_refuse_closes_the_window(locked):
    args = types.SimpleNamespace(Request=_Request(OWN + "/ui/x.js"), Response=None)
    locked.edge.on_web_resource_request(types.SimpleNamespace(), args)     # no Environment: the answer can't be made
    assert args.Response is None and locked.failed == ["its browser part could not refuse a request"]


def test_a_pywebview_without_the_hooks_does_not_open_at_all():
    class Old:
        def on_navigation_start(self, s, a):
            pass
    with pytest.raises(RuntimeError, match="no on_new_window_request"):
        window.lock_down(Old, PORT, lambda why: None)


def test_the_top_page_stays_on_its_own_address(locked):
    args = FakeArgs(OWN + "/window/#pair=AAAA", user=False)
    locked.edge.on_navigation_start(None, args)
    assert not args.Cancel and locked.edge.ran == ["start"] and locked.opened == []


def test_a_click_outside_goes_to_her_browser_and_the_window_stays(locked):
    args = FakeArgs("https://example.test/", user=True)
    locked.edge.on_navigation_start(None, args)
    assert args.Cancel and locked.edge.ran == [] and locked.opened == ["https://example.test/"]


@pytest.mark.parametrize("uri", ["https://example.test/", f"http://127.0.0.1:{PORT}/", "about:blank"])
def test_a_script_or_a_redirect_outside_is_refused_with_no_tab(locked, uri):
    args = FakeArgs(uri, user=False)
    locked.edge.on_navigation_start(None, args)
    assert args.Cancel and locked.opened == [] and locked.edge.ran == []


@pytest.mark.parametrize("uri", ["file:///C:/Windows/win.ini", "ms-settings:", "javascript:alert(1)"])
def test_a_click_on_another_scheme_is_refused(locked, uri):
    args = FakeArgs(uri, user=True)
    locked.edge.on_navigation_start(None, args)
    assert args.Cancel and locked.opened == []


def test_a_failure_in_the_lock_refuses(locked):
    args = FakeArgs(RuntimeError("boom"))
    locked.edge.on_navigation_start(None, args)
    assert args.Cancel and locked.edge.ran == []


def test_a_new_window_never_opens_and_pywebviews_hand_over_never_runs(locked):
    for uri, user, want in (("https://example.test/", True, ["https://example.test/"]), ("https://example.test/", False, []),
                            ("ms-settings:", True, []), ("ms-settings:", False, []), ("file:///C:/x", True, [])):
        locked.opened.clear()
        args = FakeArgs(uri, user=user)
        locked.edge.on_new_window_request(None, args)
        assert args.handled and locked.opened == want, uri
    bad = FakeArgs(RuntimeError("boom"), user=True)
    locked.edge.on_new_window_request(None, bad)
    assert bad.handled and "pywebview's hand-over" not in locked.edge.ran


def _call(name, params="[]", call_id="123"):
    return json.dumps([name, params, call_id])


@pytest.mark.parametrize("name", ["open_browser", "open_dashboard", "_pywebviewAlert"])
def test_the_top_page_may_make_the_calls_it_has(locked, name):
    locked.edge.on_script_notify(None, FakeArgs(source=OWN + "/window/", message=_call(name)))
    assert locked.edge.ran == ["notify"]


@pytest.mark.parametrize("source", ["http://evilframe.localhost:51235/", f"http://127.0.0.1:{PORT}/", "about:blank", "", None,
                                    OWN + "/", OWN + "/ui/window.html", OWN + "/v1/signin"])
def test_nothing_but_the_top_page_reaches_the_bridge(locked, source):
    locked.edge.on_script_notify(None, FakeArgs(source=source, message=_call("open_browser", '["http://x/"]')))
    assert locked.edge.ran == []


@pytest.mark.parametrize("message", [
    _call("__class__.__init__.__globals__.get", '["os"]'), _call("_state"), _call("_port"), _call("pywebviewMoveWindow", "[1,1]"),
    _call("pywebviewEventHandler", "{}"), _call("pywebviewStateUpdate", "{}"), _call("pywebviewAsyncCallback", "null"),
    _call("open_browser.__func__"), _call("open_browser", "[]", 'x"]();alert(1);//'), _call("open_browser", "[]", ""),
    '"FilesDropped"', '["open_browser", "[]"]', '["open_browser", "[]", 5]', "[]", "{}", "null", "not json"])
def test_the_top_page_can_make_no_other_call(locked, message):
    locked.edge.on_script_notify(None, FakeArgs(source=OWN + "/window/", message=message))
    assert locked.edge.ran == []


def test_webview_that_did_not_start_says_so(locked):
    locked.edge.on_webview_ready(None, FakeArgs(success=False))
    assert locked.failed == ["its browser part (WebView2) did not start"] and locked.edge.ran == []


def test_the_browser_settings_are_set_before_the_first_page_loads():
    settings = types.SimpleNamespace()
    sender = types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(Settings=settings), AllowExternalDrop=True)
    seen = {}

    class Edge(FakeEdge):
        def on_webview_ready(self, s, a):       # pywebview's own: the first navigation happens in here
            seen.update(vars(settings), drop=sender.AllowExternalDrop)
    window.lock_down(Edge, PORT, lambda why: None)
    Edge().on_webview_ready(sender, FakeArgs(success=True))
    assert seen == {"IsPasswordAutosaveEnabled": False, "IsGeneralAutofillEnabled": False,
                    "IsReputationCheckingRequired": False, "AreHostObjectsAllowed": False, "drop": False}


# -- the page's api --------------------------------------------------------------------------------------------
def test_the_page_gets_two_calls_and_nothing_else(state):
    api = window.WindowApi(PORT, state)
    public = [n for n in dir(api) if not n.startswith("_")]
    assert public == ["open_browser", "open_dashboard"]


def test_open_browser_goes_through_the_one_check(state, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "open_tab", lambda u: opened.append(u) or True)
    api = window.WindowApi(PORT, state)
    assert api.open_browser("https://example.test/") is True and opened == ["https://example.test/"]
    assert api.open_browser("file:///C:/x") is False and api.open_browser(None) is False and len(opened) == 1


def test_open_dashboard_makes_a_real_code_for_her_browser_and_never_gives_it_to_the_page(state, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "open_tab", lambda u: opened.append(u) or True)
    assert window.WindowApi(PORT, state).open_dashboard() == {"ok": True, "line": ""}   # never the code
    assert len(opened) == 1 and opened[0].startswith(OWN + "/#pair=")
    assert pairing.redeem(state, opened[0].split("#pair=")[1])


# -- signing in ---------------------------------------------------------------------------------------------------
class FakeWindow:
    def __init__(self, answers):
        self.answers = list(answers)
        self.loaded, self.shown = [], 0
        self.events = types.SimpleNamespace(loaded=types.SimpleNamespace(wait=lambda s: True))

    def run_js(self, script):
        assert "puffpai.device_key" in script and "/v1/settings" in script
        answer = self.answers.pop(0) if self.answers else 0
        if isinstance(answer, Exception):
            raise answer
        return answer

    def load_url(self, url):
        self.loaded.append(url)

    def show(self):
        self.shown += 1


def _codes(state):
    return json.loads((state / "pairing.json").read_text(encoding="utf-8")) if (state / "pairing.json").exists() else []


@pytest.fixture(autouse=True)
def _quick_sign_in(monkeypatch):
    monkeypatch.setattr(window, "SIGN_IN_WAIT_S", 0.2)
    monkeypatch.setattr(window.time, "sleep", lambda s: None)


def test_a_signed_in_profile_makes_no_code(state):
    w = FakeWindow([200])
    window._sign_in_and_show(w, PORT, state)      # noqa: SLF001
    assert w.loaded == [OWN + "/window/"] and w.shown == 1 and _codes(state) == []


@pytest.mark.parametrize("first", ["no-key", 401])
def test_a_signed_out_profile_gets_a_fresh_code_and_none_stays_alive(state, first):
    w = FakeWindow([first, "no-key", 200])
    made = []
    real = pairing.new_code
    pairing_new = lambda s: made.append(real(s)) or made[-1]      # noqa: E731
    from unittest import mock
    with mock.patch.object(window.pairing, "new_code", pairing_new):
        window._sign_in_and_show(w, PORT, state)   # noqa: SLF001
    assert len(made) == 1 and w.loaded == [f"{OWN}/window/#pair={made[0]}"] and w.shown == 1
    assert _codes(state) == [] and not pairing.redeem(state, made[0])


def test_a_code_the_page_never_used_is_burned(state):
    w = FakeWindow(["no-key"] + ["no-key"] * 50)
    window._sign_in_and_show(w, PORT, state)      # noqa: SLF001
    assert w.shown == 1 and _codes(state) == []


@pytest.mark.parametrize("answer", [500, 503, 0, 403, "no-storage", None, "odd", RuntimeError("the window closed")])
def test_anything_else_makes_no_code_and_she_still_gets_the_window(state, answer):
    w = FakeWindow([answer])
    window._sign_in_and_show(w, PORT, state)      # noqa: SLF001
    assert w.shown == 1 and _codes(state) == []
    assert all("#pair=" not in u for u in w.loaded)


def test_show_still_runs_when_loading_fails(state):
    class Bad(FakeWindow):
        def load_url(self, url):
            raise RuntimeError("no")
    w = Bad(["no-key"])
    window._sign_in_and_show(w, PORT, state)      # noqa: SLF001
    assert w.shown == 1 and _codes(state) == []


# -- can it open ------------------------------------------------------------------------------------------------
def _parts(monkeypatch, webview=True, net=True, dotnet=True, webview2=True):
    have = {"webview": webview, "pythonnet": net}
    monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: object() if have.get(name, True) else None)
    monkeypatch.setattr(window, "_has_dotnet", lambda: dotnet)
    monkeypatch.setattr(window, "_has_webview2", lambda: webview2)
    monkeypatch.setattr(window, "_webview_imports", lambda: True)


@pytest.mark.parametrize("missing, words", [({"webview": False}, "pywebview is not installed"),
                                            ({"net": False}, "pythonnet"),
                                            ({"dotnet": False}, ".NET Framework"),
                                            ({"webview2": False}, "WebView2 Runtime")])
def test_a_missing_piece_is_one_plain_line_and_no_process(monkeypatch, state, missing, words):
    _parts(monkeypatch, **missing)
    monkeypatch.setattr(window, "_spawn", lambda *_: pytest.fail("nothing starts"))
    opened, line = window.open_window(PORT)
    assert opened is False and words in line and "\n" not in line and "Traceback" not in line
    assert line.startswith("The window can't open on this computer: ")
    assert line.endswith(f"Your dashboard works: {OWN}")


def test_the_real_check_on_this_computer_says_a_word_or_nothing():
    assert isinstance(window.why_not(), str)


def test_a_process_that_cant_start_is_one_plain_line(monkeypatch, state):
    _parts(monkeypatch)

    def boom(*_):
        raise FileNotFoundError("pythonw")
    monkeypatch.setattr(window, "_spawn", boom)
    opened, line = window.open_window(PORT)
    assert opened is False and "FileNotFoundError" in line and "pythonw" not in line


def test_open_window_starts_it_detached_and_says_so(monkeypatch, state):
    _parts(monkeypatch)
    spawned = []
    monkeypatch.setattr(window, "_spawn", spawned.append)
    assert window.open_window(PORT) == (True, window.OPENING_LINE)
    assert spawned == [state]


def test_the_process_gets_no_code_or_port_on_its_command_line(monkeypatch, state):
    monkeypatch.undo()
    got = {}

    class Popen:
        def __init__(self, argv, **kw):
            got.update(argv=argv, kw=kw)
    monkeypatch.setattr(window.subprocess, "Popen", Popen)
    monkeypatch.setenv("PUFFPAI_STATE", str(state))
    window._spawn(state)                                        # noqa: SLF001
    assert got["argv"][1:] == ["-P", "-m", "puffpai_core.cli", "app", "--here"]
    if os.name == "nt":
        assert got["kw"]["creationflags"] & 0x8 and got["kw"]["close_fds"] is True    # DETACHED_PROCESS


def test_a_window_already_open_is_brought_forward_and_not_opened_twice(monkeypatch, state):
    _parts(monkeypatch)
    monkeypatch.setattr(window, "_spawn", lambda *_: pytest.fail("only one window per bot"))
    forward = []
    monkeypatch.setattr(window, "_bring_forward", forward.append)
    held = corelock.acquire(state / "window", port=PORT, kind=window.KIND)     # the real lock
    try:
        assert window.open_window(PORT) == (True, window.ALREADY_LINE)
    finally:
        held.release()
    assert forward == [os.getpid()]


def test_a_lock_left_by_a_window_that_died_does_not_count(monkeypatch, state):
    _parts(monkeypatch)
    spawned = []
    monkeypatch.setattr(window, "_spawn", spawned.append)
    corelock.acquire(state / "window", port=PORT, kind=window.KIND).release()
    assert window.open_window(PORT)[0] is True and spawned == [state]


# -- the process that shows it ----------------------------------------------------------------------------------
def test_here_with_no_core_says_so_and_starts_nothing(monkeypatch, capsys, state):
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    monkeypatch.setattr(window, "_show", lambda *_a, **_k: pytest.fail("no window without a Core"))
    monkeypatch.setattr(cli, "_launch_serve", lambda *_a: pytest.fail("the window never starts a Core"))
    assert cli.main(["app", "--here"]) == 1
    assert window.NOT_RUNNING_LINE in capsys.readouterr().out


@pytest.fixture
def here(monkeypatch):
    _parts(monkeypatch)
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": PORT, "token": "t"})


@pytest.mark.parametrize("stdout_none", [False, True], ids=["console", "no-console"])
def test_a_failure_in_the_window_parts_is_a_plain_line_and_an_offer(monkeypatch, capsys, state, here, stdout_none):
    def boom(*_a, **_k):
        raise ImportError("clr not found at C:\\secret\\path")
    monkeypatch.setattr(window, "_show", boom)
    boxes = []
    monkeypatch.setattr(window, "_box", lambda text, yes_no=False: boxes.append((text, yes_no)) or False)
    if stdout_none:
        monkeypatch.setattr(sys, "stdout", None)
    code = window.run_here()
    if stdout_none:
        monkeypatch.undo()
    assert code == 1
    out = capsys.readouterr().out
    text = boxes[0][0] if stdout_none else out
    assert "its window parts did not start (ImportError)" in text and "secret" not in text and "Traceback" not in text
    assert (boxes[0][1] is True) if stdout_none else boxes == []     # no console: a Yes or No for the dashboard


def test_a_second_process_brings_the_first_forward_and_ends(monkeypatch, state, here):
    forward = []
    monkeypatch.setattr(window, "_bring_forward", forward.append)
    monkeypatch.setattr(window, "_show", lambda *_a, **_k: pytest.fail("one window per bot"))
    held = corelock.acquire(state / "window", port=PORT, kind=window.KIND)
    monkeypatch.setattr(corelock, "START_WAIT_S", 0.0)
    try:
        assert window.run_here() == 0
    finally:
        held.release()
    assert forward == [os.getpid()]


def _fake_webview(monkeypatch, renderer):
    created = []
    edge = type("EdgeChrome", (FakeEdge,), {})
    settings = dict(window._PYWEBVIEW_SETTINGS, ALLOW_FILE_URLS=True, OPEN_EXTERNAL_LINKS_IN_BROWSER=True)  # noqa: SLF001
    started = {}

    def create_window(title, url, **kw):
        created.append({"title": title, "url": url, **kw})
        return types.SimpleNamespace()

    def start(func=None, args=None, **kw):
        started.update(kw, func=func, args=args)
    webview_mod = types.ModuleType("webview")
    webview_mod.settings, webview_mod.create_window, webview_mod.start = settings, create_window, start
    guilib = types.ModuleType("webview.guilib")
    guilib.initialize = lambda gui: types.SimpleNamespace(renderer=renderer)
    platforms = types.ModuleType("webview.platforms")
    edgechromium = types.ModuleType("webview.platforms.edgechromium")
    edgechromium.EdgeChrome = edge
    platforms.edgechromium = edgechromium
    for name, mod in (("webview", webview_mod), ("webview.guilib", guilib), ("webview.platforms", platforms),
                      ("webview.platforms.edgechromium", edgechromium)):
        monkeypatch.setitem(sys.modules, name, mod)
    return types.SimpleNamespace(created=created, started=started, settings=settings, edge=edge)


def test_pywebviews_fallback_to_the_old_browser_is_refused(monkeypatch, state):
    kit = _fake_webview(monkeypatch, "mshtml")
    why = window._show(PORT, state)               # noqa: SLF001
    assert why == "the Microsoft Edge WebView2 Runtime did not load" and kit.created == [] and kit.started == {}


def test_the_window_starts_hidden_locked_with_no_debug_and_its_own_profile(monkeypatch, state):
    kit = _fake_webview(monkeypatch, "edgechromium")
    (state / "settings").mkdir(parents=True)
    (state / "settings" / "bot.json").write_text(json.dumps({"name": 'Zo\u00eb "Spacey"'}), encoding="utf-8")
    assert window._show(PORT, state) == ""         # noqa: SLF001
    made = kit.created[0]
    assert made["title"] == 'Zo\u00eb "Spacey"' and made["url"] == OWN + "/v1/signin" and made["hidden"] is True
    assert (made["width"], made["height"]) == (1200, 850) and made["min_size"] == (800, 500)
    assert list(n for n in dir(made["js_api"]) if not n.startswith("_")) == ["open_browser", "open_dashboard"]
    s = kit.started
    assert s["gui"] == "edgechromium" and s["debug"] is False and s["private_mode"] is False and s["http_server"] is False
    assert s["storage_path"] == str(state / "window" / "profile") and (state / "window" / "profile").is_dir()
    assert kit.settings["ALLOW_FILE_URLS"] is False and kit.settings["REMOTE_DEBUGGING_PORT"] is None
    assert kit.settings["ALLOW_DOWNLOADS"] is False and kit.settings["WEBVIEW2_RUNTIME_PATH"] is None
    assert kit.edge.on_navigation_start is not FakeEdge.on_navigation_start    # the lock is on the class it runs


def test_what_could_change_the_windows_browser_is_cleared_before_it_starts(monkeypatch, state, here):
    monkeypatch.setenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--remote-debugging-port=9222")
    monkeypatch.setenv("WEBVIEW2_USER_DATA_FOLDER", r"C:\elsewhere")
    monkeypatch.setenv("PYWEBVIEW_LOG", "debug")
    seen = {}
    monkeypatch.setattr(window, "_show", lambda *_a, **_k: seen.update(os.environ) or "")
    assert window.run_here() == 0
    assert not {"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "WEBVIEW2_USER_DATA_FOLDER", "PYWEBVIEW_LOG"} & set(seen)


def test_the_profile_is_in_the_bot_folder_not_the_data_folder_or_the_vault(state):
    profile = window.profile_dir(state)
    assert profile == state / "window" / "profile"
    assert not {"data", "vault", "vault.inbox"} & set(profile.relative_to(state).parts)


# -- puffpai app --------------------------------------------------------------------------------------------------
@pytest.fixture
def app_kit(monkeypatch):
    kit = types.SimpleNamespace(launched=[], windows=[], opens=True)
    monkeypatch.setattr(cli, "_launch_serve", lambda argv, title: kit.launched.append(argv))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    def open_window(port, state=None):
        kit.windows.append(port)
        return (True, window.OPENING_LINE) if kit.opens else (False, window.cant_line("a test", port))
    monkeypatch.setattr(window, "open_window", open_window)
    return kit


def test_app_with_a_core_running_starts_no_second_core(monkeypatch, capsys, app_kit):
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": PORT, "token": "t"})
    assert cli.main(["app"]) == 0
    assert app_kit.launched == [] and app_kit.windows == [PORT] and window.OPENING_LINE in capsys.readouterr().out


def test_app_with_no_core_starts_it_once_by_the_same_start(monkeypatch, capsys, app_kit):
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": PORT, "token": "t"} if app_kit.launched else None)
    assert cli.main(["app", "--port", "51999"]) == 0
    assert len(app_kit.launched) == 1 and app_kit.launched[0][-3:] == ["serve", "--port", "51999"]
    assert app_kit.windows == [PORT] and capsys.readouterr().out.count("Starting BuildBot...") == 1


def test_app_says_so_when_buildbot_does_not_start(monkeypatch, capsys, app_kit):
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    assert cli.main(["app", "--wait", "0"]) == 1
    assert app_kit.windows == [] and "did not start" in capsys.readouterr().out


def test_app_that_cant_open_offers_the_dashboard(monkeypatch, capsys, app_kit, state):
    app_kit.opens = False
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": PORT, "token": "t"})
    opened = []
    monkeypatch.setattr(window, "open_tab", lambda u: opened.append(u) or True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "d")
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    assert cli.main(["app"]) == 1
    assert "The window can't open on this computer: a test." in capsys.readouterr().out
    assert len(opened) == 1 and opened[0].startswith(OWN + "/#pair=")


def test_app_that_cant_open_with_no_keyboard_only_says_so(monkeypatch, capsys, app_kit):
    app_kit.opens = False
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": PORT, "token": "t"})
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    assert cli.main(["app"]) == 1


def test_serve_from_a_shortcut_uses_the_console_python(monkeypatch, tmp_path):
    (tmp_path / "pythonw.exe").write_text("", encoding="ascii")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "window.py")])
    assert cli._serve_argv(8420)[:3] == [str(tmp_path / "python.exe"), "-m", "puffpai_core.cli"]   # noqa: SLF001
    (tmp_path / "puffpai.exe").write_text("", encoding="ascii")
    assert cli._serve_argv(51999) == [str(tmp_path / "puffpai.exe"), "serve", "--port", "51999"]   # noqa: SLF001


# -- what opens ---------------------------------------------------------------------------------------------------
def test_the_window_is_a_choice_and_the_default(state):
    assert opens.DEFAULT == "window" and opens.read(state) == "window"
    for got in ("chat", "dashboard", "window"):
        assert opens.save(state, got) == got and opens.read(state) == got


def test_the_settings_tile_offers_the_window_first_as_the_default():
    text = (REPO / "packages" / "desktop" / "web" / "pages" / "settings.js").read_text(encoding="utf-8")
    assert ('[["window", "The PuffPAI window (default)"], ["chat", "Chat in a terminal"], ["dashboard", "Your dashboard"]]'
            in text)


def test_the_contract_says_it():
    text = (REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
    assert '"opens": "window | chat | dashboard"' in text and '{"opens": "window"}' in text


# -- back to the window from the dashboard ---------------------------------------------------------------------------
@pytest.fixture
def route(monkeypatch, state):
    from puffpai_core.api import create_app
    app = create_app(state, port=PORT)
    core = app.extensions["puffpai"]
    calls = []
    answer = {"value": (True, window.OPENING_LINE)}

    def open_window(port, st=None):
        calls.append((port, st))
        return answer["value"]
    monkeypatch.setattr(window, "open_window", open_window)
    return types.SimpleNamespace(client=app.test_client(), core=core, calls=calls, answer=answer, state=state)


def test_the_route_is_for_her_paired_devices_only(route):
    assert route.client.post("/v1/window/open").status_code == 401
    pkg = {"Authorization": f"Bearer {route.core.tokens.issue_package_token('freshtable')}"}
    assert route.client.post("/v1/window/open", headers=pkg).status_code == 403
    key = {"Authorization": f"Bearer {route.core.tokens.issue_owner_key()}"}
    assert route.client.post("/v1/window/open", headers=key).status_code == 403
    assert route.client.get("/v1/window/open").status_code in (401, 403, 405)
    assert route.calls == []


def test_the_route_opens_this_bots_window_and_takes_nothing_from_the_request(route):
    device = {"Authorization": f"Bearer {route.core.tokens.issue_device('laptop')}"}
    r = route.client.post("/v1/window/open", headers=device, json={"port": 9999, "state": r"C:\elsewhere", "url": "file:///x"})
    assert r.status_code == 200 and r.json == {"ok": True, "line": window.OPENING_LINE}
    assert route.calls == [(PORT, route.state)]


def test_the_route_says_when_it_cant_open(route):
    device = {"Authorization": f"Bearer {route.core.tokens.issue_device('laptop')}"}
    route.answer["value"] = (False, window.cant_line("a test", PORT))
    r = route.client.post("/v1/window/open", headers=device)
    assert r.status_code == 200 and r.json["ok"] is False and "can't open" in r.json["line"]


def test_the_route_needs_a_same_origin_call_when_signed_in_by_cookie(route):
    token = route.core.tokens.issue_device("paired browser: a browser")
    second = route.core.tokens.bind_device(token)
    route.client.set_cookie("puffpai_device", token, domain="puffpai.localhost")
    host = {"Host": f"puffpai.localhost:{PORT}", "X-PuffPAI-Device": second}
    assert route.client.post("/v1/window/open", headers=host).status_code == 403                    # no X-PuffPAI
    bad = {**host, "X-PuffPAI": "1", "Origin": "http://evil.test"}
    assert route.client.post("/v1/window/open", headers=bad).status_code == 403
    assert route.client.post("/v1/window/open", headers={"Host": host["Host"], "X-PuffPAI": "1",
                                                         "Origin": f"http://puffpai.localhost:{PORT}"}).status_code == 401
    assert route.calls == []                                                                        # no second half
    good = {**host, "X-PuffPAI": "1", "Origin": f"http://puffpai.localhost:{PORT}"}
    assert route.client.post("/v1/window/open", headers=good).status_code == 200
    assert len(route.calls) == 1


# -- shortcuts ------------------------------------------------------------------------------------------------------
def test_shortcut_names_hold_any_bot_name():
    assert window._link_stem("Sunny") == "PuffPAI Sunny"                               # noqa: SLF001
    assert window._link_stem('Zo\u00eb "S" <a|b>:*?/\\ 100%') == "PuffPAI Zo\u00eb S ab 100%"    # noqa: SLF001
    assert window._link_stem("..\\..\\evil") == "PuffPAI evil"                        # noqa: SLF001
    assert window._link_stem("a\u202eb\x00\r\nc") == "PuffPAI abc"                    # noqa: SLF001
    assert window._link_stem("   ...   ") == "PuffPAI BuildBot"                       # noqa: SLF001
    assert window._link_stem(None) == "PuffPAI BuildBot"                              # noqa: SLF001


def test_a_shortcut_runs_this_bot_by_folder_name_or_state(monkeypatch, state):
    assert window._shortcut_args(state, 8420) == f'-P -m puffpai_core.window --state {_quoted(state)}'   # noqa: SLF001
    assert window._shortcut_args(state, 51999).endswith("--port 51999")                                  # noqa: SLF001
    monkeypatch.delenv("PUFFPAI_STATE")
    monkeypatch.setenv("PUFFPAI_BOT", "my bot")
    assert window._shortcut_args(state, 8420) == '-P -m puffpai_core.window --bot "my bot"'               # noqa: SLF001


def _quoted(path) -> str:
    return subprocess.list2cmdline([str(path)])


def test_what_a_shortcut_runs_names_the_bot_then_opens_the_app(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "main", lambda argv: calls.append((argv, os.environ.get("PUFFPAI_BOT"), os.environ.get("PUFFPAI_STATE"))) or 0)
    monkeypatch.delenv("PUFFPAI_STATE")
    assert window._shortcut_main(["--bot", "my bot", "--port", "51999"]) == 0         # noqa: SLF001
    assert calls == [(["app", "--port", "51999"], "my bot", None)]
    calls.clear()
    assert window._shortcut_main(["--state", r"C:\scratch"]) == 0                     # noqa: SLF001
    assert calls[0][0] == ["app"] and calls[0][2] == r"C:\scratch"


def test_the_script_takes_every_value_from_the_environment():
    """No value in the command text: no bot name can change the command."""
    assert "Sunny" not in window._SHORTCUT_PS and "$env:PUFFPAI_LNK_PATH" in window._SHORTCUT_PS   # noqa: SLF001
    assert '"' not in window._SHORTCUT_PS and "'" not in window._SHORTCUT_PS                        # noqa: SLF001


def test_shortcuts_use_her_real_folders_only_when_no_folders_are_given(state):
    with pytest.raises(AssertionError, match="real desktop"):                         # conftest's guard
        window.make_shortcuts(state, "Sunny")


def _read_link(path: Path) -> dict:
    """WScript.Shell can't open a name outside the code page either, so it reads a plain-named copy."""
    import shutil
    plain = path.parents[1] / "read-back.lnk"
    shutil.copyfile(path, plain)
    path = plain
    script = ("$l=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK);"
              "@{t=$l.TargetPath;a=$l.Arguments;d=$l.Description;i=$l.IconLocation}|ConvertTo-Json")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                           "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script],
                          env={**os.environ, "LINK": str(path)}, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.decode("utf-8"))


@windows_only
def test_real_shortcuts_with_an_odd_name_in_a_scratch_folder_and_undo(state, tmp_path):
    start, desk = tmp_path / "start $(whoami) 'x'", tmp_path / "desk"
    name = 'Zo\u00eb "Spacey" 100% & $(calc) `x`; \u65e5\u672c'
    made, line = window.make_shortcuts(state, name, port=51999, folders=[start, desk])
    assert made and "--no-shortcuts removes them" in line
    links = [folder / "PuffPAI Zo\u00eb Spacey 100% & $(calc) `x`; \u65e5\u672c.lnk" for folder in (start, desk)]
    assert all(p.is_file() for p in links) and all(str(p) in line for p in links)
    got = _read_link(links[0])
    assert got["t"].lower().endswith("pythonw.exe") and "puffpai_core.window" in got["a"] and "--port 51999" in got["a"]
    assert got["d"] == window.SHORTCUT_TEXT
    assert (got["i"].split(",")[0].lower() == str(window.ICON).lower()) == window.ICON.is_file()
    assert window.make_shortcuts(state, name, port=51999, folders=[start, desk])[0]   # again: the same two files
    assert sorted(p.name for p in start.iterdir()) == [links[0].name]
    said = window.remove_shortcuts(state)
    assert not any(p.exists() for p in links) and "Shortcuts removed" in said
    assert window.remove_shortcuts(state) == "There were no shortcuts to remove."


@windows_only
def test_another_bots_shortcut_of_the_same_name_is_left_alone(state, tmp_path):
    folder = tmp_path / "desk"
    folder.mkdir()
    theirs = folder / "PuffPAI Sunny.lnk"
    theirs.write_bytes(b"another bot's shortcut")
    made, line = window.make_shortcuts(state, "Sunny", folders=[folder])
    ours = folder / f"PuffPAI Sunny ({state.name}).lnk"
    assert made and ours.is_file() and theirs.read_bytes() == b"another bot's shortcut"
    window.remove_shortcuts(state)
    assert not ours.exists() and theirs.exists()


def test_undo_removes_only_link_files_it_made(state, tmp_path):
    keep = tmp_path / "precious.txt"
    keep.write_text("mine", encoding="utf-8")
    (state / "window").mkdir(parents=True)
    (state / "window" / "shortcuts.json").write_text(json.dumps({"paths": [str(keep), 5, None]}), encoding="utf-8")
    assert window.remove_shortcuts(state) == "There were no shortcuts to remove."
    assert keep.exists()


def test_shortcut_commands(monkeypatch, capsys, state):
    monkeypatch.setattr(window, "why_not", lambda: "")
    seen = []
    monkeypatch.setattr(window, "make_shortcuts", lambda st, port=8420: seen.append((st, port)) or (True, "Shortcuts made: x."))
    monkeypatch.setattr(cli, "_launch_serve", lambda *_a: pytest.fail("shortcuts start nothing"))
    monkeypatch.setattr(window, "open_window", lambda *_a: pytest.fail("shortcuts open nothing"))
    assert cli.main(["app", "--shortcuts", "--port", "51999"]) == 0 and seen == [(state, 51999)]
    assert "Shortcuts made" in capsys.readouterr().out
    assert cli.main(["app", "--no-shortcuts"]) == 0
    assert "no shortcuts to remove" in capsys.readouterr().out
    monkeypatch.setattr(window, "why_not", lambda: "a test")
    assert cli.main(["app", "--shortcuts"]) == 1
    with pytest.raises(SystemExit):
        cli.main(["app", "--shortcuts", "--no-shortcuts"])


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe")
def test_no_folder_name_reaches_cmds_line(monkeypatch):
    """A folder may be "OneDrive - Smith & Sons", or hold a real %TEMP% (D108). Each part goes in a variable."""
    got = {}

    class Popen:
        def __init__(self, line, **kw):
            got.update(line=line, env=kw["env"])
    monkeypatch.setattr(subprocess, "Popen", Popen)
    exe = r"C:\Users\x\a%TEMP%b & c\.venv\Scripts\puffpai.exe"
    cli._launch_serve([exe, "serve", "--port", "51999"], "T")   # noqa: SLF001
    from puffpai_core.devshell import system_dir
    cmd = os.path.join(system_dir(), "cmd.exe")
    assert got["line"] == (f'"{cmd}" /d /v:off /c "title T& "%PUFFPAI_SERVE_0%" "%PUFFPAI_SERVE_1%" "%PUFFPAI_SERVE_2%" '
                           '"%PUFFPAI_SERVE_3%" || pause"')
    assert got["env"]["PUFFPAI_SERVE_0"] == exe and got["env"]["PUFFPAI_SERVE_3"] == "51999" and "TEMP%" not in got["line"]


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe")
def test_real_cmd_takes_a_folder_with_percent_and_ampersand(tmp_path):
    """Real cmd.exe, the line _launch_serve builds. It must run the file by its name, with %TEMP% as plain text."""
    folder = tmp_path / "a%TEMP%b & c"
    folder.mkdir()
    out = tmp_path / "ran.txt"
    (folder / "x.cmd").write_text(f'@echo off\r\necho ran %*> "{out}"\r\n', encoding="ascii", newline="")
    argv = [str(folder / "x.cmd"), "serve"]
    env = {**os.environ, **{f"PUFFPAI_SERVE_{i}": a for i, a in enumerate(argv)}}
    from puffpai_core.devshell import system_dir
    cmd = os.path.join(system_dir(), "cmd.exe")
    line = f'"{cmd}" /d /v:off /c "title T& "%PUFFPAI_SERVE_0%" "%PUFFPAI_SERVE_1%" || exit 7"'
    done = subprocess.run(line, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    assert done.returncode == 0 and out.read_text(encoding="ascii").strip() == 'ran "serve"'


# -- a third-party Desktop gets no sign-in code from here (D108) --------------------------------------------------------
def _no_live_code(state):
    return not (state / "pairing.json").exists() or json.loads((state / "pairing.json").read_text(encoding="utf-8")) == []


def test_the_helper_gives_our_own_desktop_a_code(state):
    from puffpai_core import desktop
    link = desktop.dashboard_link(state, PORT)
    assert link.code and link.url == f"{OWN}/#pair={link.code}" and link.line == "" and pairing.redeem(state, link.code)


def test_the_helper_gives_a_third_party_desktop_the_plain_address_and_says_why(state, tmp_path, monkeypatch, drawbridge):
    from puffpai_core import desktop
    from puffpai_core.wizard import terminal
    _third_party_ui(tmp_path, monkeypatch, state)
    link = desktop.dashboard_link(state, PORT)
    assert link.code == "" and link.url == OWN + "/" and _no_live_code(state)
    assert link.line.startswith("Your Desktop is not one of PuffPAI's own, so it gets no sign-in link from here. "
                                "To sign it in, run this in a terminal: ") and link.line.endswith("pair --running.")
    start = tmp_path / "Start BuildBot.cmd"
    (tmp_path / "Sign in a browser.cmd").write_text("@echo off", encoding="ascii")
    monkeypatch.setattr(terminal, "launcher", lambda: start)
    assert desktop.dashboard_link(state, PORT).line.endswith(f"To sign it in, double-click Sign in a browser.cmd in {tmp_path}.")


def test_a_ui_with_its_own_process_gets_no_code(state, monkeypatch):
    from puffpai_core import desktop
    monkeypatch.setattr(desktop, "static_root", lambda *_a: None)
    assert desktop.dashboard_link(state, PORT).code == "" and _no_live_code(state)


def test_a_check_that_fails_gets_no_code(state, monkeypatch):
    from puffpai_core import desktop

    def boom(*_a):
        raise RuntimeError("no")
    monkeypatch.setattr(desktop, "active_ui", boom)
    assert desktop.dashboard_link(state, PORT).code == "" and _no_live_code(state)


def test_the_window_button_with_a_third_party_desktop(state, tmp_path, monkeypatch, drawbridge):
    _third_party_ui(tmp_path, monkeypatch, state)
    opened = []
    monkeypatch.setattr(window, "open_tab", lambda u: opened.append(u) or True)
    got = window.WindowApi(PORT, state).open_dashboard()
    assert opened == [OWN + "/"] and got["ok"] is True and "gets no sign-in link" in got["line"] and _no_live_code(state)
    assert "toast(r.line)" in (WEB / "window.js").read_text(encoding="utf-8")


def _chat(opened):
    from puffpai_core import termchat
    out = __import__("io").StringIO()
    look = termchat.Look(out, env={}, tty=lambda s: False, vt_on=lambda s: False)

    class Api:
        info = {"port": PORT}
    return termchat.Chat(Api(), open_url=opened.append, look=look), out


def test_dashboard_in_the_chat_with_a_third_party_desktop(state, tmp_path, monkeypatch, drawbridge):
    _third_party_ui(tmp_path, monkeypatch, state)
    opened = []
    chat, out = _chat(opened)
    assert chat.cmd_dashboard() == 0
    assert opened == [OWN + "/"] and "gets no sign-in link" in out.getvalue() and "works once" not in out.getvalue()
    assert _no_live_code(state)


def test_dashboard_in_the_chat_with_our_own_desktop(state):
    opened = []
    chat, out = _chat(opened)
    assert chat.cmd_dashboard() == 0
    assert opened[0].startswith(OWN + "/#pair=") and "works once" in out.getvalue()


def test_the_end_of_setup_with_a_third_party_desktop(state, tmp_path, monkeypatch, drawbridge):
    from puffpai_core.wizard import terminal
    _third_party_ui(tmp_path, monkeypatch, state)
    opened, said = [], []
    terminal.setup_done(PORT, state, "Sunny", read_line=lambda p="": "d", write=said.append, open_url=opened.append, chat=lambda: None)
    assert opened == [OWN + "/"] and any("gets no sign-in link" in x for x in said) and _no_live_code(state)
    assert not any("#pair=" in x for x in said)
    assert not any("#pair=" in x for x in terminal.running_lines(PORT, ""))
    assert any(f"Your Desktop is at {OWN}/" in x for x in terminal.running_lines(PORT, ""))


def test_puffpai_start_with_a_third_party_desktop(state, tmp_path, monkeypatch, drawbridge, capsys):
    _third_party_ui(tmp_path, monkeypatch, state)
    capsys.readouterr()
    opens.save(state, "dashboard")
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": PORT, "token": "t"})
    opened = []
    monkeypatch.setattr(cli, "_open_tab", opened.append)
    assert cli.main(["start"]) == 0
    out = capsys.readouterr().out
    assert opened == [OWN + "/"] and "gets no sign-in link" in out and "#pair=" not in out and _no_live_code(state)


def test_puffpai_pair_is_her_explicit_ask_and_stays(state, tmp_path, monkeypatch, drawbridge, capsys):
    _third_party_ui(tmp_path, monkeypatch, state)
    capsys.readouterr()
    assert cli.main(["pair", "--port", str(PORT)]) == 0
    assert f"{OWN}/#pair=" in capsys.readouterr().out


# -- install ----------------------------------------------------------------------------------------------------------
def test_install_writes_the_window_launcher_like_the_sign_in_one():
    text = (REPO / "install.ps1").read_text(encoding="ascii")
    block = text[text.index('$WindowLauncher = Join-Path'):text.index("Set-Content -LiteralPath $WindowLauncher")]
    assert 'Join-Path $RepoDir "Open BuildBot window.cmd"' in block
    assert '"set ""PUFFPAI_BOT=$Name"""' in block and '"set ""PUFFPAI_STATE="""' in block and '"cd /d ""%~dp0"""' in block
    assert '""%~dp0.venv\\Scripts\\puffpai.exe"" app' in block
    assert '"if not ""%BUILDBOT_EXIT%""==""0"" pause"' in block
    assert 'Say ("  Written: " + $WindowLauncher)' in text
    assert "opens the PuffPAI window." in text


def test_the_dependency_is_windows_only_and_pinned():
    import tomllib
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert 'pywebview>=6,<7; sys_platform == "win32"' in data["project"]["dependencies"]
    assert "puff.ico" in data["tool"]["setuptools"]["package-data"]["puffpai_core"]
    assert window.ICON.is_file() and window.ICON.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_no_em_dash_and_no_beta_in_what_she_reads():
    text = Path(window.__file__).read_text(encoding="utf-8")
    assert "\u2014" not in text and "beta" not in text.lower().replace("edge webview2 beta", "")


def test_no_test_can_open_her_real_browser():
    """Every way into ``webbrowser`` is cut off (``no_real_browser``): one failed stub must never reach her browser."""
    import webbrowser
    for fn in (webbrowser.open, webbrowser.open_new, webbrowser.open_new_tab):
        with pytest.raises(AssertionError):
            fn("http://puffpai.localhost:61999/")
    with pytest.raises(webbrowser.Error):
        webbrowser.get()
    assert webbrowser._tryorder == []


# -- the verifier's round: only PuffPAI's own Desktop gets the window ---------------------------------------------------
def _third_party_ui(tmp_path, monkeypatch, state, id="thirdui"):
    """A real static ``ui`` package, really added, really made the active one."""
    from puffpai_core import desktop
    from puffpai_core.registry import Registry
    folder = tmp_path / id
    folder.mkdir()
    (folder / "window.html").write_text("<!doctype html><title>THIRD PARTY</title>", encoding="utf-8")
    (folder / "puffpai.json").write_text(json.dumps({
        "schema": "puffpai-package/1", "id": id, "kind": "ui", "name": id, "version": "1.0.0", "description": "x",
        "author": "x", "license": "MIT", "consent": "A page.", "run": {"default": {"type": "static", "dir": "."}}}),
        encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["add", str(folder)]) == 0
    desktop.set_ui(state, Registry(state / "registry.db"), id)


def _desktop(state):
    from puffpai_core.api import create_app
    return create_app(state, port=PORT).test_client(), f"http://puffpai.localhost:{PORT}"


def test_the_window_is_the_built_in_page_whatever_desktop_is_active(state, tmp_path, monkeypatch, drawbridge):
    _third_party_ui(tmp_path, monkeypatch, state)
    client, base = _desktop(state)
    assert b"THIRD PARTY" in client.get("/ui/window.html", base_url=base).data     # what /ui/ serves is hers to choose
    page = client.get("/window/", base_url=base)
    assert page.status_code == 200 and b"THIRD PARTY" not in page.data and b'id="composer"' in page.data
    assert page.headers["Cache-Control"] == "no-store"
    assert client.get("/window/x.js", base_url=base).status_code == 404            # its files are not here
    assert client.get("/window/logic.js", base_url=base).data == (WEB / "logic.js").read_bytes()


def test_a_third_party_desktop_no_longer_stops_the_window(state, tmp_path, monkeypatch, drawbridge, capsys):
    _third_party_ui(tmp_path, monkeypatch, state)
    capsys.readouterr()
    _parts(monkeypatch)
    spawned = []
    monkeypatch.setattr(window, "_spawn", spawned.append)
    assert window.open_window(PORT) == (True, window.OPENING_LINE) and spawned == [state]
    window._clear_start(state)                                 # noqa: SLF001
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": PORT, "token": "t"})
    shown = []
    monkeypatch.setattr(window, "_show", lambda port, st, after=None: shown.append(port) or "")
    assert window.run_here() == 0 and shown == [PORT]


def test_the_window_page_has_the_desktops_answers(state):
    client, base = _desktop(state)
    page = client.get("/window/", base_url=base)
    csp = page.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert page.headers["X-Frame-Options"] == "DENY" and page.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert not any(k.lower() == "set-cookie" for k, _v in page.headers)


@pytest.mark.parametrize("path", ["index.html", "INDEX.HTML", "index.htm", "index.html.", "index.html ", "index.html::$DATA",
                                  "index.html/", "index.html//", "INDEX~1.HTM", "INDEX~1.HTM/", "./index.html", "pages/../index.html",
                                  "window.html/", "WINDOW.HTML", "window.html.", "window.html ", "./window.html", "LOGIC.JS", "logic.js/",
                                  "../puffpai.json", "..%2fpuffpai.json", "%2e%2e/puffpai.json", "C:/Windows/win.ini", "nothing.js",
                                  "img/HARVESTED.md", "fonts/LICENSE-fonts.txt", "x.svg", "x.xhtml", "pages"])
def test_the_window_serves_no_other_page_and_nothing_outside(state, path):
    client, base = _desktop(state)
    r = client.get("/window/" + path, base_url=base)
    assert r.status_code == 404 or (r.status_code == 308 and "index" not in r.headers["Location"]), path


def test_the_window_files_have_their_own_type_and_nosniff(state):
    client, base = _desktop(state)
    for path, kind in (("", "text/html"), ("window.html", "text/html"), ("logic.js", "text/javascript"),
                       ("pages/settings.js", "text/javascript"), ("desktop.css", "text/css"), ("img/puff.webp", "image/webp"),
                       ("img/design-cloud-1.jpg", "image/jpeg"), ("fonts/nunito/Nunito.woff2", "font/woff2")):
        r = client.get("/window/" + path, base_url=base)
        assert r.status_code == 200 and r.mimetype == kind, path
        assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store", path


def test_the_d79_gate_holds_for_the_window(state):
    client, _base = _desktop(state)
    for path in ("/window/", "/window/logic.js"):
        r = client.get(path, base_url=f"http://127.0.0.1:{PORT}")
        assert r.status_code == 302 and r.headers["Location"] == f"http://puffpai.localhost:{PORT}{path}"
        assert not r.headers.get("Set-Cookie")
        r = client.get(path, base_url=f"http://127.0.0.1:{PORT + 1}", environ_base={"puffpai.listener": "pages"})
        assert r.status_code == 404, path                       # never on the pages address


def test_every_file_the_window_page_names_is_there_and_none_is_the_active_desktops(state):
    """D108: no absolute /ui/ path anywhere the window's page uses, at load or at run time."""
    client, base = _desktop(state)
    html = (WEB / "window.html").read_text(encoding="utf-8")
    names = re.findall(r'(?:src|href)="([^"#]+)"', html)
    assert len(names) >= 6 and not [n for n in names if n.startswith("/") or "://" in n]
    css = re.findall(r"url\(([^)\"']+)\)", (WEB / "desktop.css").read_text(encoding="utf-8"))
    css += re.findall(r"url\(([^)\"']+)\)", (WEB / "window.css").read_text(encoding="utf-8"))
    assert len(css) >= 10 and not [u for u in css if u.startswith("/")]
    for name in names + [u for u in css if not u.startswith("data:")]:
        assert client.get("/window/" + name, base_url=base).status_code == 200, name
        assert client.get("/ui/" + name, base_url=base).status_code == 200, name    # the Desktop's own still works
    for name in ("window.js", "window.css", "theme.js", "github.js", "desktop.css", "window.html"):
        assert "/ui/" not in re.sub(r"/\*.*?\*/|//[^\n]*", "", (WEB / name).read_text(encoding="utf-8"), flags=re.S), name
    code = re.sub(r"/\*.*?\*/", "", (WEB / "logic.js").read_text(encoding="utf-8"), flags=re.S)
    assert re.findall(r'"/ui/[^"]*"', code) == ['"/ui/img/puff.webp"']              # OWN_FACE, the Desktop's
    assert 'f.kind === "own" ? "img/puff.webp"' in (WEB / "window.js").read_text(encoding="utf-8")


def test_the_guards_it_replaces_are_gone():
    assert not hasattr(window, "ui_problem") and not hasattr(window, "guard_active_ui") and not hasattr(window, "_page_path")


# -- a broken install --------------------------------------------------------------------------------------------------
def test_a_webview_that_is_there_but_does_not_import_is_found(monkeypatch, tmp_path):
    (tmp_path / "webview.py").write_text('raise RuntimeError("broken")\n', encoding="ascii")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(window, "_imports_load", [])
    assert window._webview_imports() is False                  # noqa: SLF001
    (tmp_path / "webview.py").unlink()                         # once per process: the answer is kept
    assert window._webview_imports() is False                  # noqa: SLF001
    assert "webview" not in sys.modules or sys.modules["webview"] is not None


def test_a_broken_install_falls_back_to_the_chat(monkeypatch, capsys, state):
    from puffpai_core import termchat
    _parts(monkeypatch)
    monkeypatch.setattr(window, "_webview_imports", lambda: False)
    monkeypatch.setattr(window, "_spawn", lambda *_: pytest.fail("nothing starts"))
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": PORT, "token": "t"})
    monkeypatch.setattr(termchat, "main", lambda: print("THE CHAT") or 0)
    assert cli.main(["start"]) == 0
    out = capsys.readouterr().out
    assert "pywebview is installed but does not load" in out and "THE CHAT" in out and "Traceback" not in out


# -- one start at a time ------------------------------------------------------------------------------------------------
def test_repeated_opens_start_one_window(monkeypatch, state):
    _parts(monkeypatch)
    spawned = []
    monkeypatch.setattr(window, "_spawn", spawned.append)
    answers = [window.open_window(PORT) for _ in range(10)]
    assert len(spawned) == 1 and all(a == (True, window.OPENING_LINE) for a in answers)


def test_a_start_that_died_does_not_block_the_next(monkeypatch, state):
    _parts(monkeypatch)
    spawned = []
    monkeypatch.setattr(window, "_spawn", spawned.append)
    window.open_window(PORT)
    mark = state / "window" / "starting"
    old = window.time.time() - window.START_MARK_S - 1
    os.utime(mark, (old, old))
    window.open_window(PORT)
    assert len(spawned) == 2


def test_a_start_that_failed_leaves_no_mark(monkeypatch, state):
    _parts(monkeypatch)

    def boom(*_):
        raise FileNotFoundError("pythonw")
    monkeypatch.setattr(window, "_spawn", boom)
    assert window.open_window(PORT)[0] is False
    assert not (state / "window" / "starting").exists()


@pytest.mark.parametrize("core_runs", [False, True], ids=["no-core", "lock-held"])
def test_the_window_process_clears_the_mark_on_every_way_out(monkeypatch, state, core_runs):
    _parts(monkeypatch)
    window._claim_start(state)                                 # noqa: SLF001
    monkeypatch.setattr(cli, "_terminal", (lambda: {"port": PORT, "token": "t"}) if core_runs else (lambda: None))
    monkeypatch.setattr(window, "_bring_forward", lambda pid: None)
    monkeypatch.setattr(corelock, "START_WAIT_S", 0.0)
    held = corelock.acquire(state / "window", port=PORT, kind=window.KIND) if core_runs else None
    try:
        window.run_here()
    finally:
        if held:
            held.release()
    assert not (state / "window" / "starting").exists()


# -- small ones ---------------------------------------------------------------------------------------------------------
def test_a_folder_at_the_opens_path_reads_as_window(state):
    from puffpai_core.api import create_app
    (state / "settings" / "opens.json").mkdir(parents=True)
    assert opens.read(state) == "window"
    app = create_app(state, port=PORT)
    owner = {"Authorization": f"Bearer {app.extensions['puffpai'].tokens.issue_owner_key()}"}
    r = app.test_client().get("/v1/settings", headers=owner)
    assert r.status_code == 200 and r.json["opens"] == "window"


def test_a_code_no_browser_took_is_burned(state, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "open_tab", lambda u: opened.append(u) or False)
    assert window.WindowApi(PORT, state).open_dashboard() == {"ok": False, "line": ""}
    assert not pairing.redeem(state, opened[0].split("#pair=")[1])
    assert json.loads((state / "pairing.json").read_text(encoding="utf-8")) == []


def test_history_skips_lines_that_are_not_objects_and_refuses_a_newline_in_the_id(state):
    from puffpai_core.api import create_app
    app = create_app(state, port=PORT)
    device = {"Authorization": f"Bearer {app.extensions['puffpai'].tokens.issue_device('laptop')}"}
    (state / "chat").mkdir()
    (state / "chat" / "odd.jsonl").write_text('5\n[1]\n"x"\nnull\n{"role": "user", "content": "hi", "at": "t"}\n', encoding="utf-8")
    client = app.test_client()
    r = client.get("/v1/chat/history?conversation=odd", headers=device)
    assert r.status_code == 200 and [m["content"] for m in r.json] == ["hi"]
    (state / "chat" / "main.jsonl").write_text('{"role": "user", "content": "hi", "at": "t"}\n', encoding="utf-8")
    assert client.get("/v1/chat/history?conversation=main%0A", headers=device).status_code == 400
    assert client.get("/v1/chat/history?conversation=main", headers=device).status_code == 200


def test_the_page_uses_a_code_only_when_it_is_signed_out():
    text = (REPO / "packages" / "desktop" / "web" / "window.js").read_text(encoding="utf-8")
    assert 'if (code && (!first || first.status === 401)) {' in text


# -- no test starts her browser's program ----------------------------------------------------------------------------
def test_the_page_reader_has_no_browser_in_a_test(monkeypatch):
    from puffpai_core.steward import jobs
    from puffpai_core.zone2 import page_reader
    monkeypatch.setattr(jobs.Job, "spawn", lambda *_a, **_k: pytest.fail("a test started a browser's program"))
    assert page_reader.browser_path() is None
    assert page_reader.read("http://127.0.0.1:9/") is None


@pytest.mark.parametrize("module", ["console_core"])
def test_a_test_core_has_no_browser_either(module):
    code = (f"import {module}\nfrom puffpai_core.zone2 import page_reader\nimport sys\n"
            "sys.exit(0 if page_reader.browser_path() is None else 1)")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPO / "core"), str(REPO / "tests")])}
    assert subprocess.run([sys.executable, "-c", code], env=env, timeout=120).returncode == 0


def test_the_demo_core_gets_the_guard_by_importing_console_core():
    assert "import console_core" in (REPO / "tests" / "window_demo_core.py").read_text(encoding="utf-8")


# -- a repo in a synced folder (D108): one plain line, and the install goes on ------------------------------------------
def _synced_block() -> str:
    text = (REPO / "install.ps1").read_text(encoding="ascii")
    return text[text.index('$SyncedBy = ""'):text.index("# --- 1. Python")]


@windows_only
@pytest.mark.parametrize("repo, env, word", [
    (r"C:\Users\x\OneDrive\Documents\bb", {}, "OneDrive"),
    (r"C:\Users\x\OneDrive - Contoso\bb", {"OneDriveCommercial": r"C:\Users\x\OneDrive - Contoso"}, "OneDrive"),
    (r"D:\odd\bb", {"OneDrive": "D:\\odd\\"}, "OneDrive"),
    (r"C:\Users\x\Dropbox\bb", {}, "Dropbox"),
    (r"C:\Users\x\Google Drive\My Drive\bb", {}, "Google Drive"),
    (r"C:\Users\x\iCloudDrive\bb", {}, "iCloud Drive"),
    (r"C:\PuffPAI", {}, ""),
    (r"C:\Users\x\OneDriveStuff\bb", {}, ""),
    (r"D:\odd2\bb", {"OneDrive": r"D:\odd"}, "")])
def test_install_says_one_line_for_a_synced_folder(repo, env, word):
    """Only that block of install.ps1, with its ``Say`` stubbed; never the install itself."""
    script = 'function Say($t) { Write-Output $t }\n$RepoDir = $env:RUN_REPO\n' + _synced_block()
    clean = {k: v for k, v in os.environ.items() if not k.lower().startswith("onedrive")}
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          env={**clean, **env, "RUN_REPO": repo}, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = done.stdout.strip()
    if word:
        assert out == (f"This folder is synced to the cloud ({word}). BuildBot works here, but its programs would sync too "
                       "(your bot's own data stays in AppData, which does not sync). A folder like C:\\PuffPAI is better.")
    else:
        assert out == ""


def test_the_bots_data_really_is_under_localappdata():
    text = (REPO / "install.ps1").read_text(encoding="ascii")
    assert "$Base = $env:LOCALAPPDATA" in text and '$Home_ = Join-Path $Base "PuffPAI"' in text
