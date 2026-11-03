# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/brain_tools_builtin.py (rewritten: the always-present set from 01)
"""Concierge: the front desk. It picks the tools of each turn (01 "How the brain sees everything").

1. **Always present**: the core tools ``list_installed``, ``open_package``,
   ``search_knowledge``, ``checklist_add``, ``ask_toolsmith``, ``read_app``,
   ``open_app``, ``arrange_home``, ``run_command``, plus any package tools the owner pinned.
   ``arrange_home`` changes only her Home layout file (D82), so it asks nothing.
   ``run_command`` runs one command after her Yes for that one (D138, ``zone2.commands``).
2. **Picked per turn**: the request's words are matched against each
   Registry row (name, id, description, its abilities' descriptions and
   sample asks; a plain word match this week, no embedder), and the best
   packages' tools are loaded until the model profile's tool budget is used.
3. **``open_package(id)``** adds any other package's tools mid-turn.

Every package tool goes over MCP with the package's token (``zone2.mcp``).
Arguments get handles, never held values (02 ``accepts_labels``: a tool gets
handles unless the owner said otherwise; that path is PROPOSED and not built).
One exception, the owner's (D140): while changing her documents is on, a tool that makes or changes her file, of a
package that can reach nowhere, gets the real text from a local brain with the vault open, never a key
(:meth:`Concierge._package_args`). Drawbridge's review, her Yes card and the conversation store still see the locked form.
Whether a tool changes things is Drawbridge's one rule
(``zone3.drawbridge.tool_changes``: the manifest's ``abilities.changes``, the
tool's own MCP annotations, Drawbridge's name rule), plus the Registry row's
flags, never the model's word (X27). In a running Core every package tool
call goes through Drawbridge's review first (``wiring.tool_review``).

Calls out to other lanes are passed in by the running Core: ``search(query, limit)``
(``search_knowledge``: lane L3's folders index, ``wiring.knowledge_search``) and ``checklist``
(``checklist_add``: lane L5's Checklist). A brain step is filed with **package-level trust**, never the
Core's: no admin step, no command check, and it is the one step shape (02). It is a reminder only (kind
``confirm`` or ``plug_in``, title and why): chat never raises a step that tells her to run a command or open
an address (lane I6a). ``ask_toolsmith`` calls the
running Core's ``toolsmith(job, ctx)`` (``wiring.chat_toolsmith``: a one-job plan, its ONE Yes card in chat,
the build, the result in the same conversation). A part that is missing says so plainly.

**Tool servers she added** (D137, ``puffpai_core.tool_servers``): a second tool source beside the Registry, passed
in as ``outside``. Their tools are named ``<id>.<tool>`` like a package's and carry the package id ``outside-<id>``,
so each call goes through the same Drawbridge review (``wiring.tool_review``; a tool with no ``readOnlyHint`` counts
as changing). ``add_tool_server``, ``remove_tool_server`` (each with its own Yes card) and ``list_tool_servers`` are
core tools when that source is here. A password never goes through chat: the Developer page takes it.

**Skills** (D149, ``zone2.skills``, ported from v1): the prompt lists each installed skill by name and one line, and
``skill_view`` reads that package's own ``SKILL.md``. Reading runs nothing; a command it asks for goes through
``run_command`` and her Yes. Both are here only on a bot with a skill.

``search_knowledge`` answers from the index only: text the gate already passed, with every held value as
its handle (``{{vault:<ref>}}``). The loop's gate (``to_model``) then gives the local brain the real
value only while the vault is open, on this computer (D25).
"""

from __future__ import annotations

import json
import re
import uuid
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .. import owner_files
from . import apps, commands, mcp, programs, skills
from .gate import clean_value, no_keys
from ..zone3.drawbridge import tool_changes
from .tools import BadArgs, Tool, ToolTable, check_schema, make

#: Installed things listed in the prompt every turn; more are reached with list_installed (v1 lesson 20).
PROMPT_ROWS = 60
WORD_RE = re.compile(r"[a-z0-9]+")
STOP = frozenset("a an and are as at be by can do for from have how i in is it me my of on or our please put "
                 "show tell that the this to up us what when where which who will with you your".split())
#: Kinds whose package may have tools for the brain.
TOOL_KINDS = frozenset({"app", "tool", "agent", "skill", "knowledge"})

#: The only Checklist kinds chat may raise: a reminder she ticks, or "plug in" hardware. Never a command to run
#: or an address to open (those come from Toolsmith's checked paths and the Core).
CHAT_STEP_KINDS = ("confirm", "plug_in")
CHAT_STEP_ONLY = ("chat can only add a reminder (kind confirm, or plug_in for hardware), never a command to run "
                  "or an address to open")

#: The Checklist "package" the brain's own steps are filed under: a name no package id can have (02).
BRAIN_STEPS = "core:concierge"
SEARCH_MAX = 20
NO_TOOLSMITH = "Toolsmith is not part of this Core, so nothing was looked for."
#: D126: where her documents go, so the brain never makes up a place ("document storage").
ADD_DOCUMENTS = ("The owner adds her documents on the Knowledge page, with the Add documents button, or adds a whole "
                 "folder there. There is no other place for them.")
