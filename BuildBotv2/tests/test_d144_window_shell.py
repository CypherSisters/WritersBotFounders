"""D144 (lane window-apps): what surrounds the window on Linux and Mac: its plain lines, the installer's --window-for and
the Terminal button. The browser and its sign-in are in ``tests/test_d144_window_app.py``. A real browser was run only in
a Linux container on a virtual screen, not here."""

from __future__ import annotations

import os
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core import cli, uninstall, window, window_app
from puffpai_core.api import network
from puffpai_core.wizard import terminal

PORT = 51234
posix = pytest.mark.skipif(os.name == "nt", reason="the branches for Linux and Mac")
bash = shutil.which("bash")


# -- the lines -------------------------------------------------------------------------------------------------------
@pytest.fixture
def home_network(monkeypatch):
    monkeypatch.setattr(network, "saved_on", lambda state: True)
    monkeypatch.setattr(network, "own_addresses", lambda: {"192.168.1.20"})
    monkeypatch.setattr(network, "own_names", lambda: set())


def test_no_screen_gives_the_home_network_address_and_where_its_code_comes_from(home_network, monkeypatch, tmp_path):
    signin = tmp_path / "signin-buildbot.sh"
    signin.write_text("")
    monkeypatch.setattr(terminal, "signin_launcher", lambda: signin)
    line = window._no_screen_line(window_app.NO_SCREEN, PORT)
    assert window_app.NO_SCREEN in line and f"http://192.168.1.20:{PORT + 3}/" in line
    assert "signin-buildbot.sh" in line and "\n" not in line


def test_with_no_launcher_it_names_a_command_that_runs_here(home_network, monkeypatch, tmp_path):
    monkeypatch.setattr(terminal, "signin_launcher", lambda: tmp_path / "gone.sh")
    line = window._no_screen_line(window_app.NO_SCREEN, PORT)
    assert terminal.cli_command("pair", "--running") in line


def test_no_home_network_says_how_to_turn_it_on(monkeypatch):
    monkeypatch.setattr(network, "saved_on", lambda state: False)
    line = window._no_screen_line(window_app.NO_SCREEN, PORT)
    assert window.origin_of(PORT) in line and terminal.cli_command("network", "on") in line and "\n" not in line


@posix
def test_why_not_is_the_browser_s_own_answer(monkeypatch):
    monkeypatch.setattr(window_app, "why_not", lambda: "a reason")
    assert window.why_not() == "a reason"


@posix
def test_no_browser_on_a_screen_gives_the_dashboard(monkeypatch):
    monkeypatch.setattr(window_app, "has_screen", lambda: True)
    assert window.cant_line(window_app.NO_BROWSER, PORT) == window.CANT_LINE.format(why=window_app.NO_BROWSER, origin=f"http://puffpai.localhost:{PORT}")


@posix
def test_no_screen_uses_the_home_network_line_and_offers_nothing(home_network, monkeypatch, capsys):
    monkeypatch.setattr(window_app, "has_screen", lambda: False)
    assert f":{PORT + 3}/" in window.cant_line(window_app.NO_SCREEN, PORT)

    def asked(*_a):
        raise AssertionError("no question and no dashboard with no screen")
    monkeypatch.setattr("builtins.input", asked)
    monkeypatch.setattr(window, "WindowApi", asked)
    window.offer_dashboard("the line", PORT, "unused")
    assert capsys.readouterr().out == "the line\n"


@posix
def test_a_line_that_cant_be_worked_out_is_still_a_plain_line(monkeypatch):
    monkeypatch.setattr(window_app, "has_screen", lambda: False)

    def boom(*_a, **_k):
        raise OSError("no")
    monkeypatch.setattr(window, "_no_screen_line", boom)
    assert window.origin_of(PORT) in window.cant_line(window_app.NO_SCREEN, PORT)


@posix
def test_the_address_on_another_computer_is_given_as_it_is(monkeypatch):
    monkeypatch.setattr(window, "_remote", "192.168.1.20")
    monkeypatch.setattr(window_app, "has_screen", lambda: False)
    assert "http://192.168.1.20:8423" in window.cant_line(window_app.NO_SCREEN, 8423)


