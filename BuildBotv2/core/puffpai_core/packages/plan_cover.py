"""The plan's ONE Yes covers an adopted app (03 3.3 "The ONE Yes and later adds").

When Toolsmith hands a working app to the Package Manager, the Core names the build plan the owner
said Yes to: ``{"consent_id", "envelope", "candidate", "job", "ticked"}`` (``ticked``: the Core's own
record of each "Your turn" step she ticked Done during this build, ``{step id: {"sha", ...}}``, written
only by the Core when she ticks it, ``toolsmith_host.service``). The Package Manager does not take the
plan on trust. :func:`check` reads the plan's Yes from the consent store itself and compares:

- the plan's Yes is live (granted, sealed, not expired), for a Toolsmith build, and its
  ``plan_sha`` is the sha of exactly this envelope (so the envelope is the one she saw);
- the candidate is on the plan (one job's candidate list) and the manifest's ``origin`` is that
  candidate's own address (``adopted:https://github.com/<owner>/<repo>``, or codeberg.org's);
- what the app needs, against what the plan covered:
  - hosts (run and install) must be among the plan's install hosts;
  - keys: the plan lists none, so any key is outside it;
  - setup steps (D60): a step is inside only when she ticked that exact step during this build
    (its id is in ``ticked`` and its fingerprint, :func:`step_sha`, is the one she ticked: the
    same kind, words, command and shell). A step she did not tick, or one that changed after she
    ticked it (same id, another program or arguments), is outside. An admin step must also be on
    the plan's setup list. A step that names a tool Drawbridge's one rule (D57) calls changing is
    outside, ticked or not;
  - tools that change things without asking (``abilities.changes``, or any tool Drawbridge's
    check classed as changing): the plan listed none, so any is outside;
  - every command the Core itself will start from the manifest (``run.<os>.setup`` and ``start``,
    a ``health`` command, a stdio MCP server, ``ui.open``; :func:`core_commands`): inside only when it
    is exactly a command the broker ran with success in this build's try of this app (``ran``: the
    broker's own record, kept in the Core's memory, ``toolsmith_host.broker.Broker.record``; no
    Toolsmith route writes it). Exactly means the same program, found the same way
    (:func:`program_of`), the same arguments and the same folder, with the try's own port, folder and
    Python put in for the placeholders. A setup step that ran only as a server, a native program that
    changed since it ran, and a run block ``env`` beyond ``PORT={port}`` and ``HOST=127.0.0.1`` (all a try
    ever had) are outside too. Her card then lists each such command exactly;
  - Drawbridge's answer: a "warn" (something to know first) is outside, ticked or not (D60); files
    Drawbridge could not check (a "hold") are outside, since the plan's Yes never saw them, unless
    she said Yes to exactly those files, unchanged since, on the try's own hold card (``plan["held"]``,
    the Core's record of that card, :func:`held_record`; one card per job listing every candidate set
    aside, each with its own fingerprint, lane I4b).

Inside the plan: ``(plan record, [])``, and the Package Manager installs with no new card for her
(the card is still made and recorded as answered by the plan's Yes, so the Bridge grants, the
Registry's ``yes_record`` and the audit trail stay one path). Outside it: ``(plan record, [plain
lines])``, and her card shows just those lines first. A plan that can't be checked: ``(None, [])``,
and the full card asks her as before.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from pathlib import Path
from typing import Any

BUILD_TARGET = "toolsmith-build-"
#: The fields of a setup step that say what it is and what it runs (the manifest step shape, 02).
STEP_FIELDS = ("id", "kind", "title", "why", "text", "shell", "admin", "get_it", "key", "check")


def step_sha(step: Any) -> str:
    """The fingerprint of one "Your turn" step: what she saw and what it runs. The Core keeps it when she
    ticks a step; the same step in an adopted app's ``setup_steps`` must give the same one."""
    s = step if isinstance(step, dict) else {}
    kind = str(s.get("kind") or "confirm")
    out: dict = {}
    for k in STEP_FIELDS:
        v = s.get(k)
        if k == "title":
            v = " ".join(str(v or "").split())
        elif k == "shell":
            # The shell the Core gives a step that names none (ToolsmithHost.agent_step_add), so both sides match.
            v = str(v or (("powershell" if os.name == "nt" else "bash") if kind == "run_command" else ""))
        elif k == "kind":
            v = kind
        elif k == "admin":
            v = v is True
        elif k == "check":
            v = v if isinstance(v, dict) else None
        else:
            v = "" if v is None else str(v)
        out[k] = v
    return hashlib.sha256(json.dumps(out, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def envelope_sha(envelope: Any) -> str:
    """The same sha the Toolsmith host keeps as ``plan_sha`` (``toolsmith_host.store.sha_of``)."""
    return hashlib.sha256(json.dumps(envelope, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def plan_record(consent: Any, plan: Any) -> dict | None:
    """The live plan Yes that *plan* names, or None (not granted, not a build, or another envelope)."""
    if not isinstance(plan, dict) or not isinstance(plan.get("envelope"), dict):
        return None
    try:
        rec = consent.granted(str(plan.get("consent_id") or ""))
    except Exception:  # noqa: BLE001 - a store that can't answer covers nothing
        return None
    if not isinstance(rec, dict):
        return None
    scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
    covers = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
    target = str(scope.get("target") or "")
    if scope.get("kind") != "package" or not target.startswith(BUILD_TARGET):
        return None
    build = str(covers.get("build") or "")
    if not build or build.rsplit("-", 1)[-1] != target[len(BUILD_TARGET):]:
        return None
    if covers.get("plan_sha") != envelope_sha(plan["envelope"]):
        return None
    return rec


def _names(items: Any) -> list[str]:
    out = []
    for x in items if isinstance(items, list) else []:
        name = x.get("name") if isinstance(x, dict) else x
        if isinstance(name, str) and name:
            out.append(name)
    return out


# -- the commands the Core starts from a manifest, against what ran in the try (D60, I2a part C) ----------

#: Program words a try's record may hold. Anything else (``other:``) is never covered by a try.
TRY_PROGRAMS = ("{python}", "{node}", "{npm}", "{npx}")
#: The run block ``env`` a try's programs had (the broker gives a server PORT and HOST, nothing else).
TRY_ENV = {"PORT": "{port}", "HOST": "127.0.0.1"}
_NO_VALUE = "\x00no-value-in-a-try\x00"


def _norm(path: Any) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def program_of(argv: list, folder: Any, python: str = "", node: str = "") -> tuple[str, list[str]]:
    """``(word, args)``: the one way the Core names the program of an argument list it starts, for a
    command the broker ran in a try and for one a manifest names. ``{python}`` is the app's own .venv
    Python (*python*), ``{node}`` the known Node.js (*node*), ``{npm}`` / ``{npx}`` that node with npm's
    own script, ``pkg:<path>`` a program file inside *folder* (not in its .venv); anything else
    (a name looked up on the PATH, a relative path, a program outside the app) is ``other:<as written>``."""
    from ..steward import noshell
    words = [str(a) for a in argv or []]
    if not words:
        return "other:", []
    first, rest = words[0], words[1:]
    if not os.path.isabs(first):
        return "other:" + first, rest
    n = _norm(first)
    if python and n == _norm(python):
        return "{python}", rest
    if node and n == _norm(node):
        for tool in ("npm", "npx"):
            cli = noshell.npm_cli(node, tool)
            if rest and cli is not None and _norm(rest[0]) == _norm(cli):
                return "{" + tool + "}", rest[1:]
        return "{node}", rest
    root = _norm(folder).rstrip("\\/") if folder else ""
    if root and n.startswith(root + os.sep):
        rel = n[len(root) + 1:].replace("\\", "/")
        if rel.split("/")[0] not in (".venv", "venv", "node_modules"):
            return "pkg:" + rel, rest
    return "other:" + first, rest


def rel_cwd(cwd: Any) -> str:
    """A run block's ``cwd`` (or a try's folder inside the app) in one form: ``""`` is the app's folder."""
    raw = str(cwd or "").replace("\\", "/").strip().strip("/")
    norm = posixpath.normpath(raw) if raw else ""
    norm = "" if norm == "." else norm
    return os.path.normcase(norm).replace("\\", "/")


def core_commands(manifest: dict) -> list[dict]:
    """Every command the Core itself starts from *manifest*, as written (placeholders not filled):
    ``[{"where", "kind" run|start, "argv", "cwd", "setup"}]``. A Core file step (``{"copy"}``,
    ``{"venv"}``) is not a command."""
    from ..steward.steward import run_block, setup_argv, split_command
    out: list[dict] = []
    block = run_block(manifest) or {}
    if block.get("type") == "command":
        cwd = block.get("cwd") or ""
        for i, item in enumerate(block.get("setup") or [], 1):
            parts = setup_argv(item)
            if parts is not None:
                out.append({"where": f"setup step {i}", "kind": "run", "argv": parts, "cwd": cwd, "setup": True})
        if block.get("start"):
            out.append({"where": "start", "kind": "start", "argv": [str(a) for a in block["start"]], "cwd": cwd})
    health = manifest.get("health") if isinstance(manifest.get("health"), dict) else {}
    if health.get("command"):
        out.append({"where": "health check", "kind": "run", "argv": [str(a) for a in health["command"]], "cwd": ""})
    mcp = (manifest.get("abilities") or {}).get("mcp") or {}
    if isinstance(mcp, dict) and mcp.get("transport") == "stdio" and mcp.get("command"):
        cmd = mcp["command"]
        out.append({"where": "its MCP server", "kind": "start", "cwd": "",
                    "argv": split_command(cmd) if isinstance(cmd, str) else [str(a) for a in cmd]})
    program = (manifest.get("ui") or {}).get("open")
    if isinstance(program, dict) and program.get("program"):
        out.append({"where": "its Open button", "kind": "start", "cwd": "",
                    "argv": [str(program["program"]), *[str(a) for a in program.get("args") or []]]})
    return out


def _filled(cmd: dict, block: dict, ran: dict) -> list[str]:
    """*cmd*'s arguments with the try's own values for the placeholders (what it would have been in the
    try): its port (none in setup, as Steward runs setup), folder, .venv Python (only with ``python: true``;
    else the Core's own Python, as Steward fills it), Node.js. ``{data}`` never existed in a try."""
    import sys
    from ..steward import noshell
    from ..steward.steward import fill
    values = {"port": "" if cmd.get("setup") else str(ran.get("port") or _NO_VALUE),
              "python": str(ran.get("python") or _NO_VALUE) if block.get("python") else sys.executable,
              "node": str(ran.get("node") or "node"), "data": _NO_VALUE, "pkg": str(ran.get("folder") or _NO_VALUE)}
    argv = [fill(a, values) for a in cmd["argv"]]
    try:
        return noshell.resolve(argv, node=str(ran.get("node") or ""))
    except noshell.NoShell:
        return ["\x00batch\x00", *argv]


def _program_sha(folder: Any, word: str) -> str:
    try:
        h = hashlib.sha256()
        with open(Path(folder) / word[len("pkg:"):], "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, TypeError):
        return ""


def _said(cmd: dict) -> str:
    words = " ".join(w if w and not re.search(r'[\s"]', w) else '"' + w.replace('"', "'") + '"'
                     for w in cmd["argv"])
    where = rel_cwd(cmd.get("cwd"))
    return f"{cmd['where']}: {words}" + (f" (in its {where} folder)" if where else "")


def commands_outside(manifest: dict, ran: Any, folder: Any = None) -> list[str]:
    """Plain lines for the commands the Core would start from *manifest* that did not run, exactly, in
    this build's try (*ran*: the broker's record); [] when every one did. *folder*: the staged app, where
    a ``pkg:`` program must still be the file that ran."""
    from ..steward.steward import run_block
    ran = ran if isinstance(ran, dict) else {}
    done = [c for c in ran.get("commands") or [] if isinstance(c, dict)]
    block = run_block(manifest) or {}
    not_ran: list[str] = []
    for cmd in core_commands(manifest):
        argv = _filled(cmd, block, ran)
        word, args = program_of(argv, ran.get("folder") or "", str(ran.get("python") or ""),
                                str(ran.get("node") or ""))
        cwd = rel_cwd(cmd.get("cwd"))
        hit = False
        if word in TRY_PROGRAMS or word.startswith("pkg:"):
            sha = _program_sha(folder, word) if word.startswith("pkg:") and folder is not None else ""
            for c in done:
                if (c.get("kind") == cmd["kind"] and c.get("program") == word and c.get("args") == args
                        and rel_cwd(c.get("cwd")) == cwd
                        and (not word.startswith("pkg:") or (sha and c.get("sha") == sha))):
                    hit = True
                    break
        if not hit:
            not_ran.append(_said(cmd))
    out = []
    if not_ran:
        out.append("BuildBot would run commands that did not run in its try during this build: "
                   + "; ".join(not_ran) + ".")
    if block.get("type") == "command":
        env = block.get("env") if isinstance(block.get("env"), dict) else {}
        extra = sorted(f"{k}={v}" for k, v in env.items() if TRY_ENV.get(k) != str(v))
        if extra:
            out.append("Its programs get settings they did not have in its try: " + ", ".join(extra) + ".")
    return out


def _changing_tools(manifest: dict, tools: Any) -> set[str]:
    """Every tool the manifest or Drawbridge's check names that Drawbridge's one rule (D57) calls changing.
    With no rule to ask (Drawbridge not here) every named tool counts as changing."""
    from .. import seams
    ab = manifest.get("abilities") or {}
    declared = set(_names(ab.get("changes")))
    named = declared | set(_names(ab.get("watch")))
    for key in ("examples", "accepts_labels"):
        named |= {k for k in (ab.get(key) or {}) if isinstance(k, str) and k}
    tools = tools if isinstance(tools, dict) else {}
    named |= {k for k in tools if isinstance(k, str) and k}
    rule = seams.find("zone3.drawbridge.tool_changes")
    origin = str(manifest.get("origin") or "local")
    out = set()
    for name in named:
        try:
            changing = True if rule is None else bool(rule(name, manifest, None, origin))
        except Exception:  # noqa: BLE001 - a rule that breaks counts as changing (fail closed)
            changing = True
        if changing or tools.get(name) == "changing" or name in declared:
            out.add(name)
    return out


def _words(step: dict) -> set[str]:
    text = " ".join(str(step.get(k) or "") for k in ("title", "why", "text", "key", "get_it"))
    return {w.strip(".-") for w in re.findall(r"[A-Za-z0-9_.-]+", text)}


def held_sha(files: Any) -> str:
    """The fingerprint of a hold card's files (``{rel: sha256}``), the same on the card and at install."""
    return hashlib.sha256(json.dumps(files if isinstance(files, dict) else {}, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def held_record(consent: Any, plan: Any) -> dict | None:
    """``{rel: sha256}`` of the held files she said Yes to on the job's hold card, or None.

    *plan* carries ``held`` from the Core's own try record (``{"consent_id", "files"}``, never a route's).
    The Yes is read from the consent store itself: live, a hold card that lists this same candidate, and
    its fingerprint for it the one of these files."""
    held = plan.get("held") if isinstance(plan, dict) else None
    if not isinstance(held, dict) or not isinstance(held.get("files"), dict) or not held["files"]:
        return None
    try:
        rec = consent.granted(str(held.get("consent_id") or ""))
    except Exception:  # noqa: BLE001 - a store that can't answer covers nothing
        return None
    card = (rec or {}).get("card") if isinstance(rec, dict) else None
    scope = rec.get("scope") if isinstance(rec, dict) and isinstance(rec.get("scope"), dict) else {}
    if not isinstance(card, dict) or scope.get("kind") != "package":
        return None
    listed = [h for h in card.get("try_holds") or [] if isinstance(h, dict)]
    if isinstance(card.get("try_hold"), dict):
        listed.append(card["try_hold"])
    hold = next((h for h in listed if h.get("candidate") == plan.get("candidate")), None)
    if hold is None or hold.get("held_sha") != held_sha(held["files"]):
        return None
    return {str(k): str(v) for k, v in held["files"].items()}


def _held_row(checked: Any) -> dict | None:
    rows = checked.get("rows") if isinstance(checked, dict) else None
    return next((r for r in rows or [] if isinstance(r, dict) and r.get("code") == "cant_check"), None)


def held_covered(held: Any, checked: Any, folder: Any) -> bool:
    """True only when every file Drawbridge holds now is one she said Yes to during the try, unchanged
    (the same bytes, or the same link target), and nothing else holds it. A cut walk, an unknown
    reason or no folder to hash is never covered. A tunnel is covered like any held file: one Yes, and that card says
    the Yes also keeps the app (D117)."""
    if not isinstance(held, dict) or not held or folder is None or not isinstance(checked, dict):
        return False
    if [str(c) for c in checked.get("reasons") or []] != ["cant_check"]:
        return False
    row = _held_row(checked)
    names = row.get("all") if isinstance(row, dict) else None
    if not isinstance(names, list) or not names or row.get("cut") is not False:
        return False
    if any(not isinstance(n, str) or n not in held for n in names):
        return False
    try:
        from ..zone3.drawbridge import held_fingerprints
        now = held_fingerprints(folder, names)
    except Exception:  # noqa: BLE001 - can't hash: not covered
        return False
    return all(now.get(n) == held[n] for n in names)


def other_notes(checked: Any) -> list[str]:
    """Drawbridge's notes besides the files it couldn't check (hosts it saw, lines hidden, size)."""
    rows = checked.get("rows") if isinstance(checked, dict) else None
    return [str(r.get("why") or r.get("code")) for r in rows or []
            if isinstance(r, dict) and r.get("result") in ("note", "fail") and r.get("code") not in ("cant_check", "")]


def difference(manifest: dict, envelope: dict, candidate: str, verdict: dict | None = None,
               job: str | None = None, ticked: Any = None, tools: Any = None, ran: Any = None,
               folder: Any = None, held: Any = None, checked: Any = None) -> list[str]:
    """What *manifest* needs that the plan did not cover, as plain lines ([] = all inside it).

    *ticked*: the Core's record of the steps she ticked during this build (``{id: {"sha"}}``);
    *tools*: Drawbridge's ``{tool: "changing" | "read_only"}`` from its check; *ran*: the broker's record
    of what ran in the try (no record: every command the Core would start is outside); *folder*: the
    staged app."""
    out: list[str] = []
    jobs = [j for j in envelope.get("jobs") or [] if isinstance(j, dict)]
    if job:
        jobs = [j for j in jobs if j.get("name") == job] or jobs
    on_plan = any(candidate in (j.get("candidates") or []) for j in jobs)
    kind, _, full = candidate.partition(":")
    home = {"github": "github.com", "codeberg": "codeberg.org"}.get(kind, "")
    if not on_plan or not home or not full:
        out.append("This app was not on the plan you said Yes to.")
    elif manifest.get("origin") != f"adopted:https://{home}/{full}":
        out.append(f"Its puffpai.json does not say it comes from {home}/{full}, the app on your plan.")
    if manifest.get("kind") != "app" and not (manifest.get("kind") == "tool" and on_plan):    # D124: a service with no page
        out.append(f"It is a {manifest.get('kind')} package, and your plan covered apps only.")
    allowed = {str(h).lower() for h in envelope.get("install_hosts") or []}
    # A cloud plan's install hosts are wide so a found app can install (D121). They never widen what an app keeps at run.
    kept = {str(h).lower() for h in envelope.get("plan_hosts", envelope.get("install_hosts")) or []}
    needs = manifest.get("needs") or {}
    run_hosts = [h for h in _names(needs.get("hosts")) if h.lower() not in kept]
    if run_hosts:
        out.append("It reaches places on the internet your plan did not list: " + ", ".join(run_hosts) + ".")
    inst = [h for h in _names(needs.get("install_hosts")) if h.lower() not in allowed]
    if inst:
        out.append("While it installs, it reaches places your plan did not list: " + ", ".join(inst) + ".")
    keys = _names(needs.get("keys"))
    if keys:
        out.append("It uses keys your plan did not list: " + ", ".join(keys) + ".")
    if needs.get("admin"):
        out.append("It needs administrator rights, which your plan did not list.")
    if needs.get("addresses"):
        out.append("It may ask to reach an address of its own, which your plan did not list. Each one needs its own Yes.")
    planned = set(envelope.get("setup") or [])
    ticked = ticked if isinstance(ticked, dict) else {}
    changing = _changing_tools(manifest, tools)
    admin, unticked, changed, uses = [], [], [], []
    for s in manifest.get("setup_steps") or []:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or s.get("id"))
        sid = s.get("id")
        tick = ticked.get(sid) if isinstance(sid, str) else None
        named = sorted(changing & _words(s))
        if named:
            uses.append(f"{title} ({', '.join(named)})")
        if s.get("admin") and sid not in planned:
            admin.append(title)
        elif not isinstance(tick, dict):
            unticked.append(title)
        elif tick.get("sha") != step_sha(s):
            changed.append(title)
    if admin:
        out.append("It has administrator steps your plan did not list: " + "; ".join(admin) + ".")
    if unticked:
        out.append("It has setup steps you did not tick Done during this build: " + "; ".join(unticked) + ".")
    if changed:
        out.append("Some setup steps are not the ones you ticked (what they run changed after you did them): "
                   + "; ".join(changed) + ".")
    if uses:
        out.append("Some setup steps use tools that change things: " + "; ".join(uses) + ".")
    out += commands_outside(manifest, ran, folder)
    changes = sorted(set(_names((manifest.get("abilities") or {}).get("changes")))
                     | {k for k, v in (tools if isinstance(tools, dict) else {}).items() if v == "changing"})
    if changes:
        out.append("It has tools that change things without asking you: " + ", ".join(changes) + ".")
    shown = verdict or {}
    tried = bool(held)                          # her Yes on the job's hold card, read from the store (held_record)
    if shown.get("verdict") == "hold" and held_covered(held, checked, folder):
        # Her Yes on this try's hold card covers exactly these files, unchanged since (one card for one
        # candidate). Anything else Drawbridge noted is still outside, as a warn is (D60).
        notes = other_notes(checked)
        if notes:
            out.append("Drawbridge found something to know first, and your plan's Yes did not cover it: "
                       + "; ".join(notes[:3]) + ".")
    elif shown.get("verdict") == "warn":
        said = "; ".join(str(n) for n in (shown.get("notes") or [])[:3] if n) or str(shown.get("line") or "")
        out.append("Drawbridge found something to know first, and your plan's Yes did not cover it"
                   + (f": {said}" if said else "") + ".")
    elif shown.get("verdict") != "allow":
        said = [str(x) for x in shown.get("held_said") or [] if isinstance(x, str)]
        held = [str(f).rsplit("/", 1)[-1] for f in shown.get("held") or [] if isinstance(f, str)]
        if shown.get("held_tunnel") is True:         # the card's Drawbridge line says which ones (D117)
            out.append("Its code can start a tunnel. "
                       + ("Its files are not the ones you said Yes to." if tried
                          else "Your plan's Yes never saw it."))
        elif shown.get("held_docs_only") is True:    # the card's Drawbridge line says which ones (D105)
            out.append("Some of its docs hold words aimed at an AI, and your plan's Yes never saw them.")
        else:
            out.append("Drawbridge couldn't check some of its files, and your plan's Yes never saw them."
                       + (" " + " ".join(said[:4]) if said else f" ({', '.join(held[:3])})" if held else ""))
    return out


def check(consent: Any, plan: Any, manifest: dict, shown_verdict: dict | None,
          tools: Any = None, folder: Any = None, checked: Any = None) -> tuple[dict | None, list[str]]:
    """``(plan record, difference lines)``; ``(None, [])`` when *plan* is not a live, matching plan Yes.
    *checked* is Drawbridge's whole verdict (its rows), for a hold she already said Yes to in the try."""
    rec = plan_record(consent, plan)
    if rec is None:
        return None, []
    return rec, difference(manifest, plan["envelope"], str(plan.get("candidate") or ""), shown_verdict,
                           str(plan.get("job") or "") or None, plan.get("ticked"), tools, plan.get("ran"),
                           folder, held_record(consent, plan), checked)


__all__ = ["BUILD_TARGET", "STEP_FIELDS", "TRY_ENV", "TRY_PROGRAMS", "check", "commands_outside", "core_commands",
           "difference", "envelope_sha", "held_covered", "held_record", "held_sha", "other_notes", "plan_record", "program_of", "rel_cwd", "step_sha"]
