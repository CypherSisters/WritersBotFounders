"""Caretaker: health checks, restarts, ``broken`` marks, the self-test and safe mode.

- **Health is probed, never declared.** For every running package Caretaker
  runs its manifest's ``health`` probe (a 127.0.0.1 URL with the status it
  expects, asked with the package's own token, since every package route
  needs it, D48d; a command with the exit code it expects, or a program file that must
  exist; a stdio MCP server's probe is a successful ``list_tools``) every
  ``every_s`` seconds, and records it with ``Registry.set_health``. Caretaker
  is the only caller of ``set_health``.
- **Where it listens, on every tick** (X17): every running package's
  processes are looked up in the listen table each tick. A package listening
  on 0.0.0.0, ``::`` or a LAN address is stopped at once, its row goes
  ``broken``, and a card offers Start again or Remove (the D26 card shape).
  A table that can't be read counts as unknown, never clean: 3 unknowns in a
  row raise a card. Docker packages are not watched here (the Docker wall,
  D30, holds them).
- **The first good probe after a start** reads the package's tools from its
  MCP server into the Registry row's ``abilities`` (``changes`` from the
  manifest's ``abilities.changes``).
- **Cards** go to ``self.cards`` and to each ``on_card`` listener:
  ``{"kind": "exposed" | "listen_unknown" | "tool_words" | "safe_mode", "package", "line", "buttons"}``
  (a ``safe_mode`` card has ``package: None`` and its ``reasons`` and ``done``).
- **Tick hooks:** each function in ``on_tick`` runs after every tick's own work (Toolsmith's
  re-scan of its tries' start-up files, lane L6). One that raises is logged; the next still runs.
- **Restarts:** a package that was running and fails (its process ended, or
  its probe fails after the start grace of 2 minutes) is restarted through
  Steward, up to 3 tries with a growing wait; then it is marked ``broken`` with
  a plain reason. A package the owner stopped is never restarted.
- **Self-test and safe mode** (D28(18), 01 "Core start"): ``selftest()`` runs the Core floor
  checks it knows about and publishes ``core.selftest``; any failure turns on safe mode.
  **``safe_mode(reason)`` is the one Core call for safe mode while the Core runs**: lane L4's
  HookGuard (a change to the Core's own Python), the Bridge's tamper checks (wired in
  ``wiring``) and lane L6's Toolsmith host all use it. Entering safe mode runs each of
  ``safe_steps`` (the wiring adds: close the Bridge, lock the vault, pause Toolsmith; each returns
  what it did in plain words) and raises one ``safe_mode`` card. The same reason again does
  nothing more (so a vault the owner opens again is not locked again for it). ``in_safe_mode``
  and ``safe_reasons`` say where it stands. Safe mode is never a refusal to start; the Package
  Manager adds nothing new while it is on; Toolsmith refuses every plan, start and agent route;
  opening the Bridge again needs the PIN (D29); the PIN and the recovery code always work. It lasts
  until the Core starts again (the self-test then decides). The effect itself is
  :func:`enter_safe_mode` over :func:`safe_mode_steps`; the ONLY other caller is the last-resort
  fallback in ``zone3.toolsmith_host.ports.Ports.safe_mode`` (no Caretaker, or its call raised).
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .. import seams
from ..bus import Bus, Source
from ..packages import mcp_tools
from ..registry import Registry
from ..steward.steward import Steward, StewardError, fill, run_block

logger = logging.getLogger(__name__)
CORE = Source("core", "core:caretaker")
START_GRACE_S = 120
DEFAULT_EVERY_S = 60
UNKNOWN_LIMIT = 3
EXPOSED_LINE = ("{name} listened on your home network ({where}), so it was stopped. "
                "Start it again, or remove it.")
#: Reason codes other parts pass to ``safe_mode``, in plain words (lane L4's HookGuard).
SAFE_WORDS = {"core_python_hook_changed": "Something changed the Python start-up files BuildBot itself runs."}
SAFE_LINE = ("BuildBot went into safe mode: {why} {done} Your PIN and recovery code still work. "
             "Restart BuildBot to check again.")
UNKNOWN_LINE = ("BuildBot could not see where {name} listens, {n} times in a row, so it can't tell "
                "whether it is open to your home network. Stop it, or keep it running.")
#: A live tool description Drawbridge flagged (lane I8a): it was dropped, and the package waits for her review.
TOOL_WORDS_LINE = ("{name} describes its tools ({tools}) in words aimed at an AI or at your answers, so "
                   "Drawbridge dropped those words and {name} waits for you. Stop it, or keep it running.")


class Caretaker:
    def __init__(self, registry: Registry, steward: Steward, bus: Bus, *, tries: int = 3, backoff_s: float = 5.0,
                 start_grace_s: float = START_GRACE_S, tick_s: float = 1.0) -> None:
        self.registry = registry
        self.steward = steward
        self.bus = bus
        self.tries = tries
        self.backoff_s = backoff_s
        self.start_grace_s = start_grace_s
        self.tick_s = tick_s
        self._selftest_reasons: list[str] = []
        self._runtime_reasons: list[str] = []
        self._safe_lock = threading.Lock()
        self.safe_steps: list[Callable[[], str | None]] = []   # what entering safe mode does (wiring)
        self.on_tick: list[Callable[[], None]] = []
        self._fails: dict[str, int] = {}
        self._next: dict[str, float] = {}
        self._fresh: dict[str, float] = {}      # id -> start time not yet checked after start
        self._unknown: dict[str, int] = {}      # id -> listen-table reads in a row that failed
        self.cards: list[dict] = []
        self.on_card: list[Callable[[dict], None]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        steward.on_start.append(self.started)
        steward.on_broken.append(self.mark_broken)     # a start refused for a changed manifest (its reason)

    # -- one probe ----------------------------------------------------------
    def probe(self, id: str) -> tuple[bool, str]:
        """``(ok, plain reason)`` for one package right now."""
        info = self.steward.info(id)
        if info is None or not self.steward.alive(id):
            return False, "It is not running."
        manifest = self.steward.read_manifest(id)          # the Core's one reader (her Yes, by its sha256)
        if manifest is None:
            return False, "Its settings file can't be used any more."
        if info.kind == "static":
            block = run_block(manifest) or {}
            ok = (self.steward.folder(id) / block.get("dir", "")).is_dir()
            return ok, "Its files are served by the Core." if ok else "Its folder of files is missing."
        folder = self.steward.folder(id)
        values = self.steward.values(id, folder, run_block(manifest) or {}, info.port)
        health = manifest.get("health")
        if health and "url" in health:
            url = fill(health["url"], values)
            want = health.get("expect", 200)
            got = _http_status(url, info.token)
            if got == want:
                return True, f"Its page answers ({got})."
            return False, f"Its health page answered {got}, not {want}." if got else "Its health page does not answer."
        if health and "command" in health:
            code = self.steward.run_check_command(id, health["command"])
            want = health.get("expect_exit", 0)
            return (code == want), ("Its health check passed." if code == want else f"Its health check ended with {code}, not {want}.")
        if health and "file" in health:
            ok = Path(fill(health["file"], values)).is_file()
            return ok, "Its program file is there." if ok else "Its program file is missing."
        ep = mcp_tools.endpoint(manifest, values)
        if ep and ep[0] == "stdio":
            try:
                mcp_tools.list_tools(manifest, values, info.token, cwd=folder)
                return True, "Its MCP server lists its tools."
            except mcp_tools.McpError as exc:
                return False, str(exc)
        return False, "It has no health check, so it can't show as running."

    def mark_broken(self, id: str, reason: str) -> None:
        if self.registry.get(id) is None:
            return
        self.registry.set_health(id, False, reason)
        self.registry.update(id, status="broken")

    def started(self, id: str) -> None:
        """Steward started *id*: probe it soon, and run the after-start work on its first good probe."""
        self._fresh[id] = time.monotonic()
        self._next[id] = 0.0

    # -- the check for one package -----------------------------------------
    def check(self, id: str) -> dict | None:
        row = self.registry.get(id)
        if row is None or row["status"] not in ("running",):
            return row
        ok, reason = self.probe(id)
        row = self.registry.get(id)
        if row is None or row["status"] != "running":       # the reader marked it broken (a changed file)
            return row
        fresh = self._fresh.get(id)
        if ok:
            self._fails.pop(id, None)
            self.registry.set_health(id, True, reason)
            if fresh is not None:
                self._fresh.pop(id, None)
                if self._exposed(id):
                    return self.registry.get(id)
                self.refresh_tools(id)
            return self.registry.get(id)
        alive = self.steward.alive(id)
        # D132: Steward says "started" half a second after the row reads running. A check in that gap is no failed start:
        # restarting it there killed the app, and then Steward's own look at the first start stopped the second.
        just_started = fresh is None and time.time() - float(getattr(self.steward.info(id), "started_at", 0)) < self.start_grace_s
        if alive and (just_started or (fresh is not None and time.monotonic() - fresh < self.start_grace_s)):
            self.registry.set_health(id, False, "Starting.")
            return self.registry.get(id)
        self.registry.set_health(id, False, reason)
        fails = self._fails.get(id, 0) + 1
        self._fails[id] = fails
        if fails > self.tries:
            self._fails.pop(id, None)
            self.steward.stop(id, status=None)
            self.mark_broken(id, f"Stopped after {self.tries} restarts. Last problem: {reason}")
            return self.registry.get(id)
        try:
            self.steward.restart(id)
            self.started(id)
            self._next[id] = time.monotonic() + min(self.backoff_s * (2 ** (fails - 1)), 60)
        except StewardError as exc:
            self.mark_broken(id, str(exc))
        return self.registry.get(id)

    def _card(self, kind: str, id: str, line: str, buttons: list[str]) -> dict:
        card = {"kind": kind, "package": id, "line": line, "buttons": buttons}
        self.cards.append(card)
        self.steward.say(id, line)
        for fn in list(self.on_card):
            try:
                fn(card)
            except Exception:
                logger.exception("caretaker: a card listener failed")
        return card

    def _name(self, id: str) -> str:
        return (self.registry.get(id) or {}).get("name") or id

    def _exposed(self, id: str) -> bool:
        """X17: stop a package that listens beyond 127.0.0.1. True when it was stopped.

        A listen table that can't be read (or a job whose processes can't be listed) is unknown:
        it counts toward the 3-in-a-row card, never as clean."""
        from ..steward import ports
        info = self.steward.info(id)
        if info is None or info.job is None:
            return False                                  # static or Docker: no process of its own here
        try:
            pids = self.steward.pids(id)
        except OSError:                                   # Linux or Mac: the process table can't be read
            pids = []
        listeners = ports.job_listeners(pids) if pids else None
        if listeners is None:
            if not self.steward.alive(id):
                return False
            n = self._unknown.get(id, 0) + 1
            self._unknown[id] = n
            if n == UNKNOWN_LIMIT:
                self._card("listen_unknown", id, UNKNOWN_LINE.format(name=self._name(id), n=n),
                           ["stop", "keep_running"])
            return False
        self._unknown.pop(id, None)
        found = [f"{a}:{p}" for a, p in listeners if ports.network_reachable([a])]
        if not found:
            return False
        self.steward.stop(id, "it listened beyond 127.0.0.1", status=None)
        self.mark_broken(id, "It listened on all networks (" + ", ".join(found) + "), so it was stopped. "
                             "Packages may listen on 127.0.0.1 only.")
        self._card("exposed", id, EXPOSED_LINE.format(name=self._name(id), where=", ".join(found)),
                   ["start_again", "remove"])
        return True

    def watch_listeners(self) -> list[str]:
        """One pass of the listen watch over every running package. The ids it stopped."""
        stopped = []
        for row in self.registry.list(status="running"):
            try:
                if self._exposed(row["id"]):
                    stopped.append(row["id"])
            except Exception:
                logger.exception("caretaker: listen check of %s failed", row["id"])
        return stopped

    def refresh_tools(self, id: str) -> list[dict]:
        """Read the tools from the package's MCP server into its Registry row."""
        info = self.steward.info(id)
        manifest = self.steward.read_manifest(id)
        if manifest is None or info is None or not (manifest.get("abilities") or {}).get("mcp"):
            return []
        folder = self.steward.folder(id)
        values = self.steward.values(id, folder, run_block(manifest) or {}, info.port)
        try:
            tools = mcp_tools.list_tools(manifest, values, info.token, cwd=folder)
        except mcp_tools.McpError as exc:
            self.steward.say(id, f"tools not read: {exc}")
            return []
        changes = set((manifest.get("abilities") or {}).get("changes", []))
        tools = [t for t in tools if _good_tool_name(t["name"])]
        # Drawbridge screens the live descriptions as it screens a manifest's words (lane I8a). A description
        # aimed at an AI or at her answers is dropped, and the package waits for her review with one card. A
        # screen that is not here drops every description (fail closed: nothing unscreened reaches a card).
        screen = seams.find("zone3.drawbridge.screen_tool_words")
        try:
            flags = screen(tools) if screen is not None else None
        except Exception:  # noqa: BLE001 - a screen that breaks: nothing unscreened is kept
            logger.exception("caretaker: tool words of %s not screened", id)
            flags = None
        if flags is None:
            flags = {t["name"]: {"why": "its words could not be checked", "review": False, "drop": True}
                     for t in tools}
        dropped = {n for n, f in flags.items() if f.get("review") or f.get("drop")}
        abilities = [{"tool": f"{id}.{t['name']}", "description": "" if t["name"] in dropped else t["description"],
                      "changes": t["name"] in changes, "needs_yes": t["name"] in changes}
                     for t in tools]
        self.registry.update(id, abilities=abilities)
        review = sorted(n for n, f in flags.items() if f.get("review"))
        if review:
            line = TOOL_WORDS_LINE.format(name=self._name(id), tools=", ".join(review[:5]))
            self.registry.set_health(id, False, line)
            self.registry.update(id, status="needs_you")
            self._card("tool_words", id, line, ["stop", "keep_running"])
        return abilities

    # -- the loop -----------------------------------------------------------
    def tick(self) -> None:
        try:
            self._tick()
        finally:
            for fn in list(self.on_tick):
                try:
                    fn()
                except Exception:
                    logger.exception("caretaker: a tick hook failed")

    def _tick(self) -> None:
        self.watch_listeners()
        now = time.monotonic()
        for row in self.registry.list(status="running"):
            id = row["id"]
            if now < self._next.get(id, 0.0):
                continue
            manifest = self.steward.read_manifest(id)
            every = ((manifest or {}).get("health") or {}).get("every_s", DEFAULT_EVERY_S)
            fresh = id in self._fresh
            self._next[id] = now + (2.0 if fresh else every)
            try:
                self.check(id)
            except Exception:
                logger.exception("caretaker: check of %s failed", id)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(self.tick_s):
                self.tick()
        self._thread = threading.Thread(target=loop, name="caretaker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    # -- safe mode ------------------------------------------------------------
    @property
    def safe_reasons(self) -> list[str]:
        out: list[str] = []
        for r in self._selftest_reasons + self._runtime_reasons:
            if r not in out:
                out.append(r)
        return out

    @property
    def in_safe_mode(self) -> bool:
        return bool(self._selftest_reasons or self._runtime_reasons)

    def safe_mode(self, reason: str) -> dict:
        """Safe mode now, for *reason* (plain words). ``{"mode": "safe", "reasons", "done", "new"}``.

        The first time for a reason: every ``safe_steps`` function runs and one ``safe_mode`` card is
        raised. The same reason again changes nothing (``new`` False, ``done`` empty)."""
        why = plain_reason(reason)
        with self._safe_lock:
            new = why not in self.safe_reasons
            if new:
                self._runtime_reasons.append(why)
        done = self._enter(why) if new else []
        return {"mode": "safe", "reasons": self.safe_reasons, "done": done, "new": new}

    def _enter(self, why: str) -> list[str]:
        done, _card = enter_safe_mode(why, self.safe_steps, self.on_card, self.cards)
        return done

    # -- self-test ------------------------------------------------------------
    def selftest(self, checks: dict[str, Callable[[], bool]] | None = None) -> dict:
        """Run the Core floor checks; any failure turns on safe mode. Returns the ``core.selftest`` payload."""
        checks = dict(default_checks(self.bus) if checks is None else checks)
        results: dict[str, str] = {}
        reasons: list[str] = []
        for item, fn in checks.items():
            try:
                ok = fn() is True
            except Exception:
                ok = False
            results[item] = "pass" if ok else "fail"
            if not ok:
                reasons.append(SELFTEST_WORDS.get(item, f"Safety check {item} failed."))
        with self._safe_lock:
            fresh = [r for r in reasons if r not in self.safe_reasons]
            self._selftest_reasons = reasons
        mode = "safe" if self.in_safe_mode else "normal"
        self.bus.publish(Bus.message("core.selftest", {"results": results, "mode": mode}, CORE))
        if fresh:
            self._enter(" ".join(fresh))
        return {"results": results, "mode": mode, "reasons": self.safe_reasons}


def plain_reason(reason: str) -> str:
    """A safe-mode reason code (``SAFE_WORDS``) or plain words, as one short plain line."""
    return " ".join(str(SAFE_WORDS.get(reason, reason) or "").split())[:300] or "A safety check failed."


def enter_safe_mode(why: str, steps: list, listeners: list, cards: list | None = None, *,
                    adds_nothing: bool = True) -> tuple[list[str], dict]:
    """THE effect of entering safe mode, shared by Caretaker and the one fallback (``toolsmith_host.ports``).

    Runs every step (each returns what it did in plain words; one that raises is logged and skipped),
    makes the one ``safe_mode`` card, keeps it on *cards* and gives it to every listener (the wiring's
    listener posts it to the Desktop notices and the terminal). ``adds_nothing``: the Package Manager
    refuses new installs (true when Caretaker is in safe mode). Returns ``(done, card)``."""
    done: list[str] = []
    for fn in list(steps):
        try:
            said = fn()
        except Exception:
            logger.exception("caretaker: a safe-mode step failed")
            continue
        if isinstance(said, str) and said.strip():
            done.append(said.strip())
    if adds_nothing:
        done.append("nothing new is added")
    if done:
        words = "So " + (", ".join(done[:-1]) + " and " if len(done) > 1 else "") + done[-1] + "."
    else:
        words = "Nothing could be closed or locked; stop BuildBot now."
    line = SAFE_LINE.format(why=why if why.endswith(".") else why + ".", done=words)
    logger.warning(line)
    card = {"kind": "safe_mode", "package": None, "line": line, "buttons": [], "reasons": [why], "done": done}
    if cards is not None:
        cards.append(card)
    for fn in list(listeners):
        try:
            fn(card)
        except Exception:
            logger.exception("caretaker: a card listener failed")
    return done, card


def safe_mode_steps(*, zone3: Callable[[], object], state: Path, bus: object,
                    toolsmith: Callable[[], object], reason: Callable[[], str] = lambda: "") -> list:
    """THE safe-mode steps: close the Bridge, lock the vault, pause Toolsmith, close the Developer command
    lines. Each says what it did.

    Used by the wiring (Caretaker's ``safe_steps``) and by the one fallback, so the effect is the same.
    *zone3* and *toolsmith* are looked up when the step runs (either may not be made yet)."""
    def close_bridge() -> str | None:
        z = zone3()
        if z is None:
            return None
        z.bridge.close(by="core")
        return "the Bridge is closed"

    def lock_vault() -> str | None:
        a = seams.archivist(state, bus)
        if a is None or not a.vault.is_set_up():
            return None
        if a.is_open():
            a.lock("safe_mode")
        return "the vault is locked"

    def pause_toolsmith() -> str | None:
        host = toolsmith()
        if host is None or not callable(getattr(host, "pause", None)):
            return None
        return host.pause(reason())

    def close_shells() -> str | None:
        """Every Developer command line in this Core ends, with everything it started (lane i8c)."""
        from .. import devshell
        return "the command lines are closed" if devshell.close_every("safe_mode") else None
    return [close_bridge, lock_vault, pause_toolsmith, close_shells]


SELFTEST_WORDS = {
    "X4": "Drawbridge is not here, so nothing new can be added and nothing leaves your computer.",
    "X10": "The Bus let something other than the Core approve a Yes card.",
}


def _drawbridge_here() -> bool:
    return seams.drawbridge_here()


def _bus_refuses_forged_yes(bus: Bus) -> bool:
    forged = Bus.message("consent.granted", {"consent_id": "selftest", "scope": {"kind": "package", "target": "selftest"},
                                             "factor": "tap"}, Source("package", "selftest"))
    return bus.contract.may_publish("consent.granted", "package") is False and \
        _would_reject(bus, forged)


def _would_reject(bus: Bus, message: dict) -> bool:
    from ..bus.bus import Rejected
    try:
        bus.contract.check(message, "event")
    except Rejected:
        return True
    return False


def default_checks(bus: Bus) -> dict[str, Callable[[], bool]]:
    """The floor items this lane can test at start. Other lanes add theirs (cipher known-answer, X1)."""
    return {"X4": _drawbridge_here, "X10": lambda: _bus_refuses_forged_yes(bus)}


def _http_status(url: str, token: str | None, timeout_s: float = 5) -> int | None:
    """The status a package's health URL answers, asked with its own token (D48d)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (OSError, ValueError):
        return None


def _good_tool_name(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name or ""))


__all__ = ["Caretaker", "default_checks", "enter_safe_mode", "plain_reason", "safe_mode_steps"]