@posix
def test_the_process_that_shows_it_goes_to_the_browser(monkeypatch):
    seen = []
    monkeypatch.setattr(window_app, "show", lambda port, state, after=None: seen.append((port, state)) or "")
    assert window._show(PORT, Path("x")) == "" and seen == [(PORT, Path("x"))]


@posix
def test_a_window_process_reads_no_input_from_her_terminal(monkeypatch, tmp_path):
    monkeypatch.undo()
    got = {}
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: got.update(kw))
    window._spawn(tmp_path)
    assert got["stdin"] == subprocess.DEVNULL and got["start_new_session"]
    assert got["stdout"] == subprocess.DEVNULL                # pytest has no terminal here: no pipe stays held


# -- the Terminal button ---------------------------------------------------------------------------------------------
def test_off_windows_terminal_opens_the_command_line_in_the_side_pane(monkeypatch, state):
    from puffpai_core.api import create_app
    app = create_app(state, port=PORT)
    device = {"Authorization": f"Bearer {app.extensions['puffpai'].tokens.issue_device('laptop')}"}
    monkeypatch.setattr(cli, "open_terminal", lambda st, c: (False, cli.TERMINAL_NOT_HERE))
    assert app.test_client().post("/v1/terminal/open", headers=device).get_json() == {"ok": False, "pane": True, "line": cli.TERMINAL_NOT_HERE}
    monkeypatch.setattr(cli, "open_terminal", lambda st, c: (False, "it broke"))
    assert "pane" not in app.test_client().post("/v1/terminal/open", headers=device).get_json()


def test_the_page_uses_that_answer():
    text = (REPO / "packages" / "desktop" / "web" / "window.js").read_text(encoding="utf-8")
    assert 'if (r.data && r.data.pane) { openPage("developer", "terminal"); return; }' in text


def test_the_dashboard_button_does_not_open_another_desktop_in_this_profile(monkeypatch, state):
    from puffpai_core import desktop
    from puffpai_core.api import create_app
    app = create_app(state, port=PORT)
    device = {"Authorization": f"Bearer {app.extensions['puffpai'].tokens.issue_device('laptop')}"}
    assert app.test_client().get("/v1/settings", headers=device).get_json()["own_desktop"] is True
    monkeypatch.setattr(desktop, "own_desktop_is_active", lambda st: False)
    assert app.test_client().get("/v1/settings", headers=device).get_json()["own_desktop"] is False
    text = (REPO / "packages" / "desktop" / "web" / "window.js").read_text(encoding="utf-8")
    assert text.index("S.settings.own_desktop === false") < text.index('window.open("/" + (hash || "")')


