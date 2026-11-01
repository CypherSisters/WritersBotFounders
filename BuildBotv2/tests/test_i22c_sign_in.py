"""Lane I22c (D92): signing a browser in after the first link ran out.

- ``Sign in a browser.cmd`` (install.ps1 writes it next to ``Start BuildBot.cmd``) runs ``puffpai pair --running``
  for the same bot: a new link while BuildBot runs; with no bot running, it says so and how to start it. It
  always pauses, so the window stays open.
- ``GET /v1/signin`` (public, only at the Desktop's own address) names that file and its folder.
- A signed-out Desktop is ONE sign-in screen with those words: no tiles, and never "Bearer", "token" or
  "API key". Settings says what is not built yet in one plain line, with no route names.

The real-Core tests run ``serve`` on free ports (never 8420-8422) in a Job Object the test makes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO
from test_desktop_js import NODE

WEB = REPO / "packages" / "desktop" / "web"
CMD = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
NEVER = re.compile(r"bearer|token|api key", re.I)
windows_only = pytest.mark.skipif(os.name != "nt", reason="cmd.exe, PowerShell and Job Objects")


# -- the Core: where the file is ---------------------------------------------------------------------------
@pytest.fixture
def app(state):
    from puffpai_core.api import create_app
    return create_app(state)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """An install folder with Start BuildBot.cmd and Sign in a browser.cmd, as install.ps1 leaves it."""
    from puffpai_core.wizard import terminal
    folder = tmp_path / "Bird Bot" / "BuildBotv2"
    folder.mkdir(parents=True)
    (folder / "Start BuildBot.cmd").write_text("@echo off\n", encoding="ascii")
    (folder / "Sign in a browser.cmd").write_text("@echo off\n", encoding="ascii")
    monkeypatch.setattr(terminal, "launcher", lambda: folder / "Start BuildBot.cmd")
    return folder


def test_signin_route_names_the_file_and_its_folder_at_the_desktop_only(app, installed):
    from desktop_kit import desktop_client
    desk = desktop_client(app)
    r = desk.get("/v1/signin")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert r.json["file"] == "Sign in a browser.cmd" and r.json["folder"] == str(installed) and "command" not in r.json
    assert app.test_client().get("/v1/signin").status_code == 302                      # 127.0.0.1: moved (D79)
    assert app.test_client().get("/v1/signin", environ_base={"puffpai.listener": "pages"}).status_code == 404
    page = desk.get("/v1/recover").get_data(as_text=True)                               # the recovery page too
    assert f"double-click Sign in a browser.cmd in {installed}, then open the link it shows." in page
    assert not NEVER.search(page.replace("puffpai.device_key", ""))


def test_signin_paths_stay_at_the_desktops_own_address(app, installed):
    """The route answers before sign-in and names a folder, which names the Windows user. No other origin can read it:
    no page gets a CORS grant, the browser holds it to the same origin, and an app's own port is another origin."""
    from desktop_kit import desktop_client
    r = desktop_client(app).get("/v1/signin", headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert not [h for h in r.headers.keys() if h.lower().startswith("access-control-")]
    assert r.headers["Cross-Origin-Resource-Policy"] == "same-origin" and r.headers["X-Frame-Options"] == "DENY"
    assert app.test_client().get("/v1/signin", environ_base={"puffpai.listener": "pages"}).status_code == 404   # any app's port
    assert app.test_client().get("/v1/signin").status_code == 302                                               # any other host
    assert app.test_client().get("/v1/signin", headers={"Host": "evil.example:8420"}).status_code == 302


def test_a_signed_out_page_is_told_only_how_to_sign_in(app, installed, tmp_path, monkeypatch):
    from flask import g
    from puffpai_core.api.groups import ui
    with app.test_request_context("/v1/signin"):
        g.caller = None
        assert set(ui.signin_where()) == {"file", "folder"}             # no chat, no program path
        assert {"chat_file", "chat_folder", "chat_command", "cli"} == set(ui.signed_in_where())


def test_signin_route_with_no_file_says_nothing_about_a_folder(app, tmp_path, monkeypatch):
    from desktop_kit import desktop_client
    from puffpai_core.wizard import terminal
    monkeypatch.setattr(terminal, "launcher", lambda: tmp_path / "Start BuildBot.cmd")    # run from source
    got = desktop_client(app).get("/v1/signin").json
    assert got["file"] == "" and got["folder"] == ""
    assert got["command"].endswith("pair --running") and got["command"].startswith(("& \"", "$env:"))    # one that runs
    page = desktop_client(app).get("/v1/recover").get_data(as_text=True)
    assert "run this in a terminal on this computer" in page and "run puffpai pair" not in page


def test_pair_running_with_no_bot_says_so_and_how_to_start_it(installed, state, capsys):
    from puffpai_core import cli
    assert cli.main(["pair", "--running"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == [cli.PAIR_NOT_RUNNING,
                                f"Start it first: double-click Start BuildBot.cmd in {installed}, then run this again."]
    assert "#pair=" not in out and not (state / "pairing.json").exists()               # no code made for nothing


def test_pair_without_running_still_makes_a_link_for_start_buildbot(state, capsys):
    from puffpai_core import cli
    assert cli.main(["pair"]) == 0                     # Start BuildBot.cmd runs this before serve, as before
    assert "/#pair=" in capsys.readouterr().out


# -- install.ps1: Sign in a browser.cmd ---------------------------------------------------------------------
def test_install_writes_a_sign_in_launcher_and_names_it():
    text = (REPO / "install.ps1").read_text(encoding="ascii")          # ASCII only, for the old console
    block = text[text.index("$SignInLauncher = "):text.index("Set-Content -LiteralPath $SignInLauncher")]
    assert '"Sign in a browser.cmd"' in block
    assert '"setlocal"' in block and '"set ""PUFFPAI_BOT=$Name"""' in block and '"set ""PUFFPAI_STATE="""' in block
    assert '""%~dp0.venv\\Scripts\\puffpai.exe"" pair --running' in block
    assert '"pause"' in block                                           # always: the link stays to copy
    start = text[text.index("$Launcher = "):text.index("Set-Content -LiteralPath $Launcher")]
    assert " pair >con" not in start                       # D94, D100: Start opens the window, so no link of its own
    assert ">con echo To sign in another browser later, double-click Sign in a browser.cmd next to this file." in start
    assert start.index("Sign in a browser.cmd") < start.index("start <con >con")
    end = text[text.rindex("BuildBot is set up."):]
    assert "To sign in a browser while it runs, double-click Sign in a browser.cmd there." in end
    assert "Sign in a browser.cmd" in text[text.index('Step 4 "'):text.index('Step 5 "')]
    assert "SetEnvironmentVariable" not in text and "reg.exe" not in text.lower()       # no PATH change (D92)


WRITE_SIGNIN = r'''
$ErrorActionPreference = "Stop"
$text = Get-Content -LiteralPath $args[0] -Raw
$m = [regex]::Match($text, '(?ms)^\$SignInLauncher = .*?^Set-Content -LiteralPath \$SignInLauncher[^\r\n]*')
if (-not $m.Success) { throw "install.ps1 has no Sign in launcher block" }
$RepoDir = $args[1]
$Name = $args[2]
Invoke-Expression $m.Value
'''


def _signin_launcher(tmp_path):
    r"""A clone's folder: ``Sign in a browser.cmd`` written by install.ps1's own block (run by PowerShell), and a
    real console ``.venv\Scripts\puffpai.exe`` running ``console_core`` (the suite's guards) on this Python."""
    from pip._vendor.distlib.scripts import ScriptMaker
    repo = tmp_path / "Build Bot"
    scripts = repo / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    maker = ScriptMaker(None, str(scripts))
    maker.executable, maker.variants = sys.executable, {""}
    maker.make("puffpai = console_core:main")
    ps1 = tmp_path / "write_signin.ps1"
    ps1.write_text(WRITE_SIGNIN, encoding="utf-8")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(ps1), str(REPO / "install.ps1"), str(repo), "testbot"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    launcher = repo / "Sign in a browser.cmd"
    text = launcher.read_text(encoding="ascii")
    assert 'set "PUFFPAI_BOT=testbot"' in text and "pair --running" in text
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "PUFFPAI_"))}
    env.update(LOCALAPPDATA=str(tmp_path / "lad"), PYTHONUNBUFFERED="1", PIP_REQUIRE_VIRTUALENV="1",
               PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
               PYTHONPATH=os.pathsep.join([str(REPO / "core"), str(REPO / "tests")]))
    return repo, launcher, env


