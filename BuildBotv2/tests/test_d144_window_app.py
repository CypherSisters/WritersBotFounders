"""D144 (lane window-apps): the window on Linux and Mac, in Chromium or Chrome app mode. No test here starts a real browser or
opens anything on a screen: a fake stands in for the browser and its pipe. The tests marked ``posix`` start a small fake
program through the real ``_start``. They do not run on Windows."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from puffpai_core import pairing, window, window_app
from puffpai_core.zone2 import page_reader

PORT = 51234
ORIGIN = f"http://puffpai.localhost:{PORT}"
PAGE_URL = f"{ORIGIN}/window/"
MAIN = "MAIN-TARGET"
SESSION = "SESSION-1"
ERROR = object()
THROWN = object()
posix = pytest.mark.skipif(os.name == "nt", reason="needs a real pipe")


class FakeBrowser:
    """The browser behind the pipe: takes frames on ``write``, gives bytes back on ``read``, a few at a time, so a message
    arrives in pieces as it can on a real pipe. ``read`` ends by itself, so a bug here can't hang the run."""

    def __init__(self, probe=(200,), ready=("complete",), mute=False, gone=False, targets=True, log=None):
        self.probe = list(probe)
        self.ready = list(ready)
        self.mute = mute
        self.eof = gone
        self.targets = targets
        self.log = log if log is not None else []
        self.sent = []
        self.closed = 0
        self.gave_up = False
        self.outbox = bytearray()
        self.navigate_error = False
        self.on_evaluate = None
        self._in = b""
        self._end = time.monotonic() + 30

    # -- what a test asks
    def calls(self, method):
        return [m for m in self.sent if m["method"] == method]

    def emit(self, method, **params):
        self.outbox += json.dumps({"method": method, "params": params}).encode() + b"\0"

    def created(self, target, url, kind="page"):
        self.emit("Target.targetCreated", targetInfo={"targetId": target, "type": kind, "url": url})

    def changed(self, target, url, kind="page"):
        self.emit("Target.targetInfoChanged", targetInfo={"targetId": target, "type": kind, "url": url})

    def destroyed(self, target):
        self.emit("Target.targetDestroyed", targetId=target)

    # -- the pipe
    def write(self, data):
        self._in += data
        while b"\0" in self._in:
            raw, self._in = self._in.split(b"\0", 1)
            message = json.loads(raw)
            self.sent.append(message)
            self.log.append(message["method"])
            if not self.mute:
                self._answer(message)

    def read(self, timeout):
        if self.outbox:
            chunk = bytes(self.outbox[:40])
            del self.outbox[:40]
            return chunk
        if self.eof:
            return b""
        if time.monotonic() > self._end:
            self.gave_up = True
            return b""
        time.sleep(0.002)
        return None

    def close(self):
        self.closed += 1

    # -- the browser's side
    def _reply(self, number, **body):
        self.outbox += json.dumps({"id": number, **body}).encode() + b"\0"

    def _answer(self, message):
        method, number, params = message["method"], message["id"], message.get("params") or {}
        if method == "Target.setDiscoverTargets":
            if self.targets:
                self.created(MAIN, PAGE_URL)
        elif method == "Target.getTargets":
            self._reply(number, result={"targetInfos": []})
            return
        elif method == "Target.attachToTarget":
            self._reply(number, result={"sessionId": SESSION})
            return
        elif method == "Runtime.evaluate":
            self._evaluate(number, params["expression"])
            return
        elif method == "Page.navigate" and self.navigate_error:
            self._reply(number, error={"code": -32000, "message": "no"})
            return
        elif method == "Target.closeTarget":
            self.destroyed(params["targetId"])
        elif method == "Browser.close":
            self.eof = True
        self._reply(number, result={})

    def _evaluate(self, number, expression):
        if self.on_evaluate is not None:
            self.on_evaluate(expression)
        if expression == window_app._READY_JS:
            value = self.ready.pop(0) if len(self.ready) > 1 else self.ready[0]
        else:
            assert expression == window._PROBE_JS
            value = self.probe.pop(0) if len(self.probe) > 1 else self.probe[0]
        if value is ERROR:
            self._reply(number, error={"code": -32000, "message": "Execution context was destroyed."})
        elif value is THROWN:
            self._reply(number, result={"result": {"type": "object"}, "exceptionDetails": {"text": "Uncaught"}})
        else:
            self._reply(number, result={"result": {"type": "string", "value": value}})


@pytest.fixture
def handed(monkeypatch):
    """What would go to her real browser. ``handed.answer`` is what ``to_browser`` says."""
    urls = types.SimpleNamespace(urls=[], answer=True)

    def to_browser(url):
        urls.urls.append(url)
        return urls.answer
    monkeypatch.setattr(window, "to_browser", to_browser)
    return urls


