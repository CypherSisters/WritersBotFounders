"""D139 (lane window-remote): the window on another computer. The lock holds for that one host, and nothing here is on a
screen: no test starts a window."""

from __future__ import annotations

import pytest

from conftest import REPO
from puffpai_core import cli, window

PORT = 8423
HOST = "192.168.1.20"
ORIGIN = f"http://{HOST}:{PORT}"
WEB = REPO / "packages" / "desktop" / "web"


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(window, "_remote", HOST)
    return HOST


@pytest.mark.parametrize("text,want", [("http://192.168.1.20:8423", (HOST, PORT)), ("192.168.1.20:8423/", (HOST, PORT)), ("http://MyBot.local:8433/", ("mybot.local", 8433))])
def test_it_takes_the_address_pair_prints(text, want):
    assert window.parse_remote(text) == want


@pytest.mark.parametrize("text", ["", "192.168.1.20", "https://192.168.1.20:8423", "http://192.168.1.20:8423/x", "http://192.168.1.20:8423/#pair=ABC", "http://a@192.168.1.20:8423",
                                  "http://192.168.1.20:8423?x=1", "ftp://x:1", "http://a b:1", "http://[::1]:8423", "file:///c:/x", "http://x&calc:8423", 'http://x":1'])
def test_anything_else_is_refused_in_words(text):
    with pytest.raises(ValueError):
        window.parse_remote(text)


@pytest.mark.parametrize("text", ["http://localhost:8423", "http://puffpai.localhost:8420", "http://127.0.0.1:8423"])
def test_this_computer_is_told_to_use_the_plain_command(text):
    with pytest.raises(ValueError, match="this computer"):
        window.parse_remote(text)


def test_this_computers_window_is_as_it_was():
    assert window.origin_of(8420) == "http://puffpai.localhost:8420"
    assert window.is_page("http://puffpai.localhost:8420/window/", 8420)
    assert not window.may_load("http://puffpai.localhost:8420/pair", 8420)
    assert not window.may_request("http://puffpai.localhost:8420/pair", 8420)
    assert not window.is_page(f"{ORIGIN}/window/", PORT)


def test_the_top_page_may_only_be_the_window_or_the_pair_screen_on_that_host(remote):
    assert window.is_page(f"{ORIGIN}/window/", PORT) and window.is_page(f"{ORIGIN}/window/window.html", PORT)
    assert window.may_load(f"{ORIGIN}/pair", PORT) and window.may_load(f"{ORIGIN}/pair?back=window", PORT)
    assert window.may_load(f"{ORIGIN}/v1/signin", PORT)
    for no in (f"{ORIGIN}/", f"{ORIGIN}/ui/x.js", f"{ORIGIN}/recover", f"http://other:{PORT}/window/", f"http://{HOST}:9999/window/", f"https://{HOST}:{PORT}/window/",
               f"http://puffpai.localhost:{PORT}/window/", f"http://a@{HOST}:{PORT}/window/"):
        assert not window.may_load(no, PORT), no


def test_the_bridge_is_only_for_the_window_page_on_that_host(remote):
    assert not window.is_page(f"{ORIGIN}/pair", PORT)
    assert not window.is_page(f"http://evil.example:{PORT}/window/", PORT)


def test_what_it_may_ask_of_the_bot_is_the_same_allowlist(remote):
    for yes in (f"{ORIGIN}/window/window.js", f"{ORIGIN}/v1/settings", f"{ORIGIN}/pair"):
        assert window.may_request(yes, PORT), yes
    for no in (f"{ORIGIN}/", f"{ORIGIN}/ui/x.js", f"{ORIGIN}/window/%2e%2e/ui/x.js", f"{ORIGIN}/v1%2fsettings", f"{ORIGIN}/recover", f"{ORIGIN}/pair/x"):
        assert not window.may_request(no, PORT), no
    assert window.may_request(f"http://{HOST}:18999/", PORT)                    # a package's own page port goes on


