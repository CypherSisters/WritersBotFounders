"""Core API group ``zones`` (lane I18f): the Developer page's per-zone settings that had no route yet.

Each route here changes a setting v2 already has, by the same code the wizard uses. Nothing here is a new
way around a Yes:

- Zone 1, the data folder and linked folders (lane L3's ``settings\\folders.json``, read by the Library and
  by Toolsmith's zone, where every one of them is a "never" place):
  ``GET /v1/folders``; ``POST /v1/folders/link`` and ``POST /v1/folders/unlink`` ``{path}`` (the wizard's
  own vault step links folders without a card, and so does this); ``POST /v1/folders/from-setup`` uses the
  folders Setup saved in ``settings\\data.json`` (before this lane the wizard wrote only that file, which
  nothing read).
- Zone 3, Toolsmith's keys (``toolsmith.<provider>`` in the vault, the name the wizard's key field and the
  sender use): ``GET /v1/toolsmith/keys`` (kept or not, never a value); ``PUT /v1/toolsmith/keys/<provider>``
  ``{value}`` (vault open: saved; locked: sealed into the inbox, as the wizard does); ``DELETE`` takes it out.
  A key alone sends nothing: Toolsmith uses a company only after her Yes to its consent sentence
  (``PUT /v1/toolsmith/model``), and only while a build she said Yes to runs.
- Zone 3, Toolsmith's cost cap: ``PUT /v1/toolsmith/cap`` ``{cost_cap_usd}`` LOWERS it at once. A higher cap
  is refused here: it goes through ``PUT /v1/toolsmith/model`` with her Yes to the sentence again.
- Zone 3, the hosts Toolsmith may reach as v2 models them (read only): ``GET /v1/toolsmith/hosts``.
- Zone 3, Toolsmith's build-time search: ``POST /v1/bridge/search`` ``{on}``. On raises a Yes card naming
  every place and exactly the wizard's search hosts (``WizardBridge.SEARCH_HOSTS``, build phase only); her Yes on
  that card turns the switch on (the Bridge's own ``_on_yes``). Off turns the switch off at once, and takes back
  every search Yes, a key's too (``PUT /v1/toolsmith/search-key/<id>``, group ``github``).

No route here returns a key, and none echoes one back.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request

from ... import jsonfile
from ...zone1 import archivist as _archivist
from ..auth import OWNER, OWNER_DEVICE
from ..routing import current_core, route

bp = Blueprint("zones", __name__)
LANE = "I18f"
MAX_PATH = 1000
MAX_KEY = 4096


def built(method: str, rule: str, access: str):
    def deco(fn):
        fn.puffpai_built_by = LANE
        return route(bp, method, rule, access)(fn)
    return deco


def _body() -> dict:
    got = request.get_json(silent=True)
    return got if isinstance(got, dict) else {}


def _no_store(payload, status: int = 200):
    r = jsonify(payload)
    r.status_code = status
    r.headers["Cache-Control"] = "no-store"
    return r


def _by() -> str:
    return getattr(getattr(g, "caller", None), "label", "") or "owner"


# -- Zone 1: the data folder and linked folders -----------------------------------------------------------

def _library():
    from ...zone1.folders import Library
    core = current_core()
    return Library(_archivist.for_state(core.state, core.bus), core.state)


def _from_setup(state: Path) -> dict | None:
    """What Setup's vault step saved (``settings\\data.json``) while ``folders.json`` is not there yet."""
    if (state / "settings" / "folders.json").exists():
        return None
    got = jsonfile.read(state / "settings" / "data.json", {})
    if not isinstance(got, dict):
        return None
    folder = got.get("data_folder") if isinstance(got.get("data_folder"), str) else ""
    linked = [p for p in got.get("linked") or [] if isinstance(p, str) and p.strip()]
    return {"data": folder, "linked": linked} if (folder or linked) else None


def _folders_view() -> dict:
    lib = _library()
    state = current_core().state
    data = lib.data_folder
    return {"data": str(data), "data_there": data.is_dir(), "default": data == state / "data",
            # What each one read and what it couldn't (D126), so a file it skips is never a silent mystery.
            "linked": [{"path": str(p), "there": p.is_dir(), **(lib.folder_report(p) if p.is_dir() else {})}
                       for p in lib.linked()],
            "from_setup": _from_setup(state),
            "line": "Your bot reads these folders where they are. It never copies, moves or changes your files, "
                    "and Toolsmith may never touch them."}


def _path_arg() -> tuple[str | None, str]:
    raw = _body().get("path")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_PATH or "\x00" in raw:
        return None, f'Send {{"path": "<a folder on this computer>"}}, at most {MAX_PATH} characters.'
    text = os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))
    if text.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        return None, "Use the folder's plain path."
    if not os.path.isabs(text):
        return None, "Use the folder's whole path, like C:\\Users\\you\\Documents\\Recipes."
    return text, ""


