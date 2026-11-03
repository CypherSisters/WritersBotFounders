"""Steward: runs packages (01 "Steward, in detail"; 02 "How the Core runs a package").

- **No shell.** ``setup`` commands and the ``start`` command run as a program
  with arguments, in the package's folder. A ``setup`` entry is one command
  line, split into arguments (quotes group); placeholders are filled per argument.
  On Windows ``npm`` and ``npx`` (bare, or their ``.cmd`` next to ``node.exe``)
  run as ``node.exe <npm-cli.js>``, and any other ``.cmd`` or ``.bat`` program is
  refused with a plain reason: Windows starts those through ``cmd.exe``
  (``steward.noshell``, D60). Health commands go the same way.
- **Placeholders:** ``{port}``, ``{python}``, ``{node}`` (a real ``node.exe``), ``{data}``, ``{pkg}``.
  ``{python}`` is the package's own ``.venv`` Python when its run block says
  ``"python": true`` (Steward makes the venv from the Core's base interpreter
  before ``setup``); otherwise it is the Core's own interpreter.
- **Environment:** the computer's own (``PATH``, ``SystemRoot``) without any
  ``PUFFPAI_*`` or proxy variable it had, plus ``PUFFPAI_PORT``,
  ``PUFFPAI_DATA``, ``PUFFPAI_API``, ``PUFFPAI_TOKEN`` (a new one each start;
  the old one stops working), the package's keys (lent by Archivist,
  ``seams.lease_for_start``, lane L3; none while that is not built), and
  ``HTTP_PROXY`` / ``HTTPS_PROXY`` = the Bridge proxy. With the Core's Zone 3
  wired (``proxy_env``, lane L4's ``Zone3.package_env``) each start (and each
  setup, phase ``install``) gets a fresh proxy credential for that package;
  without it (a bare ``create_app``, nothing wired), 127.0.0.1:9, where nothing
  listens: no Bridge proxy means no way out, and nothing goes to whatever else
  may listen on 8421.
  Walled processes (``reads: raw`` providers and ``zone3.toolsmith-model``
  adapters, X13, X29) get no token, no keys and no proxy variables.
- **Every process in its own job object**, made kill-on-close, with no console
  window; Stop ends the whole job (D18). One log per package,
  ``<state>\\logs\\<id>.log``.
- **Ports** from 18420-18999 (``steward.ports``).
- ``docker_compose`` runs only behind the Docker wall (D30): the override comes
  from ``zone3.docker_wall.override(manifest)``; without it the package does
  not start. ``static`` packages are served by the Core (``/pkg/<id>/...``).
- On Core start, ``start_saved`` starts again every package that was running
  (``settings\\steward.json`` ``start_again``, default on); on Core stop,
  ``stop_all`` stops them all (D18).
- **Setup runs only what was checked (X24).** For a copied or cloned package
  (not a linked folder): no ``.venv``, ``node_modules``, ``__pycache__`` or
  ``.git`` may be in its folder when setup starts (the Package Manager deleted
  them from staging before the Drawbridge check; they are made by setup); the
  folder's tree hash must still be the one Drawbridge checked; Steward always
  makes the ``.venv`` itself; and a setup step whose program is inside the
  package folder must be Steward's own ``{python}`` or a file of the checked
  tree. Anything else refuses setup.
- **For the Bridge's bypass watch (lane L4):** ``running() -> [id]``,
  ``pids(id) -> [int]`` (every process in the package's job object, the real
  interpreter behind a venv launcher included) and ``stop(id, reason)``
  (``reason`` is plain words for its log, never a status). On Windows a
  program that could not be put in a job object is stopped and not started.
- **It runs the manifest she said Yes to** (lane I2a part D). The Package Manager records the sha256 of
  the package's manifest file in its Registry row (``manifest_sha``) at her install Yes (an update
  records the new one only through its own Yes). ``manifest`` refuses a file that no longer has it
  (:class:`ManifestChanged`); ``start`` then marks the row ``broken`` with a plain reason and starts
  nothing. Built-in packages are checked the same way.
- **git stays inside the package** (``steward.gitfence``): every program gets
  ``GIT_CEILING_DIRECTORIES`` at the package folder's parent, and none of the Core's ``GIT_DIR``-style
  settings.
- **Stop ends its key leases** (D41): ``seams.end_holder`` asks Archivist
  (lane L3) to end every lease held by ``pkg:<id>``; with no Archivist the
  log says so.

Steward never reads package data or the vault, and it never sets health
(Caretaker does).
"""

