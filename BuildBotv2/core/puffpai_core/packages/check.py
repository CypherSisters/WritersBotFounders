"""``puffpai check <folder>``: the "Works on PuffPAI" levels (02), run on this computer.

The package is copied into a throwaway bot folder, set up and started there
by the same Steward that runs real packages, probed, listed, stopped and
removed, and the throwaway folder is deleted at the end. The owner's folder is
never written (its hash is compared before and after).

Each item is PASS, FAIL, or ``----`` (does not apply, or not checkable yet,
with the reason). A level passes when none of its items FAIL and the level
before it passed. Items not checkable yet:
- 12 (a sample ask): needs ``zone2.brain.sample_ask`` (lane L2). It runs while the package runs in the
  throwaway bot, on the model the owner picked (``model.json`` in her bot's settings folder, read only: its
  name and the address the wizard's scan found), with a privacy gate of the throwaway bot's own. When the
  model can't be asked (no model picked, Ollama not running), item 12 says so plainly and is not a FAIL.

Item 6 (hosts) runs the package under the real Bridge (lane L4): a Zone 3 of its own in the throwaway
bot folder, its proxy on any free port, and the bypass watch. Setup and the run get a proxy credential
for this package, so every outside host it asks for is written to the outside log under its subject
(``pkg:<id>``). The Bridge stays closed, so nothing leaves this computer during a check; the hosts are
recorded, refused. Then the log is read: a host not in ``needs.hosts`` (or, while it installs, not in
``needs.install_hosts`` either) is a FAIL, and so is a connection around the proxy (D26). So is a bypass
watch that was blind to the package or not running at any point during the check ("couldn't watch it,
so this can't be checked"): a quiet PASS would claim what was never seen. Honest: it
sees only what the package does during the check, and an app that needs the internet to start may not
get healthy here.
Item 7 is Drawbridge's verdict; **without Drawbridge it FAILS** (no check means no, X26).

Warnings (D55, never a FAIL): a GET route the package declares (``ui.pages``,
``ui.widgets``, ``health``) whose path names a change (add, set, delete, ...),
since GET and HEAD must never change anything; and an MCP tool whose name
names a change but is not in ``abilities.changes``.

Honest limits: item 5 sees writes in the bot's own folders and the package's
source folder, not the whole disk (02 "Honest note": native packages are not
sandboxed).
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import contrast, seams
from ..registry import Registry, row_from_manifest
from ..steward import ports
from ..steward.steward import Steward, StewardError, run_block
from . import fsutil, mcp_tools
from . import manifest as manifest_mod
from .installs import Installs

PASS, FAIL, NA = "PASS", "FAIL", "----"
LEVELS = {1: "Level 1 Runs", 2: "Level 2 Talks", 3: "Level 3 Belongs"}
TALKS = ("app", "tool", "agent")
SCREENS = ("app", "page", "ui")
# The Core's own files in a bot folder; anything else new is a stray write.
_CORE_FILES = re.compile(r"^(registry\.db.*|tokens\.db.*|consent\.db.*|settings/.*|packages/installs\.json|logs/.*)$")


@dataclass
class Report:
    id: str = ""
    title: str = ""
    items: list[tuple[int, int, str, str]] = field(default_factory=list)   # (level, item, status, words)
    warnings: list[str] = field(default_factory=list)                     # plain lines; never a FAIL (D55)
    notes: list[str] = field(default_factory=list)                        # plain lines; no level reads them (D96)

    def add(self, level: int, item: int, status: str, words: str) -> None:
        self.items.append((level, item, status, words))

    def level_ok(self, level: int) -> bool:
        return all(s != FAIL for lv, _i, s, _w in self.items if lv <= level) and \
            any(lv == level for lv, *_ in self.items)

    def passed(self) -> int:
        """The highest level passed (0 = none)."""
        best = 0
        for level in (1, 2, 3):
            if not self.level_ok(level):
                break
            best = level
        return best

    def lines(self) -> list[str]:
        out = [self.title] if self.title else []
        for level in (1, 2, 3):
            first = True
            for lv, item, status, words in self.items:
                if lv != level:
                    continue
                head = LEVELS[level] if first else ""
                out.append(f"{head:<16} {status}  {item}: {words}")
                first = False
        out.extend(f"Warning: {w}" for w in self.warnings)
        out.extend(self.notes)
        n = self.passed()
        open_items = sum(1 for *_x, s, w in self.items if s == NA and ("not built yet" in w or "not checked" in w))
        if n == 0:
            out.append("Result: does not install. Fix the Level 1 items that FAIL.")
        else:
            if open_items:
                out.append(f"Result: every item that ran passes up to Level {n}, but {open_items} item(s) could not "
                           f"be checked (marked ----), so Level {n} is not fully proven yet.")
            else:
                out.append(f"Result: passes Level {n}.")
        return out


def _snapshot(root: Path) -> set[str]:
    found = set()
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not fsutil.is_link(Path(here) / d)]
        for f in files:
            found.add((Path(here) / f).relative_to(root).as_posix())
    return found


NO_EXAMPLE = ("It has no sample ask in abilities.examples, so its description was asked: add a real request "
              "with its details there, like \"put milk on the shopping list\".")


class CantAsk(Exception):
    """The local model can't be asked, in plain words."""