@pytest.fixture
def run(monkeypatch, state, handed):
    """``show`` on a fake browser. By default the window closes as soon as the sign-in is done."""
    started = []

    def go(fake, after=None, port=PORT):
        def start(path, args):
            started.append((path, args))
            return fake

        def finish(win):
            if after is not None:
                after(win)
            else:
                fake.eof = True
        monkeypatch.setattr(window_app, "browser", lambda: "/opt/fake/chromium")
        monkeypatch.setattr(window_app, "_is_snap", lambda path: False)
        monkeypatch.setattr(window_app, "_start", start)
        why = window_app.show(port, state, finish)
        assert not fake.gave_up, "the fake browser waited 30 s: the code did not end"
        return why
    go.started = started
    return go


@pytest.fixture
def no_code_made(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a code was made")
    monkeypatch.setattr(pairing, "new_code", refuse)


def code_of(fake):
    urls = [m["params"]["url"] for m in fake.calls("Page.navigate")]
    assert len(urls) == 1 and urls[0].startswith(PAGE_URL + "#pair=")
    return urls[0].split("#pair=", 1)[1]


# -- can it open here --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("screen,path,want", [(False, "", window_app.NO_SCREEN), (False, "/x/chromium", window_app.NO_SCREEN),
                                              (True, "", window_app.NO_BROWSER), (True, "/x/chromium", "")])
def test_no_screen_is_said_before_no_browser(monkeypatch, screen, path, want):
    monkeypatch.setattr(window_app, "has_screen", lambda: screen)
    monkeypatch.setattr(window_app, "browser", lambda: path)
    assert window_app.why_not() == want


@pytest.mark.parametrize("env,want", [({}, False), ({"DISPLAY": ""}, False), ({"DISPLAY": "  "}, False), ({"WAYLAND_DISPLAY": " "}, False),
                                      ({"DISPLAY": ":0"}, True), ({"WAYLAND_DISPLAY": "wayland-0"}, True),
                                      ({"DISPLAY": "", "WAYLAND_DISPLAY": "wayland-0"}, True),
                                      ({"SSH_TTY": "/dev/pts/1", "DISPLAY": ":0"}, True)])
def test_a_linux_screen(monkeypatch, env, want):
    monkeypatch.setattr(sys, "platform", "linux")
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert window_app.has_screen() is want


@pytest.mark.parametrize("env,want", [({}, True), ({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, False), ({"SSH_TTY": "/dev/ttys001"}, False)])
def test_a_mac_screen(monkeypatch, env, want):
    monkeypatch.setattr(sys, "platform", "darwin")
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert window_app.has_screen() is want


def test_the_browser_is_the_first_on_the_page_readers_list(monkeypatch):
    monkeypatch.setattr(page_reader, "_installed", lambda: [("chrome", "/first/chrome"), ("chromium", "/second/chromium")])
    assert window_app.browser() == "/first/chrome"
    monkeypatch.setattr(page_reader, "_installed", lambda: [])
    assert window_app.browser() == ""


# -- the command line --------------------------------------------------------------------------------------------------
def test_the_command_line_holds_no_code_and_no_port(monkeypatch):
    monkeypatch.delenv("PUFFPAI_BROWSER_NO_SANDBOX", raising=False)
    args = window_app._args(PORT, "/some/profile")
    assert args[0] == f"--app={PAGE_URL}" and "#" not in args[0]
    assert not [a for a in args if "#" in a or "pair=" in a]
    assert "--user-data-dir=/some/profile" in args
    assert "--remote-debugging-pipe" in args
    assert not [a for a in args if a.startswith("--remote-debugging-port") or a.startswith("--remote-debugging-address")]
    for flag in ("--no-first-run", "--no-default-browser-check", "--disable-extensions", "--window-size=1200,850", "--password-store=basic",
                 "--use-mock-keychain", "--disable-sync", "--disable-background-networking", "--no-pings"):
        assert flag in args
    assert "--no-sandbox" not in args


def test_no_sandbox_only_when_asked_for(monkeypatch):
    monkeypatch.setenv("PUFFPAI_BROWSER_NO_SANDBOX", "1")
    assert "--no-sandbox" in window_app._args(PORT, "/p")
    monkeypatch.setenv("PUFFPAI_BROWSER_NO_SANDBOX", "")
    assert "--no-sandbox" not in window_app._args(PORT, "/p")


def test_a_signed_out_start_has_no_code_on_its_command_line(run, state):
    fake = FakeBrowser(probe=("no-key", 200))
    assert run(fake) == ""
    code = code_of(fake)
    (path, args), = run.started
    assert code not in path and not [a for a in args if code in a]


class _Fds:
    """File numbers as an OS gives them, for reading what ``_start`` does on a computer with no ``fcntl``."""
    F_DUPFD_CLOEXEC = 1030

    def __init__(self, busy=()):
        self.open = {0, 1, 2, *busy}

    def _lowest(self, low):
        while low in self.open:
            low += 1
        self.open.add(low)
        return low

    def pipe(self):
        return self._lowest(3), self._lowest(3)

    def close(self, fd):
        self.open.remove(fd)

    def fcntl(self, fd, command, low):
        assert command == self.F_DUPFD_CLOEXEC and fd in self.open
        return self._lowest(low)


def _start_with(monkeypatch, fds, popen):
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "fcntl", fds)
        patch.setattr(os, "pipe", fds.pipe)
        patch.setattr(os, "close", fds.close)
        patch.setattr(subprocess, "Popen", popen)
        return window_app._start("/usr/bin/my browser", ["--app=x", "--a=$(b)"])


