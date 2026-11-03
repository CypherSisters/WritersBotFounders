"""``puffpai``: the command line (04 "The CLI").

- ``serve``: the Core API on 127.0.0.1:8420 and the pages address on
  127.0.0.1:8422 (D49); the Desktop at http://puffpai.localhost:8420 (D79).
  At every start it revokes the earlier terminal keys and writes a new
  paired-device key, good for this Core run only, to
  ``settings\\terminal.json`` (removed at stop), which ``add``, ``remove``,
  ``start`` and ``stop`` use to talk to it. One Core per bot (D91): while one
  runs, a second ``serve`` (or ``setup``) says so plainly and starts nothing.
  A key the running Core refuses (401) is said in the owner's words
  (:data:`KEY_REFUSED`), never the API's.
- ``add <folder | git URL | built-in id | store:<id>> [--link]``: shows the one Yes card and asks in the
  terminal (the PIN for a floor-level card, hidden). With the Core running it
  goes through the Core API and the package starts; without it, it installs in
  this process and says how to start it.
- ``update [<id>]`` (lane I13): the built-ins compared with this BuildBot's own copy. A system part
  (the Desktop, setup, the cloud avatar) is updated at once; an app shows its Yes card here. With an
  id that is not a built-in, that package is fetched again from its source, with its Yes card.
- ``store list | sources | add-source <url|folder> | remove-source <url|folder> | updates`` (lane store, D89):
  the PuffPAI Store's rows (the official store first, then her own sources), her sources, and installed store
  packages whose store pins a newer commit (``puffpai update <id>`` applies one, with its Yes card). Through
  the running Core when there is one, else read and written here.
- ``remove <id> [--keep-data | --delete-data]``, ``start <id>``, ``stop <id>``
  (these two need the running Core: packages belong to the Core's process, D18).
- ``list``, ``logs <id>``, ``check <folder>`` and ``notices`` read files directly. ``notices`` lists
  what the Desktop shows: the Core's cards (safe mode, the Bridge, an app stopped) and packages' notices.
  ``serve`` also prints each Core card in its terminal as it comes.
- ``bridge [status | open | close]`` (the running Core): in safe mode ``open`` shows the reason first and
  asks for the PIN (D29); without it the Bridge stays closed.
- ``setup`` (the wizard in the terminal) and ``pair`` (a one-time link that
  signs a browser in to the Desktop) are lane L5's.
- ``apikey new [--label <words>] | list | revoke <label>`` (lane D137): program keys (``ppk_``, label
  ``auth.PROGRAM_LABEL``) for Home Assistant and other programs, in ``tokens.db``, with the Core running or not.
  A program key opens only the chat routes for programs (``/openai/v1``, ``/ollama/api``), never an owner route, on this
  computer and on the home network (D147, PROPOSED). ``new`` prints the key once, and the addresses a program uses;
  ``list`` shows labels and dates, never a key; list and revoke see program keys only.
- ``start`` with no id (D94, ``Start BuildBot.cmd``): BuildBot once, in its own window (never a second Core
  for one bot), then the chat here, or the dashboard or the window when she picked that
  (``--opens chat|dashboard|window``).
- ``app`` (D81, ``puffpai_core.window``): BuildBot once, then the PuffPAI window as its own process, and this
  terminal is free. ``--shortcuts`` makes its Start menu and desktop shortcuts, ``--no-shortcuts`` removes them.
- ``shutdown`` (lane I7): stops the running Core from any window, the same stop as Ctrl+C in its own
  (``Stop BuildBot.cmd`` runs it).
- ``puffpai`` with no words (D86): the bot's chat in this terminal, a thin client of the running Core
  (``termchat``). The same verbs for scripts: ``build "<goal>"``, ``plan [build]``, ``yes <card>``,
  ``no <card>``, ``watch [build]``, ``turn [done|skip <n>]``, ``vault [status|unlock|lock|recover]``. They never start
  the Core; with none running they say so and how to start it.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

#: What ``puffpai new`` can start (templates/<kind>/).
NEW_KINDS = ("tool", "app", "skill", "page", "avatar", "look")
NEW_ID = r"[a-z][a-z0-9-]{1,39}"


# -- talking to a running Core ----------------------------------------------
def _terminal() -> dict | None:
    """This bot's running Core (``settings\\terminal.json``), or None. A Core counts only when it takes this
    terminal's key and says it runs for this bot (``GET /v1/core``, D91): a stale terminal.json can point at a
    port another bot's Core holds now, and that Core refuses the key (401) or names another bot. Anything else
    (no answer, an older Core without that route) is not this bot's Core either."""
    from .corelock import bot_id
    from .paths import state_dir
    state = state_dir()
    path = state / "settings" / "terminal.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(info, dict) or not isinstance(info.get("token"), str):
            return None
        int(info["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        _call(info, "GET", "/v1/health")
        status, body = _call(info, "GET", "/v1/core")
    except OSError:
        return None
    if status != 200 or not isinstance(body, dict) or body.get("bot_id") != bot_id(state):
        return None
    return info


#: What the owner reads when the running Core refuses this terminal's key (401, D91). The API's own words are
#: for programs; this line is hers, in plain words.
KEY_REFUSED = ("BuildBot did not accept this terminal's key. Close the BuildBot window to stop it, "
               "then start it again.")


def owner_words(status: int, body: dict | str) -> dict | str:
    """A 401 reply as the owner reads it (D91): :data:`KEY_REFUSED`, never the API's technical text. Only the Core's own
    sign-in refusal counts (no code, or ``sign_in``, as the Desktop's logic.js reads it): a route's own 401, like a wrong
    PIN or recovery code, keeps the route's plain words."""
    own = isinstance(body, dict) and body.get("code") not in (None, "", "sign_in")
    return {"error": KEY_REFUSED} if status == 401 and not own else body


#: This bot's Core holds its lock but has not written its terminal key yet.
STARTING = "BuildBot is still starting. Try again in a moment."


def held_line(info: dict) -> str:
    """Why this bot can't be reached or changed from here while something holds its lock (D91): a
    ``puffpai`` command is busy, its Core is still starting, or its Core refused this terminal's key (or is
    older than ``GET /v1/core``). Never "not running": something runs."""
    from . import corelock
    from .paths import state_dir
    if info.get("kind") == corelock.TOOL:
        return corelock.BUSY_LINE
    if not (state_dir() / "settings" / "terminal.json").is_file():
        return STARTING
    return KEY_REFUSED


def held_now() -> str | None:
    """When :func:`_terminal` found no Core it can use: :func:`held_line` if something holds this bot's lock,
    else None (nothing runs for this bot)."""
    from . import corelock
    from .paths import state_dir
    info = corelock.holder(state_dir())
    return None if info is None else held_line(info)


def _offline_lock():
    """This bot's lock for a command that changes the bot with no Core running (add, remove, update; D91),
    held until the command ends. None after saying why it can't be had (a Core or another command runs)."""
    from . import corelock
    from .paths import state_dir
    try:
        return corelock.acquire(state_dir(), port=0, kind=corelock.TOOL, wait_s=corelock.PROBE_WAIT_S)
    except corelock.AlreadyRunning as exc:
        print(held_line({"kind": corelock.CORE, **exc.info}))
    except corelock.LockError as exc:
        for line in exc.lines():
            print(line)
    return None


def _offline(fn, args: argparse.Namespace) -> int:
    """Run *fn(args, None)* (a command's no-Core path) while it holds this bot's lock."""
    lock = _offline_lock()
    if lock is None:
        return 1
    try:
        return fn(args, None)
    finally:
        lock.release()


def _call(info: dict, method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    """One call to the running Core with this run's terminal key. A 401 comes back in the owner's words."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {info['token']}",
                                          "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    text = raw.decode("utf-8", errors="replace")
    try:
        return status, owner_words(status, json.loads(text))
    except ValueError:
        return status, owner_words(status, text)


def _show_card(card: dict, floor: bool | None = None) -> None:
    """The card exactly as the Desktop shows it (``consent.card_view``): everything the Yes grants."""
    from .consent import card_view
    seen = card_view(card, floor)
    print(seen["title"])
    for line in seen["lines"]:
        print(f"  {line}")


def _ask_yes(floor: bool) -> tuple[str, str, str | None]:
    reply = input("Say Yes to this? [y/N] ").strip().lower()
    if reply not in ("y", "yes"):
        return "no", "tap", None
    if floor:
        return "yes", "pin", getpass.getpass("This one needs your PIN: ")
    return "yes", "tap", None


def _problems(body) -> None:
    if isinstance(body, dict):
        for p in body.get("problems") or [body.get("error", "")]:
            print(f"  - {p}")
    else:
        print(f"  - {body}")


# -- commands -----------------------------------------------------------------
def fill_ports(args: argparse.Namespace) -> None:
    """The ports a flag did not name, from this bot's own folder (D129), picked and saved the first time. A bot that runs
    now keeps what it has: its own ports are not free, and it must not move for that."""
    from . import botports, corelock
    from .paths import state_dir
    if None not in (args.port, args.pages_port, args.proxy_port):
        return
    state = state_dir()
    if corelock.holder(state) is not None:
        ports = botports.read(state)
    else:
        ports, picked = botports.ensure(state)
        if picked and ports != botports.DEFAULT:
            print(botports.picked_line(ports, state), flush=True)
    args.port = ports["port"] if args.port is None else args.port
    args.pages_port = ports["pages_port"] if args.pages_port is None else args.pages_port
    args.proxy_port = ports["proxy_port"] if args.proxy_port is None else args.proxy_port


def _cmd_serve(args: argparse.Namespace) -> int:
    from . import corelock
    from .api import HOST, serve
    from .api.app import STOPPED_LINE, desktop_origin
    from .paths import state_dir

    fill_ports(args)
    try:                                           # D91: one Core per bot, said plainly before anything starts
        lock = corelock.acquire(state_dir(), port=args.port, pages_port=args.pages_port,
                                wait_s=corelock.START_WAIT_S)
    except corelock.LockError as exc:
        for line in exc.lines():
            print(line, flush=True)
        return 1
    print(f"PuffPAI Desktop at {desktop_origin(args.port)}/, Core API on http://{HOST}:{args.port}, "
          f"package pages on http://{HOST}:{args.pages_port} (Ctrl+C stops it)", flush=True)
    try:
        serve(port=args.port, pages_port=args.pages_port, proxy_port=args.proxy_port, lock=lock)
    except corelock.PortInUse as exc:                 # serve has let the lock go
        print(port_in_use_line(args.port, args.pages_port, exc), flush=True)
        return 1
    print(STOPPED_LINE, flush=True)
    return 0


def port_in_use_line(port: int, pages_port: int, exc: OSError) -> str:
    """A port BuildBot needs is taken by another program (D91): plain words, never a traceback."""
    why = getattr(exc, "strerror", None) or str(exc)
    return (f"BuildBot can't start: port {port} or {pages_port} is in use by another program ({why}). "
            "Close that program, or start BuildBot with another --port and --pages-port.")


def _cmd_list(args: argparse.Namespace) -> int:
    from .registry import Registry

    registry = Registry()
    rows = registry.list(kind=args.kind)
    if not rows:
        print("Nothing installed yet.")
        return 0
    print(f"{'ID':<24} {'KIND':<10} {'STATUS':<11} HEALTH")
    for row in rows:
        h = row["health"]
        health = "ok" if h["ok"] else ("not checked yet" if h["checked_at"] is None else f"not ok: {h['reason']}")
        print(f"{row['id']:<24} {row['kind']:<10} {status_word(row, registry):<11} {health}")
    return 0


#: The Registry's status words, as the owner reads them.
STATUS_WORDS = {"needs_you": "needs you"}


def status_word(row: dict, registry=None) -> str:
    """A package with no process of its own (static pages, an avatar, a static Desktop) is "ready" when
    it is not running: the Core serves its files, so there is nothing to start."""
    status = str(row.get("status") or "")
    if status == "stopped":
        from . import desktop
        from .paths import state_dir
        if not desktop.has_process(state_dir(), row["id"], registry):
            return "ready"
    return STATUS_WORDS.get(status, status)


def _cmd_check(args: argparse.Namespace) -> int:
    if args.floor:
        print("puffpai check --floor: a provider's floor suite is not built yet (lane L1, after the zone lanes)")
        return 2
    from .packages.check import check

    report = check(Path(args.folder), start_timeout_s=args.timeout)
    for line in report.lines():
        print(line)
    return 0 if report.passed() >= 1 else 1


def _cmd_add(args: argparse.Namespace) -> int:
    info = _terminal()
    return _add(args, info) if info is not None else _offline(_add, args)


def _add(args: argparse.Namespace, info: dict | None) -> int:
    if info is not None:
        status, body = _call(info, "POST", "/v1/packages", {"source": args.source, "link": args.link})
        if status != 202:
            print("Not added:")
            _problems(body)
            return 1
        _show_card(body["card"], body["floor"])
        answer, factor, pin = _ask_yes(body["floor"])
        from .consent import card_sha
        status, rec = _call(info, "POST", f"/v1/consent/{body['consent_id']}/answer",
                            {"answer": answer, "factor": factor, "pin": pin,
                             "card_sha": card_sha(body["card"])})       # the card shown above
        if status != 200:
            print("Your answer was not taken:")
            _problems(rec)
            return 1
        if answer == "no":
            print("Not added.")
            return 0
        print("Installing...")
        for _ in range(1800):
            status, row = _call(info, "GET", f"/v1/registry/{body['id']}")
            if status == 200 and row["status"] != "installing":
                from .registry import Registry
                print(f"{row['name']}: {status_word(row, Registry())}"
                      + ("" if row["health"]["ok"] else f" ({row['health']['reason']})"))
                return 0 if row["status"] in ("running", "stopped") else 1
            time.sleep(1)
        print("Still installing. Check with: puffpai list")
        return 0
    from .api import create_app
    from .api.app import wire_core
    from .packages.manager import AddError

    core = wire_core(create_app())                # the Core's own Drawbridge and audit log; nothing started
    core.packages.start_after_install = False     # nothing outlives this process; the Core starts it
    try:
        staged = core.packages.add(args.source, link=args.link)
    except AddError as exc:
        print("Not added:")
        for p in exc.problems:
            print(f"  - {p}")
        return 1
    _show_card(staged["card"], staged["floor"])
    answer, factor, pin = _ask_yes(staged["floor"])
    from .consent import ConsentError, card_sha
    try:
        core.consent.answer(staged["consent_id"], answer, factor, pin, by="terminal",
                            card_sha=card_sha(staged["card"]))          # the card shown above
    except ConsentError as exc:
        print(f"Your answer was not taken: {exc}")
        return 1
    if answer == "no":
        print("Not added.")
        return 0
    result = core.packages.wait(staged["consent_id"], timeout_s=3600)
    if result["status"] != "installed":
        print("Not installed:")
        for p in result.get("problems", []):
            print(f"  - {p}")
        return 1
    print(f"Installed {staged['id']}. Start the Core (puffpai serve), then: puffpai start {staged['id']}")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    info = _terminal()
    return _remove(args, info) if info is not None else _offline(_remove, args)


def _remove(args: argparse.Namespace, info: dict | None) -> int:
    keep = "keep" if args.keep_data else ("delete" if args.delete_data else None)
    if info is not None:
        status, body = _call(info, "DELETE", f"/v1/packages/{args.id}" + (f"?keep_data={keep}" if keep else ""))
    else:
        from .api import create_app
        from .api.app import wire_core
        from .packages.manager import AddError
        core = wire_core(create_app())
        try:
            body = core.packages.remove(args.id, keep_data=keep)
            status = 200 if body.get("removed") else 409
        except AddError as exc:
            status, body = exc.status, {"problems": exc.problems}
    if status == 409 and isinstance(body, dict) and body.get("question"):
        print(body["text"].replace("Answer keep or delete.", "Run again with --keep-data or --delete-data."))
        return 1
    if status != 200:
        print("Not removed:")
        _problems(body)
        return 1
    print(f"Removed {args.id}." + (" Its data is kept." if body.get("data_kept") else "")
          + (" Your folder is kept." if body.get("linked_folder_kept") else ""))
    return 0


def _builtin_ids() -> list[str]:
    """The installed packages that are copies of BuildBot's own built-ins (read from the files)."""
    from .packages.installs import Installs
    from .packages.manager import builtin_install
    from .paths import state_dir
    from .registry import Registry
    state = state_dir()
    registry, installs = Registry(), Installs(state)
    return [r["id"] for r in registry.list()
            if builtin_install(r["id"], r, installs.get(r["id"]), state / "packages") is not None]


def _cmd_update(args: argparse.Namespace) -> int:
    """``puffpai update [<id>]``: the built-ins (or one package) brought up to this BuildBot's own copy. A
    system part is updated at once; an app shows its Yes card here. With no id, every built-in."""
    info = _terminal()
    return _update(args, info) if info is not None else _offline(_update, args)


def _update(args: argparse.Namespace, info: dict | None) -> int:
    from .consent import card_sha
    ids = [args.id] if args.id else _builtin_ids()
    if not ids:
        print("No built-in packages are installed.")
        return 0
    core = None
    if info is None:
        from .api import create_app
        from .api.app import wire_core
        core = wire_core(create_app())            # the Core's own Drawbridge and audit log; nothing started
        core.packages.start_after_install = False
    failed = 0
    for id in ids:
        if core is None:
            status, body = _call(info, "POST", f"/v1/packages/{id}/update")
        else:
            from .packages.manager import AddError
            try:
                pm = core.packages
                body = pm.update_builtin(id, notices=core.notices) if pm.builtin_of(id) is not None \
                    else {**pm.update(id), "status": "card"}
                status = 202 if body.get("status") == "card" else 200
            except AddError as exc:
                status, body = exc.status, {"problems": exc.problems}
        if status not in (200, 202) or not isinstance(body, dict):
            print(f"{id} was not updated:")
            _problems(body)
            failed += 1
            continue
        if body.get("status") != "card":
            print(body.get("line") or f"{id}: {body.get('status')}")
            continue
        _show_card(body["card"], body["floor"])
        answer, factor, pin = _ask_yes(body["floor"])
        if core is None:
            status, rec = _call(info, "POST", f"/v1/consent/{body['consent_id']}/answer",
                                {"answer": answer, "factor": factor, "pin": pin, "card_sha": card_sha(body["card"])})
            if status != 200:
                print("Your answer was not taken:")
                _problems(rec)
                failed += 1
                continue
        else:
            from .consent import ConsentError
            try:
                core.consent.answer(body["consent_id"], answer, factor, pin, by="terminal",
                                    card_sha=card_sha(body["card"]))
            except ConsentError as exc:
                print(f"Your answer was not taken: {exc}")
                failed += 1
                continue
        if answer == "no":
            print(f"{id} was not updated.")
            continue
        ok = _wait_update(info, core, id, body["consent_id"])
        failed += 0 if ok else 1
    return 1 if failed else 0


def _wait_update(info: dict | None, core, id: str, cid: str) -> bool:
    """After her Yes on an update card: wait for the swap. True when the new version is in."""
    if core is not None:
        result = core.packages.wait(cid, timeout_s=3600)
        if result.get("status") == "installed":
            print(f"Updated {id}.")
            return True
        print(f"{id} was not updated:")
        for p in result.get("problems", []):
            print(f"  - {p}")
        return False
    print("Updating...")
    quiet = 0
    for _ in range(3600):
        status, row = _call(info, "GET", f"/v1/registry/{id}")
        if status == 200 and isinstance(row, dict):
            if row.get("yes_record") == cid and row.get("status") != "installing":
                print(f"Updated {row['name']} ({row.get('version') or '?'}).")
                return True
            quiet = 0 if row.get("status") == "installing" else quiet + 1
            if quiet >= 20:                       # never or no longer installing, and not the new version
                print(f"{id} was not updated; the old version is still in place. See: puffpai logs {id}")
                return False
        time.sleep(0.5)
    print("Still updating. Check with: puffpai list")
    return False


def _store_answer(args: argparse.Namespace) -> tuple[int, dict | str]:
    """One store call: through the running Core's API, or (none running) this bot folder's own files."""
    info = _terminal()
    fresh = "?fresh=1" if getattr(args, "fresh", False) else ""
    action, where = args.action, getattr(args, "where", None)
    if info is not None:
        from urllib.parse import quote
        if action == "list":
            return _call(info, "GET", "/v1/store" + fresh)
        if action == "sources":
            return _call(info, "GET", "/v1/store/sources")
        if action == "updates":
            return _call(info, "GET", "/v1/store/updates" + fresh)
        if action == "add-source":
            return _call(info, "POST", "/v1/store/sources", {"source": where})
        return _call(info, "DELETE", "/v1/store/sources?source=" + quote(where or "", safe=""))
    from .packages.store_index import Store, StoreError
    from .paths import state_dir
    from .registry import Registry
    store = Store(state_dir(), Registry())
    try:
        if action == "list":
            return 200, store.listing(fresh=True)
        if action == "sources":
            return 200, {"sources": store.sources()}
        if action == "updates":
            return 200, store.updates(fresh=True)
        if action == "add-source":
            return 201, store.add_source(where)
        return 200, store.remove_source(where)
    except StoreError as exc:
        return exc.status, {"error": exc.problems[0], "problems": exc.problems}


def _say_place(v: dict) -> None:
    print(f"Your books and downloads: {v.get('store')}")
    print(f"  {v.get('free')}")
    for key in ("line", "warning"):
        if v.get(key):
            print(f"  {v[key]}")
    m = v.get("move") or {}
    if m.get("phase") == "done":
        print(f"  Last move: {m.get('line')}")
    elif m:
        print(f"  {_move_line(m)}")
        if m.get("line"):
            print(f"  {m['line']}")


def _move_line(m: dict) -> str:
    from .zone1.fetched import size_said
    now = f", {m['now']}" if m.get("now") else ""
    return (f"{m.get('percent', 0)}%: {m.get('done_files', 0)} of {m.get('total_files', 0)} files, "
            f"{size_said(int(m.get('done_bytes') or 0))} of {size_said(int(m.get('total_bytes') or 0))}{now}")


def _watch_move(info: dict) -> int:
    """The running Core's move, shown here until it ends. Ctrl+C stops the watching; the move goes on."""
    seen = ""
    try:
        while True:
            status, body = _call(info, "GET", "/v1/knowledge/place")
            m = (body or {}).get("move") if status == 200 and isinstance(body, dict) else None
            if not m:
                print("The Core did not say how the move goes.")
                return 1
            line = _move_line(m)
            if line != seen and m.get("phase") not in ("done", "stopped"):
                print(line)
                seen = line
            if m.get("phase") == "done":
                print(m.get("line") or "Done.")
                return 0
            if m.get("phase") == "stopped":
                print(m.get("line") or "The move stopped.")
                return 1
            time.sleep(2.0)
    except KeyboardInterrupt:
        print("The move goes on in BuildBot. See it with: puffpai knowledge where")
        return 0


def _cmd_knowledge(args: argparse.Namespace) -> int:
    """``puffpai knowledge ...`` (D103): where, move <folder|default>, resume, cancel. Through the running Core's
    API, or (none running) this bot folder's own files, holding the bot's lock."""
    if args.action == "move" and not args.folder:
        print("Say which folder: puffpai knowledge move <folder>, or puffpai knowledge move default")
        return 2
    folder = "" if args.action != "move" or args.folder.strip().lower() == "default" else args.folder
    info = _terminal()
    if info is not None:
        if args.action == "where":
            status, body = _call(info, "GET", "/v1/knowledge/place")
        elif args.action == "move":
            status, body = _call(info, "POST", "/v1/knowledge/place", {"path": folder, "leave_old": args.leave_old})
        else:
            status, body = _call(info, "POST", f"/v1/knowledge/place/{args.action}", {})
        if status not in (200, 202) or not isinstance(body, dict):
            print("Not done:")
            _problems(body)
            return 1
        if args.action in ("move", "resume"):
            return _watch_move(info)
        _say_place(body)
        return 0
    return _offline(_knowledge_here, args)


def _knowledge_here(args: argparse.Namespace, _core: object) -> int:
    """No Core running: the move runs here and ends here. Ctrl+C stops it, and it can be picked up again."""
    from .paths import state_dir
    from .zone1 import knowledge_store as ks
    state = state_dir()

    def tell(_j: dict) -> None:
        m = ks.move_view(state) or {}
        if m.get("phase") not in ("done", "stopped"):
            print(_move_line(m))

    try:
        if args.action == "where":
            _say_place(ks.view(state))
            return 0
        if args.action == "cancel":
            _say_place(ks.cancel(state))
            return 0
        if args.action == "resume":
            ks.resume(state, wait=True, tell=tell)
        else:
            folder = "" if args.folder.strip().lower() == "default" else args.folder
            ks.start(state, folder, leave_old=args.leave_old, wait=True, tell=tell)
    except ks.MoveRefused as exc:
        print(f"Not done: {exc}")
        return 1
    m = ks.move_view(state) or {}
    print(m.get("line") or "Done.")
    return 0 if m.get("phase") == "done" else 1


def _source_lines(sources: list) -> None:
    for s in sources or []:
        if isinstance(s, dict) and (s.get("stale") or not s.get("ok", True)):
            print(f"  ({s.get('line')})")


def _cmd_store(args: argparse.Namespace) -> int:
    """``puffpai store ...`` (D89): list, sources, add-source, remove-source, updates."""
    if args.action in ("add-source", "remove-source") and not args.where:
        print(f"Say which source: puffpai store {args.action} <https address or folder>")
        return 2
    status, body = _store_answer(args)
    if status not in (200, 201) or not isinstance(body, dict):
        print("Not done:")
        _problems(body)
        return 1
    if args.action in ("add-source", "remove-source"):
        print(body.get("line") or "Done.")
        return 0
    if args.action == "sources":
        for s in body.get("sources") or []:
            print(f"{'official' if s.get('official') else 'yours':<9} {s.get('source')}")
            print(f"          {s.get('line')}")
        return 0
    if args.action == "updates":
        ups = body.get("updates") or []
        if not ups:
            print("No updates from the store.")
        for u in ups:
            have, new = u.get("have") or {}, u.get("store") or {}
            print(f"{u.get('id'):<24} {have.get('version') or '?'} ({str(have.get('commit') or '')[:12]}) -> "
                  f"{new.get('version') or '?'} ({str(new.get('commit') or '')[:12]}), {new.get('origin')}")
        if ups:
            print("Update one with: puffpai update <id> (it shows its Yes card first)")
        for n in body.get("not_found") or []:
            print(f"{n.get('id'):<24} {n.get('line')}")
        _source_lines(body.get("sources"))
        return 0
    rows = body.get("packages") or []
    if not rows:
        print("The store lists nothing right now.")
    else:
        print(f"{'ID':<24} {'KIND':<10} {'TIER':<10} {'VERSION':<9} FROM")
    for r in rows:
        mark = "" if r.get("used") else " (another store's row is used)"
        mark += " (pulled)" if r.get("yanked") else ""
        mark += " (installed)" if r.get("installed") else ""
        where = "official" if r.get("official") else r.get("source")
        print(f"{r.get('id'):<24} {r.get('kind'):<10} {r.get('tier'):<10} {str(r.get('version') or ''):<9} "
              f"{where}{mark}")
    if rows:
        print("Add one with: puffpai add store:<id>")
    _source_lines(body.get("sources"))
    return 0


def _cmd_start_stop(args: argparse.Namespace) -> int:
    info = _terminal()
    if info is None:
        print(held_now() or "The Core is not running. Start it with: puffpai serve")
        return 1
    status, body = _call(info, "POST", f"/v1/packages/{args.id}/{args.command}")
    if status != 200:
        print(f"Not {'started' if args.command == 'start' else 'stopped'}:")
        _problems(body)
        return 1
    row = body if args.command == "start" else body["row"]
    print(f"{row['name']}: {row['status']}" + (f" on port {row['port']}" if row.get("port") and row["status"] == "running" else ""))
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    from .paths import state_dir
    path = state_dir() / "logs" / f"{args.id}.log"
    if not path.is_file():
        print(f"No log for {args.id} yet.")
        return 1
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-args.lines:]:
        print(line)
    return 0


def _cmd_notices(args: argparse.Namespace) -> int:
    from .notices import Notices
    from .paths import state_dir
    items = Notices(state_dir() / "notices.json").list()[:args.lines]
    if not items:
        print("No notices.")
        return 0
    for n in items:
        print(f"{n['at']}  {n['title']} (from {n['package']})")
        if n.get("text"):
            print(f"  {n['text']}")
    return 0


def _cmd_bridge(args: argparse.Namespace) -> int:
    """``puffpai bridge [status | open | close]`` through the running Core. In safe mode, ``open`` shows the
    safe-mode reason first and asks for the PIN (hidden, D29); without it the Bridge stays closed."""
    info = _terminal()
    if info is None:
        print(held_now() or "The Core is not running. Start it with: puffpai serve")
        return 1
    status, view = _call(info, "GET", "/v1/bridge")
    if status != 200 or not isinstance(view, dict):
        print("The Bridge did not answer:")
        _problems(view)
        return 1
    safe = view.get("safe_mode") if isinstance(view.get("safe_mode"), dict) else {}
    if args.action == "status":
        # The Bridge's own policy, the one source GET /v1/settings reads too (lane I3b), in the same words
        # as the Desktop and the wizard (zone3.bridge.plain).
        lines = view.get("said") if isinstance(view.get("said"), list) else None
        if lines is None:                                  # an older Core: word it here, the same way
            from .zone3.bridge import plain
            lines = plain(view)["lines"]
        for line in lines:
            print(line)
        for r in safe.get("reasons") or []:
            print(f"Safe mode: {r}")
        return 0
    if args.action == "close":
        status, body = _call(info, "POST", "/v1/bridge/close", {})
        print("The Bridge is closed." if status == 200 else "The Bridge did not close.")
        return 0 if status == 200 else 1
    body: dict = {}
    if safe.get("on"):
        print("BuildBot is in safe mode:")
        for r in safe.get("reasons") or ["A safety check failed."]:
            print(f"  {r}")
        print("Opening the Bridge now needs your PIN. Safe mode itself lasts until you restart BuildBot.")
        pin = getpass.getpass("Your PIN (leave empty to keep it closed): ")
        if not pin:
            print("The Bridge stays closed.")
            return 1
        body = {"pin": pin}
    status, got = _call(info, "POST", "/v1/bridge/open", body)
    body.clear()                                   # the PIN is not kept
    if status == 200 and isinstance(got, dict) and got.get("setting", got.get("state")) == "open":
        # What it lets through now, in the same words as status: with nothing granted it still reads closed.
        lines = got.get("said") if isinstance(got.get("said"), list) else ["The Bridge is open."]
        for line in lines:
            print(line)
        return 0
    print("The Bridge stays closed:")
    _problems(got)
    return 1


#: ``puffpai shutdown`` (lane I7): what it says, and how long it waits for the Core's own stop.
NOT_RUNNING = "BuildBot is not running, so there is nothing to stop."
SHUTDOWN_WAIT_S = 60.0


def _cmd_shutdown(args: argparse.Namespace) -> int:
    """Asks the running Core to stop (``POST /v1/shutdown``, with this run's terminal key), then waits until
    it has: its stop removes ``settings\\terminal.json`` after every app and Toolsmith have ended."""
    from .api.app import STOPPED_LINE
    from .paths import state_dir
    info = _terminal()
    if info is None:
        held = held_now()                         # D91: something runs for this bot, but not reachable here
        if held:
            print("BuildBot did not stop:")
            print(f"  - {held}")
        else:
            print(NOT_RUNNING)
        return 1
    try:
        status, body = _call(info, "POST", "/v1/shutdown", {})
    except OSError:
        print(NOT_RUNNING)
        return 1
    if status != 202:
        print("BuildBot did not stop:")
        _problems(body)
        return 1
    print("BuildBot is stopping its apps.", flush=True)
    key_file = state_dir() / "settings" / "terminal.json"
    end = time.monotonic() + args.wait
    while time.monotonic() < end:
        if not key_file.exists():
            print(STOPPED_LINE)
            return 0
        time.sleep(0.25)
    print(f"BuildBot did not finish stopping within {int(args.wait)} seconds. "
          + ("Close its window to stop it." if os.name == "nt" else "Run this again in a moment."))
    return 1


#: ``puffpai start`` with no id (D94): what it says, and how long it waits for a Core it started.
START_WAIT_S = 60.0
SERVE_TITLE = "BuildBot ({name}) is running. Close this window to stop it."
START_CHAT_LINES = ("BuildBot runs in {where}. Closing this chat leaves it running; {stop} stops it.",
                    "Your dashboard: {origin}. Type /dashboard to open it.")
START_RUNS_LINE = "BuildBot runs in {where}. {stop} stops it."
#: The window's Terminal button (D115).
TERMINAL_TITLE = "BuildBot chat"
TERMINAL_WAIT_S = 3.0
TERMINAL_OPENING = "The terminal chat is opening, in this same chat."
TERMINAL_NOT_HERE = "A terminal of its own opens only on Windows so far, so this is the command line in the side pane."
TERMINAL_FAILED = "The terminal chat did not open ({why}). Chat with BuildBot.cmd opens it."
#: The console is this program with its own install, and its cwd is the bot folder: these could load code from there.
TERMINAL_ENV_DROPPED = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
_terminal_opened: dict[tuple[str, str], float] = {}       # (state, conversation): when its console last started


def _runs_words() -> dict[str, str]:
    """Where a started Core runs and what stops it, true to this computer: off Windows it has no window (D132)."""
    from .wizard.terminal import launcher_name
    return {"where": "its own window" if os.name == "nt" else "the background", "stop": launcher_name("Stop BuildBot.cmd")}


def _serve_log() -> Path:
    """Off Windows a started Core has no window of its own: what it says goes here (D132)."""
    from .paths import state_dir
    return state_dir() / "logs" / "serve.log"


def _self_argv() -> list[str]:
    """This same program: the venv's ``puffpai.exe`` that runs now (its launcher passes its own path without
    ``.exe``), else this Python with the CLI module."""
    me = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    exe = next((p for p in ((me, me.with_name(me.name + ".exe")) if me else ())
                if p.stem.lower() == "puffpai" and p.is_file()), None)
    python = Path(sys.executable)
    if not exe and python.stem.lower() == "pythonw":     # a shortcut (D81): serve's window needs the console Python
        exe = next((p for p in (python.with_name("puffpai.exe"),) if p.is_file()), None)
        python = python.with_name("python.exe")
    return [str(exe)] if exe else [str(python), "-m", "puffpai_core.cli"]


def _serve_argv(port: int | None) -> list[str]:
    """``puffpai serve`` with this same program. ``--port`` only when a flag named another one: serve reads the bot's
    own ports (D129)."""
    return _self_argv() + ["serve"] + (["--port", str(port)] if port not in (None, 8420) else [])


def _console_line(title: str, words: str) -> str:
    """The line that starts a console: the real cmd.exe by its full path, since a bare name can be picked from the
    Core's own folder. ``/d``: no AutoRun. ``/v:off``: a !VAR! in a path means nothing, whatever the registry says.
    Any code but 0 pauses, so she can read why."""
    from .devshell import system_dir
    return f'"{os.path.join(system_dir(), "cmd.exe")}" /d /v:off /c "title {title}& {words} || pause"'


def _launch_serve(argv: list[str], title: str) -> None:
    """Start ``serve`` as its own process, in its own console window on Windows (minimized), titled so she can
    find it. A serve that ends with an error pauses there, so she can read why (as the launchers do). Elsewhere it
    runs in a session of its own, so closing this terminal doesn't stop it, and writes to its log."""
    import subprocess
    if os.name != "nt":
        log = _serve_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "ab") as out:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,   # noqa: S603
                             start_new_session=True)         # this program's own serve
        return
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7                                      # SW_SHOWMINNOACTIVE: minimized, not in her way
    # Each part reaches cmd in a variable, so its characters mean nothing: "OneDrive - Smith & Sons" has an &, and a
    # folder can hold a real %TEMP%, which cmd would expand in the line itself but never twice (D108).
    env = {**os.environ, **{f"PUFFPAI_SERVE_{i}": a for i, a in enumerate(argv)}}
    words = " ".join(f'"%PUFFPAI_SERVE_{i}%"' for i in range(len(argv)))
    line = _console_line(title, words)
    subprocess.Popen(line, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si, env=env)   # noqa: S603