def _check_brain(brain_dir: Path, registry: Registry, steward: Steward):
    """A Brain for the throwaway bot: its Registry and Steward, the owner's chosen model, its own privacy gate."""
    from ..paths import state_dir
    from ..zone1 import gate as zone1_gate
    from ..zone2 import settings as model_settings
    from ..zone2.brain import Brain
    from ..zone2.gate import Gate
    own = model_settings.load(state_dir())
    if not own.model:
        raise CantAsk("no brain is picked yet; run puffpai setup first")
    model_settings.save(brain_dir, model=own.model, address=own.address, context=own.context,
                        tool_budget=own.tool_budget, timeout_s=own.timeout_s, lan_hosts=list(own.lan_hosts))
    archivist = seams.archivist(brain_dir, None)
    if archivist is None:
        raise CantAsk("the privacy gate is not here")

    def gate() -> Gate:
        # The words sent are the package's own tool descriptions and sample asks: handles only, never values.
        return Gate(prepare=lambda text, _open: zone1_gate.prepare(text, False, archivist=archivist),
                    vault_open=lambda: False)
    return Brain(registry, brain_dir, gate=gate, token=steward.token, manifest=steward.read_manifest,
                 folder=steward.folder)


def _cant_ask(why: str) -> str:
    why = " ".join(str(why or "no reason was given").split())
    return f"not checked: the local model could not be asked. {why[:1].upper()}{why[1:].rstrip('.')}."


def _sample_asks(data: dict, tools: list[dict] | None, registry: Registry, steward: Steward,
                 brain_dir: Path) -> tuple[dict | None, str]:
    """Item 12 while the package runs: ``({tool: why it was not picked}, "")``, or ``(None, plain line)``
    when it can't be checked."""
    ask = seams.find(seams.SAMPLE_ASK)
    if ask is None:
        return None, "skipped: brain not built yet (lane L2)"
    if not tools:
        return {}, ""
    from ..zone2.errors import BrainError
    try:
        brain = _check_brain(brain_dir, registry, steward)
    except (CantAsk, BrainError, OSError, ValueError) as exc:
        return None, _cant_ask(getattr(exc, "message", "") or str(exc))
    examples = (data.get("abilities") or {}).get("examples", {})
    wrong: dict[str, str] = {}
    for t in tools:
        sample = (examples.get(t["name"]) or [t["description"]])[0]
        try:
            got = ask(f"{data['id']}.{t['name']}", sample, brain=brain)
        except BrainError as exc:
            return None, _cant_ask(exc.message)
        except Exception as exc:  # noqa: BLE001 - this one ask failed: said with its reason
            wrong[t["name"]] = f"the ask failed ({type(exc).__name__})"
            continue
        ok = got is True or (isinstance(got, dict) and got.get("ok") is True)
        if not ok:
            wrong[t["name"]] = str(got.get("reason") or "") if isinstance(got, dict) else ""
            if not examples.get(t["name"]):
                # A description is often not a real request ("Plan a meal for a day of the week"), so a good
                # brain asks back instead of calling the tool. Say how to fix it.
                wrong[t["name"]] = f"{wrong[t['name']]} {NO_EXAMPLE}".strip()
    return wrong, ""


