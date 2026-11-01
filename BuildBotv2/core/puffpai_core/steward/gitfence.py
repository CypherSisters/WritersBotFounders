r"""A git boundary for every try and package (lane I2a part D).

The bot's data folder lives under ``%LOCALAPPDATA%`` (``C:\Users\<name>\AppData\Local``), so a try or
package folder can sit below a git repository the owner keeps in her home folder. A tool that walks up
looking for ``.git`` (git itself, or an npm install script that writes hooks, seen on this computer) could
then reach that repository. Two parts:

- :func:`fence`: the environment of every try program (``toolsmith_host.broker``) and every package
  program Steward starts sets ``GIT_CEILING_DIRECTORIES`` to the folder's parent, so git never looks
  above the try or package folder. The Core's own settings that point git at a repository
  (``GIT_DIR``, ``GIT_WORK_TREE`` and the rest) are never passed on.
- :func:`init_repo`: a try folder with no ``.git`` of its own gets one (``git init -q``, the real
  ``git.exe``, no template, so no hooks from anyone's template folder). A tool that walks the folders
  itself (not through git) then stops at the try's own repository. With no git on the computer, nothing
  is made and the reason is returned for the try's record.

Honest limit: a program the try runs can still read or write any folder the owner can (01 "Toolsmith's
zone"); this only stops git and tools that look for ``.git`` from finding a repository above the folder.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import procflags

#: Settings from the Core's own environment that tell git which repository to use: never passed on.
GIT_LOCATORS = frozenset({"git_dir", "git_work_tree", "git_index_file", "git_object_directory",
                          "git_alternate_object_directories", "git_common_dir", "git_namespace", "git_prefix",
                          "git_discovery_across_filesystem", "git_ceiling_directories"})
INIT_TIMEOUT_S = 60.0
NO_GIT = "Git is not on this computer, so the try has no git folder of its own."


def ceiling(folder: Path | str) -> str:
    """What ``GIT_CEILING_DIRECTORIES`` says for *folder*: its parent, as a real path."""
    return os.path.realpath(Path(folder).parent)


def fence(env: dict, folder: Path | str) -> dict:
    """*env* with git kept inside *folder*: the repository settings taken out, the ceiling set. In place."""
    for name in [k for k in env if k.lower() in GIT_LOCATORS]:
        del env[name]
    env["GIT_CEILING_DIRECTORIES"] = ceiling(folder)
    return env


def git_program() -> str | None:
    """The real git program (``git.exe`` on Windows, never a ``git.cmd`` wrapper), or None."""
    return shutil.which("git.exe" if os.name == "nt" else "git")


def init_repo(folder: Path | str, env: dict | None = None) -> str:
    """Give *folder* a git repository of its own when it has no ``.git``. "" = it has one now (or had one),
    else the reason it has none (no git here, or git failed)."""
    folder = Path(folder)
    if os.path.lexists(folder / ".git"):
        return ""
    git = git_program()
    if not git:
        return NO_GIT
    run_env = fence(dict(os.environ if env is None else env), folder)
    run_env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        # core.longpaths: in a deep bot folder git said "Filename too long" without it (seen 10-22).
        got = subprocess.run([git, "-c", "core.longpaths=true", "init", "-q", "--template=", str(folder)],
                             cwd=str(folder), env=run_env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=INIT_TIMEOUT_S,
                             check=False, **procflags.hidden())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"git init did not run ({type(exc).__name__})."
    if got.returncode != 0 or not (folder / ".git").is_dir():
        last = (got.stderr or "").strip().splitlines()[-1:] or [f"exit {got.returncode}"]
        return f"git init failed: {last[0][:200]}"
    return ""


__all__ = ["GIT_LOCATORS", "NO_GIT", "ceiling", "fence", "git_program", "init_repo"]