@windows_only
def test_sign_in_cmd_with_no_bot_running_says_so_and_waits_to_be_read(tmp_path):
    import console_kit
    from puffpai_core import cli
    repo, launcher, env = _signin_launcher(tmp_path)
    got = console_kit.run([CMD, "/c", str(launcher)], "none", env, "Press any key", tmp_path / "console",
                          screen=True, cwd=str(repo), settle_s=1.5, wait_s=3.0)
    screen = got["output"]
    assert got["ready"] and got["exit"] is None, screen                # paused on her key: she can read it
    assert cli.PAIR_NOT_RUNNING in screen and "Start it first" in screen, screen
    assert "#pair=" not in screen and "Open the link above" not in screen, screen


@windows_only
def test_sign_in_cmd_shows_a_new_link_for_the_running_bot_and_waits(tmp_path):
    import console_kit
    from test_i7_shutdown import _start_core, _wait_running
    repo, launcher, env = _signin_launcher(tmp_path)
    lad = tmp_path / "lad"
    job, _log = _start_core(dict(env, PUFFPAI_BOT="testbot"), tmp_path / "core")       # the bot the file names
    try:
        info = _wait_running(lad / "PuffPAI" / "testbot", job, None)
        got = console_kit.run([CMD, "/c", str(launcher)], "none", env, "Press any key", tmp_path / "console",
                              screen=True, cwd=str(repo), settle_s=1.5, wait_s=3.0)
        screen = got["output"]
        assert got["ready"] and got["exit"] is None, screen            # the window stays open to copy the link
        assert f"http://puffpai.localhost:{info['port']}/#pair=" in screen, screen   # the running bot's own port
        assert "Open the link above in your browser on this computer." in screen, screen
    finally:
        console_kit._end(job)                              # noqa: SLF001 - our own job only