def open_terminal(state: Path | str, conversation: str) -> tuple[bool, str]:
    """The window's switch (D115): the chat in a console window of its own, this bot's, in *conversation*, which the
    caller has checked (``conversations.valid_id``). Returns (it opened, one plain line). Two presses in one chat are
    one; another chat opens its own."""
    import subprocess
    from .termchat import CONVERSATION_ENV
    if os.name != "nt":
        return False, TERMINAL_NOT_HERE
    folder = os.path.abspath(state)                     # a relative one would be read again from inside itself
    now, key = time.monotonic(), (folder, conversation)
    if now - _terminal_opened.get(key, -TERMINAL_WAIT_S) < TERMINAL_WAIT_S:
        return True, TERMINAL_OPENING
    _terminal_opened[key] = now
    argv = _self_argv()
    if argv[1:2] == ["-m"]:
        argv.insert(1, "-P")                            # no module from the folder it starts in
    # As in _launch_serve: each part reaches cmd in a variable, so its characters mean nothing.
    env = {**{k: v for k, v in os.environ.items() if k not in TERMINAL_ENV_DROPPED},
           "PUFFPAI_STATE": folder, CONVERSATION_ENV: conversation,
           **{f"PUFFPAI_CHAT_ARG_{i}": a for i, a in enumerate(argv)}}
    words = " ".join(f'"%PUFFPAI_CHAT_ARG_{i}%"' for i in range(len(argv)))
    line = _console_line(TERMINAL_TITLE, words)
    try:
        subprocess.Popen(line, creationflags=subprocess.CREATE_NEW_CONSOLE, env=env, cwd=folder)   # noqa: S603
    except OSError as exc:
        _terminal_opened.pop(key, None)
        return False, TERMINAL_FAILED.format(why=type(exc).__name__)
    return True, TERMINAL_OPENING


