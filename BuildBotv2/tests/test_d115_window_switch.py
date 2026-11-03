"""D115 (lane window-switch): the window's Terminal, Window, Dashboard switch and its Store entry.

``POST /v1/terminal/open`` is for a paired-device token and takes one chat id; the console it starts is a stub here, so
no test opens a console on her screen. The terminal chat runs on a stand-in for the Core's answers. The page itself is
looked at in a real browser by ``tests/window_demo_core.py``."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core import cli, devshell, termchat, window
from puffpai_core.api.groups import settings
from puffpai_core.zone2 import conversations

PORT = 51234
WEB = REPO / "packages" / "desktop" / "web"


# -- the route --------------------------------------------------------------------------------------------------------
@pytest.fixture
def route(monkeypatch, state):
    from puffpai_core.api import create_app
    app = create_app(state, port=PORT)
    core = app.extensions["puffpai"]
    calls = []
    answer = {"value": (True, cli.TERMINAL_OPENING)}

    def open_terminal(st, conversation):
        calls.append((st, conversation))
        return answer["value"]
    monkeypatch.setattr(cli, "open_terminal", open_terminal)
    device = {"Authorization": f"Bearer {core.tokens.issue_device('laptop')}"}
    return types.SimpleNamespace(client=app.test_client(), core=core, calls=calls, answer=answer, state=state, device=device)


def test_the_route_is_for_her_paired_devices_only(route):
    assert route.client.post("/v1/terminal/open").status_code == 401
    pkg = {"Authorization": f"Bearer {route.core.tokens.issue_package_token('freshtable')}"}
    assert route.client.post("/v1/terminal/open", headers=pkg).status_code == 403
    key = {"Authorization": f"Bearer {route.core.tokens.issue_owner_key()}"}
    assert route.client.post("/v1/terminal/open", headers=key).status_code == 403
    assert route.client.get("/v1/terminal/open", headers=route.device).status_code in (401, 403, 405)
    assert route.calls == []


def test_the_route_needs_a_same_origin_call_when_signed_in_by_cookie(route):
    token = route.core.tokens.issue_device("paired browser: a browser")
    second = route.core.tokens.bind_device(token)
    route.client.set_cookie("puffpai_device", token, domain="puffpai.localhost")
    host = {"Host": f"puffpai.localhost:{PORT}", "X-PuffPAI-Device": second}
    assert route.client.post("/v1/terminal/open", headers=host).status_code == 403               # no X-PuffPAI
    bad = {**host, "X-PuffPAI": "1", "Origin": "http://evil.test"}
    assert route.client.post("/v1/terminal/open", headers=bad).status_code == 403
    assert route.calls == []
    good = {**host, "X-PuffPAI": "1", "Origin": f"http://puffpai.localhost:{PORT}"}
    assert route.client.post("/v1/terminal/open", headers=good).status_code == 200
    assert len(route.calls) == 1


def test_the_route_takes_the_chat_id_and_nothing_else(route):
    body = {"conversation": "c-12ab", "state": r"C:\elsewhere", "argv": ["calc.exe"], "url": "file:///x"}
    r = route.client.post("/v1/terminal/open", headers=route.device, json=body)
    assert r.status_code == 200 and r.json == {"ok": True, "line": cli.TERMINAL_OPENING}
    assert route.calls == [(route.state, "c-12ab")]


@pytest.mark.parametrize("body", [None, {}, [], "main", {"other": 1}])
def test_no_chat_named_is_the_main_chat(route, body):
    r = route.client.post("/v1/terminal/open", headers=route.device, json=body)
    assert r.status_code == 200 and route.calls == [(route.state, conversations.DEFAULT)]


@pytest.mark.parametrize("bad", ["", " ", "../x", r"..\x", "a b", "a&calc", "a|b", 'a"b', "%TEMP%", "a\n", "-x", "a" * 65, 7, None, ["main"], {"a": 1}])
def test_anything_but_a_chats_id_is_refused(route, bad):
    r = route.client.post("/v1/terminal/open", headers=route.device, json={"conversation": bad})
    assert r.status_code == 400 and r.json["error"] == "That is not a chat's id."
    assert route.calls == []


def test_the_route_says_when_it_cant_open(route):
    route.answer["value"] = (False, cli.TERMINAL_NOT_HERE)
    r = route.client.post("/v1/terminal/open", headers=route.device)
    assert r.status_code == 200 and r.json == {"ok": False, "pane": True, "line": cli.TERMINAL_NOT_HERE}


def test_the_window_may_ask_for_it_and_its_allowlist_is_as_it_was():
    origin = window.origin_of(PORT)
    assert window.may_request(origin + "/v1/terminal/open", PORT)
    for no in ("/ui/x.js", "/", "/recover", "/window/%2e%2e/ui/x.js", "/v1%2fterminal/open"):
        assert not window.may_request(origin + no, PORT), no
    assert not window.may_request(f"http://127.0.0.1:{PORT}/v1/terminal/open", PORT)
    assert window._PAGE_CALLS == frozenset({"open_browser", "open_dashboard", "_pywebviewAlert"})   # no new way past the Core's auth


def _safe_mode(monkeypatch, core, reading):
    monkeypatch.setattr(type(core.caretaker), "in_safe_mode", property(reading))


def test_safe_mode_refuses_the_console_as_it_refuses_the_command_line(route, monkeypatch):
    _safe_mode(monkeypatch, route.core, lambda _self: True)
    r = route.client.post("/v1/terminal/open", headers=route.device, json={"conversation": "c-12ab"})
    assert r.status_code == 409 and r.json == {"error": settings.TERMINAL_SAFE_MODE, "code": "safe_mode"}
    assert route.client.post("/v1/terminal/open", headers=route.device, json={"conversation": "a&b"}).status_code == 409
    assert route.calls == []
    token = route.core.tokens.issue_device("paired browser: a browser")
    browser = {"Authorization": f"Bearer {token}", "X-PuffPAI-Device": route.core.tokens.bind_device(token)}
    shell = route.client.post("/v1/dev/shell", headers=browser, json={"shell": "cmd"})
    assert shell.status_code == 409 and shell.json["code"] == r.json["code"]


def test_safe_mode_that_cant_be_read_is_closed(route, monkeypatch):
    def broken(_self):
        raise OSError("no")
    _safe_mode(monkeypatch, route.core, broken)
    assert route.client.post("/v1/terminal/open", headers=route.device).status_code == 409
    assert route.calls == []


def test_the_contract_says_safe_mode_is_a_409():
    text = (REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
    block = text[text.index("  /v1/terminal/open:"):text.index("  /v1/settings/drawbridge:")]
    assert '"409"' in block and "safe_mode" in block


# -- the console ------------------------------------------------------------------------------------------------------
def _real_cmd():
    return '"' + os.path.join(devshell.system_dir(), "cmd.exe") + '"'


class _Started(types.SimpleNamespace):
    """One console that would start. A failed test shows its line, never its environment."""

    def __repr__(self):
        return f"<console {self.line!r}>"


@pytest.fixture
def console(monkeypatch):
    monkeypatch.setattr(cli, "_terminal_opened", {})
    started = []

    def popen(line, **kw):
        started.append(_Started(line=line, **kw))
        return types.SimpleNamespace(pid=1)
    monkeypatch.setattr(subprocess, "Popen", popen)
    return started


@pytest.mark.skipif(os.name != "nt", reason="the console is built for Windows only so far")
def test_it_starts_this_program_with_no_words_in_a_console_of_its_own(console, state, monkeypatch):
    monkeypatch.setattr(sys, "argv", [r"C:\x\nothing-here"])
    assert cli.open_terminal(state, "c-12ab") == (True, cli.TERMINAL_OPENING)
    got = console[0]
    assert got.creationflags == subprocess.CREATE_NEW_CONSOLE
    assert got.cwd == str(state)
    assert got.env[termchat.CONVERSATION_ENV] == "c-12ab" and got.env["PUFFPAI_STATE"] == str(state)
    argv = [got.env[f"PUFFPAI_CHAT_ARG_{i}"] for i in range(4)]
    assert argv == [sys.executable, "-P", "-m", "puffpai_core.cli"]             # no words: the chat. -P: nothing from its folder
    assert "PUFFPAI_CHAT_ARG_4" not in got.env
    # every part reaches cmd in a variable, and neither the chat nor the folder is in the line
    assert got.line == (f'{_real_cmd()} /d /v:off /c "title BuildBot chat& "%PUFFPAI_CHAT_ARG_0%" "%PUFFPAI_CHAT_ARG_1%" '
                        '"%PUFFPAI_CHAT_ARG_2%" "%PUFFPAI_CHAT_ARG_3%" || pause"')


@pytest.mark.skipif(os.name != "nt", reason="the console is built for Windows only so far")
def test_two_presses_are_one_and_a_press_later_is_another(console, state, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    assert cli.open_terminal(state, "main")[0] and cli.open_terminal(state, "main")[0]
    assert len(console) == 1
    clock[0] += cli.TERMINAL_WAIT_S
    assert cli.open_terminal(state, "main")[0]
    assert len(console) == 2


@pytest.mark.skipif(os.name != "nt", reason="the console is built for Windows only so far")
def test_a_console_that_cant_start_says_so_and_the_next_press_tries_again(console, state, monkeypatch):
    def fails(line, **kw):
        raise OSError("no")
    monkeypatch.setattr(subprocess, "Popen", fails)
    ok, line = cli.open_terminal(state, "main")
    assert not ok and line == cli.TERMINAL_FAILED.format(why="OSError") and "bare" not in line
    monkeypatch.setattr(subprocess, "Popen", lambda line, **kw: console.append(line))
    assert cli.open_terminal(state, "main")[0] and len(console) == 1


@pytest.mark.skipif(os.name != "nt", reason="the console is built for Windows only so far")
def test_another_chat_within_the_wait_opens_its_own_and_the_same_chat_stays_one(console, state, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    assert cli.open_terminal(state, "c-a") == (True, cli.TERMINAL_OPENING)
    assert cli.open_terminal(state, "c-b") == (True, cli.TERMINAL_OPENING)
    assert [c.env[termchat.CONVERSATION_ENV] for c in console] == ["c-a", "c-b"]    # the reply was true for each
    clock[0] += cli.TERMINAL_WAIT_S - 0.5
    assert cli.open_terminal(state, "c-a")[0] and cli.open_terminal(state, "c-b")[0]
    assert len(console) == 2
    assert cli.open_terminal(state.with_name("other"), "c-a")[0] and len(console) == 3      # another bot, the same id
    clock[0] += 0.5
    assert cli.open_terminal(state, "c-a")[0] and len(console) == 4


def test_elsewhere_it_says_so_in_a_plain_line(monkeypatch, console, state):
    monkeypatch.setattr(cli.os, "name", "posix", raising=False)
    try:
        assert cli.open_terminal(state, "main") == (False, cli.TERMINAL_NOT_HERE)
    finally:
        monkeypatch.undo()
    assert console == []


def test_serve_is_started_as_it_was(monkeypatch):
    monkeypatch.setattr(sys, "argv", [r"C:\x\nothing-here"])
    assert cli._serve_argv(8420) == [sys.executable, "-m", "puffpai_core.cli", "serve"]
    assert cli._serve_argv(9000)[-2:] == ["--port", "9000"]


# -- the console's line in real cmd.exe: captured here, then run with no window, so nothing shows on her screen ---------
windows_only = pytest.mark.skipif(os.name != "nt", reason="cmd.exe")


def _captured(start, *args):
    started = []

    def popen(line, **kw):
        started.append(_Started(line=line, **kw))
        return types.SimpleNamespace(pid=1)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess, "Popen", popen)
        mp.setattr(cli, "_terminal_opened", {})
        start(*args)
    return started[0]


def _ran(got, line=None):
    return subprocess.run(line or got.line, creationflags=subprocess.CREATE_NO_WINDOW, env=got.env, cwd=getattr(got, "cwd", None),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=90)


def _says_where(folder):
    """A batch file that says the folder and the state it got: only a real cmd.exe runs it."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "x.cmd").write_text('@echo off\r\necho REAL-CMD "%CD%" "%PUFFPAI_STATE%"\r\n', encoding="ascii", newline="")
    return str(folder / "x.cmd")


