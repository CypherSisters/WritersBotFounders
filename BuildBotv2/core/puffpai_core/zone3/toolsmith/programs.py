# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (adapted)
"""A missing program is a "Your turn" step, never a refusal (03 3.3, v1 NEXT_FIXES 17).

``step_for(name)`` gives the one step shape (02 "Setup steps") that installs a
common runtime with the system package manager; the try pauses on it and goes
on after Done. ``tools_in(steps)`` names the programs a README's commands use,
``missing(tools)`` the ones not on this computer (looked for, never run).

v1 sources in ``toolsmith_run.py``
(``install_command`` :1868, ``PACKAGE_IDS`` :1890, ``_PACKAGE_ALIASES`` :1902,
``known_package`` :1908, ``TOOL_OF`` :1813, ``TOOL_PROGRAM`` :1820; adapted:
returns Checklist steps instead of queueing the command).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from typing import Any

_PACKAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@-]{0,99}\Z")

#: Common runtimes and tools by their package ids: winget, brew, apt.
PACKAGE_IDS = {
    "go": ("GoLang.Go", "go", "golang-go"),
    "ruby": ("RubyInstallerTeam.Ruby.3.3", "ruby", "ruby-full"),
    "rust": ("Rustlang.Rustup", "rustup", "cargo"),
    "java": ("EclipseAdoptium.Temurin.21.JDK", "openjdk@21", "openjdk-21-jdk"),
    "node": ("OpenJS.NodeJS.LTS", "node", "nodejs"),
    "python": ("Python.Python.3.12", "python@3.12", "python3"),
    "make": ("ezwinports.make", "make", "make"),
    "cmake": ("Kitware.CMake", "cmake", "cmake"),
    "git": ("Git.Git", "git", "git"),
    "docker": ("Docker.DockerDesktop", "docker", "docker.io"),
}
_ALIASES = {"golang": "go", "cargo": "rust", "rustup": "rust", "rustc": "rust",
            "jdk": "java", "openjdk": "java", "temurin": "java", "nodejs": "node",
            "node.js": "node", "npm": "node", "npx": "node", "python3": "python", "py": "python",
            "pip": "python", "gnu make": "make", "docker desktop": "docker", "docker-compose": "docker"}
LABELS = {"go": "Go", "ruby": "Ruby", "rust": "Rust", "java": "Java", "node": "Node.js",
          "python": "Python", "make": "make", "cmake": "CMake", "git": "Git", "docker": "Docker Desktop"}
#: The program each tool is found by on this computer.
PROGRAM = {"go": "go", "ruby": "ruby", "rust": "cargo", "java": "java", "node": "node",
           "python": "python", "make": "make", "cmake": "cmake", "git": "git", "docker": "docker"}


def _os_key() -> str:
    if os.name == "nt":
        return "nt"
    return "darwin" if os.uname().sysname == "Darwin" else "linux"


def key_of(name: Any) -> str:
    k = str(name or "").strip().lower()
    return _ALIASES.get(k, k)


def install_command(package_id: str, os_key: str = "") -> str:
    pid = str(package_id or "").strip()
    if not _PACKAGE_ID.match(pid):
        return ""
    os_key = os_key or _os_key()
    if os_key == "nt":
        return f"winget install -e --id {pid} --accept-source-agreements --accept-package-agreements"
    if os_key == "darwin":
        return f"brew install {pid}"
    if shutil.which("apt-get"):
        return f"sudo apt-get install -y {pid}"
    if shutil.which("dnf"):
        return f"sudo dnf install -y {pid}"
    return ""


def known_package(name: Any, os_key: str = "") -> str:
    ids = PACKAGE_IDS.get(key_of(name))
    if not ids:
        return ""
    os_key = os_key or _os_key()
    return ids[0] if os_key == "nt" else ids[1] if os_key == "darwin" else ids[2]


def step_for(name: Any, why: str = "") -> dict | None:
    """The "Your turn" step that installs *name*, or None for an unknown program."""
    key = key_of(name)
    pid = known_package(key)
    cmd = install_command(pid) if pid else ""
    if not cmd:
        return None
    label = "Docker" if key == "docker" and _os_key() == "linux" else LABELS.get(key, str(name))
    step = {"id": f"install-{key}", "kind": "run_command", "shell": "powershell" if _os_key() == "nt" else "bash",
            "title": f"Install {label}", "why": why or f"This app needs {label}.", "text": cmd,
            "admin": key == "docker" and _os_key() == "nt",
            "check": {"command": [PROGRAM.get(key, key), "--version" if key != "docker" else "version"]}}
    return step


def tools_in(steps: list[str]) -> list[str]:
    """The program keys a README's commands start with (node, python, docker...)."""
    out: list[str] = []
    for s in steps or []:
        first = (str(s).split() or [""])[0].lower()
        key = key_of(first)
        if key in PACKAGE_IDS and key not in out:
            out.append(key)
    return out


#: The oldest Python a try's own .venv may be made from.
PYTHON_MIN = (3, 11)
PYTHON_HERE = ("Python {v} is here already: BuildBot runs on it, and the Core makes the app's own Python from it. "
               "Run program python with args [\"-m\", \"venv\", \".venv\"], then python -m pip as usual.")


def core_python() -> str:
    """The interpreter the Core itself runs on (its base, not a venv launcher), when it is 3.11 or newer; else ""."""
    if sys.version_info[:2] < PYTHON_MIN:
        return ""
    path = getattr(sys, "_base_executable", None) or sys.executable
    return path if path and os.path.isfile(path) else ""


def python_here_line() -> str:
    return PYTHON_HERE.format(v=f"{sys.version_info[0]}.{sys.version_info[1]}")


def missing(tools: list[str]) -> list[str]:
    """The tools not on this computer. Python is never missing while the Core runs on a usable 3.11 or newer:
    a try's .venv is made from the Core's own interpreter (``broker.make_venv``), never from the PATH."""
    return [t for t in tools if not (t == "python" and core_python()) and not shutil.which(PROGRAM.get(t, t))]


__all__ = ["LABELS", "PACKAGE_IDS", "PYTHON_MIN", "core_python", "install_command", "python_here_line", "key_of",
           "known_package", "missing",
           "step_for", "tools_in"]