# -- uninstall -------------------------------------------------------------------------------------------------------
def test_uninstall_takes_this_bots_snap_profile_and_no_other(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    mine = tmp_path / "snap" / "chromium" / "common" / "puffpai-window" / "buildbot"
    other = mine.with_name("buildbot-2")
    (mine / "profile").mkdir(parents=True)
    other.mkdir()
    bot = tmp_path / "bots" / "buildbot"
    (bot / "vault").mkdir(parents=True)
    assert uninstall.snap_profiles(bot) == [mine]
    assert uninstall.remove_all(bot) == []
    assert not mine.exists() and other.is_dir()


# -- the page reader, with a snap browser. A fake path only: no snap has run this ----------------------------------------
class _NoJob:
    contained = False

    def close(self):
        pass


@pytest.fixture
def reader(monkeypatch, tmp_path):
    from puffpai_core.steward import jobs
    from puffpai_core.zone2 import page_reader
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    seen = {}

    def spawn(argv, cwd, env, log):
        seen.update(argv=argv, cwd=str(cwd), there=os.path.isdir(cwd))
        return _NoJob()
    monkeypatch.setattr(jobs.Job, "spawn", spawn)
    monkeypatch.setattr(page_reader, "browser_path", lambda: seen["browser"])
    return types.SimpleNamespace(read=page_reader.read, seen=seen, home=tmp_path)


def test_a_snap_browser_gets_its_throwaway_profile_where_it_can_write(reader):
    reader.seen["browser"] = "/snap/bin/chromium"
    assert reader.read("http://127.0.0.1:9/") is None
    profile = Path(reader.seen["cwd"])
    assert profile.parent == reader.home / "snap" / "chromium" / "common" and profile.name.startswith("bb-read-")
    assert reader.seen["there"] and f"--user-data-dir={profile}" in reader.seen["argv"]
    assert not profile.exists()                               # removed afterward, as always


@pytest.mark.parametrize("sandbox", ["", "1"])
def test_nothing_else_about_a_snap_read_changes(reader, monkeypatch, sandbox):
    monkeypatch.setenv("PUFFPAI_BROWSER_NO_SANDBOX", sandbox)
    reader.seen["browser"] = "/snap/bin/chromium"
    reader.read("http://127.0.0.1:9/")
    with_snap = [a for a in reader.seen["argv"][1:] if not a.startswith("--user-data-dir=")]
    reader.seen["browser"] = "/usr/bin/chromium"
    reader.read("http://127.0.0.1:9/")
    assert with_snap == [a for a in reader.seen["argv"][1:] if not a.startswith("--user-data-dir=")]
    assert ("--no-sandbox" in with_snap) == bool(sandbox)


def test_any_other_browser_keeps_the_usual_temp_folder(reader):
    import tempfile
    reader.seen["browser"] = "/usr/bin/chromium"
    assert reader.read("http://127.0.0.1:9/") is None
    assert Path(reader.seen["cwd"]).parent == Path(tempfile.gettempdir()) and not (reader.home / "snap").exists()


def test_a_snap_folder_that_cant_be_made_falls_back_to_the_usual_one(reader):
    import tempfile
    (reader.home / "snap").write_text("not a folder")
    reader.seen["browser"] = "/snap/bin/chromium"
    assert reader.read("http://127.0.0.1:9/") is None
    assert Path(reader.seen["cwd"]).parent == Path(tempfile.gettempdir())


@posix
def test_a_link_to_the_snap_program_counts(tmp_path):
    from puffpai_core.zone2 import page_reader
    snap = tmp_path / "snap"
    snap.write_text("")
    link = tmp_path / "chromium"
    link.symlink_to(snap)
    assert page_reader.is_snap(str(link)) and not page_reader.is_snap(str(snap.with_name("chrome")))


# -- install.sh ------------------------------------------------------------------------------------------------------
def test_install_sh_has_the_equal_of_the_windows_flag():
    text = (REPO / "install.sh").read_text(encoding="ascii")
    assert "--window-for" in text and 'app --remote "$WINDOW_FOR"' in text
    assert "open-buildbot-window.sh" in text and "puffpai pair --running" in text


def _copy_installer(tmp_path):
    repo = tmp_path / "a repo"
    repo.mkdir()
    shutil.copy(REPO / "install.sh", repo)
    (repo / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "install_tools.sh", repo / "scripts")
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "PUFFPAI_INSTALL_PRETEND_MISSING": "node,ollama",
           "PUFFPAI_TOOLS_PLATFORM": "linux-x64"}
    return types.SimpleNamespace(repo=repo, home=home, env=env)


@posix
@pytest.mark.skipif(not bash, reason="needs bash")
def test_a_dry_run_for_the_window_makes_no_bot_and_asks_for_no_ollama(tmp_path):
    box = _copy_installer(tmp_path)
    got = subprocess.run([bash, str(box.repo / "install.sh"), "--dry-run", "--window-for", "http://192.168.1.20:8423"], env=box.env,
                         capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)
    assert got.returncode == 0, got.stdout + got.stderr
    assert f"would write {box.repo}/open-buildbot-window.sh" in got.stdout and "http://192.168.1.20:8423" in got.stdout
    assert "Step 3 of 5" not in got.stdout and "Ollama" not in got.stdout.replace("Python, Node.js and Ollama", "")
    assert sorted(p.name for p in box.repo.iterdir()) == ["install.sh", "scripts"] and not list(box.home.iterdir())


@posix
@pytest.mark.skipif(not bash, reason="needs bash")
@pytest.mark.parametrize("address", ["http://x; touch /tmp/no", "$(touch /tmp/no)", "a b"])
def test_an_address_that_cant_be_one_stops_setup(tmp_path, address):
    box = _copy_installer(tmp_path)
    got = subprocess.run([bash, str(box.repo / "install.sh"), "--dry-run", "--window-for", address], env=box.env,
                         capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)
    assert got.returncode == 1 and "not an address this window can use" in got.stdout