from __future__ import annotations

import json
import re
import os
import shlex
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import seams
from ..packages import fsutil
from ..packages import manifest as manifest_mod
from ..packages.installs import Installs
from ..registry import Registry
from ..slots import RAW_READER_SLOTS
from . import gitfence, jobs, noshell, ports

if TYPE_CHECKING:
    from ..api.auth import TokenStore

PROXY = "http://127.0.0.1:9"                 # unwired: nowhere (wiring.NO_PROXY_URL); the real proxy comes from proxy_env
SETUP_TIMEOUT_S = 15 * 60
VENV_TIMEOUT_S = 5 * 60
_STRIP_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY")
_NOT_COMMUNITY = ("core", "store:verified", "local:", "made")


class StewardError(RuntimeError):
    """A start, stop or setup that could not be done. The message is plain words."""


#: Said when a package's manifest file is not the one she said Yes to (lane I2a part D).
MANIFEST_CHANGED = manifest_mod.MANIFEST_CHANGED
MANIFEST_UNRECORDED = manifest_mod.MANIFEST_UNRECORDED


class ManifestChanged(StewardError):
    """The package's manifest file no longer has the sha256 recorded at her install Yes."""


@dataclass
class Running:
    id: str
    kind: str                         # command | docker_compose | static
    port: int | None
    token: str | None
    started_at: float
    job: jobs.Job | None = None
    compose: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_block(manifest: dict) -> dict | None:
    """The run block for this computer: ``windows`` (or ``mac`` / ``linux``), else ``default``."""
    run = manifest.get("run") or {}
    here = "windows" if os.name == "nt" else ("mac" if sys.platform == "darwin" else "linux")
    return run.get(here) or run.get("default")


def base_python() -> str:
    """The Core's base interpreter (not its venv's launcher), for making package venvs."""
    return getattr(sys, "_base_executable", None) or sys.executable