@built("GET", "/v1/folders", OWNER)
def folders_get():
    return _no_store(_folders_view())


@built("POST", "/v1/folders/link", OWNER_DEVICE)
def folders_link():
    from ...zone1.folders import FolderRefused
    text, problem = _path_arg()
    if text is None:
        return _no_store({"error": problem}, 400)
    from ...zone1.folders import canon
    lib = _library()
    if canon(text) in [canon(x) for x in lib.linked()]:
        return _no_store({"error": "That folder is linked already."}, 409)
    try:
        lib.link(text)                                   # the one folder check runs in Library.link
    except FolderRefused as exc:
        return _no_store({"error": str(exc)}, 400)
    lib.scan_soon()                                      # read now, not at her next search (D126)
    return _no_store(_folders_view())


@built("POST", "/v1/folders/unlink", OWNER_DEVICE)
def folders_unlink():
    text, problem = _path_arg()
    if text is None:
        return _no_store({"error": problem}, 400)
    lib = _library()
    links = [str(x) for x in lib.linked()]
    target = text if text in links else None
    if target is None:                                   # the same folder written another way
        want = _resolved(text)
        target = next((x for x in links if _resolved(x) == want), None)
    if target is None:
        return _no_store({"error": "That folder is not linked."}, 404)
    data = lib._settings()                               # noqa: SLF001 - the Library's own file
    data["linked"] = [x for x in data.get("linked", []) if x != target]
    lib._save(data)                                      # noqa: SLF001
    _drop_index(lib, target)
    return _no_store(_folders_view())


def _resolved(text: str) -> str:
    from ...zone1.folders import canon
    return canon(text)


def _drop_index(lib, folder: str) -> None:
    """The folder's rows in the Library's index (its own files are never touched)."""
    import sqlite3
    from contextlib import closing
    seps = "\\/"
    roots = {folder.rstrip(seps), str(Path(folder).resolve()).rstrip(seps) if Path(folder).exists() else folder}
    with closing(sqlite3.connect(lib.db_path)) as db, db:
        for root in roots:
            for table in ("chunks", "files"):
                db.execute(f"DELETE FROM {table} WHERE path = ? OR substr(path, 1, ?) IN (?, ?)",
                           (root, len(root) + 1, root + "\\", root + "/"))


@built("POST", "/v1/folders/from-setup", OWNER_DEVICE)
def folders_from_setup():
    """Use the folders Setup saved: its data folder, and each linked folder that is there."""
    state = current_core().state
    setup = _from_setup(state)
    if setup is None:
        return _no_store({"error": "There is nothing from Setup to use: your folders are in use already."}, 409)
    from ...zone1.folders import FolderRefused, folder_problem
    lib = _library()
    lines = []
    refused = folder_problem(state, setup["data"], as_data=True) if setup["data"] else ""
    if refused:                                          # the same check the wizard and Library.link use
        lines.append(f"{setup['data']}: {refused} The data folder stays {lib.data_folder}.")
    elif setup["data"]:
        try:
            Path(setup["data"]).mkdir(parents=True, exist_ok=True)
        except OSError:
            lines.append(f"{setup['data']} could not be made, so the data folder stays {lib.data_folder}.")
        else:
            data = lib._settings()                                  # noqa: SLF001 - the Library's own file
            data["data"] = setup["data"]
            data.setdefault("linked", [])
            lib._save(data)                                         # noqa: SLF001
            lines.append(f"Your data folder: {setup['data']}")
    for folder in setup["linked"]:
        try:
            lib.link(folder)
            lines.append(f"Linked {folder}. It is read where it is, never copied or changed.")
        except FolderRefused as exc:
            lines.append(f"{folder}: {exc}")
    if not (state / "settings" / "folders.json").exists():          # nothing could be used: keep the file
        lib._save(lib._settings() or {"linked": []})                # noqa: SLF001 - so this asks once only
    return _no_store(dict(_folders_view(), lines=lines))


# -- Zone 3: Toolsmith's keys, cap and hosts ---------------------------------------------------------------

def _toolsmith():
    from .toolsmith import service
    return service()


def _described() -> list[dict]:
    from ...zone3.toolsmith_host import sender
    return [sender.describe(p) for p in _toolsmith().providers().values()]


def _key_state(a, name: str) -> bool | None:
    """True kept, False not kept, None can't tell (the vault is locked or not set up)."""
    if not a.is_open():
        return None
    try:
        return a.vault.meta(name) is not None
    except Exception:  # noqa: BLE001 - can't tell is not "kept"
        return None


