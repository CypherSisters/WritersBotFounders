"""No shell on Windows either (D51, D60): a program BuildBot starts is never a batch file.

Linux and Mac (D132) have no batch files, and npm and npx there are links or scripts the system's Node.js
runs. They still run as ``node <tool>-cli.js``, so a try and a saved run block read the same. ``npm_cli`` knows
the layouts of a distro's package (Debian's ``/usr/share/nodejs/npm``), nvm and Homebrew; a version manager's
shim (asdf, nodenv) that is a script hiding where the real npm is, is not followed, so npm says "not found"
there rather than running something BuildBot could not check.

Windows starts a ``.cmd`` or ``.bat`` file through ``cmd.exe`` by itself: CreateProcess does it with
no word from the caller, so a batch file as the program brings a shell back (and cmd.exe reads its
arguments with its own rules). npm and npx on Windows are such files (``npm.cmd``, ``npx.cmd``, next
to ``node.exe``). So:

- :func:`batch_file` tells a batch file by its name the way Windows reads it (trailing dots and
  spaces dropped, any letter case);
- :func:`node_exe` is the Node.js on the Core's own PATH, only when it is a real program
  (``node.exe`` starting with ``MZ``), never a ``node.cmd`` wrapper;
- :func:`resolve` turns ``npm ...`` and ``npx ...`` into ``node.exe <npm-cli.js> ...`` (the scripts
  in the Node.js folder's ``node_modules\\npm\\bin``) and refuses any other batch file with a plain
  reason (:class:`NoShell`). Steward runs every setup, start and health command through it;
- ``jobs.Job.spawn`` refuses a batch file as the program whoever asks, the last check before Windows
  starts anything.

Honest limit: npm itself still runs a package's own scripts (``npm start``, install scripts) through
its script shell. That is npm's choice, not a BuildBot shell, and it runs as the owner.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
from pathlib import Path

IS_WINDOWS = os.name == "nt"
BATCH = (".cmd", ".bat")
_NPM = re.compile(r"(?i)^(npm|npx)(?:\.cmd|\.bat|\.ps1|\.exe)?$")
BATCH_LINE = ("{name} is a Windows batch file (.cmd or .bat), and Windows runs those only through cmd.exe, "
              "a shell, so BuildBot doesn't run it.")
ODD_LINE = "{name} is written in a way BuildBot can't check (a hidden part of a file), so it was not run."
NO_NODE = ("{tool} needs Node.js, and BuildBot found no Node.js program with its {tool}-cli.js on this computer, "
           "so it was not run.")


class NoShell(PermissionError):
    """Not started: it would need a shell. ``reason`` (and ``strerror``) is plain words."""

    def __init__(self, reason: str) -> None:
        super().__init__(errno.EACCES, reason)
        self.reason = reason


def windows_name(word: str) -> str:
    """The last part of a program path as Windows reads it: lower case, trailing dots and spaces dropped."""
    return re.split(r"[\\/]", str(word))[-1].rstrip(" .").lower()


def batch_file(word: str) -> bool:
    return windows_name(word).endswith(BATCH)


def refusal(word: str) -> str:
    """Why *word* may not be the program Windows starts ("" = it may). Only Windows has batch files."""
    if not IS_WINDOWS:
        return ""
    if batch_file(word):
        return BATCH_LINE.format(name=windows_name(word) or "It")
    if ":" in str(word)[2:]:
        return ODD_LINE.format(name=windows_name(word) or "It")
    return ""


def _native(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    if IS_WINDOWS:
        return path.suffix.lower() in (".exe", ".com") and head[:2] == b"MZ"
    return True


def node_exe() -> str:
    """The Node.js program on the Core's own PATH ("" when there is none, or it is only a wrapper). Its real
    file, except off Windows when npm is only next to the link (Homebrew's ``bin/node`` into its Cellar): then
    the link, in its real folder, so npm is found next to the node it names."""
    found = shutil.which("node.exe" if IS_WINDOWS else "node")
    if not found:
        return ""
    real = Path(os.path.realpath(found))
    if not (real.is_file() and _native(real)):
        return ""
    if not IS_WINDOWS:
        link = Path(os.path.realpath(os.path.dirname(found))) / os.path.basename(found)
        if not (real.parent / "npm").exists() and (link.parent / "npm").exists():
            return str(link)
    return str(real)


def npm_cli(node: str, tool: str) -> Path | None:
    """``npm-cli.js`` / ``npx-cli.js`` of the Node.js install at *node* (None when it is not there)."""
    if not node:
        return None
    here = Path(node).parent
    bases = [here / "node_modules" / "npm" / "bin", here.parent / "lib" / "node_modules" / "npm" / "bin"]
    if not IS_WINDOWS:                                # a distro's own package (Debian, Ubuntu)
        bases += [here.parent / "share" / "nodejs" / "npm" / "bin", here.parent / "lib" / "nodejs" / "npm" / "bin"]
    for base in bases:
        cli = base / f"{tool}-cli.js"
        if cli.is_file():
            return Path(os.path.realpath(cli))
    if not IS_WINDOWS:                                # the link next to node, wherever it leads
        real = Path(os.path.realpath(here / tool))
        if real.name == f"{tool}-cli.js" and real.is_file():
            return real
    return None


def resolve(argv: list[str], node: str | None = None) -> list[str]:
    """*argv* as it can start with no shell: ``npm``/``npx`` (typed bare, or as the wrapper next to
    *node*) become ``node.exe <tool>-cli.js``; any other batch file is :class:`NoShell`."""
    if not argv:
        return list(argv)
    first = str(argv[0])
    m = _NPM.match(windows_name(first))
    if m:
        node = node_exe() if node is None else node
        tool = m.group(1).lower()
        bare = not re.search(r"[\\/]", first)
        beside = bool(node) and os.path.normcase(os.path.dirname(os.path.realpath(first))) == \
            os.path.normcase(os.path.dirname(node))
        if node and not IS_WINDOWS and not bare:      # a link next to node: its target lives elsewhere
            beside = beside or os.path.dirname(os.path.abspath(first)) == os.path.dirname(node)
        if bare or beside:
            cli = npm_cli(node or "", tool)
            if not node or cli is None:
                raise NoShell(NO_NODE.format(tool=tool))
            return [node, str(cli), *[str(a) for a in argv[1:]]]
    why = refusal(first)
    if why:
        raise NoShell(why)
    return [str(a) for a in argv]


__all__ = ["BATCH", "IS_WINDOWS", "NoShell", "batch_file", "node_exe", "npm_cli", "refusal", "resolve",
           "windows_name"]