def check(folder: Path | str, start_timeout_s: float = 120) -> Report:
    folder = Path(folder)
    report = Report()
    try:
        data, path = manifest_mod.load(folder)
    except manifest_mod.ManifestError as exc:
        report.title = str(folder)
        report.add(1, 1, FAIL, "puffpai.json is not valid: " + " ".join(exc.problems))
        return report
    root = path.parent.parent if path.parent.name == ".buildbot" else path.parent
    report.id = data["id"]
    report.title = f"{data['id']} {data['version']} ({data['kind']})"
    if data["kind"] == "look":                    # D96: said for contributors; the levels never read it
        colours = contrast.check(data)
        report.notes += [colours["line"]] + [f"  {line}" for line in colours["failed"]]
    missing = manifest_mod.check_files(root, data)
    if missing:
        report.add(1, 1, FAIL, "the files it names are not all there: " + " ".join(missing))
        return report
    report.add(1, 1, PASS, "puffpai.json is valid and the files it names are there")

    source_hash = fsutil.tree_hash(root)
    tunnel = _tunnel_line(root, data, source_hash)
    if tunnel:                                # D117: no card here, so nothing of it runs here either
        report.add(1, 7, FAIL, tunnel)
        return report
    tmp = Path(tempfile.mkdtemp(prefix="puffpai-check-"))
    asks: dict = {}
    try:
        tools = _run_levels(report, root, data, tmp / "bot", start_timeout_s, asks, tmp / "brain")
    finally:
        fsutil.remove_folder(tmp, tmp.parent)
    if fsutil.tree_hash(root) != source_hash:
        report.add(1, 5, FAIL, "it changed files in its own source folder while it ran")

    passed, problems = _drawbridge(root, data, source_hash)
    report.add(1, 7, PASS if passed else FAIL, "Drawbridge found nothing on its block list" if passed else problems)
    _level2(report, data, root, tools, asks)
    _level3(report, data, root)
    report.warnings.extend(change_warnings(data, tools))
    report.items.sort(key=lambda x: (x[0], x[1]))
    return report


def _tunnel_line(root: Path, data: dict, tree: str) -> str:
    """Item 7's words when any verdict on this folder has a tunnel, a block as much as a hold (D117), else "". It reads
    the verdict itself, since ``_verdict`` gives nothing back on a block. A block with no tunnel goes on as before."""
    try:
        got = seams.drawbridge_check(root, data, origin=tree)
    except Exception:  # noqa: BLE001 - a crash is item 7's own answer, after the run, as before
        return ""
    if not isinstance(got, dict) or not got.get("tunnels"):
        return ""
    line = str(got.get("line") or "Its code can start a tunnel.")
    if got.get("verdict") == "hold":
        return f"{line} So it was not run here. Adding it asks the owner first."
    return f"{line} Its code can also start a tunnel, so it was not run here."


def _drawbridge(root: Path, data: dict, tree: str) -> tuple[bool, str]:
    from .manager import _verdict
    ok, problems = _verdict(root, data, tree)
    if ok and ok.get("tunnels"):       # one it wrote while it ran: the same answer as before it ran (D117)
        return False, str(ok.get("line") or "Its code can start a tunnel.")
    return ok, " ".join(problems)