def venv_python(folder: Path) -> Path:
    return folder / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def fill(text: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


def split_command(line: str) -> list[str]:
    """One setup command line as arguments: spaces split, quotes group, backslashes stay."""
    out = []
    for part in shlex.split(line, posix=False):
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            part = part[1:-1]
        out.append(part)
    return out


#: Names a run block's ``env`` may never set (D51): the Core's own, the Bridge's, and system settings that
#: change which programs run or how.
_BLOCK_ENV_NEVER = re.compile(r"(?i)^(?:PUFFPAI_.*|PIP_.*|.*_?PROXY|NODE_USE_ENV_PROXY|NPM_CONFIG_.*PROXY|PATH|PATHEXT|COMSPEC|"
                              r"SYSTEMROOT|WINDIR|PYTHONPATH|PYTHONHOME|PYTHONSTARTUP|NODE_OPTIONS|NODE_PATH|"
                              r"LD_PRELOAD|LD_LIBRARY_PATH|DYLD_.*)$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def block_env(block: dict, values: dict[str, str]) -> dict[str, str]:
    """A command run block's own ``env`` (D51), placeholders filled; names it may not set are left out."""
    out = {}
    for name, value in (block.get("env") or {}).items() if isinstance(block.get("env"), dict) else []:
        if isinstance(name, str) and _ENV_NAME.match(name) and not _BLOCK_ENV_NEVER.match(name) \
                and isinstance(value, (str, int, float)):
            out[name] = fill(str(value), values)
    return out


def block_cwd(folder: Path, block: dict) -> Path:
    """The folder start and argument-list setup steps run in (D51 ``cwd``): inside the package, or an error."""
    rel = str(block.get("cwd") or "").replace("\\", "/").strip("/")
    if not rel:
        return folder
    where = (folder / rel).resolve()
    root = folder.resolve()
    if where != root and root not in where.parents or not where.is_dir() or fsutil.is_link(folder / rel):
        raise StewardError(f"Its run block names a folder ({rel}) that is not inside it.")
    return where


def setup_argv(item: Any) -> list[str] | None:
    """One setup item as arguments (a command line or an argument list, D51); None for a Core file step."""
    if isinstance(item, str):
        return split_command(item)
    if isinstance(item, list):
        return [str(x) for x in item]
    return None


def walled(manifest: dict) -> bool:
    """A raw reader or a Toolsmith model adapter: no token, no keys, no proxy (X13, X29)."""
    if manifest.get("kind") != "provider":
        return False
    slots = {p.get("slot") for p in manifest.get("provides", [])}
    return manifest.get("reads") == "raw" or bool(slots & RAW_READER_SLOTS) or "zone3.toolsmith-model" in slots


def community(origin: str) -> bool:
    return not any(origin == o or (o.endswith(":") and origin.startswith(o)) for o in _NOT_COMMUNITY)


class Steward:
    def __init__(self, state: Path | str, registry: Registry, tokens: "TokenStore", installs: Installs,
                 core_api: str = "http://127.0.0.1:8420", bus=None) -> None:
        self.state = Path(state)
        self.bus = bus
        self.registry = registry
        self.tokens = tokens
        self.installs = installs
        self.core_api = core_api
        self.logs = self.state / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self._running: dict[str, Running] = {}
        self._lock = threading.RLock()
        self.on_start: list = []                 # fn(id) after each good start (Caretaker listens)
        self.on_broken: list = []                # fn(id, reason): a start refused for a changed manifest
        self.proxy_env = None                    # fn(id, phase) -> proxy variables (lane L4's Zone3.package_env)
        self.docker_wall_kw = None               # fn(id) -> extra arguments for the Docker wall (the proxy's port)

    # -- what is where ------------------------------------------------------
    def folder(self, id: str) -> Path:
        folder = self.installs.folder(id)
        if folder is None or not folder.is_dir():
            raise StewardError(f"The files of {id!r} are not where they were installed.")
        return folder

    def manifest(self, id: str) -> dict:
        """The manifest Steward runs: for an adopted app (its Registry origin) only the one the Core wrote
        (``.buildbot/puffpai.json``), never a ``puffpai.json`` the downloaded project shipped.

        Only while the file still has the sha256 recorded in the Registry row at her install Yes
        (``manifest_sha``); else :class:`ManifestChanged`. A row with no sha recorded counts as changed."""
        row = self.registry.get(id) if self.registry is not None else None
        if row is None:
            raise StewardError(f"Nothing installed with id {id!r}.")
        try:
            return manifest_mod.installed(row, self.folder(id))      # the Core's one reader (lane I2a)
        except manifest_mod.NotAsApproved as exc:
            raise ManifestChanged(exc.problems[0] if exc.problems else MANIFEST_CHANGED) from None

    def read_manifest(self, id: str) -> dict | None:
        """The same manifest for every part of the Core that only looks (the brain's tool table and its MCP
        client, read_app, pages, ``ui.open``, Caretaker, Drawbridge's scope): the manifest, or None. A file
        that is not the one she said Yes to makes the row broken (with its reason, and stopped if it ran),
        so nothing it names is started or offered."""
        return manifest_mod.read_installed(self.registry, self.installs, id, broken=self._changed_since_yes)

    def _changed_since_yes(self, id: str, reason: str) -> None:
        row = self.registry.get(id)
        if row is None or row.get("status") == "broken":
            return
        with self._lock:
            if id in self._running:
                self.stop(id, "its settings file changed since the owner's Yes", status=None)
        self._manifest_broken(id, reason)

    def _manifest_broken(self, id: str, reason: str) -> None:
        """Mark *id* broken with *reason* (Caretaker's ``mark_broken`` when it listens), nothing else."""
        self.say(id, f"not started: {reason}")
        if self.on_broken:
            for fn in list(self.on_broken):
                fn(id, reason)
        elif self.registry.get(id) is not None:
            self.registry.update(id, status="broken")

    def data_dir(self, id: str) -> Path:
        path = self.state / "data" / id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def log_path(self, id: str) -> Path:
        return self.logs / f"{id}.log"

    def say(self, id: str, line: str) -> None:
        with open(self.log_path(id), "a", encoding="utf-8") as f:
            f.write(f"[{_now()}] puffpai: {line}\n")

    def values(self, id: str, folder: Path, block: dict, port: int | None) -> dict[str, str]:
        python = venv_python(folder) if block.get("python") else Path(sys.executable)
        return {"port": str(port or ""), "python": str(python), "node": noshell.node_exe() or "node",
                "data": str(self.data_dir(id)), "pkg": str(folder)}

    def env(self, id: str, manifest: dict, port: int | None, token: str | None, keys: dict[str, str],
            phase: str = "run") -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith("PUFFPAI_") and k.upper() not in _STRIP_ENV}
        env.update({"PUFFPAI_DATA": str(self.data_dir(id)), "PUFFPAI_API": self.core_api,
                    "PIP_REQUIRE_VIRTUALENV": "1"})       # pip never installs into the owner's own Python
        if port:
            env["PUFFPAI_PORT"] = str(port)
        if not walled(manifest):
            if token:
                env["PUFFPAI_TOKEN"] = token
            env.update(keys)
            if self.proxy_env is not None:
                env.update(self.proxy_env(id, phase))
            else:
                env.update({"HTTP_PROXY": PROXY, "HTTPS_PROXY": PROXY, "http_proxy": PROXY, "https_proxy": PROXY,
                            "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1"})
        # Last, so nothing above can change it: git never looks above the package's folder (gitfence).
        return gitfence.fence(env, self.installs.folder(id) or self.state / "packages" / id)

    # -- install-time -------------------------------------------------------
    def _linked(self, id: str) -> bool:
        return bool((self.installs.get(id) or {}).get("link"))

    def setup(self, id: str, expect_tree: str | None = None) -> None:
        """Make the venv (``"python": true``) and run each ``setup`` command, in the package's final folder.

        *expect_tree* is the tree hash Drawbridge checked: setup refuses when the folder no longer
        has it (X24). Pass None only for a package that was never checked (none today)."""
        folder = self.folder(id)
        linked = self._linked(id)
        if not linked:
            found = fsutil.regenerated(folder)
            if found:
                raise StewardError("Files appeared in its folder after its check (" + ", ".join(found[:5])
                                   + "), so setup did not run. Add it again.")
        if expect_tree is not None and fsutil.tree_hash(folder) != expect_tree:
            raise StewardError("Its files changed after its check, so setup did not run. Add it again.")
        manifest = self.manifest(id)
        block = run_block(manifest)
        if not block or block.get("type") != "command":
            return
        log = self.log_path(id)
        env = self.env(id, manifest, None, None, {}, phase="install")
        if block.get("python") and (not linked or not venv_python(folder).is_file()):
            self.say(id, "making its own Python (.venv)")
            argv = [base_python(), "-m", "venv"] + ([] if linked else ["--clear"]) + [str(folder / ".venv")]
            code = jobs.run(argv, folder, env, log, VENV_TIMEOUT_S)
            if code != 0:
                raise StewardError(f"Its own Python (.venv) could not be made (exit {code}). See its log.")
        values = self.values(id, folder, block, None)
        env.update(block_env(block, values))
        cwd = block_cwd(folder, block)
        for i, line in enumerate(block.get("setup", []), 1):
            parts = setup_argv(line)
            if parts is None:                          # a Core file step (D51): {"copy": [a, b]} or {"venv": true}
                self._file_step(id, folder, block, line, i, env, log)
                continue
            try:
                argv = noshell.resolve([fill(part, values) for part in parts])
            except noshell.NoShell as exc:
                raise StewardError(f"Setup step {i} ({line}) was not run: {exc.reason}") from None
            if not linked:
                problem = _setup_program_problem(argv, folder, values, block)
                if problem:
                    raise StewardError(f"Setup step {i} ({line}) {problem}, so setup did not run.")
            self.say(id, f"setup step {i}: {' '.join(argv)}")
            code = jobs.run(argv, cwd, env, log, SETUP_TIMEOUT_S)
            if code != 0:
                why = "it ran out of time" if code == -1 else f"exit {code}"
                raise StewardError(f"Setup step {i} ({line}) failed ({why}). See its log.")

    def _file_step(self, id: str, folder: Path, block: dict, item: Any, i: int, env: dict, log: Path) -> None:
        """``{"venv": true}`` (its own .venv, made once) or ``{"copy": [from, to]}`` inside the package (D51).
        A copy never replaces a file that is already there (her own settings stay hers)."""
        if isinstance(item, dict) and item.get("venv") is True:
            if venv_python(folder).is_file():
                return
            self.say(id, f"setup step {i}: making its own Python (.venv)")
            code = jobs.run([base_python(), "-m", "venv", str(folder / ".venv")], folder, env, log, VENV_TIMEOUT_S)
            if code != 0:
                raise StewardError(f"Setup step {i}: its own Python (.venv) could not be made (exit {code}).")
            return
        pair = item.get("copy") if isinstance(item, dict) else None
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) and x for x in pair)):
            raise StewardError(f"Setup step {i} is not a step the Core knows, so setup did not run.")
        root = folder.resolve()
        src, dst = (folder / pair[0]).resolve(), (folder / pair[1]).resolve()
        for x in (src, dst):
            if x == root or root not in x.parents:
                raise StewardError(f"Setup step {i} copies outside its own folder, so setup did not run.")
        if fsutil.is_link(folder / pair[0]) or not src.is_file():
            raise StewardError(f"Setup step {i}: {pair[0]} is not a file in its folder, so setup did not run.")
        if dst.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        self.say(id, f"setup step {i}: copied {pair[0]} to {pair[1]}")

    # -- start / stop -------------------------------------------------------
    def start(self, id: str) -> dict:
        with self._lock:
            row = self.registry.get(id)
            if row is None:
                raise StewardError(f"Nothing installed with id {id!r}.")
            if self.alive(id):
                return row
            self._running.pop(id, None)
            folder = self.folder(id)
            try:
                manifest = self.manifest(id)
            except ManifestChanged as exc:
                self._manifest_broken(id, str(exc))
                raise
            block = run_block(manifest)
            if block is None:
                return row                                    # nothing to run (skill, avatar, knowledge)
            if block["type"] == "static":
                self._running[id] = Running(id, "static", None, None, time.time())
                return self.registry.update(id, status="running", port=None)
            if block["type"] == "command" and not block.get("start"):
                # D124: only an MCP server over stdio: nothing stays running, and the Core starts it on demand with
                # its token, so the row runs while the token lives.
                self._running[id] = Running(id, "stdio", None, self.tokens.issue_package_token(id), time.time())
                row = self.registry.update(id, status="running", port=None)
                for fn in list(self.on_start):
                    fn(id)
                return self.registry.get(id) or row
            if walled(manifest) and community(row["origin"]) and id not in self._turned_on():
                raise StewardError(f"{row['name']} can see everything it reads and is not walled off. "
                                   "It stays off until you turn it on.")
            others = [r.port for r in self._running.values() if r.port]
            port = ports.free_port(self.state, avoid=others, prefer=row.get("port"))
            token = None if walled(manifest) else self.tokens.issue_package_token(id)
            keys = self._keys(id, manifest)
            env = self.env(id, manifest, port, token, keys)
            values = self.values(id, folder, block, port)
            if block["type"] == "docker_compose":
                running = self._compose_up(id, folder, manifest, block, env, port, token)
            else:
                env.update(block_env(block, values))       # D51: e.g. PORT={port}, HOST=127.0.0.1
                try:
                    argv = noshell.resolve([fill(part, values) for part in block["start"]])
                    cwd = block_cwd(folder, block)
                except noshell.NoShell as exc:
                    self.tokens.revoke_package(id)
                    raise StewardError(f"{row['name']} was not started: {exc.reason}") from None
                except StewardError:
                    self.tokens.revoke_package(id)
                    raise
                self.say(id, f"start on port {port}: {' '.join(argv)}")
                try:
                    job = jobs.Job.spawn(argv, cwd, env, self.log_path(id))
                except OSError as exc:
                    self.tokens.revoke_package(id)
                    raise StewardError(f"{row['name']} could not start: {exc.strerror or exc}.") from None
                if not job.contained:
                    job.terminate()                    # its own handle only (it never ran: jobs._contain)
                    job.close()
                    self.tokens.revoke_package(id)
                    why = (f"{row['name']} could not be held in a job of its own, so it was stopped before "
                           "it ran (BuildBot could not stop or watch all of it).")
                    self.registry.set_health(id, False, why)
                    self.registry.update(id, status="broken")
                    raise StewardError(why)
                running = Running(id, "command", port, token, time.time(), job=job)
            self._running[id] = running
            row = self.registry.update(id, status="running", port=port)
        time.sleep(0.5)
        if running.job is not None and not running.job.alive():
            code = running.job.proc.poll()
            self.stop(id, status="broken")
            raise StewardError(f"{row['name']} stopped right after it started (exit {code}). See its log.")
        for fn in list(self.on_start):
            fn(id)
        return self.registry.get(id) or row

    def _keys(self, id: str, manifest: dict) -> dict[str, str]:
        names = [k["name"] for k in (manifest.get("needs") or {}).get("keys", []) if k.get("hand") != "per_action"]
        if not names or walled(manifest):
            return {}
        try:
            got = seams.lease_for_start(self.state, id, names, self.bus)
        except Exception:
            self.say(id, "keys not added: the vault did not lend them")
            return {}
        if got is None:
            self.say(id, "keys not added: the vault is not built yet")
            return {}
        return {k: str(v) for k, v in got.items() if k in names}

    def _compose_up(self, id: str, folder: Path, manifest: dict, block: dict, env: dict, port: int,
                    token: str | None) -> Running:
        override = seams.find(seams.DOCKER_OVERRIDE)
        if override is None:
            self.tokens.revoke_package(id)
            raise StewardError("Docker apps run only behind the Docker wall, which is not built yet.")
        try:
            # Lane L4's wall reads the app's own compose file and writes the override itself (D30).
            extra = dict(self.docker_wall_kw(id)) if self.docker_wall_kw is not None else {}
            cred = env.get("HTTPS_PROXY") or ""
            if "@" in cred:
                extra.setdefault("proxy_url", cred)        # this start's own proxy credential (proxy_env)
            # The app's compose file is read with this start's values (${PUFFPAI_PORT} and the rest),
            # so the published page port is the one Steward picked.
            out = override(manifest, folder / block["file"], package_dir=folder, env=env, **extra)
        except Exception as exc:  # noqa: BLE001 - the wall refused or could not read the app: it does not start
            self.tokens.revoke_package(id)
            raise StewardError(f"The Docker wall did not let this app start: {exc}") from None
        path = folder / ".buildbot" / "compose.puffpai.yml"
        if isinstance(out, dict):                  # a wall that returns the document instead
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(out, indent=2), encoding="utf-8")   # JSON is valid YAML
        else:
            path = Path(out)
        docker = shutil.which("docker.exe" if jobs.IS_WINDOWS else "docker")     # the program, never a wrapper
        if not docker:
            raise StewardError("Docker is not installed on this computer.")
        compose = [docker, "compose", "-p", f"puffpai-{id}", "-f", block["file"], "-f", str(path)]
        self.say(id, f"docker compose up on port {port}")
        code = jobs.run(compose + ["up", "-d"], folder, env, self.log_path(id), SETUP_TIMEOUT_S)
        if code != 0:
            raise StewardError(f"docker compose up failed (exit {code}). See its log.")
        if extra.get("host_gateway"):              # plain Linux Docker: a firewall can stop the gate reaching the proxy
            self._gate_reaches_proxy(id, compose, folder, env, int(extra["host_proxy_port"]))
        return Running(id, "docker_compose", port, token, time.time(), compose=compose)

    def _gate_reaches_proxy(self, id: str, compose: list[str], folder: Path, env: dict, proxy_port: int) -> None:
        """Say so plainly, and do not leave the app up, when the wall did not hold or its gate can't reach the proxy.
        Fails closed."""
        check = seams.find(seams.DOCKER_GATE_PROBLEM)
        problem = (check(lambda args: jobs.run(compose + args, folder, env, self.log_path(id), 30), proxy_port, id)
                   if check is not None else "The Docker wall's check of its gate is not built, so this app was stopped.")
        if problem:
            jobs.run(compose + ["down", "-t", "1"], folder, env, self.log_path(id), 300)
            self.tokens.revoke_package(id)
            raise StewardError(f"{problem} This app was stopped.")

    def alive(self, id: str) -> bool:
        r = self._running.get(id)
        if r is None:
            return False
        if r.job is not None:
            return r.job.alive()
        return True

    def running(self) -> list[str]:
        """The ids of the packages running now (for the bypass watch, lane L4)."""
        return sorted(id for id in list(self._running) if self.alive(id))

    def info(self, id: str) -> Running | None:
        return self._running.get(id)

    def token(self, id: str) -> str | None:
        r = self._running.get(id)
        return r.token if r else None

    def pids(self, id: str) -> list[int]:
        r = self._running.get(id)
        return r.job.pids() if r and r.job else []

    def stop(self, id: str, reason: str | None = None, *, status: str | None = "stopped") -> bool:
        """Stop *id* and everything it started. True when nothing of it is left running.

        *reason* is plain words for its log (the bypass watch passes one); *status* is the
        Registry status to set afterwards (None leaves it to the caller)."""
        with self._lock:
            r = self._running.pop(id, None)
            clean = True
            if r is not None and r.job is not None:
                clean = r.job.terminate()
                r.job.close()
                self.say(id, ("stopped" if clean else "stop: something is still running")
                          + (f" ({reason})" if reason else ""))
            elif r is not None and r.kind == "docker_compose":
                folder = self.folder(id)
                clean = jobs.run(r.compose + ["down"], folder, self.env(id, {}, None, None, {}),
                                 self.log_path(id), 300) == 0
            self.tokens.revoke_package(id)
            self._end_leases(id)
            if status and self.registry.get(id) is not None:
                self.registry.update(id, status=status)
            return clean

    def _end_leases(self, id: str) -> None:
        """D41: the package stopped, so every key lease it holds ends (Archivist, lane L3)."""
        try:
            ended = seams.end_holder(self.state, id, self.bus)
        except Exception as exc:  # a failing Archivist never stops a stop
            self.say(id, f"key leases: Archivist could not end them ({type(exc).__name__})")
            return
        if ended is None:
            self.say(id, "key leases: none to end (the vault is not built yet)")
        elif ended:
            self.say(id, f"key leases ended: {ended}")

    def restart(self, id: str) -> dict:
        self.stop(id, status=None)
        return self.start(id)

    def run_check_command(self, id: str, argv: list[str], timeout_s: float = 30) -> int:
        """A ``health`` command, in the package's folder, in a job of its own."""
        folder = self.folder(id)
        manifest = self.manifest(id)
        block = run_block(manifest) or {}
        r = self._running.get(id)
        values = self.values(id, folder, block, r.port if r else None)
        env = self.env(id, manifest, r.port if r else None, r.token if r else None, {})
        try:
            argv = noshell.resolve([fill(a, values) for a in argv])
        except noshell.NoShell as exc:
            self.say(id, f"health command not run: {exc.reason}")
            return 1
        return jobs.run(argv, folder, env, self.log_path(id), timeout_s)

    # -- the whole bot ------------------------------------------------------
    def _settings(self) -> dict:
        try:
            return json.loads((self.state / "settings" / "steward.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self, data: dict) -> None:
        path = self.state / "settings" / "steward.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _turned_on(self) -> set[str]:
        return set(self._settings().get("raw_readers_on", []))

    def stop_all(self, remember: bool = True) -> list[str]:
        """Stop every package (Core stop, D18). Remembers which were running, for ``start_saved``."""
        ids = sorted(self._running)
        if remember:
            data = self._settings()
            data["was_running"] = ids
            self._save_settings(data)
        for id in ids:
            self.stop(id)
        return ids

    def start_saved(self, background: bool = True) -> threading.Thread | None:
        """Start again, one at a time, what was running at the last Core stop (or crash)."""
        data = self._settings()
        if data.get("start_again", True) is False:
            return None
        ids = list(dict.fromkeys(list(data.get("was_running", [])) +
                                 [r["id"] for r in self.registry.list(status="running")]))

        def go() -> None:
            for id in ids:
                if self.registry.get(id) is None:
                    continue
                try:
                    self.start(id)
                except ManifestChanged:
                    continue                              # marked broken with its reason by start()
                except Exception as exc:  # one package failing never stops the rest
                    self.say(id, f"not started again: {exc}")
                    if self.registry.get(id) is not None:
                        self.registry.update(id, status="stopped")
        if not background:
            go()
            return None
        t = threading.Thread(target=go, name="steward-start-saved", daemon=True)
        t.start()
        return t


def _setup_program_problem(argv: list[str], folder: Path, values: dict, block: dict) -> str | None:
    """Why a setup step's program may not run, or None. Only for copied and cloned packages.

    A program outside the package folder (``npm``, ``git``, the Core's Python) is the computer's
    own. One inside it must be Steward's own ``{python}`` (``"python": true``) or a file of the
    checked tree (not a link, not in a folder setup makes)."""
    if not argv or not argv[0]:
        return "has no program"
    prog = Path(argv[0])
    if not prog.is_absolute():
        if len(prog.parts) == 1 and not (folder / prog).exists():
            return None                                   # a program on PATH (npm, git)
        prog = folder / prog
    try:
        resolved = prog.resolve()
        root = folder.resolve()
    except OSError:
        return "names a program that can't be read"
    if resolved != root and root not in resolved.parents:
        return None
    if block.get("python") and resolved == venv_python(folder).resolve():
        return None
    rel = resolved.relative_to(root)
    if any(fsutil.is_skip(part) for part in rel.parts) or fsutil.is_link(prog):
        return f"runs {rel.as_posix()}, which was not in the checked files"
    return None


__all__ = ["MANIFEST_CHANGED", "MANIFEST_UNRECORDED", "PROXY", "ManifestChanged", "Running", "Steward",
           "StewardError", "base_python", "community", "fill", "run_block",
           "split_command", "venv_python", "walled"]