def test_the_dashboard_opens_that_address_and_makes_no_code(remote, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(window, "open_tab", lambda url: opened.append(url) or True)
    monkeypatch.setattr(window.pairing, "new_code", lambda s: pytest.fail("a code was made on the wrong computer"))
    assert window.WindowApi(PORT, tmp_path).open_dashboard() == {"ok": True, "line": ""}
    assert opened == [f"{ORIGIN}/"]


def test_sign_in_makes_no_code_here_and_opens_the_window_page(remote, monkeypatch, tmp_path):
    monkeypatch.setattr(window.pairing, "new_code", lambda s: pytest.fail("a code was made on the wrong computer"))
    monkeypatch.setattr(window.pairing, "redeem", lambda s, c: pytest.fail("no code to burn"))

    class Fake:
        loaded = []

        class events:
            class loaded:
                @staticmethod
                def wait(s):
                    return True

        def run_js(self, x):
            return pytest.fail("no probe on another device")

        def load_url(self, url):
            Fake.loaded.append(url)

        def show(self):
            Fake.loaded.append("shown")
    window._sign_in_and_show(Fake(), PORT, tmp_path)
    assert Fake.loaded == [f"{ORIGIN}/window/", "shown"]


def test_the_command_says_when_nothing_answers_and_opens_nothing(state, monkeypatch, capsys):
    monkeypatch.setattr(window, "answers", lambda port: False)
    monkeypatch.setattr(window, "open_window", lambda *a, **k: pytest.fail("opened"))
    monkeypatch.setattr(window, "_remote", None)
    monkeypatch.setenv("PUFFPAI_BOT", "x")
    assert cli.main(["app", "--remote", ORIGIN]) == 1
    assert "Nothing answers at" in capsys.readouterr().out


def test_the_command_refuses_a_bad_address(capsys, monkeypatch):
    monkeypatch.setattr(window, "_remote", None)
    assert cli.main(["app", "--remote", "nonsense"]) == 2
    assert "not an address" in capsys.readouterr().out


def test_the_command_opens_its_own_folder_and_no_bot_folder(tmp_path, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(window, "answers", lambda port: True)
    monkeypatch.setattr(window, "_remote", None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE", str(tmp_path / "some-bot"))
    monkeypatch.setenv("PUFFPAI_BOT", "x")

    def open_window(port, st, remote=""):
        seen.update(port=port, state=st, remote=remote)
        return True, window.OPENING_LINE
    monkeypatch.setattr(window, "open_window", open_window)
    assert cli.main(["app", "--remote", ORIGIN]) == 0
    assert seen["port"] == PORT and seen["remote"] == f"{HOST}:{PORT}"
    assert seen["state"].name == f"window-{HOST}-{PORT}" and not (seen["state"] / "settings").exists()


def test_the_window_process_is_told_its_address_never_a_code(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(window, "why_not", lambda: "")
    monkeypatch.setattr(window.corelock, "holder", lambda s: None)
    monkeypatch.setattr(window, "_spawn", lambda st, remote="": started.append((st, remote)))
    assert window.open_window(PORT, tmp_path, remote=f"{HOST}:{PORT}") == (True, window.OPENING_LINE)
    assert started == [(tmp_path, f"{HOST}:{PORT}")]


def test_a_page_goes_back_to_the_window_after_pairing_and_terminal_is_the_pane():
    js = (WEB / "window.js").read_text(encoding="utf-8")
    assert 'S.signin.pair + "?back=window"' in js
    assert 'if (L.onNetworkHost(location.hostname)) openPage("developer", "terminal"); else openTerminal(terminal);' in js   # the pane, never a console over there
    ui = (REPO / "core" / "puffpai_core" / "api" / "groups" / "ui.py").read_text(encoding="utf-8")
    assert '.get("back") === "window" ? "/window/" : "/"' in ui                 # a fixed choice: no address comes from outside


def test_install_writes_one_launcher_and_only_a_checked_address():
    text = (REPO / "install.ps1").read_text(encoding="utf-8")
    assert "[string]$WindowFor" in text and "'^[A-Za-z0-9.:/-]+$'" in text and "app --remote $WindowFor" in text
    assert text.index("if ($WindowFor)") < text.index("# --- 3. Your bot's own folder")


def test_an_unpaired_window_page_goes_to_the_pair_screen_and_says_to_come_back():
    app = (REPO / "core" / "puffpai_core" / "api" / "app.py").read_text(encoding="utf-8")
    assert 'redirect("/pair?back=window" if request.endpoint == "ui.window_root" else "/pair")' in app