def _open_tab(url: str) -> None:
    import webbrowser
    webbrowser.open_new_tab(url)


def _ensure_core(port: int | None, wait: float) -> dict | None:
    """BuildBot once, for ``puffpai start`` and ``puffpai app`` (D94, D91): a Core of this bot that already answers
    is used, never a second one; a ``terminal.json`` left by a crash does not answer, so it does not count. Else
    ``serve`` in its own window, and wait for it. Its ``terminal.json`` info, or None (said here) when it did not start."""
    import re
    from . import bot
    from .paths import state_dir
    name = re.sub(r'[&|<>^"%!()\r\n]', "", bot.read(state_dir())["name"]).strip() or "BuildBot"   # safe in a cmd line
    title = SERVE_TITLE.format(name=name)
    info = _terminal()
    if info is not None:
        print("BuildBot is already running.", flush=True)
        return info
    _launch_serve(_serve_argv(port), title)
    print("Starting BuildBot...", flush=True)
    end = time.monotonic() + wait
    while info is None and time.monotonic() < end:
        time.sleep(0.5)
        info = _terminal()
    if info is None:
        print(f"BuildBot did not start within {int(wait)} seconds. Look at the window named "
              f'"{title}" on your taskbar: it says why.' if os.name == "nt"
              else f"BuildBot did not start within {int(wait)} seconds. {_serve_log()} says why.")
    return info