@pytest.mark.parametrize("busy", [(), (3,), (3, 4), (3, 4, 5, 6), (5, 6, 7), (3, 4, 10, 11)])
def test_the_pipe_ends_reach_the_start_step_clear_of_3_and_4(monkeypatch, busy):
    seen = {}

    def popen(argv, **kw):
        seen.update(argv=argv, **kw)
        seen["open_then"] = set(fds.open)
        return "the process"
    fds = _Fds(busy)
    monkeypatch.setenv("CHROMIUM_FLAGS", "--remote-debugging-port=1234")
    monkeypatch.setenv("CHROMIUM_USER_FLAGS", "--remote-debugging-port=1234")
    ends = _start_with(monkeypatch, fds, popen)
    r, w = seen["pass_fds"]
    assert r >= 10 and w >= 10 and r != w and not {r, w} & set(busy)
    assert seen["argv"] == [sys.executable, "-I", "-S", "-c", window_app._SPAWN_SCRIPT, str(r), str(w), "/usr/bin/my browser", "--app=x", "--a=$(b)"]
    assert seen["close_fds"] is True and seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.DEVNULL
    assert "CHROMIUM_FLAGS" not in seen["env"] and "CHROMIUM_USER_FLAGS" not in seen["env"]
    assert r not in fds.open and w not in fds.open                               # no child end stays here
    assert fds.open == {0, 1, 2, *busy, ends._read, ends._write}
    assert ends._read >= 10 and ends._write >= 10


def test_a_start_that_fails_leaves_no_end_open(monkeypatch):
    def popen(argv, **kw):
        raise FileNotFoundError(2, "no such program")
    fds = _Fds()
    with pytest.raises(OSError):
        _start_with(monkeypatch, fds, popen)
    assert fds.open == {0, 1, 2}


# -- the profile -------------------------------------------------------------------------------------------------------
def test_the_profile_is_the_bots_own_folder(state, tmp_path):
    got = window_app.profile_dir(state, str(tmp_path / "chromium"))
    assert got == window.profile_dir(state) and got == Path(state) / "window" / "profile" and got.is_dir()


def test_a_snap_profile_is_in_the_snaps_own_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    got = window_app.profile_dir(tmp_path / "bots" / "mybot", "/snap/bin/chromium")
    assert got == tmp_path / "home" / "snap" / "chromium" / "common" / "puffpai-window" / "mybot" / "profile"
    assert got.is_dir()


def test_what_counts_as_a_snap(tmp_path):
    assert window_app._is_snap("/snap/bin/chromium")
    assert window_app._is_snap(str(tmp_path / "snap"))                 # what a link resolves to, on a real Ubuntu /usr/bin/snap
    assert not window_app._is_snap(str(tmp_path / "chromium"))
    assert not window_app._is_snap("/usr/bin/google-chrome")


@posix
def test_a_link_to_the_snap_program_is_a_snap(tmp_path):
    (tmp_path / "snap").write_text("")
    (tmp_path / "chromium").symlink_to(tmp_path / "snap")
    assert window_app._is_snap(str(tmp_path / "chromium"))


@posix
def test_the_profile_folders_are_hers_alone(monkeypatch, tmp_path, state):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    old = os.umask(0)
    try:
        got = window_app.profile_dir(tmp_path / "bots" / "mybot", "/snap/bin/chromium")
        for folder in (got, got.parent, got.parent.parent):
            assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        wide = window.profile_dir(state)
        wide.mkdir(parents=True, mode=0o777)
        os.chmod(wide, 0o777)
        assert stat.S_IMODE(window_app.profile_dir(state, str(tmp_path / "chromium")).stat().st_mode) == 0o700
    finally:
        os.umask(old)


def test_a_folder_that_cannot_be_made_is_said_in_words(monkeypatch, state):
    def refuse(_state, _path):
        raise PermissionError(13, "no")
    monkeypatch.setattr(window_app, "browser", lambda: "/x/chromium")
    monkeypatch.setattr(window_app, "profile_dir", refuse)
    assert window_app.show(PORT, state) == "its profile folder could not be made (PermissionError)"


# -- sign-in -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("signed_out", ["no-key", 401])
def test_a_signed_out_profile_gets_a_code_that_is_burned_after(run, state, monkeypatch, capfd, signed_out):
    log = []
    real_redeem, burned = pairing.redeem, []

    def redeem(st, code):
        log.append("burn")
        burned.append(code)
        return real_redeem(st, code)
    monkeypatch.setattr(pairing, "redeem", redeem)
    fake = FakeBrowser(probe=(signed_out, signed_out, 200), log=log)
    assert run(fake) == ""
    code = code_of(fake)
    assert burned == [code]
    assert log.index("Page.navigate") < log.index("burn")
    assert log[log.index("Page.navigate") + 1:log.index("burn")].count("Runtime.evaluate") >= 2      # it waited for the page
    assert log.index("burn") < log.index("Target.activateTarget")
    assert fake.calls("Page.navigate")[0]["sessionId"] == SESSION
    assert real_redeem(state, code) is False                         # no live code stays behind
    out = capfd.readouterr()
    assert code not in out.out and code not in out.err
    assert fake.closed == 1


