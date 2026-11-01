"""D94 (lane cli-default), and the owner's change of 10-30: the PuffPAI window is the default way in, the chat and the
dashboard are choices.

``puffpai start`` (Start BuildBot.cmd) starts BuildBot once and then opens the window, or the chat or the dashboard
when she picked that (``settings\\opens.json``, also ``PUT /v1/settings/opens`` and ``puffpai start --opens``). A
window that can't open falls back to the chat. ``/dashboard`` and "open the dashboard" in the chat open a one-time pair
link. No test here binds 8420-8422 or starts a real window: the launch, the running check, the window and the browser
are stubs, except the stale ``terminal.json`` case, which asks a closed port for real."""

from __future__ import annotations

import io
import json
import socket
import time
import types

import pytest

from conftest import REPO
from puffpai_core import cli, opens, termchat, window
from puffpai_core.wizard import terminal as term_mod

RUNNING = {"port": 51234, "pid": 1, "token": "ppd_test"}
PAIR = "http://puffpai.localhost:51234/#pair="


def _closed_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in (8420, 8421, 8422):
            return port                    # closed again: nothing answers here


@pytest.fixture
def start_kit(monkeypatch):
    """The process launch, the window, the browser and the chat, written down instead of done."""
    kit = types.SimpleNamespace(launched=[], opened=[], chats=0, windows=[], window_opens=True)

    def open_window(port, state=None):
        kit.windows.append(port)
        return (True, window.OPENING_LINE) if kit.window_opens else (False, window.cant_line("a test", port))
    monkeypatch.setattr(window, "open_window", open_window)
    monkeypatch.setattr(cli, "_launch_serve", lambda argv, title: kit.launched.append((argv, title)))
    monkeypatch.setattr(cli, "_open_tab", kit.opened.append)

    def chat():
        kit.chats += 1
        return 0
    monkeypatch.setattr(termchat, "main", chat)
    return kit


# -- puffpai start ------------------------------------------------------------------------------------------
def test_start_with_a_core_already_running_starts_no_second_core(monkeypatch, capsys, start_kit, state):
    opens.save(state, "chat")
    monkeypatch.setattr(cli, "_terminal", lambda: dict(RUNNING))
    assert cli.main(["start"]) == 0
    out = capsys.readouterr().out
    assert start_kit.launched == [] and start_kit.chats == 1 and start_kit.opened == [] and start_kit.windows == []
    assert "BuildBot is already running." in out
    assert "Closing this chat leaves it running; Stop BuildBot.cmd stops it." in out
    assert "Your dashboard: http://puffpai.localhost:51234. Type /dashboard to open it." in out


def test_start_by_default_opens_the_window_and_no_chat(monkeypatch, capsys, start_kit):
    monkeypatch.setattr(cli, "_terminal", lambda: dict(RUNNING))
    assert cli.main(["start"]) == 0
    out = capsys.readouterr().out
    assert start_kit.windows == [51234] and start_kit.chats == 0 and start_kit.launched == []
    assert window.OPENING_LINE in out and "BuildBot runs in its own window." in out and "Traceback" not in out


def test_start_falls_back_to_the_chat_with_one_plain_line_when_the_window_cant_open(monkeypatch, capsys, start_kit):
    start_kit.window_opens = False
    monkeypatch.setattr(cli, "_terminal", lambda: dict(RUNNING))
    assert cli.main(["start"]) == 0
    out = capsys.readouterr().out
    assert start_kit.chats == 1 and "The window can't open on this computer: a test." in out
    assert "Your dashboard: http://puffpai.localhost:51234" in out and "Traceback" not in out


def test_start_when_not_running_launches_serve_once_and_waits(monkeypatch, capsys, start_kit, state):
    opens.save(state, "chat")
    asked = []

    def running():
        asked.append(1)
        return dict(RUNNING) if start_kit.launched and len(asked) >= 4 else None
    monkeypatch.setattr(cli, "_terminal", running)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "bot.json").write_text(json.dumps({"name": "Sunny"}), encoding="utf-8")
    assert cli.main(["start"]) == 0
    out = capsys.readouterr().out
    assert len(start_kit.launched) == 1 and start_kit.chats == 1
    argv, title = start_kit.launched[0]
    assert "serve" in argv and "--port" not in argv            # the default port: serve's own default
    assert title == "BuildBot (Sunny) is running. Close this window to stop it."
    assert out.count("Starting BuildBot...") == 1 and len(asked) >= 4


def test_start_passes_a_port_that_is_not_the_default(monkeypatch, start_kit):
    monkeypatch.setattr(cli, "_terminal", lambda: dict(RUNNING) if start_kit.launched else None)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli.main(["start", "--port", "51999"]) == 0
    argv, _title = start_kit.launched[0]
    assert argv[argv.index("serve"):] == ["serve", "--port", "51999"]