def _cmd_start_bot(args: argparse.Namespace) -> int:
    """``puffpai start`` (D94, ``Start BuildBot.cmd``): BuildBot once, in its own window, then what she picked to
    open (``puffpai_core.opens``): the chat here (the default), the dashboard in her browser, or the PuffPAI window
    (D81; if it can't open here, the plain line, then the chat). ``--opens chat|dashboard|window`` only saves that
    choice."""
    from . import opens
    from .paths import state_dir
    state = state_dir()
    if args.opens is not None:
        try:
            got = opens.save(state, args.opens)
        except ValueError as exc:
            print(exc)
            return 1
        from .wizard.terminal import launcher_name
        print(f"Saved: {launcher_name('Start BuildBot.cmd')} opens " + {opens.CHAT: "the chat in a terminal.",
                                                    opens.DASHBOARD: "your dashboard in your browser.",
                                                    opens.WINDOW: "the PuffPAI window."}[got])
        return 0
    info = _ensure_core(args.port, args.wait)
    if info is None:
        return 1
    from .api.app import desktop_origin
    origin = desktop_origin(int(info["port"]))
    choice = opens.read(state)
    if choice == opens.DASHBOARD:
        from . import desktop
        link = desktop.dashboard_link(state, int(info["port"]))
        try:
            _open_tab(link.url)
        except Exception:  # noqa: BLE001 - no browser: the link below is enough
            pass
        print("Your dashboard opens in your browser. The link works once, for 10 minutes:" if link.code
              else "Your dashboard opens in your browser:")
        print(f"  {link.url}")
        if link.line:
            print(link.line)
        print(START_RUNS_LINE.format(**_runs_words()))
        return 0
    if choice == opens.WINDOW:
        from . import window
        opened, line = window.open_window(int(info["port"]))
        print(line, flush=True)
        if opened:
            print(START_RUNS_LINE.format(**_runs_words()))
            return 0
    for line in START_CHAT_LINES:
        print(line.format(origin=origin, **_runs_words()))
    print(flush=True)
    from . import termchat
    return termchat.main()


