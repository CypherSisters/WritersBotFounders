"""install.ps1 gets what is missing for itself, with a practice run (D145). The real downloads are not run here."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEXT = (REPO / "install.ps1").read_text(encoding="ascii")
powershell = shutil.which("powershell.exe")
windows = pytest.mark.skipif(os.name != "nt" or not powershell, reason="install.ps1 runs on Windows")


def test_every_pin_is_a_whole_sha256():
    pins = re.findall(r'"(?:python|node|ollama)-(?:x64|arm64)"\s*=\s*@\("[^"]+", "([^"]*)"\)', TEXT)
    assert len(pins) == 6 and all(re.fullmatch(r"[0-9a-f]{64}", p) for p in pins)


def test_it_never_installs_for_the_whole_computer():
    body = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
    for word in ("winget install", "msiexec", "OllamaSetup", "-Verb RunAs", "Set-ItemProperty", "New-ItemProperty",
                 "SetEnvironmentVariable", "setx "):
        assert word not in body


def test_install_tools_and_install_ps1_use_the_same_versions():
    shell = (REPO / "scripts" / "install_tools.sh").read_text(encoding="ascii")
    for name, ps in (("TOOLS_PY_VERSION", "ToolPy"), ("TOOLS_PY_BUILD", "ToolPyBuild"), ("TOOLS_NODE_VERSION", "ToolNode"),
                     ("TOOLS_OLLAMA_VERSION", "ToolOllama")):
        value = re.search(name + '="([^"]+)"', shell).group(1)
        assert f'${ps} = "{value}"' in TEXT


def test_install_cmd_passes_everything_on():
    text = (REPO / "install.cmd").read_text(encoding="ascii")
    assert 'install.ps1" %*' in text


def _dry(tmp_path, *args, pretend="python,node,ollama"):
    repo = tmp_path / "a repo"
    repo.mkdir()
    shutil.copy(REPO / "install.ps1", repo)
    local = tmp_path / "local"
    local.mkdir()
    env = {**os.environ, "LOCALAPPDATA": str(local), "PUFFPAI_INSTALL_PRETEND_MISSING": pretend, "PUFFPAI_NO_PAUSE": "1"}
    env.pop("PUFFPAI_TOOLS_DIR", None)
    got = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / "install.ps1"), "-DryRun", *args],
                         env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, check=False)
    return got, repo, local


@windows
def test_a_dry_run_with_all_missing_shows_every_step_and_changes_nothing(tmp_path):
    got, repo, local = _dry(tmp_path)
    assert got.returncode == 0, got.stdout + got.stderr
    for n in range(1, 6):
        assert f"Step {n} of 5:" in got.stdout
    for name in ("Python 3.12.15", "Node.js 24.21.0", "Ollama 0.40.2"):
        assert f"[dry-run] would download {name}" in got.stdout
    assert "PuffPAI-tools" in got.stdout and "would ask: Download them now?" in got.stdout
    for name in ("Start BuildBot.cmd", "Stop BuildBot.cmd", "Chat with BuildBot.cmd", "Sign in a browser.cmd", "Open BuildBot window.cmd"):
        assert f"would write {repo / name}" in got.stdout
    assert [p.name for p in repo.iterdir()] == ["install.ps1"] and not list(local.iterdir())


@windows
def test_a_dry_run_for_the_window_only_needs_only_python(tmp_path):
    got, repo, _ = _dry(tmp_path, "-WindowFor", "192.168.1.50:8423")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "would download Python" in got.stdout and "would download Node.js" not in got.stdout
    assert "would download Ollama" not in got.stdout and [p.name for p in repo.iterdir()] == ["install.ps1"]


@windows
def test_no_tools_downloads_nothing_and_says_what_is_missing(tmp_path):
    got, _, _ = _dry(tmp_path, "-NoTools", pretend="node,ollama")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "would download" not in got.stdout and "Node.js isn't here" in got.stdout and "Ollama isn't here" in got.stdout