NOTHING_FOUND = "No file in the data folder, the linked folders or her documents matches that. " + ADD_DOCUMENTS

CORE_TOOL_NAMES = ("list_installed", "open_package", "search_knowledge", "checklist_add", "ask_toolsmith",
                   "read_app", "open_app", "arrange_home", commands.TOOL)
#: Only when the Core passes its tool servers in (D137), and only on a turn whose words name one, so every other turn
#: spends no tool slot on them.
SERVER_TOOL_NAMES = ("add_tool_server", "remove_tool_server", "list_tool_servers")
SERVER_WORDS = frozenset({"server", "mcp"})
#: Only on a bot with a document pinned (D125), so a bot with none spends no tool slot on it.
READ_PINNED = "read_pinned"
#: Show a fix and the passage it means, highlighted (D140). She makes the change: nothing in the file changes.
SHOW_PASSAGE = "show_passage"
#: Only on a bot with an app that takes files (D127).
SEND_DOCUMENT = "send_document"
#: Only on a bot with a skill installed (D149).
SKILL_VIEW = skills.TOOL
#: Only on a bot with a desktop program she added (D138).
OPEN_IN_PROGRAM = programs.OPEN_TOOL
#: What arrange_home can do (D82): an app's own page on Home or off it, every app as pages or small tiles,
#: and the Arrange buttons (layout.LAYOUT_ACTIONS).
HOME_ACTIONS = ("add_app", "remove_app", "apps_as_pages", "apps_as_tiles", "pin", "unpin", "hide", "show", "move",
                "rename")


def words(text: str) -> set[str]:
    out = set()
    for w in WORD_RE.findall(str(text or "").lower()):
        if w in STOP or len(w) < 2:
            continue
        out.add(w)
        if w.endswith("s") and len(w) > 3:
            out.add(w[:-1])
    return out


@lru_cache(maxsize=1)
def _step_validator():
    from jsonschema import Draft202012Validator

    from ..paths import contracts_dir
    defs = json.loads((contracts_dir() / "puffpai-package.schema.json").read_text(encoding="utf-8"))["$defs"]
    return Draft202012Validator({"$ref": "#/$defs/step", "$defs": defs})


def step_problem(step: dict) -> str:
    """Empty when *step* fits 02's one step shape, else the first problem in plain words."""
    for err in _step_validator().iter_errors(step):
        plain = err.schema.get("x-plain") if isinstance(err.schema, dict) else None
        return str(plain or err.message)
    return ""