def _both_lines(state, stub, monkeypatch):
    monkeypatch.setattr(cli, "_self_argv", lambda: [stub])
    return {"open_terminal": _captured(cli.open_terminal, state, "main"), "_launch_serve": _captured(cli._launch_serve, [stub], "T")}


@windows_only
def test_a_cmd_exe_in_the_cores_folder_is_never_the_one_that_runs(tmp_path, state, monkeypatch):
    plant = tmp_path / "core-folder"
    plant.mkdir()
    shutil.copy(os.path.join(devshell.system_dir(), "whoami.exe"), plant / "cmd.exe")
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)     # this setting would hide the flaw
    monkeypatch.chdir(plant)
    state.mkdir()
    for name, got in _both_lines(state, _says_where(tmp_path / "stub"), monkeypatch).items():
        assert got.line.startswith(f"{_real_cmd()} /d /v:off /c "), name
        done = _ran(got)
        assert done.returncode == 0 and "REAL-CMD" in done.stdout, name


@windows_only
@pytest.mark.parametrize("value", [".", ";x"])
@pytest.mark.parametrize("form", ["python", "exe"])
def test_a_relative_pythonpath_cant_bring_a_module_in_from_the_bot_folder(state, monkeypatch, value, form):
    planted = state / "puffpai_core"
    planted.mkdir(parents=True)
    for name in ("__init__.py", "cli.py"):
        (planted / name).write_text("print('PLANTED')\n", encoding="ascii")
    exe = Path(sys.executable).with_name("puffpai.exe")
    if form == "exe" and not exe.is_file():
        pytest.skip("no puffpai.exe beside this Python")
    argv = [str(exe), "--help"] if form == "exe" else [sys.executable, "-m", "puffpai_core.cli", "--help"]
    monkeypatch.setattr(cli, "_self_argv", lambda: list(argv))
    monkeypatch.setenv("PYTHONPATH", value)
    monkeypatch.setenv("PYTHONHOME", str(state / "no-such-python"))
    monkeypatch.setenv("PYTHONSTARTUP", str(state / "no-such-file.py"))
    got = _captured(cli.open_terminal, state, "main")
    kept = {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"} & set(got.env)
    assert not kept
    done = _ran(got)                                                          # the real install still imports
    assert "PLANTED" not in done.stdout and done.returncode == 0 and "usage" in done.stdout.lower()


@windows_only
@pytest.mark.parametrize("as_given", [Path, str])
def test_a_relative_state_reaches_the_console_whole(tmp_path, monkeypatch, as_given):
    root = tmp_path / "root"
    (root / "relstate").mkdir(parents=True)
    stub = _says_where(tmp_path / "stub")
    monkeypatch.setattr(cli, "_self_argv", lambda: [stub])
    monkeypatch.chdir(root)
    whole = os.path.join(os.getcwd(), "relstate")
    got = _captured(cli.open_terminal, as_given("relstate"), "main")
    assert got.cwd == whole and got.env["PUFFPAI_STATE"] == whole
    assert f'REAL-CMD "{whole}" "{whole}"' in _ran(got).stdout


@windows_only
def test_a_bang_in_a_path_means_nothing_even_when_a_setting_turns_delayed_expansion_on(tmp_path, state, monkeypatch):
    state.mkdir()
    monkeypatch.setenv("USERNAME", "someone")
    stub = _says_where(tmp_path / "!USERNAME!")
    for name, got in _both_lines(state, stub, monkeypatch).items():
        assert " /d /v:off /c " in got.line, name
        assert "REAL-CMD" in _ran(got).stdout, name
        # The registry setting is not changed here: on before our switch stands for it. Without our switch it breaks.
        assert "REAL-CMD" in _ran(got, got.line.replace(" /v:off ", " /v:on /v:off ")).stdout, name
        assert "REAL-CMD" not in _ran(got, got.line.replace(" /v:off ", " /v:on ")).stdout, name


# -- the terminal chat, in the chat it was opened in ------------------------------------------------------------------
class Answers:
    """The Core's answers, with what the chat asked for written down."""

    def __init__(self, history=None):
        self.calls, self.streams, self.history = [], [], history if history is not None else []

    def call(self, method, path, body=None, timeout=termchat.CALL_TIMEOUT_S):
        self.calls.append((method, path))
        if path.startswith("/v1/chat/history"):
            return 200, self.history
        return 200, []

    def stream(self, path, body):
        self.streams.append((path, dict(body)))
        return 200, None, iter([{"type": "token", "text": "Hello."}, {"type": "done", "text": "Hello."}])


def _chat(api, conversation=""):
    out = io.StringIO()
    keys = iter(["hi there"])

    def read(prompt=""):
        try:
            return next(keys)
        except StopIteration:
            raise EOFError from None
    chat = termchat.Chat(api, read_line=read, read_hidden=read, open_url=lambda u: None, sleep=lambda s: None,
                         look=termchat.Look(out, env={}, tty=lambda s: False, vt_on=lambda s: False))
    chat.conversation = conversation
    return chat, out


def test_opened_from_the_window_it_talks_in_that_chat_and_shows_where_she_was():
    long = "x" * (termchat.RECENT_CHARS + 50)
    api = Answers([{"role": "user", "content": "plan\n  dinners"}, {"role": "assistant", "content": "Tacos. \x1b[31mred\x1b[0m"},
                   {"role": "assistant", "content": long}, {"role": "assistant", "content": ""}, "not a line"])
    chat, out = _chat(api, "c-12ab")
    assert chat.run() == 0
    assert api.streams == [("/v1/chat/stream", {"text": "hi there", "conversation": "c-12ab"})]
    assert ("GET", f"/v1/chat/history?conversation=c-12ab&limit={termchat.RECENT_LINES}") in api.calls
    text = out.getvalue()
    assert termchat.RECENT_SAID in text and "You: plan dinners" in text and "Tacos." in text
    assert "\x1b" not in text
    assert "x" * termchat.RECENT_CHARS + "..." in text and long not in text


def test_an_empty_chat_says_nothing_about_last_words():
    chat, out = _chat(Answers([]), "main")
    assert chat.run() == 0
    assert termchat.RECENT_SAID not in out.getvalue()


def test_a_plain_puffpai_is_as_it_was():
    api = Answers([{"role": "user", "content": "plan dinners"}])
    chat, out = _chat(api)
    assert chat.run() == 0
    assert api.streams == [("/v1/chat/stream", {"text": "hi there"})]
    assert not any(p.startswith("/v1/chat/history") for _m, p in api.calls)
    assert "plan dinners" not in out.getvalue()


@pytest.mark.parametrize("asked,got", [("c-12ab", "c-12ab"), ("main", "main"), ("", ""), ("../x", ""), ("a&b", ""), ("a\n", "")])
def test_main_reads_the_chat_by_the_stores_rule(monkeypatch, asked, got):
    seen = []
    chat = types.SimpleNamespace(conversation="", run=lambda: seen.append(chat.conversation) or 0)
    monkeypatch.setattr(termchat, "connect", lambda *a, **k: chat)
    monkeypatch.setenv(termchat.CONVERSATION_ENV, asked)
    assert termchat.main(out=io.StringIO()) == 0
    assert seen == [got]


# -- the page, read as text: the rest is looked at in a real browser ----------------------------------------------------
def test_the_page_has_the_three_way_switch():
    html = (WEB / "window.html").read_text(encoding="utf-8")
    for name in ("terminal", "window", "dashboard"):
        assert f'data-switch="{name}"' in html
    assert html.index('data-switch="terminal"') < html.index('data-switch="window"') < html.index('data-switch="dashboard"')


def test_the_store_opens_in_the_pane_on_the_same_routes_with_no_core_part_of_its_own():
    js = (WEB / "window.js").read_text(encoding="utf-8")
    assert 'openDashboard("#apps/available")' not in js                         # /apps and + Add a look open it here now
    for route_text in ('"/v1/packages/available"', '"/v1/store" +', '"/v1/store/sources', '"POST", "/v1/packages"', "L.storeShelf(", "cardInto(said2"):
        assert route_text in js, route_text


def test_a_store_tab_is_not_brought_back_at_start():
    """D96: the store is read only when she opens it."""
    js = (WEB / "window.js").read_text(encoding="utf-8")
    body = js[js.index("function restoreTabs()"):js.index("async function start()")]
    assert "storeTab()" not in body and "showStore" not in body and '"store"' not in body


# -- the PIN, read as text: the real run is by a driver on tests/window_demo_core.py's Core and browser ------------------
def _js_function(name):
    js = (WEB / "window.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    body = js[js.index(f"function {name}("):]
    return body[:body.index("\n  }\n")]


def test_the_pin_row_reads_once_clears_at_that_moment_and_goes_only_to_the_unlock_route():
    body = _js_function("vaultRow")
    read = body.index("const secret = box.value; box.value = \"\";")
    assert read < body.index('api("POST", "/v1/vault/unlock", { method: "pin", secret })')
    assert body.count("box.value") == 2 and body.count("secret") == 3 and body.count("api(") == 1
    assert 'pinBox("Your PIN")' in body and '"w-attnrow w-stop"' in body and '"data-waiting": "vault"' in body
    assert 'e.key === "Enter"' in body and "said(x," in body and "drawStatus()" in body and "L.UNLOCK_WAITING" in body
    assert "S.vaultNotNow = true; drawWaiting();" in body and "notNow.hidden = waits" in body
    for never in ("keep(", "localStorage", "console.", "setAttribute", "title", "toast(", "say("):
        assert never not in body, never


def test_the_waiting_bar_never_takes_a_kept_row_out_and_empties_a_dropped_one():
    body = _js_function("drawWaiting")
    assert "clear(box)" not in body and "replaceChildren" not in body           # either would take the focus from the box
    assert "forgetTyped(n); n.remove();" in body and "box.insertBefore(n, box.children[i] || null)" in body
    js = (WEB / "window.js").read_text(encoding="utf-8")
    assert 'rows.push({ key: "vault", node: vaultRow })' in js                  # Toolsmith starting to wait keeps the box


def test_a_turn_that_ends_does_not_move_her_typing_into_the_chat():
    js = (WEB / "window.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    end = js[js.index("v.busy = false; v.turn = null;"):js.index("titleFromCore(v);")]
    assert 'if (S.chatHadFocus && !inFrame) $("input").focus();' in end and 'document.activeElement.matches("iframe")' in end
    assert '$("send").disabled = false; $("input").focus();' not in js
    assert 'document.addEventListener("focusin", (e) => { S.chatHadFocus = $("composer").contains(e.target); });' in js
    assert "chatHadFocus: true" in js and js.count("chatHadFocus") == 3                      # a yes or no, kept nowhere else
    # the only others: pickChat and go("chat") from her own click, fillFrom from the list in the chat box itself
    assert js.count('$("input").focus()') == 4
    assert '$("input").focus();\n  }\n  function newChat()' in js and 'else if (view === "chat") $("input").focus();' in js
    assert 'function fillFrom(c) {' in js


def test_a_redraw_waits_for_a_secret_being_typed_by_its_value():
    body = _js_function("redraw")
    assert """querySelectorAll('input[type=password], input[data-name="code"]')).some((i) => i.value)""" in body
    assert "activeElement" not in body                                          # once sent the box is empty, focus or not
    assert "if (t.key === S.active && !typing) drawPage(t, true); else t.stale = true;" in body
    again = _js_function("drawPageAgain")
    assert "if (!typed) { t.stale = false; drawPage(t); }" in again and "drawPage(t, true)" not in again
    js = (WEB / "window.js").read_text(encoding="utf-8")
    # the page's own rd() must still draw: a recovery code it leaves in its text box goes once the form has read it
    assert """$("view").addEventListener("submit", (ev) => { ev.target.querySelectorAll('input[data-name="code"]').forEach((i) => { i.value = ""; }); });""" in js
    vault = (WEB / "pages" / "vault.js").read_text(encoding="utf-8")
    assert "i.dataset.name = name;" in vault and '["code", "Your recovery code", "text"]' in vault


def test_nothing_typed_stays_in_a_dropped_node():
    js = (WEB / "window.js").read_text(encoding="utf-8")
    assert 'const TYPED_BOXES = "input:not([type=checkbox]):not([type=radio]), textarea";' in js
    assert 'const forgetTyped = (n) => n.querySelectorAll(TYPED_BOXES).forEach((i) => { i.value = ""; });' in js
    body = _js_function("drawPage")
    assert body.index("if (!live) forgetTyped(t.node);") < body.index(".draw(t.sec, t.arg, live)")


def test_a_box_that_holds_what_it_was_drawn_with_is_not_typing():
    again = _js_function("drawPageAgain")
    assert "i.value && i.value !== i.defaultValue" in again
    assert 'value: "weather"' in (WEB / "addapi.js").read_text(encoding="utf-8")
    assert 'else n.setAttribute(k, v === true ? "" : v);' in (WEB / "window.js").read_text(encoding="utf-8")     # by attribute: its defaultValue


def test_the_command_line_has_no_own_window_in_the_one_window():
    js = (WEB / "window.js").read_text(encoding="utf-8")
    assert "window.Puff = { oneWindow: true," in js
    assert "alone || P.oneWindow ? null : own" in (WEB / "pages" / "developer.js").read_text(encoding="utf-8")
    assert "oneWindow" not in (WEB / "desktop.js").read_text(encoding="utf-8")   # the dashboard stays as it is


def test_the_bridges_pin_is_read_once_and_a_tab_with_a_recovery_code_stays():
    body = _js_function("bridgePinCard")
    assert body.index('const pin = p.value; p.value = "";') < body.index('api("POST", "/v1/bridge/open", { pin })')
    assert 'btn("Keep it closed", () => { p.value = ""; card.remove(); })' in body
    close = _js_function("closeTab")
    assert close.index(".card.note.high pre.box") < close.index("forgetTyped(t.node)") < close.index("t.node.remove()")
    # the class it looks for is the one the page draws, and no other page has a pre in such a card
    assert 'el("div", { class: "card note high" }, [el("h3", { text: "Your recovery code" }), el("pre", { class: "box"' in (WEB / "pages" / "vault.js").read_text(encoding="utf-8")


def test_the_core_s_words_only_ever_reach_the_page_as_text():
    files = [WEB / "window.js", WEB / "logic.js"] + [WEB / "pages" / f"{name}.js" for name in PAGE_FILES]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "srcdoc", "DOMParser", "eval(", "new Function"):
            assert sink not in text, (f.name, sink)
    assert 'el("h5", { text: n.title }), n.text ? el("p", { text: n.text })' in _js_function("notice")
    pin_card = _js_function("bridgePinCard")
    assert 'el("h5", { text: plan.title })' in pin_card and 'el("p", { text: t })' in pin_card and "says.textContent = said(o," in pin_card


def test_no_page_in_the_pane_names_the_shell():
    for name in PAGE_FILES:
        assert "pywebview" not in (WEB / "pages" / f"{name}.js").read_text(encoding="utf-8"), name
    js = (WEB / "window.js").read_text(encoding="utf-8")
    assert js.count("pywebview") == 2 and "const a = window.pywebview && window.pywebview.api;" in js
    puff = js[js.index("window.Puff = {"):]
    assert "shell" not in puff[:puff.index("};")]


class _Nav:
    """What WebView2 hands the lock for a navigation."""

    def __init__(self, uri, clicked):
        self.uri, self.IsUserInitiated, self.Cancel = uri, clicked, False

    def get_Uri(self):
        return self.uri


def _locked_edge(monkeypatch):
    class Edge:
        def on_navigation_start(self, sender, args):
            args.allowed = True

        def on_new_window_request(self, sender, args):
            raise AssertionError("pywebview's own hand-over")

        def on_script_notify(self, sender, args):
            args.reached = True

        def on_webview_ready(self, sender, args):
            pass

        def on_web_resource_request(self, sender, args):
            pass
    opened = []
    monkeypatch.setattr(window, "open_tab", lambda url: opened.append(url) or True)
    window.lock_down(Edge, PORT, lambda why: pytest.fail(why))
    return Edge(), opened


@pytest.mark.parametrize("link", ["/v1/recover", "/", "/ui/x.js", "/window/pages/vault.js"])
def test_a_link_in_a_page_cant_move_the_top_page(monkeypatch, link):
    edge, opened = _locked_edge(monkeypatch)
    url = window.origin_of(PORT) + link
    clicked = _Nav(url, True)
    edge.on_navigation_start(None, clicked)
    assert clicked.Cancel and opened == [url]                                   # her click: her real browser
    by_script = _Nav(url, False)
    edge.on_navigation_start(None, by_script)
    assert by_script.Cancel and opened == [url]                                 # no click: nothing opens


def test_a_javascript_or_file_link_goes_nowhere(monkeypatch):
    edge, opened = _locked_edge(monkeypatch)
    for url in ("javascript:alert(1)", "file:///C:/x", "ms-settings:", "http://u:p@example.test/"):
        nav = _Nav(url, True)
        edge.on_navigation_start(None, nav)
        assert nav.Cancel, url
    assert opened == []


def test_a_message_from_a_frame_never_reaches_the_shell(monkeypatch):
    edge, _opened = _locked_edge(monkeypatch)

    class Message:
        reached = False

        def __init__(self, source):
            self.source = source

        def get_Source(self):
            return self.source

        def get_WebMessageAsJson(self):
            return '["open_browser", "[]", "1"]'
    frame = Message(f"http://freshtable.localhost:{PORT + 1}/")
    edge.on_script_notify(None, frame)
    assert not frame.reached
    top = Message(window.origin_of(PORT) + "/window/")
    edge.on_script_notify(None, top)
    assert top.reached


# -- the dashboard's own pages in the pane: the same files, so nothing here may fall behind them -------------------------
PAGE_FILES =("home", "apps", "developer", "people", "settings", "vault", "knowledge", "agents", "outside")


def _window_puff() -> set[str]:
    js = (WEB / "window.js").read_text(encoding="utf-8")
    body = js[js.index("window.Puff = {"):]
    body = body[:body.index("};")]
    return set(re.findall(r"(?:^|[{,\s])([A-Za-z]+)(?=\s*[,:}]|\s*$)", body))


def test_the_window_gives_every_helper_the_pages_it_loads_call():
    uses = set()
    files = [WEB / "pages" / f"{name}.js" for name in PAGE_FILES]
    files += [WEB / name for name in ("knowledge_place.js", "github.js", "addapi.js", "addresses.js", "searchkeys.js")]
    for f in files:
        uses |= set(re.findall(r"\bP\.([A-Za-z]+)", f.read_text(encoding="utf-8")))
    made_by_pages = {"addModel", "toolsmithTile", "planCard", "storeRow", "knowledgePlaceTile", "sendTargets", "pickPath", "sendFileButton", "sendPathButton", "uploadInput", "editsSetting"}
    assert uses - made_by_pages - _window_puff() == set()


def test_the_window_loads_each_page_file_after_its_helpers():
    html = (WEB / "window.html").read_text(encoding="utf-8")
    at = html.index('src="window.js"')
    for name in PAGE_FILES:
        assert html.index(f'src="pages/{name}.js"') > at, name
    assert html.index('src="knowledge_place.js"') > at
    assert "/ui/" not in html                                                   # D108: never the active Desktop's files


def test_the_pages_it_loads_name_no_file_of_the_active_desktop():
    for name in PAGE_FILES:
        assert '"/ui/' not in (WEB / "pages" / f"{name}.js").read_text(encoding="utf-8"), name


def test_a_page_file_is_served_at_the_windows_own_address(state):
    from puffpai_core.api import create_app
    client = create_app(state, port=PORT).test_client()
    got = client.get("/window/pages/knowledge.js", headers={"Host": f"puffpai.localhost:{PORT}"})
    assert got.status_code == 200 and got.mimetype == "text/javascript"
    assert window.may_request(window.origin_of(PORT) + "/window/pages/knowledge.js", PORT)
    assert not window.is_page(window.origin_of(PORT) + "/window/pages/knowledge.js", PORT)   # a script, never the top page


def test_the_rail_reaches_every_page_with_no_command():
    js = (WEB / "window.js").read_text(encoding="utf-8")
    for view in ("home", "knowledge", "people", "agents", "outside", "developer"):
        assert f'pageItem("{view}",' in js, view
    assert 'openPage("apps")' in js and 'openPage("vault")' in js and 'openPage("settings", "brain")' in js
