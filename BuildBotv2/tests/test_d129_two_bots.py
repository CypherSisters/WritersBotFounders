"""D129 (lane two-bots): each bot picks a free set of ports once, saves them in its own folder, and every command reads them.

No test here binds a port: what is free is a stub. The real two bots at once are checked by hand, on scratch bots."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core import botports, cli, corelock
from puffpai_core.wizard import terminal as term_mod

SECOND = {"port": 8430, "pages_port": 8432, "proxy_port": 8431}
THIRD = {"port": 8440, "pages_port": 8442, "proxy_port": 8441}


@pytest.fixture
def busy(monkeypatch):
    """The ports other programs hold now."""
    taken: set[int] = set()
    monkeypatch.setattr(botports, "_free", lambda p: p not in taken)
    return taken


def _bot(parent, name, ports=None):
    folder = parent / name
    (folder / "settings").mkdir(parents=True)
    if ports is not None:
        (folder / "settings" / "ports.json").write_text(json.dumps(ports), encoding="utf-8")
    return folder


# -- picking --------------------------------------------------------------------------------------------------------------
def test_a_first_bot_keeps_todays_ports_and_saves_them(tmp_path, busy):
    bot = _bot(tmp_path, "one")
    assert botports.ensure(bot) == (botports.DEFAULT, True)
    assert json.loads((bot / "settings" / "ports.json").read_text(encoding="utf-8")) == botports.DEFAULT


def test_todays_ports_in_use_by_another_program_mean_the_next_free_set(tmp_path, busy):
    busy.add(8422)
    assert botports.ensure(_bot(tmp_path, "one")) == (SECOND, True)
    busy.add(8431)
    assert botports.ensure(_bot(tmp_path, "two")) == (THIRD, True)


def test_a_stopped_bots_saved_ports_are_not_taken_though_they_look_free(tmp_path, busy):
    _bot(tmp_path, "sister", SECOND)
    _bot(tmp_path, "aunt", botports.DEFAULT)
    assert botports.ensure(_bot(tmp_path, "mom"))[0] == THIRD


def test_a_bot_made_before_this_file_counts_as_holding_todays_ports(tmp_path, busy):
    _bot(tmp_path, "older")
    assert botports.ensure(_bot(tmp_path, "mom"))[0] == SECOND


def test_what_is_not_a_bot_folder_claims_nothing(tmp_path, busy):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert botports.ensure(_bot(tmp_path, "mom"))[0] == botports.DEFAULT


def test_saved_ports_stay_though_they_are_busy_now(tmp_path, busy):
    bot = _bot(tmp_path, "one", THIRD)
    busy.update(THIRD.values())
    assert botports.ensure(bot) == (THIRD, False)


@pytest.mark.parametrize("text", ["not json", "[]", '{"port": 8430}', '{"port": "8430", "pages_port": 8432, "proxy_port": 8431}',
                                  '{"port": 8430, "pages_port": 8430, "proxy_port": 8431}', '{"port": 80, "pages_port": 8432, "proxy_port": 8431}',
                                  '{"port": true, "pages_port": 8432, "proxy_port": 8431}', '{"port": 70000, "pages_port": 8432, "proxy_port": 8431}'])
def test_a_bad_file_reads_as_none_and_is_picked_again(tmp_path, busy, text):
    bot = _bot(tmp_path, "one")
    (bot / "settings" / "ports.json").write_text(text, encoding="utf-8")
    assert botports.saved(bot) is None and botports.read(bot) == botports.DEFAULT
    assert botports.ensure(bot) == (botports.DEFAULT, True)


def test_read_writes_nothing_and_asks_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(botports, "_free", lambda p: pytest.fail("read asked the system"))
    bot = tmp_path / "one"
    assert botports.read(bot) == botports.DEFAULT
    assert not bot.exists()


def test_with_no_free_set_it_gives_todays_and_the_core_says_which_is_in_use(tmp_path, monkeypatch):
    monkeypatch.setattr(botports, "_free", lambda p: False)
    bot = _bot(tmp_path, "one")
    assert botports.ensure(bot) == (botports.DEFAULT, False)
    assert botports.saved(bot) is None


def test_the_sets_stay_below_the_packages_range():
    assert all(max(s.values()) < 18420 for s in botports._candidates())


# -- the commands read them ---------------------------------------------------------------------------------------------
def _args(port=None, pages=None, proxy=None):
    return types.SimpleNamespace(port=port, pages_port=pages, proxy_port=proxy)


def test_serve_fills_what_no_flag_named_and_says_once(state, busy, capsys):
    busy.add(8420)
    a = _args()
    cli.fill_ports(a)
    assert (a.port, a.pages_port, a.proxy_port) == (8430, 8432, 8431)
    assert "8430, 8431 and 8432" in capsys.readouterr().out
    a = _args()
    cli.fill_ports(a)
    assert a.port == 8430 and capsys.readouterr().out == ""


def test_todays_ports_say_nothing(state, busy, capsys):
    a = _args()
    cli.fill_ports(a)
    assert a.port == 8420 and capsys.readouterr().out == ""


def test_a_flag_wins_for_one_run_and_saves_nothing(state, busy):
    a = _args(9000, 9002, 9001)
    cli.fill_ports(a)
    assert (a.port, a.pages_port, a.proxy_port) == (9000, 9002, 9001)
    assert botports.saved(state) is None
    a = _args(9000)
    cli.fill_ports(a)
    assert (a.port, a.pages_port, a.proxy_port) == (9000, 8422, 8421)


def test_proxy_port_zero_stays_any_free_port(state, busy):
    a = _args(proxy=0)
    cli.fill_ports(a)
    assert a.proxy_port == 0


def test_a_bot_that_runs_now_does_not_move_off_its_own_ports(state, busy, monkeypatch):
    busy.update(botports.DEFAULT.values())           # its own Core holds them
    monkeypatch.setattr(corelock, "holder", lambda s: {"port": 8420, "kind": corelock.CORE})
    a = _args()
    cli.fill_ports(a)
    assert a.port == 8420 and botports.saved(state) is None


def test_the_parser_names_no_port_of_its_own():
    p = cli.build_parser()
    for words in (["serve"], ["setup"], ["start"], ["app"], ["pair"]):
        assert p.parse_args(words).port is None, words
    a = p.parse_args(["serve", "--proxy-port", "0"])
    assert a.proxy_port == 0 and a.pages_port is None


def test_start_leaves_the_ports_to_serve(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", [r"C:\x\nothing-here"])
    assert cli._serve_argv(None)[-1] == "serve"
    assert cli._serve_argv(8420)[-1] == "serve"
    assert cli._serve_argv(9000)[-2:] == ["--port", "9000"]


def test_a_second_bots_start_runs_serve_with_no_port_flag(state, monkeypatch, capsys):
    launched = []
    monkeypatch.setattr(cli, "_launch_serve", lambda argv, title: launched.append(argv))
    monkeypatch.setattr(cli, "_terminal", lambda: None if not launched else {"port": 8430, "token": "x"})
    assert cli._ensure_core(None, 5.0)["port"] == 8430
    assert launched[0][-1] == "serve"


def test_pair_makes_its_link_for_this_bots_port(state, busy, capsys):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "ports.json").write_text(json.dumps(SECOND), encoding="utf-8")
    assert cli.main(["pair"]) == 0
    assert "http://puffpai.localhost:8430/#pair=" in capsys.readouterr().out


def test_setup_passes_on_only_the_flags_she_gave(monkeypatch):
    got = []
    monkeypatch.setattr(term_mod, "main", lambda argv: got.append(argv) or 0)
    assert cli.main(["setup"]) == 0 and cli.main(["setup", "--port", "9000", "--exit"]) == 0
    assert got == [[], ["--port", "9000", "--exit"]]


def test_setup_reads_this_bots_ports(state, busy, monkeypatch):
    busy.add(8420)
    monkeypatch.setattr(term_mod, "_running_core", lambda: None)
    seen = {}

    def acquire(st, *, port, pages_port=None, **kw):
        seen.update(port=port, pages_port=pages_port)
        raise corelock.AlreadyRunning({"port": port, "kind": corelock.CORE})
    monkeypatch.setattr(corelock, "holder", lambda s: None)
    monkeypatch.setattr(corelock, "acquire", acquire)
    assert term_mod.main([]) == 1
    assert seen == {"port": 8430, "pages_port": 8432}


# -- one folder of code, any number of bots: nobody's launchers are overwritten --------------------------------------------
INSTALL = (REPO / "install.ps1").read_text(encoding="utf-8")
SHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _function(name: str) -> str:
    return INSTALL[INSTALL.index(f"function {name} "):].split("\n}\n", 1)[0] + "\n}\n"


def _dir_for(repo, bot) -> Path:
    script = _function("Get-LauncherDir") + f"Get-LauncherDir '{repo}' '{bot}'"
    got = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    assert got.returncode == 0, got.stderr
    return Path(got.stdout.strip())


@pytest.mark.skipif(os.name != "nt" or not SHELL, reason="install.ps1 is Windows")
def test_the_first_bot_keeps_the_folder_and_each_other_gets_its_own(tmp_path):
    assert _dir_for(tmp_path, "buildbot") == tmp_path                                # nothing there yet
    (tmp_path / "Start BuildBot.cmd").write_text('@echo off\nset "PUFFPAI_BOT=buildbot"\n', encoding="ascii")
    assert _dir_for(tmp_path, "buildbot") == tmp_path                                # the same bot, installed again
    assert _dir_for(tmp_path, "buildbot-2") == tmp_path / "bots" / "buildbot-2"
    assert _dir_for(tmp_path, "buildbot-3") == tmp_path / "bots" / "buildbot-3"


@pytest.mark.skipif(os.name != "nt" or not SHELL, reason="install.ps1 is Windows")
def test_a_launcher_in_its_own_folder_runs_this_bot_from_the_code_two_folders_up(tmp_path):
    """The real launcher text, the real program copied beside a scratch .venv, run for real with no window."""
    repo = tmp_path / "repo"
    scripts = repo / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(Path(sys.executable).with_name("puffpai.exe"), scripts / "puffpai.exe")
    block = INSTALL[INSTALL.rindex("= @(", 0, INSTALL.index('pair --running",')):INSTALL.index("Set-Content -LiteralPath $SignInLauncher")]
    folder = repo / "bots" / "buildbot-2"
    folder.mkdir(parents=True)
    path = folder / "Sign in a browser.cmd"
    script = (_function("Set-LauncherLines") + f"$Name = 'buildbot-2'\n$lines {block}\n"
              + f"Set-Content -LiteralPath '{path}' -Value (Set-LauncherLines $lines) -Encoding ASCII")
    got = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    assert got.returncode == 0, got.stderr
    text = path.read_text(encoding="ascii")
    assert "%~dp0..\\..\\.venv\\Scripts\\puffpai.exe" in text and 'cd /d "%~dp0..\\..\\"' in text
    assert "%~dp0.venv" not in text
    home = tmp_path / "local"
    home.mkdir()
    env = {**os.environ, "LOCALAPPDATA": str(home), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    env.pop("PUFFPAI_STATE", None)
    subprocess.run(["cmd.exe", "/d", "/c", str(path)], env=env, capture_output=True, timeout=120, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    assert (home / "PuffPAI" / "buildbot-2").is_dir()                                # it ran, as this bot, from the code two folders up


def test_every_launcher_goes_to_its_own_folder_and_says_where_the_code_is():
    text = INSTALL.replace("\r\n", "\n")
    start = text.index("$RepoDir = $LauncherDir\n")
    end = text.index("$RepoDir = $CodeDir\n")
    assert start < end
    for name, file in (("$Launcher", "Start BuildBot.cmd"), ("$StopLauncher", "Stop BuildBot.cmd"), ("$ChatLauncher", "Chat with BuildBot.cmd"),
                       ("$SignInLauncher", "Sign in a browser.cmd"), ("$WindowLauncher", "Open BuildBot window.cmd")):
        assert start < text.index(f'{name} = Join-Path $RepoDir "{file}"') < end, name
    assert "@($Launcher, $StopLauncher, $ChatLauncher, $SignInLauncher, $WindowLauncher)" in text
    assert text.index("Set-Content -LiteralPath $WindowLauncher") < text.index("@($Launcher, $StopLauncher")


def test_the_core_names_this_bots_own_launchers(monkeypatch, tmp_path):
    fake = tmp_path / "core" / "puffpai_core" / "wizard" / "terminal.py"
    fake.parent.mkdir(parents=True)
    monkeypatch.setattr(term_mod, "__file__", str(fake))
    monkeypatch.setenv("PUFFPAI_BOT", "buildbot-2")
    assert term_mod.launcher() == tmp_path / "Start BuildBot.cmd"
    own = tmp_path / "bots" / "buildbot-2"
    own.mkdir(parents=True)
    (own / "Start BuildBot.cmd").write_text("x", encoding="ascii")
    assert term_mod.stop_launcher() == own / "Stop BuildBot.cmd"
    monkeypatch.setenv("PUFFPAI_BOT", "buildbot")
    assert term_mod.stop_launcher() == tmp_path / "Stop BuildBot.cmd"