def _cmd_app_remote(args: argparse.Namespace) -> int:
    """``puffpai app --remote <address>`` (D139): the window on this computer, for a bot on another. No Core here, and no
    bot folder: its own folder holds only the window's profile and lock."""
    from . import window
    from .paths import state_dir
    try:
        host, port = window.parse_remote(args.remote)
    except ValueError as exc:
        window.tell(str(exc))
        return 2
    os.environ.pop("PUFFPAI_STATE", None)
    os.environ["PUFFPAI_BOT"] = window.remote_bot_name(host, port)
    window.use_remote(host)
    if args.here:
        return window.run_here(remote_port=port)
    if not window.answers(port):
        window.tell(window.REMOTE_NO_ANSWER.format(origin=window.origin_of(port)))
        return 1
    opened, line = window.open_window(port, state_dir(), remote=f"{host}:{port}")
    window.tell(line)
    return 0 if opened else 1


def _cmd_app(args: argparse.Namespace) -> int:
    """``puffpai app`` (D81): BuildBot once (the same start as ``puffpai start``), then the PuffPAI window as its own
    process. ``--here`` is that process. ``--shortcuts`` and ``--no-shortcuts`` only make or remove its shortcuts."""
    from . import botports, window
    from .paths import state_dir
    if args.remote is not None:
        return _cmd_app_remote(args)
    state = state_dir()
    if args.here:
        return window.run_here()
    if args.no_shortcuts:
        print(window.remove_shortcuts(state))
        return 0
    if args.shortcuts:
        why = window.why_not()
        if why:
            print(window.cant_line(why, args.port or botports.read(state)["port"]))
            return 1
        made, line = window.make_shortcuts(state, port=args.port or window.DEFAULT_PORT)   # no flag: it reads the bot's own
        print(line)
        return 0 if made else 1
    info = _ensure_core(args.port, args.wait)
    if info is None:
        window.tell("BuildBot did not start, so its window can't open yet. Start BuildBot first.")
        return 1
    port = int(info["port"])
    opened, line = window.open_window(port)
    if opened:
        print(line)                                    # no console (a shortcut): nothing to say, the window shows
        return 0
    window.offer_dashboard(line, port, state)
    return 1


def _cmd_setup(args: argparse.Namespace) -> int:
    from .wizard.terminal import main as setup_main

    argv = [w for flag, v in (("--port", args.port), ("--pages-port", args.pages_port), ("--proxy-port", args.proxy_port))
            if v is not None for w in (flag, str(v))]
    return setup_main(argv + (["--exit"] if args.exit else []) + (["--from", args.from_folder] if args.from_folder else []))