def test_a_stale_terminal_json_does_not_count_as_running(state, capsys, start_kit):
    key = state / "settings" / "terminal.json"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps({"port": _closed_port(), "pid": 999999, "token": "ppd_gone"}), encoding="utf-8")
    assert cli._terminal() is None                                # the real check: nothing answers there
    t0 = time.monotonic()
    assert cli.main(["start", "--wait", "0.2"]) == 1
    out = capsys.readouterr().out
    assert len(start_kit.launched) == 1 and start_kit.chats == 0    # it started one, and no chat ran
    assert "BuildBot did not start within 0 seconds." in out
    assert 'Look at the window named "BuildBot (Puff) is running. Close this window to stop it."' in out
    assert time.monotonic() - t0 < 30


def test_start_opens_the_dashboard_when_she_picked_it_and_no_chat(monkeypatch, capsys, start_kit, state):
    opens.save(state, "dashboard")
    monkeypatch.setattr(cli, "_terminal", lambda: dict(RUNNING))
    assert cli.main(["start"]) == 0
    out = capsys.readouterr().out
    assert start_kit.chats == 0 and start_kit.launched == []
    assert len(start_kit.opened) == 1 and start_kit.opened[0].startswith(PAIR)
    assert start_kit.opened[0] in out and "works once, for 10 minutes" in out
    assert "BuildBot runs in its own window." in out
    from puffpai_core import pairing
    assert pairing.redeem(state, start_kit.opened[0].split("#pair=")[1])      # a real one-time code


def test_opens_saves_the_setting_and_refuses_bad_values(capsys, state, start_kit, monkeypatch):
    monkeypatch.setattr(cli, "_terminal", lambda: pytest.fail("--opens only saves"))
    assert cli.main(["start", "--opens", "dashboard"]) == 0
    assert opens.read(state) == "dashboard"
    assert json.loads((state / "settings" / "opens.json").read_text(encoding="utf-8")) == {"opens": "dashboard"}
    assert cli.main(["start", "--opens", "web"]) == 1
    assert opens.read(state) == "dashboard"                     # nothing saved
    assert 'is "window", "chat" or "dashboard"' in capsys.readouterr().out
    assert cli.main(["start", "--opens", "chat"]) == 0 and opens.read(state) == "chat"
    assert cli.main(["start", "--opens", "window"]) == 0 and opens.read(state) == "window"
    assert start_kit.launched == [] and start_kit.chats == 0 and start_kit.windows == []


