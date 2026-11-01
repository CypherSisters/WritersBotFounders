# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/integrate/readme_steps.py (as-is)
"""A project's README Quick Start, read as the commands its makers say to run.

Deterministic, never runs anything: the README's install / run section
(headings like Quick start, Getting started, Installation, Usage, Running
locally, Self-hosting, Docker) is read for fenced shell code blocks, in
order. ``git clone`` and the ``cd`` into the project are dropped (BuildBot
already has the code); comments, prompts (``$ ``) and lines that are not a
command are dropped. The port it says to open (``localhost:3000``) is kept.

``read(text) -> {"steps": [...], "port": int | None, "runtime": "node" |
"python" | "docker" | ""}``: *runtime* is how the Quick Start runs it (its
first install or start command decides), so BuildBot runs it the way it is
built. Goal-agnostic: nothing here names a project.

D85 (lane I21b) reads the common forms the first rules missed: a leading
``sudo`` is dropped (not the whole line), ``a && b`` and ``a; b`` are each
read as a command, ``\\`` continued lines are joined, indented (4-space)
code blocks are read like fenced ones, inline code (`npm start`) is read
under a run heading, and under a run, start or usage heading any
``python <file>.py`` counts as a start. These rules only say whether a
project looks runnable; Drawbridge and the try still check every command.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from typing import Any, Optional

#: Headings whose section says how to install and run the project.
_RUN_HEADING = re.compile(
    r"(?i)\b(quick ?start|getting started|get started|installation|install|usage|"
    r"running|run (it )?locally|run locally|local development|development|self[- ]?host"
    r"(ing|ed)?|setup|set up|deploy(ment)?|docker|how to (run|use|start)|start)\b")
#: Fence languages that hold shell commands ("" when none is said).
_SHELL_LANGS = frozenset({"", "sh", "bash", "shell", "console", "zsh", "terminal", "cmd",
                          "powershell", "ps", "ps1", "bat", "text", "shellsession"})
#: Programs a Quick Start command may start with.
_TOOLS = frozenset({"npm", "pnpm", "yarn", "npx", "bun", "deno", "node", "corepack",
                    "docker", "docker-compose", "podman", "pip", "pip3", "python", "python3",
                    "py", "uv", "poetry", "pipenv", "make", "go", "cargo", "cp", "copy",
                    "mv", "mkdir", "streamlit", "uvicorn", "flask", "gunicorn", "php",
                    "composer", "bundle", "rails", "java", "mvn", "gradle", "./gradlew",
                    "dotnet"})
_NODE = frozenset({"npm", "pnpm", "yarn", "npx", "bun", "deno", "node", "corepack"})
_PY = frozenset({"pip", "pip3", "python", "python3", "py", "uv", "poetry", "pipenv",
                 "streamlit", "uvicorn", "flask", "gunicorn"})
_DOCKER = frozenset({"docker", "docker-compose", "podman"})
_FENCE = re.compile(r"^(\s*)(```+|~~~+)\s*([A-Za-z0-9_+-]*)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_PORT = re.compile(r"(?i)(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})")
MAX_STEPS = 12


def _command(line: str) -> str:
    """One README line as a command, or "" when it is not one."""
    line = line.strip()
    line = re.sub(r"^(\$|>|PS [^>]*>|%)\s+", "", line)
    line = re.sub(r"\s+#\s.*$", "", line).strip()      # a trailing comment
    line = re.sub(r"^sudo(?:\s+-[A-Za-z]+)*\s+", "", line)   # D85: the command, never as root
    if not line or line.startswith("#"):
        return ""
    first = line.split()[0].lower()
    if first in ("git", "cd", "gh", "sudo") or first not in _TOOLS:
        return ""
    if re.search(r"(?i)(?:^|\s)(?:-g|--global)(?=\s|$)", line):
        return ""        # installs for the whole computer: not part of running it
    if _CHECK_RE.search(line):
        return ""        # tests, lint, type checks: for its makers, not for running it
    return line[:200]


#: Commands that check the code (tests, lint, type checks, formatting).
_CHECK_RE = re.compile(
    r"(?i)^(?:npm|pnpm|yarn|bun|npx)\s+(?:run\s+)?(?:test|tests|lint|typecheck|type-check|"
    r"check|format|fmt|prettier|eslint|coverage|e2e|storybook|vitest|jest|playwright)"
    r"(?:[:\s]|$)|^(?:pytest|tox|ruff|flake8|mypy|black|make\s+(?:test|lint|check))\b"
    r"|^python3?\s+-m\s+(?:pytest|unittest|mypy|ruff|black)\b")
#: Commands that start it (a server, its containers, a dev server).
_START_RE = re.compile(
    r"(?i)^(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:start|dev|serve|preview)\b"
    r"|^npm\s+start\b|^(?:docker[- ]compose|docker\s+compose|podman[- ]compose)\b.*\bup\b"
    r"|^(?:docker|podman)\s+run\b|^deno\s+(?:run|task)\b|^node\s+\S+"
    r"|^(?:uvicorn|gunicorn|streamlit\s+run|flask\s+run)\b"
    r"|^(?:python3?|py)\s+(?:-m\s+(?!pip\b|venv\b|ensurepip\b|build\b)\S+|\S*(?:manage\.py\s+runserver|app|main|server|run|"
    r"serve|start)\S*\.py)|^(?:poetry|uv|pipenv)\s+run\s+\S+"
    r"|^make\s+(?:run|start|serve|dev|up)\b|^php\s+artisan\s+serve\b")


#: Headings under which any ``python <file>.py`` is a start (D85): run, start, usage, not install.
_START_HEADING = re.compile(r"(?i)\b(quick ?start|getting started|get started|usage|run|running|start|"
                            r"starting|launch|how to (run|use|start))\b")
#: ``python coach.py`` (any script name) as a start, never an install, setup, test or build script.
_SCRIPT_RE = re.compile(r"(?i)^(?:python3?|py)\s+(?:-u\s+)?(?![\w./\\-]*(?:setup|install|test|build|configure|"
                        r"download)[\w-]*\.py\b)[\w./\\-]+\.py(?:\s|$)")
_INLINE = re.compile(r"`([^`\n]{3,200})`")


def has_start(steps: list[str]) -> bool:
    """Does the Quick Start say how to start it (not only install or build)?"""
    return any(_START_RE.search(s) for s in steps)


def _blocks(text: str) -> list[tuple[str, list[str], bool]]:
    """``[(heading, lines, inline)]``: every fenced shell block and every indented (4-space) code block, and each
    inline code span as a one-line block (*inline* True), with the heading it sits under ("" before any)."""
    out: list[tuple[str, list[str], bool]] = []
    heading = ""
    fence = ""
    block: list[str] = []
    lang = ""
    indented: list[str] | None = None
    blank = True
    for raw in str(text or "").splitlines():
        if fence:
            if raw.strip().startswith(fence):
                if lang in _SHELL_LANGS:
                    out.append((heading, block, False))
                fence, block = "", []
            else:
                block.append(raw)
            continue
        if indented is not None:
            if re.match(r"^(?: {4}|\t)", raw) or not raw.strip():
                indented.append(raw)
                continue
            out.append((heading, indented, False))
            indented = None
        elif blank and re.match(r"^(?: {4}|\t)\s*\S", raw) and not _FENCE.match(raw):
            indented = [raw]                       # D85: an indented code block after a blank line
            continue
        blank = not raw.strip()
        m = _FENCE.match(raw)
        if m:
            fence, lang, block = m.group(2), m.group(3).lower(), []
            continue
        h = _HEADING.match(raw)
        if h:
            heading = re.sub(r"[*_`\[\]()]|<[^>]+>", "", h.group(2)).strip()
            continue
        for span in _INLINE.findall(raw):
            if len(span.split()) >= 2:             # "`npm`" alone is a name, not a command
                out.append((heading, [span], True))
    if indented is not None:
        out.append((heading, indented, False))
    return out


def sections(text: str) -> list[tuple[str, list[str]]]:
    """``[(heading, [code block lines...])]`` for every fenced shell block and indented code block,
    with the heading it sits under ("" before any heading)."""
    return [(h, b) for h, b, inline in _blocks(text) if not inline]


def _joined(lines: list[str]) -> list[str]:
    """Lines continued with a trailing ``\\`` joined into one (D85)."""
    out: list[str] = []
    carry = ""
    for line in lines:
        s = line.rstrip()
        if s.endswith("\\") and not s.endswith("\\\\"):
            carry += s[:-1].strip() + " "
            continue
        out.append((carry + s.strip()) if carry else line)
        carry = ""
    if carry:
        out.append(carry.strip())
    return out


def _parts(line: str) -> list[str]:
    """One line's commands: ``a && b`` and ``a; b`` split, never inside quotes (D85)."""
    out, cur, quote, i = [], "", "", 0
    while i < len(line):
        c = line[i]
        if quote:
            quote = "" if c == quote else quote
        elif c in "\"'":
            quote = c
        elif c == ";" or line.startswith("&&", i):
            out.append(cur)
            cur = ""
            i += 2 if c == "&" else 1
            continue
        cur += c
        i += 1
    return [p for p in out + [cur] if p.strip()]


