# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (rewritten-from)
"""From a working try to a package (03 3.3 "From a running try to a package").

1. :func:`manifest_for` writes ``puffpai.json`` from what actually worked:
   ``origin: adopted:<url>``; a ``run`` block for this computer (``windows``, ``mac`` or ``linux``) with the setup and the
   start line it used, kept working with no shell (D51, :func:`run_block`), or
   its own compose file; ``health`` = the main page; ``ui.pages`` with
   ``show: new_tab``; a tray item; ``needs.hosts`` seen in its settings and
   compose files; ``needs.install_hosts`` for its runtime; ``setup_steps``
   for what a person did; ``consent`` in one plain sentence that says where
   it runs.
2. :func:`install` sends the manifest with its add: the Core checks it, writes it
   as ``.buildbot/puffpai.json`` itself (Toolsmith can't write that file, or a
   top-level ``puffpai.json``), stops the try and
   hands the folder to lane L1's Package Manager, which checks a copy with
   Drawbridge bound to its tree hash. Inside the plan she said Yes to, that Yes
   covers it and it installs with no new card; outside it, ONE Yes card shows
   her the difference, and the job waits (``waiting_yes``, stepping aside for
   other jobs) until she answers it; Toolsmith never answers it. Then Steward
   starts it **from the manifest**. That restart is the real test that the next
   start (after a reboot) works.

How a working start line is kept (D51; the Core runs it with no shell):
- an env prefix (``PORT=3000 HOST=127.0.0.1 node server.js``, or Windows
  ``set PORT=3000&& node server.js``) becomes ``env``; the try's own
  ``PORT``/``HOST`` (the broker gave them) are kept too;
- ``cd web && npm start`` becomes ``cwd: "web"`` (a folder inside the package);
- ``cp .env.example .env`` (or ``copy``, or PowerShell ``Copy-Item a b`` /
  ``Copy-Item -Path a -Destination b``) becomes ``{"copy": [".env.example", ".env"]}``;
- ``python -m venv .venv`` becomes ``{"venv": true}``, and the venv's own
  Python or pip becomes ``{python}`` (``python: true``);
- an interpreter path in argv[0] becomes ``{python}`` or ``{node}``;
- the try's port becomes ``{port}``.
``cwd`` is the folder of the start line and holds for every argument-list
step. Anything that still can't be written for the Core to run with no shell
(a pipe, ``;``, a setup step in another folder) gets a plain note on the job
AND a Checklist step naming the exact lines; it is never left out quietly.
A start line that can't be written at all is never replaced by a guess: that
app is not installed, with that reason, and the next candidate goes on.

v1 source: ``Run.done`` :4118 kept the try's starts (``starts`` :4149); here
they become the manifest's run block.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from .commands import read_only, is_build
from .find import tries_line
from .tries import join as join_words

_ENV_WORD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_CORE_ENV = re.compile(r"(?i)^(?:PUFFPAI_.*|HTTPS?_PROXY|ALL_PROXY|NO_PROXY|NPM_CONFIG_(?:HTTPS_)?PROXY|NODE_USE_ENV_PROXY)$")
#: System variables a package may never set: they change which programs run, or how (D51).
_SYSTEM_ENV = re.compile(r"(?i)^(?:PATH|PATHEXT|COMSPEC|SYSTEMROOT|WINDIR|PYTHONPATH|PYTHONHOME|PYTHONSTARTUP|"
                         r"NODE_OPTIONS|NODE_PATH|LD_PRELOAD|LD_LIBRARY_PATH|DYLD_.*)$")
_CD_WORDS = frozenset({"cd", "chdir", "pushd", "set-location", "sl"})
#: Programs that are a shell: saved as a start or setup step they would bring one back (D51).
_SHELLS = frozenset({"cmd", "powershell", "pwsh", "bash", "sh", "zsh", "dash", "ksh", "fish", "wsl", "wscript",
                     "cscript", "mshta", "start"})
_PYTHON = re.compile(r"(?i)^(?:.*[\\/])?(?:python(?:3(?:\.\d+)?)?|pythonw|py)(?:\.exe)?$")
_NODE = re.compile(r"(?i)^(?:.*[\\/])?node(?:\.exe)?$")
_VENV_TOOL = re.compile(r"(?i)^\.?[\\/]?\.?venv[\\/](?:Scripts|bin)[\\/](python3?|pythonw|pip3?)(?:\.exe)?$")
_URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?::\d+)?")
_SETTING_FILES = (".env", ".env.example", ".env.sample", "compose.yaml", "compose.yml",
                  "docker-compose.yml", "docker-compose.yaml", "config.js", "config.json")
#: Where a project may be cloned from. The same list as ``toolsmith_host.broker.fetch_repo``.
PROJECT_HOSTS = ("github.com", "codeberg.org")
INSTALL_HOSTS = {"node": ["registry.npmjs.org"], "python": ["pypi.org", "files.pythonhosted.org"],
                 "docker": ["registry-1.docker.io", "auth.docker.io", "production.cloudflare.docker.com"]}


def project_host(cand: Any) -> str:
    """The host a candidate is cloned from, from its own address (a plan from before Codeberg: github.com)."""
    url = str((cand or {}).get("url") or "") if isinstance(cand, dict) else ""
    host = urllib.parse.urlsplit(url).hostname if url.startswith("https://") else ""
    return host if host in PROJECT_HOSTS else "github.com"


def made(cand: Any) -> bool:
    """An app Toolsmith wrote itself (D121): no download, ``origin: made``, always on its own card."""
    return isinstance(cand, dict) and cand.get("source") == "made"


def origin_of(cand: dict) -> str:
    """The ``origin`` a try's manifest must say: the Core's own record of the candidate, never a file in the folder."""
    return "made" if made(cand) else f"adopted:{cand['url']}"


def adopted_word(cand: dict) -> str:
    """What the Package Manager gets as ``adopted``: the candidate's address, or the word for a made app."""
    return "made" if made(cand) else str(cand["url"])