def test_start_with_an_id_still_starts_a_package(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    assert cli.main(["start", "freshtable"]) == 1
    assert "The Core is not running." in capsys.readouterr().out


@pytest.mark.parametrize("text", [None, "not json", '["dashboard"]', '{"opens": "web"}', '{"opens": null}'])
def test_the_setting_reads_window_when_missing_or_bad(state, text):
    if text is not None:
        (state / "settings").mkdir(parents=True, exist_ok=True)
        (state / "settings" / "opens.json").write_text(text, encoding="utf-8")
    assert opens.read(state) == "window"


# -- the settings route ---------------------------------------------------------------------------------------
def test_the_settings_route_reads_and_writes_it_for_the_owner_only(state):
    from puffpai_core.api import create_app
    app = create_app(state)
    core = app.extensions["puffpai"]
    client = app.test_client()
    owner = {"Authorization": f"Bearer {core.tokens.issue_owner_key()}"}
    device = {"Authorization": f"Bearer {core.tokens.issue_device('laptop')}"}
    assert client.get("/v1/settings", headers=owner).json["opens"] == "window"
    assert client.put("/v1/settings/opens", json={"opens": "dashboard"}).status_code == 401
    pkg = {"Authorization": f"Bearer {core.tokens.issue_package_token('freshtable')}"}
    assert client.put("/v1/settings/opens", json={"opens": "dashboard"}, headers=pkg).status_code in (401, 403)
    assert opens.read(state) == "window"
    r = client.put("/v1/settings/opens", json={"opens": "dashboard"}, headers=device)
    assert r.status_code == 200 and r.json["opens"] == "dashboard" and opens.read(state) == "dashboard"
    for bad in ({"opens": "web"}, {"opens": None}, {}, {"opens": "chat", "more": 1}, ["chat"]):
        r = client.put("/v1/settings/opens", json=bad, headers=owner)
        assert r.status_code == 400 and r.json["error"], bad
    assert opens.read(state) == "dashboard"
    r = client.put("/v1/settings/opens", json={"opens": "chat"}, headers=owner)
    assert r.status_code == 200 and client.get("/v1/settings", headers=owner).json["opens"] == "chat"


# -- /dashboard in the chat -----------------------------------------------------------------------------------
class FakeApi:
    """The Core API as the chat sees it: every call 404, every chat turn written down and answered."""

    def __init__(self):
        self.info = dict(RUNNING)
        self.turns = []

    def call(self, method, path, body=None, timeout=termchat.CALL_TIMEOUT_S):
        return 404, {}

    def stream(self, path, body):
        self.turns.append(body["text"])
        return 200, None, iter([{"type": "done", "text": "ok"}])


def test_dashboard_command_and_plain_words_open_a_pair_link_and_other_words_go_to_the_brain(state):
    lines = ["/dashboard", "open the dashboard", "  Show me my Dashboard! ", "open my dashboard.",
             "open the dashboard please", "what is a dashboard", "show dashboards"]
    keys = iter(lines)

    def read_line(prompt=""):
        try:
            return next(keys)
        except StopIteration:
            raise EOFError from None
    out = io.StringIO()
    opened = []
    look = termchat.Look(out, env={}, tty=lambda s: False, vt_on=lambda s: False)
    api = FakeApi()
    chat = termchat.Chat(api, read_line=read_line, open_url=opened.append, sleep=lambda s: None, look=look)
    assert chat.run() == 0
    assert len(opened) == 4 and all(u.startswith(PAIR) for u in opened)
    from puffpai_core import pairing
    assert all(pairing.redeem(state, u.split("#pair=")[1]) for u in opened)   # each a real one-time code
    assert api.turns == ["open the dashboard please", "what is a dashboard", "show dashboards"]
    text = out.getvalue()
    assert text.count("(works once, for 10 minutes)") == 4 and opened[0] in text
    help_out = io.StringIO()
    termchat.Chat(api, look=termchat.Look(help_out, env={}, tty=lambda s: False, vt_on=lambda s: False)).cmd_help()
    assert "/dashboard" in help_out.getvalue() and "Open your dashboard in your browser" in help_out.getvalue()


# -- the end of setup -------------------------------------------------------------------------------------------
def _keys(*keys):
    left = list(keys)

    def read_line(prompt=""):
        if not left:
            raise EOFError
        return left.pop(0)
    return read_line


@pytest.fixture
def setup_window(monkeypatch):
    """The window and its shortcuts at the end of setup, written down instead of done."""
    kit = types.SimpleNamespace(calls=[], why="")
    monkeypatch.setattr(window, "why_not", lambda: kit.why)

    def make(state, name=None, port=8420, folders=None):
        kit.calls.append(("shortcuts", name, port))
        return True, r"Shortcuts made: C:\scratch\PuffPAI Sunny.lnk. puffpai app --no-shortcuts removes them."

    def open_window(port, state=None):
        kit.calls.append(("window", port))
        return (False, window.cant_line(kit.why, port)) if kit.why else (True, window.OPENING_LINE)
    monkeypatch.setattr(window, "make_shortcuts", make)
    monkeypatch.setattr(window, "open_window", open_window)
    return kit


def test_setup_end_enter_opens_the_window_with_its_shortcuts_then_the_terminal_stays(state, setup_window):
    events, out = [], []
    term_mod.setup_done(51234, state, "Sunny", read_line=_keys(""), write=out.append,
                        open_url=lambda u: events.append("browser"), chat=lambda: events.append("chat"))
    assert setup_window.calls == [("shortcuts", "Sunny", 51234), ("window", 51234)]
    assert events == ["chat"]                                     # no browser, and this terminal is still hers
    text = "\n".join(out)
    assert "Setup is done. Sunny is running." in text
    assert "Your dashboard is ready too:  http://puffpai.localhost:51234   (in your browser)" in text
    assert "PuffPAI Sunny.lnk" in text and "--no-shortcuts removes them" in text and window.OPENING_LINE in text


def test_setup_end_says_all_three_choices_with_enter_for_the_window(state, setup_window):
    prompts = []

    def read_line(prompt=""):
        prompts.append(prompt)
        return "t"
    term_mod.setup_done(51234, state, "Sunny", read_line=read_line, write=lambda s: None, chat=lambda: None)
    assert prompts == ["  [Enter] open the window   [d] open the dashboard   [t] chat here  > "]


def test_setup_end_d_opens_the_dashboard_then_the_chat(state, setup_window):
    events, out = [], []
    term_mod.setup_done(51234, state, "Sunny", read_line=_keys("d"), write=out.append,
                        open_url=lambda u: events.append(("open", u)), chat=lambda: events.append(("chat", None)))
    assert [e[0] for e in events] == ["open", "chat"] and events[0][1].startswith(PAIR)
    text = "\n".join(out)
    assert "Setup is done. Sunny is running." in text
    assert "Your dashboard is ready too:  http://puffpai.localhost:51234   (in your browser)" in text
    assert events[0][1] in text and "(works once, for 10 minutes)" in text
    assert setup_window.calls == []


def test_setup_end_t_goes_straight_to_the_chat_and_eof_skips_it(state, setup_window):
    events = []
    term_mod.setup_done(51234, state, "Sunny", read_line=_keys("t"), write=lambda s: None,
                        open_url=lambda u: events.append("open"), chat=lambda: events.append("chat"))
    assert events == ["chat"] and setup_window.calls == []
    events.clear()
    term_mod.setup_done(51234, state, "Sunny", read_line=_keys(), write=lambda s: None,
                        open_url=lambda u: events.append("open"), chat=lambda: events.append("chat"))
    assert events == [] and setup_window.calls == []              # no answer: nothing is made or opened


def test_setup_end_another_word_makes_nothing_and_goes_to_the_chat(state, setup_window):
    events = []
    term_mod.setup_done(51234, state, "Sunny", read_line=_keys("hello"), write=lambda s: None,
                        open_url=lambda u: events.append("open"), chat=lambda: events.append("chat"))
    assert events == ["chat"] and setup_window.calls == []


def test_setup_end_enter_with_no_way_to_open_it_makes_no_shortcuts_and_the_chat_is_there(state, setup_window):
    setup_window.why = "the Microsoft Edge WebView2 Runtime is not installed"
    events, out = [], []
    term_mod.setup_done(51234, state, "Sunny", read_line=_keys(""), write=out.append, chat=lambda: events.append("chat"))
    assert setup_window.calls == [("window", 51234)] and events == ["chat"]
    assert any("The window can't open on this computer: the Microsoft Edge WebView2 Runtime is not installed. "
               "Your dashboard works: http://puffpai.localhost:51234" in line for line in out)


def _free_port() -> int:
    return _closed_port()


@pytest.mark.parametrize("finished, exit_flag", [(True, False), (False, False), (True, True)],
                         ids=["done", "not-done", "exit"])
def test_setup_runs_the_chat_on_its_own_core_then_says_it_runs_here(monkeypatch, capsys, state, finished, exit_flag):
    """The real ``puffpai setup`` main on free ports: after a finished setup the chat runs on the Core setup
    started (its terminal key answers), and ``running_lines`` come after the chat ends."""
    from puffpai_core.zone2 import settings as model_settings
    model_settings.save(state, address="http://127.0.0.1:9")       # nothing listens: no real model server
    events = []
    monkeypatch.setattr(term_mod, "builtins", lambda core, ask, write: [])
    monkeypatch.setattr(term_mod.Terminal, "run", lambda self: 0 if finished else 1)
    from puffpai_core.api import app as app_mod
    real_accept, real_lines, stops = app_mod.accept_shutdown, term_mod.running_lines, []
    monkeypatch.setattr(app_mod, "accept_shutdown", lambda app: stops.append(real_accept(app)) or stops[-1])

    def lines_then_stop(port, code):                               # D122: the stop is accepted from the start
        stops[-1].set()
        return real_lines(port, code)
    monkeypatch.setattr(term_mod, "running_lines", lines_then_stop)
    real_done = term_mod.setup_done

    def chat():
        reached = termchat.connect(out=io.StringIO())
        events.append(("chat", reached is not None and reached.api.call("GET", "/v1/settings")[0] == 200))
        print("THE CHAT RAN HERE", flush=True)
    monkeypatch.setattr(term_mod, "setup_done", lambda port, st, name, **_kw: real_done(
        port, st, name, read_line=_keys("t"), chat=chat))
    argv = ["--port", str(_free_port()), "--pages-port", str(_free_port()), "--proxy-port", "0"]
    code = term_mod.main(argv + (["--exit"] if exit_flag else []))
    out = capsys.readouterr().out
    assert code == (0 if finished else 1)
    if finished and not exit_flag:
        assert events == [("chat", True)]
        assert out.index("THE CHAT RAN HERE") < out.index("BuildBot is running now, in this window.")
    else:
        assert events == [] and "Setup is done." not in out
    if not exit_flag:
        assert "BuildBot is running now, in this window." in out


# -- Start BuildBot.cmd -----------------------------------------------------------------------------------------
def test_the_start_launcher_runs_start_not_serve():
    text = (REPO / "install.ps1").read_text(encoding="ascii")
    block = text[text.index("$Launcher = "):text.index("Set-Content -LiteralPath $Launcher")]
    assert '""%~dp0.venv\\Scripts\\puffpai.exe"" start <con >con' in block
    assert " serve" not in block and " pair" not in block
    assert '"call ""%~f0"" --run <nul >nul"' in block                   # no "Terminate batch job" question
    assert '"if not ""%BUILDBOT_EXIT%""==""0"" pause"' in block          # a failure stays on screen to read
    assert '"set ""PUFFPAI_BOT=$Name"""' in block and '"set ""PUFFPAI_STATE="""' in block
    assert "Stop BuildBot.cmd" in block and "Chat with BuildBot.cmd" in block