# -- the Desktop ----------------------------------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sign_in_words_and_not_built_lines():
    for name in ("logic.js", "desktop.js", "pages/settings.js", "pages/chat.js", "pages/developer.js"):
        got = subprocess.run([NODE, "--check", str(WEB / name)], capture_output=True, text=True, timeout=60)
        assert got.returncode == 0, (name, got.stderr)
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "i22c.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "ok"


def _strings(src: str) -> list[str]:
    return re.findall(r'"((?:[^"\\]|\\.)*)"', src)


def test_desktop_sign_in_code_never_shows_the_core_refusal_words():
    desk = (WEB / "desktop.js").read_text(encoding="utf-8")
    body = desk[desk.index("async function pairFromHash()"):desk.index("function notice(n)")]
    assert not [s for s in _strings(body) if NEVER.search(s)]
    assert "(d || {}).error" not in body                                # the Core's pair refusal is not shown
    api = desk[desk.index("async function api("):desk.index("const said = ")]
    assert "L.signedOut(r.status, data)" in api and "L.signInWords(S.signin).line" in api   # plain words only
    start = desk[desk.index("async function start()"):]
    assert start.index("if (S.signedOut) return;") < start.index("route();")          # no tiles drawn signed out
    assert "if (S.signedOut) return;" in desk[desk.index("function show(view, arg)"):]
    chat = (WEB / "pages" / "chat.js").read_text(encoding="utf-8")
    assert "L.signedOut(r.status, data)" in chat


def test_settings_has_no_nested_later_boxes_or_route_names():
    src = (WEB / "pages" / "settings.js").read_text(encoding="utf-8")
    assert "P.later(" not in src
    shown = [s for s in _strings(src) if " " in s]                     # words, not ids, routes or class names
    bad = [s for s in shown if re.search(r"/v1/|\b(GET|PUT|POST|DELETE) /|Coming in a later build|Not in this build|"
                                         r"route|API_KEY|\bBearer\b|\btoken\b|\bAPI key\b", s)]
    assert bad == [], bad


# -- the real thing: a signed-out Desktop in headless Edge ----------------------------------------------------
SIGNED_OUT = """(() => ({
  card: Array.from(document.querySelectorAll('#notices > .card')).filter(c => !c.hidden).map(c => c.innerText),
  tiles: Array.from(document.querySelectorAll('.tile')).filter(t => t.offsetParent !== null).length,
  views: Array.from(document.querySelectorAll('#main .view')).filter(v => !v.hidden).map(v => v.id),
  rail: !document.querySelector('#rail .scroll').hidden,
  body: document.body.innerText}))()"""