@built("GET", "/v1/toolsmith/keys", OWNER)
def toolsmith_keys():
    core = current_core()
    a = _archivist.for_state(core.state, core.bus)
    out = [{"provider": d["id"], "company": d["company"], "vault_name": d["key"]["vault_name"],
            "kept": _key_state(a, d["key"]["vault_name"]), "get_it": d["key"].get("get_it") or ""}
           for d in _described()]
    return _no_store(out)


@built("PUT", "/v1/toolsmith/keys/<provider>", OWNER_DEVICE)
def toolsmith_key_put(provider: str):
    from ...zone1.vault import VaultError
    d = next((x for x in _described() if x["id"] == provider), None)
    if d is None:
        return _no_store({"error": "There is no such provider for Toolsmith."}, 404)
    value = _body().get("value")
    value = value.strip() if isinstance(value, str) else ""
    if not value or len(value) > MAX_KEY or any(ord(c) < 32 for c in value):
        return _no_store({"error": f"Paste the key {d['company']} gave you, on one line."}, 400)
    core = current_core()
    a = _archivist.for_state(core.state, core.bus)
    try:
        status = a.vault.status().get("state")
    except Exception:  # noqa: BLE001
        status = ""
    if status not in ("open", "locked"):                 # the wizard's own rule (Engine.keep_secret)
        return _no_store({"error": "Set up the vault first, so your key has a safe home." if status in ("", "not_set_up")
                          else "Your vault can't take a key right now. Open the Vault page to see why."}, 409)
    try:
        got = a.put_secret(d["key"]["vault_name"], value, label="secret.api_key")
    except VaultError as exc:
        return _no_store({"error": exc.message, "code": exc.code}, exc.status)
    finally:
        value = ""                                                   # noqa: F841 - not kept here
    line = ("Saved in your vault." if got.get("saved") else
            "Sealed in your vault's inbox. It is filed the next time you unlock.")
    return _no_store({"saved": bool(got.get("saved")), "line": line}, 200 if got.get("saved") else 202)


@built("DELETE", "/v1/toolsmith/keys/<provider>", OWNER_DEVICE)
def toolsmith_key_delete(provider: str):
    from ...zone1.vault import VaultError
    d = next((x for x in _described() if x["id"] == provider), None)
    if d is None:
        return _no_store({"error": "There is no such provider for Toolsmith."}, 404)
    core = current_core()
    a = _archivist.for_state(core.state, core.bus)
    if not a.is_open():
        return _no_store({"error": "Unlock your vault first, then take the key out."}, 423)
    try:
        gone = a.delete_secret(d["key"]["vault_name"])
    except VaultError as exc:
        return _no_store({"error": exc.message, "code": exc.code}, exc.status)
    if not gone:
        return _no_store({"error": f"No key for {d['company']} is kept."}, 404)
    return _no_store({"deleted": True, "line": f"Your {d['company']} key is out of your vault."})


@built("PUT", "/v1/toolsmith/cap", OWNER_DEVICE)
def toolsmith_cap():
    """Lower the cap at once. Raising it is a widening: that is the model choice with her Yes again."""
    from ...zone3.toolsmith_host import settings as ts_settings
    raw = _body().get("cost_cap_usd")
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        cap = float("nan")
    if isinstance(raw, bool) or not math.isfinite(cap) or cap < 0:
        return _no_store({"error": "The cost cap is a number of dollars."}, 400)
    host = _toolsmith()
    st = host.settings()
    if st.get("where") != "cloud":
        return _no_store({"error": "Toolsmith uses this computer, so there is no cost to cap."}, 409)
    now = float(st.get("cost_cap_usd") or 0)
    if cap > now:
        return _no_store({"error": "A higher cap needs your Yes again: pick the model, agree to what it sends, "
                                   "and save.", "code": "needs_yes"}, 409)
    keep = {k: st[k] for k in ("where", "provider", "model", "consent") if k in st}
    ts_settings.write(host.state, dict(keep, cost_cap_usd=round(cap, 2)))
    got = host.settings()
    return _no_store({k: got.get(k) for k in ("where", "provider", "model", "cost_cap_usd")})