def _placeheld(text: str) -> bool:
    return bool(re.fullmatch(r"([^{}]|\{(port|python|node|data|pkg)\})*", text))


def _port(text: str, port: int) -> str:
    return re.sub(rf"(?<!\d){port}(?!\d)", "{port}", text)


def _inside(rel: str) -> str | None:
    """A folder or file path inside the package, normalised ("" = the package folder), or None."""
    rel = str(rel or "").strip().strip("\"'").replace("\\", "/")
    if not rel or rel.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", rel) or "$" in rel or "%" in rel:
        return None
    norm = posixpath.normpath(rel)
    if norm == ".":
        return ""
    if norm.startswith("../") or norm == "..":
        return None
    return norm


def _split(line: str) -> list[str] | None:
    """Words of a command line. A line with backslashes (a Windows path) keeps them."""
    try:
        if "\\" in line:
            return [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'" else w
                    for w in shlex.split(line, posix=False)]
        return shlex.split(line, posix=True)
    except ValueError:
        return None


class Unsaved(ValueError):
    """The working start line can't be written for the Core to run (``lines``: what ran)."""

    def __init__(self, reason: str, lines: list[str]) -> None:
        super().__init__(reason)
        self.reason, self.lines = reason, lines


def _env_word(name: str, value: str, port: int, env: dict) -> bool:
    """Keep ``NAME=value``; False for the Core's own names; :class:`Unsaved` for a system variable."""
    if _SYSTEM_ENV.match(name):
        raise Unsaved(f"It sets {name}, a system setting a package may never change, so it was not saved.",
                      [])
    if _CORE_ENV.match(name):
        return False
    env[name] = _port(value, port)
    return True


def _cd_target(part: list[str]) -> str | None:
    """The folder of ``cd x``, ``cd /d x``, ``CD x``, ``chdir x``, ``pushd x``, ``Set-Location [-Path] x``, ``sl x``."""
    words = part[1:]
    if words and words[0].lower() == "/d":
        words = words[1:]
    if words and words[0].lower() in ("-path", "-literalpath"):
        words = words[1:]
    if len(words) != 1 or words[0] in ("-", "~") or words[0].startswith("-"):
        return None
    return words[0]