def runtime_of(steps: list[str]) -> str:
    for s in steps:
        first = s.split()[0].lower()
        if first in _DOCKER:
            return "docker"
        if first in _NODE:
            return "node"
        if first in _PY:
            return "python"
    return ""


def read(text: Any) -> dict:
    """The Quick Start's commands, the port it says to open, and its runtime."""
    text = str(text or "")
    blocks = [(h, _joined(b), inline) for h, b, inline in _blocks(text)]

    def commands(b: list[str]) -> list[str]:
        return [c for c in (_command(p) for x in b for p in _parts(x)) if c]

    chosen = [(h, b) for h, b, _inline in blocks if h and _RUN_HEADING.search(h)]
    if not any(commands(b) for _h, b in chosen):
        chosen = [(h, b) for h, b, inline in blocks if not inline]   # no run section: every shell block
    steps: list[str] = []
    scripts = False                                # a python <file>.py under a run, start or usage heading
    for h, b in chosen:
        for cmd in commands(b):
            if cmd not in steps:
                steps.append(cmd)
            scripts = scripts or bool(h and _START_HEADING.search(h) and _SCRIPT_RE.match(cmd))
        if len(steps) >= MAX_STEPS:
            break
        # The first block that installs AND starts it is the Quick Start;
        # later blocks are variants (other sites, tests, builds).
        if (scripts or any(re.search(r"\b(run (dev|start|serve)|start|up|serve|dev)\b", s) for s in steps)) \
                and runtime_of(steps):
            break
    steps = steps[:MAX_STEPS]
    if not has_start(steps) and not scripts:
        steps = []           # it never says how to start it: no run steps to show
    port: Optional[int] = None
    m = _PORT.search(text)
    if m and 1 <= int(m.group(1)) <= 65535:
        port = int(m.group(1))
    return {"steps": steps, "port": port, "runtime": runtime_of(steps)}


__all__ = ["MAX_STEPS", "has_start", "read", "runtime_of", "sections"]