def _say_stays() -> None:
    from .bot_move import STAYS
    print("This doesn't travel. It is entered again on the new computer:")
    for line in STAYS:
        print(f"  - {line}")


def _cmd_export(args: argparse.Namespace) -> int:
    """``puffpai export <folder>`` (D141): this bot, as a folder for a git repo. It only reads the bot."""
    from . import bot_move
    from .api import create_app
    core = create_app().extensions["puffpai"]
    try:
        got = bot_move.export(core.state, core.registry, core.packages, args.folder)
    except bot_move.MoveRefused as exc:
        print(f"Not exported: {exc}")
        return 1
    print(f"Exported to {got['folder']}. Apps: {', '.join(got['apps']) or 'none'}.")
    for row in got["left_out"]:
        print(f"Left out {row['name']}: {row['why']}.")
    _say_stays()
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    """``puffpai import <folder>`` (D141), on a bot already set up. A new computer uses ``puffpai setup --from <folder>``."""
    from . import bot_move
    info = _terminal()
    if info is None:
        print("Start BuildBot first, so each app can install through the Bridge, then run this again.")
        return 1
    status, body = _call(info, "POST", "/v1/settings/move/import", {"path": str(Path(args.folder).expanduser().resolve())})
    if status != 200 or not isinstance(body, dict):
        print(f"Not imported: {body.get('error') if isinstance(body, dict) else body}")
        return 1
    print(f"Imported {body['name']}.")
    for line in body["said"]:
        print(line)
    code = 0
    for app in body["apps"]:
        print(f"\nAdding {app['name']} {app['version']}")
        code |= _cmd_add(argparse.Namespace(source=app["source"], link=False))
    _say_moved(bot_move.lines_for(bot_move.read(args.folder)))
    return code


def _say_moved(said: dict) -> None:
    from .wizard.terminal import moved_lines
    for line in moved_lines(said):
        print(line)


#: ``puffpai pair --running`` (D92, ``Sign in a browser.cmd``) when no bot runs: a link would not open yet.
PAIR_NOT_RUNNING = "BuildBot is not running, so a sign-in link would not open yet."
PAIR_HOW_TO_START = "Start it first: {how}, then run this again."            # D94: Start opens the chat, no link
PAIR_HOW_TO_SERVE = "Start it first (in another terminal), then run this again: {serve}"


def _cmd_pair(args: argparse.Namespace) -> int:
    from . import botports, pairing
    from .api import network
    from .api.app import desktop_origin
    from .paths import state_dir

    port = args.port or botports.read(state_dir())["port"]
    if args.running:                                  # D92: Sign in a browser.cmd, for the bot that runs now
        info = _terminal()
        if info is None:
            # D91: this bot's Core may run and still refuse this terminal's key. The link does not need the key
            # (the running Core reads the code from pairing.json), only its port, from the bot's lock.
            from . import corelock
            held = corelock.holder(state_dir())
            if held is not None and held.get("kind") != corelock.TOOL:
                try:
                    info = {"port": int(held.get("port") or 0) or port}
                except (TypeError, ValueError):
                    info = {"port": port}
            elif held is not None:
                print(corelock.BUSY_LINE)
                return 1
        if info is None:
            from .wizard.terminal import cli_command, how_to_run, launcher
            start = launcher()
            print(PAIR_NOT_RUNNING)
            print(PAIR_HOW_TO_START.format(how=how_to_run(start)) if start.is_file() else PAIR_HOW_TO_SERVE.format(serve=cli_command('serve')))
            return 1
        port = int(info.get("port") or port)
    code = pairing.new_code(state_dir())
    print("Open this on this computer to sign the browser in (works once, for 10 minutes):")
    print(f"  {desktop_origin(port)}/#pair={code}")      # D79: the Desktop's own name, never 127.0.0.1
    if network.saved_on(state_dir()):                    # D130: the same code, typed on another device
        from . import corelock
        held = corelock.holder(state_dir())              # the running Core's own port, when one runs for this bot
        try:
            core_port = int((held or {}).get("port") or port)
        except (TypeError, ValueError):
            core_port = port
        urls = network.urls_of(network.network_ports(core_port)[0], network.own_addresses(), network.own_names())
        print("On another device on your home network, open one of these and type this code:")
        print(f"  {code}")
        for url in urls:
            print(f"  {url}")
        if not urls:
            print("  This computer has no home-network address right now.")
        print(network.WORTH_KNOWING)
    return 0


NETWORK_FIREWALL = ("The first time BuildBot listens, Windows may ask whether to allow Python through its firewall. "
                    "Choose Allow for private networks. BuildBot changes no firewall setting itself.")
NETWORK_ON_NOT_RUNNING = "BuildBot is not running. Start it, then run this again."


def _cmd_network(args: argparse.Namespace) -> int:
    """``puffpai network [status|on|off]`` (D130): BuildBot on her home network, for this bot. It is on unless she turns
    it off. Turning it on needs the running Core, which writes the audit line and opens it at once. Off works with
    BuildBot stopped too: off is always one step."""
    from .api import network
    from .paths import state_dir
    info = _terminal()
    if args.action == "on" and info is None:
        print(held_now() or NETWORK_ON_NOT_RUNNING)
        return 1
    if args.action == "off" and info is None:
        held = held_now()
        if held:                                      # something runs for this bot and did not take this key
            print(held)
            return 1
        network.save_on(state_dir(), False)
        print("Off: only this computer can open BuildBot. It stays that way until you turn it on.")
        return 0
    if info is None:
        print("On: it opens when BuildBot starts." if network.saved_on(state_dir()) else "Off: only this computer can open BuildBot.")
        return 0
    status, body = _call(info, "PUT", "/v1/network", {"on": args.action == "on"}) if args.action != "status" \
        else _call(info, "GET", "/v1/network")
    if status != 200 or not isinstance(body, dict):
        print(body.get("error") if isinstance(body, dict) else body)
        return 1
    if not body.get("on"):
        print("Off: only this computer can open BuildBot.")
        return 0
    if body.get("line"):
        print(body["line"])
    if not body.get("open"):
        return 1
    print("On: BuildBot listens on your home network, and only a device you pair gets in. A program with a key you made "
          "can also chat with it, using your bot's tools, documents and vault. Open one of these there:")
    for url in body.get("urls") or []:
        print(f"  {url}")
    print("Get a code with: puffpai pair. Turn this off with: puffpai network off")
    print(body.get("worth_knowing") or "")
    if os.name == "nt":
        print(NETWORK_FIREWALL)
    return 0


#: The one line under a new program key (lane D137).
APIKEY_KEEP = ("Keep it somewhere safe: it is shown only now. "
               "Programs send it as Authorization: Bearer <key>. It can call only the chat routes for programs "
               "(/openai/v1 and /ollama/api). A chat through them is a full turn of your bot, from this computer or "
               "another: it can use your bot's tools, your pinned documents and knowledge, and real values from your vault "
               "while it is open, and it can ask for your Yes. From another computer its conversations are its own, so it "
               "can't name your chat. On this computer it can.")
APIKEY_NONE = "No program keys yet."
APIKEY_LABEL_MAX = 60
#: Where a program uses the key (D147). An Ollama-style program adds /api/chat to its address itself, so its address stops at
#: /ollama: the routes are under /ollama/api.
APIKEY_HERE = "A program on this computer uses:"
APIKEY_THERE = ("A program on another computer uses this computer's address on your home network. It is one of these, "
                "and the others may not work from there (a virtual adapter's, for one):")
APIKEY_NO_ADDRESS = "  This computer has no home-network address right now."
APIKEY_THERE_OFF = ("BuildBot is closed to your home network right now, so a program on another computer can't reach it. "
                    "To turn it on, use Settings, Your home network, or with BuildBot running: {on}")
APIKEY_HOW = ("Put the address in the program's server or base URL setting, the line for the way it talks, with the model "
              "name puffpai. An Ollama-style program adds /api/chat itself, which makes /ollama/api/chat. These answer "
              "while BuildBot is running.")


def apikey_label_problem(label: str) -> str | None:
    """Why *label* can't name a program key, or None. One line, at most 60 characters, nothing unprintable."""
    if not label.strip():
        return "Give the key a label: a few words that say which program has it."
    if not label.isprintable():                     # a line break or any other control character
        return "A key's label must be one line of plain text."
    if len(label) > APIKEY_LABEL_MAX:
        return f"A key's label can be at most {APIKEY_LABEL_MAX} characters."
    return None


def _program_addresses(base: str) -> list[str]:
    return [f"  OpenAI-style: {base}openai/v1", f"  Ollama-style: {base}ollama"]