def test_a_code_is_live_while_the_page_needs_it(run, state, monkeypatch):
    """The real code, the real store: the page could have used it when the probe asked."""
    fake = FakeBrowser(probe=("no-key", "no-key", 200))
    live = []

    def look(expression):
        if expression == window._PROBE_JS and fake.calls("Page.navigate"):
            live.append(json.loads((Path(state) / "pairing.json").read_text(encoding="utf-8")))
    fake.on_evaluate = look
    assert run(fake) == ""
    assert live and all(len(codes) == 1 for codes in live)
    assert json.loads((Path(state) / "pairing.json").read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("answer", [200, 500, 0, "no-storage", None, ERROR, THROWN])
def test_anything_but_signed_out_makes_no_code(run, no_code_made, answer):
    fake = FakeBrowser(probe=(answer,))
    assert run(fake) == ""
    assert not fake.calls("Page.navigate")
    assert fake.calls("Target.activateTarget")


def test_a_second_launch_adds_no_device(run, state):
    fake = FakeBrowser(probe=(200,))
    assert run(fake) == ""
    assert not (Path(state) / "pairing.json").exists()


def test_on_another_device_no_code_and_no_navigate(run, no_code_made, monkeypatch):
    monkeypatch.setattr(window, "_remote", None)
    window.use_remote("192.168.1.20")
    assert window.origin_of(PORT) == f"http://192.168.1.20:{PORT}"
    fake = FakeBrowser(probe=("no-key",))
    assert run(fake) == ""
    assert not fake.calls("Page.navigate")
    assert not [m for m in fake.calls("Runtime.evaluate") if m["params"]["expression"] == window._PROBE_JS]
    assert run.started[0][1][0] == f"--app=http://192.168.1.20:{PORT}/window/"


@posix
@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP"])
def test_being_told_to_end_still_burns_the_code(run, state, monkeypatch, name):
    def told(_win):
        os.kill(os.getpid(), getattr(signal, name))
        time.sleep(5)
        raise AssertionError("the signal did nothing")
    monkeypatch.setattr(window, "_wait_signed_in", told)
    before = signal.getsignal(getattr(signal, name))
    fake = FakeBrowser(probe=("no-key",))
    assert run(fake) == ""
    assert pairing.redeem(state, code_of(fake)) is False and fake.closed
    assert signal.getsignal(getattr(signal, name)) == before


def test_a_page_that_never_signs_in_still_burns_the_code(run, state, monkeypatch):
    monkeypatch.setattr(window, "SIGN_IN_WAIT_S", 0.3)
    fake = FakeBrowser(probe=("no-key",))
    assert run(fake) == ""
    assert pairing.redeem(state, code_of(fake)) is False
    assert fake.calls("Target.activateTarget")


def test_a_reload_in_the_middle_does_not_burn_the_code_early(run, state, monkeypatch):
    """A page mid-reload has no context: an error, then a thrown one, then the page is back and signed in."""
    log = []
    real_redeem = pairing.redeem

    def redeem(st, code):
        log.append("burn")
        return real_redeem(st, code)
    monkeypatch.setattr(pairing, "redeem", redeem)
    fake = FakeBrowser(probe=("no-key", ERROR, THROWN, ERROR, 200), log=log)
    assert run(fake) == ""
    assert not fake.probe[1:]                                        # every answer was asked for
    assert log.count("burn") == 1
    probes_after = log[log.index("Page.navigate"):log.index("burn")].count("Runtime.evaluate")
    assert probes_after >= 4
    assert pairing.redeem(state, code_of(fake)) is False


def test_a_navigate_that_fails_burns_the_code_and_still_shows_the_window(run, state):
    fake = FakeBrowser(probe=("no-key",))
    fake.navigate_error = True
    assert run(fake) == ""
    assert pairing.redeem(state, code_of(fake)) is False
    assert fake.calls("Target.activateTarget")


def test_the_browser_gone_in_the_middle_of_sign_in_burns_the_code(run, state, monkeypatch):
    monkeypatch.setattr(window, "SIGN_IN_WAIT_S", 0.3)
    fake = FakeBrowser(probe=("no-key",))

    def go(expression):
        if fake.calls("Page.navigate"):
            fake.mute = True
            fake.eof = True
    fake.on_evaluate = go
    assert run(fake) == ""
    assert pairing.redeem(state, code_of(fake)) is False
    assert fake.closed == 1


# -- the adapter -------------------------------------------------------------------------------------------------------
def _adapter(fake):
    cdp = window_app._Cdp(fake)
    pages = window_app._Pages(cdp, PORT)
    cdp.on_event = pages.on_event
    pages.seen[MAIN] = PAGE_URL
    pages.main = MAIN
    return window_app._Window(cdp, pages, SESSION)


@pytest.mark.parametrize("answer", [ERROR, THROWN])
def test_run_js_is_none_on_any_failure(answer):
    win = _adapter(FakeBrowser(probe=(answer,)))
    assert win.run_js(window._PROBE_JS) is None


def test_run_js_is_none_when_the_browser_is_gone():
    fake = FakeBrowser(gone=True)
    fake.mute = True
    assert _adapter(fake).run_js(window._PROBE_JS) is None


def test_run_js_is_none_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(window_app, "CALL_WAIT_S", 0.05)
    assert _adapter(FakeBrowser(mute=True)).run_js(window._PROBE_JS) is None


def test_run_js_gives_the_value_inside_the_session():
    fake = FakeBrowser(probe=(200,))
    assert _adapter(fake).run_js(window._PROBE_JS) == 200
    sent = fake.calls("Runtime.evaluate")[0]
    assert sent["sessionId"] == SESSION and sent["params"]["returnByValue"] is True


def test_loaded_waits_for_complete():
    fake = FakeBrowser(ready=("loading", ERROR, "interactive", "complete"))
    assert _adapter(fake).events.loaded.wait(5) is True
    assert len(fake.calls("Runtime.evaluate")) == 4


def test_loaded_gives_up_in_time():
    assert _adapter(FakeBrowser(ready=("loading",))).events.loaded.wait(0.05) is False


def test_a_blank_new_page_is_not_loaded_yet():
    # about:blank says "complete", and has no storage: the probe there would make no code, and she would get no sign-in.
    assert "about:" in window_app._READY_JS and "document.readyState" in window_app._READY_JS


def test_load_url_only_goes_when_there_is_a_code():
    fake = FakeBrowser()
    win = _adapter(fake)
    win.load_url(PAGE_URL)
    assert not fake.calls("Page.navigate")
    win.load_url(PAGE_URL + "#pair=A")
    assert fake.calls("Page.navigate")[0]["params"]["url"] == PAGE_URL + "#pair=A"


def test_a_failure_never_says_what_was_sent():
    fake = FakeBrowser()
    fake.navigate_error = True
    with pytest.raises(window_app._Failed) as got:
        _adapter(fake).load_url(PAGE_URL + "#pair=SECRET-CODE")
    assert "SECRET-CODE" not in str(got.value)


def test_show_activates_the_page():
    fake = FakeBrowser()
    _adapter(fake).show()
    assert fake.calls("Target.activateTarget")[0]["params"] == {"targetId": MAIN}


# -- the watch loop ----------------------------------------------------------------------------------------------------
def test_an_outside_popup_goes_to_her_browser_and_closes(run, handed):
    fake = FakeBrowser()

    def after(_win):
        fake.created("POP", "https://example.org/a?b=c")
        fake.created("LAST", "about:blank")
        fake.destroyed("LAST")
        fake.eof = True
    assert run(fake, after) == ""
    assert handed.urls == ["https://example.org/a?b=c"]
    assert fake.calls("Target.closeTarget")[0]["params"] == {"targetId": "POP"}


def test_a_popup_that_starts_blank_goes_once_when_it_has_its_address(run, handed):
    fake = FakeBrowser()
    handed.answer = False

    def after(_win):
        fake.created("POP", "")
        fake.changed("POP", "about:blank")
        fake.changed("POP", "https://example.org/")
        fake.changed("POP", "https://example.org/other")
        fake.eof = True
    assert run(fake, after) == ""
    assert handed.urls == ["https://example.org/"]


def test_a_popup_on_the_bots_own_address_stays(run, handed):
    fake = FakeBrowser()

    def after(_win):
        fake.created("DASH", f"{ORIGIN}/#developer/terminal")
        fake.changed("DASH", f"{ORIGIN}/")
        fake.eof = True
    assert run(fake, after) == ""
    assert not handed.urls and not fake.calls("Target.closeTarget")


@pytest.mark.parametrize("url", [f"http://puffpai.localhost:{PORT}.example.org/", f"http://puffpai.localhost:{PORT + 1}/", f"https://puffpai.localhost:{PORT}/",
                                 f"http://puffpai.localhost@example.org:{PORT}/", f"http://a@puffpai.localhost:{PORT}/", f"http://127.0.0.1:{PORT}/"])
def test_an_address_that_only_looks_like_the_bots_is_outside(run, handed, url):
    fake = FakeBrowser()

    def after(_win):
        fake.created("POP", url)
        fake.eof = True
    assert run(fake, after) == ""
    assert handed.urls == [url]


def test_when_her_browser_can_not_take_it_the_popup_stays_open(run, handed):
    fake = FakeBrowser()
    handed.answer = False

    def after(_win):
        fake.created("POP", "https://example.org/")
        fake.eof = True
    assert run(fake, after) == ""
    assert handed.urls == ["https://example.org/"] and not fake.calls("Target.closeTarget")


def test_the_real_check_refuses_another_scheme(monkeypatch, state):
    """Through the real ``to_browser``: nothing but http and https reaches her browser, and that popup stays."""
    tabs = []
    monkeypatch.setattr(window, "open_tab", lambda url: tabs.append(url) or True)
    fake = FakeBrowser()
    monkeypatch.setattr(window_app, "browser", lambda: "/opt/fake/chromium")
    monkeypatch.setattr(window_app, "_is_snap", lambda path: False)
    monkeypatch.setattr(window_app, "_start", lambda path, args: fake)

    def after(_win):
        fake.created("A", "file:///etc/passwd")
        fake.created("B", "chrome://settings/")
        fake.created("C", "https://user:pw@example.org/")
        fake.created("D", "https://example.org/")
        fake.eof = True
    assert window_app.show(PORT, state, after) == ""
    assert tabs == ["https://example.org/"]
    assert [m["params"]["targetId"] for m in fake.calls("Target.closeTarget")] == ["D"]


def test_the_main_page_is_never_handed_over(run, handed):
    fake = FakeBrowser()

    def after(_win):
        fake.changed(MAIN, "https://example.org/")
        fake.eof = True
    assert run(fake, after) == ""
    assert not handed.urls


def test_only_pages_count(run, handed):
    fake = FakeBrowser()

    def after(_win):
        fake.created("W", "https://example.org/", kind="service_worker")
        fake.destroyed("W")
        fake.created("F", "https://example.org/", kind="iframe")
        fake.eof = True
    assert run(fake, after) == ""
    assert not handed.urls and not fake.calls("Browser.close")


def test_a_popup_during_sign_in_is_handed_over_too(run, handed):
    fake = FakeBrowser(probe=("no-key", "no-key", 200))
    fake.on_evaluate = lambda _expr: fake.created("POP", "https://example.org/") if fake.calls("Page.navigate") and not handed.urls else None
    assert run(fake) == ""
    assert handed.urls == ["https://example.org/"]


def test_the_page_that_was_there_before_the_main_one_was_chosen_is_not_a_popup(run, handed):
    fake = FakeBrowser(targets=False)
    real_answer = fake._answer

    def answer(message):
        if message["method"] == "Target.setDiscoverTargets":
            fake.created("BLANK", "")
            fake.created(MAIN, PAGE_URL)
        real_answer(message)
    fake._answer = answer
    assert run(fake) == ""
    assert not handed.urls
    assert fake.calls("Target.attachToTarget")[0]["params"] == {"targetId": MAIN, "flatten": True}


def test_the_last_page_gone_closes_the_browser(run):
    fake = FakeBrowser()
    assert run(fake, lambda _win: fake.destroyed(MAIN)) == ""
    assert fake.calls("Browser.close") and fake.closed == 1


def test_the_browser_stays_while_a_page_is_left(run):
    fake = FakeBrowser()
    real_read, idle = fake.read, []

    def after(_win):
        fake.created("DASH", f"{ORIGIN}/")
        fake.destroyed(MAIN)
        fake.read = read

    def read(timeout):
        chunk = real_read(timeout)
        if chunk is None and not idle:                                # main is gone and handled, the dashboard is not
            idle.append(len(fake.calls("Browser.close")))
            fake.destroyed("DASH")
        return chunk
    assert run(fake, after) == ""
    assert idle == [0]
    assert len(fake.calls("Browser.close")) == 1


def test_end_of_file_ends_it(run):
    fake = FakeBrowser()
    assert run(fake) == ""
    assert not fake.calls("Browser.close") and fake.closed == 1


# -- when it does not start --------------------------------------------------------------------------------------------
def test_a_browser_that_never_answers_is_a_plain_why_and_is_ended(run, monkeypatch):
    monkeypatch.setattr(window_app, "HELLO_WAIT_S", 0.05)
    fake = FakeBrowser(mute=True)
    assert run(fake) == window_app.NO_START
    assert fake.closed == 1
    assert [m["method"] for m in fake.sent] == ["Browser.getVersion"]


def test_a_browser_that_exits_at_once_is_a_plain_why(run):
    fake = FakeBrowser(gone=True, mute=True)
    assert run(fake) == window_app.NO_START
    assert fake.closed == 1


def test_a_browser_with_no_page_is_a_plain_why(run, monkeypatch, no_code_made):
    monkeypatch.setattr(window_app, "TARGET_WAIT_S", 0.05)
    fake = FakeBrowser(targets=False)
    assert run(fake) == window_app.NO_START
    assert fake.closed == 1


@pytest.mark.parametrize("error", [FileNotFoundError(2, "/bin/sh"), PermissionError(13, "/bin/sh")])
def test_a_browser_that_can_not_start_is_said_in_words(monkeypatch, state, error):
    def start(_path, _args):
        raise error
    monkeypatch.setattr(window_app, "browser", lambda: "/x/chromium")
    monkeypatch.setattr(window_app, "_is_snap", lambda path: False)
    monkeypatch.setattr(window_app, "_start", start)
    assert window_app.show(PORT, state) == f"its browser could not start ({type(error).__name__})"


def test_no_browser_at_show(monkeypatch, state):
    monkeypatch.setattr(window_app, "browser", lambda: "")
    assert window_app.show(PORT, state) == window_app.NO_BROWSER


# -- the pipe's messages -----------------------------------------------------------------------------------------------
class _Bytes:
    def __init__(self, *chunks):
        self.chunks = list(chunks)

    def read(self, _timeout):
        return self.chunks.pop(0) if self.chunks else b""

    def write(self, _data):
        pass


def test_messages_in_pieces_and_noise_between_them():
    events = []
    ends = _Bytes(b'{"method":"Target.targetDestr', b'oyed","params":{"targetId":"A"}}\0not json\0[1]\0', None,
                  b'{"method":"x","params":3}\0{"method":"y","sessionId":"S","params":{}}\0{"id":1,"result":{"ok":true}}\0')
    cdp = window_app._Cdp(ends, lambda method, params: events.append((method, params)))
    assert cdp.call("Anything") == {"ok": True}
    assert events == [("Target.targetDestroyed", {"targetId": "A"}), ("x", {})]


def test_an_answer_to_another_call_is_not_this_ones():
    ends = _Bytes(b'{"id":99,"result":{"no":1}}\0{"id":1,"result":{"yes":1}}\0')
    assert window_app._Cdp(ends).call("Anything") == {"yes": 1}


def test_a_browser_that_sends_without_end_is_treated_as_gone(monkeypatch):
    monkeypatch.setattr(window_app, "MAX_BUFFER", 10)
    cdp = window_app._Cdp(_Bytes(b"x" * 20, b"y"))
    with pytest.raises(window_app._Failed):
        cdp.pump(1)
    assert cdp.gone


def test_a_broken_pipe_on_send_is_gone():
    class Broken(_Bytes):
        def write(self, _data):
            raise BrokenPipeError(32, "gone")
    cdp = window_app._Cdp(Broken())
    with pytest.raises(window_app._Failed):
        cdp.send("Anything")
    assert cdp.gone


def test_each_message_has_its_own_id():
    fake = FakeBrowser()
    cdp = window_app._Cdp(fake)
    numbers = [cdp.send("Browser.getVersion") for _ in range(3)]
    assert len(set(numbers)) == 3


# -- closing -----------------------------------------------------------------------------------------------------------
class _Proc:
    def __init__(self, ends_after):
        self.ends_after = ends_after
        self.did = []

    def wait(self, timeout):
        self.did.append(("wait", timeout))
        if self.ends_after and self.ends_after in self.did:
            return 0
        if self.ends_after is None:
            return 0
        raise subprocess.TimeoutExpired("x", timeout)

    def terminate(self):
        self.did.append("terminate")

    def kill(self):
        self.did.append("kill")


@pytest.mark.parametrize("ends_after,steps", [(None, []), ("terminate", ["terminate"]), ("kill", ["terminate", "kill"])])
def test_closing_waits_then_ends_only_its_own_process(ends_after, steps):
    r, w = os.pipe()
    proc = _Proc(ends_after)
    ends = window_app._Process(proc, r, w)
    ends.close()
    assert [d for d in proc.did if isinstance(d, str)] == steps
    assert ("wait", window_app.CLOSE_WAIT_S) in proc.did
    for fd in (r, w):
        with pytest.raises(OSError):
            os.fstat(fd)
    ends.close()                                                      # twice is harmless: no number is closed again


def test_after_closing_a_write_fails_as_gone():
    r, w = os.pipe()
    ends = window_app._Process(_Proc(None), r, w)
    ends.close()
    with pytest.raises(OSError):
        ends.write(b"x")


# -- bringing it forward -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pid", [0, -1, None, "12", 1.5, True, False])
def test_bring_forward_takes_only_a_real_pid(monkeypatch, pid):
    def kill(*_a):
        raise AssertionError("a signal was sent")
    monkeypatch.setattr(os, "kill", kill)
    assert window_app.bring_forward(pid) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows")
def test_on_windows_neither_does_anything(monkeypatch):
    def refuse(*_a):
        raise AssertionError("not on Windows")
    monkeypatch.setattr(os, "kill", refuse)
    monkeypatch.setattr(signal, "signal", refuse)
    assert window_app.bring_forward(os.getpid()) is False
    window_app.ignore_forward()


def test_ignore_forward_outside_the_main_thread_is_harmless(monkeypatch):
    def refuse(*_a):
        raise ValueError("signal only works in main thread")
    monkeypatch.setattr(signal, "SIGUSR1", 30, raising=False)
    monkeypatch.setattr(signal, "signal", refuse)
    window_app.ignore_forward()
    window_app._catch_signals(types.SimpleNamespace(pages=None))()


def test_the_signal_brings_the_page_forward_and_is_put_back(run, monkeypatch):
    """With a stand-in for the signal module's two names, so this runs on Windows too."""
    handlers = []

    def set_handler(_number, handler):
        before = handlers[-1] if handlers else "before"
        handlers.append(handler)
        return before
    monkeypatch.setattr(signal, "SIGUSR1", 30, raising=False)
    monkeypatch.setattr(signal, "signal", set_handler)
    fake = FakeBrowser()
    fired = []

    def after(_win):
        before = len(fake.calls("Target.activateTarget"))
        handlers[0](30, None)
        fired.append(len(fake.calls("Target.activateTarget")) - before)
        fake.eof = True
    assert run(fake, after) == ""
    assert fired == [1]
    assert handlers[-1] == "before"
    handlers[0](30, None)                                             # a late signal: the pipe is closed, and nothing is sent
    assert len(fake.calls("Target.activateTarget")) == 2


def test_a_signal_before_the_page_is_known_does_nothing(monkeypatch):
    handlers = []
    monkeypatch.setattr(signal, "SIGUSR1", 30, raising=False)
    monkeypatch.setattr(signal, "signal", lambda _n, h: handlers.append(h) or "before")
    window_app._catch_signals(types.SimpleNamespace(pages=None))
    handlers[0](30, None)


def test_the_other_page_comes_forward_when_the_main_one_is_gone():
    fake = FakeBrowser()
    win = _adapter(fake)
    pages = win._pages
    pages.seen["DASH"] = f"{ORIGIN}/"
    del pages.seen[MAIN]
    pages.bring_forward()
    assert fake.calls("Target.activateTarget")[0]["params"] == {"targetId": "DASH"}


@posix
def test_a_real_signal_is_ignored_while_it_starts():
    before = signal.getsignal(signal.SIGUSR1)
    try:
        window_app.ignore_forward()
        assert signal.getsignal(signal.SIGUSR1) == signal.SIG_IGN
        assert window_app.bring_forward(os.getpid()) is True          # still here
    finally:
        signal.signal(signal.SIGUSR1, before)


@posix
def test_a_gone_pid_is_false():
    child = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    child.wait()
    assert window_app.bring_forward(child.pid) is False


# -- the file itself ---------------------------------------------------------------------------------------------------
def test_the_file_says_nothing_itself_and_imports_on_windows():
    text = Path(window_app.__file__).read_text(encoding="utf-8")
    assert "print(" not in text and "logging" not in text and "logger" not in text
    assert "\u2014" not in text
    assert "--remote-debugging-port" not in text
    assert "fcntl" not in vars(window_app)                            # imported where it is used: Windows has none


def test_the_start_step_holds_nothing_that_comes_from_outside():
    assert "{" not in window_app._SPAWN_SCRIPT and "os.execv(sys.argv[3], sys.argv[3:])" in window_app._SPAWN_SCRIPT


# -- the real start, on Linux and Mac ----------------------------------------------------------------------------------
_FAKE_PROGRAM = r"""#!{python}
import json, os, signal, sys
seen = {{"argv": sys.argv[1:], "usr1_default": signal.getsignal(signal.SIGUSR1) == signal.SIG_DFL, "low_open": [],
        "flags": os.environ.get("CHROMIUM_FLAGS"), "fds": [os.fstat(3).st_mode, os.fstat(4).st_mode]}}
for fd in range(5, 10):
    try:
        os.fstat(fd)
        seen["low_open"].append(fd)
    except OSError:
        pass
with open({report!r}, "w") as out:
    json.dump(seen, out)
buffer = b""
while True:
    chunk = os.read(3, 65536)
    if not chunk:
        break
    buffer += chunk
    while b"\0" in buffer:
        raw, buffer = buffer.split(b"\0", 1)
        os.write(4, json.dumps({{"id": json.loads(raw)["id"], "result": {{"it": "answered"}}}}).encode() + b"\0")
"""


@posix
def test_the_real_start_gives_the_program_its_pipe(monkeypatch, tmp_path):
    """Through the real start step, with every low file number in use here, to a small program that is no browser."""
    report = tmp_path / "report.json"
    program = tmp_path / "fake_browser"
    program.write_text(_FAKE_PROGRAM.format(python=sys.executable, report=str(report)), encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("CHROMIUM_FLAGS", "--remote-debugging-port=1234")
    before = signal.getsignal(signal.SIGUSR1)
    window_app.ignore_forward()
    put_back = window_app._catch_signals(types.SimpleNamespace(pages=None))
    args = ["--a=two words", "--b=$(touch x)", '--c="q"', "--d=#no"]
    try:
        ends = window_app._start(str(program), args)
        cdp = window_app._Cdp(ends)
        try:
            assert cdp.call("Browser.getVersion", timeout=20) == {"it": "answered"}
            seen = json.loads(report.read_text(encoding="utf-8"))
        finally:
            ends.close()
    finally:
        put_back()
        signal.signal(signal.SIGUSR1, before)
    assert seen["argv"] == args
    assert seen["low_open"] == []                                     # only 3 and 4
    assert seen["flags"] is None
    assert seen["usr1_default"] is True                               # the browser does not inherit the ignore
    assert ends._proc.returncode is not None                          # closing the pipe ended it
    assert not (tmp_path / "x").exists()


@posix
def test_a_program_that_exits_at_once_is_eof_not_a_wait(tmp_path):
    program = tmp_path / "fake_browser"
    program.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    program.chmod(0o755)
    ends = window_app._start(str(program), [])
    cdp = window_app._Cdp(ends)
    started = time.monotonic()
    try:
        with pytest.raises(window_app._Failed):
            cdp.call("Browser.getVersion", timeout=20)
    finally:
        ends.close()
    assert time.monotonic() - started < 10
    assert ends._proc.returncode == 3