def parse_line(line: str, port: int) -> dict | None:
    """One command line as the Core can run it with no shell.

    ``{"cwd": "web" | "", "env": {...}, "steps": [argv | {"copy": [...]} | {"venv": True}]}``;
    the last step is the command itself, earlier ones came before an ``&&``.
    None when it needs a shell (pipes, ``;``, ``||``, redirects, a folder outside)."""
    words = _split(re.sub(r"\s*&&\s*", " && ", str(line or "")))
    if not words:
        return None
    if any(w in ("|", "||", ";", ">", ">>", "<", "&") or re.search(r"[|;<>`]|\$\(", w) for w in words):
        return None
    parts: list[list[str]] = [[]]
    for w in words:
        if w == "&&":
            parts.append([])
        else:
            parts[-1].append(w)
    cwd, env, steps = "", {}, []
    for part in parts:
        if not part:
            return None
        while part and _ENV_WORD.match(part[0]):
            name, value = _ENV_WORD.match(part[0]).groups()
            if not _env_word(name, value, port, env):
                return None                     # the Core's own names are never a package's
            part = part[1:]
        if not part:
            return None
        if part[0].lower() == "set" and len(part) == 2 and _ENV_WORD.match(part[1]):
            name, value = _ENV_WORD.match(part[1]).groups()          # cmd: set PORT=3000&& node s.js
            if not _env_word(name, value, port, env):
                return None
            continue
        if part[0].lower() in _CD_WORDS:
            target = _cd_target(part)
            if target is None:
                return None                     # a cd it can't read: never a guessed folder
            where = _inside(posixpath.join(cwd, target.replace("\\", "/")) if cwd else target)
            if where is None:
                return None
            cwd = where
            continue
        steps.append({"cwd": cwd, "argv": part})
    if not steps:
        return None
    return {"cwd": cwd, "env": env, "steps": steps}


def _argv0(argv: list[str]) -> tuple[list[str], set[str]]:
    """argv with its interpreter as ``{python}`` / ``{node}``; the placeholders used."""
    used: set[str] = set()
    first = argv[0]
    m = _VENV_TOOL.match(first)
    if m:
        tool = m.group(1).lower()
        used.add("python")
        if tool.startswith("pip"):
            return ["{python}", "-m", "pip", *argv[1:]], used
        return ["{python}", *argv[1:]], used
    if _PYTHON.match(first):
        used.add("python")
        return ["{python}", *argv[1:]], used
    if re.match(r"(?i)^pip3?(?:\.exe)?$", first):
        used.add("python")
        return ["{python}", "-m", "pip", *argv[1:]], used
    if _NODE.match(first):
        used.add("node")
        return ["{node}", *argv[1:]], used
    return list(argv), used


#: Words a shell runs itself (PowerShell cmdlets are Verb-Noun): with no shell they are not programs.
_CMD_BUILTINS = frozenset("""assoc call cd chdir cls color copy date del dir echo endlocal erase exit for ftype goto
if md mkdir mklink move path pause popd prompt pushd rd ren rename rmdir set setlocal shift start time title
type ver verify vol""".split())
_CMDLET = re.compile(r"^[A-Za-z]+-[A-Za-z]+$")


def _needs_shell(word: str) -> bool:
    base = re.split(r"[\\/]", str(word))[-1].lower()
    base = base[:-4] if base.endswith((".exe", ".cmd", ".bat", ".com")) else base
    return word.lower() in _CMD_BUILTINS or bool(_CMDLET.match(word)) or base in _SHELLS


def _copy_pair(argv: list[str]) -> tuple[str, str] | None:
    """``(from, to)`` for ``cp a b``, ``copy a b``, ``Copy-Item a b`` or ``Copy-Item -Path a -Destination b``."""
    head = argv[0].lower()
    if head in ("cp", "copy"):
        flags = [a for a in argv[1:] if a.startswith("-") or a.startswith("/")]
        rest = [a for a in argv[1:] if a not in flags]
        if len(rest) == 2 and all(f.lower() in ("-f", "-n", "-p", "/y") for f in flags):
            return rest[0], rest[1]
        return None
    if head != "copy-item":
        return None
    named: dict[str, str] = {}
    rest: list[str] = []
    words = argv[1:]
    i = 0
    while i < len(words):
        w = words[i]
        low = w.lower()
        if low in ("-path", "-literalpath", "-destination") and i + 1 < len(words):
            named["dst" if low == "-destination" else "src"] = words[i + 1]
            i += 2
            continue
        if low in ("-force",):
            i += 1
            continue
        if w.startswith("-"):
            return None                                 # -Recurse, -Filter ...: not one plain file copy
        rest.append(w)
        i += 1
    src = named.get("src") or (rest.pop(0) if rest else None)
    dst = named.get("dst") or (rest.pop(0) if rest else None)
    return (src, dst) if src and dst and not rest else None