def _apikey_where() -> list[str]:
    """Where a program uses a new key (D147): on this computer, and on another computer unless the home network is off.
    The running Core's own port when one runs for this bot, as ``puffpai pair`` finds it."""
    from . import botports, corelock
    from .api import network
    from .api.app import HOST
    from .paths import state_dir
    from .wizard.terminal import cli_command

    state = state_dir()
    port = botports.read(state)["port"]
    try:
        port = int((corelock.holder(state) or {}).get("port") or port)
    except (TypeError, ValueError):
        pass
    lines = [APIKEY_HERE, *_program_addresses(f"http://{HOST}:{port}/")]
    if not network.saved_on(state):
        return [*lines, APIKEY_THERE_OFF.format(on=cli_command("network", "on")), APIKEY_HOW]
    urls = network.urls_of(network.network_ports(port)[0], network.own_addresses(), network.own_names())
    there = [line for url in urls for line in _program_addresses(url)] or [APIKEY_NO_ADDRESS]
    return [*lines, APIKEY_THERE, *there, APIKEY_HOW]


def _cmd_apikey(args: argparse.Namespace) -> int:
    """``puffpai apikey``: make, see and take back program keys (lane D137). A program key opens only the chat
    routes for programs, on this computer and on the home network (D147); this never issues, lists or takes back any
    other key. The same ``tokens.db`` the Core reads (sqlite), so it works with the Core running or stopped."""
    from datetime import date
    from .api.auth import TokenStore
    from .paths import state_dir

    if args.action != "revoke" and args.label is not None:
        print(f'puffpai apikey {args.action} takes no label here (new takes --label "<words>").')
        return 2
    if args.action != "new" and args.new_label is not None:
        print("--label is for puffpai apikey new.")
        return 2
    if args.action == "new":
        label = args.new_label if args.new_label is not None else f"program key {date.today().isoformat()}"
        problem = apikey_label_problem(label)
        if problem:
            print(problem)
            return 2
        label = label.strip()
        key = TokenStore(state_dir() / "tokens.db").issue_program_key(label)
        print(f'New program key "{label}":')
        print(f"  {key}")
        print(APIKEY_KEEP)
        for line in _apikey_where():
            print(line)
        return 0
    if args.action == "list":
        keys = TokenStore(state_dir() / "tokens.db").program_keys()
        if not keys:
            print(APIKEY_NONE)
            return 0
        for row in keys:
            print(f"  {str(row['created_at'])[:10]}  {row['label']}")
        return 0
    # revoke
    label = (args.label or "").strip()
    if not label:
        print("Say which key to take back: puffpai apikey revoke <label> (puffpai apikey list shows them).")
        return 2
    n = TokenStore(state_dir() / "tokens.db").revoke_program_key(label)
    print(f'Took back {n} program key{"" if n == 1 else "s"} labelled "{label}".')
    if n == 0:
        print("puffpai apikey list shows the labels.")
        return 1
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    """``puffpai new <kind> <id>`` (D133): a package that passes ``puffpai check``, in a new folder here, from
    templates/<kind>/. It only writes that folder; adding it is ``puffpai add``."""
    import re
    import shutil
    from .wizard.terminal import cli_command
    if args.kind not in NEW_KINDS:
        print(f"puffpai new can start: {', '.join(NEW_KINDS)}. A zone provider, a UI, an agent, a knowledge package or a bundle: "
              "docs/04_DEVELOPERS.md has their quick starts.")
        return 2
    if not re.fullmatch(NEW_ID, args.id):
        print("A package's id is lower case letters, digits and dashes, starting with a letter or digit.")
        return 2
    source, dest = Path(__file__).resolve().parents[2] / "templates" / args.kind, Path.cwd() / args.id
    if dest.exists():
        print(f"{dest} is already there. Nothing was written.")
        return 1
    name = args.id.replace("-", " ").title()
    shutil.copytree(source, dest)
    for f in (p for p in dest.rglob("*") if p.is_file()):
        f.write_text(f.read_text(encoding="utf-8").replace("__ID__", args.id).replace("__NAME__", name), encoding="utf-8")
    print(f"Started {name} ({args.kind}) in {dest}.")
    print("Edit it, then check it and add it:")
    print(f"  {cli_command('check', str(dest))}")
    print(f"  {cli_command('add', str(dest))}")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from . import uninstall
    return uninstall.run(args)


def _cmd_chat(args: argparse.Namespace) -> int:
    """``puffpai chat``: the same as no words. ``--once``: one message, its answer, then stop (D133)."""
    from . import termchat
    return termchat.main() if args.once is None else termchat.run_verb("once", args.once)


def _cmd_where(args: argparse.Namespace) -> int:
    """What a ``puffpai`` command here would use (D133, v1's ``where``). Never a key's value: the file it is kept in says
    enough, and a value on a screen ends up in a scrollback or a screenshot. Needs no running bot."""
    from . import botports, jsonfile
    from .paths import bot_name, state_dir
    from .wizard.terminal import launcher
    from .zone2 import settings as brain_settings
    state = state_dir()
    ports = botports.read(state)
    info = _terminal()
    data = jsonfile.read(state / "settings" / "data.json", {})
    folder = data.get("data_folder") if isinstance(data, dict) else None
    brain = brain_settings.load(state)
    start = launcher()
    print("This is the bot a puffpai command here would use:")
    print(f"  bot:         {bot_name()}")
    print(f"  bot folder:  {state}")
    print(f"  data folder: {folder or state / 'data'}")
    print(f"  code:        {Path(__file__).resolve().parents[2]}")
    print(f"  program:     {_self_argv()[0]}")
    print(f"  launchers:   {start.parent if start.is_file() else 'none: not installed by the installer'}")
    print(f"  ports:       {ports['port']} the Core, {ports['pages_port']} pages, {ports['proxy_port']} the Bridge proxy")
    print(f"  running:     {'yes, on port ' + str(info['port']) if info else 'no'}")
    print(f"  brain:       {brain.model or 'none picked yet'}, at {brain.address}")
    print(f"  key:         {state / 'settings' / 'terminal.json'} (value not printed)")
    return 0


def _cmd_verb(args: argparse.Namespace) -> int:
    """A chat verb as a shell command (D86): the same code as its /command inside ``puffpai``."""
    from . import termchat
    words = getattr(args, "words", None) or []
    arg = words if isinstance(words, str) else " ".join(str(x) for x in words if x)
    return termchat.run_verb(f"cmd_{args.command}", arg)


def _cmd_help(args: argparse.Namespace) -> int:
    """``puffpai help [word]``: the chat's command search, outside the chat (D86). No Core needed."""
    from . import termchat
    return termchat.help_verb(" ".join(args.words))


def _cmd_commands(args: argparse.Namespace) -> int:
    from . import termchat
    return termchat.commands_verb(args.json)


#: What the command line says for anything it can't take. Nothing she typed goes in it: that may be a PIN or a code.
NOT_TAKEN = "That isn't a command or option puffpai knows, or a word is missing, so nothing was done. puffpai help lists them."


def _port(text: str) -> int:
    """A port, or the parser's fixed line: a PIN typed as ``--port=<digits>`` must not start a Core or go in its lock file."""
    port = int(text)
    if not 0 <= port <= 65535:
        raise ValueError
    return port


