# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (adapted)
"""How Toolsmith reads a command line before the Core runs it (no process is started here).

Used by Toolsmith's own tools (reads are free, builds are timed, the repeat
guard) and by the Core's exec broker (timing, the background and pipe rules,
the Docker wall on a compose ``up``). This module never starts a program.

v1 sources in ``toolsmith_run.py``: ``_FOREVER`` :82, ``never_ends`` :91,
``_BUILD`` :108, ``_PIPE_CUT`` :115, ``_BACKGROUND`` :117, ``is_build`` :122,
``build_line`` :127, ``_READ_WORDS`` :1757, ``read_only`` :1764. v1's
``shell_name``/``git_bash`` are gone: the Core runs no shell (D60).
"""

from __future__ import annotations

import re

#: Commands that never end by themselves (following logs, watch, a dev server).
_FOREVER = re.compile(
    r"(?i)(?:\blogs\b[^;&|\n]*\s(?:-f|--follow)\b"
    r"|\btail\b[^;&|\n]*\s(?:-f|-F|--follow)\b"
    r"|(?:^|[;&|]\s*)watch\s"
    r"|\b(?:npm|pnpm|yarn|bun)(?:@\S*)?\s+(?:run\s+)?(?:dev|start|serve|preview)\b"
    r"|\bnext\s+(?:dev|start)\b|\bvite(?:\s*$|\s+(?!build\b))"
    r"|\bflask\s+run\b|\buvicorn\b|\bgunicorn\b|\brunserver\b|\bhttp\.server\b)")
_COMPOSE_HEAD = (r"(?:docker(?:\.exe)?\s+compose|docker-compose(?:\.exe)?)"
                 r"(?:\s+(?:-f|-p|--file|--project-name|--env-file|--profile|--project-directory"
                 r"|--ansi|--progress|--parallel)(?:\s+|=)\S+|\s+--?[A-Za-z][\w-]*(?:=\S+)?(?=\s))*\s+")
_BUILD = re.compile(
    r"(?i)(?<![\w.-])(?:docker(?:\.exe)?\s+(?:buildx\s+)?build\b|docker(?:\.exe)?\s+pull\b"
    r"|" + _COMPOSE_HEAD + r"(?:build|up|pull)(?=\s|$|[;&|)])"
    r"|(?:npm|pnpm|yarn|bun)(?:@\S*)?\s+(?:install|i|ci|add|build|run\s+build)\b"
    r"|(?:pip3?|-m\s+pip)\s+install\b|go\s+build\b|cargo\s+build\b)"
    r"|(?:^|[;&|(])\s*make\b")
_PIPE_CUT = re.compile(r"\s*\|\s*(?:tail|head)\b[^|;&\n]*$")
_BACKGROUND = re.compile(r"(?:^|[\s;&|(])nohup\s|(?<![&|>])&\s*$")
_COMPOSE_UP = re.compile(r"(?i)(?<![\w.-])" + _COMPOSE_HEAD + r"(?:up|start)(?=\s|$|[;&|)])")
BUILD_FOREGROUND = ("Run builds in the foreground so BuildBot can follow them; "
                    "start servers with start_server.")
#: Commands that only read (they spend no steps; their own cap per try).
_READ_WORDS = frozenset("""cat head tail ls find grep egrep fgrep rg type get-content gc dir
tree wc file stat more less pwd echo which where get-childitem select-string""".split())
_READ_SPLIT = re.compile(r"\|\||&&|[|;]")


def never_ends(command: str) -> bool:
    return bool(_FOREVER.search(str(command or "")))


def is_build(command: str) -> bool:
    return bool(_BUILD.search(str(command or "")))


def is_compose_up(command: str) -> bool:
    return bool(_COMPOSE_UP.search(str(command or "")))


def build_line(command: str) -> tuple[str, str]:
    """``(command, why_not)``: a build's trailing ``| tail``/``| head`` cut; a
    build sent to the background refused with one line."""
    cmd = str(command or "")
    if not is_build(cmd):
        return cmd, ""
    if _BACKGROUND.search(cmd):
        return cmd, BUILD_FOREGROUND
    while True:
        cut = _PIPE_CUT.sub("", cmd)
        if cut == cmd:
            return cmd, ""
        cmd = cut


def read_only(command: str) -> bool:
    """Does every part of *command* only read (cat, ls, grep...)?"""
    parts = [p.strip() for p in _READ_SPLIT.split(str(command or "")) if p.strip()]
    if not parts or re.search(r"[>]", str(command)):
        return False
    return all(p.split()[0].lower().rsplit("/", 1)[-1] in _READ_WORDS for p in parts)


__all__ = ["BUILD_FOREGROUND", "build_line", "is_build", "is_compose_up", "never_ends", "read_only"]