def _setup_item(step: dict, port: int) -> tuple[Any, set[str]] | None:
    """One setup step (D51): ``{"copy": [a, b]}``, ``{"venv": True}`` or an argument list."""
    argv, cwd = step["argv"], step["cwd"]
    pair = _copy_pair(argv)
    if pair is not None:
        src, dst = pair
        a = _inside(posixpath.join(cwd, src.replace("\\", "/")) if cwd else src)
        b = _inside(posixpath.join(cwd, dst.replace("\\", "/")) if cwd else dst)
        if not a or not b:
            return None
        return {"copy": [a, b]}, set()
    if _PYTHON.match(argv[0]) and argv[1:3] == ["-m", "venv"] and len(argv) == 4 \
            and _inside(posixpath.join(cwd, argv[3]) if cwd else argv[3]) == ".venv":
        return {"venv": True}, {"python"}
    if _needs_shell(argv[0]):
        return None
    out, used = _argv0([_port(a, port) for a in argv])
    if not all(_placeheld(a) for a in out):
        return None
    return out, used


def run_block(start_line: str, setup_lines: list[str], port: int) -> tuple[dict, list[str], list[str]]:
    """``(run block, notes, unsaved lines)`` for a native try from what really ran (D51).

    Raises :class:`Unsaved` when the start line itself can't be written (never a guessed start)."""
    notes: list[str] = []
    unsaved: list[str] = []
    try:
        start = parse_line(start_line, port) if start_line else None
    except Unsaved as exc:
        raise Unsaved(f"{exc.reason} The line: {start_line[:160]}", [start_line]) from None
    if start is None:
        why = ("Toolsmith did not start it with start_server, so there is no start line to save."
               if not start_line else
               f"Its start line needs a shell, and the Core starts apps with none, so it was not saved: {start_line[:160]}")
        raise Unsaved(why, [start_line] if start_line else [])
    cwd = start["cwd"]
    env: dict[str, str] = {}
    setup: list[Any] = []
    used: set[str] = set()

    def keep(step: dict, line: str, what: str) -> None:
        nonlocal used
        item = _setup_item(step, port)
        if item is None or (isinstance(item[0], list) and step["cwd"] != cwd):
            notes.append(f"{what} can't be written for the Core to run with no shell, so it is not in its "
                         f"puffpai.json: {line[:160]}")
            if line not in unsaved:
                unsaved.append(line)
            return
        if item[0] not in setup:
            setup.append(item[0])
        used |= item[1]
    for line in setup_lines:
        try:
            got = parse_line(line, port)
        except Unsaved as exc:
            notes.append(f"{exc.reason} The line: {line[:160]}")
            unsaved.append(line)
            continue
        if got is None:
            notes.append(f"This setup step needs a shell, so it is not in its puffpai.json: {line[:160]}")
            unsaved.append(line)
            continue
        env.update(got["env"])
        for step in got["steps"]:
            keep(step, line, "This setup step")
    for step in start["steps"][:-1]:            # what came before the && in the start line
        keep(step, start_line, "Part of its start line")
    last = start["steps"][-1]
    if _needs_shell(last["argv"][0]):
        raise Unsaved(f"Its start line is a shell command, and the Core starts apps with no shell, so it was not "
                      f"saved: {start_line[:160]}", [start_line])
    argv, more = _argv0([_port(a, port) for a in last["argv"]])
    if not all(_placeheld(a) for a in argv):
        raise Unsaved(f"Its start line holds braces the Core does not fill, so it was not saved: {start_line[:160]}",
                      [start_line])
    used |= more
    env.update(start["env"])
    env = {"PORT": "{port}", "HOST": "127.0.0.1", **env}       # what the try ran with
    env = {k: v for k, v in env.items() if _placeheld(v)}
    block: dict[str, Any] = {"type": "command"}
    if "python" in used:
        block["python"] = True
    if "node" in used:
        block["node"] = True
    if setup:
        block["setup"] = setup
    block["start"] = argv
    block["env"] = env
    if cwd:
        block["cwd"] = cwd
    return block, notes, unsaved


def hosts_seen(folder: Path) -> list[str]:
    out: list[str] = []
    for name in _SETTING_FILES:
        p = Path(folder) / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            continue
        for host in _URL_HOST.findall(text):
            host = host.lower()
            if host not in ("localhost",) and not host.startswith("127.") and host not in out:
                out.append(host)
    return out[:20]