@built("GET", "/v1/toolsmith/hosts", OWNER)
def toolsmith_hosts():
    """The places Toolsmith may reach, as v2 models them: fixed lists, opened only by her Yes."""
    from ...wiring import WizardBridge
    from ...zone3.bridge import CLOUD_SUBJECT, READ_SUBJECT, SEARCH_SUBJECT
    from ...zone3.policy import KNOWLEDGE_HOSTS, WEB_KEY_HOSTS
    from ...zone3.service import for_app
    from ...zone3.toolsmith_host import sender
    from .owner_apis import listed as _owner_api_rows
    z = for_app(current_app)
    view = z.bridge.view({})
    st = _toolsmith().settings()
    company = ""
    cloud_hosts: list[str] = []
    if st.get("where") == "cloud":
        d = next((x for x in _described() if x["id"] == st.get("provider")), None)
        if d is not None:
            company = d["company"]
            listed = sender.known_hosts().get(company, [])
            cloud_hosts = [h for h in d["hosts"] if h in listed]
    switches = view.get("switches") or {}
    grants = [{"subject": gr.get("subject"), "host": gr.get("host"), "phase": gr.get("phase")}
              for gr in view.get("grants") or []
              if str(gr.get("subject") or "") in (SEARCH_SUBJECT, CLOUD_SUBJECT, READ_SUBJECT)
              or str(gr.get("subject") or "").startswith("pkg:toolsmith-build-")]
    return _no_store({
        "search": {"hosts": list(WizardBridge.SEARCH_HOSTS), "on": bool(view.get("search")),
                   "switch": bool(switches.get(SEARCH_SUBJECT))},
        "search_yes_names": list(view.get("search_hosts") or []),
        "web": {"hosts": dict(WEB_KEY_HOSTS)},
        "owner_apis": [{k: row[k] for k in ("id", "name", "host", "tested", "yes", "linked", "on", "line")}
                       for row in _owner_api_rows(z)],
        "cloud": {"company": company, "hosts": cloud_hosts, "on": st.get("where") == "cloud"},
        "knowledge": {"hosts": list(KNOWLEDGE_HOSTS), "also": "a plain .gov file host named on that build's card"},
        "known": sender.known_hosts(),
        "grants": grants,
        "line": "Toolsmith reaches only these places, only while a build you said Yes to runs. Each app it tries "
                "gets its own hosts on that build's Yes card.",
    })


# -- Zone 3: Toolsmith's build-time search -----------------------------------------------------------------

def _search_cards(z, status: str) -> list[dict]:
    """Toolsmith's search Yes cards (host, ``toolsmith:search``) in *status*, oldest first."""
    from ...wiring import WizardBridge
    return [r for r in z.consents.list(status)
            if (r.get("scope") or {}).get("kind") == "host" and (r.get("scope") or {}).get("target") == WizardBridge.SEARCH]


@built("POST", "/v1/bridge/search", OWNER_DEVICE)
def bridge_search():
    from ...consent import card_view
    from ...wiring import WizardBridge, search_card
    from ...zone3.bridge import is_key_yes
    from ...zone3.service import for_app
    on = _body().get("on")
    if not isinstance(on, bool):
        return _no_store({"error": 'Send {"on": true} or {"on": false}.'}, 400)
    z = for_app(current_app)
    if not on:                                           # narrowing: at once
        try:
            z.bridge.set_switch(WizardBridge.SEARCH, False)
        except Exception:  # noqa: BLE001 - no machine key: every decision is closed already; close to be sure
            z.bridge.close(by=_by())
        # No old card can turn it back on: a waiting search card is withdrawn, and a search Yes is taken back,
        # so on again always asks a fresh Yes.
        for rec in _search_cards(z, "pending"):
            z.consents.expire(rec["consent_id"], "You turned Toolsmith's search off.")
        for rec in _search_cards(z, "granted"):
            z.consents.revoke(rec["consent_id"])
        return _no_store(dict(z.bridge.view({}), search_on=False))
    hosts = list(WizardBridge.SEARCH_HOSTS)
    waiting = []
    for rec in _search_cards(z, "pending"):
        if is_key_yes(rec):                              # a key's own card: not this card
            continue
        if set((rec.get("covers") or {}).get("hosts") or []) == set(hosts):
            waiting.append(rec)
        else:                                            # a card from before: it does not name every place
            z.consents.expire(rec["consent_id"], "This card named fewer places than Toolsmith's search now reaches.")
    if waiting:                                          # the card that waits already: never a second one
        rec = waiting[-1]
        seen = card_view(rec.get("card") if isinstance(rec.get("card"), dict) else {}, bool(rec.get("floor")))
        return _no_store({"consent_id": rec["consent_id"], "card_sha": rec.get("card_sha"), "title": seen["title"],
                          "lines": seen["lines"], "again": True}, 202)
    from ...consent import ConsentError
    try:
        rec = z.consents.ask("host", WizardBridge.SEARCH, {"hosts": hosts, "phase": "build", "card": search_card()})
    except ConsentError as exc:
        return _no_store({"error": str(getattr(exc, "message", "") or exc)}, getattr(exc, "status", 503))
    seen = card_view(rec.get("card") if isinstance(rec.get("card"), dict) else {}, bool(rec.get("floor")))
    return _no_store({"consent_id": rec["consent_id"], "card_sha": rec.get("card_sha"), "title": seen["title"],
                      "lines": seen["lines"]}, 202)