def _run_levels(report: Report, root: Path, data: dict, state: Path, timeout_s: float, asks: dict | None = None,
                brain_dir: Path | None = None) -> list[dict] | None:
    """Items 2-5 and 15: copy in, set up, start, probe, list, stop, remove. The tools, if listed."""
    id = data["id"]
    block = run_block(data)
    runs = block is not None and block["type"] in ("command", "docker_compose")
    registry = Registry(state / "registry.db")
    from ..api.auth import TokenStore
    tokens = TokenStore(state / "tokens.db")
    installs = Installs(state)
    steward = Steward(state, registry, tokens, installs, core_api="http://127.0.0.1:8420")
    zone, zone_why = _check_bridge(state, registry, steward) if runs else (None, "")
    pkg = state / "packages" / id
    fsutil.copy_package(root, pkg)
    installs.set(id, pkg, False, str(root))
    _copied, _path, manifest_sha = manifest_mod.load_with_sha(pkg)
    registry.add(row_from_manifest(data, f"local:{root}", status="stopped", manifest_sha=manifest_sha))
    before = _snapshot(state)
    tools = None
    if not runs:
        for item in (2, 3, 4):
            report.add(1, item, NA, "it does not run a process")
    else:
        try:
            def during(listed: list[dict] | None) -> None:
                if asks is not None and data["kind"] in TALKS and (data.get("abilities") or {}).get("mcp"):
                    asks["wrong"], asks["why"] = _sample_asks(data, listed, registry, steward,
                                                              brain_dir or state.parent / "brain")
            tools = _run_it(report, steward, data, id, timeout_s, watch=_watch_pass(zone), during=during)
        finally:
            report.add(1, 6, *_hosts_item(zone, zone_why, data, id))
            _stop_bridge(zone)
    after = _snapshot(state)
    strays = sorted(f for f in after - before
                    if not (f.startswith(f"packages/{id}/") or f.startswith(f"data/{id}/") or _CORE_FILES.match(f)))
    if strays:
        report.add(1, 5, FAIL, "it wrote outside packages\\<id> and data\\<id>: " + ", ".join(strays[:10]))
    else:
        report.add(1, 5, PASS, "it wrote only inside packages\\<id> and data\\<id>")
    if not runs:
        report.add(1, 6, NA, "it does not run a process")

    left_running = steward.alive(id)
    gone = fsutil.remove_folder(pkg, state / "packages")
    leftovers = [f for f in _snapshot(state) - before
                 if not (f.startswith(f"data/{id}/") or _CORE_FILES.match(f))]
    if left_running or not gone or leftovers:
        why = "something still runs" if left_running else ("its folder could not be deleted" if not gone
                                                            else "files are left: " + ", ".join(sorted(leftovers)[:10]))
        report.add(3, 15, FAIL, f"removing it leaves something behind ({why})")
    else:
        report.add(3, 15, PASS, "removing it leaves nothing behind except its own data")
    return tools


def _check_bridge(state: Path, registry: Registry, steward: Steward) -> tuple[Any, str]:
    """Item 6's Bridge: a Zone 3 of the throwaway bot folder, its proxy on any free port, the bypass
    watch on the check's Steward. ``(zone, "")``, or ``(None, plain reason)`` when it can't start."""
    from ..zone3 import service
    before = service._CURRENT
    try:
        zone = service.Zone3(state, registry=registry)
        zone.start_proxy(0)
    except Exception as exc:  # noqa: BLE001 - no Bridge: item 6 can't pass (X26)
        service._CURRENT = before
        return None, f"the Bridge proxy could not start for the check ({type(exc).__name__})"
    service._CURRENT = before                     # the running Core's Zone 3 stays the current one
    steward.proxy_env = lambda pid, phase="run": zone.package_env(pid, phase=phase)
    from ..zone3.docker_wall import wall_kw
    steward.docker_wall_kw = lambda pid: wall_kw(zone.proxy)
    zone.start_watch(steward)
    return zone, ""


def _stop_bridge(zone: Any) -> None:
    if zone is not None:
        try:
            zone.stop()
        except Exception:  # noqa: BLE001 - the throwaway folder goes anyway
            pass


WATCH_BLIND = "couldn't watch it, so this can't be checked"


def _watch_pass(zone: Any):
    """One bypass-watch pass on demand, run while the package still runs (``_run_it`` calls it before Stop),
    so the watch has looked at it at least once. A pass that raises marks the watch as not running."""
    def once() -> None:
        w = getattr(zone, "watch", None) if zone is not None else None
        if w is None:
            return
        try:
            w.check_once()
        except Exception:  # noqa: BLE001 - said by item 6 (not watched = can't be checked)
            w.problem = w.problem or "check_failed"
    return once


