"""install.sh, its launchers and what the Core says about them off Windows (D132)."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from puffpai_core import cli, corelock, paths
from puffpai_core.wizard import terminal

REPO = Path(__file__).resolve().parents[1]
posix = pytest.mark.skipif(os.name == "nt", reason="install.sh runs on Linux and Mac")
bash = shutil.which("bash")


def test_every_file_install_sh_writes_is_a_name_the_core_knows():
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    written = set(re.findall(r"^write_launcher (\S+\.sh) ", text, re.M))
    assert written == set(terminal.POSIX_LAUNCHERS.values())


def test_install_sh_prints_ascii_and_keeps_the_privacy_line():
    for name in ("install.sh", "scripts/install_tools.sh"):
        (REPO / name).read_text(encoding="ascii")
    assert "Nothing leaves your computer without your consent." in (REPO / "install.sh").read_text(encoding="ascii")


def test_shell_files_stay_lf_from_a_windows_checkout():
    assert "*.sh text eol=lf" in (REPO / ".gitattributes").read_text(encoding="utf-8")


def test_the_mac_finder_files_run_the_launchers_install_sh_writes():
    text = (REPO / "scripts" / "install_tools.sh").read_text(encoding="ascii")
    assert "start-buildbot.sh" in text and "stop-buildbot.sh" in text and "start-bot.sh" not in text


@posix
def test_the_core_finds_its_bot_folder_in_a_normal_place(monkeypatch, tmp_path):
    monkeypatch.delenv("PUFFPAI_STATE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    if sys.platform == "darwin":
        assert paths.data_root() == tmp_path / "Library" / "Application Support" / "PuffPAI"
    else:
        assert paths.data_root() == tmp_path / "xdg" / "puffpai"
        monkeypatch.setenv("XDG_DATA_HOME", "not/a/full/path")       # the XDG rule: a relative one is ignored
        assert paths.data_root() == tmp_path / ".local" / "share" / "puffpai"
    assert "AppData" not in str(paths.data_root())


@posix
def test_the_words_name_a_command_she_can_run_never_a_cmd_file(tmp_path):
    path = tmp_path / "a folder" / "start-buildbot.sh"
    assert terminal.how_to_run(path) == f"run '{path}'"
    words = cli._runs_words()
    assert words == {"where": "the background", "stop": "stop-buildbot.sh"}
    assert not any(".cmd" in line or "window" in line for line in corelock.already_running_lines({"port": 8420}))


@posix
def test_cli_command_is_a_line_a_posix_shell_reads(monkeypatch):
    monkeypatch.setenv("PUFFPAI_BOT", "my bot")
    monkeypatch.delenv("PUFFPAI_STATE", raising=False)
    line = terminal.cli_command("shutdown")
    assert line.startswith("PUFFPAI_BOT='my bot' ") and line.endswith(" shutdown") and "& " not in line and "$env:" not in line


@posix
def test_a_started_core_writes_to_its_log_in_its_own_session(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE", str(tmp_path))
    seen = {}

    class Popen:
        def __init__(self, argv, **kw):
            seen.update(kw, argv=argv)

    monkeypatch.setattr(subprocess, "Popen", Popen)
    cli._launch_serve(["puffpai", "serve"], "title")
    assert seen["start_new_session"] is True and seen["stdin"] == subprocess.DEVNULL
    assert (tmp_path / "logs" / "serve.log").is_file()


@posix
@pytest.mark.skipif(not bash, reason="needs bash")
def test_a_dry_run_shows_every_step_and_changes_nothing(tmp_path):
    repo = tmp_path / "a repo"
    repo.mkdir()
    shutil.copy(REPO / "install.sh", repo)
    (repo / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "install_tools.sh", repo / "scripts")
    home = tmp_path / "home"
    home.mkdir()
    # A Mac gets its own programs, so it is not told to be Linux: uname still says Darwin, and setup goes the Mac's way.
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "PUFFPAI_INSTALL_PRETEND_MISSING": "python,node,ollama",
           **({} if sys.platform == "darwin" else {"PUFFPAI_TOOLS_PLATFORM": "linux-x64"})}
    got = subprocess.run([bash, str(repo / "install.sh"), "--dry-run"], env=env, capture_output=True, text=True,
                         timeout=60, stdin=subprocess.DEVNULL, check=False)
    assert got.returncode == 0, got.stdout + got.stderr
    for n in range(1, 6):
        assert f"Step {n} of 5:" in got.stdout
    for name in terminal.POSIX_LAUNCHERS.values():
        assert f"would write {repo}/{name}" in got.stdout
    assert "would download Python" in got.stdout and "would download Node.js" in got.stdout
    if sys.platform == "darwin":
        assert "would download Ollama" in got.stdout and "ollama.com/install.sh" not in got.stdout
    else:
        assert "ollama.com/install.sh" in got.stdout
    assert sorted(p.name for p in repo.iterdir()) == ["install.sh", "scripts"] and not list(home.iterdir())


@posix
@pytest.mark.skipif(not bash, reason="needs bash")
def test_a_mac_downloads_a_checked_ollama_and_never_pipes_its_script(tmp_path):
    """Run anywhere: a stand-in ``uname`` says Darwin, so the Mac's own way through setup is read on every computer."""
    repo = tmp_path / "a repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "install.sh", repo)
    shutil.copy(REPO / "scripts" / "install_tools.sh", repo / "scripts")
    home, tools = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    tools.mkdir()
    (tools / "uname").write_text('#!/bin/sh\ncase "$1" in -m) echo arm64;; *) echo Darwin;; esac\n', encoding="utf-8")
    (tools / "uname").chmod(0o755)
    env = {"PATH": str(tools) + os.pathsep + os.environ["PATH"], "HOME": str(home),
           "PUFFPAI_INSTALL_PRETEND_MISSING": "python,node,ollama"}
    got = subprocess.run([bash, str(repo / "install.sh"), "--dry-run"], env=env, capture_output=True, text=True,
                         timeout=60, stdin=subprocess.DEVNULL, check=False)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "would download Ollama" in got.stdout and "signed by Ollama" in got.stdout and "darwin-arm64" in got.stdout
    assert "ollama.com/install.sh" not in got.stdout and not list(home.iterdir())


@posix
@pytest.mark.skipif(not bash, reason="needs bash")
@pytest.mark.parametrize("name", ["../up", "a/b", "", "-x", "a..b"])
def test_a_bot_name_that_cant_be_a_folder_stops_setup(tmp_path, name):
    got = subprocess.run([bash, str(REPO / "install.sh"), "--bot", name, "--dry-run"],
                         env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, capture_output=True, text=True,
                         timeout=60, stdin=subprocess.DEVNULL, check=False)
    assert got.returncode == 1 and "can't be a bot name" in got.stdout