@windows_only
def test_a_signed_out_desktop_is_one_sign_in_screen(state, tmp_path, monkeypatch):
    import console_kit
    from test_i10a_desktop_clicks import EDGE, Browser, _env, _free_ports, _wait_ready
    if EDGE is None:
        pytest.skip("Microsoft Edge is not installed")
    env = _env(PUFFPAI_STATE=str(state))
    folder = tmp_path / "core"
    folder.mkdir()
    core_port, pages_port, proxy_port = _free_ports(3)
    with open(folder / "serve.txt", "w", encoding="utf-8") as log:
        job = console_kit._held([sys.executable, str(REPO / "tests" / "console_core.py"), "serve", "--port",  # noqa: SLF001
                                 str(core_port), "--pages-port", str(pages_port), "--proxy-port", str(proxy_port)],
                                subprocess.CREATE_NEW_PROCESS_GROUP, env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
    browser = None
    try:
        _wait_ready(state, job)
        browser = Browser(tmp_path / "edge")
        for url, lead in ((f"http://puffpai.localhost:{core_port}/#settings", ""),
                          (f"http://puffpai.localhost:{core_port}/#pair=OLD-USED-CODE", "That sign-in link was already used")):
            browser.ws.call("Page.navigate", {"url": "about:blank"})     # a fresh load each time, not a hash change
            assert browser.wait("location.href === 'about:blank'")
            browser.ws.call("Page.navigate", {"url": url})
            assert browser.wait("!!document.querySelector('[data-signin]') && "
                                "document.querySelector('[data-said=signin]').innerText.indexOf('in a terminal') > 0"), \
                browser.js(SIGNED_OUT)
            got = browser.js(SIGNED_OUT)
            assert len(got["card"]) == 1 and got["card"][0].startswith("Sign in this browser"), got
            # this worktree has no Sign in a browser.cmd (no install), so the words fall back to the terminal
            assert ("To sign in this browser, run this in a terminal on this computer, then open the link "
                    "it shows: ") in got["card"][0] and "pair --running" in got["card"][0], got
            assert lead in got["card"][0], got
            assert got["tiles"] == 0 and got["views"] == [] and got["rail"] is False, got
            assert not NEVER.search(got["body"]), got["body"]
            assert "Sign in first" not in got["body"], got["body"]
        # a new link opened in this same tab (only the # part changes): the Desktop signs in and comes up
        pair = subprocess.run([sys.executable, str(REPO / "tests" / "console_core.py"), "pair", "--running"], env=env,
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        url = next(line.strip() for line in pair.stdout.splitlines() if "#pair=" in line)
        assert url.startswith(f"http://puffpai.localhost:{core_port}/#pair="), url        # the running bot's port
        browser.js(f"location.hash = {url[url.index('#'):]!r}")
        assert browser.wait("!document.querySelector('[data-signin]') && !!document.getElementById('view-home') && "
                            "!document.getElementById('view-home').hidden && !document.querySelector('#rail .scroll').hidden"), \
            browser.js(SIGNED_OUT)
    finally:
        if browser is not None:
            browser.close()
        console_kit._end(job)                              # noqa: SLF001 - our own job only


@windows_only
def test_signed_in_settings_says_what_is_not_built_in_plain_lines(state, tmp_path, monkeypatch, drawbridge):
    from test_i10a_desktop_clicks import EDGE, _desktop
    if EDGE is None:
        pytest.skip("Microsoft Edge is not installed")
    with _desktop(state, tmp_path, monkeypatch) as browser:
        browser.js("location.hash = '#settings'")
        assert browser.wait("!!document.getElementById('view-settings') && "
                            "document.getElementById('view-settings').textContent.indexOf('Paired devices') >= 0 && "
                            "document.getElementById('view-settings').textContent.indexOf('another browser') >= 0", 30)
        browser.js("document.querySelectorAll('#view-settings details.act').forEach((d) => { d.open = true; })")
        text = browser.js("document.getElementById('view-settings').innerText")
        assert "Changing the brain here is not built yet. Setup's brain step changes it." in text, text
        assert "To sign in another browser on this computer, " in text, text
        for never in ("Coming in a later build", "Not in this build", "/v1/", "PUT /", "GET /", "Bearer", "API key"):
            assert never not in text, (never, text)
        assert browser.js("document.querySelectorAll('#view-settings .card.later').length") == 0


# -- fix round: only the Core's sign-in refusal signs the Desktop out, never a route's own 401 -------------------
PIN = "Tq7#mz2Lp!"


def _signed_out_js(status, body) -> bool:
    import json
    js = (f"const L = require({json.dumps(str(WEB / 'logic.js'))});"
          f"process.stdout.write(String(L.signedOut({int(status)}, {json.dumps(body)})));")
    got = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    return got.stdout == "true"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_wrong_pin_401_is_not_a_sign_out_but_the_sign_in_refusal_is(app, state):
    """The Core's real answers, read by the Desktop's own logic.js: a wrong PIN (vault code "wrong") keeps its
    words; the refusal with no login (no code today, "sign_in" once lane I22b adds it) is the sign-in screen."""
    from desktop_kit import DESKTOP, desktop_client
    from puffpai_core.api.app import wire_core
    core = wire_core(app)
    core.vault.create("here", None, PIN)
    desk = desktop_client(app)
    desk.sign_in(core.tokens.issue_device("browser"))
    assert desk.post("/v1/vault/lock", json={}, headers=DESKTOP).status_code == 200
    wrong = desk.post("/v1/vault/unlock", json={"method": "pin", "secret": "wrong-pin-123"}, headers=DESKTOP)
    assert wrong.status_code == 401 and wrong.json["code"] == "wrong" and wrong.json["error"] == "That didn't match."
    assert _signed_out_js(401, wrong.json) is False
    out = desktop_client(app).get("/v1/vault/status")                  # no login at all
    assert out.status_code == 401
    assert out.json.get("code") in (None, "sign_in"), out.json         # lane I22b adds "sign_in"
    assert _signed_out_js(401, out.json) is True
    assert _signed_out_js(401, dict(out.json, code="sign_in")) is True
    assert _signed_out_js(401, None) is True and _signed_out_js(401, "not json") is True
    assert _signed_out_js(403, {"error": "x"}) is False


#: A fetch stand-in for one path only: the Desktop's own code runs, with the Core's answer for that path.
STUB = """(() => { const real = window.fetch; window.__stub = %s;
  window.fetch = (u, o) => String(u).indexOf(window.__stub.path) >= 0
    ? Promise.resolve(new Response(JSON.stringify(window.__stub.body), {status: 401, headers: {"Content-Type": "application/json"}}))
    : real(u, o); return true; })()"""


@windows_only
def test_a_wrong_pin_keeps_the_signed_in_desktop_and_a_sign_in_401_does_not(state, tmp_path, monkeypatch, drawbridge):
    import json
    from test_i10a_desktop_clicks import EDGE, _desktop
    if EDGE is None:
        pytest.skip("Microsoft Edge is not installed")
    wrong = {"error": "That didn't match.", "code": "wrong"}          # the Core's answer (test above)
    with _desktop(state, tmp_path, monkeypatch) as browser:
        assert browser.js(STUB % json.dumps({"path": "/v1/vault/unlock", "body": wrong}))
        got = browser.js("""(async () => { const x = await Puff.api("POST", "/v1/vault/unlock", {method: "pin", secret: "1"});
          return {status: x.status, said: Puff.said(x), out: Puff.S.signedOut,
                  card: !!document.querySelector('[data-signin]'), rail: !document.querySelector('#rail .scroll').hidden,
                  home: !document.getElementById('view-home').hidden}; })()""")
        assert got == {"status": 401, "said": "That didn't match.", "out": False, "card": False, "rail": True,
                       "home": True}, got
        for body in ({"error": "Sign in first.", "code": "sign_in"}, {"error": "Sign in first."}):
            browser.js("location.reload()")
            assert browser.wait("!!document.getElementById('view-home') && !document.getElementById('view-home').hidden")
            assert browser.js(STUB % json.dumps({"path": "/v1/checklist", "body": body}))
            got = browser.js("""(async () => { const x = await Puff.api("GET", "/v1/checklist");
              return {said: Puff.said(x), out: Puff.S.signedOut, card: !!document.querySelector('[data-signin]'),
                      tiles: Array.from(document.querySelectorAll('.tile')).filter(t => t.offsetParent !== null).length}; })()""")
            assert got["out"] is True and got["card"] is True and got["tiles"] == 0, (body, got)
            assert got["said"].startswith("To sign in this browser,") and "Sign in first" not in got["said"], got