@dataclass
class Concierge:
    """Builds each turn's :class:`ToolTable` from the Registry.

    ``manifest(id)``: the installed package's manifest from the Core's one reader (``Steward.read_manifest``:
    the file her install Yes covered, checked by its sha256), or None. With no reader, no package has a
    manifest here, so no package tool is offered and nothing is started (the Concierge never opens a
    ``puffpai.json`` itself: a project's own top-level file is never what the Core runs).
    ``folder(id)``: the folder the Core installed it in (a stdio MCP server runs there); with none, a
    stdio server is not started.
    ``token(id)``: the package's plain ``PUFFPAI_TOKEN`` (L1's Steward issues it at start).
    ``start(id)``: Steward's start (L1), ``{ok, port, said}``.
    ``targets(id)``: tests may hand an :class:`~.mcp.Target` straight in.
    ``keep(text)``: the gate's locked form, for tool arguments.
    ``real_args(value)``: ``Gate.with_real_values``, for the one exception in :meth:`_package_args`.
    ``reaches(id)``: has she allowed this package an address (``PackageManager._has_address``)? With none, or one
    that breaks, the answer is yes.
    """

    registry: Any
    state: Path
    keep: Callable[[str], str]
    manifest: Optional[Callable[[str], Optional[dict]]] = None
    folder: Optional[Callable[[str], Optional[Path]]] = None
    token: Optional[Callable[[str], Optional[str]]] = None
    start: Optional[Callable[[str], dict]] = None
    targets: Optional[Callable[[str], Optional[mcp.Target]]] = None
    browser: bool = True
    checklist: Any = None
    search: Optional[Callable[[str, int], Any]] = None
    toolsmith: Optional[Callable[[str, dict], Any]] = None
    pinned: Any = None                 # wiring.pinned_documents: names() and read(name, part, find, real) (D125)
    outside: Any = None                # tool_servers.ToolServers: the tool servers she added (D137)
    real_args: Optional[Callable[[Any], Any]] = None
    reaches: Optional[Callable[[str], bool]] = None
    skills_on: bool = True             # off for a dry run, which is one step long (Brain.sample_ask, Brain.picks)
    _listed: dict = field(default_factory=dict)

    # -- what is installed ---------------------------------------------------
    def rows(self) -> list[dict]:
        return [r for r in self.registry.list() if r.get("status") != "removed"]

    def _manifest(self, pid: str) -> Optional[dict]:
        if self.manifest is None:
            return None
        try:
            got = self.manifest(pid)
        except Exception:  # noqa: BLE001 - a reader that breaks means no manifest (fail closed)
            return None
        return got if isinstance(got, dict) else None

    def _folder(self, pid: str) -> Optional[Path]:
        if self.folder is None:
            return None
        try:
            got = self.folder(pid)
        except Exception:  # noqa: BLE001 - no folder: a stdio server is not started
            return None
        return Path(got) if got else None

    def _find(self, key: str) -> Optional[tuple[dict, Optional[dict]]]:
        """A row by id, or by its name in any spelling ("World Monitor" = "worldmonitor")."""
        flat = re.sub(r"[^a-z0-9]", "", key.lower())
        rows = self.rows()
        row = next((r for r in rows if r["id"] == key), None) or next(
            (r for r in rows if flat and flat in (re.sub(r"[^a-z0-9]", "", r["id"]),
                                                  re.sub(r"[^a-z0-9]", "", r.get("name", "").lower()))), None)
        return (row, self._manifest(row["id"])) if row else None

    def has_mcp(self, row: dict, manifest: Optional[dict]) -> bool:
        m = ((manifest or {}).get("abilities") or {}).get("mcp")
        return row.get("kind") in TOOL_KINDS and isinstance(m, dict) and m.get("audience") != "core"

    def changes_of(self, row: dict, manifest: Optional[dict]) -> set[str]:
        """Tool names (without the id) the row's flags or the manifest's list mark as changing. Each tool
        is also asked of Drawbridge's one rule (``zone3.drawbridge.tool_changes``): either one is enough (X27)."""
        out = {a["tool"].split(".", 1)[1] for a in row.get("abilities") or [] if a.get("changes")}
        out |= {str(t) for t in ((manifest or {}).get("abilities") or {}).get("changes") or []}
        return out

    def prompt_block(self) -> str:
        """Every installed thing, one line each, read fresh every turn."""
        rows = self.rows() + programs.listing(self.state)      # her own programs too (D16): the brain sees everything
        if not rows:
            return "Nothing is installed on this bot yet." + self._outside_block() + self._pinned_block()
        lines = ["Installed on this bot (use open_package with an id to get its tools; read_app reads an app's page):"]
        for r in rows[:PROMPT_ROWS]:
            desc = (r.get("description") or "").strip()
            lines.append(f"- {r['id']}: {r.get('name')} ({r.get('kind')}, {r.get('status')})" + (f": {desc}" if desc else ""))
        if len(rows) > PROMPT_ROWS:
            lines.append(f"... and {len(rows) - PROMPT_ROWS} more; list_installed shows them all.")
        return "\n".join(lines) + self._outside_block() + self._pinned_block() + self._skills_block()

    # -- skills (D149) ---------------------------------------------------------
    def _skills(self) -> list[dict]:
        """The installed skills the brain may read: a row of kind skill, in use, its manifest still the file her Yes
        covered, and its own ``SKILL.md`` readable. Read fresh every turn, as the installed list is."""
        if not self.skills_on or self.manifest is None or self.folder is None:
            return []
        out = []
        for r in self.rows():
            if r.get("kind") != "skill" or r.get("status") not in ("running", "stopped", "needs_you"):
                continue
            text = skills.read_own(self._folder(r["id"])) if self._manifest(r["id"]) is not None else None
            if text is not None:
                said = skills.description_of(text) or " ".join(str(r.get("description") or "").split())
                out.append({"id": r["id"], "name": str(r.get("name") or ""), "text": text,
                            "description": said[:skills.LIST_DESCRIPTION_MAX]})
        return out

    def _skills_block(self) -> str:
        block = skills.prompt_block(self._skills())
        return "\n\n" + block if block else ""

    def _skill(self, name: str) -> Optional[dict]:
        """An installed skill by id, or in any spelling: an id always wins over another skill's display name."""
        flat = re.sub(r"[^a-z0-9]", "", name.lower())
        found = self._skills()
        return next((s for s in found if s["id"] == name), None) or next(
            (s for s in found if flat and flat == re.sub(r"[^a-z0-9]", "", s["id"])), None) or next(
            (s for s in found if flat and flat == re.sub(r"[^a-z0-9]", "", s["name"].lower())), None)

    # -- tool servers she added (D137) -----------------------------------------
    def _outside_rows(self) -> list[dict]:
        if self.outside is None:
            return []
        try:
            return list(self.outside.ready())
        except Exception:  # noqa: BLE001 - a list that can't be read is no reason to stop a turn
            return []

    def _outside_block(self) -> str:
        found = self._outside_rows()
        if not found:
            return ""
        return ("\n\nTool servers the owner added (use open_package with an id to get their tools):\n"
                + "\n".join(f"- {r['id']}: {r['name']} ({len(r['tools'])} tools)" for r in found[:PROMPT_ROWS]))

    def _outside_tools(self, rec: dict) -> list[Tool]:
        """A tool server's tools from its live ``list_tools``, as ``<id>.<tool>``. Raises :class:`~.mcp.McpError`."""
        from ..tool_servers import ORIGIN, PREFIX
        pkg = PREFIX + rec["id"]
        target = self.targets(pkg) if self.targets is not None else None
        target = target or self.outside.target(rec["id"])
        key = ("outside", rec["id"], rec.get("url"), rec.get("added_at"))
        listed = self._listed.get(key)
        if listed is None:
            listed = mcp.list_tools(target)
            self._listed[key] = listed
        return self._made(listed, target, rec["id"], rec["name"], pkg, set(), None, ORIGIN)

    def _pinned_names(self) -> list[str]:
        if self.pinned is None:
            return []
        try:
            return list(self.pinned.names())
        except Exception:  # noqa: BLE001 - a list that can't be read is no reason to stop a turn
            return []

    def _can_send(self) -> bool:
        """``send_document`` only on a bot with an app that takes files (D127), so no other spends a tool slot."""
        take = getattr(self.pinned, "apps_take_files", None)
        try:
            return bool(callable(take) and take())
        except Exception:  # noqa: BLE001 - unsure: not offered this turn
            return False

    def _pinned_block(self) -> str:
        """The name of each pinned document, never its words (D125): read_pinned reads them in parts."""
        pinned = self._pinned_names()
        if not pinned:
            return ""
        return ("\n\nThe owner pinned these documents to this bot, for every chat. They are here already: never ask her "
                "to link, paste or send them. Read them with read_pinned, in parts:\n"
                + "\n".join(f"- {n}" for n in pinned[:PROMPT_ROWS]))

    # -- package tools -------------------------------------------------------
    def _target(self, row: dict, manifest: Optional[dict]) -> mcp.Target:
        if self.targets is not None:
            got = self.targets(row["id"])
            if got is not None:
                return got
        return mcp.target_for(row, manifest, self.token_for(row["id"]), self._folder(row["id"]))

    def token_for(self, pid: str) -> Optional[str]:
        """The one token seam: Steward's ``token(id)`` (L1), passed in as ``token``. None means refuse."""
        if self.token is None:
            return None
        try:
            return mcp.usable_token(self.token(pid))   # a non-blank string, or no token at all (D50)
        except Exception:  # noqa: BLE001 - unsure means no token, and no call
            return None

    def package_tools(self, pid: str) -> list[Tool]:
        """The package's tools from its live ``list_tools``. Raises :class:`~.mcp.McpError`."""
        if not any(r["id"] == pid for r in self.rows()):
            rec = next((r for r in self._outside_rows() if r["id"] == pid), None)
            if rec is not None:
                return self._outside_tools(rec)
        hit = self._find(pid)
        if hit is None:
            raise mcp.McpError(f"There is no package called {pid}.", "none")
        row, manifest = hit
        if not self.has_mcp(row, manifest):
            raise mcp.McpError(f"{row['name']} has no tools for the brain. read_app can read its page.", "none")
        target = self._target(row, manifest)
        key = (row["id"], row.get("port"), row.get("version"), row.get("status"))
        listed = self._listed.get(key)
        if listed is None:
            listed = mcp.list_tools(target)
            self._listed[key] = listed
        return self._made(listed, target, row["id"], row["name"], row["id"], self.changes_of(row, manifest), manifest,
                          str(row.get("origin") or "local"))

    def _made(self, listed: list[dict], target: mcp.Target, shown: str, label: str, package: str, changes: set[str],
              manifest: Optional[dict], origin: str) -> list[Tool]:
        out = []
        for t in listed:
            name = f"{shown}.{t['name']}"
            if no_keys(name) != name:
                continue                            # a tool named like a key never reaches the model
            if owner_files.hidden(self.state, manifest, t["name"], t.get("input_schema")):
                continue                            # D140: changing her documents is off until she turns it on
            fn = self._caller(target, t["name"])
            # The package's words go to the model: its description and its whole input
            # schema (property descriptions, enums, defaults) through the gate (D50).
            desc = self.keep(t["description"] or f"{t['name']} from {label}")
            try:
                entry = {"name": t["name"], "annotations": dict(t.get("annotations") or {})}
                out.append(make(name, desc, check_schema(self._handles(t.get("input_schema") or {})), fn,
                                changes=t["name"] in changes or tool_changes(t["name"], manifest, entry, origin),
                                package=package, extra={"entry": entry}))
            except ValueError:
                continue
        return out

    def _handles(self, value: Any) -> Any:
        """Every string in *value*, keys included, in its locked form (handles, never held values)."""
        return clean_value(value, self.keep)

    def _reaches(self, pid: str) -> bool:
        try:
            return self.reaches is None or self.reaches(pid) is not False
        except Exception:  # noqa: BLE001 - can't tell: it may reach somewhere
            return True

    def _package_args(self, target: mcp.Target, tool: str, args: dict, ctx: dict) -> Any:
        """What the package gets: the locked form, or for the owner's one exception the real text (D140), all read now, at
        the call, after any Yes. Any no or any doubt: locked. A file's ``path`` stays locked either way, so the file that
        changes is the file Drawbridge reviewed and her Yes named."""
        safe = self._handles(args)
        if ((ctx or {}).get("real") is not True or target.outside or self.real_args is None
                or not owner_files.takes_real_text(self.state, self._manifest(target.package), tool)
                or self._reaches(target.package)):
            return safe
        real = self.real_args(args)                 # None while the vault is locked
        if not isinstance(real, dict) or not isinstance(safe, dict):
            return safe
        return {**real, "path": safe["path"]} if "path" in safe else real

    def _caller(self, target: mcp.Target, tool: str) -> Callable[[dict, dict], Any]:
        def fn(args: dict, ctx: dict) -> Any:
            got = mcp.call_tool(target, tool, self._package_args(target, tool, args, ctx))
            return got["text"] if got["ok"] else {"ok": False, "error": got["text"]}
        return fn

    def score(self, row: dict, manifest: Optional[dict], asked: set[str]) -> int:
        text = " ".join([row["id"].replace("-", " ").replace("_", " "), row.get("name", ""), row.get("description", "")]
                        + [a.get("description", "") + " " + a["tool"].split(".", 1)[1].replace("_", " ")
                           for a in row.get("abilities") or []])
        examples = ((manifest or {}).get("abilities") or {}).get("examples") or {}
        text += " " + " ".join(f"{k.replace('_', ' ')} {' '.join(v)}" for k, v in examples.items()
                               if isinstance(v, list))
        return len(asked & words(text))

    # -- one turn ------------------------------------------------------------
    def table_for(self, text: str, budget: int, pinned: list[str] | None = None) -> ToolTable:
        asked = words(text)
        table = ToolTable(self.core_tools(lambda: table, asked))
        ranked = []
        for row in self.rows():
            manifest = self._manifest(row["id"])
            if row.get("status") not in ("running", "stopped", "needs_you") or not self.has_mcp(row, manifest):
                continue
            pin = any(p.startswith(row["id"] + ".") for p in pinned or [])
            s = self.score(row, manifest, asked)
            if s > 0 or pin:
                ranked.append((pin, s, row["id"]))
        for rec in self._outside_rows():                # tool servers she added (D137): the same picking
            pin = any(p.startswith(rec["id"] + ".") for p in pinned or [])
            text = " ".join([rec["id"].replace("_", " "), rec["name"]]
                            + [t["name"].replace("_", " ") + " " + t["description"] for t in rec["tools"]])
            s = len(asked & words(text))
            if s > 0 or pin:
                ranked.append((pin, s, rec["id"]))
        for pin, _s, pid in sorted(ranked, key=lambda x: (not x[0], -x[1], x[2])):
            if len(table) >= budget and not pin:
                break
            try:
                tools = self.package_tools(pid)
            except mcp.McpError:
                continue
            for t in tools:
                if t.name in (pinned or []) or len(table) < budget:
                    table.add(t)
        return table

    def core_tools(self, table_ref: Callable[[], ToolTable], asked: Optional[set[str]] = None) -> list[Tool]:
        def list_installed(_a: dict, _c: dict) -> Any:
            return [{"id": r["id"], "name": r.get("name"), "kind": r.get("kind"), "status": r.get("status"),
                     "description": r.get("description", ""),
                     "has_tools": self.has_mcp(r, self._manifest(r["id"]))} for r in self.rows()] + [
                {"id": r["id"], "name": r["name"], "kind": "tool_server", "status": "ready", "has_tools": True,
                 "description": f"A tool server the owner added, with {len(r['tools'])} tools."} for r in self._outside_rows()] \
                + programs.listing(self.state)

        def open_package(a: dict, _c: dict) -> Any:
            try:
                tools = self.package_tools(str(a["id"]))
            except mcp.McpError as exc:
                return {"ok": False, "error": exc.message}
            table = table_ref()
            for t in tools:
                table.add(t)
            return {"ok": True, "tools": [{"name": t.wire, "description": t.description, "changes": t.changes}
                                          for t in tools]}

        def search_knowledge(a: dict, _c: dict) -> Any:
            if self.search is None:
                return {"ok": False, "error": "The knowledge index is not here, so there is nothing to search."}
            try:
                limit = max(1, min(int(a.get("limit") or 5), SEARCH_MAX))
            except (TypeError, ValueError):
                raise BadArgs("limit is a whole number") from None
            hits = self.search(str(a["query"]), limit)
            # Her books and downloads on a drive that is gone or full (D103): still searched, and one plain line.
            away = getattr(self.search, "away", None)
            note = {"note": away()} if callable(away) and away() else {}
            if not hits:
                return {"ok": True, "hits": [], "said": NOTHING_FOUND, **note}
            return {"ok": True, "hits": hits, **note}

        def checklist_add(a: dict, _c: dict) -> Any:
            if self.checklist is None:
                return {"ok": False, "error": "The Checklist is not here, so no step was added."}
            kind = str(a.get("kind") or "confirm")
            if kind not in CHAT_STEP_KINDS:
                # A step that tells her to run a command (or open an address) never comes from chat: only from
                # Toolsmith's checked paths and the Core (lane I6a: a plain question raised a run_command step).
                raise BadArgs(CHAT_STEP_ONLY)
            step = {"id": "brain-" + uuid.uuid4().hex[:12], "kind": kind}
            for k in ("title", "why"):
                if k in a:
                    step[k] = self.keep(str(a[k]))        # the Checklist store gets handles, never held values
            problem = step_problem(step)
            if problem:
                raise BadArgs(f"that is not a valid Checklist step: {problem}")
            from ..checklist import ChecklistError
            try:
                # Package-level trust, never the Core's: no admin step, no command check (D49).
                got = self.checklist.add(step, BRAIN_STEPS, trust="package", raised_by="concierge (chat)")
            except ChecklistError as exc:
                return {"ok": False, "error": f"No step was added: {exc}"}
            return {"ok": True, "step": got["id"],
                    "said": f"A 'Your turn' card is on the Desktop: {got.get('title', '')}"}

        def ask_toolsmith(a: dict, ctx: dict) -> Any:
            if self.toolsmith is None:
                return {"ok": False, "error": NO_TOOLSMITH}
            # D77: "first" starts a new build; without it, a build still open in this chat takes the new words.
            return self.toolsmith(self.keep(str(a["job"])), {**(ctx or {}), "first": a.get("first") is True})

        def read_app(a: dict, _c: dict) -> Any:
            return apps.read_app(self._find, self.start, a, self.browser)

        def open_app(a: dict, _c: dict) -> Any:
            return apps.open_app(self._find, a)

        def arrange_home(a: dict, _c: dict) -> Any:
            from .. import layout
            action, what = str(a.get("action") or "").strip().lower(), str(a.get("tile") or "").strip()
            if action not in HOME_ACTIONS:
                raise BadArgs("action is one of " + ", ".join(HOME_ACTIONS))
            if action.startswith("apps_as_"):
                got = layout.apps_view(self.state, action[len("apps_as_"):], by="brain")
                return {"ok": got["ok"], "said": got["line"]}

            byid = what.startswith(layout.APP_TILE)
            # Her exact words for a core tile ("the bridge") mean that tile, even when an app has a like name.
            hit = self._find(what[len(layout.APP_TILE):] if byid else what) if what and (
                byid or action in ("add_app", "remove_app") or not layout.core_tile(what)) else None
            row = hit[0] if hit else None
            page = bool(row and (row.get("ui") or {}).get("pages"))
            inside = bool(row) and apps.shows_inside(row)
            if action in ("add_app", "remove_app"):
                if row is None:
                    return {"ok": False, "said": f"There is no app called {what or 'that'} here. list_installed shows them."}
                if action == "add_app" and not page:
                    return {"ok": False, "said": f"{row.get('name')} has no page to show on Home. "
                                                 "It is in Your apps, where it opens."}
                got = layout.app_page(self.state, row["id"], row.get("name"), action == "add_app", by="brain",
                                      inside=inside)
                return {"ok": got["ok"], "said": got["line"]}
            name = self.keep(str(a.get("name") or "")) if action == "rename" else ""
            if not page:                          # a tile on Home, by her words for it
                got = layout.arrange(self.state, action, what, position=a.get("position"), name=name, by="brain")
                return {"ok": got["ok"], "said": got["line"]}
            # An installed app she named: its own app/<id> tile, never a core tile with a word in common.
            tid, label, lead = layout.APP_TILE + row["id"], str(row.get("name") or row["id"]), ""
            now = layout.get_layout(self.state)["apps"]
            if row["id"] not in now["pages"] and now["view"] != "pages":
                if action not in ("pin", "show", "move"):
                    return {"ok": False, "said": f"{label}'s page is not on your Home page. It is in Your apps."}
                if action == "move" and layout.position_index(a.get("position"), 0) is None:
                    return {"ok": False, "said": "Say where it goes: first, last, or a number from 1. Nothing changed."}
                lead = layout.app_page(self.state, row["id"], label, True, by="brain", inside=inside)["line"] + " "
            got = layout.arrange(self.state, action, tid, position=a.get("position"), name=name, by="brain",
                                 label=label)
            return {"ok": got["ok"], "said": lead + got["line"] if got["ok"] else got["line"]}

        def read_pinned(a: dict, c: dict) -> Any:
            try:
                part = int(a.get("part") or 1)
            except (TypeError, ValueError):
                raise BadArgs("part is a whole number, from 1") from None
            return self.pinned.read(str(a.get("name") or ""), part, str(a.get("find") or ""),
                                    (c or {}).get("real") is True)

        def show_passage(a: dict, c: dict) -> Any:
            try:
                part = int(a.get("part") or 0)
            except (TypeError, ValueError):
                part = 0
            # Her own document's words have been through the gate; the model's own words here shown as they are may not.
            return self.pinned.show(str(a.get("name") or ""), str(a.get("passage") or ""), no_keys(str(a.get("suggestion") or "")),
                                    no_keys(str(a.get("why") or "")), part, (c or {}).get("real") is True)

        def skill_view(a: dict, _c: dict) -> Any:
            try:
                part = int(a.get("part") or 1)
            except (TypeError, ValueError):
                raise BadArgs("part is a whole number, from 1") from None
            return skills.view(self._skill, str(a.get("name") or ""), part)

        def send_document(a: dict, _c: dict) -> Any:
            return self.pinned.send(str(a.get("name") or ""), str(a.get("app") or ""))

        def add_tool_server(a: dict, _c: dict) -> Any:
            from ..tool_servers import PASSWORD_IN_CHAT
            status, got = self.outside.add(a.get("name") or "", a.get("address"), None, by="chat")
            said = str(got.get("line") or got.get("error") or "")
            if "password" in said.lower() and status >= 300:
                said += " " + PASSWORD_IN_CHAT
            return {"ok": status < 300, "said": said, "state": got.get("state") or "refused"}

        def remove_tool_server(a: dict, _c: dict) -> Any:
            from ..tool_servers import slug
            name = str(a.get("name") or "")
            sid = next((r["id"] for r in self.outside.listed() if name.strip().lower() in (r["id"], r["name"].lower())),
                       slug(name))
            status, got = self.outside.remove(sid)
            return {"ok": status < 300, "said": str(got.get("line") or got.get("error") or "")}

        def list_tool_servers(_a: dict, _c: dict) -> Any:
            found = self.outside.listed()
            if not found:
                return {"ok": True, "servers": [], "said": "No tool servers are added yet."}
            return {"ok": True, "servers": [{"id": r["id"], "name": r["name"], "address": r["address"], "state": r["state"],
                                             "tools": r["tool_count"], "line": r["line"]} for r in found]}

        def server_question(verb: str):
            def question(a: dict, _c: dict) -> dict:
                name = self.keep(" ".join(str(a.get("name") or "").split())[:60])     # handles, never held values
                said = [f"name: {name}"] if name else []
                if a.get("address"):
                    said.append("address: " + self.keep(" ".join(str(a["address"]).split())[:200]))
                return {"kind": "core_change", "tool": f"{verb}_tool_server", "arguments": said,
                        "title": f"May I {verb} the tool server {name or 'you named'}?",
                        "what": ("Add an MCP tool server at that address. BuildBot connects to it first and keeps it only "
                                 "if it answers. One off this computer also needs your Yes on the Bridge page."
                                 if verb == "add" else "Remove that tool server and its tools, and its password from your "
                                                       "vault."),
                        "why": "It changes which tools your bot has, so it asks first."}
            return question

        s = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
        idp = {"id": {"type": "string", "description": "The package id, as listed."}}
        skill_tool = [make(SKILL_VIEW, skills.VIEW_WORDS, skills.VIEW_SCHEMA, skill_view)] if self._skills() else []
        send_tool = [make(SEND_DOCUMENT, "Hand one of the owner's documents to an installed app that takes files, when "
                          "she asks, like \"add my warranty to Document search\" or \"turn my novel into a PDF\". The "
                          "document is one on her list or one in a folder she linked, by its name.",
                          s({"name": {"type": "string", "description": "The document's file name."},
                             "app": {"type": "string", "description": "The app's id, as listed."}}, ["name", "app"])
                          | {"additionalProperties": False}, send_document)] if self._can_send() else []
        pinned_tool = [make(READ_PINNED, "Read a document the owner pinned to this bot, one part at a time, whenever "
                            "she asks about it or wants work done on it. Start at part 1; each result says how many "
                            "parts there are and which is next. To reach the right place in a long one, give find "
                            "(a name, a phrase, \"Chapter 12\"): it answers which parts hold those words.",
                            s({"name": {"type": "string", "description": "The document's name, as listed."},
                               "part": {"type": "integer", "description": "Which part, from 1."},
                               "find": {"type": "string", "description": "Words to look for, instead of reading."}},
                              []) | {"additionalProperties": False}, read_pinned),
                       make(SHOW_PASSAGE, "Show the owner a passage of a document she pinned, highlighted where it is, with a "
                            "fix or a recommendation beside it. Use this for every fix you suggest in her writing: she makes "
                            "the change herself, and nothing in the file changes. The passage must be her exact words, copied "
                            "from read_pinned. Never write her text for her beyond the small fix you suggest.",
                            s({"name": {"type": "string", "description": "The document's name, as listed."},
                               "passage": {"type": "string", "description": "The exact words you mean, copied from the document."},
                               "suggestion": {"type": "string", "description": "The wording you suggest instead, or empty for a comment only."},
                               "why": {"type": "string", "description": "Why, in a sentence."},
                               "part": {"type": "integer", "description": "The part you read it in, if you know."}},
                              ["name", "passage"]) | {"additionalProperties": False}, show_passage)] if self._pinned_names() else []
        server_tools = [
            make("add_tool_server", "Add the owner's own MCP tool server by its web address, when she says something like "
                 "\"add my tool server at http://127.0.0.1:8000/mcp\". BuildBot connects to it first and keeps it only if "
                 "it answers; its tools then show up for you. Never ask for a password in chat: if it needs one, the "
                 "Developer page takes it.",
                 s({"name": {"type": "string", "description": "A short name for it."},
                    "address": {"type": "string", "description": "Its web address, starting with http:// or https://."}},
                   ["name", "address"]) | {"additionalProperties": False}, add_tool_server, changes=True,
                 question=server_question("add")),
            make("remove_tool_server", "Remove a tool server the owner added, and its tools.",
                 s({"name": {"type": "string", "description": "Its name or id, as listed."}}, ["name"])
                 | {"additionalProperties": False}, remove_tool_server, changes=True,
                 question=server_question("remove")),
            make("list_tool_servers", "List the tool servers the owner added, with how many tools each has.",
                 s({}, []), list_tool_servers),
        ] if self.outside is not None and (asked is None or asked & SERVER_WORDS) else []
        # D138: only when she added a desktop program, so no other bot spends a tool slot on it.
        open_tool = [make(programs.OPEN_TOOL, programs.OPEN_WORDS, programs.OPEN_SCHEMA,
                          lambda a, c: programs.open_tool(self.state, a, c),
                          question=lambda a, c: programs.open_question(self.state, a, c))] \
            if programs.has_desktop(self.state) else []
        return skill_tool + send_tool + pinned_tool + server_tools + open_tool + [
            make("list_installed", "List everything installed on this bot: apps, tools, pages and more, "
                 "with each one's id, status and what it does.", s({}, []), list_installed),
            make("open_package", "Get the tools of one installed package by its id, to use them in this turn.",
                 s(idp, ["id"]), open_package),
            make("search_knowledge", "Search the owner's knowledge on this computer: the books and articles on her Knowledge page, "
                 "her linked folders and her documents. This is her knowledge; the Document search app is a separate, smaller store. "
                 + ADD_DOCUMENTS,
                 s({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]), search_knowledge),
            make("checklist_add", "Put a reminder on the owner's Checklist (a 'Your turn' card with a Done tick), "
                 "only when the owner asks to be reminded of something or to tick off a step by hand, like \"remind me "
                 "to water the plants\". Never for answering a question, never for a command to run, and never "
                 "to add an app, a tool or a GitHub project: that is ask_toolsmith.",
                 s({"title": {"type": "string"}, "why": {"type": "string"},
                    "kind": {"type": "string", "enum": list(CHAT_STEP_KINDS)}},
                   ["title", "why"]) | {"additionalProperties": False}, checklist_add),
            make("ask_toolsmith", "Add an app or tool to this bot, or find books to download for its library. Call it whenever "
                 "the owner says add, install or get an app, a tool, a GitHub address or books (\"add "
                 "github.com/owner/repo\", \"add an app for my plants\", \"find books on gardening\"), or asks for a job no "
                 "installed tool can do. Toolsmith makes a plan, the owner says "
                 "Yes or No on its card, then Toolsmith builds it. Nothing is added without this tool. While a build "
                 "the owner asked for in this chat is still running, her new words are added to that build.",
                 s({"job": {"type": "string", "description": "What to add, in the owner's words, with any "
                                                              "GitHub address as the owner gave it."},
                    "first": {"type": "boolean",
                              "description": "true only when the owner says this one comes first or wants a new "
                                             "build, like \"do this first\", \"instead\", \"this is more "
                                             "important\" or \"start a new one\". Otherwise leave it out."}},
                   ["job"]),
                 ask_toolsmith),
            make("read_app", "See what is on the page of an app on this computer (its headlines, lists, numbers "
                 "and links), to answer questions about it or summarize it. A stopped app is started first.",
                 s({**idp, "path": {"type": "string", "description": "Another page of it, like /news."}}, ["id"]),
                 read_app, question=lambda a, _c: apps.read_question(self._find, a)),
            make("open_app", "Open an app's own page in the owner's browser, in a new tab.", s(idp, ["id"]),
                 open_app),
            make("arrange_home", "Change the owner's Home page (her dashboard). add_app puts an installed app's own "
                 "page on Home as its own tile (\"put World Monitor on my dashboard\"); remove_app takes it off; "
                 "apps_as_pages shows every app's page on Home, apps_as_tiles only small tiles; pin, unpin, hide, "
                 "show, move (position: first, last or a number) and rename (name) work on any tile. Never for "
                 "installing something new: that is ask_toolsmith.",
                 s({"action": {"type": "string", "enum": list(HOME_ACTIONS)},
                    "tile": {"type": "string", "description": "The app or tile, in the owner's words or by id."},
                    "position": {"description": "For move: first, last, or a number from 1."},
                    "name": {"type": "string", "description": "For rename: the new name."}}, ["action"])
                 | {"additionalProperties": False}, arrange_home),
            make(commands.TOOL, commands.description(self.state), commands.SCHEMA,
                 lambda a, c: commands.run(self.state, a, c), question=lambda a, c: commands.question(self.state, a, c)),
        ]


__all__ = ["CORE_TOOL_NAMES", "Concierge", "OPEN_IN_PROGRAM", "READ_PINNED", "SEND_DOCUMENT", "SHOW_PASSAGE", "SERVER_TOOL_NAMES", "SKILL_VIEW",
           "words"]