def _watch_trouble(zone: Any, id: str) -> str:
    """"" when the bypass watch ran and could see this package the whole check; else why not."""
    w = getattr(zone, "watch", None)
    if w is None:
        return "the bypass watch did not start"
    try:
        st = w.status()
    except Exception:  # noqa: BLE001
        return "the bypass watch could not say how it is"
    if not st.get("running") or w.problem:
        return "the bypass watch was not running"
    kinds = {c.get("kind") for c in list(zone.cards)
             if c.get("kind") == "watch_not_running" or (c.get("kind") == "watch_blind" and c.get("package") == id)}
    if "watch_not_running" in kinds:
        return "the bypass watch stopped during the check"
    if "watch_blind" in kinds or id in set(st.get("blind") or []):
        return "the bypass watch could not list its programs"
    return ""


def _hosts_item(zone: Any, why: str, data: dict, id: str) -> tuple[str, str]:
    """Item 6 from the outside log of the check's Bridge: ``(status, words)``. The bypass watch blind or
    not running at any point during the check is a FAIL: a connection around the proxy could not be seen."""
    if zone is None:
        return FAIL, f"where it connects could not be checked: {why or 'no Bridge'}"
    trouble = _watch_trouble(zone, id)
    if trouble:
        return FAIL, f"{WATCH_BLIND} ({trouble})"
    from ..zone3 import hosts as hosts_mod

    def names(items: Any) -> set[str]:
        out = set()
        for h in items or []:
            try:
                out.add(hosts_mod.split_hostport(str(h))[0])
            except ValueError:
                continue
        return out
    needs = data.get("needs") or {}
    run_ok, install_ok = names(needs.get("hosts")), names(needs.get("install_hosts"))
    lines = [x for x in zone.bridge.ledger.read(limit=100000) if x.get("subject") == f"pkg:{id}"]
    asked = sorted({str(x.get("host") or "") for x in lines})
    undeclared = sorted({str(x.get("host") or "") for x in lines
                         if str(x.get("host") or "") not in (run_ok | install_ok if x.get("phase") == "install"
                                                             else run_ok)})
    around = [c for c in list(zone.cards) if c.get("kind") == "bypass" and c.get("package") == id]
    if around:
        return FAIL, "it connected around the Bridge proxy while it ran (D26)"
    if undeclared:
        return FAIL, "it contacted hosts that are not in needs.hosts: " + ", ".join(undeclared[:10])
    if asked:
        return PASS, f"every outside host it asked for is in needs.hosts ({', '.join(asked[:10])})"
    return PASS, "it asked for no outside host while it ran (through the Bridge proxy, held closed)"


def _run_it(report: Report, steward: Steward, data: dict, id: str, timeout_s: float,
            watch: Any = None, during: Any = None) -> list[dict] | None:
    try:
        steward.setup(id)
        steward.start(id)
        # From here on, the manifest of the installed copy through the Core's one reader (by its sha256).
        data = steward.read_manifest(id)
        if data is None:
            raise StewardError("its settings file changed after it was copied in")
    except StewardError as exc:
        report.add(1, 2, FAIL, f"it does not start: {exc}")
        report.add(1, 3, NA, "it did not start")
        report.add(1, 4, NA, "it did not start")
        steward.stop(id)
        return None
    tools = None
    try:
        info = steward.info(id)
        values = steward.values(id, steward.folder(id), run_block(data) or {}, info.port if info else None)
        ok, reason = _wait_healthy(steward, data, id, values, timeout_s)
        listeners = ports.job_listeners(steward.pids(id))
        exposed = [f"{a}:{p}" for a, p in listeners or [] if ports.network_reachable([a])]
        if listeners is None:
            report.add(1, 2, FAIL, "where it listens could not be read, so 127.0.0.1-only is not shown")
        elif exposed:
            report.add(1, 2, FAIL, "it listens on all networks: " + ", ".join(exposed))
        else:
            report.add(1, 2, PASS, "it starts from its run block and listens on 127.0.0.1 only")
        report.add(1, 3, PASS if ok else FAIL, f"its health check passes ({reason})" if ok
                   else f"its health check did not pass within {int(timeout_s)} s: {reason}")
        token_ok, words = True, "it has no MCP endpoint"
        ep = mcp_tools.endpoint(data, values)
        if ep is not None:
            if ep[0] == "http":
                token_ok = mcp_tools.refuses_without_token(ep[1])
                words = "its MCP endpoint refuses calls without its token" if token_ok \
                    else "its MCP endpoint answers without its token"
            else:
                words = "its MCP server is stdio, started per call"
            try:
                tools = mcp_tools.list_tools(data, values, info.token if info else None, cwd=steward.folder(id))
            except mcp_tools.McpError as exc:
                tools = None
                words += f"; {exc}"
        if during is not None and ok:
            during(tools)                          # item 12: the sample asks, while it still runs
    finally:
        if watch is not None:
            watch()                                # item 6: the bypass watch looks at it while it runs
        clean = steward.stop(id)
    report.add(1, 4, PASS if (clean and token_ok) else FAIL,
               ("it stops cleanly with nothing left running" if clean else "something still runs after Stop") + "; " + words)
    return tools