def _version(folder: Path) -> str:
    try:
        v = str(json.loads((Path(folder) / "package.json").read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError, AttributeError):
        v = ""
    return v if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", v) else "0.1.0"


def run_key() -> str:
    """The run block an adopted app gets: it was proved on this computer, so it is for this kind of computer (D132)."""
    return "windows" if os.name == "nt" else ("mac" if sys.platform == "darwin" else "linux")


def _is_setup(line: str) -> bool:
    """A command that set the app up (an install or build, a copy, a venv)."""
    words = _split(line) or []
    return bool(is_build(line) or "venv" in words or any(w in ("cp", "copy") for w in words))


def manifest_for(t: Any) -> dict:
    """``puffpai.json`` for a try whose ``done`` passed."""
    c, r = t.cand, t.result
    name = str(c.get("name") or t.pkg_id)[:60]
    if r.get("skill"):                         # D152: text the brain reads (D149). Nothing runs, so no run block.
        return {"schema": "puffpai-package/1", "id": t.pkg_id, "kind": "skill", "name": name, "version": "0.1.0",
                "description": f"Built by Toolsmith: {str(c.get('description') or name).strip()}"[:300],
                "author": "Toolsmith", "license": "Built by Toolsmith", "origin": origin_of(c),
                "consent": f"{name} is a skill Toolsmith wrote for this goal. It is text your bot's brain reads. It runs "
                           "nothing by itself, and a command it asks for still asks you first."}
    server = r.get("mcp") or []                # D124: a service with no page, reached over its MCP server
    if t.compose_project:
        run = {run_key(): {"type": "docker_compose", "file": t.compose_files[0]}}
        page = f"http://127.0.0.1:{r['port']}{r['path']}"
        runtime = "docker"
    else:
        # An MCP server by itself: nothing stays running, so its block has only its setup (the Core starts it on demand).
        start_line = t.started_lines[-1] if t.started_lines else join_words(server)
        setup_lines = [x["command"] for x in t.ran
                       if x["ok"] and not read_only(x["command"]) and _is_setup(x["command"])]
        block, notes, unsaved = run_block(start_line, setup_lines, t.port)
        t.notes.extend(n for n in notes if n not in t.notes)
        t.unsaved = list(unsaved)
        if server and not t.started_lines:
            del block["start"], block["env"]
            block.setdefault("setup", [])
        run = {run_key(): block}
        page = "" if not r["port"] else f"http://127.0.0.1:{{port}}{r['path']}" if r["port"] == t.port else f"http://127.0.0.1:{r['port']}{r['path']}"
        runtime = c.get("runtime") or ("python" if block.get("python") else "node")
    built = made(c)
    owner = "Toolsmith" if built else c["full_name"].split("/")[0]
    home = project_host(c)
    hosts = hosts_seen(t.folder)
    where = "in Docker behind the Bridge" if t.compose_project else "as a program on this computer, as you"
    manifest = {
        "schema": "puffpai-package/1", "id": t.pkg_id, "kind": "app", "name": name,
        "version": _version(t.folder),
        "description": (f"Built by Toolsmith: {str(c.get('description') or name).strip()}" if built else
                        str(c.get("description") or "").strip()
                        or f"{name}: an app from {'Codeberg' if home == 'codeberg.org' else 'GitHub'}.")[:300],
        "author": owner, "license": "Built by Toolsmith" if built else str(c.get("license") or "See its repository"),
        "homepage": c["url"], "origin": origin_of(c),
        "run": run,
        "health": {"url": page, "expect": 200, "every_s": 60},
        "ui": {"pages": [{"id": "main", "title": name, "url": page, "show": "new_tab"}],
               "tray": [{"label": name, "opens": "main"}]},
        "needs": {"hosts": hosts, "install_hosts": ([] if built else [home]) + INSTALL_HOSTS.get(runtime, []),
                  **({"hardware": {"docker": True}} if t.compose_project else {})},
        "setup_steps": [s for s in t.human_steps],
        "consent": ((f"{name} is a small app Toolsmith built for this job. It runs {where}" if built else
                     f"{name} is an outside app from {c['url']}. It runs {where}")
                    + (f", and may reach {', '.join(hosts[:5])}." if hosts else ", and declared no outside hosts.")),
    }
    if not manifest["setup_steps"]:
        del manifest["setup_steps"]
    if not manifest["homepage"]:
        del manifest["homepage"]
    if server:                                 # no page, so no ui.pages entry that opens nothing
        words, used = _argv0([_port(a, t.port) for a in server])
        if not (built and page):               # D152: a package Toolsmith built has its page and its tools both
            del manifest["ui"]
            manifest["kind"] = "tool"
        manifest["abilities"] = {"mcp": {"transport": "stdio", "command": words}}
        if "python" in used and run[run_key()].get("type") == "command":
            run[run_key()]["python"] = True
        if not page:                           # what really shows it works is its tools listed (Caretaker, the brain)
            manifest["health"] = {"file": "{pkg}/.buildbot/puffpai.json", "every_s": 60}
        manifest["consent"] = manifest["consent"].replace(
            f"It runs {where}", f"It runs {where}, and your bot's brain can use its tools" if built and page
            else f"It runs {where}, with no page, and your bot's brain can use its tools")
    return manifest


def unsaved_step(t: Any, lines: list[str], reason: str) -> None:
    """A Checklist step naming exactly what could not be saved (never a quiet gap). Best effort."""
    if not lines:
        return
    name = str(t.cand.get("name") or t.pkg_id or "this app")[:60]
    step = {"id": f"ts-unsaved-{str(getattr(t, 'pkg_id', '') or 'app')[:40]}", "kind": "confirm",
            "title": f"Check how {name} starts"[:120],
            "why": (f"{reason} Toolsmith got {name} working with the lines below, but the Core runs an app's "
                    "steps with no shell, so they are not in its puffpai.json. If it does not start again "
                    "after a restart, run them yourself, or let Toolsmith try another app.")[:2000],
            "text": "\n".join(str(x) for x in lines)[:2000]}
    try:
        t.link.step_add(str(t.job.get("id") or ""), step, t.try_id)
    except Exception:  # noqa: BLE001 - the note on the job still says it
        pass


def _ran_line(t: Any) -> str:
    """How the try really ran, for the line after install (D120)."""
    return "It runs in Docker behind the Bridge." if t.compose_project else tries_line(t.cand)


def install(t: Any, link: Any, manifest: dict | None = None) -> dict:
    """Write the manifest through the Core, then ask the Core to add it. ``{ok, said, manifest, id}``. *manifest*: one
    already made (``assemble.make_app``)."""
    from ...packages import manifest as manifest_mod
    try:
        manifest = manifest or manifest_for(t)
    except Unsaved as exc:
        t.notes.append(exc.reason)
        unsaved_step(t, exc.lines, exc.reason)
        return {"ok": False, "said": exc.reason, "manifest": None}
    if getattr(t, "unsaved", None):
        unsaved_step(t, t.unsaved, "Some of its setup steps could not be saved.")
    problems = manifest_mod.validate(manifest)
    if problems:
        return {"ok": False, "said": "Its puffpai.json is not valid: " + problems[0], "manifest": manifest}
    # The Core writes it as .buildbot/puffpai.json itself (the only manifest an adopted app runs from).
    got = link.try_add(t.try_id, getattr(t, "built_with", "local"), manifest)
    if got.get("ok") and got.get("waiting"):
        t.closed = True                         # the Package Manager has it now; the try folder is gone
        name = str(manifest.get("name") or t.pkg_id)
        covered = bool(got.get("covered"))
        if covered:
            t.hooks.status("building", f"Installing {name}"[:120])
            t.hooks.say(f"{name} works. It is installed under the plan you said Yes to (no new card).")
        else:
            t.hooks.status("waiting_yes", f"Your Yes to add {name}"[:120])
            t.hooks.say(f"{name} works. {'Toolsmith built it itself. ' if made(t.cand) else ''}It waits for your Yes on "
                        "its card before it is added.")
        began = t.clock()

        def answered() -> dict | None:
            state = link.try_add_state(t.try_id)
            return None if state.get("ok") and state.get("waiting") else state
        got = t.hooks.wait_for(answered, 7 * 24 * 3600) or {"ok": False, "said": "Stopped before your answer."}
        t.paused_s += t.clock() - began
        if not covered and got.get("ok"):          # her answer on its card, said plainly (D84)
            t.hooks.say(f"You said Yes. {name} is added.")
        elif not covered and got.get("answer") == "no":
            t.hooks.say(f"You said No. {name} is not added.")
        t.hooks.status("building", t.cand["name"])
    if not got.get("ok"):
        return {"ok": False, "said": str(got.get("said") or "the Package Manager said no"), "manifest": manifest}
    t.installed = t.closed = True
    return {"ok": True, "said": f"Installed {manifest['name']}. {_ran_line(t)}",
            "manifest": got.get("manifest") or manifest, "id": str(got.get("id") or manifest["id"])}


__all__ = ["INSTALL_HOSTS", "Unsaved", "adopted_word", "made", "origin_of", "unsaved_step", "hosts_seen", "install", "manifest_for",
           "parse_line", "run_block"]