class Parser(argparse.ArgumentParser):
    """argparse, with its error line fixed: its own says back what was typed (``vault --help=<PIN>``, ``unlock<PIN>``).
    ``add_subparsers`` makes the commands' parsers of this same class."""

    def error(self, message: str) -> NoReturn:
        self.exit(2, NOT_TAKEN + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="puffpai", description="PuffPAI BuildBot command line. With no words: "
                                           "chat with your running bot here (D86).")
    sub = p.add_subparsers(dest="command")             # no words: the chat (main)

    s = sub.add_parser("chat", help="chat with your running bot here; --once <words>: one message, its answer, then stop")
    s.add_argument("--once", help="the one message, for scripts")
    s.set_defaults(fn=_cmd_chat)

    s = sub.add_parser("uninstall", allow_abbrev=False,                  # --y is not --yes: this one deletes
                       help="remove this one bot: says what goes, asks first, keeps the code, other bots and folders you linked")
    s.add_argument("--yes", action="store_true", help="ask nothing; the vault goes too, unless --keep-vault")
    s.add_argument("--keep-vault", action="store_true", help="keep a copy of the vault in your Documents folder")
    s.set_defaults(fn=_cmd_uninstall)

    s = sub.add_parser("where", help="what a puffpai command here would use: folders, ports, brain; never a key's value")
    s.set_defaults(fn=_cmd_where)

    s = sub.add_parser("serve", help="run the Core API on 127.0.0.1, and for paired devices on your home network unless you turn that off")
    s.add_argument("--port", type=_port, help="the Core (default: this bot's own, in settings\\ports.json)")
    s.add_argument("--pages-port", type=_port, help="the pages address (D49)")
    s.add_argument("--proxy-port", type=_port, help="the Bridge proxy (0 = any free port)")
    s.set_defaults(fn=_cmd_serve)

    s = sub.add_parser("shutdown", help="stop the running BuildBot (every app and Toolsmith too), from any window")
    s.add_argument("--wait", type=float, default=SHUTDOWN_WAIT_S, help="seconds to wait for it to stop (default 60)")
    s.set_defaults(fn=_cmd_shutdown)

    s = sub.add_parser("list", help="the Registry: id, kind, status, health")
    s.add_argument("--kind")
    s.set_defaults(fn=_cmd_list)

    s = sub.add_parser("check", help="the 'Works on PuffPAI' levels for a package folder")
    s.add_argument("folder")
    s.add_argument("--floor", action="store_true", help="a provider's floor suite only (not built yet)")
    s.add_argument("--timeout", type=float, default=120, help="seconds its health check may take (default 120)")
    s.set_defaults(fn=_cmd_check)

    s = sub.add_parser("add", help="add a package: a folder, a git URL, a built-in id (like freshtable) or "
                                   "store:<id> from the PuffPAI Store")
    s.add_argument("source")
    s.add_argument("--link", action="store_true", help="run your own folder in place (never copied or deleted)")
    s.set_defaults(fn=_cmd_add)

    s = sub.add_parser("store", help="the PuffPAI Store: list, sources, add-source <url|folder>, "
                                     "remove-source <url|folder>, updates")
    s.add_argument("action", choices=("list", "sources", "add-source", "remove-source", "updates"))
    s.add_argument("where", nargs="?", help="for add-source and remove-source: an https index address or a folder")
    s.add_argument("--fresh", action="store_true", help="ask every store again now (list and updates)")
    s.set_defaults(fn=_cmd_store)

    s = sub.add_parser("knowledge", help="where your books and downloads are kept: where, move <folder|default>, "
                                         "resume, cancel")
    s.add_argument("action", choices=("where", "move", "resume", "cancel"))
    s.add_argument("folder", nargs="?", help="for move: a folder or drive, like E:\\PuffPAI Books, or default")
    s.add_argument("--leave-old", action="store_true", help="use the new place and leave what is at the old one "
                                                            "there (when the old drive is gone for good)")
    s.set_defaults(fn=_cmd_knowledge)

    s = sub.add_parser("remove", help="remove a package")
    s.add_argument("id")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--keep-data", action="store_true")
    g.add_argument("--delete-data", action="store_true")
    s.set_defaults(fn=_cmd_remove)

    s = sub.add_parser("update", help="bring the built-ins up to this BuildBot's own copy (system parts at once, "
                                      "apps with a Yes); with an id, that one package")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=_cmd_update)

    s = sub.add_parser("start", help="with no id: start BuildBot once in its own window, then open the chat (or the "
                                     "dashboard or the window, see --opens); with an id: start that package (the Core "
                                     "must be running)")
    s.add_argument("id", nargs="?")
    s.add_argument("--port", type=_port)
    s.add_argument("--opens", help="save what opens when BuildBot starts: chat, dashboard or window, then stop")
    s.add_argument("--wait", type=float, default=START_WAIT_S, help="seconds to wait for BuildBot to answer (default 60)")
    s.set_defaults(fn=lambda a: _cmd_start_stop(a) if a.id else _cmd_start_bot(a))

    s = sub.add_parser("app", help="open the PuffPAI window for this bot (starts BuildBot once if it is not running)")
    s.add_argument("--port", type=_port)
    s.add_argument("--wait", type=float, default=START_WAIT_S, help="seconds to wait for BuildBot to answer (default 60)")
    s.add_argument("--here", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--remote", help="the address of a bot on another computer on your home network; puffpai pair prints it there")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--shortcuts", action="store_true", help="make its Start menu and desktop shortcuts, then stop")
    g.add_argument("--no-shortcuts", action="store_true", help="remove those shortcuts, then stop")
    s.set_defaults(fn=_cmd_app)

    s = sub.add_parser("stop", help="stop a package (the Core must be running)")
    s.add_argument("id")
    s.set_defaults(fn=_cmd_start_stop)

    s = sub.add_parser("logs", help="show a package's log")
    s.add_argument("id")
    s.add_argument("--lines", type=int, default=100)
    s.set_defaults(fn=_cmd_logs)

    s = sub.add_parser("notices", help="what the Desktop shows: the Core's cards and packages' notices")
    s.add_argument("--lines", type=int, default=20)
    s.set_defaults(fn=_cmd_notices)

    s = sub.add_parser("bridge", help="the Bridge: status, open or close (in safe mode, open needs your PIN)")
    s.add_argument("action", nargs="?", default="status", choices=("status", "open", "close"))
    s.set_defaults(fn=_cmd_bridge)

    s = sub.add_parser("export", help="this bot, as a folder for a git repo, to move it to another computer. Keys, the vault "
                                      "and chats never go in")
    s.add_argument("folder")
    s.set_defaults(fn=_cmd_export)
    s = sub.add_parser("import", help="a bot exported from another computer, on a bot already set up. On a new computer: "
                                      "puffpai setup --from <folder>")
    s.add_argument("folder")
    s.set_defaults(fn=_cmd_import)
    s = sub.add_parser("setup", help="the system parts (the Desktop, setup, the cloud avatar), then the wizard in this terminal (the same steps as the Desktop)")
    s.add_argument("--port", type=_port)
    s.add_argument("--pages-port", type=_port)
    s.add_argument("--proxy-port", type=_port)
    s.add_argument("--exit", action="store_true", help="stop BuildBot when setup ends (it keeps running by default)")
    s.add_argument("--from", dest="from_folder", default=None, metavar="FOLDER",
                   help="set up a bot moved from another computer (puffpai export): asks only what can't travel")
    s.set_defaults(fn=_cmd_setup)

    s = sub.add_parser("pair", help="a one-time link that signs a browser on this computer in to the Desktop, and a "
                                    "code and address for another device on your home network")
    s.add_argument("--port", type=_port)
    s.add_argument("--running", action="store_true",
                   help="only while BuildBot runs; otherwise say so and how to start it (D92)")
    s.set_defaults(fn=_cmd_pair)

    s = sub.add_parser("network", help="paired devices on your home network, on by default: status, off or on")
    s.add_argument("action", nargs="?", default="status", choices=("status", "on", "off"))
    s.set_defaults(fn=_cmd_network)
    s = sub.add_parser("apikey", help="program keys for Home Assistant and other programs, on this computer or another "
                                      "on your home network: new [--label <words>], list, revoke <label>")
    s.add_argument("action", choices=("new", "list", "revoke"))
    s.add_argument("label", nargs="?", help="for revoke: the label of the key(s) to take back")
    s.add_argument("--label", dest="new_label", metavar="WORDS",
                   help="for new: a few words that say which program has it (default: program key <date>)")
    s.set_defaults(fn=_cmd_apikey)

    # D86: the chat's verbs as shell commands, on the running Core (the same code as /build, /yes, ...).
    for name, words, helptext in (
            ("build", "+", "Toolsmith plans your goal; shows its one plan card here and takes your Yes or No"),
            ("plan", "?", "show the plan that waits for your Yes again (optionally for one build)"),
            ("yes", "?", "show a waiting Yes card by its short id and answer it"),
            ("no", "?", "say No to a waiting Yes card by its short id"),
            ("watch", "?", "follow a build until it ends or needs you (Ctrl+C stops watching, not the build)"),
            ("turn", "*", "Your turn: steps and Yes cards that wait; turn done <n>, turn skip <n>"),
            ("vault", "*", "your vault: status, unlock (PIN typed hidden), lock, or recover (your recovery code and a new "
                           "PIN, typed hidden, never a flag)"),
            ("brain", "*", "which local model answers here; brain <model> changes it to one on this computer"),
            ("agents", "*", "the seven agents, and what each is doing now"),
            ("outside", "*", "the Bridge, and what tried to leave your computer; outside log [n]: only the log"),
            ("keys", "*", "which keys are saved, never a value; keys add <company>, keys remove <company>"),
            ("prove", "*", "ask the running bot to prove it works, with one real chat turn")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("words", nargs=words, default=[] if words in ("*", "?") else None)
        s.set_defaults(fn=_cmd_verb)

    s = sub.add_parser("help", help="the chat's commands, or search them by any word")
    s.add_argument("words", nargs="*")
    s.set_defaults(fn=_cmd_help)

    s = sub.add_parser("commands", help="every chat command; --json for scripts and other AIs")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_cmd_commands)

    s = sub.add_parser("new", help="start a package in a new folder here: " + ", ".join(NEW_KINDS))
    s.add_argument("kind")
    s.add_argument("id")
    s.set_defaults(fn=_cmd_new)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        if getattr(args, "command", None) != "vault":
            parser.error(NOT_TAKEN)
        args.words = [*args.words, "?"]                # never said back: cmd_vault refuses it, recover asks hidden anyway
    if getattr(args, "command", None) is None:         # puffpai with no words: the chat (D86)
        from . import termchat
        return termchat.main()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