def _wait_healthy(steward: Steward, data: dict, id: str, values: dict, timeout_s: float) -> tuple[bool, str]:
    from ..caretaker.caretaker import Caretaker
    from ..bus import Bus
    probe = Caretaker(steward.registry, steward, Bus()).probe
    end = time.monotonic() + timeout_s
    ok, reason = False, "not checked"
    while time.monotonic() < end:
        ok, reason = probe(id)
        if ok or not steward.alive(id):
            break
        time.sleep(0.5)
    return ok, reason


#: Words in a route or tool name that suggest it changes something (D55).
CHANGE_WORDS = frozenset({
    "add", "set", "delete", "del", "remove", "rm", "update", "clear", "import", "create", "new", "edit",
    "save", "write", "reset", "rename", "move", "toggle", "mark", "send", "post", "put", "patch", "upload",
    "submit", "cancel", "buy", "pay", "destroy", "drop", "wipe", "purge", "erase", "insert", "replace",
    "append", "push", "publish", "approve", "revoke", "install", "uninstall", "enable", "disable", "start",
    "stop", "restart", "kill", "run", "exec", "logout", "login", "sync"})
CHANGE_PAIRS = frozenset({("check", "off"), ("tick", "off"), ("sign", "out"), ("log", "out")})


def _words(name: str) -> list[str]:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name or "")
    return [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]


def suggests_change(name: str) -> bool:
    """*name* (a path or a tool name) has a word that means a change, like ``clear_list`` or ``/api/add``."""
    words = _words(name)
    return any(w in CHANGE_WORDS for w in words) or any(p in CHANGE_PAIRS for p in zip(words, words[1:]))


def change_warnings(data: dict, tools: list[dict] | None) -> list[str]:
    """D55: plain warnings for GET routes and undeclared MCP tools whose names suggest a change."""
    out = []
    ui = data.get("ui") or {}
    routes = [("page", p.get("path")) for p in ui.get("pages", [])] + \
             [("widget", w.get("path")) for w in ui.get("widgets", [])]
    health = (data.get("health") or {}).get("url")
    if isinstance(health, str):
        routes.append(("health", re.sub(r"^[a-z]+://[^/]+", "", health)))
    for what, path in routes:
        if isinstance(path, str) and suggests_change(path.split("?", 1)[0]):
            out.append(f"its {what} GET route {path} sounds like it changes something. A GET or HEAD route "
                       "must never change anything (D55): do changes with POST, from its own page.")
    declared = set((data.get("abilities") or {}).get("changes", []))
    for t in tools or []:
        if t["name"] not in declared and suggests_change(t["name"]):
            out.append(f"its tool {t['name']} sounds like it changes something but is not in abilities.changes. "
                       "List it there, or rename it if it only reads (D55).")
    return out


def _level2(report: Report, data: dict, root: Path, tools: list[dict] | None, asks: dict | None = None) -> None:
    kind = data["kind"]
    if kind == "skill":
        text = (root / "SKILL.md").read_text(encoding="utf-8", errors="replace") if (root / "SKILL.md").is_file() else ""
        has = bool(re.search(r"(?im)^description:\s*\S", text)) or len(text.strip()) > 40
        report.add(2, 8, PASS if has else FAIL, "SKILL.md has a plain description" if has else "SKILL.md has no description")
        return
    if kind not in TALKS:
        report.add(2, 8, NA, f"Level 2 is for apps, tools and agents, not {kind}")
        return
    abilities = data.get("abilities") or {}
    if not abilities.get("mcp"):
        if kind == "app" and (data.get("ui") or {}).get("pages"):
            report.add(2, 8, PASS, "no MCP server; the brain reads it with read_app")
        else:
            report.add(2, 8, FAIL, "it has no MCP server (abilities.mcp)")
        return
    if tools is None:
        report.add(2, 8, FAIL, "its MCP server did not list its tools")
        return
    report.add(2, 8, PASS, f"it exposes an MCP server with {len(tools)} tool(s)")
    vague = [t["name"] for t in tools if len(t["description"]) < 8 or (t["input_schema"] or {}).get("type") != "object"]
    report.add(2, 9, FAIL if vague else PASS, ("these tools need a plain description and a schema: " + ", ".join(vague))
               if vague else "every tool has a plain description and a schema")
    changes = set(abilities.get("changes", []))
    hidden = [t["name"] for t in tools if (t["destructive"] or t["read_only"] is False) and t["name"] not in changes]
    report.add(2, 10, FAIL if hidden else PASS, ("these tools say they change things but are not in abilities.changes: "
                                                 + ", ".join(hidden)) if hidden
               else "every tool that says it changes things is in abilities.changes")
    names = {t["name"] for t in tools}
    named = (set(changes) | set(abilities.get("examples", {})) | set(abilities.get("tools", {}))
             | {a["tool"] for a in data.get("accepts", [])})
    unknown = sorted(named - names)
    report.add(2, 11, FAIL if unknown else PASS, ("named but not in its MCP server: " + ", ".join(unknown)) if unknown
               else "every tool it names is in its MCP server")
    asks = asks or {}
    if asks.get("wrong") is None:
        report.add(2, 12, NA, asks.get("why") or "not checked: it was not healthy, so no sample ask was made")
        return
    wrong = asks["wrong"]
    said = "; ".join(f"{n} ({why})" if why else n for n, why in wrong.items())
    report.add(2, 12, FAIL if wrong else PASS, ("the local model did not pick these from a sample ask: " + said)
               if wrong else "a sample ask picks the right tool every time")


def _level3(report: Report, data: dict, root: Path) -> None:
    ui = data.get("ui") or {}
    icon = "icon" in data
    screen_ok = data["kind"] not in SCREENS or bool(ui.get("tray") or ui.get("pages"))
    report.add(3, 13, PASS if icon and screen_ok else FAIL,
               "it has an icon" + (" and a tray item or page" if data["kind"] in SCREENS else "") if icon and screen_ok
               else ("it needs an icon (icon in puffpai.json)" if not icon else "an app, page or ui needs a tray item or page"))
    plain = bool(str(data.get("consent", "")).strip()) and len(str(data.get("description", "")).strip()) >= 10
    report.add(3, 14, PASS if plain else FAIL, "it has a consent sentence and a description" if plain
               else "it needs a consent sentence and a real description")
    readme = next((p for p in (root / "README.md", root / "readme.md", root / ".buildbot" / "README.md") if p.is_file()), None)
    text = readme.read_text(encoding="utf-8", errors="replace") if readme else ""
    has = bool(re.search(r"(?im)^#+\s.*\bPuffPAI\b", text))
    report.add(3, 16, PASS if has else FAIL, 'its README has a "PuffPAI" section' if has
               else 'its README needs a "PuffPAI" section: what it does, what it needs, any setup steps')


__all__ = ["FAIL", "NA", "PASS", "Report", "check"]
